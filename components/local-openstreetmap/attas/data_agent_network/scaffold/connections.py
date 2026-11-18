"""Source-owned connections and allowlisted direct provider execution."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, Mapping
from urllib.parse import quote, urlparse
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from .models import EndpointExecutionResult


ACCESS_MODE_PULSE = "data_source_pulse"
ACCESS_MODE_DIRECT = "source_endpoint"
ACCESS_MODES = (ACCESS_MODE_PULSE, ACCESS_MODE_DIRECT)


DIRECT_BROWSER_BYOK_SOURCE_IDS = frozenset(
    {
        "alpha_vantage", "finnhub", "aisa_one", "twelve_data", "massive",
        "financial_modeling_prep", "eodhd", "tiingo", "nasdaq_data_link",
        "sec_edgar", "fred", "treasury_fiscal_data", "world_bank", "imf",
        "ecb", "bis", "oecd", "coingecko", "coinmarketcap", "openfigi",
        "intrinio",
        "crunchbase", "dealroom", "quartr",
        "ceic", "similarweb", "tomorrow_io", "weatherbit", "planet",
        "noaa_weather", "nasa_cmr", "copernicus_data_space", "openweather",
        "finmind", "fugle",
    }
)

# These providers currently offer public or key-optional access. They still
# receive the same browser-local BYOK controls as every other Data Source.
OPTIONAL_BROWSER_KEY_SOURCE_IDS = frozenset(
    {
        "yfinance",
        "sec_edgar",
        "treasury_fiscal_data",
        "world_bank",
        "imf",
        "ecb",
        "bis",
        "oecd",
        "openfigi",
        "noaa_weather",
        "nasa_cmr",
        "copernicus_data_space",
        "twse",
        "tpex",
        "taifex",
        "finmind",
        "taiwan_central_bank",
    }
)


PROVIDER_EXECUTION_POLICIES: Dict[str, Dict[str, Any]] = {
    "sustainable1": {"base_url": "https://xpressapi.marketplace.spglobal.com", "auth": ("header", "Authorization", "Bearer "), "env": "SUSTAINABLE1_ACCESS_TOKEN", "allowed_base_hosts": ("xpressapi.marketplace.spglobal.com",)},
    "morningstar": {"base_url": "https://www.us-api.morningstar.com", "auth": ("header", "Authorization", "Bearer "), "env": "MORNINGSTAR_ACCESS_TOKEN"},
    "crunchbase": {"base_url": "https://api.crunchbase.com/v4/data", "auth": ("header", "X-cb-user-key", ""), "env": "CRUNCHBASE_API_KEY"},
    "moodys_analytics": {"base_url": "https://api.economy.com/data/v1", "auth": ("header", "Authorization", "Bearer "), "env": "MOODYS_ANALYTICS_ACCESS_TOKEN"},
    "dealroom": {"base_url": "https://api.dealroom.co/api/v1", "auth": ("basic_username", "Authorization", "Basic "), "env": "DEALROOM_API_KEY"},
    "quartr": {"base_url": "https://api.quartr.com", "auth": ("header", "x-api-key", ""), "env": "QUARTR_API_KEY"},
    "visible": {"base_url": "https://api.visible.vc", "auth": ("header", "Authorization", "Bearer "), "env": "VISIBLE_ACCESS_TOKEN"},
    "lseg_datascope_select": {"base_url":"https://selectapi.datascope.lseg.com/RestApi/v1", "env":"LSEG_DSS_CREDENTIALS", "workflow_adapter":True},
    "lseg_worldcheck_one": {"base_url":"https://api.risk.lseg.com/screening/v3", "env":"LSEG_WC1_CREDENTIALS", "workflow_adapter":True},
    "lseg": {"base_url": "https://api.refinitiv.com", "auth": ("header", "Authorization", "Bearer "), "env": "LSEG_ACCESS_TOKEN"},
    "aisa_one": {"base_url": "https://api.aisa.one", "auth": ("header", "Authorization", "Bearer "), "env": "AISA_API_KEY", "allowed_base_hosts": ("api.aisa.one",)},
    "twelve_data": {"base_url": "https://api.twelvedata.com", "auth": ("header", "Authorization", "apikey "), "env": "TWELVE_DATA_API_KEY"},
    "massive": {"base_url": "https://api.massive.com", "auth": ("header", "Authorization", "Bearer "), "env": "MASSIVE_API_KEY", "deny_prefixes": ("/rest/",)},
    "financial_modeling_prep": {"base_url": "https://financialmodelingprep.com", "auth": ("query", "apikey", ""), "env": "FMP_API_KEY", "deny_prefixes": ("/developer",)},
    "eodhd": {"base_url": "https://eodhd.com/api", "auth": ("query", "api_token", ""), "env": "EODHD_API_TOKEN", "defaults": {"fmt": "json"}},
    "tiingo": {"base_url": "https://api.tiingo.com", "auth": ("header", "Authorization", "Token "), "env": "TIINGO_API_TOKEN"},
    "nasdaq_data_link": {"base_url": "https://data.nasdaq.com", "auth": ("query", "api_key", ""), "env": "NASDAQ_DATA_LINK_API_KEY", "deny_prefixes": ("/docs/", "/llms",)},
    "sec_edgar": {"base_url": "https://data.sec.gov", "required": False, "headers": {"User-Agent": "Attas Data Agent Network admin@retis.ai"}},
    "fred": {"base_url": "https://api.stlouisfed.org", "auth": ("query", "api_key", ""), "env": "FRED_API_KEY", "defaults": {"file_type": "json"}},
    "treasury_fiscal_data": {"base_url": "https://api.fiscaldata.treasury.gov", "required": False},
    "world_bank": {"base_url": "https://api.worldbank.org", "required": False, "defaults": {"format": "json"}},
    "imf": {"base_url": "https://api.imf.org/external", "required": False},
    "ecb": {"base_url": "https://data-api.ecb.europa.eu", "required": False},
    "bis": {"base_url": "https://stats.bis.org/api/v2", "required": False},
    "oecd": {"base_url": "https://sdmx.oecd.org/public", "required": False},
    "coingecko": {"base_url": "https://pro-api.coingecko.com/api/v3", "auth": ("header", "x-cg-pro-api-key", ""), "env": "COINGECKO_API_KEY"},
    "coinmarketcap": {"base_url": "https://pro-api.coinmarketcap.com", "auth": ("header", "X-CMC_PRO_API_KEY", ""), "env": "COINMARKETCAP_API_KEY"},
    "openfigi": {"base_url": "https://api.openfigi.com/v3", "auth": ("header", "X-OPENFIGI-APIKEY", ""), "env": "OPENFIGI_API_KEY", "required": False},
    "intrinio": {"base_url": "https://api-v2.intrinio.com", "auth": ("header", "Authorization", "Bearer "), "env": "INTRINIO_API_KEY"},
    "ceic": {"base_url": "https://api.ceicdata.com", "auth": ("query", "token", ""), "env": "CEIC_API_TOKEN", "allowed_base_hosts": ("api.ceicdata.com",)},
    "factset": {"base_url": "https://api.factset.com", "auth": ("header", "Authorization", "Bearer "), "env": "FACTSET_ACCESS_TOKEN", "allowed_base_hosts": ("api.factset.com",)},
    "similarweb": {"base_url": "https://api.similarweb.com", "auth": ("query", "api_key", ""), "env": "SIMILARWEB_API_KEY", "allowed_base_hosts": ("api.similarweb.com",)},
    "tomorrow_io": {"base_url": "https://api.tomorrow.io/v4", "auth": ("query", "apikey", ""), "env": "TOMORROW_IO_API_KEY", "allowed_base_hosts": ("api.tomorrow.io",)},
    "weatherbit": {"base_url": "https://api.weatherbit.io/v2.0", "auth": ("query", "key", ""), "env": "WEATHERBIT_API_KEY", "allowed_base_hosts": ("api.weatherbit.io",)},
    "meteomatics": {"base_url": "https://api.meteomatics.com", "auth": ("basic_pair", "Authorization", "Basic "), "env": "METEOMATICS_CREDENTIALS", "allowed_base_hosts": ("api.meteomatics.com",)},
    "planet": {"base_url": "https://api.planet.com", "auth": ("basic_username", "Authorization", "Basic "), "env": "PLANET_API_KEY", "allowed_base_hosts": ("api.planet.com", "tiles.planet.com", "docs.planet.com")},
    "sentinel_hub": {"base_url": "https://services.sentinel-hub.com", "auth": ("header", "Authorization", "Bearer "), "env": "SENTINEL_HUB_ACCESS_TOKEN", "allowed_base_hosts": ("services.sentinel-hub.com", "services-uswest2.sentinel-hub.com")},
    "up42": {"base_url": "https://api.up42.com", "auth": ("header", "Authorization", "Bearer "), "env": "UP42_ACCESS_TOKEN", "allowed_base_hosts": ("api.up42.com",)},
    "noaa_weather": {"base_url": "https://api.weather.gov", "required": False, "headers": {"User-Agent": "Attas Data Agent Network admin@retis.ai"}, "allowed_base_hosts": ("api.weather.gov",)},
    "nasa_cmr": {"base_url": "https://cmr.earthdata.nasa.gov/stac", "required": False, "allowed_base_hosts": ("cmr.earthdata.nasa.gov",)},
    "copernicus_data_space": {"base_url": "https://stac.dataspace.copernicus.eu/v1", "auth": ("header", "Authorization", "Bearer "), "env": "COPERNICUS_DATA_SPACE_ACCESS_TOKEN", "required": False, "allowed_base_hosts": ("stac.dataspace.copernicus.eu", "odp.dataspace.copernicus.eu")},
    "google_earth_engine": {"base_url": "https://earthengine.googleapis.com", "auth": ("header", "Authorization", "Bearer "), "env": "GOOGLE_EARTH_ENGINE_ACCESS_TOKEN", "allowed_base_hosts": ("earthengine.googleapis.com",)},
    "openweather": {"base_url": "https://api.openweathermap.org", "auth": ("query", "appid", ""), "env": "OPENWEATHER_API_KEY", "allowed_base_hosts": ("api.openweathermap.org", "tile.openweathermap.org")},
    "twse": {"base_url": "https://openapi.twse.com.tw/v1", "required": False, "allowed_base_hosts": ("openapi.twse.com.tw",)},
    "tpex": {"base_url": "https://www.tpex.org.tw/openapi/v1", "required": False, "allowed_base_hosts": ("www.tpex.org.tw",)},
    "taifex": {"base_url": "https://openapi.taifex.com.tw/v1", "required": False, "allowed_base_hosts": ("openapi.taifex.com.tw",)},
    "finmind": {"base_url": "https://api.finmindtrade.com/api/v4", "auth": ("header", "Authorization", "Bearer "), "env": "FINMIND_API_TOKEN", "required": False, "allowed_base_hosts": ("api.finmindtrade.com",)},
    "taiwan_central_bank": {"base_url": "https://cpx.cbc.gov.tw", "required": False, "allowed_base_hosts": ("cpx.cbc.gov.tw",)},
    "fugle": {"base_url": "https://api.fugle.tw/marketdata/v1.0/stock", "auth": ("header", "X-API-KEY", ""), "env": "FUGLE_MARKETDATA_API_KEY", "allowed_base_hosts": ("api.fugle.tw",)},
}


def provider_endpoint_is_executable(source_id: str, operation: str) -> bool:
    """Return whether an operation is a verified provider API route."""
    policy = PROVIDER_EXECUTION_POLICIES.get(str(source_id or "").strip().lower())
    route = str(operation or "").strip()
    if (
        not policy
        or not route.startswith("/")
        or route.startswith("//")
        or any(marker in route for marker in ("?", "#", "\\"))
        or ".." in route.split("/")
    ):
        return False
    return not route.startswith(tuple(policy.get("deny_prefixes") or ()))


PROVIDER_CONNECTION_PROFILES: Dict[str, Dict[str, Any]] = {
    "lseg_workspace": {
        "credential_mode": "licensed_session", "credential_label": "Local Workspace desktop session",
        "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [], "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection", "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Keep Refinitiv/LSEG Workspace running and signed in on the source Mac. The source uses its desktop session; no browser API key is needed.",
    },
    "sustainable1": {
        "credential_mode": "delegated", "credential_label": "Sustainable1 XpressAPI bearer token",
        "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [], "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection", "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Configure SUSTAINABLE1_ACCESS_TOKEN on the source. Obtain tokens through XpressAPI authentication; licensed dataset entitlements are required.",
    },
    "lseg_datascope_select": {
        "credential_mode":"delegated", "credential_label":"DSS source-owned credential JSON", "manageable":False, "required":True,
        "access_modes":[ACCESS_MODE_PULSE], "browser_key_access_modes":[], "direct_access_enabled":False,
        "direct_access_status":"source_owned_connection", "default_access_mode":ACCESS_MODE_PULSE, "pulse_authentication":"source_owned",
        "connection_note":"Configure LSEG_DSS_CREDENTIALS on the source with username/password or access_token. Provider billing is separate from Plaza points.",
    },
    "lseg_worldcheck_one": {
        "credential_mode":"delegated", "credential_label":"World-Check One source-owned credential JSON", "manageable":False, "required":True,
        "access_modes":[ACCESS_MODE_PULSE], "browser_key_access_modes":[], "direct_access_enabled":False,
        "direct_access_status":"source_owned_connection", "default_access_mode":ACCESS_MODE_PULSE, "pulse_authentication":"source_owned",
        "connection_note":"Configure LSEG_WC1_CREDENTIALS on the source with service_account_uuid/service_account_password, or auth_mode=hmac with api_key/api_secret.",
    },
    "lseg": {
        "credential_mode": "delegated",
        "credential_label": "LSEG licensed OAuth connection",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Connect LSEG through the source-owned licensed OAuth session; browser API-key entry is disabled.",
    },
    "bloomberg": {
        "credential_mode": "licensed_session",
        "credential_label": "Bloomberg licensed BLPAPI session",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Connect through a Bloomberg Desktop API, SAPI, or B-Pipe session; browser credential entry is disabled.",
    },
    "morningstar": {
        "credential_mode": "delegated",
        "credential_label": "Morningstar Direct Web Services OAuth connection",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Use the source-owned Morningstar OAuth connection or server-side access token; browser token entry is disabled.",
    },
    "moodys_analytics": {
        "credential_mode": "delegated",
        "credential_label": "Moody's Analytics OAuth connection",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Use the source-owned Moody's OAuth client-credentials session; browser client-secret or token entry is disabled.",
    },
    "visible": {
        "credential_mode": "delegated",
        "credential_label": "Visible bearer connection",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Use a source-owned Visible bearer connection; browser access-token entry is disabled.",
    },
    "yfinance": {
        "credential_label": "YFinance credential (optional)",
        "required": False,
    },
    "alpha_vantage": {
        "credential_label": "Alpha Vantage API key",
    },
    "finnhub": {
        "credential_label": "Finnhub API key",
    },
    "aisa_one": {
        "credential_label": "AIsa.one API key",
    },
    "twelve_data": {"credential_label": "Twelve Data API key"},
    "massive": {"credential_label": "Massive API key"},
    "financial_modeling_prep": {"credential_label": "Financial Modeling Prep API key"},
    "eodhd": {"credential_label": "EODHD API token"},
    "tiingo": {"credential_label": "Tiingo API token"},
    "nasdaq_data_link": {"credential_label": "Nasdaq Data Link API key"},
    "sec_edgar": {"credential_label": "SEC EDGAR credential (optional)"},
    "fred": {"credential_label": "FRED API key"},
    "treasury_fiscal_data": {"credential_label": "Fiscal Data credential (optional)"},
    "world_bank": {"credential_label": "World Bank credential (optional)"},
    "imf": {"credential_label": "IMF credential (optional)"},
    "ecb": {"credential_label": "ECB credential (optional)"},
    "bis": {"credential_label": "BIS credential (optional)"},
    "oecd": {"credential_label": "OECD credential (optional)"},
    "coingecko": {"credential_label": "CoinGecko API key"},
    "coinmarketcap": {"credential_label": "CoinMarketCap API key"},
    "openfigi": {"credential_label": "OpenFIGI API key (optional)"},
    "intrinio": {"credential_label": "Intrinio API key"},
    "crunchbase": {"credential_label": "Crunchbase API key"},
    "dealroom": {"credential_label": "Dealroom Premium API key"},
    "quartr": {"credential_label": "Quartr API key"},
    "ceic": {"credential_label": "CEIC API token"},
    "similarweb": {"credential_label": "Similarweb API key"},
    "tomorrow_io": {"credential_label": "Tomorrow.io API key"},
    "weatherbit": {"credential_label": "Weatherbit API key"},
    "planet": {"credential_label": "Planet API key"},
    "noaa_weather": {"credential_label": "NOAA credential (not required)", "required": False},
    "nasa_cmr": {"credential_label": "NASA Earthdata credential (optional)", "required": False},
    "copernicus_data_space": {"credential_label": "Copernicus credential (optional)", "required": False},
    "openweather": {"credential_label": "OpenWeather API key"},
    "twse": {"credential_label": "TWSE credential (not required)", "required": False},
    "tpex": {"credential_label": "TPEx credential (not required)", "required": False},
    "taifex": {"credential_label": "TAIFEX credential (not required)", "required": False},
    "finmind": {"credential_label": "FinMind token (optional)", "required": False},
    "taiwan_central_bank": {"credential_label": "Taiwan central-bank credential (not required)", "required": False},
    "fugle": {"credential_label": "Fugle market-data API key"},
    "shioaji": {
        "credential_mode": "licensed_session",
        "credential_label": "SinoPac Shioaji gateway session",
        "manageable": False,
        "required": True,
        "access_modes": [ACCESS_MODE_PULSE],
        "browser_key_access_modes": [],
        "direct_access_enabled": False,
        "direct_access_status": "source_owned_connection",
        "default_access_mode": ACCESS_MODE_PULSE,
        "pulse_authentication": "source_owned",
        "connection_note": "Run `shioaji server start` with SJ_API_KEY and SJ_SEC_KEY in that gateway's .env; the Data Source calls the gateway and never handles SinoPac keys. Browser key entry is disabled.",
    },
    "factset": {"credential_mode": "delegated", "credential_label": "FactSet OAuth connection", "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [], "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned", "connection_note": "Use a source-owned FactSet OAuth connection; browser client secrets and tokens are disabled."},
    "meteomatics": {"credential_mode": "server_credentials", "credential_label": "Meteomatics licensed connection", "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [], "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned", "connection_note": "Use source-owned Meteomatics credentials; username/password entry is disabled in normal browser setup."},
    "sentinel_hub": {"credential_mode": "delegated", "credential_label": "Sentinel Hub OAuth connection", "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [], "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned", "connection_note": "Use the source-owned Sentinel Hub OAuth client; browser client-secret or token entry is disabled."},
    "up42": {"credential_mode": "delegated", "credential_label": "UP42 OAuth connection", "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [], "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned", "connection_note": "Use the source-owned UP42 OAuth connection; browser client-secret or token entry is disabled."},
    "google_earth_engine": {"credential_mode": "delegated", "credential_label": "Google Earth Engine OAuth connection", "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [], "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned", "connection_note": "Use delegated Google OAuth; browser access-token entry is disabled."},
}


from .enterprise_products import PRODUCTS as ENTERPRISE_PRODUCTS
for _id, _product in ENTERPRISE_PRODUCTS.items():
    PROVIDER_EXECUTION_POLICIES[_id] = {"base_url": "", "env": _id.upper()+"_CREDENTIALS", "workflow_adapter": True}
    PROVIDER_CONNECTION_PROFILES[_id] = {"credential_mode": "delegated", "credential_label": _product[0]+" connection JSON or token",
        "manageable": False, "required": True, "access_modes": [ACCESS_MODE_PULSE], "browser_key_access_modes": [],
        "direct_access_enabled": False, "default_access_mode": ACCESS_MODE_PULSE, "pulse_authentication": "source_owned",
        "connection_note": "Configure vendor credentials on the source. Delivery manifests and licensed schemas are installed with run_enterprise_product_sources.py."}


def provider_connection_profile(source_id: str) -> Dict[str, Any]:
    """Return uniform browser-local BYOK capabilities for one Data Source."""
    normalized_source = str(source_id or "").strip().lower()
    override = PROVIDER_CONNECTION_PROFILES.get(normalized_source) or {}
    required = bool(
        override.get(
            "required",
            normalized_source not in OPTIONAL_BROWSER_KEY_SOURCE_IDS,
        )
    )
    direct_access_enabled = bool(
        override.get(
            "direct_access_enabled",
            normalized_source in DIRECT_BROWSER_BYOK_SOURCE_IDS,
        )
    )
    display_name = " ".join(part.capitalize() for part in normalized_source.split("_") if part)
    credential_label = str(
        override.get("credential_label")
        or f"{display_name or 'Provider'} API key or token"
    )
    if override.get("connection_note"):
        connection_note = str(override["connection_note"])
    elif direct_access_enabled:
        connection_note = (
            "Direct vendor execution is allowlisted. When a key is required, it is "
            "sent only for the selected request and is never copied into a pulse."
        )
    elif required:
        connection_note = (
            "This key stays in the browser. Direct execution requires an allowlisted "
            "provider adapter; Data Source pulses never receive this key."
        )
    else:
        connection_note = (
            "Public or pulse access works without a browser key. An optional local key "
            "can be kept for a future allowlisted provider adapter."
        )
    return {
        "credential_mode": str(override.get("credential_mode") or "api_key"),
        "credential_label": credential_label,
        "manageable": bool(override.get("manageable", True)),
        "required": required,
        "access_modes": list(override.get("access_modes") or [ACCESS_MODE_PULSE, ACCESS_MODE_DIRECT]),
        "default_access_mode": str(
            override.get("default_access_mode")
            or (ACCESS_MODE_DIRECT if direct_access_enabled else ACCESS_MODE_PULSE)
        ),
        "browser_key_access_modes": list(
            override.get("browser_key_access_modes")
            if "browser_key_access_modes" in override
            else [ACCESS_MODE_DIRECT]
        ),
        "direct_access_enabled": direct_access_enabled,
        "direct_access_status": str(
            override.get("direct_access_status")
            or ("enabled" if direct_access_enabled else "adapter_required")
        ),
        "pulse_authentication": str(
            override.get("pulse_authentication")
            or ("source_owned" if required else "not_required")
        ),
        "connection_note": connection_note,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_config_path(value: Any, config_path: Path | None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = config_path.resolve().parent if config_path else Path.cwd()
        path = (base / path).resolve()
    return path


class DataSourceCredentialStore:
    """Encrypt source-owned credentials and expose only opaque connections."""

    def __init__(
        self,
        sqlite_path: str | Path = ":memory:",
        *,
        key_path: str | Path | None = None,
        encryption_key: str | bytes | None = None,
        owner_id: str = "data-source-agent",
    ) -> None:
        self.sqlite_path = str(sqlite_path or ":memory:")
        self.key_path = Path(key_path).expanduser().resolve() if key_path else None
        self.owner_id = str(owner_id or "data-source-agent").strip() or "data-source-agent"
        self._guard = threading.RLock()
        key = self._resolve_encryption_key(encryption_key)
        self._cipher = Fernet(key)
        if self.sqlite_path != ":memory:":
            database_path = Path(self.sqlite_path).expanduser().resolve()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self.sqlite_path = str(database_path)
        self._connection = sqlite3.connect(
            self.sqlite_path,
            timeout=15,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _resolve_encryption_key(self, configured: str | bytes | None) -> bytes:
        value: str | bytes | None = configured or os.getenv("ATTAS_DATA_SOURCE_CREDENTIAL_KEY")
        if value:
            return value.encode("utf-8") if isinstance(value, str) else value
        if self.key_path is None:
            return Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self.key_path.read_bytes().strip()
        except FileNotFoundError:
            generated = Fernet.generate_key()
            try:
                descriptor = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                return self.key_path.read_bytes().strip()
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(generated)
            return generated

    def _initialize(self) -> None:
        with self._guard, self._connection:
            if self.sqlite_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_source_connections (
                    connection_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    encrypted_secret BLOB NOT NULL,
                    masked_hint TEXT NOT NULL,
                    preferred_access_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, source_id)
                )
                """
            )
        if self.sqlite_path != ":memory:":
            try:
                os.chmod(self.sqlite_path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _masked_hint(secret: str) -> str:
        suffix = secret[-4:] if len(secret) >= 4 else secret[-1:]
        return f"••••{suffix}" if suffix else "••••"

    @staticmethod
    def _public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "connection_id": str(row.get("connection_id") or ""),
            "source_id": str(row.get("source_id") or ""),
            "credential_type": str(row.get("credential_type") or "api_key"),
            "masked_key": str(row.get("masked_hint") or "••••"),
            "preferred_access_mode": str(row.get("preferred_access_mode") or ACCESS_MODE_PULSE),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "configured": True,
        }

    def save_api_key(
        self,
        source_id: str,
        api_key: str,
        *,
        preferred_access_mode: str = ACCESS_MODE_PULSE,
        owner_id: str | None = None,
    ) -> Dict[str, Any]:
        normalized_source = str(source_id or "").strip().lower()
        secret = str(api_key or "").strip()
        owner = str(owner_id or self.owner_id).strip() or self.owner_id
        if not normalized_source:
            raise ValueError("source_id is required.")
        if len(secret) < 4 or len(secret) > 4096:
            raise ValueError("API key must contain between 4 and 4096 characters.")
        if preferred_access_mode not in ACCESS_MODES:
            raise ValueError("Unsupported access mode.")
        now = _utc_now()
        with self._guard, self._connection:
            existing = self._connection.execute(
                "SELECT connection_id, created_at FROM data_source_connections WHERE owner_id = ? AND source_id = ?",
                (owner, normalized_source),
            ).fetchone()
            connection_id = str(existing["connection_id"]) if existing else f"dsc_{uuid4().hex}"
            created_at = str(existing["created_at"]) if existing else now
            encrypted = self._cipher.encrypt(secret.encode("utf-8"))
            self._connection.execute(
                """
                INSERT INTO data_source_connections (
                    connection_id, owner_id, source_id, credential_type,
                    encrypted_secret, masked_hint, preferred_access_mode,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'api_key', ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, source_id) DO UPDATE SET
                    connection_id = excluded.connection_id,
                    credential_type = excluded.credential_type,
                    encrypted_secret = excluded.encrypted_secret,
                    masked_hint = excluded.masked_hint,
                    preferred_access_mode = excluded.preferred_access_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    connection_id,
                    owner,
                    normalized_source,
                    encrypted,
                    self._masked_hint(secret),
                    preferred_access_mode,
                    created_at,
                    now,
                ),
            )
        return self.connection_metadata(normalized_source, owner_id=owner) or {}

    def update_preferred_access_mode(
        self,
        source_id: str,
        preferred_access_mode: str,
        *,
        owner_id: str | None = None,
    ) -> Dict[str, Any] | None:
        if preferred_access_mode not in ACCESS_MODES:
            raise ValueError("Unsupported access mode.")
        owner = str(owner_id or self.owner_id).strip() or self.owner_id
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE data_source_connections
                   SET preferred_access_mode = ?, updated_at = ?
                 WHERE owner_id = ? AND source_id = ?
                """,
                (preferred_access_mode, _utc_now(), owner, str(source_id or "").strip().lower()),
            )
        return self.connection_metadata(source_id, owner_id=owner)

    def connection_metadata(
        self,
        source_id: str,
        *,
        owner_id: str | None = None,
    ) -> Dict[str, Any] | None:
        owner = str(owner_id or self.owner_id).strip() or self.owner_id
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM data_source_connections WHERE owner_id = ? AND source_id = ?",
                (owner, str(source_id or "").strip().lower()),
            ).fetchone()
        return self._public_row(dict(row)) if row else None

    def list_connections(self, *, owner_id: str | None = None) -> list[Dict[str, Any]]:
        owner = str(owner_id or self.owner_id).strip() or self.owner_id
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM data_source_connections WHERE owner_id = ? ORDER BY source_id",
                (owner,),
            ).fetchall()
        return [self._public_row(dict(row)) for row in rows]

    def resolve(self, connection_id: str) -> Dict[str, Any] | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM data_source_connections WHERE connection_id = ?",
                (str(connection_id or "").strip(),),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            secret = self._cipher.decrypt(bytes(record["encrypted_secret"])).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError):
            return None
        return {
            "connection_id": str(record["connection_id"]),
            "owner_id": str(record["owner_id"]),
            "source_id": str(record["source_id"]),
            "api_key": secret,
            "preferred_access_mode": str(record["preferred_access_mode"] or ACCESS_MODE_PULSE),
        }

    def resolve_source(
        self,
        source_id: str,
        *,
        owner_id: str | None = None,
    ) -> Dict[str, Any] | None:
        metadata = self.connection_metadata(source_id, owner_id=owner_id)
        return self.resolve(metadata["connection_id"]) if metadata else None

    def delete(self, source_id: str, *, owner_id: str | None = None) -> bool:
        owner = str(owner_id or self.owner_id).strip() or self.owner_id
        with self._guard, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM data_source_connections WHERE owner_id = ? AND source_id = ?",
                (owner, str(source_id or "").strip().lower()),
            )
        return bool(cursor.rowcount)


