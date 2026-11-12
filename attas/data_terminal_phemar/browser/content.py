"""Deferred content lifecycle within a Terminal-owned Browser worker."""
import asyncio
import hashlib
import json
import copy
import time
from fastapi import HTTPException
from phemacast.core.composition import composition, content_hash, reference_paths
from phemacast.core.composition_resolution import ResolutionStore, CompositionResolver
from ..content_policy import effective, POLICY_FIELDS


def setup_content(engine, instance, user):
    async def resolve(ref, inputs, context):
        return await engine.network.resolve_reference(ref, inputs, user, engine.store)
    from ..phema_security import check
    from ..source_choice import choosable
    async def authorize(current,ref,context):
        # Plaza's current decision for this viewer approves pulse links to agents it trusts.
        if hasattr(engine.network,'native_authority') and ref.get('kind')=='pulse':
            user['_nativeTrust']=await engine.network.native_authority()
        rule=check(engine.store,user,instance['phema'],current,ref,context)
        # The host classifies every pulse as an action, a canonical data read included. Its last result may still
        # be shown again after a reload: showing it runs and spends nothing, and the approval above still applies.
        # The resolver uses this rule only to decide that.
        if choosable(ref) and not any(((ref.get('extensions') or {}).get('phemacast:operation-effects') or {}).values()):
            return {**rule,'effect':'read'}
        return rule
    instance['_contentResolver'] = CompositionResolver(ResolutionStore(engine.store.path.parent/'composition-results.sqlite'), {k: resolve for k in ('pulse','api','channel','phemar-snapshot','phemar-generate','asset')},authorize=authorize)
    instance['_contentLock'] = asyncio.Lock()


def kept_scope(engine, user):
    # Results are kept for this account in this Terminal window on this Plaza. The sign-in token is not part of
    # the scope: it changes at every session renewal and sign-in, so a reload or restart found none of them.
    return hashlib.sha256(json.dumps([engine.settings_fingerprint(user['id']), getattr(engine.network, 'plaza', '')]).encode()).hexdigest()


async def refresh_content(engine, instance, user, *, references=None, trigger='open', interaction_inputs=None):
    if not instance.get('phema'): return
    if '_contentResolver' not in instance: setup_content(engine, instance, user)
    async with instance['_contentLock']:
        generation = instance['generation']
        params = {k: v['default'] for k, v in instance['entry']['design']['params'].items() if 'default' in v}
        async def update(id, state):
            if instance['generation'] == generation:
                instance.setdefault('content', {}).setdefault('results', {})[id] = state
                instance['sequence'] += 1
                log_notes(instance, id, state)
        result = await instance['_contentResolver'].resolve(effective(instance['phema'],instance.get('contentOverrides',{})), params, owner=user['id'], authorization_scope=kept_scope(engine, user), refresh=references, trigger=trigger, previous=instance.get('content'), on_update=update, interaction_inputs=interaction_inputs)
        if instance['generation'] == generation:
            instance['content'] = result; instance['sourceMode'] = 'parameters'; instance['sequence'] += 1


async def run_content(engine, instance, user, changed=None):
    refs = reference_paths(effective(instance['phema'],instance.get('contentOverrides',{})))
    try: await refresh_content(engine, instance, user, references=changed)
    except asyncio.CancelledError: raise
    except Exception as exc:
        instance['content']={'status':'partial','error':type(exc).__name__,'results':{}}
        instance['sequence']+=1
        return
    intervals = {id: r['refresh']['seconds'] for id,r in refs.items() if r.get('refresh', {}).get('trigger') == 'interval'}
    if not intervals: return
    loop=asyncio.get_running_loop(); due={id:loop.time()+seconds for id,seconds in intervals.items()}
    while True:
        await asyncio.sleep(max(0, min(due.values())-loop.time()))
        pending=[id for id,at in due.items() if at<=loop.time()]
        await refresh_content(engine, instance, user, references=pending, trigger='interval')
        for id in pending:due[id]=loop.time()+intervals[id]


