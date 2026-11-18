"""Field-level financial metadata, matching, and cross-source comparison."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Mapping, Sequence

from .financial import infer_financial_requirements


FIELD_RESULT_FORMAT = "field"
ENDPOINT_RESULT_FORMAT = "endpoint"
CANONICAL_FIELD_CONTRACT_VERSION = "attas.finance.field.v1"
ATTAS_DATA_FETCH_CONTRACT_VERSION = "attas.data_fetch.v1"
ATTAS_DATA_FETCH_PULSE_NAME = "data_fetch"
ATTAS_DATA_FETCH_PULSE_ADDRESS = "plaza://pulse/attas/data_fetch"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMBERED_FIELD_RE = re.compile(r"^\s*\d+\s*[.)_-]?\s*")

_CANONICAL_FIELDS: Dict[str, Dict[str, Any]] = {
    "timestamp": {
        "aliases": ("date", "datetime", "time", "timestamp"),
        "definition": "Observation date or timestamp identifying when the reported value applies.",
        "type": "string",
        "unit": "ISO-8601 date or datetime",
        "semantic_type": "time",
    },
    "symbol": {
        "aliases": ("instrument", "security", "symbol", "ticker"),
        "definition": "Provider identifier for the financial instrument represented by the observation.",
        "type": "string",
        "unit": "identifier",
        "semantic_type": "instrument_identifier",
    },
    "open": {
        "aliases": ("open", "open price", "opening price"),
        "definition": "First traded price recorded in the observation interval.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "price",
    },
    "high": {
        "aliases": ("high", "high price", "session high"),
        "definition": "Highest traded price recorded in the observation interval.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "price",
    },
    "low": {
        "aliases": ("low", "low price", "session low"),
        "definition": "Lowest traded price recorded in the observation interval.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "price",
    },
    "close": {
        "aliases": ("close", "close price", "closing price", "price"),
        "definition": "Last traded price recorded in the observation interval before provider-specific adjustments.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "price",
    },
    "adjusted_close": {
        "aliases": ("adj close", "adjusted close", "adjusted price", "adjusted_close"),
        "definition": "Closing price adjusted for splits and distributions according to the provider methodology.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "adjusted_price",
    },
    "volume": {
        "aliases": ("traded volume", "trading volume", "volume"),
        "definition": "Quantity traded during the observation interval; instrument units depend on the provider and asset class.",
        "type": "number",
        "unit": "shares, contracts, or asset units",
        "semantic_type": "volume",
    },
    "dividend_amount": {
        "aliases": ("dividend", "dividend amount", "dividends"),
        "definition": "Cash distribution attributed to one unit of the instrument for the observation date.",
        "type": "number",
        "unit": "quote currency per unit",
        "semantic_type": "dividend",
    },
    "split_coefficient": {
        "aliases": ("split", "split coefficient", "split factor", "stock split"),
        "definition": "Multiplicative ratio describing a split or reverse split effective on the observation date.",
        "type": "number",
        "unit": "ratio",
        "semantic_type": "corporate_action",
    },
    "bid": {
        "aliases": ("bid", "bid price"),
        "definition": "Highest currently reported price a buyer is willing to pay.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "bid_price",
    },
    "ask": {
        "aliases": ("ask", "ask price", "offer", "offer price"),
        "definition": "Lowest currently reported price a seller is willing to accept.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "ask_price",
    },
    "weight": {
        "aliases": ("allocation", "exposure", "percent", "percentage", "portfolio weight", "weight", "weighting"),
        "definition": "Share of a portfolio, fund, sector, or other aggregate represented by the observation.",
        "type": "number",
        "unit": "ratio or percent",
        "semantic_type": "portfolio_weight",
    },
    "expense_ratio": {
        "aliases": ("expense ratio", "expenseRatio", "gross expense ratio", "net expense ratio", "net_expense_ratio"),
        "definition": "Annual fund operating expenses expressed as a proportion of fund assets.",
        "type": "number",
        "unit": "ratio or percent per year",
        "semantic_type": "fund_expense_ratio",
    },
    "sector": {
        "aliases": ("industry sector", "sector", "sector name"),
        "definition": "Economic sector classification associated with an instrument or portfolio exposure.",
        "type": "string",
        "unit": "classification",
        "semantic_type": "sector_classification",
    },
    "cik": {
        "aliases": ("central index key", "cik", "issuer cik"),
        "definition": "SEC Central Index Key identifying a registrant or reporting owner.",
        "type": "string",
        "unit": "identifier",
        "semantic_type": "regulatory_identifier",
    },
    "form": {
        "aliases": ("filing form", "form", "form type", "report type", "report_type"),
        "definition": "Regulatory form or report type associated with a filing.",
        "type": "string",
        "unit": "classification",
        "semantic_type": "filing_form",
    },
    "filing_date": {
        "aliases": ("accepted date", "filed at", "filing date", "filing_date"),
        "definition": "Date on which a regulatory filing was accepted or filed.",
        "type": "string",
        "unit": "ISO-8601 date",
        "semantic_type": "filing_date",
    },
    "taxonomy": {
        "aliases": ("taxonomy", "taxonomy name", "xbrl taxonomy"),
        "definition": "Taxonomy that defines an XBRL reporting concept.",
        "type": "string",
        "unit": "classification",
        "semantic_type": "xbrl_taxonomy",
    },
    "concept": {
        "aliases": ("concept", "tag", "xbrl concept", "xbrl tag", "xbrl_tag"),
        "definition": "Machine-readable reporting concept used for a disclosed fact.",
        "type": "string",
        "unit": "identifier",
        "semantic_type": "xbrl_concept",
    },
    "owner": {
        "aliases": ("beneficial owner", "holder", "owner", "reporting owner"),
        "definition": "Institution or person reported as an owner or holder of the instrument.",
        "type": "string",
        "unit": "entity",
        "semantic_type": "owner",
    },
    "transaction_date": {
        "aliases": ("trade date", "transaction date", "transaction_date"),
        "definition": "Date on which the reported ownership transaction occurred.",
        "type": "string",
        "unit": "ISO-8601 date",
        "semantic_type": "transaction_date",
    },
    "country": {
        "aliases": ("country", "country code", "jurisdiction"),
        "definition": "Country or jurisdiction to which the observation applies.",
        "type": "string",
        "unit": "country code or name",
        "semantic_type": "geography",
    },
    "period": {
        "aliases": ("observation period", "period", "quarter", "time period"),
        "definition": "Reporting or observation period to which the value applies.",
        "type": "string",
        "unit": "provider-defined period",
        "semantic_type": "observation_period",
    },
    "value": {
        "aliases": ("observation", "observation value", "value"),
        "definition": "Reported numeric observation for the selected financial series.",
        "type": "number",
        "unit": "series-defined",
        "semantic_type": "observation_value",
    },
    "rate": {
        "aliases": ("interest rate", "policy rate", "rate"),
        "definition": "Reported interest, policy, or other financial rate.",
        "type": "number",
        "unit": "ratio or percent",
        "semantic_type": "rate",
    },
    "ratio": {
        "aliases": ("debt service ratio", "ratio"),
        "definition": "Reported ratio between the financial quantities defined by the source series.",
        "type": "number",
        "unit": "ratio or percent",
        "semantic_type": "ratio",
    },
    "maturity": {
        "aliases": ("maturity", "maturity date", "security term", "security_term", "tenor"),
        "definition": "Contractual maturity date or tenor of a debt security.",
        "type": "string",
        "unit": "date or tenor",
        "semantic_type": "maturity",
    },
    "yield": {
        "aliases": ("average yield", "high yield", "interest rate amount", "yield"),
        "definition": "Yield associated with a security, maturity, curve point, or auction result.",
        "type": "number",
        "unit": "percent per year",
        "semantic_type": "yield",
    },
    "auction_date": {
        "aliases": ("auction date", "auction_date"),
        "definition": "Date on which the Treasury security auction was conducted.",
        "type": "string",
        "unit": "ISO-8601 date",
        "semantic_type": "auction_date",
    },
    "market_cap": {
        "aliases": ("market cap", "market capitalization", "marketCap", "market_cap"),
        "definition": "Market value of the circulating asset supply at the observation time.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "market_capitalization",
    },
    "circulating_supply": {
        "aliases": ("circulating supply", "circulatingSupply", "circulating_supply"),
        "definition": "Units of the asset estimated to be circulating at the observation time.",
        "type": "number",
        "unit": "asset units",
        "semantic_type": "circulating_supply",
    },
    "strike": {
        "aliases": ("strike", "strike price", "strike_price"),
        "definition": "Contract price at which the option holder may exercise the option.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "option_strike",
    },
    "expiration": {
        "aliases": ("contract expiry", "expiration", "expiration date", "expiry", "expiry date"),
        "definition": "Date on which a derivative contract expires.",
        "type": "string",
        "unit": "ISO-8601 date",
        "semantic_type": "contract_expiration",
    },
    "implied_volatility": {
        "aliases": ("implied volatility", "impliedVolatility", "implied_volatility", "iv"),
        "definition": "Volatility implied by the option price under the provider's pricing methodology.",
        "type": "number",
        "unit": "annualized ratio or percent",
        "semantic_type": "implied_volatility",
    },
    "delta": {
        "aliases": ("delta", "option delta"),
        "definition": "First-order sensitivity of option value to the underlying price.",
        "type": "number",
        "unit": "ratio",
        "semantic_type": "option_greek",
    },
    "gamma": {
        "aliases": ("gamma", "option gamma"),
        "definition": "Sensitivity of option delta to a change in the underlying price.",
        "type": "number",
        "unit": "provider-defined",
        "semantic_type": "option_greek",
    },
    "open_interest": {
        "aliases": ("open interest", "openInterest", "open_interest"),
        "definition": "Number of derivative contracts that remain open at the observation time.",
        "type": "number",
        "unit": "contracts",
        "semantic_type": "open_interest",
    },
    "settlement_price": {
        "aliases": ("settle", "settlement", "settlement price", "settlement_price"),
        "definition": "Official or provider-designated settlement price for a contract and trading date.",
        "type": "number",
        "unit": "quote currency",
        "semantic_type": "settlement_price",
    },
    "contract": {
        "aliases": ("contract", "contract code", "contract symbol"),
        "definition": "Provider identifier for a derivative contract.",
        "type": "string",
        "unit": "identifier",
        "semantic_type": "contract_identifier",
    },
}

_ALIAS_TO_CANONICAL = {
    re.sub(r"[^a-z0-9]+", "_", alias.lower()).strip("_"): canonical
    for canonical, definition in _CANONICAL_FIELDS.items()
    for alias in (canonical, *definition.get("aliases", ()))
}

_DATA_TYPE_FIELDS = {
    "price": {"open", "high", "low", "close", "adjusted_close"},
    "adjusted_price": {"adjusted_close"},
    "ohlcv": {"timestamp", "open", "high", "low", "close", "volume"},
    "volume": {"volume"},
    "quote": {"close", "bid", "ask"},
    "bid_ask": {"bid", "ask"},
    "dividends": {"dividend_amount"},
    "splits": {"split_coefficient"},
    "corporate_actions": {"dividend_amount", "split_coefficient"},
    "fund_holdings": {"symbol", "weight", "timestamp"},
    "fund_fees": {"expense_ratio"},
    "sector_exposure": {"sector", "weight"},
    "fixed_income_yield": {"maturity", "yield", "timestamp"},
    "yield_curve": {"maturity", "yield", "timestamp"},
    "auction_results": {"auction_date", "maturity", "yield"},
    "filings": {"cik", "form", "filing_date"},
    "form_13f": {"cik", "form", "filing_date", "owner"},
    "xbrl": {"cik", "taxonomy", "concept", "value", "period"},
    "ownership": {"cik", "owner", "filing_date"},
    "insider_transactions": {"cik", "owner", "transaction_date"},
    "credit": {"country", "period", "value"},
    "policy_rate": {"country", "period", "rate", "value"},
    "debt_service_ratio": {"country", "period", "ratio", "value"},
    "market_cap": {"market_cap"},
    "supply": {"circulating_supply"},
    "options": {"strike", "expiration", "bid", "ask", "volume"},
    "implied_volatility": {"implied_volatility"},
    "option_greeks": {"delta", "gamma"},
    "open_interest": {"open_interest"},
    "settlement": {"settlement_price"},
    "contract_reference": {"contract", "expiration"},
}

_FIELD_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
            "bid",
            "ask",
            "dividend_amount",
            "split_coefficient",
            "weight",
            "expense_ratio",
            "sector",
            "cik",
            "form",
            "filing_date",
            "taxonomy",
            "concept",
            "owner",
            "transaction_date",
            "country",
            "period",
            "value",
            "rate",
            "ratio",
            "maturity",
            "yield",
            "auction_date",
            "market_cap",
            "circulating_supply",
            "strike",
            "expiration",
            "implied_volatility",
            "delta",
            "gamma",
            "open_interest",
            "settlement_price",
            "contract",
        )
    )
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def field_learning_key(source_id: Any, field_id: Any = "", canonical_name: Any = "") -> str:
    """Return the stable source-specific key used by granular field feedback."""
    source = _text(source_id)
    field = _text(field_id) or _text(canonical_name)
    return f"{source}::{field}" if source and field else ""


def _slug(value: Any) -> str:
    cleaned = _NUMBERED_FIELD_RE.sub("", _text(value)).lower()
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


def canonical_field_name(value: Any) -> str:
    slug = _slug(value)
    return _ALIAS_TO_CANONICAL.get(slug, slug or "value")


def fields_from_schema(schema: Mapping[str, Any]) -> list[Dict[str, Any]]:
    """Flatten object/array wrappers into actual output fields."""
    results: list[Dict[str, Any]] = []

    def visit(
        node: Mapping[str, Any],
        *,
        path_prefix: str = "",
        required: set[str] | None = None,
    ) -> None:
        properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
        if not properties and _text(node.get("type")) == "array" and isinstance(node.get("items"), Mapping):
            visit(node["items"], path_prefix=f"{path_prefix}[]" if path_prefix else "items[]")
            return
        required_names = set(node.get("required") or []) | set(required or set())
        for name, raw_definition in properties.items():
            definition = dict(raw_definition) if isinstance(raw_definition, Mapping) else {}
            field_type = _text(definition.get("type") or "object")
            nested = definition.get("items") if field_type == "array" else definition
            nested_properties = nested.get("properties") if isinstance(nested, Mapping) and isinstance(nested.get("properties"), Mapping) else {}
            field_path = f"{path_prefix}.{name}" if path_prefix else _text(name)
            if nested_properties:
                visit(
                    nested,
                    path_prefix=f"{field_path}[]" if field_type == "array" else field_path,
                )
                continue
            description = _text(definition.get("description") or definition.get("title"))
            results.append(
                {
                    "name": _text(name),
                    "path": field_path,
                    "type": field_type or "object",
                    "description": description,
                    "definition": description,
                    "required": _text(name) in required_names,
                    "schema": copy.deepcopy(definition),
                }
            )

    visit(schema)
    return results


def _canonical_definition(
    canonical_name: str,
    *,
    field_type: str = "",
    unit: str = "",
    semantic_type: str = "",
) -> Dict[str, Any]:
    catalog = _CANONICAL_FIELDS.get(canonical_name, {})
    definition = _text(catalog.get("definition") or f"Attas canonical field `{canonical_name}`.")
    resolved_type = _text(field_type or catalog.get("type") or "value")
    resolved_unit = _text(unit or catalog.get("unit") or "provider-defined")
    resolved_semantic_type = _text(semantic_type or catalog.get("semantic_type") or canonical_name)
    schema_type = resolved_type if resolved_type in {"array", "boolean", "integer", "null", "number", "object", "string"} else "object"
    return {
        "contract_version": CANONICAL_FIELD_CONTRACT_VERSION,
        "name": canonical_name,
        "definition": definition,
        "semantic_type": resolved_semantic_type,
        "type": resolved_type,
        "unit": resolved_unit,
        "schema": {
            "type": schema_type,
            "description": definition,
            "x-attas-canonical-name": canonical_name,
            "x-attas-semantic-type": resolved_semantic_type,
            "x-attas-unit": resolved_unit,
        },
    }


def canonical_field_definition(value: Any) -> Dict[str, Any]:
    """Return the source-independent Attas definition for one financial field."""
    return _canonical_definition(canonical_field_name(value))


def _attas_pulse_field_definition(canonical_definition: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "contract_version": ATTAS_DATA_FETCH_CONTRACT_VERSION,
        "pulse_name": ATTAS_DATA_FETCH_PULSE_NAME,
        "pulse_address": ATTAS_DATA_FETCH_PULSE_ADDRESS,
        "field_selector": "fields",
        "provider_response_container": "data",
        "canonical_response_container": "canonical_data",
        "field": copy.deepcopy(dict(canonical_definition)),
    }


def enrich_field(field: Mapping[str, Any]) -> Dict[str, Any]:
    enriched = copy.deepcopy(dict(field))
    vendor_name = _text(enriched.get("vendor_name") or enriched.get("name")) or "value"
    canonical_name = canonical_field_name(enriched.get("canonical_name") or vendor_name)
    canonical = _CANONICAL_FIELDS.get(canonical_name, {})
    schema = enriched.get("schema") if isinstance(enriched.get("schema"), Mapping) else {}
    if "provider_definition" in enriched:
        provider_definition = _text(enriched.get("provider_definition"))
    else:
        provider_definition = _text(
            enriched.get("definition")
            or enriched.get("description")
            or schema.get("description")
        )
    inferred = bool(enriched.get("inferred")) or provider_definition.startswith(
        "Field inferred from the documented financial endpoint capability"
    )
    if inferred:
        provider_definition = ""
    definition = _text(canonical.get("definition") or provider_definition or f"Provider field `{vendor_name}`.")
    field_type = _text(enriched.get("type") or schema.get("type") or canonical.get("type") or "value")
    unit = _text(enriched.get("unit") or canonical.get("unit") or "provider-defined")
    semantic_type = _text(enriched.get("semantic_type") or canonical.get("semantic_type") or canonical_name)
    canonical_definition = _canonical_definition(
        canonical_name,
        field_type=field_type,
        unit=unit,
        semantic_type=semantic_type,
    )
    schema_type = schema.get("type")
    nullable = bool(enriched.get("nullable")) or (
        isinstance(schema_type, Sequence)
        and not isinstance(schema_type, (str, bytes))
        and "null" in schema_type
    )
    tags = [_text(item) for item in enriched.get("tags") or [] if _text(item)]
    tags = list(dict.fromkeys([*tags, f"field:{canonical_name}", f"semantic:{semantic_type}"]))
    enriched.update(
        {
            "name": vendor_name,
            "vendor_name": vendor_name,
            "path": _text(enriched.get("path") or vendor_name),
            "canonical_name": canonical_name,
            "definition": definition,
            "provider_definition": provider_definition,
            "description": provider_definition or definition,
            "type": field_type,
            "unit": unit,
            "semantic_type": semantic_type,
            "required": bool(enriched.get("required")),
            "nullable": nullable,
            "inferred": inferred,
            "definition_source": "provider" if provider_definition else "canonical_inference" if inferred else "canonical",
            "canonical_definition": canonical_definition,
            "attas_pulse_definition": _attas_pulse_field_definition(canonical_definition),
            "schema": copy.deepcopy(dict(schema)),
            "tags": tags,
        }
    )
    return enriched


def _canonical_path(provider_path: str, canonical_name: str) -> str:
    if "." not in provider_path:
        return canonical_name
    prefix, _leaf = provider_path.rsplit(".", 1)
    return f"{prefix}.{canonical_name}"


def _field_mapping(field: Mapping[str, Any]) -> Dict[str, Any]:
    vendor_name = _text(field.get("vendor_name") or field.get("name"))
    canonical_name = _text(field.get("canonical_name") or canonical_field_name(vendor_name))
    provider_path = _text(field.get("path") or vendor_name)
    canonical_path = _canonical_path(provider_path, canonical_name)
    return {
        "canonical_name": canonical_name,
        "vendor_field_name": vendor_name,
        "provider_path": f"data.{provider_path}",
        "canonical_path": f"canonical_data.{canonical_path}",
        "definition": _text(field.get("definition")),
        "provider_definition": _text(field.get("provider_definition")),
        "type": _text(field.get("type")),
        "unit": _text(field.get("unit")),
        "canonical_definition": copy.deepcopy(dict(field.get("canonical_definition") or {})),
        "attas_pulse_definition": copy.deepcopy(dict(field.get("attas_pulse_definition") or {})),
    }


def _canonicalize_schema(schema: Mapping[str, Any]) -> Dict[str, Any]:
    def visit(node: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = copy.deepcopy(dict(node))
        properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
        if properties:
            normalized_properties: Dict[str, Any] = {}
            name_map: Dict[str, str] = {}
            for raw_name, raw_child in properties.items():
                child = dict(raw_child) if isinstance(raw_child, Mapping) else {}
                nested_properties = child.get("properties") if isinstance(child.get("properties"), Mapping) else {}
                item_properties = (
                    child.get("items", {}).get("properties")
                    if isinstance(child.get("items"), Mapping)
                    and isinstance(child.get("items", {}).get("properties"), Mapping)
                    else {}
                )
                if nested_properties or item_properties:
                    canonical_name = _text(raw_name)
                    normalized_child = visit(child)
                else:
                    field = enrich_field(
                        {
                            "name": _text(raw_name),
                            "type": _text(child.get("type") or "object"),
                            "definition": _text(child.get("description") or child.get("title")),
                            "schema": child,
                        }
                    )
                    canonical_name = field["canonical_name"]
                    normalized_child = copy.deepcopy(child)
                    normalized_child.update(field["canonical_definition"]["schema"])
                name_map[_text(raw_name)] = canonical_name
                normalized_properties[canonical_name] = normalized_child
            normalized["properties"] = normalized_properties
            if isinstance(node.get("required"), Sequence) and not isinstance(node.get("required"), (str, bytes)):
                normalized["required"] = [name_map.get(_text(name), _text(name)) for name in node.get("required") or []]
        if isinstance(node.get("items"), Mapping):
            normalized["items"] = visit(node["items"])
        return normalized

    return visit(schema)


def build_attas_pulse_access(dataset: Mapping[str, Any]) -> Dict[str, Any]:
    fields = [
        item
        for item in dataset.get("fields") or []
        if isinstance(item, Mapping) and not item.get("inferred")
    ]
    mappings = [_field_mapping(field) for field in fields]
    dataset_id = _text(dataset.get("dataset_id"))
    return {
        "contract_version": ATTAS_DATA_FETCH_CONTRACT_VERSION,
        "pulse_name": ATTAS_DATA_FETCH_PULSE_NAME,
        "pulse_address": ATTAS_DATA_FETCH_PULSE_ADDRESS,
        "endpoint_id": dataset_id,
        "request": {
            "dataset_id": dataset_id,
            "field_selector": "fields",
            "available_fields": list(dict.fromkeys(item["canonical_name"] for item in mappings)),
            "parameters_schema": copy.deepcopy(dict(dataset.get("input_schema") or {})),
        },
        "response": {
            "provider_container": "data",
            "canonical_container": "canonical_data",
            "provider_schema": copy.deepcopy(dict(dataset.get("data_schema") or dataset.get("output_schema") or {})),
            "canonical_schema": copy.deepcopy(dict(dataset.get("canonical_data_schema") or {})),
            "field_mappings": mappings,
        },
    }


def enrich_dataset_fields(dataset: Mapping[str, Any]) -> Dict[str, Any]:
    enriched = copy.deepcopy(dict(dataset))
    raw_fields = [item for item in enriched.get("fields") or [] if isinstance(item, Mapping)]
    if not raw_fields:
        schema = enriched.get("data_schema") if isinstance(enriched.get("data_schema"), Mapping) else {}
        raw_fields = fields_from_schema(schema)
    enriched["fields"] = [enrich_field(field) for field in raw_fields]
    provider_schema = enriched.get("data_schema") or enriched.get("output_schema") or {}
    canonical_schema = _canonicalize_schema(provider_schema) if isinstance(provider_schema, Mapping) else {}
    if not canonical_schema and enriched["fields"]:
        canonical_schema = {
            "type": "object",
            "properties": {
                field["canonical_name"]: copy.deepcopy(field["canonical_definition"]["schema"])
                for field in enriched["fields"]
            },
        }
    enriched["canonical_data_schema"] = canonical_schema
    enriched["field_mappings"] = [_field_mapping(field) for field in enriched["fields"]]
    enriched["fetch_pulse"] = ATTAS_DATA_FETCH_PULSE_NAME
    enriched["attas_pulse"] = build_attas_pulse_access(enriched)
    return enriched


def canonicalize_data(data: Any, field_mappings: Sequence[Mapping[str, Any]]) -> Any:
    """Return an Attas-named view without mutating the provider payload."""
    name_map = {
        _text(item.get("vendor_field_name")): _text(item.get("canonical_name"))
        for item in field_mappings
        if isinstance(item, Mapping)
        and _text(item.get("vendor_field_name"))
        and _text(item.get("canonical_name"))
    }

    def visit(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {name_map.get(_text(key), _text(key)): visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return [visit(item) for item in value]
        return copy.deepcopy(value)

    return visit(data)


def _table_spec(table: Any) -> tuple[list, Mapping[str, Any], int]:
    invalid = ValueError("A table names 1-50 unique columns (key and label), and may sort by one of them and keep 1-10000 rows.")
    if not isinstance(table, Mapping) or set(table) - {"columns", "sort", "limit"}:
        raise invalid
    columns, sort, limit = table.get("columns"), table.get("sort", {}), table.get("limit", 10000)
    if not isinstance(columns, list) or not 1 <= len(columns) <= 50:
        raise invalid
    for column in columns:
        if (not isinstance(column, Mapping) or set(column) != {"key", "label"}
                or not isinstance(column["key"], str) or not 0 < len(column["key"]) <= 100
                or not isinstance(column["label"], str) or not 0 < len(column["label"]) <= 200):
            raise invalid
    keys = [column["key"] for column in columns]
    if len(set(keys)) != len(keys) or type(limit) is not int or not 1 <= limit <= 10000:
        raise invalid
    if not isinstance(sort, Mapping) or set(sort) - {"key", "descending"} or (
            sort and (sort.get("key") not in keys or not isinstance(sort.get("descending", False), bool))):
        raise invalid
    return columns, sort, limit


def table_view(result: Dict[str, Any], table: Any) -> Dict[str, Any]:
    """Deliver a canonical result's rows in the generic table shape, when the model request asks.

    `table` names the columns (each a key with a label) and may sort and limit the
    rows. Only `data` changes; the canonical result and its lineage stay as they are.
    """
    if table is None:
        return result
    columns, sort, limit = _table_spec(table)
    rows = (result.get("data") or {}).get("items")
    if not isinstance(rows, list):
        return result
    keys = [column["key"] for column in columns]
    rows = [{key: copy.deepcopy(row.get(key)) for key in keys} for row in rows if isinstance(row, Mapping)]
    if sort:
        def order(row):
            value = row[sort["key"]]
            return (0, value, "") if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, 0, str(value))
        # Rows without a value follow the others, in their original order.
        present = sorted((row for row in rows if row[sort["key"]] is not None), key=order, reverse=sort.get("descending", False))
        rows = present + [row for row in rows if row[sort["key"]] is None]
    data = {"columns": copy.deepcopy(list(columns)), "rows": rows[:limit]}
    # Closed per-instrument notes, such as an unknown listing, explain an empty row or table.
    from attas.data_agent_network.canonical.service import NOTE_STATUSES
    issues = (result.get("canonical_result") or {}).get("issues") or []
    notes = {(issue["ticker"], issue["reason"]): {"instrument": issue["ticker"], "message": issue["reason"]}
             for issue in issues if isinstance(issue, Mapping) and issue.get("status") in NOTE_STATUSES
             and isinstance(issue.get("ticker"), str) and isinstance(issue.get("reason"), str)}
    if notes:
        data["notes"] = list(notes.values())[:50]
    return {**result, "data": data}


def build_data_fetch_contract(
    dataset: Mapping[str, Any],
    data: Mapping[str, Any] | None = None,
    *,
    requested_fields: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Build the shared endpoint-to-Attas pulse metadata and canonical data view."""
    enriched = enrich_dataset_fields(dataset)
    fields = copy.deepcopy(list(enriched.get("fields") or []))
    mappings = [
        _field_mapping(field)
        for field in fields
        if isinstance(field, Mapping) and not field.get("inferred")
    ]
    pulse_access = copy.deepcopy(dict(enriched.get("attas_pulse") or {}))
    selected = list(
        dict.fromkeys(
            canonical_field_name(value)
            for value in requested_fields
            if _text(value)
        )
    )
    request = dict(pulse_access.get("request") or {})
    available_fields = list(request.get("available_fields") or [])
    request["fields"] = [field for field in selected if field in available_fields] or available_fields
    pulse_access["request"] = request
    return {
        "canonical_data": canonicalize_data(dict(data or {}), mappings),
        "canonical_data_schema": copy.deepcopy(dict(enriched.get("canonical_data_schema") or {})),
        "fields": fields,
        "field_mappings": mappings,
        "attas_pulse": pulse_access,
    }


