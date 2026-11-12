"""Paired-resource client with encrypted, owner-scoped server persistence."""
import asyncio
import secrets
import time

import httpx
from fastapi import HTTPException
from prompits.core.client_sessions import challenge
from .discovery import Discovery, PROTOCOL
from .tls import interface_networks, lan_endpoint, pinned_context

UNREACHABLE = 'Local resource is not responding. Check that it is running, then try again.'
# A resource keeps its pending grants across a restart, so a pairing keeps
# waiting this long for it to come back before the poll reports it unreachable.
RESTART_GRACE = 90


class LocalClients:
    def __init__(self, store):
        self.store = store
        self.discovery = Discovery()
        self.pending, self.locks = {}, {}

    async def start(self):
        await self.discovery.start()

    async def close(self):
        await self.discovery.close()
        self.pending.clear()

    def saved(self, owner):
        return [{**row, 'grant': self.store.decrypt(row['private'])} for row in self.store.list(owner, 'local-resource')]

    def resources(self, owner):
        saved = {row['id']: row for row in self.saved(owner)}
        result = []
        for row in self.discovery.resources():
            grant = saved.get(row['id'])
            same = bool(grant and any(e['tlsFingerprintSha256'] == grant['endpoint']['tlsFingerprintSha256'] for e in row['endpoints']))
            result.append({**row, 'paired': same, 'identityChanged': bool(grant and not same)})
            if same:saved.pop(row['id'])
        result.extend({'id': row['id'], 'name': row['name'], 'kind': row['kind'], 'paired': True, 'online': False, 'endpoints': [row['endpoint']]} for row in saved.values())
        return {'status': self.discovery.status, 'resources': result}

    async def request(self, endpoint, path, *, token='', body=None, method='POST'):
        if not lan_endpoint(endpoint, interface_networks()):raise HTTPException(403, 'Resource is no longer on an attached private subnet.')
        try:
            context = await pinned_context(endpoint)
        except ValueError as exc:
            raise HTTPException(409, 'Local resource certificate changed. Pair it again.') from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise HTTPException(503, UNREACHABLE) from exc
        # Independent client: never inherits Plaza sessions, vendor credentials,
        # ambient proxies, redirects, or deployment gateway headers.
        try:
            async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False, timeout=120) as client:
                async with client.stream(method, endpoint['url'] + '/local/v1/' + path, headers={'Authorization': 'Bearer ' + token} if token else {}, json=body) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 16_000_000:raise HTTPException(502, 'Local resource response exceeds limit.')
                    if not response.is_success:raise HTTPException(response.status_code, 'Local resource rejected this request; check pairing and permitted operations.')
                    import json
                    return json.loads(content)
        except httpx.TransportError as exc:
            raise HTTPException(503, UNREACHABLE) from exc

    async def begin(self, owner, resource_id, label):
        row = next((r for r in self.discovery.resources() if r['id'] == resource_id), None)
        if row is None:raise HTTPException(404, 'Select a currently discovered resource.')
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > now}
        if len(self.pending) >= 8:raise HTTPException(429, 'Complete a pending pairing first.')
        endpoint = dict(row['endpoints'][0])
        verifier = secrets.token_urlsafe(48)
        grant = await self.request(endpoint, 'pair', body={'client_name': label[:80], 'code_challenge': challenge(verifier)})
        key = secrets.token_hex(16)
        self.pending[key] = {'owner': owner, 'resource': row, 'endpoint': endpoint, 'grant': grant, 'verifier': verifier, 'expires': now + 300, 'next': 0}
        if grant.get('approved') and (await self.poll(owner, key))['status'] == 'paired':
            # The resource accepts local pairing at once: no code to show and nothing to wait for.
            return {'status': 'paired', 'name': row['name']}
        return {'id': key, 'code': grant['user_code'], 'fingerprint': endpoint['tlsFingerprintSha256'], 'name': row['name'], 'expiresIn': 300}

    async def poll(self, owner, key):
        pending = self.pending.get(key)
        if not pending or pending['owner'] != owner or pending['expires'] < time.monotonic():raise HTTPException(404, 'Pairing request expired.')
        if pending['next'] > time.monotonic():return {'status': 'waiting'}
        pending['next'] = time.monotonic() + 5
        try:
            result = await self.request(pending['endpoint'], 'token', body={'device_code': pending['grant']['device_code'], 'code_verifier': pending['verifier']})
        except HTTPException as exc:
            if exc.status_code != 503:raise
            since = pending.setdefault('unreachableSince', time.monotonic())
            if time.monotonic() - since < RESTART_GRACE:return {'status': 'waiting'}
            raise
        pending.pop('unreachableSince', None)
        if result.get('error') in ('authorization_pending', 'slow_down'):return {'status': 'waiting'}
        if result.get('error'):
            self.pending.pop(key); raise HTTPException(403, 'Pairing was denied.')
        session = result['session']
        descriptor = await self.request(pending['endpoint'], 'resource', token=session['access_token'], method='GET')
        if descriptor['id'] != pending['resource']['id'] or descriptor['protocol'] != PROTOCOL:
            raise HTTPException(409, 'Resource identity changed while pairing.')
        row = {k: descriptor[k] for k in ('name', 'kind', 'resource', 'operations')}
        row.update(endpoint=pending['endpoint'], streams=descriptor.get('streams', []), private=self.store.encrypt(session))
        self.store.put(owner, 'local-resource', descriptor['id'], row)
        self.pending.pop(key)
        return {'status': 'paired'}

    async def credentials(self, owner, id):
        async with self.locks.setdefault((owner, id), asyncio.Lock()):
            row = self.store.get(owner, 'local-resource', id)
            # DHCP changes may update the route only when the approved resource
            # identity AND certificate stay the same. New pins require pairing.
            discovered = next((r for r in self.discovery.resources() if r['id'] == id), None)
            if discovered:
                current = next((e for e in discovered['endpoints'] if e['tlsFingerprintSha256'] == row['endpoint']['tlsFingerprintSha256']), None)
                if current and current != row['endpoint']:
                    row['endpoint'] = current; self.store.put(owner, 'local-resource', id, row)
            session = self.store.decrypt(row['private'])
            if session['expires_at'] < time.time() + 30:
                result = await self.request(row['endpoint'], 'token', body={'refresh_token': session['refresh_token']})
                session = result['session']; row['private'] = self.store.encrypt(session)
                self.store.put(owner, 'local-resource', id, row)
            return row, session['access_token']

    async def authorized_request(self, owner, id, path, body=None, method='POST'):
        row, token = await self.credentials(owner, id)
        return await self.request(row['endpoint'], path, token=token, body=body, method=method)

    async def stream(self, owner, id, operation, parameters):
        row, token = await self.credentials(owner, id)
        if operation not in row.get('streams', []):raise HTTPException(403, 'This resource does not share that stream.')
        endpoint = row['endpoint']
        if not lan_endpoint(endpoint, interface_networks()):raise HTTPException(403, 'Resource left the private subnet.')
        context = await pinned_context(endpoint)
        async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False, timeout=30) as client:
            async with client.stream('POST', endpoint['url']+'/local/v1/stream', headers={'Authorization':'Bearer '+token},
                json={'operation':operation, 'parameters':parameters}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if len(line)>500_000:raise ValueError('Stream event exceeds limit.')
                    if line.startswith('data:'):
                        import json
                        yield json.loads(line[5:])

    async def call(self, owner, id, operation, parameters):
        row = self.store.get(owner, 'local-resource', id)
        if operation not in row['operations']:raise HTTPException(403, 'Operation is not shared by this local resource.')
        return await self.authorized_request(owner, id, 'call', {'operation': operation, 'parameters': parameters})

    async def revoke(self, owner, id):
        """Disconnect always removes the pairing from this Terminal; the resource is told when it can be reached."""
        try:
            await self.authorized_request(owner, id, 'revoke', {})
            revoked = True
        except HTTPException:
            revoked = False  # Stopped, unreachable, or it no longer accepts this pairing.
        self.store.delete(owner, 'local-resource', id)
        return {'status': 'revoked', 'resourceRevoked': revoked}
