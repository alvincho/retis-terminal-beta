"""Plaza's link to its observer: reports passed on in batches, monitoring numbers asked of it.

While Plaza names an observer (`PlazaObservability.designated_observer`), a heartbeat only queues the agent's report
here, already encoded, and one thread sends batches to the observer. The heartbeat never waits on monitoring storage.
Monitoring reads send the observer the rows Plaza decided the reader may see and take back only the numbers; Plaza
re-applies its own decision to every answer, so the observer can never widen what a reader sees.

Every request is signed like Plaza's other calls to an agent (`native_authority.request_headers`, keyed by the
observer's current Plaza token, valid once), and goes only to an observer with a live heartbeat at an address it has
proven. When the observer cannot be reached, the reports of that time are lost (counted) and reads come back without
numbers; Plaza, sign-in and calls carry on.

This module is Plaza's only; agents load `observability_routes` without it.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

import httpx

from .address_proof import checkable, origin_of
from .native_authority import registered_authority, request_headers
from .observer_tickets import TICKET_SECONDS, issue_ticket

LOGGER = logging.getLogger(__name__)

OBSERVER_CHECK_SECONDS = 10
REPORT_BYTES = 384 * 1024
QUEUE_LIMIT = 5000
QUEUE_BYTES = 64 * 1024 * 1024
BATCH_LIMIT = 200
BATCH_BYTES = 4 * 1024 * 1024
ANSWER_BYTES = 64 * 1024 * 1024
KEPT_TICKETS = 20000
# Fields Plaza decides for each row; the observer's answer adds only what the reports say.
PLAZA_FIELDS = ('name', 'type', 'owner_id', 'trusted', 'monitoring_authorized', 'registration_state', 'logs_allowed',
                'endpoint', 'description', 'version', 'tags', 'owner_name')


class ObserverUnavailable(Exception):
    """The observer could not answer now."""


class ObserverRefused(ObserverUnavailable):
    """The observer answered and refused the request itself (a stale job cursor, say); `status` and `detail` say why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def masked(observed: dict) -> dict:
    """A report as a reader without log access sees it (as `with_telemetry` masks it)."""
    observed = dict(observed or {})
    observed['jobs'] = [{'id': j.get('id'), 'status': j.get('status'), 'kind': j.get('kind', 'job')}
                        for j in observed.get('jobs', []) if isinstance(j, dict)]
    observed.pop('status_message', None)
    observed.pop('display_name', None)
    return observed


def plaza_rows(rows: list, answered) -> list:
    """The observer's rows with Plaza's own decision on each: the same agents, in order, and Plaza's fields."""
    if not isinstance(answered, list) or [a.get('id') if isinstance(a, dict) else None for a in answered] != [r['id'] for r in rows]:
        raise ObserverUnavailable('The observer answered for other agents.')
    result = []
    for row, agent in zip(rows, answered):
        agent = dict(agent)
        for field in PLAZA_FIELDS:
            agent[field] = row.get(field, agent.get(field))
        agent['endpoint'] = row.get('endpoint', '') if row.get('logs_allowed') else ''
        agent['owner_name'] = row.get('owner_name', '') if row.get('logs_allowed') else ''
        observed = agent.get('observability') if isinstance(agent.get('observability'), dict) else {}
        agent['observability'] = observed if row.get('logs_allowed') else masked(observed)
        result.append(agent)
    return result


