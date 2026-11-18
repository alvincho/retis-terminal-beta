"""Reusable cascading quote helpers for Phemacast agents."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from fastapi import HTTPException


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_points(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def _quote_points(payload: Any) -> int:
    if not isinstance(payload, Mapping):
        return 0
    containers: List[Mapping[str, Any]] = [payload]
    for key in ("pricing", "billing", "cost_calculation", "cost", "price"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    for container in containers:
        for key in (
            "cost_points",
            "points",
            "plaza_points",
            "plaza_point_cost",
            "point_cost",
            "price_points",
        ):
            points = _coerce_points(container.get(key))
            if points:
                return points
            if key in container and container.get(key) in (0, "0"):
                return 0
    return 0


def _quote_ttl_seconds(value: Any, default: int = 900) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        ttl = default
    return max(ttl, 1)


def _utc_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except TypeError:
        return str(value)


class QuoteCascadeMixin:
    """Mixin that lets agents quote their own work plus downstream dependencies.

    Concrete agents can override `quote_dependency_definitions()` to describe the
    remote agent calls a request will make. Each dependency can point to another
    agent that implements `quote_request`; older Pulsers still work through the
    `quote_pulse_data` fallback.
    """

    QUOTE_REQUEST_TIMEOUT = 240

    def quoted_request_price(self, practice_id: str, content: Any) -> Optional[int]:
        """The points this agent's own quote names for a get_pulse_data request; 0 when it is free.

        A pulse priced in its quote leaves the practice free, so the provider asks this before running a call
        that came without a payment scope (prompits BaseAgent). An error here refuses an unpaid call.
        """
        quote = getattr(self, "quote_pulse_data", None)
        if practice_id != "get_pulse_data" or not callable(quote) or not isinstance(content, Mapping):
            return 0
        input_data = content.get("input_data")
        if input_data is None:
            input_data = content.get("params")
        if input_data is None:
            input_data = {key: value for key, value in content.items()
                          if key not in {"pulse_name", "pulse_address", "output_schema", "ttl_seconds", "quote_id"}}
        return _quote_points(quote(input_data=input_data or {}, pulse_name=content.get("pulse_name"),
                                   pulse_address=content.get("pulse_address")))

    def quote_request(
        self,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        request: Optional[Mapping[str, Any]] = None,
        ttl_seconds: Optional[Any] = None,
        quote_id: Optional[str] = None,
        practice_id: Optional[str] = None,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        include_self: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Return a normalized cascade quote for this agent request."""
        request_payload = dict(request or {})
        runtime_input = dict(input_data or params or request_payload.get("input_data") or request_payload.get("params") or {})
        ttl_value = _quote_ttl_seconds(
            ttl_seconds
            if ttl_seconds is not None
            else request_payload.get("ttl_seconds")
            if isinstance(request_payload, Mapping)
            else None
        )
        root_quote_id = _clean_text(quote_id or request_payload.get("quote_id") or f"quote-{uuid.uuid4()}")
        issued_at = datetime.now(timezone.utc)
        valid_until = datetime.fromtimestamp(issued_at.timestamp() + ttl_value, tz=timezone.utc)

        quotes: List[Dict[str, Any]] = []
        if include_self:
            own_quote = self.quote_own_request(
                input_data=runtime_input,
                params=runtime_input,
                request=request_payload,
                ttl_seconds=ttl_value,
                quote_id=f"{root_quote_id}:self",
                practice_id=practice_id or request_payload.get("practice_id"),
                pulse_name=pulse_name or request_payload.get("pulse_name"),
                pulse_address=pulse_address or request_payload.get("pulse_address"),
                **kwargs,
            )
            quotes.extend(self._normalize_quote_payload(own_quote, fallback={}, quote_id=f"{root_quote_id}:self"))

        dependencies = self.quote_dependency_definitions(
            input_data=runtime_input,
            params=runtime_input,
            request=request_payload,
            ttl_seconds=ttl_value,
            quote_id=root_quote_id,
            practice_id=practice_id or request_payload.get("practice_id"),
            pulse_name=pulse_name or request_payload.get("pulse_name"),
            pulse_address=pulse_address or request_payload.get("pulse_address"),
            **kwargs,
        )
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, Mapping):
                continue
            child_quote_id = f"{root_quote_id}:dep:{index + 1}"
            quotes.extend(self.quote_dependency_request(dict(dependency), ttl_seconds=ttl_value, quote_id=child_quote_id))

        points = sum(_quote_points(quote) for quote in quotes)
        return {
            "status": "quoted",
            "quote_id": root_quote_id,
            "ttl_seconds": ttl_value,
            "issued_at": _utc_iso(issued_at),
            "valid_until": _utc_iso(valid_until),
            "currency": "plaza_point",
            "unit": "request",
            "points": points,
            "cost_points": points,
            "agent_id": _clean_text(getattr(self, "agent_id", "") or getattr(self, "agent_card", {}).get("agent_id")),
            "agent_name": _clean_text(getattr(self, "name", "") or getattr(self, "agent_card", {}).get("name")),
            "quotes": quotes,
            "cascade_quotes": quotes,
        }

    def quote_own_request(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Return this agent's direct quote for the requested operation."""
        quote_pulse_data = getattr(self, "quote_pulse_data", None)
        if not callable(quote_pulse_data):
            return self._local_pulse_quote(**kwargs) or self._agent_cost_quote(**kwargs)
        pulse_name = kwargs.get("pulse_name")
        pulse_address = kwargs.get("pulse_address")
        practice_id = _clean_text(kwargs.get("practice_id") or "get_pulse_data")
        if practice_id and practice_id not in {"get_pulse_data", "quote_pulse_data", "quote_request"}:
            return self._agent_cost_quote(**kwargs)
        if not pulse_name and not pulse_address and not getattr(self, "supported_pulses", None):
            return self._agent_cost_quote(**kwargs)
        return quote_pulse_data(
            input_data=dict(kwargs.get("input_data") or kwargs.get("params") or {}),
            pulse_name=pulse_name,
            pulse_address=pulse_address,
            ttl_seconds=kwargs.get("ttl_seconds"),
            quote_id=kwargs.get("quote_id"),
        )

    def _local_pulse_quote(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        practice_id = _clean_text(kwargs.get("practice_id") or "get_pulse_data")
        if practice_id and practice_id not in {"get_pulse_data", "quote_pulse_data", "quote_request"}:
            return None
        pulse_definition = self._local_pulse_definition(
            pulse_name=kwargs.get("pulse_name"),
            pulse_address=kwargs.get("pulse_address"),
        )
        if not pulse_definition:
            return None
        ttl_value = _quote_ttl_seconds(kwargs.get("ttl_seconds"))
        issued_at = datetime.now(timezone.utc)
        valid_until = datetime.fromtimestamp(issued_at.timestamp() + ttl_value, tz=timezone.utc)
        card = getattr(self, "agent_card", {}) if isinstance(getattr(self, "agent_card", {}), Mapping) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), Mapping) else {}
        points = _quote_points(pulse_definition)
        pulse_name = _clean_text(kwargs.get("pulse_name") or pulse_definition.get("pulse_name") or pulse_definition.get("name") or "pulse")
        pulse_address = _clean_text(kwargs.get("pulse_address") or pulse_definition.get("pulse_address"))
        return {
            "status": "quoted",
            "quote_id": _clean_text(kwargs.get("quote_id") or f"quote-{uuid.uuid4()}"),
            "ttl_seconds": ttl_value,
            "issued_at": _utc_iso(issued_at),
            "valid_until": _utc_iso(valid_until),
            "currency": "plaza_point",
            "unit": "call",
            "points": points,
            "cost_points": points,
            "practice_id": "get_pulse_data",
            "pulse_name": pulse_name,
            "pulse_address": pulse_address,
            "agent_id": _clean_text(getattr(self, "agent_id", "") or card.get("agent_id")),
            "agent_name": _clean_text(getattr(self, "name", "") or card.get("name")),
            "provider_user_id": _clean_text(card.get("owner_user_id") or meta.get("owner_user_id")),
            "provider_username": _clean_text(card.get("owner_username") or meta.get("owner_username")),
            "pricing": {
                "points": points,
                "currency": "plaza_point",
                "unit": "call",
            },
            "cost_calculation": {
                **(dict(pulse_definition.get("cost_calculation") or {}) if isinstance(pulse_definition.get("cost_calculation"), Mapping) else {}),
                "points": points,
                "currency": "plaza_point",
                "unit": "call",
            },
            "metadata": {
                "own_agent_quote": True,
                "agent_role": _clean_text(card.get("role") or card.get("pit_type") or meta.get("role")),
            },
            "input_fingerprint": _fingerprint(kwargs.get("input_data") or kwargs.get("params") or {}),
        }

    def _agent_cost_quote(self, **kwargs: Any) -> Dict[str, Any]:
        """Return a direct quote row for non-pulse agent practices."""
        ttl_value = _quote_ttl_seconds(kwargs.get("ttl_seconds"))
        issued_at = datetime.now(timezone.utc)
        valid_until = datetime.fromtimestamp(issued_at.timestamp() + ttl_value, tz=timezone.utc)
        card = getattr(self, "agent_card", {}) if isinstance(getattr(self, "agent_card", {}), Mapping) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), Mapping) else {}
        practice_id = _clean_text(kwargs.get("practice_id") or "agent_request")
        practice_cost_source: Dict[str, Any] = {}
        practices = card.get("practices") if isinstance(card.get("practices"), list) else []
        for practice in practices:
            if not isinstance(practice, Mapping):
                continue
            if _clean_text(practice.get("id")) == practice_id:
                practice_cost_source = dict(practice)
                break
        points = _quote_points(practice_cost_source) if practice_cost_source else _quote_points(card)
        if not points:
            points = _quote_points(meta)
        agent_name = _clean_text(getattr(self, "name", "") or card.get("name") or "Agent")
        role = _clean_text(card.get("pit_type") or card.get("role") or meta.get("pit_type") or "Agent")
        label = _clean_text(kwargs.get("label") or practice_cost_source.get("name") or practice_id.replace("_", " ").title())
        return {
            "status": "quoted",
            "quote_id": _clean_text(kwargs.get("quote_id") or f"quote-{uuid.uuid4()}"),
            "ttl_seconds": ttl_value,
            "issued_at": _utc_iso(issued_at),
            "valid_until": _utc_iso(valid_until),
            "currency": "plaza_point",
            "unit": "request",
            "points": points,
            "cost_points": points,
            "practice_id": practice_id,
            "label": label,
            "service": practice_id,
            "agent_id": _clean_text(getattr(self, "agent_id", "") or card.get("agent_id")),
            "agent_name": agent_name,
            "provider_user_id": _clean_text(card.get("owner_user_id") or meta.get("owner_user_id")),
            "provider_username": _clean_text(card.get("owner_username") or meta.get("owner_username")),
            "pricing": {
                "points": points,
                "currency": "plaza_point",
                "unit": "request",
            },
            "cost_calculation": {
                "type": "fixed",
                "points": points,
                "currency": "plaza_point",
                "unit": "request",
            },
            "metadata": {
                "own_agent_quote": True,
                "agent_role": role,
            },
            "input_fingerprint": _fingerprint(kwargs.get("input_data") or kwargs.get("params") or {}),
        }

    def _local_pulse_definition(self, *, pulse_name: Any = None, pulse_address: Any = None) -> Dict[str, Any]:
        resolver = getattr(self, "resolve_pulse_definition", None)
        if callable(resolver):
            try:
                resolved = resolver(pulse_name=pulse_name, pulse_address=pulse_address)
                if isinstance(resolved, Mapping):
                    return dict(resolved)
            except Exception:
                pass
        supported = getattr(self, "supported_pulses", None)
        if not isinstance(supported, list):
            return {}
        requested_name = _clean_text(pulse_name).lower()
        requested_address = _clean_text(pulse_address).lower()
        for pulse in supported:
            if not isinstance(pulse, Mapping):
                continue
            names = {
                _clean_text(pulse.get(key)).lower()
                for key in ("name", "pulse_name", "pulse_id", "key")
                if _clean_text(pulse.get(key))
            }
            addresses = {
                _clean_text(pulse.get(key)).lower()
                for key in ("pulse_address", "address", "pulse_id", "key")
                if _clean_text(pulse.get(key))
            }
            if requested_name and requested_name in names:
                return dict(pulse)
            if requested_address and requested_address in addresses:
                return dict(pulse)
        return dict(supported[0]) if supported and isinstance(supported[0], Mapping) else {}

    def quote_dependency_definitions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Return configured downstream quote dependencies for this request."""
        del kwargs
        card = getattr(self, "agent_card", {}) if isinstance(getattr(self, "agent_card", {}), Mapping) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), Mapping) else {}
        raw_dependencies: List[Any] = []
        for container in (card, meta):
            if not isinstance(container, Mapping):
                continue
            for key in ("quote_dependencies", "billing_dependencies", "cost_dependencies"):
                value = container.get(key)
                if isinstance(value, list):
                    raw_dependencies.extend(value)
        return [dict(item) for item in raw_dependencies if isinstance(item, Mapping)]

    def quote_dependency_request(self, dependency: Dict[str, Any], *, ttl_seconds: int, quote_id: str) -> List[Dict[str, Any]]:
        """Ask one downstream agent for a quote, returning a fallback row on failure."""
        target = self._quote_dependency_target(dependency)
        fallback = self._fallback_dependency_quote(dependency, quote_id=quote_id, ttl_seconds=ttl_seconds)
        if not target:
            fallback["status"] = "missing-target"
            fallback.setdefault("metadata", {})["quote_error"] = "Dependency target could not be resolved."
            return [fallback]

        content = {
            "practice_id": _clean_text(dependency.get("practice_id") or "get_pulse_data"),
            "pulse_name": _clean_text(dependency.get("pulse_name")),
            "pulse_address": _clean_text(dependency.get("pulse_address")),
            "params": dict(dependency.get("params") or dependency.get("input") or {}),
            "input_data": dict(dependency.get("params") or dependency.get("input") or {}),
            "ttl_seconds": ttl_seconds,
            "quote_id": quote_id,
        }
        for key in ("phema_id", "phema_name", "format"):
            if dependency.get(key) not in (None, ""):
                content[key] = dependency.get(key)

        practice_attempts = [
            _clean_text(dependency.get("quote_practice_id") or "quote_request"),
            "quote_pulse_data",
        ]
        seen: set[str] = set()
        errors: List[str] = []
        for practice_id in practice_attempts:
            if not practice_id or practice_id in seen:
                continue
            seen.add(practice_id)
            try:
                if practice_id == "quote_pulse_data":
                    quote_content = {
                        "pulse_name": content["pulse_name"],
                        "pulse_address": content["pulse_address"],
                        "params": content["params"],
                        "ttl_seconds": ttl_seconds,
                        "quote_id": quote_id,
                    }
                else:
                    quote_content = content
                payload = self.UsePractice(
                    practice_id,
                    content=quote_content,
                    pit_address=target,
                    timeout=int(dependency.get("timeout") or self.QUOTE_REQUEST_TIMEOUT),
                )
                rows = self._normalize_quote_payload(payload, fallback=fallback, quote_id=quote_id)
                if rows:
                    return rows
            except HTTPException as exc:
                errors.append(str(exc.detail or exc))
            except Exception as exc:
                errors.append(str(exc))

        fallback["status"] = "fallback"
        if errors:
            fallback.setdefault("metadata", {})["quote_error"] = "; ".join(errors[-2:])
        return [fallback]

    def _quote_dependency_target(self, dependency: Mapping[str, Any]) -> Optional[Any]:
        target = dependency.get("target")
        if target:
            return target
        if dependency.get("pit_address"):
            return dependency.get("pit_address")
        address = _clean_text(dependency.get("address") or dependency.get("url") or dependency.get("target_url"))
        if address:
            return {
                "address": address,
                "agent_id": _clean_text(dependency.get("agent_id") or dependency.get("pit_id")),
                "pit_id": _clean_text(dependency.get("agent_id") or dependency.get("pit_id")),
                "card": {
                    "agent_id": _clean_text(dependency.get("agent_id") or dependency.get("pit_id")),
                    "name": _clean_text(dependency.get("agent_name") or dependency.get("name")),
                    "address": address,
                },
            }
        resolver = getattr(self, "_dependency_remote_target", None)
        if callable(resolver):
            return resolver(dict(dependency))
        return None

    def _normalize_quote_payload(
        self,
        payload: Any,
        *,
        fallback: Mapping[str, Any],
        quote_id: str,
    ) -> List[Dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return [
                self._normalize_quote_line(item, fallback=fallback, quote_id=f"{quote_id}:{index + 1}", index=index)
                for index, item in enumerate(payload)
                if isinstance(item, Mapping)
            ]
        if not isinstance(payload, Mapping):
            return []

        rows: List[Mapping[str, Any]] = []
        raw_quotes = payload.get("quotes")
        if not isinstance(raw_quotes, list):
            raw_quotes = payload.get("cascade_quotes")
        if isinstance(raw_quotes, list) and raw_quotes:
            rows.extend(item for item in raw_quotes if isinstance(item, Mapping))
        elif self._looks_like_quote_line(payload):
            rows.append(payload)

        normalized: List[Dict[str, Any]] = []
        for index, row in enumerate(rows):
            normalized.append(self._normalize_quote_line(row, fallback=fallback, quote_id=f"{quote_id}:{index + 1}", index=index))
        return normalized

    @staticmethod
    def _looks_like_quote_line(payload: Mapping[str, Any]) -> bool:
        return any(key in payload for key in ("points", "cost_points", "pulse_name", "pulse_address", "practice_id"))

    def _normalize_quote_line(
        self,
        payload: Mapping[str, Any],
        *,
        fallback: Mapping[str, Any],
        quote_id: str,
        index: int = 0,
    ) -> Dict[str, Any]:
        source = dict(fallback or {})
        source.update(dict(payload or {}))
        points = _quote_points(source)
        metadata = {}
        if isinstance(fallback.get("metadata"), Mapping):
            metadata.update(copy.deepcopy(dict(fallback.get("metadata") or {})))
        if isinstance(payload.get("metadata"), Mapping):
            metadata.update(copy.deepcopy(dict(payload.get("metadata") or {})))
        label = _clean_text(
            source.get("label")
            or source.get("service")
            or source.get("pulse_name")
            or source.get("agent_name")
            or f"Service {index + 1}"
        )
        return {
            **source,
            "quote_id": _clean_text(source.get("quote_id") or quote_id),
            "label": label,
            "service": _clean_text(source.get("service") or source.get("practice_id") or source.get("pulse_name") or "agent_request"),
            "points": points,
            "cost_points": points,
            "currency": _clean_text(source.get("currency") or "plaza_point"),
            "metadata": metadata,
        }

    def _fallback_dependency_quote(self, dependency: Mapping[str, Any], *, quote_id: str, ttl_seconds: int) -> Dict[str, Any]:
        card = dependency.get("card") if isinstance(dependency.get("card"), Mapping) else {}
        agent_name = _clean_text(
            dependency.get("agent_name")
            or dependency.get("name")
            or dependency.get("pulser_name")
            or card.get("name")
            or "Dependency"
        )
        agent_id = _clean_text(dependency.get("agent_id") or dependency.get("pit_id") or dependency.get("pulser_id") or card.get("agent_id"))
        pulse_name = _clean_text(dependency.get("pulse_name"))
        points = _coerce_points(dependency.get("fallback_points") or dependency.get("cost_points") or dependency.get("points"))
        meta = copy.deepcopy(dict(dependency.get("metadata") or {})) if isinstance(dependency.get("metadata"), Mapping) else {}
        if dependency.get("pulse_address"):
            meta.setdefault("pulse_address", dependency.get("pulse_address"))
        if dependency.get("practice_id"):
            meta.setdefault("practice_id", dependency.get("practice_id"))
        return {
            "status": "quoted",
            "quote_id": quote_id,
            "ttl_seconds": ttl_seconds,
            "label": pulse_name or agent_name,
            "service": _clean_text(dependency.get("practice_id") or pulse_name or "dependency"),
            "practice_id": _clean_text(dependency.get("practice_id") or "get_pulse_data"),
            "pulse_name": pulse_name,
            "pulse_address": _clean_text(dependency.get("pulse_address")),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "provider_user_id": _clean_text(dependency.get("provider_user_id") or dependency.get("owner_user_id") or card.get("owner_user_id")),
            "provider_username": _clean_text(dependency.get("provider_username") or dependency.get("owner_username") or card.get("owner_username")),
            "points": points,
            "cost_points": points,
            "currency": "plaza_point",
            "unit": "call",
            "metadata": meta,
            "input_fingerprint": _fingerprint(dependency.get("params") or dependency.get("input") or {}),
        }