def log_notes(instance, reference, state):
    """One terminal.log line per note a panel's source attached, such as a listing without data.

    Each note is written once per Browser, so refreshes do not repeat it; only the listing and the source's fixed message.
    """
    import sys, time
    value = (state or {}).get('value') if isinstance(state, dict) else None
    notes = value.get('notes') if isinstance(value, dict) else None
    if not isinstance(notes, list):
        return
    seen = instance.setdefault('_loggedNotes', set())
    for note in notes[:50]:
        if not isinstance(note, dict) or not isinstance(note.get('message'), str):
            continue
        listing = note.get('instrument') if isinstance(note.get('instrument'), str) else ''
        key = (reference, listing, note['message'])
        if key in seen or len(seen) >= 500:
            continue
        seen.add(key)
        print(time.strftime('%Y-%m-%d %H:%M:%S%z') + ' INFO:     Phema content: ' + str(reference) + ' — '
              + (listing + ': ' if listing else '') + note['message'], file=sys.stderr, flush=True)


def missing_snapshot(action, snapshot_id):
    """One WARNING line in terminal.log, so a report shows why a snapshot was unavailable."""
    import sys, time
    shown = 'an empty id' if not snapshot_id else 'snapshot ' + str(snapshot_id)[:8] + '…'
    print(time.strftime('%Y-%m-%d %H:%M:%S%z') + ' WARNING:  Phema content: ' + action + ' asked for ' + shown + ', which this Terminal does not have.',
          file=sys.stderr, flush=True)


