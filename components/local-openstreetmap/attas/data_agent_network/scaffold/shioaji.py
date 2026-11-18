"""SinoPac Securities (永豐金證券) Shioaji market-data catalog and gateway adapter.

Shioaji serves market data through ``shioaji server``, an HTTP gateway that the
account holder runs next to Attas.  The gateway reads ``SJ_API_KEY`` and
``SJ_SEC_KEY`` from its own ``.env`` and owns the single logged-in session, so
this module never receives SinoPac credentials.  Only read-only market-data,
contract-reference, and usage routes are admitted; order, portfolio, account,
and streaming routes are deliberately outside the allowlist.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import ipaddress
import json
import re
import threading
import time
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple
from urllib.parse import parse_qsl, quote, urlparse

from .models import EndpointExecutionResult, EndpointParameterSpec, EndpointSpec, VendorSpecSource
from .parsers import AbstractSpecParser


SHIOAJI_SOURCE_ID = "shioaji"
SHIOAJI_SOURCE_IDS = (SHIOAJI_SOURCE_ID,)
SHIOAJI_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080/api/v1"
SHIOAJI_GATEWAY_ENV = "SHIOAJI_GATEWAY_URL"
SHIOAJI_DOCS_REPOSITORY = "https://raw.githubusercontent.com/Sinotrade/Sinotrade.github.io/master"
SHIOAJI_DOCS_SITE = "https://sinotrade.github.io"

SHIOAJI_SOURCE_DEFINITION: Mapping[str, Any] = {
    "source_name": "Shioaji Data Source",
    "provider": "SinoPac Securities (永豐金證券) Shioaji",
    "description": (
        "SinoPac Securities Shioaji market data through the account holder's local shioaji server "
        "gateway: snapshots, historical ticks, 1-minute K-bars, rankings, margin and short balances, "
        "disposition and attention lists, and contract reference data."
    ),
    "coverage": [
        "Taiwan equities", "ETFs", "indices", "futures", "options", "warrants",
        "margin and short balances", "regulatory lists", "contract reference",
    ],
    "authentication": (
        "A SinoPac Securities account with Shioaji API access. SJ_API_KEY and SJ_SEC_KEY stay in the "
        "shioaji server's own .env; Attas only calls that gateway."
    ),
    "authentication_required": True,
}

# Published Shioaji request-rate limits (https://sinotrade.github.io/tutor/limit/).
# Exceeding them suspends the account for a minute, and repeat offences
# suspend the IP and ID, so the adapter refuses calls before the gateway does.
SHIOAJI_RATE_LIMITS: Mapping[str, Tuple[int, float]] = {
    "market_data": (50, 10.0),
}

SHIOAJI_DOCUMENTATION_PAGES = (
    "tutor/market_data/snapshot",
    "tutor/market_data/historical",
    "tutor/market_data/scanners",
    "tutor/market_data/credit_enquires",
    "tutor/market_data/short_stock_source",
    "tutor/market_data/disposition_attention",
    "tutor/contract",
    "tutor/limit",
)


@dataclass(frozen=True)
class ShioajiRoute:
    """One allowlisted read-only gateway route and the documentation that defines it."""

    method: str
    path: str
    page: str
    name: str
    description: str
    category: str
    shape: str
    attributes: Tuple[Tuple[str, str], ...] = ()
    path_parameters: Mapping[str, str] = field(default_factory=dict)
    field_descriptions: Mapping[str, str] = field(default_factory=dict)
    rate_group: str = ""
    max_items: Mapping[str, int] = field(default_factory=dict)
    timeliness: str = "reference"


_ROOTS_FIELDS = {"root": "Product root code (e.g. TXF, TXO).", "name": "Product name."}

SHIOAJI_ROUTES: Tuple[ShioajiRoute, ...] = (
    ShioajiRoute(
        "POST", "/data/snapshots", "tutor/market_data/snapshot",
        "Shioaji contract snapshots",
        "Request-time snapshot of Taiwan stock, futures, option, and index contracts: open, high, low, "
        "close, change, average price, volume and amount, best bid and ask, and volume ratio; up to 500 "
        "contracts per request.",
        "Taiwan market snapshot", "rows", (("Snapshot", ""),),
        rate_group="market_data", max_items={"contracts": 500}, timeliness="snapshot",
    ),
    ShioajiRoute(
        "POST", "/data/ticks", "tutor/market_data/historical",
        "Shioaji historical tick data",
        "Historical trade ticks for one Taiwan stock, futures, option, or index contract on a trading date: "
        "price, volume, and best bid and ask at each trade, for the whole day, a time range, or the last N ticks.",
        "Taiwan historical ticks", "columns", (("Ticks", ""),),
        rate_group="market_data", timeliness="historical",
    ),
    ShioajiRoute(
        "POST", "/data/kbars", "tutor/market_data/historical",
        "Shioaji historical 1-minute K-bars",
        "Historical 1-minute OHLCV bars (open, high, low, close, volume, amount) for one Taiwan stock, "
        "futures, option, or index contract over a date range.",
        "Taiwan historical intraday bars", "columns", (("Kbars", ""),),
        rate_group="market_data", timeliness="historical",
    ),
    ShioajiRoute(
        "POST", "/data/scanner", "tutor/market_data/scanners",
        "Shioaji stock market rankings",
        "Daily ranking of Taiwan stocks by change percent, change price, day range, volume, amount, or tick "
        "count, with OHLC, volume, best bid and ask, and bid/ask trade counts.",
        "Taiwan stock rankings", "rows", (("ScannerItem", ""),), timeliness="snapshot",
    ),
    ShioajiRoute(
        "POST", "/data/credit_enquire", "tutor/market_data/credit_enquires",
        "Shioaji margin and short balances",
        "Margin-purchase and short-sale balances and ratios for Taiwan listed and OTC stocks.",
        "Taiwan stock margin trading", "rows", (("CreditEnquire", ""),),
        rate_group="market_data", timeliness="snapshot",
    ),
    ShioajiRoute(
        "POST", "/data/short_stock_sources", "tutor/market_data/short_stock_source",
        "Shioaji borrowable shares for short selling",
        "Shares available to borrow for short selling (short stock source) for Taiwan stocks.",
        "Taiwan stock securities lending", "rows", (("ShortStockSource", ""),),
        rate_group="market_data", timeliness="snapshot",
    ),
    ShioajiRoute(
        "GET", "/data/regulatory_punish", "tutor/market_data/disposition_attention",
        "Shioaji disposition stocks",
        "Taiwan stocks currently under exchange disposition (處置股), with disposition period, matching "
        "interval, and order limits.",
        "Taiwan regulatory stock lists", "columns", (("Punish", ""),),
    ),
    ShioajiRoute(
        "GET", "/data/regulatory_notice", "tutor/market_data/disposition_attention",
        "Shioaji attention stocks",
        "Taiwan stocks announced as attention stocks (注意股), with closing price and reason.",
        "Taiwan regulatory stock lists", "columns", (("Notice", ""),),
    ),
    ShioajiRoute(
        "GET", "/data/contracts", "tutor/contract",
        "Shioaji contract list by security type",
        "Paginated list of Taiwan stock, futures, option, index, or warrant contracts with security type, "
        "exchange, and code.",
        "Taiwan contract reference", "object", (("Contract", "contracts[]"),),
        field_descriptions={
            "contracts": "Contracts on the requested page.",
            "max_page": "Last page number for the requested page size.",
            "total": "Total number of contracts of the security type.",
        },
    ),
    ShioajiRoute(
        "GET", "/data/contracts/{code}", "tutor/contract",
        "Shioaji contract lookup",
        "Resolve one Taiwan stock, futures, option, or index code to its contract identity, including the "
        "resolved target of a continuous-month futures code.",
        "Taiwan contract reference", "object", (("Contract", ""),),
        path_parameters={"code": "Product code (e.g. 2330, TXFR1, IX0001)."},
    ),
    ShioajiRoute(
        "GET", "/data/contracts/{code}/info", "tutor/contract",
        "Shioaji contract details",
        "Full reference data for one Taiwan stock, futures, option, or index contract: name, reference and "
        "limit prices, trading unit, margin and short eligibility, delivery and strike terms, and tick rules.",
        "Taiwan contract reference", "object",
        (("Contract", ""), ("StockInfo", ""), ("FuturesInfo", ""), ("OptionInfo", ""), ("IndexInfo", "")),
        path_parameters={"code": "Product code (e.g. 2330, TXFR1, IX0001)."},
    ),
    ShioajiRoute(
        "GET", "/data/contracts/futures", "tutor/contract",
        "Shioaji futures contracts",
        "Taiwan TAIFEX futures contracts for a product root or an underlying code, with delivery dates, "
        "multiplier, tick rules, and reference and limit prices.",
        "Taiwan futures contract reference", "rows", (("Contract", ""), ("FuturesInfo", "")),
    ),
    ShioajiRoute(
        "GET", "/data/contracts/futures/roots", "tutor/contract",
        "Shioaji futures products",
        "Every Taiwan TAIFEX futures product root code and name.",
        "Taiwan futures contract reference", "rows", field_descriptions=_ROOTS_FIELDS,
    ),
    ShioajiRoute(
        "GET", "/data/contracts/options", "tutor/contract",
        "Shioaji option contracts",
        "Taiwan TAIFEX option contracts for a product root, filterable by delivery month, call or put, strike "
        "range, and expiry weekday.",
        "Taiwan option contract reference", "rows", (("Contract", ""), ("OptionInfo", "")),
    ),
    ShioajiRoute(
        "GET", "/data/contracts/options/roots", "tutor/contract",
        "Shioaji option products",
        "Every Taiwan TAIFEX option product root code and name.",
        "Taiwan option contract reference", "rows", field_descriptions=_ROOTS_FIELDS,
    ),
    ShioajiRoute(
        "GET", "/data/contracts/warrants", "tutor/contract",
        "Shioaji warrants by underlying",
        "Taiwan equity warrants for an underlying code, with call or put, strike, expiry, exercise terms, "
        "barriers, and price limits.",
        "Taiwan warrant reference", "rows", (("Contract", ""), ("WarrantInfo", "")),
    ),
    ShioajiRoute(
        "GET", "/data/contracts/warrants/underlyings", "tutor/contract",
        "Shioaji warrant underlyings",
        "Taiwan stocks and indices that have warrants issued, optionally with names and warrant counts.",
        "Taiwan warrant reference", "rows",
        field_descriptions={
            "underlying_code": "Underlying product code.",
            "name": "Underlying product name.",
            "warrant_count": "Number of warrants issued on the underlying.",
        },
    ),
    ShioajiRoute(
        "GET", "/data/contracts/combo/futures", "tutor/contract",
        "Shioaji futures time-spread combos",
        "Currently valid Taiwan TAIFEX futures time-spread combo contracts for a product root, with both legs.",
        "Taiwan futures combo reference", "rows", (("sj.ComboContract", ""), ("Contract", "legs[]")),
        field_descriptions={"exchange": "Exchange that lists the combo."},
    ),
    ShioajiRoute(
        "GET", "/data/contracts/tick-bands/{rule}", "tutor/contract",
        "Shioaji futures and option tick bands",
        "Price- or premium-dependent minimum tick table for a Taiwan TAIFEX futures or option tick rule.",
        "Taiwan derivatives tick rules", "object",
        path_parameters={"rule": "Tick rule code from contract details (e.g. tw_stock_fut_price_band)."},
        field_descriptions={
            "rule": "Tick rule code.",
            "basis": "Tick basis {price, premium}.",
            "bands": "Tick bands ordered by price or premium.",
            "min": "Band lower bound (inclusive).",
            "max": "Band upper bound (exclusive); null means unbounded.",
            "tick": "Minimum tick inside the band.",
        },
    ),
    ShioajiRoute(
        "GET", "/auth/usage", "tutor/limit",
        "Shioaji API traffic usage",
        "Current Shioaji connection count and daily market-data traffic used, limit, and remaining.",
        "Shioaji account usage", "object", (("UsageStatus", ""),), timeliness="snapshot",
    ),
)


def shioaji_endpoint_id(path: str) -> str:
    """Derive the stable endpoint identifier for one gateway route template."""
    parts = []
    for segment in str(path or "").strip("/").split("/"):
        placeholder = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", segment)
        parts.append(f"by_{placeholder.group(1)}" if placeholder else segment.replace("-", "_"))
    return "shioaji." + ".".join(parts)


SHIOAJI_ROUTES_BY_ID: Mapping[str, ShioajiRoute] = {shioaji_endpoint_id(route.path): route for route in SHIOAJI_ROUTES}


def shioaji_spec_sources() -> Tuple[VendorSpecSource, ...]:
    """Return the official per-page documentation inputs for the Shioaji catalog."""
    return tuple(
        VendorSpecSource(
            source_id="shioaji-official-docs-" + page.rsplit("/", 1)[-1].replace("_", "-"),
            vendor_id=SHIOAJI_SOURCE_ID,
            kind="website",
            location=f"{SHIOAJI_DOCS_REPOSITORY}/{page}/index.md",
            media_type="text/markdown",
            description=f"Official Shioaji documentation page {SHIOAJI_DOCS_SITE}/{page}/ (published from the Sinotrade.github.io repository).",
            options={
                "allowed_hosts": ["raw.githubusercontent.com"],
                "authentication_required": True,
                "max_bytes": 2 * 1024 * 1024,
                "timeout_sec": 60,
                "shioaji.page": page,
            },
        )
        for page in SHIOAJI_DOCUMENTATION_PAGES
    )


# --- documentation parsing -------------------------------------------------

_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
_DECLARATION_RE = re.compile(r"^\s*(GET|POST)\s+/api/v1(/\S*)")
_PROSE_ROUTE_RE = re.compile(r"`(GET|POST)\s+/api/v1(/[^`\s]*)`")
_CURL_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):\d+/api/v1(/[^\s'\"\\]*)")
_CURL_BODY_RE = re.compile(r"-d\s+'(.*?)'", re.S)
_PARAMETER_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\[\]]*):\s*(.*)$")
_ATTRIBUTE_LINE_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\s*/\s*[A-Za-z_]\w*)*)\s*(?:\(([^()]*(?:\([^()]*\)[^()]*)*)\))?\s*(:)?\s+(\S.*)$"
)
_PLACEHOLDER_RE = re.compile(r"<([A-Za-z][A-Za-z0-9 ,?]*)>")
_ENUM_RE = re.compile(r"\{([^{}]+)\}")
_DEFAULT_RE = re.compile(r"\bdefaults?(?:\s+to|:)?\s+`?([A-Za-z0-9_.-]+)`?", re.I)


@dataclass(frozen=True)
class _Block:
    label: str
    code: str
    prose: str


def _plain(value: str) -> str:
    return re.sub(r"[`*_#]", "", str(value or "")).strip().rstrip(":").strip()


def _blocks(page: str) -> Tuple[_Block, ...]:
    blocks: list[_Block] = []
    cursor = 0
    for match in _FENCE_RE.finditer(page):
        prose = page[cursor:match.start()]
        lines = [line.strip() for line in prose.splitlines() if line.strip()]
        blocks.append(_Block(label=_plain(lines[-1]) if lines else "", code=match.group(1), prose=prose))
        cursor = match.end()
    return tuple(blocks)


def _curl_commands(code: str) -> Tuple[Tuple[str, str, str], ...]:
    """Return (method, path-with-query, body) for each active curl command in a block."""
    lines = code.splitlines()
    starts = [index for index, line in enumerate(lines) if re.match(r"^\s*curl\b", line)]
    commands = []
    for position, start in enumerate(starts):
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if re.match(r"^\s*(?:#|curl\b)", lines[index]) or (position + 1 < len(starts) and index == starts[position + 1]):
                end = index
                break
        text = "\n".join(lines[start:end])
        url = _CURL_URL_RE.search(text)
        if not url:
            continue
        body = _CURL_BODY_RE.search(text)
        explicit = re.search(r"-X\s+(GET|POST)\b", text)
        method = explicit.group(1) if explicit else "POST" if body else "GET"
        commands.append((method, url.group(1), body.group(1) if body else ""))
    return tuple(commands)


def _path_score(template: str, path: str) -> int | None:
    expected = template.strip("/").split("/")
    actual = path.split("?", 1)[0].strip("/").split("/")
    if len(expected) != len(actual):
        return None
    score = 0
    for want, got in zip(expected, actual):
        if want.startswith("{") and want.endswith("}"):
            if not got:
                return None
            continue
        if want != got:
            return None
        score += 1
    return score


def _match_route(method: str, path: str, routes: Sequence[ShioajiRoute]) -> ShioajiRoute | None:
    best: tuple[int, ShioajiRoute] | None = None
    for route in routes:
        if route.method != method:
            continue
        score = _path_score(route.path, path)
        if score is not None and (best is None or score > best[0]):
            best = (score, route)
    return best[1] if best else None


def _strip_ellipses(text: str) -> str:
    text = re.sub(r",\s*\.\.\.(?=\s*[,\]}])", "", text)
    return re.sub(r"(?<=[\[{])\s*\.\.\.\s*,?", "", text)


def _repair_truncated(text: str) -> str:
    """Cut an example at its last complete top-level element and close it."""
    depth = 0
    in_string = False
    escaped = False
    last_complete = -1
    opener = text[:1]
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 1:
                last_complete = index
    if last_complete < 0 or opener not in "[{":
        return text
    return text[: last_complete + 1] + ("]" if opener == "[" else "}")


def _load_example(text: str) -> Any:
    candidate = text.strip()
    if not candidate.startswith(("{", "[")):
        return None
    for attempt in (candidate, _strip_ellipses(candidate), _repair_truncated(_strip_ellipses(candidate))):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    return None


def _merge_schemas(schemas: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    present = [dict(item) for item in schemas if item]
    if not present:
        return {}
    if all(item.get("type") == "object" for item in present):
        properties: Dict[str, list] = {}
        for item in present:
            for name, value in (item.get("properties") or {}).items():
                properties.setdefault(name, []).append(value)
        return {"type": "object", "properties": {name: _merge_schemas(values) for name, values in properties.items()}}
    if all(item.get("type") == "array" for item in present):
        return {"type": "array", "items": _merge_schemas([item.get("items") or {} for item in present])}
    types = {item.get("type") for item in present}
    if types <= {"integer", "number"}:
        return {"type": "number" if "number" in types else "integer"}
    return present[0]


def _inferred_schema(value: Any) -> Dict[str, Any]:
    if value is None or value == "...":
        return {}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": _merge_schemas([_inferred_schema(item) for item in value])}
    if isinstance(value, Mapping):
        return {"type": "object", "properties": {str(key): _inferred_schema(item) for key, item in value.items()}}
    return {}


def _attribute_schema(raw_type: str, description: str, name: str) -> Dict[str, Any]:
    value = str(raw_type or "").strip()
    container = re.fullmatch(r"(?:list|List|tuple|Tuple)\[(.+)\]", value)
    if container:
        return {"type": "array", "items": _attribute_schema(container.group(1), "", name)}
    lowered = value.lower()
    if lowered in {"float", "decimal", "double"}:
        return {"type": "number"}
    if lowered in {"int", "integer"}:
        return {"type": "integer"}
    if lowered == "bool":
        return {"type": "boolean"}
    if lowered == "date":
        return {"type": "string", "format": "date"}
    if lowered == "datetime" or (not value and name == "datetime"):
        return {"type": "string", "format": "date-time"}
    if lowered in {"basecontract", "contract"}:
        return {"type": "object", "properties": {}}
    return {"type": "string"}


def _attribute_tables(blocks: Sequence[_Block]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return typed attribute tables keyed by their documentation label."""
    tables: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for block in blocks:
        entries: list[list[str]] = []
        typed = 0
        top_level = 0
        for line in block.code.splitlines():
            if not line.strip():
                continue
            if line[:1].isspace():
                if entries:
                    entries[-1][2] += " " + line.strip()
                continue
            top_level += 1
            match = _ATTRIBUTE_LINE_RE.match(line.strip())
            if not match:
                continue
            name, raw_type, colon, description = match.groups()
            if raw_type is None and "/" not in name and colon is None:
                continue
            if raw_type is not None or "/" in name:
                typed += 1
            entries.append([name, raw_type or "", description])
        if not entries or not top_level or typed < 2 or typed < 0.8 * top_level:
            continue
        table: Dict[str, Dict[str, Any]] = {}
        for name, raw_type, description in entries:
            http_name = name.split("/")[-1].strip()
            schema = _attribute_schema(raw_type, description, http_name)
            schema["description"] = " ".join(description.split())
            table[http_name] = schema
        tables.setdefault(block.label, table)
    return tables


