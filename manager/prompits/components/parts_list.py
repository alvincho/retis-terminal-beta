"""The signed parts list: which components a publisher offers, for which platforms, and how to run them.

A parts list travels as an envelope::

    {"payload": {...}, "signatures": [{"key_id": "...", "algorithm": "ed25519", "signature": "<base64>"}]}

The signature covers the canonical JSON of `payload` (sorted keys, no spaces, UTF-8). It is checked on top
of HTTPS, so a mirror or a tampered download cannot change what gets installed. `payload.sequence` only
grows: a manager refuses a list older than the newest one it has accepted, so an old list (with old,
vulnerable versions) cannot be replayed.

Payload, schema `prompits.parts-list/1`::

    {"schema": "prompits.parts-list/1", "publisher": "...", "sequence": 7, "issued_at": "2026-11-15T00:00:00Z",
     "components": [{
        "id": "example-source", "version": "1.2.0", "optional": true,
        "names": {"en": "Example source"}, "descriptions": {"en": "..."},
        "artifacts": {"macos-arm64": {"url": "https://...", "sha256": "<hex>", "size": 123, "format": "zip"}},
        "run": {"command": ["{python}", "-m", "example"], "env": {"PYTHONPATH": "{component_dir}/lib"},
                "port": 28500, "health_path": "/health"},
        "data": [{"id": "full", "names": {"en": "Full dataset"}, "size": 1000000}],
        "terms": [{"id": "licence", "version": "1", "url": "https://...", "names": {"en": "Licence"}}]}]}

`run.command` and `run.env` values may use the placeholders `{python}`, `{component_dir}`, `{data_dir}` and
`{port}`, and nothing else. `data` describes data the component downloads itself after install; it is never
part of the package.
"""
from __future__ import annotations

import base64
import json
import re
import string
from dataclasses import dataclass, field
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SCHEMA = "prompits.parts-list/1"
ARCHIVE_FORMATS = ("zip", "tar.gz")
PLACEHOLDERS = frozenset({"python", "component_dir", "data_dir", "port"})

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PartsListError(ValueError):
    """The parts list is malformed, unsigned, signed by an unknown key, or older than one already accepted."""


@dataclass(frozen=True)
class Artifact:
    url: str
    sha256: str
    size: int
    format: str


@dataclass(frozen=True)
class RunSpec:
    command: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    port: int | None = None
    health_path: str = "/health"


@dataclass(frozen=True)
class Component:
    id: str
    version: str
    names: Mapping[str, str]
    descriptions: Mapping[str, str]
    artifacts: Mapping[str, Artifact]
    run: RunSpec | None
    optional: bool = True
    data: tuple[Mapping[str, Any], ...] = ()
    terms: tuple[Mapping[str, Any], ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def artifact_for(self, platform: str) -> Artifact | None:
        return self.artifacts.get(platform)

    def name(self, language: str = "en") -> str:
        return self.names.get(language) or self.names.get("en") or self.id


@dataclass(frozen=True)
class PartsList:
    publisher: str
    sequence: int
    issued_at: str
    components: tuple[Component, ...]

    def component(self, component_id: str) -> Component | None:
        return next((item for item in self.components if item.id == component_id), None)


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(payload: Mapping[str, Any], private_key: Ed25519PrivateKey, key_id: str) -> dict[str, Any]:
    """Wrap `payload` in a signed envelope (release tooling)."""
    parse_payload(payload)
    signature = private_key.sign(canonical_bytes(payload))
    return {
        "payload": payload,
        "signatures": [{"key_id": key_id, "algorithm": "ed25519", "signature": base64.b64encode(signature).decode("ascii")}],
    }


def verify(envelope: Mapping[str, Any], trusted_keys: Mapping[str, bytes | Ed25519PublicKey],
           *, min_sequence: int = 0) -> PartsList:
    """Check the envelope's signature against `trusted_keys` (key id -> raw 32-byte Ed25519 public key) and parse it.

    Raises PartsListError unless one signature by a trusted key verifies and `sequence >= min_sequence`.
    """
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("payload"), Mapping):
        raise PartsListError("parts list has no payload")
    payload = envelope["payload"]
    message = canonical_bytes(payload)
    verified = False
    for entry in envelope.get("signatures") or ():
        if not isinstance(entry, Mapping) or entry.get("algorithm") != "ed25519":
            continue
        key = trusted_keys.get(str(entry.get("key_id") or ""))
        if key is None:
            continue
        public_key = key if isinstance(key, Ed25519PublicKey) else Ed25519PublicKey.from_public_bytes(bytes(key))
        try:
            public_key.verify(base64.b64decode(str(entry.get("signature") or ""), validate=True), message)
        except (InvalidSignature, ValueError):
            continue
        verified = True
        break
    if not verified:
        raise PartsListError("parts list is not signed by a trusted key")
    parts = parse_payload(payload)
    if parts.sequence < min_sequence:
        raise PartsListError(f"parts list sequence {parts.sequence} is older than accepted {min_sequence}")
    return parts


