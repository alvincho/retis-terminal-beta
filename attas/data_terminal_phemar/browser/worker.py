"""Private stdio worker. Inherits Terminal authority; never registers on Plaza."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
import asyncio
import json
import sys
import secrets
import traceback
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from ..network import Network
from ..store import Store
from .engine import BrowserEngine


def network_error(message):
    """The Terminal's answer to a network request, as the error it raised there."""
    error=HTTPException(message.get('status',502),message['error'])
    error.stop_fallback=message.get('stopFallback',False)
    from phemacast.core.composition_resolution import DECLARED_FAILURES
    if message.get('failureCategory') in DECLARED_FAILURES:error.failure_category=message['failureCategory']
    return error


class TerminalNetwork(Network):
    def __init__(self,plaza,registration,client,rpc):
        super().__init__(plaza,registration,client);self.rpc=rpc
    async def directory(self,token='',force=False):
        return await self.rpc('directory',{})
    async def schema_query(self,request,token=''):
        return await self.rpc('schema_query',{'request':request})
    async def execute(self, agent, name, parameters, user, store):
        # All network invocation stays behind the parent's closed, reviewed
        # transport. Inheriting Network.execute would bypass that boundary.
        return await self.rpc('execute', {'agent':agent,'name':name,'parameters':parameters})

    async def native_authority(self):
        return await self.rpc('native_authority',{})
    async def resolve_reference(self,reference,inputs,user,store):
        return await self.rpc('resolve_reference',{'reference':reference,'inputs':inputs})


async def main():
    engine = None
    client = None
    registration = None
    user = {}
    tasks = set()
    network_pending={}
    async def rpc(method,args):
        id=secrets.token_hex(16);future=asyncio.get_running_loop().create_future();network_pending[id]=future
        try:
            print(json.dumps({'networkRequestId':id,'method':method,'args':args},allow_nan=False),flush=True)
            return await asyncio.wait_for(future,120)
        except (asyncio.CancelledError, TimeoutError):
            print(json.dumps({'cancelNetworkRequestId':id}),flush=True)
            raise
        finally:network_pending.pop(id,None)


    async def dispatch(message):
        nonlocal engine, client, registration
        try:
            if message['operation'] == 'initialize':
                registration = SimpleNamespace(**message['registration'])
                client = httpx.AsyncClient(timeout=40)
                engine = BrowserEngine(Store(Path(message['stateDir'])), TerminalNetwork(message['plaza'], registration, client,rpc))
                engine.terminal_window = message['user'].get('terminalWindow','')
            else:
                registration.__dict__.update(message['registration'])
            user.update(message['user'])
            if engine:
                for instance in engine.instances.values():instance['_nativeTrustUser']=dict(user)
            if message['operation']=='native-snapshot':
                instance=engine.owned(message['mount'],user)
                print(json.dumps({'requestId':message['requestId'],'result':engine.public(instance)},allow_nan=False),flush=True)
                return
            if message['operation']=='native-content':
                from phemacast.core.composition import content_hash,reference_paths
                value=message['body'];instance=engine.owned(message['mount'],user)
                if (instance['generation']!=value['generation'] or content_hash(instance['phema'])!=value['phemaDigest']
                        or value['reference'] not in reference_paths(instance['phema'])):
                    raise HTTPException(409,'Native result belongs to a different mounted revision.')
                instance.setdefault('content',{}).setdefault('results',{})[value['reference']]={
                    'status':'ready','value':value['value'],'sourceMode':'parameters'}
                instance['sequence']+=1
                print(json.dumps({'requestId':message['requestId'],'result':{'updated':True}},allow_nan=False),flush=True)
                return
            body = json.dumps(message.get('body') or {}).encode()
            async def receive():
                return {'type':'http.request','body':body,'more_body':False}
            request = Request({'type':'http','method':message['method'],'headers':[]}, receive)
            result = await engine.route(message.get('rest',''),request,user)
            response = {'requestId':message['requestId'],'result':result}
        except HTTPException as error:
            response = {'requestId':message['requestId'],'error':error.detail,'status':error.status_code}
        except Exception as error:
            # Keep diagnostics in the Terminal service log, without exception
            # values or frame locals that could contain session/vendor secrets.
            print('Browser worker error: '+type(error).__name__+'\n'+''.join(traceback.format_tb(error.__traceback__)),file=sys.stderr,flush=True)
            response = {'requestId':message['requestId'],'error':'Browser worker operation failed.','status':502}
        print(json.dumps(response,allow_nan=False),flush=True)

    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break  # Parent exited: release all work without an agent lease.
            message = json.loads(line)
            if 'networkRequestId' in message:
                future=network_pending.get(message['networkRequestId'])
                if future and not future.done():
                    if 'error' in message:future.set_exception(network_error(message))
                    else:future.set_result(message['result'])
                continue
            task = asyncio.create_task(dispatch(message))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if engine:
            await engine.close_all()
        if client:
            await client.aclose()


if __name__ == '__main__':
    asyncio.run(main())
