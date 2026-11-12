"""Scoped work history, independent of the smaller trace display buffer."""
import hashlib
import json
import math
import re
from datetime import datetime

RETENTION_SEC = 7 * 86400
LIMIT_PER_CATEGORY = 1000
REQUEST_KINDS = {'http', 'outbound_http', 'request', 'http_request'}


def timestamp(value, fallback=0):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return max(0, value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError):
            pass
    return fallback


def outcome(status):
    status = str(status or 'unknown').lower()
    if status in ('success', 'succeeded', 'completed', 'complete', 'ok', 'done'):
        return 'succeeded'
    if status in ('error', 'failed', 'failure', 'denied'):
        return 'failed'
    if status in ('canceled', 'cancelled'):
        return 'cancelled'
    return status


def category(job):
    kind = str(job.get('kind') or '').lower()
    return 'requests' if kind in REQUEST_KINDS or not kind and re.match(
        r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/', str(job.get('label') or '')) else 'work'


class JobHistory:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS job_history(key TEXT PRIMARY KEY, agent TEXT,
                    category TEXT, at REAL, payload TEXT);
                CREATE INDEX IF NOT EXISTS job_history_agent_time ON job_history(agent,at);
                CREATE INDEX IF NOT EXISTS job_history_time ON job_history(at);
                CREATE TABLE IF NOT EXISTS job_links(key TEXT PRIMARY KEY, agent TEXT,
                    job TEXT, trace TEXT, peer TEXT, origin TEXT, direction TEXT, at REAL);
                CREATE INDEX IF NOT EXISTS job_links_agent_peer ON job_links(agent,peer,origin);
                CREATE INDEX IF NOT EXISTS job_links_agent_time ON job_links(agent,at);
                CREATE INDEX IF NOT EXISTS job_links_time ON job_links(at);
                CREATE TABLE IF NOT EXISTS job_history_migrations(version INTEGER PRIMARY KEY);
            ''')
            if not db.execute('SELECT 1 FROM job_history_migrations WHERE version=1').fetchone():
                # Completion evidence already emitted by running agents can be
                # recovered without restarting them or inventing missing work.
                for row in db.execute('SELECT agent,at,payload FROM events ORDER BY at,id').fetchall():
                    self.capture(db, row['agent'], 'legacy', json.loads(row['payload']), {}, row['at'])
                db.execute('INSERT INTO job_history_migrations VALUES(1)')
            self.prune(db)

    def capture(self, db, agent, instance, event, active, received):
        at = min(timestamp(event.get('timestamp'), received), received)
        if at < self.store.clock() - RETENTION_SEC:
            return
        job_id = str(event.get('job_id') or '')[:200]
        trace = str(event.get('trace_id') or '')[:100]
        if event.get('event') == 'interaction' and event.get('phase') == 'result':
            identity = hashlib.sha256(json.dumps([agent, event.get('id')], ensure_ascii=True).encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO job_links VALUES(?,?,?,?,?,?,?,?)',
                       (identity, agent, job_id, trace, str(event.get('peer_agent_id') or '')[:200],
                        str(event.get('peer_origin') or '').rstrip('/')[:2000], str(event.get('direction') or ''), at))
        if not job_id or not (event.get('event') == 'job_finish' or
                event.get('event') == 'span_finish' and event.get('kind') in ('job', 'http', 'outbound_http')):
            return
        # Trace/span identity keeps separate runs of a reusable dispatcher job.
        # Use the same key during recovery and live ingestion to avoid duplicates.
        identity = hashlib.sha256(json.dumps([agent, job_id, trace, event.get('span_id') or event.get('id')], ensure_ascii=True).encode()).hexdigest()
        previous = db.execute('SELECT payload FROM job_history WHERE key=?', (identity,)).fetchone()
        job = {**(json.loads(previous['payload']) if previous else {}), **active.get(job_id, {})}
        label = str(event.get('operation') or job.get('label') or job_id)
        kind = event.get('kind') or job.get('kind')
        if not kind:
            kind = 'http' if re.match(r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/', label) else 'job'
        duration = event.get('duration_ms', job.get('duration_ms'))
        duration = duration if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0 else None
        job.update({'id': job_id, 'key': identity, 'label': label, 'kind': kind,
                    'status': outcome(event.get('status')), 'state': 'finished', 'ended_at': at,
                    'duration_ms': duration, 'trace_id': trace, 'span_id': str(event.get('span_id') or '')[:100]})
        job['started_at'] = timestamp(job.get('started_at'), at - duration / 1000 if duration is not None else 0)
        # No peer identities or unstructured event payloads are exposed here.
        safe = {k: v for k, v in job.items() if k in ('id', 'key', 'label', 'kind', 'status', 'state',
                'started_at', 'ended_at', 'duration_ms', 'trace_id', 'span_id', 'log_job_id', 'detail', 'error', 'progress')}
        db.execute('INSERT OR REPLACE INTO job_history VALUES(?,?,?,?,?)',
                   (identity, agent, category(safe), at, json.dumps(safe)))

    def ingest(self, db, agent, instance, jobs, events, received):
        active = {str(j.get('id')): j for j in jobs if j.get('id')}
        for event in events:
            self.capture(db, agent, instance, event, active, received)
        self.prune(db, agent)

    def prune(self, db, agent=None):
        cutoff = self.store.clock() - RETENTION_SEC
        db.execute('DELETE FROM job_history WHERE at<?', (cutoff,))
        db.execute('DELETE FROM job_links WHERE at<?', (cutoff,))
        agents = [agent] if agent else [r['agent'] for r in db.execute('SELECT agent FROM job_history UNION SELECT agent FROM job_links')]
        for identity in agents:
            for kind in ('work', 'requests'):
                db.execute('''DELETE FROM job_history WHERE key IN (SELECT key FROM job_history
                    WHERE agent=? AND category=? ORDER BY at DESC,key DESC LIMIT -1 OFFSET ?)''',
                           (identity, kind, LIMIT_PER_CATEGORY))
            db.execute('''DELETE FROM job_links WHERE key IN (SELECT key FROM job_links
                WHERE agent=? ORDER BY at DESC,key DESC LIMIT -1 OFFSET 10000)''', (identity,))

    def query(self, agent, active, *, state='all', activity='work', status='all', search='',
              since=None, cursor='', limit=50, peer_id='', peer_origins=(), direction='outbound', strict_peer=False):
        now = self.store.clock()
        with self.store.connect() as db:
            finished = [json.loads(r['payload']) for r in db.execute(
                'SELECT payload FROM job_history WHERE agent=? AND at>=? ORDER BY at DESC,key DESC',
                (agent, max(since or 0, now - RETENTION_SEC)))]
            links = db.execute('SELECT * FROM job_links WHERE agent=? AND at>=?', (agent, now - RETENTION_SEC)).fetchall() if peer_id or peer_origins else []
            trace_by_job = {r['job']: r['trace'] for r in links if r['job'] and r['trace']}
        def peer_match(peer, origin):
            return bool(peer_id and peer == peer_id and (not peer_origins and not strict_peer or
                        not origin and not strict_peer or origin in peer_origins)
                        or not peer and origin and origin in peer_origins)
        related = [r for r in links if r['direction'] == direction and peer_match(r['peer'], r['origin'])]
        related_jobs = {r['job'] for r in related if r['job']}
        related_traces = {r['trace'] for r in related if r['trace']}
        def matches(job):
            if activity != 'all' and category(job) != activity:
                return False
            if status != 'all' and outcome(job.get('status')) != status:
                return False
            if search and search.casefold() not in ' '.join(str(job.get(k) or '') for k in ('id', 'label', 'kind', 'detail')).casefold():
                return False
            if peer_id or peer_origins:
                return ((job.get('trace_id') in related_traces if job.get('trace_id') else job['id'] in related_jobs) or
                        direction == 'outbound' and peer_match(job.get('peer_agent_id'), job.get('peer_origin', '').rstrip('/')))
            return True
        live = [{**j, 'state': 'active', 'status': outcome(j.get('status')), 'key': 'active:' + str(j.get('id')),
                 'trace_id': j.get('trace_id') or trace_by_job.get(str(j.get('id')), '')} for j in active if j.get('id')]
        active_items, finished_items = [j for j in live if matches(j)], [j for j in finished if matches(j)]
        total = len(finished_items)
        if cursor:
            index = next((i for i, j in enumerate(finished_items) if j['key'] == cursor), None)
            if index is None:
                raise ValueError('Job history changed. Refresh the list.')
            finished_items = finished_items[index + 1:]
        page = finished_items[:limit] if state != 'active' else []
        return {'active': active_items if state != 'finished' else [], 'finished': page,
                'active_count': len(active_items), 'finished_count': total,
                'next_cursor': page[-1]['key'] if page and len(finished_items) > limit else '',
                'requests_hidden': sum(category(j) == 'requests' for j in live) if activity == 'work' else 0,
                'retention_sec': RETENTION_SEC, 'history_limit_per_category': LIMIT_PER_CATEGORY,
                'generated_at': now}
