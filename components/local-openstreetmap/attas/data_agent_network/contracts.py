"""Pulse contracts for the Attas Data Agent Network.

These schemas deliberately describe the agent-to-agent boundary.  A source
may have a different internal vendor schema, but the network exchanges the
same availability and advice shapes for every source.
"""

from __future__ import annotations

from typing import Any, Dict
from .symbology_config import PUBLIC_SCHEMA as SOURCE_SYMBOLOGY_SCHEMA


DATA_SOURCE_PARTY = "attas"
DATA_SOURCE_PIT_TYPE = "DataSource"


_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_NUMBER = {"type": "number"}
_BOOLEAN = {"type": "boolean"}
_OBJECT = {"type": "object"}
_STRING_ARRAY = {"type": "array", "items": _STRING}


DATA_ACCESS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready", "authentication_required", "unavailable", "unknown"],
        },
        "fetch_ready": _BOOLEAN,
        "attempt_ready": _BOOLEAN,
        "access_mode": {"type": "string", "enum": ["data_source_pulse"]},
        "credential_required": _BOOLEAN,
        "credential_configured": _BOOLEAN,
        "verification": {
            "type": "string",
            "enum": ["public", "unverified", "verified", "failed", "preflight", "unknown"],
        },
        "verified_at": _STRING,
        "reason": _STRING,
    },
    "required": [
        "status",
        "fetch_ready",
        "attempt_ready",
        "access_mode",
        "credential_required",
        "credential_configured",
        "verification",
        "verified_at",
        "reason",
    ],
    "additionalProperties": False,
}


CONNECTIVITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready", "configured", "requires_configuration", "unavailable"]},
        "transport": _STRING,
        "address": _STRING,
        "practice_id": _STRING,
        "pulse_name": _STRING,
        "authentication": _STRING,
        "configuration_required": _STRING_ARRAY,
        "data_access": DATA_ACCESS_SCHEMA,
        "notes": _STRING_ARRAY,
    },
    "required": ["status", "transport", "pulse_name"],
}


SOURCE_PUBLIC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": _STRING,
        "source_name": _STRING,
        "provider": _STRING,
        "description": _STRING,
        "connectivity": CONNECTIVITY_SCHEMA,
        "symbology": SOURCE_SYMBOLOGY_SCHEMA,
    },
    "required": ["source_id", "source_name", "provider", "description", "connectivity", "symbology"],
}


COST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": _STRING,
        "amount": _NUMBER,
        "currency": _STRING,
        "unit": _STRING,
        "provider_cost": _STRING,
        "notes": _STRING_ARRAY,
    },
    "required": ["model", "amount", "currency", "unit"],
}


CANONICAL_FIELD_DEFINITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract_version": _STRING,
        "name": _STRING,
        "definition": _STRING,
        "semantic_type": _STRING,
        "type": _STRING,
        "unit": _STRING,
        "schema": _OBJECT,
    },
    "required": ["contract_version", "name", "definition", "semantic_type", "type", "unit", "schema"],
}


ATTAS_PULSE_FIELD_DEFINITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract_version": _STRING,
        "pulse_name": _STRING,
        "pulse_address": _STRING,
        "field_selector": _STRING,
        "provider_response_container": _STRING,
        "canonical_response_container": _STRING,
        "field": CANONICAL_FIELD_DEFINITION_SCHEMA,
    },
    "required": [
        "contract_version",
        "pulse_name",
        "pulse_address",
        "field_selector",
        "provider_response_container",
        "canonical_response_container",
        "field",
    ],
}


FIELD_MAPPING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "canonical_name": _STRING,
        "vendor_field_name": _STRING,
        "provider_path": _STRING,
        "canonical_path": _STRING,
        "definition": _STRING,
        "provider_definition": _STRING,
        "type": _STRING,
        "unit": _STRING,
        "canonical_definition": CANONICAL_FIELD_DEFINITION_SCHEMA,
        "attas_pulse_definition": ATTAS_PULSE_FIELD_DEFINITION_SCHEMA,
    },
    "required": [
        "canonical_name",
        "vendor_field_name",
        "provider_path",
        "canonical_path",
        "definition",
        "type",
        "unit",
        "canonical_definition",
        "attas_pulse_definition",
    ],
}


ATTAS_PULSE_ACCESS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract_version": _STRING,
        "pulse_name": _STRING,
        "pulse_address": _STRING,
        "endpoint_id": _STRING,
        "request": _OBJECT,
        "response": _OBJECT,
    },
    "required": ["contract_version", "pulse_name", "pulse_address", "endpoint_id", "request", "response"],
}


FIELD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": _STRING,
        "vendor_name": _STRING,
        "path": _STRING,
        "canonical_name": _STRING,
        "definition": _STRING,
        "provider_definition": _STRING,
        "type": _STRING,
        "unit": _STRING,
        "semantic_type": _STRING,
        "description": _STRING,
        "required": _BOOLEAN,
        "nullable": _BOOLEAN,
        "inferred": _BOOLEAN,
        "definition_source": _STRING,
        "canonical_definition": CANONICAL_FIELD_DEFINITION_SCHEMA,
        "attas_pulse_definition": ATTAS_PULSE_FIELD_DEFINITION_SCHEMA,
        "tags": _STRING_ARRAY,
        "schema": _OBJECT,
    },
    "required": ["name", "type", "canonical_name", "definition", "canonical_definition", "attas_pulse_definition"],
}


FINANCIAL_TIME_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequency": _STRING,
        "frequencies": _STRING_ARRAY,
        "timeliness": _STRING,
        "timeliness_modes": _STRING_ARRAY,
        "start": _STRING,
        "end": _STRING,
    },
}


FINANCIAL_DIMENSIONS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": _STRING,
        "time": {
            "type": "object",
            "properties": {
                "frequencies": _STRING_ARRAY,
                "timeliness": _STRING_ARRAY,
            },
            "required": ["frequencies", "timeliness"],
        },
        "asset_classes": _STRING_ARRAY,
        "asset_class_tags": _STRING_ARRAY,
        "underlying_asset_classes": _STRING_ARRAY,
        "regions": _STRING_ARRAY,
        "data_types": _STRING_ARRAY,
    },
    "required": ["version", "time", "asset_classes", "asset_class_tags", "underlying_asset_classes", "regions", "data_types"],
}


FINANCIAL_MATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["exact", "strong", "partial", "mismatch", "unconstrained"]},
        "confidence": _NUMBER,
        "matched_dimensions": _STRING_ARRAY,
        "missing_dimensions": _STRING_ARRAY,
        "conflicting_dimensions": _STRING_ARRAY,
        "unverified_dimensions": _STRING_ARRAY,
        "evidence_role": {
            "type": "string",
            "enum": ["direct", "underlying_proxy"],
        },
        "proxy_dimensions": _STRING_ARRAY,
        "required_asset_class_tags": _STRING_ARRAY,
        "endpoint_asset_class_tags": _STRING_ARRAY,
        "matched_asset_class_tags": _STRING_ARRAY,
        "conflicting_asset_class_tags": _STRING_ARRAY,
        "asset_class_compatible": _BOOLEAN,
        "region_match_mode": {
            "type": "string",
            "enum": ["unconstrained", "explicit", "global_claim"],
        },
        "region_verification_required": _BOOLEAN,
        "requirements": _OBJECT,
        "dimension_score": _INTEGER,
        "lexical_score": _INTEGER,
        "factual_score": _INTEGER,
    },
    "required": [
        "level",
        "confidence",
        "matched_dimensions",
        "missing_dimensions",
        "conflicting_dimensions",
        "evidence_role",
        "proxy_dimensions",
        "required_asset_class_tags",
        "endpoint_asset_class_tags",
        "matched_asset_class_tags",
        "conflicting_asset_class_tags",
        "asset_class_compatible",
        "requirements",
        "dimension_score",
    ],
}


