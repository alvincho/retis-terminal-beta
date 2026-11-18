"""
Plaza integration and web runtime for `prompits.practices.plaza`.

Prompits provides the core HTTP-native agent runtime, Plaza coordination layer, and
pool/practice infrastructure for FinMAS. Within Prompits, the practices package bundles
reusable behaviors that agents can mount or execute remotely.

Core types exposed here include `PlazaAuthenticatePractice`, `PlazaCredentialStore`,
`PlazaEndpointPractice`, `PlazaHeartbeatPractice`, and `PlazaPractice`, which carry the
main behavior or state managed by this module.
"""

import uuid
import asyncio
import httpx
import time
import threading
import secrets
import json
import os
import logging
import concurrent.futures
import functools
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Depends, Request
from prompits.core.json_response import NativeJSONResponse as JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from typing import Dict, Any, Optional, List, Set, Tuple
from pydantic import BaseModel
from prompits.channels.notifications import (
    NotificationDispatcher,
    extract_notification_config,
    store_forward_summary_from_card,
)
from prompits.core.agent_config import AgentConfigStore
from prompits.core.init_schema import (
    builtin_schema_cards,
    plaza_credentials_table_schema,
    plaza_login_history_table_schema,
    plaza_directory_table_schema,
    plaza_site_settings_table_schema,
    pulse_pulser_pairs_table_schema,
)
from prompits.core.directory_runtime import normalize_pulse_pair_entry, normalize_runtime_pulse_entry
from prompits.core.practice import Practice
from prompits.core.pool import Pool
from prompits.core.pit import PitAddress
from prompits.core.plaza_jobs import coordination_job, track_plaza_job

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)
DISPATCHER_DISCOVERY_PRACTICES = (
    "dispatcher-get-job",
    "dispatcher-register-worker",
    "dispatcher-post-job-result",
)


# Every directory listing compares each pulser's advertised pulses with one another, so the same few hundred pulse
# names were normalized tens of thousands of times a listing (most of a listing's search time). Both helpers are pure
# functions of their text, so their answers are kept, a bounded number of them.
@functools.lru_cache(maxsize=8192)
def _pulse_alias_token(value: str) -> str:
    """A pulse name, id, address or title reduced to its alias token (PlazaState._normalize_pulse_alias_token)."""
    text = value.strip().lower()
    if not text:
        return ""
    if text.startswith("urn:plaza:pulse:"):
        text = text.split("urn:plaza:pulse:", 1)[1]
    if text.startswith("plaza://pulse/"):
        text = text.split("plaza://pulse/", 1)[1]
    if "@" in text:
        text = text.split("@", 1)[0]
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


@functools.lru_cache(maxsize=4096)
def _pulse_identity_tokens(state_class: type, values: tuple) -> frozenset:
    """One pulse's identity tokens for its (name, id, address, title) text (PlazaState.pulse_identity_tokens)."""
    return state_class._pulse_identity_tokens_uncached(values)


class RegisterRequest(BaseModel):
    """Request body for `/register` used to create or relogin an agent identity."""
    agent_name: str
    address: str
    expires_in: int = 3600
    pit_type: str = "Agent"
    party: Optional[str] = None
    card: Optional[Dict[str, Any]] = None
    pulse_pulser_pairs: Optional[List[Dict[str, Any]]] = None
    agent_id: Optional[str] = None
    api_key: Optional[str] = None
    owner_key: Optional[str] = None
    accepts_inbound_from_plaza: Optional[bool] = None

class RenewRequest(BaseModel):
    """Request body for `/renew` used to rotate bearer tokens."""
    agent_name: str
    expires_in: int = 3600

class RelayMessage(BaseModel):
    """Request body for `/relay` used to forward messages between agents."""
    receiver: str
    content: Any
    msg_type: str = "message"

    traceparent: Optional[str] = None


class PlazaNotificationRequest(BaseModel):
    """Request body for `/notifications` notification-only delivery."""
    receiver: Optional[str] = None
    event_type: str = "notification"
    exchange_id: Optional[str] = None
    request_id: Optional[str] = None
    practice_id: Optional[str] = None
    practice_name: Optional[str] = None
    title: Optional[str] = None
    subject: Optional[str] = None
    message: Optional[str] = None
    message_style: Optional[str] = None
    render_mode: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    subject_prefix: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    expires_at: Optional[str] = None
    plaza_url: Optional[str] = None
    throttle_key: Optional[str] = None
    providers: Optional[List[Any]] = None
    routes: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    force: bool = False
    allow_during_no_bother: bool = False

class HeartbeatRequest(BaseModel):
    """Request body for `/heartbeat` used to mark an agent as active."""
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    content: Optional[Dict[str, Any]] = None
    observability: Optional[Dict[str, Any]] = None


