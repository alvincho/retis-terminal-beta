"""HTTPS agent routing with authenticated, certificate-pinned local alternatives."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import time
from urllib.parse import urlsplit

import httpx

PREFIX = '/_network/'
# The site's agent list comes from Plaza and changes while the Terminal runs; it is read again on a miss and on a timer.
MAX_AGENTS = 500
REFRESH_SECONDS = 600
MISS_REFRESH_SECONDS = 30
MAX_DOCUMENT_BYTES = 512000


def load_connection(path=None):
    path = path or os.environ.get('ATTAS_PUBLIC_NETWORK_FILE')
    if not path:
        return None
    return validate_connection(json.loads(Path(path).read_text(encoding='utf-8')))


def validate_connection(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid public Plaza connection; HTTPS is required.')
    public = urlsplit(value.get('publicOrigin', ''))
    if value.get('schema') != 'attas.public-network/1' or public.scheme != 'https' or public.username or public.password or public.path or public.query or public.fragment:
        raise ValueError('Invalid public Plaza connection; HTTPS is required.')
    expected = value['publicOrigin'] + PREFIX
    if value.get('plazaUrl') != expected + 'plaza' or value.get('authorizationUrl') != value['publicOrigin'] + '/connect-terminal':
        raise ValueError('Connection URLs must belong to the selected Plaza.')
    rows = value.get('agents')
    if not isinstance(rows, list) or len(rows) > MAX_AGENTS:
        raise ValueError('Invalid public agent directory.')
    ids, origins = set(), {value.get('plazaOrigin')}
    for row in [{'origin': value.get('plazaOrigin')} , *rows]:
        origin = urlsplit(row.get('origin', ''))
        if origin.scheme not in ('http','https') or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment:
            raise ValueError('Invalid logical agent origin.')
        if 'id' not in row:
            continue
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', row['id']) or row['id'] in ids or row['origin'] in origins or row.get('endpoint') != expected + 'agents/' + row['id']:
            raise ValueError('Invalid or duplicate public agent route.')
        ids.add(row['id']); origins.add(row['origin'])
    return value


from phemacast.network.tls import interface_networks, lan_endpoint, pinned_context


class PublicNetworkTransport(httpx.AsyncBaseTransport):
    def __init__(self, connection, *, local_origins=(), network_reader=interface_networks, transport=None, clock=time.monotonic):
        self.connection = connection
        self.routes = {}
        self.apply(connection)
        self.local_origins = set(local_origins)
        from .tls import verify
        self.http = transport or httpx.AsyncHTTPTransport(trust_env=False, verify=verify())
        self.network_reader = network_reader
        self.prefer_local = True
        self.verified = {}
        self.route_status = {}
        self.locks = {}
        self.clock = clock
        self.refreshed_at = clock()
        self.refresh_tried_at = self.refreshed_at - MISS_REFRESH_SECONDS - 1  # the first missing agent refreshes at once
        self.refresh_lock = asyncio.Lock()

    def apply(self, connection):
        """Use this agent list; local endpoints a running Terminal learned for an agent stay with it."""
        routes = {row['origin']:dict(row) for row in connection['agents']}
        for origin, row in routes.items():
            known = self.routes.get(origin)
            if known and known.get('agentId') == row.get('agentId') and known.get('localEndpoints') and not row.get('localEndpoints'):
                row['localEndpoints'] = known['localEndpoints']
        routes[connection['plazaOrigin']] = {'id':'plaza','endpoint':connection['plazaUrl']}
        self.connection, self.routes = connection, routes

    async def refresh(self):
        """Read the site's agent list again. The same site and Plaza only; on any failure the current list stays."""
        url = self.connection['publicOrigin'] + '/.well-known/attas-network.json'
        response = await self.http.handle_async_request(httpx.Request('GET', url, headers={'Accept': 'application/json'}))
        try:
            body = await response.aread()
        finally:
            await response.aclose()
        if response.status_code != 200 or len(body) > MAX_DOCUMENT_BYTES:
            raise ValueError('The site did not return its agent list.')
        value = validate_connection(json.loads(body))
        if value['publicOrigin'] != self.connection['publicOrigin'] or value['plazaOrigin'] != self.connection['plazaOrigin']:
            raise ValueError('The agent list belongs to a different Plaza; connect again.')
        self.apply(value)
        self.refreshed_at = self.clock()
        return value

    async def refresh_if(self, missing=False):
        """Refresh for a missing agent, or when the list is older than REFRESH_SECONDS; never more often than every
        MISS_REFRESH_SECONDS, so a site that is down is not asked on every request."""
        def due():
            now = self.clock()
            return (missing or now - self.refreshed_at > REFRESH_SECONDS) and now - self.refresh_tried_at > MISS_REFRESH_SECONDS
        if not due():
            return False
        async with self.refresh_lock:
            if not due():
                return False  # another request refreshed meanwhile
            self.refresh_tried_at = self.clock()
            try:
                await self.refresh()
                return True
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return False

    def update_endpoints(self, origin, agent_id, endpoints):
        row = self.routes.get(origin)
        if row and row.get('agentId') == agent_id and isinstance(endpoints,list):
            row['localEndpoints'] = endpoints[:8]

    async def handle_async_request(self, request):
        original = request.url
        origin = str(original.copy_with(path='',query=None,fragment=None)).rstrip('/')
        if origin in self.local_origins:
            self.route_status[origin] = {'route':'local','endpoint':origin}
            return await self.http.handle_async_request(request)
        if origin != self.connection['plazaOrigin']:
            await self.refresh_if()
        row = self.routes.get(origin)
        if not row and await self.refresh_if(missing=True):
            row = self.routes.get(origin)
        if not row:
            # References cannot turn the Terminal into an arbitrary public or private proxy: only agents the site
            # lists (Plaza's verified agents) are reachable.
            raise httpx.ConnectError('Destination is not in the selected Plaza connection.',request=request)
        target = row['endpoint']
        transport = self.http
        if self.prefer_local and row.get('kind')=='source' and row.get('agentId'):
            networks = await asyncio.to_thread(self.network_reader)
            for raw in row.get('localEndpoints',[])[:8]:
                candidate = lan_endpoint(raw,networks)
                if not candidate or raw.get('agentId',row['agentId']) != row['agentId']:
                    continue
                key = (candidate['url'],candidate['tlsFingerprintSha256'])
                async with self.locks.setdefault(key,asyncio.Lock()):
                    cached = self.verified.get(key)
                    if not cached or cached[0] < time.monotonic():
                        try:
                            context = await pinned_context(candidate)
                            if cached:await cached[1].aclose()
                            cached = (time.monotonic()+60,httpx.AsyncHTTPTransport(verify=context,trust_env=False))
                            self.verified[key] = cached
                        except (OSError,ValueError,asyncio.TimeoutError,ssl.SSLError):
                            continue
                target, transport = candidate['url'],cached[1]
                break
        def mapped(destination):
            url = httpx.URL(destination+original.path).copy_with(query=original.query or None)
            headers = request.headers.copy()
            headers['host'] = url.netloc.decode()
            # Session cookies belong to the local UI, never an agent service.
            headers.pop('cookie',None)
            return httpx.Request(request.method,url,headers=headers,stream=request.stream,extensions=request.extensions)
        try:
            response = await transport.handle_async_request(mapped(target))
        except (httpx.ConnectError,httpx.ConnectTimeout):
            if transport is self.http:
                raise
            # No request bytes were sent. Never replay after a read/write error
            # or timeout: the source may already have executed a paid operation.
            target = row['endpoint']
            response = await self.http.handle_async_request(mapped(target))
        self.route_status[row['id']] = {'route':'internet' if target==row['endpoint'] else 'lan','endpoint':target}
        return response

    async def aclose(self):
        await self.http.aclose()
        for _,transport in self.verified.values():
            await transport.aclose()
