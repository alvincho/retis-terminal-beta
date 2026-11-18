"""
Generic Data Agent pulser.

DataAgentPulser is a domain-neutral pulser that represents a data owner's catalog,
definitions, choices, caveats, and recommendation logic through Plaza pulses.
Product-specific agents should subclass it and provide catalog entries plus optional
source payload fetching.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from phemacast.agents.pulser import ConfigInput, Pulser, _read_config
from starlette.concurrency import run_in_threadpool


_STRING_FIELD = {"type": "string"}
_OBJECT_FIELD = {"type": "object"}
_ARRAY_FIELD = {"type": "array"}


def _read_data_agent_config(config: Optional[ConfigInput]) -> Dict[str, Any]:
    """Read config only when a config was supplied."""
    return _read_config(config) if config is not None else {}


def _schema(properties: Mapping[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a compact JSON object schema."""
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required or []),
    }


def _data_agent_pulse(
    *,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    output_schema: Dict[str, Any],
    tags: List[str],
    test_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a stable generic Data Agent pulse definition."""
    return {
        "name": name,
        "pulse_name": name,
        "pulse_address": f"plaza://pulse/data_agent_{name}",
        "description": description,
        "tags": ["data-agent", "data", *tags],
        "input_schema": input_schema,
        "output_schema": output_schema,
        "test_data": dict(test_data or {}),
    }


DATA_AGENT_PULSE_NAMES = {
    "data_catalog_search",
    "data_definition",
    "data_source_connect",
    "identifier_resolve",
    "coverage_check",
    "entitlement_check",
    "freshness_check",
    "quality_profile",
    "alternative_data_choices",
    "use_case_data_discovery",
    "data_use_case_query",
    "data_bundle_build",
    "data_lineage",
    "caveat_pack",
    "data_recommendation",
    "data_feedback_record",
}

DEFAULT_LLM_CHAT_PULSE_NAME = "llm_chat"


DEFAULT_SUPPORTED_PULSES = [
    _data_agent_pulse(
        name="data_catalog_search",
        description="Search the Data Agent catalog for datasets, fields, entities, and source capabilities.",
        input_schema=_schema(
            {
                "query": _STRING_FIELD,
                "tags": {"type": "array", "items": _STRING_FIELD},
                "limit": {"type": "integer"},
            }
        ),
        output_schema=_schema({"items": _ARRAY_FIELD, "count": {"type": "integer"}}),
        tags=["catalog", "search"],
        test_data={"query": "price", "limit": 5},
    ),
    _data_agent_pulse(
        name="data_definition",
        description="Explain a dataset, field, metric, methodology, units, and caveats.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "field_name": _STRING_FIELD}),
        output_schema=_schema({"definition": _OBJECT_FIELD}, required=["definition"]),
        tags=["definition", "methodology"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="data_source_connect",
        description="Resolve the best source connection metadata for a requested dataset or use case.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "use_case": _STRING_FIELD}),
        output_schema=_schema({"connection": _OBJECT_FIELD}, required=["connection"]),
        tags=["connection"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="identifier_resolve",
        description="Resolve user-facing identifiers into canonical data-owner identifiers.",
        input_schema=_schema({"query": _STRING_FIELD, "identifier": _STRING_FIELD, "identifier_type": _STRING_FIELD}),
        output_schema=_schema({"items": _ARRAY_FIELD, "count": {"type": "integer"}}),
        tags=["identifier", "resolution"],
        test_data={"query": "sample"},
    ),
    _data_agent_pulse(
        name="coverage_check",
        description="Report coverage by universe, region, history, frequency, and known gaps.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "use_case": _STRING_FIELD}),
        output_schema=_schema({"coverage": _OBJECT_FIELD}, required=["coverage"]),
        tags=["coverage"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="entitlement_check",
        description="Check entitlement and allowed output forms for a caller/use case.",
        input_schema=_schema(
            {
                "dataset_id": _STRING_FIELD,
                "use_case": _STRING_FIELD,
                "output_form": _STRING_FIELD,
                "caller": _OBJECT_FIELD,
            }
        ),
        output_schema=_schema({"entitlement": _OBJECT_FIELD}, required=["entitlement"]),
        tags=["entitlement", "rights"],
        test_data={"dataset_id": "sample_dataset", "output_form": "report"},
    ),
    _data_agent_pulse(
        name="freshness_check",
        description="Return freshness, latency, update cadence, stale flags, and as-of metadata.",
        input_schema=_schema({"dataset_id": _STRING_FIELD}),
        output_schema=_schema({"freshness": _OBJECT_FIELD}, required=["freshness"]),
        tags=["freshness", "latency"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="quality_profile",
        description="Return quality, confidence, completeness, revision behavior, and known weaknesses.",
        input_schema=_schema({"dataset_id": _STRING_FIELD}),
        output_schema=_schema({"quality": _OBJECT_FIELD}, required=["quality"]),
        tags=["quality", "confidence"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="alternative_data_choices",
        description="Rank alternative datasets for a use case using fit, freshness, entitlement, cost, and confidence.",
        input_schema=_schema({"use_case": _STRING_FIELD, "preferences": _OBJECT_FIELD, "limit": {"type": "integer"}}),
        output_schema=_schema({"alternatives": _ARRAY_FIELD, "count": {"type": "integer"}}),
        tags=["alternatives", "ranking"],
        test_data={"use_case": "daily report", "preferences": {"freshness": "high"}, "limit": 3},
    ),
    _data_agent_pulse(
        name="use_case_data_discovery",
        description="Recommend the best data bundle for a use case and user preferences.",
        input_schema=_schema({"use_case": _STRING_FIELD, "preferences": _OBJECT_FIELD, "limit": {"type": "integer"}}),
        output_schema=_schema({"recommendation": _OBJECT_FIELD}, required=["recommendation"]),
        tags=["discovery", "recommendation"],
        test_data={"use_case": "daily report", "preferences": {"output_form": "report"}, "limit": 3},
    ),
    _data_agent_pulse(
        name="data_use_case_query",
        description="Answer a use-case-based data question by searching catalog entries and embedded field definitions, optionally backed by an llm_chat pulser.",
        input_schema=_schema(
            {
                "query": _STRING_FIELD,
                "use_case": _STRING_FIELD,
                "preferences": _OBJECT_FIELD,
                "response_mode": _STRING_FIELD,
                "return_mode": _STRING_FIELD,
                "params": _OBJECT_FIELD,
                "data_params": _OBJECT_FIELD,
                "data_limit": {"type": "integer"},
                "limit": {"type": "integer"},
                "include_llm": {"type": "boolean"},
                "llm_pulser_address": _STRING_FIELD,
                "llm_pulser_url": _STRING_FIELD,
                "llm_pulse_name": _STRING_FIELD,
                "llm_model": _STRING_FIELD,
            }
        ),
        output_schema=_schema(
            {
                "query": _STRING_FIELD,
                "use_case": _STRING_FIELD,
                "answer": _STRING_FIELD,
                "response_mode": _STRING_FIELD,
                "recommended_datasets": _ARRAY_FIELD,
                "evidence": _ARRAY_FIELD,
                "selected_dataset": _OBJECT_FIELD,
                "data": _OBJECT_FIELD,
                "data_bundles": _ARRAY_FIELD,
                "data_errors": _ARRAY_FIELD,
                "llm": _OBJECT_FIELD,
            },
            required=["answer"],
        ),
        tags=["query", "use-case", "llm", "field-definition"],
        test_data={"query": "Which fields support a daily report?", "use_case": "daily report", "response_mode": "metadata", "limit": 3},
    ),
    _data_agent_pulse(
        name="data_bundle_build",
        description="Build a normalized data bundle from a selected dataset plus evidence and caveats.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "params": _OBJECT_FIELD, "use_case": _STRING_FIELD}),
        output_schema=_schema({"bundle": _OBJECT_FIELD}, required=["bundle"]),
        tags=["bundle", "normalization"],
        test_data={"dataset_id": "sample_dataset", "params": {}},
    ),
    _data_agent_pulse(
        name="data_lineage",
        description="Return provenance, source chain, citations, and transformations for a dataset.",
        input_schema=_schema({"dataset_id": _STRING_FIELD}),
        output_schema=_schema({"lineage": _OBJECT_FIELD}, required=["lineage"]),
        tags=["lineage", "provenance"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="caveat_pack",
        description="Return caveats and source notes that should travel with the output.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "use_case": _STRING_FIELD}),
        output_schema=_schema({"caveats": _ARRAY_FIELD, "count": {"type": "integer"}}),
        tags=["caveats"],
        test_data={"dataset_id": "sample_dataset"},
    ),
    _data_agent_pulse(
        name="data_recommendation",
        description="Return one selected data choice, rejected alternatives, evidence, caveats, and bundle metadata.",
        input_schema=_schema({"use_case": _STRING_FIELD, "preferences": _OBJECT_FIELD, "params": _OBJECT_FIELD}),
        output_schema=_schema({"recommendation": _OBJECT_FIELD}, required=["recommendation"]),
        tags=["recommendation", "evidence"],
        test_data={"use_case": "daily report", "preferences": {"freshness": "high"}, "params": {}},
    ),
    _data_agent_pulse(
        name="data_feedback_record",
        description="Record governed feedback about selected or rejected data recommendations.",
        input_schema=_schema({"dataset_id": _STRING_FIELD, "feedback": _OBJECT_FIELD, "use_case": _STRING_FIELD}),
        output_schema=_schema({"feedback": _OBJECT_FIELD}, required=["feedback"]),
        tags=["feedback", "learning"],
        test_data={"dataset_id": "sample_dataset", "feedback": {"accepted": True}},
    ),
]


class DataAgentPulser(Pulser):
    """Generic pulser for data-owner catalog discovery, explanation, and recommendation."""

    def __init__(
        self,
        config: Optional[ConfigInput] = None,
        *,
        name: str = "DataAgentPulser",
        supported_pulses: Optional[List[Dict[str, Any]]] = None,
        data_catalog: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ):
        """Initialize the Data Agent pulser."""
        self.data_catalog = [copy.deepcopy(dict(entry)) for entry in data_catalog or [] if isinstance(entry, Mapping)]
        self.feedback_records: List[Dict[str, Any]] = []
        config_data = _read_data_agent_config(config)
        pulser_config = config_data.get("pulser", config_data) if isinstance(config_data, Mapping) else {}
        self.llm_pulser = self._normalize_llm_pulser_config(pulser_config)
        resolved_supported_pulses = supported_pulses
        if resolved_supported_pulses is None and isinstance(pulser_config.get("supported_pulses"), list):
            resolved_supported_pulses = [
                copy.deepcopy(dict(pulse))
                for pulse in pulser_config.get("supported_pulses") or []
                if isinstance(pulse, Mapping)
            ]
        if resolved_supported_pulses is None:
            resolved_supported_pulses = copy.deepcopy(DEFAULT_SUPPORTED_PULSES)
        super().__init__(
            config=config,
            name=name,
            supported_pulses=resolved_supported_pulses,
            **kwargs,
        )
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        self.templates = Jinja2Templates(directory=template_dir)
        self._apply_data_agent_config()
        self._setup_data_agent_routes()

    def _apply_data_agent_config(self) -> None:
        """Load catalog entries from config after the base Pulser has normalized it."""
        config_catalog = self.config.get("data_catalog") or self.raw_config.get("data_catalog")
        if isinstance(config_catalog, list):
            self.data_catalog = [
                copy.deepcopy(dict(entry))
                for entry in config_catalog
                if isinstance(entry, Mapping)
            ]
        self.llm_pulser = (
            self._normalize_llm_pulser_config(self.config)
            or self._normalize_llm_pulser_config(self.raw_config)
            or self.llm_pulser
        )
        card = dict(self.agent_card or {})
        card["pit_type"] = "Pulser"
        card["role"] = card.get("role") or "pulser"
        card["description"] = card.get("description") or "Provides data-agent catalog discovery and recommendations."
        card["tags"] = self._merge_unique(card.get("tags"), ["data-agent", "pulser", "data"])
        self.agent_card = card

    def _setup_data_agent_routes(self) -> None:
        """Set up the Data Agent browser UI and editor APIs."""
        @self.app.get("/")
        async def data_agent_pulser_ui(request: Request):
            """Render the shared pulser editor with Data Agent extensions."""
            return self.templates.TemplateResponse(
                request=request,
                name="phemacast/pulsers/templates/data_agent_pulser_editor.html",
                context={
                    "agent_name": self.agent_card.get("name", self.name),
                    "config_path": str(self.config_path) if self.config_path else "",
                },
            )

        @self.app.get("/api/config")
        async def get_data_agent_pulser_config():
            """Return the current Data Agent pulser config."""
            config = await run_in_threadpool(self._load_config_document)
            return {
                "status": "success",
                "config": config,
                "config_path": str(self.config_path) if self.config_path else None,
            }

        @self.app.post("/api/config")
        async def save_data_agent_pulser_config(request: Request):
            """Save the Data Agent pulser config when this agent is file-backed."""
            payload = await request.json()
            config = payload.get("config") if isinstance(payload, dict) and isinstance(payload.get("config"), dict) else payload
            if not isinstance(config, dict):
                raise HTTPException(status_code=400, detail="Config payload must be a JSON object.")
            saved = await run_in_threadpool(self._save_config_document, config)
            return {
                "status": "success",
                "config": saved,
                "config_path": str(self.config_path) if self.config_path else None,
            }

        @self.app.post("/api/test-pulse")
        async def test_data_agent_pulse(request: Request):
            """Run a Data Agent pulse from the browser editor."""
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Test payload must be a JSON object.")

            pulse_name = payload.get("pulse_name")
            params = payload.get("params") or {}
            config = payload.get("config")
            include_debug = bool(payload.get("debug"))
            if not pulse_name:
                raise HTTPException(status_code=400, detail="pulse_name is required.")
            if not isinstance(params, dict):
                raise HTTPException(status_code=400, detail="params must be a JSON object.")
            if config is not None and not isinstance(config, dict):
                raise HTTPException(status_code=400, detail="config must be a JSON object when provided.")

            def _run_test_sync():
                runtime_config = config if isinstance(config, dict) else self._load_config_document()
                runner = self.__class__(config=runtime_config, auto_register=False)
                pulse_definition = runner.resolve_pulse_definition(pulse_name=str(pulse_name))
                result = runner.get_pulse_data(dict(params), pulse_name=str(pulse_name))
                return runner, pulse_definition, result

            try:
                runner, pulse_definition, result = await run_in_threadpool(_run_test_sync)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            response = {
                "status": "success",
                "pulse_name": str(pulse_name),
                "params": params,
                "result": result,
            }
            if include_debug:
                response["debug"] = {
                    "pulse_definition": pulse_definition,
                    "catalog_count": len(runner.catalog_entries()),
                    "feedback_count": len(runner.feedback_records),
                    "result": result,
                }
            return response

    def _load_config_document(self) -> Dict[str, Any]:
        """Load the current editor config document."""
        if self.config_path and self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.raw_config = dict(loaded)
            return self._build_editor_config_document(loaded)
        return self._build_editor_config_document(self.raw_config or self._synthesize_runtime_config())

    def _save_config_document(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a file-backed Data Agent config."""
        if not self.config_path:
            raise HTTPException(status_code=400, detail="This DataAgentPulser was not started from a config file.")

        normalized = self._normalize_config_document(config)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(normalized, indent=4), encoding="utf-8")
        self.raw_config = dict(normalized)
        self.apply_pulser_config(normalized, supported_pulses=normalized.get("supported_pulses"))
        self._apply_data_agent_config()
        return self._build_editor_config_document(normalized)

    def _normalize_config_document(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Data Agent config for the shared editor."""
        document = dict(config or {})
        document.setdefault("name", self.agent_card.get("name", self.name))
        document.setdefault("type", f"{self.__class__.__module__}.{self.__class__.__name__}")
        document.setdefault("host", self.host)
        document.setdefault("port", self.port)
        if self.plaza_url and "plaza_url" not in document:
            document["plaza_url"] = self.plaza_url
        document.setdefault("role", "pulser")
        document.setdefault("description", self.agent_card.get("description", ""))
        document["tags"] = list(document.get("tags") or self.agent_card.get("tags") or [])
        document["supported_pulses"] = [
            dict(pulse)
            for pulse in (document.get("supported_pulses") or self.supported_pulses or [])
            if isinstance(pulse, Mapping)
        ]
        document["data_catalog"] = [
            copy.deepcopy(dict(entry))
            for entry in (document.get("data_catalog") or self.data_catalog or [])
            if isinstance(entry, Mapping)
        ]
        if self.llm_pulser and not any(key in document for key in ("llm_pulser", "llm_chat_pulser")):
            document["llm_pulser"] = copy.deepcopy(dict(self.llm_pulser))
        if "pools" in self.raw_config and "pools" not in document:
            document["pools"] = self.raw_config["pools"]
        if "practices" in self.raw_config and "practices" not in document:
            document["practices"] = self.raw_config["practices"]
        return document

    def _build_editor_config_document(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Build the shared editor config document."""
        document = self._normalize_config_document(config)
        if self.supported_pulses:
            document["supported_pulses"] = [dict(pulse) for pulse in self.supported_pulses]
        if self.data_catalog:
            document["data_catalog"] = [copy.deepcopy(dict(entry)) for entry in self.data_catalog]
        return document

    def _synthesize_runtime_config(self) -> Dict[str, Any]:
        """Create a runtime config for non-file-backed Data Agent instances."""
        return {
            "name": self.agent_card.get("name", self.name),
            "type": f"{self.__class__.__module__}.{self.__class__.__name__}",
            "host": self.host,
            "port": self.port,
            "plaza_url": self.plaza_url,
            "role": self.agent_card.get("role", "pulser"),
            "description": self.agent_card.get("description", ""),
            "tags": list(self.agent_card.get("tags") or []),
            "supported_pulses": [dict(pulse) for pulse in self.supported_pulses],
            "data_catalog": [copy.deepcopy(dict(entry)) for entry in self.data_catalog],
            "llm_pulser": copy.deepcopy(dict(self.llm_pulser)) if self.llm_pulser else {},
        }

    @staticmethod
    def _normalize_llm_pulser_config(source: Any) -> Dict[str, Any]:
        """Return normalized llm_chat pulser config from namespaced or nested config."""
        if not isinstance(source, Mapping):
            return {}

        candidates: List[Mapping[str, Any]] = []
        for key in ("llm_chat_pulser", "llm_pulser"):
            value = source.get(key)
            if isinstance(value, Mapping):
                candidates.append(value)

        namespaced = {
            "address": source.get("llm_pulser_address")
            or source.get("llm_pulser_url")
            or source.get("llm_chat_pulser_address")
            or source.get("llm_chat_pulser_url"),
            "pulse_name": source.get("llm_pulse_name") or source.get("llm_chat_pulse_name"),
            "pulse_address": source.get("llm_pulse_address") or source.get("llm_chat_pulse_address"),
            "practice_id": source.get("llm_practice_id"),
            "model": source.get("llm_model"),
            "timeout_sec": source.get("llm_timeout_sec") or source.get("remote_llm_pulser_timeout_sec"),
        }
        candidates.append(namespaced)

        for candidate in candidates:
            target = str(
                candidate.get("address")
                or candidate.get("url")
                or candidate.get("target")
                or candidate.get("pulser_address")
                or candidate.get("llm_pulser_address")
                or ""
            ).strip()
            if not target:
                continue
            return {
                "address": target,
                "practice_id": str(candidate.get("practice_id") or "get_pulse_data").strip() or "get_pulse_data",
                "pulse_name": str(
                    candidate.get("pulse_name")
                    or candidate.get("llm_pulse_name")
                    or DEFAULT_LLM_CHAT_PULSE_NAME
                ).strip()
                or DEFAULT_LLM_CHAT_PULSE_NAME,
                "pulse_address": str(candidate.get("pulse_address") or candidate.get("llm_pulse_address") or "").strip(),
                "model": str(candidate.get("model") or candidate.get("llm_model") or "").strip(),
                "timeout_sec": candidate.get("timeout_sec") or candidate.get("timeout") or 240,
            }
        return {}

    @staticmethod
    def _merge_unique(*groups: Any) -> List[str]:
        """Merge tag-like values without duplicates."""
        merged: List[str] = []
        for group in groups:
            if not group:
                continue
            for item in group:
                value = str(item).strip()
                if value and value not in merged:
                    merged.append(value)
        return merged

    @staticmethod
    def _as_text(value: Any) -> str:
        """Return lower-cased searchable text for scoring."""
        if value is None:
            return ""
        if isinstance(value, Mapping):
            return " ".join(f"{key} {DataAgentPulser._as_text(item)}" for key, item in value.items()).lower()
        if isinstance(value, list):
            return " ".join(DataAgentPulser._as_text(item) for item in value).lower()
        return str(value).lower()

    @staticmethod
    def _coerce_limit(value: Any, default: int = 5) -> int:
        """Normalize a positive result limit."""
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        """Normalize common boolean input forms."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "llm"}

    @staticmethod
    def _tokenize_query(value: str) -> List[str]:
        """Tokenize a use-case query into stable ranking terms."""
        stopwords = {
            "about",
            "also",
            "analysis",
            "and",
            "are",
            "best",
            "can",
            "data",
            "dataset",
            "datasets",
            "for",
            "from",
            "has",
            "have",
            "how",
            "including",
            "into",
            "latest",
            "need",
            "needs",
            "report",
            "should",
            "source",
            "stock",
            "that",
            "the",
            "this",
            "use",
            "used",
            "what",
            "which",
            "with",
        }
        raw = str(value or "").lower()
        normalized = "".join(char if char.isalnum() else " " for char in raw)
        terms: List[str] = []
        for token in normalized.split():
            if len(token) <= 2 or token in stopwords:
                continue
            variants = [token]
            if token.endswith("ies") and len(token) > 4:
                variants.append(f"{token[:-3]}y")
            if token.endswith("s") and len(token) > 3:
                variants.append(token[:-1])
            for variant in variants:
                if variant and variant not in stopwords and variant not in terms:
                    terms.append(variant)
        return terms

    @staticmethod
    def _response_mode(value: Any) -> str:
        """Normalize data_use_case_query response mode."""
        mode = str(value or "metadata").strip().lower().replace("-", "_")
        aliases = {
            "meta": "metadata",
            "metadata_only": "metadata",
            "catalog": "metadata",
            "catalog_only": "metadata",
            "payload": "data",
            "data_only": "data",
            "direct": "data",
            "bundle": "data",
            "bundles": "data",
            "all": "both",
            "metadata_and_data": "both",
            "data_and_metadata": "both",
        }
        mode = aliases.get(mode, mode)
        return mode if mode in {"metadata", "data", "both"} else "metadata"

    def catalog_entries(self) -> List[Dict[str, Any]]:
        """Return available catalog entries."""
        return [copy.deepcopy(dict(entry)) for entry in self.data_catalog if isinstance(entry, Mapping)]

    def resolve_catalog_entry(self, input_data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Resolve a catalog entry by dataset id, pulse name, field name, or query."""
        dataset_id = str(input_data.get("dataset_id") or input_data.get("id") or "").strip()
        pulse_name = str(input_data.get("pulse_name") or input_data.get("source_pulse_name") or "").strip()
        field_name = str(input_data.get("field_name") or "").strip().lower()
        query = str(input_data.get("query") or input_data.get("identifier") or "").strip().lower()
        for entry in self.catalog_entries():
            entry_id = str(entry.get("dataset_id") or entry.get("id") or entry.get("name") or "").strip()
            entry_pulse = str(entry.get("source_pulse_name") or entry.get("pulse_name") or "").strip()
            aliases = [str(alias).strip() for alias in entry.get("aliases") or []]
            if dataset_id and dataset_id in {entry_id, entry_pulse, *aliases}:
                return entry
            if pulse_name and pulse_name in {entry_pulse, entry_id, *aliases}:
                return entry
            if field_name:
                fields = entry.get("fields") if isinstance(entry.get("fields"), Mapping) else {}
                if field_name in {str(key).lower() for key in fields.keys()}:
                    return entry
            if query and query in self._as_text(entry):
                return entry
        return None

    def search_catalog(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Search catalog entries by query and tags."""
        query = str(input_data.get("query") or "").strip().lower()
        requested_tags = {str(tag).strip().lower() for tag in input_data.get("tags") or [] if str(tag).strip()}
        limit = self._coerce_limit(input_data.get("limit"), default=10)
        scored: List[tuple[int, Dict[str, Any]]] = []
        for entry in self.catalog_entries():
            text = self._as_text(entry)
            tags = {str(tag).lower() for tag in entry.get("tags") or []}
            score = 0
            if query and query in text:
                score += 4
            if query:
                score += sum(1 for token in query.split() if token and token in text)
            if requested_tags:
                tag_hits = len(requested_tags.intersection(tags))
                if not tag_hits:
                    continue
                score += tag_hits * 3
            if not query and not requested_tags:
                score = 1
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("dataset_id") or item[1].get("name") or "")))
        items = [self._public_catalog_entry(entry, score=score) for score, entry in scored[:limit]]
        return {"items": items, "count": len(items)}

    def _public_catalog_entry(self, entry: Mapping[str, Any], *, score: Optional[int] = None) -> Dict[str, Any]:
        """Return catalog metadata safe to expose through recommendations."""
        payload = {
            "dataset_id": entry.get("dataset_id") or entry.get("id") or entry.get("name"),
            "name": entry.get("name") or entry.get("dataset_id"),
            "description": entry.get("description") or "",
            "tags": list(entry.get("tags") or []),
            "source": entry.get("source") or entry.get("provider") or "",
            "source_pulse_name": entry.get("source_pulse_name") or entry.get("pulse_name") or "",
        }
        if score is not None:
            payload["score"] = score
        return payload

    def _score_entry(self, entry: Mapping[str, Any], use_case: str, preferences: Mapping[str, Any]) -> tuple[int, List[str]]:
        """Score one catalog entry for use-case discovery."""
        text = self._as_text(entry)
        primary_text = self._as_text(
            {
                "dataset_id": entry.get("dataset_id"),
                "name": entry.get("name"),
                "description": entry.get("description"),
                "tags": entry.get("tags"),
                "aliases": entry.get("aliases"),
                "source_pulse_name": entry.get("source_pulse_name") or entry.get("pulse_name"),
            }
        )
        reasons: List[str] = []
        score = 0
        for token in self._tokenize_query(use_case):
            if token in primary_text:
                score += 4
                if len(reasons) < 3:
                    reasons.append(f"matches primary use-case term '{token}'")
            elif token in text:
                score += 1
                if len(reasons) < 3:
                    reasons.append(f"matches use-case term '{token}'")
        entry_tags = {str(tag).lower() for tag in entry.get("tags") or []}
        for key, value in preferences.items():
            preference_text = f"{key} {value}".lower()
            if str(value).lower() in {"", "none"}:
                continue
            if str(key).lower() in text or str(value).lower() in text:
                score += 2
                if len(reasons) < 5:
                    reasons.append(f"matches preference {key}={value}")
            if str(value).lower() in entry_tags:
                score += 2
        quality = entry.get("quality") if isinstance(entry.get("quality"), Mapping) else {}
        confidence = str(quality.get("confidence") or entry.get("confidence") or "").lower()
        if confidence in {"high", "strong"}:
            score += 1
        if not reasons:
            reasons.append("available catalog candidate")
        return score, reasons

    def rank_alternatives(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Rank catalog entries for a use case and preferences."""
        use_case = str(input_data.get("use_case") or input_data.get("query") or "").strip()
        preferences = input_data.get("preferences") if isinstance(input_data.get("preferences"), Mapping) else {}
        limit = self._coerce_limit(input_data.get("limit"), default=5)
        scored: List[tuple[int, Dict[str, Any], List[str]]] = []
        for entry in self.catalog_entries():
            score, reasons = self._score_entry(entry, use_case, preferences)
            if score > 0 or not use_case:
                scored.append((score, entry, reasons))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("dataset_id") or item[1].get("name") or "")))
        alternatives = []
        for score, entry, reasons in scored[:limit]:
            alternative = self._public_catalog_entry(entry, score=score)
            alternative["reasons"] = reasons
            alternative["evidence"] = self._evidence_for_entry(entry)
            alternatives.append(alternative)
        return {"alternatives": alternatives, "count": len(alternatives)}

    def _evidence_for_entry(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """Build the standard evidence package for an entry."""
        return {
            "as_of": entry.get("as_of") or datetime.now(timezone.utc).isoformat(),
            "coverage": copy.deepcopy(entry.get("coverage") or {}),
            "freshness": copy.deepcopy(entry.get("freshness") or {}),
            "quality": copy.deepcopy(entry.get("quality") or {}),
            "entitlement": copy.deepcopy(entry.get("entitlement") or {"status": "unknown"}),
            "lineage": copy.deepcopy(entry.get("lineage") or {}),
            "caveats": list(entry.get("caveats") or []),
        }

    def discover_use_case_data(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the top data recommendation for a use case."""
        ranked = self.rank_alternatives(input_data)
        alternatives = ranked["alternatives"]
        selected = alternatives[0] if alternatives else None
        rejected = alternatives[1:]
        return {
            "recommendation": {
                "use_case": input_data.get("use_case") or input_data.get("query") or "",
                "preferences": copy.deepcopy(input_data.get("preferences") or {}),
                "selected": selected,
                "alternatives_considered": alternatives,
                "rejected_alternatives": rejected,
            }
        }

    def _field_entries(self, entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Return catalog field definitions as named dictionaries."""
        fields = entry.get("fields")
        normalized: List[Dict[str, Any]] = []
        if isinstance(fields, Mapping):
            for name, definition in fields.items():
                payload = copy.deepcopy(dict(definition)) if isinstance(definition, Mapping) else {"definition": definition}
                payload.setdefault("name", str(name))
                normalized.append(payload)
            return normalized
        if isinstance(fields, list):
            for item in fields:
                if isinstance(item, Mapping):
                    payload = copy.deepcopy(dict(item))
                    payload.setdefault("name", payload.get("field_name") or "")
                    normalized.append(payload)
                elif item:
                    normalized.append({"name": str(item), "definition": ""})
        return normalized

    def _matching_fields(self, entry: Mapping[str, Any], query_text: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        """Return field definitions most relevant to a query/use case."""
        tokens = [token for token in query_text.lower().split() if len(token) > 2]
        scored: List[tuple[int, str, Dict[str, Any]]] = []
        for field in self._field_entries(entry):
            name = str(field.get("name") or field.get("field_name") or "").strip()
            text = self._as_text(field)
            score = 0
            if query_text and query_text.lower() in text:
                score += 4
            score += sum(1 for token in tokens if token in text or token in name.lower())
            if score > 0:
                scored.append((score, name, field))
        if not scored and self._field_entries(entry):
            scored = [(0, str(field.get("name") or ""), field) for field in self._field_entries(entry)]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [copy.deepcopy(field) for _, _, field in scored[:limit]]

    def _build_use_case_query_evidence(self, input_data: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Search catalog and field definitions for a use-case query."""
        query = str(input_data.get("query") or "").strip()
        use_case = str(input_data.get("use_case") or "").strip()
        combined_query = " ".join(part for part in (query, use_case) if part).strip()
        limit = self._coerce_limit(input_data.get("limit"), default=5)
        preferences = input_data.get("preferences") if isinstance(input_data.get("preferences"), Mapping) else {}
        ranked = self.rank_alternatives(
            {
                "use_case": combined_query,
                "preferences": preferences,
                "limit": limit,
            }
        )
        evidence: List[Dict[str, Any]] = []
        for alternative in ranked.get("alternatives") or []:
            entry = self.resolve_catalog_entry({"dataset_id": alternative.get("dataset_id")})
            if not entry:
                continue
            matching_fields = self._matching_fields(entry, combined_query)
            package = self._evidence_for_entry(entry)
            evidence.append(
                {
                    "dataset": self._public_catalog_entry(entry, score=alternative.get("score")),
                    "reason": "; ".join(alternative.get("reasons") or []),
                    "matching_fields": matching_fields,
                    "coverage": package.get("coverage") or {},
                    "freshness": package.get("freshness") or {},
                    "quality": package.get("quality") or {},
                    "entitlement": package.get("entitlement") or {},
                    "lineage": package.get("lineage") or {},
                    "caveats": package.get("caveats") or [],
                    "methodology": entry.get("methodology") or "",
                }
            )
        return evidence

    def _fallback_use_case_answer(
        self,
        *,
        query: str,
        use_case: str,
        evidence: List[Dict[str, Any]],
    ) -> str:
        """Build a deterministic answer when llm_chat is not configured or fails."""
        subject = query or use_case or "this use case"
        if not evidence:
            return f"I could not find catalog evidence for {subject}. Add matching catalog entries and embedded field definitions, then retry."
        selected = evidence[0]
        dataset = selected.get("dataset") if isinstance(selected.get("dataset"), Mapping) else {}
        fields = selected.get("matching_fields") if isinstance(selected.get("matching_fields"), list) else []
        field_names = [str(field.get("name") or field.get("field_name") or "").strip() for field in fields if field]
        field_text = ", ".join(name for name in field_names if name) or "the documented fields"
        caveats = selected.get("caveats") if isinstance(selected.get("caveats"), list) else []
        caveat_text = f" Caveats: {'; '.join(str(item) for item in caveats[:2])}." if caveats else ""
        return (
            f"For {subject}, use {dataset.get('name') or dataset.get('dataset_id') or 'the top-ranked dataset'} "
            f"({dataset.get('dataset_id') or 'unknown dataset'}). It matches the catalog evidence because "
            f"{selected.get('reason') or 'it is an available catalog candidate'}. Start with {field_text}."
            f"{caveat_text}"
        )

    def _build_use_case_query_prompt(
        self,
        *,
        query: str,
        use_case: str,
        preferences: Mapping[str, Any],
        evidence: List[Dict[str, Any]],
    ) -> str:
        """Build an evidence-only prompt for llm_chat."""
        evidence_json = json.dumps(evidence, indent=2, ensure_ascii=False, default=str)
        preferences_json = json.dumps(dict(preferences or {}), indent=2, ensure_ascii=False, default=str)
        return "\n".join(
            [
                "You are a generic Data Agent query responder.",
                "Answer only from the catalog evidence below. Do not invent datasets, fields, coverage, rights, or caveats.",
                "Recommend the best dataset(s) and field(s) for the requested use case.",
                "If the evidence is insufficient, say what is missing.",
                "",
                f"Query: {query}",
                f"Use case: {use_case}",
                f"Preferences: {preferences_json}",
                "",
                "Catalog evidence:",
                evidence_json,
                "",
                "Return a concise answer with recommended datasets, relevant fields, and caveats.",
            ]
        )

    @staticmethod
    def _llm_response_text(value: Any) -> str:
        """Extract the likely text response from common llm_chat payload shapes."""
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, Mapping):
            return ""
        for key in ("response", "content", "text", "message", "answer"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            if isinstance(candidate, Mapping):
                text = DataAgentPulser._llm_response_text(candidate)
                if text:
                    return text
        for key in ("result", "data", "payload"):
            nested = value.get(key)
            if nested is not value:
                text = DataAgentPulser._llm_response_text(nested)
                if text:
                    return text
        return ""

    def _llm_selection_for_query(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Resolve llm_chat pulser selection from runtime input or config."""
        runtime = self._normalize_llm_pulser_config(input_data)
        selection = runtime or self.llm_pulser or {}
        if not selection:
            return {}
        resolved = copy.deepcopy(dict(selection))
        if input_data.get("llm_pulse_name"):
            resolved["pulse_name"] = str(input_data.get("llm_pulse_name")).strip()
        if input_data.get("llm_pulse_address"):
            resolved["pulse_address"] = str(input_data.get("llm_pulse_address")).strip()
        if input_data.get("llm_model") or input_data.get("model"):
            resolved["model"] = str(input_data.get("llm_model") or input_data.get("model")).strip()
        return resolved

    def _call_llm_chat_for_use_case_query(
        self,
        *,
        selection: Mapping[str, Any],
        prompt: str,
        input_data: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Call an llm_chat pulser with the generated evidence prompt."""
        target = str(selection.get("address") or selection.get("url") or selection.get("target") or "").strip()
        if not target:
            raise ValueError("No llm_chat pulser address was configured.")
        params: Dict[str, Any] = {"prompt": prompt}
        model = str(input_data.get("llm_model") or input_data.get("model") or selection.get("model") or "").strip()
        if model:
            params["model"] = model
        for key in ("api_key", "openai_api_key"):
            value = str(input_data.get(key) or "").strip()
            if value:
                params[key] = value
        payload: Dict[str, Any] = {
            "pulse_name": str(selection.get("pulse_name") or DEFAULT_LLM_CHAT_PULSE_NAME),
            "params": params,
        }
        pulse_address = str(selection.get("pulse_address") or "").strip()
        if pulse_address:
            payload["pulse_address"] = pulse_address
        timeout = self._coerce_limit(input_data.get("timeout_sec") or selection.get("timeout_sec"), default=240)
        return self.UsePractice(
            str(selection.get("practice_id") or "get_pulse_data"),
            payload,
            pit_address=target,
            timeout=timeout,
        )

    def _data_params_for_use_case_query(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Return source-pulse params for a data-returning use-case query."""
        params: Dict[str, Any] = {}
        for key in ("params", "data_params", "source_params"):
            value = input_data.get(key)
            if isinstance(value, Mapping):
                params.update(copy.deepcopy(dict(value)))

        reserved = {
            "api_key",
            "data_limit",
            "data_params",
            "include_llm",
            "limit",
            "llm_model",
            "llm_pulse_address",
            "llm_pulse_name",
            "llm_pulser_address",
            "llm_pulser_url",
            "model",
            "openai_api_key",
            "output_mode",
            "params",
            "preferences",
            "query",
            "response_mode",
            "return_mode",
            "source_params",
            "timeout_sec",
            "use_case",
        }
        for key, value in input_data.items():
            if key in reserved or key.startswith("llm_") or key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                params.setdefault(str(key), value)
        return params

    def _build_use_case_query_data_bundles(
        self,
        *,
        input_data: Mapping[str, Any],
        evidence: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Fetch payload bundles for top-ranked use-case evidence."""
        if not evidence:
            return []
        data_limit = self._coerce_limit(input_data.get("data_limit"), default=1)
        source_params = self._data_params_for_use_case_query(input_data)
        bundles: List[Dict[str, Any]] = []
        for item in evidence[:data_limit]:
            dataset = item.get("dataset") if isinstance(item.get("dataset"), Mapping) else {}
            dataset_id = str(dataset.get("dataset_id") or "").strip()
            if not dataset_id:
                continue
            bundle = self.build_data_bundle(
                {
                    "dataset_id": dataset_id,
                    "params": source_params,
                    "use_case": input_data.get("use_case") or input_data.get("query") or "",
                }
            )
            if "bundle" in bundle:
                bundles.append(bundle["bundle"])
            else:
                bundles.append(
                    {
                        "dataset": copy.deepcopy(dict(dataset)),
                        "params": copy.deepcopy(dict(source_params)),
                        "payload": copy.deepcopy(dict(bundle)) if isinstance(bundle, Mapping) else {"error": str(bundle)},
                        "evidence": copy.deepcopy(item),
                        "use_case": input_data.get("use_case") or input_data.get("query") or "",
                    }
                )
        return bundles

    def answer_use_case_query(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Search embedded field definitions and answer a natural-language use-case query."""
        query = str(input_data.get("query") or "").strip()
        use_case = str(input_data.get("use_case") or "").strip()
        preferences = input_data.get("preferences") if isinstance(input_data.get("preferences"), Mapping) else {}
        response_mode = self._response_mode(
            input_data.get("response_mode")
            or input_data.get("return_mode")
            or input_data.get("output_mode")
        )
        evidence = self._build_use_case_query_evidence(input_data)
        fallback_answer = self._fallback_use_case_answer(query=query, use_case=use_case, evidence=evidence)
        selection = self._llm_selection_for_query(input_data)
        include_llm = self._coerce_bool(input_data.get("include_llm"), default=False)
        llm_meta: Dict[str, Any] = {
            "used": False,
            "pulse_name": str(selection.get("pulse_name") or DEFAULT_LLM_CHAT_PULSE_NAME) if selection else DEFAULT_LLM_CHAT_PULSE_NAME,
            "pulser_address": str(selection.get("address") or selection.get("url") or selection.get("target") or "") if selection else "",
            "model": str(input_data.get("llm_model") or input_data.get("model") or selection.get("model") or "") if selection else "",
        }
        answer = fallback_answer
        if include_llm and evidence:
            prompt = self._build_use_case_query_prompt(
                query=query,
                use_case=use_case,
                preferences=preferences,
                evidence=evidence,
            )
            llm_meta["prompt_chars"] = len(prompt)
            try:
                llm_result = self._call_llm_chat_for_use_case_query(selection=selection, prompt=prompt, input_data=input_data)
                llm_answer = self._llm_response_text(llm_result)
                if llm_answer:
                    answer = llm_answer
                    llm_meta["used"] = True
                    if isinstance(llm_result, Mapping):
                        llm_meta["provider"] = str(llm_result.get("provider") or "")
                        llm_meta["model"] = str(llm_result.get("model") or llm_meta.get("model") or "")
                else:
                    llm_meta["error"] = "llm_chat returned no text response."
            except Exception as exc:
                llm_meta["error"] = str(exc)
        elif include_llm and not evidence:
            llm_meta["error"] = "No catalog evidence was available for llm_chat."

        recommended_datasets = [
            copy.deepcopy(item.get("dataset") or {})
            for item in evidence
            if isinstance(item.get("dataset"), Mapping)
        ]
        data_bundles: List[Dict[str, Any]] = []
        if response_mode in {"data", "both"}:
            data_bundles = self._build_use_case_query_data_bundles(input_data=input_data, evidence=evidence)

        result: Dict[str, Any] = {
            "query": query,
            "use_case": use_case,
            "preferences": copy.deepcopy(dict(preferences or {})),
            "response_mode": response_mode,
            "answer": answer,
            "llm": llm_meta,
        }
        if recommended_datasets:
            result["selected_dataset"] = copy.deepcopy(recommended_datasets[0])
        if response_mode in {"metadata", "both"}:
            result["recommended_datasets"] = recommended_datasets
            result["evidence"] = evidence
        if response_mode in {"data", "both"}:
            result["data_bundles"] = data_bundles
            result["data_errors"] = [
                copy.deepcopy(bundle.get("payload"))
                for bundle in data_bundles
                if isinstance(bundle.get("payload"), Mapping) and bundle.get("payload", {}).get("error")
            ]
            if data_bundles:
                result["data"] = copy.deepcopy(data_bundles[0].get("payload") or {})
        return result

    def fetch_dataset_payload(self, entry: Mapping[str, Any], params: Mapping[str, Any]) -> Dict[str, Any]:
        """Fetch source data for an entry. Subclasses can override this."""
        sample_data = entry.get("sample_data")
        if isinstance(sample_data, Mapping):
            return copy.deepcopy(dict(sample_data))
        return {}

    def build_data_bundle(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Build a normalized bundle with payload, evidence, lineage, and caveats."""
        entry = self.resolve_catalog_entry(input_data)
        if entry is None:
            return {"error": "No matching dataset found.", "input": dict(input_data)}
        params = input_data.get("params") if isinstance(input_data.get("params"), Mapping) else input_data
        source_payload = self.fetch_dataset_payload(entry, params)
        public_entry = self._public_catalog_entry(entry)
        return {
            "bundle": {
                "dataset": public_entry,
                "params": copy.deepcopy(dict(params or {})),
                "payload": source_payload,
                "evidence": self._evidence_for_entry(entry),
                "use_case": input_data.get("use_case") or "",
            }
        }

    def data_recommendation(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        """Return recommendation plus the selected bundle when possible."""
        recommendation = self.discover_use_case_data(input_data)["recommendation"]
        selected = recommendation.get("selected") if isinstance(recommendation.get("selected"), Mapping) else None
        if selected and selected.get("dataset_id"):
            bundle_input = {
                "dataset_id": selected["dataset_id"],
                "params": input_data.get("params") if isinstance(input_data.get("params"), Mapping) else {},
                "use_case": input_data.get("use_case") or "",
            }
            bundle = self.build_data_bundle(bundle_input)
            if "bundle" in bundle:
                recommendation["bundle"] = bundle["bundle"]
        return {"recommendation": recommendation}

    def fetch_pulse_payload(self, pulse_name: str, input_data: Dict[str, Any], pulse_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch generic Data Agent pulse payloads."""
        input_data = input_data or {}
        if pulse_name == "data_catalog_search":
            return self.search_catalog(input_data)
        if pulse_name == "data_definition":
            entry = self.resolve_catalog_entry(input_data) or {}
            fields = entry.get("fields") if isinstance(entry.get("fields"), Mapping) else {}
            field_name = str(input_data.get("field_name") or "").strip()
            return {
                "definition": {
                    "dataset": self._public_catalog_entry(entry) if entry else {},
                    "field_name": field_name,
                    "field": copy.deepcopy(fields.get(field_name) or {}) if field_name else {},
                    "methodology": entry.get("methodology") or "",
                    "caveats": list(entry.get("caveats") or []),
                }
            }
        if pulse_name == "data_source_connect":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {
                "connection": {
                    "dataset": self._public_catalog_entry(entry) if entry else {},
                    "source": entry.get("source") or entry.get("provider") or "",
                    "source_pulse_name": entry.get("source_pulse_name") or entry.get("pulse_name") or "",
                    "status": "ready" if entry else "not_found",
                }
            }
        if pulse_name == "identifier_resolve":
            query = str(input_data.get("query") or input_data.get("identifier") or "").strip().lower()
            items = []
            for entry in self.catalog_entries():
                identifiers = entry.get("identifiers") if isinstance(entry.get("identifiers"), list) else []
                for identifier in identifiers:
                    text = self._as_text(identifier)
                    if not query or query in text:
                        items.append(copy.deepcopy(identifier))
            return {"items": items, "count": len(items)}
        if pulse_name == "coverage_check":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {"coverage": copy.deepcopy(entry.get("coverage") or {})}
        if pulse_name == "entitlement_check":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {"entitlement": copy.deepcopy(entry.get("entitlement") or {"status": "unknown"})}
        if pulse_name == "freshness_check":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {"freshness": copy.deepcopy(entry.get("freshness") or {})}
        if pulse_name == "quality_profile":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {"quality": copy.deepcopy(entry.get("quality") or {})}
        if pulse_name == "alternative_data_choices":
            return self.rank_alternatives(input_data)
        if pulse_name == "use_case_data_discovery":
            return self.discover_use_case_data(input_data)
        if pulse_name == "data_use_case_query":
            return self.answer_use_case_query(input_data)
        if pulse_name == "data_bundle_build":
            return self.build_data_bundle(input_data)
        if pulse_name == "data_lineage":
            entry = self.resolve_catalog_entry(input_data) or {}
            return {"lineage": copy.deepcopy(entry.get("lineage") or {})}
        if pulse_name == "caveat_pack":
            entry = self.resolve_catalog_entry(input_data) or {}
            caveats = list(entry.get("caveats") or [])
            return {"caveats": caveats, "count": len(caveats)}
        if pulse_name == "data_recommendation":
            return self.data_recommendation(input_data)
        if pulse_name == "data_feedback_record":
            record = {
                "dataset_id": input_data.get("dataset_id") or "",
                "use_case": input_data.get("use_case") or "",
                "feedback": copy.deepcopy(input_data.get("feedback") or {}),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            self.feedback_records.append(record)
            return {"feedback": record}
        return {"error": f"Unsupported Data Agent pulse '{pulse_name}'.", "pulse_name": pulse_name}


__all__ = ["DATA_AGENT_PULSE_NAMES", "DEFAULT_SUPPORTED_PULSES", "DataAgentPulser"]
