"""A local resource: a program on this computer that this computer's Terminal uses with no sign-in or pairing.

It listens on 127.0.0.1 only, so nothing on another computer can reach it, and announces itself with Bonjour so the
Terminal finds it at once (`discovery.py`). There is no account, token, pairing or certificate: being on this
computer is the trust. A web page can't reach it either: a request that carries an `Origin` header (every browser
fetch from a page does) or names any `Host` but `127.0.0.1:<port>` (DNS rebinding) is refused, and bodies are JSON.
"""
import asyncio
from contextlib import nullcontext
import ipaddress
import json
from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from .discovery import Advertisement, PROTOCOL

LOOPBACK = '127.0.0.1'
CALLER = {'authentication': 'this-computer'}
# Every client is on this computer, so one budget covers them all: generous enough for a Terminal with several
# open views polling their components, small enough to stop a runaway local program.
REQUESTS_PER_MINUTE = 1200


def endpoint(config):
    """Where this computer's clients reach the resource."""
    port = int(config['port'])
    if not 1024 <= port <= 65535:
        raise ValueError('Select an unprivileged port.')
    return {'url': f'http://{LOOPBACK}:{port}'}


def host_addresses():
    """This computer's own interface addresses: a Bonjour advert naming one of them comes from this computer."""
    import socket
    import psutil
    found = set()
    for values in psutil.net_if_addrs().values():
        for value in values:
            if value.family in (socket.AF_INET, socket.AF_INET6):
                try:found.add(ipaddress.ip_address(value.address.split('%')[0]))
                except ValueError:pass
    return found


# The files an agent view release may serve; anything else is refused.
VIEW_FILE = re.compile(r'^[A-Za-z0-9_-]{1,64}\.(js|css|json)$')
VIEW_RELEASE = re.compile(r'^[0-9a-f]{64}$')
PHEMA_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,79}$')


def phema_summary(phema_id, value):
    """What the descriptor says about one Phema the resource ships: enough for a Terminal to keep a current copy."""
    from prompits.core.capabilities import digest
    return {'id': phema_id, 'name': str(value.get('name') or phema_id)[:120], 'revision': int(value.get('revision') or 1),
            'digest': digest(value['phema'])}


