"""Authenticated observability APIs, delegated federation and NAT-safe diagnostics."""
import asyncio
import json
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit, quote, urlencode

import httpx
from fastapi import HTTPException, Request, Query
from fastapi.responses import JSONResponse, FileResponse
from starlette.concurrency import run_in_threadpool

from .observability import ObservationStore, state_path

from .json_response import NativeJSONResponse

STATIC = Path(__file__).parents[1] / 'agents/static/observability'


def plaza_url(value):
    """Canonical endpoint without credentials, query secrets or UI suffixes."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError('Enter a Plaza URL of at most 2048 characters.')
    value = value.strip()
    if re.search(r'[\s\\\x00-\x1f\x7f]', value):
        raise ValueError('Enter a valid Plaza URL.')
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
        if (parts.scheme not in ('http', 'https') or not host or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or not re.fullmatch(r'[a-zA-Z0-9.:-]+', host) or port == 0):
            raise ValueError()
    except ValueError:
        raise ValueError('Use a Plaza HTTP(S) URL without credentials, query parameters or fragments.') from None
    host = host.lower().rstrip('.')
    if not host:
        raise ValueError('Enter a valid Plaza hostname.')
    if parts.scheme == 'http' and host not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Use HTTPS for a remote Plaza. HTTP is allowed on this computer only.')
    if host == 'localhost':
        host = '127.0.0.1'
    netloc = '[' + host + ']' if ':' in host else host
    if port and port != (443 if parts.scheme == 'https' else 80):
        netloc += ':' + str(port)
    path = parts.path.rstrip('/')
    for suffix in ('/observability', '/plazas'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return parts.scheme + '://' + netloc + path


def merge_relationship(links, key, endpoints, observation):
    link = links.setdefault(key, {**endpoints, 'calls': 0, 'errors': 0,
                                 'total_duration_ms': 0, 'duration_samples': 0})
    link['calls'] += observation.get('calls', 1)
    link['errors'] += observation.get('errors', int(observation.get('status') in ('failed', 'denied', 'error')))
    duration = observation.get('duration_ms')
    link['total_duration_ms'] += observation.get('total_duration_ms', duration if isinstance(duration, (int, float)) else 0)
    link['duration_samples'] += observation.get('duration_samples', int(isinstance(duration, (int, float))))
    first = observation.get('first_seen') or observation.get('timestamp')
    last = observation.get('last_seen') or observation.get('timestamp')
    if first:
        link['first_seen'] = min(link.get('first_seen', first), first)
    if last:
        link['last_seen'] = max(link.get('last_seen', last), last)


def relationship_links(links):
    for link in links.values():
        count, total = link.pop('duration_samples'), link.pop('total_duration_ms')
        if count:
            link['mean_duration_ms'] = round(total / count, 2)
    return list(links.values())


def install_agent_observability(agent):
    @agent.app.middleware('http')
    async def observe_request(request: Request, call_next):
        if request.url.path.startswith(('/api/observability', '/observability')) or not agent._should_track_request_work(request):
            return await call_next(request)
        with agent.observations.span(request.method + ' ' + request.url.path, kind='http',
                                     traceparent=request.headers.get('traceparent')):
            response = await call_next(request)
            if response.status_code >= 400:
                agent.observations.metric('http_errors_total', 1, kind='counter')
            return response

    @agent.app.get('/api/agent/diagnostics/logs')
    async def owner_logs(request: Request):
        await agent._authorize_shared_agent_settings_write(request)
        return JSONResponse(await run_in_threadpool(agent.observations.store.logs, log_query(dict(request.query_params))),
                            headers={'Cache-Control': 'no-store'})

    @agent.app.on_event('shutdown')
    def release_log_handler():
        from prompits.agents.base import logger
        logger.removeHandler(agent._observation_log_handler)
        agent._observation_log_handler.close()


def log_query(value):
    result = {}
    for key in ('after', 'before', 'since', 'until', 'limit'):
        if value.get(key) not in (None, ''):
            try:
                result[key] = float(value[key]) if key in ('since', 'until') else int(value[key])
            except (ValueError, TypeError, OverflowError):
                raise HTTPException(422, 'Invalid log query.') from None
            if not math.isfinite(result[key]) or result[key] < 0 or result[key] > 10**15:
                raise HTTPException(422, 'Invalid log query.')
    result['limit'] = max(1, min(result.get('limit', 100), 500))
    for key in ('job_id', 'trace_id', 'span_id', 'level'):
        if value.get(key):
            result[key] = str(value[key])[:100]
    if result.get('level') and result['level'] not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        raise HTTPException(422, 'Invalid log severity.')
    return result


class PlazaObservability:
    def __init__(self, plaza, store=None):
        self.plaza = plaza
        config = (getattr(plaza, 'raw_config', {}) or {}).get('observability') or {}
        self.store = store or ObservationStore(config.get('state_path') or state_path('plazas', str(plaza.port)))
        self.stale_after = max(10, float(config.get('stale_after_sec', 45)))
        self.offline_after = max(self.stale_after + 1, float(config.get('offline_after_sec', 120)))
        self.remote_timeout = max(3, min(60, float(config.get('remote_timeout_sec', 30))))
        self.sources = {'local': {'id': 'local', 'name': config.get('name', plaza.name), 'url': '', 'local': True}}
        for source in config.get('plazas', [])[:20]:
            source_id = str(source.get('id', ''))
            url = str(source.get('url', '')).rstrip('/')
            parts = urlsplit(url)
            if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', source_id) or source_id == 'local'
                    or parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment):
                raise ValueError('Invalid observability Plaza source configuration.')
            if parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('Remote Plaza connections require HTTPS.')
            self.sources[source_id] = {'id': source_id, 'name': str(source.get('name', source_id))[:80], 'url': url, 'local': False}
        self.connection_locks = {}
        self.address_aliases = {}
        for alias, target in list((config.get('address_aliases') or {}).items())[:80]:
            if all(urlsplit(url).scheme in ('http','https') and urlsplit(url).hostname
                   and not urlsplit(url).username and not urlsplit(url).password
                   and not urlsplit(url).query and not urlsplit(url).fragment for url in (alias,target)):
                self.address_aliases[alias.rstrip('/')] = target.rstrip('/')

    def sources_for(self, user):
        sources = {key: {**value, 'removable': False} for key, value in self.sources.items()}
        for value in self.store.sources(user['id']):
            if value['id'] not in sources:
                sources[value['id']] = {**value, 'local': False, 'removable': True}
        return sources

    def source_for(self, user, source_id):
        return self.sources_for(user).get(source_id)

    def add_source(self, user, value):
        url = plaza_url(value.get('url'))
        name = value.get('name', '')
        if not isinstance(name, str) or len(name) > 80 or re.search(r'[\x00-\x1f\x7f]', name):
            raise ValueError('Use a Plaza name of at most 80 characters.')
        existing = [source.get('url') for source in self.sources_for(user).values() if not source['local']]
        existing.append(self.plaza.agent_card.get('address') or self.plaza._resolve_advertised_address())
        existing.append('http://127.0.0.1:' + str(self.plaza.port))
        for endpoint in existing:
            try:
                if plaza_url(endpoint) == url:
                    raise HTTPException(409, 'This Plaza is already in your list.')
            except ValueError:
                continue
        return {**self.store.add_source(user['id'], name.strip() or urlsplit(url).hostname, url),
                'local': False, 'removable': True, 'connected': False}

    def attach(self):
        practice = self.plaza._get_plaza_practice()
        if practice:
            practice.state.observation_store = self.store
        return practice.state if practice else None

    def call_user(self, request, grant_token):
        """An agent serving a user's call reads as that user, never seeing more than its owner can.

        The agent authenticates with its own Plaza token and presents the call grant it
        received. Plaza accepts it only while the grant is live, addressed to that agent and
        bearing a user, and only when the agent's owner is that user or the Plaza owner.
        The account reads as itself: an administrator sees what an administrator sees here.
        """
        from fastapi.security import HTTPAuthorizationCredentials
        from .approved_access import card_owner
        state = self.attach()
        try:
            actor = state.verify_token(HTTPAuthorizationCredentials(scheme='Bearer', credentials=self.plaza._extract_bearer_token(request)))
            grant = self.plaza.call_grants.read(grant_token, include_consumed=True)
        except (HTTPException, PermissionError):
            raise HTTPException(401, 'Call grant expired or revoked.') from None
        user_id = (grant.get('user') or {}).get('id')
        owner = card_owner(state.agent_cards.get(actor['agent_id']))
        if grant['audience'] != actor['agent_id'] or grant.get('authority') or not user_id or owner not in (user_id, self.owner_id()):
            raise HTTPException(403, 'This agent cannot read observability for that account.')
        profile = self.plaza._get_ui_user(user_id, include_preferences_fallback=False, include_credit_ledger=False)
        if not profile or not self.plaza._ui_user_status_is_active(profile):
            raise HTTPException(401, 'This account is disabled.')
        role = 'admin' if str(profile.get('role') or '').strip().lower() in ('admin', 'owner') else 'user'
        return {**profile, 'role': role, 'client_session_id': 'call-grant:' + grant['id']}

    def user(self, request, *, normal=False):
        # A pc-call Bearer token would be consumed by Plaza's own request middleware,
        # so the grant travels in its own header beside the agent's token.
        grant_token = request.headers.get('x-call-grant', '')
        if grant_token:
            if normal:
                raise HTTPException(403, 'Use your normal Plaza browser session for this action.')
            return self.call_user(request, grant_token)
        token = self.plaza._extract_bearer_token(request)
        if token.startswith('obs-access-'):
            if normal:
                raise HTTPException(403, 'Use your normal Plaza browser session for this action.')
            owner = self.store.resolve_device(token)
            profile = self.plaza._get_ui_user(owner, include_preferences_fallback=False, include_credit_ledger=False) if owner else None
            if not profile or not self.plaza._ui_user_status_is_active(profile):
                raise HTTPException(401, 'Monitoring connection expired or revoked.')
            return profile
        _, _, profile = self.plaza._get_authenticated_ui_context(request)
        if normal and profile.get('client_session_id'):
            raise HTTPException(403, 'Use your normal Plaza browser session for this action.')
        return profile

    def owner_id(self):
        return (os.environ.get('PROMPITS_AGENT_TRUST_OWNER_ID') or os.environ.get('PROMPITS_CLIENT_ENROLLMENT_OWNER_ID')
                or ((getattr(self.plaza, 'raw_config', {}) or {}).get('plaza_owner_account') or {}).get('user_id'))

    def is_admin(self, user):
        owner_id = self.owner_id()
        return user.get('role') in ('admin', 'owner') or user['id'] == owner_id

    def self_agent_ids(self):
        state = self.attach()
        endpoint = str(self.plaza.agent_card.get('address') or self.plaza._resolve_advertised_address()).rstrip('/')
        with state.lock:
            registered_id = state.agent_ids.get(self.plaza.name)
            configured_id = self.plaza.agent_card.get('agent_id')
            return [identity for identity, card in state.agent_cards.items()
                    if (identity.startswith('plaza:') or identity in (registered_id, configured_id))
                    and card.get('name') == self.plaza.name
                    and str(card.get('address', '')).rstrip('/') == endpoint]

    def observe_self(self):
        state = self.attach()
        snapshot = self.plaza.observations.snapshot()
        with state.lock:
            snapshot['metrics'].extend([
                {'name':'plaza_registered_agents','value':len(state.agent_cards),'unit':'count','kind':'gauge','labels':{}},
                {'name':'plaza_active_agents','value':sum(time.time()-state.last_active.get(identity, 0) < self.offline_after for identity in state.agent_cards),'unit':'count','kind':'gauge','labels':{}}
            ])
        for identity in self.self_agent_ids():
            self.store.ingest(identity, snapshot)

    def registered_owners(self):
        """Each agent's owner as Plaza verified it at registration; never the owner an agent card claims."""
        try:
            with self.plaza._registered_agent_owners().connect() as db:
                return dict(db.execute('SELECT agent, owner FROM registered_agent_owners').fetchall())
        except Exception:
            return {}

    def owner_names(self, owners):
        names = {}
        for owner in owners:
            if not owner:
                continue
            try:
                profile = self.plaza._get_ui_user(owner, include_preferences_fallback=False, include_credit_ledger=False) or {}
            except Exception:
                profile = {}
            names[owner] = str(profile.get('display_name') or profile.get('username') or '')[:80]
        return names

    def visible_agents(self, user):
        """The agents this account may see.

        Everyone sees the agents Plaza registered to them and the ones they trust or monitor.
        An administrator sees every agent that signs in to this Plaza. An approved agent that
        has not connected yet appears until it first connects; one that connected and then left
        the directory is gone from here, though its approval stays.
        """
        state = self.attach()
        if not state:
            return []
        with state.lock:
            cards = {key: dict(card) for key, card in state.agent_cards.items()}
            last_active = dict(state.last_active)
            pit_types = dict(state.pit_types)
            signed_in = {identity for identity in state.credentials_by_id
                         if pit_types.get(identity) not in ('Pulse', 'AgentJob')}
        admin = self.is_admin(user)
        registered = self.registered_owners()
        known_identities = {(c.get('meta') or {}).get('trusted_agent_identity') for c in cards.values()}
        enrollments = getattr(self.plaza, 'agent_trust_requests', None)
        if enrollments:
            with enrollments.connect() as db:
                grants = [dict(r) for r in db.execute("SELECT * FROM client_enrollments WHERE status='approved'")]
            for grant in grants:
                identity = grant['installation']
                if (identity in known_identities or grant.get('claimed')
                        or not (admin or grant['requester'] == user['id'])):
                    continue
                try:
                    enrollments.authorize_installation(identity, grant['requester'])
                except HTTPException:
                    continue
                cards['approved:' + grant['id']] = {'name': grant['label'], 'owner_user_id': grant['requester'],
                    'meta': {'trusted_agent_identity': identity}, 'registration_state': 'not_registered'}
        trusted_entries = self.plaza._list_ui_trust_entry_records(user_id=user['id'], include_inactive=False)
        owner_id = self.owner_id()
        global_entries = self.plaza._list_ui_trust_entry_records(user_id=owner_id, include_inactive=False) if owner_id and owner_id != user['id'] else trusted_entries
        snapshots, result, now = self.store.snapshots(), [], time.time()
        monitoring_agents = self.store.monitoring_agents(user['id'])
        rows = []
        for agent_id, card in cards.items():
            meta = card.get('meta') or {}
            owner = card.get('owner_user_id') or meta.get('owner_user_id') or card.get('owner_id')
            registered_owner = registered.get(agent_id)
            entry = {'id': agent_id, 'agent_id': agent_id, 'name': card.get('name', agent_id), 'card': card, 'meta': meta}
            own_match = [v for v in trusted_entries if self.plaza._trust_entry_matches_agent(v, entry)]
            global_match = [v for v in global_entries if self.plaza._trust_entry_matches_agent(v, entry)]
            # Never accept trust flags from the agent card.
            matches = own_match or global_match
            blocked = any(v.get('trust_level') in ('blocked', 'untrusted') for v in matches)
            approved = any(v.get('trust_level') in ('trusted', 'approved') and v.get('status') == 'active' for v in matches)
            identity = meta.get('trusted_agent_identity')
            if identity:
                try:
                    self.plaza.agent_trust_requests.authorize_installation(identity, owner)
                    approved = True
                except HTTPException:
                    approved = False
            monitored = agent_id in monitoring_agents
            owned = bool(registered_owner) and registered_owner == user['id']
            listed = not blocked and (approved or monitored) and (admin or owner == user['id'] or own_match)
            if not (listed or (owned and not blocked) or (admin and agent_id in signed_in)):
                continue
            # Plaza's own registration record decides whose agent this is.
            owner = registered_owner or owner
            telemetry = snapshots.get(agent_id)
            received = max((telemetry or {}).get('received_at', 0), last_active.get(agent_id, 0))
            age = max(0, now - received) if received else None
            interval = float((telemetry or {}).get('heartbeat_interval_sec') or 10)
            stale = max(self.stale_after, interval * 3)
            offline = max(self.offline_after, interval * 6)
            connectivity = 'unknown' if age is None else 'online' if age <= stale else 'stale' if age <= offline else 'offline'
            telemetry_age = max(0, now - telemetry['received_at']) if telemetry else None
            details_allowed = owner == user['id'] or self.is_admin(user)
            observed = dict(telemetry or {})
            # Keep private labels and job metadata behind ownership permission.
            if not details_allowed:
                observed['jobs'] = [{'id': j.get('id'), 'status': j.get('status'), 'kind': j.get('kind', 'job')}
                                    for j in observed.get('jobs', [])]
                observed.pop('status_message', None)
                observed.pop('display_name', None)
            tags = card.get('tags') if isinstance(card.get('tags'), list) else []
            rows.append({'id': agent_id, 'name': str(card.get('name', agent_id)),
                         'type': state.infer_runtime_pit_type(pit_types.get(agent_id), card, default='Agent'),
                         'owner_id': owner, 'trusted': approved, 'monitoring_authorized': True, 'connectivity': connectivity,
                         'registration_state': card.get('registration_state', 'registered'),
                         'heartbeat_age_sec': round(age, 1) if age is not None else None,
                         'telemetry_supported': bool(telemetry), 'telemetry_stale': connectivity != 'online' or telemetry_age is None or telemetry_age > stale,
                         'telemetry_age_sec': round(telemetry_age, 1) if telemetry_age is not None else None,
                         'logs_allowed': details_allowed, 'endpoint': str(card.get('address', '')) if details_allowed else '',
                         # What the agent's card says about itself, as the directory lists it.
                         'description': str(card.get('description') or '')[:400], 'version': str(card.get('version') or '')[:40],
                         'tags': [str(tag)[:40] for tag in tags if isinstance(tag, (str, int, float))][:12],
                         'observability': observed})
        # Owner names go only to the owner and administrators; others see an agent's owner as another account.
        names = self.owner_names({row['owner_id'] for row in rows if row['logs_allowed']})
        for row in rows:
            row['owner_name'] = names.get(row['owner_id'], '') if row['logs_allowed'] else ''
            result.append(row)
        return result

    def require_agent(self, user, agent_id, *, logs=False):
        agent = next((v for v in self.visible_agents(user) if v['id'] == agent_id), None)
        if not agent or logs and not agent['logs_allowed']:
            raise HTTPException(403, 'This trusted agent is unavailable to your account.')
        return agent

    def local_snapshot(self, user, call_window_sec=None):
        self.observe_self()
        agents = self.visible_agents(user)
        permitted = [v['id'] for v in agents if v['logs_allowed']]
        events = self.store.events(permitted)
        until = self.store.clock()
        since = until - call_window_sec if call_window_sec is not None else None
        relationships = self.store.relationships(permitted, since=since, until=until)
        visible_ids = {v['id'] for v in agents}
        # Do not leak peer identities outside the viewer's trusted set.
        endpoints = {v['id']: v.get('endpoint', '').rstrip('/') for v in agents}
        endpoint_ids = {endpoint: identity for identity, endpoint in endpoints.items() if endpoint}
        for event in [*events, *relationships]:
            peer = event.get('peer_agent_id')
            origin = str(event.get('peer_origin') or '').rstrip('/')
            origin = self.address_aliases.get(origin, origin)
            if origin:
                event['peer_origin'] = origin
            if not peer and origin in endpoint_ids:
                peer = event['peer_agent_id'] = endpoint_ids[origin]
            if peer and (peer not in visible_ids or origin and endpoints.get(peer) and origin != endpoints[peer]):
                event['peer_ref'] = hashlib.sha256(event['peer_agent_id'].encode()).hexdigest()
                event['peer_agent_id'] = ''
        links = {}
        for event in relationships:
            peer = event.get('peer_agent_id')
            if not peer or peer not in visible_ids:
                continue
            key = (event['agent_id'], peer)
            merge_relationship(links, key, {'source': key[0], 'target': key[1]}, event)
        self_id = next((identity for identity in self.self_agent_ids() if identity in visible_ids), None)
        return {'id': 'local', 'name': self.sources['local']['name'], 'status': 'connected',
                'address': str(self.plaza.agent_card.get('address') or self.plaza._resolve_advertised_address() or ''),
                'plaza_agent_id': self_id,
                'agents': agents, 'events': events, 'relationships': relationships,
                'links': relationship_links(links), 'generated_at': time.time(),
                'call_window_sec': call_window_sec, 'calls_since': since, 'calls_until': until}

    async def remote(self, user, source_id, path, *, body=None, method=None):
        source = await run_in_threadpool(self.source_for, user, source_id)
        if not source or source['local']:
            raise HTTPException(404, 'Unknown Plaza.')
        connection = await run_in_threadpool(self.store.connection, user['id'], source_id)
        token = connection.get('access_token')
        if not token:
            raise HTTPException(401, 'Connect this Plaza to monitor its agents.')
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.remote_timeout, connect=min(5, self.remote_timeout)), follow_redirects=False) as client:
            response = await client.request(method or ('POST' if body is not None else 'GET'), source['url'] + path,
                                            json=body, headers={'Authorization': 'Bearer ' + token})
        if response.status_code >= 400:
            raise HTTPException(response.status_code if response.status_code in (401, 403, 404) else 502,
                                'Remote Plaza denied access.' if response.status_code in (401, 403) else 'Remote Plaza unavailable.')
        if len(response.content) > 4 * 1024 * 1024:
            raise HTTPException(502, 'Remote Plaza response is too large.')
        return response.json()

    async def snapshot(self, user, call_window_sec=None):
        async def read(source):
            try:
                if source['local']:
                    return await run_in_threadpool(self.local_snapshot, user, call_window_sec)
                path = '/api/observability/local'
                if call_window_sec is not None:
                    path += '?call_window_sec=' + str(call_window_sec)
                value = await self.remote(user, source['id'], path)
                if (not isinstance(value, dict)
                        or any(not isinstance(value.get(field), list) or any(not isinstance(v, dict) for v in value[field])
                               for field in ('agents', 'events', 'links'))
                        or any(not isinstance(a.get('id'), str) for a in value['agents'])):
                    raise ValueError('Invalid remote observation snapshot.')
                if call_window_sec is not None and (
                        value.get('call_window_sec') != call_window_sec or 'relationships' not in value):
                    raise HTTPException(409, 'This Plaza does not support the call time range. Update it or clear Past.')
                if 'relationships' in value and (
                        not isinstance(value['relationships'], list) or any(
                            not isinstance(row, dict)
                            or not isinstance(row.get('agent_id'), str)
                            or any(not isinstance(row.get(field, ''), str) for field in ('peer_agent_id', 'peer_origin', 'peer_ref'))
                            or any(type(row.get(field, 0)) is not int or row.get(field, 0) < 0 for field in ('calls', 'errors', 'duration_samples'))
                            or any(not isinstance(row.get(field, 0), (int, float)) or not math.isfinite(row.get(field, 0))
                                   or row.get(field, 0) < 0 for field in ('total_duration_ms', 'first_seen', 'last_seen'))
                            for row in value['relationships'])):
                    raise ValueError('Invalid remote relationship snapshot.')
                return {**value, 'id': source['id'], 'name': source['name'], 'url': source.get('url', '')}
            except (HTTPException, httpx.HTTPError, ValueError) as exc:
                return {'id': source['id'], 'name': source['name'], 'url': source.get('url', ''), 'agents': [], 'links': [], 'events': [],
                        'status': 'disconnected' if isinstance(exc, HTTPException) and exc.status_code in (401, 403) else 'unavailable',
                        'error': exc.detail if isinstance(exc, HTTPException) else 'Remote Plaza timed out. Retry with Refresh.' if isinstance(exc, httpx.TimeoutException) else 'Connection unavailable'}
        sources = await run_in_threadpool(self.sources_for, user)
        plazas = await asyncio.gather(*(read(v) for v in sources.values()))
        visible = {}
        visible_origins = {}
        for plaza in plazas:
            for agent in plaza['agents']:
                candidate = (plaza['id'], agent['id'], agent.get('endpoint', '').rstrip('/'))
                visible.setdefault(hashlib.sha256(agent['id'].encode()).hexdigest(), []).append(candidate)
                if candidate[2]:
                    visible_origins.setdefault(candidate[2], []).append(candidate)
        def resolve_peer(event):
            candidates = visible.get(event.get('peer_ref'), [])
            origin = str(event.get('peer_origin') or '').rstrip('/')
            if origin:
                matching = [c for c in candidates if c[2] == origin]
                if matching or any(c[2] for c in candidates):
                    candidates = matching
                if not event.get('peer_ref') and not event.get('peer_agent_id'):
                    candidates = visible_origins.get(origin, [])
            return candidates[0] if len(candidates) == 1 else None
        cross_links = {}
        for plaza in plazas:
            relationships = plaza.get('relationships')
            if relationships is None:
                relationships = [dict(e) for e in plaza['events'] if e.get('event') == 'interaction'
                                 and e.get('direction') == 'outbound' and e.get('phase') == 'result']
            for event in plaza['events']:
                peer = resolve_peer(event)
                if peer:
                    event['peer_source'], event['peer_agent_id'] = peer[:2]
            # Older Plazas may only expose recent events. Updated peers provide
            # independently retained, deduplicated relationship totals.
            authorized = {a['id'] for a in plaza['agents']}
            for event in relationships:
                peer = resolve_peer(event)
                if not peer or event.get('agent_id') not in authorized:
                    continue
                target_source, target, _ = peer
                if target_source == plaza['id']:
                    continue
                event['peer_agent_id'], event['peer_source'] = target, target_source
                identity = (plaza['id'], event['agent_id'], target_source, target)
                merge_relationship(cross_links, identity,
                    {'source_plaza': plaza['id'], 'source': event['agent_id'], 'target_plaza': target_source, 'target': target}, event)
        return {'schema_version': 1, 'plazas': plazas, 'links': relationship_links(cross_links),
                'call_window_sec': call_window_sec,
                'generated_at': time.time(), 'user': {'id': user['id'], 'name': user.get('display_name') or user.get('email') or user['id']}}


