"""Provider-neutral contracts for composing Phemars inside other Phemars.

The contract in this module belongs to Phemacast.  It deliberately describes
only generic discovery, trust, attachment, caching, and presentation concerns;
product-specific hosts can advertise additional compatible host contracts.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional
from urllib.parse import unquote, urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from prompits.core.message import Message
from prompits.core.practice import Practice


SUB_PHEMAR_HOST_CONTRACT = "phemacast.sub-phemar-host/v1"
SUB_PHEMAR_SPEC_VERSION = "1"
PHEMAR_FEATURE_CONTRACT = "phemacast.phemar-feature"
PHEMAR_FEATURE_VERSION = "1"
ACCEPT_SUB_PHEMAR_PULSE_NAME = "accept_sub_phemar"
REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS = (
    "identity",
    "compatibility",
    "sections",
    "supported_pulses",
    "ui",
    "permissions",
    "lifecycle",
)

SUB_PHEMAR_SECTION_KINDS = ("navigation", "sample", "workspace", "status", "action", "content")
SUB_PHEMAR_PLACEMENTS = ("sidebar", "top", "main", "inline")
SUB_PHEMAR_SECTION_VISIBILITIES = ("public", "admin")
SUB_PHEMAR_UI_SURFACE_KINDS = ("iframe", "web-component", "html", "link")
SUB_PHEMAR_SOURCE_MODES = ("parameters", "snapshot")
DEFAULT_SUB_PHEMAR_MAX_DEPTH = 4
DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS = 60
DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_NODES = 128
DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_EDGES = 256
DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_CHILDREN = 32
DEFAULT_SUB_PHEMAR_TOPOLOGY_TIMEOUT_SECONDS = 10
DEFAULT_SUB_PHEMAR_TOPOLOGY_RESPONSE_BYTES = 1_048_576
SYNTHESIZED_SUB_PHEMAR_CAPABILITY_PROVIDER = "phemacast.sub-phemar"
PHEMAR_HOST_CONTENT_CONTRACT = "phemacast.host-content/v1"
PHEMAR_HOST_CONTENT_VERSION = "1"
PHEMAR_HOST_CONTENT_MAX_ITEMS = 64
PHEMAR_HOST_CONTENT_MAX_SELECT_OPTIONS = 32
PHEMAR_HOST_CONTENT_SLOT_KINDS: dict[str, tuple[str, ...]] = {
    "shell.catalog-menu": ("link",),
    "shell.audience-menu": ("link",),
    "shell.help-menu": ("link",),
    "catalog.cards": ("card",),
    "catalog.routes": ("route",),
    "audience.routes": ("route",),
    "audience.sections": ("section",),
    "contact.interests": ("option",),
    "keys.actions": ("link",),
    "settings.panels": ("panel",),
    "settings.preferences": ("preference",),
    "page.regions": ("section",),
}
PHEMAR_HOST_CONTENT_VALUE_TYPES = ("boolean", "string", "number", "select")
PHEMAR_HOST_CONTENT_ITEM_FIELDS = (
    "id",
    "parent_id",
    "parent_route_id",
    "slot",
    "kind",
    "label",
    "description",
    "detail",
    "surface_id",
    "route_id",
    "region_id",
    "order",
    "icon",
    "tone",
    "value",
    "preference_key",
    "value_type",
    "default_value",
    "options",
)
_PORTABLE_HOST_CONTENT_ROUTE_ID = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$"
)
_PORTABLE_HOST_CONTENT_ITEM_ID = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$"
)
_PORTABLE_HOST_CONTENT_KEY = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"
)
_HOST_CONTENT_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
DERIVED_PROVIDER_URL_KEYS = {
    "url",
    "embed_url",
    "module_url",
    "style_url",
    "feature_endpoint",
    "descriptor_endpoint",
    "manifest_endpoint",
}


def _now_iso() -> str:
    """Return a UTC timestamp suitable for persisted attachment records."""
    return datetime.now(timezone.utc).isoformat()


def _copy(value: Any) -> Any:
    """Return a defensive copy of JSON-like data."""
    return copy.deepcopy(value)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge a configuration mapping without sharing mutable state."""
    merged = _copy(dict(base))
    for key, value in dict(override or {}).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = _copy(value)
    return merged


def _feature_digest(candidate: Any) -> str:
    """Return a deterministic digest for a submitted descriptor."""
    canonical = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _descriptor_integrity_value(value: Any) -> Any:
    """Strip request-origin decorations while preserving hosted behavior."""
    if isinstance(value, Mapping):
        return {
            str(key): _descriptor_integrity_value(item)
            for key, item in value.items()
            if str(key) not in DERIVED_PROVIDER_URL_KEYS
        }
    if isinstance(value, list):
        return [_descriptor_integrity_value(item) for item in value]
    return _copy(value)


def _descriptor_integrity_digest(candidate: Mapping[str, Any]) -> str:
    """Digest required hosted behavior while excluding derived absolute URLs."""
    projection = {
        key: _descriptor_integrity_value(candidate.get(key))
        for key in (
            "contract",
            "version",
            "id",
            "name",
            *REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS,
            "extensions",
        )
    }
    return _feature_digest(projection)


def _provider_bound_url(provider_origin: str, value: Any) -> str:
    """Bind a provider-owned URL hint to the verified Plaza origin."""
    text = str(value or "").strip()
    if not provider_origin or not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc:
        if SubPhemarHostRegistry._normalized_origin(text) != provider_origin:
            return ""
        relative = parsed.path or "/"
        if parsed.query:
            relative = f"{relative}?{parsed.query}"
    else:
        relative = text
    if not _safe_ui_path(relative):
        return ""
    if not relative.startswith("/"):
        relative = f"/{relative}"
    return f"{provider_origin}{relative}"


def _canonical_descriptor_for_storage(
    descriptor: Mapping[str, Any],
    provider_origin: str,
) -> dict[str, Any]:
    """Copy canonical content while rebinding derived URLs to a trusted origin."""

    def normalize(value: Any) -> Any:
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                if name in DERIVED_PROVIDER_URL_KEYS:
                    bound = _provider_bound_url(provider_origin, item)
                    if bound:
                        normalized[name] = bound
                    continue
                normalized[name] = normalize(item)
            return normalized
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return _copy(value)

    return normalize(descriptor)


def _phemar_host_content_schema() -> dict[str, Any]:
    """Return the provider-neutral hosted-content extension schema."""
    kinds = sorted(
        {
            kind
            for slot_kinds in PHEMAR_HOST_CONTENT_SLOT_KINDS.values()
            for kind in slot_kinds
        }
    )
    item_schema: dict[str, Any] = {
        "type": "object",
        "required": ["id", "slot", "kind", "label"],
        "properties": {
            "id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": _PORTABLE_HOST_CONTENT_ITEM_ID.pattern,
            },
            "parent_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": _PORTABLE_HOST_CONTENT_ITEM_ID.pattern,
            },
            "parent_route_id": {
                "type": "string",
                "maxLength": 64,
                "pattern": _PORTABLE_HOST_CONTENT_ROUTE_ID.pattern,
            },
            "slot": {"enum": list(PHEMAR_HOST_CONTENT_SLOT_KINDS)},
            "kind": {"enum": kinds},
            "label": {"type": "string", "minLength": 1, "maxLength": 160},
            "description": {"type": "string", "maxLength": 500},
            "detail": {"type": "string", "maxLength": 300},
            "surface_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "route_id": {
                "type": "string",
                "pattern": _PORTABLE_HOST_CONTENT_ROUTE_ID.pattern,
            },
            "region_id": {
                "type": "string",
                "pattern": _PORTABLE_HOST_CONTENT_ROUTE_ID.pattern,
            },
            "order": {"type": "number"},
            "icon": {
                "type": "string",
                "maxLength": 128,
                "pattern": _PORTABLE_HOST_CONTENT_KEY.pattern,
            },
            "tone": {
                "type": "string",
                "maxLength": 128,
                "pattern": _PORTABLE_HOST_CONTENT_KEY.pattern,
            },
            "value": {"type": "string", "minLength": 1, "maxLength": 160},
            "preference_key": {
                "type": "string",
                "pattern": _PORTABLE_HOST_CONTENT_KEY.pattern,
            },
            "value_type": {"enum": list(PHEMAR_HOST_CONTENT_VALUE_TYPES)},
            "default_value": {"type": ["boolean", "string", "number"]},
            "options": {
                "type": "array",
                "minItems": 1,
                "maxItems": PHEMAR_HOST_CONTENT_MAX_SELECT_OPTIONS,
                "items": {
                    "type": "object",
                    "required": ["value", "label"],
                    "properties": {
                        "value": {"type": "string", "minLength": 1, "maxLength": 128},
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": "route"}}, "required": ["kind"]},
                "then": {"required": ["route_id", "surface_id"]},
            },
            {
                "if": {
                    "properties": {"kind": {"enum": ["link", "card"]}},
                    "required": ["kind"],
                },
                "then": {"anyOf": [{"required": ["surface_id"]}, {"required": ["route_id"]}]},
            },
            {
                "if": {
                    "properties": {"kind": {"enum": ["panel", "section"]}},
                    "required": ["kind"],
                },
                "then": {"required": ["surface_id"]},
            },
            {
                "if": {"properties": {"kind": {"const": "option"}}, "required": ["kind"]},
                "then": {"required": ["value"]},
            },
            {
                "if": {
                    "properties": {"kind": {"const": "preference"}},
                    "required": ["kind"],
                },
                "then": {"required": ["preference_key", "value_type"]},
            },
            {
                "if": {
                    "properties": {"slot": {"const": "page.regions"}},
                    "required": ["slot"],
                },
                "then": {"required": ["region_id", "surface_id"]},
            },
            {
                "if": {"required": ["parent_id"]},
                "then": {
                    "properties": {
                        "slot": {"const": "shell.catalog-menu"},
                        "kind": {"const": "link"},
                    }
                },
            },
            {
                "if": {"required": ["parent_route_id"]},
                "then": {
                    "properties": {"slot": {"const": "shell.catalog-menu"}, "kind": {"const": "link"}},
                    "not": {"required": ["parent_id"]},
                },
            },
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["contract", "version", "endpoint", "pulse_name", "items"],
        "properties": {
            "contract": {"const": PHEMAR_HOST_CONTENT_CONTRACT},
            "version": {"const": PHEMAR_HOST_CONTENT_VERSION},
            "endpoint": {"type": "string", "pattern": "^/(?!/)"},
            "pulse_name": {"type": "string", "minLength": 1, "maxLength": 128},
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": PHEMAR_HOST_CONTENT_MAX_ITEMS,
                "items": item_schema,
            },
        },
        "additionalProperties": False,
    }


def build_phemar_host_content_spec() -> dict[str, Any]:
    """Describe portable host regions that an attached Phemar may contribute."""
    return {
        "contract": PHEMAR_HOST_CONTENT_CONTRACT,
        "version": PHEMAR_HOST_CONTENT_VERSION,
        "descriptor_extension": "extensions.host_content",
        "acceptance_pulse_name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
        "pulse_mode": "validation-only",
        "delivery": {
            "endpoint_field": "endpoint",
            "pulse_field": "pulse_name",
            "cache": "host keeps the last validated artifact while revalidating",
        },
        "max_items": PHEMAR_HOST_CONTENT_MAX_ITEMS,
        "slots": [
            {"id": slot, "kinds": list(kinds)}
            for slot, kinds in PHEMAR_HOST_CONTENT_SLOT_KINDS.items()
        ],
        "item_fields": list(PHEMAR_HOST_CONTENT_ITEM_FIELDS),
        "preference_value_types": list(PHEMAR_HOST_CONTENT_VALUE_TYPES),
        "schema": _phemar_host_content_schema(),
        "rules": {
            "endpoint": "safe same-origin relative path beginning with a single slash",
            "pulse_name": "must reference descriptor.supported_pulses[].pulse_name",
            "surface_id": "must reference descriptor.ui.surfaces[].id",
            "direct_links": "link and card items may target a declared surface directly",
            "route_aliases": "route items bind a unique child-scoped route_id to a declared surface",
            "route_references": "link and card route_id values must reference a route item in this extension",
            "catalog_menu_hierarchy": (
                "shell.catalog-menu links may declare a portable parent_id that references "
                "another shell.catalog-menu link in this extension; only one child level is allowed"
            ),
            "catalog_menu_parent_route": (
                "parent_route_id optionally groups a link under the unique root menu for that route "
                "among accepted sibling contributions, instead of parent_id; unresolved, ambiguous, "
                "or cyclic references remain flat and never grant provider access"
            ),
            "page_regions": "page.regions sections require a portable region_id and declared surface",
            "unique_ids": ["items.id", "route items.route_id", "preference items.preference_key"],
            "slot_kind_compatibility": {
                slot: list(kinds) for slot, kinds in PHEMAR_HOST_CONTENT_SLOT_KINDS.items()
            },
            "rendering": "all labels and descriptions are text; arbitrary markup and scripts are not accepted",
        },
    }


def _candidate_schema() -> dict[str, Any]:
    """Return the portable feature descriptor schema advertised by every host."""
    schema_object = {
        "type": "object",
        "required": ["type"],
        "properties": {"type": {"type": ["string", "array"]}},
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "required": [
            "contract",
            "version",
            "id",
            "name",
            *REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS,
        ],
        "properties": {
            "contract": {"const": PHEMAR_FEATURE_CONTRACT},
            "version": {"const": PHEMAR_FEATURE_VERSION},
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1},
            "identity": {
                "type": "object",
                "required": ["id", "name", "role", "agent_id"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "role": {"const": "phemar"},
                    "agent_id": {"type": "string", "minLength": 1},
                },
            },
            "compatibility": {
                "type": "object",
                "required": ["host_contracts"],
                "properties": {
                    "host_contracts": {
                        "type": "array",
                        "contains": {"const": SUB_PHEMAR_HOST_CONTRACT},
                    },
                    "source_modes": {
                        "type": "array",
                        "items": {"enum": list(SUB_PHEMAR_SOURCE_MODES)},
                        "uniqueItems": True,
                    },
                },
            },
            "sections": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id", "label", "kind", "placement"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "label": {"type": "string", "minLength": 1},
                        "kind": {"enum": list(SUB_PHEMAR_SECTION_KINDS)},
                        "placement": {"enum": list(SUB_PHEMAR_PLACEMENTS)},
                        "surface_id": {"type": "string", "minLength": 1},
                        "pulse_name": {"type": "string", "minLength": 1},
                        "visibility": {"enum": list(SUB_PHEMAR_SECTION_VISIBILITIES)},
                    },
                    "additionalProperties": True,
                },
            },
            "supported_pulses": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["name", "pulse_name", "input_schema", "output_schema"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "pulse_name": {"type": "string", "minLength": 1},
                        "input_schema": _copy(schema_object),
                        "output_schema": _copy(schema_object),
                    },
                    "additionalProperties": True,
                },
            },
            "ui": {
                "type": "object",
                "required": ["surfaces"],
                "properties": {
                    "surfaces": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id", "kind", "placement", "path"],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "kind": {"enum": list(SUB_PHEMAR_UI_SURFACE_KINDS)},
                                "placement": {"enum": list(SUB_PHEMAR_PLACEMENTS)},
                                "path": {"type": "string", "pattern": "^/(?!/)"},
                                "title": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    }
                },
            },
            "permissions": {"type": "object"},
            "lifecycle": {"type": "object"},
            "extensions": {
                "type": "object",
                "properties": {"host_content": _phemar_host_content_schema()},
                "additionalProperties": True,
            },
        },
        "additionalProperties": True,
    }


