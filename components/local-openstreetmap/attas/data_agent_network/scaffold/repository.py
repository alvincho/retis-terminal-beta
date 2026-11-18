"""JSON-backed endpoint specification snapshot repository."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Mapping, Tuple

from .models import EndpointParameterSpec, EndpointSpec, EndpointSpecSnapshot, VendorSpecSource


def _safe_vendor_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-.")
    if not normalized:
        raise ValueError("vendor_id is required")
    return normalized


def _snapshot_from_mapping(payload: Mapping[str, Any]) -> EndpointSpecSnapshot:
    sources = tuple(VendorSpecSource(**dict(item)) for item in payload.get("sources") or [])
    endpoints: list[EndpointSpec] = []
    for raw_endpoint in payload.get("endpoints") or []:
        endpoint = dict(raw_endpoint)
        endpoint["parameters"] = tuple(
            EndpointParameterSpec(**dict(parameter))
            for parameter in endpoint.get("parameters") or []
        )
        endpoints.append(EndpointSpec(**endpoint))
    return EndpointSpecSnapshot(
        vendor_id=str(payload.get("vendor_id") or ""),
        retrieved_at=str(payload.get("retrieved_at") or ""),
        content_hash=str(payload.get("content_hash") or ""),
        parser_version=str(payload.get("parser_version") or ""),
        sources=sources,
        endpoints=tuple(endpoints),
        warnings=tuple(str(item) for item in payload.get("warnings") or []),
    )


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return digest


class AbstractEndpointSpecRepository(ABC):
    """Persist and query immutable endpoint specification snapshots."""

    @abstractmethod
    def load(self, vendor_id: str) -> EndpointSpecSnapshot:
        """Load the active snapshot for a vendor."""

    @abstractmethod
    def save(self, snapshot: EndpointSpecSnapshot) -> None:
        """Persist a validated snapshot and make it active."""

    @abstractmethod
    def search(
        self,
        vendor_id: str,
        *,
        query: str = "",
        endpoint_id: str = "",
        limit: int = 20,
    ) -> Tuple[EndpointSpec, ...]:
        """Search the active snapshot deterministically."""

    def notes(self, vendor_id: str) -> Dict[str, Tuple[Mapping[str, Any], ...]]:
        """Hand-kept notes per endpoint for limits the vendor states only elsewhere; none by default."""
        return {}


@dataclass
class JsonEndpointSpecRepository(AbstractEndpointSpecRepository):
    """Store versioned endpoint snapshots and an active manifest as JSON."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    def _vendor_root(self, vendor_id: str) -> Path:
        return self.root / _safe_vendor_id(vendor_id)

    def notes(self, vendor_id: str) -> Dict[str, Tuple[Mapping[str, Any], ...]]:
        """`<vendor>/notes.json`: {"endpoints": {endpoint_id: [{"text", "source", "observed"}]}}.

        A note records, verbatim, a limit the vendor states somewhere other than its documentation, such
        as an error message, with where it was seen. Refreshing the snapshot never touches it.
        """
        path = self._vendor_root(_safe_vendor_id(vendor_id)) / "notes.json"
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        endpoints = payload.get("endpoints") if isinstance(payload, Mapping) else None
        if not isinstance(endpoints, Mapping):
            raise ValueError(f"Invalid endpoint notes for {vendor_id}")
        notes: Dict[str, Tuple[Mapping[str, Any], ...]] = {}
        for endpoint_id, items in endpoints.items():
            if not isinstance(items, list) or any(not isinstance(item, Mapping) or not str(item.get("text") or "").strip()
                                                  or not str(item.get("source") or "").strip() for item in items):
                raise ValueError(f"Invalid endpoint notes for {vendor_id}: {endpoint_id}")
            notes[str(endpoint_id)] = tuple(dict(item) for item in items)
        return notes

    def load(self, vendor_id: str) -> EndpointSpecSnapshot:
        vendor = _safe_vendor_id(vendor_id)
        vendor_root = self._vendor_root(vendor)
        manifest_path = vendor_root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"No endpoint specification snapshot for {vendor}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative_path = str(manifest.get("active_snapshot") or "")
        snapshot_path = (vendor_root / relative_path).resolve()
        if vendor_root not in snapshot_path.parents or not snapshot_path.is_file():
            raise ValueError(f"Invalid active endpoint snapshot for {vendor}")
        raw = snapshot_path.read_bytes()
        expected_hash = str(manifest.get("file_hash") or "")
        if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(f"Endpoint snapshot file hash mismatch for {vendor}")
        payload = json.loads(raw.decode("utf-8"))
        snapshot = _snapshot_from_mapping(payload)
        if _safe_vendor_id(snapshot.vendor_id) != vendor:
            raise ValueError(f"Endpoint snapshot vendor mismatch: {snapshot.vendor_id!r}")
        endpoint_ids = [endpoint.endpoint_id for endpoint in snapshot.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError(f"Endpoint snapshot contains duplicate identifiers for {vendor}")
        return snapshot

    def save(self, snapshot: EndpointSpecSnapshot) -> None:
        vendor = _safe_vendor_id(snapshot.vendor_id)
        if not snapshot.content_hash:
            raise ValueError("snapshot.content_hash is required")
        endpoint_ids = [endpoint.endpoint_id for endpoint in snapshot.endpoints]
        if not endpoint_ids:
            raise ValueError("snapshot must contain at least one endpoint")
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError(f"snapshot contains duplicate endpoint identifiers for {vendor}")
        vendor_root = self._vendor_root(vendor)
        timestamp = re.sub(r"[^0-9A-Za-z]+", "", snapshot.retrieved_at)[:20] or "snapshot"
        filename = f"{timestamp}-{snapshot.content_hash[:12]}.json"
        relative = Path("snapshots") / filename
        snapshot_path = vendor_root / relative
        payload = asdict(snapshot)
        file_hash = _atomic_json_write(snapshot_path, payload)
        manifest = {
            "vendor_id": vendor,
            "active_snapshot": relative.as_posix(),
            "file_hash": file_hash,
            "content_hash": snapshot.content_hash,
            "retrieved_at": snapshot.retrieved_at,
            "parser_version": snapshot.parser_version,
            "endpoint_count": len(snapshot.endpoints),
        }
        _atomic_json_write(vendor_root / "manifest.json", manifest)

    @staticmethod
    def _tokens(value: str) -> tuple[str, ...]:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        return tuple(dict.fromkeys(token for token in normalized.split() if len(token) > 1))

    def search(
        self,
        vendor_id: str,
        *,
        query: str = "",
        endpoint_id: str = "",
        limit: int = 20,
    ) -> Tuple[EndpointSpec, ...]:
        snapshot = self.load(vendor_id)
        try:
            bounded_limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            bounded_limit = 20
        exact = str(endpoint_id or "").strip().lower()
        if exact:
            matches = [endpoint for endpoint in snapshot.endpoints if endpoint.endpoint_id.lower() == exact]
            return tuple(matches[:bounded_limit])
        tokens = self._tokens(query)
        if not tokens:
            return tuple(snapshot.endpoints[:bounded_limit])
        ranked: list[tuple[int, int, int, EndpointSpec]] = []
        for index, endpoint in enumerate(snapshot.endpoints):
            identifier = endpoint.endpoint_id.lower()
            name = endpoint.name.lower()
            category = endpoint.category.lower()
            parameter_names = " ".join(parameter.name.lower() for parameter in endpoint.parameters)
            searchable = " ".join(
                (
                    identifier,
                    name,
                    endpoint.description.lower(),
                    category,
                    endpoint.operation.lower(),
                    parameter_names,
                    str(endpoint.extensions).lower(),
                )
            )
            score = 0
            matched_tokens = 0
            for token in tokens:
                matched = False
                if token in identifier:
                    score += 8
                    matched = True
                if token in name:
                    score += 6
                    matched = True
                if token in category:
                    score += 4
                    matched = True
                if token in parameter_names:
                    score += 3
                    matched = True
                if token in searchable:
                    score += 1
                    matched = True
                if matched:
                    matched_tokens += 1
            if score:
                ranked.append((matched_tokens, score, -index, endpoint))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return tuple(item[3] for item in ranked[:bounded_limit])
