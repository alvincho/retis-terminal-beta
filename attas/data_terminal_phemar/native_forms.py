"""Opt-in generic mounted native Pulse transport; no application schemas/stores."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
from copy import deepcopy
from urllib.parse import quote
import asyncio
import json
import time
import os
import math
from fastapi.responses import StreamingResponse
from fastapi import HTTPException
from starlette.requests import Request
from phemacast.core.composition import composition,content_hash,reference_paths
from phemacast.agent_view.grants import VerifiedOperationGrantIssuer
from phemacast.agent_view.broker import ReceiptJournal
from phemacast.operation_forms_candidate.source_sdk import MountedPulseSourceSdk,protected_content_request
from phemacast.operation_forms_candidate.pulse_forms import EXTENSION,binding_of


def native_owner_timeout(environ):
    """Bound the generic native transport; keep the installed default."""
    value = float(environ.get('ATTAS_NATIVE_OWNER_TIMEOUT_SECONDS', '40'))
    if not math.isfinite(value) or not 1 <= value <= 240:
        raise ValueError('Native owner timeout must be between one and 240 seconds.')
    return value


class NativeForms:
    def __init__(self, principal, engine, network, store):
        self.auth,self.engine,self.network,self.store=principal,engine,network,store
        self.owner_timeout=native_owner_timeout(os.environ)
        # Each read performs issue, consume and final disclosure validation.
        # Bound the shared transport so opening several views cannot flood the
        # authority service with those dependent requests at once.
        self.read_limit=asyncio.Semaphore(3)
        self.marker=object()
        from .native_trust import NativeTrust
        self.trust=NativeTrust(network)
        engine.native_trust=self.trust
        engine.broker.native_trust=self.trust
        async def identity(request):
            user=await self.user(request)
            return {'actor':user['id'],'serviceActor':network.registration.agent_id,'window':user.get('terminalWindow') or 'default'}
        async def protected(request,context):
            if request.scope.get('_native_marker') is self.marker:return True
            if request.method not in ('GET','HEAD'):
                if request.headers.get('origin','').rstrip('/')!=str(request.base_url).rstrip('/'):return False
                if 'application/json' not in request.headers.get('content-type',''):return False
                if len(await request.body())>300000:return False
            return True
        async def mounted(context,mount):
            request=self.sdk.request_scope.get();user=await self.user(request)
            child=engine.instances.get(mount)
            if not child or child.closed or not engine.belongs(child,user):raise PermissionError('Current window mount unavailable.')
            child.user.update(user)
            snapshot=await self.snapshot(child,mount,user)
            return {**context,'mount':mount,'generation':snapshot['generation'],'active':True,
                    'phema':snapshot['phema'],'sourceMode':snapshot['sourceMode']}
        async def authorize(context,mount,release,ref,resource):
            request=self.sdk.request_scope.get();user=await self.user(request)
            child=engine.instances.get(mount)
            if child:await self.trust.apply(user,child.phema,force=True)
            if not child or not engine.belongs(child,user):return False
            snapshot=await self.snapshot(child,mount,user)
            if content_hash(snapshot['phema'])!=release or not any(r==ref for r in reference_paths(snapshot['phema']).values()):return False
            from .phema_security import check,effect
            rule=check(store,user,snapshot['phema'],snapshot['phema'],ref,{'trigger':'manual'})
            expected='action' if any(resource['effects'].values()) else 'read'
            return effect(ref)==expected and rule['effect']==expected and rule.get('maxPoints',0)==0
        async def resolve(context,ref):
            headers=await self.headers()
            r=await network.client.get(network.plaza+'/api/resources/'+quote(ref['id'],safe='')+'/'+ref['version'],params={'digest':ref['digest']},headers=headers);r.raise_for_status()
            value=r.json();r=await network.client.post(network.plaza+'/api/operation-contracts/resolve',json={'ref':ref},headers=headers);r.raise_for_status()
            return {'ref':value['ref'],'resource':value['resource'],'verification':r.json()['verification']}
        async def issue(context,**kwargs):
            outer=self
            class Plaza:
                async def post(self,path,**options):return await network.client.post(network.plaza+path,**options)
            async def current(actor):
                user=await outer.user(outer.sdk.request_scope.get())
                if actor!=user['id']:raise PermissionError('Original user changed.')
                return actor,{'Authorization':'Bearer '+user['token']}
            # A Terminal approved moments ago may not have registered yet: register now, as network.py does on a 401.
            if not network.registration._token and callable(getattr(network.registration,'register_once',None)):
                await network.registration.register_once()
            issuer=VerifiedOperationGrantIssuer(Plaza(),user_context=current,service_headers=lambda:{'X-Agent-Token':network.registration._token},service_actor_id=network.registration.agent_id)
            return await issuer(**kwargs)
        async def transport(context,resource,wire,grant):
            user=await self.user(self.sdk.request_scope.get());pub=resource['publisher']['agentId']
            headers={'Authorization':'Bearer '+user['token']}
            r=await network.client.get(network.plaza+'/api/directory/entries',params={'q':pub,'limit':500},headers=headers);r.raise_for_status()
            rows=[x for x in r.json()['entries'] if x['agent_id']==pub]
            if len(rows)!=1:raise PermissionError('Current publisher registration unavailable.')
            target={'id':pub,'address':rows[0]['address'].rstrip('/')}
            await network.require_trusted(target, user['token'])
            if wire['method']!='POST' or wire['path']!='/use_practice/get_pulse_data' or wire['query']:raise PermissionError('Exact native Pulse transport required.')
            r=await network.client.post(target['address']+wire['path'],headers={'Authorization':'Bearer '+grant['token']},json={**deepcopy(wire['body']),'caller_plaza_token':grant['token']},follow_redirects=False,timeout=self.owner_timeout);r.raise_for_status()
            value=r.json()
            if value.get('status')!='ok' or value.get('practice_id')!='get_pulse_data' or not isinstance(value.get('result'),dict):raise PermissionError('Native owner response envelope unavailable.')
            return value['result']
        async def writes(context,mount,release,ref,resource):
            effects=resource['effects']
            allowed={'writesState','startsJob'} if os.environ.get('ATTAS_NATIVE_JOB_FORMS')=='1' else {'writesState'}
            return (effects['writesState'] is True and not any(v for k,v in effects.items() if k not in allowed)
                    and resource['constraints']['expectedRevision'] is True and bool(ref.get('interaction'))
                    and await authorize(context,mount,release,ref,resource))
        async def jobs(context,mount,release,ref,resource):
            return (os.environ.get('ATTAS_NATIVE_JOB_FORMS')=='1' and resource['effects']['startsJob'] is True
                    and not any(resource['effects'][k] for k in ('externalSend','paidExecution','opensStream'))
                    and resource['constraints']['idempotency']=='required' and bool(ref.get('interaction'))
                    and await authorize(context,mount,release,ref,resource))
        self.sdk=MountedPulseSourceSdk(principal=identity,verify_request=protected,read_mount=mounted,
            authorize=authorize,resolver=resolve,issuer=issue,transport=transport,
            journal=ReceiptJournal(store.path.parent/'native-operation-receipts'),write_ceiling=writes,
            job_ceiling=jobs if os.environ.get('ATTAS_NATIVE_JOB_FORMS')=='1' else None)

    def release(self,child):
        """Free the protected session of a Browser that has closed."""
        mount=(child.snapshot or {}).get('id')
        if mount:
            # Same user and window identity() gives the session when it opens.
            self.sdk.close(actor=child.user.get('id'),window=child.user.get('terminalWindow') or 'default',mount=mount)

    async def snapshot(self,child,mount,user):
        return await child.call({'operation':'native-snapshot','method':'GET','mount':mount,
            'user':user,'registration':self.engine.authority()})

    async def user(self,request):
        if request is None:raise PermissionError('Current request required.')
        cached=request.scope.get('_native_user_cache')
        if cached and cached[0]>time.monotonic():return deepcopy(cached[1])
        if request.scope.get('_native_marker') is self.marker:
            child=request.scope['_native_child']
            if child.closed or child not in self.engine.instances.values():raise PermissionError('Browser closed.')
            user=deepcopy(child.user)
        else:user=await self.auth(request)
        # Authenticate the original account once per bounded SDK request.
        # Current trust is rechecked at each authorization boundary; Source
        # grants and ACLs revalidate the session before protected dispatch.
        r=await self.network.client.get(self.network.plaza+'/api/ui_auth/me',headers={'Authorization':'Bearer '+user['token']},timeout=20);r.raise_for_status()
        if r.json().get('user',r.json()).get('id')!=user['id']:raise PermissionError('Original user no longer current.')
        child=request.scope.get('_native_child') or self.engine.instances.get(request.url.path.split('/api/instances/',1)[-1].split('/')[0])
        if child:await self.trust.apply(user,child.phema)
        request.scope['_native_user_cache']=(time.monotonic()+60,deepcopy(user))
        return user

    async def headers(self):
        user=await self.user(self.sdk.request_scope.get())
        return {'Authorization':'Bearer '+user['token']}

    async def read_reference(self,child,ref,inputs):
        async with self.read_limit:
            return await self._read_reference(child,ref,inputs)

    async def _read_reference(self,child,ref,inputs):
        # Background resolution only reads. Interaction always uses the exact
        # HTTP prepare/invoke/observe path; no legacy or worker write fallback.
        paths=reference_paths(child.phema)
        selected=[path for path,r in paths.items() if r==ref]
        if len(selected)!=1 or ref.get('interaction') or inputs!=ref.get('inputs',{}):raise PermissionError('Only exact mounted noninteractive reads can resolve in a worker.')
        request=Request({'type':'http','method':'GET','headers':[], '_native_marker':self.marker,'_native_child':child})
        result=await self.sdk.handle(request,child.snapshot['id'],{'references':selected})
        ack=result['results'][selected[0]]
        if ack['state']!='complete':raise PermissionError('Read outcome is not complete.')
        return {'value':ack['value'],'provenance':{'agentId':ref['target']['agentId'],'operationRef':binding_of(ref)['operationRef']}}

    async def route(self,original,rest,request,user):
        parts=rest.strip('/').split('/')
        mount=parts[0] if parts and parts[0] else None
        child=self.engine.instances.get(mount)
        if child and not self.engine.belongs(child,user):raise HTTPException(404,'Browser unavailable in this window.')
        if child and request.method=='GET':
            # The worker's public snapshot checks the short-lived trust proof.
            # Renew it before that disclosure, not after redacted content has
            # already been produced while adding the transport capability.
            user=await self.user(request)
        if child:child.user.update(user)
        if child and parts[1:]==['content'] and request.method=='POST':
            body=await request.json()
            if protected_content_request(child.phema,body):
                result=await self.sdk.handle(request,mount,body)
                value=result.get('value') if result.get('state')=='complete' else result.get('receipt') if body.get('operation')=='observe-interaction' else None
                if value is not None:
                    await child.call({'operation':'native-content','method':'POST','mount':mount,'user':user,
                        'registration':self.engine.authority(),'body':{'generation':child.snapshot['generation'],
                        'phemaDigest':content_hash(child.phema),'reference':body['references'][0],'value':value}})
                return result
        result=await original(rest,request,user)
        if isinstance(result,StreamingResponse) and child and parts[1:]==['stream']:
            iterator=result.body_iterator
            async def marked():
                async for packet in iterator:
                    text=packet.decode() if isinstance(packet,bytes) else packet
                    if text.startswith('event: update\ndata: '):
                        snapshot=json.loads(text.split('data: ',1)[1].strip())
                        if snapshot.get('sourceMode')=='parameters' and snapshot.get('phema'):
                            if any(EXTENSION in r.get('extensions',{}) for r in reference_paths(snapshot['phema']).values()):
                                snapshot.setdefault('content',{}).update(await self.sdk.describe(request,mount))
                                text='event: update\ndata: '+json.dumps(snapshot,allow_nan=False)+'\n\n'
                    yield text
            result.body_iterator=marked()
        if isinstance(result,dict) and result.get('phema') and result.get('sourceMode')=='parameters':
            if any(EXTENSION in r.get('extensions',{}) for r in reference_paths(result['phema']).values()):
                result.setdefault('content',{}).update(await self.sdk.describe(request,result['id']))
        return result


def mount_native_forms(principal, engine, network, store):
    gateway=NativeForms(principal,engine,network,store)
    original=engine.route
    async def route(rest,request,user):
        try:return await gateway.route(original,rest,request,user)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    engine.route=route
    engine.broker.native_forms=gateway
    return gateway
