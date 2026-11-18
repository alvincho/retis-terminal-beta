"""
Pulser logic for `phemacast.agents.pulser`.

Phemacast assembles pulse inputs, phemas, and castrs into rendered research artifacts
and interactive tooling. Within Phemacast, the agents package contains the actor roles
that build, bind, and render phemas.

Key definitions include `Pulser` and `validate_pulser_config_test_parameters`, which
provide the main entry points used by neighboring modules and tests.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from fastapi.staticfiles import StaticFiles
from prompits.agents.standby import StandbyAgent
from prompits.core.message import Message
from prompits.core.pit import PitAddress

from phemacast.agents.quote_cascade import QuoteCascadeMixin
from phemacast.core.pulse_runtime import normalize_runtime_pulse_entry
from phemacast.core.pulse_observability import observed_pulse, pulse_stage, active_pulse_work
from phemacast.practices.pulser import GetPulseDataPractice, QuotePulseDataPractice
from phemacast.practices.quote import QuoteRequestPractice


ConfigInput = Union[str, Path, Mapping[str, Any]]
DEFAULT_PULSE_COST_POINTS = 1


def _read_config(config: ConfigInput) -> Dict[str, Any]:
    """Internal helper to read the config."""
    if isinstance(config, Mapping):
        return dict(config)

    config_path = Path(config)
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_path(data: Any, path: str) -> Any:
    """Internal helper to resolve the path."""
    current = data
    remaining = str(path or "").strip()
    if not remaining:
        return current

    while remaining:
        if isinstance(current, Mapping):
            if remaining in current:
                return current[remaining]

            matched = False
            for key in sorted(current.keys(), key=lambda value: len(str(value)), reverse=True):
                key_text = str(key)
                prefix = f"{key_text}."
                if remaining.startswith(prefix):
                    current = current[key]
                    remaining = remaining[len(prefix):]
                    matched = True
                    break
            if matched:
                continue
            return None

        if isinstance(current, list):
            part, separator, rest = remaining.partition(".")
            if not part.isdigit():
                return None
            index = int(part)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            if not separator:
                return current
            remaining = rest
            continue

        return None

    return current


def _assign_path(data: Dict[str, Any], path: str, value: Any) -> None:
    """Internal helper to return the assign path."""
    current = data
    parts = path.split(".")
    for part in parts[:-1]:
        node = current.get(part)
        if not isinstance(node, dict):
            node = {}
            current[part] = node
        current = node
    current[parts[-1]] = value


def _coerce_plaza_point_cost(value: Any) -> Optional[int]:
    """Normalize an advertised Plaza point cost."""
    if value in (None, ""):
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _point_cost_from_payload(payload: Any) -> int:
    """Extract Plaza point pricing from pulse metadata."""
    if not isinstance(payload, Mapping):
        return DEFAULT_PULSE_COST_POINTS
    containers: List[Mapping[str, Any]] = [payload]
    for key in ("pricing", "billing", "cost_calculation", "cost", "price"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    definition = payload.get("pulse_definition")
    if isinstance(definition, Mapping):
        containers.append(definition)
        for key in ("pricing", "billing", "cost_calculation", "cost", "price"):
            value = definition.get(key)
            if isinstance(value, Mapping):
                containers.append(value)
    for container in containers:
        for key in (
            "cost_points",
            "plaza_point_cost",
            "plaza_points",
            "point_cost",
            "price_points",
            "points",
        ):
            cost = _coerce_plaza_point_cost(container.get(key))
            if cost is not None:
                return cost
    return DEFAULT_PULSE_COST_POINTS


def _transform_item_mapping(item: Any, field_rules: Mapping[str, Any]) -> Dict[str, Any]:
    """Internal helper to transform the item mapping."""
    if not isinstance(item, Mapping):
        return {}

    transformed: Dict[str, Any] = {}
    for output_field, rule in field_rules.items():
        if isinstance(rule, str):
            value = _resolve_path(item, rule)
            if value is not None:
                _assign_path(transformed, output_field, value)
            continue

        if isinstance(rule, Mapping):
            if "value" in rule:
                _assign_path(transformed, output_field, rule["value"])
                continue

            source = rule.get("source") or rule.get("from") or rule.get("path") or rule.get("input")
            if source:
                value = _resolve_path(item, str(source))
                if value is not None:
                    _assign_path(transformed, output_field, value)
                    continue

            if "default" in rule:
                _assign_path(transformed, output_field, rule["default"])

    return transformed


def _coerce_positive_limit(value: Any) -> Optional[int]:
    """Internal helper to coerce the positive limit."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if limit >= 0 else None


def _coerce_number(value: Any) -> Optional[float]:
    """Internal helper to coerce the number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _config_supported_pulses(config: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Internal helper to return the config supported pulses."""
    pulser_config = config.get("pulser") if isinstance(config.get("pulser"), Mapping) else config
    raw_pulses = pulser_config.get("supported_pulses") or config.get("supported_pulses") or []
    return [pulse for pulse in raw_pulses if isinstance(pulse, Mapping)]


