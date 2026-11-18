"""Taiwan-specific public API specifications and executable adapters.

The exchange and central-bank sources are public HTTPS APIs.  FinMind accepts
an optional bearer token.  Fugle requires a user-owned API key and also exposes
authenticated WebSocket market-data channels.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import re
from typing import Any, Mapping, Sequence, Tuple

from .connections import execute_financial_provider_endpoint, provider_endpoint_is_executable
from .models import EndpointExecutionResult, EndpointParameterSpec, EndpointSpec, VendorSpecSource
from .parsers import AbstractSpecParser, PublicOpenAPISpecParser


TAIWAN_SOURCE_IDS = (
    "twse",
    "tpex",
    "taifex",
    "finmind",
    "taiwan_central_bank",
    "fugle",
)

TAIWAN_SOURCE_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "twse": {
        "source_name": "TWSE Data Source",
        "provider": "Taiwan Stock Exchange",
        "description": "Complete public TWSE OpenAPI catalog and executable Taiwan listed-market data access.",
        "coverage": ["Taiwan equities", "ETFs", "indices", "listed-company disclosures", "market statistics"],
        "authentication": "No API key is required for the public TWSE OpenAPI.",
        "authentication_required": False,
    },
    "tpex": {
        "source_name": "TPEx Data Source",
        "provider": "Taipei Exchange",
        "description": "Complete public TPEx OpenAPI catalog and executable OTC, emerging-stock, and bond-market data access.",
        "coverage": ["Taiwan OTC equities", "emerging stocks", "bonds", "ETFs", "market statistics"],
        "authentication": "No API key is required for the public TPEx OpenAPI.",
        "authentication_required": False,
    },
    "taifex": {
        "source_name": "TAIFEX Data Source",
        "provider": "Taiwan Futures Exchange",
        "description": "Complete public TAIFEX OpenAPI catalog and executable futures, options, clearing, and statistics access.",
        "coverage": ["Taiwan futures", "options", "open interest", "institutional trading", "clearing statistics"],
        "authentication": "No API key is required for the public TAIFEX OpenAPI.",
        "authentication_required": False,
    },
    "finmind": {
        "source_name": "FinMind Data Source",
        "provider": "FinMind",
        "description": "Complete FinMind v4 public data API, including every documented dataset selector and executable access.",
        "coverage": ["Taiwan equities", "fundamentals", "ownership", "futures", "options", "convertible bonds", "macro", "international equities"],
        "authentication": "A token is optional; anonymous access is rate limited and a free registered token raises the limit.",
        "authentication_required": False,
    },
    "taiwan_central_bank": {
        "source_name": "Taiwan Central Bank Data Source",
        "provider": "Central Bank of the Republic of China (Taiwan)",
        "description": "Every series code in the official central-bank statistics API guide, with direct executable JSON retrieval.",
        "coverage": ["Taiwan macroeconomics", "exchange rates", "money", "banking", "interest rates", "payments", "balance of payments"],
        "authentication": "No API key is required for the public central-bank statistics API.",
        "authentication_required": False,
    },
    "fugle": {
        "source_name": "Fugle Data Source",
        "provider": "Fugle",
        "description": "All documented Fugle stock and futures/options REST operations plus executable live WebSocket channels.",
        "coverage": ["Taiwan equities", "ETFs", "indices", "futures", "options", "corporate actions", "ownership", "institutional trading", "streaming quotes"],
        "authentication": "A user-owned Fugle market-data API key and endpoint entitlement are required.",
        "authentication_required": True,
    },
}


FUGLE_STREAM_SCHEMAS: Mapping[str, Mapping[str, str]] = {
    "fugle.stock.trades.v1": {"product": "stock", "channel": "trades"},
    "fugle.stock.candles.v1": {"product": "stock", "channel": "candles"},
    "fugle.stock.books.v1": {"product": "stock", "channel": "books"},
    "market.quotes.v1": {"product": "stock", "channel": "aggregates"},
    "fugle.stock.indices.v1": {"product": "stock", "channel": "indices"},
    "fugle.futopt.trades.v1": {"product": "futopt", "channel": "trades"},
    "fugle.futopt.candles.v1": {"product": "futopt", "channel": "candles"},
    "fugle.futopt.books.v1": {"product": "futopt", "channel": "books"},
    "fugle.futopt.aggregates.v1": {"product": "futopt", "channel": "aggregates"},
}

FUGLE_STREAM_CAPABILITY: Mapping[str, Any] = {
    "path": "/api/streams/fugle/market-data",
    "schemas": list(FUGLE_STREAM_SCHEMAS),
    "cost": 0,
    "currency": "plaza_point",
    "transport": "sse",
    "fields": ["symbol", "last", "changePercent", "volume", "observedAt", "currency"],
    "replay": False,
    "license": "Fugle market-data terms and exchange entitlements",
    "maxSymbols": 5,
    "credentialRequired": True,
    "credentialTransport": "user-terminal-direct",
}


def taiwan_spec_sources(vendor_id: str) -> Tuple[VendorSpecSource, ...]:
    """Return the approved official specification source for one Taiwan vendor."""
    sources: Mapping[str, VendorSpecSource] = {
        "twse": VendorSpecSource(
            source_id="twse-official-openapi",
            vendor_id="twse",
            kind="website",
            location="https://openapi.twse.com.tw/v1/swagger.json",
            media_type="application/json",
            description="Official TWSE Swagger specification.",
            options={"allowed_hosts": ["openapi.twse.com.tw"], "authentication_required": False, "max_bytes": 8 * 1024 * 1024, "timeout_sec": 120},
        ),
        "tpex": VendorSpecSource(
            source_id="tpex-official-openapi",
            vendor_id="tpex",
            kind="website",
            location="https://www.tpex.org.tw/openapi/swagger.json",
            media_type="application/json",
            description="Official Taipei Exchange OpenAPI specification.",
            options={"allowed_hosts": ["www.tpex.org.tw", "tpex.org.tw"], "authentication_required": False, "use_curl": True, "curl_resume": True, "curl_resume_attempts": 8, "max_bytes": 8 * 1024 * 1024, "timeout_sec": 180},
        ),
        "taifex": VendorSpecSource(
            source_id="taifex-official-openapi",
            vendor_id="taifex",
            kind="website",
            location="https://openapi.taifex.com.tw/swagger.json",
            media_type="application/json",
            description="Official Taiwan Futures Exchange OpenAPI specification.",
            options={"allowed_hosts": ["openapi.taifex.com.tw"], "authentication_required": False, "max_bytes": 8 * 1024 * 1024, "timeout_sec": 120},
        ),
        "finmind": VendorSpecSource(
            source_id="finmind-official-openapi",
            vendor_id="finmind",
            kind="website",
            location="https://raw.githubusercontent.com/FinMind/FinMind.github.io/master/openapi.yaml",
            media_type="application/yaml",
            description="Official FinMind v4 OpenAPI specification.",
            options={"allowed_hosts": ["raw.githubusercontent.com"], "api_hosts": ["api.finmindtrade.com"], "authentication_required": False, "max_bytes": 2 * 1024 * 1024, "timeout_sec": 120},
        ),
        "taiwan_central_bank": VendorSpecSource(
            source_id="taiwan-central-bank-official-api-guide",
            vendor_id="taiwan_central_bank",
            kind="website",
            location="https://cpx.cbc.gov.tw/Data/ExportToAPIInfo",
            media_type="application/pdf",
            description="Official Taiwan central-bank statistics API guide and complete item-code table.",
            options={"allowed_hosts": ["cpx.cbc.gov.tw"], "extract_pdf_text": True, "authentication_required": False, "max_bytes": 16 * 1024 * 1024, "timeout_sec": 120},
        ),
        "fugle": VendorSpecSource(
            source_id="fugle-official-market-data-docs",
            vendor_id="fugle",
            kind="website",
            location="https://developer.fugle.tw/llms.txt",
            media_type="text/plain",
            description="Official Fugle documentation index and linked REST/WebSocket market-data specifications.",
            options={
                "allowed_hosts": ["developer.fugle.tw"],
                "api_hosts": ["api.fugle.tw"],
                "authentication_required": True,
                "follow_markdown_links": True,
                "follow_link_path_prefix": "/docs/data",
                "follow_link_limit": 80,
                "max_bytes": 32 * 1024 * 1024,
                "timeout_sec": 180,
            },
        ),
    }
    normalized = str(vendor_id or "").strip().lower().replace("-", "_")
    return (sources[normalized],) if normalized in sources else ()


class _TaiwanOpenAPISpecParser(PublicOpenAPISpecParser):
    VERSION = "1"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        parsed = super().parse(source, content)
        result = []
        for endpoint in parsed:
            extensions = dict(endpoint.extensions)
            extensions.update({"attas.region": "TW", "attas.specification_only": False})
            result.append(
                replace(
                    endpoint,
                    executable=provider_endpoint_is_executable(source.vendor_id, endpoint.operation),
                    quality={**dict(endpoint.quality), "region": "TW"},
                    extensions=extensions,
                )
            )
        return tuple(result)


class TWSESpecParser(_TaiwanOpenAPISpecParser):
    VENDOR_ID = "twse"


class TPExSpecParser(_TaiwanOpenAPISpecParser):
    VENDOR_ID = "tpex"


class TAIFEXSpecParser(_TaiwanOpenAPISpecParser):
    VENDOR_ID = "taifex"


class FinMindSpecParser(_TaiwanOpenAPISpecParser):
    """Parse data routes, omitting the credential-issuing login helper."""

    VENDOR_ID = "finmind"
    VERSION = "5"
    _ENDPOINT_DATASETS = {
        "/datalist": (
            "CrudeOilPrices",
            "CurrencyCirculation",
            "ExchangeRate",
            "GovernmentBonds",
            "GovernmentBondsYield",
            "InterestRate",
            "TaiwanExchangeRate",
        ),
        "/translation": (
            "RawMaterialFuturesPrices",
            "TaiwanFutures",
            "TaiwanOption",
            "TaiwanStockBalanceSheet",
            "TaiwanStockCashFlowsStatement",
            "TaiwanStockDividend",
            "TaiwanStockFinancialStatements",
            "TaiwanStockInstitutionalInvestorsBuySell",
            "TaiwanStockMarginPurchaseShortSale",
            "TaiwanStockShareholding",
            "TaiwanStockTotalMarginPurchaseShortSale",
            "USStockPrice",
        ),
    }
    _DATASET_RE = re.compile(
        r"(?:Taiwan[A-Za-z0-9_]*|taiwan_[a-z0-9_]+|USStock[A-Za-z0-9_]*|UKStock[A-Za-z0-9_]*|EuropeStock[A-Za-z0-9_]*|JapanStock[A-Za-z0-9_]*|InterestRate|GoldPrice|CrudeOilPrices|GovernmentBondsYield|CnnFearGreedIndex)"
    )

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        from pathlib import Path
        definitions = json.loads((Path(__file__).resolve().parents[1] / 'catalogs/finmind/dataset-definitions.json').read_text())['datasets']
        document = self._load_document(content)
        raw_dataset_description = ""
        try:
            raw_parameters = document["paths"]["/data"]["get"]["parameters"]
            raw_dataset_description = next(
                str(item.get("description") or "")
                for item in raw_parameters
                if isinstance(item, Mapping) and item.get("name") == "dataset"
            )
        except (KeyError, StopIteration, TypeError):
            pass
        endpoints = []
        for endpoint in super().parse(source, content):
            if endpoint.operation == "/login":
                continue
            parameters = []
            for parameter in endpoint.parameters:
                if parameter.name == "dataset":
                    values = self._ENDPOINT_DATASETS.get(endpoint.operation) or tuple(
                        value
                        for value in dict.fromkeys(self._DATASET_RE.findall(raw_dataset_description or parameter.description))
                        if value != "Taiwan"
                    )
                    if endpoint.operation == '/data':
                        values = tuple(dict.fromkeys([*values, *(d['dataset'] for d in definitions)]))
                    schema = dict(parameter.schema)
                    if values:
                        schema["enum"] = list(values)
                    parameter = replace(parameter, schema=schema)
                parameters.append(parameter)
            endpoints.append(
                replace(
                    endpoint,
                    parameters=tuple(parameters),
                    authentication={
                        "scheme": "optional_bearer",
                        "provider_scheme": "bearer",
                        "required": False,
                        "credential_reference": "connection_id",
                    },
                )
            )
        if not endpoints:
            raise ValueError("FinMind OpenAPI contains no executable data routes")
        data_endpoint = next((e for e in endpoints if e.operation == '/data'), None)
        if data_endpoint:
            for dataset in definitions:
                parameters = tuple(replace(p, schema={**dict(p.schema), 'const':dataset['dataset']},
                    description='Dataset selector fixed by this operation.') if p.name == 'dataset' else p
                    for p in data_endpoint.parameters)
                # Separate dataset definitions retain the provider's raw field
                # names. Financial meanings belong to source model mappings.
                schema = {'type':'object','properties':{'status':{'type':'integer'},'msg':{'type':'string'},
                    'data':{'type':'array','items':{'type':'object','properties':{name:{} for name in dataset['columns']}}}}}
                endpoints.append(replace(data_endpoint, endpoint_id='finmind.dataset.'+dataset['dataset'],
                    description='FinMind '+dataset['dataset']+'; access tier: '+dataset['tier'],
                    parameters=parameters, response_schema=schema,
                    extensions={**dict(data_endpoint.extensions),'attas.dataset':dataset['dataset'],
                                'attas.access_tier':dataset['tier']}))
        return tuple(endpoints)


_CBC_CODE_RE = re.compile(r"(?:[A-Z][A-Z0-9]{1,6}[YQMDmＭ][0-9]{2,3}|FL0[12]_cn)")


def _cbc_response_schema() -> Mapping[str, Any]:
    return {
        "type": "object",
        "properties": {
            "meta": {
                "type": "object",
                "description": "Series metadata supplied by the central bank.",
                "properties": {
                    "title": {"type": "string"},
                    "sender": {"type": "string"},
                    "prepared": {"type": "string"},
                    "filename": {"type": "string"},
                    "links": {"type": "string"},
                    "note": {"type": "string"},
                    "refperiod": {"type": "string"},
                    "last_updated": {"type": "string"},
                    "units": {"type": "string"},
                    "matrix": {"type": "string"},
                },
            },
            "data": {
                "type": "object",
                "properties": {
                    "dataSets": {
                        "type": "array",
                        "description": "Observation rows in the order defined by structure.",
                        "items": {"type": "array", "items": {"type": ["string", "number", "null"]}},
                    },
                    "structure": {
                        "type": "object",
                        "description": "Dimension labels used to interpret each observation column.",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"data": {"type": "string"}}},
                        },
                    },
                },
                "required": ["dataSets", "structure"],
            },
        },
        "required": ["meta", "data"],
    }


class TaiwanCentralBankSpecParser(AbstractSpecParser):
    VERSION = "2"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id == "taiwan_central_bank"

    @staticmethod
    def _series(content: str) -> Sequence[tuple[str, str]]:
        ignored = {"項目名稱", "期別", "項目代號"}
        pending: list[str] = []
        current_name = "Central-bank statistical series"
        values: list[tuple[str, str]] = []
        for raw_line in content.splitlines():
            line = " ".join(raw_line.split())
            if not line or line in ignored or line.isdigit():
                continue
            matches = _CBC_CODE_RE.findall(line)
            if not matches:
                if not any(marker in line for marker in ("API", "URL", "https://", "說明文件", "中央銀行統計 資料庫")):
                    pending.append(line)
                    pending = pending[-3:]
                continue
            prefix = _CBC_CODE_RE.sub("", line)
            prefix = re.sub(r"(?:^|\s)(?:年|月|日|季)(?:\s|$)", " ", prefix)
            prefix = " ".join(prefix.split()).strip("-–—:：")
            if prefix:
                current_name = " ".join([*pending, prefix]).strip()
            pending.clear()
            for code in matches:
                values.append((code.replace("Ｍ", "M"), current_name))
        unique: dict[str, str] = {}
        for code, name in values:
            unique.setdefault(code, name or f"Central-bank series {code}")
        return tuple(sorted(unique.items()))

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        series = self._series(content)
        if not series:
            raise ValueError("No central-bank API item codes were found in the official guide")
        endpoints = []
        frequency_names = {"Y": "annual", "Q": "quarterly", "M": "monthly", "D": "daily"}
        for code, name in series:
            frequency = next((frequency_names[key] for key in frequency_names if re.search(key + r"\d{2,3}$", code, re.I)), "published")
            endpoints.append(
                EndpointSpec(
                    endpoint_id=f"taiwan_central_bank.{code.lower()}",
                    vendor_id="taiwan_central_bank",
                    name=f"{name} ({frequency})",
                    description=f"Complete {frequency} central-bank statistical dataset for official item code {code}.",
                    category="Taiwan central-bank statistics",
                    transport="https",
                    operation="/API/DataAPI/Get",
                    parameters=(
                        EndpointParameterSpec(
                            name="FileName",
                            location="query",
                            description="Official central-bank item code.",
                            required=False,
                            schema={"type": "string", "const": code},
                            default=code,
                            examples=(code,),
                        ),
                    ),
                    response_schema=_cbc_response_schema(),
                    authentication={"scheme": "public_provider", "required": False},
                    cost={"model": "public_access", "amount": 0, "currency": "TWD", "unit": "request", "provider_cost": "No API key is required.", "notes": []},
                    quality={"status": "provider_documented", "region": "TW", "frequency": frequency, "notes": ["The API returns the complete dataset for the selected item code."]},
                    documentation_url=source.location,
                    executable=True,
                    extensions={"attas.schema_provenance": "official_api_guide", "attas.region": "TW", "cbc.item_code": code, "attas.http_method": "GET", "attas.base_url": "https://cpx.cbc.gov.tw"},
                )
            )
        return tuple(endpoints)


def _markdown_plain(value: str) -> str:
    value = value.replace("\\*", "*").replace("\\[", "[").replace("\\]", "]")
    # Underscores inside a word belong to it (`start_date`); only emphasis markers go.
    return re.sub(r"[`*]|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", "", value).strip()


def _markdown_table(content: str, heading: str) -> list[tuple[str, str, str, bool]]:
    match = re.search(rf"^##\s+{re.escape(heading)}[^\n]*\n(.*?)(?=^##\s+|\Z)", content, flags=re.I | re.M | re.S)
    if not match:
        return []
    rows = []
    header: list[str] = []
    for line in match.group(1).splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= {"-", ":", " "}:
            continue
        if cells[0].lower() == "name":
            header = cells
            continue
        required = "\\*" in cells[0] or cells[0].rstrip().endswith("*")
        # Columns after Description (such as 適用 timeframe) keep their header as a label.
        extra = len(header) - 3 if len(header) > 3 and len(cells) == len(header) else 0
        description = "|".join(cells[2:len(cells) - extra])
        for label, value in zip(header[len(cells) - extra:], cells[len(cells) - extra:]):
            if value and value not in {"—", "-"}:
                description += f"（{_markdown_plain(label)}：{value}）"
        description = description.replace("\\*", "*").replace("\\[", "[").replace("\\]", "]").strip()
        rows.append((_markdown_plain(cells[0]).rstrip("*"), _markdown_plain(cells[1]), description, required))
    return rows


def _json_type(raw_type: str) -> Mapping[str, Any]:
    value = raw_type.lower().replace(" ", "")
    is_array = value.endswith("[]")
    value = value[:-2] if is_array else value
    scalar: Mapping[str, Any]
    if value in {"number", "float", "double"}:
        scalar = {"type": "number"}
    elif value in {"integer", "int"}:
        scalar = {"type": "integer"}
    elif value in {"boolean", "bool"}:
        scalar = {"type": "boolean"}
    elif value == "object":
        scalar = {"type": "object", "properties": {}}
    else:
        scalar = {"type": "string"}
    return {"type": "array", "items": scalar} if is_array else scalar


def _response_schema(rows: Sequence[tuple[str, str, str, bool]]) -> Mapping[str, Any]:
    root: dict[str, Any] = {"type": "object", "properties": {}}
    for raw_path, raw_type, description, required in rows:
        parts = [item for item in raw_path.split(".") if item]
        if not parts:
            continue
        node = root
        for index, raw_part in enumerate(parts):
            part = re.sub(r"\[\d+\]", "", raw_part)
            array_path = bool(re.search(r"\[\d+\]", raw_part))
            props = node.setdefault("properties", {})
            prop = props.setdefault(part, {})
            last = index == len(parts) - 1
            if last:
                declared = dict(_json_type(raw_type))
                if description:
                    declared["description"] = description
                if prop.get("properties") and declared.get("type") == "object":
                    declared["properties"] = prop["properties"]
                if prop.get("items") and declared.get("type") == "array":
                    declared["items"] = prop["items"]
                prop.update(declared)
                if required:
                    node.setdefault("required", [])
                    if part not in node["required"]:
                        node["required"].append(part)
                continue
            if array_path or prop.get("type") == "array":
                prop.setdefault("type", "array")
                prop.setdefault("items", {"type": "object", "properties": {}})
                node = prop["items"]
            else:
                prop.setdefault("type", "object")
                prop.setdefault("properties", {})
                node = prop
    return root


def _inferred_schema(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": _inferred_schema(value[0]) if value else {}}
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {str(key): _inferred_schema(item) for key, item in value.items()},
            "required": [str(key) for key in value],
        }
    return {}


def _example_response_schema(page: str) -> Mapping[str, Any]:
    for block in reversed(re.findall(r"```json\s*(.*?)```", page, flags=re.I | re.S)):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping) and isinstance(value.get("data"), Mapping):
            value = value["data"]
        schema = _inferred_schema(value)
        if schema:
            return schema
    return {}


def _parameter_specs(rows: Sequence[tuple[str, str, str, bool]], route: str, *, channel: str = "") -> Tuple[EndpointParameterSpec, ...]:
    parameters = []
    for name, raw_type, description, required in rows:
        schema = dict(_json_type(raw_type))
        candidates = tuple(dict.fromkeys(re.findall(r"`([^`]+)`", description)))
        examples: tuple[Any, ...] = ()
        default: Any = None
        # Only a quoted value is a default; "預設為今日" or "預設 30 天前" only describe one.
        default_match = re.search(r"default\s*:\s*(?:`([^`]+)`|([^，。;；,\s]+))|(?:預設|省略時)(?:為|回傳)?\s*`([^`]+)`", description, re.I)
        if default_match:
            default = next((item for item in default_match.groups() if item is not None), None)
            if schema.get("type") == "boolean" and str(default).lower() in {"true", "false"}:
                default = str(default).lower() == "true"
        if any("," in item for item in candidates):
            # A comma-separated selection such as `fields`: any of the listed choices, joined by commas.
            listed = re.search(r"可選(.*?)(?:。|$)", description, re.S)
            choices = list(dict.fromkeys(re.findall(r"`([^`,]+)`", listed.group(1)))) if listed else []
            if choices:
                choice = "|".join(re.escape(item) for item in choices)
                schema["pattern"] = f"^(?:{choice})(?:,(?:{choice}))*$"
                examples = (default,) if default else (choices[0],)
        elif candidates and any(marker in description for marker in ("可選", "頻道", "channel")):
            schema["enum"] = list(candidates)
        if name == "channel" and channel:
            schema = {"type": "string", "const": channel}
            default = channel
            required = False
        parameters.append(
            EndpointParameterSpec(
                name=name,
                location="path" if "{" + name + "}" in route else "query",
                description=description,
                required=required,
                schema=schema,
                default=default,
                examples=examples,
            )
        )
    return tuple(parameters)


class FugleSpecParser(AbstractSpecParser):
    """Normalize every endpoint page linked by Fugle's official LLM index."""

    VERSION = "5"
    _SECTION_RE = re.compile(r"<!-- SOURCE URL: (https://developer\.fugle\.tw/[^ ]+) -->\n(.*?)(?=\n\n<!-- SOURCE URL:|\Z)", re.S)
    # "屬於 **開發者** 及 **進階用戶** 方案的專屬功能！" marks a plan-only endpoint.
    _PLAN_RE = re.compile(r"^屬於.+方案的專屬功能！?[ \t]*$", re.M)
    _ADMONITIONS = {"info", "tip", "note", "caution", "warning", "danger"}

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id == "fugle"

    @classmethod
    def _name_and_description(cls, page: str) -> tuple[str, str]:
        title = re.search(r"^#\s+(.+)$", page, re.M)
        name = _markdown_plain(title.group(1)) if title else "Fugle market data"
        start = title.end() if title else 0
        description = ""
        for line in page[start:].splitlines():
            if line.strip().lower() in cls._ADMONITIONS or cls._PLAN_RE.match(line):
                continue
            value = _markdown_plain(line)
            if value and not value.startswith(("#", "[", "```", "|")):
                description = value
                break
        return name, description or name

    @classmethod
    def _plan_notes(cls, page: str) -> list[str]:
        return [_markdown_plain(item) for item in cls._PLAN_RE.findall(page)][:1]

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        endpoints: list[EndpointSpec] = []
        seen: set[str] = set()
        for url, page in self._SECTION_RE.findall(content):
            path = url.split("developer.fugle.tw", 1)[1]
            product = "futopt" if "/data-futopt/" in path else "stock"
            is_websocket = "/websocket-api/market-data-channels/" in path
            is_rest = "/http-api/" in path and "/getting-started" not in path
            if not (is_websocket or is_rest):
                continue
            name, description = self._name_and_description(page)
            response_rows = _markdown_table(page, "Response")
            schema = _response_schema(response_rows) if response_rows else _example_response_schema(page)
            if not schema:
                continue
            if is_websocket:
                channel = path.rsplit("/", 1)[-1].removesuffix(".md")
                if channel not in ({"trades", "candles", "books", "aggregates", "indices"} if product == "stock" else {"trades", "candles", "books", "aggregates"}):
                    continue
                operation = f"streaming:{product}:{channel}"
                endpoint_id = f"fugle.{product}.streaming.{channel}"
                parameter_rows = _markdown_table(page, "Parameters")
                if not parameter_rows:
                    parameter_rows = [
                        ("channel", "string", f"Documented Fugle channel `{channel}`.", True),
                        ("symbol", "string", "Provider instrument symbol.", True),
                    ]
                parameters = _parameter_specs(parameter_rows, operation, channel=channel)
                base_url = f"wss://api.fugle.tw/marketdata/{'v1.1' if product == 'futopt' else 'v1.0'}/{product}/streaming"
                transport = "websocket"
                category = f"{product} live market data"
                extensions = {
                    "attas.schema_provenance": "official_markdown_tables",
                    "attas.region": "TW",
                    "attas.mode": "stream",
                    "fugle.product": product,
                    "fugle.channel": channel,
                    "fugle.websocket_url": base_url,
                }
            else:
                route_match = re.search(r"\bGET\s+(/[^\s`]+)", page)
                if not route_match:
                    continue
                operation = route_match.group(1).rstrip("/") or "/"
                endpoint_id = "fugle." + product + "." + re.sub(r"[^a-z0-9]+", ".", operation.lower()).strip(".").replace("symbol", "by_symbol")
                parameters = _parameter_specs(_markdown_table(page, "Parameters"), operation)
                base_url = f"https://api.fugle.tw/marketdata/v1.0/{product}"
                transport = "https"
                channel = ""
                category = f"{product} market data"
                extensions = {
                    "attas.schema_provenance": "official_markdown_tables",
                    "attas.region": "TW",
                    "attas.http_method": "GET",
                    "attas.base_url": base_url,
                    "fugle.product": product,
                }
            if endpoint_id in seen:
                raise ValueError(f"Duplicate Fugle endpoint identifier: {endpoint_id}")
            seen.add(endpoint_id)
            endpoints.append(
                EndpointSpec(
                    endpoint_id=endpoint_id,
                    vendor_id="fugle",
                    name=f"Fugle {product} {name}",
                    description=description,
                    category=category,
                    transport=transport,
                    operation=operation,
                    parameters=parameters,
                    response_schema=schema,
                    authentication={"scheme": "api_key", "required": True, "credential_reference": "connection_id", "header": "X-API-KEY"},
                    cost={"model": "provider_terms", "amount": 0, "currency": "TWD", "unit": "subscription" if is_websocket else "request", "provider_cost": "Availability depends on the user's Fugle market-data plan.", "notes": self._plan_notes(page)},
                    quality={"status": "provider_documented", "region": "TW", "realtime": is_websocket or "/intraday/" in operation, "notes": ["Fugle and exchange entitlements determine actual coverage."]},
                    documentation_url=url,
                    executable=True,
                    extensions=extensions,
                )
            )
        if not endpoints:
            raise ValueError("No executable Fugle market-data operations were found")
        return tuple(sorted(endpoints, key=lambda item: item.endpoint_id))


