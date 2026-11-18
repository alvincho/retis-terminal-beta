"""Approved public specification inputs for the ranked financial providers."""

from __future__ import annotations

from typing import Any, Mapping

from .lseg import LSEG_DATA_ENDPOINTS
from .models import VendorSpecSource
from .workspace_spec import DOCS as WORKSPACE_DOCS
from .shioaji import SHIOAJI_SOURCE_DEFINITION, SHIOAJI_SOURCE_ID, SHIOAJI_SOURCE_IDS, shioaji_spec_sources
from .taiwan import TAIWAN_SOURCE_DEFINITIONS, TAIWAN_SOURCE_IDS, taiwan_spec_sources


def _tiingo_parameters(operation: str) -> list[dict[str, Any]]:
    """Return the provider-documented controls shared by Tiingo route families."""
    names: list[str] = []
    if operation.endswith("/prices") or operation.endswith("/eod"):
        names.extend(("startDate", "endDate", "resampleFreq", "columns"))
    if operation == "/tiingo/news":
        names.extend(("tickers", "tags", "sources", "startDate", "endDate", "limit", "offset", "sortBy"))
    elif operation.startswith("/tiingo/news/bulk_download"):
        names.extend(("startDate", "endDate"))
    if operation in {"/tiingo/crypto/prices", "/tiingo/crypto/top", "/tiingo/fx/top"}:
        names.extend(("tickers", "startDate", "endDate", "resampleFreq"))
    if "/fundamentals/" in operation:
        names.extend(("startDate", "endDate", "year", "quarter"))
    if "/corporate-actions/" in operation:
        names.extend(("startDate", "endDate", "exDate", "distributionType"))
    if "/crypto-yield/" in operation:
        names.extend(("platform", "pool_code", "startDate", "endDate", "limit"))
    if "/utilities/search" in operation:
        names.extend(("query", "columns"))
    return [
        {
            "name": name,
            "schema": {"type": "integer"} if name in {"limit", "offset", "year", "quarter"} else {"type": "string"},
        }
        for name in dict.fromkeys(names)
        if f"{{{name}}}" not in operation
    ]


TOP_TEN_SOURCE_IDS = (
    "alpha_vantage",
    "finnhub",
    "twelve_data",
    "massive",
    "financial_modeling_prep",
    "eodhd",
    "tiingo",
    "nasdaq_data_link",
    "sec_edgar",
    "fred",
)

RANKED_SOURCE_IDS_11_TO_20 = (
    "treasury_fiscal_data",
    "world_bank",
    "imf",
    "ecb",
    "bis",
    "oecd",
    "coingecko",
    "coinmarketcap",
    "openfigi",
    "intrinio",
)

TOP_TWENTY_SOURCE_IDS = (*TOP_TEN_SOURCE_IDS, *RANKED_SOURCE_IDS_11_TO_20)

ALTERNATIVE_DATA_SOURCE_IDS = (
    "morningstar",
    "crunchbase",
    "moodys_analytics",
    "dealroom",
    "quartr",
    "visible",
)

ADDITIONAL_SOURCE_IDS = ("aisa_one", "lseg", "lseg_datascope_select", "lseg_worldcheck_one", "sustainable1", "bloomberg", *ALTERNATIVE_DATA_SOURCE_IDS)

INVESTMENT_ALTERNATIVE_DATA_SOURCE_IDS = (
    "ceic",
    "factset",
    "similarweb",
    "tomorrow_io",
    "weatherbit",
    "meteomatics",
    "planet",
    "sentinel_hub",
    "up42",
    "noaa_weather",
    "nasa_cmr",
    "copernicus_data_space",
    "google_earth_engine",
    "openweather",
)

SUPPORTED_SOURCE_IDS = (
    *TOP_TWENTY_SOURCE_IDS,
    *ADDITIONAL_SOURCE_IDS,
    *INVESTMENT_ALTERNATIVE_DATA_SOURCE_IDS,
    *TAIWAN_SOURCE_IDS,
    *SHIOAJI_SOURCE_IDS,
)