def _parameter_lines(code: str) -> Tuple[Tuple[str, str], ...]:
    entries: list[list[str]] = []
    for line in code.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace():
            if entries:
                entries[-1][1] += " " + line.strip()
            continue
        match = _PARAMETER_LINE_RE.match(line.strip())
        if match:
            entries.append([match.group(1), match.group(2).strip()])
    return tuple((name, " ".join(description.split())) for name, description in entries)


def _enum_values(description: str) -> list[str]:
    match = _ENUM_RE.search(description)
    if not match:
        return []
    values = [item.strip().strip("'\"") for item in match.group(1).split(",")]
    if not values or any(not item or ":" in item or " " in item for item in values):
        return []
    if {item.lower() for item in values} <= {"true", "false"}:
        return []
    return values


def _default_value(description: str) -> Any:
    match = _DEFAULT_RE.search(description)
    if not match:
        return None
    raw = match.group(1).rstrip(".")
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _template_value(value: Any) -> tuple[Dict[str, Any], bool]:
    """Return (schema, optional) for one value of a documented request template."""
    if isinstance(value, str) and value.startswith("\x00"):
        token = value[1:]
        optional = token.endswith("?") or ", optional" in token
        name = token.rstrip("?").split(",", 1)[0].strip().lower()
        schema = {
            "string": {"type": "string"},
            "int": {"type": "integer"},
            "integer": {"type": "integer"},
            "float": {"type": "number"},
            "bool": {"type": "boolean"},
        }.get(name, {"type": "string"})
        return dict(schema), optional
    if isinstance(value, Mapping):
        properties: Dict[str, Any] = {}
        required: list[str] = []
        for key, item in value.items():
            schema, optional = _template_value(item)
            properties[str(key)] = schema
            if not optional:
                required.append(str(key))
        node: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            node["required"] = required
        return node, False
    if isinstance(value, list):
        items = _template_value(value[0])[0] if value else {}
        return {"type": "array", "items": items}, False
    if isinstance(value, bool):
        return {"type": "boolean", "enum": [value]}, False
    if isinstance(value, (int, float)):
        return {"type": "number", "enum": [value]}, False
    return {"type": "string", "enum": [value]}, False