class LocalResource:
    def __init__(self, config, describe, handlers, *, streams=None, views=None, phemas=None):
        """`views` maps an agent-view release digest to the folder holding its files (`/local/v2/views/<release>/<file>`).
        `phemas` maps a Phema id to `{'name', 'revision', 'phema'}`: Phemas this resource ships, which the Terminal
        keeps in its own library (`/local/v2/phemas/<id>`)."""
        self.config, self.describe, self.handlers = config, describe, handlers
        self.streams = streams or {}
        self.views = views if views is not None else {}
        self.phemas = {key: value for key, value in (phemas or {}).items() if PHEMA_ID.fullmatch(key)}
        self.endpoint = endpoint(config)
        self.app = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        self.advertisement = Advertisement(config) if config.get('advertiseBonjour') else None
        self.server = self.task = self.settings_task = None
        self._install()

    def settings(self):
        path = self.config.get('configurationFile')
        if path:
            try:
                saved = json.loads(Path(path).read_text(encoding='utf-8'))['local_network']
                if saved['resourceId'] != self.config['resourceId']:raise ValueError('Changed local identity')
                for key in ('acceptLocalConnections', 'advertiseBonjour'):
                    self.config[key] = saved.get(key) is True
            except (OSError, ValueError, KeyError):
                self.config['acceptLocalConnections'] = False
        return self.config

    def descriptor(self):
        return {'protocol': PROTOCOL, 'id': self.config['resourceId'], 'name': self.config['name'], 'kind': self.config['kind'],
                'operations': sorted(self.handlers), 'streams': sorted(self.streams), 'resource': self.describe(),
                'phemas': [phema_summary(key, value) for key, value in sorted(self.phemas.items())]}

    def _install(self):
        counts, inflight = [time.monotonic(), 0], asyncio.Semaphore(4)
        host = f"{LOOPBACK}:{int(self.config['port'])}"

        @self.app.middleware('http')
        async def boundary(request, next):
            if not self.settings().get('acceptLocalConnections'):
                return JSONResponse({'detail': 'Local connections are disabled.'}, 403)
            try:client = ipaddress.ip_address(request.client.host if request.client else '')
            except ValueError:client = None
            # Bound to 127.0.0.1, so only this computer connects; a browser page can't call it, and a name that
            # resolves here (DNS rebinding) is refused by its Host.
            if client is None or not client.is_loopback or request.headers.get('origin') or request.headers.get('host') != host:
                return JSONResponse({'detail': 'Only programs on this computer are accepted.'}, 403)
            now = time.monotonic()
            if now - counts[0] >= 60:counts[:] = [now, 0]
            counts[1] += 1
            if counts[1] > REQUESTS_PER_MINUTE:return JSONResponse({'detail': 'Local request limit reached.'}, 429)
            payload = bytearray()
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > 1_000_000:return JSONResponse({'detail': 'Request too large.'}, 413)
            request._body = bytes(payload)
            response = await next(request)
            response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
            return response

        async def body(request):
            if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                raise HTTPException(415, 'Send JSON.')
            try:
                value = await request.json()
                if not isinstance(value, dict):raise ValueError()
                return value
            except ValueError:raise HTTPException(422, 'Expected a JSON object.')

        @self.app.get('/local/v2/resource')
        async def resource():
            return self.descriptor()

        @self.app.get('/local/v2/phemas/{phema_id}')
        async def phema(phema_id: str):
            value = self.phemas.get(phema_id) if PHEMA_ID.fullmatch(phema_id) else None
            if value is None:raise HTTPException(404, 'Phema unavailable.')
            return {**phema_summary(phema_id, value), 'phema': value['phema']}

        @self.app.get('/local/v2/views/{release}/{name}')
        async def view_file(release: str, name: str):
            # Immutable, digest-checked view code; it carries no data.
            folder = self.views.get(release) if VIEW_RELEASE.fullmatch(release) else None
            if folder is None or not VIEW_FILE.fullmatch(name) or not (Path(folder) / name).is_file():
                raise HTTPException(404, 'View file unavailable.')
            return FileResponse(Path(folder) / name)

        stream_limit = asyncio.Semaphore(4)
        @self.app.post('/local/v2/stream')
        async def stream(request: Request):
            value = await body(request)
            if set(value) != {'operation', 'parameters'} or not isinstance(value['parameters'], dict):raise HTTPException(422, 'Invalid stream request.')
            handler = self.streams.get(value['operation'])
            if handler is None:raise HTTPException(403, 'This resource has no such stream.')
            try:await asyncio.wait_for(stream_limit.acquire(), timeout=.05)
            except asyncio.TimeoutError:raise HTTPException(429, 'Stream capacity reached.')
            async def events():
                iterator = handler(value['parameters'], dict(CALLER))
                pending = None
                try:
                    while True:
                        if pending is None:pending = asyncio.create_task(anext(iterator))
                        done, _ = await asyncio.wait([pending], timeout=10)
                        if not done:
                            yield ': heartbeat\n\n'; continue
                        try:event = pending.result()
                        except StopAsyncIteration:return
                        pending = None
                        encoded = json.dumps(event, allow_nan=False)
                        if len(encoded) > 500_000:raise ValueError('Stream event is too large.')
                        yield 'data: ' + encoded + '\n\n'
                except (HTTPException, ValueError):
                    yield 'data: {"status":"unavailable","error":"Local stream ended. Check the provider connection."}\n\n'
                finally:
                    if pending:
                        pending.cancel(); await asyncio.gather(pending, return_exceptions=True)
                    await iterator.aclose()
                    stream_limit.release()
            return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})

        @self.app.post('/local/v2/call')
        async def call(request: Request):
            value = await body(request)
            if set(value) != {'operation', 'parameters'} or not isinstance(value['parameters'], dict):
                raise HTTPException(422, 'Provide an operation and parameter object.')
            handler = self.handlers.get(value['operation'])
            if handler is None:raise HTTPException(403, 'This resource has no such operation.')
            try:await asyncio.wait_for(inflight.acquire(), timeout=.05)
            except asyncio.TimeoutError:raise HTTPException(429, 'Resource is busy.')
            try:
                result = await asyncio.wait_for(handler(value['parameters'], dict(CALLER)), 120)
                if len(json.dumps(result, default=str)) > 16_000_000:raise HTTPException(413, 'Result too large.')
                return result
            except asyncio.TimeoutError:raise HTTPException(504, 'Local operation timed out; it was not replayed.')
            finally:inflight.release()

    async def start(self):
        import uvicorn
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=LOOPBACK, port=int(self.config['port']),
            log_level='warning', access_log=False))
        self.server.install_signal_handlers = lambda: None
        self.server.capture_signals = nullcontext
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(50):
            if self.server.started:
                if self.advertisement:await self.advertisement.start()
                async def monitor():
                    while True:
                        await asyncio.sleep(5)
                        desired = self.settings().get('acceptLocalConnections') and self.config.get('advertiseBonjour')
                        if desired and (not self.advertisement or not self.advertisement.zc):
                            self.advertisement = Advertisement(self.config)
                            await self.advertisement.start()
                        elif not desired and self.advertisement:await self.advertisement.close()
                self.settings_task = asyncio.create_task(monitor())
                return
            if self.task.done():self.task.result();raise RuntimeError('Local resource listener failed.')
            await asyncio.sleep(.1)
        await self.close()
        raise RuntimeError('Local resource listener startup timed out.')

    async def close(self):
        if self.settings_task:
            self.settings_task.cancel(); await asyncio.gather(self.settings_task, return_exceptions=True)
        if self.advertisement:await self.advertisement.close()
        if self.server:self.server.should_exit = True
        if self.task:await self.task


def install_local_resource(app, config, describe, handlers, *, streams=None, views=None, phemas=None):
    if not config or not config.get('acceptLocalConnections'):return None
    resource = LocalResource(config, describe, handlers, streams=streams, views=views, phemas=phemas)
    app.router.add_event_handler('startup', resource.start)
    app.router.add_event_handler('shutdown', resource.close)
    return resource
