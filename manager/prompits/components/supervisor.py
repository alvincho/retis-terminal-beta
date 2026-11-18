"""Run installed components: start, stop, adopt survivors, restart crashes with growing delays, rotate logs.

Each component runs in its own process group (session on macOS/Linux, process group without a console on
Windows) and writes to its own log file, so it keeps running when the manager stops. The manager records the
pid and the process start time; after a manager restart, `adopt()` takes over a recorded process only when
both still match, so it never starts a second copy and never stops a stranger that reused the pid.

Crash handling: a component that exits while it should be running is restarted after 1, 2, 4, ... seconds
(capped). A run that lasted `stable_after` seconds resets the count. `max_crashes` crashes within
`crash_window` seconds stop the restarts and mark the component as crash-looping until someone starts it again.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from prompits.components import logs
from prompits.components.parts_list import Component, PartsListError, parse_component
from prompits.components.state import Layout, State
from prompits.core.process_utils import background_popen_kwargs, pid_is_running, terminate_pid, terminate_process

try:  # psutil gives a reliable process start time on every platform
    import psutil
except ImportError:  # pragma: no cover - exercised only where psutil is missing
    psutil = None

# Only these variables pass from the manager to a component; the parts list adds the component's own.
INHERITED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SHELL",
                 "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR")
CREATE_NO_WINDOW = 0x08000000


class SupervisorError(RuntimeError):
    pass


@dataclass
class _Runtime:
    crashes: list[float]
    next_restart_at: float | None = None
    crash_loop: bool = False
    last_exit_code: int | None = None
    started_at: float | None = None


def process_start_time(pid: int) -> float | None:
    if psutil is None:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def recorded_process_alive(record: dict[str, Any]) -> bool:
    """True when the recorded pid is alive and is still the process we started (same start time)."""
    pid = int(record.get("pid") or 0)
    if pid <= 0 or not pid_is_running(pid):
        return False
    started = record.get("create_time")
    if psutil is not None and started is not None:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:  # ended, waiting to be reaped
                return False
            return abs(float(process.create_time()) - float(started)) < 1.0
        except Exception:
            return False
    if os.name != "nt":
        command = [str(part) for part in record.get("command") or []]
        found = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        return bool(command[1:]) and found.stdout.strip().endswith(" ".join(command[1:]))
    return False  # cannot prove identity: never adopt or stop it


class Supervisor:
    def __init__(self, state: State, layout: Layout, *, python: str = sys.executable,
                 clock: Callable[[], float] = time.time, backoff_base: float = 1.0, backoff_cap: float = 300.0,
                 stable_after: float = 600.0, crash_window: float = 600.0, max_crashes: int = 5,
                 max_log_bytes: int = 5 * 1024 * 1024, stop_timeout: float = 10.0):
        self.state = state
        self.layout = layout
        self.python = python
        self.clock = clock
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.stable_after = stable_after
        self.crash_window = crash_window
        self.max_crashes = max_crashes
        self.max_log_bytes = max_log_bytes
        self.stop_timeout = stop_timeout
        self._children: dict[str, subprocess.Popen] = {}
        self._adopted: dict[str, int] = {}
        self._runtime: dict[str, _Runtime] = {}
        # Variables given to one start (such as a key a client hands over): kept in memory for its restarts, never
        # written to the records. A manager that restarts no longer has them, and waits to be given them again.
        self._start_env: dict[str, dict[str, str]] = {}

    # ----- what to run
    def component(self, component_id: str) -> Component:
        record = self.state.read()["components"].get(component_id) or {}
        version = record.get("current")
        entry = (record.get("entries") or {}).get(version) if version else None
        if not entry:
            raise SupervisorError(f"{component_id} has no current version")
        try:
            return parse_component(entry)
        except PartsListError as error:
            raise SupervisorError(str(error)) from error

    def location(self, component: Component) -> Path:
        """Where the component's files are: its installed version, or the folder a bundled component lives in."""
        record = self.state.read()["components"].get(component.id) or {}
        return Path(record["location"]) if record.get("location") else self.layout.version_dir(component.id, component.version)

    def values_for(self, component: Component) -> dict[str, str]:
        return {
            "python": self.python,
            "component_dir": str(self.location(component)),
            "data_dir": str(self.layout.data_dir(component.id)),
            "port": str(component.run.port or "") if component.run else "",
        }

    def environment(self, component: Component, values: dict[str, str]) -> dict[str, str]:
        env = {name: os.environ[name] for name in INHERITED_ENV if name in os.environ}
        if component.run:
            env.update({name: value.format(**values) for name, value in component.run.env.items()})
        env.setdefault("PYTHONNOUSERSITE", "1")
        env.update(self._start_env.get(component.id, {}))
        return env

    def command_for(self, component: Component) -> tuple[list[str], dict[str, str], Path]:
        if component.run is None:
            raise SupervisorError(f"{component.id} is not a program the manager runs")
        values = self.values_for(component)
        command = [part.format(**values) for part in component.run.command]
        return command, self.environment(component, values), Path(values["component_dir"])

    # ----- lifecycle
    def adopt(self) -> dict[str, list[str]]:
        """Run once when the manager starts: take over survivors, forget dead records, start what should run."""
        adopted, forgotten, started = [], [], []
        with self.state.edit() as data:
            for component_id, record in list(data["processes"].items()):
                if recorded_process_alive(record):
                    self._adopted[component_id] = int(record["pid"])
                    self._runtime.setdefault(component_id, _Runtime(crashes=[])).started_at = record.get("started_at")
                    adopted.append(component_id)
                else:
                    data["processes"].pop(component_id)
                    forgotten.append(component_id)
            wanted = [cid for cid, record in data["components"].items() if record.get("desired") == "running"]
        for component_id in adopted:
            self.state.record_event("adopted", component_id, pid=self._adopted[component_id])
        for component_id in wanted:
            if not self.is_running(component_id):
                try:
                    self._launch(component_id)
                    started.append(component_id)
                except SupervisorError as error:
                    self.state.record_event("start_failed", component_id, reason=str(error))
        return {"adopted": adopted, "forgotten": forgotten, "started": started}

    def start(self, component_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
        if env is not None:
            self._start_env[component_id] = dict(env)
        with self.state.edit() as data:
            record = data["components"].get(component_id)
            if record is None:
                raise SupervisorError(f"{component_id} is not installed")
            record["desired"] = "running"
        runtime = self._runtime.setdefault(component_id, _Runtime(crashes=[]))
        runtime.crash_loop, runtime.crashes, runtime.next_restart_at = False, [], None
        if not self.is_running(component_id):
            self._launch(component_id)
        return self.runtime(component_id)

    def stop(self, component_id: str) -> dict[str, Any]:
        with self.state.edit() as data:
            record = data["components"].get(component_id)
            if record is not None:
                record["desired"] = "stopped"
        self._terminate(component_id)
        runtime = self._runtime.setdefault(component_id, _Runtime(crashes=[]))
        runtime.next_restart_at, runtime.crash_loop = None, False
        self.state.record_event("stopped", component_id)
        return self.runtime(component_id)

    def restart(self, component_id: str) -> dict[str, Any]:
        self._terminate(component_id)
        return self.start(component_id)

    def tick(self) -> list[dict[str, Any]]:
        """Call every few seconds: notice exits, restart what is due, rotate logs. Returns the events raised."""
        events = []
        for component_id, process in list(self._children.items()):
            code = process.poll()
            if code is not None:
                self._children.pop(component_id)
                events.extend(self._exited(component_id, code))
        for component_id, pid in list(self._adopted.items()):
            record = self.state.read()["processes"].get(component_id) or {"pid": pid}
            if not recorded_process_alive(record):
                self._adopted.pop(component_id)
                events.extend(self._exited(component_id, None))
        now = self.clock()
        desired = {cid: record.get("desired") for cid, record in self.state.read()["components"].items()}
        for component_id, runtime in self._runtime.items():
            if runtime.next_restart_at is not None and now >= runtime.next_restart_at:
                runtime.next_restart_at = None
                if desired.get(component_id) == "running" and not self.is_running(component_id):
                    try:
                        self._launch(component_id)
                    except SupervisorError as error:
                        events.append(self.state.record_event("start_failed", component_id, reason=str(error)))
        for component_id in desired:
            logs.rotate_if_needed(self.layout.log_path(component_id), max_bytes=self.max_log_bytes)
        return events

    def shutdown(self, *, stop_components: bool = False):
        """Leave components running (the default) or stop them all, e.g. on uninstall."""
        if stop_components:
            for component_id in list(self._children) + list(self._adopted):
                self._terminate(component_id)
        self._children.clear()
        self._adopted.clear()

    # ----- views
    def is_running(self, component_id: str) -> bool:
        process = self._children.get(component_id)
        if process is not None and process.poll() is None:
            return True
        return component_id in self._adopted

    def pid(self, component_id: str) -> int | None:
        process = self._children.get(component_id)
        if process is not None and process.poll() is None:
            return process.pid
        return self._adopted.get(component_id)

    def runtime(self, component_id: str) -> dict[str, Any]:
        runtime = self._runtime.get(component_id) or _Runtime(crashes=[])
        now = self.clock()
        return {
            "running": self.is_running(component_id),
            "pid": self.pid(component_id),
            "adopted": component_id in self._adopted,
            "started_at": runtime.started_at if self.is_running(component_id) else None,
            "restart_attempt": len(runtime.crashes),
            "restart_in": max(0.0, runtime.next_restart_at - now) if runtime.next_restart_at is not None else None,
            "crash_loop": runtime.crash_loop,
            "last_exit_code": runtime.last_exit_code,
        }

    # ----- internals
    def _launch(self, component_id: str):
        component = self.component(component_id)
        command, env, cwd = self.command_for(component)
        log_path = self.layout.log_path(component_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = background_popen_kwargs()
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        with open(log_path, "ab") as log:
            try:
                process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, **kwargs)
            except OSError as error:
                raise SupervisorError(f"{component_id} could not start: {error}") from error
        started_at = self.clock()
        self._children[component_id] = process
        runtime = self._runtime.setdefault(component_id, _Runtime(crashes=[]))
        runtime.started_at = started_at
        with self.state.edit() as data:
            data["processes"][component_id] = {
                "pid": process.pid, "create_time": process_start_time(process.pid), "command": command,
                "version": component.version, "started_at": started_at,
            }
        self.state.record_event("started", component_id, version=component.version, pid=process.pid)

    def _terminate(self, component_id: str):
        process = self._children.pop(component_id, None)
        if process is not None:
            terminate_process(process, timeout_sec=self.stop_timeout)
        pid = self._adopted.pop(component_id, None)
        if pid is not None:
            record = self.state.read()["processes"].get(component_id) or {}
            if recorded_process_alive(record):
                terminate_pid(pid, timeout_sec=self.stop_timeout)
        with self.state.edit() as data:
            data["processes"].pop(component_id, None)

    def _exited(self, component_id: str, code: int | None) -> list[dict[str, Any]]:
        with self.state.edit() as data:
            data["processes"].pop(component_id, None)
            desired = (data["components"].get(component_id) or {}).get("desired")
        runtime = self._runtime.setdefault(component_id, _Runtime(crashes=[]))
        runtime.last_exit_code = code
        now = self.clock()
        if desired != "running":
            return [self.state.record_event("exited", component_id, code=code)]
        if runtime.started_at is not None and now - runtime.started_at >= self.stable_after:
            runtime.crashes = []
        runtime.crashes = [at for at in runtime.crashes if now - at < self.crash_window] + [now]
        attempt = len(runtime.crashes)
        if attempt >= self.max_crashes:
            runtime.crash_loop, runtime.next_restart_at = True, None
            return [self.state.record_event("crash_loop", component_id, code=code, crashes=attempt)]
        delay = min(self.backoff_base * 2 ** (attempt - 1), self.backoff_cap)
        runtime.next_restart_at = now + delay
        return [self.state.record_event("crashed", component_id, code=code, attempt=attempt, restart_in=delay)]
