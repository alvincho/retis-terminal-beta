"""The Terminal's own Phemar host descriptor: its feature, host contributions and Plaza card.

The Terminal registers with Plaza and offers its host contributions under the existing
Phemacast sub-Phemar contract and the legacy Attas host contract. It keeps that
descriptor here, in its own package, so it ships no other agent's code.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from phemacast.agents.sub_phemar import (
    PHEMAR_FEATURE_CONTRACT,
    PHEMAR_FEATURE_VERSION,
    PHEMAR_HOST_CONTENT_CONTRACT,
    PHEMAR_HOST_CONTENT_VERSION,
    SUB_PHEMAR_HOST_CONTRACT,
    accept_sub_phemar_supported_pulse,
    build_sub_phemar_spec,
)


LEGACY_ATTAS_HOST_CONTRACT = "attas.phemar-host/v1"
LEGACY_ATTAS_CONTENT_CONTRACT = "attas.host-content/v1"
LEGACY_ATTAS_CONTENT_VERSION = "1"
HOST_CONTRACTS = [SUB_PHEMAR_HOST_CONTRACT, LEGACY_ATTAS_HOST_CONTRACT]


@dataclass(frozen=True)
class HostDefinition:
    """What the Terminal declares to its host and to Plaza."""

    slug: str
    feature_id: str
    name: str
    logical_agent_id: str
    description: str
    sections: tuple[Mapping[str, Any], ...]
    surfaces: tuple[Mapping[str, Any], ...]
    portable_items: tuple[Mapping[str, Any], ...]
    attas_items: tuple[Mapping[str, Any], ...]

    @property
    def descriptor_pulse(self) -> str:
        return f"{self.slug.replace('-', '_')}_descriptor"

    @property
    def portable_content_pulse(self) -> str:
        return f"{self.slug.replace('-', '_')}_host_content"

    @property
    def attas_content_pulse(self) -> str:
        return f"{self.slug.replace('-', '_')}_attas_host_content"


def _empty_object_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False}


def _artifact_schema(contract: str, version: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["contract", "version", "items"],
        "properties": {
            "contract": {"const": contract},
            "version": {"const": version},
            "items": {"type": "array", "minItems": 1},
        },
    }


def portable_host_content(definition: HostDefinition) -> dict[str, Any]:
    return {
        "contract": PHEMAR_HOST_CONTENT_CONTRACT,
        "version": PHEMAR_HOST_CONTENT_VERSION,
        "items": copy.deepcopy(list(definition.portable_items)),
    }


def attas_host_content(definition: HostDefinition) -> dict[str, Any]:
    items = copy.deepcopy(list(definition.attas_items))
    revision = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "contract": LEGACY_ATTAS_CONTENT_CONTRACT,
        "version": LEGACY_ATTAS_CONTENT_VERSION,
        "revision": revision,
        "items": items,
    }


def supported_pulses(definition: HostDefinition) -> list[dict[str, Any]]:
    accept = accept_sub_phemar_supported_pulse(build_sub_phemar_spec())
    accept["endpoint"] = "/api/sub-phemars/accept"
    return [
        accept,
        {
            "name": f"{definition.name} descriptor",
            "pulse_name": definition.descriptor_pulse,
            "pulse_address": f"local://{definition.descriptor_pulse}",
            "description": f"Describe {definition.name}, its pulses, sections, and hosted UI.",
            "input_schema": _empty_object_schema(),
            "output_schema": {
                "type": "object",
                "required": ["status", "feature"],
                "properties": {
                    "status": {"const": "success"},
                    "feature": {"type": "object"},
                },
            },
            "endpoint": f"/api/phemar/pulses/{definition.descriptor_pulse}",
        },
        {
            "name": f"Portable {definition.name} host content",
            "pulse_name": definition.portable_content_pulse,
            "pulse_address": f"local://{definition.portable_content_pulse}",
            "description": f"Return portable host contributions owned by {definition.name}.",
            "input_schema": _empty_object_schema(),
            "output_schema": _artifact_schema(
                PHEMAR_HOST_CONTENT_CONTRACT,
                PHEMAR_HOST_CONTENT_VERSION,
            ),
            "endpoint": f"/api/phemar/pulses/{definition.portable_content_pulse}",
        },
        {
            "name": f"Attas {definition.name} host content",
            "pulse_name": definition.attas_content_pulse,
            "pulse_address": f"local://{definition.attas_content_pulse}",
            "description": f"Return Attas home contributions owned by {definition.name}.",
            "input_schema": _empty_object_schema(),
            "output_schema": _artifact_schema(
                LEGACY_ATTAS_CONTENT_CONTRACT,
                LEGACY_ATTAS_CONTENT_VERSION,
            ),
            "endpoint": f"/api/phemar/pulses/{definition.attas_content_pulse}",
        },
    ]


def feature(
    definition: HostDefinition,
    agent_id: str | None = None,
) -> dict[str, Any]:
    identity = str(agent_id or definition.logical_agent_id).strip() or definition.logical_agent_id
    portable = portable_host_content(definition)
    legacy = attas_host_content(definition)
    return {
        "contract": PHEMAR_FEATURE_CONTRACT,
        "version": PHEMAR_FEATURE_VERSION,
        "id": definition.feature_id,
        "name": definition.name,
        "description": definition.description,
        "identity": {
            "id": definition.logical_agent_id,
            "name": definition.name.replace(" ", ""),
            "role": "phemar",
            "agent_id": identity,
        },
        "compatibility": {
            "host_contracts": list(HOST_CONTRACTS),
            "source_modes": ["parameters"],
        },
        "sections": copy.deepcopy(list(definition.sections)),
        "supported_pulses": supported_pulses(definition),
        "ui": {"surfaces": copy.deepcopy(list(definition.surfaces))},
        "permissions": {
            "network_origins": ["self", "plaza", "persona-service"],
            "clipboard": False,
        },
        "lifecycle": {
            "health_path": "/health",
            "detach_behavior": "hide-contributions",
            "cache_strategy": "stale-while-revalidate",
            "background_reconnect": True,
            "attachment_depth": 0,
            "ancestry": [],
            "attached_children": [],
            "descendants": [],
        },
        "extensions": {
            "host_content": {
                **portable,
                "endpoint": "/api/phemar/phemacast-host-content",
                "pulse_name": definition.portable_content_pulse,
            },
            "attas_host_content": {
                "contract": legacy["contract"],
                "version": legacy["version"],
                "endpoint": "/api/phemar/attas-host-content",
                "pulse_name": definition.attas_content_pulse,
                "items": copy.deepcopy(legacy["items"]),
            },
        },
    }


def capability(definition: HostDefinition) -> dict[str, Any]:
    return {
        "id": definition.feature_id,
        "name": definition.name,
        "contract": PHEMAR_FEATURE_CONTRACT,
        "version": PHEMAR_FEATURE_VERSION,
        "compatibility": {
            "host_contracts": list(HOST_CONTRACTS),
            "source_modes": ["parameters"],
        },
        "feature_endpoint": "/api/phemar/feature",
        "descriptor_endpoint": "/api/phemar/feature",
    }


def plaza_card(definition: HostDefinition, public_url: str) -> dict[str, Any]:
    origin = str(public_url or "").rstrip("/")
    return {
        "name": definition.name.replace(" ", ""),
        "description": definition.description,
        "role": "phemar",
        "pit_type": "Phemar",
        "address": origin,
        "accepts_inbound_from_plaza": True,
        "accepts_direct_call": True,
        "connectivity_mode": "plaza-forward",
        "meta": {
            "role": "phemar",
            "accepts_inbound_from_plaza": True,
            "accepts_direct_call": True,
            "connectivity_mode": "plaza-forward",
            "host_contracts": list(HOST_CONTRACTS),
            "sub_phemar_spec": build_sub_phemar_spec(),
            "supported_pulses": supported_pulses(definition),
            "phemar_capabilities": [capability(definition)],
        },
    }


__all__ = [
    "HOST_CONTRACTS",
    "LEGACY_ATTAS_CONTENT_CONTRACT",
    "HostDefinition",
    "attas_host_content",
    "capability",
    "feature",
    "plaza_card",
    "portable_host_content",
    "supported_pulses",
]