def execute_taiwan_http_endpoint(
    source_id: str,
    endpoint: EndpointSpec,
    parameters: Mapping[str, Any],
    api_key: str = "",
    *,
    http_client: Any = None,
) -> EndpointExecutionResult:
    """Execute a normalized Taiwan REST endpoint through the shared allowlist."""
    values = dict(parameters)
    if source_id == "taiwan_central_bank":
        values.setdefault("FileName", str(endpoint.extensions.get("cbc.item_code") or ""))
    return execute_financial_provider_endpoint(
        source_id,
        asdict(endpoint),
        values,
        api_key,
        http_client=http_client,
    )


def execute_fugle_websocket_endpoint(
    endpoint: EndpointSpec,
    parameters: Mapping[str, Any],
    api_key: str,
    *,
    websocket_factory: Any = None,
) -> EndpointExecutionResult:
    """Fetch the first authorized event from one documented Fugle channel."""
    if not api_key:
        return EndpointExecutionResult(status="authentication_required", endpoint_id=endpoint.endpoint_id, error="A Fugle API key is required for live streaming.")
    specs = {item.name: item for item in endpoint.parameters}
    unknown = sorted(set(parameters) - set(specs))
    if unknown:
        return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error=f"Unsupported endpoint parameter(s): {', '.join(unknown)}")
    symbol = str(parameters.get("symbol") or "").strip()
    if not symbol:
        return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error="Missing required endpoint parameter(s): symbol")
    url = str(endpoint.extensions.get("fugle.websocket_url") or "")
    if not url.startswith("wss://api.fugle.tw/marketdata/") or not url.endswith("/streaming"):
        return EndpointExecutionResult(status="not_executable", endpoint_id=endpoint.endpoint_id, error="Unsupported Fugle WebSocket route.")
    channel = str(endpoint.extensions.get("fugle.channel") or "")
    subscription = {"channel": channel, "symbol": symbol}
    for name, value in parameters.items():
        if name not in {"symbol", "channel"} and value is not None:
            subscription[name] = value
    socket = None
    try:
        if websocket_factory is None:
            import websocket

            websocket_factory = lambda target, timeout: websocket.create_connection(target, timeout=timeout)
        socket = websocket_factory(url, 20)
        socket.send(json.dumps({"event": "auth", "data": {"apikey": api_key}}))
        authenticated = False
        for _ in range(20):
            message = json.loads(socket.recv())
            event = str(message.get("event") or "")
            if event == "authenticated":
                authenticated = True
                break
            if event == "error":
                return EndpointExecutionResult(status="authentication_required", endpoint_id=endpoint.endpoint_id, error="Fugle rejected the API key or streaming entitlement.")
        if not authenticated:
            return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error="Fugle streaming authentication timed out.")
        socket.send(json.dumps({"event": "subscribe", "data": subscription}))
        for _ in range(100):
            message = json.loads(socket.recv())
            if message.get("event") == "data" and message.get("channel") == channel:
                return EndpointExecutionResult(
                    status="completed",
                    endpoint_id=endpoint.endpoint_id,
                    data=message,
                    data_schema=endpoint.response_schema,
                    cost=endpoint.cost,
                )
            if message.get("event") == "error":
                return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error="Fugle rejected the subscription parameters or entitlement.")
        return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error="No Fugle market-data event arrived before the bounded fetch ended.")
    except Exception:
        return EndpointExecutionResult(status="failed", endpoint_id=endpoint.endpoint_id, error="The Fugle WebSocket request could not be completed.")
    finally:
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass


__all__ = [
    "FUGLE_STREAM_CAPABILITY",
    "FUGLE_STREAM_SCHEMAS",
    "FinMindSpecParser",
    "FugleSpecParser",
    "TAIFEXSpecParser",
    "TAIWAN_SOURCE_DEFINITIONS",
    "TAIWAN_SOURCE_IDS",
    "TPExSpecParser",
    "TWSESpecParser",
    "TaiwanCentralBankSpecParser",
    "execute_fugle_websocket_endpoint",
    "execute_taiwan_http_endpoint",
    "taiwan_spec_sources",
]
