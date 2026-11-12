"""One supervised OS child per Browser; all API access stays in Terminal."""
import asyncio
import json
import os
import secrets
import sys
import time
import httpx
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..contracts import identifier, layout_operation
from .broker import NetworkBroker


class Child:
    def __init__(self, process, user, broker=None):
        self.process, self.user = process, dict(user)
        self.pending = {}
        self.write_lock = asyncio.Lock()
        self.snapshot = None
        self.phema = None
        self.subscribers = 0
        self.lease_until = time.time()+90
        self.closed = False
        self.broker=broker
        self.rpc_tasks=set()
        self.network_requests={}
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if 'cancelNetworkRequestId' in message:
                    task=self.network_requests.get(message['cancelNetworkRequestId'])
                    if task:task.cancel()
                    continue
                if 'networkRequestId' in message:
                    task=asyncio.create_task(self.network_request(message))
                    self.rpc_tasks.add(task);task.add_done_callback(self.rpc_tasks.discard)
                    key=message['networkRequestId'];self.network_requests[key]=task
                    task.add_done_callback(lambda _,key=key:self.network_requests.pop(key,None))
                    continue
                future = self.pending.get(message['requestId'])
                if future and not future.done():
                    if 'error' in message:
                        future.set_exception(HTTPException(message.get('status',502),message['error']))
                    else:
                        future.set_result(message['result'])
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(HTTPException(502,'Browser process exited. Reopen this Browser to restart it.'))

    async def network_request(self,message):
        try:
            result=await self.broker.call(self,message['method'],message.get('args',{}))
            response={'networkRequestId':message['networkRequestId'],'result':result}
        except HTTPException as error:
            response={'networkRequestId':message['networkRequestId'],'error':error.detail,'status':error.status_code,'stopFallback':getattr(error,'stop_fallback',False)}
            # A failure the host names (such as a stopped local source) keeps its name in the Browser worker.
            from phemacast.core.composition_resolution import DECLARED_FAILURES
            if getattr(error,'failure_category',None) in DECLARED_FAILURES:response['failureCategory']=error.failure_category
        except asyncio.CancelledError:
            return
        except httpx.TimeoutException:
            response={'networkRequestId':message['networkRequestId'],'error':'Data Consultant metadata request timed out.' if message['method']=='advice' else 'Source request timed out.','status':504}
        except httpx.HTTPStatusError as error:
            status=error.response.status_code
            label='Model registry' if message['method']=='schema_query' else 'Data Consultant' if message['method']=='advice' else 'Source'
            detail=f'{label} returned HTTP {status}.'
            # Registry integrity failures precede provider execution. Explain
            # these known errors without exposing upstream bodies, submitted
            # inputs, credentials, or arbitrary vendor error text.
            if message['method']=='schema_query':
                try:reason=error.response.json().get('detail')
                except (ValueError, AttributeError):reason=None
                if reason=='Resource digest mismatch.':
                    detail+=' The source mapping differs from its published version; update the registered mapping.'
                elif reason=='Resource unavailable.':
                    detail+=' The required model or mapping is not available to this account.'
            response={'networkRequestId':message['networkRequestId'],'error':detail,'status':status if 400<=status<500 else 502}
        except Exception:
            response={'networkRequestId':message['networkRequestId'],'error':'Terminal source transport failed.','status':502}
        try:
            async with self.write_lock:
                if not self.closed:
                    self.process.stdin.write(json.dumps(response,allow_nan=False).encode()+b'\n');await self.process.stdin.drain()
        except (BrokenPipeError,ConnectionResetError):pass

    async def call(self, message):
        if self.closed or self.process.returncode is not None:
            raise HTTPException(502,'Browser process is unavailable. Reopen this Browser.')
        key=secrets.token_hex(12);future=asyncio.get_running_loop().create_future()
        self.pending[key]=future
        try:
            async with self.write_lock:
                self.process.stdin.write(json.dumps({**message,'requestId':key},allow_nan=False).encode()+b'\n')
                await self.process.stdin.drain()
            result=await asyncio.wait_for(future,60)
            if isinstance(result,dict) and 'entry' in result:
                self.phema=result.get('phema',self.phema)
                self.snapshot={**result,'processId':self.process.pid,'runtime':'terminal-child-process'}
                return self.snapshot
            return result
        except asyncio.TimeoutError:
            raise HTTPException(504,'Browser process did not respond. The request will not be replayed.') from None
        except (BrokenPipeError,ConnectionResetError):
            raise HTTPException(502,'Browser process exited. Reopen this Browser.') from None
        finally:
            self.pending.pop(key,None)

    async def close(self):
        if self.closed:
            return
        self.closed=True
        for task in self.rpc_tasks:task.cancel()
        await asyncio.gather(*self.rpc_tasks,return_exceptions=True)
        if self.broker and hasattr(self.broker,'close_child'):
            await self.broker.close_child(self)
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(),5)
        except asyncio.TimeoutError:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(),3)
            except asyncio.TimeoutError:
                self.process.kill();await self.process.wait()
        await asyncio.gather(self.reader,return_exceptions=True)