def _request_template(code: str) -> Mapping[str, Any] | None:
    start = code.find("{")
    if start < 0:
        return None
    text = _PLACEHOLDER_RE.sub(lambda match: json.dumps("\x00" + match.group(1)), code[start:])
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def _nested_node(schema: Dict[str, Any], path: Sequence[str]) -> Dict[str, Any] | None:
    node = schema
    for part in path:
        if part.endswith("[]"):
            part = part[:-2]
            child = (node.get("properties") or {}).get(part)
            if not isinstance(child, dict) or child.get("type") != "array":
                return None
            node = child.setdefault("items", {"type": "object", "properties": {}})
        else:
            child = (node.get("properties") or {}).get(part)
            if not isinstance(child, dict):
                return None
            node = child
    return node


def _annotate(schema: Dict[str, Any], description: str) -> None:
    schema["description"] = description
    values = _enum_values(description)
    if values and schema.get("type") in {None, "string"}:
        schema["enum"] = values
    default = _default_value(description)
    if default is not None and "default" not in schema:
        schema["default"] = default


def _query_schema(name: str, description: str, examples: Sequence[str]) -> Dict[str, Any]:
    lowered = description.lower()
    if lowered.startswith("whether") or any(item.lower() in {"true", "false"} for item in examples):
        schema: Dict[str, Any] = {"type": "boolean"}
    elif "price" in lowered:
        schema = {"type": "number"}
    elif re.search(r"\b(?:page|number of|count)\b", lowered):
        schema = {"type": "integer"}
    else:
        schema = {"type": "string"}
    values = _enum_values(description)
    if values and schema["type"] == "string":
        schema["enum"] = values
    if description:
        schema["description"] = description
    return schema