PROVIDER_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "morningstar": {
        "source_name": "Morningstar Direct Web Services Data Source",
        "provider": "Morningstar",
        "description": "Executable Morningstar Direct Web Services routes for investment universes, investment data packages, time series, screening, available views, and scenario analysis.",
        "coverage": ["funds", "equity", "fixed income", "private capital", "portfolio holdings", "esg", "analyst research", "time series", "screening", "scenario analysis"],
        "authentication": "User-owned Morningstar Direct Web Services bearer token and product entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "morningstar-official-direct-web-services-openapi-index",
                "location": "https://developer.morningstar.com/site-navigation/documentation-navigation-public.json",
                "media_type": "application/json",
                "description": "Official public Morningstar developer navigation and linked Direct Web Services OpenAPI specifications.",
                "options": {
                    "allowed_hosts": ["developer.morningstar.com"],
                    "api_hosts": ["www.us-api.morningstar.com"],
                    "authentication_required": True,
                    "follow_json_swagger_links": True,
                    "follow_link_path_prefix": "/content/documentation/direct-web-services/",
                    "follow_link_limit": 64,
                    "max_bytes": 64 * 1024 * 1024,
                    "timeout_sec": 240,
                },
            },
        ),
    },
    "crunchbase": {
        "source_name": "Crunchbase Data Source",
        "provider": "Crunchbase",
        "description": "Executable Crunchbase API routes for private-company fundamentals, people, investors, funds, financing, acquisitions, ownership, events, insights, and predictions.",
        "coverage": ["private companies", "private equity", "venture capital", "funding rounds", "acquisitions", "investors", "funds", "people", "ownership", "growth insights", "predictions"],
        "authentication": "User-owned Crunchbase API key and package entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "crunchbase-official-api-index",
                "location": "https://data.crunchbase.com/llms.txt",
                "description": "Official public Crunchbase documentation index covering the current API packages and reference operations.",
                "options": {
                    "allowed_hosts": ["data.crunchbase.com"],
                    "api_hosts": ["api.crunchbase.com"],
                    "authentication_required": True,
                    "declared_endpoints": [
                        {"identity": "autocomplete", "operation": "/autocompletes", "method": "GET", "name": "Autocomplete entities", "category": "Discovery", "parameters": [{"name": "query", "required": True}, {"name": "collection_ids", "schema": {"type": "array", "items": {"type": "string"}}}, {"name": "limit", "schema": {"type": "integer"}}]},
                        {"identity": "lookup-entity", "operation": "/entities/{collection}/{entity_id}", "method": "GET", "name": "Lookup entity", "category": "Entity lookup", "parameters": [{"name": "collection", "location": "path", "required": True}, {"name": "entity_id", "location": "path", "required": True}, {"name": "field_ids", "schema": {"type": "array", "items": {"type": "string"}}}, {"name": "card_ids", "schema": {"type": "array", "items": {"type": "string"}}}]},
                        {"identity": "lookup-entity-card", "operation": "/entities/{collection}/{entity_id}/cards/{card_id}", "method": "GET", "name": "Lookup entity relationship card", "category": "Entity lookup", "parameters": [{"name": "collection", "location": "path", "required": True}, {"name": "entity_id", "location": "path", "required": True}, {"name": "card_id", "location": "path", "required": True}, {"name": "after_id"}, {"name": "before_id"}, {"name": "limit", "schema": {"type": "integer"}}]},
                        {"identity": "search-entities", "operation": "/searches/{collection}", "method": "POST", "name": "Search entity collection", "category": "Search", "parameters": [{"name": "collection", "location": "path", "required": True}, {"name": "body", "location": "body", "required": True, "schema": {"type": "object"}}]},
                        {"identity": "deleted-entities", "operation": "/deleted_entities", "method": "GET", "name": "Retrieve deleted entities", "category": "Changes"},
                        {"identity": "deleted-entities-by-collection", "operation": "/deleted_entities/{collection}", "method": "GET", "name": "Retrieve deleted entities by collection", "category": "Changes", "parameters": [{"name": "collection", "location": "path", "required": True}]},
                    ],
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "moodys_analytics": {
        "source_name": "Moody's Analytics Data Source",
        "provider": "Moody's Analytics",
        "description": "Executable Moody's Economy.com API for economic series search, vintages, multi-series retrieval, and licensed basket/order delivery.",
        "coverage": ["macroeconomics", "economic forecasts", "historical series", "vintages", "countries", "regions", "credit and risk drivers"],
        "authentication": "Source-owned Moody's Analytics OAuth access token and Economy.com API entitlement required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "moodys-analytics-official-economy-swagger",
                "location": "https://api.economy.com/data/v1/swagger/docs/v1",
                "media_type": "application/json",
                "description": "Official public Swagger specification for the Moody's Analytics Economy.com Data API.",
                "options": {"allowed_hosts": ["api.economy.com"], "authentication_required": True, "max_bytes": 2 * 1024 * 1024, "timeout_sec": 90},
            },
        ),
    },
    "dealroom": {
        "source_name": "Dealroom Premium Data Source",
        "provider": "Dealroom.co",
        "description": "Executable Dealroom Premium API for companies, founders, investors, transactions, universities, limited partners, analytics, news, patents, and ecosystem discovery.",
        "coverage": ["private companies", "venture capital", "private equity", "funding transactions", "investors", "limited partners", "founders", "universities", "company analytics", "patents", "news"],
        "authentication": "User-owned Dealroom Premium API key and contractual entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "dealroom-official-premium-openapi",
                "location": "https://docs.dealroom.co/docs/source/v1",
                "media_type": "application/json",
                "description": "Official public Dealroom Premium API OpenAPI specification.",
                "options": {"allowed_hosts": ["docs.dealroom.co"], "authentication_required": True, "max_bytes": 2 * 1024 * 1024, "timeout_sec": 120},
            },
        ),
    },
    "quartr": {
        "source_name": "Quartr Data Source",
        "provider": "Quartr",
        "description": "Executable Quartr API for companies, events, filings, reports, slides, transcripts, summaries, audio, and live events.",
        "coverage": ["public companies", "earnings events", "transcripts", "filings", "annual and interim reports", "presentation slides", "summaries", "live audio"],
        "authentication": "User-owned Quartr API key and plan entitlement required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "quartr-official-openapi",
                "location": "https://quartr.com/docs/openapi.json",
                "media_type": "application/json",
                "description": "Official public Quartr OpenAPI specification.",
                "options": {"allowed_hosts": ["quartr.com"], "authentication_required": True, "max_bytes": 2 * 1024 * 1024, "timeout_sec": 90},
            },
        ),
    },
    "visible": {
        "source_name": "Visible Data Source",
        "provider": "Visible.vc",
        "description": "Executable read-only Visible API for portfolio metrics, data points, company profiles, notes, properties, options, files, folders, and tags.",
        "coverage": ["portfolio companies", "private-company metrics", "data points", "investor reporting", "portfolio notes", "portfolio properties", "portfolio files"],
        "authentication": "User-owned Visible bearer access token required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "visible-official-openapi",
                "location": "https://docs.visible.vc/source.yaml",
                "media_type": "application/yaml",
                "description": "Official public Visible OpenAPI specification. Data Source ingestion intentionally registers its read operations only.",
                "options": {"allowed_hosts": ["docs.visible.vc"], "authentication_required": True, "max_bytes": 2 * 1024 * 1024, "timeout_sec": 90},
            },
        ),
    },
    "lseg": {
        "source_name": "LSEG Data Platform",
        "provider": "London Stock Exchange Group",
        "description": "Executable licensed LSEG Data Platform REST routes for pricing, news, ESG, ownership, estimates, filings, analytics, discovery, and fundamental/reference data.",
        "coverage": ["equity", "fixed income", "fx", "commodities", "fundamentals", "estimates", "reference data", "news", "esg", "ownership", "filings", "analytics", "funds", "bulk files"],
        "authentication": "Source-owned LSEG OAuth bearer session and applicable product entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "lseg-official-data-platform-documentation",
                "location": "https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/refinitiv-data-platform-apis/",
                "description": "Official LSEG Data Platform API documentation and public developer guidance.",
                "options": {
                    "allowed_hosts": ["developers.lseg.com"],
                    "api_hosts": ["api.refinitiv.com"],
                    "authentication_required": True,
                    "declared_endpoints": LSEG_DATA_ENDPOINTS,
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "lseg_datascope_select": {
        "source_name": "LSEG DataScope Select", "provider": "London Stock Exchange Group",
        "description": "Licensed non-streaming pricing, reference data and corporate-action extractions, field dictionaries, instrument search and resumable extraction jobs.",
        "coverage": ["equity", "fixed income", "fx", "derivatives", "reference data", "corporate actions", "historical pricing"],
        "authentication": "Source-owned DSS username/password or DSS token; licensed content entitlements required.",
        "authentication_required": True,
        "sources": ({"source_id":"lseg-dss-search-metadata", "location":"https://selectapi.datascope.lseg.com/RestApi/v1/Search/$metadata", "media_type":"application/xml", "description":"Official DSS OData search schema and public extraction tutorials.", "options":{"allowed_hosts":["selectapi.datascope.lseg.com"], "max_bytes":2097152}},),
    },
    "lseg_worldcheck_one": {
        "source_name": "LSEG World-Check One", "provider": "London Stock Exchange Group",
        "description": "World-Check One v3 group and case-schema discovery, explicit case screening and matched-record retrieval. Licensed schema imports add read-only case results and monitoring queries.",
        "coverage": ["entity screening", "sanctions", "PEP", "adverse media", "third-party risk"],
        "authentication": "Source-owned World-Check One OAuth service account or HMAC connection; API and group permissions required.",
        "authentication_required": True,
        "sources": ({"source_id":"lseg-wc1-v3-quickstart", "location":"https://developers.lseg.com/en/api-catalog/customer-and-third-party-screening/world-check-one-api/quick-start", "description":"Official public World-Check One v3 screening quick start.", "options":{"allowed_hosts":["developers.lseg.com"], "max_bytes":2097152}},),
    },
    "bloomberg": {
        "source_name": "Bloomberg Data Source",
        "provider": "Bloomberg L.P.",
        "description": "Executable Bloomberg reference, historical, and intraday requests through an authorized BLPAPI session.",
        "coverage": ["equity", "fixed income", "fx", "commodities", "indices", "reference data", "historical data"],
        "authentication": "Licensed Bloomberg Desktop API, SAPI, or B-Pipe session and data entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "bloomberg-official-blpapi-documentation",
                "location": "https://bloomberg.github.io/blpapi-docs/",
                "description": "Official public BLPAPI SDK documentation; authorized service schemas are discovered at runtime.",
                "options": {
                    "allowed_hosts": ["bloomberg.github.io"],
                    "authentication_required": True,
                    "declared_endpoints": [
                        {
                            "operation": "//blp/refdata:ReferenceDataRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "ReferenceDataRequest",
                            "name": "Reference Data",
                            "category": "Reference Data",
                            "parameters": [
                                {"name": "securities", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "fields", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "overrides", "schema": {"type": "array", "items": {"type": "object"}}},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:HistoricalDataRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "HistoricalDataRequest",
                            "name": "Historical Data",
                            "category": "Historical Data",
                            "parameters": [
                                {"name": "securities", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "fields", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "startDate", "required": True},
                                {"name": "endDate", "required": True},
                                {"name": "periodicitySelection", "default": "DAILY"},
                                {"name": "overrides", "schema": {"type": "array", "items": {"type": "object"}}},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:IntradayBarRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "IntradayBarRequest",
                            "name": "Intraday Bars",
                            "category": "Intraday Data",
                            "parameters": [
                                {"name": "security", "required": True},
                                {"name": "eventType", "required": True},
                                {"name": "interval", "required": True, "schema": {"type": "integer"}},
                                {"name": "startDateTime", "required": True},
                                {"name": "endDateTime", "required": True},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:IntradayTickRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "IntradayTickRequest",
                            "name": "Intraday Ticks",
                            "category": "Intraday Data",
                            "parameters": [
                                {"name": "security", "required": True},
                                {"name": "eventTypes", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "startDateTime", "required": True},
                                {"name": "endDateTime", "required": True},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:PortfolioDataRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "PortfolioDataRequest",
                            "name": "Portfolio Data",
                            "category": "Portfolio Data",
                            "parameters": [
                                {"name": "securities", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "fields", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "overrides", "schema": {"type": "array", "items": {"type": "object"}}},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:BeqsRequest",
                            "service_name": "//blp/refdata",
                            "request_type": "BeqsRequest",
                            "name": "Bloomberg Equity Screening",
                            "category": "Equity Screening",
                            "parameters": [
                                {"name": "screenName", "required": True},
                                {"name": "screenType", "default": "PRIVATE"},
                                {"name": "Group"},
                                {"name": "languageId"},
                                {"name": "overrides", "schema": {"type": "array", "items": {"type": "object"}}},
                            ],
                        },
                        {
                            "operation": "//blp/refdata:ServiceSchema",
                            "service_name": "//blp/refdata",
                            "request_type": "ServiceSchema",
                            "name": "Authorized Service Schema",
                            "category": "Runtime Schema",
                            "description": "Inspect operations and request/response schemas exposed by the currently authorized Bloomberg service.",
                            "parameters": [],
                        },
                        {
                            "operation": "//blp/apiflds:FieldInfoRequest",
                            "service_name": "//blp/apiflds",
                            "request_type": "FieldInfoRequest",
                            "name": "Field Information",
                            "category": "Field Metadata",
                            "parameters": [
                                {"name": "id", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                {"name": "returnFieldDocumentation", "schema": {"type": "boolean"}, "default": False},
                            ],
                        },
                        {
                            "operation": "//blp/apiflds:FieldSearchRequest",
                            "service_name": "//blp/apiflds",
                            "request_type": "FieldSearchRequest",
                            "name": "Field Search",
                            "category": "Field Metadata",
                            "parameters": [
                                {"name": "searchSpec", "required": True},
                                {"name": "exclude", "schema": {"type": "object"}},
                            ],
                        },
                        {
                            "operation": "//blp/apiflds:CategorizedFieldSearchRequest",
                            "service_name": "//blp/apiflds",
                            "request_type": "CategorizedFieldSearchRequest",
                            "name": "Categorized Field Search",
                            "category": "Field Metadata",
                            "parameters": [
                                {"name": "searchSpec", "required": True},
                                {"name": "exclude", "schema": {"type": "object"}},
                            ],
                        },
                        {
                            "operation": "//blp/apiflds:ServiceSchema",
                            "service_name": "//blp/apiflds",
                            "request_type": "ServiceSchema",
                            "name": "API Field Service Schema",
                            "category": "Runtime Schema",
                            "parameters": [],
                        },
                        {
                            "operation": "//blp/instruments:instrumentListRequest",
                            "service_name": "//blp/instruments",
                            "request_type": "instrumentListRequest",
                            "name": "Instrument List",
                            "category": "Instrument Discovery",
                            "parameters": [
                                {"name": "query", "required": True},
                                {"name": "yellowKeyFilter", "default": "YK_FILTER_NONE"},
                                {"name": "languageOverride", "default": ""},
                                {"name": "maxResults", "schema": {"type": "integer"}, "default": 10},
                            ],
                        },
                        {
                            "operation": "//blp/instruments:curveListRequest",
                            "service_name": "//blp/instruments",
                            "request_type": "curveListRequest",
                            "name": "Curve List",
                            "category": "Instrument Discovery",
                            "parameters": [{"name": "query", "required": True}, {"name": "maxResults", "schema": {"type": "integer"}, "default": 10}],
                        },
                        {
                            "operation": "//blp/instruments:govListRequest",
                            "service_name": "//blp/instruments",
                            "request_type": "govListRequest",
                            "name": "Government List",
                            "category": "Instrument Discovery",
                            "parameters": [{"name": "query", "required": True}, {"name": "maxResults", "schema": {"type": "integer"}, "default": 10}],
                        },
                        {
                            "operation": "//blp/instruments:ServiceSchema",
                            "service_name": "//blp/instruments",
                            "request_type": "ServiceSchema",
                            "name": "Instrument Service Schema",
                            "category": "Runtime Schema",
                            "parameters": [],
                        },
                        *(
                            {
                                "operation": f"{service_name}:Subscription",
                                "service_name": service_name,
                                "request_type": "Subscription",
                                "name": display_name,
                                "category": category,
                                "parameters": [
                                    {"name": "securities", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                    {"name": "fields", "required": True, "schema": {"type": "array", "items": {"type": "string"}}},
                                    {"name": "options", "schema": {"type": "array", "items": {"type": "string"}}},
                                    {"name": "max_events", "schema": {"type": "integer"}, "default": 1},
                                ],
                            }
                            for service_name, display_name, category in (
                                ("//blp/mktdata", "Live Market Data Subscription", "Realtime Market Data"),
                                ("//blp/mktbar", "Live Market Bar Subscription", "Realtime Market Bars"),
                            )
                        ),
                        *(
                            {
                                "operation": f"{service_name}:ServiceSchema",
                                "service_name": service_name,
                                "request_type": "ServiceSchema",
                                "name": display_name,
                                "category": "Runtime Schema",
                                "parameters": [],
                            }
                            for service_name, display_name in (
                                ("//blp/mktdata", "Market Data Service Schema"),
                                ("//blp/mktbar", "Market Bar Service Schema"),
                            )
                        ),
                    ],
                },
            },
        ),
    },
    "aisa_one": {
        "source_name": "AIsa.one Data Source",
        "provider": "AIsa",
        "description": "AIsa's public financial API catalog for prices, fundamentals, statements, estimates, ownership, filings, and macro data.",
        "coverage": [
            "equity",
            "market data",
            "fundamentals",
            "financial statements",
            "estimates",
            "ownership",
            "filings",
            "macro",
        ],
        "authentication": "User-owned AIsa API connection, credits, and connected-provider entitlements required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "aisa-one-official-openapi",
                "location": "https://aisa.one/openapi.yaml",
                "media_type": "application/yaml",
                "description": "Official AIsa OpenAPI 3.1 specification; only Financial Data operations are retained.",
                "options": {
                    "allowed_hosts": ["aisa.one", "www.aisa.one"],
                    "use_curl": True,
                    "max_bytes": 16 * 1024 * 1024,
                    "timeout_sec": 180,
                },
            },
            *(
                {
                    "source_id": f"aisa-one-official-financial-{slug}",
                    "location": f"https://aisa.one/api/{slug}",
                    "description": f"Official AIsa {label} financial API reference.",
                    "options": {
                        "allowed_hosts": ["aisa.one", "www.aisa.one"],
                        "api_hosts": ["api.aisa.one"],
                        "use_curl": True,
                        "documented_endpoint_prefixes": ["/apis/v1/financial/"],
                        "documented_endpoint_category": label,
                        "max_bytes": 2 * 1024 * 1024,
                        "timeout_sec": 60,
                    },
                }
                for slug, label in (
                    ("analyst-estimates", "Analyst Estimates"),
                    ("company-information", "Company Information"),
                    ("earnings", "Earnings"),
                    ("financial-metrics", "Financial Metrics"),
                    ("financial-statements", "Financial Statements"),
                    ("insider-trades", "Insider Trades"),
                    ("institutional-ownership", "Institutional Ownership"),
                    ("interest-rates", "Interest Rates"),
                    ("market-data", "Market Data"),
                    ("sec-filings", "SEC Filings"),
                )
            ),
        ),
    },
    "twelve_data": {
        "source_name": "Twelve Data Data Source",
        "provider": "Twelve Data",
        "description": "Public Twelve Data endpoint catalog for stocks, ETFs, FX, crypto, and indicators.",
        "coverage": ["equity", "etf", "fx", "crypto", "technical indicators"],
        "authentication": "User-owned Twelve Data API connection and endpoint entitlement required.",
        "sources": tuple(
            {
                "source_id": f"twelve-data-official-{section}",
                "location": f"https://twelvedata.com/docs/llms/{section}.md",
                "media_type": "text/markdown",
                "description": f"Official Twelve Data {section.replace('-', ' ')} API documentation.",
                "options": {
                    "allowed_hosts": ["twelvedata.com", "www.twelvedata.com"],
                    "api_hosts": ["api.twelvedata.com"],
                    "follow_markdown_links": True,
                    "follow_link_limit": 250,
                    "max_bytes": 16 * 1024 * 1024,
                    "timeout_sec": 180,
                },
            }
            for section in (
                "market-data", "reference-data", "fundamentals", "currencies", "etfs",
                "mutual-funds", "money-market-funds", "technical-indicators", "analysis",
                "regulatory", "advanced",
            )
        ),
    },
    "massive": {
        "source_name": "Massive Data Source",
        "provider": "Massive (formerly Polygon.io)",
        "description": "Public Massive REST catalog for stocks, options, futures, indices, FX, crypto, and economic data.",
        "coverage": ["equity", "option", "futures", "index", "fx", "crypto", "macro"],
        "authentication": "User-owned Massive API connection and applicable market-data plan required.",
        "sources": (
            {
                "source_id": "massive-official-rest-documentation",
                "location": "https://massive.com/docs/rest",
                "description": "Official Massive REST endpoint documentation and navigation catalog.",
                "options": {
                    "allowed_hosts": ["massive.com", "www.massive.com"],
                    "documentation_path_prefixes": ["/rest/"],
                    "declared_endpoints": [
                        {"identity": "massive-dividends", "name": "Dividends", "operation": "/v3/reference/dividends", "category": "Reference Data"},
                    ],
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
            {
                "source_id": "massive-official-python-client",
                "kind": "repository",
                "location": "https://github.com/massive-com/client-python.git",
                "media_type": "text/x-python",
                "description": "Official Massive Python client REST route inventory.",
                "options": {
                    "github_text_globs": ["massive/rest/**/*.py"],
                    "max_bytes": 16 * 1024 * 1024,
                    "timeout_sec": 180,
                },
            },
        ),
    },
    "financial_modeling_prep": {
        "source_name": "Financial Modeling Prep Data Source",
        "provider": "Financial Modeling Prep",
        "description": "Provider-owned public API catalog for prices, statements, ratios, estimates, and filings.",
        "coverage": ["equity", "fundamentals", "estimates", "filings", "fx", "crypto", "commodity"],
        "authentication": "User-owned Financial Modeling Prep API connection and plan required.",
        "sources": (
            {
                "source_id": "fmp-provider-api3-documentation",
                "location": "https://raw.githubusercontent.com/FinancialModelingPrep/API3-integration/master/README.md",
                "media_type": "text/markdown",
                "description": "Provider-owned public API integration endpoint documentation.",
                "options": {
                    "allowed_hosts": ["raw.githubusercontent.com"],
                    "api_hosts": ["financialmodelingprep.com", "www.financialmodelingprep.com"],
                    "documented_endpoint_prefixes": ["/v3/", "/v4/"],
                    "documented_endpoint_category": "Provider-owned API3 route inventory",
                    "declared_endpoints": [
                        {"identity": "fmp-stable-quote", "name": "Quote", "operation": "/stable/quote", "category": "Market Data"},
                        {"identity": "fmp-stable-profile", "name": "Company Profile", "operation": "/stable/profile", "category": "Company Information"},
                        {"identity": "fmp-stable-income-statement", "name": "Income Statement", "operation": "/stable/income-statement", "category": "Financial Statements"},
                        {"identity": "fmp-stable-historical-price", "name": "Historical Price EOD", "operation": "/stable/historical-price-eod/full", "category": "Market Data"},
                    ],
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "eodhd": {
        "source_name": "EODHD Data Source",
        "provider": "EODHD",
        "description": "Public EODHD endpoint catalog for prices, fundamentals, news, and macro data.",
        "coverage": ["equity", "etf", "fund", "fx", "crypto", "fundamentals", "news", "macro"],
        "authentication": "User-owned EODHD API token and endpoint entitlement required.",
        "sources": (
            {
                "source_id": "eodhd-official-openapi",
                "kind": "repository",
                "location": "https://github.com/EodHistoricalData/eodhd-openapi.git",
                "media_type": "application/yaml",
                "description": "Official comprehensive EODHD OpenAPI 3.1 specification repository.",
                "options": {
                    "github_openapi_file": "openapi.yaml",
                    "authentication_required": True,
                    "max_bytes": 32 * 1024 * 1024,
                    "timeout_sec": 180,
                },
            },
        ),
    },
    "tiingo": {
        "source_name": "Tiingo Data Source",
        "provider": "Tiingo",
        "description": "Public Tiingo documentation catalog for EOD, realtime equity, news, fundamentals, FX, and crypto.",
        "coverage": ["equity", "etf", "fund", "fundamentals", "news", "fx", "crypto"],
        "authentication": "User-owned Tiingo API token and applicable data license required.",
        "sources": (
            {
                "source_id": "tiingo-official-documentation",
                "location": "https://www.tiingo.com/documentation/general/overview",
                "description": "Official Tiingo API documentation and its publicly documented REST route inventory.",
                "options": {
                    "allowed_hosts": ["www.tiingo.com", "tiingo.com"],
                    "api_hosts": ["api.tiingo.com"],
                    "declared_endpoints": [
                        {"identity": identity, "name": name, "operation": operation, "category": category, "parameters": _tiingo_parameters(operation)}
                        for identity, name, operation, category in (
                            ("daily-meta", "Daily Ticker Metadata", "/tiingo/daily/{ticker}", "End-of-Day"),
                            ("daily-prices", "Daily Prices", "/tiingo/daily/{ticker}/prices", "End-of-Day"),
                            ("daily-supported", "Supported Daily Tickers", "/tiingo/daily/supported_tickers.zip", "End-of-Day"),
                            ("news", "Financial News", "/tiingo/news", "News"),
                            ("news-bulk", "News Bulk Downloads", "/tiingo/news/bulk_download", "News"),
                            ("news-bulk-file", "News Bulk Download File", "/tiingo/news/bulk_download/{file_id}", "News"),
                            ("crypto-meta", "Crypto Metadata", "/tiingo/crypto", "Crypto"),
                            ("crypto-prices", "Crypto Prices", "/tiingo/crypto/prices", "Crypto"),
                            ("crypto-top", "Crypto Top of Book", "/tiingo/crypto/top", "Crypto"),
                            ("fx-prices", "Forex Prices", "/tiingo/fx/{ticker}/prices", "Forex"),
                            ("fx-top-ticker", "Forex Top of Book", "/tiingo/fx/{ticker}/top", "Forex"),
                            ("fx-top", "Forex Multi-pair Top of Book", "/tiingo/fx/top", "Forex"),
                            ("equity-intraday", "Equity Intraday Metadata", "/tiingo/equity/intraday/{ticker}", "Equity Realtime"),
                            ("equity-intraday-prices", "Equity Intraday Prices", "/tiingo/equity/intraday/{ticker}/prices", "Equity Realtime"),
                            ("iex", "IEX Metadata", "/iex/{ticker}", "IEX"),
                            ("iex-prices", "IEX Prices", "/iex/{ticker}/prices", "IEX"),
                            ("boats", "BOATS Metadata", "/boats/{ticker}", "BOATS Overnight"),
                            ("boats-prices", "BOATS Prices", "/boats/{ticker}/prices", "BOATS Overnight"),
                            ("smallx-meta", "Small Exchange Metadata", "/smallx/meta", "Small Exchange"),
                            ("smallx-eod", "Small Exchange EOD", "/smallx/{ticker}/eod", "Small Exchange"),
                            ("smallx-prices", "Small Exchange Prices", "/smallx/{ticker}/prices", "Small Exchange"),
                            ("smallx-tops", "Small Exchange Tops", "/smallx/{ticker}/tops", "Small Exchange"),
                            ("fundamental-definitions", "Fundamental Definitions", "/tiingo/fundamentals/definitions", "Fundamentals"),
                            ("fundamental-meta", "Fundamental Metadata", "/tiingo/fundamentals/meta", "Fundamentals"),
                            ("fundamental-daily", "Daily Fundamentals", "/tiingo/fundamentals/{ticker}/daily", "Fundamentals"),
                            ("fundamental-statements", "Financial Statements", "/tiingo/fundamentals/{ticker}/statements", "Fundamentals"),
                            ("fund-meta", "Fund Metadata and Fees", "/tiingo/funds/{ticker}", "Fund and ETF Fees"),
                            ("fund-metrics", "Fund Metrics", "/tiingo/funds/{ticker}/metrics", "Fund and ETF Fees"),
                            ("distribution-yield", "Distribution Yield", "/tiingo/corporate-actions/{ticker}/distribution-yield", "Corporate Actions"),
                            ("ticker-distributions", "Ticker Distributions", "/tiingo/corporate-actions/{ticker}/distributions", "Corporate Actions"),
                            ("distributions", "Distribution Calendar", "/tiingo/corporate-actions/distributions", "Corporate Actions"),
                            ("ticker-splits", "Ticker Splits", "/tiingo/corporate-actions/{ticker}/splits", "Corporate Actions"),
                            ("splits", "Split Calendar", "/tiingo/corporate-actions/splits", "Corporate Actions"),
                            ("yield-platforms", "Crypto Yield Platforms", "/tiingo/crypto-yield/platforms", "Crypto Yield"),
                            ("yield-pools", "Crypto Yield Pools", "/tiingo/crypto-yield/pools", "Crypto Yield"),
                            ("yield-ticks", "Crypto Yield Ticks", "/tiingo/crypto-yield/ticks", "Crypto Yield"),
                            ("yield-metrics", "Crypto Yield Pool Metrics", "/tiingo/crypto-yield/{pool_code}/metrics", "Crypto Yield"),
                            ("search", "Instrument Search", "/tiingo/utilities/search", "Utilities"),
                            ("search-path", "Instrument Search by Path", "/tiingo/utilities/search/{query}", "Utilities"),
                        )
                    ],
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "nasdaq_data_link": {
        "source_name": "Nasdaq Data Link Data Source",
        "provider": "Nasdaq Data Link",
        "description": "Public Nasdaq Data Link catalog for tables, realtime/delayed, and streaming datasets.",
        "coverage": ["market data", "economic data", "alternative data", "premium datasets"],
        "authentication": "Dataset-dependent Nasdaq Data Link API key and subscription required.",
        "sources": (
            {
                "source_id": "nasdaq-data-link-official-documentation",
                "location": "https://docs.data.nasdaq.com/llms.txt",
                "description": "Official Nasdaq Data Link API documentation index.",
                "options": {
                    "allowed_hosts": ["docs.data.nasdaq.com"],
                    "api_hosts": ["data.nasdaq.com"],
                    "declared_endpoints": [
                        {"identity": "nasdaq-dataset", "name": "Dataset", "operation": "/api/v3/datasets/{database_code}/{dataset_code}.json", "category": "Datasets", "parameters": [{"name": "start_date"}, {"name": "end_date"}, {"name": "column_index", "schema": {"type": "integer"}}, {"name": "collapse"}, {"name": "transform"}, {"name": "order"}, {"name": "rows", "schema": {"type": "integer"}}, {"name": "limit", "schema": {"type": "integer"}}]},
                        {"identity": "nasdaq-dataset-metadata", "name": "Dataset metadata", "operation": "/api/v3/datasets/{database_code}/{dataset_code}/metadata.json", "category": "Datasets"},
                        {"identity": "nasdaq-datatable", "name": "Data Table", "operation": "/api/v3/datatables/{datatable_code}.json", "category": "Data Tables", "parameters": [{"name": "qopts.columns", "schema": {"type": "array", "items": {"type": "string"}}}, {"name": "qopts.cursor_id"}, {"name": "qopts.per_page", "schema": {"type": "integer"}}, {"name": "qopts.export", "schema": {"type": "boolean"}}]},
                        {"identity": "nasdaq-datatable-format", "name": "Data Table by Format", "operation": "/api/v3/datatables/{datatable_code}.{format}", "category": "Data Tables", "parameters": [{"name": "qopts.columns", "schema": {"type": "array", "items": {"type": "string"}}}, {"name": "qopts.cursor_id"}, {"name": "qopts.per_page", "schema": {"type": "integer"}}]},
                        {"identity": "nasdaq-datatable-metadata", "name": "Data Table Metadata", "operation": "/api/v3/datatables/{datatable_code}/metadata.{format}", "category": "Data Tables"},
                        {"identity": "nasdaq-bulk-download", "name": "Bulk data-table export", "operation": "/api/v1/bulkdownloads/{datatable_code}", "category": "Data Tables", "parameters": [{"name": "qopts.export", "default": "true"}]},
                    ],
                    "declared_only": True,
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "sec_edgar": {
        "source_name": "SEC EDGAR Data Source",
        "provider": "U.S. Securities and Exchange Commission",
        "description": "Public SEC EDGAR submissions and XBRL data API catalog.",
        "coverage": ["filings", "company facts", "xbrl", "fundamentals"],
        "authentication": "No API key; automated clients must identify themselves and follow SEC access policy.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "sec-edgar-official-data-api",
                "location": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
                "description": "Official SEC EDGAR submissions and XBRL API documentation.",
                "options": {
                    "allowed_hosts": ["www.sec.gov", "sec.gov"],
                    "api_hosts": ["data.sec.gov"],
                    "authentication_required": False,
                    "user_agent": "Attas Data Agent Network admin@retis.ai",
                    "declared_endpoints": [
                        {"identity": "submissions", "name": "Company submissions", "operation": "/submissions/CIK{cik}.json", "category": "Submissions", "parameters": [{"name": "cik", "location": "path", "required": True}]},
                        {"identity": "companyfacts", "name": "Company facts", "operation": "/api/xbrl/companyfacts/CIK{cik}.json", "category": "XBRL", "parameters": [{"name": "cik", "location": "path", "required": True}]},
                        {"identity": "companyconcept", "name": "Company concept", "operation": "/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json", "category": "XBRL", "parameters": [{"name": "cik", "location": "path", "required": True}, {"name": "taxonomy", "location": "path", "required": True}, {"name": "tag", "location": "path", "required": True}]},
                        {"identity": "frames", "name": "XBRL frames", "operation": "/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json", "category": "XBRL", "parameters": [{"name": "taxonomy", "location": "path", "required": True}, {"name": "tag", "location": "path", "required": True}, {"name": "unit", "location": "path", "required": True}, {"name": "period", "location": "path", "required": True}]},
                        {"identity": "submissions-bulk", "name": "Submissions bulk archive", "operation": "/submissions.zip", "category": "Bulk archives"},
                        {"identity": "companyfacts-bulk", "name": "Company facts bulk archive", "operation": "/companyfacts.zip", "category": "Bulk archives"},
                    ],
                    "declared_only": True,
                    "max_bytes": 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "fred": {
        "source_name": "FRED and ALFRED Data Source",
        "provider": "Federal Reserve Bank of St. Louis",
        "description": "Public FRED and ALFRED endpoint catalog for economic and financial time series.",
        "coverage": ["macro", "interest rates", "economic indicators", "financial series"],
        "authentication": "User-owned FRED API key required for API requests.",
        "sources": (
            {
                "source_id": "fred-official-api-index",
                "location": "https://fred.stlouisfed.org/docs/api/fred/fred/",
                "description": "Official FRED API endpoint index.",
                "options": {
                    "allowed_hosts": ["fred.stlouisfed.org"],
                    "api_hosts": ["api.stlouisfed.org"],
                    "fred_index": True,
                    "use_curl": True,
                    "use_default_curl_user_agent": True,
                    "max_bytes": 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "treasury_fiscal_data": {
        "source_name": "U.S. Treasury Fiscal Data Source",
        "provider": "U.S. Department of the Treasury, Bureau of the Fiscal Service",
        "description": "Public Fiscal Data API catalog for federal debt, exchange rates, revenue, spending, and government finance.",
        "coverage": ["sovereign debt", "treasury", "fx", "government finance", "macro"],
        "authentication": "No API key; public access is subject to Fiscal Data usage guidance.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "treasury-fiscal-data-official-api-documentation",
                "location": "https://fiscaldata.treasury.gov/api-documentation/",
                "description": "Official U.S. Treasury Fiscal Data API documentation.",
                "options": {
                    "allowed_hosts": ["fiscaldata.treasury.gov"],
                    "api_hosts": ["api.fiscaldata.treasury.gov"],
                    "authentication_required": False,
                    "declared_endpoints": [
                        {
                            "identity": "fiscal-service-table",
                            "name": "Fiscal Service dataset table",
                            "operation": "/services/api/fiscal_service/{version}/{domain}/{dataset}/{table}",
                            "category": "Fiscal Data",
                            "parameters": [{"name": "fields"}, {"name": "filter"}, {"name": "sort"}, {"name": "format", "default": "json"}, {"name": "page[number]", "schema": {"type": "integer"}}, {"name": "page[size]", "schema": {"type": "integer"}}],
                        },
                        {
                            "identity": "average-interest-rates-treasury-securities",
                            "name": "Average Interest Rates on U.S. Treasury Securities",
                            "operation": "/services/api/fiscal_service/v2/accounting/od/avg_interest_rates",
                            "category": "Treasury Interest Rates",
                            "parameters": [{"name": "fields"}, {"name": "filter"}, {"name": "sort"}, {"name": "format", "default": "json"}, {"name": "page[number]", "schema": {"type": "integer"}}, {"name": "page[size]", "schema": {"type": "integer"}}],
                        },
                        {
                            "identity": "treasury-securities-auction-results",
                            "name": "Treasury Securities Auction Results",
                            "operation": "/services/api/fiscal_service/v1/accounting/od/auctions_query",
                            "category": "Treasury Auctions",
                            "parameters": [{"name": "fields"}, {"name": "filter"}, {"name": "sort"}, {"name": "format", "default": "json"}, {"name": "page[number]", "schema": {"type": "integer"}}, {"name": "page[size]", "schema": {"type": "integer"}}],
                        },
                    ],
                    "max_bytes": 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "world_bank": {
        "source_name": "World Bank Indicators Data Source",
        "provider": "World Bank",
        "description": "Public World Bank Indicators API catalog for development, debt, macroeconomic, and financial indicators.",
        "coverage": ["macro", "development", "sovereign debt", "economic indicators", "global"],
        "authentication": "No API key is required for the World Bank Indicators API.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "world-bank-official-indicators-api",
                "location": "https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures",
                "description": "Official World Bank Indicators API documentation.",
                "options": {
                    "allowed_hosts": ["datahelpdesk.worldbank.org"],
                    "api_hosts": ["api.worldbank.org"],
                    "authentication_required": False,
                    "declared_only": True,
                    "declared_endpoints": [
                        *({"identity": resource, "name": resource.replace("_", " ").title(), "operation": f"/v2/{resource}", "category": "Reference data", "parameters": [{"name": "page", "schema": {"type": "integer"}}, {"name": "per_page", "schema": {"type": "integer"}}, {"name": "format", "default": "json"}, {"name": "source"}, {"name": "date"}] } for resource in ("country", "indicator", "source", "topic", "region", "incomelevel", "lendingtype")),
                        {"identity": "country-detail", "name": "Country detail", "operation": "/v2/country/{country}", "category": "Reference data", "parameters": [{"name": "format", "default": "json"}]},
                        {"identity": "indicator-detail", "name": "Indicator detail", "operation": "/v2/indicator/{indicator}", "category": "Reference data", "parameters": [{"name": "format", "default": "json"}, {"name": "source"}]},
                        {"identity": "country-indicator", "name": "Country indicator data", "operation": "/v2/country/{country}/indicator/{indicator}", "category": "Indicator data", "parameters": [{"name": "date"}, {"name": "mrv", "schema": {"type": "integer"}}, {"name": "mrnev", "schema": {"type": "integer"}}, {"name": "gapfill", "schema": {"type": "boolean"}}, {"name": "frequency"}, {"name": "page", "schema": {"type": "integer"}}, {"name": "per_page", "schema": {"type": "integer"}}, {"name": "format", "default": "json"}]},
                        {"identity": "source-country-series", "name": "Source country series data", "operation": "/v2/sources/{source}/country/{country}/series/{series}", "category": "Indicator data", "parameters": [{"name": "date"}, {"name": "page", "schema": {"type": "integer"}}, {"name": "per_page", "schema": {"type": "integer"}}, {"name": "format", "default": "json"}]},
                    ],
                    "max_bytes": 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "imf": {
        "source_name": "IMF Data Source",
        "provider": "International Monetary Fund",
        "description": "Official IMF SDMX API family catalog for international financial and macroeconomic statistics.",
        "coverage": ["macro", "balance of payments", "financial statistics", "national accounts", "global"],
        "authentication": "No API key is required for public IMF SDMX data requests; portal sign-in may apply to interactive exploration.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "imf-official-api-page",
                "location": "https://data.imf.org/en/Resource-Pages/IMF-API",
                "description": "Official IMF SDMX 2.1 and SDMX 3.0 API documentation.",
                "options": {
                    "allowed_hosts": ["data.imf.org"],
                    "use_curl": True,
                    "use_default_curl_user_agent": True,
                    "declared_endpoints": [
                        {"identity": "sdmx-2-1-data", "name": "SDMX 2.1 data API", "operation": "/sdmx/2.1/data/{flowRef}/{key}", "category": "SDMX 2.1", "parameters": [{"name": "startPeriod"}, {"name": "endPeriod"}, {"name": "firstNObservations", "schema": {"type": "integer"}}, {"name": "lastNObservations", "schema": {"type": "integer"}}, {"name": "dimension_at_observation"}, {"name": "detail"}, {"name": "includeHistory", "schema": {"type": "boolean"}}]},
                        {"identity": "sdmx-3-0-data", "name": "SDMX 3.0 data API", "operation": "/sdmx/3.0/data/{context}/{agency}/{resource}/{version}/{key}", "category": "SDMX 3.0", "parameters": [{"name": "startPeriod"}, {"name": "endPeriod"}, {"name": "firstNObservations", "schema": {"type": "integer"}}, {"name": "lastNObservations", "schema": {"type": "integer"}}, {"name": "attributes"}, {"name": "measures"}, {"name": "includeHistory", "schema": {"type": "boolean"}}]},
                    ],
                    "max_bytes": 512 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "ecb": {
        "source_name": "European Central Bank Data Source",
        "provider": "European Central Bank",
        "description": "Public ECB SDMX API catalog for monetary, banking, interest-rate, price, and exchange-rate statistics.",
        "coverage": ["macro", "central bank", "interest rates", "fx", "banking", "Europe"],
        "authentication": "No API key is required for the public ECB Data Portal web service.",
        "authentication_required": False,
        "sources": tuple(
            {
                "source_id": f"ecb-official-{kind}-api",
                "location": f"https://data.ecb.europa.eu/help/api/{kind}",
                "description": f"Official ECB SDMX {kind} API documentation.",
                "options": {
                    "allowed_hosts": ["data.ecb.europa.eu"],
                    "api_hosts": ["data-api.ecb.europa.eu"],
                    "authentication_required": False,
                    "declared_endpoints": [declared],
                    "max_bytes": 512 * 1024,
                    "timeout_sec": 60,
                },
            }
            for kind, declared in (
                ("data", {"identity": "sdmx-data", "name": "ECB SDMX data", "operation": "/service/data/{flowRef}/{key}", "category": "SDMX data", "parameters": [{"name": "startPeriod"}, {"name": "endPeriod"}, {"name": "firstNObservations", "schema": {"type": "integer"}}, {"name": "lastNObservations", "schema": {"type": "integer"}}, {"name": "detail"}, {"name": "updatedAfter"}]}),
                ("metadata", {"identity": "sdmx-metadata", "name": "ECB SDMX metadata", "operation": "/service/{resource}/{agencyID}/{resourceID}/{version}", "category": "SDMX metadata", "parameters": [{"name": "detail"}, {"name": "references"}]}),
            )
        ),
    },
    "bis": {
        "source_name": "Bank for International Settlements Data Source",
        "provider": "Bank for International Settlements",
        "description": "Official BIS SDMX OpenAPI catalog for banking, credit, debt, property, and exchange-rate statistics.",
        "coverage": ["banking", "credit", "debt securities", "fx", "property", "global"],
        "authentication": "No API key is required for public BIS statistics.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "bis-official-sdmx-openapi",
                "location": "https://stats.bis.org/api-doc/v2/bis-stats-api-latest.yaml",
                "media_type": "application/yaml",
                "description": "Official BIS SDMX REST OpenAPI specification.",
                "options": {"allowed_hosts": ["stats.bis.org"], "authentication_required": False, "use_curl": True, "max_bytes": 1024 * 1024, "timeout_sec": 60},
            },
        ),
    },
    "oecd": {
        "source_name": "OECD Data Explorer Data Source",
        "provider": "Organisation for Economic Co-operation and Development",
        "description": "Official OECD Data Explorer SDMX OpenAPI catalog for economic, financial, investment, and trade statistics.",
        "coverage": ["macro", "trade", "investment", "financial statistics", "global"],
        "authentication": "No API key; free public API subject to OECD terms and rate limits.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "oecd-official-sdmx-openapi",
                "location": "https://sdmx.oecd.org/public/swagger/v1/swagger.json",
                "media_type": "application/json",
                "description": "Official OECD Data Explorer SDMX REST OpenAPI specification.",
                "options": {
                    "allowed_hosts": ["sdmx.oecd.org"],
                    "authentication_required": False,
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "coingecko": {
        "source_name": "CoinGecko Data Source",
        "provider": "CoinGecko",
        "description": "Official CoinGecko endpoint catalog for cryptocurrency, exchanges, derivatives, NFTs, and on-chain markets.",
        "coverage": ["crypto", "digital assets", "derivatives", "nft", "on-chain"],
        "authentication": "User-owned CoinGecko Demo or Pro API connection and plan may be required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "coingecko-official-pro-openapi",
                "location": "https://docs.coingecko.com/openapi-specs/pro-api.json",
                "media_type": "application/json",
                "description": "Official public CoinGecko Pro API OpenAPI specification.",
                "options": {
                    "allowed_hosts": ["docs.coingecko.com"],
                    "authentication_required": True,
                    "max_bytes": 8 * 1024 * 1024,
                    "timeout_sec": 60,
                },
            },
        ),
    },
    "coinmarketcap": {
        "source_name": "CoinMarketCap Data Source",
        "provider": "CoinMarketCap",
        "description": "Official CoinMarketCap API catalog for cryptocurrency, exchanges, derivatives, global metrics, and DEX data.",
        "coverage": ["crypto", "digital assets", "derivatives", "rwa", "on-chain"],
        "authentication": "A user-owned CoinMarketCap API key is required for the full catalog; a limited keyless API may be available.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "coinmarketcap-official-llms-documentation",
                "location": "https://pro.coinmarketcap.com/llms-full.txt",
                "media_type": "text/plain",
                "description": "Official CoinMarketCap AI-friendly API reference corpus.",
                "options": {
                    "allowed_hosts": ["pro.coinmarketcap.com"],
                    "documented_endpoint_prefixes": ["/v1/", "/v2/", "/v3/", "/v4/", "/v5/"],
                    "documented_endpoint_category": "CoinMarketCap API",
                    "max_bytes": 1024 * 1024,
                    "timeout_sec": 90,
                },
            },
        ),
    },
    "openfigi": {
        "source_name": "OpenFIGI Data Source",
        "provider": "OpenFIGI",
        "description": "Official OpenFIGI OpenAPI catalog for identifier mapping and instrument reference-data search.",
        "coverage": ["security identifiers", "instrument reference", "equity", "fixed_income", "derivatives"],
        "authentication": "API key is optional; unauthenticated public access has lower limits.",
        "authentication_required": False,
        "sources": (
            {
                "source_id": "openfigi-official-openapi",
                "location": "https://api.openfigi.com/schema",
                "media_type": "application/json",
                "description": "Official OpenFIGI OpenAPI schema.",
                "options": {"allowed_hosts": ["api.openfigi.com"], "authentication_required": False, "max_bytes": 1024 * 1024, "timeout_sec": 60},
            },
        ),
    },
    "intrinio": {
        "source_name": "Intrinio Data Source",
        "provider": "Intrinio",
        "description": "Official Intrinio OpenAPI catalog for securities, prices, fundamentals, statements, and options.",
        "coverage": ["equity", "options", "fundamentals", "financial statements", "market data"],
        "authentication": "User-owned Intrinio API connection and product subscription required.",
        "authentication_required": True,
        "sources": (
            {
                "source_id": "intrinio-official-openapi",
                "location": "https://intrinio-api-spec.s3.amazonaws.com/open_api_spec.json",
                "media_type": "application/json",
                "description": "Official Intrinio OpenAPI specification used for generated SDKs and documentation.",
                "options": {"allowed_hosts": ["intrinio-api-spec.s3.amazonaws.com"], "max_bytes": 4 * 1024 * 1024, "timeout_sec": 120},
            },
        ),
    },
}


# Investment-relevant alternative-data providers. Machine-readable schemas are
# preferred; declared routes below are used only where the provider's public
# documentation does not expose one anonymous aggregate OpenAPI document.
PROVIDER_DEFINITIONS.update(
    {
        "ceic": {
            "source_name": "CEIC Data Source", "provider": "CEIC / ISI Markets",
            "description": "CEIC macroeconomic, alternative-data, release, insight, and time-series APIs.",
            "coverage": ["macroeconomics", "alternative data", "economic forecasts", "fund flows", "release calendars"],
            "authentication": "User-owned CEIC API token and database entitlements required.", "authentication_required": True,
            "sources": ({
                "source_id": "ceic-official-python-full-guide", "location": "https://developer.isimarkets.com/en/docs/ceic/macroeconomic/python/full-guide",
                "description": "Official generated CEIC API client reference.",
                "options": {"allowed_hosts": ["developer.isimarkets.com"], "api_hosts": ["api.ceicdata.com"], "declared_only": True, "declared_endpoints": [
                    {"identity": "dictionaries", "operation": "/v2/dictionary", "name": "List dictionaries"},
                    *[{"identity": f"dictionary-{name}", "operation": f"/v2/dictionary/{name}", "name": f"List {name}"} for name in ("databases", "countries", "frequencies", "indicators", "regions", "sources", "statuses", "units")],
                    {"identity": "series-search", "operation": "/v2/series/search", "name": "Search series", "parameters": [{"name": name} for name in ("keyword", "geo", "status", "data_source", "series", "topic", "section", "table", "frequency", "unit", "source", "start_date", "end_date", "limit", "offset")]},
                    {"identity": "series", "operation": "/v2/series/{id}", "name": "Get series", "parameters": [{"name": "id", "location": "path", "required": True}, {"name": "start_date"}, {"name": "end_date"}]},
                    {"identity": "series-statistics", "operation": "/v2/series/{id}/statistics", "name": "Get series statistics", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "continuous-series", "operation": "/v2/series/{id}/continuous", "name": "Get continuous series", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "continuous-series-timepoints", "operation": "/v2/series/{id}/continuous/{chain_id}/timepoints", "name": "Get continuous series timepoints", "parameters": [{"name": "id", "location": "path", "required": True}, {"name": "chain_id", "location": "path", "required": True}]},
                    {"identity": "series-release", "operation": "/v2/series/{id}/release", "name": "Get series release schedule", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "release-search", "operation": "/v2/releases/search", "name": "Search releases", "parameters": [{"name": "keyword"}, {"name": "release_date_from"}, {"name": "release_date_to"}, {"name": "limit"}, {"name": "offset"}]},
                    {"identity": "filters", "operation": "/v2/filters", "name": "List filters"},
                    {"identity": "create-filter", "operation": "/v2/filters", "method": "POST", "name": "Create filter", "parameters": [{"name": "body", "location": "body", "required": True, "schema": {"type": "object"}}]},
                    {"identity": "filter", "operation": "/v2/filters/{id}", "name": "Get filter", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "dumps", "operation": "/v2/dumps", "name": "List dumps"},
                    {"identity": "create-dump", "operation": "/v2/dumps", "method": "POST", "name": "Create dump", "parameters": [{"name": "body", "location": "body", "required": True, "schema": {"type": "object"}}]},
                    {"identity": "dump", "operation": "/v2/dumps/{id}", "name": "Get dump", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "feeds", "operation": "/v2/feeds", "name": "List feeds"},
                    {"identity": "feed", "operation": "/v2/feeds/{id}", "name": "Get feed", "parameters": [{"name": "id", "location": "path", "required": True}]},
                    {"identity": "insight-series", "operation": "/v2/insights/series/{series_id}", "name": "Get insight series", "parameters": [{"name": "series_id", "location": "path", "required": True}]},
                ], "max_bytes": 2 * 1024 * 1024, "timeout_sec": 90},
            },),
        },
        "factset": {
            "source_name": "FactSet Enterprise Data Source", "provider": "FactSet",
            "description": "All GET and POST data and analytics operations published in FactSet's official Enterprise SDK.",
            "coverage": ["fundamentals", "estimates", "ownership", "private markets", "debt", "ESG", "documents", "analytics"],
            "authentication": "Source-owned FactSet OAuth connection or API-key entitlement required.", "authentication_required": True,
            "sources": ({"source_id": "factset-official-enterprise-sdk", "kind": "repository", "location": "https://github.com/factset/enterprise-sdk.git", "media_type": "text/markdown", "description": "Official OpenAPI-generated FactSet Enterprise SDK reference files.", "options": {"github_sparse_patterns": ["code/python/*/*/docs/*Api.md"], "github_text_globs": ["code/python/*/*/docs/*Api.md"], "max_bytes": 64 * 1024 * 1024, "timeout_sec": 600}},),
        },
        "similarweb": {
            "source_name": "Similarweb Data Source", "provider": "Similarweb",
            "description": "Website, app, search, audience, digital marketing, and competitive-intelligence APIs.",
            "coverage": ["web traffic", "app intelligence", "search", "audience", "marketing channels", "competitive intelligence"],
            "authentication": "User-owned Similarweb API key and data credits required.", "authentication_required": True,
            "sources": ({"source_id": "similarweb-official-reference-index", "location": "https://developers.similarweb.com/llms.txt", "media_type": "text/plain", "description": "Official endpoint index and linked per-endpoint OpenAPI definitions.", "options": {"allowed_hosts": ["developers.similarweb.com"], "follow_markdown_links": True, "follow_link_path_prefix": "/reference/", "follow_link_limit": 500, "max_bytes": 128 * 1024 * 1024, "timeout_sec": 600}},),
        },
        "tomorrow_io": {
            "source_name": "Tomorrow.io Data Source", "provider": "Tomorrow.io",
            "description": "Weather timelines, forecasts, history, maps, routes, events, climate normals, and AI summaries.",
            "coverage": ["weather", "climate", "air quality", "agriculture", "aviation", "maritime", "routes"],
            "authentication": "User-owned Tomorrow.io API key and plan entitlements required.", "authentication_required": True,
            "sources": ({"source_id": "tomorrow-io-official-reference-index", "location": "https://docs.tomorrow.io/llms.txt", "media_type": "text/plain", "description": "Official endpoint index and linked per-endpoint OpenAPI definitions.", "options": {"allowed_hosts": ["docs.tomorrow.io"], "follow_markdown_links": True, "follow_link_path_prefix": "/reference/", "follow_link_limit": 250, "max_bytes": 96 * 1024 * 1024, "timeout_sec": 600}},),
        },
        "weatherbit": {
            "source_name": "Weatherbit Data Source", "provider": "Weatherbit",
            "description": "Current, forecast, historical, air-quality, agricultural, energy, climate, and severe-weather APIs.",
            "coverage": ["weather", "air quality", "agriculture", "energy", "climate", "weather alerts"],
            "authentication": "User-owned Weatherbit API key and endpoint plan required.", "authentication_required": True,
            "sources": ({"source_id": "weatherbit-official-swagger", "location": "https://www.weatherbit.io/static/swagger.json", "media_type": "application/json", "description": "Official Weatherbit Swagger specification.", "options": {"allowed_hosts": ["www.weatherbit.io"], "max_bytes": 16 * 1024 * 1024, "timeout_sec": 120}},),
        },
        "meteomatics": {
            "source_name": "Meteomatics Data Source", "provider": "Meteomatics",
            "description": "Point, time-series, polygon, route, model, radar, satellite, climate, and ensemble weather queries.",
            "coverage": ["weather", "climate", "radar", "satellite", "energy", "agriculture"],
            "authentication": "Source-owned Meteomatics credentials and product entitlements required.", "authentication_required": True,
            "sources": ({"source_id": "meteomatics-official-swagger-documentation", "location": "https://www.meteomatics.com/en/api/swagger-documentation/", "description": "Official Meteomatics Swagger documentation and dynamic URL contract.", "options": {"allowed_hosts": ["www.meteomatics.com"], "declared_only": True, "declared_endpoints": [{"identity": "weather-query", "operation": "/{valid_time}/{parameters}/{locations}/{format}", "name": "Retrieve weather data", "parameters": [{"name": name, "location": "path", "required": True} for name in ("valid_time", "parameters", "locations", "format")] + [{"name": name} for name in ("model", "calibrated", "ens_select", "cluster_select", "on_invalid", "run")]}]}},),
        },
        "planet": {
            "source_name": "Planet Data Source", "provider": "Planet Labs",
            "description": "Planet imagery catalog, orders, subscriptions, basemaps, and tasking APIs.",
            "coverage": ["satellite imagery", "tasking", "basemaps", "change detection", "analytics"],
            "authentication": "User-owned Planet API key and imagery entitlements required.", "authentication_required": True,
            "sources": tuple({"source_id": f"planet-official-{name}-openapi", "location": f"https://docs.planet.com/redocusaurus/{file}.yaml", "media_type": "text/yaml", "description": f"Official Planet {name} OpenAPI specification.", "options": {"allowed_hosts": ["docs.planet.com"], "max_bytes": 16 * 1024 * 1024, "timeout_sec": 180}} for name, file in (("data", "data-api-spec"), ("orders", "orders-api-spec"), ("subscriptions", "subscriptions-api-spec"), ("basemaps", "basemaps-api-spec"), ("tasking", "tasking-api-spec"))),
        },
        "sentinel_hub": {
            "source_name": "Sentinel Hub Data Source", "provider": "Sentinel Hub / Planet",
            "description": "Processing, catalog, statistical, batch, BYOC, Zarr, and third-party satellite-data APIs.",
            "coverage": ["satellite imagery", "STAC", "statistics", "batch processing", "BYOC", "third-party imagery"],
            "authentication": "Source-owned Sentinel Hub OAuth connection and processing-unit entitlements required.", "authentication_required": True,
            "sources": ({"source_id": "sentinel-hub-official-openapi", "location": "https://docs.sentinel-hub.com/api/latest/reference/openapi.v1.yaml", "media_type": "text/yaml", "description": "Official complete Sentinel Hub OpenAPI specification.", "options": {"allowed_hosts": ["docs.sentinel-hub.com"], "max_bytes": 32 * 1024 * 1024, "timeout_sec": 240}},),
        },
        "up42": {
            "source_name": "UP42 Data Source", "provider": "UP42",
            "description": "Satellite and aerial catalog, ordering, tasking, storage, processing, and STAC APIs.",
            "coverage": ["satellite imagery", "aerial imagery", "tasking", "processing", "STAC"],
            "authentication": "Source-owned UP42 OAuth connection and product credits required.", "authentication_required": True,
            "sources": ({"source_id": "up42-official-reference-index", "location": "https://developer.up42.com/llms.txt", "media_type": "text/plain", "description": "Official endpoint index and linked per-endpoint OpenAPI definitions.", "options": {"allowed_hosts": ["developer.up42.com"], "follow_markdown_links": True, "follow_link_path_prefix": "/reference/", "follow_link_limit": 300, "max_bytes": 96 * 1024 * 1024, "timeout_sec": 600}},),
        },
        "noaa_weather": {
            "source_name": "NOAA / NWS Weather Data Source", "provider": "NOAA National Weather Service",
            "description": "Official alerts, forecasts, observations, stations, radar, products, and gridpoint data.",
            "coverage": ["weather", "alerts", "forecasts", "observations", "radar"],
            "authentication": "No credential required; an identifying User-Agent is required.", "authentication_required": False,
            "sources": ({"source_id": "noaa-nws-official-openapi", "location": "https://api.weather.gov/openapi.json", "media_type": "application/json", "description": "Official NWS API OpenAPI document.", "options": {"allowed_hosts": ["api.weather.gov"], "authentication_required": False, "max_bytes": 8 * 1024 * 1024}},),
        },
        "nasa_cmr": {
            "source_name": "NASA CMR STAC Data Source", "provider": "NASA Earthdata",
            "description": "NASA Common Metadata Repository STAC collections, items, and spatiotemporal search.",
            "coverage": ["satellite metadata", "earth observation", "STAC", "collections", "granules"],
            "authentication": "Catalog search is public; individual data assets may require Earthdata Login.", "authentication_required": False,
            "sources": ({"source_id": "nasa-cmr-official-stac-redoc", "location": "https://cmr.earthdata.nasa.gov/stac/docs/index.html", "media_type": "text/html", "description": "Official NASA CMR STAC Redoc page with serialized OpenAPI specification.", "options": {"allowed_hosts": ["cmr.earthdata.nasa.gov"], "authentication_required": False, "max_bytes": 16 * 1024 * 1024}},),
        },
        "copernicus_data_space": {
            "source_name": "Copernicus Data Space Data Source", "provider": "Copernicus Data Space Ecosystem",
            "description": "Copernicus asset-level STAC catalog and spatiotemporal search API.",
            "coverage": ["Sentinel imagery", "STAC", "earth observation", "Copernicus collections"],
            "authentication": "STAC metadata search is public; downloads and processing may require a Copernicus account.", "authentication_required": False,
            "sources": (
                {"source_id": "copernicus-data-space-official-stac-openapi", "location": "https://stac.dataspace.copernicus.eu/v1/api", "media_type": "application/json", "description": "Official Copernicus Data Space STAC OpenAPI specification.", "options": {"allowed_hosts": ["stac.dataspace.copernicus.eu"], "authentication_required": False, "max_bytes": 8 * 1024 * 1024}},
                {"source_id": "copernicus-data-space-official-odp-openapi", "location": "https://odp.dataspace.copernicus.eu/odata/openapi.json", "media_type": "application/json", "description": "Official Copernicus On-Demand Production OpenAPI specification.", "options": {"allowed_hosts": ["odp.dataspace.copernicus.eu"], "authentication_required": True, "max_bytes": 8 * 1024 * 1024}},
            ),
        },
        "google_earth_engine": {
            "source_name": "Google Earth Engine Data Source", "provider": "Google Earth Engine",
            "description": "Complete stable, beta, and alpha Earth Engine REST data and computation operations.",
            "coverage": ["satellite imagery", "geospatial computation", "rasters", "vectors", "tables", "exports"],
            "authentication": "Source-owned Google OAuth connection and registered Earth Engine Cloud project required.", "authentication_required": True,
            "sources": tuple({"source_id": f"google-earth-engine-{version}-discovery", "location": f"https://earthengine.googleapis.com/$discovery/rest?version={version}", "media_type": "application/json", "description": f"Official Earth Engine {version} REST Discovery document.", "options": {"allowed_hosts": ["earthengine.googleapis.com"], "max_bytes": 16 * 1024 * 1024}} for version in ("v1", "v1beta", "v1alpha")),
        },
        "openweather": {
            "source_name": "OpenWeather Data Source", "provider": "OpenWeather",
            "description": "Current, forecast, historical, climate, air-pollution, geocoding, road-risk, solar, and map APIs.",
            "coverage": ["weather", "climate", "air pollution", "geocoding", "solar energy", "road risk"],
            "authentication": "User-owned OpenWeather API key and endpoint subscription required.", "authentication_required": True,
            "sources": ({"source_id": "openweather-official-api-documentation", "location": "https://openweathermap.org/api", "description": "Official OpenWeather API catalog.", "options": {"allowed_hosts": ["openweathermap.org"], "declared_only": True, "declared_endpoints": [
                *[{"identity": name, "operation": path, "name": name.replace("-", " ").title(), "parameters": [{"name": item} for item in params]} for name, path, params in (
                    ("current-weather", "/data/2.5/weather", ("lat", "lon", "q", "id", "units", "lang")), ("five-day-forecast", "/data/2.5/forecast", ("lat", "lon", "q", "id", "units", "lang", "cnt")),
                    ("one-call", "/data/3.0/onecall", ("lat", "lon", "exclude", "units", "lang")), ("one-call-history", "/data/3.0/onecall/timemachine", ("lat", "lon", "dt", "units")), ("day-summary", "/data/3.0/onecall/day_summary", ("lat", "lon", "date", "tz", "units")), ("weather-overview", "/data/3.0/onecall/overview", ("lat", "lon", "date", "units")),
                    ("air-pollution", "/data/2.5/air_pollution", ("lat", "lon")), ("air-pollution-forecast", "/data/2.5/air_pollution/forecast", ("lat", "lon")), ("air-pollution-history", "/data/2.5/air_pollution/history", ("lat", "lon", "start", "end")),
                    ("direct-geocoding", "/geo/1.0/direct", ("q", "limit")), ("reverse-geocoding", "/geo/1.0/reverse", ("lat", "lon", "limit")), ("zip-geocoding", "/geo/1.0/zip", ("zip",)),
                    ("road-risk", "/data/2.5/roadrisk", ()), ("solar-data", "/energy/1.0/solar/data", ("lat", "lon", "date", "tz")), ("solar-forecast", "/energy/1.0/solar/forecast", ("lat", "lon", "tz")), ("solar-interval", "/energy/1.0/solar/interval_data", ("lat", "lon", "start", "end", "interval", "tz")),
                    ("history-city", "/data/2.5/history/city", ("lat", "lon", "type", "start", "end", "cnt")), ("statistical-year", "/data/2.5/aggregated/year", ("lat", "lon")), ("statistical-month", "/data/2.5/aggregated/month", ("lat", "lon", "month")), ("statistical-day", "/data/2.5/aggregated/day", ("lat", "lon", "month", "day")),
                    ("weather-map", "/map/{layer}/{z}/{x}/{y}.png", ()),
                )],
            ]}},),
        },
    }
)

# CEIC's public application loads this complete Swagger document at runtime.
# Keep the machine schema as the sole active source so all published operations,
# parameters, and response models are normalized instead of retaining a manual
# sample of routes from the generated client guide.
PROVIDER_DEFINITIONS["ceic"] = {
    "source_name": "CEIC Data Source",
    "provider": "CEIC / ISI Markets",
    "description": "Complete CEIC macroeconomic, alternative-data, release, insight, and time-series Swagger API.",
    "coverage": ["macroeconomics", "alternative data", "economic forecasts", "fund flows", "release calendars"],
    "authentication": "User-owned CEIC API token and database entitlements required.",
    "authentication_required": True,
    "sources": (
        {
            "source_id": "ceic-official-public-swagger",
            "location": "https://developer.isimarkets.com/api/routing/swagger-spec/ceic-macroeconomic",
            "media_type": "application/json",
            "description": "Complete public Swagger document loaded by the official CEIC developer portal.",
            "options": {
                "allowed_hosts": ["developer.isimarkets.com"],
                "max_bytes": 4 * 1024 * 1024,
                "timeout_sec": 120,
            },
        },
    ),
}

PROVIDER_DEFINITIONS["planet"] = {
    "source_name": "Planet Data Source",
    "provider": "Planet Labs",
    "description": "Complete Planet-native imagery catalog, orders, subscriptions, basemaps, tasking, analytics, features, destinations, quota, and reports APIs.",
    "coverage": ["satellite imagery", "tasking", "basemaps", "change detection", "analytics"],
    "authentication": "User-owned Planet API key and imagery entitlements required.",
    "authentication_required": True,
    "sources": tuple(
        {
            "source_id": f"planet-official-{name}-openapi",
            "location": f"https://docs.planet.com/redocusaurus/{filename}.yaml",
            "media_type": "text/yaml",
            "description": f"Official Planet {name} OpenAPI specification.",
            "options": {
                "allowed_hosts": ["docs.planet.com"],
                "max_bytes": 16 * 1024 * 1024,
                "timeout_sec": 180,
            },
        }
        for name, filename in (
            ("analytics", "analytics-api-spec"),
            ("basemaps", "basemaps-api-spec"),
            ("data", "data-api-spec"),
            ("destinations", "destinations-api-spec"),
            ("features", "features-api-spec"),
            ("orders", "orders-api-spec"),
            ("quota", "quota-api-spec"),
            ("reports", "reports-api-spec"),
            ("subscriptions", "subscriptions-api-spec"),
            ("tasking", "tasking-api-spec"),
        )
    ),
}

PROVIDER_DEFINITIONS["sustainable1"] = {
    "source_name": "S&P Global Sustainable1 Data Source",
    "provider": "S&P Global Sustainable1",
    "description": "Official XpressAPI ESG scores, climate physical risk, Trucost environmental data, carbon earnings at risk and Paris alignment.",
    "coverage": ["esg", "climate risk", "emissions", "environmental", "paris alignment"],
    "authentication": "Source-owned XpressAPI bearer token and dataset entitlements required.",
    "authentication_required": True,
    "sources": tuple({
        "source_id": "sustainable1-"+service+"-openapi",
        "location": "https://xpressapi.marketplace.spglobal.com/"+service+"/v3/api-docs",
        "media_type": "application/json",
        "description": "Official S&P Global XpressAPI "+service+" OpenAPI specification.",
        "options": {"allowed_hosts": ["xpressapi.marketplace.spglobal.com"], "max_bytes": 16*1024*1024, "timeout_sec": 30},
    } for service in ("esgscores", "physicalrisk", "trucostcear", "trucostenvironmental", "trucostparisalignment")),
}

PROVIDER_DEFINITIONS.update(TAIWAN_SOURCE_DEFINITIONS)
PROVIDER_DEFINITIONS[SHIOAJI_SOURCE_ID] = SHIOAJI_SOURCE_DEFINITION


from .enterprise_products import PRODUCTS as ENTERPRISE_PRODUCTS, PUBLIC_SPECS as ENTERPRISE_PUBLIC_SPECS

for _id, (_name, _class, _port, _url, _coverage) in ENTERPRISE_PRODUCTS.items():
    PROVIDER_DEFINITIONS[_id] = {
        "source_name": _name, "provider": "Bloomberg" if _id.startswith("bloomberg") else "FactSet",
        "description": _name + ": source-owned licensed API, file, cloud and feed connections.",
        "coverage": _coverage, "authentication": "Source-owned licensed connection required; no credentials in Browser requests.",
        "authentication_required": True,
        "sources": ({"source_id": _id+"-product", "location": _url}, *(
            {"source_id": _id+"-"+name, "location": "https://raw.githubusercontent.com/factset/enterprise-sdk/main/specs/"+name,
             "media_type": "application/yaml", "options": {"allowed_hosts": ["api.factset.com", "api-sandbox.factset.com"]}}
            for name in ENTERPRISE_PUBLIC_SPECS.get(_id, []))),
    }
PROVIDER_DEFINITIONS['lseg_workspace'] = {
    'source_name': 'LSEG Workspace', 'provider': 'LSEG',
    'description': 'Source-owner local Workspace desktop API through the LSEG Data Library.',
    'coverage': ['equities','funds','quotes','history','fundamentals','news','search','symbology'],
    'authentication': 'Signed-in Workspace desktop session on the source host.',
    'authentication_required': True,
    'sources': ({'source_id': 'lseg-workspace-official-library', 'location': WORKSPACE_DOCS+'index.html',
                 'options': {'allowed_hosts': ['cdn.refinitiv.com']}},),
}
ADDITIONAL_SOURCE_IDS = (*ADDITIONAL_SOURCE_IDS, *ENTERPRISE_PRODUCTS, 'lseg_workspace')
SUPPORTED_SOURCE_IDS = (*TOP_TWENTY_SOURCE_IDS, *ADDITIONAL_SOURCE_IDS,
                        *INVESTMENT_ALTERNATIVE_DATA_SOURCE_IDS, *TAIWAN_SOURCE_IDS, *SHIOAJI_SOURCE_IDS)


def provider_spec_sources(vendor_id: str) -> tuple[VendorSpecSource, ...]:
    """Build canonical VendorSpecSource values shared by runtime and refresh."""
    if vendor_id in TAIWAN_SOURCE_IDS:
        return taiwan_spec_sources(vendor_id)
    if vendor_id in SHIOAJI_SOURCE_IDS:
        return shioaji_spec_sources()
    definition = PROVIDER_DEFINITIONS[vendor_id]
    sources: list[VendorSpecSource] = []
    for item in definition["sources"]:
        sources.append(
            VendorSpecSource(
                source_id=str(item["source_id"]),
                vendor_id=vendor_id,
                kind=str(item.get("kind") or "website"),
                location=str(item["location"]),
                media_type=str(item.get("media_type") or "text/html"),
                description=str(item.get("description") or ""),
                options=dict(item.get("options") or {}),
            )
        )
    return tuple(sources)