class PlazaCreditPointRequest(BaseModel):
    """Request body for Plaza credit point balance and adjustment operations."""
    action: str = "balance"
    delta: Optional[int] = None
    amount: Optional[int] = None
    caller_user_token: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PlazaCredentialStore:
    """
    Persistence adapter for Plaza credentials and login-history audit records.

    This component isolates persistence mechanics from Plaza endpoint logic and
    safely degrades when optional history writes fail.
    """

    PLAZA_CREDENTIALS_TABLE = "plaza_credentials"
    PLAZA_LOGIN_HISTORY_TABLE = "plaza_login_history"

    def __init__(self, pool: Optional[Pool] = None):
        """Initialize the Plaza credential store."""
        self.pool = pool
        self._login_history_available = True

    def _ensure_plaza_credentials_table(self):
        """Create credential table if needed."""
        if getattr(self, "_credentials_table_ensured", False):
            return
        if not self.pool:
            return
        if self.pool._TableExists(self.PLAZA_CREDENTIALS_TABLE):
            self._credentials_table_ensured = True
            return
        schema = plaza_credentials_table_schema()
        self.pool._CreateTable(self.PLAZA_CREDENTIALS_TABLE, schema)
        self._credentials_table_ensured = True

    @staticmethod
    def _normalize_plaza_url(plaza_url: Optional[str]) -> str:
        """Internal helper to normalize the Plaza URL."""
        return str(plaza_url or "").rstrip("/")

    @classmethod
    def _credential_row_id(
        cls,
        agent_name: Optional[str] = None,
        plaza_url: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> str:
        """Internal helper for credential row ID."""
        normalized_plaza = cls._normalize_plaza_url(plaza_url)
        normalized_name = str(agent_name or "").strip()
        if normalized_name and normalized_plaza:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"plaza-credentials:{normalized_name}:{normalized_plaza}"))
        return str(agent_id or normalized_name or "").strip()

    @classmethod
    def _legacy_credential_row_id(
        cls,
        agent_name: Optional[str] = None,
        plaza_url: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> str:
        """Internal helper for legacy credential row ID."""
        normalized_plaza = cls._normalize_plaza_url(plaza_url)
        normalized_name = str(agent_name or "").strip()
        if normalized_name and normalized_plaza:
            return f"{normalized_name}:{normalized_plaza}"
        return str(agent_id or normalized_name or "").strip()

    def save(self, agent_name: str, agent_id: str, api_key: str, plaza_url: Optional[str] = None):
        """Save the value."""
        if not self.pool:
            return
        if not agent_name or not agent_id or not api_key:
            return
        self._ensure_plaza_credentials_table()
        normalized_plaza = self._normalize_plaza_url(plaza_url)
        row = {
            "id": self._credential_row_id(agent_name=agent_name, plaza_url=normalized_plaza, agent_id=agent_id),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "api_key": api_key,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if normalized_plaza:
            row["plaza_url"] = normalized_plaza
        self.pool._Insert(self.PLAZA_CREDENTIALS_TABLE, row)

    @staticmethod
    def _parse_updated_at(value: Any) -> datetime:
        """Internal helper to parse the updated at."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return datetime.fromtimestamp(0, tz=timezone.utc)
        return datetime.fromtimestamp(0, tz=timezone.utc)

    @staticmethod
    def _to_epoch_seconds(value: Any) -> float:
        """Convert the value to epoch seconds."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
        return 0.0

    def load(
        self,
        agent_name: Optional[str] = None,
        agent_id: Optional[str] = None,
        plaza_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Load most recent credential record by `agent_id` or `agent_name`."""
        if not self.pool:
            return None
        if not agent_name and not agent_id:
            return None
        self._ensure_plaza_credentials_table()
        normalized_plaza = self._normalize_plaza_url(plaza_url)
        rows: List[Dict[str, Any]] = []
        if agent_name and normalized_plaza:
            row_id = self._credential_row_id(agent_name=agent_name, plaza_url=normalized_plaza, agent_id=agent_id)
            rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"id": row_id}) or []
            if not rows:
                legacy_row_id = self._legacy_credential_row_id(
                    agent_name=agent_name,
                    plaza_url=normalized_plaza,
                    agent_id=agent_id,
                )
                rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"id": legacy_row_id}) or []
            if not rows:
                rows = self.pool._GetTableData(
                    self.PLAZA_CREDENTIALS_TABLE,
                    {"agent_name": agent_name, "plaza_url": normalized_plaza},
                ) or []
        elif agent_id:
            rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"agent_id": agent_id}) or []
        else:
            rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"agent_name": agent_name}) or []
        if not rows and agent_id:
            rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"agent_id": agent_id}) or []
        if not rows and agent_name:
            rows = self.pool._GetTableData(self.PLAZA_CREDENTIALS_TABLE, {"agent_name": agent_name}) or []
        if not rows:
            return None
        row = max(rows, key=lambda r: self._parse_updated_at(r.get("updated_at")))
        if row.get("agent_id") and row.get("api_key"):
            return {"agent_id": row["agent_id"], "api_key": row["api_key"]}
        return None

    def clear(self, agent_id: str, agent_name: str = "", plaza_url: Optional[str] = None):
        """Handle clear for the Plaza credential store."""
        if not self.pool:
            return
        if not agent_id:
            return
        self._ensure_plaza_credentials_table()
        normalized_plaza = self._normalize_plaza_url(plaza_url)
        row = {
            "id": self._credential_row_id(agent_name=agent_name, plaza_url=normalized_plaza, agent_id=agent_id),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "api_key": "",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if normalized_plaza:
            row["plaza_url"] = normalized_plaza
        self.pool._Insert(self.PLAZA_CREDENTIALS_TABLE, row)

    def _ensure_login_history_table(self):
        """Internal helper to ensure the login history table exists."""
        if getattr(self, "_login_history_table_ensured", False):
            return
        if not self.pool or not self._login_history_available:
            return
        try:
            if self.pool._TableExists(self.PLAZA_LOGIN_HISTORY_TABLE):
                self._login_history_table_ensured = True
                return
            schema = plaza_login_history_table_schema()
            self.pool._CreateTable(self.PLAZA_LOGIN_HISTORY_TABLE, schema)
            self._login_history_table_ensured = True
        except Exception:
            # Fail open: history persistence is optional and must never block plaza ops.
            self._login_history_available = False

    def append_login_event(self, agent_id: str, agent_name: str, address: str, event: str, ts: float):
        """Append the login event."""
        if not self.pool or not agent_id or not self._login_history_available:
            return
        try:
            self._ensure_login_history_table()
            if not self._login_history_available:
                return
            ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            self.pool._Insert(self.PLAZA_LOGIN_HISTORY_TABLE, {
                "id": str(uuid.uuid4()),
                "agent_id": agent_id,
                "agent_name": agent_name,
                "address": address,
                "event": event,
                "timestamp": ts_iso
            })
        except Exception:
            self._login_history_available = False

    def load_login_history(self, agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Load the login history."""
        if not self.pool or not agent_id or not self._login_history_available:
            return []
        try:
            self._ensure_login_history_table()
            if not self._login_history_available:
                return []
            rows = self.pool._GetTableData(self.PLAZA_LOGIN_HISTORY_TABLE, {"agent_id": agent_id}) or []
            normalized = []
            for row in rows:
                item = dict(row)
                item["timestamp"] = self._to_epoch_seconds(item.get("timestamp"))
                normalized.append(item)
            normalized.sort(key=lambda r: float(r.get("timestamp", 0)))
            return normalized[-limit:]
        except Exception:
            self._login_history_available = False
            return []

class PlazaState:
    """
    Shared mutable state backing all Plaza endpoint practices.

    Centralizes:
    - registration directory and card storage
    - token/auth state
    - credential and login history tracking
    - heartbeat/activity timestamps
    """

    SUPPORTED_PIT_TYPES = {"Agent", "AgentConfig", "Pulser", "Schema", "Pulse", "Party"}
    TRUST_LEVELS = {"blocked", "untrusted", "trusted", "approved"}
    TRUSTED_LEVELS = {"trusted", "approved"}
    TRUST_SCOPES = {"agent", "owner", "account", "viewer", "self", "builtin", "unknown"}
    DIRECTORY_TABLE = "plaza_directory"
    PULSE_PULSER_TABLE = "pulse_pulser_pairs"
    SITE_SETTINGS_TABLE = "plaza_site_settings"
    SITE_SETTINGS_ROW_ID = "site"
    SITE_SETTINGS_FALLBACK_PRACTICE_ID = "plaza-site-settings"
    LOGIN_HISTORY_LIMIT = 10
    PLAZA_TOKENS_TABLE = "plaza_tokens"
    IMPORTED_INIT_SUFFIX = "_imported.json"
    LEGACY_INIT_PULSE_FILE = "init_pulse.json"
    INIT_PULSE_PREFIX = "init_pulse_"
    AGENT_CONFIG_FILE_SUFFIXES = (".agent", ".pulser")
    PULSE_ALIAS_FAMILIES = {
        "ohlc_bar_series": {
            "ohlc_bar_series",
            "intraday_ohlcv_bar",
            "daily_ohlcv_bar",
            "daily_price_history",
            "ai_demo_finance_price_ohlc_bar_series",
        }
    }

    def current_token_for_agent(self, agent_id: str) -> Optional[str]:
        """The Plaza token an agent holds now, or None; the key of its address proof."""
        token = self.agent_tokens.get(self.agent_names_by_id.get(agent_id, ""))
        data = self.tokens.get(token) if token else None
        if not data or data.get("agent_id") != agent_id or float(data.get("expires_at") or 0) < time.time():
            return None
        return token

    # A registered agent that has neither heartbeated nor signed in for this long leaves the directory. Its
    # credential, owner and trust stay, so if it runs again its next heartbeat asks it to register and it is back.
    RETIRE_SILENT_AGENTS_AFTER_DAYS = 14
    RETIREMENT_CHECK_SECONDS = 3600

    def __init__(
        self,
        registry: Optional[Dict[str, Any]] = None,
        self_heartbeat_interval: int = 10,
        init_files: Optional[List[str] | str] = None,
        config_dir: Optional[str] = None,
        notification_providers: Optional[Any] = None,
        notification_settings: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the Plaza state."""
        self.registry = registry if registry is not None else {}
        self.self_heartbeat_interval = self_heartbeat_interval
        self.init_files = self._normalize_init_files(init_files)
        self.config_dir = str(config_dir or "").strip()
        self.tokens: Dict[str, Dict[str, Any]] = {}
        self.agent_tokens: Dict[str, str] = {}
        # Which agents answered at their registered address (prompits/core/address_proof.py).
        from prompits.core.address_proof import AddressProofs
        self.address_proofs = AddressProofs(self.current_token_for_agent)
        self.pit_types: Dict[str, str] = {}
        self.agent_ids: Dict[str, str] = {}
        self.registry_by_name: Dict[str, str] = {}
        self.agent_names_by_id: Dict[str, str] = {}
        self.credentials_by_id: Dict[str, Dict[str, Any]] = {}
        self._self_heartbeat_started = False
        self.credential_store: Optional[PlazaCredentialStore] = None
        self.plaza_url_for_store: str = ""
        self.directory_pool: Optional[Pool] = None
        self.agent_config_store: Optional[AgentConfigStore] = None
        self.agent_cards: Dict[str, Dict[str, Any]] = {}
        self.last_active: Dict[str, float] = {}
        self.login_history_by_id: Dict[str, List[Dict[str, Any]]] = {}
        self.party_registration_permissions: Dict[str, Any] = self._default_party_registration_permissions()
        self._loaded_login_history_ids: set[str] = set()
        self._login_history_load_events: Dict[str, threading.Event] = {}
        self._token_store_available = True
        self._directory_persistence_batch_depth = 0
        self._pending_directory_rows: Dict[str, Dict[str, Any]] = {}
        self.is_starting = True
        self.lock = threading.RLock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="plaza_bg_")
        self._housekeeping_started = False
        # The first check waits a full interval, so every running agent has heartbeated since this Plaza started.
        self._retirement_checked_at = time.time()
        self.site_settings: Dict[str, Any] = self._default_site_settings()
        self.notification_dispatcher = NotificationDispatcher(
            provider_configs=notification_providers,
            settings=notification_settings,
        )
        self._load_site_settings()

    def _default_site_settings(self) -> Dict[str, Any]:
        """Internal helper to return the default site settings."""
        return {
            "typography": {
                "title": {"size": "1.75rem", "color": "#0f172a", "weight": "700"},
                "header": {"size": "1.25rem", "color": "#1e293b", "weight": "600"},
                "content": {"size": "1rem", "color": "#334155", "weight": "400"}
            },
            "theme": "paper",
            "accent": "cobalt"
        }

    @staticmethod
    def _policy_text(value: Any) -> str:
        """Return a normalized policy string."""
        return str(value or "").strip()

    @classmethod
    def normalize_trust_level(cls, value: Any, *, default: str = "untrusted", strict: bool = False) -> str:
        """Normalize an advertised or effective trust level."""
        text = cls._policy_text(value).lower().replace("-", "_")
        aliases = {
            "allow": "trusted",
            "allowed": "trusted",
            "approve": "approved",
            "approved_for_use": "approved",
            "deny": "blocked",
            "denied": "blocked",
            "block": "blocked",
        }
        normalized = aliases.get(text, text)
        if not normalized:
            normalized = default
        if normalized not in cls.TRUST_LEVELS:
            if strict:
                raise ValueError(f"Unsupported trust level '{value}'")
            normalized = default
        return normalized

    @classmethod
    def normalize_trust_scope(cls, value: Any, *, default: str = "unknown") -> str:
        """Normalize the scope that produced a trust decision."""
        text = cls._policy_text(value).lower().replace("-", "_")
        if text in {"agent_id", "agent_name", "agent_address"}:
            return "agent"
        if text in {"owner_id", "owner_user", "owner_user_id", "owner_email", "owner_key"}:
            return "owner"
        return text if text in cls.TRUST_SCOPES else default

    @staticmethod
    def _truthy_flag(value: Any) -> bool:
        """Return whether a loose JSON value is truthy."""
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "on", "trusted", "approved"}

    @classmethod
    def normalize_card_trust_fields(cls, card: Dict[str, Any], *, strict: bool = False) -> Dict[str, Any]:
        """Apply generic Plaza trust defaults to one directory card."""
        normalized_card = dict(card or {})
        meta = normalized_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}

        raw_level = (
            normalized_card.get("trust_level")
            or normalized_card.get("agent_trust_level")
            or meta.get("trust_level")
            or meta.get("agent_trust_level")
        )
        if raw_level in (None, "") and (
            cls._truthy_flag(normalized_card.get("trusted"))
            or cls._truthy_flag(meta.get("trusted"))
        ):
            raw_level = "trusted"
        trust_level = cls.normalize_trust_level(raw_level, strict=strict)
        trust_scope = cls.normalize_trust_scope(
            normalized_card.get("trust_scope")
            or meta.get("trust_scope")
            or ("agent" if raw_level not in (None, "") else "unknown")
        )
        trust_source = cls._policy_text(normalized_card.get("trust_source") or meta.get("trust_source"))
        if not trust_source:
            trust_source = "agent_card" if raw_level not in (None, "") else "default"

        trusted = trust_level in cls.TRUSTED_LEVELS
        meta["trust_level"] = trust_level
        meta["trust_scope"] = trust_scope
        meta["trust_source"] = trust_source
        meta["trusted"] = trusted
        normalized_card["meta"] = meta
        normalized_card["trust_level"] = trust_level
        normalized_card["trust_scope"] = trust_scope
        normalized_card["trust_source"] = trust_source
        normalized_card["trusted"] = trusted
        return normalized_card

    @classmethod
    def validate_card_trust_fields(cls, card: Dict[str, Any]):
        """Validate explicit trust fields from a registration or directory update."""
        cls.normalize_card_trust_fields(card, strict=True)

    @classmethod
    def _normalize_party_permission_action(cls, value: Any, *, default: str = "allow") -> str:
        """Normalize a party registration permission action."""
        text = cls._policy_text(value).lower()
        if text in {"deny", "denied", "block", "blocked", "forbid", "forbidden", "reject", "rejected"}:
            return "deny"
        if text in {"allow", "allowed", "accept", "accepted", "permit", "permitted"}:
            return "allow"
        return "deny" if str(default).lower() == "deny" else "allow"

    @staticmethod
    def _normalize_party_selector_list(value: Any) -> List[Any]:
        """Normalize one-or-many user selectors for party registration rules."""
        if value in (None, "", [], {}, ()):
            return []
        if isinstance(value, (list, tuple, set)):
            return [entry for entry in value if entry not in (None, "", [], {}, ())]
        return [value]

    @classmethod
    def _normalize_party_name_list(cls, value: Any) -> List[str]:
        """Normalize one-or-many Party names for party registration rules."""
        values: List[str] = []
        if value in (None, "", [], {}, ()):
            return values
        if isinstance(value, (list, tuple, set)):
            for item in value:
                values.extend(cls._normalize_party_name_list(item))
        else:
            values.extend(
                cls._policy_text(item)
                for item in re.split(r"[\n,]+", str(value or ""))
                if cls._policy_text(item)
            )
        deduped: List[str] = []
        seen: Set[str] = set()
        for item in values:
            key = item.lower()
            if key and key not in seen:
                deduped.append(item)
                seen.add(key)
        return deduped

    @classmethod
    def _normalize_party_registration_rule(cls, value: Any) -> Dict[str, Any]:
        """Normalize a single Party registration rule."""
        source = dict(value or {}) if isinstance(value, dict) else {}
        parties: List[str] = []
        for key in ("parties", "party_names", "party_list", "party", "party_name"):
            parties.extend(cls._normalize_party_name_list(source.get(key)))
        parties = cls._normalize_party_name_list(parties)
        rule: Dict[str, Any] = {
            "party": parties[0] if parties else "",
            "parties": parties,
            "pit_type": cls._policy_text(source.get("pit_type") or source.get("type") or source.get("agent_type")),
            "role": cls._policy_text(source.get("role") or source.get("agent_role")),
            "action": cls._normalize_party_permission_action(source.get("action") or source.get("default") or source.get("access"), default="allow"),
            "allow_users": [],
            "deny_users": [],
        }
        for key in ("allow_users", "allowed_users", "user_allow", "users_allow", "allow"):
            rule["allow_users"].extend(cls._normalize_party_selector_list(source.get(key)))
        for key in ("deny_users", "denied_users", "user_deny", "users_deny", "deny"):
            rule["deny_users"].extend(cls._normalize_party_selector_list(source.get(key)))
        return rule

    @classmethod
    def _default_party_registration_permissions(cls) -> Dict[str, Any]:
        """Return default Party registration permissions."""
        return {
            "enabled": False,
            "require_party": False,
            "require_owner": False,
            "default_action": "allow",
            "allow_users": [],
            "deny_users": [],
            "parties": {},
            "rules": [],
        }

    @classmethod
    def _normalize_party_registration_party_config(cls, value: Any) -> Dict[str, Any]:
        """Normalize per-Party registration permission settings."""
        source = dict(value or {}) if isinstance(value, dict) else {}
        allow_users: List[Any] = []
        deny_users: List[Any] = []
        for key in ("allow_users", "allowed_users", "user_allow", "users_allow", "allow"):
            allow_users.extend(cls._normalize_party_selector_list(source.get(key)))
        for key in ("deny_users", "denied_users", "user_deny", "users_deny", "deny"):
            deny_users.extend(cls._normalize_party_selector_list(source.get(key)))
        return {
            "default_action": cls._normalize_party_permission_action(
                source.get("default_action") or source.get("default") or source.get("action"),
                default="allow",
            ),
            "allow_users": allow_users,
            "deny_users": deny_users,
        }

    @classmethod
    def _normalize_party_registration_party_configs(cls, value: Any) -> Dict[str, Dict[str, Any]]:
        """Normalize Party keyed registration permission settings."""
        normalized: Dict[str, Dict[str, Any]] = {}
        items: List[Any] = []
        if isinstance(value, dict):
            items = [
                {"party": party_name, **(config if isinstance(config, dict) else {})}
                for party_name, config in value.items()
            ]
        elif isinstance(value, list):
            items = list(value)
        for item in items:
            source = item if isinstance(item, dict) else {}
            party_names = cls._normalize_party_name_list(
                source.get("party") or source.get("party_name") or source.get("name")
            )
            if not party_names:
                continue
            party_name = party_names[0]
            normalized[party_name] = cls._normalize_party_registration_party_config(source)
        return normalized

    @classmethod
    def normalize_party_registration_permissions(cls, value: Any) -> Dict[str, Any]:
        """Normalize Party registration permission settings."""
        if not isinstance(value, dict) or not value:
            return cls._default_party_registration_permissions()

        enabled = value.get("enabled")
        require_party = value.get("require_party")
        require_owner = (
            value.get("require_owner")
            if "require_owner" in value
            else value.get("require_owner_key")
            if "require_owner_key" in value
            else value.get("require_authenticated_user")
        )

        allow_users: List[Any] = []
        deny_users: List[Any] = []
        global_block = value.get("global") if isinstance(value.get("global"), dict) else {}
        for source in (global_block, value):
            for key in ("allow_users", "allowed_users", "user_allow", "users_allow"):
                allow_users.extend(cls._normalize_party_selector_list(source.get(key)))
            for key in ("deny_users", "denied_users", "user_deny", "users_deny"):
                deny_users.extend(cls._normalize_party_selector_list(source.get(key)))

        rules_value = value.get("rules") or value.get("party_rules") or []
        rules: List[Dict[str, Any]] = []
        if isinstance(rules_value, dict):
            rules_value = [
                {"party": party_name, **(rule if isinstance(rule, dict) else {})}
                for party_name, rule in rules_value.items()
            ]
        if isinstance(rules_value, list):
            for item in rules_value:
                rule = cls._normalize_party_registration_rule(item)
                source = item if isinstance(item, dict) else {}
                has_explicit_action = any(key in source for key in ("action", "default", "access"))
                if (
                    rule.get("party")
                    or rule.get("pit_type")
                    or rule.get("role")
                    or rule.get("allow_users")
                    or rule.get("deny_users")
                    or has_explicit_action
                ):
                    rules.append(rule)

        party_configs: Dict[str, Dict[str, Any]] = {}
        for key in ("parties", "party_configs", "party_settings"):
            party_configs.update(cls._normalize_party_registration_party_configs(value.get(key)))

        return {
            "enabled": bool(enabled),
            "require_party": bool(require_party),
            "require_owner": bool(require_owner),
            "default_action": cls._normalize_party_permission_action(
                value.get("default_action") or value.get("default") or value.get("action"),
                default="allow",
            ),
            "allow_users": allow_users,
            "deny_users": deny_users,
            "parties": party_configs,
            "rules": rules,
        }

    def set_party_registration_permissions(self, value: Any) -> Dict[str, Any]:
        """Set normalized Party registration permission settings."""
        self.party_registration_permissions = self.normalize_party_registration_permissions(value)
        return dict(self.party_registration_permissions)

    @staticmethod
    def _party_policy_value_matches(expected: Any, actual: Any) -> bool:
        """Return whether a selector value matches an actual user field."""
        if isinstance(expected, (list, tuple, set)):
            return any(PlazaState._party_policy_value_matches(entry, actual) for entry in expected)
        expected_text = str(expected or "").strip().lower()
        actual_text = str(actual or "").strip().lower()
        if not expected_text:
            return False
        if expected_text == "*":
            return True
        return bool(actual_text and expected_text == actual_text)

    @classmethod
    def _party_user_selector_matches(cls, selector: Any, user: Dict[str, Any]) -> bool:
        """Return whether one selector matches a Plaza UI user."""
        if not isinstance(user, dict) or not user:
            return False
        if isinstance(selector, dict):
            field_aliases = {
                "id": "id",
                "user_id": "id",
                "uid": "id",
                "username": "username",
                "name": "username",
                "email": "email",
                "display_name": "display_name",
                "role": "role",
            }
            compared = False
            for raw_key, raw_value in selector.items():
                canonical_key = field_aliases.get(str(raw_key or "").strip())
                if not canonical_key:
                    continue
                compared = True
                actual_value = user.get(canonical_key)
                if canonical_key == "id" and actual_value in (None, ""):
                    actual_value = user.get("user_id")
                if not cls._party_policy_value_matches(raw_value, actual_value):
                    return False
            return compared

        selector_text = cls._policy_text(selector)
        if not selector_text:
            return False
        if selector_text == "*":
            return True
        return any(
            cls._party_policy_value_matches(selector_text, user.get(field))
            for field in ("id", "user_id", "username", "email", "display_name", "role")
        )

    @classmethod
    def _any_party_user_selector_matches(cls, selectors: List[Any], user: Dict[str, Any]) -> bool:
        """Return whether any selector matches a Plaza UI user."""
        return any(cls._party_user_selector_matches(selector, user) for selector in selectors or [])

    @staticmethod
    def registration_party_from_card(req_party: Any, card: Dict[str, Any], meta: Dict[str, Any]) -> str:
        """Return the Party namespace advertised by a registration payload."""
        return str(
            req_party
            or card.get("party")
            or meta.get("party")
            or ""
        ).strip()

    @staticmethod
    def _party_rule_matches(rule: Dict[str, Any], *, party: str, pit_type: str, role: str) -> bool:
        """Return whether a Party registration rule applies."""
        rule_parties = PlazaState._normalize_party_name_list(rule.get("parties") or rule.get("party"))
        normalized_party = str(party or "").strip().lower()
        rule_pit_type = str(rule.get("pit_type") or "").strip().lower()
        rule_role = str(rule.get("role") or "").strip().lower()
        return (
            (not rule_parties or any(item == "*" or item.lower() == normalized_party for item in rule_parties))
            and (not rule_pit_type or rule_pit_type == str(pit_type or "").strip().lower())
            and (not rule_role or rule_role == str(role or "").strip().lower())
        )

    def evaluate_party_registration_permission(
        self,
        *,
        party: str,
        pit_type: str,
        role: str,
        user: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate whether an agent may register into a Party namespace."""
        settings = self.normalize_party_registration_permissions(self.party_registration_permissions)
        if not settings.get("enabled"):
            return {"allowed": True, "reason": "party registration permissions disabled", "mode": "disabled"}

        party_text = str(party or "").strip()
        pit_type_text = str(pit_type or "").strip()
        role_text = str(role or "").strip()
        user_profile = user if isinstance(user, dict) else {}

        if settings.get("require_party") and not party_text:
            return {"allowed": False, "reason": "registration party is required", "mode": "missing_party"}
        if settings.get("require_owner") and not user_profile:
            return {"allowed": False, "reason": "Plaza user owner is required for party registration", "mode": "missing_owner"}
        if self._any_party_user_selector_matches(settings.get("deny_users") or [], user_profile):
            return {"allowed": False, "reason": "global party registration deny user matched", "mode": "global_deny"}
        if self._any_party_user_selector_matches(settings.get("allow_users") or [], user_profile):
            return {"allowed": True, "reason": "global party registration allow user matched", "mode": "global_allow"}

        party_configs = settings.get("parties") if isinstance(settings.get("parties"), dict) else {}
        party_config = None
        normalized_party_key = party_text.lower()
        for configured_party, configured_settings in party_configs.items():
            if str(configured_party or "").strip().lower() == normalized_party_key:
                party_config = configured_settings if isinstance(configured_settings, dict) else {}
                break
        if party_config is not None:
            if self._any_party_user_selector_matches(party_config.get("deny_users") or [], user_profile):
                return {"allowed": False, "reason": "party registration deny user matched", "mode": "party_deny"}
            if self._any_party_user_selector_matches(party_config.get("allow_users") or [], user_profile):
                return {"allowed": True, "reason": "party registration allow user matched", "mode": "party_allow"}
            action = self._normalize_party_permission_action(
                party_config.get("default_action"),
                default=settings.get("default_action") or "allow",
            )
            return {
                "allowed": action == "allow",
                "reason": f"party registration party default is {action}",
                "mode": "party_default",
            }

        matched_rules = [
            rule for rule in settings.get("rules") or []
            if isinstance(rule, dict) and self._party_rule_matches(rule, party=party_text, pit_type=pit_type_text, role=role_text)
        ]
        for rule in matched_rules:
            if self._any_party_user_selector_matches(rule.get("deny_users") or [], user_profile):
                return {"allowed": False, "reason": "party registration rule deny user matched", "mode": "rule_deny"}
        for rule in matched_rules:
            if self._any_party_user_selector_matches(rule.get("allow_users") or [], user_profile):
                return {"allowed": True, "reason": "party registration rule allow user matched", "mode": "rule_allow"}
        for rule in matched_rules:
            action = self._normalize_party_permission_action(rule.get("action"), default=settings.get("default_action") or "allow")
            return {
                "allowed": action == "allow",
                "reason": f"party registration rule default is {action}",
                "mode": "rule_default",
            }

        default_action = self._normalize_party_permission_action(settings.get("default_action"), default="allow")
        return {
            "allowed": default_action == "allow",
            "reason": f"party registration default is {default_action}",
            "mode": "default",
        }

    @staticmethod
    def _heartbeat_is_active(last_active: Any, *, window_sec: int = 60) -> bool:
        """Return whether a heartbeat timestamp is fresh enough for direct calls."""
        try:
            timestamp = float(last_active or 0)
        except (TypeError, ValueError):
            return False
        if timestamp <= 0:
            return False
        return time.time() - timestamp <= window_sec

    def build_availability_metadata(self, card: Dict[str, Any], last_active: Any) -> Dict[str, Any]:
        """Return directory availability labels for direct and store-forward calls."""
        store_forward = store_forward_summary_from_card(card)
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        accepts_direct = bool(
            card.get(
                "accepts_direct_call",
                card.get("accepts_inbound_from_plaza", meta.get("accepts_inbound_from_plaza", True)),
            )
        )
        connectivity_mode = str(card.get("connectivity_mode") or meta.get("connectivity_mode") or "").strip().lower()
        store_forward_enabled = bool(store_forward.get("enabled"))
        direct_active = bool(
            not store_forward_enabled
            and accepts_direct
            and connectivity_mode != "store-forward"
            and self._heartbeat_is_active(last_active)
        )
        remote_practice_available = bool(direct_active or store_forward_enabled)
        if store_forward_enabled:
            availability = "offline"
        elif direct_active:
            availability = "online"
        else:
            availability = "inactive"
        return {
            "availability": availability,
            "remote_practice_available": remote_practice_available,
            "store_forward": store_forward,
            "store_forward_configured": bool(store_forward.get("configured")),
            "store_forward_enabled": bool(store_forward.get("enabled")),
            "store_forward_poll_enabled": bool(store_forward.get("poll_enabled")),
            "store_forward_payload_transport": store_forward.get("payload_transport") or "plaza",
        }

    def _load_site_settings(self):
        """Internal helper to load the site settings."""
        if not self.directory_pool:
            return
        try:
            loaded = self._load_site_settings_from_pool()
            if isinstance(loaded, dict):
                self.site_settings.update(loaded)
                return
            root_path = getattr(self.directory_pool, "root_path", None)
            if root_path:
                settings_path = os.path.join(root_path, "site_settings.json")
                if os.path.exists(settings_path):
                    with open(settings_path, "r") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            self.site_settings.update(loaded)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load site settings: {e}")

    def _load_site_settings_from_pool(self) -> Optional[Dict[str, Any]]:
        """Load site settings from the configured directory pool when available."""
        if not self.directory_pool:
            return None
        for table_name, where in (
            (self.SITE_SETTINGS_TABLE, {"id": self.SITE_SETTINGS_ROW_ID}),
            ("agent_practices", {"id": self.SITE_SETTINGS_ROW_ID}),
        ):
            try:
                if not self.directory_pool._TableExists(table_name):
                    continue
                rows = self.directory_pool._GetTableData(table_name, where) or []
            except Exception:
                continue
            if not rows:
                continue
            row = dict(rows[-1])
            raw_settings = row.get("settings")
            if raw_settings is None:
                practice_data = row.get("practice_data") if isinstance(row.get("practice_data"), dict) else {}
                raw_settings = practice_data.get("settings")
            settings = self._normalize_site_settings_payload(raw_settings)
            if settings is not None:
                return settings
        return None

    def _normalize_site_settings_payload(self, value: Any) -> Optional[Dict[str, Any]]:
        """Normalize a persisted site settings value."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _save_site_settings_to_pool(self) -> bool:
        """Persist site settings in the configured directory pool."""
        if not self.directory_pool:
            return False
        now = datetime.now(timezone.utc).isoformat()
        if self.directory_pool._TableExists(self.SITE_SETTINGS_TABLE):
            try:
                return bool(
                    self.directory_pool._Insert(
                        self.SITE_SETTINGS_TABLE,
                        {
                            "id": self.SITE_SETTINGS_ROW_ID,
                            "settings": self.site_settings,
                            "updated_at": now,
                        },
                    )
                )
            except Exception:
                pass

        try:
            if not self.directory_pool._TableExists("agent_practices"):
                self.directory_pool._CreateTable("agent_practices", agent_practices_table_schema())
            if self.directory_pool._TableExists("agent_practices"):
                return bool(
                    self.directory_pool._Insert(
                        "agent_practices",
                        {
                            "id": self.SITE_SETTINGS_ROW_ID,
                            "agent_name": "Plaza",
                            "practice_id": self.SITE_SETTINGS_FALLBACK_PRACTICE_ID,
                            "practice_name": "Plaza Site Settings",
                            "practice_description": "Durable Plaza site-wide settings.",
                            "practice_data": {"settings": self.site_settings},
                            "is_deleted": False,
                            "updated_at": now,
                        },
                    )
                )
        except Exception:
            return False
        return False

    def save_site_settings(self, settings: Dict[str, Any]):
        """Save the site settings."""
        def deep_update(d, u):
            """Handle deep update for the Plaza state."""
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = deep_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d

        with self.lock:
            deep_update(self.site_settings, settings)
            if not self.directory_pool:
                return
            persisted_to_pool = self._save_site_settings_to_pool()
            try:
                root_path = getattr(self.directory_pool, "root_path", None)
                if root_path:
                    settings_path = os.path.join(root_path, "site_settings.json")
                    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
                    with open(settings_path, "w") as f:
                        json.dump(self.site_settings, f, indent=2)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to save site settings: {e}")
            if not persisted_to_pool:
                logging.getLogger(__name__).warning("Failed to persist Plaza site settings to the directory pool")

    def _submit_background_task(self, fn, *args, **kwargs):
        """Internal helper to submit the background task."""
        labels = {
            'persist_token': 'Persist agent token',
            'upsert_directory_entry': 'Persist agent directory',
            'upsert_pulse_pulser_pairs': 'Update capability directory',
            '_hydrate_plaza_state': 'Hydrate agent directory',
        }
        agent = getattr(self, 'runtime_agent', None)
        tracked = (coordination_job(self, labels.get(getattr(fn, '__name__', ''), 'Plaza background maintenance'))(fn)
                   if getattr(agent, 'observations', None) else fn)
        future = self._executor.submit(tracked, *args, **kwargs)

        def _log_failure(done_future: concurrent.futures.Future):
            """Internal helper to log the failure."""
            try:
                done_future.result()
            except Exception:
                logging.getLogger(__name__).exception("[Plaza] Background task failed")

        future.add_done_callback(_log_failure)
        return future

    def start_housekeeping_loop(self):
        """Start the housekeeping loop."""
        with self.lock:
            if self._housekeeping_started:
                return
            self._housekeeping_started = True

        def _housekeeping():
            """Internal helper for housekeeping."""
            logger = logging.getLogger(__name__)
            while True:
                try:
                    time.sleep(60)
                    with track_plaza_job(self, 'Plaza housekeeping'):
                        self._run_housekeeping()

                except Exception as e:
                    logger.error(f"[Plaza Housekeeping] Error: {e}")

        threading.Thread(target=_housekeeping, daemon=True, name="PlazaHousekeeping").start()

    def _run_housekeeping(self):
        """Perform one maintenance cycle; the idle wait is not active work."""
        logger = logging.getLogger(__name__)
        now = time.time()
        with self.lock:
            expired_tokens = [t for t, data in self.tokens.items() if data.get("expires_at", 0) < now]
            for t in expired_tokens:
                data = self.tokens.pop(t, None)
                if data and data.get("agent_name"):
                    name = data["agent_name"]
                    if self.agent_tokens.get(name) == t:
                        self.agent_tokens.pop(name, None)
            if expired_tokens:
                logger.info(f"[Plaza Housekeeping] Purged {len(expired_tokens)} expired tokens")

        if now - self._retirement_checked_at >= self.RETIREMENT_CHECK_SECONDS:
            self._retirement_checked_at = now
            retired = self.retire_silent_agents(now)
            if retired:
                logger.info(f"[Plaza Housekeeping] Retired {len(retired)} silent agent(s) from the directory")

        if self.notification_dispatcher.pending_summary_count():
            result = asyncio.run(self.notification_dispatcher.flush_due_summaries())
            if result.get("deliveries"):
                logger.info(
                    "[Plaza Housekeeping] Flushed %s notification summary bucket(s)",
                    len(result.get("deliveries") or []),
                )

    def retire_silent_agents(self, now: Optional[float] = None) -> List[str]:
        """Remove signed-in agents silent past the site's limit from the directory; return their ids.

        Only agents that sign in (they hold a credential) are considered, so pulse definitions, jobs and other
        directory entries stay. The site setting `retire_silent_agents_after_days` changes the limit; 0 turns it off.
        """
        now = float(now or time.time())
        try:
            days = float(self.site_settings.get("retire_silent_agents_after_days", self.RETIRE_SILENT_AGENTS_AFTER_DAYS))
        except (TypeError, ValueError):
            days = float(self.RETIRE_SILENT_AGENTS_AFTER_DAYS)
        remove = getattr(getattr(self, "runtime_agent", None), "_delete_directory_entry", None)
        if days <= 0 or not callable(remove):
            return []
        cutoff = now - days * 86400
        with self.lock:
            silent = [agent_id for agent_id in self.agent_cards
                      if agent_id in self.credentials_by_id and agent_id != getattr(self, "runtime_agent_id", None)
                      and self.pit_types.get(agent_id) not in ("Pulse", "AgentJob")
                      and float(self.last_active.get(agent_id) or 0) < cutoff]
        retired = []
        for agent_id in silent:
            history = self.credential_store.load_login_history(agent_id, limit=1) if self.credential_store else []
            if any(float(row.get("timestamp") or 0) >= cutoff for row in history):
                continue
            with self.lock:
                # A heartbeat may have arrived while the sign-in history was read.
                if float(self.last_active.get(agent_id) or 0) >= cutoff:
                    continue
            try:
                remove(agent_id)
                retired.append(agent_id)
            except Exception as exc:
                logging.getLogger(__name__).warning("[Plaza Housekeeping] Could not retire a silent agent: %s", exc)
        return retired

    def ensure_tokens_table(self):
        """Ensure the tokens table exists."""
        if getattr(self, "_tokens_table_ensured", False):
            return True
        if not self.directory_pool or not self._token_store_available:
            return False
        try:
            if self.directory_pool._TableExists(self.PLAZA_TOKENS_TABLE):
                self._tokens_table_ensured = True
                return True
            schema = {
                "token": "TEXT PRIMARY KEY",
                "agent_id": "TEXT",
                "agent_name": "TEXT",
                "expires_at": "REAL",
                "created_at": "TEXT"
            }
            created = self.directory_pool._CreateTable(self.PLAZA_TOKENS_TABLE, schema)
            if created is False:
                self._token_store_available = False
                return False
            if not self.directory_pool._TableExists(self.PLAZA_TOKENS_TABLE):
                self._token_store_available = False
                return False
            self._tokens_table_ensured = True
            return True
        except Exception:
            self._token_store_available = False
            return False

    def persist_token(self, token: str, payload: Dict[str, Any]):
        """Persist the token."""
        if not self.directory_pool or not self._token_store_available:
            return
        try:
            if not self.ensure_tokens_table():
                return
            inserted = self.directory_pool._Insert(self.PLAZA_TOKENS_TABLE, {
                "token": token,
                "agent_id": payload.get("agent_id", ""),
                "agent_name": payload.get("agent_name", ""),
                "expires_at": float(payload.get("expires_at", 0)),
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            if inserted is False:
                self._token_store_available = False
        except Exception:
            self._token_store_available = False
            pass

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        """Return whether an environment flag is enabled."""
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}

    def _hydrate_plaza_state(self):
        """Internal helper to return the hydrate Plaza state."""
        try:
            if self.directory_pool:
                # Load tokens
                if self._token_store_available and self.directory_pool._TableExists(self.PLAZA_TOKENS_TABLE):
                    now = time.time()
                    token_rows = self.directory_pool._GetTableData(self.PLAZA_TOKENS_TABLE) or []
                    for row in token_rows:
                        token = row.get("token")
                        expires_at = float(row.get("expires_at", 0))
                        if token and expires_at > now:
                            agent_name = row.get("agent_name", "")
                            self.tokens[token] = {
                                "agent_name": agent_name,
                                "agent_id": row.get("agent_id", ""),
                                "expires_at": expires_at
                            }
                            if agent_name:
                                self.agent_tokens[agent_name] = token

                if not self._env_flag("PROMPITS_PLAZA_HYDRATE_DIRECTORY"):
                    return

                # Load directory statuses
                if self.directory_pool._TableExists(self.DIRECTORY_TABLE):
                    dir_rows = self.directory_pool._GetTableData(self.DIRECTORY_TABLE) or []
                    self._begin_directory_persistence_batch()
                    try:
                        for row in dir_rows:
                            agent_id = row.get("agent_id") or row.get("id")
                            if not agent_id:
                                continue
                            card = row.get("card", {})
                            if isinstance(card, str):
                                try:
                                    card = json.loads(card)
                                except Exception:
                                    card = {}
                            if not isinstance(card, dict):
                                card = {}

                            agent_name = row.get("name") or card.get("name")
                            stored_pit_type = self.canonical_pit_type(
                                row.get("type") or card.get("pit_type"),
                                default="Agent",
                            ) or "Agent"
                            pit_type = self.infer_runtime_pit_type(row.get("type"), card, default="Agent")
                            address = row.get("address") or card.get("address")
                            updated_at = PlazaCredentialStore._to_epoch_seconds(row.get("updated_at"))
                            last_active = updated_at or time.time()
                            normalized_card = self.normalize_card_for_pit(
                                card,
                                pit_type,
                                agent_name=str(agent_name or ""),
                                address=str(address or ""),
                            )

                            self.agent_cards.setdefault(agent_id, normalized_card)
                            self.pit_types[agent_id] = pit_type
                            self.last_active[agent_id] = last_active
                            if agent_name:
                                self.agent_names_by_id[agent_id] = agent_name
                                self.agent_ids[agent_name] = agent_id
                            if address:
                                self.registry[agent_id] = address
                                if agent_name:
                                    self.registry_by_name[agent_name] = address
                            if normalized_card != card or pit_type != stored_pit_type:
                                self.upsert_directory_entry(str(agent_id), str(agent_name or ""), str(address or ""), str(pit_type), normalized_card)
                                if pit_type == "Pulser" or self._card_is_pulser_capable(normalized_card):
                                    self.upsert_pulse_pulser_pairs(
                                        str(agent_id),
                                        str(agent_name or ""),
                                        normalized_card.get("pit_address") or address or "",
                                        normalized_card,
                                    )
                    finally:
                        self._end_directory_persistence_batch()
                self.ensure_pulse_directory_entries_from_pair_rows()
        except Exception:
            pass
        finally:
            self.is_starting = False

    def compact_pit_ref(self, value: Any) -> str:
        """Handle compact pit ref for the Plaza state."""
        pit_address = PitAddress.from_value(value)
        return pit_address.to_ref(reference_plaza=self.plaza_url_for_store)

    @staticmethod
    def _normalize_pulse_alias_token(value: Any) -> str:
        """Internal helper to normalize the pulse alias token."""
        return _pulse_alias_token(str(value or ""))

    @classmethod
    def pulse_alias_family(
        cls,
        *,
        pulse_name: Any = None,
        pulse_id: Any = None,
        pulse_address: Any = None,
        title: Any = None,
    ) -> str:
        """Handle pulse alias family for the Plaza state."""
        tokens = {
            cls._normalize_pulse_alias_token(value)
            for value in (pulse_name, pulse_id, pulse_address, title)
        }
        tokens.discard("")
        for family, aliases in cls.PULSE_ALIAS_FAMILIES.items():
            if tokens & aliases:
                return family
        return ""

    @classmethod
    def pulse_identity_tokens(
        cls,
        *,
        pulse_name: Any = None,
        pulse_id: Any = None,
        pulse_address: Any = None,
        title: Any = None,
    ) -> set[str]:
        """Handle pulse identity tokens for the Plaza state."""
        return set(_pulse_identity_tokens(cls, tuple(str(value or "") for value in (pulse_name, pulse_id, pulse_address, title))))

    @classmethod
    def _pulse_identity_tokens_uncached(cls, values: tuple) -> frozenset:
        """The identity tokens of one pulse's name, id, address and title, as text (pulse_identity_tokens)."""
        tokens = {_pulse_alias_token(value) for value in values}
        tokens.discard("")
        family = cls.pulse_alias_family(
            pulse_name=values[0],
            pulse_id=values[1],
            pulse_address=values[2],
            title=values[3],
        )
        if family:
            tokens.add(family)
        return frozenset(tokens)

    def pulse_entries_match(
        self,
        *,
        left_name: Any = None,
        left_id: Any = None,
        left_address: Any = None,
        left_title: Any = None,
        right_name: Any = None,
        right_id: Any = None,
        right_address: Any = None,
        right_title: Any = None,
    ) -> bool:
        """Handle pulse entries match for the Plaza state."""
        left_id_text = str(left_id or "").strip()
        right_id_text = str(right_id or "").strip()
        if left_id_text and right_id_text and left_id_text == right_id_text:
            return True
        if left_address and right_address and self.same_pit_ref(left_address, right_address):
            return True
        left_tokens = self.pulse_identity_tokens(
            pulse_name=left_name,
            pulse_id=left_id,
            pulse_address=left_address,
            title=left_title,
        )
        right_tokens = self.pulse_identity_tokens(
            pulse_name=right_name,
            pulse_id=right_id,
            pulse_address=right_address,
            title=right_title,
        )
        return bool(left_tokens and right_tokens and left_tokens & right_tokens)

    @classmethod
    def canonical_pulse_family_name(
        cls,
        *,
        pulse_name: Any = None,
        pulse_id: Any = None,
        pulse_address: Any = None,
        title: Any = None,
    ) -> str:
        """Return the canonical pulse family name."""
        return cls.pulse_alias_family(
            pulse_name=pulse_name,
            pulse_id=pulse_id,
            pulse_address=pulse_address,
            title=title,
        )

    @staticmethod
    def same_pit_ref(left: Any, right: Any) -> bool:
        """Handle same pit ref for the Plaza state."""
        left_address = PitAddress.from_value(left)
        right_address = PitAddress.from_value(right)
        if left_address.pit_id and right_address.pit_id:
            return left_address.pit_id == right_address.pit_id
        return str(left or "").strip() == str(right or "").strip()

    def _supported_pulse_preference_key(self, entry: Dict[str, Any]) -> tuple:
        """Internal helper to return the supported pulse preference key."""
        pulse_definition = entry.get("pulse_definition") if isinstance(entry.get("pulse_definition"), dict) else {}
        input_schema = entry.get("input_schema") if isinstance(entry.get("input_schema"), dict) else {}
        output_schema = entry.get("output_schema") if isinstance(entry.get("output_schema"), dict) else {}
        test_data = entry.get("test_data") if isinstance(entry.get("test_data"), dict) else {}
        return (
            1 if self.pulse_definition_is_complete(entry) else 0,
            len(output_schema),
            len(input_schema),
            len(test_data),
            len(str(entry.get("description") or "")),
            len(str(pulse_definition.get("title") or "")),
        )

    def _dedupe_supported_pulses(self, supported_pulses: Any) -> List[Dict[str, Any]]:
        """Internal helper for dedupe supported pulses."""
        if not isinstance(supported_pulses, list):
            return []

        deduped: List[Dict[str, Any]] = []
        for entry in supported_pulses:
            if not isinstance(entry, dict):
                continue
            matched_index: Optional[int] = None
            for index, existing in enumerate(deduped):
                if self.pulse_entries_match(
                    left_name=existing.get("pulse_name") or existing.get("name"),
                    left_id=existing.get("pulse_id"),
                    left_address=existing.get("pulse_address"),
                    left_title=(existing.get("pulse_definition") or {}).get("title"),
                    right_name=entry.get("pulse_name") or entry.get("name"),
                    right_id=entry.get("pulse_id"),
                    right_address=entry.get("pulse_address"),
                    right_title=(entry.get("pulse_definition") or {}).get("title"),
                ):
                    matched_index = index
                    break
            if matched_index is None:
                deduped.append(entry)
                continue
            if self._supported_pulse_preference_key(entry) > self._supported_pulse_preference_key(deduped[matched_index]):
                deduped[matched_index] = entry
        return deduped

    @staticmethod
    def _pulser_result_dedupe_key(entry: Dict[str, Any]) -> str:
        """Internal helper to return the pulser result dedupe key."""
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        name = str(entry.get("name") or card.get("name") or "").strip().lower()
        address = str(card.get("address") or entry.get("address") or "").strip().lower()
        agent_id = str(entry.get("agent_id") or card.get("agent_id") or "").strip().lower()
        if name and address:
            return f"{name}|{address}"
        return name or address or agent_id

    def _normalize_pulser_search_result(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper to normalize the pulser search result."""
        resolved_type = self.normalize_pit_type(entry.get("pit_type") or entry.get("type") or "Agent")
        if resolved_type != "Pulser":
            return entry

        meta = entry.get("meta")
        if not isinstance(meta, dict):
            return entry

        supported_pulses = self._dedupe_supported_pulses(meta.get("supported_pulses"))
        if not supported_pulses:
            return entry

        normalized_entry = dict(entry)
        normalized_meta = dict(meta)
        normalized_meta["supported_pulses"] = supported_pulses
        normalized_meta["pulse_id"] = supported_pulses[0].get("pulse_id")
        normalized_meta["pulse_definition"] = dict(supported_pulses[0].get("pulse_definition") or {})
        if not isinstance(normalized_meta.get("input_schema"), dict):
            normalized_meta["input_schema"] = dict(supported_pulses[0].get("input_schema") or {})
        if not normalized_meta.get("pulse_address"):
            normalized_meta["pulse_address"] = supported_pulses[0].get("pulse_address")
        normalized_entry["meta"] = normalized_meta
        return normalized_entry

    def _pulser_result_preference_key(self, entry: Dict[str, Any]) -> tuple:
        """Internal helper to return the pulser result preference key."""
        normalized = self._normalize_pulser_search_result(entry)
        meta = normalized.get("meta") if isinstance(normalized.get("meta"), dict) else {}
        supported_pulses = meta.get("supported_pulses") if isinstance(meta, dict) else []
        return (
            float(normalized.get("last_active") or 0),
            len(supported_pulses) if isinstance(supported_pulses, list) else 0,
            sum(1 for pulse in supported_pulses if isinstance(pulse, dict) and self.pulse_definition_is_complete(pulse))
            if isinstance(supported_pulses, list)
            else 0,
            len(str(normalized.get("description") or "")),
        )

    def _dedupe_search_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Internal helper for dedupe search results."""
        deduped: List[Dict[str, Any]] = []
        pulser_indexes: Dict[str, int] = {}

        for entry in results:
            normalized_entry = self._normalize_pulser_search_result(entry)
            resolved_type = self.normalize_pit_type(normalized_entry.get("pit_type") or normalized_entry.get("type") or "Agent")
            if resolved_type != "Pulser":
                deduped.append(normalized_entry)
                continue

            key = self._pulser_result_dedupe_key(normalized_entry)
            if not key:
                deduped.append(normalized_entry)
                continue

            existing_index = pulser_indexes.get(key)
            if existing_index is None:
                pulser_indexes[key] = len(deduped)
                deduped.append(normalized_entry)
                continue

            existing = deduped[existing_index]
            if self._pulser_result_preference_key(normalized_entry) > self._pulser_result_preference_key(existing):
                deduped[existing_index] = normalized_entry

        return deduped

    def normalize_card_for_pit(
        self,
        card: Dict[str, Any],
        pit_type: str,
        *,
        agent_name: str = "",
        address: str = "",
    ) -> Dict[str, Any]:
        """Normalize the card for the pit."""
        normalized_card = self.normalize_card_trust_fields(dict(card or {}))
        normalized_card["pit_type"] = pit_type
        parents = self.clean_pit_parents(normalized_card.get("pit_types"), pit_type)
        if parents:
            normalized_card["pit_types"] = parents
        else:
            normalized_card.pop("pit_types", None)
        meta = normalized_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}

        if pit_type == "Pulser" or self._card_is_pulser_capable(normalized_card):
            if pit_type == "Pulser":
                normalized_card.setdefault("role", "pulser")
            supported_pulses = meta.get("supported_pulses")
            if isinstance(supported_pulses, list) and supported_pulses:
                default_pulse_address = meta.get("pulse_address")
                normalized_supported = [
                    normalize_runtime_pulse_entry(
                        pulse,
                        default_name=str(pulse.get("name") or ""),
                        default_description=str(pulse.get("description") or normalized_card.get("description") or ""),
                        default_pulse_address=str(pulse.get("pulse_address") or default_pulse_address or ""),
                    )
                    for pulse in supported_pulses
                    if isinstance(pulse, dict)
                ]
                normalized_supported = self._dedupe_supported_pulses(normalized_supported)
                if normalized_supported:
                    meta["supported_pulses"] = normalized_supported
                    meta["pulse_id"] = normalized_supported[0].get("pulse_id")
                    meta["pulse_definition"] = dict(normalized_supported[0].get("pulse_definition") or {})
                    if not isinstance(meta.get("input_schema"), dict):
                        meta["input_schema"] = dict(normalized_supported[0].get("input_schema") or {})
                    if not meta.get("pulse_address"):
                        meta["pulse_address"] = normalized_supported[0].get("pulse_address")
        elif pit_type == "Pulse":
            normalized_pulse = normalize_runtime_pulse_entry(
                meta or normalized_card,
                default_name=str(normalized_card.get("name") or agent_name),
                default_description=str(normalized_card.get("description") or ""),
                default_pulse_address=str(meta.get("pulse_address") or normalized_card.get("address") or address or ""),
            )
            meta["pulse_id"] = normalized_pulse.get("pulse_id")
            meta["pulse_definition"] = dict(normalized_pulse.get("pulse_definition") or {})
            meta["pulse_address"] = normalized_pulse.get("pulse_address")
            meta["input_schema"] = dict(normalized_pulse.get("input_schema") or {})
            meta["output_schema"] = dict(normalized_pulse.get("output_schema") or {})
            meta["description"] = normalized_pulse.get("description") or normalized_card.get("description", "")
            if "examples" not in meta and isinstance(normalized_pulse.get("pulse_definition"), dict):
                meta["examples"] = list(normalized_pulse["pulse_definition"].get("examples") or [])
            normalized_card["description"] = normalized_pulse.get("description") or normalized_card.get("description", "")

        normalized_card["meta"] = meta
        return normalized_card

    @staticmethod
    def _normalize_init_files(init_files: Optional[List[str] | str]) -> List[str]:
        """Internal helper to normalize the init files."""
        if init_files is None:
            return []
        if isinstance(init_files, str):
            return [init_files]
        if isinstance(init_files, (list, tuple, set)):
            return [str(item) for item in init_files if item]
        return []

    @staticmethod
    def stable_pulse_id(name: str) -> str:
        """Handle stable pulse ID for the Plaza state."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"plaza-pulse:{str(name).strip().lower()}"))

    @classmethod
    def is_imported_init_file(cls, path: str) -> bool:
        """Return whether the value is an imported init file."""
        base_name = os.path.basename(str(path or "")).strip().lower()
        return bool(base_name) and base_name.endswith(cls.IMPORTED_INIT_SUFFIX)

    @classmethod
    def is_tagged_init_pulse_file(cls, path: str) -> bool:
        """Return whether the value is a tagged init pulse file."""
        base_name = os.path.basename(str(path or "")).strip().lower()
        if not base_name or not base_name.endswith(".json"):
            return False
        if cls.is_imported_init_file(base_name):
            return False
        if base_name == cls.LEGACY_INIT_PULSE_FILE:
            return True
        stem, _ = os.path.splitext(base_name)
        return stem.startswith(cls.INIT_PULSE_PREFIX) and len(stem) > len(cls.INIT_PULSE_PREFIX)

    def build_pulse_directory_card(
        self,
        payload: Dict[str, Any],
        *,
        default_name: str = "",
        default_description: str = "",
        owner: str = "Plaza",
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Build the pulse directory card."""
        raw_payload = dict(payload or {})
        raw_card = dict(raw_payload.get("card") or {})
        pulse_name = str(
            raw_payload.get("name")
            or raw_payload.get("pulse_name")
            or raw_card.get("name")
            or default_name
            or ""
        ).strip()
        normalized_pulse = normalize_runtime_pulse_entry(
            raw_payload,
            default_name=pulse_name or default_name,
            default_description=str(
                raw_payload.get("description")
                or raw_card.get("description")
                or default_description
                or ""
            ),
            default_pulse_address=str(raw_payload.get("pulse_address") or raw_card.get("address") or ""),
        )

        pulse_name = str(
            normalized_pulse.get("pulse_name")
            or normalized_pulse.get("name")
            or pulse_name
            or default_name
            or "default_pulse"
        ).strip()
        agent_id = self.stable_pulse_id(pulse_name)
        pulse_definition = dict(normalized_pulse.get("pulse_definition") or {})
        pulse_meta = self._normalize_seed_meta(raw_card.get("meta", raw_payload.get("meta")))
        pulse_meta.update({
            "pulse_id": normalized_pulse.get("pulse_id"),
            "pulse_address": normalized_pulse.get("pulse_address"),
            "pulse_definition": pulse_definition,
            "input_schema": normalized_pulse.get("input_schema"),
            "output_schema": normalized_pulse.get("output_schema"),
            "description": normalized_pulse.get("description"),
            "examples": pulse_definition.get("examples", raw_payload.get("examples", [])),
        })

        card = raw_card
        card["name"] = pulse_name
        card["description"] = normalized_pulse.get("description") or str(
            raw_payload.get("description")
            or card.get("description")
            or default_description
            or ""
        )
        card["pit_type"] = "Pulse"
        card["owner"] = str(card.get("owner") or raw_payload.get("owner") or owner)
        tags = raw_payload.get("tags", card.get("tags", []))
        if isinstance(tags, list):
            card["tags"] = list(tags)
        elif tags:
            card["tags"] = [str(tags)]
        else:
            card["tags"] = []
        card["meta"] = pulse_meta

        pit_address = PitAddress.from_value(card.get("pit_address"))
        pit_address.pit_id = agent_id
        if self.plaza_url_for_store:
            pit_address.register_plaza(self.plaza_url_for_store)
        card["pit_address"] = pit_address.to_dict()
        return agent_id, pulse_name, card

    def upsert_pulse_directory_entry(
        self,
        payload: Dict[str, Any],
        *,
        default_name: str = "",
        default_description: str = "",
        owner: str = "Plaza",
    ) -> str:
        """Handle upsert pulse directory entry for the Plaza state."""
        agent_id, agent_name, card = self.build_pulse_directory_card(
            payload,
            default_name=default_name,
            default_description=default_description,
            owner=owner,
        )
        self._remember_directory_entry(agent_id, agent_name, "Pulse", card)
        row = self._build_directory_row(agent_id, agent_name, card.get("address", ""), "Pulse", card)
        if row is not None:
            self._persist_directory_rows([row])
        return agent_id

    def ensure_pulse_directory_entries_from_pair_rows(
        self,
        pair_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Ensure the pulse directory entries from pair rows exists."""
        rows = pair_rows if isinstance(pair_rows, list) else self.get_pulse_pulser_rows()
        if not rows:
            return 0

        existing_ids, existing_name_types = self._load_existing_directory_snapshot()
        restored = 0
        directory_rows: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            payload = dict(row.get("pulse_definition") or {})
            pulse_name = str(
                payload.get("name")
                or payload.get("pulse_name")
                or row.get("pulse_name")
                or ""
            ).strip()
            if not pulse_name:
                continue
            pulse_directory_id = self.stable_pulse_id(pulse_name)
            if pulse_directory_id in existing_ids or (pulse_name, "Pulse") in existing_name_types:
                continue

            payload.setdefault("name", pulse_name)
            payload.setdefault("pulse_name", pulse_name)
            payload.setdefault("pulse_id", row.get("pulse_id"))
            payload.setdefault("pulse_address", row.get("pulse_address"))
            payload.setdefault("input_schema", row.get("input_schema"))
            payload.setdefault("status", row.get("status"))
            if row.get("completion_status") not in (None, ""):
                payload.setdefault("completion_status", row.get("completion_status"))
            if row.get("completion_errors") not in (None, ""):
                payload.setdefault("completion_errors", row.get("completion_errors"))
            if row.get("is_complete") is not None:
                payload.setdefault("is_complete", row.get("is_complete"))

            agent_id, agent_name, card = self.build_pulse_directory_card(
                payload,
                default_name=pulse_name,
                default_description=str(payload.get("description") or ""),
                owner="Plaza",
            )
            self._remember_directory_entry(agent_id, agent_name, "Pulse", card)
            directory_row = self._build_directory_row(agent_id, agent_name, card.get("address", ""), "Pulse", card)
            if directory_row is not None:
                directory_rows.append(directory_row)
            restored += 1
            existing_ids.add(agent_id)
            existing_name_types.add((agent_name, "Pulse"))
        self._persist_directory_rows(directory_rows)
        return restored

    def verify_token(self, creds: HTTPAuthorizationCredentials = Depends(security)):
        """Validate bearer token existence and expiry, returning token payload."""
        token = str(creds.credentials or "")
        with self.lock:
            if token not in self.tokens:
                raise HTTPException(status_code=401, detail=self._auth_error_detail("Invalid token", creds=creds))
            token_data = dict(self.tokens[token])
        if time.time() > token_data["expires_at"]:
            expired_by_seconds = max(time.time() - float(token_data.get("expires_at") or 0), 0.0)
            raise HTTPException(
                status_code=401,
                detail=self._auth_error_detail(
                    "Token expired",
                    creds=creds,
                    token_data=token_data,
                    extra={
                        "expired_by_seconds": round(expired_by_seconds, 3),
                    },
                ),
            )
        validator = getattr(self, 'trusted_agent_validator', None)
        if validator:
            validator(self.agent_cards.get(token_data.get('agent_id'), {}))
        blocker = getattr(self, 'agent_block_reason', None)
        if blocker:
            # A suspended agent, or one whose verified owner account is disabled, is refused on its next call.
            reason = blocker(token_data.get('agent_id'), self.agent_cards.get(token_data.get('agent_id'), {}))
            if reason:
                raise HTTPException(status_code=403, detail=reason)
        return token_data

    @staticmethod
    def _auth_request_parameters(
        creds: Optional[HTTPAuthorizationCredentials] = None,
        *,
        token_data: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build safe, non-secret context for Plaza auth errors."""
        token = str(getattr(creds, "credentials", "") or "")
        parameters: Dict[str, Any] = {
            "auth_scheme": str(getattr(creds, "scheme", "") or "").strip(),
            "token_provided": bool(token),
            "token_length": len(token),
        }
        if isinstance(token_data, dict):
            parameters["agent_name"] = str(token_data.get("agent_name") or "")
            expires_at = float(token_data.get("expires_at") or 0)
            if expires_at > 0:
                parameters["expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value not in (None, ""):
                    parameters[key] = value
        return parameters

    def _auth_error_detail(
        self,
        message: str,
        *,
        creds: Optional[HTTPAuthorizationCredentials] = None,
        token_data: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return structured error detail for Plaza auth failures."""
        return {
            "message": str(message or "").strip(),
            "parameters": self._auth_request_parameters(
                creds,
                token_data=token_data,
                extra=extra,
            ),
        }

    @classmethod
    def canonical_pit_type(cls, pit_type: Optional[str], *, default: str = "Agent") -> Optional[str]:
        """Handle canonical pit type for the Plaza state."""
        raw_value = str(pit_type or default or "").strip()
        if not raw_value:
            return None
        for candidate in cls.SUPPORTED_PIT_TYPES:
            if candidate.lower() == raw_value.lower():
                return candidate
        normalized = raw_value.title()
        for candidate in cls.SUPPORTED_PIT_TYPES:
            if candidate.lower() == normalized.lower():
                return candidate
        return raw_value

    MAX_PIT_PARENTS = 8

    @classmethod
    def clean_pit_parents(cls, value: Any, pit_type: Optional[str]) -> List[str]:
        """Return a card's type chain (its own type first, then its parents), or [] when it names none.

        An agent lists the types it derives from, most specific first, e.g. ``["Consultant", "Persona",
        "Pulser"]``. The chain must start with the agent's registered type. "Agent", the root every agent
        shares, is never listed, so a search for "Agent" keeps matching only agents registered as "Agent".
        """
        own = cls.canonical_pit_type(pit_type, default="") or ""
        if not own or not isinstance(value, list) or not value or len(value) > cls.MAX_PIT_PARENTS:
            return []
        chain: List[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 80:
                return []
            name = cls.canonical_pit_type(item, default="") or ""
            if name and name != "Agent" and name not in chain:
                chain.append(name)
        if not chain or chain[0] != own or len(chain) < 2:
            return []
        return chain

    def pit_type_matches(self, resolved_type: str, card: Optional[Dict[str, Any]], wanted: str) -> bool:
        """Return whether an agent of ``resolved_type`` with ``card`` answers a search for type ``wanted``.

        It matches its own type, any parent its card lists, and (as before) "Pulser" when it serves pulses.
        """
        if resolved_type == wanted:
            return True
        if isinstance(card, dict) and wanted in self.clean_pit_parents(card.get("pit_types"), resolved_type):
            return True
        return wanted == "Pulser" and self._card_is_pulser_capable(card or {})

    @staticmethod
    def _card_role_tokens(card: Dict[str, Any]) -> set[str]:
        """Return normalized role and tag tokens from a card payload."""
        tokens: set[str] = set()
        if not isinstance(card, dict):
            return tokens

        role = str(card.get("role") or "").strip().lower()
        if role:
            tokens.add(role)
        for tag in card.get("tags") or []:
            token = str(tag or "").strip().lower()
            if token:
                tokens.add(token)

        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        meta_role = str(meta.get("role") or "").strip().lower()
        if meta_role:
            tokens.add(meta_role)
        for tag in meta.get("tags") or []:
            token = str(tag or "").strip().lower()
            if token:
                tokens.add(token)
        return tokens

    def _card_is_pulser_capable(self, card: Dict[str, Any]) -> bool:
        """Return whether a card should appear in Pulser discovery."""
        if not isinstance(card, dict):
            return False
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        if self._dedupe_supported_pulses(meta.get("supported_pulses")):
            return True
        return "pulser" in self._card_role_tokens(card) or bool(meta.get("pulser_compatible"))

    def infer_runtime_pit_type(
        self,
        pit_type: Optional[str],
        card: Optional[Dict[str, Any]],
        *,
        default: str = "Agent",
    ) -> str:
        """Infer the effective pit type from explicit type plus card metadata."""
        explicit_type = self.canonical_pit_type(pit_type, default="") or ""
        if explicit_type and explicit_type != "Agent":
            return explicit_type

        inferred_type = ""
        if isinstance(card, dict):
            card_type = self.canonical_pit_type(card.get("pit_type") or card.get("type"), default="") or ""
            if card_type and card_type != "Agent":
                inferred_type = card_type

            meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
            if self._card_is_pulser_capable(card):
                inferred_type = "Pulser"
            elif not inferred_type:
                pulse_definition = meta.get("pulse_definition") if isinstance(meta.get("pulse_definition"), dict) else {}
                if (
                    meta.get("pulse_id")
                    or meta.get("pulse_address")
                    or pulse_definition
                    or meta.get("resource_type") == "pulse_definition"
                ):
                    inferred_type = "Pulse"
                elif any(
                    key in meta or key in card
                    for key in ("rowSchema", "primary_key", "schema_kind", "columns")
                ):
                    inferred_type = "Schema"

        if inferred_type:
            return inferred_type
        if explicit_type:
            return explicit_type
        return self.canonical_pit_type(default, default=default) or default

    def normalize_pit_type(self, pit_type: Optional[str]) -> str:
        """Normalize the pit type."""
        return self.canonical_pit_type(pit_type, default="Agent") or "Agent"

    def record_login_event(self, agent_id: str, agent_name: str, address: str, event: str):
        """Handle record login event for the Plaza state."""
        ts = time.time()
        event_entry = {
            "timestamp": ts,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "address": address,
            "event": event
        }
        if agent_id:
            with self.lock:
                by_id = list(self.login_history_by_id.get(agent_id, []))
                by_id.append(event_entry)
                self.login_history_by_id[agent_id] = by_id[-self.LOGIN_HISTORY_LIMIT:]
                if event == "issued":
                    self._loaded_login_history_ids.add(agent_id)
        if self.credential_store:
            self._submit_background_task(
                self.credential_store.append_login_event,
                agent_id, agent_name, address, event, ts
            )

    @staticmethod
    def _merge_login_history_rows(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Internal helper to merge the login history rows."""
        merged: List[Dict[str, Any]] = []
        seen: set[Tuple[Any, ...]] = set()
        for group in groups:
            for row in group or []:
                item = dict(row)
                key = (
                    str(item.get("id") or ""),
                    str(item.get("agent_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("address") or ""),
                    str(item.get("event") or ""),
                    float(item.get("timestamp") or 0),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        merged.sort(key=lambda r: float(r.get("timestamp", 0)))
        return merged

    def hydrate_login_history_for_id(self, agent_id: str, block: bool = True):
        """Handle hydrate login history for the ID for the Plaza state."""
        if not agent_id:
            return
        loader_event: Optional[threading.Event] = None
        should_start_loader = False
        while True:
            with self.lock:
                if agent_id in self._loaded_login_history_ids:
                    return
                loader_event = self._login_history_load_events.get(agent_id)
                if loader_event is None:
                    loader_event = threading.Event()
                    self._login_history_load_events[agent_id] = loader_event
                    should_start_loader = True
                    break
            if not block:
                return
            loader_event.wait()

        if should_start_loader:
            self._submit_background_task(self._run_hydrate_login_history, agent_id, loader_event)
        if block:
            loader_event.wait()

    def _run_hydrate_login_history(self, agent_id: str, loader_event: threading.Event):
        """Internal helper to run the hydrate login history."""
        rows: List[Dict[str, Any]] = []
        try:
            if self.credential_store:
                rows = self.credential_store.load_login_history(agent_id, limit=self.LOGIN_HISTORY_LIMIT)
        finally:
            with self.lock:
                current_rows = list(self.login_history_by_id.get(agent_id, []))
                self.login_history_by_id[agent_id] = self._merge_login_history_rows(
                    rows,
                    current_rows,
                )[-self.LOGIN_HISTORY_LIMIT:]
                self._loaded_login_history_ids.add(agent_id)
                self._login_history_load_events.pop(agent_id, None)
                loader_event.set()

    def ensure_directory_table(self):
        """Ensure the directory table exists."""
        if getattr(self, "_directory_table_ensured", False):
            return
        if not self.directory_pool:
            return
        if self.directory_pool._TableExists(self.DIRECTORY_TABLE):
            self._directory_table_ensured = True
            return
        schema = plaza_directory_table_schema()
        self.directory_pool._CreateTable(self.DIRECTORY_TABLE, schema)
        self._directory_table_ensured = True

    def ensure_pulse_pulser_table(self):
        """Ensure pulse pairs can persist their normalized pricing fields."""
        if getattr(self, "_pulse_pulser_table_ensured", False):
            return
        if not self.directory_pool:
            return
        with self.lock:
            if getattr(self, "_pulse_pulser_table_ensured", False):
                return
            if not self.directory_pool._TableExists(self.PULSE_PULSER_TABLE):
                schema = pulse_pulser_pairs_table_schema()
                self.directory_pool._CreateTable(self.PULSE_PULSER_TABLE, schema)
            if self.directory_pool.__class__.__name__ == "SQLitePool":
                self._ensure_sqlite_pulse_pricing_columns()
            self._pulse_pulser_table_ensured = True

    def _ensure_sqlite_pulse_pricing_columns(self):
        """Add missing pricing columns without replacing existing pulse rows."""
        pool = self.directory_pool
        required = {"cost_points": "INTEGER", "pricing": "TEXT", "cost_calculation": "TEXT"}
        columns = {row[1] for row in pool._Query(f"PRAGMA table_info({self.PULSE_PULSER_TABLE})")}
        for name, sql_type in required.items():
            if name not in columns:
                pool._Query(f"ALTER TABLE {self.PULSE_PULSER_TABLE} ADD COLUMN {name} {sql_type}")
        # SQLitePool reports query failures as empty results. Verify the
        # migration before caching success so a failed attempt can retry.
        columns = {row[1] for row in pool._Query(f"PRAGMA table_info({self.PULSE_PULSER_TABLE})")}
        missing = required.keys() - columns
        if missing:
            raise RuntimeError(f"Pulse-pulser pricing migration is incomplete: {', '.join(sorted(missing))}")

    def _remember_directory_entry(self, agent_id: str, agent_name: str, pit_type: str, card: Dict[str, Any]):
        """Internal helper to remember the directory entry."""
        self.agent_cards[agent_id] = card
        self.pit_types[agent_id] = pit_type
        self.agent_ids[agent_name] = agent_id
        self.agent_names_by_id[agent_id] = agent_name
        self.last_active[agent_id] = time.time()

    def _build_directory_row(
        self,
        agent_id: str,
        agent_name: str,
        address: str,
        pit_type: str,
        card: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Internal helper to build the directory row."""
        if not agent_id:
            return None
        entry_card = dict(card or {})
        meta = entry_card.get("meta", {})
        if not isinstance(meta, (dict, list)):
            meta = {"value": meta}
        return {
            "id": agent_id,
            "agent_id": agent_id,
            "name": entry_card.get("name") or agent_name or agent_id,
            "type": pit_type,
            "description": entry_card.get("description", ""),
            "owner": entry_card.get("owner") or agent_name or "",
            "address": address or entry_card.get("address", ""),
            "meta": meta,
            "card": entry_card,
            "trust_level": entry_card.get("trust_level") or (meta.get("trust_level") if isinstance(meta, dict) else "untrusted"),
            "trust_scope": entry_card.get("trust_scope") or (meta.get("trust_scope") if isinstance(meta, dict) else "unknown"),
            "trust_source": entry_card.get("trust_source") or (meta.get("trust_source") if isinstance(meta, dict) else "default"),
            "trusted": bool(entry_card.get("trusted") or (meta.get("trusted") if isinstance(meta, dict) else False)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _persist_directory_rows(self, rows: List[Dict[str, Any]]):
        """Internal helper to persist the directory rows."""
        if not self.directory_pool or not rows:
            return
        if self._directory_persistence_batch_depth > 0:
            for row in rows:
                row_id = str(row.get("id") or row.get("agent_id") or "").strip()
                if row_id:
                    self._pending_directory_rows[row_id] = dict(row)
            return
        try:
            self.ensure_directory_table()
            if len(rows) == 1:
                self.directory_pool._Insert(self.DIRECTORY_TABLE, rows[0])
                return
            self.directory_pool._InsertMany(self.DIRECTORY_TABLE, rows)
        except Exception:
            pass

    def _begin_directory_persistence_batch(self):
        """Internal helper for begin directory persistence batch."""
        self._directory_persistence_batch_depth = max(int(self._directory_persistence_batch_depth), 0) + 1

    def _end_directory_persistence_batch(self):
        """Internal helper for end directory persistence batch."""
        current_depth = max(int(self._directory_persistence_batch_depth), 0)
        if current_depth == 0:
            return
        self._directory_persistence_batch_depth = current_depth - 1
        if self._directory_persistence_batch_depth == 0:
            rows = list((self._pending_directory_rows or {}).values())
            self._pending_directory_rows = {}
            if rows:
                self._persist_directory_rows(rows)

    def upsert_directory_entry(self, agent_id: str, agent_name: str, address: str, pit_type: str, card: Dict[str, Any]):
        """Handle upsert directory entry for the Plaza state."""
        row = self._build_directory_row(agent_id, agent_name, address, pit_type, card)
        if row is not None:
            self._persist_directory_rows([row])

    def upsert_pulse_pulser_pairs(
        self,
        pulser_id: str,
        pulser_name: str,
        pulser_address: Any,
        card: Dict[str, Any],
        pulse_pulser_pairs: Optional[List[Dict[str, Any]]] = None,
    ):
        """Handle upsert pulse pulser pairs for the Plaza state."""
        if not self.directory_pool or not pulser_id:
            return

        card_meta = card.get("meta", {})
        if not isinstance(card_meta, dict):
            card_meta = {}

        default_pulse_address = card_meta.get("pulse_address")
        default_input_schema = card_meta.get("input_schema")
        supported_pulses = card_meta.get("supported_pulses")
        if not isinstance(supported_pulses, list):
            supported_pulses = []
        explicit_pairs = pulse_pulser_pairs if isinstance(pulse_pulser_pairs, list) else card_meta.get("pulse_pulser_pairs")
        if not isinstance(explicit_pairs, list):
            explicit_pairs = []

        entries: List[Dict[str, Any]] = []
        for pulse in supported_pulses:
            if not isinstance(pulse, dict):
                continue
            entries.append(dict(pulse))

        for pair in explicit_pairs:
            if not isinstance(pair, dict):
                continue
            entries.append(dict(pair))

        if not entries:
            return

        self.ensure_pulse_pulser_table()
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            effective_pulser_id = str(entry.get("pulser_id") or pulser_id or "").strip()
            if not effective_pulser_id:
                continue
            effective_pulser_name = str(entry.get("pulser_name") or pulser_name or "").strip()
            effective_pulser_address = entry.get("pulser_address") or pulser_address
            pulse_name = str(entry.get("pulse_name") or entry.get("name") or "").strip()
            pulse_address_value = entry.get("pulse_address") or default_pulse_address or (f"plaza://pulse/{pulse_name}" if pulse_name else "")
            compact_pulse_address = (
                self.compact_pit_ref(pulse_address_value)
                if PitAddress.from_value(pulse_address_value).pit_id
                else str(pulse_address_value)
            )
            normalized_pair = normalize_pulse_pair_entry(
                entry,
                pulser_id=effective_pulser_id,
                pulser_name=effective_pulser_name,
                pulser_address=self.compact_pit_ref(effective_pulser_address),
                default_name=pulse_name or str(card.get("name") or pulser_name),
                default_description=str(entry.get("description") or card.get("description") or ""),
                default_pulse_address=compact_pulse_address,
            )
            if not normalized_pair.get("pulse_name"):
                continue
            input_schema = normalized_pair.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = default_input_schema if isinstance(default_input_schema, dict) else {}
                normalized_pair["input_schema"] = input_schema

            pulse_id = str(normalized_pair.get("pulse_id") or "").strip()
            pulse_name = str(normalized_pair.get("pulse_name") or "").strip()
            pulse_address = str(normalized_pair.get("pulse_address") or "").strip()
            pulse_directory_id = str(
                normalized_pair.get("pulse_directory_id")
                or (self.stable_pulse_id(pulse_name) if pulse_name else "")
            ).strip()
            pulser_directory_id = str(
                normalized_pair.get("pulser_directory_id") or effective_pulser_id
            ).strip()
            if pulse_directory_id:
                normalized_pair["pulse_directory_id"] = pulse_directory_id
            if pulser_directory_id:
                normalized_pair["pulser_directory_id"] = pulser_directory_id
            row_id_basis = f"{pulse_name}:{pulse_address}" if pulse_name and pulse_address else (pulse_id or pulse_name or pulse_address)
            row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{effective_pulser_id}:{row_id_basis}"))
            normalized_pair["id"] = row_id
            normalized_pair["updated_at"] = datetime.now(timezone.utc).isoformat()
            rows_by_id[row_id] = normalized_pair
        rows = list(rows_by_id.values())
        if not rows:
            return
        try:
            self.directory_pool._InsertMany(self.PULSE_PULSER_TABLE, rows)
        except Exception:
            pass
        self.ensure_pulse_directory_entries_from_pair_rows(rows)

    def lookup_pulser_ids(self, pulse_id: Optional[str] = None, pulse_name: Optional[str] = None, pulse_address: Optional[str] = None) -> Optional[set[str]]:
        """Look up the pulser IDs."""
        if not self.directory_pool or (not pulse_id and not pulse_name and not pulse_address):
            return None
        try:
            rows = self.get_pulse_pulser_rows()
        except Exception:
            return None
        with self.lock:
            agent_cards_snapshot = dict(self.agent_cards)

        matched_ids: set[str] = set()
        for row in rows:
            if not self.pulse_entries_match(
                left_name=row.get("pulse_name"),
                left_id=row.get("pulse_id"),
                left_address=row.get("pulse_address"),
                right_name=pulse_name,
                right_id=pulse_id,
                right_address=pulse_address,
            ):
                continue
            pulser_id = row.get("pulser_id")
            if not pulser_id:
                continue
            card = agent_cards_snapshot.get(str(pulser_id), {})
            if not self._is_supported_pulse_complete(card, pulse_id, pulse_name, pulse_address):
                continue
            matched_ids.add(str(pulser_id))
        return matched_ids

    def get_pulse_pulser_rows(self) -> List[Dict[str, Any]]:
        """Return the pulse pulser rows."""
        if not self.directory_pool:
            return []
        try:
            self.ensure_pulse_pulser_table()
            rows = self.directory_pool._GetTableData(self.PULSE_PULSER_TABLE) or []
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    @staticmethod
    def contains(haystack: Any, needle: Optional[str]) -> bool:
        """Return whether the value contains value."""
        if not needle:
            return True
        return needle.lower() in str(haystack or "").lower()

    @staticmethod
    def pulse_definition_is_complete(entry: Any) -> bool:
        """Handle pulse definition is complete for the Plaza state."""
        if not isinstance(entry, dict):
            return True
        if entry.get("is_complete") is False:
            return False
        status = str(entry.get("completion_status") or entry.get("status") or "").strip().lower()
        if status in {"unfinished", "incomplete", "invalid"}:
            return False
        return True

    def resolve_supported_pulse_entry(
        self,
        supported_pulses: Any,
        *,
        pulse_id: Optional[str] = None,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve the supported pulse entry."""
        if not isinstance(supported_pulses, list):
            return None
        for entry in supported_pulses:
            if not isinstance(entry, dict):
                continue
            entry_pulse_id = entry.get("pulse_id")
            entry_name = entry.get("pulse_name") or entry.get("name")
            entry_address = entry.get("pulse_address")
            if (pulse_id or pulse_name or pulse_address) and not self.pulse_entries_match(
                left_name=entry_name,
                left_id=entry_pulse_id,
                left_address=entry_address,
                left_title=entry.get("title"),
                right_name=pulse_name,
                right_id=pulse_id,
                right_address=pulse_address,
            ):
                continue
            return entry
        return None

    def _is_supported_pulse_complete(self, card: Dict[str, Any], pulse_id: Optional[str], pulse_name: Optional[str], pulse_address: Optional[str]) -> bool:
        """Return whether the value is a supported pulse complete."""
        matched = self.resolve_supported_pulse_entry(
            card.get("meta", {}).get("supported_pulses") if isinstance(card.get("meta"), dict) else [],
            pulse_id=pulse_id,
            pulse_name=pulse_name,
            pulse_address=pulse_address,
        )
        if not isinstance(matched, dict):
            return True
        return self.pulse_definition_is_complete(matched)

    def search_entries(
        self,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        type: Optional[str] = None,
        description: Optional[str] = None,
        owner: Optional[str] = None,
        meta: Optional[str] = None,
        role: Optional[str] = None,
        practice: Optional[str] = None,
        pit_type: Optional[str] = None,
        pulse_id: Optional[str] = None,
        pulse_name: Optional[str] = None,
        pulse_address: Optional[str] = None,
        party: Optional[str] = None,
        use_persisted_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search the entries."""
        effective_type_filter = pit_type or type
        normalized_filter = self.normalize_pit_type(effective_type_filter) if effective_type_filter else None
        has_filters = any([
            name,
            agent_id,
            type,
            description,
            owner,
            meta,
            role,
            practice,
            pit_type,
            pulse_id,
            pulse_name,
            pulse_address,
            party,
        ])
        pulser_rows = self.get_pulse_pulser_rows() if (pulse_id or pulse_name or pulse_address) else []
        matched_pulser_ids: Optional[set[str]] = None
        if pulse_id or pulse_name or pulse_address:
            matched_pulser_ids = set()
            for row in pulser_rows:
                if not self.pulse_entries_match(
                    left_name=row.get("pulse_name"),
                    left_id=row.get("pulse_id"),
                    left_address=row.get("pulse_address"),
                    right_name=pulse_name,
                    right_id=pulse_id,
                    right_address=pulse_address,
                ):
                    continue
                pulser_id = row.get("pulser_id")
                if pulser_id:
                    matched_pulser_ids.add(str(pulser_id))
        if (pulse_id or pulse_name or pulse_address) and normalized_filter is None:
            normalized_filter = "Pulser"

        results: List[Dict[str, Any]] = []
        with self.lock:
            agent_cards_snapshot = list(self.agent_cards.items())
            agent_names_by_id = dict(self.agent_names_by_id)
            pit_types = dict(self.pit_types)
            last_active = dict(self.last_active)

        blocker = getattr(self, 'agent_block_reason', None)
        for aid, acard in agent_cards_snapshot:
            if blocker and blocker(aid, acard):
                continue  # suspended agents and those of disabled accounts leave the directory
            display_name = acard.get("name") or agent_names_by_id.get(aid) or aid
            resolved_type = self.infer_runtime_pit_type(pit_types.get(aid), acard, default="Agent")
            entry_owner = acard.get("owner") or display_name
            entry_desc = acard.get("description", "")
            emeta = acard.get("meta", {})
            if not isinstance(emeta, (dict, list)):
                emeta = {"value": emeta}

            if agent_id and aid != agent_id:
                continue
            if name and not self.contains(display_name, name):
                continue
            if role and acard.get("role") != role:
                continue
            if normalized_filter and not self.pit_type_matches(resolved_type, acard, normalized_filter):
                continue
            if matched_pulser_ids is not None and aid not in matched_pulser_ids:
                continue
            if pulse_id or pulse_name or pulse_address:
                supported_pulses = emeta.get("supported_pulses") if isinstance(emeta, dict) else []
                matched_pulse = self.resolve_supported_pulse_entry(
                    supported_pulses,
                    pulse_id=pulse_id,
                    pulse_name=pulse_name,
                    pulse_address=pulse_address,
                )
                if matched_pulse is not None and not self.pulse_definition_is_complete(matched_pulse):
                    continue
            if description and not self.contains(entry_desc, description):
                continue
            if owner and not self.contains(entry_owner, owner):
                continue
            if meta and not self.contains(emeta, meta):
                continue
            if practice:
                practices_list = acard.get("practices", [])
                if not any(p.get("id") == practice for p in practices_list):
                    continue
            if party and acard.get("party") != party:
                continue
            if agent_id:
                self.hydrate_login_history_for_id(aid, block=False)
            with self.lock:
                login_history = list(self.login_history_by_id.get(aid, []))
            resolved_last_active = last_active.get(aid, 0)
            availability = self.build_availability_metadata(acard, resolved_last_active)

            results.append({
                "name": display_name,
                "card": acard,
                "pit_type": resolved_type,
                "type": resolved_type,
                "description": entry_desc,
                "owner": entry_owner,
                "meta": emeta,
                "trust_level": acard.get("trust_level") or (emeta.get("trust_level") if isinstance(emeta, dict) else "untrusted"),
                "trust_scope": acard.get("trust_scope") or (emeta.get("trust_scope") if isinstance(emeta, dict) else "unknown"),
                "trust_source": acard.get("trust_source") or (emeta.get("trust_source") if isinstance(emeta, dict) else "default"),
                "trusted": bool(acard.get("trusted") or (emeta.get("trusted") if isinstance(emeta, dict) else False)),
                "last_active": resolved_last_active,
                "agent_id": aid,
                "accepts_inbound_from_plaza": bool(acard.get("accepts_inbound_from_plaza", True)),
                "accepts_direct_call": bool(acard.get("accepts_direct_call", acard.get("accepts_inbound_from_plaza", True))),
                "connectivity_mode": str(
                    acard.get("connectivity_mode")
                    or ("plaza-forward" if acard.get("accepts_inbound_from_plaza", True) else "outbound-only")
                ),
                "login_history": login_history,
                **availability,
            })

        should_fallback_to_pool = (
            use_persisted_fallback
            and bool(self.directory_pool)
            and (not results or len(agent_cards_snapshot) <= 1 or not has_filters)
        )
        if not should_fallback_to_pool:
            return results if agent_id else self._dedupe_search_results(results)

        directory_rows = []
        try:
            self.ensure_directory_table()
            directory_rows = self.directory_pool._GetTableData(self.DIRECTORY_TABLE) or []
        except Exception:
            directory_rows = []

        for row in directory_rows:
            aid = str(row.get("agent_id") or row.get("id") or "")
            if not aid:
                continue

            raw_card = row.get("card", {})
            if isinstance(raw_card, str):
                try:
                    parsed_card = json.loads(raw_card)
                    acard = parsed_card if isinstance(parsed_card, dict) else {}
                except Exception:
                    acard = {}
            elif isinstance(raw_card, dict):
                acard = raw_card
            else:
                acard = {}

            raw_meta = row.get("meta", {})
            if isinstance(raw_meta, str):
                try:
                    parsed_meta = json.loads(raw_meta)
                    emeta = parsed_meta if isinstance(parsed_meta, (dict, list)) else {"value": parsed_meta}
                except Exception:
                    emeta = {"value": raw_meta}
            elif isinstance(raw_meta, (dict, list)):
                emeta = raw_meta
            else:
                emeta = {"value": raw_meta}

            display_name = acard.get("name") or row.get("name") or agent_names_by_id.get(aid) or aid
            resolved_type = self.infer_runtime_pit_type(
                row.get("type") or pit_types.get(aid),
                acard,
                default="Agent",
            )
            entry_owner = row.get("owner") or acard.get("owner") or display_name
            entry_desc = row.get("description") or acard.get("description", "")
            if isinstance(acard, dict):
                acard = self.normalize_card_for_pit(
                    acard,
                    resolved_type,
                    agent_name=display_name,
                    address=str(acard.get("address") or row.get("address") or ""),
                )
                normalized_meta = acard.get("meta")
                if isinstance(normalized_meta, (dict, list)):
                    emeta = normalized_meta

            if agent_id and aid != agent_id:
                continue
            if name and not self.contains(display_name, name):
                continue
            if role and acard.get("role") != role:
                continue
            if normalized_filter and not self.pit_type_matches(resolved_type, acard, normalized_filter):
                continue
            if matched_pulser_ids is not None and aid not in matched_pulser_ids:
                continue
            if pulse_id or pulse_name or pulse_address:
                supported_pulses = emeta.get("supported_pulses") if isinstance(emeta, dict) else []
                matched_pulse = self.resolve_supported_pulse_entry(
                    supported_pulses,
                    pulse_id=pulse_id,
                    pulse_name=pulse_name,
                    pulse_address=pulse_address,
                )
                if matched_pulse is not None and not self.pulse_definition_is_complete(matched_pulse):
                    continue
            if description and not self.contains(entry_desc, description):
                continue
            if owner and not self.contains(entry_owner, owner):
                continue
            if meta and not self.contains(emeta, meta):
                continue
            if practice:
                practices_list = acard.get("practices", [])
                if not any(p.get("id") == practice for p in practices_list):
                    continue
            if party and acard.get("party") != party:
                continue

            fallback_active = 0
            if aid not in last_active:
                upd = row.get("updated_at")
                if isinstance(upd, (int, float)):
                    fallback_active = float(upd)
                elif isinstance(upd, str):
                    try:
                        fallback_active = datetime.fromisoformat(upd).timestamp()
                    except Exception:
                        pass
            if agent_id:
                self.hydrate_login_history_for_id(aid, block=False)
            with self.lock:
                login_history = list(self.login_history_by_id.get(aid, []))
            resolved_last_active = last_active.get(aid, fallback_active)
            availability = self.build_availability_metadata(acard, resolved_last_active)

            if any(existing.get("agent_id") == aid for existing in results):
                continue
            results.append({
                "name": display_name,
                "card": acard,
                "pit_type": resolved_type,
                "type": resolved_type,
                "description": entry_desc,
                "owner": entry_owner,
                "meta": emeta,
                "trust_level": acard.get("trust_level") or (emeta.get("trust_level") if isinstance(emeta, dict) else "untrusted"),
                "trust_scope": acard.get("trust_scope") or (emeta.get("trust_scope") if isinstance(emeta, dict) else "unknown"),
                "trust_source": acard.get("trust_source") or (emeta.get("trust_source") if isinstance(emeta, dict) else "default"),
                "trusted": bool(acard.get("trusted") or (emeta.get("trusted") if isinstance(emeta, dict) else False)),
                "last_active": resolved_last_active,
                "agent_id": aid,
                "login_history": login_history,
                "accepts_inbound_from_plaza": bool(acard.get("accepts_inbound_from_plaza", True)),
                "accepts_direct_call": bool(acard.get("accepts_direct_call", acard.get("accepts_inbound_from_plaza", True))),
                "connectivity_mode": str(
                    acard.get("connectivity_mode")
                    or ("plaza-forward" if acard.get("accepts_inbound_from_plaza", True) else "outbound-only")
                ),
                **availability,
            })

        return results if agent_id else self._dedupe_search_results(results)

    @staticmethod
    def _string_values(value: Any) -> List[str]:
        """Return normalized string values from a scalar or list-like value."""
        if value in (None, "", [], {}, ()):
            return []
        if isinstance(value, (list, tuple, set)):
            values: List[str] = []
            for item in value:
                values.extend(PlazaState._string_values(item))
            return values
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _dispatcher_address_from_entry(entry: Any) -> str:
        """Return the advertised address from one dispatcher directory entry."""
        if not isinstance(entry, dict):
            return ""
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        for candidate in (entry.get("address"), card.get("address"), entry.get("pit_address")):
            normalized = str(candidate or "").strip().rstrip("/")
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _dispatcher_entry_party(entry: Any) -> str:
        """Return the Party name from one dispatcher entry."""
        if not isinstance(entry, dict):
            return ""
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        return str(card.get("party") or meta.get("party") or entry.get("party") or "").strip()

    @classmethod
    def _dispatcher_candidate_sort_key(cls, entry: Any) -> Tuple[int, int, int, int, float]:
        """Return a preference key for one dispatcher candidate."""
        if not isinstance(entry, dict):
            return (0, 0, 0, 0, 0.0)
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        practices = card.get("practices") if isinstance(card.get("practices"), list) else []
        practice_ids = {
            str(practice.get("id") or "").strip().lower()
            for practice in practices
            if isinstance(practice, dict)
        }
        role = str(card.get("role") or meta.get("role") or entry.get("role") or "").strip().lower()
        tags = {
            tag.lower()
            for tag in cls._string_values(card.get("tags")) + cls._string_values(meta.get("tags"))
        }
        name = str(entry.get("name") or card.get("name") or "").strip().lower()
        try:
            last_active = float(entry.get("last_active") or 0.0)
        except (TypeError, ValueError):
            last_active = 0.0
        return (
            1 if role == "dispatcher" else 0,
            sum(1 for practice_id in DISPATCHER_DISCOVERY_PRACTICES if practice_id in practice_ids),
            1 if "dispatcher" in tags else 0,
            1 if name == "dispatcher" else 0,
            last_active,
        )

    def dispatcher_directory(self, *, party: Optional[str] = None) -> Dict[str, Any]:
        """Return dispatcher candidates without requiring caller registration."""
        selected_party = str(party or "").strip()
        search_plans = (
            {"role": "dispatcher", "practice": "dispatcher-get-job", "pit_type": "Agent"},
            {"role": "dispatcher", "practice": "dispatcher-register-worker", "pit_type": "Agent"},
            {"role": "dispatcher", "pit_type": "Agent"},
            {"practice": "dispatcher-get-job", "pit_type": "Agent"},
            {"name": "Dispatcher", "pit_type": "Agent"},
        )
        candidates: Dict[str, Dict[str, Any]] = {}
        for search_params in search_plans:
            for entry in self.search_entries(**search_params, use_persisted_fallback=False):
                address = self._dispatcher_address_from_entry(entry)
                if not address:
                    continue
                candidate_key = str(entry.get("agent_id") or address)
                existing = candidates.get(candidate_key)
                if existing is None or self._dispatcher_candidate_sort_key(entry) > self._dispatcher_candidate_sort_key(existing):
                    candidates[candidate_key] = dict(entry)

        normalized_entries: List[Dict[str, Any]] = []
        for entry in candidates.values():
            card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
            try:
                last_active = float(entry.get("last_active") or 0.0)
            except (TypeError, ValueError):
                last_active = 0.0
            normalized_entries.append(
                {
                    "agent_id": str(entry.get("agent_id") or card.get("agent_id") or "").strip(),
                    "name": str(entry.get("name") or card.get("name") or "Dispatcher").strip() or "Dispatcher",
                    "address": self._dispatcher_address_from_entry(entry),
                    "party": self._dispatcher_entry_party(entry),
                    "role": str(card.get("role") or entry.get("role") or "").strip(),
                    "description": str(card.get("description") or entry.get("description") or "").strip(),
                    "last_active": last_active,
                }
            )
        normalized_entries.sort(
            key=lambda item: self._dispatcher_candidate_sort_key({"card": {"role": item["role"], "tags": ["dispatcher"]}, **item}),
            reverse=True,
        )
        parties = sorted({entry["party"] for entry in normalized_entries if entry.get("party")})
        filtered = [
            entry
            for entry in normalized_entries
            if not selected_party or entry.get("party") == selected_party
        ]
        selected_address = str(filtered[0].get("address") or "").strip() if filtered else ""
        return {
            "status": "success" if selected_address else "not_found",
            "dispatcher_party": selected_party,
            "dispatcher_address": selected_address,
            "selected_dispatcher_address": selected_address,
            "parties": parties,
            "dispatchers": filtered,
        }

    def hydrate_credentials_from_pool(self):
        """Handle hydrate credentials from pool for the Plaza state."""
        if not self.credential_store or not self.credential_store.pool:
            return
        self.credential_store._ensure_plaza_credentials_table()
        rows = self.credential_store.pool._GetTableData(self.credential_store.PLAZA_CREDENTIALS_TABLE) or []
        for row in rows:
            agent_id = row.get("agent_id")
            api_key = row.get("api_key")
            agent_name = row.get("agent_name")
            if not agent_id or not api_key:
                continue
            with self.lock:
                self.credentials_by_id[agent_id] = {
                    "api_key": api_key,
                    "created_at": row.get("updated_at", time.time())
                }
                if agent_name:
                    self.agent_names_by_id[agent_id] = agent_name

    def persist_credential_to_pool(self, agent_name: str, agent_id: str, api_key: str):
        """Persist the credential to pool."""
        if not self.credential_store:
            return
        self._submit_background_task(
            self.credential_store.save,
            agent_name, agent_id, api_key, self.plaza_url_for_store
        )

    def persist_token_async(self, token: str, payload: Dict[str, Any]):
        """Persist the token async."""
        self._submit_background_task(self.persist_token, token, payload)

    def upsert_directory_entry_async(
        self,
        agent_id: str,
        agent_name: str,
        address: str,
        pit_type: str,
        card: Dict[str, Any],
    ):
        """Handle upsert directory entry async for the Plaza state."""
        self._submit_background_task(
            self.upsert_directory_entry,
            agent_id,
            agent_name,
            address,
            pit_type,
            card,
        )

    def upsert_pulse_pulser_pairs_async(
        self,
        pulser_id: str,
        pulser_name: str,
        pulser_address: Any,
        card: Dict[str, Any],
        pulse_pulser_pairs: Optional[List[Dict[str, Any]]] = None,
    ):
        """Handle upsert pulse pulser pairs async for the Plaza state."""
        self._submit_background_task(
            self.upsert_pulse_pulser_pairs,
            pulser_id,
            pulser_name,
            pulser_address,
            card,
            pulse_pulser_pairs,
        )

    def start_self_heartbeat_loop(self, agent_id: str, agent_name: Optional[str] = None):
        """Start the self heartbeat loop."""
        if self._self_heartbeat_started:
            return
        self._self_heartbeat_started = True

        def _beat():
            """Internal helper for beat."""
            while True:
                with track_plaza_job(self, 'Refresh Plaza presence'), self.lock:
                    self.last_active[agent_id] = time.time()
                    if agent_name:
                        self.last_active[agent_name] = self.last_active[agent_id]
                time.sleep(self.self_heartbeat_interval)

        threading.Thread(target=_beat, daemon=True).start()

    def bootstrap_plaza_agent(self, agent: Any):
        """Handle bootstrap Plaza agent for the Plaza state."""
        if agent is None:
            return
        self.runtime_agent = agent
        self.trusted_agent_validator = getattr(agent, '_authorize_trusted_agent_token', None)
        self.agent_block_reason = getattr(agent, '_agent_block_reason', None)
        agent_name = getattr(agent, "name", None)
        agent_card = dict(getattr(agent, "agent_card", {}) or {})
        if not agent_name or not agent_card:
            return
        address = agent_card.get("address")
        agent_id = agent_card.get("agent_id") or f"plaza:{address or agent_name}"
        self.runtime_agent_id = agent_id
        service = getattr(agent, 'observability', None)
        if service is not None:
            self.observation_store = service.store
        if address:
            self.registry[agent_id] = address
            self.registry_by_name[agent_name] = address
        pit_type = self.normalize_pit_type(agent_card.get("pit_type", "Agent"))
        agent_card["pit_type"] = pit_type
        agent_card["agent_id"] = agent_id
        agent_card = self.normalize_card_for_pit(
            agent_card,
            pit_type,
            agent_name=str(agent_name or ""),
            address=str(address or ""),
        )
        self.agent_cards[agent_id] = agent_card
        self.pit_types[agent_id] = pit_type
        self.agent_ids[agent_name] = agent_id
        self.agent_names_by_id[agent_id] = agent_name
        self.last_active[agent_id] = time.time()
        self.last_active[agent_name] = self.last_active[agent_id]
        self.start_self_heartbeat_loop(agent_id, agent_name=agent_name)
        self.plaza_url_for_store = address or f"http://{getattr(agent, 'host', '127.0.0.1')}:{getattr(agent, 'port', 8000)}"
        self.credential_store = PlazaCredentialStore(pool=getattr(agent, "pool", None))
        self.directory_pool = getattr(agent, "pool", None)
        self.agent_config_store = AgentConfigStore(pool=self.directory_pool)
        raw_config = getattr(agent, "raw_config", None)
        if isinstance(raw_config, dict):
            providers, settings = extract_notification_config(raw_config)
            if providers or settings:
                self.notification_dispatcher.configure(
                    provider_configs=providers,
                    settings=settings,
                    merge=True,
                )
        self.hydrate_credentials_from_pool()
        self._begin_directory_persistence_batch()
        try:
            self.upsert_directory_entry(agent_id, agent_name, address, pit_type, agent_card)
            if pit_type == "Pulser" or self._card_is_pulser_capable(agent_card):
                self.upsert_pulse_pulser_pairs(agent_id, agent_name, address or "", agent_card)
            self.bootstrap_builtin_schemas()
            self.bootstrap_init_pits()
            self.bootstrap_agent_configs()
        finally:
            self._end_directory_persistence_batch()

        # Start background hydration and housekeeping
        self._submit_background_task(self._hydrate_plaza_state)
        self.start_housekeeping_loop()

    def bootstrap_builtin_schemas(self):
        """Handle bootstrap builtin schemas for the Plaza state."""
        plaza_url = self.plaza_url_for_store or ""
        for entry in builtin_schema_cards(plaza_url):
            schema_id = str(entry.get("schema_id") or "")
            card = dict(entry.get("card") or {})
            schema_name = card.get("name") or entry.get("name") or schema_id
            if not schema_id or schema_id in self.agent_cards:
                continue

            # Register schema pit in in-memory index and optional persisted directory.
            self.agent_cards[schema_id] = card
            self.pit_types[schema_id] = "Schema"
            self.agent_ids[schema_name] = schema_id
            self.agent_names_by_id[schema_id] = schema_name
            self.last_active[schema_id] = time.time()
            self.upsert_directory_entry(schema_id, schema_name, card.get("address", ""), "Schema", card)

    def _iter_init_files(self) -> List[str]:
        """Internal helper for iter init files."""
        files: List[str] = []
        for source in self.init_files:
            if not source:
                continue
            if os.path.isdir(source):
                for name in sorted(os.listdir(source)):
                    if name.startswith("."):
                        continue
                    if self.is_tagged_init_pulse_file(name):
                        files.append(os.path.join(source, name))
            elif os.path.isfile(source):
                if self.is_imported_init_file(source):
                    continue
                files.append(source)
        return files

    def _iter_agent_config_files(self) -> List[str]:
        """Internal helper for iter agent config files."""
        files: List[str] = []
        seen: set[str] = set()
        candidate_directories: List[str] = []

        if self.config_dir and os.path.isdir(self.config_dir):
            candidate_directories.append(self.config_dir)

        for source in self.init_files:
            if not source:
                continue
            if os.path.isdir(source):
                candidate_directories.append(source)
            elif os.path.isfile(source) and str(source).lower().endswith(self.AGENT_CONFIG_FILE_SUFFIXES):
                absolute = os.path.abspath(source)
                if absolute not in seen:
                    seen.add(absolute)
                    files.append(absolute)

        for directory in candidate_directories:
            try:
                names = sorted(os.listdir(directory))
            except Exception:
                continue
            for name in names:
                if name.startswith(".") or not name.lower().endswith(self.AGENT_CONFIG_FILE_SUFFIXES):
                    continue
                absolute = os.path.abspath(os.path.join(directory, name))
                if not os.path.isfile(absolute) or absolute in seen:
                    continue
                seen.add(absolute)
                files.append(absolute)

        return files

    def _load_init_entries(self, file_path: str) -> List[Tuple[Dict[str, Any], Optional[str]]]:
        """Internal helper to load the init entries."""
        try:
            with open(file_path, "r") as handle:
                payload = json.load(handle)
        except Exception:
            return []

        file_level_type = None
        if isinstance(payload, dict):
            file_level_type = (
                payload.get("PitType")
                or payload.get("pit_type")
                or payload.get("Type")
                or payload.get("type")
            )
            data = payload.get("data")
            if isinstance(data, list):
                return [(item, file_level_type) for item in data if isinstance(item, dict)]

        if isinstance(payload, dict):
            return [(payload, file_level_type)]
        if isinstance(payload, list):
            return [(item, None) for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_seed_meta(value: Any) -> Dict[str, Any]:
        """Internal helper to normalize the seed meta."""
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return {"items": value}
        if value in (None, ""):
            return {}
        return {"value": value}

    def _infer_seed_pit_type(self, seed: Dict[str, Any], file_path: str, default_pit_type: Optional[str] = None) -> str:
        """Internal helper for infer seed pit type."""
        explicit = default_pit_type or seed.get("PitType") or seed.get("pit_type") or seed.get("Type") or seed.get("type")
        if explicit:
            return self.normalize_pit_type(explicit)
        if seed.get("resource_type") == "pulse_definition":
            return "Pulse"
        if "pulse" in os.path.basename(file_path).lower():
            return "Pulse"
        if any(key in seed for key in ("output_schema", "input_schema", "rowSchema", "primary_key", "schema_kind")):
            return "Schema"
        return "Agent"

    def _resolve_seed_id(self, seed: Dict[str, Any], file_path: str, pit_type: str, card: Dict[str, Any]) -> str:
        """Internal helper to resolve the seed ID."""
        for value in (
            seed.get("schema_id"),
            seed.get("agent_id"),
            seed.get("id"),
            (seed.get("pit_address") or {}).get("pit_id") if isinstance(seed.get("pit_address"), dict) else None,
            (card.get("pit_address") or {}).get("pit_id") if isinstance(card.get("pit_address"), dict) else None,
            card.get("agent_id"),
        ):
            if value:
                return str(value)

        seed_name = card.get("name") or seed.get("name") or os.path.splitext(os.path.basename(file_path))[0]
        seed_basis = f"{os.path.basename(file_path)}:{pit_type}:{seed_name}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed_basis))

    def _load_existing_directory_snapshot(self) -> Tuple[set[str], set[Tuple[str, str]]]:
        """Internal helper to load the existing directory snapshot."""
        existing_ids: set[str] = set()
        existing_name_types: set[Tuple[str, str]] = set()

        for existing_id, existing_card in self.agent_cards.items():
            existing_name = str(self.agent_names_by_id.get(existing_id) or existing_card.get("name") or "").strip()
            existing_type = self.canonical_pit_type(
                self.pit_types.get(existing_id) or existing_card.get("pit_type"),
                default="Agent",
            ) or "Agent"
            if existing_id:
                existing_ids.add(str(existing_id))
            if existing_name and existing_type:
                existing_name_types.add((existing_name, existing_type))

        if not self.directory_pool or not self._env_flag("PROMPITS_PLAZA_HYDRATE_DIRECTORY"):
            return existing_ids, existing_name_types

        try:
            self.ensure_directory_table()
            rows = self.directory_pool._GetTableData(self.DIRECTORY_TABLE) or []
        except Exception:
            return existing_ids, existing_name_types

        for row in rows:
            row_id = row.get("agent_id") or row.get("id")
            row_name = str(row.get("name") or "").strip()
            raw_card = row.get("card", {})
            if isinstance(raw_card, str):
                try:
                    parsed_card = json.loads(raw_card)
                    row_card = parsed_card if isinstance(parsed_card, dict) else {}
                except Exception:
                    row_card = {}
            elif isinstance(raw_card, dict):
                row_card = raw_card
            else:
                row_card = {}
            row_type = self.infer_runtime_pit_type(row.get("type"), row_card, default="Agent")
            if row_id:
                existing_ids.add(str(row_id))
            if row_name and row_type:
                existing_name_types.add((row_name, row_type))

        return existing_ids, existing_name_types

    def _build_seed_card(self, seed: Dict[str, Any], file_path: str, pit_type: str) -> Dict[str, Any]:
        """Internal helper to build the seed card."""
        card = dict(seed.get("card") or {})

        for key in ("name", "description", "owner", "address", "role"):
            if key in seed and key not in card:
                card[key] = seed.get(key)

        seed_name = card.get("name") or seed.get("name") or os.path.splitext(os.path.basename(file_path))[0]
        card["name"] = seed_name
        card.setdefault("description", seed.get("description", ""))

        tags = card.get("tags", seed.get("tags", []))
        if isinstance(tags, list):
            card["tags"] = list(tags)
        elif tags:
            card["tags"] = [str(tags)]
        else:
            card["tags"] = []

        card["pit_type"] = pit_type
        card["meta"] = self._normalize_seed_meta(card.get("meta", seed.get("meta")))
        return card

    def bootstrap_init_pits(self):
        """Handle bootstrap init pits for the Plaza state."""
        existing_ids, existing_name_types = self._load_existing_directory_snapshot()
        for file_path in self._iter_init_files():
            for seed, default_pit_type in self._load_init_entries(file_path):
                card = self._build_seed_card(seed, file_path, self._infer_seed_pit_type(seed, file_path, default_pit_type))
                pit_type = card.get("pit_type", "Agent")
                agent_id = self._resolve_seed_id(seed, file_path, pit_type, card)
                agent_name = card.get("name") or agent_id

                if pit_type == "Pulse":
                    pulse_payload = dict(seed)
                    pulse_payload["card"] = dict(card)
                    agent_id, agent_name, card = self.build_pulse_directory_card(
                        pulse_payload,
                        default_name=str(agent_name),
                        default_description=str(seed.get("description") or card.get("description") or ""),
                        owner=str(card.get("owner") or seed.get("owner") or "Plaza"),
                    )

                # Pulse seeds represent canonical shared schemas and should refresh
                # existing directory entries on startup when the source seed changes.
                if pit_type != "Pulse" and (agent_id in existing_ids or (agent_name, pit_type) in existing_name_types):
                    continue

                if pit_type != "Pulse":
                    pit_address = PitAddress.from_value(card.get("pit_address"))
                    pit_address.pit_id = agent_id
                    if self.plaza_url_for_store:
                        pit_address.register_plaza(self.plaza_url_for_store)
                    card["pit_address"] = pit_address.to_dict()

                if pit_type == "Schema":
                    card.setdefault("owner", seed.get("owner") or "Plaza")
                    schema_meta = self._normalize_seed_meta(card.get("meta"))
                    schema_meta.setdefault("schema_name", seed.get("schema_name") or agent_name)
                    schema_meta.setdefault("schema_kind", seed.get("schema_kind") or "data")
                    if "schema" not in schema_meta:
                        schema_payload = dict(seed)
                        schema_payload.pop("card", None)
                        schema_meta["schema"] = schema_payload
                    card["meta"] = schema_meta

                self.agent_cards[agent_id] = card
                self.pit_types[agent_id] = pit_type
                self.agent_ids.setdefault(agent_name, agent_id)
                self.agent_names_by_id[agent_id] = agent_name
                self.last_active[agent_id] = time.time()
                self.upsert_directory_entry(agent_id, agent_name, card.get("address", ""), pit_type, card)
                existing_ids.add(agent_id)
                existing_name_types.add((agent_name, pit_type))

    def bootstrap_agent_configs(self):
        """Return the bootstrap agent configs."""
        store = self.agent_config_store
        if store is None or not self.directory_pool:
            return

        for file_path in self._iter_agent_config_files():
            try:
                with open(file_path, "r") as handle:
                    payload = json.load(handle)
            except Exception:
                continue

            if not store.looks_like_config_payload(payload):
                continue

            agent_card = payload.get("agent_card")
            if not isinstance(agent_card, dict):
                agent_card = {}

            resolved_name = str(payload.get("name") or os.path.splitext(os.path.basename(file_path))[0])
            resolved_owner = str(
                payload.get("owner")
                or agent_card.get("owner")
                or "Plaza"
            )
            resolved_description = str(
                payload.get("description")
                or agent_card.get("description")
                or ""
            )

            try:
                saved = store.upsert(
                    payload,
                    name=resolved_name,
                    owner=resolved_owner,
                    description=resolved_description,
                )
            except Exception as exc:
                # Agent-config discovery should never prevent Plaza from starting.
                # Keep a searchable in-memory entry and let persistence recover later.
                sanitized = store.sanitize_config(payload)
                logging.getLogger(__name__).warning(
                    "[Plaza] Failed persisting bootstrapped agent config '%s' from %s; "
                    "keeping in-memory entry only: %s",
                    resolved_name,
                    file_path,
                    exc,
                )
                saved = {
                    "id": store._row_id(name=resolved_name),
                    "name": resolved_name,
                    "description": resolved_description,
                    "owner": resolved_owner,
                    "role": store._derived_role(sanitized),
                    "agent_type": store._as_text(sanitized.get("type")),
                    "tags": store._derived_tags(sanitized),
                }
                store._apply_store_forward_summary(
                    saved,
                    store._config_store_forward_summary(sanitized),
                )

            agent_id = str(saved.get("id") or "")
            agent_name = str(saved.get("name") or agent_id)
            card_meta = {
                "resource_type": "agent_config",
                "agent_type": str(saved.get("agent_type") or ""),
                "config_id": agent_id,
            }
            store_forward = saved.get("store_forward") if isinstance(saved.get("store_forward"), dict) else {}
            if store_forward.get("configured"):
                card_meta["store_forward"] = dict(store_forward)
                card_meta["store_forward_configured"] = bool(saved.get("store_forward_configured", store_forward.get("configured")))
                card_meta["store_forward_enabled"] = bool(saved.get("store_forward_enabled", store_forward.get("enabled")))
                card_meta["store_forward_poll_enabled"] = bool(saved.get("store_forward_poll_enabled", store_forward.get("poll_enabled")))
                card_meta["store_forward_payload_transport"] = saved.get("store_forward_payload_transport") or store_forward.get("payload_transport") or "plaza"
            card = {
                "name": agent_name,
                "description": str(saved.get("description") or ""),
                "owner": str(saved.get("owner") or "Plaza"),
                "role": str(saved.get("role") or ""),
                "tags": list(saved.get("tags") or []),
                "pit_type": "AgentConfig",
                "meta": card_meta,
            }
            self._remember_directory_entry(agent_id, agent_name, "AgentConfig", card)


class PlazaEndpointPractice(Practice):
    """
    Base class for individual Plaza HTTP endpoints implemented as practices.

    All endpoint practices share the same `PlazaState` instance to guarantee
    consistent state across register/search/auth/heartbeat/relay flows.
    """

    def __init__(
        self,
        state: PlazaState,
        name: str,
        description: str,
        id: str,
        tags: Optional[List[str]] = None,
        examples: Optional[List[str]] = None,
        input_modes: Optional[List[str]] = None,
        output_modes: Optional[List[str]] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the Plaza endpoint practice."""
        super().__init__(
            name=name,
            description=description,
            id=id,
            tags=tags or ["core", "plaza"],
            examples=examples or [],
            inputModes=input_modes or ["http-post", "json"],
            outputModes=output_modes or ["json"],
            parameters=parameters or {},
        )
        self.state = state


class PlazaRegisterPractice(PlazaEndpointPractice):
    """Endpoint practice for agent registration and identity issuance/relogin."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza register practice."""
        super().__init__(
            state,
            "Plaza Register",
            "Register agents with Plaza.",
            "register",
            examples=[
                "POST /register {'agent_name':'alice','address':'http://127.0.0.1:8012','accepts_inbound_from_plaza':true}"
            ],
            parameters={
                "agent_name": {"type": "string", "description": "Agent display name."},
                "address": {"type": "string", "description": "HTTP base URL for this agent."},
                "expires_in": {"type": "integer", "description": "Token TTL in seconds."},
                "pit_type": {"type": "string", "description": "Agent type string. Plaza normalizes built-ins but also accepts custom values."},
                "card": {"type": "object", "description": "Optional agent card payload."},
                "pulse_pulser_pairs": {"type": "array", "description": "Optional batch of pulse-pulser pair rows to index in the same request."},
                "agent_id": {"type": "string", "description": "Optional existing identity for relogin."},
                "api_key": {"type": "string", "description": "Optional existing secret for relogin."},
                "owner_key": {"type": "string", "description": "Optional Plaza UI owner key used to claim agent ownership for a signed-in user."},
                "accepts_inbound_from_plaza": {"type": "boolean", "description": "Whether Plaza can directly open inbound HTTP connections to this agent."},
            },
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()

        @router.post("/register")
        @coordination_job(self.state, 'Register agent')
        async def register(req: RegisterRequest, request: Request):
            """Route handler for POST /register."""
            def _register_sync():
                if self.state.is_starting:
                    raise HTTPException(status_code=503, detail="Starting")
                card = dict(req.card or {})
                card_meta = card.get("meta")
                if not isinstance(card_meta, dict):
                    card_meta = {}
                    card["meta"] = card_meta
                try:
                    self.state.validate_card_trust_fields(card)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
                if "native_authority" in card_meta:
                    # The agent's own operation authority (prompits/core/native_authority.py): read on each grant,
                    # never pinned. A malformed one is refused here rather than silently ignored later.
                    from prompits.core.native_authority import validate_card as validate_native_authority
                    try:
                        card_meta["native_authority"] = validate_native_authority(card_meta["native_authority"])
                    except (ValueError, TypeError) as exc:
                        raise HTTPException(status_code=400, detail=str(exc))
                owner_key = str(req.owner_key or "").strip()
                for container in (card, card_meta):
                    if not isinstance(container, dict):
                        continue
                    for key_name in ("plaza_owner_key", "owner_key", "plaza_owner_key_secret", "owner_key_secret"):
                        if not owner_key:
                            owner_key = str(container.get(key_name) or "").strip()
                        container.pop(key_name, None)
                provided_id = (req.agent_id or "").strip()
                provided_key = (req.api_key or "").strip()
                known_relogin_credentials = False
                if provided_id or provided_key:
                    if not (provided_id and provided_key):
                        raise HTTPException(status_code=400, detail="Both agent_id and api_key are required together")
                    with self.state.lock:
                        existing_creds = self.state.credentials_by_id.get(provided_id)
                    if existing_creds and existing_creds.get("api_key") != provided_key:
                        raise HTTPException(status_code=401, detail="Invalid agent_id or api_key")
                    known_relogin_credentials = bool(existing_creds and existing_creds.get("api_key") == provided_key)
                blocker = getattr(self.state, 'agent_block_reason', None)
                if blocker and provided_id and blocker(provided_id):
                    raise HTTPException(status_code=403, detail=blocker(provided_id))
                owner_context = None
                if owner_key:
                    owner_resolver = getattr(
                        self.agent,
                        "_resolve_agent_owner_from_key_cached",
                        getattr(self.agent, "_resolve_agent_owner_from_key", None),
                    )
                    if not callable(owner_resolver):
                        raise HTTPException(status_code=501, detail="Owner keys are unavailable for this Plaza")
                    owner_context = owner_resolver(owner_key)
                pit_type = self.state.infer_runtime_pit_type(req.pit_type, card, default="Agent")
                registration_party = self.state.registration_party_from_card(req.party, card, card_meta)
                if registration_party:
                    card.setdefault("party", registration_party)
                    card_meta.setdefault("party", registration_party)
                registration_role = str(card.get("role") or card_meta.get("role") or "").strip()
                bearer_owner_context = None
                if owner_context is None and str(request.headers.get("authorization") or "").strip():
                    ui_context_resolver = getattr(self.agent, "_get_authenticated_ui_context", None)
                    if callable(ui_context_resolver):
                        _, _, bearer_owner_context = ui_context_resolver(request)
                trust_registration = getattr(self.agent, '_authorize_agent_trust_registration', None)
                if trust_registration:
                    trust_registration(card, bearer_owner_context, self.state.agent_cards.get(provided_id))
                permission_user = owner_context or bearer_owner_context
                party_decision = self.state.evaluate_party_registration_permission(
                    party=registration_party,
                    pit_type=pit_type,
                    role=registration_role,
                    user=permission_user,
                )
                if not party_decision.get("allowed"):
                    raise HTTPException(status_code=403, detail=party_decision.get("reason") or "Party registration denied")
                issued_new_identity = False
                accepts_inbound = req.accepts_inbound_from_plaza
                if accepts_inbound is None:
                    accepts_inbound = card.get("accepts_inbound_from_plaza")
                if accepts_inbound is None:
                    accepts_inbound = card_meta.get("accepts_inbound_from_plaza")
                accepts_inbound = True if accepts_inbound is None else bool(accepts_inbound)
                store_forward = store_forward_summary_from_card(card)
                store_forward_enabled = bool(store_forward.get("enabled"))

                with self.state.lock:
                    if provided_id or provided_key:
                        if not (provided_id and provided_key):
                            raise HTTPException(status_code=400, detail="Both agent_id and api_key are required together")
                        creds = self.state.credentials_by_id.get(provided_id)
                        if creds and creds.get("api_key") != provided_key:
                            raise HTTPException(status_code=401, detail="Invalid agent_id or api_key")
                        if not creds:
                            self.state.credentials_by_id[provided_id] = {"api_key": provided_key, "created_at": time.time()}
                        agent_id = provided_id
                        api_key = provided_key
                        login_event = "relogin"
                    else:
                        agent_id = str(uuid.uuid4())
                        api_key = secrets.token_urlsafe(32)
                        self.state.credentials_by_id[agent_id] = {"api_key": api_key, "created_at": time.time()}
                        issued_new_identity = True
                        login_event = "issued"

                    ownership_store = getattr(self.agent, '_registered_agent_owners', None)
                    verified_registration_owner = None
                    if callable(ownership_store):
                        authenticated_owner = str((owner_context or {}).get('user_id') or
                                                  (bearer_owner_context or {}).get('id') or '') or None
                        verified_registration_owner = ownership_store().bind(agent_id, api_key, authenticated_owner)
                        if verified_registration_owner:
                            for container in (card, card_meta):
                                container['owner_user_id'] = verified_registration_owner
                                container['owner_id'] = verified_registration_owner
                            card_meta['owner_source'] = 'authenticated_registration'
                    if blocker and blocker(agent_id, card):
                        raise HTTPException(status_code=403, detail=blocker(agent_id, card))

                    token = str(uuid.uuid4())
                    self.state.registry[agent_id] = req.address
                    self.state.registry_by_name[req.agent_name] = req.address
                    expires_at = time.time() + req.expires_in
                    token_payload = {"agent_name": req.agent_name, "agent_id": agent_id, "expires_at": expires_at}
                    self.state.tokens[token] = token_payload
                    self.state.agent_tokens[req.agent_name] = token

                    card.setdefault("name", req.agent_name)
                    card.setdefault("address", req.address)
                    card["pit_type"] = pit_type
                    card["agent_id"] = agent_id
                    if owner_context:
                        owner_label = str(owner_context.get("owner_label") or "").strip()
                        if owner_label:
                            card["owner"] = owner_label
                        card_meta["owner_source"] = "ui_agent_key"
                        card_meta["owner_user_id"] = str(owner_context.get("user_id") or "")
                        card_meta["owner_username"] = str(owner_context.get("username") or "")
                        card_meta["owner_display_name"] = str(owner_context.get("display_name") or "")
                        card_meta["owner_email"] = str(owner_context.get("email") or "")
                        card_meta["plaza_owner_key_id"] = str(owner_context.get("key_id") or card_meta.get("plaza_owner_key_id") or "")
                    card["accepts_inbound_from_plaza"] = accepts_inbound
                    card["accepts_direct_call"] = accepts_inbound
                    card["store_forward_configured"] = bool(store_forward.get("configured"))
                    card["store_forward_enabled"] = store_forward_enabled
                    card["connectivity_mode"] = "plaza-forward" if accepts_inbound else "store-forward" if store_forward_enabled else "outbound-only"
                    card_meta["accepts_inbound_from_plaza"] = accepts_inbound
                    card_meta["accepts_direct_call"] = accepts_inbound
                    card_meta["store_forward_configured"] = bool(store_forward.get("configured"))
                    card_meta["store_forward_enabled"] = store_forward_enabled
                    card_meta["connectivity_mode"] = card["connectivity_mode"]
                    if pit_type == "Pulser" or self.state._card_is_pulser_capable(card):
                        supported_pulses = card_meta.get("supported_pulses")
                        if isinstance(supported_pulses, list) and supported_pulses:
                            default_pulse_address = card_meta.get("pulse_address")
                            normalized_supported = [
                                normalize_runtime_pulse_entry(
                                    pulse,
                                    default_name=str(pulse.get("name") or ""),
                                    default_description=str(pulse.get("description") or card.get("description") or ""),
                                    default_pulse_address=str(pulse.get("pulse_address") or default_pulse_address or ""),
                                )
                                for pulse in supported_pulses
                                if isinstance(pulse, dict)
                            ]
                            normalized_supported = self.state._dedupe_supported_pulses(normalized_supported)
                            if normalized_supported:
                                card_meta["supported_pulses"] = normalized_supported
                                card_meta["pulse_id"] = normalized_supported[0].get("pulse_id")
                                card_meta["pulse_definition"] = dict(normalized_supported[0].get("pulse_definition") or {})
                                if not isinstance(card_meta.get("input_schema"), dict):
                                    card_meta["input_schema"] = dict(normalized_supported[0].get("input_schema") or {})
                                if not card_meta.get("pulse_address"):
                                    card_meta["pulse_address"] = normalized_supported[0].get("pulse_address")
                    elif pit_type == "Pulse":
                        normalized_pulse = normalize_runtime_pulse_entry(
                            card_meta,
                            default_name=str(card.get("name") or req.agent_name),
                            default_description=str(card.get("description") or ""),
                            default_pulse_address=str(card_meta.get("pulse_address") or card.get("address") or req.address or ""),
                        )
                        card_meta["pulse_id"] = normalized_pulse.get("pulse_id")
                        card_meta["pulse_definition"] = dict(normalized_pulse.get("pulse_definition") or {})
                        card_meta["input_schema"] = dict(normalized_pulse.get("input_schema") or {})
                        card_meta["output_schema"] = dict(normalized_pulse.get("output_schema") or {})
                        card_meta["description"] = normalized_pulse.get("description") or card.get("description", "")
                    pit_address = PitAddress.from_value(card.get("pit_address"))
                    pit_address.pit_id = agent_id
                    if self.state.plaza_url_for_store:
                        pit_address.register_plaza(self.state.plaza_url_for_store)
                    card["pit_address"] = pit_address.to_dict()
                    card = self.state.normalize_card_for_pit(
                        card,
                        pit_type,
                        agent_name=req.agent_name,
                        address=req.address,
                    )
                    from prompits.core.native_authority import describe_change as native_authority_change
                    authority_change = native_authority_change(self.state.agent_cards.get(agent_id), card)
                    self.state.agent_cards[agent_id] = card
                    self.state.pit_types[agent_id] = pit_type
                    self.state.agent_ids[req.agent_name] = agent_id
                    self.state.agent_names_by_id[agent_id] = req.agent_name
                    self.state.last_active[agent_id] = time.time()

                if authority_change:
                    # Every change of an agent's signing key, ACL path, decider or offered contracts is kept in its
                    # login history, which its owner sees; Plaza accepts the new registration either way.
                    logging.getLogger(__name__).warning("[Plaza] Agent %s (%s): %s", req.agent_name, agent_id, authority_change)
                    self.state.record_login_event(agent_id=agent_id, agent_name=req.agent_name, address=req.address,
                                                  event=authority_change[:500])
                self.state.persist_token_async(token, token_payload)
                self.state.persist_credential_to_pool(req.agent_name, agent_id, api_key)
                self.state.record_login_event(agent_id=agent_id, agent_name=req.agent_name, address=req.address, event=login_event)
                self.state.upsert_directory_entry_async(agent_id, req.agent_name, req.address, pit_type, card)
                should_upsert_pairs = pit_type == "Pulser" or self.state._card_is_pulser_capable(card) or bool(req.pulse_pulser_pairs)
                if should_upsert_pairs:
                    self.state.upsert_pulse_pulser_pairs_async(
                        agent_id,
                        req.agent_name,
                        card.get("pit_address"),
                        card,
                        pulse_pulser_pairs=req.pulse_pulser_pairs,
                    )
                if owner_context:
                    schedule_owner_key_touch = getattr(self.agent, "_schedule_ui_agent_key_usage_touch", None)
                    if callable(schedule_owner_key_touch):
                        try:
                            schedule_owner_key_touch(owner_context.get("_key_row") or owner_context.get("key_id"))
                        except Exception as exc:
                            logger.warning("[Plaza] Failed scheduling owner key usage update: %s", exc)

                # Plaza asks the address for a proof bound to the token just issued; an edge routes only to verified ones.
                self.state.address_proofs.check(agent_id, req.address, inbound=accepts_inbound)
                return {
                    "status": "registered",
                    "token": token,
                    "expires_in": req.expires_in,
                    "pit_type": pit_type,
                    "agent_id": agent_id,
                    "api_key": api_key,
                    "issued_new_identity": issued_new_identity
                }

            return await run_in_threadpool(_register_sync)
        app.include_router(router)


class PlazaRenewPractice(PlazaEndpointPractice):
    """Endpoint practice for bearer token renewal."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza renew practice."""
        super().__init__(
            state,
            "Plaza Renew",
            "Renew Plaza tokens.",
            "renew",
            examples=["POST /renew {'agent_name':'alice','expires_in':3600}"],
            parameters={
                "agent_name": {"type": "string", "description": "Agent name bound to the token."},
                "expires_in": {"type": "integer", "description": "New token TTL in seconds."},
            },
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()

        @router.post("/renew")
        @coordination_job(self.state, 'Renew agent token')
        async def renew(req: RenewRequest, creds: HTTPAuthorizationCredentials = Depends(security)):
            """Route handler for POST /renew."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            token = str(creds.credentials or "")
            with self.state.lock:
                if token not in self.state.tokens:
                    raise HTTPException(
                        status_code=401,
                        detail=self.state._auth_error_detail("Invalid token", creds=creds),
                    )
                token_data = dict(self.state.tokens[token])
                if token_data["agent_name"] != req.agent_name:
                    raise HTTPException(
                        status_code=401,
                        detail=self.state._auth_error_detail(
                            "Token does not belong to agent",
                            creds=creds,
                            token_data=token_data,
                            extra={"requested_agent_name": str(req.agent_name or "")},
                        ),
                    )

                del self.state.tokens[token]
                new_token = str(uuid.uuid4())
                token_payload = {
                    "agent_name": req.agent_name,
                    "agent_id": token_data.get("agent_id"),
                    "expires_at": time.time() + req.expires_in
                }
                self.state.tokens[new_token] = token_payload
                self.state.agent_tokens[req.agent_name] = new_token
            self.state.persist_token_async(new_token, token_payload)
            # The proof is keyed by the token, so the address is checked again with the new one.
            renewed_id = str(token_payload.get("agent_id") or "")
            if renewed_id and self.state.registry.get(renewed_id):
                card = self.state.agent_cards.get(renewed_id) or {}
                self.state.address_proofs.check(renewed_id, self.state.registry[renewed_id],
                                                inbound=card.get("accepts_inbound_from_plaza") is not False)
            return {"status": "renewed", "token": new_token, "expires_in": req.expires_in}

        app.include_router(router)


class PlazaAuthenticatePractice(PlazaEndpointPractice):
    """Endpoint practice for bearer-token authentication."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza authenticate practice."""
        super().__init__(
            state,
            "Plaza Authenticate",
            "Authenticate Plaza agent tokens or signed-in UI user tokens.",
            "authenticate",
            examples=["POST /authenticate (Bearer token for an agent or UI user)"],
            parameters={},
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()

        @router.post("/authenticate")
        @coordination_job(self.state, 'Authenticate agent')
        async def authenticate(request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(optional_security)):
            """Route handler for POST /authenticate."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            if creds is not None:
                token = str(creds.credentials or "").strip()
                agent_auth_error: Optional[HTTPException] = None
                try:
                    auth = self.state.verify_token(creds)
                except HTTPException as exc:
                    agent_auth_error = exc
                else:
                    return {
                        "status": "authenticated",
                        "subject_type": "agent",
                        "auth_mode": "agent_token",
                        "agent_name": auth.get("agent_name"),
                        "agent_id": auth.get("agent_id"),
                    }

                if token and getattr(self, "agent", None) is not None:
                    get_user = getattr(self.agent, "_get_supabase_user", None)
                    # Match Plaza's browser-auth routes: verification should use
                    # the busy-storage-aware profile path and its dedicated auth
                    # executor. A full inline profile sync can otherwise queue
                    # behind directory/heartbeat persistence and make a healthy
                    # Plaza appear unavailable to callers with short auth budgets.
                    sync_profile = getattr(self.agent, "_sync_ui_user_profile_for_auth_response", None)
                    if not callable(sync_profile):
                        sync_profile = getattr(self.agent, "_sync_ui_user_profile", None)
                    if callable(get_user) and callable(sync_profile):
                        try:
                            def _resolve_ui_user_profile():
                                context = getattr(self.agent, '_get_authenticated_ui_context', None)
                                if callable(context):
                                    # Agent calls and browser APIs must apply
                                    # the same session revocation and user policy.
                                    return context(request)[2]
                                user_payload = get_user(token)
                                return sync_profile(user_payload)

                            run_ui_auth_sync = getattr(self.agent, "_run_ui_auth_sync", None)
                            if callable(run_ui_auth_sync):
                                profile = await run_ui_auth_sync(_resolve_ui_user_profile)
                            else:
                                profile = await run_in_threadpool(_resolve_ui_user_profile)
                            if profile.get("status") == "disabled":
                                raise HTTPException(status_code=403, detail="This account is disabled")
                        except HTTPException as exc:
                            if exc.status_code == 403:
                                raise
                        else:
                            return {
                                "status": "authenticated",
                                "subject_type": "user",
                                "auth_mode": "ui_user",
                                "user_id": profile.get("id"),
                                "username": profile.get("username"),
                                "email": profile.get("email"),
                                "role": profile.get("role"),
                                "user": profile,
                            }

                if agent_auth_error is not None:
                    raise agent_auth_error

                auth = self.state.verify_token(creds)
                return {
                    "status": "authenticated",
                    "subject_type": "agent",
                    "auth_mode": "agent_token",
                    "agent_name": auth.get("agent_name"),
                    "agent_id": auth.get("agent_id"),
                }

            raise HTTPException(
                status_code=401,
                detail=self.state._auth_error_detail("Missing authentication credentials"),
            )

        app.include_router(router)


class PlazaHeartbeatPractice(PlazaEndpointPractice):
    """Endpoint practice for authenticated heartbeat updates."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza heartbeat practice."""
        super().__init__(
            state,
            "Plaza Heartbeat",
            "Accept heartbeat updates from agents.",
            "heartbeat",
            examples=["POST /heartbeat {'agent_id':'...'}"],
            parameters={
                "agent_id": {"type": "string", "description": "Agent id sending heartbeat."},
                "agent_name": {"type": "string", "description": "Fallback name to resolve agent id."},
                "content": {"type": "object", "description": "Optional payload that may include agent_id."},
            },
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()

        @router.post("/heartbeat")
        @coordination_job(self.state, 'Process agent heartbeat')
        def heartbeat(req: HeartbeatRequest, auth: Dict[str, Any] = Depends(self.state.verify_token)):
            """Route handler for POST /heartbeat."""
            # Registration and directory updates hold a threading lock. FastAPI
            # must run this handler in its threadpool so a contended lock never
            # stalls authentication, health checks, or the ASGI event loop.
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            auth_agent_id = auth.get("agent_id")
            requested_agent_id = req.agent_id
            if not requested_agent_id and req.content:
                requested_agent_id = req.content.get("agent_id")
            if not requested_agent_id and req.agent_name:
                with self.state.lock:
                    requested_agent_id = self.state.agent_ids.get(req.agent_name)
            if not requested_agent_id:
                raise HTTPException(status_code=400, detail="Missing agent_id in heartbeat")
            if auth_agent_id != requested_agent_id:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "message": "Unauthorized heartbeat mismatch",
                        "parameters": {
                            "authenticated_agent_id": str(auth_agent_id or ""),
                            "requested_agent_id": str(requested_agent_id or ""),
                            "agent_name": str(req.agent_name or ""),
                        },
                    },
                )
            with self.state.lock:
                self.state.last_active[requested_agent_id] = time.time()
                needs_register = requested_agent_id not in self.state.agent_cards
                address = (getattr(self.state, "registry", None) or {}).get(requested_agent_id)
                card = self.state.agent_cards.get(requested_agent_id) or {}
            proofs = getattr(self.state, "address_proofs", None)
            if address and proofs is not None:
                # A failed address check is tried again here, at most once a minute (address_proof.py).
                proofs.recheck(requested_agent_id, address, inbound=card.get("accepts_inbound_from_plaza") is not False)
            diagnostics = []
            observation_store = getattr(self.state, "observation_store", None)
            if observation_store:
                if req.observability:
                    try:
                        observation_store.ingest(requested_agent_id, req.observability)
                    except (ValueError, TypeError, OverflowError):
                        logging.getLogger(__name__).warning("Invalid observation from authenticated agent")
                diagnostics = observation_store.pending(requested_agent_id)
            return {
                "status": "ok",
                "site_settings": self.state.site_settings,
                "needs_register": needs_register,
                "diagnostics_requests": diagnostics,
            }

        @router.post("/deregister")
        def deregister(auth: Dict[str, Any] = Depends(self.state.verify_token)):
            """Route handler for POST /deregister: an agent that is being removed leaves the directory.

            Only the caller's own entry, named by its token, is removed. It should stop heartbeating first;
            otherwise its next heartbeat is asked to register and the entry is back.
            """
            agent_id = str(auth.get("agent_id") or "")
            remove = getattr(getattr(self.state, "runtime_agent", None), "_delete_directory_entry", None)
            if not agent_id or not callable(remove):
                raise HTTPException(status_code=503, detail="This Plaza cannot remove directory entries.")
            try:
                remove(agent_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            self.state.address_proofs.forget(agent_id)
            return {"status": "ok", "agent_id": agent_id}

        app.include_router(router)


class PlazaSearchPractice(PlazaEndpointPractice):
    """Endpoint practice for directory lookup across persisted and in-memory state."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza search practice."""
        super().__init__(
            state,
            "Plaza Search",
            "Search plaza directory and in-memory registry.",
            "search",
            examples=["GET /search?name=alice&practice=mailbox"],
            input_modes=["http-get", "query"],
            parameters={
                "name": {"type": "string", "description": "Filter by name substring."},
                "agent_id": {"type": "string", "description": "Filter by exact agent id."},
                "type": {"type": "string", "description": "Alias for pit_type filter."},
                "description": {"type": "string", "description": "Filter by description substring."},
                "owner": {"type": "string", "description": "Filter by owner substring."},
                "meta": {"type": "string", "description": "Filter by metadata substring."},
                "role": {"type": "string", "description": "Filter by agent card role."},
                "practice": {"type": "string", "description": "Filter by practice id in agent card."},
                "pit_type": {"type": "string", "description": "Agent type filter."},
                "pulse_id": {"type": "string", "description": "Filter pulsers by supported PDS pulse id."},
                "pulse_name": {"type": "string", "description": "Filter pulsers by supported pulse name."},
                "pulse_address": {"type": "string", "description": "Filter pulsers by supported pulse address."},
                "party": {"type": "string", "description": "Filter by party name."},
            },
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()

        @router.get("/search")
        @coordination_job(self.state, 'Search agent directory')
        async def search(
            name: Optional[str] = None,
            agent_id: Optional[str] = None,
            type: Optional[str] = None,
            description: Optional[str] = None,
            owner: Optional[str] = None,
            meta: Optional[str] = None,
            role: Optional[str] = None,
            practice: Optional[str] = None,
            pit_type: Optional[str] = None,
            pulse_id: Optional[str] = None,
            pulse_name: Optional[str] = None,
            pulse_address: Optional[str] = None,
            party: Optional[str] = None,
            auth: Dict[str, Any] = Depends(self.state.verify_token)
        ):
            """Route handler for GET /search."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            def read():
                return JSONResponse(self.state.search_entries(
                    name=name,
                    agent_id=agent_id,
                    type=type,
                    description=description,
                    owner=owner,
                    meta=meta,
                    role=role,
                    practice=practice,
                    pit_type=pit_type,
                    pulse_id=pulse_id,
                    pulse_name=pulse_name,
                    pulse_address=pulse_address,
                    party=party,
                    use_persisted_fallback=False,
                ))
            return await run_in_threadpool(read)

        app.include_router(router)


class PlazaDispatcherPractice(PlazaEndpointPractice):
    """Endpoint practice for unauthenticated dispatcher lookup."""

    def __init__(self, state: PlazaState):
        """Initialize the Plaza dispatcher lookup practice."""
        super().__init__(
            state,
            "Plaza Dispatcher Lookup",
            "Return dispatcher addresses without registering a caller.",
            "dispatcher",
            examples=["GET /dispatcher?party=Collectible"],
            input_modes=["http-get", "query"],
            parameters={
                "party": {"type": "string", "description": "Optional Party filter for dispatcher selection."},
            },
        )

    def mount(self, app):
        """Mount the dispatcher lookup endpoints."""
        router = APIRouter()

        @coordination_job(self.state, 'Resolve dispatcher')
        async def dispatcher_payload(party: Optional[str] = None):
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            return await run_in_threadpool(self.state.dispatcher_directory, party=party)

        @router.get("/dispatcher")
        async def dispatcher(party: Optional[str] = None):
            """Route handler for GET /dispatcher."""
            return await dispatcher_payload(party=party)

        @router.get("/dispatcher/resolve")
        async def dispatcher_resolve(party: Optional[str] = None):
            """Route handler for GET /dispatcher/resolve."""
            return await dispatcher_payload(party=party)

        app.include_router(router)


class PlazaNotificationPractice(PlazaEndpointPractice):
    """Endpoint practice for notification-only delivery provider fanout."""

    def __init__(self, state: PlazaState):
        """Initialize the Plaza notification practice."""
        super().__init__(
            state,
            "Plaza Notifications",
            "Send notification-only wake-up messages through configured providers.",
            "notifications",
            examples=[
                "POST /notifications {'receiver':'worker','event_type':'practice_call.pending','exchange_id':'8F31'}"
            ],
            parameters={
                "receiver": {"type": "string", "description": "Target agent id or name."},
                "event_type": {"type": "string", "description": "Notification event type."},
                "exchange_id": {"type": "string", "description": "Plaza exchange identifier to fetch."},
                "providers": {"type": "array", "description": "Optional provider IDs or route objects."},
                "force": {"type": "boolean", "description": "Bypass summary throttling for this event."},
            },
        )

    def mount(self, app):
        """Mount the notification endpoints."""
        router = APIRouter()

        def _request_dict(req: PlazaNotificationRequest) -> Dict[str, Any]:
            """Return the request payload across pydantic versions."""
            if hasattr(req, "model_dump"):
                return req.model_dump(exclude_none=True)
            return req.dict(exclude_none=True)

        def _resolve_receiver(receiver: Optional[str]) -> Tuple[str, Dict[str, Any]]:
            """Resolve an optional receiver name/id to agent card metadata."""
            receiver_text = str(receiver or "").strip()
            if not receiver_text:
                return "", {}
            receiver_id = receiver_text
            if receiver_id not in self.state.registry:
                receiver_id = self.state.agent_ids.get(receiver_text, receiver_text)
            if receiver_id not in self.state.registry and receiver_id not in self.state.agent_cards:
                raise HTTPException(status_code=404, detail="Receiver not found")
            return receiver_id, dict(self.state.agent_cards.get(receiver_id, {}) or {})

        def _resolve_notification_auth(
            request: Request,
            creds: Optional[HTTPAuthorizationCredentials],
        ) -> Dict[str, Any]:
            """Authenticate notification senders as agents or signed-in UI users."""
            if creds is None:
                raise HTTPException(
                    status_code=401,
                    detail=self.state._auth_error_detail("Missing authentication credentials"),
                )

            agent_auth_error: Optional[HTTPException] = None
            try:
                auth = self.state.verify_token(creds)
            except HTTPException as exc:
                agent_auth_error = exc
            else:
                return {
                    **auth,
                    "subject_type": "agent",
                    "auth_mode": "agent_token",
                }

            ui_context_resolver = getattr(self.agent, "_get_authenticated_ui_context", None)
            if callable(ui_context_resolver):
                try:
                    _access_token, _user_payload, profile = ui_context_resolver(request)
                except HTTPException as exc:
                    if exc.status_code == 403:
                        raise
                else:
                    return {
                        "subject_type": "user",
                        "auth_mode": "ui_user",
                        "user_id": profile.get("id"),
                        "username": profile.get("username"),
                        "email": profile.get("email"),
                        "role": profile.get("role"),
                    }

            if agent_auth_error is not None:
                raise agent_auth_error
            raise HTTPException(
                status_code=401,
                detail=self.state._auth_error_detail("Invalid token", creds=creds),
            )

        @router.post("/notifications")
        async def notify(
            req: PlazaNotificationRequest,
            request: Request,
            creds: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
        ):
            """Route handler for POST /notifications."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")

            auth = _resolve_notification_auth(request, creds)
            receiver_id, receiver_card = _resolve_receiver(req.receiver)
            payload = _request_dict(req)
            for key in ("providers", "routes", "force", "allow_during_no_bother"):
                payload.pop(key, None)
            sender = (
                auth.get("agent_name")
                or auth.get("username")
                or auth.get("email")
                or auth.get("agent_id")
                or auth.get("user_id")
            )
            payload.setdefault("sender", sender)
            payload.setdefault("sender_id", auth.get("agent_id") or auth.get("user_id"))
            payload.setdefault("sender_type", auth.get("subject_type"))
            if receiver_id:
                payload.setdefault("receiver_id", receiver_id)
            if req.receiver:
                payload.setdefault("receiver", req.receiver)
            payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            site_origin = str(request.headers.get("origin") or "").strip()
            if not site_origin:
                referer = str(request.headers.get("referer") or "").strip()
                if referer:
                    parsed_referer = urlparse(referer)
                    if parsed_referer.scheme and parsed_referer.netloc:
                        site_origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
            if site_origin:
                payload.setdefault("site_origin", site_origin)
            payload.setdefault(
                "plaza_url",
                self.state.plaza_url_for_store
                or getattr(self.agent, "agent_card", {}).get("address")
                or "",
            )

            result = await self.state.notification_dispatcher.submit(
                payload,
                receiver_card=receiver_card,
                routes=req.routes,
                requested_providers=req.providers,
                force=req.force,
                allow_during_no_bother=req.allow_during_no_bother,
            )
            return result

        @router.post("/notifications/flush")
        async def flush_notifications(auth: Dict[str, Any] = Depends(self.state.verify_token)):
            """Route handler for POST /notifications/flush."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            return await self.state.notification_dispatcher.flush_due_summaries(force=True)

        app.include_router(router)


class PlazaCreditPointPractice(PlazaEndpointPractice):
    """Endpoint practice for Plaza-owned user credit point balances."""

    def __init__(self, state: PlazaState):
        """Initialize the Plaza credit point practice."""
        super().__init__(
            state,
            "Plaza Credit Points",
            "Read or adjust the signed-in Plaza user's credit point balance.",
            "credits",
            tags=["core", "plaza", "credits"],
            examples=[
                "GET /credits (Bearer UI user token)",
                "POST /credits {'action':'spend','amount':1}",
                "UsePractice credits {'action':'balance'} with caller_user_token",
            ],
            parameters={
                "action": {"type": "string", "description": "balance, adjust, spend, debit, grant, or credit."},
                "delta": {"type": "integer", "description": "Signed point delta for adjust operations."},
                "amount": {"type": "integer", "description": "Positive point amount for spend or grant operations."},
                "caller_user_token": {"type": "string", "description": "Optional Plaza UI user token when called by another agent."},
                "reason": {"type": "string", "description": "Optional audit note supplied by caller."},
                "metadata": {"type": "object", "description": "Optional caller metadata."},
            },
        )

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        """Return a best-effort dictionary from pydantic or plain values."""
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            return value.model_dump(exclude_none=True)
        if hasattr(value, "dict"):
            return value.dict(exclude_none=True)
        return {}

    def _profile_from_token(self, token: str) -> Dict[str, Any]:
        """Resolve a Plaza UI user profile from a Supabase access token."""
        normalized = str(token or "").strip()
        if not normalized:
            raise HTTPException(status_code=401, detail="A Plaza UI user token is required")
        get_user = getattr(self.agent, "_get_supabase_user", None)
        sync_profile = getattr(self.agent, "_sync_ui_user_profile", None)
        require_confirmed = getattr(self.agent, "_require_confirmed_password_email", None)
        if not callable(get_user) or not callable(sync_profile):
            raise HTTPException(status_code=501, detail="Plaza UI user storage is unavailable")
        user_payload = get_user(normalized)
        if callable(require_confirmed):
            require_confirmed(user_payload)
        profile = sync_profile(user_payload)
        if profile.get("status") == "disabled":
            raise HTTPException(status_code=403, detail="This account is disabled")
        return profile

    def _profile_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the Plaza UI user represented by a practice-call payload."""
        caller = payload.get("_caller") if isinstance(payload.get("_caller"), dict) else {}
        caller_user = caller.get("user") if isinstance(caller.get("user"), dict) else {}
        user_id = str(
            caller.get("user_id")
            or caller_user.get("id")
            or caller_user.get("user_id")
            or ""
        ).strip()
        username = str(
            caller.get("username")
            or caller_user.get("username")
            or ""
        ).strip()

        get_user = getattr(self.agent, "_get_ui_user", None)
        find_user = getattr(self.agent, "_find_ui_user_by_username", None)
        profile = None
        if user_id and callable(get_user):
            profile = get_user(user_id)
        if profile is None and username and callable(find_user):
            profile = find_user(username)
        if profile is not None:
            if profile.get("status") == "disabled":
                raise HTTPException(status_code=403, detail="This account is disabled")
            return profile

        token = str(payload.get("caller_user_token") or "").strip()
        if token:
            return self._profile_from_token(token)
        raise HTTPException(status_code=401, detail="A Plaza-authenticated user is required")

    def _credit_count(self, profile: Dict[str, Any]) -> int:
        """Return normalized credit points from a Plaza UI user profile."""
        coerce = getattr(self.agent, "_coerce_ui_credit_points", None)
        if callable(coerce):
            return coerce(profile.get("credit_points"))
        try:
            return max(int(profile.get("credit_points") or 0), 0)
        except (TypeError, ValueError):
            return 0

    def _result_payload(self, profile: Dict[str, Any], *, action: str, previous_points: Optional[int] = None) -> Dict[str, Any]:
        """Build the public credit response payload."""
        credit_points = self._credit_count(profile)
        payload = {
            "status": "success",
            "action": action,
            "credit_points": credit_points,
            "points": credit_points,
            "user": profile,
        }
        if previous_points is not None:
            payload["previous_credit_points"] = previous_points
            payload["delta"] = credit_points - previous_points
        return payload

    def _resolve_delta(self, payload: Dict[str, Any], action: str) -> Optional[int]:
        """Resolve the requested credit delta for mutating actions."""
        if action in {"balance", "get", "read"}:
            return None
        raw_delta = payload.get("delta")
        raw_amount = payload.get("amount")
        if action in {"spend", "debit", "use", "charge"}:
            value = raw_amount if raw_amount is not None else raw_delta
            try:
                return -abs(int(value))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Spend amount must be an integer") from exc
        if action in {"grant", "credit", "add"}:
            value = raw_amount if raw_amount is not None else raw_delta
            try:
                return abs(int(value))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Credit amount must be an integer") from exc
        if action == "adjust":
            try:
                return int(raw_delta)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Credit point delta must be an integer") from exc
        raise HTTPException(status_code=400, detail=f"Unsupported credit point action '{action}'")

    def _handle(self, payload: Dict[str, Any], *, token: str = "") -> Dict[str, Any]:
        """Read or mutate one Plaza UI user's credit balance."""
        request_payload = dict(payload or {})
        if token and not request_payload.get("caller_user_token"):
            request_payload["caller_user_token"] = token
        action = str(request_payload.get("action") or "balance").strip().lower() or "balance"
        profile = self._profile_from_payload(request_payload)
        previous_points = self._credit_count(profile)
        delta = self._resolve_delta(request_payload, action)
        if delta is None:
            return self._result_payload(profile, action="balance")
        if delta > 0 and str(profile.get("role") or "").strip().lower() != "admin":
            raise HTTPException(status_code=403, detail="Credit top-ups require an admin account")
        adjust = getattr(self.agent, "_adjust_ui_credit_points", None)
        if not callable(adjust):
            raise HTTPException(status_code=501, detail="Plaza credit point storage is unavailable")
        metadata = request_payload.get("metadata") if isinstance(request_payload.get("metadata"), dict) else {}
        updated = adjust(
            user_id=str(profile.get("id") or ""),
            delta=delta,
            actor=profile,
            action="top_up" if delta > 0 else ("spend" if delta < 0 else "adjust"),
            reason=str(request_payload.get("reason") or "Plaza credit practice"),
            source=str(request_payload.get("source") or "credit_practice"),
            metadata=metadata,
            request_id=str(request_payload.get("request_id") or ""),
            practice_id=str(metadata.get("practice_id") or "credits"),
            agent_id=str(metadata.get("agent_id") or ""),
            agent_name=str(metadata.get("agent_name") or ""),
        )
        return self._result_payload(updated, action=action, previous_points=previous_points)

    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the credit point service through UsePractice."""
        return self._handle(dict(kwargs or {}))

    def mount(self, app):
        """Mount Plaza credit point routes."""
        router = APIRouter()

        @router.get("/credits")
        async def get_credits(creds: Optional[HTTPAuthorizationCredentials] = Depends(optional_security)):
            """Route handler for GET /credits."""
            token = str(creds.credentials or "").strip() if creds is not None else ""
            return await run_in_threadpool(self._handle, {}, token=token)

        @router.post("/credits")
        async def update_credits(
            req: PlazaCreditPointRequest,
            creds: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
        ):
            """Route handler for POST /credits."""
            token = str(creds.credentials or "").strip() if creds is not None else ""
            return await run_in_threadpool(self._handle, self._as_dict(req), token=token)

        app.include_router(router)


class PlazaRelayPractice(PlazaEndpointPractice):
    """Endpoint practice that proxies messages from one authenticated agent to another."""
    def __init__(self, state: PlazaState):
        """Initialize the Plaza relay practice."""
        super().__init__(
            state,
            "Plaza Relay",
            "Relay messages between agents.",
            "relay",
            examples=[
                "POST /relay {'receiver':'bob','content':'hi','msg_type':'message'}"
            ],
            parameters={
                "receiver": {"type": "string", "description": "Target agent id or name."},
                "content": {"type": "object", "description": "Relay payload for destination practice."},
                "msg_type": {"type": "string", "description": "Message type used for routing."},
            },
        )

    def mount(self, app):
        """Mount the value."""
        router = APIRouter()
        self._client = httpx.AsyncClient(timeout=240.0)

        @app.on_event("shutdown")
        async def shutdown_event():
            """Handle shutdown event for the Plaza relay practice."""
            await self._client.aclose()

        @router.post("/relay")
        @coordination_job(self.state, 'Relay agent message')
        async def relay_message(msg: RelayMessage, auth: Dict[str, Any] = Depends(self.state.verify_token)):
            """Route handler for POST /relay."""
            if self.state.is_starting:
                raise HTTPException(status_code=503, detail="Starting")
            receiver_id = msg.receiver
            if receiver_id not in self.state.registry:
                receiver_id = self.state.agent_ids.get(msg.receiver, msg.receiver)
            if receiver_id not in self.state.registry:
                raise HTTPException(status_code=404, detail="Receiver not found")

            receiver_address = self.state.registry[receiver_id]
            receiver_card = self.state.agent_cards.get(receiver_id, {})
            receiver_practices = receiver_card.get("practices", []) if isinstance(receiver_card, dict) else []
            practice_entry = next(
                (
                    entry for entry in receiver_practices
                    if isinstance(entry, dict) and str(entry.get("id") or "").strip() == str(msg.msg_type or "").strip()
                ),
                None,
            )
            path = str(practice_entry.get("path") or "/mailbox") if isinstance(practice_entry, dict) else "/mailbox"
            if not path.startswith("/"):
                path = f"/{path}"
            from prompits.core.observability import trace_headers, TRACE
            started = time.monotonic()
            def record_relay(status):
                store = getattr(self.state, "observation_store", None)
                if store:
                    store.record_event(auth.get("agent_id"), {
                        "id": str(uuid.uuid4()), "event": "interaction", "direction": "outbound", "phase": "result",
                        "operation": msg.msg_type, "peer_agent_id": receiver_id, "status": status,
                        "transport": "plaza_relay", "timestamp": time.time(), "duration_ms": (time.monotonic()-started)*1000,
                        **(TRACE.get() or {})})
            try:
                payload = {
                    "sender": auth.get("agent_name") or auth.get("agent_id"),
                    "receiver": msg.receiver,
                    "content": msg.content,
                    "msg_type": msg.msg_type
                }
                response = await self._client.post(f"{receiver_address}{path}", json=payload,
                                                  headers=trace_headers())
                response.raise_for_status()
                record_relay("succeeded")
                return {"status": "relayed", "response": response.json() if response.content else {}}
            except Exception as e:
                record_relay("failed")
                raise HTTPException(status_code=502, detail=f"Failed to relay: {str(e)}")

        app.include_router(router)


class PlazaPractice(Practice):
    """
    Composite practice that mounts the full Plaza endpoint suite.

    This class is a wrapper/aggregator around endpoint sub-practices and
    preserves backward-compatible attribute access for existing callers/tests.
    """

    SUPPORTED_PIT_TYPES = PlazaState.SUPPORTED_PIT_TYPES
    DIRECTORY_TABLE = PlazaState.DIRECTORY_TABLE
    PULSE_PULSER_TABLE = PlazaState.PULSE_PULSER_TABLE

    def __init__(
        self,
        registry: Optional[Dict[str, Any]] = None,
        self_heartbeat_interval: int = 10,
        init_files: Optional[List[str] | str] = None,
        config_dir: Optional[str] = None,
        notification_providers: Optional[Any] = None,
        notification_settings: Optional[Dict[str, Any]] = None,
        delivery_providers: Optional[Any] = None,
    ):
        """Initialize the Plaza practice."""
        super().__init__(
            name="Plaza Practice",
            description="Plaza endpoint bundle wrapper.",
            id="plaza",
            tags=["core", "plaza"]
        )
        self.state = PlazaState(
            registry=registry,
            self_heartbeat_interval=self_heartbeat_interval,
            init_files=init_files,
            config_dir=config_dir,
            notification_providers=notification_providers if notification_providers is not None else delivery_providers,
            notification_settings=notification_settings,
        )
        self.init_files = self.state.init_files
        self.config_dir = self.state.config_dir
        self.endpoint_practices: List[PlazaEndpointPractice] = [
            PlazaRegisterPractice(self.state),
            PlazaRenewPractice(self.state),
            PlazaAuthenticatePractice(self.state),
            PlazaHeartbeatPractice(self.state),
            PlazaSearchPractice(self.state),
            PlazaDispatcherPractice(self.state),
            PlazaNotificationPractice(self.state),
            PlazaCreditPointPractice(self.state),
            PlazaRelayPractice(self.state),
        ]
        # Backward-compatible attribute access used by tests/callers.
        self.registry = self.state.registry
        self.self_heartbeat_interval = self.state.self_heartbeat_interval
        self.tokens = self.state.tokens
        self.agent_tokens = self.state.agent_tokens
        self.pit_types = self.state.pit_types
        self.agent_ids = self.state.agent_ids
        self.registry_by_name = self.state.registry_by_name
        self.agent_names_by_id = self.state.agent_names_by_id
        self.credentials_by_id = self.state.credentials_by_id
        self.credential_store = self.state.credential_store
        self.plaza_url_for_store = self.state.plaza_url_for_store
        self.directory_pool = self.state.directory_pool
        self.agent_cards = self.state.agent_cards
        self.last_active = self.state.last_active
        self.login_history_by_id = self.state.login_history_by_id

    def get_callable_endpoints(self) -> List[Dict[str, Any]]:
        """Return the callable endpoints."""
        endpoints = []
        for practice in self.endpoint_practices:
            endpoints.append({
                "name": practice.name,
                "description": practice.description,
                "id": practice.id,
                "cost": practice.cost,
                "tags": practice.tags,
                "examples": practice.examples,
                "inputModes": practice.inputModes,
                "outputModes": practice.outputModes,
                "parameters": practice.parameters,
                "path": f"/{practice.id}"
            })
        return endpoints

    def execute_callable_endpoint(self, practice_id: str, content: Any) -> Any:
        """Execute one endpoint practice exposed by this Plaza bundle."""
        endpoint = next((practice for practice in self.endpoint_practices if practice.id == practice_id), None)
        if endpoint is None:
            raise HTTPException(status_code=404, detail=f"Practice '{practice_id}' not found")
        if endpoint.__class__.execute is Practice.execute:
            raise HTTPException(status_code=501, detail=f"Practice '{practice_id}' is not executable through UsePractice")
        if isinstance(content, dict):
            return endpoint.execute(**content)
        if content is None:
            return endpoint.execute()
        return endpoint.execute(content=content)

    def can_execute_callable_endpoint(self, practice_id: str) -> bool:
        """Return whether one exposed endpoint supports UsePractice execution."""
        endpoint = next((practice for practice in self.endpoint_practices if practice.id == practice_id), None)
        return bool(endpoint is not None and endpoint.__class__.execute is not Practice.execute)

    def mount(self, app):
        """Mount the value."""
        self.state.bootstrap_plaza_agent(self.agent)
        self.credential_store = self.state.credential_store
        self.plaza_url_for_store = self.state.plaza_url_for_store
        self.directory_pool = self.state.directory_pool
        for practice in self.endpoint_practices:
            practice.bind(self.agent)
            practice.mount(app)
