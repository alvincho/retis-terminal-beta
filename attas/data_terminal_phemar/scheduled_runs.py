"""Scheduled runs: this Terminal repeats one declared reference of a reviewed Phema as its user.

A view asks for a run of a reference its mounted Phema declares. The Terminal
keeps the run in its own store (inputs, an opaque carry-over value, the latest
result and a short log) and calls the reference on a timer through the same
reviewed transport a Browser uses (`NetworkBroker.resolve_for`). Plaza's current
trust and the Phema's approved points apply to every tick; the user's own keys
reach only the vendor. Nothing here reads what a run computes: the carry-over is
copied from each result into the next call at paths the view names.

A run lives on the Terminal window's sign-in. It pauses when that sign-in ends,
when Plaza refuses the call, or after three failures in a row, and resumes when
the window signs in again or the view starts it again. Closing the view or its
Browser does not stop a run; the view or the Terminal's runs list stops it.
"""
import asyncio
import copy
import json
import logging
import re
import secrets
import time

from fastapi import HTTPException
from starlette.requests import Request

from phemacast.core.composition import content_hash, reference_paths, validate_reference
from phemacast.core.schema_security import validate_async
from .window_sessions import window_scope

LOG = logging.getLogger(__name__)
KIND = 'scheduled-run'
RESULT = 'scheduled-run-result'
PHEMA = 'scheduled-run-phema'
MIN_SECONDS, MAX_SECONDS = 60, 86400
MAX_RUNS = 16          # per account and Terminal window
MAX_INPUT = 200_000    # bytes of a run's inputs, and of its carry-over value
MAX_RESULT = 450_000   # bytes of the latest result kept for the view
LOG_ENTRIES = 50
FAILURES = 3
KEY = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
FIELD = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
SECRET = re.compile(r'^(?:__proto__|prototype|constructor|password|secret|token|access_token|refresh_token|'
                    r'authorization|cookie|api_key|caller_.*token)$', re.I)


def size(value):
    return len(json.dumps(value, allow_nan=False, separators=(',', ':')).encode())


def no_secrets(value):
    """The same rule views follow: credentials and transport identities are never run inputs or results."""
    if isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if SECRET.match(str(key)):
                raise HTTPException(422, 'Credentials and transport identities are not run inputs.')
            no_secrets(item)


def path_of(value, name):
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= 4 or not all(isinstance(p, str) and FIELD.match(p) for p in value):
        raise HTTPException(422, 'Name the ' + name + ' path as one to four field names.')
    return value


def read_path(value, path):
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def write_path(value, path, item):
    for part in path[:-1]:
        value = value.setdefault(part, {})
        if not isinstance(value, dict):
            raise HTTPException(422, 'The carry-over path crosses a value that is not an object.')
    value[path[-1]] = item


def public(run, result=None, *, carry=True):
    """What a view sees, and without the carry-over what the runs list sees; never the stored Phema."""
    keys = ('id', 'key', 'label', 'phemaId', 'phemaTitle', 'reference', 'everySeconds', 'status', 'reason',
            'nextAt', 'lastAt', 'startedAt', 'createdAt', 'ticks', 'failures', 'log')
    value = {k: copy.deepcopy(run[k]) for k in keys if k in run}
    # The carry-over is the view's own state (for a strategy monitor, its paper positions).
    if carry:
        value['carry'] = copy.deepcopy(run.get('carryValue'))
    if result is not None:
        value['latest'] = result
    return value


