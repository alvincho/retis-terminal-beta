"""Normalized models used by the Attas Data Agent Network endpoint catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Tuple


SpecSourceKind = Literal["website", "document", "repository"]


@dataclass(frozen=True)
class VendorSpecSource:
    """Configured source for one vendor specification snapshot."""

    source_id: str
    vendor_id: str
    kind: SpecSourceKind
    location: str
    media_type: str = ""
    vendor_version: str = ""
    description: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EndpointParameterSpec:
    """Normalized description of one vendor endpoint parameter."""

    name: str
    location: str = "query"
    description: str = ""
    required: bool = False
    schema: Mapping[str, Any] = field(default_factory=dict)
    default: Any = None
    examples: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class EndpointSpec:
    """Vendor-neutral representation of one documented operation."""

    endpoint_id: str
    vendor_id: str
    name: str
    description: str
    category: str
    transport: str
    operation: str
    parameters: Tuple[EndpointParameterSpec, ...] = ()
    response_schema: Mapping[str, Any] = field(default_factory=dict)
    authentication: Mapping[str, Any] = field(default_factory=dict)
    cost: Mapping[str, Any] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    documentation_url: str = ""
    executable: bool = False
    pulse_name: str = ""
    extensions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EndpointSpecSnapshot:
    """Immutable vendor endpoint catalog captured from documented sources."""

    vendor_id: str
    retrieved_at: str
    content_hash: str
    parser_version: str
    sources: Tuple[VendorSpecSource, ...] = ()
    endpoints: Tuple[EndpointSpec, ...] = ()
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EndpointExecutionResult:
    """Normalized result returned by a source endpoint executor."""

    status: str
    endpoint_id: str
    data: Mapping[str, Any] = field(default_factory=dict)
    data_schema: Mapping[str, Any] = field(default_factory=dict)
    cost: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    error: str = ""