def build_sub_phemar_spec(overrides: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Build the inherited host contract, enforcing its non-negotiable safeguards."""
    defaults = {
        "contract": SUB_PHEMAR_HOST_CONTRACT,
        "version": SUB_PHEMAR_SPEC_VERSION,
        "accepts": {"contract": PHEMAR_FEATURE_CONTRACT, "version": PHEMAR_FEATURE_VERSION},
        "required_sections": list(REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS),
        "candidate_schema": _candidate_schema(),
        "host_content": build_phemar_host_content_spec(),
        "acceptance": {
            "pulse_name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
            "practice_id": ACCEPT_SUB_PHEMAR_PULSE_NAME,
            "pulse_mode": "validation-only",
            "http_path": "/api/sub-phemars/accept",
            "validation_path": "/api/sub-phemars/validate",
            "mutation_authorization": "host-admin",
        },
        "discovery": {
            "spec_path": "/api/sub-phemars/spec",
            "descriptor_path": "/api/sub-phemars/descriptor",
            "registry_path": "/api/sub-phemars",
            "manager_path": "/sub-phemars",
        },
        "trust": {
            "required": True,
            "source": "plaza",
            "mode": "explicit-server-verification",
            "status_path": "/api/plazas_status",
            "status_query": {"pit_type": "Phemar", "live_only": 1, "trusted_only": 1},
            "requirements": ["trusted", "live", "requestable", "matching_agent_identity"],
            "client_assertions_accepted": False,
            "host_admin_approval": {
                "durable": True,
                "viewer_token_persisted": False,
                "plaza_ui_initial_trust": "current-viewer-trusted-row",
                "direct_admin_initial_trust": "explicit-admin-approval-with-live-requestable-plaza-row",
                "revalidation_requires": [
                    "pinned_plaza_registration",
                    "pinned_provider_origin",
                    "live",
                    "requestable",
                    "matching_descriptor",
                    "matching_topology",
                ],
                "viewer_trust_required_after_approval": False,
            },
        },
        "storage": {
            "source_of_truth": "server",
            "registry_field": "phemar.sub_phemars",
            "browser_cache": "non-authoritative",
        },
        "cache": {
            "strategy": "stale-while-revalidate",
            "serve_last_known_good": True,
            "revalidate_after_seconds": DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS,
            "show_connection_errors_only_after_user_action": True,
            "background_revalidation_on_read": True,
            "manual_revalidation_path": "/api/sub-phemars/revalidate",
        },
        "safety": {
            "reject_self": True,
            "reject_cycles": True,
            "max_depth": DEFAULT_SUB_PHEMAR_MAX_DEPTH,
            "identity_fields": ["identity.agent_id", "identity.id"],
            "topology_source": "bounded-server-observed-provider-registries",
            "topology_fail_closed": True,
            "topology_max_nodes": DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_NODES,
            "topology_max_edges": DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_EDGES,
            "topology_max_children_per_node": DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_CHILDREN,
            "topology_timeout_seconds": DEFAULT_SUB_PHEMAR_TOPOLOGY_TIMEOUT_SECONDS,
        },
        "display": {
            "required_info": list(REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS),
        },
    }
    spec = _deep_merge(defaults, overrides or {})

    # Config may tighten these rules but must not turn them off.
    spec["contract"] = SUB_PHEMAR_HOST_CONTRACT
    spec["version"] = SUB_PHEMAR_SPEC_VERSION
    spec["accepts"] = {"contract": PHEMAR_FEATURE_CONTRACT, "version": PHEMAR_FEATURE_VERSION}
    spec["required_sections"] = list(REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS)
    spec["candidate_schema"] = _candidate_schema()
    spec["host_content"] = build_phemar_host_content_spec()
    spec.setdefault("acceptance", {}).update(
        {
            "pulse_name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
            "practice_id": ACCEPT_SUB_PHEMAR_PULSE_NAME,
            "pulse_mode": "validation-only",
            "http_path": "/api/sub-phemars/accept",
            "validation_path": "/api/sub-phemars/validate",
            "mutation_authorization": "host-admin",
        }
    )
    spec.setdefault("trust", {}).update(
        {
            "required": True,
            "source": "plaza",
            "mode": "explicit-server-verification",
            "client_assertions_accepted": False,
            "host_admin_approval": {
                "durable": True,
                "viewer_token_persisted": False,
                "plaza_ui_initial_trust": "current-viewer-trusted-row",
                "direct_admin_initial_trust": "explicit-admin-approval-with-live-requestable-plaza-row",
                "revalidation_requires": [
                    "pinned_plaza_registration",
                    "pinned_provider_origin",
                    "live",
                    "requestable",
                    "matching_descriptor",
                    "matching_topology",
                ],
                "viewer_trust_required_after_approval": False,
            },
        }
    )
    spec.setdefault("storage", {}).update(
        {"source_of_truth": "server", "browser_cache": "non-authoritative"}
    )
    spec.setdefault("cache", {}).update(
        {
            "strategy": "stale-while-revalidate",
            "serve_last_known_good": True,
            "background_revalidation_on_read": True,
            "manual_revalidation_path": "/api/sub-phemars/revalidate",
        }
    )
    safety = spec.setdefault("safety", {})
    safety.update(
        {
            "reject_self": True,
            "reject_cycles": True,
            "topology_source": "bounded-server-observed-provider-registries",
            "topology_fail_closed": True,
        }
    )
    try:
        configured_depth = int(safety.get("max_depth") or DEFAULT_SUB_PHEMAR_MAX_DEPTH)
    except (TypeError, ValueError):
        configured_depth = DEFAULT_SUB_PHEMAR_MAX_DEPTH
    safety["max_depth"] = max(1, min(configured_depth, 32))
    for key, default, maximum in (
        ("topology_max_nodes", DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_NODES, 512),
        ("topology_max_edges", DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_EDGES, 2048),
        ("topology_max_children_per_node", DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_CHILDREN, 128),
        ("topology_timeout_seconds", DEFAULT_SUB_PHEMAR_TOPOLOGY_TIMEOUT_SECONDS, 30),
    ):
        try:
            configured = int(safety.get(key) or default)
        except (TypeError, ValueError):
            configured = default
        safety[key] = max(1, min(configured, maximum))
    spec.setdefault("display", {})["required_info"] = list(REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS)
    return spec


def accept_sub_phemar_supported_pulse(spec: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Return the typed pulse definition inherited by every Phemar."""
    contract = build_sub_phemar_spec(spec)
    return {
        "id": ACCEPT_SUB_PHEMAR_PULSE_NAME,
        "name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
        "pulse_name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
        "pulse_address": f"plaza://pulse/{ACCEPT_SUB_PHEMAR_PULSE_NAME}",
        "practice_id": ACCEPT_SUB_PHEMAR_PULSE_NAME,
        "description": "Describe the host contract and validate a Phemar feature descriptor without mutating attachments.",
        "tags": ["phemar", "sub-phemar", "composition", "plaza-trusted"],
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate": _copy(contract["candidate_schema"]),
                "provider_agent_id": {
                    "type": "string",
                    "description": "Plaza registration id when it differs from the descriptor's stable logical id.",
                },
                "provider_address": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["contract", "accepted", "attached", "issues", "digest"],
            "properties": {
                "contract": {"const": SUB_PHEMAR_HOST_CONTRACT},
                "accepted": {"type": ["boolean", "null"]},
                "attached": {"type": "boolean"},
                "issues": {"type": "array"},
                "digest": {"type": ["string", "null"]},
                "attachment": {"type": ["object", "null"]},
            },
        },
    }


def merge_sub_phemar_supported_pulses(*groups: Any, spec: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
    """Merge pulse collections while guaranteeing the generic acceptance pulse."""
    merged: list[dict[str, Any]] = []
    by_name: dict[str, int] = {}
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        for entry in group:
            if not isinstance(entry, Mapping):
                continue
            pulse = _copy(dict(entry))
            name = str(pulse.get("pulse_name") or pulse.get("name") or pulse.get("id") or "").strip()
            if not name:
                continue
            pulse.setdefault("pulse_name", name)
            pulse.setdefault("name", name)
            if name in by_name:
                merged[by_name[name]] = pulse
            else:
                by_name[name] = len(merged)
                merged.append(pulse)
    generic = accept_sub_phemar_supported_pulse(spec)
    if ACCEPT_SUB_PHEMAR_PULSE_NAME in by_name:
        merged[by_name[ACCEPT_SUB_PHEMAR_PULSE_NAME]] = generic
    else:
        merged.append(generic)
    return merged


def _slug(value: Any) -> str:
    """Return a stable display identifier without imposing product vocabulary."""
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    return "-".join(part for part in text.split("-") if part) or "phemar"


def _mapping(value: Any) -> dict[str, Any]:
    return _copy(dict(value)) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_copy(dict(entry)) for entry in value if isinstance(entry, Mapping)]


def _is_owned_synthesized_capability(value: Any) -> bool:
    """Return whether a capability is the fallback owned by this module."""
    return (
        isinstance(value, Mapping)
        and value.get("synthesized") is True
        and value.get("provider") == SYNTHESIZED_SUB_PHEMAR_CAPABILITY_PROVIDER
        and value.get("feature_endpoint") == "/api/sub-phemars/descriptor"
    )


def _merge_unique_rows(groups: list[list[dict[str, Any]]], *keys: str) -> list[dict[str, Any]]:
    """Merge descriptor rows by the first available identity key."""
    rows: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for group in groups:
        for entry in group:
            identity = next((str(entry.get(key) or "").strip() for key in keys if str(entry.get(key) or "").strip()), "")
            if not identity:
                identity = _feature_digest(entry)
            if identity in indexes:
                combined = dict(rows[indexes[identity]])
                combined.update(_copy(entry))
                rows[indexes[identity]] = combined
            else:
                indexes[identity] = len(rows)
                rows.append(_copy(entry))
    return rows


def build_phemar_feature_descriptor(service: Any) -> dict[str, Any]:
    """Build one complete attachable descriptor from a live Phemar service."""
    config = _mapping(getattr(service, "config", None))
    card = _mapping(getattr(service, "agent_card", None))
    meta = _mapping(card.get("meta"))
    capabilities = [
        capability
        for capability in _list_of_mappings(meta.get("phemar_capabilities"))
        if capability.get("contract") == PHEMAR_FEATURE_CONTRACT
        and not _is_owned_synthesized_capability(capability)
    ]

    name = str(getattr(service, "name", "") or card.get("name") or config.get("name") or "Phemar").strip()
    agent_id = str(getattr(service, "agent_id", "") or card.get("agent_id") or _slug(name)).strip()
    configured_identity = _mapping(config.get("identity"))
    identity = {
        **configured_identity,
        "id": str(configured_identity.get("id") or config.get("id") or agent_id or _slug(name)),
        "name": str(configured_identity.get("name") or name),
        "role": "phemar",
        "agent_id": str(configured_identity.get("agent_id") or agent_id),
    }

    compatibility = _mapping(config.get("compatibility"))
    host_contracts: list[str] = []
    source_modes: list[str] = []
    for source in [compatibility, *[_mapping(capability.get("compatibility")) for capability in capabilities]]:
        for contract in source.get("host_contracts") or []:
            normalized = str(contract or "").strip()
            if normalized and normalized not in host_contracts:
                host_contracts.append(normalized)
        for mode in source.get("source_modes") or []:
            normalized = str(mode or "").strip()
            if normalized and normalized not in source_modes:
                source_modes.append(normalized)
    if SUB_PHEMAR_HOST_CONTRACT not in host_contracts:
        host_contracts.insert(0, SUB_PHEMAR_HOST_CONTRACT)
    if not source_modes:
        source_modes = list(SUB_PHEMAR_SOURCE_MODES)
    compatibility.update({"host_contracts": host_contracts, "source_modes": source_modes})

    configured_sections = _list_of_mappings(config.get("host_sections"))
    capability_sections = [_list_of_mappings(capability.get("sections")) for capability in capabilities]
    sections = _merge_unique_rows([configured_sections, *capability_sections], "id")

    configured_surfaces = _list_of_mappings(config.get("ui_surfaces"))
    capability_surfaces = [
        _list_of_mappings(_mapping(capability.get("ui")).get("surfaces"))
        for capability in capabilities
    ]
    surfaces = _merge_unique_rows([configured_surfaces, *capability_surfaces], "id")
    if not surfaces:
        display = _mapping(meta.get("display_server"))
        display_path = str(display.get("path") or "/sub-phemars").strip() or "/sub-phemars"
        surfaces = [
            {
                "id": "phemar-workspace",
                "kind": "iframe",
                "placement": "main",
                "path": display_path,
                "title": name,
                "presentation": {"chrome": "host", "border": "host", "sizing": "viewport"},
            }
        ]
    if not sections:
        sections = [
            {
                "id": "phemar-workspace",
                "label": name,
                "kind": "workspace",
                "placement": "main",
                "surface_id": surfaces[0]["id"],
            }
        ]

    service_pulses = getattr(service, "supported_pulses", None)
    capability_pulses = [_list_of_mappings(capability.get("supported_pulses")) for capability in capabilities]
    pulses = merge_sub_phemar_supported_pulses(
        _list_of_mappings(meta.get("supported_pulses")),
        _list_of_mappings(service_pulses),
        *capability_pulses,
        spec=getattr(service, "sub_phemar_spec", None),
    )

    permissions = _mapping(config.get("permissions"))
    lifecycle = _mapping(config.get("lifecycle"))
    extensions: dict[str, Any] = {}
    for capability in capabilities:
        if not permissions:
            permissions = _mapping(capability.get("permissions"))
        if not lifecycle:
            lifecycle = _mapping(capability.get("lifecycle"))
        capability_extensions = _mapping(capability.get("extensions"))
        if capability_extensions:
            extensions = _deep_merge(extensions, capability_extensions)
    configured_extensions = _mapping(config.get("extensions"))
    if configured_extensions:
        extensions = _deep_merge(extensions, configured_extensions)
    permissions.setdefault("attachment_trust", "plaza")
    lifecycle.setdefault("attachment_depth", int(config.get("sub_phemar_depth") or 0))
    lifecycle.setdefault("ancestry", list(config.get("sub_phemar_parent_chain") or []))
    lifecycle.setdefault("cache_strategy", "stale-while-revalidate")
    registry = getattr(service, "_sub_phemar_registry", None)
    attached_children: list[dict[str, str]] = []
    descendants: list[dict[str, str]] = []
    if isinstance(registry, SubPhemarHostRegistry):
        for record in registry.list():
            child_identity = _mapping(record.get("child"))
            child_row = {
                "id": str(child_identity.get("id") or ""),
                "agent_id": str(child_identity.get("agent_id") or ""),
            }
            if child_row["id"] or child_row["agent_id"]:
                attached_children.append(child_row)
                descendants.append(child_row)
            verified_topology = _mapping(record.get("topology"))
            child_lifecycle = _mapping(_mapping(record.get("descriptor")).get("lifecycle"))
            topology_descendants = (
                verified_topology.get("descendants")
                if verified_topology.get("verified") is True
                else child_lifecycle.get("descendants")
                or child_lifecycle.get("attached_children")
                or []
            )
            for descendant in topology_descendants:
                if not isinstance(descendant, Mapping):
                    continue
                descendant_row = {
                    "id": str(descendant.get("id") or ""),
                    "agent_id": str(descendant.get("agent_id") or ""),
                }
                if descendant_row not in descendants and (descendant_row["id"] or descendant_row["agent_id"]):
                    descendants.append(descendant_row)
    lifecycle["attached_children"] = attached_children
    lifecycle["descendants"] = descendants

    descriptor = {
        "contract": PHEMAR_FEATURE_CONTRACT,
        "version": PHEMAR_FEATURE_VERSION,
        "id": str(config.get("feature_id") or identity["id"] or _slug(name)),
        "name": name,
        "description": str(config.get("description") or card.get("description") or ""),
        "identity": identity,
        "compatibility": compatibility,
        "sections": sections,
        "supported_pulses": pulses,
        "ui": {"surfaces": surfaces},
        "permissions": permissions,
        "lifecycle": lifecycle,
    }
    if capabilities:
        descriptor["features"] = capabilities
    if extensions:
        descriptor["extensions"] = extensions
    return descriptor


def _issue(issues: list[dict[str, str]], code: str, path: str, message: str) -> None:
    issues.append({"code": code, "path": path, "message": message})


def _required_text(document: Mapping[str, Any], field: str, path: str, issues: list[dict[str, str]]) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        _issue(issues, "required_text", f"{path}.{field}", "A non-empty string is required.")
        return ""
    return value.strip()


def _required_mapping(
    document: Mapping[str, Any], field: str, path: str, issues: list[dict[str, str]]
) -> Optional[Mapping[str, Any]]:
    value = document.get(field)
    if not isinstance(value, Mapping):
        _issue(issues, "required_object", f"{path}.{field}", "A JSON object is required.")
        return None
    return value


def _required_list(
    document: Mapping[str, Any], field: str, path: str, issues: list[dict[str, str]]
) -> Optional[list[Any]]:
    value = document.get(field)
    if not isinstance(value, list) or not value:
        _issue(issues, "required_list", f"{path}.{field}", "A non-empty array is required.")
        return None
    return value


def _unique(value: str, *, path: str, seen: set[str], issues: list[dict[str, str]]) -> None:
    if not value:
        return
    if value in seen:
        _issue(issues, "duplicate_id", path, f"Identifier '{value}' must be unique.")
    else:
        seen.add(value)


def _safe_ui_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    if any(character in value for character in ("\r", "\n", "\t")):
        return False
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    decoded = unquote(parsed.path)
    return "\\" not in decoded and all(segment not in {".", ".."} for segment in decoded.split("/"))


