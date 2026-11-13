"""Generic reviewed-Phema transport; business operations run in owners."""
import asyncio
from fastapi import HTTPException

class NetworkBroker:
    def __init__(self, store, network):
        self.store, self.network = store, network
        self.limit = asyncio.Semaphore(6)
        self.live = {}

    async def call(self, child, method, args):
        user = dict(child.user)
        # A Browser's calls go out under its window account's own registration (account_access.py).
        bind = getattr(self.network, 'bind_account', None)
        if bind: bind(user.get('id'))
        if method == 'native_authority':
            if not getattr(self, 'native_trust', None): return None
            await self.native_trust.apply(user, child.phema, force=True)
            child.user['_nativeTrust'] = user.get('_nativeTrust')
            return user.get('_nativeTrust')
        if method == 'directory':
            return await self.network.directory(user['token'])
        if method == 'schema_query':
            return await self.network.schema_query(args['request'], user['token'])
        if method=='resolve_reference':
            return await self.resolve_for(user,getattr(child,'phema',None),args['reference'],args['inputs'],child=child)
        raise HTTPException(403, 'Unsupported Browser transport operation.')

    async def resolve_for(self, user, root, ref, inputs, child=None):
        """Resolve one reference of a reviewed Phema as `user`: a Browser's call, or a scheduled run's tick."""
        from phemacast.core.composition import reference_paths, walk_compositions
        from phemacast.core.composition_resolution import reference_contract
        from ..phema_security import check
        bind = getattr(self.network, 'bind_account', None)
        if bind: bind(user.get('id'))
        if getattr(self, 'native_trust', None):
            await self.native_trust.apply(user, root)
        if not root:raise HTTPException(403,'Phema authority is not initialized.')
        # The viewer's chosen data source travels with the reference; the reviewed contract is the rest of it.
        from ..source_choice import split
        try:reviewed,_=split(ref)
        except ValueError as exc:raise HTTPException(403,str(exc)) from exc
        match=next(((p,r) for _,p in walk_compositions(root) for r in reference_paths(p).values() if r['id']==ref['id'] and r['target']==ref['target'] and r['kind']==ref['kind']),None)
        if not match:raise HTTPException(403,'Reference is outside this Browser Phema.')
        if reference_contract(reviewed)!=reference_contract(match[1]):
            raise HTTPException(403,'Reference contract differs from the reviewed Phema.')
        try:rule=check(self.store,user,root,match[0],match[1],{'trigger':'manual'})
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        user['_permissionMaxPoints']=rule.get('maxPoints',0)
        async with self.limit:
            if 'phemacast:operation-binding' in ref.get('extensions',{}):
                if not getattr(self,'native_forms',None):raise HTTPException(409,'Native operation transport is not installed.')
                if child is None:raise HTTPException(409,'Native operations run only in an open Browser.')
                return await self.native_forms.read_reference(child,ref,inputs)
            result = await self.network.resolve_reference(ref, inputs, user, self.store)
        if child is not None and ref.get('refresh',{}).get('trigger') == 'stream':
            paths=[p for p,r in reference_paths(root).items() if r['id']==ref['id'] and reference_contract(r)==reference_contract(reviewed)]
            if paths:self.stream(child,paths[0],ref,inputs,user)
        return result

    def stream(self, child, path, reference, inputs, user):
        """Keep one live delivery per stream reference, pushed into the mounted Browser."""
        from phemacast.core.composition import content_hash
        from ..content_references import live_reference
        key=(id(child),path)
        old=self.live.pop(key,None)
        if old:old.cancel()
        if len(self.live)>=16:return  # Bounded vendor sockets per Terminal; the reference keeps its open value.
        registration={k:getattr(self.network.registration,k,None) for k in ('agent_id','agent_name','public_url','_token')}
        state={'generation':None}
        async def snapshot(mount):
            return await child.call({'operation':'native-snapshot','method':'GET','mount':mount,'user':child.user,'registration':registration})
        async def deliver(value):
            mount=(child.snapshot or {}).get('id')
            if state['generation'] is None:
                # The Browser writes its whole resolution when it finishes; a
                # delivery made earlier would be overwritten, so wait for it.
                for _ in range(240):
                    current=await snapshot(mount)
                    results=(current.get('content') or {}).get('results') or {}
                    if path in results and not any(r.get('status')=='resolving' for r in results.values() if isinstance(r,dict)):break
                    await asyncio.sleep(.25)
                state['generation']=current['generation']
            for attempt in range(2):
                if state['generation'] is None:
                    state['generation']=(await snapshot(mount))['generation']
                try:
                    await child.call({'operation':'native-content','method':'POST','mount':mount,'user':child.user,'registration':registration,
                        'body':{'generation':state['generation'],'phemaDigest':content_hash(child.phema),'reference':path,'value':value}})
                    return
                except HTTPException as exc:
                    # The Browser moved to a new generation; read it once, then stop if the reference is gone.
                    if exc.status_code!=409 or attempt:raise
                    state['generation']=None
        async def run():
            try:await live_reference(self.network,self.store,user,reference,inputs,deliver)
            except asyncio.CancelledError:raise
            except PermissionError:
                try:await deliver({'status':'unauthorized','rows':[],'error':'The vendor rejected your key or stream entitlement.'})
                except Exception:pass
            except Exception:
                try:await deliver({'status':'unavailable','rows':[],'error':'The live vendor stream stopped. Refresh to reconnect.'})
                except Exception:pass
        task=asyncio.create_task(run())
        self.live[key]=task
        task.add_done_callback(lambda done,key=key:self.live.pop(key,None) if self.live.get(key) is done else None)

    async def close_child(self, child):
        # Every way a Browser closes ends here: free its protected session first.
        if getattr(self,'native_forms',None):self.native_forms.release(child)
        tasks=[self.live.pop(key) for key in [k for k in self.live if k[0]==id(child)]]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
