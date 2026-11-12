"""Opt-in generic mount/review routes; importing this never installs them."""
import asyncio
import inspect
from copy import deepcopy
import secrets,re
import time

from fastapi import HTTPException, Request

from prompits.core.capabilities import digest, negotiate
from .broker import AgentViewSession
from .contracts import check, exact, manifest_digest, resolve_contracts, validate_manifest, verify_assets


async def result(value):
    return await value if inspect.isawaitable(value) else value


class AgentViewHost:
    """Required host adapters supply identity, CSRF, origin, ACL and transport.

    This owns discardable mount/review metadata only. Durable application data,
    preferences, snapshots and jobs remain in their existing owning backends.
    All approvals expire and bind original user/window/parameters/release.
    """
    def __init__(self, *, principal, verify_csrf, recent_action, load_view,
                 resolver, authorize, issue_grant, journal, advertisement,
                 approval_seconds=600, max_mounts=8):
        self.principal,self.verify_csrf,self.recent_action=principal,verify_csrf,recent_action
        self.load_view,self.resolver,self.authorize=load_view,resolver,authorize
        self.issue_grant,self.journal,self.advertisement=issue_grant,journal,advertisement
        self.approval_seconds,self.max_mounts=approval_seconds,max_mounts
        self.reviews={};self.mounts={}

    async def context(self,request):
        context=await result(self.principal(request))
        if not isinstance(context,dict) or not context.get('actor') or not context.get('window'):
            raise HTTPException(401,'A verified host user and window are required.')
        if request.method!='GET' and not await result(self.verify_csrf(request,context)):
            raise HTTPException(403,'Host session/CSRF check failed.')
        return context

    def prune(self):
        for identity,review in list(self.reviews.items()):
            if review['expiresAt']<=time.time():del self.reviews[identity]
        for identity,mount in list(self.mounts.items()):
            if mount['session'].approval['expiresAt']<=time.time():
                mount['session'].close()
                # A trusted interface keeps only its bounded mount metadata.
                # It receives no further authority until current trust, assets,
                # registry resources and the original window are checked again.
                if not mount.get('renewal'):del self.mounts[identity]

    async def review(self,context,value):
        self.prune()
        exact(value,['publisherAgentId','viewId','manifestDigest','parameters','sourceMode'],['hostRelease'])
        if 'hostRelease' in value and (not isinstance(value['hostRelease'],str) or not re.fullmatch('[a-f0-9]{64}',value['hostRelease'])):
            raise ValueError('Pin one approved generic host release.')
        if value['sourceMode'] not in ('parameters','snapshot'):raise ValueError('Select parameters or snapshot.')
        plan=await result(self.load_view(context,value))
        if value.get('hostRelease')!=plan.get('hostRelease'):raise PermissionError('Reviewed generic host release differs.')
        manifest=validate_manifest(plan['manifest'],allow_local=plan.get('allowLocal') is True)
        if value['manifestDigest']=='current':
            # Follows its publisher: review and pin the release that publisher advertises now.
            value={**value,'manifestDigest':manifest_digest(manifest)}
        if (manifest_digest(manifest)!=value['manifestDigest'] or manifest['viewId']!=value['viewId']
                or manifest['publisher']['agentId']!=value['publisherAgentId']):
            raise PermissionError('Accepted publisher view differs from the requested exact release.')
        if not negotiate(manifest['requirements'],self.advertisement)['compatible']:
            raise ValueError('Required client capability is unsupported.')
        verify_assets(manifest,plan['assets'])
        contracts=resolve_contracts(manifest,lambda ref:self.resolver(context,ref))
        parameters=check(manifest['parameterSchema'],value['parameters'])
        identity=secrets.token_urlsafe(24)
        material={'manifestDigest':value['manifestDigest'],'parameterDigest':digest(parameters),
            'sourceMode':value['sourceMode'],'resourceRefs':plan.get('resourceRefs',[]),'maxPoints':plan.get('maxPoints',0)}
        if plan.get('hostRelease'):
            material.update(hostRelease=plan['hostRelease'],runtimeDigest=plan['runtime']['digest'])
        review={'actor':context['actor'],'window':context['window'],'plan':plan,'manifest':manifest,
            'parameters':parameters,'material':material,'reviewDigest':digest(material),
            'expiresAt':time.time()+self.approval_seconds}
        # Pending reviews are also bounded; they never load active owner code.
        if sum(r['actor']==context['actor'] and r['window']==context['window'] for r in self.reviews.values())>=self.max_mounts:
            raise PermissionError('Close or finish another pending view review.')
        self.reviews[identity]=review
        return {'reviewId':identity,'reviewDigest':review['reviewDigest'],'manifestDigest':value['manifestDigest'],
            'publisher':manifest['publisher'],'viewId':manifest['viewId'],'origin':manifest['origin'],'release':manifest['release'],
            'disclosure':manifest['disclosure'],'sourceMode':value['sourceMode'],'maxPoints':material['maxPoints'],
            'operations':[{'id':entry['id'],'effects':contracts[entry['id']]['effects'],
                'classification':entry['classification'],'ref':entry['ref']} for entry in manifest['operations']],
            **({'deploymentApproval':plan['deploymentApproval']} if plan.get('deploymentApproval') else {})}

    async def approve(self,request,context,identity,value):
        self.prune();exact(value,['reviewDigest','codeExecution','privateData','localNetwork'])
        review=self.reviews.get(identity)
        if not review or (review['actor'],review['window'])!=(context['actor'],context['window']):
            raise PermissionError('Review unavailable for this user/window.')
        if value['reviewDigest']!=review['reviewDigest'] or not await result(self.recent_action(request,context)):
            raise PermissionError('Exact reviewed input and a recent explicit host action are required.')
        if any(type(value[key]) is not bool for key in ('codeExecution','privateData','localNetwork')):
            raise ValueError('Provide separate disclosure choices.')
        manifest,plan=review['manifest'],review['plan'];publisher=manifest['publisher']
        view=(publisher.get('agentId') if isinstance(publisher,dict) else publisher,manifest['viewId'])
        mine=[key for key,m in self.mounts.items() if m['actor']==context['actor'] and m['window']==context['window']]
        if len(mine)>=self.max_mounts:
            # A reloaded page never closes the mounts it had, so the window's limit makes room instead of refusing:
            # an expired mount first, then this view's oldest, then the window's oldest.
            expired=[key for key in mine if self.mounts[key]['session'].approval['expiresAt']<=time.time()]
            victim=(expired or [key for key in mine if self.mounts[key].get('view')==view] or mine)[0]
            self.mounts.pop(victim)['session'].close()
        mount_id=secrets.token_urlsafe(24)
        session=self.create_session(context,review,mount_id,value)
        renewal=deepcopy(value) if plan.get('renewableInterface') is True else None
        self.mounts[mount_id]={'actor':context['actor'],'window':context['window'],'session':session,'view':view,
            'renewal':renewal,'lock':asyncio.Lock(),'hostRelease':plan.get('hostRelease')}
        del self.reviews[identity]
        return {'mountId':mount_id,'manifestDigest':session.digest,'title':plan.get('title',manifest['viewId']),
            'html':manifest['html'],'parameters':review['parameters'],'sourceMode':session.source_mode,
            'assets':{key:{'text':data.decode(),'digest':manifest['assets'][key]['digest']} for key,data in plan['assets'].items()},
            **({'runtime':plan['runtime']} if plan.get('runtime') else {}),
            **({'hostRelease':plan['hostRelease']} if plan.get('hostRelease') else {})}

    def create_session(self,context,review,mount_id,value):
        manifest,plan=review['manifest'],review['plan']
        approval={'actor':context['actor'],'mountId':mount_id,'manifestDigest':manifest_digest(manifest),
            'expiresAt':review['expiresAt'],'resourceRefs':review['material']['resourceRefs'],
            'maxPoints':review['material']['maxPoints'],**{key:value[key] for key in ('codeExecution','privateData','localNetwork')}}
        guard=plan.get('egressGuard')
        if callable(plan.get('createEgressGuard')):
            guard=plan['createEgressGuard'](actor=context['actor'],mount_id=mount_id,manifest_digest=approval['manifestDigest'],
                publisher_origin=manifest['origin'],expires_at=review['expiresAt'])
        session=AgentViewSession(manifest,actor=context['actor'],mount_id=mount_id,approved_digest=approval['manifestDigest'],
            assets=plan['assets'],resolver=lambda ref:self.resolver(context,ref),
            authorize=lambda *args:self.authorize({**context,'reviewParameters':review['parameters'],'reviewManifest':manifest},*args),issue_grant=self.issue_grant,
            transport=plan['transport'],journal=self.journal,advertisement=self.advertisement,
            parameters=review['parameters'],source_mode=review['material']['sourceMode'],
            allow_local=plan.get('allowLocal') is True,egress_guard=guard,approval=approval)
        return session

    async def renew_interface(self,context,identity,mount):
        async with mount['lock']:
            previous=mount['session']
            if previous.approval['expiresAt']>time.time():return
            manifest=previous.manifest
            reference={'publisherAgentId':manifest['publisher']['agentId'],'viewId':manifest['viewId'],
                'manifestDigest':previous.digest,'parameters':previous.parameters,'sourceMode':previous.source_mode,
                **({'hostRelease':mount['hostRelease']} if mount.get('hostRelease') else {})}
            reviewed=await self.review(context,reference)
            review=self.reviews.pop(reviewed['reviewId'])
            choices={key:mount['renewal'][key] for key in ('codeExecution','privateData','localNetwork')}
            if review['plan'].get('renewableInterface') is not True or review['plan'].get('deploymentApproval')!=choices:
                raise PermissionError('Trusted interface access changed. Reload this Phema to reconnect.')
            if self.mounts.get(identity) is not mount:
                raise PermissionError('View was closed while reconnecting.')
            replacement=self.create_session(context,review,identity,choices)
            previous.close();mount['session']=replacement

    async def interact(self,request,context,identity,operation,value):
        self.prune();mount=self.mounts.get(identity)
        if not mount or (mount['actor'],mount['window'])!=(context['actor'],context['window']):
            raise PermissionError('Mounted view unavailable for this user/window.')
        if mount['session'].approval['expiresAt']<=time.time() and operation!='close':
            # Renewal only precedes a new proposal or a receipt observation.
            # Never resend an invocation or carry old write confirmations over.
            if operation not in ('propose','mode-propose','status'):
                raise PermissionError('View session expired while confirming. Review this action again.')
            await self.renew_interface(context,identity,mount)
        broker=mount['session']
        if operation in ('confirm','mode-confirm') and not await result(self.recent_action(request,context)):
            raise PermissionError('A recent explicit host action is required.')
        if operation=='propose':return broker.propose(value)
        if operation=='confirm':return {'confirmationId':broker.confirm(value)}
        if operation=='mode-propose':return broker.propose_mode(value)
        if operation=='mode-confirm':return {'confirmationId':broker.confirm_mode(value)}
        if operation=='set-mode':return broker.change_mode(value)
        if operation=='invoke':return await broker.invoke(value)
        if operation=='status':return await broker.status(value)
        if operation=='close':
            broker.close();del self.mounts[identity];return {'closed':True}
        raise ValueError('Undeclared host interaction.')

    async def renew_observer(self,context,identity,request,receipt_id):
        """Trusted server status/lease adapter; deliberately has no UI route."""
        self.prune();mount=self.mounts.get(identity)
        if not mount or (mount['actor'],mount['window'])!=(context['actor'],context['window']):
            raise PermissionError('Observer mount is unavailable for this current user/window.')
        return await mount['session'].renew_observer(request,receipt_id)


def mount_agent_view_routes(app,host,*,prefix='/api/agent-views'):
    """Explicit candidate adoption only; no auto-mount or owner business imports."""
    async def body(request):
        if len(await request.body())>300000:raise HTTPException(413,'View request is too large.')
        try:return await request.json()
        except ValueError as exc:raise HTTPException(422,'Provide a typed view request.') from exc
    async def handle(request,callback):
        context=await host.context(request)
        try:return await callback(context,await body(request))
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except (ValueError,KeyError) as exc:raise HTTPException(409,'View contract or revision changed; review before retrying.') from exc
    @app.post(prefix+'/reviews')
    async def review(request:Request):return await handle(request,lambda context,value:host.review(context,value))
    @app.post(prefix+'/reviews/{identity}/approve')
    async def approve(identity:str,request:Request):return await handle(request,lambda context,value:host.approve(request,context,identity,value))
    @app.post(prefix+'/mounts/{identity}/{operation}')
    async def interact(identity:str,operation:str,request:Request):
        return await handle(request,lambda context,value:host.interact(request,context,identity,operation,value))
