"""What components use (CPU, memory, disk), what needs attention, and the one-shot jobs that fetch their data.

- `Usage`: CPU and memory of a component's process and its children, from psutil, sampled between calls.
- `folder_size`: bytes under a folder, cached for a few minutes because data folders can hold gigabytes.
- `DataJobs`: a component's data download, run as its own process (the parts list names its command), with its
  output in its own log, so a download survives the page closing and is never part of the package.
- `alerts`: the conditions the manager's page shows at the top: a component that keeps crashing or has been
  unhealthy for a while, an update waiting, and low disk space.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from prompits.core.process_utils import background_popen_kwargs, terminate_process

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

LOW_DISK_BYTES = 2 * 1024 ** 3
LOW_DISK_SHARE = 0.05
UNHEALTHY_ALERT_SECONDS = 600


class Usage:
    """CPU percent (since the previous sample of the same process) and resident memory, children included."""

    def __init__(self):
        self._processes: dict[int, Any] = {}

    def sample(self, pid: int | None) -> dict[str, Any] | None:
        if not pid or psutil is None:
            return None
        try:
            process = self._processes.get(pid)
            if process is None or not process.is_running():
                process = self._processes[pid] = psutil.Process(pid)
                process.cpu_percent(None)  # the first reading starts the interval
            family = [process]
            try:
                family += process.children(recursive=True)
            except psutil.Error:
                pass
            cpu = memory = 0.0
            for member in family:
                try:
                    if member.pid != pid:
                        cached = self._processes.setdefault(member.pid, member)
                        cpu += cached.cpu_percent(None)
                    else:
                        cpu += process.cpu_percent(None)
                    memory += member.memory_info().rss
                except psutil.Error:
                    continue
            return {'cpu_percent': round(cpu, 1), 'memory_bytes': int(memory), 'processes': len(family)}
        except psutil.Error:
            self._processes.pop(pid, None)
            return None


_SIZES: dict[str, tuple[float, int]] = {}


def forget_size(path: Path) -> None:
    """Drop a folder's remembered size, so the next read walks it again."""
    _SIZES.pop(str(path), None)


def folder_size(path: Path, *, max_age: float = 300.0, clock: Callable[[], float] = time.monotonic) -> int:
    key = str(path)
    cached = _SIZES.get(key)
    if cached and clock() - cached[0] < max_age:
        return cached[1]
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    _SIZES[key] = (clock(), total)
    return total


def disk(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    low = usage.free < LOW_DISK_BYTES or usage.free < usage.total * LOW_DISK_SHARE
    return {'free_bytes': usage.free, 'total_bytes': usage.total, 'low': low}


class DataJobs:
    """At most one data download per component, run as a separate process with its own log."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def start(self, component_id: str, data_id: str, command: list[str], env: dict[str, str], cwd: Path, log_path: Path):
        if self.running(component_id):
            raise RuntimeError(f'{component_id} is already downloading its data.')
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'ab') as log:
            process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, **background_popen_kwargs())
        self._processes[component_id] = process
        self.jobs[component_id] = {'data': data_id, 'state': 'running', 'started_at': self.clock(), 'log': str(log_path)}
        return dict(self.jobs[component_id])

    def running(self, component_id: str) -> bool:
        process = self._processes.get(component_id)
        return process is not None and process.poll() is None

    def poll(self) -> list[dict[str, Any]]:
        """Finish jobs whose process ended; return those jobs."""
        ended = []
        for component_id, process in list(self._processes.items()):
            code = process.poll()
            if code is None:
                continue
            self._processes.pop(component_id)
            job = self.jobs[component_id]
            job.update(state='done' if code == 0 else 'failed', code=code, ended_at=self.clock())
            ended.append({'component': component_id, **job})
        return ended

    def stop(self, component_id: str):
        process = self._processes.pop(component_id, None)
        if process is not None:
            terminate_process(process)
            self.jobs[component_id].update(state='stopped', ended_at=self.clock())

    def view(self, component_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(component_id)
        if not job:
            return None
        from prompits.components.logs import tail
        lines = tail(Path(job['log']), 1)
        return {**job, 'last_line': lines[-1][:300] if lines else ''}


def alerts(rows: list[dict[str, Any]], disk_state: dict[str, Any] | None, *, now: float) -> list[dict[str, Any]]:
    found = []
    for row in rows:
        process = row.get('process') or {}
        if process.get('crash_loop'):
            found.append({'kind': 'crash_loop', 'component': row['id'], 'severity': 'error'})
        elif row.get('state') == 'unhealthy' and process.get('started_at') and now - process['started_at'] > UNHEALTHY_ALERT_SECONDS:
            found.append({'kind': 'unhealthy', 'component': row['id'], 'severity': 'warning', 'reason': row.get('reason', '')})
        if row.get('update'):
            found.append({'kind': 'update', 'component': row['id'], 'severity': 'info', 'version': row['update']})
        job = row.get('data_job') or {}
        if job.get('state') == 'failed':
            found.append({'kind': 'data_failed', 'component': row['id'], 'severity': 'warning'})
    if disk_state and disk_state.get('low'):
        found.append({'kind': 'low_disk', 'component': None, 'severity': 'error', 'free_bytes': disk_state['free_bytes']})
    return found
