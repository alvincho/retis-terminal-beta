"""Bounded, generic runtime telemetry and durable Plaza observations.

Product layers attach metrics to this contract without importing into Prompits.
Diagnostic content is redacted at capture; no request bodies are collected.
"""
from __future__ import annotations

import contextvars
import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
import queue
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid
import traceback

TRACE = contextvars.ContextVar('prompits_observation_trace', default=None)
TRACE_TIMING = contextvars.ContextVar('prompits_observation_timing', default=None)
SECRET_KEY = re.compile(r'password|secret|token|authorization|cookie|api[_-]?key|credential', re.I)
SECRET_VALUE = re.compile(r'(?i)(Bearer\s+)\S+|((?:password|secret|token|api[_-]?key|cookie)\s*[=:]\s*)[^\s,;]+')
INTERACTION_RETENTION_SEC = 7 * 86400
INTERACTION_LIMIT_PER_AGENT = 10000


def redact(value, depth=0):
    if depth > 6:
        return '[truncated]'
    if isinstance(value, dict):
        return {str(k)[:100]: '[redacted]' if SECRET_KEY.search(str(k)) else redact(v, depth + 1)
                for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value[:200]]
    if isinstance(value, str):
        value = re.sub(r'''(?i)(["'](?:password|secret|access_token|refresh_token|api_key|token|cookie)["']\s*:\s*)["'][^"']*["']''', r'\1"[redacted]"', value)
        value = SECRET_VALUE.sub(lambda m: (m.group(1) or m.group(2) or '') + '[redacted]', value)
        # URLs may embed credentials or arbitrary query secrets.
        value = re.sub(r'(https?://)[^/\s@]+:[^/\s@]+@', r'\1[redacted]@', value)
        value = re.sub(r'(https?://[^\s?#]+)\?[^\s]+', r'\1?[redacted]', value)
        return value[:4000]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return redact(str(value), depth + 1)


def state_path(kind, identity):
    root = Path(os.environ.get('PROMPITS_OBSERVABILITY_STATE_DIR',
                              str(Path.home() / '.local/state/finmas/observability')))
    return root / kind / (hashlib.sha256(identity.encode()).hexdigest()[:24] + '.sqlite')