class BrowserProcesses:
    def __init__(self,store,network):
        self.store,self.network=store,network
        self.instances={}
        self.starting=set()
        self.pending_slots=0
        self.max_processes=32
        self.epochs={}
        self.limit=asyncio.Semaphore(6)
        self.refresh_user=None
        self.auth_interval=30
        self.broker=NetworkBroker(store,network)

    def authority(self):
        r=self.network.registration
        return {k:getattr(r,k,None) for k in ('agent_id','agent_name','public_url','_token')}

    async def invoke(self,child,method,rest='',body=None,initialize=False):
        if getattr(self,'native_trust',None):await self.native_trust.apply(child.user,child.phema)
        return await child.call({'operation':'initialize' if initialize else 'request','method':method,'rest':rest,'body':body,'user':child.user,'registration':self.authority(),'stateDir':str(self.store.path.parent),'plaza':self.network.plaza})

    async def close_owner(self,owner):
        self.epochs[owner]=self.epochs.get(owner,0)+1
        for key in list(self.epochs):
            if isinstance(key,tuple) and key[0]==owner:self.epochs[key]+=1
        children=[child for child in self.instances.values() if child.user['id']==owner]
        children += [child for child in self.starting if child.user['id']==owner]
        self.instances={id:child for id,child in self.instances.items() if child.user['id']!=owner}
        await asyncio.gather(*(child.close() for child in children))

    @staticmethod
    def window_key(user):
        return (user['id'],user.get('terminalWindow',''))

    def belongs(self,child,user):
        return self.window_key(child.user)==self.window_key(user)

    async def close_window(self,owner,window):
        key=(owner,window)
        self.epochs[key]=self.epochs.get(key,0)+1
        user={'id':owner,'terminalWindow':window}
        children=[child for child in set(self.instances.values())|self.starting if self.belongs(child,user)]
        self.instances={id:child for id,child in self.instances.items() if not self.belongs(child,user)}
        await asyncio.gather(*(child.close() for child in children))

    async def close_all(self):
        children=set(self.instances.values())|self.starting
        self.instances.clear()
        await asyncio.gather(*(child.close() for child in children))

    async def reap(self,interval=15):
        while True:
            await asyncio.sleep(interval)
            for id,child in list(self.instances.items()):
                if not child.subscribers and time.time()>child.lease_until:
                    self.instances.pop(id,None);await child.close()

    async def route(self,rest,request,user):
        parts=rest.strip('/').split('/') if rest.strip('/') else []
        if not parts:
            if request.method=='GET':
                children=[c for c in self.instances.values() if self.belongs(c,user)]
                async def snapshot(child):
                    child.user.update(user)
                    child.lease_until=time.time()+90
                    try:return await self.invoke(child,'GET','/'+child.snapshot['id'])
                    except HTTPException:return {**child.snapshot,'status':'unavailable','error':'Browser process exited. Reopen this Browser.'}
                return {'instances':await asyncio.gather(*(snapshot(c) for c in children))}
            if request.method=='DELETE':
                await self.close_window(user['id'],user.get('terminalWindow',''));return {'status':'closed'}
            if request.method!='POST':raise HTTPException(405)
            window_key=self.window_key(user)
            epoch=self.epochs.setdefault(window_key,0)
            body=await request.json()
            from ..phema_transfer import accept
            body['phema'], body['entry'] = await accept(body)
            identifier(body.get('workspaceId'))
            if body.get('range','1M') not in ('1D','1W','1M','3M','1Y'):raise HTTPException(422,'Unsupported chart range.')
            child=None
            reserved=False
            try:
                async with self.limit:
                    if epoch!=self.epochs.get(window_key,0):raise HTTPException(409,'Terminal session closed during Browser creation.')
                    if len(self.instances)+self.pending_slots>=self.max_processes:raise HTTPException(429,'Terminal supports at most 32 active Browser processes.')
                    self.pending_slots+=1;reserved=True
                    env={**os.environ,'PYTHONPATH':os.pathsep.join(p for p in sys.path if p),'PYTHONUNBUFFERED':'1'}
                    # Resolve modules from the running release, not an unrelated
                    # current checkout (which may contain cloud-only source files).
                    process=await asyncio.create_subprocess_exec(sys.executable,'-m','attas.data_terminal_phemar.browser.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,env=env,cwd=str(Path(__file__).resolve().parents[3]),limit=16_000_000)
                    child=Child(process,user,self.broker);child.phema=body['phema'];self.starting.add(child)
                    result=await self.invoke(child,'POST',body=body,initialize=True)
                    if epoch!=self.epochs.get(window_key,0):raise HTTPException(409,'Terminal session closed during Browser creation.')
                    self.instances[result['id']]=child
                    return result
            except BaseException:
                if child:await child.close()
                raise
            finally:
                if child:self.starting.discard(child)
                if reserved:self.pending_slots-=1
        id=identifier(parts[0]);child=self.instances.get(id)
        if not child or not self.belongs(child,user):raise HTTPException(404,'Browser unavailable in this Terminal window.')
        child.user.update(user);child.lease_until=time.time()+90
        if len(parts)==1 and request.method=='DELETE':
            self.instances.pop(id,None);await child.close();return {'status':'closed'}
        if len(parts)==2 and parts[1]=='events' and request.method=='GET':
            async def events():
                child.subscribers+=1
                checked=0
                try:
                    while id in self.instances and not await request.is_disconnected():
                        child.lease_until=time.time()+90
                        checking_auth=False
                        try:
                            if self.refresh_user and time.monotonic()-checked >= self.auth_interval:
                                checking_auth=True
                                fresh=await self.refresh_user(request)
                                if self.window_key(fresh) != self.window_key(child.user):
                                    raise HTTPException(401,'Terminal account changed.')
                                child.user.update(fresh)
                                if getattr(child,'auth_suspended',False):
                                    await self.invoke(child,'PUT','/'+id,{'suspended':False})
                                    child.auth_suspended=False
                                checked=time.monotonic()
                                checking_auth=False
                            snapshot=await self.invoke(child,'GET','/'+id)
                        except HTTPException as error:
                            if checking_auth:
                                child.auth_suspended=True
                                try:await self.invoke(child,'PUT','/'+id,{'suspended':True,'error':'Plaza session verification failed. Retrying verification before resuming data.'})
                                except HTTPException:pass
                            # A failed read must not stop the data tasks. Auth
                            # failures pause execution until re-verification.
                            snapshot={**child.snapshot,'status':'unavailable','transport':'Reconnecting','error':str(error.detail) if error.status_code in (401,403,409) else 'Plaza session verification unavailable; retrying.' if checking_auth else 'Browser status unavailable; reconnecting without restarting data.'}
                            yield 'event: update\ndata: '+json.dumps(snapshot)+'\n\n'
                            if error.status_code in (401,403,409):break
                            await asyncio.sleep(2)
                            continue
                        yield 'event: update\ndata: '+json.dumps(snapshot,allow_nan=False)+'\n\n'
                        await asyncio.sleep(2)
                finally:child.subscribers-=1
            return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
        return await self.invoke(child,request.method,rest,await request.json() if request.method in ('POST','PUT') else None)
