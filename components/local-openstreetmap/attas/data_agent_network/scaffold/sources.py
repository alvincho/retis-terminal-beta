"""Runnable Data Source hierarchy for vendor specification and data access."""

from __future__ import annotations

from attas.data_agent_network.timezones import timezone_fetch

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import date, datetime, timezone
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Mapping, Tuple
from urllib.parse import quote

from phemacast.agents.pulser import ConfigInput, _read_config
from phemacast.pulsers.data_agent_pulser import DataAgentPulser

from ..contracts import (
    DATA_AVAILABILITY_PULSE,
    DATA_FETCH_PULSE,
    DATA_SOURCE_PARTY,
    DATA_SOURCE_PIT_TYPE,
    DATA_SPEC_PULSE,
)
from .fields import (
    ATTAS_DATA_FETCH_PULSE_NAME,
    attach_result_sets,
    build_data_fetch_contract,
    enrich_dataset_fields,
    fields_from_schema,
)
from .connections import (
    DataSourceCredentialStore,
    PROVIDER_EXECUTION_POLICIES,
    credential_store_from_config,
    execute_direct_provider_endpoint,
    execute_financial_provider_endpoint,
    provider_endpoint_is_executable,
)
from .bloomberg import (
    bloomberg_client_from_config,
    bloomberg_endpoint_is_executable,
    execute_bloomberg_endpoint,
)
from .lseg_products import DataScopeSelectSpecParser, WorldCheckOneSpecParser, ProductClient, ProductError
from .workspace_spec import WorkspaceSpecParser
from .enterprise_products import EnterpriseClient, EnterpriseSpecParser
from .financial import enrich_financial_dataset, rank_financial_datasets
from .ingestion import VendorSpecIngestionService
from .models import EndpointExecutionResult, EndpointSpec, EndpointSpecSnapshot, VendorSpecSource
from .parsers import (
    AlphaVantageSpecParser,
    AIsaSpecParser,
    BISSpecParser,
    BloombergSpecParser,
    CoinGeckoSpecParser,
    CoinMarketCapSpecParser,
    CEICSpecParser,
    CopernicusDataSpaceSpecParser,
    CrunchbaseSpecParser,
    DealroomSpecParser,
    ECBSpecParser,
    EODHDSpecParser,
    FinancialModelingPrepSpecParser,
    FactSetSpecParser,
    FinnhubSpecParser,
    FREDSpecParser,
    IMFSpecParser,
    IntrinioSpecParser,
    LSEGSpecParser,
    Sustainable1SpecParser,
    GoogleEarthEngineSpecParser,
    MassiveSpecParser,
    MorningstarSpecParser,
    MoodysAnalyticsSpecParser,
    MeteomaticsSpecParser,
    NASACMRSpecParser,
    NOAAWeatherSpecParser,
    NasdaqDataLinkSpecParser,
    OECDSpecParser,
    OpenFIGISpecParser,
    OpenWeatherSpecParser,
    PlanetSpecParser,
    QuartrSpecParser,
    SECEdgarSpecParser,
    SentinelHubSpecParser,
    SimilarwebSpecParser,
    TiingoSpecParser,
    TreasuryFiscalDataSpecParser,
    TwelveDataSpecParser,
    TomorrowIOSpecParser,
    UP42SpecParser,
    VisibleSpecParser,
    WeatherbitSpecParser,
    WorldBankSpecParser,
    YFinanceSpecParser,
)
from .provider_catalog import PROVIDER_DEFINITIONS, provider_spec_sources
from .readers import DocumentSpecReader, GitHubRepositorySpecReader, WebsiteSpecReader
from .repository import AbstractEndpointSpecRepository, JsonEndpointSpecRepository
from .shioaji import (
    SHIOAJI_GATEWAY_ENV,
    SHIOAJI_RATE_LIMITS,
    SHIOAJI_SOURCE_DEFINITION,
    SHIOAJI_SOURCE_ID,
    ShioajiRateLimiter,
    ShioajiSpecParser,
    execute_shioaji_endpoint,
    normalize_shioaji_gateway_url,
    shioaji_endpoint_is_executable,
    shioaji_spec_sources,
)
from .taiwan import (
    FUGLE_STREAM_CAPABILITY,
    FinMindSpecParser,
    FugleSpecParser,
    TAIFEXSpecParser,
    TAIWAN_SOURCE_DEFINITIONS,
    TPExSpecParser,
    TWSESpecParser,
    TaiwanCentralBankSpecParser,
    execute_fugle_websocket_endpoint,
    execute_taiwan_http_endpoint,
    taiwan_spec_sources,
)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "reset_index") and hasattr(value, "to_dict"):
        try:
            return [_json_safe(item) for item in value.reset_index().to_dict(orient="records")]
        except (TypeError, ValueError):
            pass
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist"):
        try:
            return _json_safe(value.tolist())
        except (TypeError, ValueError):
            pass
    return str(value)

DEFINITIONS_ONLY_REASON = (
    "This source publishes definitions only. Request its data from your own Terminal "
    "with your own provider key; no key or provider request passes through this server."
)


