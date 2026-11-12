"""Shared telemetry for standalone ASGI agents using an existing Plaza identity."""
import threading
import time
import uuid
from functools import wraps
from .observability import RuntimeObservations, trace_headers


class ASGIObservability:
    PLAZA_HEARTBEAT_INTERVAL = 15
    AGENT_RUNTIME_VERSION = '1'

    def __init__(self, registration, *, path=None):
        self.registration = registration
        from urllib.parse import urlsplit
        self.port = urlsplit(registration.public_url).port or 443
        self.jobs, self.lock = {}, threading.RLock()
        self.observations = RuntimeObservations(self, path=path)

    @property
    def name(self):
        return self.registration.agent_name

    @property
    def agent_id(self):
        return self.registration.agent_id

    @property
    def status(self):
        return self.registration.state

    @property
    def working_list(self):
        with self.lock:
            return list(self.jobs.values())

    def collect_health(self):
        return {'status': 'healthy' if self.registration.registered else 'degraded',
                'reasons': [] if self.registration.registered else ['Plaza connection is recovering']}

    def collect_display_name(self):
        """The running instance's name, when the registration offers one."""
        value = getattr(self.registration, 'display_name', None)
        return value() if callable(value) else value or ''

    def collect_metrics(self):
        return [{'name':'active_requests', 'value':len(self.working_list), 'kind':'gauge','unit':'count','labels':{}}]

    def observe_client(self, client):
        """Observe actual HTTP sends, including background and brokered work.

        Wrap the public send API so mounts, proxies, caller hooks and streaming
        remain owned by HTTPX. Only origin/path/status are captured, never
        request headers, query parameters, bodies or response content.
        Streaming duration measures receipt of headers, not stream lifetime.
        """
        if getattr(client, '_prompits_observer', None) is self:
            return client
        if getattr(client, '_prompits_observer', None) is not None:
            raise ValueError('An HTTP client cannot report for two agent identities.')
        send = client.send

        @wraps(send)
        async def observed(request, *args, **kwargs):
            # HTTPX's URL authority may include credentials. Build an origin
            # from host/port only; the query is excluded from the operation.
            host = request.url.host
            if ':' in host:
                host = '[' + host + ']'
            origin = request.url.scheme + '://' + host
            if request.url.port is not None:
                origin += ':' + str(request.url.port)
            label = request.method + ' ' + request.url.path
            work_id = uuid.uuid4().hex
            with self.lock:
                self.jobs[work_id] = {'id':work_id, 'label':label, 'kind':'outbound_http',
                                      'status':'running', 'started_at':time.time(), 'peer_origin':origin}
            started, outcome, status_code = time.monotonic(), 'failed', None
            try:
                with self.observations.span(label, kind='outbound_http', job_id=work_id,
                                            traceparent=request.headers.get('traceparent')) as context:
                    for key, value in trace_headers().items():
                        request.headers.setdefault(key, value)
                    try:
                        response = await send(request, *args, **kwargs)
                        status_code = response.status_code
                        outcome = 'failed' if status_code >= 400 else 'succeeded'
                        if outcome == 'failed':
                            context['outcome'] = 'error'
                        return response
                    except BaseException as exc:
                        if type(exc).__name__ == 'CancelledError':
                            outcome = 'cancelled'
                        raise
                    finally:
                        duration = round((time.monotonic() - started) * 1000, 2)
                        self.observations.event('interaction', direction='outbound', phase='result',
                            operation=label, peer_origin=origin, status=outcome,
                            status_code=status_code, duration_ms=duration)
                        self.observations.metric('agent_calls_total', 1, kind='counter',
                            labels={'peer':origin, 'status':outcome})
                        self.observations.metric('agent_call_duration_ms', duration, unit='ms',
                            labels={'peer':origin})
            finally:
                with self.lock:
                    self.jobs.pop(work_id, None)

        client.send = observed
        client._prompits_observer = self
        return client

    def install(self, app):
        from fastapi import Request
        @app.middleware('http')
        async def observe(request: Request, call_next):
            if not request.url.path.startswith(('/api/', '/use_practice/')) or request.url.path.startswith('/api/agent/'):
                return await call_next(request)
            work_id = uuid.uuid4().hex
            label = request.method+' '+request.url.path
            with self.lock:
                self.jobs[work_id] = {'id': work_id, 'label':label,'kind':'http','status':'running','started_at':time.time()}
            try:
                with self.observations.span(label, kind='http', job_id=work_id, traceparent=request.headers.get('traceparent')) as context:
                    response = await call_next(request)
                    if response.status_code >= 400:
                        context['outcome'] = 'error'
                    return response
            finally:
                with self.lock:
                    self.jobs.pop(work_id, None)
        @app.on_event('shutdown')
        def close_logs():
            self.observations.close_log_capture()

        @app.get('/api/agent/status')
        async def status():
            # Detailed jobs and logs travel only through the authenticated Plaza.
            return {'name':self.name,'status':self.status,'health':self.collect_health()}