def _host_content_text(
    document: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[dict[str, str]],
    *,
    required: bool = False,
    max_length: int = 500,
) -> str:
    """Read one bounded, text-only host-content value."""
    if field not in document:
        if required:
            _issue(issues, "required_text", f"{path}.{field}", "A non-empty string is required.")
        return ""
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        _issue(
            issues,
            "required_text" if required else "host_content_text",
            f"{path}.{field}",
            "A non-empty string is required." if required else "The value must be a non-empty string.",
        )
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        _issue(
            issues,
            "host_content_length",
            f"{path}.{field}",
            f"The value must contain at most {max_length} characters.",
        )
    if _HOST_CONTENT_CONTROL_CHARACTERS.search(normalized):
        _issue(
            issues,
            "host_content_control",
            f"{path}.{field}",
            "Control characters are not allowed.",
        )
    return normalized


def _descriptor_pulse_names(descriptor: Mapping[str, Any]) -> set[str]:
    pulses = descriptor.get("supported_pulses")
    if not isinstance(pulses, list):
        return set()
    return {
        str(pulse.get("pulse_name")).strip()
        for pulse in pulses
        if isinstance(pulse, Mapping)
        and isinstance(pulse.get("pulse_name"), str)
        and str(pulse.get("pulse_name")).strip()
    }


def _descriptor_surface_ids(descriptor: Mapping[str, Any]) -> set[str]:
    ui = descriptor.get("ui")
    if not isinstance(ui, Mapping) or not isinstance(ui.get("surfaces"), list):
        return set()
    return {
        str(surface.get("id")).strip()
        for surface in ui["surfaces"]
        if isinstance(surface, Mapping)
        and isinstance(surface.get("id"), str)
        and str(surface.get("id")).strip()
    }


def _finite_json_number(value: Any) -> bool:
    """Return whether a value is a finite JSON-style number without int overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)


def _validate_host_content_default(
    item: Mapping[str, Any],
    *,
    value_type: str,
    option_values: set[str],
    path: str,
    issues: list[dict[str, str]],
) -> None:
    """Validate a preference default against its declared portable value type."""
    if "default_value" not in item:
        return
    value = item.get("default_value")
    valid = False
    if value_type == "boolean":
        valid = isinstance(value, bool)
    elif value_type == "string":
        valid = isinstance(value, str) and len(value) <= 500 and not _HOST_CONTENT_CONTROL_CHARACTERS.search(value)
    elif value_type == "number":
        valid = _finite_json_number(value)
    elif value_type == "select":
        valid = isinstance(value, str) and value in option_values
    if not valid:
        _issue(
            issues,
            "host_content_preference_default",
            f"{path}.default_value",
            "The default value must match value_type and, for select, one declared option.",
        )


def _validate_host_content_preference_options(
    item: Mapping[str, Any],
    *,
    value_type: str,
    path: str,
    issues: list[dict[str, str]],
) -> set[str]:
    """Validate bounded select options and return their normalized values."""
    if value_type != "select":
        if "options" in item:
            _issue(
                issues,
                "host_content_preference_options",
                f"{path}.options",
                "Options are only allowed when value_type is 'select'.",
            )
        return set()
    options = item.get("options")
    if not isinstance(options, list) or not options:
        _issue(
            issues,
            "host_content_preference_options",
            f"{path}.options",
            "A select preference requires a non-empty options array.",
        )
        return set()
    if len(options) > PHEMAR_HOST_CONTENT_MAX_SELECT_OPTIONS:
        _issue(
            issues,
            "host_content_preference_option_limit",
            f"{path}.options",
            f"At most {PHEMAR_HOST_CONTENT_MAX_SELECT_OPTIONS} options are allowed.",
        )
    values: set[str] = set()
    for index, option in enumerate(options[:PHEMAR_HOST_CONTENT_MAX_SELECT_OPTIONS]):
        option_path = f"{path}.options[{index}]"
        if not isinstance(option, Mapping):
            _issue(
                issues,
                "host_content_preference_option_type",
                option_path,
                "Each select option must be a JSON object.",
            )
            continue
        for field in sorted(set(option) - {"value", "label"}, key=str):
            _issue(
                issues,
                "host_content_preference_option_property",
                f"{option_path}.{field}",
                "Unsupported select-option property.",
            )
        value = _host_content_text(
            option,
            "value",
            option_path,
            issues,
            required=True,
            max_length=128,
        )
        _host_content_text(
            option,
            "label",
            option_path,
            issues,
            required=True,
            max_length=160,
        )
        _unique(value, path=f"{option_path}.value", seen=values, issues=issues)
    return values


def validate_phemar_host_content(candidate: Any) -> dict[str, Any]:
    """Validate an optional portable hosted-content extension.

    The complete Phemar descriptor is required so pulse and UI-surface
    references are checked in the same trust decision as the attachment.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(candidate, Mapping):
        _issue(issues, "candidate_type", "$", "A Phemar feature descriptor is required.")
        return {
            "accepted": False,
            "present": False,
            "issues": issues,
            "digest": _feature_digest(candidate),
        }
    descriptor = dict(candidate)
    extensions = descriptor.get("extensions")
    if extensions is None:
        return {
            "accepted": True,
            "present": False,
            "issues": [],
            "digest": _feature_digest(candidate),
        }
    if not isinstance(extensions, Mapping):
        _issue(issues, "extensions_type", "$.extensions", "Descriptor extensions must be a JSON object.")
        return {
            "accepted": False,
            "present": False,
            "issues": issues,
            "digest": _feature_digest(candidate),
        }
    if "host_content" not in extensions:
        return {
            "accepted": True,
            "present": False,
            "issues": [],
            "digest": _feature_digest(candidate),
        }

    extension_path = "$.extensions.host_content"
    extension = extensions.get("host_content")
    if not isinstance(extension, Mapping):
        _issue(
            issues,
            "host_content_type",
            extension_path,
            "Hosted content must be a JSON object.",
        )
        return {
            "accepted": False,
            "present": True,
            "issues": issues,
            "digest": _feature_digest(extension),
        }
    content = dict(extension)
    for field in sorted(
        set(content) - {"contract", "version", "endpoint", "pulse_name", "items"},
        key=str,
    ):
        _issue(
            issues,
            "host_content_property",
            f"{extension_path}.{field}",
            "Unsupported hosted-content property.",
        )

    contract = _host_content_text(
        content,
        "contract",
        extension_path,
        issues,
        required=True,
        max_length=80,
    )
    if contract and contract != PHEMAR_HOST_CONTENT_CONTRACT:
        _issue(
            issues,
            "unsupported_host_content_contract",
            f"{extension_path}.contract",
            f"Expected '{PHEMAR_HOST_CONTENT_CONTRACT}'.",
        )
    version = _host_content_text(
        content,
        "version",
        extension_path,
        issues,
        required=True,
        max_length=16,
    )
    if version and version != PHEMAR_HOST_CONTENT_VERSION:
        _issue(
            issues,
            "unsupported_host_content_version",
            f"{extension_path}.version",
            f"Expected version '{PHEMAR_HOST_CONTENT_VERSION}'.",
        )
    endpoint = _host_content_text(
        content,
        "endpoint",
        extension_path,
        issues,
        required=True,
        max_length=500,
    )
    if endpoint and not _safe_ui_path(endpoint):
        _issue(
            issues,
            "unsafe_host_content_endpoint",
            f"{extension_path}.endpoint",
            "The endpoint must be a safe same-origin relative path beginning with a single slash.",
        )
    pulse_name = _host_content_text(
        content,
        "pulse_name",
        extension_path,
        issues,
        required=True,
        max_length=128,
    )
    if pulse_name and pulse_name not in _descriptor_pulse_names(descriptor):
        _issue(
            issues,
            "host_content_pulse_reference",
            f"{extension_path}.pulse_name",
            "The referenced supported pulse does not exist.",
        )

    items = content.get("items")
    if not isinstance(items, list) or not items:
        _issue(
            issues,
            "host_content_items",
            f"{extension_path}.items",
            "A non-empty array is required.",
        )
        items = []
    elif len(items) > PHEMAR_HOST_CONTENT_MAX_ITEMS:
        _issue(
            issues,
            "host_content_item_limit",
            f"{extension_path}.items",
            f"At most {PHEMAR_HOST_CONTENT_MAX_ITEMS} items are allowed.",
        )

    declared_route_ids = {
        str(item.get("route_id")).strip()
        for item in items[:PHEMAR_HOST_CONTENT_MAX_ITEMS]
        if isinstance(item, Mapping)
        and item.get("kind") == "route"
        and isinstance(item.get("route_id"), str)
        and _PORTABLE_HOST_CONTENT_ROUTE_ID.fullmatch(str(item.get("route_id")).strip())
    }
    surface_ids = _descriptor_surface_ids(descriptor)
    item_ids: set[str] = set()
    route_ids: set[str] = set()
    preference_keys: set[str] = set()
    contact_values: set[str] = set()
    known_item_fields = set(PHEMAR_HOST_CONTENT_ITEM_FIELDS)
    known_kinds = {
        kind
        for slot_kinds in PHEMAR_HOST_CONTENT_SLOT_KINDS.values()
        for kind in slot_kinds
    }
    item_records: list[dict[str, Any]] = []
    for index, raw_item in enumerate(items[:PHEMAR_HOST_CONTENT_MAX_ITEMS]):
        path = f"{extension_path}.items[{index}]"
        if not isinstance(raw_item, Mapping):
            _issue(
                issues,
                "host_content_item_type",
                path,
                "Each content item must be a JSON object.",
            )
            continue
        item = dict(raw_item)
        for field in sorted(set(item) - known_item_fields, key=str):
            _issue(
                issues,
                "host_content_item_property",
                f"{path}.{field}",
                "Unsupported hosted-content item property.",
            )

        item_id = _host_content_text(item, "id", path, issues, required=True, max_length=128)
        if item_id:
            if not _PORTABLE_HOST_CONTENT_ITEM_ID.fullmatch(item_id):
                _issue(
                    issues,
                    "host_content_item_id",
                    f"{path}.id",
                    "The item id must be portable.",
                )
            _unique(item_id, path=f"{path}.id", seen=item_ids, issues=issues)
        slot = _host_content_text(item, "slot", path, issues, required=True, max_length=80)
        kind = _host_content_text(item, "kind", path, issues, required=True, max_length=32)
        allowed_kinds = PHEMAR_HOST_CONTENT_SLOT_KINDS.get(slot) if slot else None
        if slot and allowed_kinds is None:
            _issue(
                issues,
                "host_content_slot",
                f"{path}.slot",
                f"Allowed slots: {', '.join(PHEMAR_HOST_CONTENT_SLOT_KINDS)}.",
            )
        if kind and kind not in known_kinds:
            _issue(
                issues,
                "host_content_kind",
                f"{path}.kind",
                f"Allowed kinds: {', '.join(sorted(known_kinds))}.",
            )
        elif kind and allowed_kinds is not None and kind not in allowed_kinds:
            _issue(
                issues,
                "host_content_slot_kind",
                f"{path}.kind",
                f"Kind '{kind}' is not allowed in slot '{slot}'.",
            )

        parent_id = _host_content_text(
            item,
            "parent_id",
            path,
            issues,
            max_length=128,
        )
        parent_route_id = _host_content_text(item, "parent_route_id", path, issues, max_length=64)
        if parent_route_id:
            if not _PORTABLE_HOST_CONTENT_ROUTE_ID.fullmatch(parent_route_id):
                _issue(issues, "host_content_parent_route", f"{path}.parent_route_id", "The parent route must be portable.")
            if slot != "shell.catalog-menu" or kind != "link" or parent_id:
                _issue(issues, "host_content_parent_scope", f"{path}.parent_route_id", "Use parent_route_id only on catalog-menu links, without parent_id.")
            if parent_route_id == item.get("route_id"):
                _issue(issues, "host_content_parent_self", f"{path}.parent_route_id", "A menu link cannot be its own parent route.")
        if parent_id:
            if not _PORTABLE_HOST_CONTENT_ITEM_ID.fullmatch(parent_id):
                _issue(
                    issues,
                    "host_content_parent_id",
                    f"{path}.parent_id",
                    "The parent id must be portable.",
                )
            if slot != "shell.catalog-menu" or kind != "link":
                _issue(
                    issues,
                    "host_content_parent_scope",
                    f"{path}.parent_id",
                    "parent_id is only allowed on shell.catalog-menu link items.",
                )
        item_records.append(
            {
                "id": item_id,
                "parent_id": parent_id,
                "parent_route_id": parent_route_id,
                "path": path,
                "slot": slot,
                "kind": kind,
            }
        )

        _host_content_text(item, "label", path, issues, required=True, max_length=160)
        _host_content_text(item, "description", path, issues, max_length=500)
        _host_content_text(item, "detail", path, issues, max_length=300)
        for field in ("icon", "tone"):
            token = _host_content_text(item, field, path, issues, max_length=128)
            if token and not _PORTABLE_HOST_CONTENT_KEY.fullmatch(token):
                _issue(
                    issues,
                    "host_content_style_token",
                    f"{path}.{field}",
                    "Display tokens must be portable lowercase identifiers.",
                )
        if "order" in item:
            order = item.get("order")
            if not _finite_json_number(order):
                _issue(
                    issues,
                    "host_content_order",
                    f"{path}.order",
                    "Order must be a finite number.",
                )

        route_required = kind == "route"
        route_id = _host_content_text(
            item,
            "route_id",
            path,
            issues,
            required=route_required,
            max_length=64,
        )
        if route_id:
            if not _PORTABLE_HOST_CONTENT_ROUTE_ID.fullmatch(route_id):
                _issue(
                    issues,
                    "host_content_route_id",
                    f"{path}.route_id",
                    "Route id must be a portable lowercase identifier.",
                )
            if kind == "route":
                _unique(route_id, path=f"{path}.route_id", seen=route_ids, issues=issues)
            elif kind in {"link", "card"}:
                if route_id not in declared_route_ids:
                    _issue(
                        issues,
                        "host_content_route_reference",
                        f"{path}.route_id",
                        "The referenced route is not declared by this extension.",
                    )
            else:
                _issue(
                    issues,
                    "host_content_kind_field",
                    f"{path}.route_id",
                    "route_id is only allowed on route, link, and card items.",
                )

        surface_required = kind in {"route", "panel", "section"}
        surface_id = _host_content_text(
            item,
            "surface_id",
            path,
            issues,
            required=surface_required,
            max_length=128,
        )
        if surface_id:
            if kind not in {"link", "card", "route", "panel", "section"}:
                _issue(
                    issues,
                    "host_content_kind_field",
                    f"{path}.surface_id",
                    "This item kind cannot target a UI surface.",
                )
            elif surface_id not in surface_ids:
                _issue(
                    issues,
                    "host_content_surface_reference",
                    f"{path}.surface_id",
                    "The referenced UI surface does not exist.",
                )
        if kind in {"link", "card"} and not surface_id and not route_id:
            _issue(
                issues,
                "host_content_target",
                path,
                "Link and card items require a surface_id or declared route_id target.",
            )

        region_required = slot == "page.regions"
        region_id = _host_content_text(
            item,
            "region_id",
            path,
            issues,
            required=region_required,
            max_length=64,
        )
        if region_id:
            if not _PORTABLE_HOST_CONTENT_ROUTE_ID.fullmatch(region_id):
                _issue(
                    issues,
                    "host_content_region_id",
                    f"{path}.region_id",
                    "Region id must be a portable lowercase identifier.",
                )
            if not region_required:
                _issue(
                    issues,
                    "host_content_kind_field",
                    f"{path}.region_id",
                    "region_id is only allowed in the page.regions slot.",
                )

        if kind == "option":
            value = _host_content_text(
                item,
                "value",
                path,
                issues,
                required=True,
                max_length=160,
            )
            _unique(value, path=f"{path}.value", seen=contact_values, issues=issues)
        elif "value" in item:
            _issue(
                issues,
                "host_content_kind_field",
                f"{path}.value",
                "value is only allowed on option items.",
            )

        preference_fields = {"preference_key", "value_type", "default_value", "options"}
        if kind == "preference":
            preference_key = _host_content_text(
                item,
                "preference_key",
                path,
                issues,
                required=True,
                max_length=128,
            )
            if preference_key:
                if not _PORTABLE_HOST_CONTENT_KEY.fullmatch(preference_key):
                    _issue(
                        issues,
                        "host_content_preference_key",
                        f"{path}.preference_key",
                        "Preference key must be a portable lowercase identifier.",
                    )
                _unique(
                    preference_key,
                    path=f"{path}.preference_key",
                    seen=preference_keys,
                    issues=issues,
                )
            value_type = _host_content_text(
                item,
                "value_type",
                path,
                issues,
                required=True,
                max_length=32,
            )
            if value_type and value_type not in PHEMAR_HOST_CONTENT_VALUE_TYPES:
                _issue(
                    issues,
                    "host_content_preference_type",
                    f"{path}.value_type",
                    f"Allowed value types: {', '.join(PHEMAR_HOST_CONTENT_VALUE_TYPES)}.",
                )
            option_values = _validate_host_content_preference_options(
                item,
                value_type=value_type,
                path=path,
                issues=issues,
            )
            _validate_host_content_default(
                item,
                value_type=value_type,
                option_values=option_values,
                path=path,
                issues=issues,
            )
        else:
            for field in sorted(preference_fields & set(item)):
                _issue(
                    issues,
                    "host_content_kind_field",
                    f"{path}.{field}",
                    f"{field} is only allowed on preference items.",
                )

    catalog_links: dict[str, dict[str, Any]] = {}
    parent_by_id: dict[str, str] = {}
    for record in item_records:
        item_id = record["id"]
        if (
            item_id
            and _PORTABLE_HOST_CONTENT_ITEM_ID.fullmatch(item_id)
            and record["slot"] == "shell.catalog-menu"
            and record["kind"] == "link"
        ):
            catalog_links.setdefault(item_id, record)
            parent_id = record["parent_id"]
            if parent_id and _PORTABLE_HOST_CONTENT_ITEM_ID.fullmatch(parent_id):
                parent_by_id.setdefault(item_id, parent_id)

    cyclic_item_ids: set[str] = set()
    for start_id in parent_by_id:
        chain: list[str] = []
        positions: dict[str, int] = {}
        current_id = start_id
        while current_id in parent_by_id:
            if current_id in positions:
                cycle = chain[positions[current_id] :]
                if len(cycle) > 1:
                    cyclic_item_ids.update(cycle)
                break
            positions[current_id] = len(chain)
            chain.append(current_id)
            current_id = parent_by_id[current_id]

    for record in item_records:
        item_id = record["id"]
        parent_id = record["parent_id"]
        if (
            not item_id
            or not parent_id
            or record["slot"] != "shell.catalog-menu"
            or record["kind"] != "link"
            or not _PORTABLE_HOST_CONTENT_ITEM_ID.fullmatch(parent_id)
        ):
            continue
        parent_path = f"{record['path']}.parent_id"
        if parent_id == item_id:
            _issue(
                issues,
                "host_content_parent_self",
                parent_path,
                "A catalog-menu link cannot be its own parent.",
            )
            continue
        parent = catalog_links.get(parent_id)
        if parent is None:
            _issue(
                issues,
                "host_content_parent_reference",
                parent_path,
                "The referenced parent must be another shell.catalog-menu link in this extension.",
            )
            continue
        if item_id in cyclic_item_ids:
            _issue(
                issues,
                "host_content_parent_cycle",
                parent_path,
                "Catalog-menu parent relationships cannot form a cycle.",
            )
        elif parent.get("parent_id") or parent.get("parent_route_id"):
            _issue(
                issues,
                "host_content_parent_nested",
                parent_path,
                "Only one catalog-menu child level is allowed; a parent cannot have a parent_id.",
            )

    return {
        "accepted": not issues,
        "present": True,
        "issues": issues,
        "digest": _feature_digest(content),
    }