def _example_value(raw: str, schema: Mapping[str, Any]) -> Any:
    kind = schema.get("type")
    try:
        if kind == "integer":
            return int(raw)
        if kind == "number":
            return float(raw)
    except ValueError:
        return raw
    if kind == "boolean":
        return raw.lower() == "true"
    return raw


@dataclass
class _RouteEvidence:
    declaration: str = ""
    parameters: Tuple[Tuple[str, str], ...] = ()
    example: Any = None
    request_examples: list = field(default_factory=list)
    query_examples: Dict[str, list] = field(default_factory=dict)
    path_examples: Dict[str, list] = field(default_factory=dict)
    documented: bool = False


def _route_evidence(blocks: Sequence[_Block], routes: Sequence[ShioajiRoute]) -> Dict[str, _RouteEvidence]:
    evidence: Dict[str, _RouteEvidence] = {route.path: _RouteEvidence() for route in routes}

    def record_path(route: ShioajiRoute, concrete: str) -> None:
        item = evidence[route.path]
        path, _, query = concrete.partition("?")
        for want, got in zip(route.path.strip("/").split("/"), path.strip("/").split("/")):
            if want.startswith("{") and not got.startswith(("{", "<")):
                item.path_examples.setdefault(want[1:-1], []).append(got)
        for name, value in parse_qsl(query, keep_blank_values=False):
            if not value.startswith(("{", "<")):
                item.query_examples.setdefault(name, []).append(value)

    def take_parameters(route: ShioajiRoute, index: int) -> None:
        item = evidence[route.path]
        if not item.parameters and index < len(blocks) and "parameters" in blocks[index].label.lower():
            item.parameters = _parameter_lines(blocks[index].code)

    for index, block in enumerate(blocks):
        for method, path in _PROSE_ROUTE_RE.findall(block.prose):
            route = _match_route(method, path, routes)
            if route is not None:
                evidence[route.path].documented = True
                record_path(route, path)
        declaration = _DECLARATION_RE.match(block.code)
        if declaration:
            route = _match_route(declaration.group(1), declaration.group(2), routes)
            if route is not None:
                item = evidence[route.path]
                item.documented = True
                item.declaration = item.declaration or block.code
                record_path(route, declaration.group(2))
                take_parameters(route, index + 1)
            continue
        commands = _curl_commands(block.code)
        for position, (method, path, body) in enumerate(commands):
            route = _match_route(method, path, routes)
            if route is None:
                continue
            item = evidence[route.path]
            item.documented = True
            record_path(route, path)
            if body:
                parsed = _load_example(body)
                if isinstance(parsed, Mapping):
                    item.request_examples.append(dict(parsed))
            if position == len(commands) - 1 and index + 1 < len(blocks) and blocks[index + 1].label == "Out":
                example = _load_example(blocks[index + 1].code)
                if item.example is None and example is not None:
                    item.example = example
                take_parameters(route, index + 2)
    return evidence


