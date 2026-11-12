"""Server-owned workspaces, canonical Phema storage and legacy-device imports."""
import copy
import json
import secrets
from fastapi import HTTPException
from prompits.core.capabilities import digest
from .contracts import layout_operation
from .phema_transfer import from_entry,to_entry

KIND='workspace-state'


def project(document):
    result=copy.deepcopy(document)
    for workspace in result.get('workspaces',[]):
        for browser in workspace.get('browsers',[]):
            if browser.get('phema'):
                browser['customLayout']=to_entry(browser.pop('phema'))
    return result


async def canonical_document(document):
    if not isinstance(document,dict) or not isinstance(document.get('workspaces'),list) or not 1<=len(document['workspaces'])<=32:
        raise HTTPException(422,'Provide 1–32 workspaces.')
    if any(not isinstance(w,dict) or not isinstance(w.get('browsers'),list) or len(w['browsers'])>12 for w in document['workspaces']):
        raise HTTPException(422,'Each workspace supports up to 12 Browsers.')
    normalized=(await layout_operation('workspace',document=project(document)))['document']
    originals={w.get('id'):w for w in document['workspaces']}
    for workspace in normalized['workspaces']:
        for index,browser in enumerate(workspace['browsers']):
            if browser.get('customLayout'):
                entry=browser.pop('customLayout')
                candidates=originals.get(workspace['id'],{}).get('browsers',[])
                existing=candidates[index].get('phema') if index<len(candidates) else None
                # Preserve immutable snapshot/publication content on handoff.
                # Legacy layout imports become new authored secured revisions.
                browser['phema']=copy.deepcopy(existing) if existing and to_entry(existing)==entry else from_entry(entry, secure=True)
                to_entry(browser['phema'])  # Media/reference validation, no fetch.
    return normalized


def read(store, owner, key='default'):
    try:
        record=store.get(owner,KIND,key)
        return {**record,'document':project(record['document'])}
    except HTTPException as exc:
        if exc.status_code!=404:raise
        return {'revision':0,'document':None,'migrations':[]}


def initialize_window(store,owner,key):
    current=read(store,owner,key)
    if not key.startswith('window.') or current['document']:return current
    # Preserve the pre-isolation desk in the first window. Other windows start
    # independently and can explicitly load a copy from the saved library.
    if any(r['id'].startswith('window.') for r in store.list(owner,KIND)):return current
    legacy=read(store,owner)
    if not legacy['document']:return current
    try:
        store.put(owner,KIND,key,{'document':canonical_copy(store,owner),
                                'migrations':['window-isolation/1']},0)
    except HTTPException as exc:
        if exc.status_code!=409:raise
    return read(store,owner,key)


def canonical_copy(store,owner):
    return copy.deepcopy(store.get(owner,KIND,'default')['document'])


async def save(store,owner,body,key='default'):
    if not isinstance(body,dict) or type(body.get('revision')) is not int:raise HTTPException(422,'Expected workspace revision required.')
    document=await canonical_document(body.get('document'))
    current=read(store,owner,key)
    try:
        return store.put(owner,KIND,key,{'document':document,'migrations':current.get('migrations',[])},body['revision'])
    except HTTPException as exc:
        if exc.status_code!=409:raise
        id=secrets.token_hex(16)
        store.put(owner,'workspace-conflict',id,{'document':document,'baseRevision':body['revision'],'windowKey':key})
        raise HTTPException(409,{'message':'Workspace changed in another session. Your draft is saved on the server.','conflictId':id}) from exc


async def migrate(store,owner,body,state_key='default'):
    if not isinstance(body,dict):raise HTTPException(422,'Provide a workspace document.')
    document=await canonical_document(body.get('document'))
    key=digest(document)
    current=read(store,owner,state_key)
    if key in current.get('migrations',[]):return current
    combined=await canonical_document(current['document']) if current['document'] else copy.deepcopy(document)
    if current['document']:
        for workspace in document['workspaces']:
            existing=next((w for w in combined['workspaces'] if w['id']==workspace['id']),None)
            if existing==workspace:continue
            copy_workspace=copy.deepcopy(workspace)
            if existing:copy_workspace.update(id=secrets.token_hex(12),name=(workspace['name']+' · imported')[:60])
            combined['workspaces'].append(copy_workspace)
        if len(combined['workspaces'])>32:raise HTTPException(409,'Import exceeds 32 workspaces. Legacy state is preserved; remove an unused workspace before retrying.')
    store.put(owner,'workspace-import',key,{'document':document})
    saved=store.put(owner,KIND,state_key,{'document':combined,'migrations':[*current.get('migrations',[]),key]},current['revision'])
    return {**saved,'document':project(saved['document'])}


def rollback_workspace(store, owner, previous_revision, expected_revision, key='default'):
    """Restore a saved workspace revision without applying application policy."""
    previous = store.get(owner, 'workspace-revision', key+':'+str(previous_revision))
    restored = {k: v for k, v in previous.items() if k not in ('id', 'revision', 'updatedAt')}
    return store.put(owner, 'workspace-state', key, {**restored, 'restoredRevision': previous_revision}, expected_revision)