class ObserverLink:
    def __init__(self, service, *, client_factory=None, clock=time.time, start_thread=True, timeout=None):
        self.service = service
        self.clock = clock
        config = (getattr(service.plaza, 'raw_config', {}) or {}).get('observability') or {}
        self.timeout = timeout or max(1.0, min(60.0, float(config.get('observer_timeout_sec', 15))))
        self.client_factory = client_factory or (lambda: httpx.Client(trust_env=False, follow_redirects=False,
                                                                      timeout=self.timeout))
        self.reports: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.start_thread = start_thread
        self.lock = threading.Lock()
        self.queued_bytes = 0
        self.counts = {'forwarded': 0, 'dropped': 0, 'failed_batches': 0}
        self._cache = (0.0, None)
        self._tickets: dict = {}
        self._sender = None
        self._stopping = threading.Event()
        self._warned_at = 0.0

    # -- which observer -----------------------------------------------------------------------------------------

    def observer(self):
        """The observer Plaza names now, rechecked at most every few seconds (heartbeats ask on every call)."""
        now = self.clock()
        expires, value = self._cache
        if now < expires:
            return value
        value = self.service.designated_observer()
        self._cache = (now + OBSERVER_CHECK_SECONDS, value)
        return value

    def reachable(self, observer) -> bool:
        """The observer has a live heartbeat and has proven the address Plaza would send to."""
        if not checkable(origin_of(observer.get('address'))):
            return False
        try:
            registered_authority(self.service.plaza).live(observer['agentId'], observer['address'])
            return True
        except PermissionError:
            return False

    def ticket(self, agent_id: str, observer) -> dict:
        """A ticket for `agent_id` to send to `observer`, reused until half its life is gone."""
        key = (agent_id, observer['agentId'])
        kept = self._tickets.get(key)
        if kept and kept['claims']['expiresAt'] - self.clock() > TICKET_SECONDS / 2:
            return kept
        ticket = issue_ticket(self.service.ticket_key(), agent_id=agent_id, observer_id=observer['agentId'],
                              clock=self.clock)
        if len(self._tickets) >= KEPT_TICKETS:
            self._tickets.clear()
        self._tickets[key] = ticket
        return ticket

    # -- reports ------------------------------------------------------------------------------------------------

    def submit(self, agent_id: str, report) -> bool:
        """Queue one report for the observer; False (counted as dropped) when it is too large or the queue is full."""
        encoded = json.dumps(report, separators=(',', ':'))
        size = len(encoded)
        with self.lock:
            if size > REPORT_BYTES or self.queued_bytes + size > QUEUE_BYTES:
                self.counts['dropped'] += 1
                return False
            try:
                self.reports.put_nowait((agent_id, encoded))
            except queue.Full:
                self.counts['dropped'] += 1
                return False
            self.queued_bytes += size
        self._ensure_sender()
        return True

    def _ensure_sender(self):
        if not self.start_thread or self._stopping.is_set():
            return
        with self.lock:
            if self._sender is None or not self._sender.is_alive():
                self._sender = threading.Thread(target=self._send_loop, name='plaza-observer-reports', daemon=True)
                self._sender.start()

    def _take(self, first=None) -> list:
        batch, size = ([first], len(first[1])) if first else ([], 0)
        while len(batch) < BATCH_LIMIT and size < BATCH_BYTES:
            try:
                item = self.reports.get_nowait()
            except queue.Empty:
                break
            batch.append(item)
            size += len(item[1])
        return batch

    def _send(self, batch: list) -> int:
        """Send one batch; returns how many reports the observer kept. Lost reports are counted, never retried."""
        kept = 0
        try:
            body = ('{"reports":[' + ','.join('{"agentId":' + json.dumps(agent) + ',"report":' + encoded + '}'
                                                for agent, encoded in batch) + ']}').encode()
            observer = self.observer()
            if not observer or not self.reachable(observer):
                raise ObserverUnavailable('No reachable observer.')
            answer = self.post(observer, '/api/observer/plaza-reports', body, expect=202)
            kept = int(answer.get('accepted', 0)) if isinstance(answer, dict) else 0
            kept = max(0, min(kept, len(batch)))
        except Exception as exc:  # the observer is down or refused: these reports are lost
            with self.lock:
                self.counts['failed_batches'] += 1
            if self.clock() - self._warned_at > 60:
                self._warned_at = self.clock()
                LOGGER.warning('Monitoring reports could not be passed to the observer: %s', exc)
        with self.lock:
            self.queued_bytes -= sum(len(encoded) for _, encoded in batch)
            self.counts['forwarded'] += kept
            self.counts['dropped'] += len(batch) - kept
        for _ in batch:
            self.reports.task_done()
        return kept

    def flush(self) -> int:
        """Send what is queued now, batch by batch; returns how many reports the observer kept."""
        kept = 0
        while True:
            batch = self._take()
            if not batch:
                return kept
            kept += self._send(batch)

    def _send_loop(self):
        while not self._stopping.is_set():
            try:
                first = self.reports.get(timeout=0.5)
            except queue.Empty:
                continue
            self._send(self._take(first))

    def stop(self, timeout: float = 5.0):
        self._stopping.set()
        sender = self._sender
        if sender is not None and sender is not threading.current_thread():
            sender.join(timeout=timeout)
        self.flush()

    def stats(self) -> dict:
        with self.lock:
            return {**self.counts, 'queue_depth': self.reports.qsize(), 'queue_bytes': self.queued_bytes}

    # -- reads --------------------------------------------------------------------------------------------------

    def post(self, observer, path: str, raw: bytes, *, expect: int = 200):
        """One signed request to the observer; its JSON answer, or ObserverUnavailable."""
        state = self.service.attach()
        token = state.current_token_for_agent(observer['agentId']) if state else None
        if not token:
            raise ObserverUnavailable('The observer holds no current Plaza token.')
        headers = {**request_headers(token, observer['agentId'], 'POST', path, raw, clock=self.clock),
                   'Content-Type': 'application/json', 'Accept': 'application/json'}
        client = self.client_factory()
        try:
            response = client.post(origin_of(observer['address']) + path, content=raw, headers=headers)
            if response.status_code in (409, 422):
                try:
                    detail = response.json().get('detail')
                except (ValueError, AttributeError):
                    detail = None
                raise ObserverRefused(response.status_code, str(detail or 'The observer refused this request.')[:500])
            if response.status_code != expect:
                raise ObserverUnavailable(f'The observer answered {response.status_code}.')
            if len(response.content) > ANSWER_BYTES:
                raise ObserverUnavailable("The observer's answer is too large.")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ObserverUnavailable(f'The observer is unreachable: {type(exc).__name__}.') from None
        finally:
            client.close()

    def read(self, path: str, body: dict) -> dict:
        """A monitoring read answered by the observer; ObserverUnavailable when there is none or it cannot answer."""
        observer = self.observer()
        if not observer or not self.reachable(observer):
            raise ObserverUnavailable('No reachable observer.')
        answer = self.post(observer, path, json.dumps(body, separators=(',', ':')).encode())
        if not isinstance(answer, dict):
            raise ObserverUnavailable('The observer answered something else.')
        return answer