FIELD_ACCESS_ROUTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset_id": _STRING,
        "endpoint_id": _STRING,
        "name": _STRING,
        "pulse_name": _STRING,
        "pulse_address": _STRING,
        "executable": _BOOLEAN,
        "authentication": _OBJECT,
        "cost": COST_SCHEMA,
        "financial_dimensions": FINANCIAL_DIMENSIONS_SCHEMA,
        "financial_match": FINANCIAL_MATCH_SCHEMA,
        "field_mapping": FIELD_MAPPING_SCHEMA,
        "attas_pulse": ATTAS_PULSE_ACCESS_SCHEMA,
    },
    "required": [
        "dataset_id",
        "endpoint_id",
        "pulse_name",
        "pulse_address",
        "executable",
        "field_mapping",
        "attas_pulse",
    ],
}


FIELD_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "field_id": _STRING,
        "name": _STRING,
        "canonical_name": _STRING,
        "definition": _STRING,
        "canonical_definition": CANONICAL_FIELD_DEFINITION_SCHEMA,
        "attas_pulse_definition": ATTAS_PULSE_FIELD_DEFINITION_SCHEMA,
        "semantic_type": _STRING,
        "canonical_definition": CANONICAL_FIELD_DEFINITION_SCHEMA,
        "attas_pulse_definition": ATTAS_PULSE_FIELD_DEFINITION_SCHEMA,
        "vendor_field_names": _STRING_ARRAY,
        "provider_definitions": _STRING_ARRAY,
        "types": _STRING_ARRAY,
        "type": _STRING,
        "units": _STRING_ARRAY,
        "unit": _STRING,
        "tags": _STRING_ARRAY,
        "availability": _STRING,
        "inferred": _BOOLEAN,
        "definition_sources": _STRING_ARRAY,
        "access_routes": {"type": "array", "items": FIELD_ACCESS_ROUTE_SCHEMA},
        "quality": _OBJECT,
        "cost": COST_SCHEMA,
        "coverage": _OBJECT,
        "freshness": _OBJECT,
        "match": _OBJECT,
    },
    "required": [
        "field_id",
        "name",
        "canonical_name",
        "definition",
        "canonical_definition",
        "attas_pulse_definition",
        "type",
        "unit",
        "vendor_field_names",
        "availability",
        "access_routes",
    ],
}


RESULT_SET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "format": _STRING,
        "label": _STRING,
        "description": _STRING,
        "primary": _BOOLEAN,
        "count": _INTEGER,
        "items": {"type": "array", "items": _OBJECT},
    },
    "required": ["format", "primary", "count", "items"],
    "additionalProperties": False,
}


FIELD_COMPARISON_SOURCE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": _STRING,
        "source_name": _STRING,
        "provider": _STRING,
        "field_id": _STRING,
        "vendor_field_names": _STRING_ARRAY,
        "provider_definitions": _STRING_ARRAY,
        "definition_source": _STRING,
        "type": _STRING,
        "unit": _STRING,
        "availability": _STRING,
        "direct_access": _BOOLEAN,
        "access_route_count": _INTEGER,
        "frequencies": _STRING_ARRAY,
        "pulse_names": _STRING_ARRAY,
        "canonical_contract_version": _STRING,
        "cost": COST_SCHEMA,
        "quality": _OBJECT,
    },
    "required": ["source_id", "source_name", "field_id", "type", "unit", "availability"],
}


FIELD_COMPARISON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "canonical_name": _STRING,
        "definition": _STRING,
        "canonical_definition": CANONICAL_FIELD_DEFINITION_SCHEMA,
        "attas_pulse_definition": ATTAS_PULSE_FIELD_DEFINITION_SCHEMA,
        "semantic_type": _STRING,
        "requested": _BOOLEAN,
        "match_score": _INTEGER,
        "source_count": _INTEGER,
        "coverage": _STRING,
        "compatibility": _STRING,
        "differences": _STRING_ARRAY,
        "sources": {"type": "array", "items": FIELD_COMPARISON_SOURCE_SCHEMA},
        "recommended_source_id": _STRING,
        "recommendation": _STRING,
    },
    "required": [
        "canonical_name",
        "definition",
        "canonical_definition",
        "attas_pulse_definition",
        "source_count",
        "coverage",
        "compatibility",
        "differences",
        "sources",
        "recommended_source_id",
        "recommendation",
    ],
}


ENDPOINT_PARAMETER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": _STRING,
        "location": _STRING,
        "description": _STRING,
        "required": _BOOLEAN,
        "schema": _OBJECT,
        "default": {},
        "examples": {"type": "array"},
    },
    "required": ["name", "location", "required", "schema"],
}


ENDPOINT_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "endpoint_id": _STRING,
        "vendor_id": _STRING,
        "name": _STRING,
        "description": _STRING,
        "category": _STRING,
        "transport": _STRING,
        "operation": _STRING,
        "parameters": {"type": "array", "items": ENDPOINT_PARAMETER_SCHEMA},
        "response_schema": _OBJECT,
        "authentication": _OBJECT,
        "cost": COST_SCHEMA,
        "quality": _OBJECT,
        "documentation_url": _STRING,
        "executable": _BOOLEAN,
        "pulse_name": _STRING,
        "source_pulse_name": _STRING,
        "extensions": _OBJECT,
        "canonical_response_schema": _OBJECT,
        "field_mappings": {"type": "array", "items": FIELD_MAPPING_SCHEMA},
        "attas_pulse": ATTAS_PULSE_ACCESS_SCHEMA,
    },
    "required": [
        "endpoint_id",
        "vendor_id",
        "name",
        "description",
        "category",
        "transport",
        "operation",
        "parameters",
        "response_schema",
        "authentication",
        "cost",
        "quality",
        "documentation_url",
        "executable",
        "canonical_response_schema",
        "field_mappings",
        "attas_pulse",
    ],
}


DATA_SPEC_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language search across the source's documented endpoint specifications.",
        },
        "endpoint_id": {
            "type": "string",
            "description": "Exact endpoint identifier when a single known specification is requested.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
    },
    "anyOf": [{"required": ["query"]}, {"required": ["endpoint_id"]}],
    "additionalProperties": False,
}


DATA_SPEC_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": _STRING,
        "endpoint_id": _STRING,
        "source": SOURCE_PUBLIC_SCHEMA,
        "endpoints": {"type": "array", "items": ENDPOINT_SPEC_SCHEMA},
        "count": _INTEGER,
        "offset": {"type": "integer", "minimum": 0},
        "total": {"type": "integer", "minimum": 0},
        "snapshot": _OBJECT,
        "warnings": _STRING_ARRAY,
    },
    "required": ["query", "endpoint_id", "source", "endpoints", "count", "snapshot", "warnings"],
    "additionalProperties": False,
}


DATA_SPEC_PULSE: Dict[str, Any] = {
    "name": "data_spec",
    "pulse_name": "data_spec",
    "pulse_address": "plaza://pulse/attas/data_spec",
    "description": "Search or retrieve documented vendor API endpoint specifications from a Data Source.",
    "tags": ["attas", "data-agent", "data-source", "specification"],
    "input_schema": DATA_SPEC_INPUT_SCHEMA,
    "output_schema": DATA_SPEC_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {"query": "daily historical prices and volume", "limit": 5},
}