def _field_match(canonical_name: str, request: Mapping[str, Any]) -> Dict[str, Any]:
    explicit = {
        canonical_field_name(item)
        for item in request.get("fields") or []
        if _text(item)
    }
    query = " ".join(_TOKEN_RE.findall(_text(request.get("query")).lower()))
    requirements = infer_financial_requirements(request)
    reasons: list[str] = []
    score = 10
    requested = False
    if canonical_name in explicit:
        score = 120
        requested = True
        reasons.append("explicit field request")
    aliases = _CANONICAL_FIELDS.get(canonical_name, {}).get("aliases", ())
    query_for_field = query
    if canonical_name == "close":
        query_for_field = re.sub(r"\b(?:adj|adjusted) close\b", " ", query_for_field)
    if any(re.search(rf"\b{re.escape(' '.join(_TOKEN_RE.findall(alias.lower())))}\b", query_for_field) for alias in aliases):
        score = max(score, 100)
        requested = True
        reasons.append("field named in the question")
    compatible_types = {
        field_name
        for data_type in requirements.get("data_types") or []
        for field_name in _DATA_TYPE_FIELDS.get(data_type, set())
    }
    if canonical_name in compatible_types:
        score = max(score, 75)
        reasons.append("supports the requested data type")
    if canonical_name == "timestamp" and (
        requirements.get("time", {}).get("frequencies")
        or requirements.get("time", {}).get("timeliness")
    ):
        score = max(score, 65)
        reasons.append("identifies the requested observation time")
    return {
        "requested": requested,
        "matched": score > 10,
        "score": score,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _access_route(dataset: Mapping[str, Any], field: Mapping[str, Any]) -> Dict[str, Any]:
    dataset_id = _text(dataset.get("dataset_id"))
    mapping = _field_mapping(field)
    dataset_pulse = dict(dataset.get("attas_pulse") or build_attas_pulse_access(dataset))
    dataset_request = dict(dataset_pulse.get("request") or {})
    dataset_response = dict(dataset_pulse.get("response") or {})
    # Field results carry a compact, executable pulse view. Full provider and
    # canonical schemas remain once on the endpoint result instead of being
    # repeated for every field/access-route pair.
    pulse_access = {
        "contract_version": _text(
            dataset_pulse.get("contract_version") or ATTAS_DATA_FETCH_CONTRACT_VERSION
        ),
        "pulse_name": _text(dataset_pulse.get("pulse_name") or ATTAS_DATA_FETCH_PULSE_NAME),
        "pulse_address": _text(
            dataset_pulse.get("pulse_address") or ATTAS_DATA_FETCH_PULSE_ADDRESS
        ),
        "endpoint_id": dataset_id,
        "request": {
            "dataset_id": dataset_id,
            "field_selector": "fields",
            "fields": [mapping["canonical_name"]],
            "parameters_schema": copy.deepcopy(
                dict(dataset_request.get("parameters_schema") or {})
            ),
        },
        "response": {
            "provider_container": _text(
                dataset_response.get("provider_container") or "data"
            ),
            "canonical_container": _text(
                dataset_response.get("canonical_container") or "canonical_data"
            ),
            "provider_path": mapping["provider_path"],
            "canonical_path": mapping["canonical_path"],
        },
    }
    route = {
        "dataset_id": dataset_id,
        "endpoint_id": dataset_id,
        "name": _text(dataset.get("name") or dataset_id),
        "pulse_name": _text(pulse_access.get("pulse_name") or ATTAS_DATA_FETCH_PULSE_NAME),
        "pulse_address": _text(pulse_access.get("pulse_address") or ATTAS_DATA_FETCH_PULSE_ADDRESS),
        "executable": bool(dataset.get("executable")),
        "authentication": copy.deepcopy(dict(dataset.get("authentication") or {})),
        "cost": copy.deepcopy(dict(dataset.get("cost") or {})),
        "field_mapping": mapping,
        "attas_pulse": pulse_access,
    }
    if isinstance(dataset.get("financial_dimensions"), Mapping) and dataset.get("financial_dimensions"):
        route["financial_dimensions"] = copy.deepcopy(dict(dataset["financial_dimensions"]))
    if isinstance(dataset.get("financial_match"), Mapping) and dataset.get("financial_match"):
        route["financial_match"] = copy.deepcopy(dict(dataset["financial_match"]))
    return route


def build_field_results(
    datasets: Sequence[Mapping[str, Any]],
    request: Mapping[str, Any],
    *,
    source_id: str = "",
    limit: int = 50,
) -> list[Dict[str, Any]]:
    """Group equivalent vendor fields and retain every applicable access route."""
    groups: Dict[str, Dict[str, Any]] = {}
    for raw_dataset in datasets:
        if not isinstance(raw_dataset, Mapping):
            continue
        dataset = enrich_dataset_fields(raw_dataset)
        for raw_field in dataset.get("fields") or []:
            if not isinstance(raw_field, Mapping):
                continue
            field = enrich_field(raw_field)
            route = _access_route(dataset, field)
            canonical_name = field["canonical_name"]
            group = groups.setdefault(
                canonical_name,
                {
                    "field_id": f"{_text(source_id) or 'source'}.{canonical_name}",
                    "name": canonical_name,
                    "canonical_name": canonical_name,
                    "definition": field["definition"],
                    "semantic_type": field["semantic_type"],
                    "canonical_definition": copy.deepcopy(dict(field.get("canonical_definition") or {})),
                    "attas_pulse_definition": copy.deepcopy(dict(field.get("attas_pulse_definition") or {})),
                    "vendor_field_names": [],
                    "provider_definitions": [],
                    "types": [],
                    "units": [],
                    "tags": [],
                    "access_routes": [],
                    "inferred": True,
                    "definition_sources": [],
                    "quality": copy.deepcopy(dict(dataset.get("quality") or {})),
                    "cost": copy.deepcopy(dict(dataset.get("cost") or {})),
                    "coverage": copy.deepcopy(dict(dataset.get("coverage") or {})),
                    "freshness": copy.deepcopy(dict(dataset.get("freshness") or {})),
                },
            )
            field_is_inferred = bool(field.get("inferred"))
            if field_is_inferred:
                if group["inferred"]:
                    group["vendor_field_names"].append(field["vendor_name"])
            else:
                if group["inferred"]:
                    # Replace synthetic canonical placeholders as soon as a
                    # documented provider field is available for the group.
                    group["vendor_field_names"] = []
                group["vendor_field_names"].append(field["vendor_name"])
            if field.get("provider_definition"):
                group["provider_definitions"].append(field["provider_definition"])
            group["types"].append(field["type"])
            group["units"].append(field["unit"])
            group["tags"].extend(field.get("tags") or [])
            group["inferred"] = bool(group["inferred"] and field_is_inferred)
            group["definition_sources"].append(field["definition_source"])
            if route["dataset_id"] and route["dataset_id"] not in {
                item.get("dataset_id") for item in group["access_routes"]
            }:
                group["access_routes"].append(copy.deepcopy(route))

    fields: list[Dict[str, Any]] = []
    for canonical_name, group in groups.items():
        group["vendor_field_names"] = list(dict.fromkeys(group["vendor_field_names"]))
        group["provider_definitions"] = list(dict.fromkeys(group["provider_definitions"]))
        group["types"] = list(dict.fromkeys(group["types"]))
        group["units"] = list(dict.fromkeys(group["units"]))
        group["tags"] = list(dict.fromkeys(group["tags"]))
        group["definition_sources"] = list(dict.fromkeys(group["definition_sources"]))
        group["type"] = group["types"][0] if len(group["types"]) == 1 else " | ".join(group["types"])
        group["unit"] = group["units"][0] if len(group["units"]) == 1 else " | ".join(group["units"])
        group["availability"] = "available" if group["access_routes"] else "unavailable"
        group["match"] = _field_match(canonical_name, request)
        fields.append(group)

    fields.sort(
        key=lambda item: (
            int((item.get("match") or {}).get("score") or 0),
            -_FIELD_ORDER.get(_text(item.get("canonical_name")), 10_000),
            _text(item.get("canonical_name")),
        ),
        reverse=True,
    )
    return fields[: max(1, int(limit or 1))]


def attach_result_sets(
    source: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    field_limit: int = 50,
) -> Dict[str, Any]:
    """Attach extensible typed result sets while retaining compatibility aliases."""
    enriched = copy.deepcopy(dict(source))
    datasets = [
        enrich_dataset_fields(item)
        for item in enriched.get("datasets") or []
        if isinstance(item, Mapping)
    ]
    fields = build_field_results(datasets, request, source_id=_text(enriched.get("source_id")), limit=field_limit)
    result_sets: list[Dict[str, Any]] = []
    if fields:
        result_sets.append(
            {
                "format": FIELD_RESULT_FORMAT,
                "label": "Field capabilities",
                "description": "Canonical fields with provider definitions and applicable access routes.",
                "primary": True,
                "count": len(fields),
                "items": fields,
            }
        )
    if datasets:
        result_sets.append(
            {
                "format": ENDPOINT_RESULT_FORMAT,
                "label": "Endpoint access routes",
                "description": "Documented endpoints retained for inspection and direct access.",
                "primary": not fields,
                "count": len(datasets),
                "items": datasets,
            }
        )
    enriched.update(
        {
            "datasets": datasets,
            "fields": fields,
            "field_count": len(fields),
            "endpoint_count": len(datasets),
            "result_formats": [item["format"] for item in result_sets],
            "result_sets": result_sets,
        }
    )
    return enriched


def _route_frequencies(field: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            _text(value)
            for route in field.get("access_routes") or []
            if isinstance(route, Mapping)
            for value in ((route.get("financial_dimensions") or {}).get("time") or {}).get("frequencies") or []
            if _text(value)
        }
    )


