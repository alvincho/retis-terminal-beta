"""`ComponentManager`: one facade over parts list, installer, supervisor and health checks.

Every public method returns plain JSON-ready values, so an agent can serve them as operations to a local
client today and to the owner's other computers later.

Statuses (`status()[i]["state"]`): not_installed, installing, stopped, waiting, starting, running, needs_data,
unhealthy, restarting. `waiting`: wanted, but it starts only with variables whoever starts it hands over
(`run.handover`), and none were handed over since this manager started. `update` is reported beside the state, as the version a newer parts list
offers, because an update can be available whatever the component is doing.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from prompits.components import health as health_checks
from prompits.components import logs
from prompits.components import monitor
from prompits.components.report import write_report
from prompits.components.installer import InstallError, Installer, Opener, https_opener, pip_libraries
from prompits.components.parts_list import Component, PartsList, PartsListError, parse_component, parse_payload, verify
from prompits.components.platforms import current_platform
from prompits.components.state import Layout, State
from prompits.components.supervisor import Supervisor, SupervisorError

NEEDS_STATES = {"data": "needs_data"}
NOTICES = "THIRD_PARTY_NOTICES.txt"


class ManagerError(RuntimeError):
    pass


def _term_key(term: Mapping[str, Any]) -> str:
    return f"{term.get('id')}@{term.get('version')}"


class ComponentManager:
    def __init__(self, root, trusted_keys: Mapping[str, bytes], *, platform: str | None = None,
                 opener: Opener = https_opener, python: str = sys.executable, clock: Callable[[], float] = time.time,
                 probe: Callable[..., health_checks.HealthReport] = health_checks.probe, start_grace: float = 60.0,
                 install_libraries: bool = True, pip: Sequence[str] | None = None, **supervisor_options: Any):
        self.layout = Layout(root)
        self.state = State(root)
        self.trusted_keys = dict(trusted_keys)
        self.platform = platform or current_platform()
        self.clock = clock
        self.probe = probe
        self.start_grace = start_grace
        self.installer = Installer(self.state, self.layout, opener=opener,
                                   libraries=pip_libraries(python, pip=pip) if install_libraries else None)
        self.supervisor = Supervisor(self.state, self.layout, python=python, clock=clock, **supervisor_options)
        self._installing: dict[str, dict[str, Any]] = {}
        # Components the host app ships (offer_included): offered without a parts list, kept in memory only.
        self._included: dict[str, tuple[Component, Path]] = {}
        self.usage = monitor.Usage()
        self.data_jobs = monitor.DataJobs(clock)

    # ----- parts list
    def accept_parts_list(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Verify a signed parts list and keep it if it is at least as new as the one already accepted."""
        current = self.state.read().get("parts_list") or {}
        try:
            parts = verify(envelope, self.trusted_keys, min_sequence=int(current.get("sequence") or 0))
        except PartsListError as error:
            raise ManagerError(str(error)) from error
        with self.state.edit() as data:
            data["parts_list"] = {"sequence": parts.sequence, "publisher": parts.publisher,
                                  "issued_at": parts.issued_at, "payload": dict(envelope["payload"])}
        self.state.record_event("parts_list_accepted", None, sequence=parts.sequence)
        return {"sequence": parts.sequence, "components": [item.id for item in parts.components]}

    def parts_list(self) -> PartsList | None:
        accepted = self.state.read().get("parts_list") or {}
        return parse_payload(accepted["payload"]) if accepted.get("payload") else None

    def offer_included(self, entry: Mapping[str, Any], folder) -> None:
        """Offer a component the host app ships in `folder` (its files, without its libraries).

        Installing it copies the folder instead of downloading a package; its `requirements.lock` libraries still
        install from the package index. A parts list that offers the same component takes precedence. The host app
        offers it again at every start, so nothing about the offer is written to the records."""
        try:
            component = parse_component({**dict(entry), "artifacts": {}})
        except PartsListError as error:
            raise ManagerError(str(error)) from error
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ManagerError(f"{component.id}: {folder} is not a folder")
        self._included[component.id] = (component, folder)

    def _offer(self, component_id: str) -> tuple[Component | None, Path | None]:
        """What is offered for the component: the parts list's version, else the host app's, with its folder."""
        parts = self.parts_list()
        component = parts.component(component_id) if parts else None
        if component is not None:
            return component, None
        if component_id in self._included:
            return self._included[component_id]
        return None, None

    def _offered(self, component_id: str) -> Component:
        component, _ = self._offer(component_id)
        if component is None:
            raise ManagerError(f"{component_id} is not in the parts list")
        if not self._offered_here(component_id):
            raise ManagerError(f"{component_id} is not offered for this computer ({self.platform})")
        return component

    def _offered_here(self, component_id: str) -> bool:
        """A parts-list offer has a package for this platform; an app's copy names the platforms it installs on."""
        component, included = self._offer(component_id)
        if component is None:
            return False
        if included is None:
            return component.artifact_for(self.platform) is not None
        platforms = component.raw.get("platforms")
        return not platforms or self.platform in platforms

    def notices_text(self, component_id: str) -> str:
        """The component's third-party notices (THIRD_PARTY_NOTICES.txt): the installed version's, else the offer's."""
        record = self.state.read()["components"].get(component_id) or {}
        folders = []
        if record.get("current"):
            folders.append(Path(record["location"]) if record.get("location")
                           else self.layout.version_dir(component_id, record["current"]))
        _, included = self._offer(component_id)
        if included is not None:
            folders.append(included)
        for folder in folders:
            path = folder / NOTICES
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        return ""

    # ----- install / update / remove
    def download(self, component_id: str, *, accept_terms: Iterable[str] = ()) -> dict[str, Any]:
        """Fetch and unpack the offered version beside the installed ones, without switching or touching processes.

        Safe to run while other calls start and stop components; `install` and `update` then find it in place.
        """
        component = self._offered(component_id)
        self._check_terms(component, accept_terms)
        self._download(component)
        return {"id": component_id, "version": component.version}

    def install(self, component_id: str, *, accept_terms: Iterable[str] = ()) -> dict[str, Any]:
        """Install the offered version. Every term must be accepted as `<id>@<version>`."""
        component = self._offered(component_id)
        self.download(component_id, accept_terms=accept_terms)
        record = self.state.read()["components"][component_id]
        if not record.get("current"):
            self.installer.activate(component_id, component.version)
        return self._row(component_id)

    def update(self, component_id: str, *, restart: bool = True, accept_terms: Iterable[str] = ()) -> dict[str, Any]:
        """Install the offered version beside the current one and switch to it.

        A running component switches when it restarts: now if `restart`, otherwise at its next `start`.
        The previous version stays for `rollback`; older ones are removed.
        """
        component = self._offered(component_id)
        record = self.state.read()["components"].get(component_id)
        if not record:
            raise ManagerError(f"{component_id} is not installed")
        if record.get("current") == component.version:
            return self._row(component_id)
        self._check_terms(component, accept_terms)
        self._download(component)
        if self.supervisor.is_running(component_id) and not restart:
            with self.state.edit() as data:
                data["components"][component_id]["pending"] = component.version
            return self._row(component_id)
        was_running = self.supervisor.is_running(component_id)
        if was_running:
            self.supervisor.stop(component_id)
        self.installer.activate(component_id, component.version)
        self.state.record_event("updated", component_id, version=component.version)
        if was_running:
            self.supervisor.start(component_id)
        self.installer.prune(component_id)
        return self._row(component_id)

    def rollback(self, component_id: str) -> dict[str, Any]:
        was_running = self.supervisor.is_running(component_id)
        if was_running:
            self.supervisor.stop(component_id)
        try:
            self.installer.rollback(component_id)
        except InstallError as error:
            raise ManagerError(str(error)) from error
        if was_running:
            self.supervisor.start(component_id)
        return self._row(component_id)

    def remove(self, component_id: str, *, keep_data: bool = True) -> dict[str, Any]:
        """Stop and remove the component. Keeping its data also keeps the terms the user accepted for it, so
        installing it again asks only for terms that changed; removing everything forgets them too."""
        if self.supervisor.is_running(component_id):
            self.supervisor.stop(component_id)
        with self.state.edit() as data:
            kept = data.setdefault("accepted_terms", {})
            accepted = self._accepted_in(data, component_id)
            if keep_data and accepted:
                kept[component_id] = sorted(accepted)
            else:
                kept.pop(component_id, None)
        self.installer.remove(component_id, keep_data=keep_data)
        return self._row(component_id)

    # ----- run
    def register_bundled(self, entry: Mapping[str, Any], folder) -> dict[str, Any]:
        """Run a component that ships inside its host app (`folder`) instead of downloading it.

        Its version is the app's; it updates with the app, not from the parts list."""
        try:
            component = parse_component({**dict(entry), 'artifacts': {}})
        except PartsListError as error:
            raise ManagerError(str(error)) from error
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ManagerError(f'{component.id}: {folder} is not a folder')
        with self.state.edit() as data:
            record = data['components'].setdefault(component.id, {'desired': 'stopped', 'entries': {}})
            record.update(current=component.version, previous=None, location=str(folder), bundled=True)
            record['entries'] = {component.version: dict(component.raw, artifacts={})}
        return self._row(component.id, probe=False)

    def start(self, component_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        pending = (self.state.read()["components"].get(component_id) or {}).get("pending")
        if pending and not self.supervisor.is_running(component_id):
            self.installer.activate(component_id, pending)
            with self.state.edit() as data:
                data["components"][component_id].pop("pending", None)
            self.state.record_event("updated", component_id, version=pending)
            self.installer.prune(component_id)
        try:
            self.supervisor.start(component_id, dict(env) if env is not None else None)
        except SupervisorError as error:
            raise ManagerError(str(error)) from error
        return self._row(component_id)

    def stop(self, component_id: str) -> dict[str, Any]:
        self.supervisor.stop(component_id)
        return self._row(component_id)

    def restart(self, component_id: str) -> dict[str, Any]:
        self.stop(component_id)
        return self.start(component_id)

    def resume(self) -> dict[str, list[str]]:
        """Call once when the manager starts."""
        self.state.prune_events()
        return self.supervisor.adopt()

    def tick(self) -> list[dict[str, Any]]:
        events = self.supervisor.tick()
        for job in self.data_jobs.poll():
            kind = 'data_downloaded' if job['state'] == 'done' else 'data_download_failed'
            monitor.forget_size(self.layout.data_dir(job['component']))
            events.append(self.state.record_event(kind, job['component'], data=job['data'], code=job.get('code')))
        return events

    # ----- data downloads
    def download_data(self, component_id: str, data_id: str) -> dict[str, Any]:
        """Start the component's download of one of its data sets, as its parts list describes."""
        try:
            component = self.supervisor.component(component_id)
        except SupervisorError as error:
            raise ManagerError(str(error)) from error
        item = next((entry for entry in component.data if entry.get('id') == data_id), None)
        if item is None or not item.get('command'):
            raise ManagerError(f'{component_id} offers no download for {data_id}.')
        values = self.supervisor.values_for(component)
        command = [part.format(**values) for part in item['command']]
        log = self.layout.log_path(component_id).with_name(f'{component_id}-data.log')
        try:
            job = self.data_jobs.start(component_id, data_id, command, self.supervisor.environment(component, values),
                                       Path(values['component_dir']), log)
        except RuntimeError as error:
            raise ManagerError(str(error)) from error
        self.state.record_event('data_download_started', component_id, data=data_id)
        return job

    def stop_download(self, component_id: str) -> dict[str, Any]:
        self.data_jobs.stop(component_id)
        monitor.forget_size(self.layout.data_dir(component_id))
        return self._row(component_id)

    # ----- monitoring
    def overview(self, *, language: str = 'en', probe: bool = True) -> dict[str, Any]:
        """Every component's row, this computer's disk, and what needs attention."""
        rows = self.status(language=language, probe=probe)
        space = monitor.disk(self.layout.root)
        return {'components': rows, 'disk': space, 'alerts': monitor.alerts(rows, space, now=self.clock())}

    def report(self, *, extra: dict[str, Any] | None = None, attachments: dict[str, str] | None = None) -> dict[str, Any]:
        """Write a problem report (records, recent events, every log's end, no credentials); return where it is.

        `attachments` maps a plain file name to text the host attaches, such as a component's own report."""
        data = self.state.read()
        files = {}
        for component_id in data['components']:
            log = self.layout.log_path(component_id)
            files[component_id] = [p for p in (log, log.with_name(log.name + '.1'), log.with_name(f'{component_id}-data.log')) if p.exists()]
        path = write_report(self.layout.root, data, self.state.events(since=self.clock() - 7 * 86400), files,
                            extra=extra, attachments=attachments, clock=self.clock)
        self.state.record_event('report_written', None, file=path.name)
        return {'path': str(path), 'bytes': path.stat().st_size}

    # ----- views
    def status(self, *, language: str = "en", probe: bool = True) -> list[dict[str, Any]]:
        parts = self.parts_list()
        ids = list(dict.fromkeys([*(item.id for item in (parts.components if parts else ())), *self._included,
                                  *self.state.read()["components"].keys()]))
        return [self._row(component_id, language=language, probe=probe) for component_id in ids]

    def logs(self, component_id: str, lines: int = 200) -> list[str]:
        return logs.tail(self.layout.log_path(component_id), lines)

    def data_logs(self, component_id: str, lines: int = 200) -> list[str]:
        """The end of the component's data download log (empty when it never downloaded)."""
        return logs.tail(self.layout.log_path(component_id).with_name(f'{component_id}-data.log'), lines)

    def events(self, *, since: float = 0.0, component_id: str | None = None) -> list[dict[str, Any]]:
        return self.state.events(since=since, component_id=component_id)

    # ----- internals
    @staticmethod
    def _accepted_in(data: Mapping[str, Any], component_id: str) -> set[str]:
        """The terms the user accepted for the component (`<id>@<version>`); older records kept them on the record."""
        record = data["components"].get(component_id) or {}
        return set((data.get("accepted_terms") or {}).get(component_id) or ()) | set(record.get("accepted_terms") or ())

    def _check_terms(self, component: Component, accept_terms: Iterable[str]):
        """Every term must be accepted, now or earlier: a term accepted once at its version is not asked again."""
        wanted = [_term_key(term) for term in component.terms]
        accepted = set(accept_terms) | self._accepted_in(self.state.read(), component.id)
        missing = [key for key in wanted if key not in accepted]
        if missing:
            raise ManagerError(f"{component.id} needs these terms accepted first: {', '.join(missing)}")
        if wanted:
            with self.state.edit() as data:
                kept = data.setdefault("accepted_terms", {})
                kept[component.id] = sorted(self._accepted_in(data, component.id) | set(wanted))

    def _download(self, component: Component):
        progress = {"version": component.version, "received": 0, "total": 0}
        self._installing[component.id] = progress

        def report(received: int, total: int):
            progress["received"], progress["total"] = received, total

        _, folder = self._offer(component.id)
        try:
            self.installer.install(component, self.platform, progress=report, folder=folder)
        except InstallError as error:
            self.state.record_event("install_failed", component.id, version=component.version, reason=str(error))
            raise ManagerError(str(error)) from error
        finally:
            self._installing.pop(component.id, None)

    def _row(self, component_id: str, *, language: str = "en", probe: bool = True) -> dict[str, Any]:
        data = self.state.read()
        record = data["components"].get(component_id)
        accepted = self._accepted_in(data, component_id)
        offered, included = self._offer(component_id)
        current = record.get("current") if record else None
        entry = None
        if record and current:
            entry = parse_component((record.get("entries") or {})[current])
        described = entry or offered
        row: dict[str, Any] = {
            "id": component_id,
            "name": described.name(language) if described else component_id,
            "description": (described.descriptions.get(language) or described.descriptions.get("en", "")) if described else "",
            "optional": described.optional if described else True,
            "version": current,
            "bundled": bool(record.get("bundled")) if record else False,
            "previous": record.get("previous") if record else None,
            "pending": record.get("pending") if record else None,
            # An update is a newer version for an installed component; before the first install there is none.
            "update": offered.version if offered and current and offered.version not in {current, record.get("pending")} else None,
            "available_here": self._offered_here(component_id),
            "included": included is not None,
            "data": [dict(item) for item in (described.data if described else ())],
            # `accepted`: the user accepted this term at this version before, so installing does not ask again.
            "terms": [{**dict(item), "accepted": _term_key(item) in accepted} for item in (offered.terms if offered else ())],
            "notices": [{key: str(item.get(key) or "") for key in ("name", "version", "license")}
                        for item in ((offered or described).raw.get("notices") or () if (offered or described) else ())
                        if isinstance(item, Mapping)],
            "last_error": logs.last_error(self.layout.log_path(component_id)) if current else "",
            "health": None,
            "reason": "",
        }
        if component_id in self._installing:
            row.update(state="installing", progress=dict(self._installing[component_id]))
            return row
        if not record or not current:
            row["state"] = "not_installed"
            return row
        runtime = self.supervisor.runtime(component_id)
        row["process"] = runtime
        row["resources"] = self.usage.sample(runtime.get("pid")) if runtime.get("running") else None
        row["data_job"] = self.data_jobs.view(component_id)
        # Programs change only on install or update; downloaded data grows while a download runs, so it is re-read sooner.
        downloading = (row["data_job"] or {}).get("state") == "running"
        row["data_bytes"] = monitor.folder_size(self.layout.data_dir(component_id), max_age=5.0 if downloading else 30.0)
        row["disk_bytes"] = monitor.folder_size(self.layout.versions_dir(component_id)) + row["data_bytes"]
        if runtime["crash_loop"]:
            row.update(state="unhealthy", reason="keeps crashing")
        elif runtime["restart_in"] is not None:
            row.update(state="restarting", reason=f"attempt {runtime['restart_attempt']}")
        elif not runtime["running"] and runtime.get("waiting"):
            row.update(state="waiting", reason="waiting for handover")
        elif not runtime["running"]:
            row["state"] = "stopped"
        elif entry is None or entry.run is None or entry.run.port is None or not probe:
            row["state"] = "running"
        else:
            report = self.probe(entry.run.port, entry.run.health_path)
            row["health"] = report.as_dict()
            row.update(self._health_state(report, runtime))
        return row

    def _health_state(self, report: health_checks.HealthReport, runtime: dict[str, Any]) -> dict[str, str]:
        in_grace = runtime.get("started_at") is not None and self.clock() - runtime["started_at"] < self.start_grace
        if report.reachable and report.up:
            for need in report.needs:
                if need in NEEDS_STATES:
                    return {"state": NEEDS_STATES[need], "reason": report.reason}
            if report.ready:
                return {"state": "running", "reason": ""}
        if in_grace:
            return {"state": "starting", "reason": report.reason}
        return {"state": "unhealthy", "reason": report.reason or "not ready"}
