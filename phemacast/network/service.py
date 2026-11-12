"""Opt-in local TLS API. Operator approval is independent of Plaza identity."""
import asyncio
from contextlib import nullcontext
import hashlib
import ipaddress
import json
from pathlib import Path
import ssl
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prompits.core.client_sessions import ClientSessions
from .discovery import Advertisement, PROTOCOL
from .tls import interface_networks, lan_endpoint


def descriptor(config):
    pem = Path(config['certificate']).read_text()
    address = ipaddress.ip_address(config['address'])
    host = '[' + str(address) + ']' if address.version == 6 else str(address)
    endpoint = {'url': f"https://{host}:{int(config['port'])}", 'tlsFingerprintSha256': hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}
    if not 1024 <= int(config['port']) <= 65535 or not lan_endpoint(endpoint, interface_networks()):
        raise ValueError('Select an attached private interface and an unprivileged TLS port.')
    return endpoint


def sessions(config):
    # A pairing lasts until the Terminal disconnects it or the owner revokes it; it never times out.
    return ClientSessions(Path(config['stateDir']) / 'clients.sqlite3', access_prefix='lc-access-', refresh_prefix='lc-refresh-', lasting=True)


class LocalResource:
    def __init__(self, config, describe, handlers, *, streams=None):
        self.config, self.describe, self.handlers = config, describe, handlers
        self.streams = streams or {}
        self.endpoint = descriptor(config)
        self.sessions = sessions(config)
        self.app = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        self.advertisement = Advertisement(config, self.endpoint) if config.get('advertiseBonjour') else None
        self.server = self.task = self.settings_task = None
        self._install()

    def settings(self):
        path = self.config.get('configurationFile')
        if path:
            try:
                saved = json.loads(Path(path).read_text())['local_network']
                if saved['resourceId'] != self.config['resourceId']:raise ValueError('Changed local identity')
                for key in ('acceptLocalConnections', 'advertiseBonjour'):
                    self.config[key] = saved.get(key) is True
            except (OSError, ValueError, KeyError):
                self.config['acceptLocalConnections'] = False
        return self.config

    def _install(self):
        counts, inflight = {}, asyncio.Semaphore(4)
        @self.app.middleware('http')
        async def boundary(request, next):
            if not self.settings().get('acceptLocalConnections'):
                return JSONResponse({'detail': 'Local connections are disabled.'}, 403)
            host = request.client.host if request.client else ''
            try:
                address = ipaddress.ip_address(host)
                local = any(address.version == n.version and address in n for n in interface_networks())
            except ValueError:local = False
            if not local or request.headers.get('origin'):
                return JSONResponse({'detail': 'Only local resource clients are accepted.'}, 403)
            now = time.monotonic()
            for key in list(counts):
                if now - counts[key][0] >= 60:counts.pop(key)
            if host not in counts and len(counts) >= 1024:
                return JSONResponse({'detail': 'Try again later.'}, 429)
            start, count = counts.get(host, (now, 0)); counts[host] = start, count + 1
            if count >= 120:return JSONResponse({'detail': 'Local request limit reached.'}, 429)
            payload = bytearray()
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > 1_000_000:return JSONResponse({'detail': 'Request too large.'}, 413)
            request._body = bytes(payload)
            response = await next(request)
            response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
            return response

        async def body(request):
            try:
                value = await request.json()
                if not isinstance(value, dict):raise ValueError()
                return value
            except ValueError:raise HTTPException(422, 'Expected a JSON object.')

        def authenticated(request):
            if not self.settings().get('acceptLocalConnections'):raise HTTPException(403, 'Local connections are disabled.')
            token = request.headers.get('authorization', '').removeprefix('Bearer ')
            if not token.startswith('lc-access-'):raise HTTPException(401, 'Pair with this resource first.')
            return self.sessions.resolve(token)

        @self.app.post('/local/v1/pair')
        async def pair(request: Request):
            # A pairing request that passed the local-network boundary is accepted at once. The client
            # still proves its own code challenge to collect the grant, and the owner can revoke it.
            value = await body(request)
            grant = self.sessions.begin(value.get('client_name'), value.get('code_challenge'))
            self.sessions.approve(grant['user_code'], self.config['resourceId'], True)
            return {**grant, 'approved': True}

        @self.app.post('/local/v1/token')
        async def token(request: Request):
            value = await body(request)
            if value.get('refresh_token'):
                return {'session': self.sessions.refresh(value['refresh_token'])}
            result = self.sessions.exchange(value.get('device_code'), value.get('code_verifier'))
            result.pop('owner', None)
            return result

        @self.app.post('/local/v1/revoke')
        async def revoke(request: Request):
            session = authenticated(request)
            return self.sessions.revoke(session['owner'], session['id'])

        @self.app.get('/local/v1/resource')
        async def resource(request: Request):
            authenticated(request)
            return {'protocol': PROTOCOL, 'id': self.config['resourceId'], 'name': self.config['name'], 'kind': self.config['kind'],
                    'operations': sorted(self.handlers), 'streams': sorted(self.streams), 'resource': self.describe()}

        stream_limit = asyncio.Semaphore(4)
        @self.app.post('/local/v1/stream')
        async def stream(request: Request):
            session = authenticated(request)
            value = await body(request)
            if set(value) != {'operation', 'parameters'} or not isinstance(value['parameters'], dict):raise HTTPException(422, 'Invalid stream request.')
            handler = self.streams.get(value['operation'])
            if handler is None:raise HTTPException(403, 'Stream is not shared locally.')
            try:await asyncio.wait_for(stream_limit.acquire(), timeout=.05)
            except asyncio.TimeoutError:raise HTTPException(429, 'Stream capacity reached.')
            async def events():
                iterator = handler(value['parameters'], {'clientId':session['id'], 'authentication':'local-pairing'})
                pending = None
                try:
                    while True:
                        authenticated(request)  # Revocation is checked even while idle.
                        if pending is None:pending = asyncio.create_task(anext(iterator))
                        done, _ = await asyncio.wait([pending], timeout=10)
                        if not done:
                            yield ': heartbeat\n\n'; continue
                        try:event = pending.result()
                        except StopAsyncIteration:return
                        pending = None
                        authenticated(request)
                        encoded = json.dumps(event, allow_nan=False)
                        if len(encoded) > 500_000:raise ValueError('Stream event is too large.')
                        yield 'data: ' + encoded + '\n\n'
                except (HTTPException, ValueError):
                    yield 'data: {"status":"unavailable","error":"Local stream ended. Check the grant and provider connection."}\n\n'
                finally:
                    if pending:
                        pending.cancel(); await asyncio.gather(pending, return_exceptions=True)
                    await iterator.aclose()
                    stream_limit.release()
            return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})

        @self.app.post('/local/v1/call')
        async def call(request: Request):
            session = authenticated(request)
            value = await body(request)
            if set(value) != {'operation', 'parameters'} or not isinstance(value['parameters'], dict):
                raise HTTPException(422, 'Provide an operation and parameter object.')
            handler = self.handlers.get(value['operation'])
            if handler is None:raise HTTPException(403, 'This operation is not shared locally.')
            try:await asyncio.wait_for(inflight.acquire(), timeout=.05)
            except asyncio.TimeoutError:raise HTTPException(429, 'Resource is busy.')
            try:
                result = await asyncio.wait_for(handler(value['parameters'], {'clientId': session['id'], 'label': session['label'], 'authentication': 'local-pairing'}), 120)
                if len(json.dumps(result, default=str)) > 16_000_000:raise HTTPException(413, 'Result too large.')
                return result
            except asyncio.TimeoutError:raise HTTPException(504, 'Local operation timed out; it was not replayed.')
            finally:inflight.release()

    async def start(self):
        import uvicorn
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=self.config['address'], port=self.config['port'],
            ssl_certfile=self.config['certificate'], ssl_keyfile=self.config['private_key'], log_level='warning', access_log=False))
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
                            self.advertisement = Advertisement(self.config, self.endpoint)
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


def install_local_resource(app, config, describe, handlers, *, streams=None):
    if not config or not config.get('acceptLocalConnections'):return None
    resource = LocalResource(config, describe, handlers, streams=streams)
    app.router.add_event_handler('startup', resource.start)
    app.router.add_event_handler('shutdown', resource.close)
    return resource