DATASET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset_id": _STRING,
        "name": _STRING,
        "description": _STRING,
        "pulse_name": _STRING,
        "source_pulse_name": _STRING,
        "input_schema": _OBJECT,
        "output_schema": _OBJECT,
        "data_schema": _OBJECT,
        "canonical_data_schema": _OBJECT,
        "fields": {"type": "array", "items": FIELD_SCHEMA},
        "field_mappings": {"type": "array", "items": FIELD_MAPPING_SCHEMA},
        "attas_pulse": ATTAS_PULSE_ACCESS_SCHEMA,
        "cost": COST_SCHEMA,
        "quality": _OBJECT,
        "authentication": _OBJECT,
        "entitlement": _OBJECT,
        "sample_data": _OBJECT,
        "tags": _STRING_ARRAY,
        "coverage": _OBJECT,
        "freshness": _OBJECT,
        "financial_dimensions": FINANCIAL_DIMENSIONS_SCHEMA,
        "financial_match": FINANCIAL_MATCH_SCHEMA,
        "caveats": _STRING_ARRAY,
        "backend_id": _STRING,
        "backend_dataset_id": _STRING,
        "fetch_pulse": _STRING,
        "executable": _BOOLEAN,
    },
    "required": [
        "dataset_id",
        "name",
        "description",
        "pulse_name",
        "data_schema",
        "canonical_data_schema",
        "fields",
        "field_mappings",
        "attas_pulse",
        "cost",
        "sample_data",
    ],
}


SOURCE_SUITABILITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rank": {"type": "integer", "minimum": 1},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "result_weight": _NUMBER,
        "rating_weight": _NUMBER,
        "rating_applied": _BOOLEAN,
        "match_level": _STRING,
        "requirement_coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "matched_dimensions": _STRING_ARRAY,
        "unverified_dimensions": _STRING_ARRAY,
        "proxy_dimensions": _STRING_ARRAY,
        "region_verification_required": _BOOLEAN,
        "authority_weight": _NUMBER,
    },
    "required": [
        "rank",
        "score",
        "result_weight",
        "rating_weight",
        "rating_applied",
        "match_level",
        "requirement_coverage",
        "matched_dimensions",
        "authority_weight",
    ],
    "additionalProperties": False,
}


SOURCE_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": _STRING,
        "source_name": _STRING,
        "provider": _STRING,
        "description": _STRING,
        "available": _BOOLEAN,
        "suitability": SOURCE_SUITABILITY_SCHEMA,
        "symbology": {"anyOf": [SOURCE_SYMBOLOGY_SCHEMA, {"type": "object", "maxProperties": 0}]},
        "connectivity": CONNECTIVITY_SCHEMA,
        "datasets": {"type": "array", "items": DATASET_SCHEMA},
        "fields": {"type": "array", "items": FIELD_RESULT_SCHEMA},
        "field_count": _INTEGER,
        "endpoint_count": _INTEGER,
        "result_formats": _STRING_ARRAY,
        "result_sets": {"type": "array", "items": RESULT_SET_SCHEMA},
        "match_reason": _STRING,
        "count": _INTEGER,
        "warnings": _STRING_ARRAY,
        "error": _STRING,
    },
    "required": [
        "source_id",
        "source_name",
        "provider",
        "description",
        "available",
        "connectivity",
        "datasets",
        "fields",
        "field_count",
        "endpoint_count",
        "result_formats",
        "result_sets",
    ],
}


DATA_AVAILABILITY_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language question about data available from this source.",
        },
        "use_case": _STRING,
        "fields": _STRING_ARRAY,
        "asset_class": _STRING,
        "asset_classes": _STRING_ARRAY,
        "region": _STRING,
        "regions": _STRING_ARRAY,
        "data_type": _STRING,
        "data_types": _STRING_ARRAY,
        "time": FINANCIAL_TIME_SCHEMA,
        "preferences": _OBJECT,
        "result_formats": _STRING_ARRAY,
        "field_limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["query"],
    "additionalProperties": False,
}


DATA_AVAILABILITY_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": _STRING,
        "source": SOURCE_PUBLIC_SCHEMA,
        "available": _BOOLEAN,
        "datasets": {"type": "array", "items": DATASET_SCHEMA},
        "fields": {"type": "array", "items": FIELD_RESULT_SCHEMA},
        "field_count": _INTEGER,
        "endpoint_count": _INTEGER,
        "result_formats": _STRING_ARRAY,
        "result_sets": {"type": "array", "items": RESULT_SET_SCHEMA},
        "count": _INTEGER,
        "match_reason": _STRING,
        "catalog_fingerprint": {
            "type": "string",
            "description": (
                "Stable digest of the source catalog and public connectivity profile. "
                "Consultants use it to skip rebuilding unchanged retrieval memory."
            ),
        },
        "catalog_not_modified": {
            "type": "boolean",
            "description": (
                "True when the caller supplied the current catalog fingerprint and "
                "the source intentionally omitted unchanged datasets."
            ),
        },
        "as_of": {"type": "string", "format": "date-time"},
        "warnings": _STRING_ARRAY,
    },
    "required": [
        "query",
        "source",
        "available",
        "datasets",
        "fields",
        "field_count",
        "endpoint_count",
        "result_formats",
        "result_sets",
        "count",
        "as_of",
        "warnings",
    ],
    "additionalProperties": False,
}


DATA_AVAILABILITY_PULSE: Dict[str, Any] = {
    "name": "data_availability",
    "pulse_name": "data_availability",
    "pulse_address": "plaza://pulse/attas/data_availability",
    "description": "Describe which datasets and fields are available from this Data Source for a natural-language query.",
    "tags": ["attas", "data-agent", "data-source", "availability"],
    "input_schema": DATA_AVAILABILITY_INPUT_SCHEMA,
    "output_schema": DATA_AVAILABILITY_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {"query": "Do you have daily historical prices and volume for public equities?"},
}


from .timezones import TIMEZONE_SCHEMA


DATA_FETCH_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "timezone": TIMEZONE_SCHEMA,
        "target_model": {"type": "object", "description": "Exact published PDS model reference: id, version and digest."},
        "mapping_ref": {"type": "object", "description": "Optional exact installed source mapping reference."},
        "mapping_chain": {"type": "array", "maxItems": 3, "items": {"type": "object"}, "description": "Exact, source-installed and fixture-verified model projection chain."},
        "model_request": {"type": "object", "description": "Source-independent fields, instruments, time span and semantic arguments."},
        "source_id": {
            "type": "string",
            "description": "Direct Data Source selected by the Data User.",
        },
        "source": _STRING,
        "dataset_id": {
            "type": "string",
            "description": "Broker dataset identifier selected from data_advice.",
        },
        "endpoint_id": {
            "type": "string",
            "description": "Exact documented endpoint identifier selected from data_spec or data_advice.",
        },
        "parameters": {
            "type": "object",
            "description": "Backend-neutral request parameters for the selected dataset.",
        },
        "fields": {
            "type": "array",
            "items": _STRING,
            "description": "Optional Attas canonical field names selected from data_advice.",
        },
        "backend_id": _STRING,
        "auth_context": {
            "type": "object",
            "description": "Opaque source-issued authorization context; never raw credentials.",
        },
        "access_mode": {
            "type": "string",
            "enum": ["data_source_pulse", "source_endpoint"],
            "description": "Route live data through the Data Source pulse or directly to an allowlisted vendor endpoint.",
        },
        "canonical": {
            "type": "boolean",
            "description": "false returns the vendor data alone, without its canonical copy, schemas and field mappings, "
                           "for a caller that maps the data itself; the answer is then about the size of the data.",
        },
    },
    "required": ["parameters"],
    "anyOf": [{"required": ["dataset_id"]}, {"required": ["endpoint_id"]}, {"required": ["target_model", "model_request"]}],
    "additionalProperties": False,
}


