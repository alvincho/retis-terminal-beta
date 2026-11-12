"""Window-specific Plaza selection; each destination has a separate session vault."""
from contextlib import AsyncExitStack
import asyncio
import ipaddress
import json
from pathlib import Path
import socket
import ssl
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .trusted_plazas import plaza_key
from .window_sessions import window_context, window_scope


def plaza_origin(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise HTTPException(422, 'Enter a Plaza HTTP or HTTPS address.')
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise HTTPException(422, 'Enter a Plaza HTTP or HTTPS address.') from None
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise HTTPException(422, 'Enter a Plaza origin without a path, credentials, query or fragment.')
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError()
    except ValueError:
        if host == 'localhost':address = ipaddress.ip_address('127.0.0.1')
        elif ':' in host or host.replace('.', '').isdigit():
            raise HTTPException(422, 'This network address cannot be used as a Plaza.') from None
        else:address = None
    if parsed.scheme == 'http' and not (address and address.is_loopback):
        raise HTTPException(422, 'Use HTTPS for a remote Plaza; HTTP is allowed only on this computer.')
    host = '['+host+']' if ':' in host else host
    return parsed.scheme+'://'+host+(':'+str(port) if port and port != (443 if parsed.scheme=='https' else 80) else '')


def unreachable(error):
    """Nothing answered (switched off, no route, unknown name), as opposed to an answer that failed a check."""
    if isinstance(error, (httpx.TimeoutException, socket.gaierror, TimeoutError)):return True
    if not isinstance(error, httpx.ConnectError):return False
    while error is not None:
        if isinstance(error, ssl.SSLError):return False
        error = error.__cause__ or error.__context__
    return True


def require_local(request, port, message):
    """Allow a change only from the Terminal's own page on this computer."""
    if request.url.hostname not in ('127.0.0.1', 'localhost', '::1') or request.url.port != port:
        raise HTTPException(403, message)
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin mutation rejected.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-origin mutation rejected.')


async def read_metadata(client, url):
    async with client.stream('GET', url) as response:
        response.raise_for_status()
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 512000:
                raise HTTPException(422, 'Plaza connection metadata is too large.')
        return json.loads(content)


class PlazaSelection:
    def __init__(self, app, store, state_dir, agent_id, port, default, principal, factory, trusted=None):
        self.app, self.store, self.directory = app, store, Path(state_dir)
        self.trusted = trusted
        self.agent_id, self.port, self.default = agent_id, port, default.rstrip('/')
        self.principal, self.factory = principal, factory
        self.contexts, self.locks, self.window_locks = {}, {}, {}
        self.stack = AsyncExitStack()
        self.context_lock = asyncio.Lock()
        self.closed = False
        # Tests replace only this factory; production never inherits proxy env or credentials.
        from .tls import verify
        self.http_factory = lambda **options: httpx.AsyncClient(trust_env=False, verify=verify(), follow_redirects=False, timeout=8, **options)

    def selected(self, window):
        if not window:return self.default
        try:return self.store.get('_installation', 'plaza-windows:'+self.agent_id, window)['origin']
        except HTTPException as exc:
            if exc.status_code != 404:raise
            return self.default

    def installation(self, origin, folder):
        """A trusted Plaza keeps its own identity and state, so its approval still applies, and it lists the
        Terminal's trusted Plazas; another address gets its own identity and list."""
        row = self.trusted.read()['plazas'].get(plaza_key(origin)) if self.trusted else None
        if row and row.get('origin') == origin:return row['agentIdentity'], Path(row['stateDir'])/'state', str(self.trusted.path)
        return self.agent_id[:60]+'-p'+plaza_key(origin), folder/'state', None

    async def prepare(self, origin):
        host = urlsplit(origin).hostname
        if host in ('127.0.0.1', 'localhost', '::1') and urlsplit(origin).port == self.port:
            raise HTTPException(422, 'Use the Plaza address, not this Terminal address.')
        # Prevent access to cloud metadata and similar special network addresses.
        rows = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, urlsplit(origin).port or 443), 3)
        for row in rows:
            address = ipaddress.ip_address(row[4][0])
            if address.is_link_local or address.is_multicast or address.is_unspecified:
                raise HTTPException(422, 'This network address cannot be used as a Plaza.')
        folder = self.directory/'plaza-connections'/plaza_key(origin)
        async with self.http_factory() as client:
            if urlsplit(origin).scheme == 'https':
                connection = await read_metadata(client, origin+'/.well-known/attas-network.json')
                if not isinstance(connection, dict) or connection.get('publicOrigin') != origin:
                    raise HTTPException(422, 'Connection metadata belongs to a different Plaza.')
                from .public_transport import load_connection
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                candidate = folder/'network-candidate.json'
                candidate.write_text(json.dumps(connection));candidate.chmod(0o600)
                try:connection = load_connection(candidate)
                finally:candidate.unlink(missing_ok=True)
            else:
                config = await read_metadata(client, origin+'/api/ui_auth/config')
                if not isinstance(config, dict) or config.get('auth_enabled') is not True:
                    raise HTTPException(422, 'The address does not provide Plaza sign-in.')
                connection = None
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if connection:
            network_file = folder/'public-network.json'
            temporary = folder/'public-network.tmp'
            temporary.write_text(json.dumps(connection));temporary.chmod(0o600);temporary.replace(network_file)
        data = {'origin':origin, 'connection':connection}
        temporary = folder/'connection.tmp'
        temporary.write_text(json.dumps(data));temporary.chmod(0o600);temporary.replace(folder/'connection.json')
        return data

    async def context(self, origin, prepared=None):
        if origin == self.default:return self.app
        async with self.context_lock:
            if origin in self.contexts:return self.contexts[origin]
            if self.closed:raise HTTPException(503, 'Terminal is stopping.')
            if len(self.contexts) >= 8:
                raise HTTPException(409, 'Eight Plaza connections are already open. Restart the Terminal before adding another.')
            folder = self.directory/'plaza-connections'/plaza_key(origin)
            if prepared is None:
                try:prepared = json.loads((folder/'connection.json').read_text())
                except (FileNotFoundError, ValueError):
                    raise HTTPException(409, 'Reconnect this Plaza from the login dialog.') from None
                if prepared.get('origin') != origin:
                    raise HTTPException(409, 'Reconnect this Plaza from the login dialog.')
            connection = prepared['connection']
            if connection:
                from .public_transport import PublicNetworkTransport
                def make_client(**options):
                    transport = PublicNetworkTransport(connection)
                    client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15, transport=transport, **options)
                    client.public_network = transport
                    return client
            else:make_client = self.http_factory
            plaza = connection['plazaOrigin'] if connection else origin
            client = make_client()
            try:
                identity, state, registry = self.installation(origin, folder)
                app = self.factory('data-terminal', identity, self.port,
                    state_dir=state, plaza_url=plaza, http_client=client,
                    plaza_context={**prepared, 'registration_client':lambda:make_client(base_url=plaza), 'registry':registry},
                    allow_plaza_selection=False)
                await self.stack.enter_async_context(app.router.lifespan_context(app))
            except BaseException:
                await client.aclose();raise
            self.contexts[origin] = app
            return app

    def check_local(self, request):
        require_local(request, self.port, 'Change the Plaza address from the local Terminal.')

    def install(self):
        @self.app.put('/api/plaza-connection')
        async def select(request: Request):
            self.check_local(request)
            if 'application/json' not in request.headers.get('content-type', ''):
                raise HTTPException(415, 'Use a JSON request.')
            window = window_context(request)
            if not window or not request.headers.get('x-terminal-window'):
                raise HTTPException(422, 'A Terminal window is required.')
            async with self.window_locks.setdefault(window, asyncio.Lock()):
                body = await request.json()
                if not isinstance(body, dict) or set(body) != {'url'}:
                    raise HTTPException(422, 'Enter a Plaza HTTP or HTTPS address.')
                origin = plaza_origin(body['url'])
                previous = self.selected(window)
                if origin == previous:return {'status':'unchanged', 'url':origin}
                current = await self.context(previous)
                if await current.state.principal(request, False):
                    raise HTTPException(409, 'Sign out of this window before changing its Plaza address.')
                # Validate before changing the durable choice. An unreachable or
                # malformed destination leaves the previous connection intact.
                try:
                    if origin != self.default:
                        async with self.locks.setdefault('prepare:'+origin, asyncio.Lock()):
                            await self.context(origin, await self.prepare(origin))
                except HTTPException:raise
                except (httpx.HTTPError, OSError, ValueError, TimeoutError) as error:
                    raise HTTPException(502, "This Plaza can't be reached. If it is switched off, turn it on and try again."
                        if unreachable(error) else 'Plaza could not be verified. Check its address and try again.') from None
                scope = current.state.network.plaza+'|'+(current.state.plaza_agent_id)
                current.state.store.delete('_installation', 'window-signin', window_scope(scope, window))
                self.store.put('_installation', 'plaza-windows:'+self.agent_id, window, {'origin':origin})
                return {'status':'changed', 'url':origin}

    async def close(self):
        self.closed = True
        await self.stack.aclose()
        self.contexts.clear()


class PlazaSelectionMiddleware:
    """Dispatch every API for a selected window into the same Plaza context."""
    def __init__(self, app, manager):self.app, self.manager = app, manager

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] == '/api/plaza-connection':
            return await self.app(scope, receive, send)
        request = Request(scope)
        try:
            window = window_context(request)
            origin = self.manager.selected(window)
            if origin == self.manager.default:return await self.app(scope, receive, send)
            self.manager.check_local(request)
            app = await self.manager.context(origin)
            return await app(scope, receive, send)
        except HTTPException as exc:
            response = JSONResponse({'detail':exc.detail}, status_code=exc.status_code,
                                    headers={'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})
            return await response(scope, receive, send)
