"""Pair the explicitly installed, same-host source under each signed-in user."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: providers. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
import asyncio
import json
from pathlib import Path
import secrets
import time

from prompits.core.client_sessions import challenge
from phemacast.network.service import descriptor, sessions


class BundledSource:
    def __init__(self, clients, path):
        self.clients, self.path = clients, Path(path)
        self.locks, self.attached = {}, set()
        self.retry_at, self.errors = {}, {}

    async def attach(self, owner):
        if self.retry_at.get(owner, 0) > time.monotonic(): return
        try:
            await asyncio.wait_for(self._attach(owner), timeout=5)
            self.errors.pop(owner, None)
        except Exception:
            # A temporarily unavailable optional source must not prevent Plaza
            # login, access to the settings, or use of other sources.
            self.errors[owner] = 'Local yfinance is unavailable; retrying.'
            self.retry_at[owner] = time.monotonic()+30

    async def _attach(self, owner):
        async with self.locks.setdefault(owner, asyncio.Lock()):
            if owner in self.attached: return
            config = json.loads(self.path.read_text())['local_network']
            resource_id = config['resourceId']
            if any(row['id'] == resource_id for row in self.clients.saved(owner)):
                self.attached.add(owner); return
            endpoint = descriptor(config)
            verifier = secrets.token_urlsafe(48)
            grant = await self.clients.request(endpoint, 'pair', body={'client_name': 'Terminal local yfinance', 'code_challenge': challenge(verifier)})
            # Installation explicitly selected this source. Approval uses its
            # host-owned SQLite state, never a network-accessible admin endpoint.
            if not grant.get('approved'):  # An older source still waits for approval.
                sessions(config).approve(grant['user_code'], owner, True)
            result = await self.clients.request(endpoint, 'token', body={'device_code': grant['device_code'], 'code_verifier': verifier})
            session = result['session']
            resource = await self.clients.request(endpoint, 'resource', token=session['access_token'], method='GET')
            if resource['id'] != resource_id: raise ValueError('Bundled source identity changed.')
            self.clients.store.put(owner, 'local-resource', resource_id, {
                **{key: resource[key] for key in ('name', 'kind', 'resource', 'operations', 'streams')},
                'endpoint': endpoint, 'private': self.clients.store.encrypt(session)})
            self.attached.add(owner)
