"""Named Plaza coordination jobs using the normal runtime and diagnostic logs."""
import asyncio
from contextlib import contextmanager
from functools import wraps

from starlette.concurrency import run_in_threadpool


def _persist(state, completion):
    store = getattr(state, 'observation_store', None)
    identity = getattr(state, 'runtime_agent_id', None)
    if store is not None and identity and completion:
        # Save the actual runtime completion immediately: short coordination
        # jobs must survive eviction from the heartbeat's small event buffer.
        store.record_event(identity, completion, instance=state.runtime_agent.observations.instance_id)


@contextmanager
def track_plaza_job(state, label, *, completion=None, persist=True):
    agent = getattr(state, 'runtime_agent', None)
    if agent is None or not callable(getattr(agent, '_start_runtime_work', None)) or not getattr(agent, 'observations', None):
        yield None
        return
    result = completion if completion is not None else {}
    work_id = agent._start_runtime_work(label=label, kind='job', detail=label)
    try:
        with agent.observations.span(label, kind='job', job_id=work_id):
            status, error = 'success', ''
            try:
                yield work_id
            except BaseException as exc:
                status = 'cancelled' if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit)) else 'error'
                error = type(exc).__name__
                raise
            finally:
                event = agent._finish_runtime_work(work_id, status=status, error=error)
                if event:
                    result.update(event)
    finally:
        if persist:
            _persist(state, result)


def coordination_job(state, label):
    """Preserve endpoint signatures and keep diagnostic writes off the event loop."""
    def decorate(fn):
        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def asynchronous(*args, **kwargs):
                completion = {}
                try:
                    with track_plaza_job(state, label, completion=completion, persist=False):
                        return await fn(*args, **kwargs)
                finally:
                    if completion:
                        await run_in_threadpool(_persist, state, completion)
            return asynchronous

        @wraps(fn)
        def synchronous(*args, **kwargs):
            with track_plaza_job(state, label):
                return fn(*args, **kwargs)
        return synchronous
    return decorate