def credential_store_from_config(
    config: Mapping[str, Any] | None,
    config_path: Path | None,
    *,
    create_default: bool = False,
) -> DataSourceCredentialStore | None:
    """Build a credential store from an agent config without exposing its key."""
    raw = config.get("credential_store") if isinstance(config, Mapping) else None
    if not isinstance(raw, Mapping):
        return DataSourceCredentialStore() if create_default else None
    if raw.get("enabled") is False:
        return DataSourceCredentialStore() if create_default else None
    database_path = _resolve_config_path(raw.get("sqlite_path"), config_path)
    key_path = _resolve_config_path(raw.get("key_path"), config_path)
    if database_path is None:
        if not create_default:
            return None
        database: str | Path = ":memory:"
    else:
        database = database_path
    return DataSourceCredentialStore(
        database,
        key_path=key_path,
        owner_id=str(raw.get("owner_id") or "data-source-agent"),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parameters(endpoint: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item.get("name") or ""): item
        for item in endpoint.get("parameters") or []
        if isinstance(item, Mapping) and str(item.get("name") or "")
    }


def _execution_failure(endpoint: Mapping[str, Any], error: str, *, status: str = "failed") -> EndpointExecutionResult:
    return EndpointExecutionResult(
        status=status,
        endpoint_id=str(endpoint.get("endpoint_id") or ""),
        data_schema=_mapping(endpoint.get("response_schema")),
        cost=_mapping(endpoint.get("cost")),
        error=error,
    )