async def content_route(engine, instance, request, user):
    store = ResolutionStore(engine.store.path.parent/'composition-results.sqlite')
    if request.method == 'GET':
        return {'snapshots': [s for s in store.list(user['id'], instance['phema']['id']) if s['browserSnapshot']], 'current': engine.public(instance).get('content'), 'sourceMode': instance.get('sourceMode', 'parameters'), 'references':reference_paths(effective(instance['phema'],instance.get('contentOverrides',{}))),
                'dataSources':await data_sources(engine,instance,user)}
    body = await request.json()
    if body.get('operation')=='source':
        # The viewer's data source for this Browser's data panels; None returns them to the automatic choice.
        from ..source_choice import apply, candidates
        refs=reference_paths(instance['phema']);entry=None
        if body.get('source') is not None:
            directory=await engine.network.directory(user.get('token',''))
            listed={row['id'] for row in candidates(directory,refs)['sources']}
            entry=next((e for e in directory if isinstance(e,dict) and e.get('id')==body['source']),None)
            if entry is None or body['source'] not in listed:raise HTTPException(422,'Choose one of the listed data sources.')
        overrides,changed=apply(instance.get('contentOverrides',{}),refs,entry)
        instance['contentOverrides']=overrides;instance['sequence']+=1
        if changed and instance.get('sourceMode')!='snapshot':
            # Interval panels keep their schedule; the changed panels read from their new source now, as ↻ does.
            old=instance.get('_contentTask')
            if old:old.cancel();await asyncio.gather(old,return_exceptions=True)
            instance['_contentTask']=asyncio.create_task(run_content(engine,instance,user,changed=[]))
            start_manual(engine,instance,user,changed)
        return {'contentOverrides':overrides,'changed':changed,'dataSources':await data_sources(engine,instance,user)}
    if body.get('operation')=='configure':
        from phemacast.core.composition import validate_reference
        path=body.get('reference');ref=reference_paths(instance['phema']).get(path)
        values=body.get('values')
        if not ref or not isinstance(values,dict) or set(values)-POLICY_FIELDS:raise HTTPException(422,'Select a reference and its refresh/failure settings.')
        try:validate_reference({**ref,**values})
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        from ..source_choice import FIELD
        kept=(instance.get('contentOverrides',{}).get(path) or {}).get(FIELD)  # Refresh settings keep the panel's chosen source.
        instance.setdefault('contentOverrides',{})[path]={**copy.deepcopy(values),**({FIELD:kept} if kept else {})}
        instance['sequence']+=1
        # Restart only the content scheduler, never financial datasets/streams.
        old=instance.get('_contentTask')
        if old:old.cancel();await asyncio.gather(old,return_exceptions=True)
        if instance.get('sourceMode')!='snapshot':instance['_contentTask']=asyncio.create_task(run_content(engine,instance,user,changed=[]))
        return {'reference':path,'values':values,'contentOverrides':instance['contentOverrides']}
    if body.get('operation') == 'capture':
        content=copy.deepcopy(instance.get('content',{}))
        paths,label=body.get('references'),body.get('label')
        if label is not None and (not isinstance(label,str) or not label.strip() or len(label)>120):raise HTTPException(422,'Use a short plain label.')
        if paths is not None:
            # Keep only the named results, and only the exact one the caller received.
            declared=reference_paths(instance['phema'])
            if not isinstance(paths,list) or not paths or len(paths)>32 or any(path not in declared for path in paths):raise HTTPException(422,'Select declared references to save.')
            results={path:(content.get('results') or {}).get(path) for path in paths}
            if any(not isinstance(result,dict) or result.get('status')!='ready' for result in results.values()):raise HTTPException(409,'Only a ready result can be saved.')
            receipt=body.get('receipt')
            if receipt is not None and any((result.get('value') or {}).get('channel_receipt')!=receipt for result in results.values()):
                raise HTTPException(409,'That result is no longer current. Save it again after it reloads.')
            content={**content,'results':results}
        value={**content,**({'references':paths} if paths is not None else {}),**({'label':label.strip()} if label is not None else {}), 'schema':'phemacast:resolved-composition/1', 'phemaId':instance['phema']['id'], 'contentHash':content_hash(instance['phema']), 'parameters':{k:v['default'] for k,v in instance['entry']['design']['params'].items() if 'default' in v}, 'financialResults':copy.deepcopy(instance['results']), 'status':engine.public(instance)['status'], 'panelTitles':panel_titles(instance), 'capturedAt':time.time()}
        return {'snapshotId':store.snapshot(user['id'],value)}
    if body.get('operation') == 'view':
        try:return store.read(user['id'],body.get('snapshotId'))
        except KeyError as exc:
            missing_snapshot('View Content',body.get('snapshotId'))
            raise HTTPException(404,'Snapshot unavailable.') from exc
    if body.get('sourceMode') == 'snapshot':
        return await restore_snapshot(engine,instance,user,body.get('snapshotId'))
    if body.get('sourceMode') == 'parameters':
        await engine.cancel(instance['id'])
        instance['sourceMode']='parameters';instance['generation']+=1
        engine.jobs[instance['id']]=asyncio.create_task(engine.run(instance,user))
        return {'status':'resolving','sourceMode':'parameters'}
    if instance.get('sourceMode')=='snapshot':raise HTTPException(409,'Switch to parameter mode before refreshing content.')
    refs = body.get('references')
    if not isinstance(refs,list) or not refs or any(r not in reference_paths(instance['phema']) for r in refs):raise HTTPException(422,'Select declared references to refresh.')
    interaction_inputs=None
    if body.get('operation')=='interact':
        from phemacast.core.schema_security import validate_async
        from jsonschema.exceptions import ValidationError
        from phemacast.core.composition import validate_reference
        if len(refs)!=1:raise HTTPException(422,'Interact with one component at a time.')
        reference=reference_paths(instance['phema'])[refs[0]]
        if not reference.get('interaction'):raise HTTPException(422,'Component does not accept interaction.')
        supplied=body.get('inputs')
        try:
            await validate_async(reference['interaction']['inputSchema'], supplied)
            validate_reference({**reference,'inputs':supplied})
        except (ValueError,ValidationError) as exc:raise HTTPException(422,'Interaction inputs do not match the component schema.') from exc
        interaction_inputs={refs[0]:supplied}
    key=start_manual(engine,instance,user,refs,interaction_inputs)
    # The run this ↻ started or joined; `manualRuns` names it for each of its panels once it has ended.
    return {'status':'resolving','references':list(key),'run':instance['_manualRunOf'][key]}