def _parameters_for(route: ShioajiRoute, item: _RouteEvidence) -> Tuple[EndpointParameterSpec, ...]:
    parameters: list[EndpointParameterSpec] = []
    for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", route.path):
        examples = tuple(dict.fromkeys(item.path_examples.get(name, ())))
        parameters.append(
            EndpointParameterSpec(
                name=name,
                location="path",
                description=route.path_parameters.get(name, name),
                required=True,
                schema={"type": "string"},
                examples=examples,
            )
        )
    documented = dict(item.parameters)
    if route.method == "POST":
        template = _request_template(item.declaration) if item.declaration else None
        if not template:
            return ()
        for name, value in template.items():
            schema, optional = _template_value(value)
            description = documented.get(name, "")
            prefix = f"{name}."
            for nested_name, nested_description in item.parameters:
                if nested_name.startswith((prefix, f"{name}[].")):
                    parts = nested_name.split(".")
                    node = _nested_node({"type": "object", "properties": {name: schema}}, parts[:-1])
                    if node is not None:
                        leaf = node.setdefault("properties", {}).setdefault(parts[-1], {"type": "string"})
                        _annotate(leaf, nested_description)
            if description:
                _annotate(schema, description)
            default = schema.pop("default", None)
            lowered = description.lower()
            required = not optional and default is None and not lowered.startswith("optional")
            examples = tuple(
                example[name] for example in item.request_examples if name in example
            )[:3]
            parameters.append(
                EndpointParameterSpec(
                    name=name,
                    location="body",
                    description=description or name,
                    required=required,
                    schema=schema,
                    default=default,
                    examples=examples,
                )
            )
        return tuple(parameters)
    for name, description in item.parameters:
        if "." in name or "[" in name:
            continue
        examples = tuple(dict.fromkeys(item.query_examples.get(name, ())))
        schema = _query_schema(name, description, examples)
        default = _default_value(description)
        required = "(required)" in description.lower()
        parameters.append(
            EndpointParameterSpec(
                name=name,
                location="query",
                description=description,
                required=required,
                schema=schema,
                default=None if required else default,
                examples=tuple(_example_value(value, schema) for value in examples[:3]),
            )
        )
    documented_query = {parameter.name for parameter in parameters}
    for name in item.query_examples:
        if name not in documented_query:
            values = tuple(dict.fromkeys(item.query_examples[name]))
            schema = _query_schema(name, "", values)
            parameters.append(
                EndpointParameterSpec(
                    name=name, location="query", description=name, schema=schema,
                    examples=tuple(_example_value(value, schema) for value in values[:3]),
                )
            )
    return tuple(parameters)