class ScheduledRuns:
    def __init__(self, store, engine, principal, scope):
        self.store, self.engine, self.principal, self.scope = store, engine, principal, scope
        self.executing = {}   # run id -> asyncio.Lock held while a call is out
        self.inflight = set()
        self.limit = asyncio.Semaphore(4)

    # ---- records -------------------------------------------------------------------
    def records(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT owner,id,payload FROM documents WHERE kind=?', (KIND,)).fetchall()
        return [(row['owner'], {'id': row['id'], **json.loads(row['payload'])}) for row in rows]

    def mine(self, user):
        window = user.get('terminalWindow', '')
        return [run for run in self.store.list(user['id'], KIND) if run.get('window', '') == window]

    def read(self, owner, id):
        try:
            run = self.store.get(owner, KIND, id)
        except HTTPException:
            return None
        return run

    def save(self, owner, run):
        payload = {k: v for k, v in run.items() if k not in ('id', 'revision', 'updatedAt')}
        return self.store.put(owner, KIND, run['id'], payload)

    def result(self, owner, id):
        try:
            return self.store.get(owner, RESULT, id).get('value')
        except HTTPException:
            return None

    def owned(self, user, id):
        run = self.read(user['id'], id)
        if not run or run.get('window', '') != user.get('terminalWindow', ''):
            raise HTTPException(404, 'This run is unavailable in this Terminal window.')
        return run

    def note(self, run, status, error=None):
        run['log'] = ([{'at': time.time(), 'status': status, **({'error': str(error)[:300]} if error else {})}] +
                      run.get('log', []))[:LOG_ENTRIES]

    # ---- routes --------------------------------------------------------------------
    async def instance_route(self, instance_id, parts, request, user):
        """/api/instances/{id}/runs[/{run}[/now|/stop]] from a mounted view."""
        child = self.engine.instances.get(instance_id)
        if not child or not self.engine.belongs(child, user):
            raise HTTPException(404, 'Browser unavailable in this Terminal window.')
        root = child.phema
        if not root:
            raise HTTPException(409, 'The Phema has not finished opening.')
        phema_id = root.get('id')
        if not parts:
            if request.method == 'GET':
                return {'runs': [public(run, self.result(user['id'], run['id'])) for run in self.mine(user)
                                 if run.get('phemaId') == phema_id]}
            if request.method == 'POST':
                return await self.schedule(user, root, await request.json())
            raise HTTPException(405)
        run = self.owned(user, parts[0])
        if run.get('phemaId') != phema_id:
            raise HTTPException(404, 'This run belongs to another Phema.')
        if len(parts) == 2 and request.method == 'POST' and parts[1] == 'now':
            body = await request.json()
            return await self.now(user, run, body.get('input') if isinstance(body, dict) else None)
        if len(parts) == 2 and request.method == 'POST' and parts[1] == 'stop':
            body = await request.json()
            return self.stop(user, run, forget=isinstance(body, dict) and body.get('forget') is True)
        raise HTTPException(404, 'Unsupported run operation.')

    async def account_route(self, parts, request, user):
        """/api/scheduled-runs[/{run}/stop] for the Terminal's own runs list."""
        if not parts and request.method == 'GET':
            return {'runs': [public(run, carry=False) for run in self.mine(user)]}
        if len(parts) == 2 and parts[1] == 'stop' and request.method == 'POST':
            stopped = self.stop(user, self.owned(user, parts[0]))
            stopped.pop('carry', None)
            stopped.pop('latest', None)
            return stopped
        raise HTTPException(404, 'Unsupported run operation.')

    # ---- operations ----------------------------------------------------------------
    def reference(self, root, path):
        ref = reference_paths(root).get(path) if isinstance(path, str) else None
        if not ref or not ref.get('interaction'):
            raise HTTPException(422, 'Schedule a declared reference that accepts inputs.')
        if 'phemacast:operation-binding' in ref.get('extensions', {}):
            raise HTTPException(422, 'Native operations run only in an open Browser.')
        if ref.get('select') or '$param' in json.dumps(ref.get('inputs', {})) or '$result' in json.dumps(ref.get('inputs', {})):
            raise HTTPException(422, 'This reference depends on other content and cannot run on a schedule.')
        return ref

    async def inputs(self, ref, run, override=None):
        """The exact inputs of one call: the run's (or one call's) inputs, a fresh key and the carry-over."""
        value = copy.deepcopy(run['input'] if override is None else override)
        if run.get('keyField'):
            value[run['keyField']] = secrets.token_hex(16)
        if run.get('carry') and run.get('carryValue') is not None:
            write_path(value, run['carry']['into'], copy.deepcopy(run['carryValue']))
        try:
            await validate_async(ref['interaction']['inputSchema'], value)
            validate_reference({**ref, 'inputs': value})
        except Exception as exc:
            raise HTTPException(422, 'Run inputs do not match the reference schema.') from exc
        return {**copy.deepcopy(ref.get('inputs', {})), **value}

    def checked_input(self, value):
        if not isinstance(value, dict) or size(value) > MAX_INPUT:
            raise HTTPException(422, 'Send run inputs as one object of at most 200 KB.')
        no_secrets(value)
        return copy.deepcopy(value)

    async def schedule(self, user, root, body):
        if not isinstance(body, dict):
            raise HTTPException(422, 'Describe the run.')
        allowed = {'reference', 'key', 'label', 'everySeconds', 'input', 'keyField', 'carry'}
        if set(body) - allowed:
            raise HTTPException(422, 'Unknown run fields: ' + ', '.join(sorted(set(body) - allowed)))
        ref = self.reference(root, body.get('reference'))
        key = body.get('key')
        if not isinstance(key, str) or not KEY.match(key):
            raise HTTPException(422, 'Give the run a short key.')
        label = str(body.get('label') or key).strip()[:120]
        every = body.get('everySeconds')
        if type(every) is not int or not MIN_SECONDS <= every <= MAX_SECONDS:
            raise HTTPException(422, 'Run at most once a minute and at least once a day.')
        key_field = body.get('keyField')
        if key_field is not None and (not isinstance(key_field, str) or not FIELD.match(key_field)):
            raise HTTPException(422, 'Name the key field as one input field.')
        carry, initial = body.get('carry'), None
        if carry is not None:
            if not isinstance(carry, dict) or set(carry) - {'into', 'from', 'initial'}:
                raise HTTPException(422, 'Describe the carry-over with into, from and initial.')
            initial = carry.get('initial')
            carry = {'into': path_of(carry.get('into'), 'carry-over input'), 'from': path_of(carry.get('from'), 'carry-over result')}
            if not carry['into'] or not carry['from']:
                raise HTTPException(422, 'Name both carry-over paths.')
            if initial is not None:
                if size(initial) > MAX_INPUT:
                    raise HTTPException(422, 'The carry-over value exceeds 200 KB.')
                no_secrets(initial)
        draft = {'input': self.checked_input(body.get('input')), 'keyField': key_field, 'carry': carry,
                 'carryValue': copy.deepcopy(initial)}
        await self.inputs(ref, draft)  # refuse a run whose first call would not validate
        digest = content_hash(root)
        self.store.put(user['id'], PHEMA, digest, {'phema': root})
        now = time.time()
        existing = next((run for run in self.mine(user) if run.get('phemaId') == root.get('id') and run['key'] == key), None)
        if existing is None and sum(run.get('status') != 'stopped' for run in self.mine(user)) >= MAX_RUNS:
            raise HTTPException(429, 'This Terminal window already has 16 runs. Stop one first.')
        run = existing or {'id': secrets.token_hex(16), 'key': key, 'createdAt': now, 'ticks': 0, 'log': [],
                           'window': user.get('terminalWindow', '')}
        # Starting again keeps the carry-over unless the view sends a new one.
        if existing and initial is None and existing.get('carry') == carry:
            draft['carryValue'] = existing.get('carryValue')
        run.update(draft, label=label, phemaId=root.get('id'), phemaTitle=str(root.get('name') or root.get('title') or '')[:120],
                   phemaDigest=digest, reference=body['reference'], everySeconds=every, status='running', reason=None,
                   nextAt=now, startedAt=now, failures=0, generation=run.get('generation', 0) + 1)
        self.note(run, 'started')
        saved = self.save(user['id'], run)
        return public(saved, self.result(user['id'], saved['id']))

    def stop(self, user, run, forget=False):
        if forget:
            for kind in (KIND, RESULT):
                self.store.delete(user['id'], kind, run['id'])
            return {'id': run['id'], 'status': 'removed'}
        run.update(status='stopped', reason=None, generation=run.get('generation', 0) + 1)
        self.note(run, 'stopped')
        return public(self.save(user['id'], run), self.result(user['id'], run['id']))

    async def now(self, user, run, override):
        """One call now with this call's own inputs; its carry-over continues the run."""
        if run.get('status') != 'running':
            raise HTTPException(409, 'Start this run before sending it a call.')
        override = self.checked_input(override)
        outcome = await self.execute(user['id'], run['id'], user=user, override=override)
        if outcome.get('error'):
            raise HTTPException(outcome.get('code', 502), outcome['error'])
        return public(self.read(user['id'], run['id']), outcome.get('value'))

    # ---- execution -----------------------------------------------------------------
    async def user_for(self, owner, window):
        """The window's current Plaza sign-in, verified and refreshed as an ordinary request would be."""
        binding = self.store.terminal_login(window_scope(self.scope, window))
        if not binding or binding.get('owner') != owner:
            return None
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/scheduled-runs', 'raw_path': b'/api/scheduled-runs',
                 'query_string': b'', 'root_path': '', 'scheme': 'http', 'client': ('127.0.0.1', 0),
                 'server': ('127.0.0.1', 0),
                 'headers': [(b'x-terminal-window', window.encode())] if window else [],
                 'state': {'selected_session_id': binding['session_id'], 'expected_session_owner': owner,
                           'remembered_login_probe': True}}
        try:
            return await self.principal(Request(scope), False)
        except HTTPException:
            return None

    async def execute(self, owner, id, *, user=None, override=None, due=False):
        lock = self.executing.setdefault(id, asyncio.Lock())
        async with lock:
            run = self.read(owner, id)
            if not run or run.get('status') != 'running':
                return {'error': 'This run is not running.', 'code': 409}
            if due and run.get('nextAt', 0) > time.time():
                return {}  # a call the view sent just now already served this tick
            generation = run.get('generation')
            user = user or await self.user_for(owner, run.get('window', ''))
            if not user:
                return self.finish(owner, id, generation, pause='Sign in to this Terminal window again to resume.',
                                   code=401, waiting=True)
            try:
                root = self.store.get(owner, PHEMA, run['phemaDigest'])['phema']
                ref = self.reference(root, run['reference'])
                inputs = await self.inputs(ref, run, override)
                async with self.limit:
                    response = await asyncio.wait_for(
                        self.engine.broker.resolve_for(dict(user), root, ref, inputs),
                        min(ref.get('timeoutSeconds', 20), 300))
                if not isinstance(response, dict) or 'value' not in response:
                    raise ValueError('The agent returned no result.')
                value = response['value']
                await validate_async(ref.get('outputSchema', {}), value)
                no_secrets(value)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else 'The call was refused.'
                if exc.status_code == 401:
                    return self.finish(owner, id, generation, pause='Sign in to this Terminal window again to resume.',
                                       code=401, waiting=True)
                if exc.status_code == 403:
                    return self.finish(owner, id, generation, pause=detail, code=403)
                return self.finish(owner, id, generation, failure=detail, code=exc.status_code)
            except asyncio.TimeoutError:
                return self.finish(owner, id, generation, failure='The agent did not answer in time.')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return self.finish(owner, id, generation, failure=str(exc)[:300] or type(exc).__name__)
            # A result naming its own error (the agent's answer, not a transport failure) counts as a failure.
            if isinstance(value, dict) and isinstance(value.get('error'), str):
                return self.finish(owner, id, generation, failure=value['error'][:300], code=422, value=value)
            return self.finish(owner, id, generation, value=value)

    def finish(self, owner, id, generation, *, value=None, failure=None, pause=None, code=None, waiting=False):
        run = self.read(owner, id)
        if not run or run.get('generation') != generation:
            # Stopped or restarted while the call was out: the call's outcome no longer belongs to the run.
            return {'error': 'The run changed while this call was out.', 'code': 409}
        now = time.time()
        run['lastAt'] = now
        if pause:
            run.update(status='paused', reason=pause, waitingForSignIn=waiting, generation=generation + 1)
            self.note(run, 'paused', pause)
        elif failure:
            run['failures'] = run.get('failures', 0) + 1
            self.note(run, 'failed', failure)
            if run['failures'] >= FAILURES:
                run.update(status='paused', reason='Three calls in a row failed: ' + failure, generation=generation + 1)
            else:
                run['nextAt'] = now + run['everySeconds']
        else:
            run.update(failures=0, ticks=run.get('ticks', 0) + 1, nextAt=now + run['everySeconds'])
            carried = read_path(value, run['carry']['from']) if run.get('carry') else None
            if carried is not None:
                if size(carried) > MAX_INPUT:
                    run.update(status='paused', reason='The carry-over value grew past 200 KB.', generation=generation + 1)
                else:
                    run['carryValue'] = carried
            self.note(run, 'ok')
        if value is not None:
            kept = value if size(value) <= MAX_RESULT else {'error': 'The latest result exceeds 450 KB and was not kept.'}
            self.store.put(owner, RESULT, id, {'value': {'at': now, 'value': kept}})
        self.save(owner, run)
        if pause or failure:
            return {'error': pause or failure, 'code': code or 502, 'value': value}
        return {'value': {'at': now, 'value': value}}

    async def resume_signed_in(self, owner, run):
        """A run paused only for the window's sign-in resumes once that window signs in again."""
        if not await self.user_for(owner, run.get('window', '')):
            return
        current = self.read(owner, run['id'])
        if current and current.get('status') == 'paused' and current.get('waitingForSignIn'):
            current.update(status='running', reason=None, waitingForSignIn=False, nextAt=time.time(), failures=0,
                           generation=current.get('generation', 0) + 1)
            self.note(current, 'resumed')
            self.save(owner, current)

    def done(self, id, task):
        self.inflight.discard(id)
        if not task.cancelled() and task.exception() is not None:
            LOG.warning('Scheduled run %s failed: %s', id, type(task.exception()).__name__)

    async def loop(self, interval=5):
        checked = {}
        while True:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                for owner, run in self.records():
                    if run['id'] in self.inflight:
                        continue
                    if run.get('status') == 'running' and run.get('nextAt', 0) <= now:
                        job = self.execute(owner, run['id'], due=True)
                    elif run.get('status') == 'paused' and run.get('waitingForSignIn') and now - checked.get(run['id'], 0) >= 60:
                        checked[run['id']] = now
                        job = self.resume_signed_in(owner, run)
                    else:
                        continue
                    self.inflight.add(run['id'])
                    asyncio.create_task(job).add_done_callback(lambda task, id=run['id']: self.done(id, task))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning('Scheduled runs check failed: %s', type(exc).__name__)
