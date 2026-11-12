"""Generic container: Plaza-trusted publishers, scoped calls and a confined VM.

Manifest locations come from current Plaza registrations, never a Phema URL.
Application schemas/state remain in publishers.
"""
import asyncio,json,time,secrets
from pathlib import Path
from fastapi import HTTPException,Request
from fastapi.responses import Response,HTMLResponse
import httpx
from prompits.core.capabilities import digest
from .broker import ReceiptJournal,pointer
from .contracts import CONFINED_CAPABILITY,manifest_digest
from .host_releases import HostReleases
from .host import AgentViewHost,mount_agent_view_routes
from .grants import VerifiedOperationGrantIssuer
from .transport import PinnedPublisherTransport
from .trust import trusted_interface_approval


def mount_container(app,*,principal,network,config_path,state_dir,window_context):
    hosts=HostReleases(config_path)
    current={};records={}
    advertisement={'schema':'capability-advertisement/1','capabilities':[CONFINED_CAPABILITY]}
    async def refresh(context,refs):
        async def read(ref):
            headers={'Authorization':'Bearer '+context['user']['token']}
            row=await network.client.get(network.plaza+'/api/resources/'+ref['id']+'/'+ref['version'],params={'digest':ref['digest']},headers=headers,timeout=10)
            row.raise_for_status()
            resolved=await network.client.post(network.plaza+'/api/operation-contracts/resolve',headers=headers,json={'ref':ref,'sourceMode':'parameters','grantedScopes':[]},timeout=10)
            resolved.raise_for_status();value=row.json();value['verification']=resolved.json()['verification'];return digest(ref),value
        for key,value in await asyncio.gather(*(read(ref) for ref in refs)):records[(context['actor'],key)]=value
    async def context_for(request):
        user=await principal(request);window=window_context(request)
        if not window or user.get('authentication')=='scoped-call':raise HTTPException(403,'A current browser window session is required.')
        context={'actor':user['id'],'window':window,'user':user};current[user['id']]=context
        # Revalidate selected published resources on every closed interaction.
        parts=request.url.path.split('/')
        if len(parts)>4 and parts[3]=='mounts':
            mount=host.mounts.get(parts[4])
            if mount and (mount['actor'],mount['window'])==(user['id'],window):
                body=await request.json();bindings=mount['session'].manifest['operations'];operation=body.get('operationId')
                refs=[entry['ref'] for entry in bindings if not operation or entry['id']==operation]
                await refresh(context,refs)
        return context
    async def csrf(request,context):
        expected=str(request.base_url).rstrip('/')
        origin=request.headers.get('origin');referer=request.headers.get('referer','')
        return request.headers.get('x-phemacast-view-request')=='1' and request.headers.get('x-terminal-window')==context['window'] and (origin==expected or not origin and referer.startswith(expected+'/'))
    async def load_view(context,value):
        runtime=hosts.runtime(value.get('hostRelease'))
        configured=json.loads(Path(config_path).read_text()).get('views',[])
        peers=await network.directory(context['user']['token'],force=True);peer=next((row for row in peers if row['id']==value['publisherAgentId']),None)
        if not peer:raise PermissionError('Current publisher registration is unavailable.')
        await network.require_trusted(peer, context['user']['token'])
        declared=peer.get('meta',{}).get('phemacast_agent_views',{}).get('views',[])
        if value['manifestDigest']=='current':
            # A Phema that follows its publisher mounts the one release advertised now.
            matches=[row for row in declared if row.get('viewId')==value['viewId']]
            if len(matches)!=1 or not isinstance(matches[0].get('manifestDigest'),str):raise PermissionError('Current publisher does not advertise this view.')
            value={**value,'manifestDigest':matches[0]['manifestDigest']}
        else:
            matches=[row for row in declared if (row.get('viewId'),row.get('manifestDigest'))==(value['viewId'],value['manifestDigest'])]
        if len(matches)!=1:raise PermissionError('Current publisher does not advertise this exact view manifest.')
        previous=next((row for row in configured if (row['agentId'],row['viewId'])==(peer['id'],value['viewId'])),{})
        is_successor=bool(previous and previous['manifestDigest']!=value['manifestDigest'])
        # Old per-view entries may preserve interface review preferences. They
        # never grant a connection or override current registration/advertisement.
        entry={**previous,'agentId':peer['id'],'viewId':value['viewId'],'origin':peer['address'],
               'manifestDigest':value['manifestDigest'],'manifestPath':matches[0]['manifestPath']}
        if is_successor:entry.pop('deploymentApproval',None)
        transport=PinnedPublisherTransport(peer['id'],peer['address'],client=network.client,allow_local=True)
        response=await network.client.get(transport.target(entry['manifestPath']),timeout=10,follow_redirects=False);response.raise_for_status()
        if len(response.content)>512000:raise PermissionError('Manifest exceeds its limit.')
        manifest=response.json()
        if manifest_digest(manifest)!=value['manifestDigest']:raise PermissionError('Exact accepted owner manifest changed.')
        if manifest['dependencies']:
            compiler=manifest.get('extensions',{}).get('phemacast:dependencyAdmission',{}).get('pulse')
            names={row.get('name') or row.get('pulse_name') for row in peer.get('meta',{}).get('supported_pulses',[])}
            if compiler not in names:raise PermissionError('Publisher dependency admission is not advertised.')
        await refresh(context,[binding['ref'] for binding in [*manifest['operations'],*manifest['dependencies']]])
        async def invoke(accepted_origin, wire, grant):
            # Recheck trust and the pinned identity/address for every operation,
            # including an already mounted view after trust has been revoked.
            await network.require_trusted(peer, current[context['actor']]['user']['token'])
            return await transport.invoke(accepted_origin, wire, grant)
        plan={'manifest':manifest,'assets':await transport.assets(manifest),'allowLocal':True,'transport':invoke,
            'runtime':runtime.reference(),'createEgressGuard':runtime.guard,'resourceRefs':[],'maxPoints':0,'title':entry.get('title',entry['viewId'])}
        if value.get('hostRelease'):plan['hostRelease']=value['hostRelease']
        interface=trusted_interface_approval(entry,peer,context,manifest,successor=is_successor)
        approved=entry.get('deploymentApproval',{})
        if interface is not None:
            plan['deploymentApproval']=interface
            plan['renewableInterface']=True
        elif (approved.get('actor')==context['actor'] and approved.get('window')==context['window']
                and approved.get('parameterDigest')==digest(value['parameters']) and approved.get('sourceMode')==value['sourceMode']
                and approved.get('expiresAt',0)>time.time()
                and all(not any(resolver(context,binding['ref'])['resource']['effects'].values()) for binding in manifest['operations'])):
            plan['deploymentApproval']={key:True for key in ('codeExecution','privateData','localNetwork')}
        return plan
    def resolver(context,ref):
        row=records.get((context['actor'],digest(ref)))
        if row is None:raise PermissionError('Fresh registry resolution is required.')
        return row
    def authorize(context,actor,entry,resource,value):
        # This is an additional review-scope fence. The real current owner ACL
        # runs at verified Plaza grant issuance and again at owner consumption.
        if actor!=context['actor']:return False
        bindings={**context['reviewManifest'].get('extensions',{}).get('phemacast:parameterBindings',{}),**entry.get('extensions',{}).get('phemacast:parameterBindings',{})}
        return all(pointer(value,path)==context['reviewParameters'][key] for path,key in bindings.items())
    async def dependency_admission(value):
        context=current[value['actor']]
        peers=await network.directory(context['user']['token'],force=True)
        peer=next((row for row in peers if row['id']==value['publisherAgentId']),None)
        if not peer:raise PermissionError('Dependency compiler publisher unavailable.')
        # Locate the exact admitted mount. Never take a compiler target or pulse
        # from arbitrary frame input, and never grant its declared effects here.
        session=next((mount['session'] for mount in host.mounts.values() if mount['actor']==value['actor']
            and mount['session'].digest==value['manifestDigest']),None)
        if session is None:raise PermissionError('Exact current owner mount required for dependency compilation.')
        pulse=session.manifest.get('extensions',{}).get('phemacast:dependencyAdmission',{}).get('pulse')
        names={row.get('name') or row.get('pulse_name') for row in peer.get('meta',{}).get('supported_pulses',[])}
        if pulse not in names:raise PermissionError('Current publisher no longer advertises its compiler.')
        return await network.pulse(peer,pulse,value,context['user']['token'])
    issuer=VerifiedOperationGrantIssuer(httpx.AsyncClient(base_url=network.plaza,timeout=15,trust_env=False),
        user_context=lambda actor:(actor,{'Authorization':'Bearer '+current[actor]['user']['token']}),
        service_headers=lambda:{'X-Agent-Token':network.registration._token},service_actor_id=network.registration.agent_id,dependency_admission=dependency_admission)
    async def issue(**kwargs):
        # A container approved moments ago may not have registered with Plaza yet: register before asking for a grant.
        if not network.registration._token and callable(getattr(network.registration,'register_once',None)):
            await network.registration.register_once()
        issuer.service_actor_id=network.registration.agent_id
        return await issuer(**kwargs)
    host=AgentViewHost(principal=context_for,verify_csrf=csrf,recent_action=csrf,load_view=load_view,resolver=resolver,authorize=authorize,
        issue_grant=issue,journal=ReceiptJournal(Path(state_dir)/'agent-view-receipts'),advertisement=advertisement,approval_seconds=3600,max_mounts=16)
    mount_agent_view_routes(app,host)
    @app.post('/api/agent-views/host-release')
    async def host_release(request:Request):
        await host.context(request)
        try:return Response(json.dumps(hosts.reference()),media_type='application/json',headers={'Cache-Control':'no-store'})
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except (ValueError,KeyError,OSError) as exc:raise HTTPException(409,'The approved generic host release is unavailable.') from exc
    @app.get('/api/agent-view-host-releases/{identity}/host.mjs')
    async def host_module(identity:str):
        try:code=hosts.module(identity)
        except (PermissionError,ValueError,KeyError,OSError) as exc:raise HTTPException(404,'Approved host module unavailable.') from exc
        return Response(code,media_type='text/javascript',headers={'Cache-Control':'public,max-age=31536000,immutable','X-Content-Type-Options':'nosniff'})
    @app.get('/api/agent-view-host-releases/{identity}/{name}.css')
    async def host_style(identity:str,name:str):
        try:style=hosts.style(identity,name+'.css')
        except (PermissionError,ValueError,KeyError,OSError) as exc:raise HTTPException(404,'Approved host style unavailable.') from exc
        return Response(style,media_type='text/css',headers={'Cache-Control':'public,max-age=31536000,immutable','X-Content-Type-Options':'nosniff'})
    @app.get('/api/agent-view-runtime/{identity}/frame.js')
    async def frame_runtime(identity:str):
        try:runtime=hosts.runtime_by_digest(identity)
        except (PermissionError,ValueError,KeyError,OSError) as exc:raise HTTPException(404) from exc
        return Response(runtime.read(),media_type='application/javascript',headers={'Cache-Control':'public,max-age=31536000,immutable','X-Content-Type-Options':'nosniff'})
    @app.get('/api/agent-view-runtime/{identity}/frame')
    async def frame_document(identity:str):
        try:runtime=hosts.runtime_by_digest(identity)
        except (PermissionError,ValueError,KeyError,OSError) as exc:raise HTTPException(404) from exc
        runtime.verify()
        nonce=secrets.token_hex(20)
        body='<!doctype html><meta charset="utf-8"><main></main><script nonce="'+nonce+'" src="/api/agent-view-runtime/'+identity+'/frame.js"></script>'
        return HTMLResponse(body,headers={'Content-Security-Policy':"sandbox allow-scripts allow-forms; default-src 'none'; script-src 'nonce-"+nonce+"' 'wasm-unsafe-eval'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",'Referrer-Policy':'no-referrer'})
    app.state.agent_view_host=host;app.state.agent_view_capability=CONFINED_CAPABILITY;app.state.agent_view_host_releases=hosts
    return host