def _target_node(item: Dict[str, Any], target: str) -> Dict[str, Any]:
    node = item
    for part in [segment for segment in target.split(".") if segment]:
        is_array = part.endswith("[]")
        name = part[:-2] if is_array else part
        properties = node.setdefault("properties", {})
        child = properties.setdefault(name, {"type": "array" if is_array else "object"})
        if is_array:
            child["type"] = "array"
            node = child.setdefault("items", {"type": "object", "properties": {}})
            node.setdefault("type", "object")
            node.setdefault("properties", {})
        else:
            child.setdefault("type", "object")
            node = child
    return node


def _echo_description(description: str) -> str:
    """Describe a response field that echoes a request parameter."""
    text = re.sub(r"\s*\(required\)", "", description)
    text = re.sub(r"[,;]?\s*defaults? to\b.*$", "", text, flags=re.I)
    return text.strip().rstrip(",;")


def _describe_missing(node: Dict[str, Any], descriptions: Mapping[str, str]) -> None:
    for name, child in (node.get("properties") or {}).items():
        if not isinstance(child, dict):
            continue
        if not child.get("description") and descriptions.get(name):
            child["description"] = descriptions[name]
        if child.get("type") == "array" and isinstance(child.get("items"), dict):
            _describe_missing(child["items"], descriptions)
        elif child.get("type") == "object":
            _describe_missing(child, descriptions)


def _response_schema(
    route: ShioajiRoute,
    example: Any,
    tables: Mapping[str, Mapping[str, Mapping[str, Any]]],
    parameter_descriptions: Mapping[str, str],
) -> Dict[str, Any]:
    inferred = _inferred_schema(example) if example is not None else {}
    if route.shape == "rows":
        item = dict(inferred.get("items") or {}) if inferred.get("type") == "array" else {}
    else:
        item = dict(inferred) if inferred.get("type") == "object" else {}
    item.setdefault("type", "object")
    item.setdefault("properties", {})
    declared_paths: set[tuple[str, str]] = set()
    for label, target in route.attributes:
        node = _target_node(item, target)
        for name, declared in (tables.get(label) or {}).items():
            # The first table that declares a field (for example StockInfo
            # before IndexInfo in the contract-details union) defines it.
            if (target, name) in declared_paths:
                continue
            declared_paths.add((target, name))
            declared = dict(declared)
            if route.shape == "columns" and not target and declared.get("type") != "array":
                description = declared.pop("description", "")
                declared = {"type": "array", "items": declared, "description": description}
            current = node["properties"].get(name)
            if isinstance(current, dict) and current.get("type") in {"array", "object"} and declared.get("type") == current.get("type"):
                merged = dict(current)
                merged["description"] = declared.get("description", "")
                if current.get("type") == "array" and isinstance(declared.get("items"), Mapping):
                    nested = dict(current.get("items") or {})
                    for key in ("type", "format"):
                        if key in declared["items"] and not nested.get("properties"):
                            nested[key] = declared["items"][key]
                    merged["items"] = nested
                node["properties"][name] = merged
            else:
                node["properties"][name] = declared
    echoed = {name: _echo_description(description) for name, description in parameter_descriptions.items()}
    _describe_missing(item, {**echoed, **dict(route.field_descriptions)})
    if not item["properties"]:
        return {}
    if route.shape == "rows":
        return {
            "type": "object",
            "properties": {"items": {"type": "array", "description": f"{route.name} rows.", "items": item}},
            "required": ["items"],
        }
    return item


def _page_key(source: VendorSpecSource) -> str:
    configured = str((source.options or {}).get("shioaji.page") or "").strip("/")
    if configured:
        return configured
    path = urlparse(source.location).path
    marker = "/master/"
    path = path.split(marker, 1)[1] if marker in path else path
    return path.removesuffix("/index.md").strip("/")


def _quality(route: ShioajiRoute) -> Dict[str, Any]:
    notes = [
        "Served by the account holder's shioaji server gateway; SinoPac account entitlements determine coverage.",
    ]
    if route.rate_group == "market_data":
        notes.append(
            "Request-type query limited to 50 market-data calls per 10 seconds; do not poll it during trading "
            "hours as a real-time feed — use Shioaji quote subscriptions for live data."
        )
        notes.append("Once the daily traffic cap is spent, market-data queries return empty results until 08:00 the next trading day.")
    return {
        "status": "provider_documented",
        "region": "TW",
        "realtime": False,
        "timeliness": route.timeliness,
        "notes": notes,
    }


def _cost(route: ShioajiRoute) -> Dict[str, Any]:
    notes = []
    if route.rate_group == "market_data":
        notes.append("Responses count toward the daily traffic cap (500 MB to 10 GB by 30-day API trading activity).")
    return {
        "model": "provider_terms",
        "amount": 0,
        "currency": "TWD",
        "unit": "request",
        "provider_cost": "Included with a SinoPac Securities account that has Shioaji API access.",
        "notes": notes,
    }