DATA_FETCH_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_timezone": TIMEZONE_SCHEMA,
        "canonical_result": {"type": "object", "description": "Versioned canonical result with exact model, mapping and field lineage."},
        "status": {
            "type": "string",
            "enum": ["completed", "accepted", "authentication_required", "not_executable", "failed"],
        },
        "source": SOURCE_PUBLIC_SCHEMA,
        "dataset_id": _STRING,
        "backend_id": _STRING,
        "data": _OBJECT,
        "canonical_data": _OBJECT,
        "data_schema": _OBJECT,
        "canonical_data_schema": _OBJECT,
        "fields": {"type": "array", "items": FIELD_SCHEMA},
        "field_mappings": {"type": "array", "items": FIELD_MAPPING_SCHEMA},
        "attas_pulse": ATTAS_PULSE_ACCESS_SCHEMA,
        "cost": COST_SCHEMA,
        "job_id": _STRING,
        "warnings": _STRING_ARRAY,
        "error": _STRING,
        "access_mode": {
            "type": "string",
            "enum": ["data_source_pulse", "source_endpoint"],
        },
    },
    "required": [
        "status",
        "source",
        "dataset_id",
        "backend_id",
        "data",
        "canonical_data",
        "data_schema",
        "canonical_data_schema",
        "fields",
        "field_mappings",
        "attas_pulse",
        "warnings",
    ],
    "additionalProperties": False,
}


DATA_FETCH_PULSE: Dict[str, Any] = {
    "name": "data_fetch",
    "pulse_name": "data_fetch",
    "pulse_address": "plaza://pulse/attas/data_fetch",
    "description": "Fetch a selected endpoint through the versioned Attas data_fetch contract, returning both provider and canonical field views after user authentication.",
    "tags": ["attas", "data-agent", "data-source", "fetch"],
    "input_schema": DATA_FETCH_INPUT_SCHEMA,
    "output_schema": DATA_FETCH_OUTPUT_SCHEMA,
    "cost_points": 1,
    "cost": 1,
    "test_data": {
        "dataset_id": "data-broker.yfinance.ohlc_bar_series",
        "parameters": {"symbol": "AAPL", "interval": "1d", "data_points": 5},
    },
}


DATA_SOURCE_STATUS_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


DATA_SOURCE_UPDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": _STRING,
        "source_name": _STRING,
        "provider": _STRING,
        "available": _BOOLEAN,
        "connectivity": CONNECTIVITY_SCHEMA,
        "symbology": {"anyOf": [SOURCE_SYMBOLOGY_SCHEMA, {"type": "object", "maxProperties": 0}]},
        "dataset_count": _INTEGER,
        "last_update_at": _STRING,
        "last_update_attempt_at": _STRING,
        "last_update_status": {
            "type": "string",
            "enum": ["ready", "partial", "error", "never"],
        },
        "last_update_error": _BOOLEAN,
        "error": _STRING,
        "stale": _BOOLEAN,
        "included": _BOOLEAN,
        "consultant_rating": {"type": "integer", "minimum": 0, "maximum": 5},
        "user_rating": {"type": "integer", "minimum": 0, "maximum": 5},
        "user_rating_average": {"type": "number", "minimum": 0, "maximum": 5},
        "user_rating_count": {"type": "integer", "minimum": 0},
        "effective_rating": {"type": "number", "minimum": 0, "maximum": 5},
    },
    "required": [
        "source_id",
        "source_name",
        "provider",
        "available",
        "connectivity",
        "dataset_count",
        "last_update_at",
        "last_update_attempt_at",
        "last_update_status",
        "last_update_error",
        "error",
        "stale",
        "included",
        "consultant_rating",
        "user_rating",
        "user_rating_average",
        "user_rating_count",
        "effective_rating",
    ],
    "additionalProperties": False,
}


DATA_SOURCE_DISCOVERY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": _BOOLEAN,
        "provider": _STRING,
        "status": {
            "type": "string",
            "enum": ["disabled", "ready", "empty", "partial", "error"],
        },
        "plaza_url": _STRING,
        "party": _STRING,
        "pit_type": _STRING,
        "pulse_name": _STRING,
        "required_tags": _STRING_ARRAY,
        "require_data_source_profile": _BOOLEAN,
        "require_trusted": _BOOLEAN,
        "allowed_source_ids": {"type": ["array", "null"], "items": {"type": "string"}},
        "include_configured": _BOOLEAN,
        "configured_source_count": _INTEGER,
        "candidate_count": _INTEGER,
        "discovered_source_count": _INTEGER,
        "source_ids": _STRING_ARRAY,
        "last_attempt_at": _STRING,
        "last_success_at": _STRING,
        "cache_age_seconds": _NUMBER,
        "error": _STRING,
    },
    "required": [
        "enabled",
        "provider",
        "status",
        "plaza_url",
        "party",
        "pit_type",
        "pulse_name",
        "required_tags",
        "require_data_source_profile",
        "require_trusted",
        "include_configured",
        "configured_source_count",
        "candidate_count",
        "discovered_source_count",
        "source_ids",
        "last_attempt_at",
        "last_success_at",
        "cache_age_seconds",
        "error",
    ],
    "additionalProperties": False,
}


DATA_SOURCE_STATUS_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready", "partial", "error", "empty", "disabled", "refreshing"],
        },
        "enabled": _BOOLEAN,
        "refreshing": _BOOLEAN,
        "latest_update_at": _STRING,
        "latest_update_attempt_at": _STRING,
        "last_update_error": _BOOLEAN,
        "next_update_at": _STRING,
        "refresh_interval_sec": _NUMBER,
        "source_count": _INTEGER,
        "dataset_count": _INTEGER,
        "sources": {"type": "array", "items": DATA_SOURCE_UPDATE_SCHEMA},
        "discovery": DATA_SOURCE_DISCOVERY_SCHEMA,
        "errors": _STRING_ARRAY,
        "as_of": {"type": "string", "format": "date-time"},
    },
    "required": [
        "status",
        "enabled",
        "refreshing",
        "latest_update_at",
        "latest_update_attempt_at",
        "last_update_error",
        "next_update_at",
        "refresh_interval_sec",
        "source_count",
        "dataset_count",
        "sources",
        "discovery",
        "errors",
        "as_of",
    ],
    "additionalProperties": False,
}


DATA_SOURCE_STATUS_PULSE: Dict[str, Any] = {
    "name": "data_source_status",
    "pulse_name": "data_source_status",
    "pulse_address": "plaza://pulse/attas/data_source_status",
    "description": "List configured or auto-discovered Data Sources with discovery health, catalog freshness, and the outcome of each latest update attempt.",
    "tags": ["attas", "data-agent", "data-consultant", "source-status"],
    "input_schema": DATA_SOURCE_STATUS_INPUT_SCHEMA,
    "output_schema": DATA_SOURCE_STATUS_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {},
}


