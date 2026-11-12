"""Plaza discovery and strictly registered-source transport."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import json
import re
import secrets

import httpx
from fastapi import HTTPException


def unwrap(value):
    for _ in range(5):
        if not isinstance(value, dict):
            break
        if any(k in value for k in ('endpoints', 'datasets', 'canonical_data', 'sources')):
            break
        key = next((k for k in ('result', 'payload', 'output', 'content') if isinstance(value.get(k), dict)), None)
        if not key:
            break
        value = value[key]
    return value










class Network:
    def __init__(self, plaza, registration, client):
        self.plaza, self.registration, self.client = plaza, registration, client
        self.local_clients = None
        self.local_users = {}
        self.local_only = False
        self.cache = None
        self.expiry = 0
        self.fetched = 0
        self.directory_pending = {}
        self.limit = asyncio.Semaphore(6)
        self.payment_locks = {}
        self.public_transport = getattr(client, 'public_network', None)
        from prompits.core.plaza_directory_trust import PlazaDirectoryTrust
        # Reuse each account's Plaza trust answer for 10 seconds; Plaza stays the authority.
        self.plaza_trust = PlazaDirectoryTrust(plaza, client, ttl=10)
        from .local_transport import local_source_origins
        self.local_source_origins = local_source_origins(plaza_url=self.plaza)
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        manifest_path = root/'manifest.json' if (root/'manifest.json').is_file() else root/'config/releases/attas-b-030.json'
        if os.environ.get('ATTAS_DEPLOYMENT_MANIFEST'):
            manifest_path = Path(os.environ['ATTAS_DEPLOYMENT_MANIFEST'])
            if not manifest_path.is_file():
                raise ValueError('The configured deployment manifest does not exist.')
        deployment = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        self.discovery_attachments = deployment.get('discoveryAttachments', []) if self.plaza == deployment.get('plaza') else []
        policy = deployment.get('sourcePolicy', {}) if self.plaza == deployment.get('plaza') else {}
        self.lifecycle_sources = set(policy.get('alwaysRunning', []) + policy.get('retainWhenCredentialsConfigured', []) + policy.get('publicOnDemand', []))
        self.consultant_archive_origin = None

    def local_owner(self, token):
        return self.local_users.get(hashlib.sha256(token.encode()).hexdigest())

    def bind_local_user(self, token, owner):
        self.local_users[hashlib.sha256(token.encode()).hexdigest()] = owner
        while len(self.local_users) > 16:self.local_users.pop(next(iter(self.local_users)))

    async def local_call(self, agent, operation, parameters, token):
        owner = self.local_owner(token)
        if not owner or owner != agent.get("localOwner"):
            raise HTTPException(403, "This local resource belongs to another Terminal account.")
        return await self.local_clients.call(owner, agent["localId"], operation, parameters)

    async def require_trusted(self, agent, token=''):
        if agent.get('localId'):
            owner = self.local_owner(token)
            rows = self.local_clients.saved(owner) if self.local_clients and owner else []
            if (agent.get('localOwner') != owner or not owner
                    or agent.get('id') != 'local-' + agent['localId']
                    or agent.get('address') != 'phemacast-local://' + agent['localId']
                    or not any(row['id'] == agent['localId'] for row in rows)):
                raise HTTPException(403, 'This local resource is not paired with the current account.')
            return agent
        return await self.plaza_trust.require(agent, token)

    async def trusted_agents(self, token=''):
        # Discovery caches never grant connection authority. Execution performs
        # a fresh Plaza lookup even when a caller holds an older directory row.
        return [row for row in await self.directory(token, force=True)
                if row.get('_plazaTrusted') is True
                or row.get('localOwner') and row['localOwner'] == self.local_owner(token)]

    async def directory(self, token='', force=False):
        if token.startswith('pc-call-'):
            from prompits.core.call_grants import CURRENT
            parent = CURRENT.get()
            if not parent or parent.get('token') != token:
                raise HTTPException(403, 'Directory lookup requires a verified call context.')
            # A call grant is audience-bound to this agent, not a Plaza login.
            # Discovery uses the agent identity; source calls still require a
            # child grant constrained by the verified parent authorization.
            token = self.registration._token or ''
            if not token:
                raise HTTPException(503, 'Agent Plaza connection is unavailable for source discovery.')
        # Cache is scoped by authenticated viewer; private metadata must not leak.
        scope = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        if self.cache and self.cache[0] == scope and (now < self.expiry if not force else now - self.fetched < 10):
            return self.cache[1]  # a forced read accepts an answer at most 10 seconds old
        # Lookups for the same viewer at the same time share one Plaza request.
        loop = asyncio.get_running_loop()
        task = self.directory_pending.get((scope, id(loop)))
        if task is None:
            task = self.directory_pending[(scope, id(loop))] = loop.create_task(self.fetch_directory(token, scope))
            task.add_done_callback(lambda done, key=(scope, id(loop)): self.directory_pending.pop(key, None))
        return await asyncio.shield(task)

    async def fetch_directory(self, token, scope):
        plazas = []
        primary = {}
        if not self.local_only:
            try:
                response = await self.client.get(self.plaza + '/api/plazas_status', headers={'Authorization': f'Bearer {token}'} if token else {}, timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
                response.raise_for_status()
                primary = response.json()
                plazas = primary.get('plazas', [])
            except httpx.HTTPError:
                if not self.local_clients or not self.local_owner(token):raise
        for attachment in self.discovery_attachments:
            try:
                # Explicit deployment attachments authorize this metadata-only
                # directory query. Execution still requires active Plaza trust.
                extra = await self.client.get(attachment['plaza']+'/api/plazas_status')
                extra.raise_for_status()
                for item in extra.json().get('plazas', []):
                    plazas.append({**item,'trust_evaluated':False,'agents':[r for r in item.get('agents',[]) if (r.get('card') or {}).get('address','').rstrip('/') in attachment['origins']]})
            except httpx.HTTPError:
                continue
        from prompits.core.plaza_directory_trust import directory_entries
        # Additional discovery Plazas are metadata only; connection authority
        # always comes from the active authenticated Plaza at invocation.
        entries = directory_entries({**primary, 'plazas': plazas})
        if self.public_transport:
            for entry in entries:
                self.public_transport.update_endpoints(entry['address'], entry['id'], entry['meta'].get('network_endpoints', []))
        owner = self.local_owner(token)
        if self.local_clients and owner:
            for row in self.local_clients.saved(owner):
                id = 'local-' + row['id']
                address = 'phemacast-local://' + row['id']
                meta = row['resource'].get('meta', {})
                if meta.get('data_source'):
                    source = dict(meta['data_source'])
                    if 'data_fetch' not in row['operations']:source['modelSupport'] = []
                    if 'quotes' not in row.get('streams', []):source.pop('streaming', None)
                    meta = {**meta, 'data_source':source, 'supported_pulses': [{**p, 'cost': 0} for p in meta.get('supported_pulses', []) if (p.get('pulse_name') or p.get('name')) in row['operations']]}
                entries.append({'id': id, 'localId': row['id'], 'localOwner': owner, 'name': row['name'], 'address': address, 'meta': meta, 'card': {'meta': meta}, 'availability': 'available'})
        self.cache, self.expiry, self.fetched = (scope, entries), time.monotonic() + 15, time.monotonic()
        return entries

    async def agent(self, id, token=''):
        entries = await self.directory(token)
        result = next((a for a in entries if a['id'] == id or a['meta'].get('data_terminal', {}).get('id') == id), None)
        if not result:
            raise HTTPException(404, 'Agent is not available in Plaza.')
        return result


    async def call_headers(self, agent, token, method, path, body=None, query=None, *, operation='', compile_onward=True):
        """Ordinary account and agent credentials travel only to Plaza."""
        from prompits.core.call_grants import binding, CURRENT
        await self.require_trusted(agent, token)
        query_string=str(httpx.QueryParams(query or {}))
        delegates=[]
        # Legacy source/analysis delegation. Application execution authority
        # belongs to publisher operation contracts and the generic broker.
        allowed={'data_fetch','historical_prices','llm_chat',
                 'resolve_symbols','resolve','symbol_convert','channel_content','articles_comment'}
        parent=CURRENT.get()
        declaration=next((p.get('extensions',{}).get('prompits:call_dependency_compiler')
            for p in agent.get('meta',{}).get('supported_pulses',[])
            if (p.get('pulse_name') or p.get('name'))==operation),None)
        if not parent and token and compile_onward and declaration:
            from prompits.core.declared_dependencies import compile_dependencies
            content=(body or {}).get('content',{})
            try:
                delegates=await compile_dependencies(self,agent,operation,content.get('params',{}),
                    token=token,user_id=self.local_owner(token))
            except PermissionError as exc:
                raise HTTPException(403,'Publisher dependencies do not match this account and request.') from exc
            peers={p['id']:p for p in await self.directory(token)}
            for dependency in delegates:
                peer=peers.get(dependency['agentId'])
                if peer is None:raise HTTPException(403,'Declared dependency is unavailable.')
                await self.require_trusted(peer, token)
        elif not parent and token and compile_onward:
            for candidate in await self.trusted_agents(token):
                meta=candidate.get('meta',{})
                owner=meta.get('owner_user_id') or candidate.get('card',{}).get('owner_user_id')
                # Transitive calls are limited to the signed-in owner's agents.
                # A selected third-party source gets this one call, not general
                # authority to forward its inputs to other recipients.
                if not owner or owner != self.local_owner(token):
                    continue
                for pulse in meta.get('supported_pulses',[]):
                    name=pulse.get('pulse_name') or pulse.get('name')
                    if name in allowed:
                        delegates.append({'agentId':candidate['id'],'operation':name})
        grant_headers={
            # The parent grant authorizes this dependency in the JSON body.
            # It is not a reusable Plaza login or an audience grant for Plaza.
            'Authorization':'Bearer '+((self.registration._token or '') if parent else (token or self.registration._token or '')),
            'X-Agent-Token':self.registration._token or ''}
        grant_body={
            'audience':agent['id'],'binding':binding(method,path,body,query_string),'operation':operation or path,
            'delegates':delegates[:128],**({'parent':parent['token']} if parent else {})}
        response=await self.client.post(self.plaza+'/api/call-grants',headers=grant_headers,json=grant_body,timeout=10)
        if response.status_code == 401 and callable(getattr(self.registration, 'register_once', None)):
            # Scope issuance has not executed the provider operation. Repair a
            # revoked/renewed caller token once, preserving its durable identity.
            previous_id = self.registration.agent_id
            result = await self.registration.register_once()
            if result == 'success':
                if self.registration.agent_id != previous_id:
                    raise HTTPException(409, 'Caller identity changed. Refresh before retrying.')
                grant_headers['X-Agent-Token'] = self.registration._token or ''
                if parent or not token:
                    grant_headers['Authorization'] = 'Bearer ' + (self.registration._token or '')
                response=await self.client.post(self.plaza+'/api/call-grants',headers=grant_headers,json=grant_body,timeout=10)
        response.raise_for_status()
        scoped=response.json().get('token','')
        if not scoped.startswith('pc-call-'):raise HTTPException(403,'Scoped remote authorization is unavailable.')
        return {'Authorization':'Bearer '+scoped,'Cache-Control':'no-store'}

    async def agent_request(self, agent, token, method, path, *, json=None, params=None, timeout=30):
        headers=await self.call_headers(agent,token,method,path,json,params)
        return await self.client.request(method,agent['address']+path,headers=headers,
            **({'json':json} if json is not None else {}),params=params,timeout=timeout,follow_redirects=False)

    async def pulse(self, agent, name, parameters, token='', *, practice='get_pulse_data', billing=None, compile_onward=True):
        await self.require_trusted(agent, token)
        if agent.get('localId'):
            if practice != 'get_pulse_data' or billing:raise HTTPException(403, 'Plaza payment cannot be delegated to a local resource.')
            return await self.local_call(agent, name, parameters, token)
        # 'strategy_consultation' is the Strategy Consultant's consultation pulse (strategy_consultant.contracts).
        execution_timeouts = {'strategy_consultation': 250, 'historical_prices': 90}
        timeout = execution_timeouts.get(name, 40) if practice == 'get_pulse_data' else 40
        path='/use_practice/'+practice
        payload={
            'sender':self.registration.agent_id,'receiver':agent['id'],
            'content':{'pulse_name':name,'params':parameters},'msg_type':'get_pulse_data',
            'caller_agent_name':self.registration.agent_name,
            'caller_agent_address':{'pit_id':self.registration.agent_id,'plazas':[]},
            'caller_agent_url':self.registration.public_url,'caller_protocol_version':'1.0',
            'billing':billing}
        headers=await self.call_headers(agent,token,'POST',path,payload,operation=name,compile_onward=compile_onward)
        scoped=headers['Authorization'].removeprefix('Bearer ')
        payload.update(caller_plaza_token=scoped,caller_user_token=scoped if token else None)
        async with self.limit:
            response=await self.client.post(agent['address']+path,headers=headers,json=payload,timeout=timeout,follow_redirects=False)
            response.raise_for_status()
            return unwrap(response.json())

    async def execute_free(self, agent, name, parameters, user, store):
        """Explicit free controls keep scoped authorization and avoid payment queues."""
        await self.require_trusted(agent, user['token'])
        self.bind_local_user(user['token'], user['id'])
        from .billing import execute_free
        return await execute_free(self, user, agent, name, parameters)


    async def execute(self, agent, name, parameters, user, store, *, _symbols_prepared=False):
        # Remote delegation needs the verified account identity even when this
        # Terminal has no local-resource connector installed.
        self.bind_local_user(user['token'],user['id'])
        from .billing import execute, budget_ceiling, PricedRequest
        from .window_sessions import preferences as window_preferences
        preferences = window_preferences(store,user)
        # A published zero-cost operation does not need the account payment
        # queue. The existing free transport still validates its current exact
        # quote, trust, scoped authorization and zero-cost ceiling before use.
        pulses = agent.get('meta', {}).get('supported_pulses', [])
        if any((pulse.get('pulse_name') or pulse.get('name')) == name
               and type(pulse.get('cost')) is int and pulse['cost'] == 0
               for pulse in pulses if isinstance(pulse, dict)):
            try:
                return await self.execute_free(agent, name, parameters, user, store)
            except PricedRequest:
                pass  # Priced per request (e.g. by strategy): pay through Plaza below.
        lock=self.payment_locks.setdefault(user['id'],asyncio.Lock())
        async with lock:
            # The Phema's per-call grant applies in execute; a daily limit applies only when turned on.
            return await execute(self, store, user, agent, name, parameters, budget_ceiling(preferences))

    async def resolve_reference(self, reference, inputs, user, store):
        from .content_references import resolve_reference
        return await resolve_reference(self, store, user, reference, inputs)


    async def consultants(self, token=''):
        def matching(entries):
            return [a for a in entries if a['meta'].get('data_terminal', {}).get('kind') == 'app-consultant']
        agents = matching(await self.trusted_agents(token))
        # Do not keep an empty startup directory cached after consultants have
        # registered. Revalidate once; preserve account scope and origin checks.
        if not agents:
            agents = matching(await self.trusted_agents(token))
        return agents



    async def pulse_catalog(self, agent, token=''):
        return await self.catalog(token, agent_id=agent['id'])

    async def catalog(self, token='', *, agent_id=None):
        agents = await self.directory(token, force=True)
        publishers, pulses = [], []
        for agent in agents:
            if agent_id is not None and agent['id'] != agent_id: continue
            publishers.append({'id':agent['id'], 'name':agent['name'],
                'type':agent.get('pit_type','agent'), 'activity':agent.get('availability','unknown')})
            advertised = [*agent.get('meta',{}).get('supported_pulses',[]),
                          *agent.get('card',{}).get('supported_pulses',[])]
            seen = set()
            for record in advertised:
                if not isinstance(record, dict): continue
                name = record.get('pulse_name') or record.get('name')
                if not name or name in seen: continue
                seen.add(name)
                pulses.append({'id':agent['id']+':'+name, 'name':record.get('name') or name,
                    'description':record.get('description',''), 'publisherId':agent['id'],
                    'operation':name, 'provenance':{'agentId':agent['id'], 'endpointId':name},
                    'inputSchema':record.get('input_schema',{}), 'outputSchema':record.get('output_schema',{}),
                    'fields':record.get('fields',[]), 'mode':record.get('mode','snapshot'),
                    'category':record.get('category','Pulse'), 'schema':record.get('schema',''),
                    'executionKind':'pulse', 'templates':record.get('templates',[])})
        return {'publishers':publishers, 'pulses':pulses, 'warnings':[]}


    async def schema_query(self, request, token=''):
        result = {'records': []}
        if not self.local_only:
            try:
                response = await self.client.post(self.plaza+'/api/schema-query', headers={'Authorization':'Bearer '+token} if token else {},json=request,timeout=8)
                response.raise_for_status(); result = response.json()
            except httpx.HTTPError:
                if not self.local_clients:raise
        if self.local_clients and self.local_owner(token):
            index = {(r['ref']['id'], r['ref']['version']): r for r in result.get('records', [])}
            for agent in await self.directory(token):
                if not agent.get('localId'):continue
                try:
                    extra = await self.local_call(agent, 'schema_query', request, token)
                except (HTTPException, httpx.HTTPError):continue
                for row in extra.get('records', []):
                    key = (row['ref']['id'], row['ref']['version'])
                    if key in index and index[key]['ref'] != row['ref']:
                        raise HTTPException(409, 'Local publishers disagree on this model version; choose an explicit model revision.')
                    index[key] = row
            result = {**result, 'records': list(index.values())}
        return result