def _pulse_has_explicit_test_parameters(pulse: Mapping[str, Any]) -> bool:
    """Return whether the pulse has explicit test parameters."""
    for key in ("test_data", "test_payload", "sample_input"):
        value = pulse.get(key)
        if isinstance(value, Mapping) and value:
            return True
    if str(pulse.get("test_data_path") or "").strip():
        return True
    return False


def validate_pulser_config_test_parameters(config: Mapping[str, Any]) -> None:
    """Validate the pulser config test parameters."""
    supported_pulses = _config_supported_pulses(config)
    if any(_pulse_has_explicit_test_parameters(pulse) for pulse in supported_pulses):
        return
    raise ValueError(
        "Pulser config must provide at least one set of test parameters in supported_pulses "
        "via test_data or test_data_path."
    )


class Pulser(QuoteCascadeMixin, StandbyAgent):
    """
    Standby agent specialized for pulse payload delivery.

    A pulser advertises its supported pulses in `agent_card.meta.supported_pulses`,
    exposes `get_pulse_data` as a mounted practice, and registers with Plaza as
    soon as it is initialized when `plaza_url` is provided.

    Each subclass family names its Plaza type in ``PIT_TYPE``; the card lists the chain
    (see ``pit_type_chain``).
    """

    PIT_TYPE = "Pulser"

    def __init__(
        self,
        config: Optional[ConfigInput] = None,
        *,
        config_path: Optional[ConfigInput] = None,
        name: str = "Pulser",
        host: str = "127.0.0.1",
        port: int = 8000,
        plaza_url: Optional[str] = None,
        agent_card: Optional[Dict[str, Any]] = None,
        pool: Any = None,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        supported_pulses: Optional[List[Dict[str, Any]]] = None,
        auto_register: bool = True,
    ):
        """Initialize the pulser."""
        config_data = _read_config(config) if config is not None else {}
        resolved_config_path = config_path
        if resolved_config_path is None and isinstance(config, (str, Path)):
            resolved_config_path = config

        self.config_path = Path(resolved_config_path).resolve() if resolved_config_path else None
        self.raw_config = dict(config_data)
        self._shared_pulse_cards_by_id: Dict[str, Dict[str, Any]] = {}
        self._shared_pulse_cards_by_name: Dict[str, Dict[str, Any]] = {}
        self._runtime_lock = threading.RLock()
        self._runtime_working: Dict[str, Dict[str, Any]] = {}
        self._runtime_status_message = ""
        pulser_config = config_data.get("pulser", config_data)

        pulse_definitions = self._build_supported_pulses(
            config=pulser_config,
            pulse_name=pulse_name,
            pulse_address=pulse_address,
            input_schema=input_schema,
            mapping=mapping,
            output_schema=output_schema,
            supported_pulses=supported_pulses,
        )
        if not pulse_definitions:
            raise ValueError("Pulser requires at least one configured pulse.")

        card = dict(agent_card or pulser_config.get("agent_card") or {})
        card.setdefault("name", name)
        card.setdefault("role", "pulser")
        card.setdefault("pit_type", "Pulser")

        super().__init__(
            name=name,
            host=host,
            port=port,
            plaza_url=plaza_url,
            agent_card=card,
            pool=pool,
        )

        self.apply_pulser_config(
            config_data or pulser_config,
            supported_pulses=supported_pulses,
            pulse_name=pulse_name,
            pulse_address=pulse_address,
            input_schema=input_schema,
            mapping=mapping,
            output_schema=output_schema,
            agent_card_overrides=card,
        )

        current_dir = os.path.dirname(os.path.abspath(__file__))
        shared_static_dir = os.path.abspath(os.path.join(current_dir, "..", "..", "prompits", "agents", "static"))
        try:
            self.app.mount("/static", StaticFiles(directory=shared_static_dir), name="static")
        except Exception:
            # Pulser subclasses may already expose this mount.
            pass

        self.add_practice(GetPulseDataPractice())
        self.add_practice(QuotePulseDataPractice())
        self.add_practice(QuoteRequestPractice())

        if self.plaza_url and auto_register:
            self.register()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        method = cls.__dict__.get("get_pulse_data")
        if method and not getattr(method, "_pulse_observed", False):
            cls.get_pulse_data = observed_pulse(method)

    @property
    def status(self) -> str:
        """Return a compact live status for pulser runtime work."""
        runtime_lock = getattr(self, "_runtime_lock", None)
        if runtime_lock is None:
            return "Initializing pulser runtime."
        with runtime_lock:
            active_items = sorted(
                getattr(self, "_runtime_working", {}).values(),
                key=lambda item: float(item.get("_started_perf") or 0.0),
            )
            if active_items:
                latest = active_items[-1]
                return str(latest.get("detail") or latest.get("label") or "Serving pulse data.")
            if self._runtime_status_message:
                return self._runtime_status_message
        pulse_count = len(getattr(self, "supported_pulses", []) or [])
        return f"Idle; ready to serve {pulse_count} pulse{'s' if pulse_count != 1 else ''}."

    @property
    def working_list(self) -> List[Dict[str, Any]]:
        """Return live pulse requests currently handled by this pulser."""
        runtime_lock = getattr(self, "_runtime_lock", None)
        if runtime_lock is None:
            return []
        with runtime_lock:
            active_items = sorted(
                getattr(self, "_runtime_working", {}).values(),
                key=lambda item: float(item.get("_started_perf") or 0.0),
            )
            return [
                {
                    "id": str(item.get("id") or ""),
                    "label": str(item.get("label") or ""),
                    "status": str(item.get("status") or "running"),
                    "kind": str(item.get("kind") or "pulse"),
                    "detail": str(item.get("detail") or ""),
                    "started_at": str(item.get("started_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                }
                for item in active_items
            ]

    @staticmethod
    def _runtime_now() -> str:
        """Return the current UTC timestamp for pulser runtime status."""
        return datetime.now(timezone.utc).isoformat()

    @contextmanager
    def _track_pulser_work(self, label: str, detail: str):
        """Track one active pulse request for the public runtime status API."""
        observed_work = active_pulse_work(self)
        if observed_work:
            self._update_runtime_work(observed_work, detail=detail)
            yield observed_work
            return
        work_id = f"pulser-{uuid.uuid4()}"
        started_at = self._runtime_now()
        item = {
            "id": work_id,
            "label": str(label or "Pulse request"),
            "status": "running",
            "detail": str(detail or label or "Serving pulse data."),
            "started_at": started_at,
            "updated_at": started_at,
            "_started_perf": time.monotonic(),
        }
        with self._runtime_lock:
            self._runtime_working[work_id] = item
            self._runtime_status_message = item["detail"]
        completed = False
        try:
            yield work_id
            completed = True
        except Exception as exc:
            with self._runtime_lock:
                item["status"] = "error"
                item["detail"] = f"Error during {item['label']}: {exc}"
                item["updated_at"] = self._runtime_now()
                self._runtime_status_message = item["detail"]
            raise
        finally:
            with self._runtime_lock:
                self._runtime_working.pop(work_id, None)
                ended_at = self._runtime_now()
                history_item = {
                    "id": work_id,
                    "label": str(item.get("label") or "Pulse request"),
                    "status": "success" if completed else "error",
                    "detail": str(item.get("detail") or ""),
                    "kind": "pulse",
                    "started_at": str(item.get("started_at") or ""),
                    "ended_at": ended_at,
                    "updated_at": ended_at,
                    "duration_ms": max(0, int((time.monotonic() - float(item.get("_started_perf") or time.monotonic())) * 1000)),
                    "error": "" if completed else str(item.get("detail") or "Pulse request failed."),
                }
                if not self._runtime_working:
                    self._runtime_status_message = (
                        f"Idle; last completed {item['label']}."
                        if completed
                        else f"Idle; last failed {item['label']}."
                    )
            self._append_runtime_work_history(history_item)

    @classmethod
    def from_config(cls, config: ConfigInput, **kwargs: Any) -> "Pulser":
        """Build an instance from config."""
        return cls(config=config, **kwargs)

    @staticmethod
    def _merge_tags(*tag_groups: Any) -> List[str]:
        """Internal helper to merge the tags."""
        merged: List[str] = []
        for group in tag_groups:
            if not group:
                continue
            for tag in group:
                value = str(tag)
                if value not in merged:
                    merged.append(value)
        return merged

    @staticmethod
    def _sample_value_from_schema_field(field_definition: Any) -> Any:
        """Internal helper for sample value from schema field."""
        if not isinstance(field_definition, Mapping):
            return ""
        if "default" in field_definition:
            return field_definition.get("default")
        examples = field_definition.get("examples")
        if isinstance(examples, list) and examples:
            return examples[0]
        enum_values = field_definition.get("enum")
        if isinstance(enum_values, list) and enum_values:
            return enum_values[0]
        field_type = str(field_definition.get("type") or "").strip().lower()
        if field_type in {"number", "integer"}:
            return 0
        if field_type == "boolean":
            return False
        return ""

    @classmethod
    def _sample_payload_from_schema(cls, schema: Any) -> Optional[Dict[str, Any]]:
        """Internal helper to return the sample payload from schema."""
        if not isinstance(schema, Mapping):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return None
        payload: Dict[str, Any] = {}
        for field_name, field_definition in properties.items():
            payload[str(field_name)] = cls._sample_value_from_schema_field(field_definition)
        return payload if payload else None

    def _resolve_sample_parameters(self, pulse_definition: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Internal helper to resolve the sample parameters."""
        candidates: List[Mapping[str, Any]] = []
        if isinstance(pulse_definition, Mapping):
            candidates.append(pulse_definition)
            nested_definition = pulse_definition.get("pulse_definition")
            if isinstance(nested_definition, Mapping):
                candidates.append(nested_definition)

        for candidate in candidates:
            for key in ("test_data", "test_payload", "sample_input", "resolved_test_data"):
                value = candidate.get(key)
                if isinstance(value, Mapping) and value:
                    return dict(value)

        input_schema = pulse_definition.get("input_schema")
        if not isinstance(input_schema, Mapping):
            nested_definition = pulse_definition.get("pulse_definition")
            interface = nested_definition.get("interface") if isinstance(nested_definition, Mapping) else {}
            input_schema = interface.get("request_schema") if isinstance(interface, Mapping) else {}
        return self._sample_payload_from_schema(input_schema)

    def _attach_sample_parameters(self, pulse_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper for attach sample parameters."""
        normalized = dict(pulse_definition)
        sample_parameters = self._resolve_sample_parameters(normalized)
        if isinstance(sample_parameters, Mapping) and sample_parameters:
            normalized["test_data"] = dict(sample_parameters)

        pulse_definition_payload = dict(normalized.get("pulse_definition") or {})
        if isinstance(normalized.get("test_data"), Mapping) and normalized.get("test_data"):
            pulse_definition_payload["test_data"] = dict(normalized["test_data"])
        test_data_path = normalized.get("test_data_path") or pulse_definition_payload.get("test_data_path")
        if str(test_data_path or "").strip():
            pulse_definition_payload["test_data_path"] = str(test_data_path)
        normalized["pulse_definition"] = pulse_definition_payload
        return normalized

    def _build_supported_pulses(
        self,
        *,
        config: Dict[str, Any],
        pulse_name: Optional[str],
        pulse_address: Optional[str],
        input_schema: Optional[Dict[str, Any]],
        mapping: Optional[Dict[str, Any]],
        output_schema: Optional[Dict[str, Any]],
        supported_pulses: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Internal helper to build the supported pulses."""
        raw_pulses = supported_pulses or config.get("supported_pulses")
        if raw_pulses:
            return [self._normalize_pulse_definition(pulse) for pulse in raw_pulses if isinstance(pulse, Mapping)]

        return [
            self._normalize_pulse_definition(
                {
                    "name": pulse_name or config.get("name") or "default_pulse",
                    "pulse_address": pulse_address or config.get("pulse_address"),
                    "input_schema": input_schema if input_schema is not None else config.get("input_schema", {}),
                    "mapping": mapping if mapping is not None else config.get("mapping", {}),
                    "output_schema": output_schema if output_schema is not None else config.get("output_schema", {}),
                    "description": config.get("description", ""),
                    "tags": config.get("tags", []),
                    "cost": config.get("cost", config.get("cost_points", DEFAULT_PULSE_COST_POINTS)),
                }
            )
        ]

    def build_register_payload(
        self,
        plaza_url: str,
        card: Optional[Dict[str, Any]] = None,
        address: Optional[str] = None,
        expires_in: int = 3600,
        pit_type: Optional[str] = None,
        pit_id: Optional[str] = None,
        api_key: Optional[str] = None,
        accepts_inbound_from_plaza: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Build the register payload."""
        payload = super().build_register_payload(
            plaza_url=plaza_url,
            card=card,
            address=address,
            expires_in=expires_in,
            pit_type=pit_type or "Pulser",
            pit_id=pit_id,
            api_key=api_key,
            accepts_inbound_from_plaza=accepts_inbound_from_plaza,
        )
        supported_pulses = self.supported_pulses if isinstance(getattr(self, "supported_pulses", None), list) else []
        if supported_pulses:
            pairs = []
            for pulse in supported_pulses:
                if not isinstance(pulse, dict) or not (pulse.get("pulse_id") or pulse.get("pulse_name") or pulse.get("name")):
                    continue
                pair = {
                    "pulse_id": pulse.get("pulse_id"),
                    "pulse_name": pulse.get("pulse_name") or pulse.get("name"),
                    "pulse_address": pulse.get("pulse_address"),
                    "pulse_definition": pulse.get("pulse_definition"),
                    "input_schema": pulse.get("input_schema"),
                }
                for key in ("cost", "cost_points", "pricing", "cost_calculation"):
                    if key in pulse:
                        pair[key] = copy.deepcopy(pulse.get(key))
                if isinstance(pulse.get("test_data"), Mapping) and pulse.get("test_data"):
                    pair["test_data"] = dict(pulse["test_data"])
                if str(pulse.get("test_data_path") or "").strip():
                    pair["test_data_path"] = str(pulse.get("test_data_path"))
                for key in ("is_complete", "completion_status", "completion_errors"):
                    if key in pulse:
                        pair[key] = pulse.get(key)
                pair["status"] = pulse.get("completion_status") or pulse.get("status")
                pairs.append(pair)
            payload["pulse_pulser_pairs"] = pairs
        card = payload.get("card")
        if isinstance(card, dict):
            chain = self.pit_type_chain(payload.get("pit_type"))
            if len(chain) > 1:
                card["pit_types"] = chain
            else:
                card.pop("pit_types", None)
        return payload

    @classmethod
    def pit_type_chain(cls, registered: Optional[str] = None) -> List[str]:
        """The registered type, then each type this class derives from, most specific first.

        Read from the ``PIT_TYPE`` each class on the chain declares, e.g. ``["Consultant", "Persona",
        "Pulser"]``. Plaza lets a search for a parent match. "Agent", the root, is never listed.
        """
        chain: List[str] = []
        for value in (registered, *(klass.__dict__.get("PIT_TYPE") for klass in cls.__mro__)):
            name = str(value or "").strip() if isinstance(value, str) or value is None else ""
            if name and name != "Agent" and name not in chain:
                chain.append(name)
        return chain

    def _normalize_pulse_definition(self, pulse: Mapping[str, Any]) -> Dict[str, Any]:
        """Internal helper to normalize the pulse definition."""
        normalized = dict(pulse)
        normalized.setdefault("name", "default_pulse")
        default_pulse_address = self.config.get("pulse_address") if hasattr(self, "config") else None
        if not default_pulse_address:
            default_pulse_address = normalized.get("pit_address") or normalized.get("pulse_pit_address") or normalized.get("shared_pulse_address")
        if default_pulse_address:
            normalized.setdefault("pulse_address", self._compact_pit_ref(default_pulse_address))
        normalized = normalize_runtime_pulse_entry(
            normalized,
            default_name=str(normalized.get("name") or "default_pulse"),
            default_description=str(normalized.get("description") or ""),
            default_pulse_address=normalized.get("pulse_address"),
        )
        normalized["input_schema"] = dict(normalized.get("input_schema") or {})
        normalized["mapping"] = dict(normalized.get("mapping") or {})
        normalized["output_schema"] = dict(normalized.get("output_schema") or {})
        normalized["tags"] = list(normalized.get("tags") or [])
        cost_points = _point_cost_from_payload(normalized)
        normalized["cost_points"] = cost_points
        normalized.setdefault("cost", cost_points)
        pricing = dict(normalized.get("pricing") or {})
        pricing.setdefault("plaza_points", cost_points)
        pricing.setdefault("unit", "call")
        normalized["pricing"] = pricing
        cost_calculation = dict(normalized.get("cost_calculation") or {})
        cost_calculation.setdefault("type", "fixed")
        cost_calculation.setdefault("points", cost_points)
        cost_calculation.setdefault("currency", "plaza_point")
        cost_calculation.setdefault("unit", "call")
        normalized["cost_calculation"] = cost_calculation
        return self._attach_sample_parameters(normalized)

    def _compact_pit_ref(self, value: Any) -> str:
        """Internal helper for compact pit ref."""
        pit_address = PitAddress.from_value(value)
        if pit_address.pit_id:
            return pit_address.to_ref(reference_plaza=self.plaza_url)
        return str(value or "")

    @staticmethod
    def _same_pit_ref(left: Any, right: Any) -> bool:
        """Internal helper for same pit ref."""
        left_address = PitAddress.from_value(left)
        right_address = PitAddress.from_value(right)
        if left_address.pit_id and right_address.pit_id:
            return left_address.pit_id == right_address.pit_id
        return str(left or "").strip() == str(right or "").strip()

    def _search_plaza_directory(self, **params: Any) -> Optional[List[Dict[str, Any]]]:
        """Internal helper to search the Plaza directory."""
        if not self.plaza_url:
            return None
        try:
            headers = self._ensure_token_valid() or {}
            if not headers.get("Authorization"):
                return None
            response = self._plaza_get("/search", params=params, headers=headers)
            if response.status_code == 401:
                self.register()
                headers = self._ensure_token_valid() or {}
                response = self._plaza_get("/search", params=params, headers=headers)
            if response.status_code != 200:
                return None
            payload = response.json()
            return payload if isinstance(payload, list) else []
        except Exception:
            return None

    def _cache_shared_pulse_card(self, card: Mapping[str, Any]) -> None:
        """Internal helper to return the cache shared pulse card."""
        if not isinstance(card, Mapping):
            return
        normalized = dict(card)
        pit_address = PitAddress.from_value(
            normalized.get("pit_address")
            or normalized.get("address")
            or normalized.get("pulse_address")
        )
        if pit_address.pit_id:
            self._shared_pulse_cards_by_id[str(pit_address.pit_id)] = normalized
        card_name = str(normalized.get("name") or "").strip()
        if card_name:
            self._shared_pulse_cards_by_name[card_name] = normalized

    def _prime_shared_pulse_cache(self) -> None:
        """Internal helper for prime shared pulse cache."""
        matches = self._search_plaza_directory(pit_type="Pulse")
        if matches is None:
            return
        self._shared_pulse_cards_by_id = {}
        self._shared_pulse_cards_by_name = {}
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            card = match.get("card") if isinstance(match.get("card"), Mapping) else match
            self._cache_shared_pulse_card(card)

    def _resolve_shared_pulse_card(self, pulse_definition: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Internal helper to resolve the shared pulse card."""
        pulse_address = pulse_definition.get("pulse_address")
        pit_address = PitAddress.from_value(pulse_address)
        if pit_address.pit_id:
            cached = self._shared_pulse_cards_by_id.get(str(pit_address.pit_id))
            if cached:
                return dict(cached)

        pulse_name = pulse_definition.get("pulse_name") or pulse_definition.get("name")
        if pulse_name:
            cached = self._shared_pulse_cards_by_name.get(str(pulse_name))
            if cached:
                return dict(cached)
        return None

    def _enrich_pulse_definition(self, pulse_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper for enrich pulse definition."""
        normalized = dict(pulse_definition)
        shared_card = self._resolve_shared_pulse_card(normalized)
        if not isinstance(shared_card, dict):
            return normalized

        shared_meta = dict(shared_card.get("meta") or {})
        shared_pit_address = shared_card.get("pit_address")
        if shared_pit_address:
            normalized["pulse_address"] = self._compact_pit_ref(shared_pit_address)
        normalized.setdefault("pulse_name", shared_card.get("name") or normalized.get("name"))
        if not normalized.get("name") or normalized.get("name") == "default_pulse":
            normalized["name"] = str(shared_card.get("name") or "default_pulse")
        if not normalized.get("description"):
            normalized["description"] = str(shared_card.get("description") or shared_meta.get("description") or "")
        normalized["tags"] = self._merge_tags(shared_card.get("tags"), shared_meta.get("tags"), normalized.get("tags"))
        if not normalized.get("output_schema") and isinstance(shared_meta.get("output_schema"), dict):
            normalized["output_schema"] = dict(shared_meta["output_schema"])
        if not normalized.get("pulse_id"):
            normalized["pulse_id"] = shared_meta.get("pulse_id")
        shared_definition = shared_meta.get("pulse_definition")
        if isinstance(shared_definition, dict):
            normalized["pulse_definition"] = dict(shared_definition)
        return self._normalize_pulse_definition(normalized)

    def apply_pulser_config(
        self,
        config_data: Dict[str, Any],
        *,
        supported_pulses: Optional[List[Dict[str, Any]]] = None,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        agent_card_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Return the apply pulser config."""
        raw_config = dict(config_data or {})
        pulser_config = raw_config.get("pulser", raw_config)
        self.raw_config = raw_config
        self.config = dict(pulser_config)

        pulse_definitions = self._build_supported_pulses(
            config=self.config,
            pulse_name=pulse_name,
            pulse_address=pulse_address,
            input_schema=input_schema,
            mapping=mapping,
            output_schema=output_schema,
            supported_pulses=supported_pulses,
        )
        if not pulse_definitions:
            raise ValueError("Pulser requires at least one configured pulse.")

        if self.plaza_url:
            self._prime_shared_pulse_cache()
        self.supported_pulses = [self._enrich_pulse_definition(pulse) for pulse in pulse_definitions]
        primary_pulse = self.supported_pulses[0]
        self.pulse_address = primary_pulse.get("pulse_address")
        self.input_schema = primary_pulse.get("input_schema", {})
        self.mapping = primary_pulse.get("mapping", {})
        self.output_schema = primary_pulse.get("output_schema", {})

        card = dict(self.agent_card or {})
        if agent_card_overrides:
            card.update(agent_card_overrides)

        resolved_name = raw_config.get("name") or self.config.get("name") or card.get("name") or self.name
        self.name = str(resolved_name)
        card["name"] = self.name
        card["role"] = raw_config.get("role") or self.config.get("role") or card.get("role") or "pulser"
        card["pit_type"] = "Pulser"
        card["description"] = (
            self.config.get("description")
            or raw_config.get("description")
            or card.get("description")
            or "Provides pulse data and schema mapping."
        )
        card["tags"] = self._merge_tags(raw_config.get("tags"), self.config.get("tags"), card.get("tags"), ["pulser", "pulse"])
        if raw_config.get("address"):
            card["address"] = raw_config["address"]

        meta = dict(card.get("meta") or {})
        meta["pulse_address"] = self.pulse_address
        meta["input_schema"] = self.input_schema
        meta["supported_pulses"] = self.supported_pulses
        meta["pulse_id"] = primary_pulse.get("pulse_id")
        meta["pulse_definition"] = dict(primary_pulse.get("pulse_definition") or {})
        card["meta"] = meta
        self.agent_card = card
        self.app.title = self.name
        self._refresh_pit_address()
        self._refresh_get_pulse_practice_metadata()

    def _refresh_get_pulse_practice_metadata(self) -> None:
        """Refresh pulse practice metadata after config changes."""
        for practice_id in ("get_pulse_data", "quote_pulse_data", "quote_request"):
            practice = next((entry for entry in self.practices if entry.id == practice_id), None)
            if practice is None:
                continue
            practice.bind(self)
            for practice_entry in self._resolve_callable_practice_entries(practice):
                self._upsert_practice_metadata_in_card(practice_entry)

    def resolve_pulse_definition(
        self,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve the pulse definition."""
        if pulse_name:
            requested_name = str(pulse_name).strip()
            for pulse in self.supported_pulses:
                aliases = [
                    str(alias).strip()
                    for alias in list(pulse.get("aliases") or [])
                    if str(alias).strip()
                ]
                if (
                    pulse.get("name") == requested_name
                    or pulse.get("pulse_name") == requested_name
                    or requested_name in aliases
                ):
                    return pulse

        if pulse_address:
            for pulse in self.supported_pulses:
                if self._same_pit_ref(pulse.get("pulse_address"), pulse_address):
                    return pulse

        return self.supported_pulses[0]

    def register(
        self,
        *,
        start_reconnect_on_failure: bool = True,
        request_retries: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ):
        """Register the value."""
        force_register = bool(getattr(self, "_force_plaza_register_once", False))
        if self.plaza_token and time.time() < (self.token_expires_at - 60) and not force_register:
            return
        response = super().register(
            start_reconnect_on_failure=start_reconnect_on_failure,
            request_retries=request_retries,
            request_timeout=request_timeout,
        )
        try:
            self.apply_pulser_config(self.raw_config or self.config, supported_pulses=self.supported_pulses)
        except Exception:
            pass
        return response

    def fetch_pulse_payload(self, pulse_name: str, input_data: Dict[str, Any], pulse_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the pulse payload."""
        return input_data

    def quote_pulse_data(
        self,
        input_data: Dict[str, Any],
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        ttl_seconds: Optional[Any] = None,
        quote_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a Plaza point quote for a pulse request without fetching payload data."""
        pulse_definition = self.resolve_pulse_definition(pulse_name=pulse_name, pulse_address=pulse_address)
        active_name = str(pulse_name or pulse_definition.get("name") or pulse_definition.get("pulse_name") or "pulse")
        active_address = str(pulse_address or pulse_definition.get("pulse_address") or "")
        try:
            ttl_value = int(ttl_seconds)
        except (TypeError, ValueError):
            ttl_value = 900
        ttl_value = max(ttl_value, 1)
        issued_at = datetime.now(timezone.utc)
        valid_until = datetime.fromtimestamp(issued_at.timestamp() + ttl_value, tz=timezone.utc)
        card_meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        provider_user_id = str(self.agent_card.get("owner_user_id") or card_meta.get("owner_user_id") or "")
        provider_username = str(self.agent_card.get("owner_username") or card_meta.get("owner_username") or "")
        points = _point_cost_from_payload(pulse_definition)
        cost_calculation = dict(pulse_definition.get("cost_calculation") or {})
        cost_calculation.setdefault("type", "fixed")
        cost_calculation.setdefault("points", points)
        cost_calculation.setdefault("currency", "plaza_point")
        cost_calculation.setdefault("unit", "call")
        return {
            "status": "quoted",
            "quote_id": str(quote_id or f"quote-{uuid.uuid4()}"),
            "ttl_seconds": ttl_value,
            "issued_at": issued_at.isoformat(),
            "valid_until": valid_until.isoformat(),
            "currency": "plaza_point",
            "unit": "call",
            "points": points,
            "cost_points": points,
            "practice_id": "get_pulse_data",
            "pulse_name": active_name,
            "pulse_address": active_address,
            "agent_id": str(self.agent_id or self.agent_card.get("agent_id") or ""),
            "agent_name": str(self.name or self.agent_card.get("name") or ""),
            "provider_user_id": provider_user_id,
            "provider_username": provider_username,
            "pricing": {
                "points": points,
                "currency": "plaza_point",
                "unit": "call",
            },
            "cost_calculation": cost_calculation,
            "input_fingerprint": self._quote_input_fingerprint(input_data),
        }

    @staticmethod
    def _quote_input_fingerprint(input_data: Dict[str, Any]) -> str:
        """Return a stable, non-secret fingerprint for quote matching."""
        try:
            encoded = json.dumps(input_data or {}, sort_keys=True, default=str)
        except TypeError:
            encoded = str(input_data or {})
        return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))

    def transform(
        self,
        input_data: Dict[str, Any],
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transform the value."""
        pulse_definition = self.resolve_pulse_definition(pulse_name=pulse_name, pulse_address=pulse_address)
        schema = output_schema or pulse_definition.get("output_schema") or self.output_schema or {}
        mapping_rules = mapping or pulse_definition.get("mapping") or self.mapping
        properties = schema.get("properties", {})

        output_fields: List[str] = []
        for field_name in list(properties.keys()) + list(mapping_rules.keys()):
            if field_name not in output_fields:
                output_fields.append(field_name)
        transformed: Dict[str, Any] = {}
        for output_field in output_fields:
            rule = mapping_rules.get(output_field)
            if rule is None:
                continue

            value, found = self._resolve_mapping_value(rule, input_data)
            if found:
                _assign_path(transformed, output_field, value)

        return transformed

    @observed_pulse
    def get_pulse_data(
        self,
        input_data: Dict[str, Any],
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the pulse data."""
        pulse_definition = self.resolve_pulse_definition(pulse_name=pulse_name, pulse_address=pulse_address)
        active_name = pulse_name or pulse_definition.get("name")
        active_label = str(active_name or "pulse")
        with self._track_pulser_work(
            f"{active_label} pulse request",
            f"Serving {active_label} pulse data.",
        ):
            with pulse_stage(self, active_name, "fetch"):
                raw_payload = self.fetch_pulse_payload(active_name, input_data, pulse_definition) or {}
            if not isinstance(raw_payload, dict):
                raise TypeError("fetch_pulse_payload() must return a dict.")
            if raw_payload.get("error"):
                return raw_payload

            mapping_rules = pulse_definition.get("mapping") or self.mapping
            if mapping_rules:
                with pulse_stage(self, active_name, "transform"):
                    return self.transform(
                        raw_payload, pulse_name=active_name,
                        pulse_address=pulse_definition.get("pulse_address"),
                        output_schema=output_schema or pulse_definition.get("output_schema"), mapping=mapping_rules
                    )
            return raw_payload

    def _resolve_mapping_value(self, rule: Any, input_data: Dict[str, Any]) -> tuple[Any, bool]:
        """Internal helper to resolve the mapping value."""
        if isinstance(rule, str):
            value = _resolve_path(input_data, rule)
            return value, value is not None

        if isinstance(rule, Mapping):
            item_rules = rule.get("items")
            source = rule.get("source") or rule.get("from") or rule.get("path") or rule.get("input")
            if item_rules is not None and source:
                value = _resolve_path(input_data, str(source))
                if isinstance(value, list):
                    limit = None
                    if "limit" in rule:
                        limit = _coerce_positive_limit(rule.get("limit"))
                    elif rule.get("limit_from"):
                        limit = _coerce_positive_limit(_resolve_path(input_data, str(rule.get("limit_from"))))
                    entries = value[:limit] if limit is not None else value
                    return [
                        _transform_item_mapping(entry, item_rules)
                        for entry in entries
                        if isinstance(entry, Mapping)
                    ], True
                if "default" in rule:
                    return rule["default"], True
                return None, False

            if rule.get("op") or rule.get("operation"):
                return self._resolve_mapping_operation(rule, input_data)

            if "value" in rule:
                return rule["value"], True

            if source:
                value = _resolve_path(input_data, str(source))
                if value is not None:
                    return value, True

            if "default" in rule:
                return rule["default"], True

            return None, False

        return rule, rule is not None

    def _resolve_mapping_operation(self, rule: Mapping[str, Any], input_data: Dict[str, Any]) -> tuple[Any, bool]:
        """Internal helper to resolve the mapping operation."""
        operation = str(rule.get("op") or rule.get("operation") or "").strip().lower()
        if not operation:
            if "default" in rule:
                return rule["default"], True
            return None, False

        operands = rule.get("args")
        if operands is None:
            operands = []
            if "left" in rule:
                operands.append(rule["left"])
            if "right" in rule:
                operands.append(rule["right"])
        elif not isinstance(operands, list):
            operands = [operands]

        resolved_values: List[Any] = []
        for operand in operands:
            value, found = self._resolve_mapping_operand(operand, input_data)
            if not found:
                if "default" in rule:
                    return rule["default"], True
                return None, False
            resolved_values.append(value)

        numeric_values: List[float] = []
        for value in resolved_values:
            numeric = _coerce_number(value)
            if numeric is None:
                if "default" in rule:
                    return rule["default"], True
                return None, False
            numeric_values.append(numeric)

        try:
            if operation == "abs":
                if len(numeric_values) != 1:
                    raise ValueError("abs expects exactly one operand")
                result = abs(numeric_values[0])
            elif operation == "add":
                result = sum(numeric_values)
            elif operation == "subtract":
                if not numeric_values:
                    raise ValueError("subtract expects at least one operand")
                result = numeric_values[0]
                for value in numeric_values[1:]:
                    result -= value
            elif operation == "subtract_abs":
                if not numeric_values:
                    raise ValueError("subtract_abs expects at least one operand")
                result = numeric_values[0]
                for value in numeric_values[1:]:
                    result -= abs(value)
            elif operation in {"multiply", "product"}:
                if not numeric_values:
                    raise ValueError("multiply expects at least one operand")
                result = 1.0
                for value in numeric_values:
                    result *= value
            elif operation in {"divide", "ratio"}:
                if len(numeric_values) < 2:
                    raise ValueError("divide expects at least two operands")
                result = numeric_values[0]
                for value in numeric_values[1:]:
                    if value == 0:
                        if "default" in rule:
                            return rule["default"], True
                        return None, False
                    result /= value
            else:
                raise ValueError(f"Unsupported mapping operation: {operation}")
        except (TypeError, ValueError):
            if "default" in rule:
                return rule["default"], True
            return None, False

        if "round" in rule:
            try:
                result = round(result, int(rule["round"]))
            except (TypeError, ValueError):
                if "default" in rule:
                    return rule["default"], True
                return None, False

        return result, True

    def _resolve_mapping_operand(self, operand: Any, input_data: Dict[str, Any]) -> tuple[Any, bool]:
        """Internal helper to resolve the mapping operand."""
        if isinstance(operand, Mapping) and (operand.get("op") or operand.get("operation")):
            return self._resolve_mapping_operation(operand, input_data)
        return self._resolve_mapping_value(operand, input_data)

    def receive(self, message: Message):
        """Handle receive for the pulser."""
        if message.msg_type == "get_pulse":
            content = message.content or {}
            if not isinstance(content, dict):
                return {"error": "get_pulse content must be a JSON object"}
            return self.get_pulse_data(
                input_data=content.get("params", {}),
                pulse_name=content.get("pulse_name"),
                pulse_address=content.get("pulse_address"),
                output_schema=content.get("output_schema"),
            )
        return super().receive(message)