DATA_SOURCE_SETTING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": _STRING,
        "source_name": _STRING,
        "provider": _STRING,
        "included": _BOOLEAN,
        "consultant_rating": {"type": "integer", "minimum": 0, "maximum": 5},
        "user_rating": {"type": "integer", "minimum": 0, "maximum": 5},
        "user_rating_average": {"type": "number", "minimum": 0, "maximum": 5},
        "user_rating_count": {"type": "integer", "minimum": 0},
        "effective_rating": {"type": "number", "minimum": 0, "maximum": 5},
        "consultant_weight": _NUMBER,
        "user_weight": _NUMBER,
        "updated_at": _STRING,
        "connectivity": CONNECTIVITY_SCHEMA,
        "symbology": {"anyOf": [SOURCE_SYMBOLOGY_SCHEMA, {"type": "object", "maxProperties": 0}]},
        "dataset_count": _INTEGER,
        "last_update_at": _STRING,
        "last_update_status": _STRING,
        "last_update_error": _BOOLEAN,
    },
    "required": [
        "source_id",
        "source_name",
        "provider",
        "included",
        "consultant_rating",
        "user_rating",
        "user_rating_average",
        "user_rating_count",
        "effective_rating",
        "consultant_weight",
        "user_weight",
        "updated_at",
        "connectivity",
        "dataset_count",
        "last_update_at",
        "last_update_status",
        "last_update_error",
    ],
    "additionalProperties": False,
}


DATA_SOURCE_SETTINGS_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": {
            "type": "string",
            "minLength": 1,
            "description": "Configured source to update. Omit to list all source settings.",
        },
        "included": {
            "type": "boolean",
            "description": "Whether this source participates in Data Consultant recommendations.",
        },
        "consultant_rating": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": "Consultant/Owner source rating. Zero clears the rating.",
        },
        "user_rating": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": "This Data User's source rating. Zero clears only this user's rating.",
        },
    },
    "additionalProperties": False,
}


DATA_SOURCE_SETTINGS_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready", "updated", "invalid", "not_found", "disabled"],
        },
        "accepted": _BOOLEAN,
        "source_count": _INTEGER,
        "included_count": _INTEGER,
        "consultant_weight": _NUMBER,
        "user_weight": _NUMBER,
        "sources": {"type": "array", "items": DATA_SOURCE_SETTING_SCHEMA},
        "message": _STRING,
        "error": _STRING,
        "as_of": {"type": "string", "format": "date-time"},
    },
    "required": [
        "status",
        "accepted",
        "source_count",
        "included_count",
        "consultant_weight",
        "user_weight",
        "sources",
        "message",
        "error",
        "as_of",
    ],
    "additionalProperties": False,
}


DATA_SOURCE_SETTINGS_PULSE: Dict[str, Any] = {
    "name": "data_source_settings",
    "pulse_name": "data_source_settings",
    "pulse_address": "plaza://pulse/attas/data_source_settings",
    "description": "List or update source inclusion and weighted Consultant/User source ratings.",
    "tags": ["attas", "data-agent", "data-consultant", "source-settings", "learning"],
    "input_schema": DATA_SOURCE_SETTINGS_INPUT_SCHEMA,
    "output_schema": DATA_SOURCE_SETTINGS_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {},
}


AI_PROVIDER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": _STRING,
        "label": _STRING,
        "default_endpoint": _STRING,
        "suggested_models": _STRING_ARRAY,
        "note": _STRING,
        "credential_required": _BOOLEAN,
        "credential_configured": _BOOLEAN,
        "credential_env": _STRING,
    },
    "required": [
        "id",
        "label",
        "default_endpoint",
        "suggested_models",
        "note",
        "credential_required",
        "credential_configured",
        "credential_env",
    ],
    "additionalProperties": False,
}


AI_TARGET_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "provider": _STRING,
        "endpoint": _STRING,
        "model": _STRING,
        "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        "max_tokens": {"type": "integer", "minimum": 64, "maximum": 32768},
        "num_ctx": {"type": "integer", "minimum": 1024, "maximum": 262144},
        "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 900},
        "reasoning_effort": {
            "type": "string",
            "enum": ["low", "medium", "high", "xhigh", "max"],
        },
    },
    "additionalProperties": False,
}


AI_TARGET_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        **AI_TARGET_INPUT_SCHEMA["properties"],
        "role": {"type": "string", "enum": ["simple", "complex"]},
        "configured": _BOOLEAN,
        "provider_label": _STRING,
        "credential_required": _BOOLEAN,
        "credential_configured": _BOOLEAN,
        "credential_env": _STRING,
    },
    "required": [
        "provider",
        "endpoint",
        "model",
        "temperature",
        "max_tokens",
        "num_ctx",
        "timeout_sec",
        "reasoning_effort",
        "role",
        "configured",
        "provider_label",
        "credential_required",
        "credential_configured",
        "credential_env",
    ],
    "additionalProperties": False,
}


AI_ROUTING_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": _BOOLEAN,
        "complexity_threshold": {"type": "number", "minimum": 0, "maximum": 10},
        "threshold_min": {"type": "number", "minimum": 0, "maximum": 10},
        "threshold_max": {"type": "number", "minimum": 0, "maximum": 10},
        "feedback_limit": {"type": "number", "minimum": 0, "maximum": 3},
        "simple": AI_TARGET_INPUT_SCHEMA,
        "complex": AI_TARGET_INPUT_SCHEMA,
    },
    "additionalProperties": False,
}


AI_ROUTING_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": _BOOLEAN,
        "complexity_threshold": _NUMBER,
        "threshold_min": _NUMBER,
        "threshold_max": _NUMBER,
        "feedback_limit": _NUMBER,
        "simple": AI_TARGET_OUTPUT_SCHEMA,
        "complex": AI_TARGET_OUTPUT_SCHEMA,
    },
    "required": [
        "enabled",
        "complexity_threshold",
        "threshold_min",
        "threshold_max",
        "feedback_limit",
        "simple",
        "complex",
    ],
    "additionalProperties": False,
}


AI_SETTINGS_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["list", "update", "models"],
            "description": "List settings, update the active AI, or discover models for a provider.",
        },
        "provider": {
            "type": "string",
            "minLength": 1,
            "description": "Persona-compatible chat provider identifier.",
        },
        "endpoint": {
            "type": "string",
            "description": "Provider endpoint or configured llm_chat service address.",
        },
        "model": {
            "type": "string",
            "description": "Provider model identifier. May be blank when the AI service supplies its default.",
        },
        "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        "max_tokens": {"type": "integer", "minimum": 64, "maximum": 32768},
        "routing": AI_ROUTING_INPUT_SCHEMA,
    },
    "additionalProperties": False,
}


