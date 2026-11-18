"""Independent DataSource ASGI candidate. Never mount this in Terminal."""
import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from jsonschema import ValidationError
from starlette.concurrency import run_in_threadpool

from .streams import ProviderStreams
from .vault import AccessDenied, Conflict


def install_private_headers(app):
    @app.middleware('http')
    async def private_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/source/'):
            response.headers.update({'Cache-Control': 'no-store', 'Pragma': 'no-cache',
                                     'Vary': 'Authorization', 'X-Content-Type-Options': 'nosniff'})
        return response


def create_app(service, authenticate, *, connectors=None):
    """authenticate(request, action) must validate a user/scoped grant AND ACL.

    It supplies id and optional window from trusted authentication context.
    A client-supplied owner, scope or vendor header is never trusted.
    """
    app=FastAPI(title=service.source_id+' source operations',version='1.0.0')
    streams=ProviderStreams(service,connectors=connectors)
    app.state.provider_service=service
    app.state.provider_streams=streams
    app.add_event_handler('shutdown',streams.close)

    install_private_headers(app)

    @app.exception_handler(Conflict)
    async def conflict(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':str(exc)},status_code=409)

    @app.exception_handler(AccessDenied)
    async def denied(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':str(exc)},status_code=403)

    @app.exception_handler(ValueError)
    @app.exception_handler(ValidationError)
    async def invalid(request,exc):
        from fastapi.responses import JSONResponse
        # Validation exceptions can contain credential input; never echo values.
        return JSONResponse({'error':'Request does not match the source operation contract.'},status_code=422)

    async def user(request,action):
        if request.headers.get('x-attas-vendor-key'):
            raise HTTPException(400,'Vendor credentials are not accepted on execution transport.')
        principal=await authenticate(request,action)
        if not principal or not principal.get('id'):
            raise HTTPException(401,'Source-authorized user required.')
        return principal['id'],principal.get('window','')

    async def body(request):
        raw=await request.body()
        if len(raw)>256_000:
            raise HTTPException(413,'Source request exceeds 256 KB.')
        try:
            value=json.loads(raw)
        except ValueError:
            raise HTTPException(422,'Invalid JSON request.') from None
        if not isinstance(value,dict):
            raise HTTPException(422,'An operation object is required.')
        return value

    @app.get('/api/source/v1/interface')
    async def interface(request:Request):
        await user(request,'source.read')
        from .presentation import interface_candidate
        return interface_candidate(service.profile())

    @app.get('/api/source/v1/connections')
    async def connections(request:Request):
        owner,window=await user(request,'connections.read')
        return {'connections':service.vault.list(owner,window),'profile':service.profile()}

    @app.post('/api/source/v1/connections')
    async def save(request:Request):
        owner,window=await user(request,'connections.write')
        return await run_in_threadpool(service.save_connection,owner,window,await body(request),request.headers.get('idempotency-key',''))

    @app.post('/api/source/v1/fetch')
    async def fetch(request:Request):
        owner,window=await user(request,'source.execute')
        return await run_in_threadpool(service.fetch,owner,window,await body(request),request.headers.get('idempotency-key',''))

    @app.post('/api/source/v1/probe')
    async def probe(request:Request):
        owner,window=await user(request,'source.probe')
        return await run_in_threadpool(service.probe_connection,owner,window,await body(request),request.headers.get('idempotency-key',''))

    @app.get('/api/source/v1/snapshots')
    async def snapshots(request:Request):
        owner,window=await user(request,'source.read')
        return {'snapshots':service.snapshots(owner,window)}

    @app.get('/api/source/v1/snapshots/{id}')
    async def snapshot(id:str,request:Request):
        owner,window=await user(request,'source.read')
        return service.snapshot(owner,window,id)

    @app.post('/api/source/v1/stream')
    async def stream(request:Request):
        owner,window=await user(request,'source.stream')
        query=await body(request)
        # Validate before headers without opening a vendor socket. subscribe()
        # performs final lease/quota checks atomically inside its generator.
        service._record(owner,window,query)
        async def events():
            try:
                async with streams.subscribe(owner,window,query) as queue:
                    while True:
                        if await user(request,'source.stream') != (owner,window):
                            return
                        service._record(owner,window,query)
                        try:
                            event=await asyncio.wait_for(queue.get(),10)
                            service._record(owner,window,query)
                            yield 'data: '+json.dumps(event,allow_nan=False)+'\n\n'
                        except asyncio.TimeoutError:
                            yield ': heartbeat\n\n'
            except (AccessDenied,Conflict,ValueError,HTTPException):
                yield 'data: {"status":"unauthorized","error":"Source stream access or binding is unavailable."}\n\n'
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store'})

    return app
