"""This computer's local resources as a Terminal uses them: found by Bonjour, used at once, no pairing or tokens.

Every resource listens on 127.0.0.1 (`service.py`). The client keeps no records: what Bonjour lists now is what
exists, and each resource's description (operations, streams, agent views, Phemas) is read again at most once a
minute, so an updated resource shows its changes without anything else to do.
"""
import asyncio
import json
import time

import httpx
from fastapi import HTTPException
from .discovery import Discovery, PROTOCOL

UNREACHABLE = 'Local resource is not responding. Check that it is running, then try again.'
# A resource's description is read again at most this often.
DESCRIBE_EVERY = 60
VIEW_PREFIX = '/local/v2/views/'


class LocalClients:
    def __init__(self, discovery=None):
        self.discovery = discovery or Discovery()
        self.described = {}

    async def start(self):
        await self.discovery.start()

    async def close(self):
        await self.discovery.close()

    def _row(self, id):
        row = next((r for r in self.discovery.resources() if r['id'] == id), None)
        if row is None:raise HTTPException(404, 'This local resource is not running on this computer.')
        return row

    async def request(self, url, path, *, body=None, method='POST', limit=16_000_000, raw=False):
        # Independent client: never inherits Plaza sessions, vendor credentials, ambient proxies or redirects.
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=120) as client:
                async with client.stream(method, url + path, json=body) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:raise HTTPException(502, 'Local resource response exceeds limit.')
                    if not response.is_success:
                        detail = 'Local resource refused this request.'
                        try:detail = json.loads(content).get('detail') or detail
                        except (ValueError, AttributeError):pass
                        raise HTTPException(response.status_code, detail)
                    return bytes(content) if raw else json.loads(content)
        except httpx.TransportError as exc:
            raise HTTPException(503, UNREACHABLE) from exc

    def resources(self):
        """What discovery lists now: {status, resources: [{id, name, kind, url, online}]}."""
        return {'status': self.discovery.status,
                'resources': [{**row, 'online': True} for row in self.discovery.resources()]}

    def running(self, id):
        return any(row['id'] == id for row in self.discovery.resources())

    async def describe(self, id, *, force=False):
        """The resource's own description, read again when it is a minute old (or `force`)."""
        row = self._row(id)
        cached = self.described.get(id)
        if cached and not force and time.monotonic() - cached[0] < DESCRIBE_EVERY and cached[1]['url'] == row['url']:
            return cached[1]
        value = await self.request(row['url'], '/local/v2/resource', method='GET')
        if not isinstance(value, dict) or value.get('id') != id or value.get('protocol') != PROTOCOL:
            raise HTTPException(409, 'A different program answers at this local resource\'s address.')
        described = {'id': id, 'name': value.get('name') or row['name'], 'kind': value.get('kind') or row['kind'],
                     'url': row['url'], 'operations': list(value.get('operations') or []),
                     'streams': list(value.get('streams') or []), 'resource': value.get('resource') or {},
                     'phemas': [p for p in value.get('phemas') or [] if isinstance(p, dict)]}
        self.described[id] = (time.monotonic(), described)
        while len(self.described) > 256:self.described.pop(next(iter(self.described)))
        return described

    async def all_described(self):
        """Every running resource's description; one that does not answer now is left out."""
        rows = self.discovery.resources()
        results = await asyncio.gather(*(self.describe(row['id']) for row in rows), return_exceptions=True)
        return [value for value in results if isinstance(value, dict)]

    async def view_origin(self, id):
        """The resource's origin: what a local publisher's view manifest must name as its origin."""
        return self._row(id)['url']

    async def view_file(self, id, path, limit):
        """One immutable agent-view file the resource serves at `path` (under /local/v2/views/), as bytes."""
        if (not isinstance(path, str) or not path.startswith(VIEW_PREFIX)
                or any(mark in path for mark in ('..', '\\', '%', '#', '?', '//'))):
            raise HTTPException(422, 'Invalid local view file path.')
        url = self._row(id)['url']
        try:
            return await self.request(url, path, method='GET', limit=limit, raw=True)
        except HTTPException as exc:
            if exc.status_code == 503:raise
            if exc.status_code == 502 and 'limit' in str(exc.detail):
                raise HTTPException(502, 'Local view file exceeds its declared size.') from exc
            raise HTTPException(502, 'Local resource did not serve this view file.') from exc

    async def phema(self, id, phema_id):
        """One Phema the resource ships: {id, name, revision, digest, phema}."""
        value = await self.request(self._row(id)['url'], '/local/v2/phemas/' + phema_id, method='GET')
        if not isinstance(value, dict) or value.get('id') != phema_id or not isinstance(value.get('phema'), dict):
            raise HTTPException(502, 'Local resource did not return that Phema.')
        return value

    async def stream(self, id, operation, parameters):
        described = await self.describe(id)
        if operation not in described['streams']:raise HTTPException(403, 'This resource has no such stream.')
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
            async with client.stream('POST', described['url'] + '/local/v2/stream',
                                     json={'operation': operation, 'parameters': parameters}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if len(line) > 500_000:raise ValueError('Stream event exceeds limit.')
                    if line.startswith('data:'):
                        yield json.loads(line[5:])

    async def call(self, id, operation, parameters):
        described = await self.describe(id)
        if operation not in described['operations']:raise HTTPException(403, 'This resource has no such operation.')
        return await self.request(described['url'], '/local/v2/call', body={'operation': operation, 'parameters': parameters})