AI_SETTINGS_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready", "updated", "models", "invalid", "disabled", "error"],
        },
        "accepted": _BOOLEAN,
        "configured": _BOOLEAN,
        "provider": _STRING,
        "provider_label": _STRING,
        "endpoint": _STRING,
        "model": _STRING,
        "temperature": _NUMBER,
        "max_tokens": _INTEGER,
        "routing": AI_ROUTING_OUTPUT_SCHEMA,
        "credential_required": _BOOLEAN,
        "credential_configured": _BOOLEAN,
        "credential_env": _STRING,
        "providers": {"type": "array", "items": AI_PROVIDER_SCHEMA},
        "models": _STRING_ARRAY,
        "model_provider": _STRING,
        "models_endpoint": _STRING,
        "settings_store": _STRING,
        "synthesis_policy": _STRING,
        "updated_at": _STRING,
        "message": _STRING,
        "error": _STRING,
        "as_of": {"type": "string", "format": "date-time"},
    },
    "required": [
        "status",
        "accepted",
        "configured",
        "provider",
        "provider_label",
        "endpoint",
        "model",
        "temperature",
        "max_tokens",
        "routing",
        "credential_required",
        "credential_configured",
        "credential_env",
        "providers",
        "models",
        "model_provider",
        "models_endpoint",
        "settings_store",
        "synthesis_policy",
        "updated_at",
        "message",
        "error",
        "as_of",
    ],
    "additionalProperties": False,
}


AI_SETTINGS_PULSE: Dict[str, Any] = {
    "name": "ai_settings",
    "pulse_name": "ai_settings",
    "pulse_address": "plaza://pulse/attas/ai_settings",
    "description": "List and persist single-model or feedback-adaptive local/complex AI routing for Data Consultant synthesis.",
    "tags": ["attas", "data-agent", "data-consultant", "ai-settings", "adaptive-routing"],
    "input_schema": AI_SETTINGS_INPUT_SCHEMA,
    "output_schema": AI_SETTINGS_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {"operation": "list"},
}


DATA_ADVICE_FEEDBACK_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "advice_id": {
            "type": "string",
            "minLength": 1,
            "description": "Identifier returned by the data_advice result being rated.",
        },
        "rating": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "One-to-five-star rating for the complete advice result.",
        },
        "comment": {"type": "string", "maxLength": 1000},
    },
    "required": ["advice_id", "rating"],
    "additionalProperties": False,
}


DATA_ADVICE_FEEDBACK_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["learned", "updated", "learning_disabled", "not_found", "invalid"],
        },
        "accepted": _BOOLEAN,
        "advice_id": _STRING,
        "rating": _INTEGER,
        "recorded_at": _STRING,
        "feedback_count": _INTEGER,
        "rater_type": {"type": "string", "enum": ["user", "owner"]},
        "rater_id": _STRING,
        "message": _STRING,
        "error": _STRING,
    },
    "required": [
        "status",
        "accepted",
        "advice_id",
        "rating",
        "recorded_at",
        "feedback_count",
        "rater_type",
        "rater_id",
        "message",
        "error",
    ],
    "additionalProperties": False,
}


DATA_ADVICE_FEEDBACK_PULSE: Dict[str, Any] = {
    "name": "data_advice_feedback",
    "pulse_name": "data_advice_feedback",
    "pulse_address": "plaza://pulse/attas/data_advice_feedback",
    "description": "Rate a Data Consultant result from one to five stars and persist a learning signal for future advice.",
    "tags": ["attas", "data-agent", "data-consultant", "feedback", "learning"],
    "input_schema": DATA_ADVICE_FEEDBACK_INPUT_SCHEMA,
    "output_schema": DATA_ADVICE_FEEDBACK_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {"advice_id": "example-advice-id", "rating": 5},
}


DATA_EVIDENCE_FEEDBACK_TARGET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_type": {
            "type": "string",
            "enum": ["source", "dataset", "field"],
            "description": "Granularity of the evidence being judged.",
        },
        "source_id": {
            "type": "string",
            "minLength": 1,
            "description": "Source shown in the rated advice result.",
        },
        "dataset_id": {
            "type": "string",
            "description": "Dataset or endpoint shown in the advice when target_type is dataset or field.",
        },
        "field_id": {
            "type": "string",
            "description": "Source-specific field identifier shown in the advice.",
        },
        "canonical_name": {
            "type": "string",
            "description": "Canonical Attas field name shown in the advice.",
        },
        "verdict": {
            "type": "string",
            "enum": ["correct", "partial", "incorrect"],
            "description": "Factual judgment, kept separate from the optional quality rating.",
        },
        "rating": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Optional one-to-five-star usefulness or quality rating for this target.",
        },
        "reason_codes": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 64},
        },
        "comment": {"type": "string", "maxLength": 1000},
    },
    "required": ["target_type", "source_id", "verdict"],
    "additionalProperties": False,
}


DATA_EVIDENCE_FEEDBACK_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "advice_id": {
            "type": "string",
            "minLength": 1,
            "description": "Identifier of the advice whose displayed evidence is being judged.",
        },
        "targets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": DATA_EVIDENCE_FEEDBACK_TARGET_SCHEMA,
        },
    },
    "required": ["advice_id", "targets"],
    "additionalProperties": False,
}


DATA_EVIDENCE_FEEDBACK_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "accepted": _BOOLEAN,
        "target_type": {"type": "string", "enum": ["source", "dataset", "field"]},
        "target_id": _STRING,
        "source_id": _STRING,
        "dataset_id": _STRING,
        "field_id": _STRING,
        "canonical_name": _STRING,
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "rating": _INTEGER,
        "recorded_at": _STRING,
        "error": _STRING,
    },
    "required": [
        "accepted",
        "target_type",
        "target_id",
        "source_id",
        "dataset_id",
        "field_id",
        "canonical_name",
        "verdict",
        "rating",
        "recorded_at",
        "error",
    ],
    "additionalProperties": False,
}


DATA_EVIDENCE_FEEDBACK_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["learned", "updated", "partial", "learning_disabled", "not_found", "invalid"],
        },
        "accepted": _BOOLEAN,
        "advice_id": _STRING,
        "accepted_count": _INTEGER,
        "rejected_count": _INTEGER,
        "feedback_count": _INTEGER,
        "evidence_feedback_count": _INTEGER,
        "rater_type": {"type": "string", "enum": ["user", "owner"]},
        "rater_id": _STRING,
        "results": {"type": "array", "items": DATA_EVIDENCE_FEEDBACK_RESULT_SCHEMA},
        "message": _STRING,
        "error": _STRING,
    },
    "required": [
        "status",
        "accepted",
        "advice_id",
        "accepted_count",
        "rejected_count",
        "feedback_count",
        "evidence_feedback_count",
        "rater_type",
        "rater_id",
        "results",
        "message",
        "error",
    ],
    "additionalProperties": False,
}


DATA_EVIDENCE_FEEDBACK_PULSE: Dict[str, Any] = {
    "name": "data_evidence_feedback",
    "pulse_name": "data_evidence_feedback",
    "pulse_address": "plaza://pulse/attas/data_evidence_feedback",
    "description": "Judge source, dataset, or source-specific field evidence from one Data Consultant answer.",
    "tags": ["attas", "data-agent", "data-consultant", "feedback", "evidence", "learning"],
    "input_schema": DATA_EVIDENCE_FEEDBACK_INPUT_SCHEMA,
    "output_schema": DATA_EVIDENCE_FEEDBACK_OUTPUT_SCHEMA,
    "cost_points": 0,
    "cost": 0,
    "test_data": {
        "advice_id": "example-advice-id",
        "targets": [
            {
                "target_type": "source",
                "source_id": "yfinance",
                "verdict": "correct",
                "rating": 5,
            }
        ],
    },
}