def start_manual(engine, instance, user, refs, interaction_inputs=None):
    """Re-read these panels as ↻ does, ↻-only ones too; returns the coalescing key (its run is `_manualRunOf[key]`)."""
    # Generation can outlive an HTTP/IPC request. Report progress over the
    # existing Browser stream and coalesce repeated clicks while work is active.
    active=instance.setdefault('_manualContentTasks', {})
    key=tuple(sorted(set(refs)))
    if interaction_inputs and key in active:raise HTTPException(409,'This component is still processing its previous interaction.')
    if key not in active:
        # Runs are numbered. When one ends, `manualRuns` says so for each of its panels, so the page can tell
        # the viewer that a ↻ it sent got no new data even though the Terminal accepted it.
        run=instance['_manualRun']=instance.get('_manualRun',0)+1
        instance.setdefault('_manualRunOf',{})[key]=run
        async def generate():
            try: await refresh_content(engine,instance,user,references=list(refs),trigger='manual',interaction_inputs=interaction_inputs)
            except asyncio.CancelledError: raise
            except Exception as exc:
                instance.setdefault('content', {})['error']=type(exc).__name__
                instance['sequence']+=1
            finally:
                active.pop(key,None)
                instance.setdefault('manualRuns',{}).update(dict.fromkeys(key,run))
                instance['sequence']+=1
        active[key]=asyncio.create_task(generate())
    return key


def panel_titles(instance):
    # Find layouts names a snapshot's panels as its layout did when it was saved.
    titles={}
    for layout in [instance.get('entry') or {},*(instance.get('nestedLayouts') or {}).values()]:
        for node in ((layout.get('design') or {}).get('layout') or {}).get('children',[]):
            if node.get('referenceId') and isinstance(node.get('title'),str):titles.setdefault(node['referenceId'],node['title'][:120])
    return titles


async def restore_snapshot(engine, instance, user, snapshot_id):
    store=ResolutionStore(engine.store.path.parent/'composition-results.sqlite')
    try: snapshot=store.read(user['id'], snapshot_id)
    except KeyError as exc:
        missing_snapshot('Apply', snapshot_id)
        raise HTTPException(404, 'Snapshot unavailable.') from exc
    if snapshot['phemaId'] != instance['phema']['id'] or snapshot['contentHash'] != content_hash(instance['phema']): raise HTTPException(409, 'Snapshot belongs to another Phema revision.')
    if 'financialResults' not in snapshot: raise HTTPException(422,'Select a complete Browser snapshot.')
    await engine.cancel(instance['id'])
    instance['results']=copy.deepcopy(snapshot['financialResults'])
    instance['status']=snapshot['status']
    instance['content']={**snapshot,'snapshotId':snapshot_id,'sourceMode':'snapshot'}
    instance['sourceMode']='snapshot';instance['sequence']+=1
    return instance['content']


async def data_sources(engine, instance, user):
    """The sources this viewer can choose for the Browser's data panels, and the current choice."""
    from ..source_choice import candidates, chosen
    refs = reference_paths(instance['phema']) if instance.get('phema') else {}
    network = getattr(engine, 'network', None)
    try:
        directory = await network.directory(user.get('token', '')) if refs and network else []
    except HTTPException:
        directory = []  # Sources are listed again when the Terminal can reach its directory.
    listing = candidates(directory, refs)
    return {**listing, 'chosen': chosen(instance.get('contentOverrides', {}), refs)}