def _identity_tokens(identity: Any) -> set[str]:
    if isinstance(identity, Mapping):
        values = (identity.get("agent_id"), identity.get("id"))
    elif isinstance(identity, (list, tuple, set)):
        tokens: set[str] = set()
        for entry in identity:
            tokens.update(_identity_tokens(entry))
        return tokens
    else:
        values = (identity,)
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def validate_sub_phemar_descriptor(
    candidate: Any,
    *,
    parent_identity: Any = None,
    parent_ancestry: Any = None,
    max_depth: int = DEFAULT_SUB_PHEMAR_MAX_DEPTH,
    requested_depth: Optional[int] = None,
) -> dict[str, Any]:
    """Validate one child descriptor and enforce generic composition safety."""
    issues: list[dict[str, str]] = []
    digest = _feature_digest(candidate)
    if not isinstance(candidate, Mapping):
        _issue(issues, "candidate_type", "$", "The candidate must be a JSON object.")
        return {"accepted": False, "issues": issues, "digest": digest, "descriptor": None}
    descriptor = _copy(dict(candidate))
    contract = _required_text(descriptor, "contract", "$", issues)
    if contract and contract != PHEMAR_FEATURE_CONTRACT:
        _issue(issues, "unsupported_contract", "$.contract", f"Expected '{PHEMAR_FEATURE_CONTRACT}'.")
    version = _required_text(descriptor, "version", "$", issues)
    if version and version != PHEMAR_FEATURE_VERSION:
        _issue(issues, "unsupported_version", "$.version", f"Expected version '{PHEMAR_FEATURE_VERSION}'.")
    _required_text(descriptor, "id", "$", issues)
    _required_text(descriptor, "name", "$", issues)

    identity = _required_mapping(descriptor, "identity", "$", issues)
    child_tokens: set[str] = set()
    if identity is not None:
        _required_text(identity, "id", "$.identity", issues)
        _required_text(identity, "name", "$.identity", issues)
        role = _required_text(identity, "role", "$.identity", issues)
        _required_text(identity, "agent_id", "$.identity", issues)
        if role and role.lower() != "phemar":
            _issue(issues, "identity_role", "$.identity.role", "The role must be 'phemar'.")
        child_tokens = _identity_tokens(identity)

    compatibility = _required_mapping(descriptor, "compatibility", "$", issues)
    if compatibility is not None:
        host_contracts = _required_list(compatibility, "host_contracts", "$.compatibility", issues)
        if host_contracts is not None:
            normalized = [str(value).strip() for value in host_contracts if isinstance(value, str) and value.strip()]
            if len(normalized) != len(host_contracts):
                _issue(issues, "host_contract_type", "$.compatibility.host_contracts", "Every host contract must be a non-empty string.")
            if SUB_PHEMAR_HOST_CONTRACT not in normalized:
                _issue(issues, "incompatible_host", "$.compatibility.host_contracts", f"The candidate must declare '{SUB_PHEMAR_HOST_CONTRACT}'.")

    pulses = _required_list(descriptor, "supported_pulses", "$", issues)
    pulse_names: set[str] = set()
    if pulses is not None:
        for index, pulse in enumerate(pulses):
            path = f"$.supported_pulses[{index}]"
            if not isinstance(pulse, Mapping):
                _issue(issues, "pulse_type", path, "Each supported pulse must be a JSON object.")
                continue
            _required_text(pulse, "name", path, issues)
            pulse_name = _required_text(pulse, "pulse_name", path, issues)
            _unique(pulse_name, path=f"{path}.pulse_name", seen=pulse_names, issues=issues)
            for schema_field in ("input_schema", "output_schema"):
                schema = _required_mapping(pulse, schema_field, path, issues)
                if schema is not None and not schema.get("type"):
                    _issue(issues, "schema_type", f"{path}.{schema_field}.type", "The schema must declare a type.")

    ui = _required_mapping(descriptor, "ui", "$", issues)
    surfaces: Optional[list[Any]] = None
    surface_ids: set[str] = set()
    if ui is not None:
        surfaces = _required_list(ui, "surfaces", "$.ui", issues)
        if surfaces is not None:
            for index, surface in enumerate(surfaces):
                path = f"$.ui.surfaces[{index}]"
                if not isinstance(surface, Mapping):
                    _issue(issues, "surface_type", path, "Each UI surface must be a JSON object.")
                    continue
                surface_id = _required_text(surface, "id", path, issues)
                _unique(surface_id, path=f"{path}.id", seen=surface_ids, issues=issues)
                kind = _required_text(surface, "kind", path, issues)
                if kind and kind not in SUB_PHEMAR_UI_SURFACE_KINDS:
                    _issue(issues, "surface_kind", f"{path}.kind", f"Allowed kinds: {', '.join(SUB_PHEMAR_UI_SURFACE_KINDS)}.")
                placement = _required_text(surface, "placement", path, issues)
                if placement and placement not in SUB_PHEMAR_PLACEMENTS:
                    _issue(issues, "placement", f"{path}.placement", f"Allowed placements: {', '.join(SUB_PHEMAR_PLACEMENTS)}.")
                if not any(isinstance(surface.get(key), str) and surface.get(key).strip() for key in ("title", "label")):
                    _issue(issues, "surface_label", path, "A non-empty title or label is required.")
                if not _safe_ui_path(surface.get("path")):
                    _issue(issues, "unsafe_ui_path", f"{path}.path", "UI paths must be safe same-origin relative paths.")

    sections = _required_list(descriptor, "sections", "$", issues)
    section_ids: set[str] = set()
    if sections is not None:
        for index, section in enumerate(sections):
            path = f"$.sections[{index}]"
            if not isinstance(section, Mapping):
                _issue(issues, "section_type", path, "Each section must be a JSON object.")
                continue
            section_id = _required_text(section, "id", path, issues)
            _unique(section_id, path=f"{path}.id", seen=section_ids, issues=issues)
            _required_text(section, "label", path, issues)
            kind = _required_text(section, "kind", path, issues)
            if kind and kind not in SUB_PHEMAR_SECTION_KINDS:
                _issue(issues, "section_kind", f"{path}.kind", f"Allowed kinds: {', '.join(SUB_PHEMAR_SECTION_KINDS)}.")
            placement = _required_text(section, "placement", path, issues)
            if placement and placement not in SUB_PHEMAR_PLACEMENTS:
                _issue(issues, "placement", f"{path}.placement", f"Allowed placements: {', '.join(SUB_PHEMAR_PLACEMENTS)}.")
            visibility = section.get("visibility")
            if visibility is not None and visibility not in SUB_PHEMAR_SECTION_VISIBILITIES:
                _issue(
                    issues,
                    "section_visibility",
                    f"{path}.visibility",
                    f"Allowed visibility values: {', '.join(SUB_PHEMAR_SECTION_VISIBILITIES)}.",
                )
            surface_id = section.get("surface_id")
            if surface_id is not None and (not isinstance(surface_id, str) or not surface_id.strip() or (surfaces is not None and surface_id not in surface_ids)):
                _issue(issues, "surface_reference", f"{path}.surface_id", "The referenced UI surface does not exist.")
            pulse_name = section.get("pulse_name")
            if pulse_name is not None and (not isinstance(pulse_name, str) or not pulse_name.strip() or (pulses is not None and pulse_name not in pulse_names)):
                _issue(issues, "pulse_reference", f"{path}.pulse_name", "The referenced supported pulse does not exist.")

    host_content = validate_phemar_host_content(descriptor)
    issues.extend(_copy(host_content["issues"]))
    _required_mapping(descriptor, "permissions", "$", issues)
    lifecycle = _required_mapping(descriptor, "lifecycle", "$", issues)

    parent_tokens = _identity_tokens(parent_identity)
    ancestor_tokens = _identity_tokens(parent_ancestry)
    if child_tokens & parent_tokens:
        _issue(issues, "self_attachment", "$.identity", "A Phemar cannot attach itself.")
    if child_tokens & ancestor_tokens:
        _issue(issues, "attachment_cycle", "$.identity", "The child is already an ancestor of this Phemar.")
    if lifecycle is not None:
        child_ancestry = _identity_tokens(lifecycle.get("ancestry"))
        if parent_tokens & child_ancestry:
            _issue(issues, "attachment_cycle", "$.lifecycle.ancestry", "The child ancestry already contains this Phemar.")
        attached_children = lifecycle.get("attached_children")
        descendants = lifecycle.get("descendants")
        child_descendants = _identity_tokens(
            [
                *(attached_children if isinstance(attached_children, list) else []),
                *(descendants if isinstance(descendants, list) else []),
            ]
        )
        if parent_tokens & child_descendants:
            _issue(
                issues,
                "attachment_cycle",
                "$.lifecycle.descendants",
                "The candidate already contains this Phemar in its attached descendants.",
            )

    try:
        depth = int(requested_depth if requested_depth is not None else 1)
    except (TypeError, ValueError):
        depth = 0
    if depth < 1:
        _issue(issues, "attachment_depth", "$.lifecycle.attachment_depth", "Attachment depth must be a positive integer.")
    if depth > max_depth:
        _issue(issues, "max_depth", "$.lifecycle.attachment_depth", f"Attachment depth exceeds the host limit of {max_depth}.")

    return {
        "accepted": not issues,
        "issues": issues,
        "digest": digest,
        "descriptor": descriptor if not issues else None,
        "depth": depth,
    }


def validate_sub_phemar_trust(candidate: Any, trust: Any) -> dict[str, Any]:
    """Validate Plaza evidence already produced by the host server.

    This helper never establishes trust by itself.  Attachment code must first
    obtain the evidence from Plaza rather than accepting a caller assertion.
    """
    issues: list[dict[str, str]] = []
    identity = candidate.get("identity") if isinstance(candidate, Mapping) and isinstance(candidate.get("identity"), Mapping) else {}
    child_agent_id = str(identity.get("agent_id") or "").strip()
    if not isinstance(trust, Mapping):
        _issue(issues, "plaza_trust_required", "$.trust", "Explicit Plaza trust evidence is required.")
        return {"trusted": False, "issues": issues, "trust": None}
    normalized = _copy(dict(trust))
    if str(normalized.get("source") or "").strip().lower() != "plaza":
        _issue(issues, "trust_source", "$.trust.source", "Trust must come from Plaza.")
    if normalized.get("verified") is not True:
        _issue(issues, "trust_verification", "$.trust.verified", "Plaza verification must be explicit.")
    trust_agent_id = str(normalized.get("agent_id") or "").strip()
    if not trust_agent_id:
        _issue(issues, "trust_agent_id", "$.trust.agent_id", "The verified Plaza agent id is required.")
    elif child_agent_id and trust_agent_id != child_agent_id:
        _issue(issues, "trust_identity_mismatch", "$.trust.agent_id", "The trusted agent id does not match the child descriptor.")
    if not str(normalized.get("plaza_url") or "").strip():
        _issue(issues, "trust_plaza", "$.trust.plaza_url", "The verifying Plaza URL is required.")
    normalized.setdefault("verified_at", _now_iso())
    return {"trusted": not issues, "issues": issues, "trust": normalized if not issues else None}


def _candidate_from_input(input_data: Any) -> tuple[bool, Any]:
    if not isinstance(input_data, Mapping):
        return (input_data is not None, input_data)
    for key in ("candidate", "descriptor", "phemar"):
        if key in input_data:
            return True, input_data.get(key)
    if input_data.get("contract") == PHEMAR_FEATURE_CONTRACT:
        return True, input_data
    return False, None