class ShioajiSpecParser(AbstractSpecParser):
    """Normalize the official Shioaji HTTP gateway documentation pages."""

    VERSION = "1"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id == SHIOAJI_SOURCE_ID

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        page = _page_key(source)
        routes = tuple(route for route in SHIOAJI_ROUTES if route.page == page)
        if not routes:
            raise ValueError(f"No allowlisted Shioaji routes are documented on {page!r}")
        blocks = _blocks(content)
        tables = _attribute_tables(blocks)
        evidence = _route_evidence(blocks, routes)
        endpoints: list[EndpointSpec] = []
        for route in routes:
            item = evidence[route.path]
            if not item.documented:
                continue
            parameters = _parameters_for(route, item)
            if route.method == "POST" and not parameters:
                continue
            schema = _response_schema(route, item.example, tables, dict(item.parameters))
            if not schema:
                continue
            endpoints.append(
                EndpointSpec(
                    endpoint_id=shioaji_endpoint_id(route.path),
                    vendor_id=SHIOAJI_SOURCE_ID,
                    name=route.name,
                    description=route.description,
                    category=route.category,
                    transport="http",
                    operation=route.path,
                    parameters=parameters,
                    response_schema=schema,
                    authentication={
                        "scheme": "local_gateway_session",
                        "required": True,
                        "credential_reference": "shioaji_server_env",
                        "notes": "SJ_API_KEY and SJ_SEC_KEY stay in the shioaji server .env; Attas never receives them.",
                    },
                    cost=_cost(route),
                    quality=_quality(route),
                    documentation_url=f"{SHIOAJI_DOCS_SITE}/{page}/",
                    executable=True,
                    extensions={
                        "attas.schema_provenance": "official_http_examples_and_attribute_tables",
                        "attas.region": "TW",
                        "attas.http_method": route.method,
                        "attas.specification_only": False,
                        "shioaji.route": "/api/v1" + route.path,
                        "shioaji.response_shape": route.shape,
                        "shioaji.rate_group": route.rate_group,
                        "shioaji.max_items": dict(route.max_items),
                    },
                )
            )
        if not endpoints:
            raise ValueError(f"No executable Shioaji gateway routes were found on {page!r}")
        return tuple(endpoints)


# --- gateway execution -----------------------------------------------------