def build_field_comparisons(
    sources: Sequence[Mapping[str, Any]],
    request: Mapping[str, Any],
    *,
    limit: int = 12,
    include_recommendation: bool = True,
    field_source_scores: Mapping[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Align canonical fields and optionally attach a source recommendation."""
    available_source_ids = [
        _text(source.get("source_id"))
        for source in sources
        if isinstance(source, Mapping) and source.get("available")
    ]
    groups: Dict[str, list[Dict[str, Any]]] = {}
    definitions: Dict[str, str] = {}
    canonical_definitions: Dict[str, Dict[str, Any]] = {}
    pulse_definitions: Dict[str, Dict[str, Any]] = {}
    matches: Dict[str, Mapping[str, Any]] = {}
    for source_rank, source in enumerate(sources):
        if not isinstance(source, Mapping):
            continue
        for field in source.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            canonical_name = _text(field.get("canonical_name") or field.get("name"))
            if not canonical_name:
                continue
            routes = [item for item in field.get("access_routes") or [] if isinstance(item, Mapping)]
            entry = {
                "source_id": _text(source.get("source_id")),
                "source_name": _text(source.get("source_name") or source.get("source_id")),
                "provider": _text(source.get("provider")),
                "field_id": _text(field.get("field_id")),
                "vendor_field_names": list(field.get("vendor_field_names") or []),
                "provider_definitions": list(field.get("provider_definitions") or []),
                "definition_source": (
                    "provider"
                    if field.get("provider_definitions")
                    else "canonical_inference"
                    if field.get("inferred")
                    else "canonical"
                ),
                "type": _text(field.get("type")),
                "unit": _text(field.get("unit")),
                "availability": _text(field.get("availability") or "available"),
                "direct_access": any(bool(route.get("executable")) for route in routes),
                "access_route_count": len(routes),
                "frequencies": _route_frequencies(field),
                "pulse_names": sorted(
                    {
                        _text(route.get("pulse_name"))
                        for route in routes
                        if _text(route.get("pulse_name"))
                    }
                ),
                "canonical_contract_version": _text(
                    (field.get("canonical_definition") or {}).get("contract_version")
                ),
                "cost": copy.deepcopy(dict(field.get("cost") or {})),
                "quality": copy.deepcopy(dict(field.get("quality") or {})),
                "source_rank": source_rank,
            }
            groups.setdefault(canonical_name, []).append(entry)
            definitions[canonical_name] = _text(field.get("definition"))
            canonical_definitions[canonical_name] = copy.deepcopy(
                dict(field.get("canonical_definition") or {})
            )
            pulse_definitions[canonical_name] = copy.deepcopy(
                dict(field.get("attas_pulse_definition") or {})
            )
            matches[canonical_name] = field.get("match") if isinstance(field.get("match"), Mapping) else {}

    learned_scores = field_source_scores if isinstance(field_source_scores, Mapping) else {}
    comparisons: list[Dict[str, Any]] = []
    for canonical_name, entries in groups.items():
        def _field_rank(item: Mapping[str, Any]) -> tuple[float, int]:
            key = field_learning_key(item.get("source_id"), item.get("field_id"), canonical_name)
            try:
                learned = float(learned_scores.get(key) or 0.0)
            except (TypeError, ValueError):
                learned = 0.0
            return -learned, int(item.get("source_rank") or 0)

        entries.sort(key=_field_rank)
        types = {_text(item.get("type")) for item in entries if _text(item.get("type"))}
        units = {_text(item.get("unit")) for item in entries if _text(item.get("unit"))}
        vendor_names = {name for item in entries for name in item.get("vendor_field_names") or []}
        definition_sources = {_text(item.get("definition_source")) for item in entries}
        direct_access = {bool(item.get("direct_access")) for item in entries}
        frequency_profiles = {tuple(item.get("frequencies") or []) for item in entries}
        differences: list[str] = []
        if len(vendor_names) > 1:
            differences.append(f"Vendor labels differ: {', '.join(sorted(vendor_names))}.")
        if len(types) > 1:
            differences.append(f"Reported types differ: {', '.join(sorted(types))}.")
        if len(units) > 1:
            differences.append(f"Units differ: {', '.join(sorted(units))}.")
        if len(definition_sources) > 1:
            differences.append(
                "Definition provenance differs by source: "
                f"{', '.join(sorted(definition_sources))}."
            )
        if len(direct_access) > 1:
            differences.append("Direct execution is available from only some sources.")
        if len(frequency_profiles) > 1:
            differences.append("Available observation frequencies differ by source.")
        compatibility = "different" if len(types) > 1 or len(units) > 1 else "equivalent" if len(entries) > 1 else "single_source"
        coverage = "all_sources" if available_source_ids and len({item["source_id"] for item in entries}) == len(set(available_source_ids)) else "partial"
        recommended = entries[0]
        comparisons.append(
            {
                "canonical_name": canonical_name,
                "definition": definitions.get(canonical_name, ""),
                "canonical_definition": canonical_definitions.get(canonical_name, {}),
                "attas_pulse_definition": pulse_definitions.get(canonical_name, {}),
                "semantic_type": _text(_CANONICAL_FIELDS.get(canonical_name, {}).get("semantic_type") or canonical_name),
                "requested": bool(matches.get(canonical_name, {}).get("requested")),
                "match_score": int(matches.get(canonical_name, {}).get("score") or 0),
                "source_count": len({item["source_id"] for item in entries}),
                "coverage": coverage,
                "compatibility": compatibility,
                "differences": differences,
                "sources": [{key: value for key, value in item.items() if key != "source_rank"} for item in entries],
                "recommended_source_id": recommended["source_id"] if include_recommendation else "",
                "recommendation": (
                    f"Prefer {recommended['source_name']} for this field based on the current source ranking; "
                    "confirm provider methodology and units before combining observations."
                    if include_recommendation
                    else ""
                ),
            }
        )

    comparisons.sort(
        key=lambda item: (
            int(item.get("match_score") or 0),
            int(item.get("source_count") or 0),
            -_FIELD_ORDER.get(_text(item.get("canonical_name")), 10_000),
        ),
        reverse=True,
    )
    return comparisons[: max(1, int(limit or 1))]


__all__ = [
    "ATTAS_DATA_FETCH_CONTRACT_VERSION",
    "ATTAS_DATA_FETCH_PULSE_ADDRESS",
    "ATTAS_DATA_FETCH_PULSE_NAME",
    "CANONICAL_FIELD_CONTRACT_VERSION",
    "ENDPOINT_RESULT_FORMAT",
    "FIELD_RESULT_FORMAT",
    "attach_result_sets",
    "build_attas_pulse_access",
    "build_data_fetch_contract",
    "build_field_comparisons",
    "build_field_results",
    "canonical_field_definition",
    "canonical_field_name",
    "canonicalize_data",
    "enrich_dataset_fields",
    "enrich_field",
    "fields_from_schema",
    "field_learning_key",
]