class SubPhemarHostRegistry:
    """Server-side attachment registry and contract facade for one Phemar."""

    def __init__(self, service: Any, config: Optional[Mapping[str, Any]] = None):
        self.service = service
        self._lock = threading.RLock()
        self._revalidation_inflight: set[str] = set()
        self.spec = build_sub_phemar_spec()
        self._records: dict[str, dict[str, Any]] = {}
        self.apply_config(config)

    def apply_config(self, config: Optional[Mapping[str, Any]]) -> None:
        """Reload the authoritative host spec and attachment registry from config."""
        config_map = _mapping(config)
        if isinstance(config_map.get("phemar"), Mapping):
            config_map = _mapping(config_map.get("phemar"))
        spec = build_sub_phemar_spec(config_map.get("sub_phemar_spec"))
        raw_records = (
            config_map.get("sub_phemars")
            if "sub_phemars" in config_map
            else config_map.get("attached_sub_phemars")
        ) or []
        records: dict[str, dict[str, Any]] = {}
        for raw_record in raw_records if isinstance(raw_records, list) else []:
            normalized = self._normalize_existing_record(raw_record)
            if normalized:
                records[normalized["attachment_id"]] = normalized
        with self._lock:
            self.spec = spec
            self._records = records

    def _normalize_existing_record(self, raw_record: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw_record, Mapping):
            return None
        record = _copy(dict(raw_record))
        descriptor = record.get("descriptor") if isinstance(record.get("descriptor"), Mapping) else record
        identity = descriptor.get("identity") if isinstance(descriptor, Mapping) and isinstance(descriptor.get("identity"), Mapping) else {}
        key = str(record.get("attachment_id") or identity.get("agent_id") or identity.get("id") or descriptor.get("id") or "").strip()
        if not key:
            return None
        attachment_id = str(record.get("attachment_id") or f"sub-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}")
        now = _now_iso()
        return {
            **record,
            "attachment_id": attachment_id,
            "descriptor": _copy(dict(descriptor)),
            "status": str(record.get("status") or "attached"),
            "attached_at": str(record.get("attached_at") or now),
            "updated_at": str(record.get("updated_at") or record.get("attached_at") or now),
        }

    def descriptor(self) -> dict[str, Any]:
        return build_phemar_feature_descriptor(self.service)

    def _parent_identity(self) -> dict[str, Any]:
        return _mapping(self.descriptor().get("identity"))

    def _parent_lifecycle(self) -> dict[str, Any]:
        return _mapping(self.descriptor().get("lifecycle"))

    def validate(self, input_data: Any = None) -> dict[str, Any]:
        provided, candidate = _candidate_from_input(input_data)
        response: dict[str, Any] = {
            "status": "success",
            "pulse_name": ACCEPT_SUB_PHEMAR_PULSE_NAME,
            "contract": SUB_PHEMAR_HOST_CONTRACT,
            "version": SUB_PHEMAR_SPEC_VERSION,
            "spec": _copy(self.spec),
            "accepted": None,
            "attached": False,
            "issues": [],
            "digest": None,
            "attachment": None,
        }
        if not provided:
            return response
        lifecycle = self._parent_lifecycle()
        parent_depth = int(lifecycle.get("attachment_depth") or 0)
        requested_depth = parent_depth + 1
        validation = validate_sub_phemar_descriptor(
            candidate,
            parent_identity=self._parent_identity(),
            parent_ancestry=lifecycle.get("ancestry") or [],
            max_depth=int(self.spec["safety"]["max_depth"]),
            requested_depth=requested_depth,
        )
        response.update(
            {
                "accepted": validation["accepted"],
                "issues": validation["issues"],
                "digest": validation["digest"],
                "descriptor": validation.get("descriptor"),
                "depth": validation.get("depth"),
            }
        )
        return response

    @staticmethod
    def _row_mapping(value: Any) -> dict[str, Any]:
        return _copy(dict(value)) if isinstance(value, Mapping) else {}

    @classmethod
    def _row_text(cls, row: Mapping[str, Any], *keys: str) -> str:
        card = cls._row_mapping(row.get("card"))
        for key in keys:
            for source in (row, card):
                text = str(source.get(key) or "").strip()
                if text:
                    return text
        return ""

    @classmethod
    def _row_boolean(cls, row: Mapping[str, Any], *keys: str) -> bool:
        card = cls._row_mapping(row.get("card"))
        return any(source.get(key) is True for key in keys for source in (row, card))

    @classmethod
    def _is_trusted_status_row(cls, row: Mapping[str, Any]) -> bool:
        trust_level = cls._row_text(row, "trust_level", "trustLevel").lower()
        return cls._row_boolean(row, "trusted") or trust_level in {"trusted", "approved"}

    @classmethod
    def _is_live_status_row(cls, row: Mapping[str, Any]) -> bool:
        availability = cls._row_text(row, "availability").lower()
        return availability == "online" or cls._row_boolean(row, "live", "online")

    @classmethod
    def _is_requestable_status_row(cls, row: Mapping[str, Any]) -> bool:
        return cls._row_boolean(row, "remote_practice_available", "remotePracticeAvailable")

    @classmethod
    def _status_row_agent_id(cls, row: Mapping[str, Any]) -> str:
        return cls._row_text(row, "agent_id", "id")

    @classmethod
    def _status_row_address(cls, row: Mapping[str, Any]) -> str:
        return cls._row_text(row, "address")

    @classmethod
    def _status_row_is_phemar(cls, row: Mapping[str, Any]) -> bool:
        return cls._row_text(row, "pit_type", "pitType", "type").lower() == "phemar"

    @staticmethod
    def _normalized_origin(value: Any) -> str:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    class _ResponseSizeLimitError(ValueError):
        """Raised before decoding a provider response that exceeds its cap."""

    @classmethod
    def _bounded_response_json(
        cls,
        response: Any,
        *,
        max_bytes: int = DEFAULT_SUB_PHEMAR_TOPOLOGY_RESPONSE_BYTES,
        deadline: Optional[float] = None,
    ) -> Any:
        def check_deadline() -> None:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Response exceeded the topology deadline.")

        check_deadline()
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            try:
                declared = int(headers.get("content-length") or 0)
            except (TypeError, ValueError):
                declared = 0
            if declared > max_bytes:
                raise cls._ResponseSizeLimitError("Response exceeded the size limit.")
        iterator = getattr(response, "iter_content", None)
        if callable(iterator):
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in iterator(chunk_size=65_536):
                    check_deadline()
                    if not chunk:
                        continue
                    encoded = bytes(chunk)
                    size += len(encoded)
                    if size > max_bytes:
                        raise cls._ResponseSizeLimitError(
                            "Response exceeded the size limit."
                        )
                    chunks.append(encoded)
                    check_deadline()
                check_deadline()
                payload = json.loads(b"".join(chunks).decode("utf-8"))
                check_deadline()
                return payload
            finally:
                closer = getattr(response, "close", None)
                if callable(closer):
                    closer()
        payload = response.json() if getattr(response, "content", True) else {}
        check_deadline()
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > max_bytes:
            raise cls._ResponseSizeLimitError("Response exceeded the size limit.")
        return payload

    @staticmethod
    def _close_response(response: Any) -> None:
        closer = getattr(response, "close", None)
        if callable(closer):
            closer()

    @classmethod
    def _status_row_capabilities(cls, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        card = cls._row_mapping(row.get("card"))
        meta = cls._row_mapping(row.get("meta"))
        card_meta = cls._row_mapping(card.get("meta"))
        for value in (
            card_meta.get("phemar_capabilities"),
            meta.get("phemar_capabilities"),
            card.get("phemar_capabilities"),
            row.get("phemar_capabilities"),
        ):
            capabilities = _list_of_mappings(value)
            if capabilities:
                return capabilities
        return []

    @staticmethod
    def _capability_identity_matches(capability: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
        if capability.get("contract") != PHEMAR_FEATURE_CONTRACT:
            return False
        compatibility = _mapping(capability.get("compatibility"))
        if SUB_PHEMAR_HOST_CONTRACT not in compatibility.get("host_contracts", []):
            return False
        if str(capability.get("id") or "").strip() != str(candidate.get("id") or "").strip():
            return False
        candidate_identity = _mapping(candidate.get("identity"))
        capability_identity = _mapping(capability.get("identity"))
        for field in ("id", "agent_id"):
            candidate_value = str(candidate_identity.get(field) or "").strip()
            capability_value = str(capability_identity.get(field) or "").strip()
            if not candidate_value or not capability_value or candidate_value != capability_value:
                return False
        return True

    @classmethod
    def _capability_advertisement_matches(
        cls,
        capability: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> bool:
        """Select a full capability or an exact-id endpoint advertisement safely."""
        if capability.get("contract") != PHEMAR_FEATURE_CONTRACT:
            return False
        compatibility = _mapping(capability.get("compatibility"))
        if SUB_PHEMAR_HOST_CONTRACT not in compatibility.get("host_contracts", []):
            return False
        identity = _mapping(capability.get("identity"))
        has_identity = bool(identity.get("id") or identity.get("agent_id"))
        if has_identity:
            return cls._capability_identity_matches(capability, candidate)
        return (
            str(capability.get("id") or "").strip() == str(candidate.get("id") or "").strip()
            and bool(capability.get("feature_endpoint") or capability.get("descriptor_endpoint"))
        )

    @staticmethod
    def _complete_feature_descriptor(value: Any) -> Optional[dict[str, Any]]:
        if not isinstance(value, Mapping):
            return None
        descriptor = _copy(dict(value))
        required = {
            "contract",
            "version",
            "id",
            "name",
            *REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS,
        }
        return descriptor if required.issubset(descriptor) else None

    @classmethod
    def _descriptor_from_provider_payload(
        cls,
        payload: Any,
        candidate: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return None
        for key in ("descriptor", "feature"):
            descriptor = cls._complete_feature_descriptor(payload.get(key))
            if descriptor is not None and cls._capability_identity_matches(descriptor, candidate):
                return descriptor
        direct = cls._complete_feature_descriptor(payload)
        if direct is not None and cls._capability_identity_matches(direct, candidate):
            return direct
        for capability in _list_of_mappings(payload.get("phemar_capabilities")):
            descriptor = cls._complete_feature_descriptor(capability)
            if descriptor is not None and cls._capability_identity_matches(descriptor, candidate):
                return descriptor
        return None

    @classmethod
    def _provider_descriptor_url(cls, provider_address: str, endpoint: Any) -> str:
        address = str(provider_address or "").strip().rstrip("/")
        provider_origin = cls._normalized_origin(address)
        endpoint_text = str(endpoint or "").strip()
        if not provider_origin or not endpoint_text:
            return ""
        parsed = urlsplit(endpoint_text)
        if parsed.scheme or parsed.netloc:
            if cls._normalized_origin(endpoint_text) != provider_origin:
                return ""
            relative = parsed.path or "/"
            if parsed.query:
                relative = f"{relative}?{parsed.query}"
        else:
            relative = endpoint_text
        if not _safe_ui_path(relative):
            return ""
        if not relative.startswith("/"):
            relative = f"/{relative}"
        return f"{provider_origin}{relative}"

    def _fetch_provider_descriptor(
        self,
        provider_address: str,
        capability: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        deadline: Optional[float] = None,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        endpoint = capability.get("feature_endpoint") or capability.get("descriptor_endpoint")
        url = self._provider_descriptor_url(provider_address, endpoint)
        if not url:
            return None, {
                "code": "plaza_descriptor_mismatch",
                "message": "The advertised descriptor endpoint is missing or unsafe.",
                "transient": False,
            }
        request_get = getattr(self.service, "_sub_phemar_request_get", None) or requests.get
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, {
                    "code": "provider_descriptor_unavailable",
                    "message": "Provider descriptor verification timed out.",
                    "transient": True,
                }
            request_timeout = max(0.1, min(3.0, remaining))
        else:
            request_timeout = 10
        try:
            response = request_get(
                url,
                headers={"Accept": "application/json"},
                timeout=request_timeout,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(getattr(response, "status_code", 500) or 500)
            if status_code in {408, 425, 429} or status_code >= 500:
                self._close_response(response)
                return None, {
                    "code": "provider_descriptor_unavailable",
                    "message": f"Provider descriptor is temporarily unavailable (HTTP {status_code}).",
                    "transient": True,
                }
            if status_code >= 300:
                self._close_response(response)
                return None, {
                    "code": "plaza_descriptor_mismatch",
                    "message": f"Provider descriptor returned HTTP {status_code}.",
                    "transient": False,
                }
            payload = self._bounded_response_json(response, deadline=deadline)
        except self._ResponseSizeLimitError as exc:
            return None, {
                "code": "provider_descriptor_unavailable",
                "message": str(exc),
                "transient": True,
            }
        except Exception as exc:
            return None, {
                "code": "provider_descriptor_unavailable",
                "message": f"Provider descriptor fetch failed: {exc}",
                "transient": True,
            }
        descriptor = self._descriptor_from_provider_payload(payload, candidate)
        if descriptor is None:
            return None, {
                "code": "plaza_descriptor_mismatch",
                "message": "Provider descriptor response did not contain the matching complete feature.",
                "transient": False,
            }
        return descriptor, None

    def _canonical_descriptor_match(
        self,
        candidate: Mapping[str, Any],
        capabilities: list[dict[str, Any]],
        provider_address: str,
        *,
        allow_verified_update: bool = False,
    ) -> tuple[Optional[dict[str, Any]], str, Optional[dict[str, Any]]]:
        candidate_digest = _descriptor_integrity_digest(candidate)
        matching = [
            capability
            for capability in capabilities
            if self._capability_advertisement_matches(capability, candidate)
        ]
        if not matching:
            return None, "", {
                "code": "plaza_descriptor_mismatch",
                "message": "The trusted Plaza row does not advertise the candidate's Phemar identity.",
                "transient": False,
            }
        fetch_issues: list[dict[str, Any]] = []
        confirmed_mismatch = False
        for capability in matching:
            embedded = self._complete_feature_descriptor(capability)
            if embedded is not None:
                if (
                    allow_verified_update
                    or _descriptor_integrity_digest(embedded) == candidate_digest
                ):
                    return embedded, "plaza-embedded", None
                confirmed_mismatch = True
            fetched, fetch_issue = self._fetch_provider_descriptor(provider_address, capability, candidate)
            if fetched is not None:
                if (
                    allow_verified_update
                    or _descriptor_integrity_digest(fetched) == candidate_digest
                ):
                    return fetched, "provider-endpoint", None
                confirmed_mismatch = True
            if fetch_issue:
                fetch_issues.append(fetch_issue)
                if fetch_issue.get("transient") is not True:
                    confirmed_mismatch = True
        if confirmed_mismatch:
            details = [
                str(issue.get("message") or "")
                for issue in fetch_issues
                if issue.get("transient") is not True
            ]
            return None, "", {
                "code": "plaza_descriptor_mismatch",
                "message": "; ".join(filter(None, details))
                or "Candidate content differs from the provider's canonical descriptor.",
                "transient": False,
            }
        transient_details = [str(issue.get("message") or "") for issue in fetch_issues]
        return None, "", {
            "code": "provider_descriptor_unavailable",
            "message": "; ".join(filter(None, transient_details))
            or "The provider descriptor is temporarily unavailable.",
            "transient": True,
        }

    def _trusted_status_directory(
        self,
        payload: Mapping[str, Any],
        *,
        require_current_trust: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Index Plaza-observed Phemars suitable for topology verification.

        A durable host-admin approval pins the accepted graph. Revalidation of
        that graph still requires live, requestable registrations at their
        observed origins, but does not require a viewer-scoped trust result on
        the host agent's own Plaza token.
        """
        directory: dict[str, list[dict[str, Any]]] = {}
        for plaza in payload.get("plazas") or []:
            if not isinstance(plaza, Mapping):
                continue
            for row in plaza.get("agents") or []:
                if not isinstance(row, Mapping) or not self._status_row_is_phemar(row):
                    continue
                provider_agent_id = self._status_row_agent_id(row)
                provider_origin = self._normalized_origin(self._status_row_address(row))
                if (
                    not provider_agent_id
                    or not provider_origin
                    or (require_current_trust and not self._is_trusted_status_row(row))
                    or not self._is_live_status_row(row)
                    or not self._is_requestable_status_row(row)
                ):
                    continue
                directory.setdefault(provider_agent_id, []).append(
                    {
                        "provider_agent_id": provider_agent_id,
                        "provider_origin": provider_origin,
                        "plaza": _copy(dict(plaza)),
                        "row": _copy(dict(row)),
                    }
                )
        return directory

    def _current_canonical_descriptor(
        self,
        descriptor_hint: Mapping[str, Any],
        row: Mapping[str, Any],
        provider_origin: str,
        *,
        deadline: float,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Resolve current provider content without trusting a cached registry copy."""
        if time.monotonic() >= deadline:
            return None, {
                "code": "sub_phemar_topology_unavailable",
                "message": "Sub-Phemar topology verification timed out.",
            }
        capabilities = self._status_row_capabilities(row)
        matching = [
            capability
            for capability in capabilities
            if self._capability_advertisement_matches(capability, descriptor_hint)
        ]
        if not matching:
            return None, {
                "code": "sub_phemar_topology_mismatch",
                "message": "A descendant is no longer advertised by its trusted Plaza row.",
            }
        transient_issues: list[dict[str, Any]] = []
        for capability in matching:
            embedded = self._complete_feature_descriptor(capability)
            if embedded is not None and self._capability_identity_matches(
                embedded,
                descriptor_hint,
            ):
                return embedded, None
            fetched, fetch_issue = self._fetch_provider_descriptor(
                provider_origin,
                capability,
                descriptor_hint,
                deadline=deadline,
            )
            if fetched is not None:
                return fetched, None
            if fetch_issue and fetch_issue.get("transient") is True:
                transient_issues.append(fetch_issue)
        if transient_issues:
            return None, {
                "code": "sub_phemar_topology_unavailable",
                "message": "; ".join(
                    str(issue.get("message") or "") for issue in transient_issues
                ),
            }
        return None, {
            "code": "sub_phemar_topology_mismatch",
            "message": "A descendant's current canonical descriptor is invalid or mismatched.",
        }

    def _fetch_observed_registry(
        self,
        provider_origin: str,
        *,
        deadline: float,
    ) -> tuple[Optional[list[dict[str, Any]]], Optional[dict[str, Any]]]:
        """Fetch the fixed registry route from a Plaza-observed provider origin."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, {
                "code": "sub_phemar_topology_unavailable",
                "message": "Sub-Phemar topology verification timed out.",
            }
        request_get = getattr(self.service, "_sub_phemar_request_get", None) or requests.get
        try:
            response = request_get(
                f"{provider_origin}/api/sub-phemars",
                headers={"Accept": "application/json"},
                timeout=max(0.1, min(3.0, remaining)),
                allow_redirects=False,
                stream=True,
            )
            status_code = int(getattr(response, "status_code", 500) or 500)
            if status_code in {408, 425, 429} or status_code >= 500:
                self._close_response(response)
                return None, {
                    "code": "sub_phemar_topology_unavailable",
                    "message": f"A descendant registry is temporarily unavailable (HTTP {status_code}).",
                }
            if status_code >= 300:
                self._close_response(response)
                return None, {
                    "code": "sub_phemar_topology_invalid",
                    "message": f"A descendant registry returned HTTP {status_code}.",
                }
            payload = self._bounded_response_json(response, deadline=deadline)
        except self._ResponseSizeLimitError:
            return None, {
                "code": "sub_phemar_topology_limit_exceeded",
                "message": "A descendant registry response exceeded the size limit.",
            }
        except Exception as exc:
            return None, {
                "code": "sub_phemar_topology_unavailable",
                "message": f"A descendant registry could not be reached: {exc}",
            }
        if not isinstance(payload, Mapping) or not isinstance(payload.get("sub_phemars"), list):
            return None, {
                "code": "sub_phemar_topology_invalid",
                "message": "A descendant registry returned an invalid document.",
            }
        records = [
            _copy(dict(record))
            for record in payload.get("sub_phemars") or []
            if isinstance(record, Mapping)
        ]
        if len(records) != len(payload.get("sub_phemars") or []):
            return None, {
                "code": "sub_phemar_topology_invalid",
                "message": "A descendant registry contains an invalid attachment record.",
            }
        return records, None

    def _resolve_topology_child(
        self,
        record: Mapping[str, Any],
        directory: Mapping[str, list[dict[str, Any]]],
        *,
        deadline: float,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        descriptor_hint = _mapping(record.get("descriptor"))
        identity_hint = _mapping(record.get("child")) or _mapping(
            descriptor_hint.get("identity")
        )
        trust_hint = _mapping(record.get("trust"))
        provider_agent_id = str(
            trust_hint.get("provider_agent_id")
            or identity_hint.get("agent_id")
            or ""
        ).strip()
        if not provider_agent_id or not descriptor_hint:
            return None, {
                "code": "sub_phemar_topology_invalid",
                "message": "A descendant registry record lacks identity or provider information.",
            }
        matches = list(directory.get(provider_agent_id) or [])
        if len(matches) != 1:
            return None, {
                "code": "sub_phemar_topology_unavailable"
                if not matches
                else "sub_phemar_topology_ambiguous",
                "message": "A descendant could not be resolved uniquely in the trusted Plaza snapshot.",
            }
        observed = matches[0]
        canonical, issue = self._current_canonical_descriptor(
            descriptor_hint,
            _mapping(observed.get("row")),
            str(observed.get("provider_origin") or ""),
            deadline=deadline,
        )
        if canonical is None:
            return None, issue
        canonical_identity = _mapping(canonical.get("identity"))
        for field in ("id", "agent_id"):
            hinted = str(identity_hint.get(field) or "").strip()
            current = str(canonical_identity.get(field) or "").strip()
            if hinted and hinted != current:
                return None, {
                    "code": "sub_phemar_topology_mismatch",
                    "message": "A descendant registry identity differs from its canonical descriptor.",
                }
        origin = str(observed.get("provider_origin") or "")
        return {
            "key": provider_agent_id,
            "provider_agent_id": provider_agent_id,
            "provider_origin": origin,
            "identity": canonical_identity,
            "descriptor": _canonical_descriptor_for_storage(canonical, origin),
        }, None

    def _verify_recursive_topology(
        self,
        root_node: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        require_current_trust: bool = True,
    ) -> dict[str, Any]:
        """Verify the live descendant DAG through Plaza-observed fixed origins."""
        directory = self._trusted_status_directory(
            payload,
            require_current_trust=require_current_trust,
        )
        safety = _mapping(self.spec.get("safety"))
        max_nodes = int(safety.get("topology_max_nodes") or DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_NODES)
        max_edges = int(safety.get("topology_max_edges") or DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_EDGES)
        max_children = int(
            safety.get("topology_max_children_per_node")
            or DEFAULT_SUB_PHEMAR_TOPOLOGY_MAX_CHILDREN
        )
        timeout_seconds = int(
            safety.get("topology_timeout_seconds")
            or DEFAULT_SUB_PHEMAR_TOPOLOGY_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + timeout_seconds
        parent_tokens = _identity_tokens(self._parent_identity())
        colors: dict[str, str] = {}
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, list[str]] = {}
        finished: list[str] = []
        issues: list[dict[str, str]] = []
        edge_count = 0

        def fail(code: str, message: str) -> bool:
            _issue(issues, code, "$.topology", message)
            return False

        def visit(raw_node: Mapping[str, Any]) -> bool:
            nonlocal edge_count
            if time.monotonic() >= deadline:
                return fail(
                    "sub_phemar_topology_unavailable",
                    "Sub-Phemar topology verification timed out.",
                )
            node = _copy(dict(raw_node))
            key = str(node.get("key") or "").strip()
            if not key:
                return fail("sub_phemar_topology_invalid", "A topology node lacks a provider id.")
            color = colors.get(key)
            if color == "gray":
                return fail("attachment_cycle", "The candidate's live attachment graph already contains a cycle.")
            if color == "black":
                return True
            if parent_tokens & _identity_tokens(node.get("identity")):
                return fail("attachment_cycle", "The candidate's live descendants already contain this host Phemar.")
            if len(nodes) >= max_nodes:
                return fail("sub_phemar_topology_limit_exceeded", "The topology node limit was exceeded.")
            colors[key] = "gray"
            nodes[key] = node
            records, fetch_issue = self._fetch_observed_registry(
                str(node.get("provider_origin") or ""),
                deadline=deadline,
            )
            if records is None:
                return fail(
                    str(_mapping(fetch_issue).get("code") or "sub_phemar_topology_unavailable"),
                    str(_mapping(fetch_issue).get("message") or "A descendant topology is unavailable."),
                )
            if len(records) > max_children:
                return fail("sub_phemar_topology_limit_exceeded", "A topology node exceeded the child limit.")
            edges[key] = []
            for record in records:
                edge_count += 1
                if edge_count > max_edges:
                    return fail("sub_phemar_topology_limit_exceeded", "The topology edge limit was exceeded.")
                child, child_issue = self._resolve_topology_child(
                    record,
                    directory,
                    deadline=deadline,
                )
                if child is None:
                    return fail(
                        str(_mapping(child_issue).get("code") or "sub_phemar_topology_invalid"),
                        str(_mapping(child_issue).get("message") or "A descendant could not be verified."),
                    )
                child_key = str(child.get("key") or "")
                if child_key not in edges[key]:
                    edges[key].append(child_key)
                if not visit(child):
                    return False
            colors[key] = "black"
            finished.append(key)
            return True

        if not visit(root_node):
            return {"verified": False, "issues": issues, "topology": None}

        root_key = str(root_node.get("key") or "")
        distances = {root_key: 1}
        for node_key in reversed(finished):
            distance = distances.get(node_key, 1)
            for child_key in edges.get(node_key, []):
                distances[child_key] = max(distances.get(child_key, 1), distance + 1)
        max_relative_depth = max(distances.values(), default=1)
        parent_depth = int(self._parent_lifecycle().get("attachment_depth") or 0)
        resulting_max_depth = parent_depth + max_relative_depth
        max_depth = int(safety.get("max_depth") or DEFAULT_SUB_PHEMAR_MAX_DEPTH)
        if resulting_max_depth > max_depth:
            _issue(
                issues,
                "max_depth",
                "$.topology",
                f"The verified attachment subtree exceeds the host limit of {max_depth}.",
            )
            return {"verified": False, "issues": issues, "topology": None}

        def identity_row(node_key: str) -> dict[str, Any]:
            identity = _mapping(nodes[node_key].get("identity"))
            return {
                "id": str(identity.get("id") or ""),
                "agent_id": str(identity.get("agent_id") or ""),
                "provider_agent_id": str(nodes[node_key].get("provider_agent_id") or ""),
            }

        direct_children = [identity_row(key) for key in edges.get(root_key, [])]
        descendants = [
            {**identity_row(key), "relative_depth": distances.get(key, 1)}
            for key in nodes
            if key != root_key
        ]
        descendants.sort(
            key=lambda row: (
                int(row.get("relative_depth") or 0),
                str(row.get("agent_id") or ""),
                str(row.get("id") or ""),
            )
        )
        topology = {
            "contract": "phemacast.sub-phemar-topology/v1",
            "verified": True,
            "verified_at": _now_iso(),
            "root": identity_row(root_key),
            "attached_children": direct_children,
            "descendants": descendants,
            "node_count": len(nodes),
            "edge_count": edge_count,
            "max_relative_depth": max_relative_depth,
            "resulting_max_depth": resulting_max_depth,
        }
        topology_structure = {
            "contract": topology["contract"],
            "root_provider_agent_id": root_key,
            "nodes": [identity_row(key) for key in sorted(nodes)],
            "edges": [
                {
                    "provider_agent_id": key,
                    "child_provider_agent_ids": sorted(edges.get(key, [])),
                }
                for key in sorted(edges)
            ],
            "max_relative_depth": max_relative_depth,
            "resulting_max_depth": resulting_max_depth,
        }
        topology["structure_digest"] = _feature_digest(topology_structure)
        topology["digest"] = _feature_digest(topology)
        return {"verified": True, "issues": [], "topology": topology}

    def _server_verify_plaza_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        provider_agent_id: str = "",
        provider_address: str = "",
        verification_headers: Optional[Mapping[str, Any]] = None,
        require_current_trust: bool = True,
        pinned_approval: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Resolve a candidate against live Plaza status on the server.

        ``verification_headers`` is an ephemeral, already-authorized viewer
        context used only by the host-admin attachment route. It is never
        copied into trust evidence or registry state. Once that route records
        durable approval, later checks may rely on its immutable pins while
        still requiring the provider to remain live and requestable.
        """
        issues: list[dict[str, str]] = []
        identity = _mapping(candidate.get("identity"))
        child_agent_id = str(identity.get("agent_id") or "").strip()
        registry_agent_id = str(provider_agent_id or child_agent_id).strip()
        claimed_address = str(provider_address or identity.get("address") or candidate.get("address") or "").strip()
        approval = _mapping(pinned_approval)
        pins = _mapping(approval.get("pins"))
        pinned_provider_agent_id = str(pins.get("provider_agent_id") or "").strip()
        pinned_provider_origin = self._normalized_origin(pins.get("provider_origin"))
        pinned_descriptor_digest = str(pins.get("descriptor_digest") or "").strip()
        pinned_topology_digest = str(pins.get("topology_structure_digest") or "").strip()
        allow_verified_updates = bool(
            approval.get("durable") is True
            and getattr(self.service, "allow_verified_sub_phemar_updates", False) is True
        )
        plaza_get = getattr(self.service, "_plaza_get", None)
        ensure_token = getattr(self.service, "_ensure_token_valid", None)
        plaza_url = str(getattr(self.service, "plaza_url", "") or "").strip()
        if not child_agent_id:
            _issue(issues, "trust_agent_id", "$.identity.agent_id", "The child agent id is required for Plaza verification.")
        if not registry_agent_id:
            _issue(issues, "provider_agent_id", "$.provider_agent_id", "The Plaza provider id is required for verification.")
        if pinned_provider_agent_id and registry_agent_id != pinned_provider_agent_id:
            _issue(
                issues,
                "plaza_registration_pin_mismatch",
                "$.provider_agent_id",
                "The provider no longer matches the approved Plaza registration.",
            )
        if pinned_descriptor_digest and _descriptor_integrity_digest(candidate) != pinned_descriptor_digest:
            _issue(
                issues,
                "plaza_descriptor_pin_mismatch",
                "$.id",
                "The cached descriptor no longer matches the host-admin approval.",
            )
        has_ephemeral_headers = isinstance(verification_headers, Mapping) and bool(verification_headers)
        if not plaza_url or not callable(plaza_get) or (not has_ephemeral_headers and not callable(ensure_token)):
            _issue(issues, "plaza_verification_unavailable", "$.trust", "Server-side Plaza verification is not configured.")
            return {"trusted": False, "issues": issues, "trust": None}
        if issues:
            return {"trusted": False, "issues": issues, "trust": None}
        try:
            headers = dict(verification_headers) if has_ephemeral_headers else (ensure_token() or {})
            authorization = str(headers.get("Authorization") or headers.get("authorization") or "") if isinstance(headers, Mapping) else ""
            if not authorization.lower().startswith("bearer "):
                raise RuntimeError("Authenticated host Plaza viewer context is unavailable")
            status_params: dict[str, Any] = {"pit_type": "Phemar", "live_only": 1}
            if require_current_trust:
                status_params["trusted_only"] = 1
            response = plaza_get(
                "/api/plazas_status",
                params=status_params,
                headers=dict(headers),
                timeout=getattr(self.service, "PLAZA_CONNECTION_REQUEST_TIMEOUT", 10),
                retries=0,
            )
            status_code = int(getattr(response, "status_code", 500) or 500)
            payload = response.json() if getattr(response, "content", True) else {}
            if status_code >= 400 or not isinstance(payload, Mapping):
                raise RuntimeError(f"Plaza status returned HTTP {status_code}")
        except Exception as exc:
            _issue(issues, "plaza_verification_failed", "$.trust", f"Server-side Plaza verification failed: {exc}")
            return {"trusted": False, "issues": issues, "trust": None}

        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for plaza in payload.get("plazas") or []:
            if not isinstance(plaza, Mapping):
                continue
            for row in plaza.get("agents") or []:
                if not isinstance(row, Mapping) or not self._status_row_is_phemar(row):
                    continue
                if self._status_row_agent_id(row) == registry_agent_id:
                    matches.append((plaza, row))
        if not matches:
            _issue(issues, "plaza_agent_not_found", "$.identity.agent_id", "Plaza did not report this Phemar identity.")
            return {"trusted": False, "issues": issues, "trust": None}
        if len(matches) != 1:
            _issue(
                issues,
                "plaza_agent_ambiguous",
                "$.provider_agent_id",
                "Plaza reported more than one matching Phemar registration.",
            )
            return {"trusted": False, "issues": issues, "trust": None}

        selected_plaza, selected = matches[0]
        current_plaza_trusted = self._is_trusted_status_row(selected)
        if require_current_trust and not current_plaza_trusted:
            _issue(issues, "plaza_agent_untrusted", "$.trust", "Plaza did not mark this Phemar as trusted.")
        if not self._is_live_status_row(selected):
            _issue(issues, "plaza_agent_offline", "$.trust", "Plaza did not report this Phemar as live.")
        if not self._is_requestable_status_row(selected):
            _issue(issues, "plaza_agent_not_requestable", "$.trust", "Plaza did not report this Phemar as requestable.")
        observed_address = self._status_row_address(selected)
        observed_origin = self._normalized_origin(observed_address)
        if not observed_origin:
            _issue(
                issues,
                "plaza_provider_address_invalid",
                "$.trust.address",
                "Plaza did not report a valid HTTP(S) provider origin.",
            )
        if claimed_address and self._normalized_origin(claimed_address) != self._normalized_origin(observed_address):
            _issue(issues, "plaza_address_mismatch", "$.identity.address", "The claimed child address does not match Plaza.")
        if pinned_provider_origin and observed_origin != pinned_provider_origin:
            _issue(
                issues,
                "plaza_origin_pin_mismatch",
                "$.trust.address",
                "The provider origin no longer matches the host-admin approval.",
            )
        if issues:
            return {"trusted": False, "issues": issues, "trust": None}
        capabilities = self._status_row_capabilities(selected)
        canonical_descriptor, descriptor_source, descriptor_issue = self._canonical_descriptor_match(
            candidate,
            capabilities,
            observed_address,
            allow_verified_update=allow_verified_updates,
        )
        if canonical_descriptor is None:
            _issue(
                issues,
                str(_mapping(descriptor_issue).get("code") or "plaza_descriptor_mismatch"),
                "$.id",
                str(_mapping(descriptor_issue).get("message") or "The candidate does not match the trusted provider descriptor."),
            )
        if issues:
            return {"trusted": False, "issues": issues, "trust": None}

        canonical_for_storage = _canonical_descriptor_for_storage(
            canonical_descriptor,
            observed_origin,
        )
        canonical_digest = _descriptor_integrity_digest(canonical_for_storage)
        descriptor_updated = bool(
            pinned_descriptor_digest and canonical_digest != pinned_descriptor_digest
        )
        if descriptor_updated and not allow_verified_updates:
            _issue(
                issues,
                "plaza_descriptor_pin_mismatch",
                "$.id",
                "The live descriptor no longer matches the host-admin approval.",
            )
            return {"trusted": False, "issues": issues, "trust": None}
        topology_validation = self._verify_recursive_topology(
            {
                "key": registry_agent_id,
                "provider_agent_id": registry_agent_id,
                "provider_origin": observed_origin,
                "identity": _mapping(canonical_for_storage.get("identity")),
                "descriptor": canonical_for_storage,
            },
            payload,
            require_current_trust=require_current_trust,
        )
        if topology_validation.get("verified") is not True:
            return {
                "trusted": False,
                "issues": _copy(topology_validation.get("issues") or []),
                "trust": None,
            }
        topology = _mapping(topology_validation.get("topology"))
        topology_structure_digest = str(topology.get("structure_digest") or "")
        topology_updated = bool(
            pinned_topology_digest
            and topology_structure_digest != pinned_topology_digest
        )
        if topology_updated and not allow_verified_updates:
            _issue(
                issues,
                "plaza_topology_pin_mismatch",
                "$.topology",
                "The live attachment topology no longer matches the host-admin approval.",
            )
            return {"trusted": False, "issues": issues, "trust": None}
        checked_at = _now_iso()
        evidence = {
            "source": "plaza",
            "verified": True,
            "agent_id": child_agent_id,
            "provider_agent_id": registry_agent_id,
            "plaza_url": str(selected_plaza.get("url") or selected_plaza.get("address") or plaza_url),
            "address": observed_origin,
            "trusted": True,
            "current_plaza_trusted": current_plaza_trusted,
            "trust_basis": (
                "current-plaza-viewer-trust"
                if require_current_trust
                else "durable-host-admin-approval"
            ),
            "live": True,
            "requestable": True,
            "descriptor_source": descriptor_source,
            "descriptor_digest": canonical_digest,
            "topology_digest": str(topology.get("digest") or ""),
            "topology_structure_digest": topology_structure_digest,
            "verified_contract_update": descriptor_updated or topology_updated,
            "verified_at": checked_at,
            "observed_at": checked_at,
        }
        return {
            "trusted": True,
            "issues": [],
            "trust": evidence,
            "canonical_descriptor": canonical_for_storage,
            "topology": topology,
        }

    def accept(
        self,
        input_data: Any = None,
        *,
        attach: Optional[bool] = None,
        verification_headers: Optional[Mapping[str, Any]] = None,
        durable_admin_approval: bool = False,
        require_current_plaza_trust: bool = True,
    ) -> dict[str, Any]:
        """Validate and optionally persist a server-verified child.

        Durable approval is reserved for the separately authorized host API;
        validation-only practice calls never reach this mutation method.
        """
        response = self.validate(input_data)
        if response.get("accepted") is not True:
            return response
        payload = dict(input_data) if isinstance(input_data, Mapping) else {}
        should_attach = bool(payload.get("attach", True) if attach is None else attach)
        if not should_attach:
            return response
        descriptor = _mapping(response.get("descriptor"))
        provider_agent_id = str(payload.get("provider_agent_id") or payload.get("plaza_agent_id") or "").strip()
        provider_address = str(payload.get("provider_address") or "").strip()
        trust_validation = self._server_verify_plaza_candidate(
            descriptor,
            provider_agent_id=provider_agent_id,
            provider_address=provider_address,
            verification_headers=verification_headers,
            require_current_trust=require_current_plaza_trust,
        )
        if trust_validation.get("trusted") is not True:
            response.update(
                {
                    "accepted": False,
                    "attached": False,
                    "issues": [*response.get("issues", []), *trust_validation.get("issues", [])],
                    "trust": None,
                }
            )
            return response
        trust = _mapping(trust_validation.get("trust"))
        response["topology"] = _copy(trust_validation.get("topology"))
        descriptor = _mapping(trust_validation.get("canonical_descriptor"))
        response["descriptor"] = _copy(descriptor)
        response["digest"] = _feature_digest(descriptor)
        approval: dict[str, Any] = {}
        if durable_admin_approval:
            approved_at = _now_iso()
            trust.update(
                {
                    "trusted": True,
                    "trust_basis": "durable-host-admin-approval",
                    "durable_admin_approval": True,
                    "approved_at": approved_at,
                }
            )
            topology = _mapping(response.get("topology"))
            approval = {
                "mode": "host-admin",
                "durable": True,
                "approved_at": approved_at,
                "viewer_token_persisted": False,
                "pins": {
                    "plaza_url": str(trust.get("plaza_url") or ""),
                    "provider_agent_id": str(trust.get("provider_agent_id") or ""),
                    "provider_origin": str(trust.get("address") or ""),
                    "logical_agent_id": str(trust.get("agent_id") or ""),
                    "descriptor_digest": str(trust.get("descriptor_digest") or ""),
                    "topology_structure_digest": str(
                        topology.get("structure_digest")
                        or trust.get("topology_structure_digest")
                        or ""
                    ),
                },
            }
        response["trust"] = _copy(trust)
        response["approval"] = _copy(approval) if approval else None
        identity = _mapping(descriptor.get("identity"))
        key = str(identity.get("agent_id") or identity.get("id") or descriptor.get("id") or "").strip()
        attachment_id = f"sub-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"
        with self._lock:
            existing = self._records.get(attachment_id)
            if existing is not None:
                response.update(
                    {
                        "accepted": False,
                        "attached": False,
                        "issues": [
                            *response.get("issues", []),
                            {
                                "code": "attachment_exists",
                                "path": "$.identity.agent_id",
                                "message": "This Sub-Phemar is already attached; use the refresh endpoint to replace it.",
                            },
                        ],
                        "attachment": _copy(existing),
                    }
                )
                return response
            now = _now_iso()
            record = {
                "attachment_id": attachment_id,
                "parent": self._parent_identity(),
                "child": identity,
                "descriptor": descriptor,
                "trust": trust,
                "topology": _mapping(response.get("topology")),
                **({"approval": approval} if approval else {}),
                "status": "attached",
                "depth": response.get("depth"),
                "subtree_depth": _mapping(response.get("topology")).get(
                    "max_relative_depth"
                ),
                "digest": response.get("digest"),
                "attached_at": now,
                "updated_at": now,
                "last_validated_at": now,
                "cache": {
                    "strategy": "stale-while-revalidate",
                    "state": "fresh",
                    "cached_at": now,
                    "serve_last_known_good": True,
                    "revalidate_after_seconds": int(self.spec["cache"].get("revalidate_after_seconds") or DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS),
                },
            }
            proposed = _copy(self._records)
            proposed[attachment_id] = record
            self._commit_records(proposed)
        response.update({"attached": True, "attachment": _copy(record)})
        return response

    def refresh(
        self,
        attachment_id: str,
        input_data: Any,
        *,
        verification_headers: Optional[Mapping[str, str]] = None,
        renew_durable_approval: bool = False,
        require_current_plaza_trust: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            existing = _copy(self._records.get(attachment_id))
            if not existing:
                raise KeyError(attachment_id)
        payload = dict(input_data) if isinstance(input_data, Mapping) else {"candidate": input_data}
        response = self.validate(payload)
        if response.get("accepted") is not True:
            return response
        descriptor = _mapping(response.get("descriptor"))
        if _identity_tokens(descriptor.get("identity")) != _identity_tokens(existing.get("child")):
            response.update(
                {
                    "accepted": False,
                    "issues": [
                        *response.get("issues", []),
                        {
                            "code": "attachment_identity_mismatch",
                            "path": "$.identity",
                            "message": "Refresh cannot replace an attachment with a different Phemar identity.",
                        },
                    ],
                }
            )
            return response
        trust_validation = self._server_verify_plaza_candidate(
            descriptor,
            provider_agent_id=str(payload.get("provider_agent_id") or payload.get("plaza_agent_id") or existing.get("trust", {}).get("provider_agent_id") or ""),
            provider_address=str(payload.get("provider_address") or existing.get("trust", {}).get("address") or ""),
            verification_headers=verification_headers,
            require_current_trust=require_current_plaza_trust,
        )
        if trust_validation.get("trusted") is not True:
            response.update(
                {
                    "accepted": False,
                    "attached": False,
                    "issues": [*response.get("issues", []), *trust_validation.get("issues", [])],
                }
            )
            return response
        descriptor = _mapping(trust_validation.get("canonical_descriptor"))
        response["descriptor"] = _copy(descriptor)
        response["digest"] = _feature_digest(descriptor)
        response["topology"] = _copy(trust_validation.get("topology"))
        now = _now_iso()
        trust = _mapping(trust_validation.get("trust"))
        approval = _mapping(existing.get("approval"))
        if renew_durable_approval and approval.get("durable") is True:
            topology = _mapping(response.get("topology"))
            trust.update(
                {
                    "trusted": True,
                    "trust_basis": "durable-host-admin-approval",
                    "durable_admin_approval": True,
                    "approved_at": now,
                }
            )
            approval = {
                **approval,
                "approved_at": now,
                "pins": {
                    "plaza_url": str(trust.get("plaza_url") or ""),
                    "provider_agent_id": str(trust.get("provider_agent_id") or ""),
                    "provider_origin": str(trust.get("address") or ""),
                    "logical_agent_id": str(trust.get("agent_id") or ""),
                    "descriptor_digest": str(trust.get("descriptor_digest") or ""),
                    "topology_structure_digest": str(
                        topology.get("structure_digest")
                        or trust.get("topology_structure_digest")
                        or ""
                    ),
                },
            }
        record = {
            **existing,
            "descriptor": descriptor,
            "child": _mapping(descriptor.get("identity")),
            "trust": trust,
            "topology": _mapping(trust_validation.get("topology")),
            **({"approval": approval} if approval else {}),
            "status": "attached",
            "depth": response.get("depth"),
            "subtree_depth": _mapping(trust_validation.get("topology")).get(
                "max_relative_depth"
            ),
            "digest": response.get("digest"),
            "updated_at": now,
            "last_validated_at": now,
            "cache": {
                "strategy": "stale-while-revalidate",
                "state": "fresh",
                "cached_at": now,
                "serve_last_known_good": True,
                "revalidate_after_seconds": int(self.spec["cache"].get("revalidate_after_seconds") or DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS),
            },
        }
        with self._lock:
            proposed = _copy(self._records)
            proposed[attachment_id] = record
            self._commit_records(proposed)
        response.update({"attached": True, "trust": record["trust"], "attachment": _copy(record)})
        return response

    @staticmethod
    def _record_revalidation_due(record: Mapping[str, Any], *, force: bool = False) -> bool:
        if force:
            return True
        cache = _mapping(record.get("cache"))
        try:
            interval = max(1, int(cache.get("revalidate_after_seconds") or DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS))
        except (TypeError, ValueError):
            interval = DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS
        checked_at = str(
            cache.get("revalidated_at")
            or _mapping(record.get("trust")).get("observed_at")
            or cache.get("cached_at")
            or record.get("last_validated_at")
            or ""
        ).strip()
        if not checked_at:
            return True
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        return (datetime.now(timezone.utc) - parsed).total_seconds() >= interval

    def _revalidation_targets(self, attachment_id: str = "", *, force: bool = False) -> list[str]:
        requested = str(attachment_id or "").strip()
        with self._lock:
            if requested and requested not in self._records:
                raise KeyError(requested)
            candidates = [requested] if requested else list(self._records)
            return [
                key
                for key in candidates
                if key not in self._revalidation_inflight
                and self._record_revalidation_due(self._records[key], force=force)
            ]

    def _revalidate_targets(self, attachment_ids: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        staged: dict[str, tuple[Any, dict[str, Any], dict[str, Any]]] = {}
        try:
            for attachment_id in attachment_ids:
                with self._lock:
                    existing = _copy(self._records.get(attachment_id))
                if not existing:
                    continue
                descriptor = _mapping(existing.get("descriptor"))
                previous_trust = _mapping(existing.get("trust"))
                approval = _mapping(existing.get("approval"))
                approval_pins = _mapping(approval.get("pins"))
                durable_approval = approval.get("durable") is True
                verification = self._server_verify_plaza_candidate(
                    descriptor,
                    provider_agent_id=str(
                        approval_pins.get("provider_agent_id")
                        or previous_trust.get("provider_agent_id")
                        or ""
                    ),
                    provider_address=str(
                        approval_pins.get("provider_origin")
                        or previous_trust.get("address")
                        or ""
                    ),
                    require_current_trust=not durable_approval,
                    pinned_approval=approval if durable_approval else None,
                )
                now = _now_iso()
                cache = _mapping(existing.get("cache"))
                cache.update(
                    {
                        "strategy": "stale-while-revalidate",
                        "serve_last_known_good": True,
                        "revalidated_at": now,
                        "revalidate_after_seconds": int(
                            cache.get("revalidate_after_seconds")
                            or self.spec["cache"].get("revalidate_after_seconds")
                            or DEFAULT_SUB_PHEMAR_REVALIDATE_SECONDS
                        ),
                    }
                )
                trusted = verification.get("trusted") is True
                issues = _copy(verification.get("issues") or [])
                if trusted:
                    trust = _mapping(verification.get("trust"))
                    if durable_approval:
                        trust.update(
                            {
                                "trusted": True,
                                "trust_basis": "durable-host-admin-approval",
                                "durable_admin_approval": True,
                                "approved_at": str(approval.get("approved_at") or ""),
                            }
                        )
                        if (
                            getattr(
                                self.service,
                                "allow_verified_sub_phemar_updates",
                                False,
                            )
                            is True
                            and trust.get("verified_contract_update") is True
                        ):
                            topology = _mapping(verification.get("topology"))
                            approval = {
                                **approval,
                                "pins": {
                                    **approval_pins,
                                    "plaza_url": str(trust.get("plaza_url") or ""),
                                    "provider_agent_id": str(
                                        trust.get("provider_agent_id") or ""
                                    ),
                                    "provider_origin": str(trust.get("address") or ""),
                                    "logical_agent_id": str(trust.get("agent_id") or ""),
                                    "descriptor_digest": str(
                                        trust.get("descriptor_digest") or ""
                                    ),
                                    "topology_structure_digest": str(
                                        topology.get("structure_digest")
                                        or trust.get("topology_structure_digest")
                                        or ""
                                    ),
                                },
                                "last_verified_update_at": now,
                            }
                    canonical_descriptor = _mapping(
                        verification.get("canonical_descriptor")
                    )
                    cache.update({"state": "fresh", "cached_at": now})
                    replacement = {
                        **existing,
                        "descriptor": canonical_descriptor,
                        "child": _mapping(canonical_descriptor.get("identity")),
                        "digest": _feature_digest(canonical_descriptor),
                        "topology": _mapping(verification.get("topology")),
                        "subtree_depth": _mapping(verification.get("topology")).get(
                            "max_relative_depth"
                        ),
                        "trust": trust,
                        **({"approval": approval} if approval else {}),
                        "status": "attached",
                        "updated_at": now,
                        "last_validated_at": now,
                        "cache": cache,
                    }
                    replacement.pop("last_revalidation_error", None)
                else:
                    issue_codes = {
                        str(issue.get("code") or "")
                        for issue in issues
                        if isinstance(issue, Mapping)
                    }
                    transient = bool(
                        issue_codes
                        & {
                            "plaza_verification_failed",
                            "plaza_verification_unavailable",
                            "provider_descriptor_unavailable",
                            "sub_phemar_topology_unavailable",
                        }
                    )
                    state = "stale" if transient else "revoked"
                    trust = {
                        **previous_trust,
                        "verified": False,
                        "trusted": False,
                        "live": False,
                        "requestable": False,
                        "last_checked_at": now,
                    }
                    cache["state"] = state
                    replacement = {
                        **existing,
                        # Keep the last-known-good descriptor while visibly
                        # demoting trust after a failed server revalidation.
                        "trust": trust,
                        "status": "cached" if transient else "revoked",
                        "updated_at": now,
                        "cache": cache,
                        "last_revalidation_error": {"at": now, "issues": issues},
                    }
                staged[attachment_id] = (
                    existing.get("updated_at"),
                    replacement,
                    {
                        "attachment_id": attachment_id,
                        "trusted": trusted,
                        "cache_state": cache.get("state"),
                        "issues": issues,
                    },
                )
            if staged:
                with self._lock:
                    proposed = _copy(self._records)
                    for attachment_id, (expected_updated_at, replacement, result) in staged.items():
                        current = self._records.get(attachment_id)
                        if not current or current.get("updated_at") != expected_updated_at:
                            continue
                        proposed[attachment_id] = replacement
                        results.append(result)
                    if results:
                        self._commit_records(proposed)
            return {"status": "success", "revalidated": results, "sub_phemars": self.list()}
        finally:
            with self._lock:
                self._revalidation_inflight.difference_update(attachment_ids)

    def revalidate(
        self,
        attachment_id: str = "",
        *,
        force: bool = False,
        background: bool = False,
    ) -> dict[str, Any]:
        """Revalidate cached children against Plaza, optionally in the background."""
        attachment_ids = self._revalidation_targets(attachment_id, force=force)
        if not attachment_ids:
            return {"status": "success", "revalidated": [], "sub_phemars": self.list()}
        with self._lock:
            self._revalidation_inflight.update(attachment_ids)
        if background:
            thread = threading.Thread(
                target=self._revalidate_targets,
                args=(attachment_ids,),
                name=f"{getattr(self.service, 'name', 'phemar')}-sub-phemar-revalidate",
                daemon=True,
            )
            thread.start()
            return {
                "status": "revalidating",
                "attachment_ids": attachment_ids,
                "sub_phemars": self.list(),
            }
        return self._revalidate_targets(attachment_ids)

    def list(
        self,
        *,
        revalidate: bool = False,
        force: bool = False,
        background: bool = False,
    ) -> list[dict[str, Any]]:
        if revalidate:
            self.revalidate(force=force, background=background)
        with self._lock:
            return sorted((_copy(record) for record in self._records.values()), key=lambda row: (str(row.get("attached_at") or ""), row["attachment_id"]))

    def get(
        self,
        attachment_id: str,
        *,
        revalidate: bool = False,
        force: bool = False,
        background: bool = False,
    ) -> Optional[dict[str, Any]]:
        if revalidate:
            try:
                self.revalidate(attachment_id, force=force, background=background)
            except KeyError:
                return None
        with self._lock:
            record = self._records.get(str(attachment_id or ""))
            return _copy(record) if record else None

    def delete(self, attachment_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            key = str(attachment_id or "")
            record = self._records.get(key)
            if record is not None:
                proposed = _copy(self._records)
                proposed.pop(key, None)
                self._commit_records(proposed)
            return _copy(record) if record else None

    def _commit_records(self, proposed: Mapping[str, Mapping[str, Any]]) -> None:
        """Persist a proposed registry and roll memory back on any write failure."""
        previous = self._records
        self._records = {
            str(key): _copy(dict(value))
            for key, value in proposed.items()
            if isinstance(value, Mapping)
        }
        try:
            self._persist()
        except Exception:
            self._records = previous
            raise

    def _persist(self) -> None:
        loader = getattr(self.service, "_load_config_document", None)
        saver = getattr(self.service, "_save_config_document", None)
        if not callable(loader) or not callable(saver):
            return
        document = loader()
        if not isinstance(document, Mapping):
            return
        document = _copy(dict(document))
        nested = _mapping(document.get("phemar"))
        nested.setdefault(
            "sub_phemar_spec",
            {"contract": SUB_PHEMAR_HOST_CONTRACT, "version": SUB_PHEMAR_SPEC_VERSION},
        )
        nested["sub_phemars"] = self.list()
        document["phemar"] = nested
        saver(document)


class AcceptSubPhemarPractice(Practice):
    """Expose the typed acceptance pulse through the standard practice protocol."""

    def __init__(self):
        pulse = accept_sub_phemar_supported_pulse()
        super().__init__(
            name="Accept Sub-Phemar",
            description=pulse["description"],
            id=ACCEPT_SUB_PHEMAR_PULSE_NAME,
            tags=list(pulse["tags"]),
            examples=["POST /use_practice/accept_sub_phemar with candidate and Plaza trust evidence"],
            inputModes=["http-post", "json"],
            outputModes=["json"],
            parameters=_copy(pulse["input_schema"]),
        )

    def mount(self, app: Any) -> None:
        router = APIRouter()

        @router.post(self.path)
        async def accept_sub_phemar(message: Message):
            content = message.content or {}
            if not isinstance(content, dict):
                raise HTTPException(status_code=400, detail="Sub-Phemar content must be a JSON object.")
            # Practice invocations are intentionally validation-only. Registry
            # mutation is available only through the authorized host API.
            return self.agent.validate_sub_phemar(content)

        app.include_router(router)

    def execute(self, **kwargs: Any) -> Any:
        if not self.agent:
            raise RuntimeError("AcceptSubPhemarPractice is not bound to an agent.")
        return self.agent.validate_sub_phemar(kwargs)


def sync_sub_phemar_metadata(service: Any) -> dict[str, Any]:
    """Publish the inherited contract and generic pulse without dropping provider pulses."""
    registry = getattr(service, "_sub_phemar_registry", None)
    spec = registry.spec if isinstance(registry, SubPhemarHostRegistry) else build_sub_phemar_spec()
    card = getattr(service, "agent_card", None)
    if not isinstance(card, dict):
        return spec
    meta = _mapping(card.get("meta"))
    host_contracts = [str(value) for value in meta.get("host_contracts") or [] if str(value).strip()]
    if SUB_PHEMAR_HOST_CONTRACT not in host_contracts:
        host_contracts.insert(0, SUB_PHEMAR_HOST_CONTRACT)
    pulses = merge_sub_phemar_supported_pulses(
        _list_of_mappings(meta.get("supported_pulses")),
        _list_of_mappings(getattr(service, "supported_pulses", None)),
        spec=spec,
    )
    service.supported_pulses = pulses
    meta["host_contracts"] = host_contracts
    meta["sub_phemar_spec"] = _copy(spec)
    meta["supported_pulses"] = _copy(pulses)
    descriptor = build_phemar_feature_descriptor(service)
    descriptor["feature_endpoint"] = "/api/sub-phemars/descriptor"
    descriptor["descriptor_endpoint"] = "/api/sub-phemars/descriptor"
    capabilities = _list_of_mappings(meta.get("phemar_capabilities"))
    non_owned_capabilities = [
        capability
        for capability in capabilities
        if not _is_owned_synthesized_capability(capability)
    ]
    has_explicit_feature = any(
        capability.get("contract") == PHEMAR_FEATURE_CONTRACT
        and capability.get("synthesized") is not True
        for capability in non_owned_capabilities
    )
    if has_explicit_feature:
        capabilities = non_owned_capabilities
    else:
        synthesized = _copy(descriptor)
        synthesized["synthesized"] = True
        synthesized["provider"] = SYNTHESIZED_SUB_PHEMAR_CAPABILITY_PROVIDER
        capabilities = [*non_owned_capabilities, synthesized]
    meta["phemar_capabilities"] = capabilities
    meta["phemar_feature_descriptor"] = descriptor
    card["meta"] = meta
    return spec


def render_sub_phemar_manager(service: Any) -> HTMLResponse:
    """Render a compact read-only manager with every required child section."""
    registry = getattr(service, "_sub_phemar_registry", None)
    records = (
        registry.list(revalidate=True, background=True)
        if isinstance(registry, SubPhemarHostRegistry)
        else []
    )
    cards: list[str] = []
    for record in records:
        descriptor = _mapping(record.get("descriptor"))
        identity = _mapping(descriptor.get("identity"))
        section_markup = []
        for section_name in REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS:
            value = descriptor.get(section_name)
            pretty = html.escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))
            section_markup.append(
                f'<details open><summary>{html.escape(section_name.replace("_", " ").title())}</summary><pre>{pretty}</pre></details>'
            )
        name = html.escape(str(identity.get("name") or descriptor.get("name") or "Sub-Phemar"))
        agent_id = html.escape(str(identity.get("agent_id") or identity.get("id") or ""))
        trust = _mapping(record.get("trust"))
        plaza = html.escape(str(trust.get("plaza_url") or "Plaza"))
        cache_state = str(_mapping(record.get("cache")).get("state") or "cached")
        if trust.get("trusted") is True and cache_state == "fresh":
            badge_label = f"Trusted · {plaza}"
            badge_class = "badge"
        elif cache_state == "stale":
            badge_label = "Cached · reconnecting"
            badge_class = "badge stale"
        else:
            badge_label = "Cached · trust unavailable"
            badge_class = "badge unavailable"
        cards.append(
            f'<article class="child"><header><div><h2>{name}</h2><p>{agent_id}</p></div>'
            f'<span class="{badge_class}">{badge_label}</span></header>{"".join(section_markup)}</article>'
        )
    content = "".join(cards) or (
        '<section class="empty"><h2>No attached Sub-Phemars</h2>'
        '<p>Accepted children remain available here from the server-backed registry.</p></section>'
    )
    title = html.escape(str(getattr(service, "name", "") or "Phemar"))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Sub-Phemars</title><style>
:root{{--bg:#f5f7f8;--panel:#fff;--line:#d9e1e4;--text:#17343d;--muted:#607780;--accent:#0f766e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,sans-serif}}
main{{max-width:1120px;margin:auto;padding:18px}}.top{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}}
h1,h2,p{{margin:0}}h1{{font-size:20px}}h2{{font-size:16px}}.top p,.child header p,.empty p{{color:var(--muted);font-size:12px;margin-top:3px}}
.links{{display:flex;gap:8px;flex-wrap:wrap}}a{{color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:7px;padding:6px 9px;background:var(--panel)}}
.child,.empty{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px;margin-bottom:12px}}.child header{{display:flex;align-items:start;justify-content:space-between;gap:10px;margin-bottom:8px}}
.badge{{font-size:11px;color:var(--accent);background:#e8f5f2;border-radius:999px;padding:4px 7px}}.badge.stale{{color:#8a5b00;background:#fff5d6}}.badge.unavailable{{color:#8b3a3a;background:#fce8e8}}details{{border-top:1px solid var(--line);padding:7px 0}}summary{{cursor:pointer;font-weight:650;font-size:12px}}
pre{{margin:7px 0 0;background:#f7f9fa;border:1px solid #e5ebed;border-radius:7px;padding:9px;max-height:260px;overflow:auto;font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word}}
@media(max-width:700px){{.top,.child header{{align-items:stretch;flex-direction:column}}}}
</style></head><body><main><header class="top"><div><h1>Sub-Phemars</h1><p>{title} · {len(records)} attached</p></div><nav class="links"><a href="/api/sub-phemars/spec">Host spec</a><a href="/api/sub-phemars/descriptor">Feature descriptor</a><a href="/api/sub-phemars">Registry API</a></nav></header>{content}</main></body></html>"""
    )


def mount_sub_phemar_routes(app: Any, service_resolver: Callable[[], Any]) -> None:
    """Mount public discovery, validation, registry, and read-only manager routes."""

    @app.get("/sub-phemars", response_class=HTMLResponse)
    async def sub_phemar_manager():
        return render_sub_phemar_manager(service_resolver())

    @app.get("/api/sub-phemars/spec")
    async def get_sub_phemar_spec():
        service = service_resolver()
        return {"status": "success", "sub_phemar_spec": _copy(service.sub_phemar_spec)}

    @app.get("/api/sub-phemars/descriptor")
    async def get_sub_phemar_descriptor():
        return {"status": "success", "descriptor": service_resolver().sub_phemar_descriptor()}

    @app.get("/api/sub-phemars")
    async def list_sub_phemars():
        service = service_resolver()
        return {
            "status": "success",
            "sub_phemar_spec": _copy(service.sub_phemar_spec),
            "sub_phemars": service.list_sub_phemars(revalidate=True, background=True),
        }

    @app.post("/api/sub-phemars/validate")
    async def validate_sub_phemar_api(request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Sub-Phemar validation payload must be a JSON object.")
        return service_resolver().validate_sub_phemar(payload)

    @app.post("/api/sub-phemars/accept")
    async def accept_sub_phemar_api(request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Sub-Phemar acceptance payload must be a JSON object.")
        service = service_resolver()
        authorizer = getattr(service, "_authorize_shared_agent_settings_write", None)
        authorization_context: Mapping[str, Any] = {"auth_mode": "local_unprotected"}
        if callable(authorizer):
            authorized = await authorizer(request)
            authorization_context = _mapping(authorized)
        verification_headers: Optional[dict[str, str]] = None
        auth_mode = str(authorization_context.get("auth_mode") or "").strip().lower()
        if auth_mode == "plaza_ui":
            # Forward only the already-authorized viewer bearer to Plaza for
            # this one verification request. It is never added to the payload,
            # response, attachment record, or persisted configuration.
            authorization = str(request.headers.get("authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                verification_headers = {"Authorization": authorization}
        explicitly_authorized_admin = auth_mode not in {"", "local_unprotected", "plaza_ui"}
        genuinely_unprotected = (
            auth_mode == "local_unprotected"
            and not str(getattr(service, "plaza_url", "") or "").strip()
            and not str(getattr(service, "direct_auth_token", "") or "").strip()
            and not callable(getattr(service, "_get_authenticated_ui_context", None))
        )
        # A direct host-admin credential authorizes the attachment decision,
        # so Plaza supplies identity/liveness/capability evidence without
        # imposing a viewer-specific trust list. Plaza UI requests retain the
        # stricter viewer-trusted lookup. Unprotected routes receive this
        # authority only when the host is genuinely configured that way.
        require_current_plaza_trust = not (
            explicitly_authorized_admin or genuinely_unprotected
        )
        registry = getattr(service, "_sub_phemar_registry", None)
        if not isinstance(registry, SubPhemarHostRegistry):
            raise HTTPException(status_code=500, detail="Sub-Phemar registry is unavailable")
        return registry.accept(
            payload,
            verification_headers=verification_headers,
            durable_admin_approval=True,
            require_current_plaza_trust=require_current_plaza_trust,
        )

    @app.get("/api/sub-phemars/{attachment_id}")
    async def get_sub_phemar(attachment_id: str):
        record = service_resolver().get_sub_phemar(
            attachment_id,
            revalidate=True,
            background=True,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Sub-Phemar attachment not found")
        return {"status": "success", "sub_phemar": record}

    @app.post("/api/sub-phemars/revalidate")
    async def revalidate_sub_phemars(request: Request):
        service = service_resolver()
        authorizer = getattr(service, "_authorize_shared_agent_settings_write", None)
        if callable(authorizer):
            await authorizer(request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Sub-Phemar revalidation payload must be a JSON object.")
        try:
            return service.revalidate_sub_phemars(
                attachment_id=str(payload.get("attachment_id") or ""),
                force=bool(payload.get("force", True)),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sub-Phemar attachment not found") from exc

    @app.patch("/api/sub-phemars/{attachment_id}")
    async def refresh_sub_phemar(attachment_id: str, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Sub-Phemar refresh payload must be a JSON object.")
        service = service_resolver()
        authorizer = getattr(service, "_authorize_shared_agent_settings_write", None)
        authorization_context: Mapping[str, Any] = {}
        if callable(authorizer):
            authorization_context = _mapping(await authorizer(request))
        verification_headers: Optional[dict[str, str]] = None
        auth_mode = str(authorization_context.get("auth_mode") or "").strip().lower()
        if auth_mode == "plaza_ui":
            authorization = str(request.headers.get("authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                verification_headers = {"Authorization": authorization}
        explicitly_authorized_admin = auth_mode not in {"", "local_unprotected", "plaza_ui"}
        genuinely_unprotected = (
            auth_mode == "local_unprotected"
            and not str(getattr(service, "plaza_url", "") or "").strip()
            and not str(getattr(service, "direct_auth_token", "") or "").strip()
            and not callable(getattr(service, "_get_authenticated_ui_context", None))
        )
        require_current_plaza_trust = not (
            explicitly_authorized_admin or genuinely_unprotected
        )
        registry = getattr(service, "_sub_phemar_registry", None)
        if not isinstance(registry, SubPhemarHostRegistry):
            raise HTTPException(status_code=500, detail="Sub-Phemar registry is unavailable")
        try:
            return registry.refresh(
                attachment_id,
                payload,
                verification_headers=verification_headers,
                renew_durable_approval=True,
                require_current_plaza_trust=require_current_plaza_trust,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sub-Phemar attachment not found") from exc

    @app.delete("/api/sub-phemars/{attachment_id}")
    async def delete_sub_phemar(attachment_id: str, request: Request):
        service = service_resolver()
        authorizer = getattr(service, "_authorize_shared_agent_settings_write", None)
        if callable(authorizer):
            await authorizer(request)
        deleted = service.delete_sub_phemar(attachment_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="Sub-Phemar attachment not found")
        return {"status": "success", "attachment_id": attachment_id, "sub_phemar": deleted}


__all__ = [
    "ACCEPT_SUB_PHEMAR_PULSE_NAME",
    "AcceptSubPhemarPractice",
    "DEFAULT_SUB_PHEMAR_MAX_DEPTH",
    "PHEMAR_FEATURE_CONTRACT",
    "PHEMAR_FEATURE_VERSION",
    "PHEMAR_HOST_CONTENT_CONTRACT",
    "PHEMAR_HOST_CONTENT_ITEM_FIELDS",
    "PHEMAR_HOST_CONTENT_MAX_ITEMS",
    "PHEMAR_HOST_CONTENT_SLOT_KINDS",
    "PHEMAR_HOST_CONTENT_VALUE_TYPES",
    "PHEMAR_HOST_CONTENT_VERSION",
    "REQUIRED_SUB_PHEMAR_DESCRIPTOR_SECTIONS",
    "SUB_PHEMAR_HOST_CONTRACT",
    "SUB_PHEMAR_SPEC_VERSION",
    "SubPhemarHostRegistry",
    "accept_sub_phemar_supported_pulse",
    "build_phemar_host_content_spec",
    "build_phemar_feature_descriptor",
    "build_sub_phemar_spec",
    "merge_sub_phemar_supported_pulses",
    "mount_sub_phemar_routes",
    "render_sub_phemar_manager",
    "sync_sub_phemar_metadata",
    "validate_sub_phemar_descriptor",
    "validate_sub_phemar_trust",
    "validate_phemar_host_content",
]