class ShioajiRateLimiter:
    """Sliding-window guard for Shioaji's published request-rate limits."""

    def __init__(
        self,
        limits: Mapping[str, Tuple[int, float]] = SHIOAJI_RATE_LIMITS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = dict(limits)
        self._clock = clock
        self._calls: Dict[str, deque] = {group: deque() for group in self._limits}
        self._guard = threading.Lock()

    def acquire(self, group: str) -> float:
        """Reserve one call; return 0 when allowed, else seconds until a slot frees."""
        if group not in self._limits:
            return 0.0
        count, window = self._limits[group]
        with self._guard:
            now = self._clock()
            calls = self._calls[group]
            while calls and now - calls[0] >= window:
                calls.popleft()
            if len(calls) >= count:
                return max(0.0, window - (now - calls[0]))
            calls.append(now)
            return 0.0


_GATEWAY_LIMITERS: Dict[str, ShioajiRateLimiter] = {}
_GATEWAY_LIMITERS_GUARD = threading.Lock()


def shioaji_rate_limiter(gateway_url: str) -> ShioajiRateLimiter:
    """Return the process-wide limiter shared by every source using one gateway."""
    with _GATEWAY_LIMITERS_GUARD:
        return _GATEWAY_LIMITERS.setdefault(str(gateway_url), ShioajiRateLimiter())


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_shioaji_gateway_url(value: Any = "") -> str:
    """Validate a source-owned gateway URL; plain HTTP is accepted only on loopback."""
    text = str(value or "").strip() or SHIOAJI_DEFAULT_GATEWAY_URL
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("The Shioaji gateway URL must be an http(s) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The Shioaji gateway URL must not contain credentials, a query, or a fragment.")
    if parsed.scheme == "http" and not _is_loopback(host):
        raise ValueError(
            "A plain-HTTP Shioaji gateway must run on loopback; enable HTTPS (SJ_HTTP_TLS_CERT and "
            "SJ_HTTP_TLS_KEY) for a gateway on another host."
        )
    path = parsed.path.rstrip("/") or "/api/v1"
    if not path.endswith("/api/v1"):
        raise ValueError("The Shioaji gateway URL must end with /api/v1.")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def shioaji_endpoint_is_executable(endpoint: EndpointSpec) -> bool:
    route = SHIOAJI_ROUTES_BY_ID.get(endpoint.endpoint_id)
    return bool(
        route is not None
        and endpoint.vendor_id == SHIOAJI_SOURCE_ID
        and endpoint.operation == route.path
        and str(endpoint.extensions.get("attas.http_method") or route.method).upper() == route.method
    )


_SECRET_NAMES = {
    "token", "api_key", "apikey", "api-key", "api_token", "authorization", "secret", "secret_key",
    "sec_key", "password", "ca_passwd", "ca_path", "person_id",
}


def _failure(endpoint: EndpointSpec, error: str, *, status: str = "failed") -> EndpointExecutionResult:
    return EndpointExecutionResult(
        status=status,
        endpoint_id=endpoint.endpoint_id,
        data_schema=endpoint.response_schema,
        cost=endpoint.cost,
        error=error,
    )


def _gateway_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    detail = next((payload.get(key) for key in ("detail", "message", "error") if isinstance(payload.get(key), str)), "")
    detail = re.sub(r"[A-Za-z0-9+/=_-]{24,}", "…", " ".join(str(detail).split()))
    return detail[:160]


def _is_empty_market_data(route: ShioajiRoute, raw: Any) -> bool:
    if route.rate_group != "market_data":
        return False
    if isinstance(raw, list):
        return not raw
    if isinstance(raw, Mapping):
        values = [value for value in raw.values() if isinstance(value, list)]
        return bool(values) and all(not value for value in values)
    return raw is None


def execute_shioaji_endpoint(
    endpoint: EndpointSpec,
    parameters: Mapping[str, Any],
    *,
    gateway_url: str = "",
    http_client: Any = None,
    rate_limiter: ShioajiRateLimiter | None = None,
    timeout_sec: float = 30.0,
    max_response_bytes: int = 32 * 1024 * 1024,
) -> EndpointExecutionResult:
    """Execute one allowlisted read-only route against the user's Shioaji gateway."""
    route = SHIOAJI_ROUTES_BY_ID.get(endpoint.endpoint_id)
    if route is None or not shioaji_endpoint_is_executable(endpoint):
        return _failure(endpoint, "This operation is not in the Shioaji read-only route allowlist.", status="not_executable")
    values = dict(parameters or {})
    if any(str(name).lower() in _SECRET_NAMES for name in values):
        return _failure(endpoint, "Shioaji credentials stay in the shioaji server .env and are not accepted as parameters.")
    specs = {parameter.name: parameter for parameter in endpoint.parameters}
    unknown = sorted(set(values) - set(specs))
    if unknown:
        return _failure(endpoint, f"Unsupported endpoint parameter(s): {', '.join(unknown)}")
    missing = [
        parameter.name for parameter in endpoint.parameters
        if parameter.required and parameter.default is None and values.get(parameter.name) in (None, "")
    ]
    if missing:
        return _failure(endpoint, f"Missing required endpoint parameter(s): {', '.join(missing)}")
    for name, value in values.items():
        if value is None:
            continue
        schema = specs[name].schema
        enum = schema.get("enum")
        if enum and value not in enum:
            return _failure(endpoint, f"Unsupported value for {name}; expected one of: {', '.join(map(str, enum))}")
        if schema.get("type") == "array" and not isinstance(value, list):
            return _failure(endpoint, f"Parameter {name} must be a list.")
        if schema.get("type") == "object" and not isinstance(value, Mapping):
            return _failure(endpoint, f"Parameter {name} must be an object.")
        limit = route.max_items.get(name)
        if limit and isinstance(value, list) and len(value) > limit:
            return _failure(endpoint, f"Parameter {name} accepts at most {limit} items per request.")
    try:
        base_url = normalize_shioaji_gateway_url(gateway_url)
    except ValueError as exc:
        return _failure(endpoint, str(exc), status="not_executable")

    path = route.path
    query: Dict[str, Any] = {}
    body: Dict[str, Any] = {}
    for name, value in values.items():
        if value is None:
            continue
        location = specs[name].location
        if location == "path":
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        elif location == "body":
            body[name] = value
        else:
            query[name] = str(value).lower() if isinstance(value, bool) else value
    if "{" in path:
        return _failure(endpoint, "One or more endpoint path parameters are unresolved.")

    limiter = rate_limiter or shioaji_rate_limiter(base_url)
    if route.rate_group:
        wait = limiter.acquire(route.rate_group)
        if wait > 0:
            count, window = SHIOAJI_RATE_LIMITS[route.rate_group]
            return _failure(
                endpoint,
                f"Shioaji allows {count} market-data requests per {window:g} seconds; retry in {wait:.1f}s.",
                status="rate_limited",
            )

    options: Dict[str, Any] = {"headers": {"Accept": "application/json"}}
    if query:
        options["params"] = query
    if route.method == "POST":
        options["json"] = body
    client = http_client
    owns_client = client is None
    try:
        if client is None:
            import httpx

            loopback = _is_loopback((urlparse(base_url).hostname or "").lower())
            client = httpx.Client(timeout=timeout_sec, follow_redirects=False, trust_env=not loopback)
        response = client.request(route.method, base_url + path, **options)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            return _failure(
                endpoint,
                "The Shioaji gateway rejected the request; check that shioaji server is logged in and the account is entitled.",
                status="authentication_required",
            )
        if status_code == 429:
            return _failure(endpoint, "The Shioaji gateway reported a rate limit.", status="rate_limited")
        if status_code >= 500:
            return _failure(endpoint, f"The Shioaji gateway provider request failed with HTTP status {status_code}.")
        if status_code < 200 or status_code >= 300:
            detail = _gateway_detail(response)
            return _failure(
                endpoint,
                f"The Shioaji gateway rejected the request (HTTP {status_code})" + (f": {detail}" if detail else "."),
            )
        content = getattr(response, "content", b"") or b""
        if len(content) > max_response_bytes:
            return _failure(endpoint, f"The Shioaji response exceeded {max_response_bytes} bytes; narrow the request.")
        try:
            raw = response.json()
        except Exception:
            return _failure(endpoint, "The Shioaji gateway returned a non-JSON response.")
        data = {"items": raw} if isinstance(raw, list) else dict(raw) if isinstance(raw, Mapping) else {"value": raw}
        warnings: Tuple[str, ...] = ()
        if _is_empty_market_data(route, raw):
            warnings = (
                "The gateway returned no rows; check the date and contract, or the remaining daily traffic with shioaji.auth.usage.",
            )
        return EndpointExecutionResult(
            status="completed",
            endpoint_id=endpoint.endpoint_id,
            data=data,
            data_schema=endpoint.response_schema,
            cost=endpoint.cost,
            warnings=warnings,
        )
    except Exception as exc:
        kind = type(exc).__name__
        if kind == "ConnectError":
            return _failure(endpoint, "The Shioaji gateway is not reachable (connection refused); start it with `shioaji server start`.")
        if "Timeout" in kind:
            return _failure(endpoint, "The Shioaji gateway request timed out.")
        return _failure(endpoint, "The Shioaji gateway request could not be completed.")
    finally:
        if owns_client and client is not None:
            client.close()


__all__ = [
    "SHIOAJI_DEFAULT_GATEWAY_URL",
    "SHIOAJI_DOCUMENTATION_PAGES",
    "SHIOAJI_GATEWAY_ENV",
    "SHIOAJI_RATE_LIMITS",
    "SHIOAJI_ROUTES",
    "SHIOAJI_ROUTES_BY_ID",
    "SHIOAJI_SOURCE_DEFINITION",
    "SHIOAJI_SOURCE_ID",
    "SHIOAJI_SOURCE_IDS",
    "ShioajiRateLimiter",
    "ShioajiRoute",
    "ShioajiSpecParser",
    "execute_shioaji_endpoint",
    "normalize_shioaji_gateway_url",
    "shioaji_endpoint_id",
    "shioaji_endpoint_is_executable",
    "shioaji_rate_limiter",
    "shioaji_spec_sources",
]
