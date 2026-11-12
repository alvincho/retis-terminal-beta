"""Owner-scoped deferred content resolution with persisted, reproducible snapshots.

Resolvers are installed host callbacks. A Phema cannot install code or choose a
network transport. The host authenticates/authorizes every callback, including
cache reads when its permission scope changes.
"""
from __future__ import annotations
import asyncio
import copy
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from .schema_security import validate_async
from prompits.core.capabilities import canonical, digest
from .composition import validate_composition, composition, content_hash, dependencies, component_parameters, reference_paths


LOG = logging.getLogger(__name__)


# Categories a host may name on its own error, beyond what the HTTP status says.
DECLARED_FAILURES = frozenset({'local-source-unreachable', 'local-pairing-rejected'})


def resolution_failure(error):
    """Closed failure categories; upstream text and credentials never escape."""
    if getattr(error, 'failure_category', None) in DECLARED_FAILURES:
        return error.failure_category
    status = getattr(error, 'status_code', None)
    response = getattr(error, 'response', None)
    if status is None and response is not None:
        status = getattr(response, 'status_code', None)
    category = {400: 'request-rejected', 401: 'authentication-required',
                403: 'access-denied', 404: 'source-unavailable',
                409: 'request-conflict', 422: 'input-rejected',
                429: 'source-busy', 502: 'source-failed',
                503: 'source-failed', 504: 'source-timeout'}.get(status)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        category = 'source-timeout'
    return category or 'source-failed'


def pointer(value, path=''):
    if not path: return copy.deepcopy(value)
    if not isinstance(path, str) or not path.startswith('/'): raise ValueError('Bindings use JSON pointers.')
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list): value = value[int(part)]
        else: value = value[part]
    return copy.deepcopy(value)


def bind(value, parameters, results):
    if isinstance(value, list): return [bind(v, parameters, results) for v in value]
    if not isinstance(value, dict): return value
    if set(value) == {'$param'}: return pointer(parameters, value['$param'])
    if set(value) == {'$result'}:
        source = value['$result']
        return pointer(results[source['reference']]['value'], source.get('path', ''))
    return {k: bind(v, parameters, results) for k, v in value.items()}


def select(value, rule):
    if not rule: return value
    rows = pointer(value, rule.get('path', ''))
    if not isinstance(rows, list): raise ValueError('Selection target must be a list.')
    rows = [r for r in rows if isinstance(r, dict) and all(r.get(k) == v for k, v in rule.get('where', {}).items())]
    order, identity = rule.get('orderBy', 'publishedAt'), rule.get('idField', 'id')
    # Source timestamps must be normalized ISO timestamps by the registered
    # connector. A stable identity breaks ties independently of response order.
    def timestamp(row):
        value=row.get(order)
        if type(value) in (int,float):return value
        if not isinstance(value,str):raise ValueError('Latest-item selection requires a timestamp.')
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    rows.sort(key=lambda r: (timestamp(r), str(r.get(identity, '')), digest(r)), reverse=True)
    return rows[:rule.get('limit', 1)]


def kept_until(ref, cached):
    """When a kept result stops being shown on open. A result is kept until its reference, inputs, selection or
    scope change (a new cache key) or the viewer refreshes; only a reference that declares freshness, by
    maxAgeSeconds (0 always asks again) or an interval refresh's period, asks again when opened later."""
    age = ref.get('maxAgeSeconds')
    if age is None and ref.get('refresh', {}).get('trigger') == 'interval':
        age = ref['refresh'].get('seconds')
    return float('inf') if age is None else cached['resolvedAt'] + age


def cache_key(ref, inputs, selection, scope):
    """The stored result's key: the reference as authored (not its id or refresh policy), its bound inputs and
    selection, and the host's authorization scope."""
    return digest({'reference': {k: ref[k] for k in ref if k not in ('id', 'refresh', 'required')}, 'inputs': inputs, 'selection': selection, 'scope': scope})


def reads_only(ref, rule):
    """A reference whose last result may be shown again without running it: a read, with no declared effect."""
    if isinstance(rule, dict) and rule.get('effect', 'read') != 'read': return False
    return not any((ref.get('extensions') or {}).get('phemacast:operation-effects', {}).values())