DATA_ADVICE_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["en", "zh-Hant", "zh-Hans", "ja", "ko", "es", "fr", "de"], "default": "en"},
        "source_limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10, "description": "Maximum number of ranked matching sources to return."},
        "query": {
            "type": "string",
            "description": "Natural-language data request from a Data User.",
        },
        "use_case": _STRING,
        "preferences": _OBJECT,
        "asset_class": _STRING,
        "asset_classes": _STRING_ARRAY,
        "region": _STRING,
        "regions": _STRING_ARRAY,
        "data_type": _STRING,
        "data_types": _STRING_ARRAY,
        "time": FINANCIAL_TIME_SCHEMA,
        "fields": _STRING_ARRAY,
        "result_formats": _STRING_ARRAY,
        "limit": {"type": "integer", "minimum": 1},
        "include_sample_data": _BOOLEAN,
        "include_llm": {
            "type": "boolean",
            "deprecated": True,
            "description": "Deprecated compatibility field. A configured LLM is always attempted for Data Consultant advice.",
        },
        "advice_mode": {
            "type": "string",
            "enum": ["auto", "comparison", "recommendation"],
            "description": "Optional response intent override. Auto detects comparison-only wording.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


DATA_ADVICE_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "advice_id": _STRING,
        "request": {
            "type": "object",
            "properties": {
                "query": _STRING,
                "use_case": _STRING,
                "preferences": _OBJECT,
                "asset_class": _STRING,
                "asset_classes": _STRING_ARRAY,
                "region": _STRING,
                "regions": _STRING_ARRAY,
                "data_type": _STRING,
                "data_types": _STRING_ARRAY,
                "time": FINANCIAL_TIME_SCHEMA,
                "fields": _STRING_ARRAY,
                "advice_mode": {
                    "type": "string",
                    "enum": ["auto", "comparison", "recommendation"],
                },
            },
            "required": ["query"],
        },
        "answer": _STRING,
        "sources": {"type": "array", "items": SOURCE_RESULT_SCHEMA},
        "source_count": _INTEGER,
        "result_formats": _STRING_ARRAY,
        "field_comparisons": {"type": "array", "items": FIELD_COMPARISON_SCHEMA},
        "comparison_sets": {"type": "array", "items": RESULT_SET_SCHEMA},
        "llm": {
            "type": "object",
            "properties": {
                "used": _BOOLEAN,
                "attempted": _BOOLEAN,
                "fallback_used": _BOOLEAN,
                "pulse_name": _STRING,
                "address": _STRING,
                "model": _STRING,
                "provider": _STRING,
                "model_tier": {
                    "type": "string",
                    "enum": ["none", "single", "local", "complex"],
                },
                "reasoning_effort": _STRING,
                "error": _STRING,
                "skipped_reason": _STRING,
                "rejected_reason": _STRING,
                "call_count": {"type": "integer", "minimum": 0},
                "duration_ms": {"type": "number", "minimum": 0},
                "attempts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "used": _BOOLEAN,
                            "provider": _STRING,
                            "model": _STRING,
                            "model_tier": _STRING,
                            "reasoning_effort": _STRING,
                            "duration_ms": _NUMBER,
                            "error": _STRING,
                        },
                        "required": [
                            "used",
                            "provider",
                            "model",
                            "model_tier",
                            "reasoning_effort",
                            "duration_ms",
                            "error",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["used", "pulse_name"],
        },
        "synthesis": {
            "type": "object",
            "properties": {
                "tier": {"type": "string", "enum": ["rag_instant", "evidence_gap", "llm"]},
                "llm_required": _BOOLEAN,
                "rag_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "base_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "feedback_adjustment": {"type": "number", "minimum": -0.12, "maximum": 0.12},
                "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "feedback_samples": {"type": "integer", "minimum": 0},
                "owner_guidance_applied": _BOOLEAN,
                "match_level": _STRING,
                "source_id": _STRING,
                "dataset_id": _STRING,
                "lexical_score": {"type": "integer", "minimum": 0},
                "top_source_requirement_coverage": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "requires_reasoned_synthesis": _BOOLEAN,
                "reason": _STRING,
                "routing_enabled": _BOOLEAN,
                "model_tier": {
                    "type": "string",
                    "enum": ["single", "local", "complex"],
                },
                "initial_model_tier": {
                    "type": "string",
                    "enum": ["single", "local", "complex"],
                },
                "complexity_score": {"type": "number", "minimum": 0, "maximum": 10},
                "complexity_base_threshold": {"type": "number", "minimum": 0, "maximum": 10},
                "complexity_feedback_adjustment": {"type": "number", "minimum": -3, "maximum": 3},
                "complexity_threshold": {"type": "number", "minimum": 0, "maximum": 10},
                "complexity_feedback_samples": {"type": "integer", "minimum": 0},
                "complexity_factors": _STRING_ARRAY,
                "instrument_terms": _STRING_ARRAY,
                "selected_provider": _STRING,
                "selected_model": _STRING,
                "routing_reason": _STRING,
                "fallback_reason": _STRING,
                "validation": {
                    "type": "object",
                    "properties": {
                        "valid": _BOOLEAN,
                        "grounded": _BOOLEAN,
                        "word_count": _INTEGER,
                        "required_terms": _STRING_ARRAY,
                        "missing_terms": _STRING_ARRAY,
                        "ungrounded_urls": _STRING_ARRAY,
                        "ungrounded_names": _STRING_ARRAY,
                        "instruction_like_sources": _STRING_ARRAY,
                        "reasons": _STRING_ARRAY,
                    },
                    "required": ["valid", "word_count", "required_terms", "missing_terms", "reasons"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "tier",
                "llm_required",
                "rag_confidence",
                "base_threshold",
                "feedback_adjustment",
                "confidence_threshold",
                "feedback_samples",
                "owner_guidance_applied",
                "match_level",
                "source_id",
                "dataset_id",
                "lexical_score",
                "reason",
            ],
            "additionalProperties": False,
        },
        "timing": {
            "type": "object",
            "properties": {
                "total_ms": {"type": "number", "minimum": 0},
                "llm_ms": {"type": "number", "minimum": 0},
                "memory_search_ms": {"type": "number", "minimum": 0},
                "evidence_and_orchestration_ms": {"type": "number", "minimum": 0},
                "llm_chat_calls": {"type": "integer", "minimum": 0},
            },
            "required": [
                "total_ms",
                "llm_ms",
                "memory_search_ms",
                "evidence_and_orchestration_ms",
                "llm_chat_calls",
            ],
            "additionalProperties": False,
        },
        "memory": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["rag_memory", "live_source"]},
                "status": _STRING,
                "hit": _BOOLEAN,
                "stale": _BOOLEAN,
                "refreshed_at": _STRING,
                "age_seconds": _NUMBER,
                "search_ms": _NUMBER,
                "refresh_interval_sec": _NUMBER,
                "source_count": _INTEGER,
                "dataset_count": _INTEGER,
                "field_count": _INTEGER,
                "financial_requirements": _OBJECT,
            },
            "required": [
                "mode",
                "status",
                "hit",
                "stale",
                "refreshed_at",
                "age_seconds",
                "search_ms",
                "refresh_interval_sec",
                "source_count",
                "dataset_count",
            ],
            "additionalProperties": False,
        },
        "learning": {
            "type": "object",
            "properties": {
                "enabled": _BOOLEAN,
                "applied": _BOOLEAN,
                "feedback_count": _INTEGER,
                "user_feedback_count": _INTEGER,
                "user_rater_count": _INTEGER,
                "owner_feedback_count": _INTEGER,
                "matching_feedback_count": _INTEGER,
                "source_scores": {
                    "type": "object",
                    "additionalProperties": _NUMBER,
                },
                "dataset_scores": {
                    "type": "object",
                    "additionalProperties": _NUMBER,
                },
                "field_source_scores": {
                    "type": "object",
                    "additionalProperties": _NUMBER,
                },
                "evidence_feedback_count": _INTEGER,
                "last_feedback_at": _STRING,
                "user_weight": _NUMBER,
                "owner_weight": _NUMBER,
                "source_rating_count": _INTEGER,
                "source_ratings": _OBJECT,
                "instant_feedback_count": _INTEGER,
                "instant_confidence_adjustment": _NUMBER,
                "routing_feedback_count": _INTEGER,
                "routing_complexity_adjustment": _NUMBER,
                "owner_guidance_count": _INTEGER,
                "owner_guidance": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "advice_id": _STRING,
                            "query": _STRING,
                            "rating": _INTEGER,
                            "preferred_text": _STRING,
                            "comment": _STRING,
                            "similarity": _NUMBER,
                            "recorded_at": _STRING,
                        },
                        "required": [
                            "advice_id",
                            "query",
                            "rating",
                            "preferred_text",
                            "comment",
                            "similarity",
                            "recorded_at",
                        ],
                        "additionalProperties": False,
                    },
                },
                "consultant_assessment_count": _INTEGER,
                "consultant_description_match_count": _INTEGER,
                "consultant_guidance": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_type": {"type": "string", "enum": ["source", "endpoint", "field"]},
                            "source_id": _STRING,
                            "endpoint_id": _STRING,
                            "field_id": _STRING,
                            "target_name": _STRING,
                            "rating": {"type": "integer", "minimum": 0, "maximum": 5},
                            "description": _STRING,
                            "comment": _STRING,
                            "similarity": _NUMBER,
                            "updated_at": _STRING,
                        },
                        "required": [
                            "target_type",
                            "source_id",
                            "endpoint_id",
                            "field_id",
                            "target_name",
                            "rating",
                            "description",
                            "comment",
                            "similarity",
                            "updated_at",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "enabled",
                "applied",
                "feedback_count",
                "user_feedback_count",
                "user_rater_count",
                "owner_feedback_count",
                "matching_feedback_count",
                "source_scores",
                "dataset_scores",
                "field_source_scores",
                "evidence_feedback_count",
                "last_feedback_at",
                "user_weight",
                "owner_weight",
                "source_rating_count",
                "source_ratings",
                "owner_guidance_count",
                "owner_guidance",
                "consultant_assessment_count",
                "consultant_description_match_count",
                "consultant_guidance",
            ],
            "additionalProperties": False,
        },
        "as_of": {"type": "string", "format": "date-time"},
        "warnings": _STRING_ARRAY,
    },
    "required": ["request", "answer", "sources", "source_count", "llm", "as_of", "warnings"],
    "additionalProperties": False,
}


