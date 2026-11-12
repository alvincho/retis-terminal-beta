"""Authenticated Plaza trust decisions, bound to current peer identity and origin.

Directory/card declarations are descriptive. Plaza's authenticated owner decision
authorizes a remote connection; no local or per-agent allowlist is required.
"""
import asyncio
import copy
import hashlib
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .approved_access import BASIS, TRUSTED_ACCOUNT


def directory_entries(payload):
    entries = []
    for plaza in payload.get('plazas', []):
        if plaza.get('online') is not True:
            continue
        for record in plaza.get('agents', []):
            card = record.get('card') or {}
            address = str(card.get('address') or '').rstrip('/')
            try:
                url = urlsplit(address)
                valid = (url.scheme in ('http', 'https') and url.hostname and url.port != 0
                         and not any((url.username, url.password, url.path, url.query, url.fragment))
                         and not any(c.isspace() for c in address) and '\\' not in address)
            except ValueError:
                valid = False
            identity = record.get('agent_id')
            if not valid or not isinstance(identity, str) or not identity:
                continue
            # Plaza resets these top-level fields after evaluating this viewer.
            # Never read trust from the publisher's card/meta or a bare boolean.
            decision = record.get('connection_decision') or {}
            accepted = (decision.get('schema') == 'plaza-connection-decision/1'
                        and decision.get('accepted') is True
                        and decision.get('agent') == identity and decision.get('origin') == address
                        and isinstance(decision.get('viewer'), str) and bool(decision['viewer']))
            owned = accepted and decision.get('basis') == 'registered_owner' and decision.get('owner') == decision['viewer']
            # Plaza-owner approval of the viewer's agent: practices only, never UI or configuration.
            approved = (accepted and decision.get('basis') == BASIS
                        and bool(decision.get('owner')) and decision.get('owner') != decision['viewer'])
            # Another account the viewer chose to trust on Plaza's Agent trust page.
            # Plaza names the verified registrant; card owner fields never count.
            account = (accepted and decision.get('basis') == TRUSTED_ACCOUNT
                       and bool(decision.get('owner')) and decision.get('owner') != decision['viewer'])
            # Plaza could not look up this viewer's trust: no decision either way, not a 'no'.
            evaluated = (payload.get('trust_evaluated') is True
                         and plaza.get('trust_evaluated') is True
                         and record.get('trust_evaluated') is True)
            trusted = (evaluated
                       and (record.get('trust_source') == 'registered_owner' and owned
                            or record.get('trust_source') == BASIS and approved
                            or record.get('trust_source') == TRUSTED_ACCOUNT and account)
                       and record.get('trust_level') in ('trusted', 'approved'))
            scope = 'owner' if owned else 'account' if account else 'practice'
            entries.append({'id': identity, 'name': card.get('name'), 'address': address,
                            'card': card, 'meta': card.get('meta') or {},
                            'availability': record.get('availability'),
                            '_plazaViewer': decision.get('viewer'),
                            '_plazaOwner': decision.get('owner') or None,
                            '_plazaOwnerName': (decision.get('ownerName') or None) if decision.get('owner') else None,
                            '_plazaTrusted': trusted,
                            '_plazaEvaluated': evaluated,
                            '_plazaScope': scope if trusted else None})
    return entries


def decision(row):
    # Copying a whole directory row on every check costs more than the check: one card can be megabytes.
    # The owner stays: a Phema stamped by an agent is accepted only when Plaza names that agent's owner.
    # Whether Plaza evaluated this viewer's trust at all tells a 'not trusted' from a 'could not tell'.
    return {key: row.get(key) for key in ('id', 'address', '_plazaViewer', '_plazaTrusted', '_plazaEvaluated', '_plazaScope', '_plazaOwner')}


class PlazaDirectoryTrust:
    def __init__(self, plaza, client, *, ttl=0):
        self.plaza, self.client = plaza.rstrip('/'), client
        # Optional: reuse each account's answer for `ttl` seconds and share requests
        # made at the same time. Plaza stays the authority; a revoked trust takes
        # effect within `ttl`. Without it, every check asks Plaza.
        self.ttl, self.cached, self.pending = ttl, {}, {}

    async def entries(self, token, *, agent_id=None):
        listed = await self.answer(token, agent_id=agent_id)
        return copy.deepcopy(listed) if self.ttl else listed

    async def decisions(self, token):
        """Plaza's current decision for each listed agent, without copying any agent's card."""
        return [decision(row) for row in await self.answer(token)]

    async def answer(self, token, *, agent_id=None):
        # With `ttl`, the answer is shared by every check for this account; callers only read it.
        if not token or token.startswith('pc-call-'):
            raise HTTPException(403, 'Current Plaza account trust is required for this connection.')
        if not self.ttl:
            return await self.fetch(token, agent_id)
        key = (hashlib.sha256(token.encode()).hexdigest(), agent_id)
        hit = self.cached.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        loop = asyncio.get_running_loop()
        task = self.pending.get((key, id(loop)))
        if task is None:
            task = self.pending[(key, id(loop))] = loop.create_task(self.fetch(token, agent_id))
            task.add_done_callback(lambda done, key=key, loop=loop: self.settle(key, loop, done))
        return await asyncio.shield(task)

    def settle(self, key, loop, task):
        self.pending.pop((key, id(loop)), None)
        if task.cancelled() or task.exception():
            return  # failures are never reused
        now = time.monotonic()
        if len(self.cached) >= 256:
            self.cached = {k: v for k, v in self.cached.items() if v[0] > now}
        self.cached[key] = (now + self.ttl, task.result())

    async def fetch(self, token, agent_id=None):
        response = await self.client.get(self.plaza + '/api/plazas_status',
            headers={'Authorization': 'Bearer ' + token},
            params={'include_supported_pulses': 'true', **({'q': agent_id} if agent_id else {})},
            timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
        response.raise_for_status()
        return directory_entries(response.json())

    async def owner(self, token, record):
        """Plaza's current answer for a content owner record: genuine, and whether this account trusts its owner.

        Reused for `ttl` seconds per account and record, like the directory; failures are never reused.
        """
        if not token or token.startswith('pc-call-'):
            raise HTTPException(403, 'Current Plaza account trust is required for this connection.')
        signature = record.get('signature') if isinstance(record, dict) else ''
        key = ('owner', hashlib.sha256(token.encode()).hexdigest(), str(signature))
        now = time.monotonic()
        hit = self.cached.get(key)
        if self.ttl and hit and hit[0] > now:
            return dict(hit[1])
        response = await self.client.post(self.plaza + '/api/owner-records/verify', headers={'Authorization': 'Bearer ' + token},
                                          json={'record': record}, timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
        response.raise_for_status()
        value = response.json()
        if self.ttl:
            if len(self.cached) >= 256:
                self.cached = {k: v for k, v in self.cached.items() if v[0] > now}
            self.cached[key] = (now + self.ttl, dict(value))
        return value

    async def require(self, agent, token):
        identity, address = agent.get('id'), agent.get('address')
        if not identity or not address:
            raise HTTPException(403, 'An exact registered agent identity and address are required.')
        # A cached caller shares one full answer across agents instead of one per agent.
        listed = await (self.answer(token) if self.ttl else self.answer(token, agent_id=identity))
        rows = [row for row in listed if row['id'] == identity]
        if len(rows) != 1 or rows[0]['address'] != address:
            raise HTTPException(403, 'The agent registration changed or is unavailable in Plaza.')
        if rows[0]['_plazaTrusted'] is not True:
            raise HTTPException(403, 'Plaza could not verify an accepted connection to this agent for this account.')
        return decision(rows[0])