class ResolutionStore:
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS composition_cache(owner TEXT, key TEXT, value TEXT, PRIMARY KEY(owner,key))')
            db.execute('CREATE TABLE IF NOT EXISTS composition_snapshots(owner TEXT, id TEXT, value TEXT, created REAL, PRIMARY KEY(owner,id))')
            if 'pinned' not in {row[1] for row in db.execute('PRAGMA table_info(composition_snapshots)')}:
                db.execute('ALTER TABLE composition_snapshots ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
        self.path.chmod(0o600)

    def connect(self): return sqlite3.connect(self.path, timeout=15)

    def cached(self, owner, key):
        with self.connect() as db: row = db.execute('SELECT value FROM composition_cache WHERE owner=? AND key=?', (owner, key)).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, owner, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO composition_cache VALUES(?,?,?)', (owner, key, canonical(value)))
            db.execute('DELETE FROM composition_cache WHERE owner=? AND rowid NOT IN (SELECT rowid FROM composition_cache WHERE owner=? ORDER BY rowid DESC LIMIT 512)', (owner, owner))

    def snapshot(self, owner, value):
        id = digest(value)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO composition_snapshots(owner,id,value,created,pinned) VALUES(?,?,?,?,?)', (owner, id, canonical(value), time.time(), int('financialResults' in value)))
            db.execute('DELETE FROM composition_snapshots WHERE owner=? AND pinned=0 AND id NOT IN (SELECT id FROM composition_snapshots WHERE owner=? AND pinned=0 ORDER BY created DESC LIMIT 200)', (owner, owner))
        return id

    def read(self, owner, id):
        with self.connect() as db: row = db.execute('SELECT value FROM composition_snapshots WHERE owner=? AND id=?', (owner, id)).fetchone()
        if not row: raise KeyError('Snapshot unavailable.')
        return json.loads(row[0])

    def list(self, owner, phema_id):
        with self.connect() as db: rows = db.execute('SELECT id,value,created FROM composition_snapshots WHERE owner=? ORDER BY created DESC', (owner,)).fetchall()
        return [{'id': id, 'createdAt': created, 'status': value['status'], 'parameters': value['parameters'], 'contentHash': value['contentHash'], 'browserSnapshot': 'financialResults' in value,
                 'label': value.get('label'), 'references': value.get('references')}
                for id, raw, created in rows if (value := json.loads(raw)).get('phemaId') == phema_id]


def reference_contract(ref):
    return digest({k:v for k,v in ref.items() if k not in {'refresh','maxAgeSeconds','timeoutSeconds','onFailure','required'}})


class CompositionResolver:
    def __init__(self, store, handlers, *, max_concurrency=4, authorize=None):
        self.store, self.handlers = store, dict(handlers)
        self.authorize = authorize
        self.limit = asyncio.Semaphore(max_concurrency)
        self.inflight = {}
        self.waiters = {}

    async def resolve(self, phema, parameters, *, owner, authorization_scope, refresh=None, trigger='open', previous=None, on_update=None, interaction_inputs=None, ancestor_permissions=()):
        if not owner or not authorization_scope: raise ValueError('Owner and authorization scope are required.')
        value = validate_composition(phema)
        await validate_async(value.get('input_schema', {}), parameters)
        refs = {r['id']: r for r in composition(value).get('references', [])}
        previous = previous or {}
        if previous and (previous.get('parameters') != parameters or previous.get('authorizationScopeHash') != digest(authorization_scope)): previous = {}
        results = {id:copy.deepcopy(state) for id,state in previous.get('results', {}).items() if id in refs and previous.get('referenceContracts',{}).get(id)==reference_contract(refs[id])}
        all_paths = reference_paths(value)
        if refresh is not None and set(refresh) - set(all_paths): raise ValueError('Unknown refresh reference.')
        chosen = set(refs) if refresh is None else set(refresh)
        chosen &= set(refs)
        # Refresh downstream only; unchanged upstream results remain usable.
        while True:
            downstream = {id for id, ref in refs.items() if dependencies(ref) & chosen}
            if downstream <= chosen: break
            chosen |= downstream
        tasks = {}

        async def report(id, state):
            # The host received and validated this exact interaction. Failed
            # replies need the same binding as successful replies; a repeated
            # error string cannot identify which request completed.
            if id in (interaction_inputs or {}):
                state = {**state, 'interactionDigest': digest(interaction_inputs[id])}
            results[id] = state
            if on_update: await on_update(id, copy.deepcopy(state))

        def kept(ref, rule):
            if not reads_only(ref, rule): return None
            if any(results.get(dep, {}).get('status') != 'ready' for dep in dependencies(ref)): return None
            try: key = cache_key(ref, bind(ref.get('inputs', {}), parameters, results), bind(ref.get('select'), parameters, results), authorization_scope)
            except (KeyError, IndexError, TypeError, ValueError): return None
            cached = self.store.cached(owner, key)
            return {**cached, 'kept': True} if cached and cached.get('status') == 'ready' else None

        async def resolve_one(id):
            ref = refs[id]
            try:
                if not self.authorize: raise PermissionError('Host authorization is required.')
                interactive_idle = bool(ref.get('interaction')) and id not in (interaction_inputs or {})
                execute=id in chosen and not interactive_idle and not (ref.get('refresh',{}).get('trigger') in ('manual','stream') and trigger!='manual')
                context={'owner':owner,'authorizationScope':authorization_scope,'trigger':trigger,'ancestorPermissions':ancestor_permissions,'execute':execute}
                decision=self.authorize(value,ref,context)
                if asyncio.iscoroutine(decision):decision=await decision
            except PermissionError:
                await report(id,{'status':'blocked','reason':'permission-required'});return results[id]
            if id not in chosen and id in results:return results[id]
            # Reloading a view may refresh all references. Interactive channels
            # require their separately validated inputs; a reload must not send
            # an empty action or overwrite its last result with a schema error.
            if ref.get('interaction') and id not in (interaction_inputs or {}):
                await report(id, results.get(id, {'status': 'pending', 'reason': 'interaction-required'}))
                return results[id]
            policy = ref.get('refresh', {}).get('trigger', 'open')
            # A live stream starts like a manual reference: when the viewer asks.
            if policy in ('manual', 'stream') and trigger != 'manual':
                # A reference that waits for the viewer's refresh shows the last result fetched for exactly this
                # request, marked kept, until the viewer refreshes it. Showing it runs nothing and spends nothing.
                state = results[id] if id in results else (policy == 'manual' and kept(ref, decision)) or {'status': 'pending', 'reason': 'manual'}
                if dependencies(ref) & chosen and state.get('value') is not None:
                    state = {**state, 'status':'stale', 'reason':'manual-dependency-changed'}
                await report(id, state); return state
            for dep in sorted(dependencies(ref)):
                if dep not in tasks: tasks[dep] = asyncio.create_task(resolve_one(dep))
                await tasks[dep]
                if results[dep]['status'] != 'ready':
                    state = {'status': 'pending', 'reason': 'dependency', 'dependency': dep}
                    await report(id, state); return state
            last_known=copy.deepcopy(results.get(id, {}))
            await report(id, {**last_known, 'status': 'resolving'})
            cached, key = None, None
            try:
                inputs = bind(ref.get('inputs', {}), parameters, results)
                if id in (interaction_inputs or {}):
                    supplied=interaction_inputs[id]
                    if 'interaction' not in ref:raise ValueError('This reference does not accept interaction.')
                    await validate_async(ref['interaction']['inputSchema'], supplied)
                    inputs={**inputs, **supplied}
                selection = bind(ref.get('select'), parameters, results)
                key = cache_key(ref, inputs, selection, authorization_scope)
                cached = self.store.cached(owner, key)
                if refresh is None and cached and kept_until(ref, cached) > time.time():
                    await report(id, cached); return cached
                handler = self.handlers.get(ref['kind'])
                if not handler:
                    state = {'status': 'unavailable', 'reason': 'resolver-not-installed', 'kind': ref['kind']}
                    await report(id, state); return state
                async def fetch():
                    async with self.limit:
                        response = await asyncio.wait_for(handler(copy.deepcopy(ref), inputs, {'owner': owner, 'authorizationScope': authorization_scope}), ref.get('timeoutSeconds', 20))
                    if not isinstance(response, dict) or 'value' not in response: raise ValueError('Resolver must return value and provenance.')
                    data = select(response['value'], selection)
                    await validate_async(ref.get('outputSchema', {}), data)
                    if data is None or data == {} or data == []: raise ValueError('Resolver returned no content.')
                    if len(canonical(data).encode()) > 2_000_000: raise ValueError('Resolved content exceeds 2 MB.')
                    return {'status': 'ready', 'value': data, 'provenance': response.get('provenance', {}), 'resolvedAt': time.time(), 'sourceMode': 'snapshot' if ref['kind'] == 'phemar-snapshot' else 'parameters'}
                scope_key = (owner, key)
                task = self.inflight.get(scope_key)
                if task is None:
                    task = asyncio.create_task(fetch()); self.inflight[scope_key] = task
                self.waiters[scope_key] = self.waiters.get(scope_key, 0) + 1
                try: state = await asyncio.shield(task)
                finally:
                    self.waiters[scope_key] -= 1
                    if not self.waiters[scope_key]:
                        self.waiters.pop(scope_key, None)
                        self.inflight.pop(scope_key, None)
                        if not task.done(): task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.store.cache(owner, key, state)
            except asyncio.CancelledError:
                await report(id, {'status': 'pending', 'reason': 'cancelled'})
                raise
            except Exception as exc:
                # Never expose raw provider errors/tokens in the content snapshot.
                state = {'status': 'unavailable' if ref.get('onFailure')=='unavailable' else 'failed',
                         'reason': resolution_failure(exc)}
                # The host's own log names the failing step: a host error's detail, never upstream text or URLs.
                detail = getattr(exc, 'detail', None)
                LOG.warning('Reference %s failed: %s %s %s', ref.get('id'), type(exc).__name__,
                            getattr(exc, 'status_code', '') or getattr(getattr(exc, 'response', None), 'status_code', ''),
                            detail[:200] if isinstance(detail, str) else '')
                if isinstance(exc,PermissionError):
                    await report(id,{'status':'blocked','reason':'permission-required'});return results[id]
                retained=cached or (last_known if last_known.get('value') is not None else None)
                if retained and ref.get('onFailure', 'retain') == 'retain': state = {**retained, **state, 'status': 'stale'}
            await report(id, state)
            return state

        children = {}
        child_tasks = []
        async def resolve_child(child):
            id = child['id']; prefix = id + '/'
            selected = None if refresh is None else [path[len(prefix):] for path in refresh if path.startswith(prefix)]
            async def child_update(path, state):
                if on_update: await on_update(prefix + path, state)
            from .composition_permissions import manifest
            child_ceiling=(manifest(value) or {}).get('childAuthority',{}).get(id,{'effects':[],'classifications':[]})
            children[id] = await self.resolve(child['phema'], component_parameters(child, parameters), owner=owner, authorization_scope=authorization_scope,
                ancestor_permissions=(*ancestor_permissions,child_ceiling), refresh=selected, trigger=trigger, previous=previous.get('components', {}).get(id), on_update=child_update, interaction_inputs={k[len(prefix):]:v for k,v in (interaction_inputs or {}).items() if k.startswith(prefix)})
        try:
            for id in refs:
                if id not in tasks: tasks[id] = asyncio.create_task(resolve_one(id))
            child_tasks = [asyncio.create_task(resolve_child(child)) for child in composition(value).get('components', [])]
            await asyncio.gather(*tasks.values(), *child_tasks)
        finally:
            for task in [*tasks.values(), *child_tasks]:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks.values(), *child_tasks, return_exceptions=True)
        status = 'ready' if all(results[id]['status'] == 'ready' for id, ref in refs.items() if ref.get('required', True)) else 'partial'
        if any(children[c['id']]['status'] != 'ready' for c in composition(value).get('components', []) if c.get('required', True)): status = 'partial'
        for id, child in children.items():
            results.update({id+'/'+key:state for key,state in child['results'].items()})
        snapshot = {'schema': 'phemacast:resolved-composition/1', 'phemaId': value['id'], 'contentHash': content_hash(value), 'parameters': parameters, 'sourceMode': 'parameters', 'status': status, 'results': results}
        snapshot['referenceContracts'] = {id:reference_contract(ref) for id,ref in refs.items()}
        snapshot['authorizationScopeHash'] = digest(authorization_scope)
        if children: snapshot['components'] = children
        if len(canonical(snapshot).encode()) > 4_000_000: raise ValueError('Resolved Phema tree exceeds 4 MB.')
        return {**snapshot, 'snapshotId': self.store.snapshot(owner, snapshot)}