DATA_ADVICE_PULSE: Dict[str, Any] = {
    "name": "data_advice",
    "pulse_name": "data_advice",
    "pulse_address": "plaza://pulse/attas/data_advice",
    "description": "Translate a natural-language data request into source availability, connectivity, schema, cost, and sample-data advice.",
    "tags": ["attas", "data-agent", "data-consultant", "advice"],
    "input_schema": DATA_ADVICE_INPUT_SCHEMA,
    "output_schema": DATA_ADVICE_OUTPUT_SCHEMA,
    "cost_points": 1,
    "cost": 1,
    "test_data": {"query": "I need free daily prices and volume for US stocks for a prototype."},
}


DATA_REQUEST_PULSE: Dict[str, Any] = {
    "name": "data_request",
    "pulse_name": "data_request",
    "pulse_address": "plaza://pulse/attas/data_request",
    "description": "Let a Data User submit a natural-language request to a configured Data Consultant.",
    "tags": ["attas", "data-agent", "data-user", "request"],
    "input_schema": DATA_ADVICE_INPUT_SCHEMA,
    "output_schema": DATA_ADVICE_OUTPUT_SCHEMA,
    "cost_points": 1,
    "cost": 1,
    "test_data": {"query": "What free market data can I use for a daily equity report?"},
}


__all__ = [
    "AI_PROVIDER_SCHEMA",
    "AI_SETTINGS_INPUT_SCHEMA",
    "AI_SETTINGS_OUTPUT_SCHEMA",
    "AI_SETTINGS_PULSE",
    "ATTAS_PULSE_ACCESS_SCHEMA",
    "ATTAS_PULSE_FIELD_DEFINITION_SCHEMA",
    "CANONICAL_FIELD_DEFINITION_SCHEMA",
    "CONNECTIVITY_SCHEMA",
    "COST_SCHEMA",
    "DATA_ACCESS_SCHEMA",
    "DATASET_SCHEMA",
    "DATA_SPEC_INPUT_SCHEMA",
    "DATA_SPEC_OUTPUT_SCHEMA",
    "DATA_SPEC_PULSE",
    "DATA_ADVICE_FEEDBACK_INPUT_SCHEMA",
    "DATA_ADVICE_FEEDBACK_OUTPUT_SCHEMA",
    "DATA_ADVICE_FEEDBACK_PULSE",
    "DATA_EVIDENCE_FEEDBACK_TARGET_SCHEMA",
    "DATA_EVIDENCE_FEEDBACK_INPUT_SCHEMA",
    "DATA_EVIDENCE_FEEDBACK_RESULT_SCHEMA",
    "DATA_EVIDENCE_FEEDBACK_OUTPUT_SCHEMA",
    "DATA_EVIDENCE_FEEDBACK_PULSE",
    "DATA_ADVICE_INPUT_SCHEMA",
    "DATA_ADVICE_OUTPUT_SCHEMA",
    "DATA_ADVICE_PULSE",
    "DATA_AVAILABILITY_INPUT_SCHEMA",
    "DATA_AVAILABILITY_OUTPUT_SCHEMA",
    "DATA_AVAILABILITY_PULSE",
    "DATA_FETCH_INPUT_SCHEMA",
    "DATA_FETCH_OUTPUT_SCHEMA",
    "DATA_FETCH_PULSE",
    "DATA_REQUEST_PULSE",
    "DATA_SOURCE_DISCOVERY_SCHEMA",
    "DATA_SOURCE_STATUS_INPUT_SCHEMA",
    "DATA_SOURCE_STATUS_OUTPUT_SCHEMA",
    "DATA_SOURCE_STATUS_PULSE",
    "DATA_SOURCE_PARTY",
    "DATA_SOURCE_PIT_TYPE",
    "DATA_SOURCE_UPDATE_SCHEMA",
    "DATA_SOURCE_SETTING_SCHEMA",
    "DATA_SOURCE_SETTINGS_INPUT_SCHEMA",
    "DATA_SOURCE_SETTINGS_OUTPUT_SCHEMA",
    "DATA_SOURCE_SETTINGS_PULSE",
    "ENDPOINT_PARAMETER_SCHEMA",
    "ENDPOINT_SPEC_SCHEMA",
    "FIELD_ACCESS_ROUTE_SCHEMA",
    "FIELD_COMPARISON_SCHEMA",
    "FIELD_MAPPING_SCHEMA",
    "FIELD_RESULT_SCHEMA",
    "FIELD_SCHEMA",
    "FINANCIAL_DIMENSIONS_SCHEMA",
    "FINANCIAL_MATCH_SCHEMA",
    "FINANCIAL_TIME_SCHEMA",
    "RESULT_SET_SCHEMA",
    "SOURCE_RESULT_SCHEMA",
    "SOURCE_SUITABILITY_SCHEMA",
]
