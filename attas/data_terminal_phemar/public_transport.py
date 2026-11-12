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


def load_connection(path=None):
    path = path or os.environ.get('ATTAS_PUBLIC_NETWORK_FILE')
    if not path:
        return None
    value = json.loads(Path(path).read_text())
    public = urlsplit(value.get('publicOrigin', ''))
    if value.get('schema') != 'attas.public-network/1' or public.scheme != 'https' or public.username or public.password or public.path or public.query or public.fragment:
        raise ValueError('Invalid public Plaza connection; HTTPS is required.')
    expected = value['publicOrigin'] + PREFIX
    if value.get('plazaUrl') != expected + 'plaza' or value.get('authorizationUrl') != value['publicOrigin'] + '/connect-terminal':
        raise ValueError('Connection URLs must belong to the selected Plaza.')
    rows = value.get('agents')
    if not isinstance(rows, list) or len(rows) > 100:
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
    def __init__(self, connection, *, local_origins=(), network_reader=interface_networks, transport=None):
        self.connection = connection
        self.routes = {row['origin']:dict(row) for row in connection['agents']}
        self.routes[connection['plazaOrigin']] = {'id':'plaza','endpoint':connection['plazaUrl']}
        self.local_origins = set(local_origins)
        from .tls import verify
        self.http = transport or httpx.AsyncHTTPTransport(trust_env=False, verify=verify())
        self.network_reader = network_reader
        self.prefer_local = True
        self.verified = {}
        self.route_status = {}
        self.locks = {}

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
        row = self.routes.get(origin)
        if not row:
            # References cannot turn the Terminal into an arbitrary public or
            # private proxy, nor silently bypass the deployment allowlist.
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