# Binary replies a provider may answer with, passed on as base64 with their type, and the largest one passed on: images
# (checked by their first bytes) and vector tiles (Mapbox Vector Tile protobuf, which has no signature).
IMAGE_MEDIA_TYPES = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF",
                     "application/vnd.mapbox-vector-tile": b"", "application/x-protobuf": b""}
MAX_IMAGE_BYTES = 1_000_000


def execute_financial_provider_endpoint(
    source_id: str,
    endpoint: Mapping[str, Any],
    parameters: Mapping[str, Any],
    api_key: str = "",
    *,
    http_client: Any = None,
) -> EndpointExecutionResult:
    """Execute an allowlisted financial-provider route without accepting raw secrets."""
    normalized_source = str(source_id or "").strip().lower()
    policy = PROVIDER_EXECUTION_POLICIES.get(normalized_source)
    operation = str(endpoint.get("operation") or "").strip()
    if not policy or not provider_endpoint_is_executable(normalized_source, operation):
        return _execution_failure(endpoint, "This catalog entry is documentation, not an executable provider API route.", status="not_executable")

    if policy.get("workflow_adapter"):
        return _execution_failure(endpoint, "This product requires its source-owned workflow adapter.")

    credential = str(api_key or "").strip()
    if bool(policy.get("required", True)) and not credential:
        return _execution_failure(endpoint, "A valid provider connection is required for live data access.", status="authentication_required")
    secret_names = {"token", "api_key", "apikey", "api-key", "api_token", "authorization", "secret", "password"}
    if any(str(name).lower() in secret_names for name in parameters):
        return _execution_failure(endpoint, "Raw API credentials are not accepted as endpoint parameters; use a source-owned connection.")

    specs = _parameters(endpoint)
    path_parameter_names = tuple(dict.fromkeys(re.findall(r"\{([A-Za-z][A-Za-z0-9_-]*)\}", operation)))
    missing = [
        name for name, spec in specs.items()
        if spec.get("required") and spec.get("default") is None
        and (parameters.get(name) is None or parameters.get(name) == "")
    ]
    missing.extend(
        name
        for name in path_parameter_names
        if name not in missing and (parameters.get(name) is None or parameters.get(name) == "")
    )
    if missing:
        return _execution_failure(endpoint, f"Missing required endpoint parameter(s): {', '.join(missing)}")

    resolved_path = operation
    query: Dict[str, Any] = dict(policy.get("defaults") or {})
    headers: Dict[str, str] = {"Accept": "application/json, application/vnd.sdmx.data+json;q=0.9, text/csv;q=0.8, application/xml;q=0.7"}
    headers.update({str(k): str(v) for k, v in (policy.get("headers") or {}).items()})
    body: Any = None
    for name, value in parameters.items():
        if value is None:
            continue
        spec = specs.get(name) or {}
        location = str(
            spec.get("location")
            or ("path" if name in path_parameter_names else "body" if name == "body" else "query")
        )
        if location == "path":
            marker = "{" + name + "}"
            if marker not in resolved_path:
                return _execution_failure(endpoint, f"Path parameter {name!r} is not present in the endpoint route.")
            resolved_path = resolved_path.replace(marker, quote(str(value), safe="+,:@"))
        elif location == "body":
            body = value
        elif location in {"query", "formData"}:
            query[name] = value
        elif location == "header":
            if name.lower() in secret_names or name.lower() in {"host", "content-length"}:
                return _execution_failure(endpoint, f"Request header {name!r} is controlled by the Data Source.")
            headers[name] = str(value)
        else:
            return _execution_failure(endpoint, f"Unsupported parameter location: {location}")
    if "{" in resolved_path or "}" in resolved_path:
        return _execution_failure(endpoint, "One or more endpoint path parameters are unresolved.")

    auth = policy.get("auth")
    if credential and isinstance(auth, tuple) and len(auth) == 3:
        location, name, prefix = auth
        if location == "header":
            headers[str(name)] = f"{prefix}{credential}"
        elif location == "query":
            query[str(name)] = f"{prefix}{credential}"
        elif location == "basic_username":
            encoded = base64.b64encode(f"{credential}:".encode("utf-8")).decode("ascii")
            headers[str(name)] = f"{prefix}{encoded}"
        elif location == "basic_pair":
            if ":" not in credential:
                return _execution_failure(endpoint, "The configured provider credential must contain a username and password pair.", status="authentication_required")
            encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
            headers[str(name)] = f"{prefix}{encoded}"
    method = str(_mapping(endpoint.get("extensions")).get("attas.http_method") or "GET").upper()
    if method not in {"GET", "POST"}:
        return _execution_failure(endpoint, f"Unsupported HTTP method: {method}")
    base_url = str(policy["base_url"]).rstrip("/")
    documented_base = str(_mapping(endpoint.get("extensions")).get("attas.base_url") or "").rstrip("/")
    if documented_base:
        parsed_base = urlparse(documented_base)
        allowed_hosts = {str(item).lower() for item in policy.get("allowed_base_hosts") or ()}
        if parsed_base.scheme == "https" and parsed_base.hostname and parsed_base.hostname.lower() in allowed_hosts:
            base_url = documented_base
    url = f"{base_url}{resolved_path}"
    options: Dict[str, Any] = {"params": query or None, "headers": headers}
    if body is not None:
        options["json"] = body

    client = http_client
    owns_client = client is None
    try:
        if client is None:
            import httpx
            client = httpx.Client(timeout=30, follow_redirects=False)
        response = client.request(method, url, **options)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 402, 403}:
            return _execution_failure(endpoint, "The provider rejected the credential, credits, or endpoint entitlement.", status="authentication_required")
        if status_code == 429:
            return _execution_failure(endpoint, "The provider rate limit was reached.", status="rate_limited")
        if normalized_source == 'finmind' and status_code == 400:
            try: message = str(response.json().get('msg', '')).lower()
            except (ValueError, AttributeError, TypeError): message = ''
            if 'update your user level' in message or 'sponsor' in message:
                return _execution_failure(endpoint, 'This FinMind dataset requires a Sponsor subscription on the selected connection.', status='authentication_required')
        if status_code < 200 or status_code >= 300:
            return _execution_failure(endpoint, f"Provider request failed with HTTP status {status_code}.")
        content_type = str(getattr(response, "headers", {}).get("content-type", "")).split(";", 1)[0].strip().lower()
        if content_type in IMAGE_MEDIA_TYPES:
            # A picture or a vector tile travels as base64 with its type; read as text it would be garbled.
            content = bytes(getattr(response, "content", b"") or b"")
            if len(content) > MAX_IMAGE_BYTES:
                return _execution_failure(endpoint, "The provider's file is larger than a Data Source passes on.")
            if not content.startswith(IMAGE_MEDIA_TYPES[content_type]):
                return _execution_failure(endpoint, "The provider's reply is not the image its media type says.")
            raw = {"content_base64": base64.b64encode(content).decode("ascii"), "content_type": content_type}
        else:
            try:
                raw = response.json()
            except Exception:
                text = str(getattr(response, "text", ""))
                raw = {"content": text, "content_type": content_type or "text/plain"}
        normalized = raw if isinstance(raw, Mapping) else {"items": raw}
        return EndpointExecutionResult(
            status="completed",
            endpoint_id=str(endpoint.get("endpoint_id") or ""),
            data=dict(normalized),
            data_schema=_mapping(endpoint.get("response_schema")),
            cost=_mapping(endpoint.get("cost")),
        )
    except Exception:
        return _execution_failure(endpoint, "The provider request could not be completed.")
    finally:
        if owns_client and client is not None:
            client.close()