def install_plaza_observability(plaza):
    service = plaza.observability = PlazaObservability(plaza)
    service.attach()

    async def user(request, normal=False):
        return await run_in_threadpool(service.user, request, normal=normal)

    async def body(request, limit=8192):
        raw = await request.body()
        if len(raw) > limit:
            raise HTTPException(413, 'Request too large.')
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise HTTPException(422, 'Expected a JSON object.') from None

    @plaza.app.get('/observability')
    async def dashboard():
        return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"})

    @plaza.app.get('/observability/assets/{name}')
    async def assets(name: str):
        if name not in ('dashboard.js', 'dashboard.css', 'graph.js', 'jobs.js', 'job-logs.js'):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @plaza.app.get('/api/observability/snapshot')
    async def snapshot(request: Request, call_window_sec: int = Query(default=None, ge=60, le=604800)):
        value = await service.snapshot(await user(request), call_window_sec)
        return await run_in_threadpool(NativeJSONResponse, value, headers={'Cache-Control': 'no-store'})

    @plaza.app.get('/api/observability/local')
    async def local(request: Request, call_window_sec: int = Query(default=None, ge=60, le=604800)):
        value = await run_in_threadpool(service.local_snapshot, await user(request), call_window_sec)
        return await run_in_threadpool(NativeJSONResponse, value, headers={'Cache-Control': 'no-store'})

    @plaza.app.get('/api/observability/sources')
    async def sources(request: Request):
        actor = await user(request)
        def read():
            return {'sources': [{**v, 'connected': v['local'] or bool(service.store.connection(actor['id'], v['id']).get('access_token'))}
                                for v in service.sources_for(actor).values()]}
        return await run_in_threadpool(read)

    @plaza.app.post('/api/observability/sources', status_code=201)
    async def add_source(request: Request):
        actor, value = await user(request, True), await body(request)
        try:
            return await run_in_threadpool(service.add_source, actor, value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @plaza.app.delete('/api/observability/sources/{source_id}')
    async def remove_source(source_id: str, request: Request):
        actor = await user(request, True)
        lock = service.connection_locks.setdefault((actor['id'], source_id), asyncio.Lock())
        async with lock:
            source = await run_in_threadpool(service.source_for, actor, source_id)
            if not source:
                raise HTTPException(404, 'Unknown Plaza.')
            if not source['removable']:
                raise HTTPException(403, 'This Plaza is managed by the server configuration.')
            try:
                await asyncio.wait_for(service.remote(actor, source_id, '/api/observability/device/revoke', body={}), 3)
            except (HTTPException, httpx.HTTPError, ValueError, asyncio.TimeoutError):
                pass
            await run_in_threadpool(service.store.remove_source, actor['id'], source_id)
        return {'status': 'removed'}

    @plaza.app.get('/api/observability/preferences')
    async def preferences(request: Request):
        return await run_in_threadpool(service.store.preferences, (await user(request))['id'])

    @plaza.app.post('/api/observability/monitoring-grants')
    async def grant_monitoring(request: Request):
        actor, value = await user(request, True), await body(request)
        if not service.is_admin(actor):
            raise HTTPException(403, 'Only the Plaza administrator can enroll deployment agents for monitoring.')
        agents = value.get('agents')
        if not isinstance(agents, list) or len(agents) > 200 or any(not isinstance(v, str) or len(v) > 200 for v in agents):
            raise HTTPException(422, 'Provide up to 200 registered agent IDs.')
        state = service.attach()
        with state.lock:
            if any(agent not in state.agent_cards for agent in agents):
                raise HTTPException(404, 'An agent is no longer registered.')
        service.store.grant_monitoring(actor['id'], agents)
        plaza.observations.log('INFO', 'Deployment agents enrolled for monitoring', viewer_id=actor['id'], count=len(agents))
        return {'status':'enrolled', 'count':len(agents), 'execution_trust_changed':False}

    @plaza.app.delete('/api/observability/monitoring-grants/{agent_id}')
    async def revoke_monitoring(agent_id: str, request: Request):
        actor = await user(request, True)
        with service.store.connect() as db:
            db.execute('DELETE FROM monitoring_grants WHERE owner=? AND agent=?', (actor['id'], agent_id))
        return {'status':'revoked'}

    @plaza.app.put('/api/observability/preferences')
    async def save_preferences(request: Request):
        actor, value = await user(request, True), await body(request)
        try:
            return await run_in_threadpool(service.store.preferences, actor['id'], value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @plaza.app.post('/api/observability/sources/{source_id}/connect')
    async def connect(source_id: str, request: Request):
        actor = await user(request, True)
        lock = service.connection_locks.setdefault((actor['id'], source_id), asyncio.Lock())
        async with lock:
            source = await run_in_threadpool(service.source_for, actor, source_id)
            if not source or source['local']:
                raise HTTPException(404, 'Unknown remote Plaza.')
            from .client_sessions import challenge
            verifier = secrets.token_urlsafe(48)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(service.remote_timeout, connect=min(5, service.remote_timeout)), follow_redirects=False) as client:
                    response = await client.post(source['url'] + '/api/observability/device', json={'challenge': challenge(verifier)})
                if response.status_code != 200:
                    raise ValueError()
                value = response.json()
                if not isinstance(value, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,128}', str(value.get('code', ''))):
                    raise ValueError()
            except (httpx.HTTPError, ValueError):
                raise HTTPException(502, 'Cannot connect. Check the Plaza URL and that it supports monitoring.') from None
            await run_in_threadpool(service.store.connection, actor['id'], source_id,
                                   {'code': value['code'], 'verifier': verifier, 'expires_at': time.time() + 300})
            return {'approval_url': source['url'] + '/observability?approve=' + value['code'], 'status': 'pending'}

    @plaza.app.post('/api/observability/sources/{source_id}/poll')
    async def poll(source_id: str, request: Request):
        actor = await user(request, True)
        lock = service.connection_locks.setdefault((actor['id'], source_id), asyncio.Lock())
        async with lock:
            source = await run_in_threadpool(service.source_for, actor, source_id)
            if not source or source['local']:
                raise HTTPException(404)
            value = await run_in_threadpool(service.store.connection, actor['id'], source_id)
            if value.get('access_token'):
                return {'status': 'connected'}
            if not value.get('code') or value.get('expires_at', 0) <= time.time():
                raise HTTPException(400, 'Monitoring approval expired. Connect again.')
            async with httpx.AsyncClient(timeout=httpx.Timeout(service.remote_timeout, connect=min(5, service.remote_timeout)), follow_redirects=False) as client:
                response = await client.post(source['url'] + '/api/observability/device/exchange',
                                             json={'code': value['code'], 'verifier': value['verifier']})
            if response.status_code != 200:
                raise HTTPException(502, 'Monitoring connection failed.')
            result = response.json()
            if result.get('status') == 'connected':
                await run_in_threadpool(service.store.connection, actor['id'], source_id, result)
            return {'status': result.get('status', 'pending')}

    @plaza.app.delete('/api/observability/sources/{source_id}/connect')
    async def disconnect(source_id: str, request: Request):
        actor = await user(request, True)
        try:
            await service.remote(actor, source_id, '/api/observability/device/revoke', body={})
        except (HTTPException, httpx.HTTPError):
            pass
        service.store.disconnect(actor['id'], source_id)
        return {'status': 'disconnected'}

    @plaza.app.post('/api/observability/device')
    async def device(request: Request):
        value = await body(request)
        try:
            return service.store.begin_device(value.get('challenge'))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @plaza.app.post('/api/observability/device/approve')
    async def approve(request: Request):
        actor, value = await user(request, True), await body(request)
        if not isinstance(value.get('approved'), bool):
            raise HTTPException(422, 'Approval must be a boolean.')
        if not service.store.approve_device(value.get('code'), actor['id'], value['approved']):
            raise HTTPException(400, 'Approval expired or already decided.')
        return {'status': 'approved' if value['approved'] else 'denied'}

    @plaza.app.post('/api/observability/device/exchange')
    async def exchange(request: Request):
        value = await body(request)
        try:
            return service.store.exchange_device(value.get('code'), value.get('verifier'))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @plaza.app.post('/api/observability/device/revoke')
    async def revoke(request: Request):
        await user(request)
        service.store.revoke_device(plaza._extract_bearer_token(request))
        return {'status': 'revoked'}

    @plaza.app.get('/api/observability/plazas/{source_id}/agents/{agent_id:path}/jobs')
    async def jobs(source_id: str, agent_id: str, request: Request,
                   state: str = Query('all', pattern='^(all|active|finished)$'),
                   activity: str = Query('work', pattern='^(work|requests|all)$'),
                   status: str = Query('all', pattern='^(all|succeeded|failed|cancelled|expired)$'),
                   search: str = Query('', max_length=160), since: float = Query(None, ge=0),
                   cursor: str = Query('', max_length=64), limit: int = Query(50, ge=1, le=100),
                   peer_id: str = Query('', max_length=200), peer_origin: str = Query('', max_length=2000),
                   direction: str = Query('outbound', pattern='^(inbound|outbound)$'), strict_peer: bool = False):
        actor = await user(request)
        if since is not None and not math.isfinite(since):
            raise HTTPException(422, 'Invalid job time range.')
        if source_id != 'local':
            return await service.remote(actor, source_id, '/api/observability/plazas/local/agents/' + quote(agent_id, safe='') + '/jobs?' + urlencode(dict(request.query_params)))
        agent = await run_in_threadpool(service.require_agent, actor, agent_id, logs=True)
        origin = peer_origin.rstrip('/')
        origins = {origin, service.address_aliases.get(origin, origin)} if origin else set()
        origins.update(k for k, v in service.address_aliases.items() if v in origins)
        try:
            value = await run_in_threadpool(service.store.job_history.query, agent_id, agent['observability'].get('jobs', []),
                state=state, activity=activity, status=status, search=search, since=since, cursor=cursor, limit=limit,
                peer_id=peer_id, peer_origins=origins, direction=direction, strict_peer=strict_peer)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        value.update({'telemetry_supported': agent['telemetry_supported'], 'telemetry_stale': agent['telemetry_stale']})
        return NativeJSONResponse(value, headers={'Cache-Control': 'no-store'})

    @plaza.app.post('/api/observability/plazas/{source_id}/agents/{agent_id:path}/logs')
    async def request_logs(source_id: str, agent_id: str, request: Request):
        actor, query = await user(request), log_query(await body(request))
        if source_id != 'local':
            return await service.remote(actor, source_id, '/api/observability/plazas/local/agents/' + quote(agent_id, safe='') + '/logs', body=query)
        agent = await run_in_threadpool(service.require_agent, actor, agent_id, logs=True)
        if not agent['telemetry_supported']:
            raise HTTPException(409, 'Upgrade this agent to retrieve diagnostic logs.')
        try:
            request_id = service.store.request_logs(actor['id'], agent_id, query)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from None
        if agent['connectivity'] == 'offline':
            cached = service.store.cached_logs(agent_id, query)
            if cached['items']:
                service.store.complete(request_id, agent_id, cached, cache=False)
        if agent_id in service.self_agent_ids():
            service.store.complete(request_id, agent_id, plaza.observations.store.logs(query))
        plaza.observations.log('INFO', 'Diagnostic logs requested', target_agent_id=agent_id, viewer_id=actor['id'])
        return {'id': request_id, 'status': 'pending', 'connectivity': agent['connectivity']}

    @plaza.app.get('/api/observability/plazas/{source_id}/diagnostics/{request_id}')
    async def diagnostic(source_id: str, request_id: str, request: Request):
        actor = await user(request)
        if source_id != 'local':
            return await service.remote(actor, source_id, '/api/observability/plazas/local/diagnostics/' + quote(request_id, safe=''))
        row = service.store.diagnostic(request_id)
        if not row or row['owner'] != actor['id']:
            raise HTTPException(404, 'Log request expired or unavailable.')
        await run_in_threadpool(service.require_agent, actor, row['agent'], logs=True)
        return JSONResponse({'id': request_id, 'status': 'ready' if row['response'] else 'pending',
                             'result': json.loads(row['response']) if row['response'] else None}, headers={'Cache-Control': 'no-store'})

    @plaza.app.post('/api/observability/diagnostics/{request_id}/complete')
    async def complete(request_id: str, request: Request):
        state = service.attach()
        if not state:
            raise HTTPException(503)
        from fastapi.security import HTTPAuthorizationCredentials
        auth = await run_in_threadpool(state.verify_token, HTTPAuthorizationCredentials(scheme='Bearer', credentials=plaza._extract_bearer_token(request)))
        value = await body(request, 1048576)
        try:
            completed = service.store.complete(request_id, auth.get('agent_id'), value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if not completed:
            raise HTTPException(404, 'Log request expired or unavailable.')
        return {'status': 'received'}
