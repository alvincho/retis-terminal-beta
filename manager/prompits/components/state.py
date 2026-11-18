"""The manager's own records on disk, and where components live.

Layout under the manager's root folder::

    state.json, state.lock     installed versions, running processes, accepted parts list
    events.jsonl               a few days of started / crashed / updated events
    downloads/                 partial downloads
    components/<id>/versions/<version>/   unpacked packages, kept side by side for rollback
    components/<id>/data/      the component's own data, kept across versions
    logs/<id>/<id>.log         the component's output, rotated

The records hold only what the manager needs to do its job. A component's user data stays wherever the
component keeps it.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

from prompits.core.file_lock import exclusive


def _empty() -> dict[str, Any]:
    return {"components": {}, "processes": {}, "parts_list": None}


class Layout:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    def component_dir(self, component_id: str) -> Path:
        return self.root / "components" / component_id

    def versions_dir(self, component_id: str) -> Path:
        return self.component_dir(component_id) / "versions"

    def version_dir(self, component_id: str, version: str) -> Path:
        return self.versions_dir(component_id) / version

    def data_dir(self, component_id: str) -> Path:
        return self.component_dir(component_id) / "data"

    def log_path(self, component_id: str) -> Path:
        return self.root / "logs" / component_id / f"{component_id}.log"


class State:
    """`state.json`, read and written under one cross-process lock; every write replaces the file atomically."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"
        self.lock_path = self.root / "state.lock"
        self.events_path = self.root / "events.jsonl"

    def read(self) -> dict[str, Any]:
        with exclusive(self.lock_path):
            return self._load()

    @contextlib.contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        with exclusive(self.lock_path):
            data = self._load()
            before = copy.deepcopy(data)
            yield data
            if data != before:
                self._save(data)

    def record_event(self, kind: str, component_id: str | None = None, **detail: Any) -> dict[str, Any]:
        event = {"at": time.time(), "kind": kind, "component": component_id, **detail}
        with exclusive(self.lock_path):
            with open(self.events_path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self, *, since: float = 0.0, component_id: str | None = None) -> list[dict[str, Any]]:
        with exclusive(self.lock_path):
            rows = self._read_events()
        return [row for row in rows if row.get("at", 0) >= since and (component_id is None or row.get("component") == component_id)]

    def prune_events(self, *, keep_days: float = 7.0, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - keep_days * 86400
        with exclusive(self.lock_path):
            rows = self._read_events()
            kept = [row for row in rows if row.get("at", 0) >= cutoff]
            if len(kept) != len(rows):
                temporary = self.events_path.with_suffix(".tmp")
                temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept), encoding="utf-8", newline="\n")
                os.replace(temporary, self.events_path)
        return len(rows) - len(kept)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            # A damaged file is kept for inspection instead of being silently replaced.
            self.path.replace(self.path.with_suffix(f".damaged-{int(time.time())}"))
            return _empty()
        merged = _empty()
        merged.update(data if isinstance(data, dict) else {})
        return merged

    def _save(self, data: dict[str, Any]):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        os.replace(temporary, self.path)