def execute_direct_provider_endpoint(
    source_id: str,
    endpoint: Mapping[str, Any],
    parameters: Mapping[str, Any],
    api_key: str,
    *,
    http_client: Any = None,
) -> EndpointExecutionResult:
    """Call one allowlisted vendor endpoint with a request-scoped API key."""
    normalized_source = str(source_id or "").strip().lower()
    if normalized_source in PROVIDER_EXECUTION_POLICIES:
        return execute_financial_provider_endpoint(
            normalized_source,
            endpoint,
            parameters,
            api_key,
            http_client=http_client,
        )
    if normalized_source not in {"alpha_vantage", "finnhub"}:
        return _execution_failure(endpoint, "Direct vendor access is not enabled for this Data Source.", status="not_executable")
    if not str(api_key or "").strip():
        return _execution_failure(endpoint, "An API key is required for this request.", status="authentication_required")

    secret_names = {"token", "api_key", "apikey", "api-key", "secret", "password"}
    if any(str(name).lower() in secret_names for name in parameters):
        return _execution_failure(endpoint, "Raw API credentials are not accepted as endpoint parameters.")

    specs = _parameters(endpoint)
    public_specs = {name: value for name, value in specs.items() if name.lower() not in secret_names}
    unknown = sorted(set(parameters) - set(public_specs))
    if unknown:
        return _execution_failure(endpoint, f"Unsupported endpoint parameter(s): {', '.join(unknown)}")
    required = [
        name
        for name, spec in public_specs.items()
        if spec.get("required")
        and spec.get("default") is None
        and (parameters.get(name) is None or parameters.get(name) == "")
    ]
    if normalized_source == "alpha_vantage":
        required = [name for name in required if name.lower() != "function"]
    if required:
        return _execution_failure(endpoint, f"Missing required endpoint parameter(s): {', '.join(required)}")

    method = "GET"
    url = ""
    request_options: Dict[str, Any] = {"headers": {"Accept": "application/json"}}
    if normalized_source == "alpha_vantage":
        url = "https://www.alphavantage.co/query"
        query = {name: value for name, value in parameters.items() if value is not None}
        query["function"] = str(endpoint.get("operation") or query.get("function") or "")
        query["apikey"] = str(api_key)
        request_options["params"] = query
    else:
        extensions = _mapping(endpoint.get("extensions"))
        method = str(extensions.get("finnhub.http_method") or "").upper()
        path = str(extensions.get("finnhub.path") or "")
        base_url = str(extensions.get("finnhub.base_url") or "")
        if method not in {"GET", "POST"} or not path.startswith("/") or base_url != "https://finnhub.io/api/v1":
            return _execution_failure(endpoint, "The endpoint has an unsupported Finnhub execution route.")
        query: Dict[str, Any] = {}
        body: Dict[str, Any] = {}
        resolved_path = path
        for name, value in parameters.items():
            if value is None:
                continue
            location = str(public_specs[name].get("location") or "query")
            if location == "path":
                marker = "{" + name + "}"
                if marker not in resolved_path:
                    return _execution_failure(endpoint, f"Path parameter {name!r} is not present in the endpoint route.")
                resolved_path = resolved_path.replace(marker, quote(str(value), safe=""))
            elif location == "body":
                if isinstance(value, Mapping):
                    body.update(value)
                else:
                    body[name] = value
            elif location in {"query", "formData"}:
                query[name] = value
            else:
                return _execution_failure(endpoint, f"Unsupported parameter location: {location}")
        if "{" in resolved_path or "}" in resolved_path:
            return _execution_failure(endpoint, "One or more endpoint path parameters are unresolved.")
        url = f"{base_url}{resolved_path}"
        request_options["params"] = query or None
        request_options["headers"]["X-Finnhub-Token"] = str(api_key)
        if body:
            request_options["json"] = body

    client = http_client
    owns_client = client is None
    try:
        if client is None:
            import httpx

            client = httpx.Client(timeout=30, follow_redirects=False)
        response = client.request(method, url, **request_options)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            provider = "Finnhub" if normalized_source == "finnhub" else "Alpha Vantage"
            return _execution_failure(
                endpoint,
                f"{provider} rejected the connection credential or endpoint entitlement.",
                status="authentication_required",
            )
        if status_code < 200 or status_code >= 300:
            provider = "Finnhub" if normalized_source == "finnhub" else "Alpha Vantage"
            return _execution_failure(endpoint, f"{provider} request failed with HTTP status {status_code}.")
        try:
            raw = response.json()
        except Exception:
            provider = "Finnhub" if normalized_source == "finnhub" else "Alpha Vantage"
            return _execution_failure(endpoint, f"{provider} returned a non-JSON response.")
        if normalized_source == "finnhub" and isinstance(raw, Mapping) and raw.get("error"):
            return _execution_failure(endpoint, "Finnhub rejected the request parameters or endpoint entitlement.")
        if normalized_source == "alpha_vantage" and isinstance(raw, Mapping):
            provider_message = str(raw.get("Error Message") or raw.get("Information") or raw.get("Note") or "").strip()
            if provider_message:
                lowered = provider_message.lower()
                status = "authentication_required" if "api key" in lowered and ("invalid" in lowered or "claim" in lowered) else "failed"
                return _execution_failure(
                    endpoint,
                    "Alpha Vantage rejected the credential, request parameters, rate limit, or endpoint entitlement.",
                    status=status,
                )
        normalized = json.loads(json.dumps(raw, default=str))
        data = normalized if isinstance(normalized, Mapping) else {"items": normalized}
        return EndpointExecutionResult(
            status="completed",
            endpoint_id=str(endpoint.get("endpoint_id") or ""),
            data=dict(data),
            data_schema=_mapping(endpoint.get("response_schema")),
            cost=_mapping(endpoint.get("cost")),
        )
    except Exception:
        provider = "Finnhub" if normalized_source == "finnhub" else "Alpha Vantage"
        return _execution_failure(endpoint, f"{provider} request could not be completed.")
    finally:
        if owns_client and client is not None:
            client.close()


__all__ = [
    "ACCESS_MODE_DIRECT",
    "ACCESS_MODE_PULSE",
    "ACCESS_MODES",
    "DIRECT_BROWSER_BYOK_SOURCE_IDS",
    "OPTIONAL_BROWSER_KEY_SOURCE_IDS",
    "DataSourceCredentialStore",
    "PROVIDER_CONNECTION_PROFILES",
    "credential_store_from_config",
    "execute_direct_provider_endpoint",
    "provider_connection_profile",
]