class ObservationStore:
    """SQLite storage with bounded histories, report ordering, and diagnostic queues."""
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        # Serialize this store's short transactions before opening SQLite. Fleet
        # heartbeat bursts otherwise race for the writer and exhaust busy_timeout.
        self._connection_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshots(agent TEXT PRIMARY KEY, instance TEXT, seq INTEGER,
                    received REAL, payload TEXT);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, agent TEXT, trace TEXT,
                    at REAL, payload TEXT);
                CREATE INDEX IF NOT EXISTS events_trace ON events(trace,at);
                CREATE INDEX IF NOT EXISTS events_time ON events(at);
                CREATE TABLE IF NOT EXISTS interaction_history(id TEXT PRIMARY KEY, agent TEXT,
                    peer TEXT, origin TEXT, at REAL, status TEXT, duration REAL);
                CREATE INDEX IF NOT EXISTS interaction_agent_time ON interaction_history(agent,at);
                CREATE INDEX IF NOT EXISTS interaction_time ON interaction_history(at);
                CREATE TABLE IF NOT EXISTS logs(cursor INTEGER PRIMARY KEY AUTOINCREMENT, at REAL,
                    level TEXT, job TEXT, trace TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS diagnostics(id TEXT PRIMARY KEY, owner TEXT, agent TEXT,
                    created REAL, expires REAL, query TEXT, response TEXT);
                CREATE TABLE IF NOT EXISTS log_cache(agent TEXT, cursor INTEGER, at REAL,
                    level TEXT, job TEXT, trace TEXT, payload TEXT, PRIMARY KEY(agent,cursor));
                CREATE TABLE IF NOT EXISTS connections(owner TEXT, source TEXT, payload TEXT,
                    PRIMARY KEY(owner,source));
                CREATE TABLE IF NOT EXISTS monitoring_sources(owner TEXT, id TEXT, name TEXT, url TEXT,
                    PRIMARY KEY(owner,id), UNIQUE(owner,url));
                CREATE TABLE IF NOT EXISTS devices(code TEXT PRIMARY KEY, proof TEXT, expires REAL,
                    owner TEXT, decision TEXT, access TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS monitoring_grants(owner TEXT, agent TEXT, created REAL,
                    PRIMARY KEY(owner,agent));
                CREATE INDEX IF NOT EXISTS logs_time ON logs(at);
                CREATE INDEX IF NOT EXISTS log_cache_time ON log_cache(at);
            ''')
            # Recover existing relationships before the general trace buffer can
            # evict them. Event IDs make this restart-safe and idempotent.
            migrated_agents = set()
            for row in db.execute("""SELECT e.id,e.agent,e.at,e.payload FROM events e
                    WHERE json_extract(e.payload,'$.event')='interaction'
                    AND json_extract(e.payload,'$.direction')='outbound'
                    AND json_extract(e.payload,'$.phase')='result'
                    AND NOT EXISTS (SELECT 1 FROM interaction_history h WHERE h.id=e.id)""").fetchall():
                if self._record_interaction(db, row['id'], row['agent'], json.loads(row['payload']), row['at']):
                    migrated_agents.add(row['agent'])
            for agent in migrated_agents:
                self._prune_interactions(db, agent)
        os.chmod(self.path, 0o600)
        from .job_history import JobHistory
        self.job_history = JobHistory(self)

    @contextmanager
    def connect(self):
        with self._connection_lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=NORMAL')
            try:
                with db:
                    yield db
            finally:
                db.close()

    def monitoring_agents(self, owner):
        with self.connect() as db:
            return {row['agent'] for row in db.execute('SELECT agent FROM monitoring_grants WHERE owner=?', (owner,))}

    def grant_monitoring(self, owner, agents):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO monitoring_grants VALUES(?,?,?)',
                           [(owner, agent, self.clock()) for agent in agents])

    def _record_interaction(self, db, identity, agent, event, received):
        if (event.get('event') != 'interaction' or event.get('direction') != 'outbound'
                or event.get('phase') != 'result'):
            return False
        peer, origin = event.get('peer_agent_id') or '', event.get('peer_origin') or ''
        if not isinstance(peer, str) or not isinstance(origin, str) or not (peer or origin):
            return False
        at = event.get('timestamp', received)
        if not isinstance(at, (int, float)) or not math.isfinite(at) or at <= 0:
            at = received
        at = min(at, received)  # Agent clocks cannot extend server retention.
        if at < self.clock() - INTERACTION_RETENTION_SEC:
            return False
        duration = event.get('duration_ms')
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            duration = None
        return bool(db.execute('INSERT OR IGNORE INTO interaction_history VALUES(?,?,?,?,?,?,?)',
                    (identity, agent, peer, origin.rstrip('/'), at, str(event.get('status') or ''), duration)).rowcount)

    def _prune_interactions(self, db, agent):
        db.execute('DELETE FROM interaction_history WHERE at<?', (self.clock() - INTERACTION_RETENTION_SEC,))
        # A noisy agent must not displace another agent's quieter relationships.
        db.execute('''DELETE FROM interaction_history WHERE id IN
            (SELECT id FROM interaction_history WHERE agent=? ORDER BY at DESC,id DESC LIMIT -1 OFFSET ?)''',
            (agent, INTERACTION_LIMIT_PER_AGENT))

    def relationships(self, agents, *, since=None, until=None):
        if not agents:
            return []
        until = self.clock() if until is None else until
        since = max(self.clock() - INTERACTION_RETENTION_SEC,
                    since if since is not None else self.clock() - INTERACTION_RETENTION_SEC)
        marks = ','.join('?' for _ in agents)
        with self.connect() as db:
            return [dict(row) for row in db.execute(f'''SELECT agent AS agent_id,peer AS peer_agent_id,
                origin AS peer_origin,COUNT(*) AS calls,
                SUM(status IN ('failed','denied','error')) AS errors,
                COALESCE(SUM(duration),0) AS total_duration_ms,COUNT(duration) AS duration_samples,
                MIN(at) AS first_seen,MAX(at) AS last_seen FROM interaction_history
                WHERE agent IN ({marks}) AND at>=? AND at<=? GROUP BY agent,peer,origin''',
                [*agents, since, until])]

    def ingest(self, agent, report):
        if not isinstance(report, dict) or report.get('schema_version') != 1:
            raise ValueError('Unsupported observation schema.')
        for field in ('jobs', 'metrics', 'events', 'panels'):
            if not isinstance(report.get(field, []), list) or any(not isinstance(v, dict) for v in report.get(field, [])):
                raise ValueError('Invalid observation collection.')
        if not isinstance(report.get('health', {}), dict):
            raise ValueError('Invalid health observation.')
        report = redact(report)
        for field in ('started_at', 'observed_at', 'uptime_sec', 'heartbeat_interval_sec'):
            value = report.get(field, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid observation timing.')
        if report.get('heartbeat_interval_sec', 0) > 86400:
            raise ValueError('Invalid heartbeat interval.')
        health = report.get('health', {})
        if (health.get('status', 'unknown') not in ('healthy', 'degraded', 'unhealthy', 'unknown')
                or not isinstance(health.get('reasons', []), list)
                or any(not isinstance(v, str) for v in health.get('reasons', []))):
            raise ValueError('Invalid health observation.')
        for metric in report.get('metrics', []):
            if (not isinstance(metric.get('name'), str) or not isinstance(metric.get('value'), (int, float))
                    or not math.isfinite(metric['value']) or not isinstance(metric.get('labels', {}), dict)):
                raise ValueError('Invalid metric.')
        report['panels'] = [p for p in report.get('panels', [])[:10]
                            if isinstance(p.get('id'), str) and re.fullmatch(r'[a-z0-9_-]{1,60}', p['id'])
                            and p['id'] not in ('jobs', 'metrics', 'traces', 'logs')
                            and isinstance(p.get('label'), str) and isinstance(p.get('prefix'), str)
                            and isinstance(p.get('measures', []), list)
                            and all(isinstance(m, dict) and isinstance(m.get('filter', {}), dict) for m in p.get('measures', []))]
        if 'display_name' in report:
            name = display_text(report.pop('display_name'))
            if name:
                report['display_name'] = name
        if len(json.dumps(report)) > 262144:
            raise ValueError('Observation exceeds 256 KiB.')
        instance, seq = str(report.get('instance_id', '')), int(report.get('sequence', 0))
        if not instance or seq < 1:
            raise ValueError('Observation needs an instance and sequence.')
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM snapshots WHERE agent=?', (agent,)).fetchone()
            if previous:
                old = json.loads(previous['payload'])
                if instance == previous['instance'] and seq <= previous['seq']:
                    return False
                if instance != previous['instance'] and float(report.get('started_at', 0)) <= float(old.get('started_at', 0)):
                    return False
            events = report.pop('events', [])
            report.pop('logs', None)
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?)',
                       (agent, instance, seq, now, json.dumps(report)))
            for event in events[:200]:
                if not isinstance(event, dict) or not event.get('id'):
                    continue
                event['agent_id'] = agent  # Identity is authenticated, never self-reported.
                identity = agent + ':' + instance + ':' + str(event['id'])[:100]
                db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',
                           (identity, agent, str(event.get('trace_id', ''))[:100], now, json.dumps(event)))
                self._record_interaction(db, identity, agent, event, now)
            self._prune_interactions(db, agent)
            self.job_history.ingest(db, agent, instance, report.get('jobs', []), events[:200], now)
            db.execute('DELETE FROM events WHERE at<?', (now - 7 * 86400,))
            db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY at DESC LIMIT -1 OFFSET 20000)')
            db.execute('DELETE FROM diagnostics WHERE expires<?', (now,))
            db.execute('DELETE FROM log_cache WHERE at<?', (now - 7 * 86400,))
        return True

    def record_event(self, agent, event, *, instance='relay'):
        event = redact({**event, "agent_id": agent})
        try:
            with self.connect() as db:
                identity, now = agent + ':' + instance + ':' + event["id"], self.clock()
                db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)", (identity, agent,
                           event.get("trace_id", ""), now, json.dumps(event)))
                self._record_interaction(db, identity, agent, event, now)
                self._prune_interactions(db, agent)
                self.job_history.ingest(db, agent, instance, [], [event], now)
                db.execute("DELETE FROM events WHERE at<?", (self.clock() - 7 * 86400,))
                db.execute("DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY at DESC LIMIT -1 OFFSET 20000)")
        except (sqlite3.Error, OSError, ValueError):
            pass

    def snapshots(self):
        with self.connect() as db:
            return {r['agent']: {**json.loads(r['payload']), 'received_at': r['received']}
                    for r in db.execute('SELECT * FROM snapshots')}

    def events(self, agents, trace=None, limit=500):
        if not agents:
            return []
        marks = ','.join('?' for _ in agents)
        args = list(agents)
        condition = ''
        if trace:
            condition = ' AND trace=?'
            args.append(trace)
        args.append(min(limit, 1000))
        with self.connect() as db:
            return [json.loads(r['payload']) for r in db.execute(
                f'SELECT payload FROM events WHERE agent IN ({marks}){condition} ORDER BY at DESC LIMIT ?', args)]

    def log(self, event):
        event = redact(event)
        with self.connect() as db:
            db.execute('INSERT INTO logs(at,level,job,trace,payload) VALUES(?,?,?,?,?)',
                       (self.clock(), event.get('level', 'INFO'), event.get('job_id', ''),
                        event.get('trace_id', ''), json.dumps(event)))
            db.execute('DELETE FROM logs WHERE at<?', (self.clock() - 7 * 86400,))
            db.execute('DELETE FROM logs WHERE cursor IN (SELECT cursor FROM logs ORDER BY cursor DESC LIMIT -1 OFFSET 10000)')

    def logs(self, query):
        conditions, args = [], []
        for key, column, op in [('after', 'cursor', '>'), ('before', 'cursor', '<'),
                                ('since', 'at', '>='), ('until', 'at', '<='),
                                ('level', 'level', '='), ('job_id', 'job', '='), ('trace_id', 'trace', '=')]:
            if query.get(key) not in (None, ''):
                conditions.append(f'{column}{op}?')
                args.append(query[key])
        if query.get('span_id'):
            conditions.append("json_extract(payload,'$.span_id')=?")
            args.append(query['span_id'])
        limit = max(1, min(int(query.get('limit', 100)), 500))
        args.append(limit + 1)
        ascending = bool(query.get('after'))
        with self.connect() as db:
            rows = db.execute('SELECT * FROM logs' + (' WHERE ' + ' AND '.join(conditions) if conditions else '')
                              + f" ORDER BY cursor {'ASC' if ascending else 'DESC'} LIMIT ?", args).fetchall()
        items = [{**json.loads(r['payload']), 'cursor': r['cursor'], 'captured_at': r['at']} for r in rows[:limit]]
        return {'items': items, 'has_more': len(rows) > limit,
                'next_cursor': items[-1]['cursor'] if items else None,
                'latest_cursor': max((v['cursor'] for v in items), default=0)}

    def request_logs(self, owner, agent, query):
        now = self.clock()
        with self.connect() as db:
            if db.execute('SELECT count(*) FROM diagnostics WHERE owner=? AND expires>? AND response IS NULL',
                          (owner, now)).fetchone()[0] >= 10:
                raise ValueError('Too many pending log requests.')
            request_id = uuid.uuid4().hex
            db.execute('INSERT INTO diagnostics VALUES(?,?,?,?,?,?,NULL)',
                       (request_id, owner, agent, now, now + 120, json.dumps(query)))
        return request_id

    def pending(self, agent):
        with self.connect() as db:
            return [{'id': r['id'], 'query': json.loads(r['query'])} for r in db.execute(
                'SELECT * FROM diagnostics WHERE agent=? AND expires>? AND response IS NULL ORDER BY created LIMIT 5',
                (agent, self.clock()))]

    def diagnostic(self, request_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM diagnostics WHERE id=?', (request_id,)).fetchone()
        return dict(row) if row and row['expires'] > self.clock() else None

    def complete(self, request_id, agent, response, *, cache=True):
        if not isinstance(response.get('items', []), list) or any(not isinstance(v, dict) for v in response.get('items', [])):
            raise ValueError('Invalid diagnostic log response.')
        response = redact(response)
        if len(json.dumps(response)) > 1048576:
            raise ValueError('Diagnostic response exceeds 1 MiB.')
        with self.connect() as db:
            row = db.execute('SELECT query FROM diagnostics WHERE id=? AND agent=? AND expires>? AND response IS NULL',
                             (request_id, agent, self.clock())).fetchone()
            if not row:
                return False
            query = json.loads(row['query'])
            # Enforce the requested scope even when an older agent ignores a
            # newer filter. The cache and browser receive only matching logs.
            def matches(item):
                if any(query.get(k) and item.get(k) != query[k] for k in ('job_id', 'trace_id', 'span_id', 'level')):
                    return False
                captured = item.get('captured_at', item.get('timestamp'))
                if query.get('since') is not None and (not isinstance(captured, (int, float)) or captured < query['since']):
                    return False
                if query.get('until') is not None and (not isinstance(captured, (int, float)) or captured > query['until']):
                    return False
                return True
            response['items'] = [item for item in response.get('items', []) if matches(item)]
            changed = db.execute('UPDATE diagnostics SET response=? WHERE id=? AND agent=? AND expires>? AND response IS NULL',
                                (json.dumps(response), request_id, agent, self.clock())).rowcount
            if changed and cache:
                for item in response.get('items', [])[:500]:
                    cursor = item.get('cursor')
                    if not isinstance(cursor, int) or cursor < 1:
                        continue
                    db.execute('INSERT OR REPLACE INTO log_cache VALUES(?,?,?,?,?,?,?)',
                               (agent, cursor, self.clock(), item.get('level', 'INFO'), item.get('job_id', ''),
                                item.get('trace_id', ''), json.dumps(item)))
                db.execute('DELETE FROM log_cache WHERE rowid IN (SELECT rowid FROM log_cache ORDER BY at DESC LIMIT -1 OFFSET 20000)')
        return bool(changed)

    def cached_logs(self, agent, query):
        conditions, args = ['agent=?', 'at>?'], [agent, self.clock() - 7 * 86400]
        for key, column, op in [('after', 'cursor', '>'), ('before', 'cursor', '<'),
                                ('level', 'level', '='), ('job_id', 'job', '='), ('trace_id', 'trace', '=')]:
            if query.get(key) not in (None, ''):
                conditions.append(column + op + '?')
                args.append(query[key])
        if query.get('span_id'):
            conditions.append("json_extract(payload,'$.span_id')=?")
            args.append(query['span_id'])
        # Agent capture time is stored in the JSON entry, not the cache receipt.
        for key, op in [('since', '>='), ('until', '<=')]:
            if query.get(key) not in (None, ''):
                conditions.append("json_extract(payload, '$.captured_at')" + op + '?')
                args.append(query[key])
        args.append(max(1, min(int(query.get('limit', 100)), 500)))
        with self.connect() as db:
            rows = db.execute('SELECT * FROM log_cache WHERE ' + ' AND '.join(conditions) + ' ORDER BY cursor DESC LIMIT ?', args).fetchall()
        return {'items': [json.loads(r['payload']) for r in rows], 'cached': True,
                'retrieved_at': max((r['at'] for r in rows), default=0), 'latest_cursor': max((r['cursor'] for r in rows), default=0)}

    def connection(self, owner, source, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO connections VALUES(?,?,?)', (owner, source, json.dumps(value)))
                return value
            row = db.execute('SELECT payload FROM connections WHERE owner=? AND source=?', (owner, source)).fetchone()
            return json.loads(row[0]) if row else {}

    def disconnect(self, owner, source):
        with self.connect() as db:
            db.execute('DELETE FROM connections WHERE owner=? AND source=?', (owner, source))

    def sources(self, owner):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,name,url FROM monitoring_sources WHERE owner=? ORDER BY rowid', (owner,))]

    def add_source(self, owner, name, url):
        with self.connect() as db:
            if db.execute('SELECT 1 FROM monitoring_sources WHERE owner=? AND url=?', (owner, url)).fetchone():
                raise ValueError('This Plaza is already in your list.')
            if db.execute('SELECT COUNT(*) FROM monitoring_sources WHERE owner=?', (owner,)).fetchone()[0] >= 20:
                raise ValueError('You can save up to 20 additional Plazas.')
            source = {'id': 'saved-' + secrets.token_hex(12), 'name': name, 'url': url}
            db.execute('INSERT INTO monitoring_sources VALUES(?,?,?,?)', (owner, source['id'], name, url))
            return source

    def remove_source(self, owner, source):
        with self.connect() as db:
            removed = db.execute('DELETE FROM monitoring_sources WHERE owner=? AND id=?', (owner, source)).rowcount
            if removed:
                db.execute('DELETE FROM connections WHERE owner=? AND source=?', (owner, source))
                row = db.execute('SELECT payload FROM preferences WHERE owner=?', (owner,)).fetchone()
                if row:
                    value = json.loads(row[0])
                    if value.get('source') == source:
                        value['source'] = 'all'
                        db.execute('UPDATE preferences SET payload=? WHERE owner=?', (json.dumps(value), owner))
            return bool(removed)

    def preferences(self, owner, value=None):
        def normalize(raw, *, strict=False):
            result = {key: raw[key] for key in ('source', 'health', 'search', 'tab')
                      if isinstance(raw.get(key), str) and len(raw[key]) <= 200}
            if 'types' in raw:
                types = raw['types']
                valid = (isinstance(types, list) and len(types) <= 50
                         and all(isinstance(item, str) and 0 < len(item) <= 200 and item.strip() for item in types))
                if not valid and strict:
                    raise ValueError('Choose up to 50 PIT types with nonempty names of at most 200 characters.')
                result['types'] = sorted(set(types)) if valid else []
            elif isinstance(raw.get('type'), str) and len(raw['type']) <= 200:
                result['types'] = [] if raw['type'] in ('', 'all') else [raw['type']]
            for key in ('minCalls', 'maxCalls'):
                if key not in raw:
                    continue
                bound = raw[key]
                if bound is None or type(bound) is int and 0 <= bound <= 9007199254740991:
                    result[key] = bound
                elif strict:
                    raise ValueError('Call limits must be nonnegative whole numbers or empty.')
            if (result.get('minCalls') is not None and result.get('maxCalls') is not None
                    and result['minCalls'] > result['maxCalls']):
                if strict:
                    raise ValueError('Min calls must not exceed max calls.')
                result.pop('minCalls', None)
                result.pop('maxCalls', None)
            if 'callWindowMinutes' in raw:
                minutes = raw['callWindowMinutes']
                if minutes is None or type(minutes) is int and 1 <= minutes <= 10080:
                    result['callWindowMinutes'] = minutes
                elif strict:
                    raise ValueError('Past must be between 1 minute and 168 hours, or empty.')
            if 'callWindowUnit' in raw:
                if raw['callWindowUnit'] in ('minutes', 'hours'):
                    result['callWindowUnit'] = raw['callWindowUnit']
                elif strict:
                    raise ValueError('Choose minutes or hours for the call time range.')
            if 'density' in raw:
                density = raw['density']
                if type(density) is int and 0 <= density <= 100:
                    result['density'] = density
                elif strict:
                    raise ValueError('Node density must be a whole number from 0 to 100.')
            if 'jobFilters' in raw:
                filters = raw['jobFilters']
                valid = (isinstance(filters, dict) and filters.get('state', 'all') in ('all', 'active', 'finished')
                         and filters.get('activity', 'work') in ('work', 'requests', 'all')
                         and filters.get('status', 'all') in ('all', 'succeeded', 'failed', 'cancelled', 'expired')
                         and isinstance(filters.get('search', ''), str) and len(filters.get('search', '')) <= 160
                         and (filters.get('minutes') is None or type(filters.get('minutes')) is int and 1 <= filters['minutes'] <= 10080))
                if not valid and strict:
                    raise ValueError('Invalid job filters.')
                if valid:
                    result['jobFilters'] = {key: filters[key] for key in ('state', 'activity', 'status', 'search', 'minutes') if key in filters}
            return result
        with self.connect() as db:
            if value is not None:
                normalized = normalize(value, strict=True)
                if 'density' not in value:
                    # A still-open older dashboard knows only the filter fields.
                    # Its next save must not erase the user's new layout setting.
                    previous = db.execute('SELECT payload FROM preferences WHERE owner=?', (owner,)).fetchone()
                    saved = normalize(json.loads(previous[0])) if previous else {}
                    if 'density' in saved:
                        normalized['density'] = saved['density']
                if 'jobFilters' not in value:
                    previous = db.execute('SELECT payload FROM preferences WHERE owner=?', (owner,)).fetchone()
                    saved = normalize(json.loads(previous[0])) if previous else {}
                    if 'jobFilters' in saved:
                        normalized['jobFilters'] = saved['jobFilters']
                value = normalized
                db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', (owner, json.dumps(value)))
            row = db.execute('SELECT payload FROM preferences WHERE owner=?', (owner,)).fetchone()
            if not row:
                return {}
            previous = json.loads(row[0])
            value = normalize(previous)
            if value != previous:
                # Migrate saved single-type views on the server, so the same
                # selection follows this user across browsers and computers.
                db.execute('UPDATE preferences SET payload=? WHERE owner=?', (json.dumps(value), owner))
            return value

    def begin_device(self, proof):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', str(proof)):
            raise ValueError('An S256 proof challenge is required.')
        code = secrets.token_hex(8)
        with self.connect() as db:
            db.execute('DELETE FROM devices WHERE expires<?', (self.clock(),))
            if db.execute('SELECT count(*) FROM devices').fetchone()[0] >= 1000:
                raise ValueError('Too many pending connections.')
            db.execute('INSERT INTO devices VALUES(?,?,?,NULL,NULL,NULL)', (code, proof, self.clock() + 300))
        return {'code': code, 'expires_in': 300}

    def approve_device(self, code, owner, approved):
        with self.connect() as db:
            return bool(db.execute('UPDATE devices SET owner=?,decision=? WHERE code=? AND expires>? AND decision IS NULL',
                                   (owner, 'approved' if approved else 'denied', code, self.clock())).rowcount)

    def exchange_device(self, code, verifier):
        from .client_sessions import challenge, digest
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM devices WHERE code=? AND expires>?', (code, self.clock())).fetchone()
            if not row or not secrets.compare_digest(row['proof'], challenge(str(verifier))) or row['access']:
                raise ValueError('Connection expired or proof invalid.')
            if row['decision'] != 'approved':
                return {'status': row['decision'] or 'pending'}
            token = 'obs-access-' + secrets.token_urlsafe(32)
            db.execute('UPDATE devices SET access=?,expires=? WHERE code=?', (digest(token), self.clock() + 86400, code))
        return {'status': 'connected', 'access_token': token, 'expires_at': self.clock() + 86400}

    def resolve_device(self, token):
        from .client_sessions import digest
        with self.connect() as db:
            row = db.execute('SELECT owner FROM devices WHERE access=? AND decision=? AND expires>?',
                             (digest(token), 'approved', self.clock())).fetchone()
        return row[0] if row else None

    def revoke_device(self, token):
        from .client_sessions import digest
        with self.connect() as db:
            db.execute('DELETE FROM devices WHERE access=?', (digest(token),))


class RuntimeObservations:
    def __init__(self, agent, path=None):
        self.agent = agent
        self.instance_id = uuid.uuid4().hex
        self.started_at, self.sequence = time.time(), 0
        self.lock = threading.RLock()
        self.store = ObservationStore(path or state_path('agents', str(agent.agent_id or agent.name) + '|' + str(agent.port)))
        self._events, self._metrics = [], {}
        self.panels = []
        self.started_perf = time.monotonic()
        self._log_handler = None

    def metric(self, name, value, *, unit='count', kind='gauge', labels=None, increment=False):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        labels = {str(k)[:60]: str(v)[:160] for k, v in (labels or {}).items() if not SECRET_KEY.search(str(k))}
        key = name + '|' + json.dumps(labels, sort_keys=True)
        with self.lock:
            if key not in self._metrics and len(self._metrics) >= 500:
                return
            previous = self._metrics.get(key, {}).get('value', 0)
            self._metrics[key] = {'name': name[:120], 'value': previous + value if kind == 'counter' or increment else value,
                                  'unit': unit, 'kind': kind, 'labels': labels}

    def event(self, event, **fields):
        context = TRACE.get() or {}
        item = redact({'id': uuid.uuid4().hex, 'event': event, 'timestamp': time.time(),
                       'agent_id': str(self.agent.agent_id or self.agent.name), **context, **fields})
        with self.lock:
            self._events.append(item)
            del self._events[:-200]
        self.log('INFO' if fields.get('status') not in ('error', 'failed') else 'ERROR', event, **fields)
        return item

    def log(self, level, message, **fields):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._persist_log(level, message, **fields)
        else:
            with self.lock:
                if self._log_handler is None:
                    AgentLogHandler(self)
                handler = self._log_handler
            handler.enqueue(level, message, {'timestamp': time.time(), **(TRACE.get() or {}), **fields})

    def close_log_capture(self):
        if self._log_handler is not None:
            self._log_handler.close()

    def _persist_log(self, level, message, **fields):
        try:
            self.store.log({'timestamp': time.time(), 'level': level, 'message': message,
                            'agent_id': str(self.agent.agent_id or self.agent.name),
                            'instance_id': self.instance_id, **(TRACE.get() or {}), **fields})
        except (sqlite3.Error, OSError, ValueError):
            pass

    @contextmanager
    def span(self, operation, *, peer_agent_id='', kind='practice', job_id='', traceparent=None):
        parent = TRACE.get() or {}
        if traceparent and re.fullmatch(r'00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]', traceparent):
            _, trace, span, _ = traceparent.split('-')
            parent = {'trace_id': trace, 'span_id': span}
        context = {'trace_id': parent.get('trace_id') or uuid.uuid4().hex, 'span_id': secrets.token_hex(8),
                   'parent_span_id': parent.get('span_id', ''), 'job_id': job_id or parent.get('job_id', '')}
        reset = TRACE.set(context)
        started = time.monotonic()
        timing_reset = TRACE_TIMING.set(started)
        status = 'success'
        self.event('span_start', operation=operation, kind=kind, peer_agent_id=peer_agent_id)
        try:
            yield context
        except BaseException as exc:
            status = 'cancelled' if isinstance(exc, (KeyboardInterrupt, GeneratorExit)) or type(exc).__name__ == 'CancelledError' else 'error'
            self.log('ERROR', 'Operation failed', operation=operation, error=type(exc).__name__,
                     traceback=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            raise
        finally:
            status = context.pop("outcome", status) if status == "success" else status
            elapsed = (time.monotonic() - started) * 1000
            self.event('span_finish', operation=operation, kind=kind, peer_agent_id=peer_agent_id, status=status, duration_ms=round(elapsed, 2))
            self.metric('operations_total', 1, kind='counter', labels={'kind': kind, 'status': status})
            self.metric('operation_duration_ms', elapsed, unit='ms', labels={'kind': kind})
            TRACE.reset(reset)
            TRACE_TIMING.reset(timing_reset)

    def snapshot(self):
        agent = self.agent
        try:
            jobs = redact(agent.working_list)[:100]
            status = str(agent.status)
        except Exception:
            jobs, status = [], 'Status collection unavailable'
        try:
            health = redact(agent.collect_health())
        except Exception:
            health = {'status': 'unknown', 'reasons': ['Health collection failed']}
        try:
            custom = redact(agent.collect_metrics())
        except Exception:
            custom = []
        try:
            # A name the owner gave this running instance, such as a session name; the card keeps its registered name.
            collect = getattr(agent, 'collect_display_name', None)
            display_name = display_text(collect()) if callable(collect) else ''
        except Exception:
            display_name = ''
        with self.lock:
            self.sequence += 1
            return {'schema_version': 1, 'instance_id': self.instance_id, 'sequence': self.sequence,
                    'started_at': self.started_at, 'observed_at': time.time(),
                    'heartbeat_interval_sec': agent.PLAZA_HEARTBEAT_INTERVAL,
                    'uptime_sec': round(time.monotonic() - self.started_perf, 1),
                    'version': str(agent.AGENT_RUNTIME_VERSION), 'health': health,
                    'activity': 'busy' if jobs else 'idle', 'status_message': redact(status),
                    'jobs': jobs, 'metrics': list(self._metrics.values()) + (custom if isinstance(custom, list) else []),
                    'events': list(self._events), 'panels': list(self.panels)[:10],
                    **({'display_name': display_name} if display_name else {})}


def display_text(value):
    """One line of at most 80 characters, or nothing."""
    if not isinstance(value, str):
        return ''
    value = ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', value).split())
    return value[:80]


def elapsed_ms():
    started = TRACE_TIMING.get()
    return round((time.monotonic() - started) * 1000, 2) if started is not None else None


def trace_headers():
    context = TRACE.get()
    return {'traceparent': f"00-{context['trace_id']}-{context['span_id']}-01"} if context else {}


class AgentLogHandler(logging.Handler):
    def __init__(self, runtime):
        level = os.environ.get('PROMPITS_OBSERVABILITY_LOG_LEVEL', 'INFO').upper()
        super().__init__(getattr(logging, level, logging.INFO))
        import weakref
        self.runtime = weakref.ref(runtime)
        self._queue = queue.Queue(maxsize=1000)
        self._capture_closed = threading.Event()
        self._drained = threading.Condition()
        self._worker = None
        runtime._log_handler = self

    def _persist(self):
        while True:
            if self.runtime() is None or (self._capture_closed.is_set() and self._queue.empty()):
                return
            try:
                level, message, fields = self._queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                runtime = self.runtime()
                if runtime is not None:
                    runtime._persist_log(level, message, **fields)
            except Exception:
                pass  # Diagnostics must not fail work or recursively log failures.
            finally:
                runtime = None
                self._queue.task_done()
                with self._drained:
                    self._drained.notify_all()

    def flush(self):
        # Bound shutdown waits even if the diagnostic store is unavailable.
        with self._drained:
            self._drained.wait_for(lambda: self._queue.unfinished_tasks == 0, timeout=2)

    def close(self):
        self.acquire()
        try:
            self._capture_closed.set()
        finally:
            self.release()
        self.flush()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=2)
        super().close()

    def emit(self, record):
        runtime = self.runtime()
        if runtime is None or getattr(record, 'agent_name', None) != runtime.agent.name or getattr(record, 'agent_port', None) != runtime.agent.port:
            return
        try:
            # Existing request logger embeds query/body text. Capture a safe event
            # for those lines instead of persisting arbitrary request parameters.
            message = record.getMessage()
            if message.startswith(('Incoming request ', 'Request body:', 'POST body:')) or message.lstrip().startswith('- Content:'):
                message = 'HTTP request received'
            # ContextVars do not propagate to the background writer. Capture the
            # request's trace and timestamp before returning to the request loop.
            fields = {**(TRACE.get() or {}), 'timestamp': record.created,
                      **{k: getattr(record, k) for k in ('job_id', 'trace_id', 'span_id') if getattr(record, k, None)}}
            if record.exc_info:
                fields['traceback'] = ''.join(traceback.format_exception(*record.exc_info))
            self.enqueue(record.levelname, message, fields)
        except Exception:
            pass  # Diagnostics must not fail work or recursively log failures.

    def enqueue(self, level, message, fields):
        self.acquire()
        try:
            if self._capture_closed.is_set():
                return
            try:
                self._queue.put_nowait((level, redact(message), redact(fields)))
            except queue.Full:
                runtime = self.runtime()
                if runtime is not None:
                    runtime.metric('observation_logs_dropped_total', 1, kind='counter')
                return
            if self._worker is None:
                self._worker = threading.Thread(target=self._persist, name='observation-log-writer', daemon=True)
                self._worker.start()
        finally:
            self.release()
