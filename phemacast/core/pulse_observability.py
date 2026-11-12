"""Pulse-specific counters, timings and trace spans owned by Phemacast."""
from contextlib import contextmanager
from functools import wraps
import inspect
import contextvars
import time
from collections.abc import Mapping

from prompits.core.observability import TRACE

ACTIVE_PULSE = contextvars.ContextVar("phemacast_observed_pulse", default=None)


def observed_pulse(method):
    signature = inspect.signature(method)

    @wraps(method)
    def invoke(self, *args, **kwargs):
        arguments = signature.bind(self, *args, **kwargs).arguments
        pulse = arguments.get('pulse_name')
        if not pulse:
            resolver = getattr(self, 'resolve_pulse_definition', None)
            try:
                definition = resolver(pulse_address=arguments.get('pulse_address')) if resolver else (getattr(self, 'supported_pulses', []) or [{}])[0]
                pulse = definition.get('name') or definition.get('pulse_name')
            except Exception:
                pass  # Metric labels must not change execution behavior.
        pulse = str(pulse or arguments.get('pulse_address') or 'default')[:160]
        labels = {'pulse': pulse}
        runtime = self.observations
        identity = (runtime.instance_id, pulse)
        if ACTIVE_PULSE.get() == identity:
            return method(self, *args, **kwargs)
        reset = ACTIVE_PULSE.set(identity)
        runtime.metric('pulse_inflight', 1, labels=labels, increment=True)
        started, status = time.monotonic(), 'error'
        inputs = arguments.get('input_data')
        target = (inputs.get('endpoint_id') or inputs.get('dataset_id')) if isinstance(inputs, Mapping) else None
        label = pulse + (' · ' + str(target)[:160] if target else '')
        work_id = self._start_runtime_work(label=label, detail='Serving ' + pulse, kind='job')
        try:
            with runtime.span(label, kind='job', job_id=work_id) as span:
                error = ''
                try:
                    result = method(self, *args, **kwargs)
                    response_status = result.get('status') if isinstance(result, dict) else None
                    status = ('error' if isinstance(result, dict) and (result.get('error') or response_status in ('error', 'failed'))
                              else response_status if response_status in ('retry', 'cancelled') else 'success')
                    span['outcome'] = status
                    error = str(result.get('error') or '') if isinstance(result, dict) else ''
                    return result
                except BaseException as exc:
                    status = 'cancelled' if type(exc).__name__ == 'CancelledError' else 'error'
                    error = type(exc).__name__
                    raise
                finally:
                    self._finish_runtime_work(work_id, status=status, error=error)
        except BaseException as exc:
            status = 'cancelled' if type(exc).__name__ == 'CancelledError' else 'error'
            raise
        finally:
            elapsed = (time.monotonic() - started) * 1000
            runtime.metric('pulse_inflight', -1, labels=labels, increment=True)
            runtime.metric('pulse_requests_total', 1, kind='counter', labels={**labels, 'status': status})
            runtime.metric('pulse_duration_ms_sum', elapsed, unit='ms', kind='counter', labels=labels)
            runtime.metric('pulse_duration_ms_count', 1, kind='counter', labels=labels)
            for boundary in (100, 500, 1000, 5000, 30000, 240000):
                if elapsed <= boundary:
                    runtime.metric('pulse_duration_ms_bucket', 1, kind='counter', labels={**labels, 'le': str(boundary)})
            if status == 'success':
                runtime.metric('pulse_last_success', time.time(), unit='unix_seconds', labels=labels)
            runtime.event('pulse_finish', operation=pulse, status=status, duration_ms=elapsed)
            ACTIVE_PULSE.reset(reset)
    invoke._pulse_observed = True
    return invoke


def active_pulse_work(agent):
    """Reuse the observed request's work item in the legacy Pulser tracker."""
    identity = ACTIVE_PULSE.get()
    if identity and identity[0] == agent.observations.instance_id:
        return (TRACE.get() or {}).get('job_id')
    return None


def recover_pulse_jobs(store, agent_id, logs):
    """Recover real legacy completion evidence from owner-authorized agent logs.

    An operator supplies logs from the specified trusted agent. Counters and
    unfinished spans are never converted into completed work. Stable span IDs
    make this safe to rerun and retain independent executions on the same trace.
    """
    recovered = 0
    with store.connect() as db:
        for item in logs:
            if (item.get('agent_id') != agent_id or item.get('message') != 'span_finish'
                    or item.get('kind') != 'pulse' or not item.get('span_id') or not item.get('trace_id')):
                continue
            work_id = 'pulse-' + str(item.get('instance_id') or 'legacy') + '-' + str(item['span_id'])
            event = {**item, 'event': 'job_finish', 'kind': 'job', 'job_id': work_id,
                     'id': work_id, 'operation': str(item.get('operation') or 'Pulse request')}
            store.job_history.capture(db, agent_id, 'legacy', event,
                {work_id: {'detail': 'Recovered from a recorded pulse completion.',
                           'log_job_id': str(item.get('job_id') or '')}}, store.clock())
            recovered += 1
        store.job_history.prune(db, agent_id)
    return recovered


@contextmanager
def pulse_stage(agent, pulse, stage):
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed = (time.monotonic() - started) * 1000
        agent.observations.metric('pulse_stage_duration_ms', elapsed, unit='ms', labels={'pulse': str(pulse), 'stage': stage})


def register_pulse_panel(agent):
    panel = {"id": "pulses", "label": "Pulses", "type": "metric_groups", "prefix": "pulse_", "group_by": "pulse",
             "measures": [
                 {"label": "Calls", "metric": "pulse_requests_total"},
                 {"label": "Errors", "metric": "pulse_requests_total", "filter": {"status": "error"}},
                 {"label": "Retries", "metric": "pulse_requests_total", "filter": {"status": "retry"}},
                 {"label": "In flight", "metric": "pulse_inflight"},
                 {"label": "Mean", "metric": "pulse_duration_ms_sum", "divide_by": "pulse_duration_ms_count", "unit": "ms"}],
             "timestamp_metric": "pulse_last_success", "stage_metric": "pulse_stage_duration_ms",
             "notes": "Counters reset when the agent restarts. Durations describe observed executions."}
    if not any(p.get("id") == panel["id"] for p in agent.observations.panels):
        agent.observations.panels.append(panel)