def parse_payload(payload: Mapping[str, Any]) -> PartsList:
    if payload.get("schema") != SCHEMA:
        raise PartsListError(f"unsupported parts list schema {payload.get('schema')!r}")
    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise PartsListError("parts list sequence must be a non-negative integer")
    components = []
    seen = set()
    for raw in payload.get("components") or ():
        component = parse_component(raw)
        if component.id in seen:
            raise PartsListError(f"component {component.id} is listed twice")
        seen.add(component.id)
        components.append(component)
    return PartsList(
        publisher=str(payload.get("publisher") or ""),
        sequence=sequence,
        issued_at=str(payload.get("issued_at") or ""),
        components=tuple(components),
    )


def parse_component(raw: Any) -> Component:
    if not isinstance(raw, Mapping):
        raise PartsListError("component entry must be an object")
    component_id = str(raw.get("id") or "")
    if not _ID.match(component_id):
        raise PartsListError(f"invalid component id {component_id!r}")
    version = str(raw.get("version") or "")
    if not _VERSION.match(version) or version in {".", ".."}:
        raise PartsListError(f"{component_id}: invalid version {version!r}")
    artifacts = {}
    for platform, item in (raw.get("artifacts") or {}).items():
        artifacts[str(platform)] = _parse_artifact(component_id, platform, item)
    run = _parse_run(component_id, raw.get("run")) if raw.get("run") is not None else None
    return Component(
        id=component_id,
        version=version,
        names=_text_map(raw.get("names")),
        descriptions=_text_map(raw.get("descriptions")),
        artifacts=artifacts,
        run=run,
        optional=bool(raw.get("optional", True)),
        data=tuple(_parse_data(component_id, item) for item in raw.get("data") or ()),
        terms=tuple(dict(item) for item in raw.get("terms") or () if isinstance(item, Mapping)),
        raw=dict(raw),
    )


def _parse_artifact(component_id: str, platform: str, item: Any) -> Artifact:
    if not isinstance(item, Mapping):
        raise PartsListError(f"{component_id}/{platform}: artifact must be an object")
    url = str(item.get("url") or "")
    if not url.startswith("https://"):
        raise PartsListError(f"{component_id}/{platform}: artifact url must use https")
    sha256 = str(item.get("sha256") or "").lower()
    if not _SHA256.match(sha256):
        raise PartsListError(f"{component_id}/{platform}: sha256 must be 64 hex characters")
    size = item.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise PartsListError(f"{component_id}/{platform}: size must be a positive integer")
    archive_format = str(item.get("format") or "zip")
    if archive_format not in ARCHIVE_FORMATS:
        raise PartsListError(f"{component_id}/{platform}: unsupported format {archive_format!r}")
    return Artifact(url=url, sha256=sha256, size=size, format=archive_format)


def _parse_data(component_id: str, item: Any) -> dict[str, Any]:
    """Data a component downloads after install; `command` (optional) is how the manager starts that download."""
    if not isinstance(item, Mapping) or not _ID.match(str(item.get("id") or "")):
        raise PartsListError(f"{component_id}: each data entry needs a short lowercase id")
    command = item.get("command")
    if command is not None:
        if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
            raise PartsListError(f"{component_id}/{item['id']}: data command must be a non-empty list of strings")
        for part in command:
            _check_placeholders(component_id, part)
    return dict(item)


def _parse_run(component_id: str, raw: Any) -> RunSpec:
    if not isinstance(raw, Mapping):
        raise PartsListError(f"{component_id}: run must be an object")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        raise PartsListError(f"{component_id}: run.command must be a non-empty list of strings")
    env = raw.get("env") or {}
    if not isinstance(env, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise PartsListError(f"{component_id}: run.env must map strings to strings")
    for text in [*command, *env.values()]:
        _check_placeholders(component_id, text)
    port = raw.get("port")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
        raise PartsListError(f"{component_id}: run.port must be 1-65535")
    health_path = str(raw.get("health_path") or "/health")
    if not health_path.startswith("/"):
        raise PartsListError(f"{component_id}: run.health_path must start with /")
    return RunSpec(command=tuple(command), env=dict(env), port=port, health_path=health_path)


def _check_placeholders(component_id: str, text: str):
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError as error:
        raise PartsListError(f"{component_id}: bad placeholder in {text!r}") from error
    unknown = names - PLACEHOLDERS
    if unknown:
        raise PartsListError(f"{component_id}: unknown placeholder(s) {sorted(unknown)} in {text!r}")


def _text_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(text) for key, text in value.items() if isinstance(text, str)}