class AbstractDataSource(DataAgentPulser, ABC):
    """Pulser-derived boundary for vendor endpoint specs and direct data access."""

    NETWORK_PULSES = ("data_spec", "data_availability", "data_fetch", "schema_query")
    CATALOG_FINGERPRINT_VERSION = "attas-data-source-catalog-v3-parameter-defaults"

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        repository: AbstractEndpointSpecRepository | None = None,
        ingestion: VendorSpecIngestionService | None = None,
        spec_snapshot: EndpointSpecSnapshot | None = None,
        credential_store: DataSourceCredentialStore | None = None,
        auto_refresh: bool | None = None,
        **kwargs: Any,
    ):
        config_data = _read_config(config) if config is not None else {}
        self._config_data = dict(config_data) if isinstance(config_data, Mapping) else {}
        # A definitions-only source publishes its catalog, schemas and
        # availability but never executes provider requests or holds keys;
        # users call the provider directly from their own Terminal.
        self._definitions_only = (
            str(self._config_data.get("data_execution") or "").strip().lower() == "definitions_only"
        )
        self._credential_store = None if self._definitions_only else credential_store
        self._data_access_guard = threading.RLock()
        self._data_access_verification: Dict[str, Any] = {}
        try:
            self._data_access_verification_ttl_sec = max(
                30,
                int(self._config_data.get("data_access_verification_ttl_sec") or 900),
            )
        except (TypeError, ValueError):
            self._data_access_verification_ttl_sec = 900
        auto_register = bool(kwargs.pop("auto_register", True))
        configured_root = self._config_data.get("spec_catalog_root") or self._config_data.get("endpoint_catalog_root")
        catalog_root = Path(str(configured_root or Path(__file__).resolve().parents[1] / "catalogs"))
        self.spec_repository = repository or JsonEndpointSpecRepository(catalog_root)
        self.spec_ingestion = ingestion or VendorSpecIngestionService(
            readers=(WebsiteSpecReader(), GitHubRepositorySpecReader(), DocumentSpecReader()),
            parsers=(
                YFinanceSpecParser(),
                AIsaSpecParser(),
                AlphaVantageSpecParser(),
                FinnhubSpecParser(),
                TwelveDataSpecParser(),
                MassiveSpecParser(),
                FinancialModelingPrepSpecParser(),
                EODHDSpecParser(),
                TiingoSpecParser(),
                NasdaqDataLinkSpecParser(),
                SECEdgarSpecParser(),
                FREDSpecParser(),
                TreasuryFiscalDataSpecParser(),
                WorldBankSpecParser(),
                IMFSpecParser(),
                ECBSpecParser(),
                BISSpecParser(),
                OECDSpecParser(),
                CoinGeckoSpecParser(),
                CoinMarketCapSpecParser(),
                CEICSpecParser(),
                FactSetSpecParser(),
                SimilarwebSpecParser(),
                TomorrowIOSpecParser(),
                WeatherbitSpecParser(),
                MeteomaticsSpecParser(),
                PlanetSpecParser(),
                SentinelHubSpecParser(),
                UP42SpecParser(),
                NOAAWeatherSpecParser(),
                NASACMRSpecParser(),
                CopernicusDataSpaceSpecParser(),
                GoogleEarthEngineSpecParser(),
                OpenWeatherSpecParser(),
                OpenFIGISpecParser(),
                IntrinioSpecParser(),
                LSEGSpecParser(),
                WorkspaceSpecParser(),
                DataScopeSelectSpecParser(),
                WorldCheckOneSpecParser(),
                EnterpriseSpecParser(),
                Sustainable1SpecParser(),
                BloombergSpecParser(),
                MorningstarSpecParser(),
                CrunchbaseSpecParser(),
                MoodysAnalyticsSpecParser(),
                DealroomSpecParser(),
                QuartrSpecParser(),
                VisibleSpecParser(),
                TWSESpecParser(),
                TPExSpecParser(),
                TAIFEXSpecParser(),
                FinMindSpecParser(),
                TaiwanCentralBankSpecParser(),
                FugleSpecParser(),
                ShioajiSpecParser(),
            ),
            repository=self.spec_repository,
        )
        profile = dict(self.source_profile())
        self.vendor_id = str(profile.get("source_id") or profile.get("vendor_id") or "data-source")
        from ..symbology_config import configured
        self._symbology = configured(self.vendor_id, {**profile, **dict(self._config_data.get("source_profile") or {})})
        if spec_snapshot is not None:
            self.spec_repository.save(spec_snapshot)
        should_refresh = (
            bool(auto_refresh)
            if auto_refresh is not None
            else bool(self._config_data.get("refresh_specs_on_start", False))
        )
        try:
            self.spec_snapshot = self.spec_repository.load(self.vendor_id)
        except (FileNotFoundError, ValueError):
            self.spec_snapshot = None
        if should_refresh:
            self.spec_snapshot = self.refresh_spec_catalog(self.spec_ingestion)
        try:
            refresh_hours = float(self._config_data.get("refresh_specs_every_hours") or 0)
        except (TypeError, ValueError):
            refresh_hours = 0.0
        if refresh_hours > 0:
            self._start_spec_refresh(refresh_hours)

        catalog = self._catalog_entries(self.spec_snapshot)
        supported_pulses = [
            copy.deepcopy(DATA_SPEC_PULSE),
            copy.deepcopy(DATA_AVAILABILITY_PULSE),
        ]
        if not self._definitions_only:
            data_fetch = copy.deepcopy(DATA_FETCH_PULSE)
            # The source operator prices its own fetches; a site may publish them free.
            price = self._config_data.get("data_fetch_cost_points")
            if price is not None:
                if type(price) is not int or price < 0:
                    raise ValueError("data_fetch_cost_points must be a non-negative integer.")
                data_fetch["cost_points"] = data_fetch["cost"] = price
            supported_pulses.append(data_fetch)
            from .historical_prices import installed as installed_history, pulse as history_pulse
            if installed_history(self):
                supported_pulses.append(history_pulse(self))
        from prompits.core.pulse_models import SCHEMA_QUERY_PULSE
        supported_pulses.append(copy.deepcopy(SCHEMA_QUERY_PULSE))
        for key in ("host", "port", "plaza_url"):
            if key not in kwargs and self._config_data.get(key) is not None:
                kwargs[key] = self._config_data[key]
        name = str(kwargs.pop("name", "") or self._config_data.get("name") or self.__class__.__name__)
        super().__init__(
            config=config,
            name=name,
            supported_pulses=supported_pulses,
            data_catalog=catalog,
            auto_register=False,
            **kwargs,
        )
        if self._credential_store is None and not self._definitions_only:
            self._credential_store = credential_store_from_config(
                self._config_data,
                getattr(self, "config_path", None),
            )
        if not self._definitions_only:
            from attas.data_agent_network.lan_endpoint import install_lan_endpoint
            install_lan_endpoint(self)
            from attas.data_agent_network.local_resources import source_local
            source_local(self)
        self._apply_source_card()
        from attas.data_agent_network.canonical.routes import install_model_routes
        install_model_routes(self)
        if not self._definitions_only:
            from attas.data_agent_network.provider_runtime.deployment import install_provider_runtime
            install_provider_runtime(self)
        from attas.source_lifecycle.source import install_source_lifecycle
        install_source_lifecycle(self)
        if self.plaza_url and auto_register:
            self.register()

    @abstractmethod
    def source_profile(self) -> Mapping[str, Any]:
        """Return normalized source identity, connectivity, cost, and quality metadata."""

    @abstractmethod
    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        """Return approved documentation sources used to refresh the endpoint catalog."""

    def _normalized_data_access(
        self,
        value: Mapping[str, Any] | None = None,
        *,
        fetch_ready: bool = False,
        attempt_ready: bool | None = None,
        credential_required: bool = False,
        credential_configured: bool = False,
        verification: str = "",
        verified_at: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        configured = dict(value or {})
        required = bool(configured.get("credential_required", credential_required))
        credential = bool(configured.get("credential_configured", credential_configured))
        configured_verification = str(configured.get("verification") or verification or "").strip().lower()
        allowed_verifications = {"public", "unverified", "verified", "failed", "preflight", "unknown"}
        if configured_verification not in allowed_verifications:
            configured_verification = "unknown"
        ready = bool(configured.get("fetch_ready", fetch_ready))
        configured_attempt = configured.get("attempt_ready")
        if configured_attempt is None:
            configured_attempt = attempt_ready if attempt_ready is not None else ready or credential
        can_attempt = bool(configured_attempt)
        if not required and (ready or can_attempt):
            configured_verification = "public" if configured_verification in {"", "unknown", "preflight"} else configured_verification
            ready = configured_verification in {"public", "verified"}
            can_attempt = True
        elif required:
            # A configured credential is only a precondition. Credentialed
            # access is not advertised as usable until a live fetch succeeds.
            can_attempt = bool(can_attempt or credential)
            ready = bool(ready and configured_verification == "verified")
            if configured_verification in {"", "unknown", "preflight"}:
                configured_verification = "unverified" if can_attempt else "unknown"
        if ready:
            can_attempt = True
        status = str(configured.get("status") or "").strip().lower()
        if status not in {"ready", "authentication_required", "unavailable", "unknown"}:
            status = "ready" if ready else "authentication_required" if required else "unavailable"
        if ready:
            status = "ready"
        elif can_attempt and configured_verification == "unverified":
            status = "unknown"
        elif status == "ready":
            status = "unknown" if can_attempt else "authentication_required" if required else "unavailable"
        normalized = {
            "status": status,
            "fetch_ready": ready,
            "attempt_ready": can_attempt,
            "access_mode": "data_source_pulse",
            "credential_required": required,
            "credential_configured": credential,
            "verification": configured_verification or ("unverified" if can_attempt else "unknown"),
            "verified_at": str(configured.get("verified_at") or verified_at or ""),
            "reason": str(configured.get("reason") or reason),
        }

        with self._data_access_guard:
            runtime = copy.deepcopy(self._data_access_verification)
        if not runtime or not can_attempt:
            return normalized
        runtime_verification = str(runtime.get("verification") or "")
        runtime_verified_at = str(runtime.get("verified_at") or "")
        if runtime_verification == "verified" and runtime_verified_at:
            try:
                verified_time = datetime.fromisoformat(runtime_verified_at.replace("Z", "+00:00"))
                if verified_time.tzinfo is None:
                    verified_time = verified_time.replace(tzinfo=timezone.utc)
                age_sec = (datetime.now(timezone.utc) - verified_time.astimezone(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                age_sec = self._data_access_verification_ttl_sec + 1
            if age_sec <= self._data_access_verification_ttl_sec:
                normalized.update(
                    {
                        "status": "ready",
                        "fetch_ready": True,
                        "verification": "verified",
                        "verified_at": runtime_verified_at,
                        "reason": str(runtime.get("reason") or "A live provider fetch succeeded recently."),
                    }
                )
            else:
                normalized.update(
                    {
                        "status": "unknown",
                        "fetch_ready": False,
                        "verification": "unverified",
                        "verified_at": runtime_verified_at,
                        "reason": "The last successful fetch is stale; run a live fetch to verify current access.",
                    }
                )
        elif runtime_verification == "failed":
            normalized.update(
                {
                    "status": str(runtime.get("status") or "unavailable"),
                    "fetch_ready": False,
                    "verification": "failed",
                    "verified_at": runtime_verified_at,
                    "reason": str(runtime.get("reason") or "The latest live provider access check failed."),
                }
            )
        return normalized

    def _record_data_access_result(self, result: EndpointExecutionResult) -> None:
        """Record only outcomes that prove or disprove the current access path."""
        status = str(result.status or "").strip().lower()
        error = str(result.error or "").strip()
        now = datetime.now(timezone.utc).isoformat()
        update: Dict[str, Any] = {}
        if status == "completed":
            update = {
                "verification": "verified",
                "status": "ready",
                "verified_at": now,
                "reason": "A live provider fetch succeeded recently.",
            }
        elif status == "authentication_required":
            update = {
                "verification": "failed",
                "status": "authentication_required",
                "verified_at": now,
                "reason": error or "The latest provider authentication or entitlement check failed.",
            }
        elif status == "rate_limited":
            update = {
                "verification": "failed",
                "status": "unavailable",
                "verified_at": now,
                "reason": error or "The provider is currently rate limited.",
            }
        elif status == "failed" and any(
            marker in error.lower()
            for marker in (
                "request could not be completed",
                "provider request failed with http status",
                "timed out",
                "connection refused",
                "network is unreachable",
                "resource deadlock avoided",
            )
        ):
            update = {
                "verification": "failed",
                "status": "unavailable",
                "verified_at": now,
                "reason": error or "The latest provider request could not be completed.",
            }
        if not update:
            return
        with self._data_access_guard:
            self._data_access_verification = update
        if hasattr(self, "agent_card"):
            self._apply_source_card()

    def _configured_data_access(self) -> Dict[str, Any] | None:
        if getattr(self, "_definitions_only", False):
            return {
                "status": "unavailable",
                "fetch_ready": False,
                "attempt_ready": False,
                "verification": "unknown",
                "reason": DEFINITIONS_ONLY_REASON,
            }
        profile = self.source_profile()
        connectivity = profile.get("connectivity") if isinstance(profile.get("connectivity"), Mapping) else {}
        configured = connectivity.get("data_access") if isinstance(connectivity, Mapping) else None
        return dict(configured) if isinstance(configured, Mapping) else None

    def _source_owned_credential_configured(self, source_id: str, env_name: str = "") -> bool:
        normalized_source = str(source_id or "").strip().lower()
        if self._credential_store is not None and normalized_source:
            try:
                if self._credential_store.connection_metadata(normalized_source):
                    return True
            except Exception:
                pass
        return bool(env_name and len(str(os.getenv(env_name) or "").strip()) >= 4)

    def _resolve_source_owned_credential(
        self,
        source_id: str,
        auth_context: Mapping[str, Any],
        *,
        env_name: str = "",
        connection_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> str:
        normalized_source = str(source_id or "").strip().lower()
        connection: Mapping[str, Any] | None = None
        connection_id = str(auth_context.get("connection_id") or "").strip()
        if connection_id and connection_resolver is not None:
            try:
                candidate = connection_resolver(connection_id)
                connection = candidate if isinstance(candidate, Mapping) else None
            except Exception:
                connection = None
        if connection is None and self._credential_store is not None and normalized_source:
            try:
                candidate = self._credential_store.resolve_source(normalized_source)
                connection = candidate if isinstance(candidate, Mapping) else None
            except Exception:
                connection = None
        if connection is not None:
            connection_source = str(connection.get("source_id") or normalized_source).strip().lower()
            if connection_source == normalized_source:
                credential = str(connection.get("api_key") or connection.get("token") or "").strip()
                if credential:
                    return credential
        return str(os.getenv(env_name) or "").strip() if env_name else ""

    def data_access_status(self) -> Dict[str, Any]:
        """Describe whether this source can accept a data_fetch pulse right now."""
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        return self._normalized_data_access(
            fetch_ready=False,
            attempt_ready=False,
            reason="No executable Data Source pulse runtime is configured.",
        )

    def _profile_with_data_access(self) -> Dict[str, Any]:
        profile = copy.deepcopy(dict(self.source_profile()))
        from ..symbology_config import advertised
        profile["symbology"] = advertised(self._symbology)
        connectivity = copy.deepcopy(dict(profile.get("connectivity") or {}))
        access = self.data_access_status()
        connectivity["data_access"] = access
        if access["attempt_ready"]:
            connectivity["configuration_required"] = []
        profile["connectivity"] = connectivity
        from attas.data_agent_network.canonical.service import installed_support
        from attas.data_agent_network.canonical.installed import advertisements
        profile["modelSupport"] = installed_support(self.vendor_id) + advertisements(self)
        profile["modelProtocol"] = "canonical-result/1"
        from attas.data_agent_network.canonical.native import namespace
        profile['nativeModelNamespace'] = namespace(self.vendor_id)
        from .history import endpoint_history_inputs
        from .historical_prices import advertisements as historical_advertisements
        from attas.data_agent_network.canonical.history_bindings import advertisements as canonical_history
        profile['historical_inputs'] = canonical_history(profile) + historical_advertisements(self) + endpoint_history_inputs(self)
        from ..timezones import TIMEZONE_SUPPORT
        profile['timezoneSupport'] = copy.deepcopy(TIMEZONE_SUPPORT)
        from .direct_access import summary
        endpoints = self.spec_snapshot.endpoints if self.spec_snapshot else ()
        profile['directAccess'] = summary((e.transport, self._published_authentication(e), {**dict(e.extensions), **self._direct_extensions(e)})
                                          for e in endpoints)
        # Only a source whose operator owns the data it serves may use the operator's own key.
        profile['operatorOwnedData'] = self._operator_owned_data()
        return profile

    def _operator_owned_data(self) -> bool:
        return self._config_data.get("operator_owned_data") is True

    def _apply_source_card(self) -> None:
        card = dict(self.agent_card or {})
        profile = self._profile_with_data_access()
        card["name"] = self.name
        card["role"] = "pulser"
        card["party"] = DATA_SOURCE_PARTY
        card["pit_type"] = DATA_SOURCE_PIT_TYPE
        card["description"] = str(profile.get("description") or "Data Source agent.")
        card["tags"] = self._merge_unique(card.get("tags"), ["attas", "data-agent", "data-source", self.vendor_id])
        meta = dict(card.get("meta") or {})
        meta["party"] = DATA_SOURCE_PARTY
        meta["pit_type"] = DATA_SOURCE_PIT_TYPE
        meta["data_source"] = profile
        if getattr(self, '_lan_descriptor', None):
            meta['network_endpoints'] = [dict(self._lan_descriptor)]
        meta["endpoint_count"] = len(self.spec_snapshot.endpoints) if self.spec_snapshot else 0
        meta["spec_snapshot"] = self._snapshot_metadata()
        card["meta"] = meta
        self.agent_card = card

    def register(self, **kwargs: Any):
        """Register as the Attas DataSource PIT and preserve that identity locally."""
        response = super().register(**kwargs)
        self._apply_source_card()
        return response

    def _snapshot_metadata(self) -> Dict[str, Any]:
        snapshot = self.spec_snapshot
        if snapshot is None:
            return {"status": "missing", "vendor_id": self.vendor_id, "endpoint_count": 0}
        return {
            "status": "ready",
            "vendor_id": snapshot.vendor_id,
            "retrieved_at": snapshot.retrieved_at,
            "content_hash": snapshot.content_hash,
            # Changes when the snapshot or its hand-kept notes change, so a reader of this spec knows to re-read it.
            "spec_hash": hashlib.sha256(
                (snapshot.content_hash + json.dumps(self._vendor_notes(), sort_keys=True)).encode("utf-8")
            ).hexdigest(),
            "parser_version": snapshot.parser_version,
            "endpoint_count": len(snapshot.endpoints),
        }

    def _public_source(self) -> Dict[str, Any]:
        profile = self._profile_with_data_access()
        connectivity = copy.deepcopy(dict(profile.get("connectivity") or {}))
        connectivity.setdefault("status", "ready" if self.spec_snapshot else "requires_configuration")
        connectivity.setdefault("transport", "local_pulser")
        connectivity.setdefault("address", str((self.agent_card or {}).get("address") or ""))
        connectivity.setdefault("practice_id", "get_pulse_data")
        connectivity.setdefault("pulse_name", "data_availability")
        connectivity.setdefault("authentication", "source-dependent")
        connectivity.setdefault("configuration_required", [])
        connectivity.setdefault("notes", [])
        return {
            "source_id": self.vendor_id,
            "source_name": str(profile.get("source_name") or self.name),
            "provider": str(profile.get("provider") or self.vendor_id),
            "description": str(profile.get("description") or ""),
            "connectivity": connectivity,
            "symbology": profile["symbology"],
        }

    def _catalog_fingerprint(self) -> str:
        """Return a stable digest without serializing the complete endpoint catalog."""
        snapshot = self._snapshot_metadata()
        public_source = self._public_source()
        connectivity = public_source.get("connectivity")
        if isinstance(connectivity, Mapping):
            connectivity = copy.deepcopy(dict(connectivity))
            data_access = connectivity.get("data_access")
            if isinstance(data_access, Mapping):
                # Verification time is operational metadata and must not force
                # a complete RAG rebuild when catalog content is unchanged.
                data_access = copy.deepcopy(dict(data_access))
                data_access.pop("verified_at", None)
                connectivity["data_access"] = data_access
            public_source["connectivity"] = connectivity
        fingerprint_payload = {
            "format_version": self.CATALOG_FINGERPRINT_VERSION,
            "vendor_id": self.vendor_id,
            "snapshot": {
                "content_hash": str(snapshot.get("content_hash") or ""),
                "parser_version": str(snapshot.get("parser_version") or ""),
                "endpoint_count": int(snapshot.get("endpoint_count") or len(self.data_catalog or ())),
            },
            "source": public_source,
        }
        encoded = json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _fields_from_schema(schema: Mapping[str, Any]) -> list[Dict[str, Any]]:
        return fields_from_schema(schema)

    @staticmethod
    def _catalog_sync_entry(dataset: Mapping[str, Any]) -> Dict[str, Any]:
        """Return lossless search metadata without repeated derived schemas."""
        derived_keys = {
            "attas_pulse",
            "canonical_data_schema",
            "field_mappings",
            "fields",
            "output_schema",
        }
        compact = {
            key: copy.deepcopy(value)
            for key, value in dataset.items()
            if key not in derived_keys
        }
        schema = dataset.get("data_schema") or dataset.get("output_schema") or {}
        compact["data_schema"] = copy.deepcopy(dict(schema)) if isinstance(schema, Mapping) else {}
        field_keys = (
            "name",
            "vendor_name",
            "path",
            "canonical_name",
            "definition",
            "provider_definition",
            "description",
            "type",
            "unit",
            "semantic_type",
            "required",
            "nullable",
            "inferred",
            "definition_source",
            "tags",
        )
        fields: list[Dict[str, Any]] = []
        for raw_field in dataset.get("fields") or ():
            if not isinstance(raw_field, Mapping):
                continue
            field = {
                key: copy.deepcopy(raw_field[key])
                for key in field_keys
                if key in raw_field
            }
            vendor_name = str(
                raw_field.get("vendor_name") or raw_field.get("name") or ""
            ).strip()
            field["vendor_field_names"] = [vendor_name] if vendor_name else []
            fields.append(field)
        endpoint_id = str(compact.get("dataset_id") or "")
        pulse = dataset.get("attas_pulse") if isinstance(dataset.get("attas_pulse"), Mapping) else {}
        compact.update(
            {
                # Keep the public pulse result contract valid while carrying a
                # lightweight field index for the consultant's private memory.
                "fields": [],
                "field_index": fields,
                "canonical_data_schema": {},
                "field_mappings": [],
                "attas_pulse": {
                    "contract_version": str(pulse.get("contract_version") or "1.0"),
                    "pulse_name": str(pulse.get("pulse_name") or ATTAS_DATA_FETCH_PULSE_NAME),
                    "pulse_address": str(pulse.get("pulse_address") or "plaza://pulse/attas/data_fetch"),
                    "endpoint_id": endpoint_id,
                    "request": {
                        "dataset_id": endpoint_id,
                        "field_selector": "fields",
                    },
                    "response": {
                        "provider_container": "data",
                        "canonical_container": "canonical_data",
                    },
                },
            }
        )
        compact["catalog_metadata_mode"] = "compact"
        return compact

    def is_endpoint_executable(self, endpoint: EndpointSpec) -> bool:
        """Return runtime execution capability independently of spec licensing."""
        return bool(endpoint.executable)

    @staticmethod
    def _endpoint_input_schema(endpoint: EndpointSpec) -> Dict[str, Any]:
        properties = {}
        for parameter in endpoint.parameters:
            schema = copy.deepcopy(dict(parameter.schema))
            if parameter.default is not None:
                schema.setdefault('default', copy.deepcopy(parameter.default))
            properties[parameter.name] = schema
        return {'type': 'object', 'properties': properties,
                'required': [parameter.name for parameter in endpoint.parameters if parameter.required]}

    def _catalog_entries(self, snapshot: EndpointSpecSnapshot | None) -> list[Dict[str, Any]]:
        if snapshot is None:
            return []
        entries: list[Dict[str, Any]] = []
        for endpoint in snapshot.endpoints:
            entries.append(
                enrich_dataset_fields(enrich_financial_dataset(
                    {
                    "dataset_id": endpoint.endpoint_id,
                    "name": endpoint.name,
                    "description": endpoint.description,
                    "source": endpoint.vendor_id,
                    "provider": self.source_profile().get("provider", endpoint.vendor_id),
                    "source_pulse_name": endpoint.pulse_name,
                    "pulse_name": ATTAS_DATA_FETCH_PULSE_NAME,
                    "fetch_pulse": ATTAS_DATA_FETCH_PULSE_NAME,
                    "tags": [endpoint.vendor_id, endpoint.category, endpoint.transport],
                    "input_schema": self._endpoint_input_schema(endpoint),
                    "output_schema": dict(endpoint.response_schema),
                    "data_schema": dict(endpoint.response_schema),
                    "fields": self._fields_from_schema(endpoint.response_schema),
                    "sample_data": {"illustrative": True, "operation": endpoint.operation},
                    "cost": dict(endpoint.cost),
                    "quality": dict(endpoint.quality),
                    "coverage": {"category": endpoint.category},
                    "freshness": {"spec_retrieved_at": snapshot.retrieved_at},
                    "authentication": dict(endpoint.authentication),
                    "entitlement": dict(endpoint.authentication),
                    "lineage": {
                        "vendor_id": endpoint.vendor_id,
                        "endpoint_id": endpoint.endpoint_id,
                        "documentation_url": endpoint.documentation_url,
                    },
                    "caveats": list(endpoint.quality.get("notes") or []),
                    "executable": self.is_endpoint_executable(endpoint),
                    "operation": endpoint.operation,
                    "category": endpoint.category,
                    },
                    source_id=endpoint.vendor_id,
                ))
            )
        return entries

    def refresh_spec_once(self) -> bool:
        """Re-read the vendor's documentation now; a failed refresh keeps the current snapshot."""
        try:
            self.refresh_spec_catalog(self.spec_ingestion)
            return True
        except Exception as exc:  # the vendor's site may be down; the last good spec stays published
            self._spec_refresh_error = f"{type(exc).__name__}: {exc}"
            return False

    def _start_spec_refresh(self, hours: float) -> threading.Event:
        """Refresh the vendor spec every `hours` (at least 15 minutes) in the background (`refresh_specs_every_hours`).

        Each refresh writes a new snapshot to the catalog root and makes it active, so the root must be writable
        and outside a shared checkout.
        """
        stop = threading.Event()
        self._spec_refresh_stop = stop

        def loop() -> None:
            while not stop.wait(max(hours, 0.25) * 3600):
                self.refresh_spec_once()

        threading.Thread(target=loop, name=f"spec-refresh-{self.vendor_id}", daemon=True).start()
        return stop

    def refresh_spec_catalog(self, ingestion: VendorSpecIngestionService) -> EndpointSpecSnapshot:
        snapshots = ingestion.refresh_many(self.spec_sources())
        matching = [snapshot for snapshot in snapshots if snapshot.vendor_id == self.vendor_id]
        if not matching:
            raise ValueError(f"No refreshed snapshot returned for {self.vendor_id}")
        self.spec_snapshot = matching[0]
        if hasattr(self, "data_catalog"):
            self.data_catalog = self._catalog_entries(self.spec_snapshot)
        if hasattr(self, "agent_card"):
            self._apply_source_card()
        return self.spec_snapshot

    def resolve_endpoint(
        self,
        repository: AbstractEndpointSpecRepository | None = None,
        *,
        endpoint_id: str = "",
        query: str = "",
    ) -> EndpointSpec:
        selected_repository = repository or self.spec_repository
        matches = selected_repository.search(
            self.vendor_id,
            endpoint_id=endpoint_id,
            query=query,
            limit=2,
        )
        if not matches:
            raise LookupError(f"No endpoint specification matched {endpoint_id or query!r}")
        if not endpoint_id and len(matches) > 1:
            raise LookupError(f"Endpoint query is ambiguous: {query!r}")
        return matches[0]

    def _direct_extensions(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        """Extra published extensions a user's Terminal needs to call this vendor itself."""
        return {}

    def _published_authentication(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        """The endpoint's authentication as published; a source may add where its vendor expects the key."""
        return dict(endpoint.authentication)

    def _vendor_notes(self) -> Dict[str, Any]:
        """The vendor's hand-kept endpoint notes (repository `notes`), re-read when the file changes."""
        reader = getattr(self.spec_repository, "notes", None)
        if not callable(reader):
            return {}
        vendor_root = getattr(self.spec_repository, "_vendor_root", None)
        path = Path(vendor_root(self.vendor_id)) / "notes.json" if callable(vendor_root) else None
        stamp = path.stat().st_mtime_ns if path is not None and path.is_file() else None
        cached = getattr(self, "_notes_cache", None)
        if cached is None or cached[0] != stamp:
            try:
                notes = {key: [dict(item) for item in items] for key, items in reader(self.vendor_id).items()}
            except (OSError, ValueError):
                notes = {}
            self._notes_cache = (stamp, notes)
        return self._notes_cache[1]

    def _endpoint_payload(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        payload = _json_safe(asdict(endpoint))
        payload["authentication"] = _json_safe(self._published_authentication(endpoint))
        direct = self._direct_extensions(endpoint)
        if direct:
            payload["extensions"] = {**dict(payload.get("extensions") or {}), **_json_safe(direct)}
        notes = self._vendor_notes().get(endpoint.endpoint_id)
        if notes:
            payload["extensions"] = {**dict(payload.get("extensions") or {}), "vendor_notes": _json_safe(notes)}
        payload["executable"] = self.is_endpoint_executable(endpoint)
        entry = next(
            (
                item
                for item in self.data_catalog or []
                if isinstance(item, Mapping) and item.get("dataset_id") == endpoint.endpoint_id
            ),
            None,
        )
        if entry is None:
            entry = enrich_dataset_fields(
                {
                    "dataset_id": endpoint.endpoint_id,
                    "name": endpoint.name,
                    "description": endpoint.description,
                    "pulse_name": ATTAS_DATA_FETCH_PULSE_NAME,
                    "fetch_pulse": ATTAS_DATA_FETCH_PULSE_NAME,
                    "input_schema": self._endpoint_input_schema(endpoint),
                    "output_schema": dict(endpoint.response_schema),
                    "data_schema": dict(endpoint.response_schema),
                    "fields": self._fields_from_schema(endpoint.response_schema),
                }
            )
        payload["source_pulse_name"] = str(payload.get("pulse_name") or "").strip()
        payload["pulse_name"] = ATTAS_DATA_FETCH_PULSE_NAME
        payload["canonical_response_schema"] = copy.deepcopy(
            dict(entry.get("canonical_data_schema") or {})
        )
        payload["field_mappings"] = copy.deepcopy(list(entry.get("field_mappings") or []))
        payload["attas_pulse"] = copy.deepcopy(dict(entry.get("attas_pulse") or {}))
        from attas.data_agent_network.canonical.service import endpoint_support
        from attas.data_agent_network.canonical.installed import advertisements
        payload["canonical_models"] = endpoint_support(self.vendor_id, endpoint) + advertisements(self, endpoint)
        return payload

    def data_spec(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        query = str(input_data.get("query") or "").strip()
        endpoint_id = str(input_data.get("endpoint_id") or "").strip()
        try:
            limit = max(1, min(int(input_data.get("limit") or 20), 500))
        except (TypeError, ValueError):
            limit = 20
        try:
            offset = max(0, int(input_data.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        warnings: list[str] = []
        try:
            snapshot = self.spec_repository.load(self.vendor_id)
            matches = snapshot.endpoints if not query and not endpoint_id else self.spec_repository.search(
                self.vendor_id, query=query, endpoint_id=endpoint_id, limit=500,
            )
            endpoints = matches[offset:offset + limit]
            total = len(matches)
            if (query or endpoint_id) and total == 500:
                warnings.append("Refine the search: search results are limited to 500 matches.")
            self.spec_snapshot = snapshot
        except (FileNotFoundError, ValueError) as exc:
            endpoints = ()
            snapshot = None
            total = 0
            warnings.append(str(exc))
        return {
            "query": query,
            "endpoint_id": endpoint_id,
            "source": self._public_source(),
            "endpoints": [self._endpoint_payload(endpoint) for endpoint in endpoints],
            "count": len(endpoints),
            "offset": offset,
            "total": total,
            "snapshot": self._snapshot_metadata() if snapshot else {"status": "missing", "vendor_id": self.vendor_id},
            "warnings": list(dict.fromkeys(warnings + (list(snapshot.warnings) if snapshot else []))),
        }

    def data_availability(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        query = str(input_data.get("query") or "").strip()
        preferences = (
            dict(input_data.get("preferences") or {})
            if isinstance(input_data.get("preferences"), Mapping)
            else {}
        )
        catalog_sync = not query and bool(preferences.get("catalog_sync"))
        try:
            limit = max(1, min(int(input_data.get("limit") or 10), 500))
        except (TypeError, ValueError):
            limit = 10
        snapshot = self.spec_snapshot
        warnings = list(snapshot.warnings) if snapshot else [f"No endpoint specification snapshot for {self.vendor_id}"]
        # The normalized catalog is built when the source starts and replaced
        # whenever its specification snapshot is refreshed.  Rebuilding it for
        # every availability request is especially expensive for large vendor
        # schemas (for example, Morningstar's hundreds of OpenAPI operations).
        catalog = list(self.data_catalog or ())
        catalog_fingerprint = self._catalog_fingerprint() if catalog_sync else ""
        known_fingerprints = (
            preferences.get("catalog_fingerprints")
            if isinstance(preferences.get("catalog_fingerprints"), Mapping)
            else {}
        )
        known_fingerprint = str(
            known_fingerprints.get(self.vendor_id)
            or preferences.get("catalog_fingerprint")
            or ""
        ).strip()
        if catalog_sync and known_fingerprint == catalog_fingerprint:
            public_source = self._public_source()
            connectivity = public_source["connectivity"]
            available = bool(catalog) and connectivity.get("status") not in {
                "unavailable",
                "requires_configuration",
            }
            return {
                "query": query,
                "source": public_source,
                "available": available,
                "datasets": [],
                "fields": [],
                "field_count": 0,
                "endpoint_count": len(catalog),
                "result_formats": ["endpoint"] if catalog else [],
                "result_sets": [],
                "count": 0,
                "match_reason": "Catalog fingerprint matches consultant memory; datasets were not retransmitted.",
                "catalog_fingerprint": catalog_fingerprint,
                "catalog_not_modified": True,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "warnings": warnings,
            }
        # An empty query is the consultant's catalog synchronization request;
        # preserve the complete vendor catalog instead of filtering by the
        # synchronization use-case label.
        datasets = catalog if catalog_sync else catalog[:limit] if not query else rank_financial_datasets(catalog, input_data, limit=limit)
        if catalog_sync:
            datasets = [self._catalog_sync_entry(dataset) for dataset in datasets]
        connectivity = self._public_source()["connectivity"]
        available = bool(datasets) and connectivity.get("status") not in {"unavailable", "requires_configuration"}
        executable_count = sum(1 for dataset in datasets if dataset.get("executable"))
        result = {
            "query": query,
            "source": self._public_source(),
            "source_id": self.vendor_id,
            "available": available,
            "datasets": datasets,
            "count": len(datasets),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "warnings": warnings,
        }
        if catalog_sync:
            # Memory synchronization consumes the canonical endpoint records
            # directly. Building field and endpoint result sets here would
            # duplicate the same large schemas several times on the wire.
            result.update(
                {
                    "catalog_fingerprint": catalog_fingerprint,
                    "catalog_not_modified": False,
                    "fields": [],
                    "field_count": 0,
                    "endpoint_count": len(datasets),
                    "result_formats": ["endpoint"] if datasets else [],
                    "result_sets": [],
                }
            )
        else:
            result = attach_result_sets(
                result,
                input_data,
                field_limit=max(50, limit * 12),
            )
        result.pop("source_id", None)
        result["match_reason"] = (
            f"Matched {result['field_count']} field(s) across {len(datasets)} documented endpoint access route(s); "
            f"{executable_count} route(s) currently executable."
        )
        return result

    def historical_prices(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        if getattr(self, "_definitions_only", False):
            return {"status": "not_executable", "error": DEFINITIONS_ONLY_REASON}
        from .historical_prices import execute
        # Verified practice invocation adds private caller metadata. It is not
        # part of the public historical request or its provider field mapping.
        request = dict(input_data)
        request.pop("_caller", None)
        return execute(self, request)

    CALL_PLAN_PULSE = "data_call_plan"  # the Data Consultant's pulse that plans a history request's vendor calls

    def _data_consultant_address(self) -> str:
        """The Plaza address of the agent that publishes data_call_plan, looked up at most every ten minutes."""
        cached = getattr(self, "_consultant_address_cache", None)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        address = ""
        for entry in self._search_plaza_directory() or []:
            card = entry.get("card") if isinstance(entry, Mapping) and isinstance(entry.get("card"), Mapping) else {}
            pulses = (card.get("meta") or {}).get("supported_pulses") if isinstance(card.get("meta"), Mapping) else None
            if any(isinstance(p, Mapping) and (p.get("pulse_name") or p.get("name")) == self.CALL_PLAN_PULSE
                   for p in pulses or []):
                address = str(card.get("address") or entry.get("address") or "")
                if address:
                    break
        self._consultant_address_cache = (time.monotonic() + (600 if address else 60), address)
        return address

    def consultant_plan(self, request: Mapping[str, Any], failed: Mapping[str, Any] | None = None) -> Dict[str, Any] | None:
        """The Data Consultant's plan for one canonical history request on this source; None when it cannot be reached.

        The source never plans a dated history request itself, so None fails that request with a clear message.
        After a generated plan failed here, `failed` reports it and asks for the reviewed recipe's plan.
        """
        address = self._data_consultant_address() if getattr(self, "agent_id", None) else ""
        if not address:
            return None
        params: Dict[str, Any] = {"agentId": self.agent_id, "model_request": dict(request)}
        if failed:
            params.update(failed=dict(failed), recipe="fallback")
        from prompits.core.call_grants import as_self
        try:
            # The source's own request for its plan (a free pulse), not one made on its caller's behalf: the
            # caller's delegation never names the Consultant, so a call under it would be refused.
            with as_self():
                reply = self.UsePractice("get_pulse_data", {"pulse_name": self.CALL_PLAN_PULSE, "params": params},
                                         pit_address=address, timeout=120)
        except Exception:
            return None  # the Consultant is not answering
        reply = reply.get("result", reply) if isinstance(reply, Mapping) else None
        return dict(reply) if isinstance(reply, Mapping) and (reply.get("calls") or reply.get("error")) else None

    @timezone_fetch
    def data_fetch(self, input_data: Mapping[str, Any]) -> Dict[str, Any]:
        if getattr(self, "_definitions_only", False):
            return {
                "status": "not_executable",
                "dataset_id": str(input_data.get("endpoint_id") or input_data.get("dataset_id") or ""),
                "error": DEFINITIONS_ONLY_REASON,
            }
        if input_data.get("target_model"):
            from attas.data_agent_network.canonical.service import execute_model
            from .fields import table_view
            context = input_data.get("auth_context") if isinstance(input_data.get("auth_context"), Mapping) else {}
            query = input_data.get("model_request") if isinstance(input_data.get("model_request"), Mapping) else {}
            try:
                answer = execute_model(self, input_data, context)
            except ValueError as exc:
                # A model request this source cannot answer, such as listings it does not cover, is an answer the caller
                # can act on, not a server error: TPEx answered a watchlist of 2330.TXO with a 500 (reported 2026-11, issue 74).
                return {"status": "failed", "source": self._public_source(), "backend_id": self.vendor_id,
                        "data": {"items": []}, "canonical_data": {"items": []}, "warnings": [], "error": str(exc)}
            return table_view(answer, query.get("table"))
        endpoint_id = str(input_data.get("endpoint_id") or input_data.get("dataset_id") or "").strip()
        parameters = input_data.get("parameters") if isinstance(input_data.get("parameters"), Mapping) else {}
        auth_context = input_data.get("auth_context") if isinstance(input_data.get("auth_context"), Mapping) else {}
        entry = next(
            (
                item
                for item in self.data_catalog or []
                if isinstance(item, Mapping) and item.get("dataset_id") == endpoint_id
            ),
            None,
        )
        try:
            endpoint = self.resolve_endpoint(endpoint_id=endpoint_id)
            result = self.execute_endpoint(endpoint, parameters, auth_context)
        except Exception as exc:
            result = EndpointExecutionResult(status="failed", endpoint_id=endpoint_id, error=str(exc))
        self._record_data_access_result(result)
        data = _json_safe(result.data)
        if not isinstance(data, Mapping):
            data = {"items": data}
        cost = _json_safe(result.cost) if result.cost else {
            "model": "unknown",
            "amount": 0,
            "currency": "USD",
            "unit": "request",
            "provider_cost": "Unknown because no endpoint specification was resolved.",
            "notes": [],
        }
        if input_data.get("canonical") is False:
            # The caller maps the vendor data itself: the data alone, not a renamed copy of it with its schemas.
            return {"status": result.status, "source": self._public_source(), "dataset_id": endpoint_id,
                    "backend_id": self.vendor_id, "data": dict(data), "cost": cost,
                    "warnings": list(result.warnings), "error": result.error}
        contract = build_data_fetch_contract(
            entry
            or {
                "dataset_id": endpoint_id,
                "pulse_name": ATTAS_DATA_FETCH_PULSE_NAME,
                "fetch_pulse": ATTAS_DATA_FETCH_PULSE_NAME,
                "data_schema": _json_safe(result.data_schema),
                "fields": fields_from_schema(_json_safe(result.data_schema)),
            },
            dict(data),
            requested_fields=input_data.get("fields") or (),
        )
        return {
            "status": result.status,
            "source": self._public_source(),
            "dataset_id": endpoint_id,
            "backend_id": self.vendor_id,
            "data": dict(data),
            "data_schema": _json_safe(result.data_schema),
            **contract,
            "cost": cost,
            "warnings": list(result.warnings),
            "error": result.error,
        }

    @abstractmethod
    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        """Execute one normalized endpoint or return a structured unsupported result."""

    def fetch_pulse_payload(
        self,
        pulse_name: str,
        input_data: Dict[str, Any],
        pulse_definition: Dict[str, Any],
    ) -> Dict[str, Any]:
        if pulse_name == "schema_query":
            from attas.data_agent_network.canonical.catalog import resources, PROFILES
            from attas.data_agent_network.canonical.service import mapping_resource
            from prompits.core.pulse_models import query_installed
            from attas.data_agent_network.canonical.installed import installations
            from attas.data_agent_network.canonical.native import installed_native, support
            from prompits.core.resource_registry import reference
            mappings = [mapping_resource(self.vendor_id, schema) for schema in PROFILES]
            installed = [r['resource'] for entry in installations(self) for r in entry['records']]
            native = installed_native(self,input_data.get('refs')) if input_data.get('include_support') or str(input_data.get('namespace','')).startswith('ai.attas.vendor.') or any(str(r.get('id','')).startswith('ai.attas.vendor.') for r in input_data.get('refs',[])) else []
            installed.extend(r for entry in native for r in (entry['model'],entry['mapping'],*entry.get('dependencies',[])))
            values = { (r['id'], r['version']):r for r in resources() + [m for m in mappings if m] + installed }
            result = query_installed(list(values.values()), input_data or {})
            if input_data.get('include_support'):
                requested=input_data.get('refs',[])
                result['support']=[support(e) for e in native if not requested or reference(e['model']) in requested]
            return result
        if pulse_name == "data_spec":
            return self.data_spec(input_data or {})
        if pulse_name == "data_availability":
            return self.data_availability(input_data or {})
        if pulse_name == "data_fetch":
            return self.data_fetch(input_data or {})
        if pulse_name == "historical_prices":
            return self.historical_prices(input_data or {})
        return super().fetch_pulse_payload(pulse_name, input_data or {}, pulse_definition)


class DataSource(AbstractDataSource):
    """Compatibility implementation driven entirely by source configuration."""

    def source_profile(self) -> Mapping[str, Any]:
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        return _deep_merge(
            {
                "source_id": "data-source",
                "source_name": "Data Source",
                "provider": "Configured provider",
                "description": "Configured endpoint specification source.",
                "connectivity": {
                    "status": "configured",
                    "transport": "local_pulser",
                    "pulse_name": "data_availability",
                    "authentication": "source-dependent",
                    "configuration_required": [],
                    "notes": [],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return tuple(
            VendorSpecSource(**dict(item))
            for item in self._config_data.get("spec_sources") or []
            if isinstance(item, Mapping)
        )

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        del parameters, auth_context
        return EndpointExecutionResult(
            status="not_executable",
            endpoint_id=endpoint.endpoint_id,
            data_schema=endpoint.response_schema,
            cost=endpoint.cost,
            warnings=("No generic endpoint executor is configured.",),
            error="The endpoint is available as a specification only.",
        )


class TaiwanProviderDataSource(AbstractDataSource):
    """Shared executable runtime for the public Taiwan provider APIs."""

    VENDOR_ID = ""
    ENV_NAME = ""

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        connection_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        http_client: Any = None,
        websocket_factory: Any = None,
        **kwargs: Any,
    ) -> None:
        self._connection_resolver = connection_resolver
        self._http_client = http_client
        self._websocket_factory = websocket_factory
        super().__init__(config=config, **kwargs)
        if self._connection_resolver is None and self._credential_store is not None:
            self._connection_resolver = self._credential_store.resolve

    def source_profile(self) -> Mapping[str, Any]:
        definition = TAIWAN_SOURCE_DEFINITIONS[self.VENDOR_ID]
        required = bool(definition.get("authentication_required"))
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        profile = {
            "source_id": self.VENDOR_ID,
            "source_name": definition["source_name"],
            "provider": definition["provider"],
            "description": definition["description"],
            "connectivity": {
                "status": "configured" if required else "ready",
                "transport": "https",
                "pulse_name": "data_availability",
                "authentication": definition["authentication"],
                "configuration_required": [f"{definition['provider']} provider connection"] if required else [],
                "notes": [
                    "Every cataloged operation has a live execution adapter.",
                    "Provider and exchange terms remain controlling for storage and redistribution.",
                ],
            },
            "coverage": {"region": ["TW"], "asset_classes": list(definition.get("coverage") or [])},
            "entitlement": {
                "status": "provider-terms" if required else "public-access",
                "allowed_output_forms": ["request-scoped provider response"],
                "notes": ["Execution does not grant redistribution rights."],
            },
        }
        if self.VENDOR_ID == "fugle":
            profile["streaming"] = dict(FUGLE_STREAM_CAPABILITY)
        return _deep_merge(profile, configured)

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return taiwan_spec_sources(self.VENDOR_ID)

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        required = bool(TAIWAN_SOURCE_DEFINITIONS[self.VENDOR_ID].get("authentication_required"))
        credential_configured = self._operator_key_allowed() and self._source_owned_credential_configured(self.VENDOR_ID, self.ENV_NAME)
        return self._normalized_data_access(
            fetch_ready=not required,
            attempt_ready=not required or credential_configured,
            credential_required=required,
            credential_configured=credential_configured,
            verification="public" if not required else "unverified" if credential_configured else "unknown",
            reason=(
                "The provider route is public and needs no credential."
                if not required
                else "A source-owned credential is configured; a live request is still required to verify the entitlement."
                if credential_configured
                else "Each user calls this provider from their own Terminal with their own key."
            ),
        )

    def _operator_key_allowed(self) -> bool:
        # Fugle and FinMind data belong to their vendors. Users bring their own
        # keys in their Terminal; this source uses an operator key only when its
        # operator owns the data it serves.
        return not getattr(self, "ENV_NAME", "") or self._operator_owned_data()

    def is_endpoint_executable(self, endpoint: EndpointSpec) -> bool:
        if endpoint.transport == "websocket" and self.VENDOR_ID == "fugle":
            return endpoint.operation.startswith("streaming:")
        return provider_endpoint_is_executable(self.VENDOR_ID, endpoint.operation)

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        if getattr(self, "_definitions_only", False):
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error=DEFINITIONS_ONLY_REASON,
            )
        secret_names = {"token", "api_key", "apikey", "api-key", "api_token", "authorization", "secret", "password"}
        if any(str(name).lower() in secret_names for name in auth_context):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="Raw credentials are not accepted in auth_context; use an opaque connection_id.",
            )
        api_key = self._resolve_source_owned_credential(
            self.VENDOR_ID,
            auth_context,
            env_name=self.ENV_NAME,
            connection_resolver=self._connection_resolver,
        ) if self._operator_key_allowed() else ""
        if endpoint.transport == "websocket" and self.VENDOR_ID == "fugle":
            return execute_fugle_websocket_endpoint(
                endpoint,
                parameters,
                api_key,
                websocket_factory=self._websocket_factory,
            )
        return execute_taiwan_http_endpoint(
            self.VENDOR_ID,
            endpoint,
            parameters,
            api_key,
            http_client=self._http_client,
        )


class TWSEDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "twse"


class TPExDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "tpex"


class TAIFEXDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "taifex"


class FinMindDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "finmind"
    ENV_NAME = "FINMIND_API_TOKEN"


class TaiwanCentralBankDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "taiwan_central_bank"


class FugleDataSource(TaiwanProviderDataSource):
    VENDOR_ID = "fugle"
    ENV_NAME = "FUGLE_MARKETDATA_API_KEY"

    def _setup_data_agent_routes(self):
        super()._setup_data_agent_routes()
        if getattr(self, "_definitions_only", False):
            return
        from attas.data_agent_network.provider_runtime.stream_hub import StreamHub
        from attas.data_agent_network.provider_runtime.fugle import install_fugle_stream_route

        self.fugle_streams = StreamHub(retry_seconds=3)
        install_fugle_stream_route(self.app, self.plaza_url, self.fugle_streams, agent=self)
        self.app.router.add_event_handler("shutdown", self.fugle_streams.close)

    def _direct_extensions(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        from .fugle_direct import extensions
        return extensions(endpoint)

    def _endpoint_payload(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        payload = super()._endpoint_payload(endpoint)
        if endpoint.transport == "websocket":
            payload["mode"] = "stream"
            payload["delivery_mode"] = "stream"
        return payload


class ShioajiDataSource(AbstractDataSource):
    """SinoPac Securities (永豐金證券) Shioaji market data through the user's gateway.

    The account holder runs ``shioaji server start`` with ``SJ_API_KEY`` and
    ``SJ_SEC_KEY`` in that server's own ``.env``.  This source calls only the
    allowlisted read-only routes of that gateway and never handles the keys.
    """

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        http_client: Any = None,
        rate_limiter: ShioajiRateLimiter | None = None,
        **kwargs: Any,
    ) -> None:
        self._http_client = http_client
        self._rate_limiter = rate_limiter
        super().__init__(config=config, **kwargs)

    def _shioaji_settings(self) -> Mapping[str, Any]:
        settings = self._config_data.get("shioaji")
        return settings if isinstance(settings, Mapping) else {}

    def gateway_url(self) -> str:
        """Return the validated source-owned gateway URL, or an empty string."""
        configured = self._shioaji_settings().get("gateway_url") or os.getenv(SHIOAJI_GATEWAY_ENV) or ""
        try:
            return normalize_shioaji_gateway_url(configured)
        except ValueError:
            return ""

    def source_profile(self) -> Mapping[str, Any]:
        definition = SHIOAJI_SOURCE_DEFINITION
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        gateway_ready = bool(self.gateway_url())
        count, window = SHIOAJI_RATE_LIMITS["market_data"]
        return _deep_merge(
            {
                "source_id": SHIOAJI_SOURCE_ID,
                "source_name": definition["source_name"],
                "provider": definition["provider"],
                "description": definition["description"],
                "connectivity": {
                    "status": "configured" if gateway_ready else "requires_configuration",
                    "transport": "http",
                    "pulse_name": "data_availability",
                    "authentication": definition["authentication"],
                    "configuration_required": [
                        "Run `shioaji server start` with SJ_API_KEY and SJ_SEC_KEY in the gateway's .env"
                        if gateway_ready
                        else "Set shioaji.gateway_url to a loopback http or any https shioaji server URL ending in /api/v1"
                    ],
                    "notes": [
                        "Executes only read-only market-data, contract-reference, and usage routes; order, portfolio, and account routes are not exposed.",
                        f"Market-data routes are limited to {count} requests per {window:g} seconds per gateway, matching the published Shioaji limit.",
                        "Real-time quote subscriptions (SSE) are not exposed by this source.",
                    ],
                },
                "coverage": {"region": ["TW"], "asset_classes": list(definition.get("coverage") or [])},
                "entitlement": {
                    "status": "provider-terms",
                    "allowed_output_forms": ["request-scoped provider response"],
                    "notes": [
                        "SinoPac Securities, TWSE, TPEx, and TAIFEX terms remain controlling; execution does not grant redistribution rights.",
                    ],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return shioaji_spec_sources()

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        gateway_ready = bool(self.gateway_url())
        return self._normalized_data_access(
            fetch_ready=False,
            attempt_ready=gateway_ready,
            credential_required=True,
            credential_configured=gateway_ready,
            verification="unverified" if gateway_ready else "unknown",
            reason=(
                "A Shioaji gateway URL is configured; a successful live request verifies that shioaji server is logged in."
                if gateway_ready
                else "Configure a valid Shioaji gateway URL before fetching live data."
            ),
        )

    def is_endpoint_executable(self, endpoint: EndpointSpec) -> bool:
        return shioaji_endpoint_is_executable(endpoint)

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        secret_names = {"token", "api_key", "apikey", "secret", "secret_key", "password", "authorization", "credential", "ca_passwd"}
        if any(str(name).lower() in secret_names for name in auth_context):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="Raw Shioaji credentials are not accepted in auth_context; they stay in the shioaji server .env.",
            )
        gateway_url = self.gateway_url()
        if not gateway_url:
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="The configured Shioaji gateway URL is invalid; plain HTTP is accepted only on loopback.",
            )
        try:
            timeout_sec = float(self._shioaji_settings().get("timeout_sec") or 30)
        except (TypeError, ValueError):
            timeout_sec = 30.0
        return execute_shioaji_endpoint(
            endpoint,
            parameters,
            gateway_url=gateway_url,
            http_client=self._http_client,
            rate_limiter=self._rate_limiter,
            timeout_sec=min(max(timeout_sec, 1.0), 120.0),
        )


class FinancialProviderDataSource(AbstractDataSource):
    """Shared executable runtime for provider APIs with server-side BYOK isolation."""

    VENDOR_ID = ""
    # A source that sets this publishes its vendor's key slot and base URL (from its execution policy), so a
    # user's Terminal can call the vendor with the user's own key. The key itself never reaches the source.
    PUBLISHES_KEY_SLOT = False

    def _key_slot(self) -> Tuple[str, str] | None:
        auth = (PROVIDER_EXECUTION_POLICIES.get(self.VENDOR_ID) or {}).get("auth")
        # Only a bare key in a query parameter or header; a prefixed or signed credential is not a slot a Terminal fills.
        if not self.PUBLISHES_KEY_SLOT or not isinstance(auth, tuple) or len(auth) != 3 or auth[2] or auth[0] not in ("query", "header"):
            return None
        return ("parameter" if auth[0] == "query" else "header"), str(auth[1])

    def _published_authentication(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        from .direct_access import declares_key
        published = super()._published_authentication(endpoint)
        slot = self._key_slot()
        if slot and published.get("required") is not False and not declares_key(published):
            published[slot[0]] = slot[1]
        return published

    def _direct_extensions(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        extensions = dict(super()._direct_extensions(endpoint))
        base = (PROVIDER_EXECUTION_POLICIES.get(self.VENDOR_ID) or {}).get("base_url")
        if self._key_slot() and base and not endpoint.extensions.get("attas.base_url"):
            extensions["attas.base_url"] = base
        return extensions

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        connection_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        http_client: Any = None,
        **kwargs: Any,
    ) -> None:
        self._connection_resolver = connection_resolver
        self._http_client = http_client
        super().__init__(config=config, **kwargs)
        if self._connection_resolver is None and self._credential_store is not None:
            self._connection_resolver = self._credential_store.resolve

    def source_profile(self) -> Mapping[str, Any]:
        definition = PROVIDER_DEFINITIONS[self.VENDOR_ID]
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        authentication = str(definition["authentication"])
        authentication_required = bool(definition.get("authentication_required", True))
        return _deep_merge(
            {
                "source_id": self.VENDOR_ID,
                "source_name": definition["source_name"],
                "provider": definition["provider"],
                "description": definition["description"],
                "connectivity": {
                    "status": "configured",
                    "transport": "https",
                    "pulse_name": "data_availability",
                    "authentication": authentication,
                    "configuration_required": [] if not authentication_required else [f"{definition['provider']} provider connection"],
                    "notes": [
                        "Public endpoint specifications are available without provider credentials.",
                        "Executable API routes use a server-side provider adapter and never accept raw credentials in pulse parameters.",
                    ],
                },
                "coverage": {"asset_classes": list(definition.get("coverage") or [])},
                "entitlement": {
                    "status": "provider-terms",
                    "allowed_output_forms": ["request-scoped provider response"],
                    "notes": ["Execution does not grant data redistribution rights; provider terms and user entitlements remain controlling."],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return provider_spec_sources(self.VENDOR_ID)

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        policy = PROVIDER_EXECUTION_POLICIES[self.VENDOR_ID]
        credential_required = bool(policy.get("required", True))
        credential_configured = self._source_owned_credential_configured(
            self.VENDOR_ID,
            str(policy.get("env") or ""),
        )
        if not credential_required:
            reason = "The provider route is public and needs no credential."
        elif credential_configured:
            reason = "A source-owned credential is configured; a successful live fetch is still required to verify access."
        else:
            reason = "A source-owned provider credential is required before Data Source pulse access can fetch data."
        return self._normalized_data_access(
            fetch_ready=not credential_required,
            attempt_ready=not credential_required or credential_configured,
            credential_required=credential_required,
            credential_configured=credential_configured,
            verification="public" if not credential_required else "unverified" if credential_configured else "unknown",
            reason=reason,
        )

    def is_endpoint_executable(self, endpoint: EndpointSpec) -> bool:
        return provider_endpoint_is_executable(self.VENDOR_ID, endpoint.operation)

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        if getattr(self, "_definitions_only", False):
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error=DEFINITIONS_ONLY_REASON,
            )
        secret_names = {"token", "api_key", "apikey", "api-key", "api_token", "authorization", "secret", "password"}
        if any(str(name).lower() in secret_names for name in auth_context):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="Raw credentials are not accepted in auth_context; use an opaque connection_id.",
            )
        policy = PROVIDER_EXECUTION_POLICIES[self.VENDOR_ID]
        api_key = self._resolve_source_owned_credential(
            self.VENDOR_ID,
            auth_context,
            env_name=str(policy.get("env") or ""),
            connection_resolver=self._connection_resolver,
        )
        return execute_financial_provider_endpoint(
            self.VENDOR_ID,
            asdict(endpoint),
            parameters,
            api_key,
            http_client=self._http_client,
        )


# Backward-compatible import for downstream code; new implementations must use
# FinancialProviderDataSource and provide an executable provider policy.
SpecificationOnlyFinancialDataSource = FinancialProviderDataSource


class TwelveDataDataSource(FinancialProviderDataSource):
    VENDOR_ID = "twelve_data"


class MassiveDataSource(FinancialProviderDataSource):
    VENDOR_ID = "massive"


class FinancialModelingPrepDataSource(FinancialProviderDataSource):
    VENDOR_ID = "financial_modeling_prep"


class EODHDDataSource(FinancialProviderDataSource):
    VENDOR_ID = "eodhd"


class TiingoDataSource(FinancialProviderDataSource):
    VENDOR_ID = "tiingo"


class NasdaqDataLinkDataSource(FinancialProviderDataSource):
    VENDOR_ID = "nasdaq_data_link"


class SECEdgarDataSource(FinancialProviderDataSource):
    VENDOR_ID = "sec_edgar"


class FREDDataSource(FinancialProviderDataSource):
    VENDOR_ID = "fred"


class TreasuryFiscalDataDataSource(FinancialProviderDataSource):
    VENDOR_ID = "treasury_fiscal_data"


class WorldBankDataSource(FinancialProviderDataSource):
    VENDOR_ID = "world_bank"


class IMFDataSource(FinancialProviderDataSource):
    VENDOR_ID = "imf"


class ECBDataSource(FinancialProviderDataSource):
    VENDOR_ID = "ecb"


class BISDataSource(FinancialProviderDataSource):
    VENDOR_ID = "bis"


class OECDDataSource(FinancialProviderDataSource):
    VENDOR_ID = "oecd"


class CoinGeckoDataSource(FinancialProviderDataSource):
    VENDOR_ID = "coingecko"


class CoinMarketCapDataSource(FinancialProviderDataSource):
    VENDOR_ID = "coinmarketcap"


class OpenFIGIDataSource(FinancialProviderDataSource):
    VENDOR_ID = "openfigi"


class IntrinioDataSource(FinancialProviderDataSource):
    VENDOR_ID = "intrinio"


class MorningstarDataSource(FinancialProviderDataSource):
    VENDOR_ID = "morningstar"


class CrunchbaseDataSource(FinancialProviderDataSource):
    VENDOR_ID = "crunchbase"


class MoodysAnalyticsDataSource(FinancialProviderDataSource):
    VENDOR_ID = "moodys_analytics"


class DealroomDataSource(FinancialProviderDataSource):
    VENDOR_ID = "dealroom"


class QuartrDataSource(FinancialProviderDataSource):
    VENDOR_ID = "quartr"


class VisibleDataSource(FinancialProviderDataSource):
    VENDOR_ID = "visible"


class CEICDataSource(FinancialProviderDataSource):
    VENDOR_ID = "ceic"


class FactSetDataSource(FinancialProviderDataSource):
    VENDOR_ID = "factset"


class SimilarwebDataSource(FinancialProviderDataSource):
    VENDOR_ID = "similarweb"


class TomorrowIODataSource(FinancialProviderDataSource):
    VENDOR_ID = "tomorrow_io"


class WeatherbitDataSource(FinancialProviderDataSource):
    VENDOR_ID = "weatherbit"
    PUBLISHES_KEY_SLOT = True


class MeteomaticsDataSource(FinancialProviderDataSource):
    VENDOR_ID = "meteomatics"


class PlanetDataSource(FinancialProviderDataSource):
    VENDOR_ID = "planet"


class SentinelHubDataSource(FinancialProviderDataSource):
    VENDOR_ID = "sentinel_hub"


class UP42DataSource(FinancialProviderDataSource):
    VENDOR_ID = "up42"


class NOAAWeatherDataSource(FinancialProviderDataSource):
    VENDOR_ID = "noaa_weather"


class NASACMRDataSource(FinancialProviderDataSource):
    VENDOR_ID = "nasa_cmr"


class CopernicusDataSpaceDataSource(FinancialProviderDataSource):
    VENDOR_ID = "copernicus_data_space"


class GoogleEarthEngineDataSource(FinancialProviderDataSource):
    VENDOR_ID = "google_earth_engine"


class OpenWeatherDataSource(FinancialProviderDataSource):
    VENDOR_ID = "openweather"
    PUBLISHES_KEY_SLOT = True
    # Weather map tiles (Weather Maps 1.0) are PNG pictures served from their own host, not the API host.
    PICTURE_ENDPOINTS = {"openweather.weather_map": "https://tile.openweathermap.org"}

    def _direct_extensions(self, endpoint: EndpointSpec) -> Dict[str, Any]:
        extensions = dict(super()._direct_extensions(endpoint))
        host = self.PICTURE_ENDPOINTS.get(endpoint.endpoint_id)
        if host:
            extensions.update({"attas.base_url": host, "attas.media_type": "image/png"})
        return extensions


class AIsaOneDataSource(FinancialProviderDataSource):
    VENDOR_ID = "aisa_one"


class Sustainable1DataSource(FinancialProviderDataSource):
    """Licensed Sustainable1 XpressAPI retrieval using a source-owned token."""
    VENDOR_ID = "sustainable1"


class LicensedWorkflowDataSource(FinancialProviderDataSource):
    """Source-owned licensed product sessions and durable request workflows."""
    def __init__(self,config=None,**kwargs):
        super().__init__(config=config,**kwargs)
        root=self._config_data.get('workflow_state_root') or str(Path(self._config_data.get('spec_catalog_root') or 'data/catalogs').parent/'workflows'/self.VENDOR_ID)
        self._workflow=ProductClient(self.VENDOR_ID,root,http_client=self._http_client)
        self.app.router.add_event_handler('shutdown',self._workflow.close)

    def is_endpoint_executable(self,endpoint):
        return endpoint.executable and (endpoint.operation=='local:resume' or provider_endpoint_is_executable(self.VENDOR_ID,endpoint.operation))

    def _catalog_entries(self,snapshot):
        entries=super()._catalog_entries(snapshot)
        for entry in entries:
            if self.VENDOR_ID=='lseg_worldcheck_one' and entry.get('dataset_id')==self.VENDOR_ID+'.create_and_screen':
                entry['mode']='interactive';entry['delivery_mode']='interactive'
        return entries

    def _endpoint_payload(self,endpoint):
        payload=super()._endpoint_payload(endpoint)
        if endpoint.extensions.get('attas.side_effect')=='create_case':
            payload['mode']='interactive';payload['delivery_mode']='interactive'
        return payload

    def execute_endpoint(self,endpoint,parameters,auth_context):
        if set(auth_context)-{'connection_id'}:
            return EndpointExecutionResult(status='failed',endpoint_id=endpoint.endpoint_id,error='Use only an opaque connection_id for source authentication.')
        connection_id=str(auth_context.get('connection_id') or '')
        if connection_id:
            record=self._connection_resolver(connection_id) if self._connection_resolver else None
            if not isinstance(record,Mapping) or record.get('source_id')!=self.VENDOR_ID:
                return EndpointExecutionResult(status='authentication_required',endpoint_id=endpoint.endpoint_id,error='The selected source connection is unavailable.')
            credential=str(record.get('api_key') or record.get('token') or '')
        else:
            credential=self._resolve_source_owned_credential(self.VENDOR_ID,{},env_name=PROVIDER_EXECUTION_POLICIES[self.VENDOR_ID]['env'],connection_resolver=self._connection_resolver)
        return self._workflow.execute(endpoint,parameters,credential)


class DataScopeSelectDataSource(LicensedWorkflowDataSource):
    VENDOR_ID='lseg_datascope_select'


class WorldCheckOneDataSource(LicensedWorkflowDataSource):
    VENDOR_ID='lseg_worldcheck_one'

    def spec_sources(self):
        sources=list(super().spec_sources())
        for item in self._config_data.get('worldcheck_spec_sources') or ():
            source=VendorSpecSource(**dict(item))
            if source.vendor_id!=self.VENDOR_ID or source.media_type not in {'application/json','application/yaml','text/yaml'}:
                raise ValueError('World-Check schema imports must identify this source and use OpenAPI JSON/YAML.')
            sources.append(source)
        return tuple(sources)


class LSEGDataSource(FinancialProviderDataSource):
    """Licensed LSEG Data Platform REST source using source-owned OAuth access."""

    VENDOR_ID = "lseg"

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        """Include optional owner-supplied Swagger exports from API Playground."""
        sources = list(super().spec_sources())
        for item in self._config_data.get("lseg_spec_sources") or ():
            if not isinstance(item, Mapping):
                continue
            source = VendorSpecSource(**dict(item))
            if source.vendor_id != self.VENDOR_ID:
                raise ValueError("Every lseg_spec_sources entry must use vendor_id='lseg'.")
            if source.media_type not in {"application/json", "application/yaml", "text/yaml"}:
                raise ValueError("Entitled LSEG specification sources must be Swagger/OpenAPI JSON or YAML.")
            sources.append(source)
        return tuple(sources)


class BloombergDataSource(AbstractDataSource):
    """Licensed Bloomberg source using BLPAPI request and runtime schema services."""

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        bloomberg_client: Any = None,
        **kwargs: Any,
    ) -> None:
        self._bloomberg_client = bloomberg_client
        super().__init__(config=config, **kwargs)

    def source_profile(self) -> Mapping[str, Any]:
        definition = PROVIDER_DEFINITIONS["bloomberg"]
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        runtime_available = self._bloomberg_client is not None or importlib.util.find_spec("blpapi") is not None
        return _deep_merge(
            {
                "source_id": "bloomberg",
                "source_name": definition["source_name"],
                "provider": definition["provider"],
                "description": definition["description"],
                "connectivity": {
                    "status": "configured" if runtime_available else "requires_configuration",
                    "transport": "blpapi",
                    "pulse_name": "data_availability",
                    "authentication": definition["authentication"],
                    "configuration_required": [] if runtime_available else ["Bloomberg BLPAPI Python SDK and licensed Desktop API, SAPI, or B-Pipe session"],
                    "notes": [
                        "BLPAPI operations are executable only through the configured Bloomberg session.",
                        "Authorized request and response schemas are discovered and validated at runtime.",
                    ],
                },
                "coverage": {"asset_classes": list(definition.get("coverage") or [])},
                "entitlement": {
                    "status": "provider-terms",
                    "allowed_output_forms": ["request-scoped provider response"],
                    "notes": ["Bloomberg licensing, exchange entitlements, and redistribution restrictions remain controlling."],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return provider_spec_sources("bloomberg")

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        runtime_available = self._bloomberg_client is not None or importlib.util.find_spec("blpapi") is not None
        return self._normalized_data_access(
            fetch_ready=False,
            attempt_ready=runtime_available,
            credential_required=True,
            credential_configured=runtime_available,
            verification="unverified" if runtime_available else "unknown",
            reason=(
                "A Bloomberg BLPAPI runtime is configured; a successful live request is still required to verify session entitlements."
                if runtime_available
                else "Install the Bloomberg BLPAPI SDK and configure a licensed Desktop API, SAPI, or B-Pipe session."
            ),
        )

    def is_endpoint_executable(self, endpoint: EndpointSpec) -> bool:
        return bloomberg_endpoint_is_executable(endpoint.operation, endpoint.extensions)

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        secret_names = {"password", "token", "secret", "authorization", "credential", "api_key", "apikey"}
        if any(str(name).lower() in secret_names for name in auth_context):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                error="Raw Bloomberg credentials are not accepted in auth_context; use the source-owned licensed session.",
            )
        settings = self._config_data.get("bloomberg") if isinstance(self._config_data.get("bloomberg"), Mapping) else {}
        client = self._bloomberg_client or bloomberg_client_from_config(settings)
        return execute_bloomberg_endpoint(asdict(endpoint), parameters, client=client)


class YFinanceDataSource(AbstractDataSource):
    """YFinance source with a complete public API catalog and direct execution."""

    def source_profile(self) -> Mapping[str, Any]:
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        return _deep_merge(
            {
                "source_id": "yfinance",
                "source_name": "YFinance Data Source",
                "provider": "Yahoo Finance via yfinance",
                "description": "Documents the public yfinance API and executes supported library operations directly.",
                "connectivity": {
                    "status": "ready",
                    "transport": "python_library",
                    "pulse_name": "data_availability",
                    "authentication": "No user credential required; provider terms and limits apply.",
                    "configuration_required": [],
                    "notes": ["Live results remain provider- and symbol-dependent."],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        try:
            import yfinance as yf

            version = str(getattr(yf, "__version__", ""))
        except ImportError:
            version = ""
        return (
            VendorSpecSource(
                source_id="yfinance-official-reference",
                vendor_id="yfinance",
                kind="website",
                location="https://ranaroussi.github.io/yfinance/reference/index.html",
                media_type="text/html",
                vendor_version=version,
                description="Official yfinance API reference; endpoint signatures are verified against the installed package.",
                options={"allowed_hosts": ["ranaroussi.github.io"]},
            ),
        )

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        return self._normalized_data_access(
            fetch_ready=True,
            attempt_ready=True,
            credential_required=False,
            credential_configured=False,
            verification="public",
            reason="The installed yfinance runtime can fetch public provider data without a user credential.",
        )

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        del auth_context
        if not endpoint.executable:
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="This documented yfinance operation is not enabled for direct execution.",
            )
        try:
            import yfinance as yf

            operation = endpoint.operation.removeprefix("yfinance.")
            call_parameters = dict(parameters)
            if "." not in operation:
                target = getattr(yf, operation)
                if operation == "download":
                    call_parameters.setdefault("progress", False)
                raw = target(**call_parameters)
            else:
                class_name, member_name = operation.split(".", 1)
                if class_name not in {"Ticker", "Tickers", "Market", "Search", "Lookup", "Sector", "Industry", "Calendars"}:
                    raise ValueError(f"Direct execution is not enabled for yfinance.{class_name}")
                constructor_keys = {
                    "Ticker": "symbol", "Tickers": "tickers", "Market": "market",
                    "Search": "query", "Lookup": "query", "Sector": "key", "Industry": "key",
                }
                constructor_key = constructor_keys.get(class_name)
                if constructor_key:
                    constructor_value = call_parameters.pop(constructor_key, "")
                    if not constructor_value:
                        raise ValueError(f"{constructor_key} is required")
                    instance = getattr(yf, class_name)(constructor_value)
                else:
                    constructor_parameters = {
                        name: call_parameters.pop(name)
                        for name in ("start", "end")
                        if name in call_parameters
                    }
                    instance = getattr(yf, class_name)(**constructor_parameters)
                target = getattr(instance, member_name)
                if callable(target):
                    raw = target(**call_parameters)
                else:
                    if call_parameters:
                        raise ValueError(f"{endpoint.operation} is a property and accepts no call parameters")
                    raw = target
            if operation in ('Ticker.funds_data','Ticker.get_funds_data'):
                raw = {'top_holdings':_json_safe(raw.top_holdings), 'description':str(raw.description or '')}
            normalized = _json_safe(raw)
            data = normalized if isinstance(normalized, Mapping) else {"items": normalized}
            return EndpointExecutionResult(
                status="completed",
                endpoint_id=endpoint.endpoint_id,
                data=dict(data),
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
            )
        except Exception as exc:  # provider boundary
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error=str(exc),
            )


class AlphaVantageDataSource(AbstractDataSource):
    """Alpha Vantage source with public specs and source-owned auth execution."""

    _SECRET_PARAMETER_NAMES = {"token", "api_key", "apikey", "api-key"}

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        connection_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        http_client: Any = None,
        **kwargs: Any,
    ) -> None:
        self._connection_resolver = connection_resolver
        self._http_client = http_client
        super().__init__(config=config, **kwargs)
        if self._connection_resolver is None and self._credential_store is not None:
            self._connection_resolver = self._credential_store.resolve

    def source_profile(self) -> Mapping[str, Any]:
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        return _deep_merge(
            {
                "source_id": "alpha_vantage",
                "source_name": "Alpha Vantage Data Source",
                "provider": "Alpha Vantage",
                "description": "Documents Alpha Vantage API functions and executes entitled endpoints with a source-owned connection.",
                "connectivity": {
                    "status": "configured",
                    "transport": "https",
                    "pulse_name": "data_availability",
                    "authentication": "API key and endpoint entitlement are required for live access.",
                    "configuration_required": ["Alpha Vantage provider connection"],
                    "notes": [
                        "Specification discovery is available without exposing an API key.",
                        "Pulse requests accept only a source-issued opaque connection_id; source-owned credentials are resolved server-side.",
                    ],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return (
            VendorSpecSource(
                source_id="alpha-vantage-official-documentation",
                vendor_id="alpha_vantage",
                kind="website",
                location="https://www.alphavantage.co/documentation/",
                media_type="text/html",
                description="Official Alpha Vantage API documentation.",
                options={"allowed_hosts": ["www.alphavantage.co", "alphavantage.co"], "max_bytes": 16 * 1024 * 1024},
            ),
        )

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        credential_configured = self._source_owned_credential_configured(
            "alpha_vantage",
            "ALPHA_VANTAGE_API_KEY",
        )
        return self._normalized_data_access(
            fetch_ready=False,
            attempt_ready=credential_configured,
            credential_required=True,
            credential_configured=credential_configured,
            verification="unverified" if credential_configured else "unknown",
            reason=(
                "A source-owned Alpha Vantage credential is configured; a successful live fetch is still required to verify access."
                if credential_configured
                else "A source-owned Alpha Vantage credential is required before Data Source pulse access can fetch data."
            ),
        )

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        if not endpoint.executable:
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                warnings=(
                    "Live Alpha Vantage Pulse access requires a source-owned API key resolved through an opaque connection ID.",
                ),
                error="This Alpha Vantage operation is available as a specification only.",
            )
        if any(name.lower() in self._SECRET_PARAMETER_NAMES for name in parameters):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="Raw API credentials are not accepted as endpoint parameters; use auth_context.connection_id.",
            )
        if any(name.lower() in self._SECRET_PARAMETER_NAMES for name in auth_context):
            return EndpointExecutionResult(
                status="failed",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="Raw API credentials are not accepted in auth_context; use an opaque connection_id.",
            )
        api_key = self._resolve_source_owned_credential(
            "alpha_vantage",
            auth_context,
            env_name="ALPHA_VANTAGE_API_KEY",
            connection_resolver=self._connection_resolver,
        )
        if not api_key:
            return EndpointExecutionResult(
                status="authentication_required",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="No usable source-owned Alpha Vantage credential is configured.",
            )
        return execute_direct_provider_endpoint(
            "alpha_vantage",
            asdict(endpoint),
            parameters,
            api_key,
            http_client=self._http_client,
        )


class FinnhubDataSource(AbstractDataSource):
    """Finnhub source with a public spec catalog and server-side BYOK execution."""

    _BASE_URL = "https://finnhub.io/api/v1"
    _SECRET_PARAMETER_NAMES = {"token", "api_key", "apikey", "api-key"}

    def __init__(
        self,
        config: ConfigInput | None = None,
        *,
        connection_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        http_client: Any = None,
        **kwargs: Any,
    ) -> None:
        self._connection_resolver = connection_resolver
        self._http_client = http_client
        super().__init__(config=config, **kwargs)
        if self._connection_resolver is None and self._credential_store is not None:
            self._connection_resolver = self._credential_store.resolve

    def source_profile(self) -> Mapping[str, Any]:
        configured = self._config_data.get("source_profile") if isinstance(self._config_data.get("source_profile"), Mapping) else {}
        return _deep_merge(
            {
                "source_id": "finnhub",
                "source_name": "Finnhub Data Source",
                "provider": "Finnhub",
                "description": "Catalogs Finnhub's public endpoint specification and executes entitled endpoints with a source-owned connection.",
                "connectivity": {
                    "status": "configured",
                    "transport": "https",
                    "pulse_name": "data_availability",
                    "authentication": "A source-owned Finnhub connection and applicable endpoint entitlement are required for live Pulse access.",
                    "configuration_required": ["Finnhub provider connection"],
                    "notes": [
                        "Specification discovery is public and does not require an API key.",
                        "Pulse requests accept only a source-issued opaque connection_id; source-owned credentials are resolved server-side.",
                    ],
                },
            },
            configured,
        )

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return (
            VendorSpecSource(
                source_id="finnhub-official-swagger",
                vendor_id="finnhub",
                kind="website",
                location="https://finnhub.io/static/swagger.json",
                media_type="application/json",
                vendor_version="1.0.0",
                description="Official public Finnhub Swagger 2.0 endpoint specification.",
                options={
                    "allowed_hosts": ["finnhub.io", "www.finnhub.io"],
                    "max_bytes": 2 * 1024 * 1024,
                    "timeout_sec": 120,
                },
            ),
        )

    def data_access_status(self) -> Dict[str, Any]:
        configured = self._configured_data_access()
        if configured is not None:
            return self._normalized_data_access(configured)
        credential_configured = self._source_owned_credential_configured(
            "finnhub",
            "FINNHUB_API_KEY",
        )
        return self._normalized_data_access(
            fetch_ready=False,
            attempt_ready=credential_configured,
            credential_required=True,
            credential_configured=credential_configured,
            verification="unverified" if credential_configured else "unknown",
            reason=(
                "A source-owned Finnhub credential is configured; a successful live fetch is still required to verify access."
                if credential_configured
                else "A source-owned Finnhub credential is required before Data Source pulse access can fetch data."
            ),
        )

    @staticmethod
    def _execution_failure(endpoint: EndpointSpec, error: str) -> EndpointExecutionResult:
        return EndpointExecutionResult(
            status="failed",
            endpoint_id=endpoint.endpoint_id,
            data_schema=endpoint.response_schema,
            cost=endpoint.cost,
            error=error,
        )

    def execute_endpoint(
        self,
        endpoint: EndpointSpec,
        parameters: Mapping[str, Any],
        auth_context: Mapping[str, Any],
    ) -> EndpointExecutionResult:
        if not endpoint.executable:
            return EndpointExecutionResult(
                status="not_executable",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="This Finnhub operation is available as a specification only.",
            )

        if any(name.lower() in self._SECRET_PARAMETER_NAMES for name in parameters):
            return self._execution_failure(
                endpoint,
                "Raw API credentials are not accepted as endpoint parameters; use auth_context.connection_id.",
            )
        if any(name.lower() in self._SECRET_PARAMETER_NAMES for name in auth_context):
            return self._execution_failure(
                endpoint,
                "Raw API credentials are not accepted in auth_context; use an opaque connection_id.",
            )
        api_key = self._resolve_source_owned_credential(
            "finnhub",
            auth_context,
            env_name="FINNHUB_API_KEY",
            connection_resolver=self._connection_resolver,
        )
        if not api_key:
            return EndpointExecutionResult(
                status="authentication_required",
                endpoint_id=endpoint.endpoint_id,
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
                error="No usable source-owned Finnhub credential is configured.",
            )

        method = str(endpoint.extensions.get("finnhub.http_method") or "").upper()
        path = str(endpoint.extensions.get("finnhub.path") or "")
        base_url = str(endpoint.extensions.get("finnhub.base_url") or "")
        if method not in {"GET", "POST"} or not path.startswith("/") or base_url != self._BASE_URL:
            return self._execution_failure(endpoint, "The endpoint has an unsupported Finnhub execution route.")

        parameter_specs = {item.name: item for item in endpoint.parameters}
        unknown = sorted(set(parameters) - set(parameter_specs))
        if unknown:
            return self._execution_failure(endpoint, f"Unsupported endpoint parameter(s): {', '.join(unknown)}")
        missing = [item.name for item in endpoint.parameters if item.required and parameters.get(item.name) is None]
        if missing:
            return self._execution_failure(endpoint, f"Missing required endpoint parameter(s): {', '.join(missing)}")

        query_parameters: dict[str, Any] = {}
        body_parameters: dict[str, Any] = {}
        resolved_path = path
        for name, value in parameters.items():
            spec = parameter_specs[name]
            if value is None:
                continue
            if spec.location == "path":
                marker = "{" + name + "}"
                if marker not in resolved_path:
                    return self._execution_failure(endpoint, f"Path parameter {name!r} is not present in the endpoint route.")
                resolved_path = resolved_path.replace(marker, quote(str(value), safe=""))
            elif spec.location == "body":
                if isinstance(value, Mapping):
                    body_parameters.update(value)
                else:
                    body_parameters[name] = value
            elif spec.location in {"query", "formData"}:
                query_parameters[name] = value
            else:
                return self._execution_failure(endpoint, f"Unsupported parameter location: {spec.location}")
        if "{" in resolved_path or "}" in resolved_path:
            return self._execution_failure(endpoint, "One or more endpoint path parameters are unresolved.")

        client = self._http_client
        owns_client = client is None
        try:
            if client is None:
                import httpx

                client = httpx.Client(timeout=30, follow_redirects=False)
            request_options: dict[str, Any] = {
                "params": query_parameters or None,
                "headers": {"Accept": "application/json", "X-Finnhub-Token": api_key},
            }
            if body_parameters:
                request_options["json"] = body_parameters
            response = client.request(
                method,
                f"{self._BASE_URL}{resolved_path}",
                **request_options,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in {401, 403}:
                return EndpointExecutionResult(
                    status="authentication_required",
                    endpoint_id=endpoint.endpoint_id,
                    data_schema=endpoint.response_schema,
                    cost=endpoint.cost,
                    error="Finnhub rejected the connection credential or endpoint entitlement.",
                )
            if status_code < 200 or status_code >= 300:
                return self._execution_failure(endpoint, f"Finnhub request failed with HTTP status {status_code}.")
            try:
                raw = response.json()
            except Exception:
                return self._execution_failure(endpoint, "Finnhub returned a non-JSON response.")
            if isinstance(raw, Mapping) and raw.get("error"):
                return self._execution_failure(endpoint, "Finnhub rejected the request parameters or endpoint entitlement.")
            normalized = _json_safe(raw)
            data = normalized if isinstance(normalized, Mapping) else {"items": normalized}
            return EndpointExecutionResult(
                status="completed",
                endpoint_id=endpoint.endpoint_id,
                data=dict(data),
                data_schema=endpoint.response_schema,
                cost=endpoint.cost,
            )
        except Exception:
            return self._execution_failure(endpoint, "Finnhub request could not be completed.")
        finally:
            if owns_client and client is not None:
                client.close()


class EnterpriseProductDataSource(FinancialProviderDataSource):
    """Delivery parameters and licensed SDKs are configured exclusively by the owner."""
    def __init__(self,config=None,**kwargs):
        super().__init__(config=config,**kwargs)
        root=self._config_data.get('workflow_state_root') or str(Path(self._config_data.get('spec_catalog_root') or 'data/catalogs').parent/'workflows'/self.VENDOR_ID)
        self.delivery_config=self._config_data.get('enterprise_delivery') or {}
        self._enterprise=EnterpriseClient(self.VENDOR_ID,self.delivery_config,root,self._http_client)
        self.app.router.add_event_handler('shutdown',self._enterprise.close)
        from .enterprise_stream import install_stream
        install_stream(self)

    def spec_sources(self):
        return (*super().spec_sources(), *(VendorSpecSource(**row) for row in self._config_data.get('enterprise_spec_sources',[])))

    def source_profile(self):
        profile=dict(super().source_profile())
        config=self._config_data.get('enterprise_delivery') or {}
        from .enterprise_stream import capability
        stream=capability(self.VENDOR_ID,config)
        if stream: profile['streaming']=stream
        profile['connectivity']['notes']=['Public definitions do not verify licensed data access. Install licensed manifests or schemas for additional delivery modes.']
        return profile

    def is_endpoint_executable(self,e):
        return bool(e.executable)

    def source_credential(self,auth_context):
        if set(auth_context)-{'connection_id'}: raise ValueError('Only connection_id is accepted.')
        key=auth_context.get('connection_id')
        if key:
            row=self._connection_resolver(key) if self._connection_resolver else None
            if not isinstance(row,Mapping) or row.get('source_id')!=self.VENDOR_ID: raise PermissionError('Unknown source connection.')
            return str(row.get('api_key') or row.get('token') or '')
        return str(self._resolve_source_owned_credential(self.VENDOR_ID,{},env_name=self.VENDOR_ID.upper()+'_CREDENTIALS',connection_resolver=self._connection_resolver) or '')

    def execute_endpoint(self,e,parameters,auth_context):
        try: raw=self.source_credential(auth_context)
        except (PermissionError,ValueError):
            return EndpointExecutionResult(status='authentication_required',endpoint_id=e.endpoint_id,error='Select a valid source-owned connection.')
        if self.VENDOR_ID=='bloomberg_bpipe' and e.transport=='blpapi':
            settings=self.delivery_config.get('bloomberg',{})
            return execute_bloomberg_endpoint(asdict(e),parameters,client=bloomberg_client_from_config(settings))
        return self._enterprise.execute(e,parameters,raw)


class BloombergDataLicenseSource(EnterpriseProductDataSource): VENDOR_ID='bloomberg_data_license'
class BloombergEventFeedSource(EnterpriseProductDataSource): VENDOR_ID='bloomberg_event_feeds'
class FactSetStandardFeedSource(EnterpriseProductDataSource): VENDOR_ID='factset_standard_feeds'
class FactSetExchangeFeedSource(EnterpriseProductDataSource): VENDOR_ID='factset_exchange_feed'
class FactSetOnDemandSource(EnterpriseProductDataSource): VENDOR_ID='factset_ondemand'
class FactSetPartnerSource(EnterpriseProductDataSource): VENDOR_ID='factset_partners'
class BloombergBPipeSource(EnterpriseProductDataSource): VENDOR_ID='bloomberg_bpipe'
