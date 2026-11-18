"""
Base module for `prompits.agents.base`.

Prompits provides the core HTTP-native agent runtime, Plaza coordination layer, and
pool/practice infrastructure for FinMAS. Within Prompits, these modules provide reusable
agent hosts and user-facing agent variants.

Core types exposed here include `BaseAgent` and `PracticeInvocationRequest`, which carry
the main behavior or state managed by this module.
"""

import logging
import asyncio
import copy
import fnmatch
import hashlib
import html
import inspect
import json
import os
import re
import requests
import secrets
import httpx
import uvicorn
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Mapping, Tuple
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from prompits.channels.notifications import store_forward_summary_from_card
from prompits.core.message import Message
from prompits.core.init_schema import agent_practices_table_schema
from prompits.core.pit import Pit, PitAddress
from prompits.core.practice import Practice
from prompits.core.schema import TableSchema
from prompits.practices.plaza import PlazaCredentialStore
import threading
from urllib.parse import urlparse
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from prompits.core.pool import Pool


class PracticeInvocationRequest(BaseModel):
    """Envelope used for remote `UsePractice` execution between agents."""

    sender: str
    receiver: str
    content: Any = None
    msg_type: str
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    caller_agent_address: Optional[Dict[str, Any]] = None
    caller_agent_name: Optional[str] = None
    caller_agent_url: Optional[str] = None
    caller_plaza_token: Optional[str] = None
    caller_direct_token: Optional[str] = None
    caller_user_token: Optional[str] = None
    caller_protocol_version: Optional[str] = None
    billing_scope_id: Optional[str] = None
    billing_scope_token: Optional[str] = None
    billing_quote_id: Optional[str] = None
    billing_pulse_name: Optional[str] = None
    billing_pulse_address: Optional[str] = None
    billing: Optional[Dict[str, Any]] = None

class PaymentScopeExecuted(HTTPException):
    """A payment scope whose paid work already started or was delivered, and whose result this provider no longer holds.

    The work is never run again for that scope. The caller reconciles its escrow with Plaza instead: a refund pays the
    provider for work Plaza recorded as delivered and returns the rest.
    """
    OUTCOME_HEADER = "X-Payment-Scope-Outcome"
    OUTCOME = "executed-result-lost"

    def __init__(self, scope_id: str, scope_status: str = ""):
        super().__init__(
            status_code=409,
            detail=(f"Payment scope {scope_id} already ran ({scope_status or 'started'}) and its result is no longer "
                    "held; it will not run again. Reconcile the payment with Plaza."),
            headers={self.OUTCOME_HEADER: self.OUTCOME},
        )


class BaseAgent(Pit, ABC):
    """
    Core runtime abstraction for every networked agent in the Prompits system.

    Responsibilities:
    - Host a FastAPI app and mount practices.
    - Register/authenticate with Plaza and maintain heartbeat/token lifecycle.
    - Resolve peers and route local/remote practice invocations.
    - Persist and reload practice metadata and Plaza credentials through `Pool`.
    """

    AGENT_PRACTICES_TABLE = "agent_practices"
    PLAZA_REQUEST_TIMEOUT = 240
    PLAZA_REQUEST_RETRIES = 5
    PLAZA_CONNECTION_REQUEST_TIMEOUT = 240
    REMOTE_PRACTICE_REQUEST_TIMEOUT = 240
    PLAZA_RETRY_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
    PLAZA_HEARTBEAT_INTERVAL = 10
    PLAZA_REJECTED_CREDENTIAL_RETRY_DELAY = 60
    PLAZA_RECONNECT_INTERVAL = 60
    PLAZA_CONNECTION_ACTIVE_WINDOW_SEC = 60
    PLAZA_AGENT_VERIFICATION_CACHE_TTL = 30
    PLAZA_USER_VERIFICATION_CACHE_TTL = 30
    MAILBOX_PRACTICE_ID = "mailbox"
    LEGACY_REMOVED_PRACTICE_IDS = ("chat-practice", "llm")
    REMOTE_PRACTICE_AUDIT_TABLE = "cross_agent_practice_audit"
    AGENT_RUNTIME_VERSION = "0.2.1"
    AGENT_PROTOCOL_VERSION = "1.0"
    REMOTE_PROTOCOL_DISCOVERY_TIMEOUT = 15
    REMOTE_PROTOCOL_CACHE_TTL = 300
    AGENT_RUNTIME_PROPERTY_NOT_IMPLEMENTED = "not implemented"
    AGENT_RUNTIME_WORK_HISTORY_LIMIT = 100
    AGENT_RUNTIME_WORK_STALE_AFTER_SEC = 60 * 60
    VERSION_HISTORY_FILENAME = "agent_version_history.json"
    VERSION_SECRET_KEY_TOKENS = ("api_key", "password", "secret", "token", "credential", "private_key")

    @staticmethod
    def _normalize_url(value: Any) -> str:
        """Internal helper to normalize the URL."""
        if value is None:
            return ""
        return str(value).strip().rstrip("/")

    @classmethod
    def _same_url_origin(cls, left: Any, right: Any) -> bool:
        """Return whether two URLs point at the same origin, treating localhost aliases as equal."""
        left_url = cls._normalize_url(left)
        right_url = cls._normalize_url(right)
        if not left_url or not right_url:
            return False
        if left_url == right_url:
            return True
        try:
            left_parts = urlparse(left_url)
            right_parts = urlparse(right_url)
        except Exception:
            return False
        if (left_parts.scheme or "http").lower() != (right_parts.scheme or "http").lower():
            return False
        left_host = (left_parts.hostname or "").lower()
        right_host = (right_parts.hostname or "").lower()
        localhost_aliases = {"localhost", "127.0.0.1", "::1"}
        hosts_match = left_host == right_host or (left_host in localhost_aliases and right_host in localhost_aliases)
        return hosts_match and left_parts.port == right_parts.port

    @classmethod
    def _normalize_url_list(cls, value: Any) -> List[str]:
        """Internal helper to normalize one-or-many Plaza URLs."""
        raw_values: List[Any]
        if isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        elif isinstance(value, str):
            raw_values = [part for part in re.split(r"[\n,]", value) if str(part).strip()]
        elif value is None:
            raw_values = []
        else:
            raw_values = [value]

        normalized: List[str] = []
        for entry in raw_values:
            normalized_url = cls._normalize_url(entry)
            if normalized_url and normalized_url not in normalized:
                normalized.append(normalized_url)
        return normalized

    @staticmethod
    def _coerce_optional_bool(value: Any) -> Optional[bool]:
        """Internal helper to coerce the optional bool."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
        return None

    @classmethod
    def _plaza_request_timeout_value(cls) -> float:
        """Return the configured Plaza request timeout."""
        raw = str(os.getenv("PROMPITS_PLAZA_REQUEST_TIMEOUT") or "").strip()
        if not raw:
            return float(cls.PLAZA_REQUEST_TIMEOUT)
        try:
            return float(raw)
        except ValueError:
            return float(cls.PLAZA_REQUEST_TIMEOUT)

    @staticmethod
    def _verification_cache_key(scope: str, plaza_url: str, token: str, subject_id: str = "") -> str:
        """Build a stable cache key for short-lived Plaza auth verification results."""
        return "|".join(
            (
                str(scope or "").strip(),
                str(plaza_url or "").strip(),
                str(subject_id or "").strip(),
                str(token or "").strip(),
            )
        )

    @staticmethod
    def _normalize_policy_action(value: Any, *, default: str = "allow") -> str:
        """Internal helper to normalize a remote-practice policy action."""
        lowered = str(value or "").strip().lower()
        if lowered in {"deny", "blocked", "block", "forbid", "forbidden"}:
            return "deny"
        if lowered in {"allow", "allowed", "permit", "permitted"}:
            return "allow"
        return default

    @staticmethod
    def _policy_string(value: Any) -> str:
        """Internal helper to normalize policy string matching values."""
        return str(value or "").strip()

    @classmethod
    def _policy_value_matches(cls, rule_value: Any, context_value: Any) -> bool:
        """Return whether one policy field matches the provided context value."""
        if isinstance(rule_value, (list, tuple, set)):
            return any(cls._policy_value_matches(candidate, context_value) for candidate in rule_value)

        if isinstance(context_value, (list, tuple, set)):
            return any(cls._policy_value_matches(rule_value, candidate) for candidate in context_value)

        if isinstance(rule_value, bool):
            return bool(context_value) is rule_value

        rule_text = cls._policy_string(rule_value)
        context_text = cls._policy_string(context_value)
        if not rule_text:
            return False
        if not context_text:
            return False
        if any(token in rule_text for token in ("*", "?", "[")):
            return fnmatch.fnmatch(context_text.lower(), rule_text.lower())
        return context_text.lower() == rule_text.lower()

    @classmethod
    def _normalize_remote_policy_rule(cls, direction: str, rule: Any) -> Dict[str, Any]:
        """Normalize one inbound/outbound remote practice policy rule."""
        if isinstance(rule, str):
            normalized_text = cls._policy_string(rule)
            return {"practice_id": normalized_text} if normalized_text else {}
        if not isinstance(rule, dict):
            return {}

        if direction == "outbound":
            aliases = {
                "practice": "practice_id",
                "agent_id": "target_agent_id",
                "agent_name": "target_name",
                "name": "target_name",
                "address": "target_address",
                "url": "target_address",
                "target": "target_address",
                "target_url": "target_address",
                "destination": "target_address",
                "destination_address": "target_address",
                "role": "target_role",
                "pit_type": "target_pit_type",
                "type": "target_pit_type",
            }
            allowed_fields = {
                "practice_id",
                "target_agent_id",
                "target_name",
                "target_address",
                "target_role",
                "target_pit_type",
                "plaza_url",
            }
        else:
            aliases = {
                "practice": "practice_id",
                "agent_id": "caller_agent_id",
                "agent_name": "caller_name",
                "name": "caller_name",
                "address": "caller_address",
                "url": "caller_address",
                "caller_url": "caller_address",
                "user_id": "caller_user_id",
                "username": "caller_username",
                "user": "caller_username",
                "email": "caller_user_email",
                "user_email": "caller_user_email",
                "user_role": "caller_user_role",
            }
            allowed_fields = {
                "practice_id",
                "caller_agent_id",
                "caller_name",
                "caller_address",
                "auth_mode",
                "plaza_url",
                "caller_user_id",
                "caller_username",
                "caller_user_email",
                "caller_user_role",
            }

        normalized: Dict[str, Any] = {}
        for raw_key, raw_value in rule.items():
            canonical_key = aliases.get(str(raw_key or "").strip(), str(raw_key or "").strip())
            if canonical_key not in allowed_fields:
                continue
            if raw_value in (None, "", [], {}, ()):
                continue
            normalized[canonical_key] = raw_value
        return normalized

    @classmethod
    def _normalize_remote_use_practice_policy(cls, value: Any) -> Dict[str, Any]:
        """Normalize remote `UsePractice` policy configuration."""
        if not isinstance(value, dict):
            value = {}

        outbound_block = value.get("outbound") if isinstance(value.get("outbound"), dict) else {}
        inbound_block = value.get("inbound") if isinstance(value.get("inbound"), dict) else {}

        def normalize_rules(direction: str, entries: Any) -> List[Dict[str, Any]]:
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                return []
            normalized_rules: List[Dict[str, Any]] = []
            for entry in entries:
                normalized_rule = cls._normalize_remote_policy_rule(direction, entry)
                if normalized_rule:
                    normalized_rules.append(normalized_rule)
            return normalized_rules

        default_action = cls._normalize_policy_action(value.get("default"), default="allow")
        outbound_default = cls._normalize_policy_action(
            value.get("outbound_default", outbound_block.get("default")),
            default=default_action,
        )
        inbound_default = cls._normalize_policy_action(
            value.get("inbound_default", inbound_block.get("default")),
            default=default_action,
        )

        return {
            "enabled": cls._coerce_optional_bool(value.get("enabled")) is not False,
            "outbound_default": outbound_default,
            "inbound_default": inbound_default,
            "outbound_allow": normalize_rules("outbound", value.get("outbound_allow", outbound_block.get("allow"))),
            "outbound_deny": normalize_rules("outbound", value.get("outbound_deny", outbound_block.get("deny"))),
            "inbound_allow": normalize_rules("inbound", value.get("inbound_allow", inbound_block.get("allow"))),
            "inbound_deny": normalize_rules("inbound", value.get("inbound_deny", inbound_block.get("deny"))),
        }

    @classmethod
    def _normalize_remote_use_practice_audit(cls, value: Any) -> Dict[str, Any]:
        """Normalize remote `UsePractice` audit configuration."""
        if not isinstance(value, dict):
            value = {}
        table_name = cls._policy_string(value.get("table_name") or cls.REMOTE_PRACTICE_AUDIT_TABLE)
        return {
            "enabled": cls._coerce_optional_bool(value.get("enabled")) is not False,
            "emit_logs": cls._coerce_optional_bool(value.get("emit_logs")) is not False,
            "persist": cls._coerce_optional_bool(value.get("persist")) is not False,
            "table_name": table_name or cls.REMOTE_PRACTICE_AUDIT_TABLE,
        }

    @classmethod
    def _normalize_remote_practice_billing(cls, value: Any) -> Dict[str, Any]:
        """Normalize remote `UsePractice` billing configuration."""
        if not isinstance(value, dict):
            value = {}

        env_mode = str(os.getenv("PROMPITS_REMOTE_PRACTICE_BILLING_MODE") or "").strip()
        raw_mode = cls._policy_string(
            value.get("mode")
            or value.get("inbound_mode")
            or value.get("billing_mode")
            or env_mode
            or "legacy"
        ).lower()
        mode_aliases = {
            "direct": "legacy",
            "direct_charge": "legacy",
            "caller_user": "legacy",
            "caller_user_charge": "legacy",
            "scope": "escrow_required",
            "escrow": "escrow_required",
            "required": "escrow_required",
            "scope_required": "escrow_required",
            "require_scope": "escrow_required",
            "escrow_scope": "escrow_required",
            "escrow_scope_required": "escrow_required",
            "scope_or_legacy": "escrow_or_legacy",
            "escrow_or_legacy": "escrow_or_legacy",
            "hybrid": "escrow_or_legacy",
            "scope_optional": "scope_optional",
            "escrow_optional": "scope_optional",
            "optional": "scope_optional",
            "disabled": "disabled",
            "off": "disabled",
            "none": "disabled",
        }
        mode = mode_aliases.get(raw_mode, raw_mode if raw_mode in {"legacy", "escrow_required", "escrow_or_legacy", "scope_optional", "disabled"} else "legacy")

        enabled = cls._coerce_optional_bool(value.get("enabled"))
        require_scope = mode == "escrow_required"
        legacy_fallback = mode in {"legacy", "escrow_or_legacy"}
        complete_scope_on_success = True

        configured_require_scope = None
        for key in ("require_scope", "require_escrow", "require_payment_scope", "require_scope_for_paid_practices"):
            if key in value:
                configured_require_scope = cls._coerce_optional_bool(value.get(key))
                break
        if configured_require_scope is not None:
            require_scope = configured_require_scope

        configured_legacy = None
        for key in ("legacy_fallback", "allow_legacy", "allow_legacy_user_charge", "fallback_to_legacy"):
            if key in value:
                configured_legacy = cls._coerce_optional_bool(value.get(key))
                break
        if configured_legacy is not None:
            legacy_fallback = configured_legacy

        configured_complete = cls._coerce_optional_bool(value.get("complete_scope_on_success"))
        if configured_complete is not None:
            complete_scope_on_success = configured_complete

        normalized = {
            "enabled": enabled is not False and mode != "disabled",
            "mode": mode,
            "require_scope_for_paid_practices": bool(require_scope),
            "legacy_fallback": bool(legacy_fallback),
            "use_scope_when_present": cls._coerce_optional_bool(value.get("use_scope_when_present")) is not False,
            "complete_scope_on_success": bool(complete_scope_on_success),
            # Whether a caller settling a whole run through its escrow's capture may ask this provider to leave its
            # payment scope open (it is then paid at capture, or by a refund once Plaza recorded it delivered).
            "allow_settlement_on_capture": cls._coerce_optional_bool(value.get("allow_settlement_on_capture")) is not False,
        }
        # Tuning for the delivery record a provider waits for before releasing escrow-settled work.
        for key, kind, minimum in (("delivery_ack_attempts", int, 1), ("delivery_ack_backoff_seconds", float, 0.0),
                                   ("delivery_result_hold_seconds", float, 1.0), ("delivery_result_hold_limit", int, 1)):
            if value.get(key) is None:
                continue
            try:
                normalized[key] = max(kind(float(value[key])), kind(minimum))
            except (TypeError, ValueError):
                pass
        if value.get("scope_protocol") == "agent-payment/1":
            normalized["scope_protocol"] = "agent-payment/1"
            if isinstance(value.get("provider_intent_path"), str):
                normalized["provider_intent_path"] = value["provider_intent_path"]
        return normalized

    @classmethod
    def _normalize_user_selector_list(cls, value: Any) -> List[Any]:
        """Normalize one-or-many Plaza UI user selectors for permission matching."""
        if value in (None, "", [], {}, ()):
            return []
        if isinstance(value, (list, tuple, set)):
            return [entry for entry in value if entry not in (None, "", [], {}, ())]
        return [value]

    @classmethod
    def _normalize_practice_permission_rule(cls, value: Any) -> Dict[str, Any]:
        """Normalize one per-practice Plaza user permission rule."""
        if not isinstance(value, dict):
            value = {}

        action_value = None
        for key in (
            "default",
            "action",
            "access",
            "default_action",
            "default_access",
            "authenticated_users",
            "authenticated_user_default",
        ):
            if value.get(key) not in (None, ""):
                action_value = value.get(key)
                break

        require_user = None
        for key in ("require_authenticated_user", "require_user", "authenticated_only"):
            if key in value:
                require_user = cls._coerce_optional_bool(value.get(key))
                break

        allow_users: List[Any] = []
        deny_users: List[Any] = []
        for key in ("allow_users", "allowed_users", "user_allow", "users_allow", "allow"):
            allow_users.extend(cls._normalize_user_selector_list(value.get(key)))
        for key in ("deny_users", "denied_users", "user_deny", "users_deny", "deny"):
            deny_users.extend(cls._normalize_user_selector_list(value.get(key)))

        normalized: Dict[str, Any] = {
            "allow_users": allow_users,
            "deny_users": deny_users,
        }
        if action_value not in (None, ""):
            normalized["action"] = cls._normalize_policy_action(action_value, default="allow")
        if require_user is not None:
            normalized["require_authenticated_user"] = require_user
        return normalized

    @classmethod
    def _normalize_practice_permission_practices(cls, value: Any) -> Dict[str, Dict[str, Any]]:
        """Normalize practice-id keyed permission overrides."""
        if not value:
            return {}
        entries: Dict[str, Any] = {}
        if isinstance(value, dict):
            entries = dict(value)
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                practice_id = str(item.get("practice_id") or item.get("id") or item.get("practice") or "").strip()
                if practice_id:
                    entries[practice_id] = item

        normalized: Dict[str, Dict[str, Any]] = {}
        for raw_practice_id, raw_rule in entries.items():
            practice_id = str(raw_practice_id or "").strip()
            if not practice_id:
                continue
            rule = cls._normalize_practice_permission_rule(raw_rule)
            if rule.get("action") or rule.get("allow_users") or rule.get("deny_users") or "require_authenticated_user" in rule:
                normalized[practice_id] = rule
        return normalized

    @classmethod
    def _normalize_practice_user_permissions(cls, value: Any) -> Dict[str, Any]:
        """Normalize Plaza UI user permissions for inbound practice execution."""
        if not isinstance(value, dict) or not value:
            return {
                "enabled": False,
                "default_action": "allow",
                "require_authenticated_user": False,
                "allow_users": [],
                "deny_users": [],
                "practices": {},
            }

        global_block = value.get("global") if isinstance(value.get("global"), dict) else {}
        enabled = cls._coerce_optional_bool(value.get("enabled"))
        default_value = (
            value.get("default")
            if value.get("default") not in (None, "")
            else value.get("action")
            if value.get("action") not in (None, "")
            else value.get("access")
            if value.get("access") not in (None, "")
            else global_block.get("default")
            if global_block.get("default") not in (None, "")
            else global_block.get("action")
            if global_block.get("action") not in (None, "")
            else global_block.get("access")
        )

        require_user = None
        for source in (value, global_block):
            for key in ("require_authenticated_user", "require_user", "authenticated_only"):
                if key in source:
                    require_user = cls._coerce_optional_bool(source.get(key))
                    break
            if require_user is not None:
                break

        allow_users: List[Any] = []
        deny_users: List[Any] = []
        for source in (global_block, value):
            for key in ("allow_users", "allowed_users", "user_allow", "users_allow"):
                allow_users.extend(cls._normalize_user_selector_list(source.get(key)))
            for key in ("deny_users", "denied_users", "user_deny", "users_deny"):
                deny_users.extend(cls._normalize_user_selector_list(source.get(key)))

        practices = cls._normalize_practice_permission_practices(
            value.get("practices")
            or value.get("practice_overrides")
            or value.get("overrides")
        )

        return {
            "enabled": enabled is not False,
            "default_action": cls._normalize_policy_action(default_value, default="allow"),
            "require_authenticated_user": bool(require_user) if require_user is not None else False,
            "allow_users": allow_users,
            "deny_users": deny_users,
            "practices": practices,
        }

    def _consume_runtime_setting(self, key: str) -> Any:
        """Consume one runtime-only setting passed through the agent card."""
        if key in self.agent_card:
            return self.agent_card.pop(key, None)
        meta = self.agent_card.get("meta")
        if isinstance(meta, dict) and key in meta:
            return meta.pop(key, None)
        return None

    @staticmethod
    def _safe_version_part(value: Any, fallback: str = "agent") -> str:
        """Return a filesystem-safe segment for version history paths."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
        return cleaned or fallback

    @classmethod
    def _runtime_version_from_environment(cls) -> str:
        """Return a deployment-provided runtime version when one is configured."""
        configured = str(os.getenv("PROMPITS_AGENT_RUNTIME_VERSION") or "").strip()
        return configured or str(cls.AGENT_RUNTIME_VERSION)

    def __init__(self, name: str, host: str = "127.0.0.1", port: int = 8000, plaza_url: Optional[str] = None, agent_card: Dict[str, Any] = None, pool: Optional[Pool] = None):
        """Initialize the base agent."""
        self.AGENT_RUNTIME_VERSION = self._runtime_version_from_environment()
        seed_card = agent_card or {"name": name, "role": "generic", "tags": []}
        super().__init__(
            name=name,
            description=seed_card.get("description", ""),
            address=PitAddress.from_value(seed_card.get("pit_address")),
            meta=seed_card.get("meta", {})
        )
        self.name = name
        self.host = host
        self.port = port
        self.plaza_url = plaza_url
        self.agent_card = seed_card
        self.agent_card.setdefault("pit_type", "Agent")
        self.agent_card.setdefault("meta", {})
        self.agent_card.setdefault("host", host)
        self.agent_card.setdefault("port", port)
        self.agent_card["address"] = self._resolve_advertised_address()
        self._sync_agent_version_metadata()
        self.pool = pool
        self.plaza_credential_store = PlazaCredentialStore(pool=pool)
        self.agent_id: Optional[str] = self.agent_card.get("agent_id")
        self.api_key: Optional[str] = self.agent_card.get("api_key")
        raw_plaza_auto_register = self._consume_runtime_setting("plaza_auto_register")
        parsed_plaza_auto_register = self._coerce_optional_bool(raw_plaza_auto_register)
        self.plaza_auto_register = True if parsed_plaza_auto_register is None else parsed_plaza_auto_register
        raw_remote_policy = self._consume_runtime_setting("remote_use_practice_policy")
        raw_remote_audit = self._consume_runtime_setting("remote_use_practice_audit")
        raw_remote_billing = self._consume_runtime_setting("remote_practice_billing")
        if raw_remote_billing is None:
            raw_remote_billing = self._consume_runtime_setting("remote_use_practice_billing")
        raw_practice_permissions = self._consume_runtime_setting("practice_permissions")
        if raw_practice_permissions is None:
            for alias in ("practice_access", "plaza_user_permissions", "user_practice_permissions"):
                raw_practice_permissions = self._consume_runtime_setting(alias)
                if raw_practice_permissions is not None:
                    break
        configured_direct_token = (
            self.agent_card.get("meta", {}).get("direct_auth_token")
            or os.getenv("PROMPITS_DIRECT_TOKEN")
            or ""
        )
        self.direct_auth_token: Optional[str] = str(configured_direct_token).strip() or None
        self.trusted_plaza_urls: List[str] = []
        self._sync_trusted_plaza_metadata()
        self.remote_use_practice_policy = self._normalize_remote_use_practice_policy(raw_remote_policy)
        self.remote_use_practice_audit = self._normalize_remote_use_practice_audit(raw_remote_audit)
        self.remote_practice_billing = self._normalize_remote_practice_billing(raw_remote_billing)
        self.practice_user_permissions = self._normalize_practice_user_permissions(raw_practice_permissions)
        self._sync_connectivity_metadata()
        self.pit_address: PitAddress = self.address
        self._refresh_pit_address()
        
        self.plaza_token: Optional[str] = None
        self.token_expires_at: float = 0.0
        self._credential_retry_after: float = 0.0
        self.last_plaza_heartbeat_at: float = 0.0
        self._plaza_connection_error: str = ""
        self._plaza_agent_verification_cache: Dict[str, Dict[str, Any]] = {}
        self._plaza_user_verification_cache: Dict[str, Dict[str, Any]] = {}
        self._remote_protocol_cache: Dict[str, Dict[str, Any]] = {}
        self._remote_target_directory_cache: Dict[str, Dict[str, Any]] = {}
        self._plaza_verification_cache_lock = threading.Lock()
        self._remote_target_directory_cache_lock = threading.Lock()
        self._register_lock = threading.Lock()
        self._plaza_heartbeat_thread: Optional[threading.Thread] = None
        self._plaza_heartbeat_thread_lock = threading.Lock()
        if type(self)._start_heartbeat_thread is BaseAgent._start_heartbeat_thread:
            self._heartbeat_thread = None
            self._heartbeat_thread_lock = self._plaza_heartbeat_thread_lock
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnect_thread_lock = threading.Lock()
        
        # Ensure practices list exists in card
        if "practices" not in self.agent_card:
            self.agent_card["practices"] = []
        self._practice_persistence_batch_depth = 0
        self._pending_practice_rows: Dict[str, Dict[str, Any]] = {}
        self._remote_practice_audit_table_ensured = False
        if not hasattr(self, "_runtime_lock"):
            self._runtime_lock = threading.RLock()
        if not hasattr(self, "_runtime_working"):
            self._runtime_working: Dict[str, Dict[str, Any]] = {}
        if not hasattr(self, "_runtime_work_history"):
            self._runtime_work_history: List[Dict[str, Any]] = []
        if not hasattr(self, "_runtime_work_history_limit"):
            self._runtime_work_history_limit = self.AGENT_RUNTIME_WORK_HISTORY_LIMIT
        if not hasattr(self, "_runtime_status_message"):
            self._runtime_status_message = f"Idle; {self.name} is ready."

        self._ensure_agent_practices_table()
        self._load_practices_info_from_pool()
        
        # FastAPI App
        self.app = FastAPI(title=name)
        
        self.practices: List[Practice] = []
        self.logger = logging.LoggerAdapter(logger, {"agent_name": self.name, "agent_port": self.port})
        from prompits.core.observability import RuntimeObservations, AgentLogHandler
        self.observations = RuntimeObservations(self)
        self._observation_log_handler = AgentLogHandler(self.observations)
        logger.addHandler(self._observation_log_handler)
        from prompits.core.observability_routes import install_agent_observability
        install_agent_observability(self)

        # Static Files mapping
        base_dir = Path(__file__).parent.resolve()
        static_dir = base_dir / "static"
        
        if static_dir.exists():
            self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            self.logger.info(f"Mounted static directory: {static_dir}")
        else:
            self.logger.warning(f"Static directory NOT FOUND: {static_dir}")

        self.site_settings: Dict[str, Any] = {}
        self._install_request_logging()
        self._install_configuration_owner_guard()
        self._record_agent_version_snapshot()

        self._begin_practice_persistence_batch()
        try:
            self._prune_legacy_practices()
            self.add_practice_endpoint(self._mailbox_practice_metadata())
            self._register_pool_operation_practices()
        finally:
            self._end_practice_persistence_batch()
        self._record_agent_version_snapshot()
        self.setup_routes()
        # Authentication must run before configuration and product UI guards.
        from prompits.core.call_grants import install_agent_call_grants
        install_agent_call_grants(self)

    def collect_health(self) -> Dict[str, Any]:
        """Cheap health hook; subclasses can add cached dependency checks."""
        reasons = []
        if self.plaza_url and self._plaza_connection_error:
            reasons.append("Plaza connection is recovering")
        return {"status": "degraded" if reasons else "healthy", "reasons": reasons}

    def collect_metrics(self) -> List[Dict[str, Any]]:
        """Product layers may append bounded metrics with name/value/unit/kind."""
        return [{"name": "active_jobs", "value": len(self.working_list), "unit": "count", "kind": "gauge", "labels": {}}]

    def track_operation(self, label: str, *, kind: str = "job", job_id: str = ""):
        """Wrap background work with current-job tracking and trace context."""
        from contextlib import contextmanager
        @contextmanager
        def track():
            work_id = self._start_runtime_work(label=label, kind=kind, detail=label, work_id=job_id or None)
            with self.observations.span(label, kind=kind, job_id=job_id or work_id):
                try:
                    yield work_id
                except BaseException as exc:
                    self._finish_runtime_work(work_id, status="error", error=type(exc).__name__)
                    raise
                else:
                    self._finish_runtime_work(work_id, status="success")
        return track()

    @property
    @abstractmethod
    def status(self) -> str:
        """Return this agent's live work status."""
        raise NotImplementedError("BaseAgent subclasses must implement status")

    @property
    @abstractmethod
    def working_list(self) -> List[Dict[str, Any]]:
        """Return live work items currently handled by this agent.

        The returned list must be JSON-safe and should include long-running
        work, external API calls, queued jobs, or other visible waiting states.
        """
        raise NotImplementedError("BaseAgent subclasses must implement working_list")

    @property
    @abstractmethod
    def work_history(self) -> List[Dict[str, Any]]:
        """Return recent completed work items handled by this agent.

        The returned list must be JSON-safe and should include recent completed
        work, errors, and long-running operations that have left the live list.
        """
        raise NotImplementedError("BaseAgent subclasses must implement work_history")

    @staticmethod
    def _runtime_now() -> str:
        """Return the current UTC timestamp for runtime status entries."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _runtime_json_safe(value: Any) -> Any:
        """Return a JSON-safe runtime metadata value."""
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    @classmethod
    def _runtime_metadata_dict(cls, value: Any) -> Dict[str, Any]:
        """Return a JSON-safe metadata dictionary for runtime entries."""
        if not isinstance(value, dict):
            return {"value": cls._runtime_json_safe(value)} if value not in (None, "") else {}
        return {
            str(key): cls._runtime_json_safe(item)
            for key, item in value.items()
        }

    def _runtime_working_items(self) -> List[Dict[str, Any]]:
        """Return generic live work items tracked by BaseAgent."""
        self._expire_stale_runtime_work()
        with self._runtime_lock:
            items = sorted(
                [dict(item) for item in self._runtime_working.values()],
                key=lambda item: float(item.get("_started_perf") or 0.0),
            )
        return [
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or ""),
                "status": str(item.get("status") or "running"),
                "detail": str(item.get("detail") or ""),
                "started_at": str(item.get("started_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "kind": str(item.get("kind") or ""),
            }
            for item in items
        ]

    def _runtime_work_history_items(self) -> List[Dict[str, Any]]:
        """Return generic completed work items tracked by BaseAgent."""
        self._expire_stale_runtime_work()
        with self._runtime_lock:
            return [dict(item) for item in self._runtime_work_history]

    def _expire_stale_runtime_work(
        self,
        *,
        stale_after_seconds: Optional[float] = None,
        reason: str = "",
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> int:
        """Move stale live work items to history so status views recover."""
        threshold = (
            self.AGENT_RUNTIME_WORK_STALE_AFTER_SEC
            if stale_after_seconds is None
            else stale_after_seconds
        )
        try:
            threshold_value = float(threshold)
        except (TypeError, ValueError):
            threshold_value = float(self.AGENT_RUNTIME_WORK_STALE_AFTER_SEC)
        if threshold_value <= 0:
            return 0

        expired: List[Dict[str, Any]] = []
        now_perf = time.perf_counter()
        ended_at = self._runtime_now()
        with self._runtime_lock:
            for item_id, item in list(self._runtime_working.items()):
                if predicate is not None and not predicate(dict(item)):
                    continue
                started_perf = float(item.get("_started_perf") or now_perf)
                age_seconds = max(now_perf - float(item.get("_updated_perf") or started_perf), 0.0)
                if age_seconds < threshold_value:
                    continue
                stale_item = self._runtime_working.pop(item_id, None)
                if not stale_item:
                    continue
                stale_item["status"] = "expired"
                stale_item["detail"] = reason or (
                    f"Expired stale {stale_item.get('label') or 'work'} after "
                    f"{int(age_seconds)} seconds."
                )
                stale_item["error"] = (
                    f"Runtime work item exceeded stale threshold "
                    f"({int(threshold_value)} seconds)."
                )
                stale_item["ended_at"] = ended_at
                stale_item["updated_at"] = ended_at
                stale_item["duration_ms"] = int(age_seconds * 1000)
                expired.append(stale_item)
            if expired and not self._runtime_working:
                self._runtime_status_message = (
                    f"Idle; expired stale {expired[-1].get('label') or 'work'}."
                )
        for item in expired:
            self._append_runtime_work_history(item)
        return len(expired)

    def _current_runtime_status(self, *, default: Optional[str] = None) -> str:
        """Return a status message from the generic runtime tracker."""
        with self._runtime_lock:
            active_items = sorted(
                self._runtime_working.values(),
                key=lambda item: float(item.get("_started_perf") or 0.0),
            )
            if active_items:
                latest = active_items[-1]
                return str(latest.get("detail") or latest.get("label") or "Working.").strip()
            status_message = str(self._runtime_status_message or "").strip()
        return status_message or str(default or f"Idle; {self.name} is ready.")

    def _start_runtime_work(
        self,
        *,
        label: str,
        detail: str = "",
        kind: str = "work",
        work_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Start tracking one live work item."""
        item_id = str(work_id or f"work-{uuid.uuid4()}").strip()
        now = self._runtime_now()
        item = {
            "id": item_id,
            "label": str(label or "Work"),
            "status": "running",
            "detail": str(detail or label or "Working."),
            "kind": str(kind or "work"),
            "started_at": now,
            "updated_at": now,
            "metadata": self._runtime_metadata_dict(metadata or {}),
            "_started_perf": time.perf_counter(),
        }
        with self._runtime_lock:
            self._runtime_working[item_id] = item
            self._runtime_status_message = item["detail"]
        return item_id

    def _update_runtime_work(
        self,
        work_id: str,
        *,
        status: Optional[str] = None,
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update one live work item if it is still active."""
        item_id = str(work_id or "").strip()
        if not item_id:
            return
        with self._runtime_lock:
            item = self._runtime_working.get(item_id)
            if not item:
                return
            if status is not None:
                item["status"] = str(status)
            if detail is not None:
                item["detail"] = str(detail)
                self._runtime_status_message = str(detail)
            if metadata:
                existing = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                item["metadata"] = {**existing, **self._runtime_metadata_dict(metadata)}
            item["updated_at"] = self._runtime_now()
            item["_updated_perf"] = time.perf_counter()

    def _append_runtime_work_history(self, item: Dict[str, Any]) -> None:
        """Append one completed work item to the generic runtime log."""
        if not isinstance(item, dict):
            return
        history_item = dict(item)
        history_item.pop("_started_perf", None)
        history_item.pop("_updated_perf", None)
        history_item["metadata"] = self._runtime_metadata_dict(history_item.get("metadata") or {})
        with self._runtime_lock:
            self._runtime_work_history.insert(0, history_item)
            limit = max(1, int(getattr(self, "_runtime_work_history_limit", self.AGENT_RUNTIME_WORK_HISTORY_LIMIT) or 1))
            del self._runtime_work_history[limit:]

    def _finish_runtime_work(
        self,
        work_id: str,
        *,
        status: str = "success",
        detail: Optional[str] = None,
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Stop tracking one live work item and write it to the work log."""
        item_id = str(work_id or "").strip()
        if not item_id:
            return
        ended_at = self._runtime_now()
        with self._runtime_lock:
            item = self._runtime_working.pop(item_id, None)
            if not item:
                return
            started_perf = float(item.get("_started_perf") or time.perf_counter())
            item["status"] = str(status or "success")
            if detail is not None:
                item["detail"] = str(detail)
            if error:
                item["error"] = str(error)
            if metadata:
                existing = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                item["metadata"] = {**existing, **self._runtime_metadata_dict(metadata)}
            item["ended_at"] = ended_at
            item["updated_at"] = ended_at
            item["duration_ms"] = max(0, int((time.perf_counter() - started_perf) * 1000))
            if not self._runtime_working:
                self._runtime_status_message = (
                    f"Idle; last completed {item.get('label') or 'work'}."
                    if str(status or "").lower() in {"success", "completed", "ok"}
                    else f"Idle; last failed {item.get('label') or 'work'}."
                )
        self._append_runtime_work_history(item)
        completion = self.observations.event("job_finish", job_id=item_id, operation=str(item.get("label") or "job"),
                                             status=str(item.get("status") or "unknown"), duration_ms=item.get("duration_ms"))
        self.observations.metric("jobs_finished_total", 1, kind="counter", labels={"status": str(item.get("status") or "unknown")})
        return completion

    @staticmethod
    def _request_path_for_runtime(request: Request) -> str:
        """Return the request path used in runtime status entries."""
        query = f"?{request.url.query}" if request.url.query else ""
        return f"{request.url.path}{query}"

    @staticmethod
    def _should_track_request_work(request: Request) -> bool:
        """Return whether this request should appear in live work/log views."""
        path = request.url.path
        method = request.method.upper()
        ignored_prefixes = (
            "/api/observability",
            "/observability",
            "/api/agent/diagnostics",
            "/static",
            "/favicon",
            "/health",
            "/heartbeat",
            "/register",
            "/renew",
            "/authenticate",
            "/search",
            "/readiness",
            "/api/readiness",
            "/api/plazas_status",
            "/api/ui_",
            "/api/ui_visit_logs",
            "/api/ui_auth/config",
            "/api/ui_auth/me",
            "/api/ui_auth/refresh",
            "/api/ui_auth/session",
            "/api/ui_agent_keys/authenticate",
            "/api/observability/local",
            "/api/observability/snapshot",
            "/api/agent/status",
            "/_agent/api/status",
            "/api/agent/working-stream",
            "/_agent/api/working-stream",
            "/api/agent/work-log",
            "/_agent/api/work-log",
            "/api/agent/version",
            "/api/agent/versions",
            "/_agent/api/version",
        )
        ignored_raw_prefixes = (
            "/api/ui_",
        )
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in ignored_prefixes):
            return False
        if any(path.startswith(prefix) for prefix in ignored_raw_prefixes):
            return False
        return method != "OPTIONS"

    def _runtime_property_is_implemented(self, property_name: str) -> bool:
        """Return whether a mandatory runtime property is available."""
        try:
            getattr(type(self), property_name)
        except AttributeError:
            return False
        return True

    @staticmethod
    def _normalize_working_item(item: Any, index: int = 0) -> Dict[str, Any]:
        """Normalize one live working-list item for public API responses."""
        if isinstance(item, Mapping):
            source = dict(item)
        else:
            source = {"label": str(item or "").strip()}
        item_id = str(
            source.get("id")
            or source.get("job_id")
            or source.get("task_id")
            or source.get("request_id")
            or ""
        ).strip()
        label = str(
            source.get("label")
            or source.get("title")
            or source.get("name")
            or source.get("message")
            or source.get("detail")
            or item_id
            or f"work-{index + 1}"
        ).strip()
        return {
            "id": item_id or f"work-{index + 1}",
            "label": label,
            "status": str(source.get("status") or source.get("phase") or "working").strip() or "working",
            "detail": str(source.get("detail") or source.get("message") or source.get("description") or "").strip(),
            "started_at": str(source.get("started_at") or source.get("startedAt") or "").strip(),
            "updated_at": str(source.get("updated_at") or source.get("updatedAt") or "").strip(),
        }

    @classmethod
    def _normalize_work_history_item(cls, item: Any, index: int = 0) -> Dict[str, Any]:
        """Normalize one completed work-history item for public API responses."""
        normalized = cls._normalize_working_item(item, index)
        source = dict(item) if isinstance(item, Mapping) else {}
        normalized.update(
            {
                "ended_at": str(source.get("ended_at") or source.get("endedAt") or "").strip(),
                "duration_ms": source.get("duration_ms", source.get("durationMs", None)),
                "error": str(source.get("error") or "").strip(),
                "kind": str(source.get("kind") or source.get("type") or "").strip(),
            }
        )
        return normalized

    def _public_agent_runtime_status(self) -> Dict[str, Any]:
        """Return this agent's public live status and working-list payload."""
        status_implemented = True
        working_implemented = True
        history_implemented = True

        status_value = "unknown"
        status_error = ""
        try:
            status_value = str(self.status or "").strip() or "unknown"
        except Exception as exc:
            status_value = "error"
            status_error = str(exc)

        working_items: List[Dict[str, Any]] = []
        working_error = ""
        try:
            raw_items = self.working_list
            if isinstance(raw_items, list):
                working_items = [
                    self._normalize_working_item(item, index)
                    for index, item in enumerate(raw_items)
                ]
            elif raw_items:
                working_items = [self._normalize_working_item(raw_items)]
        except Exception as exc:
            working_error = str(exc)

        history_items: List[Dict[str, Any]] = []
        history_error = ""
        try:
            raw_history = self.work_history
            if isinstance(raw_history, list):
                history_items = [
                    self._normalize_work_history_item(item, index)
                    for index, item in enumerate(raw_history)
                ]
            elif raw_history:
                history_items = [self._normalize_work_history_item(raw_history)]
        except Exception as exc:
            history_error = str(exc)

        return {
            "status": "success",
            "agent": self._agent_readiness_identity(),
            "agent_status": {
                "implemented": status_implemented,
                "value": status_value,
                "detail": status_error,
            },
            "working": {
                "implemented": working_implemented,
                "items": working_items,
                "detail": working_error,
            },
            "work_history": {
                "implemented": history_implemented,
                "items": history_items,
                "detail": history_error,
            },
            "working_list": working_items,
            "work_history_list": history_items,
            "work_log": {
                "implemented": history_implemented,
                "items": history_items,
                "detail": history_error,
            },
            "work_log_list": history_items,
        }

    def _sync_agent_version_metadata(self) -> None:
        """Attach Prompits runtime version metadata to the public agent card."""
        meta = self.agent_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.agent_card["meta"] = meta
        version = str(self.AGENT_RUNTIME_VERSION)
        protocol_version = str(self.AGENT_PROTOCOL_VERSION)
        self.agent_card["version"] = version
        meta["prompits_agent_version"] = version
        meta["prompits_runtime_version"] = version
        meta["prompits_protocol_version"] = protocol_version
        meta["prompits_protocol_family"] = self._agent_protocol_family(protocol_version)

    @staticmethod
    def _agent_protocol_family(protocol_version: Any) -> str:
        """Return the major protocol family for one agent protocol version."""
        text = str(protocol_version or "").strip().lower()
        if not text:
            return "legacy"
        if text == "legacy":
            return "legacy"
        return text.split(".", 1)[0]

    def _agent_version_history_dir(self) -> Path:
        """Return the directory where this agent stores version snapshots."""
        configured = str(os.getenv("PROMPITS_AGENT_VERSION_HISTORY_DIR") or "").strip()
        root = Path(configured).expanduser() if configured else Path.home() / ".prompits" / "agent_versions"
        folder_name = "-".join(
            [
                self._safe_version_part(self.__class__.__module__),
                self._safe_version_part(self.__class__.__name__),
                self._safe_version_part(self.name),
                self._safe_version_part(self.port),
            ]
        )
        path = root / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _agent_version_history_path(self) -> Path:
        """Return the on-disk history path for this agent instance."""
        return self._agent_version_history_dir() / self.VERSION_HISTORY_FILENAME

    def _redact_version_snapshot(self, value: Any) -> Any:
        """Return a JSON-safe copy of version metadata with secret fields redacted."""
        if isinstance(value, dict):
            redacted: Dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key or "").lower()
                if any(token in lowered for token in self.VERSION_SECRET_KEY_TOKENS):
                    redacted[key] = "***"
                else:
                    redacted[key] = self._redact_version_snapshot(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_version_snapshot(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_version_snapshot(item) for item in value]
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    def _current_agent_version_record(self) -> Dict[str, Any]:
        """Build the current version snapshot record."""
        now = datetime.now(timezone.utc).isoformat()
        version = str(self.AGENT_RUNTIME_VERSION)
        return {
            "id": version,
            "version": version,
            "protocol_version": str(self.AGENT_PROTOCOL_VERSION),
            "created_at": now,
            "updated_at": now,
            "agent": {
                "name": self.name,
                "class": f"{self.__class__.__module__}.{self.__class__.__name__}",
                "role": self.agent_card.get("role", ""),
                "pit_type": self.agent_card.get("pit_type", "Agent"),
                "address": self.agent_card.get("address", ""),
                "host": self.host,
                "port": self.port,
            },
            "snapshot": {
                "agent_card": self._redact_version_snapshot(copy.deepcopy(self.agent_card)),
                "practices": self._redact_version_snapshot(copy.deepcopy(self.agent_card.get("practices", []))),
                "settings": self._redact_version_snapshot(copy.deepcopy(self.site_settings)),
            },
        }

    def _load_agent_version_history(self) -> Dict[str, Any]:
        """Load this agent's version history payload."""
        path = self._agent_version_history_path()
        if not path.exists():
            return {"agent_name": self.name, "current_version": str(self.AGENT_RUNTIME_VERSION), "history": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"agent_name": self.name, "current_version": str(self.AGENT_RUNTIME_VERSION), "history": []}
        if not isinstance(payload, dict):
            return {"agent_name": self.name, "current_version": str(self.AGENT_RUNTIME_VERSION), "history": []}
        history = payload.get("history")
        if not isinstance(history, list):
            payload["history"] = []
        payload["agent_name"] = self.name
        payload["current_version"] = str(self.AGENT_RUNTIME_VERSION)
        return payload

    def _save_agent_version_history(self, payload: Dict[str, Any]) -> None:
        """Persist this agent's version history payload."""
        path = self._agent_version_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n")

    def _record_agent_version_snapshot(self) -> Dict[str, Any]:
        """Upsert the current runtime version into history."""
        self._sync_agent_version_metadata()
        payload = self._load_agent_version_history()
        history = [entry for entry in payload.get("history", []) if isinstance(entry, dict)]
        current = self._current_agent_version_record()
        existing_idx = next(
            (idx for idx, entry in enumerate(history) if str(entry.get("version") or entry.get("id") or "") == current["version"]),
            None,
        )
        if existing_idx is None:
            history.append(current)
        else:
            existing = history[existing_idx]
            current["created_at"] = str(existing.get("created_at") or current["created_at"])
            history[existing_idx] = current
        history.sort(key=lambda entry: str(entry.get("created_at") or ""), reverse=True)
        payload = {
            "agent_name": self.name,
            "agent_class": f"{self.__class__.__module__}.{self.__class__.__name__}",
            "current_version": str(self.AGENT_RUNTIME_VERSION),
            "history": history,
        }
        self._save_agent_version_history(payload)
        return current

    def _public_agent_version_history(self) -> Dict[str, Any]:
        """Return public version history metadata with download URLs."""
        self._record_agent_version_snapshot()
        payload = self._load_agent_version_history()
        history = []
        for entry in payload.get("history", []) or []:
            if not isinstance(entry, dict):
                continue
            version_id = str(entry.get("id") or entry.get("version") or "").strip()
            public_entry = {
                "id": version_id,
                "version": str(entry.get("version") or version_id),
                "protocol_version": str(entry.get("protocol_version") or ""),
                "created_at": str(entry.get("created_at") or ""),
                "updated_at": str(entry.get("updated_at") or ""),
                "agent": dict(entry.get("agent") or {}),
                "download_url": f"/api/agent/versions/{version_id}/download" if version_id else "",
            }
            history.append(public_entry)
        current_version = str(self.AGENT_RUNTIME_VERSION)
        current = next((entry for entry in history if entry.get("version") == current_version), None)
        return {
            "status": "success",
            "agent": {
                "name": self.name,
                "class": f"{self.__class__.__module__}.{self.__class__.__name__}",
                "address": self.agent_card.get("address", ""),
            },
            "current_version": current_version,
            "protocol_version": str(self.AGENT_PROTOCOL_VERSION),
            "current": current,
            "history": history,
        }

    @classmethod
    def _remote_protocol_cache_key(cls, target_url: Any) -> str:
        """Return the cache key for one remote protocol lookup."""
        return cls._normalize_url(target_url)

    def _get_cached_remote_protocol(self, target_url: Any) -> Optional[Dict[str, Any]]:
        """Return one recent remote protocol lookup result."""
        key = self._remote_protocol_cache_key(target_url)
        if not key:
            return None
        now = time.time()
        with self._plaza_verification_cache_lock:
            cached = self._remote_protocol_cache.get(key)
            if not isinstance(cached, dict):
                return None
            looked_up_at = float(cached.get("looked_up_at") or 0.0)
            if looked_up_at <= 0 or now - looked_up_at > float(self.REMOTE_PROTOCOL_CACHE_TTL):
                self._remote_protocol_cache.pop(key, None)
                return None
            return dict(cached.get("payload") or {})

    def _set_cached_remote_protocol(self, target_url: Any, payload: Dict[str, Any]) -> None:
        """Cache one remote protocol lookup result."""
        key = self._remote_protocol_cache_key(target_url)
        if not key:
            return
        with self._plaza_verification_cache_lock:
            self._remote_protocol_cache[key] = {
                "looked_up_at": time.time(),
                "payload": dict(payload or {}),
            }

    def _infer_remote_protocol_from_payload(self, payload: Any) -> Dict[str, Any]:
        """Extract remote protocol metadata from one version-like payload."""
        protocol_version = ""
        runtime_version = ""
        agent_name = ""
        if isinstance(payload, dict):
            protocol_version = str(
                payload.get("protocol_version")
                or (payload.get("current") or {}).get("protocol_version")
                or (payload.get("agent") or {}).get("protocol_version")
                or ""
            ).strip()
            runtime_version = str(
                payload.get("current_version")
                or payload.get("version")
                or (payload.get("current") or {}).get("version")
                or ""
            ).strip()
            agent_name = str((payload.get("agent") or {}).get("name") or payload.get("agent_name") or payload.get("agent") or "").strip()
        protocol_version = protocol_version or "legacy"
        return {
            "protocol_version": protocol_version,
            "protocol_family": self._agent_protocol_family(protocol_version),
            "runtime_version": runtime_version,
            "agent_name": agent_name,
        }

    def _discover_remote_protocol(self, target: Dict[str, Any]) -> Dict[str, Any]:
        """Query one remote agent for protocol metadata with a legacy fallback."""
        target_url = self._normalize_url(target.get("url"))
        cached = self._get_cached_remote_protocol(target_url)
        if cached:
            return cached
        info: Dict[str, Any] = {
            "protocol_version": "legacy",
            "protocol_family": "legacy",
            "runtime_version": "",
            "agent_name": str(target.get("agent_id") or ""),
            "source": "default",
            "compatible": True,
        }
        card = target.get("card") if isinstance(target.get("card"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        if str(meta.get("prompits_protocol_version") or "").strip():
            info.update(
                {
                    "protocol_version": str(meta.get("prompits_protocol_version")).strip(),
                    "protocol_family": self._agent_protocol_family(meta.get("prompits_protocol_version")),
                    "runtime_version": str(meta.get("prompits_runtime_version") or card.get("version") or "").strip(),
                    "agent_name": str(card.get("name") or info.get("agent_name") or ""),
                    "source": "card",
                }
            )
        if target_url:
            for path in ("/api/agent/version", "/_agent/api/version"):
                try:
                    response = requests.get(
                        f"{target_url}{path}",
                        timeout=min(self.REMOTE_PROTOCOL_DISCOVERY_TIMEOUT, self.REMOTE_PRACTICE_REQUEST_TIMEOUT),
                    )
                except Exception:
                    continue
                if response.status_code != 200:
                    continue
                try:
                    payload = response.json()
                except Exception:
                    continue
                info.update(self._infer_remote_protocol_from_payload(payload))
                info["source"] = path
                break
        local_family = self._agent_protocol_family(self.AGENT_PROTOCOL_VERSION)
        remote_family = self._agent_protocol_family(info.get("protocol_version"))
        info["compatible"] = remote_family in ("legacy", local_family)
        self._set_cached_remote_protocol(target_url, info)
        return info

    def _assert_remote_protocol_compatible(
        self,
        *,
        target: Dict[str, Any],
        remote_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Reject incompatible remote protocol families while allowing legacy peers."""
        info = dict(remote_info or self._discover_remote_protocol(target))
        if info.get("compatible"):
            return info
        remote_version = str(info.get("protocol_version") or "legacy")
        remote_name = str(info.get("agent_name") or target.get("agent_id") or target.get("url") or "remote agent")
        local_version = str(self.AGENT_PROTOCOL_VERSION)
        raise HTTPException(
            status_code=409,
            detail=(
                f"Remote agent '{remote_name}' uses incompatible protocol version "
                f"'{remote_version}' (local '{local_version}')."
            ),
        )

    def _assert_inbound_protocol_compatible(
        self,
        *,
        caller_protocol_version: Any,
        caller_name: str = "",
    ) -> Dict[str, Any]:
        """Reject incompatible caller protocol families while allowing legacy callers."""
        remote_version = str(caller_protocol_version or "").strip() or "legacy"
        remote_family = self._agent_protocol_family(remote_version)
        local_family = self._agent_protocol_family(self.AGENT_PROTOCOL_VERSION)
        compatible = remote_family in ("legacy", local_family)
        if not compatible:
            remote_label = caller_name or "remote caller"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Caller '{remote_label}' uses incompatible protocol version "
                    f"'{remote_version}' (local '{self.AGENT_PROTOCOL_VERSION}')."
                ),
            )
        return {
            "protocol_version": remote_version,
            "protocol_family": remote_family,
            "compatible": compatible,
        }

    def _agent_version_download_record(self, version_id: str) -> Dict[str, Any]:
        """Return a saved version snapshot by id or version."""
        wanted = str(version_id or "").strip()
        if not wanted:
            raise KeyError(version_id)
        self._record_agent_version_snapshot()
        payload = self._load_agent_version_history()
        for entry in payload.get("history", []) or []:
            if not isinstance(entry, dict):
                continue
            if wanted in {str(entry.get("id") or ""), str(entry.get("version") or "")}:
                return entry
        raise KeyError(version_id)

    def _install_request_logging(self):
        """Internal helper for install request logging."""
        @self.app.middleware("http")
        async def log_incoming_request(request: Request, call_next):
            """Log the incoming request."""
            started_at = time.perf_counter()
            client_host = request.client.host if request.client else "-"
            client_port = request.client.port if request.client else "-"
            query = f"?{request.url.query}" if request.url.query else ""
            path = f"{request.url.path}{query}"
            runtime_work_id = ""
            if self._should_track_request_work(request):
                runtime_work_id = self._start_runtime_work(
                    label=f"{request.method} {request.url.path}",
                    detail=f"Handling {request.method} {path}",
                    kind="http_request",
                    metadata={
                        "method": request.method,
                        "path": request.url.path,
                        "query": request.url.query,
                        "client": f"{client_host}:{client_port}",
                    },
                )

            self.logger.info(
                f"Incoming request {request.method} {path} from {client_host}:{client_port}"
            )
            try:
                response = await call_next(request)
            except Exception:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                if runtime_work_id:
                    self._finish_runtime_work(
                        runtime_work_id,
                        status="error",
                        detail=f"Failed {request.method} {path}",
                        error="Unhandled request error",
                        metadata={"duration_ms": int(elapsed_ms)},
                    )
                self.logger.exception(
                    f"Request failed {request.method} {path} from {client_host}:{client_port} "
                    f"after {elapsed_ms:.1f}ms"
                )
                raise

            elapsed_ms = (time.perf_counter() - started_at) * 1000
            error_detail = ""
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                buffered_body: Optional[bytes] = getattr(response, "body", None)
                if buffered_body is None and hasattr(response, "body_iterator"):
                    chunks = []
                    async for chunk in response.body_iterator:
                        if isinstance(chunk, (bytes, bytearray)):
                            chunks.append(bytes(chunk))
                        else:
                            chunks.append(str(chunk).encode("utf-8", errors="replace"))
                    buffered_body = b"".join(chunks)
                    response.body_iterator = iterate_in_threadpool([buffered_body])
                error_detail = self._extract_error_detail_from_response(response, body=buffered_body)
            detail_suffix = f" detail={error_detail}" if error_detail else ""
            if runtime_work_id:
                status_text = "success" if int(getattr(response, "status_code", 0) or 0) < 400 else "error"
                self._finish_runtime_work(
                    runtime_work_id,
                    status=status_text,
                    detail=f"Completed {request.method} {path} with HTTP {response.status_code}",
                    error=error_detail if status_text == "error" else "",
                    metadata={
                        "status_code": int(getattr(response, "status_code", 0) or 0),
                        "duration_ms": int(elapsed_ms),
                    },
                )
            self.logger.info(
                f"Completed request {request.method} {path} from {client_host}:{client_port} "
                f"with {response.status_code} in {elapsed_ms:.1f}ms{detail_suffix}"
            )
            return response

    @staticmethod
    def _format_error_detail_for_log(value: Any) -> str:
        """Convert structured error payloads into concise log text."""
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [BaseAgent._format_error_detail_for_log(item) for item in value]
            return " | ".join(part for part in parts if part)
        if isinstance(value, dict):
            message = str(
                value.get("message")
                or value.get("detail")
                or value.get("error")
                or value.get("reason")
                or ""
            ).strip()
            parameters = value.get("parameters")
            if parameters is None:
                parameters = value.get("params")
            if parameters not in (None, "", {}, []):
                try:
                    parameters_text = json.dumps(parameters, sort_keys=True, default=str)
                except Exception:
                    parameters_text = str(parameters)
                return f"{message} | parameters={parameters_text}" if message else f"parameters={parameters_text}"
            reasons = value.get("reasons")
            if isinstance(reasons, list) and reasons:
                reasons_text = " | ".join(str(reason).strip() for reason in reasons if str(reason).strip())
                return f"{message} | reasons={reasons_text}" if message else reasons_text
            if value.get("loc") and value.get("msg"):
                location = value.get("loc")
                if isinstance(location, list):
                    location = ".".join(str(part) for part in location if part != "body")
                location_text = str(location or "").strip()
                message_text = str(value.get("msg") or "").strip()
                if location_text and message_text:
                    return f"{location_text}: {message_text}"
                return message_text or location_text
            if message:
                return message
            try:
                return json.dumps(value, sort_keys=True, default=str)
            except Exception:
                return str(value)
        return str(value)

    @classmethod
    def _extract_error_detail_from_response(cls, response: Any, *, body: Optional[bytes] = None) -> str:
        """Return a readable error detail string from a response object."""
        body = body if body is not None else getattr(response, "body", None)
        content_type = str(getattr(response, "headers", {}).get("content-type") or "").lower()

        parsed: Any = None
        if isinstance(body, (bytes, bytearray)) and body:
            text = body.decode("utf-8", errors="replace").strip()
            if text:
                if "application/json" in content_type:
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = text
                else:
                    parsed = text
        elif hasattr(response, "json") and hasattr(response, "content"):
            try:
                if getattr(response, "content", None):
                    parsed = response.json()
            except Exception:
                parsed = getattr(response, "text", "")

        if isinstance(parsed, dict):
            detail_value = parsed.get("detail")
            if detail_value in (None, ""):
                detail_value = parsed.get("message")
            if detail_value in (None, ""):
                detail_value = parsed
            return cls._format_error_detail_for_log(detail_value)
        if isinstance(parsed, list):
            return cls._format_error_detail_for_log(parsed)

        text_value = str(parsed or getattr(response, "text", "") or "").strip()
        return cls._format_error_detail_for_log(text_value)

    @classmethod
    def _heartbeat_is_active(cls, last_active: Any) -> bool:
        """Return whether the heartbeat is active."""
        try:
            timestamp = float(last_active or 0)
        except (TypeError, ValueError):
            return False
        if timestamp <= 0:
            return False
        return max(0.0, time.time() - timestamp) <= cls.PLAZA_CONNECTION_ACTIVE_WINDOW_SEC

    def _mark_plaza_connection_alive(self, timestamp: Optional[float] = None):
        """Internal helper for mark Plaza connection alive."""
        self.last_plaza_heartbeat_at = float(timestamp or time.time())
        self._plaza_connection_error = ""

    def _fetch_remote_plaza_last_active(self, headers: Optional[Dict[str, str]] = None) -> float:
        """Internal helper to fetch the remote Plaza last active."""
        if not self.plaza_url or not self.agent_id:
            return 0.0

        request_headers = headers if isinstance(headers, dict) and headers.get("Authorization") else self._ensure_token_valid()
        if not request_headers or not request_headers.get("Authorization"):
            return 0.0

        try:
            response = self._plaza_get(
                "/search",
                params={"agent_id": self.agent_id},
                headers=request_headers,
                timeout=self.PLAZA_CONNECTION_REQUEST_TIMEOUT,
                retries=0,
            )
        except Exception as exc:
            self._plaza_connection_error = self._plaza_connection_error or str(exc)
            return 0.0

        if response.status_code in (401, 403):
            self._plaza_connection_error = self._plaza_connection_error or f"Plaza rejected agent lookup ({response.status_code})"
            return 0.0
        if response.status_code != 200:
            return 0.0

        try:
            payload = response.json() if response.content else []
        except Exception:
            return 0.0
        if not isinstance(payload, list) or not payload:
            return 0.0
        first = payload[0] if isinstance(payload[0], dict) else {}
        return float(first.get("last_active") or 0)

    def get_plaza_connection_status(self) -> Dict[str, Any]:
        """Return the Plaza connection status."""
        normalized_plaza_url = self._normalize_url(self.plaza_url)
        base_status = {
            "status": "success",
            "plaza_url": normalized_plaza_url,
            "agent_name": self.name,
            "agent_id": str(self.agent_id or ""),
            "online": False,
            "authenticated": False,
            "last_active": 0.0,
            "connection_status": "not_configured",
            "error": "",
        }

        if not normalized_plaza_url:
            return base_status

        try:
            health = self._plaza_get(
                "/health",
                plaza_url=normalized_plaza_url,
                timeout=self.PLAZA_CONNECTION_REQUEST_TIMEOUT,
                retries=0,
            )
            base_status["online"] = health.status_code == 200
        except Exception as exc:
            self._plaza_connection_error = self._plaza_connection_error or str(exc)

        headers = self._ensure_token_valid()
        base_status["authenticated"] = bool(headers and headers.get("Authorization"))

        remote_last_active = self._fetch_remote_plaza_last_active(headers=headers)
        last_active = max(float(self.last_plaza_heartbeat_at or 0), float(remote_last_active or 0))
        base_status["last_active"] = last_active
        base_status["connection_status"] = (
            "connected"
            if base_status["online"] and self._heartbeat_is_active(last_active)
            else "disconnected"
        )

        if self._plaza_connection_error:
            base_status["error"] = self._plaza_connection_error
        elif not base_status["online"]:
            base_status["error"] = "Plaza is unreachable."
        elif not base_status["authenticated"]:
            base_status["error"] = "Waiting for Plaza authentication."
        elif not last_active:
            base_status["error"] = "No heartbeat reported yet."

        return base_status

    @staticmethod
    def _readiness_status_is_ready(payload: Dict[str, Any]) -> bool:
        """Return whether a readiness-style payload reports a ready agent."""
        status = str(payload.get("status") or "").strip().lower()
        if payload.get("ready") is False:
            return False
        return status not in {"blocked", "degraded", "error", "offline", "unavailable", "unready"}

    @staticmethod
    def _readiness_detail(payload: Dict[str, Any], fallback: str = "") -> str:
        """Return a short readiness detail string."""
        for key in ("detail", "message", "error"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        issues = payload.get("issues")
        if isinstance(issues, list) and issues:
            return "; ".join(str(issue) for issue in issues[:3] if str(issue).strip())
        status = str(payload.get("status") or "").strip()
        return status or fallback

    @staticmethod
    def _normalize_agent_url(value: Any) -> str:
        """Normalize an agent base URL."""
        text = str(value or "").strip()
        if not text:
            return ""
        return text.rstrip("/")

    def _agent_readiness_identity(self) -> Dict[str, Any]:
        """Return public identity fields for readiness responses."""
        meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        address = self.agent_card.get("address") or self._resolve_advertised_address()
        return {
            "id": str(self.agent_id or self.agent_card.get("agent_id") or ""),
            "name": self.name,
            "pit_type": str(self.agent_card.get("pit_type") or meta.get("pit_type") or self.__class__.__name__),
            "address": str(address or ""),
        }

    def readiness_dependency_definitions(self, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return generic dependencies this agent should verify before work starts."""
        del params
        meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        raw_dependencies: List[Any] = []
        for container in (self.agent_card, meta):
            if not isinstance(container, dict):
                continue
            for key in ("dependencies", "dependent_agents", "required_agents", "requires"):
                value = container.get(key)
                if isinstance(value, list):
                    raw_dependencies.extend(value)

        dependencies: List[Dict[str, Any]] = []
        for item in raw_dependencies:
            if isinstance(item, Mapping):
                dependencies.append(dict(item))
            elif str(item or "").strip():
                dependencies.append({"name": str(item).strip()})
        return dependencies

    def _dependency_remote_target(self, dependency: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Resolve a readiness dependency into a target URL and card."""
        direct_url = self._normalize_agent_url(
            dependency.get("url")
            or dependency.get("address")
            or dependency.get("agent_url")
            or dependency.get("target_url")
        )
        if direct_url:
            card = dependency.get("card") if isinstance(dependency.get("card"), dict) else {}
            resolved_card = dict(card)
            resolved_card.setdefault("name", dependency.get("name") or dependency.get("agent_name") or direct_url)
            resolved_card.setdefault("address", direct_url)
            return {
                "agent_id": str(dependency.get("agent_id") or dependency.get("pit_id") or dependency.get("id") or ""),
                "card": resolved_card,
                "url": direct_url,
            }

        pit_address = dependency.get("pit_address") or dependency.get("agent_address")
        target = self._resolve_remote_target(pit_address or dependency)
        if target:
            return target

        target_id = str(
            dependency.get("agent_id")
            or dependency.get("pit_id")
            or dependency.get("id")
            or dependency.get("name")
            or ""
        ).strip()
        if not target_id:
            return None
        info = self.lookup_agent_info(target_id)
        if not info:
            return None
        card = info.get("card") if isinstance(info.get("card"), dict) else {}
        target_url = self._normalize_agent_url(card.get("address") or info.get("address"))
        if not target_url:
            return None
        return {"agent_id": str(info.get("agent_id") or target_id), "card": card, "url": target_url}

    def check_readiness_dependency(
        self,
        dependency: Dict[str, Any],
        *,
        depth: int = 0,
        timeout: float = 240.0,
    ) -> Dict[str, Any]:
        """Check one downstream agent dependency."""
        label = str(
            dependency.get("name")
            or dependency.get("agent_name")
            or dependency.get("id")
            or dependency.get("agent_id")
            or "dependency"
        ).strip()
        role = str(dependency.get("role") or dependency.get("pit_type") or "agent").strip() or "agent"
        result = {
            "id": str(dependency.get("id") or dependency.get("agent_id") or dependency.get("pit_id") or ""),
            "name": label,
            "role": role,
            "status": "missing",
            "ready": False,
            "detail": "Dependency target is not configured.",
        }
        for key in ("pulse_name", "pulse_address", "practice_id", "path"):
            if dependency.get(key):
                result[key] = dependency.get(key)

        target = self._dependency_remote_target(dependency)
        if not target:
            return result

        target_url = self._normalize_agent_url(target.get("url"))
        card = target.get("card") if isinstance(target.get("card"), dict) else {}
        result.update(
            {
                "id": str(target.get("agent_id") or result.get("id") or card.get("agent_id") or ""),
                "name": str(card.get("name") or result.get("name") or target_url),
                "role": str(card.get("pit_type") or card.get("role") or result.get("role") or "agent"),
                "url": target_url,
            }
        )
        if not target_url:
            result.update({"status": "missing", "detail": "Dependency target URL is not configured."})
            return result

        probe_paths = []
        dependency_mode = str(
            dependency.get("readiness_mode")
            or dependency.get("dependency_mode")
            or dependency.get("probe_mode")
            or ""
        ).strip().lower()
        if depth > 0 and dependency_mode in {"deep", "readiness", "recursive"}:
            probe_paths.append(f"/api/readiness?depth={max(depth - 1, 0)}")
        probe_paths.append("/health")

        last_detail = ""
        for path in probe_paths:
            try:
                response = requests.get(f"{target_url}{path}", timeout=timeout)
            except Exception as exc:
                last_detail = str(exc)
                continue

            if response.status_code == 404 and path.startswith("/api/readiness"):
                last_detail = "Readiness endpoint not available."
                continue

            payload: Dict[str, Any]
            try:
                payload = response.json() if response.content else {}
            except Exception:
                payload = {}

            if response.status_code >= 400:
                result.update(
                    {
                        "status": "degraded",
                        "ready": False,
                        "detail": self._readiness_detail(payload, f"HTTP {response.status_code}"),
                    }
                )
                return result

            if path.startswith("/api/readiness"):
                ready = self._readiness_status_is_ready(payload)
                result.update(
                    {
                        "status": "ready" if ready else str(payload.get("status") or "blocked"),
                        "ready": ready,
                        "detail": self._readiness_detail(payload, "Ready" if ready else "Not ready"),
                    }
                )
                if isinstance(payload.get("dependencies"), list):
                    result["dependencies"] = payload["dependencies"]
                if isinstance(payload.get("issues"), list):
                    result["issues"] = payload["issues"]
                return result

            status = str(payload.get("status") or "").strip().lower()
            ready = response.status_code == 200 and status not in {"error", "offline", "unavailable"}
            result.update(
                {
                    "status": "ready" if ready else "degraded",
                    "ready": ready,
                    "detail": self._readiness_detail(payload, "Health check passed" if ready else "Health check failed"),
                }
            )
            return result

        result.update({"status": "offline", "ready": False, "detail": last_detail or "Dependency is unreachable."})
        return result

    def check_readiness_dependencies(
        self,
        dependencies: List[Dict[str, Any]],
        *,
        depth: int = 0,
        timeout: float = 240.0,
    ) -> List[Dict[str, Any]]:
        """Check dependency reachability in parallel so readiness returns promptly."""
        if not dependencies:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(dependencies)
        max_workers = max(1, min(len(dependencies), 8))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self.check_readiness_dependency, dependency, depth=depth, timeout=timeout): index
                for index, dependency in enumerate(dependencies)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    dependency = dependencies[index]
                    results[index] = {
                        "id": str(dependency.get("id") or dependency.get("agent_id") or dependency.get("pit_id") or ""),
                        "name": str(dependency.get("name") or dependency.get("agent_name") or "dependency"),
                        "role": str(dependency.get("role") or dependency.get("pit_type") or "agent"),
                        "status": "offline",
                        "ready": False,
                        "detail": str(exc) or "Dependency readiness check failed.",
                    }

        return [result for result in results if result is not None]

    def build_readiness_report(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build this agent's readiness report, including generic dependencies."""
        params = dict(params or {})
        try:
            depth = int(params.get("depth", 2))
        except (TypeError, ValueError):
            depth = 2
        depth = max(depth, 0)

        dependency_mode = str(params.get("dependency_mode") or params.get("dependency_check") or "health").strip().lower()
        dependency_depth = max(depth - 1, 0) if dependency_mode in {"deep", "readiness", "recursive"} else 0
        dependencies = self.check_readiness_dependencies(
            self.readiness_dependency_definitions(params),
            depth=dependency_depth,
        )
        issues = [
            f"{dependency.get('name') or dependency.get('id') or 'dependency'}: {dependency.get('detail') or dependency.get('status')}"
            for dependency in dependencies
            if not dependency.get("ready")
        ]
        ready = not issues
        return {
            "status": "ready" if ready else "blocked",
            "ready": ready,
            "agent": self._agent_readiness_identity(),
            "dependencies": dependencies,
            "issues": issues,
        }

    def _load_plaza_credentials_from_pool(self):
        """Internal helper to load the Plaza credentials from pool."""
        if not self.pool or not self.plaza_url:
            return
        if not self._reuse_plaza_identity():
            return
        if self.agent_id and self.api_key:
            return
        try:
            creds = self.plaza_credential_store.load(agent_name=self.name, plaza_url=self.plaza_url)
            if not creds:
                return
            agent_id = creds.get("agent_id")
            api_key = creds.get("api_key")
            if agent_id and api_key:
                self.agent_id = agent_id
                self.api_key = api_key
                self.agent_card["agent_id"] = agent_id
                self._refresh_pit_address()
                self.logger.info(f"Loaded Plaza credentials from pool for {self.plaza_url}")
        except Exception as e:
            self.logger.warning(f"Failed loading Plaza credentials from pool: {e}")

    def _reuse_plaza_identity(self) -> bool:
        """Internal helper to return the reuse Plaza identity."""
        meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        configured = self._coerce_optional_bool(
            self.agent_card.get("reuse_plaza_identity")
            if self.agent_card.get("reuse_plaza_identity") is not None
            else meta.get("reuse_plaza_identity")
        )
        return True if configured is None else configured

    @staticmethod
    def _coerce_pit_address(value: Any) -> Optional[PitAddress]:
        """Internal helper to coerce the pit address."""
        if value is None:
            return None
        if isinstance(value, PitAddress):
            return value
        if isinstance(value, dict):
            return PitAddress.from_value(value)
        return None

    def _refresh_pit_address(self):
        """Internal helper to return the refresh pit address."""
        if not isinstance(self.address, PitAddress):
            self.address = PitAddress.from_value(self.address)
        self.pit_address = self.address
        if self.agent_id:
            self.pit_address.pit_id = str(self.agent_id)
        if self.plaza_url:
            self.pit_address.register_plaza(self.plaza_url)
        self.agent_card["pit_address"] = self.pit_address.to_dict()
        self.agent_card.pop("agent_address", None)

    def _resolve_advertised_address(self) -> str:
        """Internal helper to resolve the advertised address."""
        local_address = f"http://{self.host}:{self.port}"
        plaza_address = self._normalize_url(self.plaza_url)

        env_address = self._normalize_url(os.getenv("PROMPITS_PUBLIC_URL"))
        if env_address and not (
            plaza_address
            and self._same_url_origin(env_address, plaza_address)
            and env_address != local_address
        ):
            return env_address

        configured_address = self._normalize_url(self.agent_card.get("address"))
        if configured_address and not (
            plaza_address
            and self._same_url_origin(configured_address, plaza_address)
            and configured_address != local_address
        ):
            return configured_address

        return local_address

    def _resolve_trusted_plaza_urls(self) -> List[str]:
        """Return the receiver-side Plaza allowlist used for token verification."""
        meta = self.agent_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.agent_card["meta"] = meta

        trusted: List[str] = []
        for candidate in (
            self.agent_card.get("trusted_plaza_urls"),
            self.agent_card.get("trusted_plazas"),
            meta.get("trusted_plaza_urls"),
            meta.get("trusted_plazas"),
            os.getenv("PROMPITS_TRUSTED_PLAZA_URLS"),
            self.plaza_url,
        ):
            for normalized in self._normalize_url_list(candidate):
                if normalized not in trusted:
                    trusted.append(normalized)
        return trusted

    def _sync_trusted_plaza_metadata(self):
        """Persist canonical trusted Plaza metadata onto the runtime agent card."""
        meta = self.agent_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.agent_card["meta"] = meta
        trusted = self._resolve_trusted_plaza_urls()
        self.trusted_plaza_urls = list(trusted)
        self.agent_card["trusted_plaza_urls"] = list(trusted)
        meta["trusted_plaza_urls"] = list(trusted)

    def _resolve_accepts_inbound_from_plaza(self) -> bool:
        """Internal helper to resolve the accepts inbound from Plaza."""
        meta = self.agent_card.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}
            self.agent_card["meta"] = meta
        configured = self._coerce_optional_bool(self.agent_card.get("accepts_inbound_from_plaza"))
        if configured is None:
            configured = self._coerce_optional_bool(meta.get("accepts_inbound_from_plaza"))
        if configured is None:
            configured = self._coerce_optional_bool(os.getenv("PROMPITS_ACCEPTS_INBOUND_FROM_PLAZA"))
        return True if configured is None else configured

    def _sync_connectivity_metadata(self):
        """Internal helper to synchronize the connectivity metadata."""
        meta = self.agent_card.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.agent_card["meta"] = meta

        accepts_inbound = self._resolve_accepts_inbound_from_plaza()
        store_forward = store_forward_summary_from_card(self.agent_card)
        existing_mode = (
            str(self.agent_card.get("connectivity_mode") or meta.get("connectivity_mode") or "").strip()
        )
        if accepts_inbound:
            connectivity_mode = existing_mode or "plaza-forward"
        else:
            connectivity_mode = "store-forward" if store_forward.get("enabled") else "outbound-only"

        self.agent_card["accepts_inbound_from_plaza"] = accepts_inbound
        self.agent_card["accepts_direct_call"] = accepts_inbound
        self.agent_card["connectivity_mode"] = connectivity_mode
        self.agent_card["store_forward_configured"] = bool(store_forward.get("configured"))
        self.agent_card["store_forward_enabled"] = bool(store_forward.get("enabled"))
        meta["accepts_inbound_from_plaza"] = accepts_inbound
        meta["accepts_direct_call"] = accepts_inbound
        meta["connectivity_mode"] = connectivity_mode
        meta["store_forward_configured"] = bool(store_forward.get("configured"))
        meta["store_forward_enabled"] = bool(store_forward.get("enabled"))

    def _save_plaza_credentials_to_pool(self):
        """Internal helper to save the Plaza credentials to pool."""
        if not self.pool or not self.plaza_url:
            return
        if not self._reuse_plaza_identity():
            return
        if not (self.agent_id and self.api_key):
            return
        try:
            self.plaza_credential_store.save(
                self.name,
                self.agent_id,
                self.api_key,
                plaza_url=self.plaza_url,
            )
        except Exception as e:
            self.logger.warning(f"Failed saving Plaza credentials to pool: {e}")

    def _clear_plaza_credentials_in_pool(self):
        """Internal helper for clear Plaza credentials in pool."""
        if not self.pool or not self.plaza_url:
            return
        if not self._reuse_plaza_identity():
            return
        if not self.agent_id:
            return
        try:
            self.plaza_credential_store.clear(self.agent_id, self.name, plaza_url=self.plaza_url)
        except Exception as e:
            self.logger.warning(f"Failed clearing Plaza credentials in pool: {e}")

    def _plaza_request(
        self,
        method: str,
        path: str,
        *,
        plaza_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> requests.Response:
        """Internal helper for Plaza request."""
        base_url = (plaza_url or self.plaza_url or "").rstrip("/")
        if not base_url:
            raise ValueError("plaza_url is required for Plaza requests")
        request_fn = getattr(requests, method.lower())
        url = f"{base_url}/{str(path).lstrip('/')}"
        request_headers = dict(headers or {})
        request_headers.setdefault("Connection", "close")
        timeout_value = timeout if timeout is not None else self._plaza_request_timeout_value()
        retry_count = self.PLAZA_REQUEST_RETRIES if retries is None else max(int(retries), 0)
        last_response: Optional[requests.Response] = None

        for attempt in range(retry_count + 1):
            try:
                response = request_fn(
                    url,
                    headers=request_headers,
                    params=params,
                    json=json,
                    timeout=timeout_value,
                )
                if self._is_plaza_starting_response(response):
                    return response
                if response.status_code not in self.PLAZA_RETRY_STATUS_CODES or attempt == retry_count:
                    return response
                last_response = response
                self.logger.warning(
                    f"Plaza {method.upper()} {url} returned {response.status_code}; "
                    f"retrying {attempt + 1}/{retry_count}."
                )
            except requests.RequestException as exc:
                if attempt == retry_count:
                    raise
                self.logger.warning(
                    f"Plaza {method.upper()} {url} failed: {exc}. "
                    f"Retrying {attempt + 1}/{retry_count}."
                )

        if last_response is not None:
            return last_response
        raise RuntimeError(f"Plaza {method.upper()} {url} failed without a response")

    async def _plaza_request_async(
        self,
        method: str,
        path: str,
        *,
        plaza_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> httpx.Response:
        """Internal helper for Plaza request async."""
        base_url = (plaza_url or self.plaza_url or "").rstrip("/")
        if not base_url:
            raise ValueError("plaza_url is required for Plaza requests")
        url = f"{base_url}/{str(path).lstrip('/')}"
        request_headers = dict(headers or {})
        request_headers.setdefault("Connection", "close")
        timeout_value = timeout if timeout is not None else self._plaza_request_timeout_value()
        retry_count = self.PLAZA_REQUEST_RETRIES if retries is None else max(int(retries), 0)
        last_response: Optional[httpx.Response] = None

        async with httpx.AsyncClient() as client:
            for attempt in range(retry_count + 1):
                try:
                    response = await client.request(
                        method.upper(),
                        url,
                        headers=request_headers,
                        params=params,
                        json=json,
                        timeout=timeout_value,
                    )
                    if self._is_plaza_starting_response(response):
                        return response
                    if response.status_code not in self.PLAZA_RETRY_STATUS_CODES or attempt == retry_count:
                        return response
                    last_response = response
                    self.logger.warning(
                        f"Plaza {method.upper()} {url} returned {response.status_code}; "
                        f"retrying {attempt + 1}/{retry_count}."
                    )
                except httpx.RequestError as exc:
                    if attempt == retry_count:
                        raise
                    self.logger.warning(
                        f"Plaza {method.upper()} {url} failed: {exc}. "
                        f"Retrying {attempt + 1}/{retry_count}."
                    )

        if last_response is not None:
            return last_response
        raise RuntimeError(f"Plaza {method.upper()} {url} failed without a response")

    def _plaza_get(self, path: str, **kwargs: Any) -> requests.Response:
        """Internal helper for Plaza get."""
        return self._plaza_request("get", path, **kwargs)

    def _plaza_post(self, path: str, **kwargs: Any) -> requests.Response:
        """Internal helper for Plaza post."""
        return self._plaza_request("post", path, **kwargs)

    def _ensure_agent_practices_table(self):
        """Internal helper to ensure the agent practices table exists."""
        if not self.pool:
            return
        if getattr(self, "_agent_practices_table_ensured", False):
            return
        if self.pool._TableExists(self.AGENT_PRACTICES_TABLE):
            self._agent_practices_table_ensured = True
            return
        schema = agent_practices_table_schema()
        self.pool._CreateTable(self.AGENT_PRACTICES_TABLE, schema)
        self._agent_practices_table_ensured = True

    def _practice_row_id(self, practice_id: str) -> str:
        """Internal helper for practice row ID."""
        return f"{self.name}:{practice_id}"

    @staticmethod
    def _parse_practice_updated_at(value: Any) -> datetime:
        """Internal helper to parse the practice updated at."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return datetime.fromtimestamp(0, tz=timezone.utc)
        return datetime.fromtimestamp(0, tz=timezone.utc)

    def _upsert_practice_metadata_in_card(self, metadata: Dict[str, Any]):
        """Internal helper to return the upsert practice metadata in card."""
        practices = self.agent_card.setdefault("practices", [])
        for idx, current in enumerate(practices):
            if current.get("id") == metadata.get("id"):
                practices[idx] = metadata
                return
        practices.append(metadata)

    def _default_practice_metadata(self, practice: Practice) -> Dict[str, Any]:
        """Internal helper to return the default practice metadata."""
        return {
            "name": practice.name,
            "description": practice.description,
            "id": practice.id,
            "cost": practice.cost,
            "tags": practice.tags,
            "examples": practice.examples,
            "inputModes": practice.inputModes,
            "outputModes": practice.outputModes,
            "parameters": practice.parameters,
            "path": practice.path
        }

    def _mailbox_practice_metadata(self) -> Dict[str, Any]:
        """Internal helper for mailbox practice metadata."""
        return {
            "name": "Mailbox",
            "description": "Default inbound message endpoint for generic agent delivery.",
            "id": self.MAILBOX_PRACTICE_ID,
            "cost": 0,
            "tags": ["message", "mailbox"],
            "examples": ["POST /mailbox {'sender':'alice','content':'hello','msg_type':'message'}"],
            "inputModes": ["http-post", "json"],
            "outputModes": ["json"],
            "parameters": {
                "sender": {"type": "string", "description": "Sending agent name or id."},
                "receiver": {"type": "string", "description": "Target agent name or id."},
                "content": {"type": "object", "description": "Message payload."},
                "msg_type": {"type": "string", "description": "Message routing key."},
            },
            "path": "/mailbox",
        }

    def _prune_legacy_practices(self) -> None:
        """Internal helper for prune legacy practices."""
        for practice_id in self.LEGACY_REMOVED_PRACTICE_IDS:
            self.delete_practice(practice_id)

    def _normalize_practice_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper to normalize the practice metadata."""
        normalized = dict(metadata or {})
        normalized["cost"] = Practice._normalize_cost(normalized.get("cost", 0))
        return normalized

    def _resolve_callable_practice_entries(self, practice: Practice) -> List[Dict[str, Any]]:
        # Practices can expose multiple callable endpoints (e.g., Plaza bundle).
        # If a practice does not define that expansion, we expose its default endpoint.
        """Internal helper to resolve the callable practice entries."""
        if hasattr(practice, "get_callable_endpoints"):
            entries = practice.get_callable_endpoints()
            if isinstance(entries, list):
                resolved = []
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id") and entry.get("path"):
                        resolved.append(self._normalize_practice_metadata(entry))
                if resolved:
                    return resolved
        return [self._default_practice_metadata(practice)]

    def _build_practice_pool_row(self, practice_metadata: Dict[str, Any], is_deleted: bool = False) -> Optional[Dict[str, Any]]:
        """Internal helper to build the practice pool row."""
        if not practice_metadata:
            return None
        practice_id = practice_metadata.get("id")
        if not practice_id:
            return None
        return {
            "id": self._practice_row_id(practice_id),
            "agent_name": self.name,
            "practice_id": practice_id,
            "practice_name": practice_metadata.get("name", ""),
            "practice_description": practice_metadata.get("description", ""),
            "practice_data": practice_metadata,
            "is_deleted": bool(is_deleted),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _begin_practice_persistence_batch(self):
        """Internal helper for begin practice persistence batch."""
        self._practice_persistence_batch_depth = max(int(self._practice_persistence_batch_depth), 0) + 1

    def _end_practice_persistence_batch(self):
        """Internal helper for end practice persistence batch."""
        current_depth = max(int(self._practice_persistence_batch_depth), 0)
        if current_depth == 0:
            return
        self._practice_persistence_batch_depth = current_depth - 1
        if self._practice_persistence_batch_depth == 0:
            self._flush_pending_practice_rows()

    def _flush_pending_practice_rows(self):
        """Internal helper to return the flush pending practice rows."""
        if not self.pool:
            self._pending_practice_rows = {}
            return
        rows = list((self._pending_practice_rows or {}).values())
        self._pending_practice_rows = {}
        if not rows:
            return
        self._ensure_agent_practices_table()
        if len(rows) == 1:
            self.pool._Insert(self.AGENT_PRACTICES_TABLE, rows[0])
            return
        self.pool._InsertMany(self.AGENT_PRACTICES_TABLE, rows)

    def _persist_practices_to_pool(self, practice_metadata_list: List[Dict[str, Any]], is_deleted: bool = False):
        """Internal helper to persist the practices to pool."""
        if not self.pool or not practice_metadata_list:
            return
        rows = []
        for practice_metadata in practice_metadata_list:
            row = self._build_practice_pool_row(practice_metadata, is_deleted=is_deleted)
            if row is not None:
                rows.append(row)
        if not rows:
            return
        if self._practice_persistence_batch_depth > 0:
            for row in rows:
                row_id = str(row.get("id") or "")
                if row_id:
                    self._pending_practice_rows[row_id] = row
            return
        self._ensure_agent_practices_table()
        if len(rows) == 1:
            self.pool._Insert(self.AGENT_PRACTICES_TABLE, rows[0])
            return
        self.pool._InsertMany(self.AGENT_PRACTICES_TABLE, rows)

    def _persist_practice_to_pool(self, practice_metadata: Dict[str, Any], is_deleted: bool = False):
        """Internal helper to persist the practice to pool."""
        self._persist_practices_to_pool([practice_metadata], is_deleted=is_deleted)

    def _load_practices_info_from_pool(self):
        """Internal helper to load the practices info from pool."""
        if not self.pool:
            return
        try:
            self._ensure_agent_practices_table()
            rows = self.pool._GetTableData(
                self.AGENT_PRACTICES_TABLE,
                {"agent_name": self.name}
            ) or []
        except Exception as e:
            self.logger.warning(f"Failed loading practices info from pool: {e}")
            return

        # Keep newest row per practice_id to honor append-only upsert behavior.
        latest_by_id: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            practice_id = row.get("practice_id")
            if not practice_id:
                continue
            current = latest_by_id.get(practice_id)
            if (not current or
                self._parse_practice_updated_at(row.get("updated_at")) >= self._parse_practice_updated_at(current.get("updated_at"))):
                latest_by_id[practice_id] = row

        for row in latest_by_id.values():
            if row.get("is_deleted"):
                self.agent_card["practices"] = [
                    p for p in self.agent_card.get("practices", [])
                    if p.get("id") != row.get("practice_id")
                ]
                continue
            metadata = row.get("practice_data")
            if isinstance(metadata, dict) and metadata.get("id"):
                self._upsert_practice_metadata_in_card(self._normalize_practice_metadata(metadata))

    def add_practice(self, practice: Practice):
        """Bind and mount a practice to the agent."""
        # Check for duplicates by ID
        if any(p.id == practice.id for p in self.practices):
            self.logger.warning(f"Practice with ID '{practice.id}' already exists. Skipping.")
            return

        practice.bind(self)
        practice.mount(self.app)
        self.practices.append(practice)
        practice_entries = self._resolve_callable_practice_entries(practice)
        for practice_entry in practice_entries:
            self._upsert_practice_metadata_in_card(practice_entry)
        self._persist_practices_to_pool(practice_entries, is_deleted=False)
        self._record_agent_version_snapshot()
        
        self.logger.info(f"Mounted practice: {practice.name}")

    def _register_pool_operation_practices(self):
        """Internal helper to register the pool operation practices."""
        if not self.pool or not hasattr(self.pool, "get_operation_practices"):
            return
        try:
            for practice in self.pool.get_operation_practices() or []:
                if isinstance(practice, Practice):
                    self.add_practice(practice)
        except Exception as e:
            self.logger.warning(f"Failed registering pool operation practices: {e}")

    def add_practice_endpoint(self, metadata: Dict[str, Any]) -> bool:
        """Add one callable endpoint entry to the agent card and persist it."""
        if not isinstance(metadata, dict):
            return False
        if not metadata.get("id") or not metadata.get("path"):
            return False
        normalized = self._normalize_practice_metadata(metadata)
        self._upsert_practice_metadata_in_card(normalized)
        self._persist_practice_to_pool(normalized, is_deleted=False)
        self._record_agent_version_snapshot()
        return True

    def delete_practice(self, practice_id: str) -> bool:
        """Delete a callable endpoint at runtime and persist the deletion state."""
        if not practice_id:
            return False

        removed_practice: Optional[Practice] = None
        for idx, practice in enumerate(self.practices):
            if practice.id == practice_id:
                removed_practice = self.practices.pop(idx)
                break

        removed_card_entry: Optional[Dict[str, Any]] = None
        remaining_practices = []
        for entry in self.agent_card.get("practices", []):
            if entry.get("id") == practice_id and removed_card_entry is None:
                removed_card_entry = dict(entry)
                continue
            remaining_practices.append(entry)
        self.agent_card["practices"] = remaining_practices

        metadata = removed_card_entry
        if not metadata and removed_practice:
            metadata = self._default_practice_metadata(removed_practice)
        if metadata:
            self._persist_practice_to_pool(metadata, is_deleted=True)

        deleted = removed_practice is not None or removed_card_entry is not None
        if deleted:
            self._record_agent_version_snapshot()
            self.logger.info(f"Deleted practice: {practice_id}")
        return deleted

    def delete_practice_endpoint(self, practice_id: str) -> bool:
        """Delete the practice endpoint."""
        return self.delete_practice(practice_id)

    def _remote_practice_audit_table_schema(self) -> TableSchema:
        """Return the schema used for remote `UsePractice` audit rows."""
        return TableSchema(
            {
                "name": self.remote_use_practice_audit.get("table_name") or self.REMOTE_PRACTICE_AUDIT_TABLE,
                "description": "Audit rows for cross-agent remote UsePractice attempts and outcomes.",
                "primary_key": ["id"],
                "rowSchema": {
                    "id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "timestamp": {"type": "datetime"},
                    "direction": {"type": "string"},
                    "event": {"type": "string"},
                    "local_agent_name": {"type": "string"},
                    "local_agent_id": {"type": "string"},
                    "peer_agent_id": {"type": "string"},
                    "peer_name": {"type": "string"},
                    "peer_address": {"type": "string"},
                    "peer_user_id": {"type": "string"},
                    "peer_username": {"type": "string"},
                    "practice_id": {"type": "string"},
                    "plaza_url": {"type": "string"},
                    "auth_mode": {"type": "string"},
                    "policy_allowed": {"type": "boolean"},
                    "policy_reason": {"type": "string"},
                    "outcome": {"type": "string"},
                    "status_code": {"type": "integer"},
                    "error": {"type": "string"},
                    "metadata": {"type": "json"},
                },
            }
        )

    def _ensure_remote_practice_audit_table(self):
        """Ensure the remote practice audit table exists when persistence is enabled."""
        if not self.pool:
            return
        if not self.remote_use_practice_audit.get("persist"):
            return
        if self._remote_practice_audit_table_ensured:
            return
        table_name = self.remote_use_practice_audit.get("table_name") or self.REMOTE_PRACTICE_AUDIT_TABLE
        if self.pool._TableExists(table_name):
            self._remote_practice_audit_table_ensured = True
            return
        self.pool._CreateTable(table_name, self._remote_practice_audit_table_schema())
        self._remote_practice_audit_table_ensured = True

    def _record_remote_practice_audit(
        self,
        *,
        request_id: str,
        direction: str,
        event: str,
        practice_id: str,
        peer_agent_id: str = "",
        peer_name: str = "",
        peer_address: str = "",
        peer_user_id: str = "",
        peer_username: str = "",
        plaza_url: str = "",
        auth_mode: str = "",
        policy_allowed: Optional[bool] = None,
        policy_reason: str = "",
        outcome: str = "",
        status_code: Optional[int] = None,
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Emit one remote practice audit row to logs and, when available, the pool."""
        from prompits.core.observability import elapsed_ms
        self.observations.event("interaction", request_id=request_id, direction=direction,
                                operation=practice_id, peer_agent_id=peer_agent_id, peer_origin=peer_address,
                                status=outcome, phase=event, status_code=status_code,
                                duration_ms=elapsed_ms() if event == "result" else None)
        if event == "result" and direction == "outbound":
            self.observations.metric("agent_calls_total", 1, kind="counter",
                                     labels={"peer": peer_agent_id, "status": outcome})
        audit_enabled = self.remote_use_practice_audit.get("enabled")
        if audit_enabled is False:
            return

        row = {
            "id": str(uuid.uuid4()),
            "request_id": str(request_id or ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": str(direction or ""),
            "event": str(event or ""),
            "local_agent_name": self.name,
            "local_agent_id": str(self.agent_id or ""),
            "peer_agent_id": str(peer_agent_id or ""),
            "peer_name": str(peer_name or ""),
            "peer_address": self._normalize_url(peer_address),
            "peer_user_id": str(peer_user_id or ""),
            "peer_username": str(peer_username or ""),
            "practice_id": str(practice_id or ""),
            "plaza_url": self._normalize_url(plaza_url),
            "auth_mode": str(auth_mode or ""),
            "policy_allowed": None if policy_allowed is None else bool(policy_allowed),
            "policy_reason": str(policy_reason or ""),
            "outcome": str(outcome or ""),
            "status_code": int(status_code) if status_code is not None else None,
            "error": str(error or ""),
            "metadata": dict(metadata or {}),
        }

        if self.remote_use_practice_audit.get("emit_logs"):
            self.logger.info(
                "Remote UsePractice audit request_id=%s direction=%s event=%s practice=%s peer=%s outcome=%s allowed=%s reason=%s",
                row["request_id"],
                row["direction"],
                row["event"],
                row["practice_id"],
                row["peer_agent_id"] or row["peer_name"] or row["peer_address"],
                row["outcome"],
                row["policy_allowed"],
                row["policy_reason"],
            )

        if not self.pool or not self.remote_use_practice_audit.get("persist"):
            return
        try:
            self._ensure_remote_practice_audit_table()
            self.pool._Insert(self.remote_use_practice_audit.get("table_name") or self.REMOTE_PRACTICE_AUDIT_TABLE, row)
        except Exception as exc:
            self.logger.warning(f"Failed persisting remote UsePractice audit row: {exc}")

    def _remote_progress_context_from_content(self, content: Any) -> Dict[str, Any]:
        """Extract an optional caller-provided progress callback context."""
        if not isinstance(content, Mapping):
            return {}
        raw_progress = content.get("_progress")
        if not isinstance(raw_progress, Mapping):
            return {}
        callback_url = self._normalize_url(raw_progress.get("callback_url") or raw_progress.get("callbackUrl"))
        if not callback_url.startswith(("http://", "https://")):
            return {}
        progress_id = str(raw_progress.get("progress_id") or raw_progress.get("progressId") or "").strip()
        return {
            "callback_url": callback_url,
            "progress_id": progress_id,
            "stage": str(raw_progress.get("stage") or "snapshot").strip() or "snapshot",
            "parent_call_id": str(raw_progress.get("parent_call_id") or raw_progress.get("parentCallId") or "").strip(),
            "parent_agent_name": str(raw_progress.get("parent_agent_name") or raw_progress.get("parentAgentName") or "").strip(),
            "parent_agent_id": str(raw_progress.get("parent_agent_id") or raw_progress.get("parentAgentId") or "").strip(),
            "parent_agent_address": self._normalize_url(
                raw_progress.get("parent_agent_address") or raw_progress.get("parentAgentAddress")
            ),
            "call_id": str(raw_progress.get("remote_call_id") or raw_progress.get("remoteCallId") or "").strip(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_perf": time.perf_counter(),
        }

    def _emit_remote_progress_callback(self, progress_context: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        """Send one best-effort progress event back to the caller."""
        callback_url = str(progress_context.get("callback_url") or "").strip()
        if not callback_url:
            return
        payload = {
            "progress_id": progress_context.get("progress_id") or "",
            "event": dict(event or {}),
        }

        def _post_progress_callback() -> None:
            try:
                requests.post(callback_url, json=payload, timeout=self.REMOTE_PRACTICE_REQUEST_TIMEOUT)
            except Exception as exc:
                self.logger.debug("Remote progress callback failed: %s", exc)

        threading.Thread(target=_post_progress_callback, daemon=True).start()

    def _remote_progress_kind(self) -> str:
        """Return the generation-log kind that best describes this agent."""
        text = " ".join(
            str(value or "")
            for value in (
                self.agent_card.get("pit_type"),
                self.agent_card.get("role"),
                self.__class__.__name__,
                self.name,
            )
        ).lower()
        if "persona" in text:
            return "persona"
        if "phemar" in text:
            return "phemar"
        if "castr" in text:
            return "castr"
        if "plaza" in text:
            return "plaza"
        if "pulser" in text:
            return "pulser"
        return "external_api"

    def _emit_remote_practice_progress(
        self,
        progress_context: Mapping[str, Any],
        *,
        practice_id: str,
        request_id: str,
        status: str,
        ok: Optional[bool] = None,
        detail: str = "",
        error: str = "",
        ended: bool = False,
    ) -> None:
        """Emit one inbound practice progress event when the caller requested it."""
        if not progress_context:
            return
        call_id = str(progress_context.get("call_id") or "").strip()
        if not call_id:
            parent_call_id = str(progress_context.get("parent_call_id") or "").strip()
            suffix = str(self.agent_id or self.name or request_id or uuid.uuid4())
            call_id = f"{parent_call_id}:remote:{suffix}" if parent_call_id else str(uuid.uuid4())
        started_at = str(progress_context.get("started_at") or datetime.now(timezone.utc).isoformat())
        event: Dict[str, Any] = {
            "id": call_id,
            "stage": str(progress_context.get("stage") or "snapshot"),
            "kind": self._remote_progress_kind(),
            "name": f"{self.name} {practice_id}",
            "status": status,
            "ok": ok,
            "started_at": started_at,
            "method": "POST",
            "url": f"{self._normalize_url(self.agent_card.get('address'))}/use_practice/{practice_id}",
            "request": {
                "practice_id": practice_id,
                "request_id": request_id,
                "agent": self.name,
            },
            "detail": detail,
        }
        for source_key, event_key in (
            ("parent_call_id", "parent_call_id"),
            ("parent_agent_name", "parent_agent_name"),
            ("parent_agent_id", "parent_agent_id"),
            ("parent_agent_address", "parent_agent_address"),
        ):
            value = str(progress_context.get(source_key) or "").strip()
            if value:
                event[event_key] = value
        if ended:
            event["ended_at"] = datetime.now(timezone.utc).isoformat()
            try:
                event["duration_ms"] = max((time.perf_counter() - float(progress_context.get("started_perf") or 0)) * 1000.0, 0.0)
            except Exception:
                event["duration_ms"] = 0
        if error:
            event["error"] = error
        self._emit_remote_progress_callback(progress_context, event)

    @staticmethod
    def _match_remote_policy_rule(rule: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Return whether a normalized policy rule matches the given context."""
        if not isinstance(rule, dict) or not rule:
            return False
        for key, rule_value in rule.items():
            if key not in context:
                return False
            if not BaseAgent._policy_value_matches(rule_value, context.get(key)):
                return False
        return True

    def _evaluate_remote_use_practice_policy(
        self,
        *,
        direction: str,
        practice_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate inbound/outbound remote `UsePractice` policy for one request."""
        policy = self.remote_use_practice_policy
        if policy.get("enabled") is False:
            return {
                "allowed": True,
                "reason": "policy disabled",
                "matched_rule": None,
                "mode": "disabled",
            }

        direction = "inbound" if direction == "inbound" else "outbound"
        deny_rules = policy.get(f"{direction}_deny") or []
        allow_rules = policy.get(f"{direction}_allow") or []
        default_action = self._normalize_policy_action(policy.get(f"{direction}_default"), default="allow")

        for rule in deny_rules:
            if self._match_remote_policy_rule(rule, context):
                return {
                    "allowed": False,
                    "reason": f"{direction} policy deny rule matched",
                    "matched_rule": dict(rule),
                    "mode": "deny",
                }

        if allow_rules:
            for rule in allow_rules:
                if self._match_remote_policy_rule(rule, context):
                    return {
                        "allowed": True,
                        "reason": f"{direction} allow rule matched",
                        "matched_rule": dict(rule),
                        "mode": "allow",
                    }
            return {
                "allowed": False,
                "reason": f"{direction} allowlist requires a matching rule",
                "matched_rule": None,
                "mode": "allowlist",
            }

        return {
            "allowed": default_action == "allow",
            "reason": f"{direction} default is {default_action}",
            "matched_rule": None,
            "mode": "default",
        }

    @staticmethod
    def _verified_user_from_context(verified: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return normalized Plaza UI user details from verified remote caller context."""
        payload = verified or {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        return {
            "id": str(payload.get("user_id") or user.get("id") or "").strip(),
            "username": str(payload.get("username") or user.get("username") or "").strip(),
            "email": str(payload.get("email") or user.get("email") or "").strip().lower(),
            "display_name": str(payload.get("display_name") or user.get("display_name") or "").strip(),
            "role": str(payload.get("user_role") or user.get("role") or "").strip().lower(),
            "status": str(user.get("status") or "").strip().lower(),
        }

    @classmethod
    def _user_selector_matches(cls, selector: Any, user: Dict[str, Any]) -> bool:
        """Return whether one configured selector matches a Plaza UI user."""
        if not user or not user.get("id"):
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
                if not cls._policy_value_matches(raw_value, user.get(canonical_key)):
                    return False
            return compared

        selector_text = cls._policy_string(selector)
        if not selector_text:
            return False
        if selector_text == "*":
            return True
        return any(
            cls._policy_value_matches(selector_text, user.get(field))
            for field in ("id", "username", "email", "display_name", "role")
        )

    @classmethod
    def _any_user_selector_matches(cls, selectors: List[Any], user: Dict[str, Any]) -> bool:
        """Return whether any configured selector matches a Plaza UI user."""
        return any(cls._user_selector_matches(selector, user) for selector in selectors or [])

    def _practice_permission_rule_from_card(self, practice_id: str) -> Dict[str, Any]:
        """Return per-practice permission metadata from the agent card, when present."""
        practices = self.agent_card.get("practices") if isinstance(self.agent_card.get("practices"), list) else []
        entry = next((item for item in practices if isinstance(item, dict) and item.get("id") == practice_id), None)
        if not isinstance(entry, dict):
            return {}

        collected: Dict[str, Any] = {}
        for key in ("permissions", "permission", "practice_permissions", "user_permissions", "plaza_user_permissions"):
            value = entry.get(key)
            if isinstance(value, dict):
                collected.update(value)

        for key in (
            "allow_users",
            "allowed_users",
            "deny_users",
            "denied_users",
            "default_access",
            "default_action",
            "authenticated_users",
            "require_authenticated_user",
        ):
            if key in entry and key not in collected:
                collected[key] = entry[key]

        return self._normalize_practice_permission_rule(collected) if collected else {}

    @staticmethod
    def _merge_practice_permission_rules(*rules: Dict[str, Any]) -> Dict[str, Any]:
        """Merge global practice permission overrides with card-level metadata."""
        merged: Dict[str, Any] = {
            "allow_users": [],
            "deny_users": [],
        }
        for rule in rules:
            if not isinstance(rule, dict) or not rule:
                continue
            if rule.get("action"):
                merged["action"] = rule.get("action")
            if "require_authenticated_user" in rule:
                merged["require_authenticated_user"] = rule.get("require_authenticated_user")
            merged["allow_users"].extend(rule.get("allow_users") or [])
            merged["deny_users"].extend(rule.get("deny_users") or [])
        return merged

    def _evaluate_practice_user_permission(
        self,
        *,
        practice_id: str,
        verified: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate Plaza UI user permission settings for one inbound practice call."""
        policy = self.practice_user_permissions
        if not policy.get("enabled"):
            return {
                "allowed": True,
                "reason": "practice user permissions disabled",
                "matched_rule": None,
                "mode": "disabled",
            }

        user = self._verified_user_from_context(verified)
        has_user = bool(user.get("id"))
        configured_practice_rule = (policy.get("practices") or {}).get(str(practice_id or ""), {})
        card_practice_rule = self._practice_permission_rule_from_card(str(practice_id or ""))
        practice_rule = self._merge_practice_permission_rules(configured_practice_rule, card_practice_rule)
        require_user = bool(
            practice_rule.get(
                "require_authenticated_user",
                policy.get("require_authenticated_user", False),
            )
        )

        if require_user and not has_user:
            return {
                "allowed": False,
                "reason": "practice requires a Plaza-authenticated user",
                "matched_rule": None,
                "mode": "require_user",
            }

        if has_user and self._any_user_selector_matches(practice_rule.get("deny_users") or [], user):
            return {
                "allowed": False,
                "reason": "practice user deny rule matched",
                "matched_rule": {"practice_id": practice_id, "deny_users": practice_rule.get("deny_users") or []},
                "mode": "practice_user_deny",
            }

        if has_user and self._any_user_selector_matches(practice_rule.get("allow_users") or [], user):
            return {
                "allowed": True,
                "reason": "practice user allow rule matched",
                "matched_rule": {"practice_id": practice_id, "allow_users": practice_rule.get("allow_users") or []},
                "mode": "practice_user_allow",
            }

        if practice_rule.get("allow_users"):
            return {
                "allowed": False,
                "reason": "practice user allowlist requires a matching Plaza user",
                "matched_rule": None,
                "mode": "practice_user_allowlist",
            }

        if practice_rule.get("action"):
            action = self._normalize_policy_action(practice_rule.get("action"), default="allow")
            return {
                "allowed": action == "allow",
                "reason": f"practice default is {action}",
                "matched_rule": {"practice_id": practice_id, "action": action},
                "mode": "practice_default",
            }

        global_deny = policy.get("deny_users") or []
        global_allow = policy.get("allow_users") or []
        if has_user and self._any_user_selector_matches(global_deny, user):
            return {
                "allowed": False,
                "reason": "global user deny rule matched",
                "matched_rule": {"deny_users": global_deny},
                "mode": "global_user_deny",
            }

        if has_user and self._any_user_selector_matches(global_allow, user):
            return {
                "allowed": True,
                "reason": "global user allow rule matched",
                "matched_rule": {"allow_users": global_allow},
                "mode": "global_user_allow",
            }

        if global_allow:
            return {
                "allowed": False,
                "reason": "global user allowlist requires a matching Plaza user",
                "matched_rule": None,
                "mode": "global_user_allowlist",
            }

        default_action = self._normalize_policy_action(policy.get("default_action"), default="allow")
        return {
            "allowed": default_action == "allow",
            "reason": f"global practice user default is {default_action}",
            "matched_rule": None,
            "mode": "global_default",
        }

    def _build_outbound_policy_context(self, practice_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
        """Build policy and audit context for one outbound remote practice call."""
        target_card = target.get("card") if isinstance(target.get("card"), dict) else {}
        return {
            "practice_id": str(practice_id or ""),
            "target_agent_id": str(target.get("agent_id") or target_card.get("agent_id") or ""),
            "target_name": str(target_card.get("name") or ""),
            "target_address": self._normalize_url(target.get("url") or target_card.get("address") or ""),
            "target_role": str(target_card.get("role") or ""),
            "target_pit_type": str(target_card.get("pit_type") or target_card.get("type") or ""),
            "plaza_url": self._normalize_url(self.plaza_url),
        }

    def _build_inbound_policy_context(
        self,
        *,
        practice_id: str,
        request: PracticeInvocationRequest,
        verified: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build policy and audit context for one inbound remote practice call."""
        caller_pit_address = self._coerce_pit_address(request.caller_agent_address)
        verified_payload = verified or {}
        caller_pit_id = caller_pit_address.pit_id if caller_pit_address else ""
        return {
            "practice_id": str(practice_id or ""),
            "caller_agent_id": str(verified_payload.get("agent_id") or caller_pit_id or ""),
            "caller_name": str(verified_payload.get("agent_name") or request.caller_agent_name or request.sender or ""),
            "caller_address": self._normalize_url(request.caller_agent_url),
            "auth_mode": str(verified_payload.get("auth_mode") or ""),
            "plaza_url": self._normalize_url(verified_payload.get("plaza_url")),
            "caller_user_id": str(verified_payload.get("user_id") or ""),
            "caller_username": str(verified_payload.get("username") or ""),
            "caller_user_email": str(verified_payload.get("email") or ""),
            "caller_user_role": str(verified_payload.get("user_role") or ""),
        }

    def _render_shared_agent_settings_page(self) -> str:
        """Return the universal BaseAgent settings shell."""
        title = html.escape(f"{self.name} Settings")
        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <link rel="stylesheet" href="/static/shared_agent_settings.css">
</head>
<body>
    <main id="shared-agent-settings-root" data-agent-settings-root>
        <section class="shared-settings-boot">
            <p>Loading agent settings...</p>
        </section>
    </main>
    <script src="/static/shared_agent_settings.js" defer></script>
</body>
</html>"""

    def _shared_agent_config_path(self) -> Optional[str]:
        """Return the attached config path for shared settings, when available."""
        config_path = getattr(self, "config_path", None)
        if not config_path:
            return None
        try:
            return os.fspath(config_path)
        except TypeError:
            return str(config_path)

    def _shared_agent_runtime_config_document(self) -> Dict[str, Any]:
        """Load the most complete generic config document available to this agent."""
        loader = getattr(self, "_load_config_document", None)
        if callable(loader):
            loaded = loader()
            if isinstance(loaded, dict):
                return copy.deepcopy(loaded)

        raw_config = getattr(self, "raw_config", None)
        if isinstance(raw_config, dict) and raw_config:
            return copy.deepcopy(raw_config)

        return {
            "name": self.agent_card.get("name", self.name),
            "host": self.host,
            "port": self.port,
            "plaza_url": self.plaza_url or "",
            "role": self.agent_card.get("role", ""),
            "description": self.agent_card.get("description", ""),
            "tags": list(self.agent_card.get("tags") or []),
        }

    def _shared_agent_practice_summaries(self) -> List[Dict[str, Any]]:
        """Return stable summaries for practices exposed in the shared UI."""
        summaries: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for practice in self.practices:
            practice_id = str(getattr(practice, "id", "") or "").strip()
            if not practice_id or practice_id in seen:
                continue
            seen.add(practice_id)
            summaries.append({
                "id": practice_id,
                "name": str(getattr(practice, "name", "") or practice_id),
                "description": str(getattr(practice, "description", "") or ""),
            })

        card_practices = self.agent_card.get("practices") if isinstance(self.agent_card.get("practices"), list) else []
        for entry in card_practices:
            if not isinstance(entry, dict):
                continue
            practice_id = str(entry.get("id") or "").strip()
            if not practice_id or practice_id in seen:
                continue
            seen.add(practice_id)
            summaries.append({
                "id": practice_id,
                "name": str(entry.get("name") or practice_id),
                "description": str(entry.get("description") or ""),
            })
        return summaries

    def _build_shared_agent_settings_payload(self) -> Dict[str, Any]:
        """Build the JSON contract used by all shared agent settings UIs."""
        config_path = self._shared_agent_config_path()
        store_forward = store_forward_summary_from_card(self.agent_card)
        return {
            "status": "success",
            "agent": {
                "name": self.agent_card.get("name", self.name),
                "role": self.agent_card.get("role", ""),
                "pit_type": self.agent_card.get("pit_type", "Agent"),
                "address": self.agent_card.get("address", ""),
                "agent_id": self.agent_id or self.agent_card.get("agent_id", ""),
                "plaza_url": self.plaza_url or "",
            },
            "config_path": config_path,
            "settings": {
                "practice_permissions": copy.deepcopy(self.practice_user_permissions),
                "delivery": self._current_delivery_settings(),
            },
            "connectivity": {
                "accepts_inbound_from_plaza": bool(self.agent_card.get("accepts_inbound_from_plaza", True)),
                "accepts_direct_call": bool(self.agent_card.get("accepts_direct_call", True)),
                "connectivity_mode": str(self.agent_card.get("connectivity_mode") or ""),
                "store_forward": store_forward,
            },
            "authorization": {
                "save_requires_authorization": bool(
                    self.plaza_url
                    or self.direct_auth_token
                    or callable(getattr(self, "_get_authenticated_ui_context", None))
                ),
                "direct_token_configured": bool(self.direct_auth_token),
                "plaza_url": self.plaza_url or "",
            },
            "practices": self._shared_agent_practice_summaries(),
        }

    def _current_delivery_settings(self) -> Dict[str, Any]:
        """Return the editable delivery settings currently advertised by this agent."""
        meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        delivery = copy.deepcopy(meta.get("delivery") if isinstance(meta.get("delivery"), dict) else {})
        store_forward = store_forward_summary_from_card(self.agent_card)
        if "store_forward" not in delivery and store_forward.get("configured"):
            delivery["store_forward"] = {
                "enabled": bool(store_forward.get("enabled")),
                "poll_enabled": bool(store_forward.get("poll_enabled")),
                "poll_interval_sec": int(store_forward.get("poll_interval_sec") or 30),
                "payload_transport": store_forward.get("payload_transport") or "plaza",
            }
        return delivery

    def _configuration_owner_id(self) -> str:
        """Resolve ownership from this agent's server-side configuration."""
        documents = [getattr(self, key, None) for key in ('raw_config', '_config_data', 'config')]
        for document in documents:
            if not isinstance(document, dict):
                continue
            account = document.get('plaza_owner_account')
            if isinstance(account, dict) and account.get('user_id'):
                return str(account['user_id']).strip()
        card = self.agent_card if isinstance(self.agent_card, dict) else {}
        for source in (card, card.get('meta') or {}):
            if isinstance(source, dict) and (source.get('owner_user_id') or source.get('owner_id')):
                return str(source.get('owner_user_id') or source['owner_id']).strip()
        return ''

    def _configuration_owner_required(self, path: str, method: str) -> bool:
        # Editor config reads can contain credentials; testing an arbitrary
        # editor config is also an owner operation. Public pulses are separate.
        # Agent settings and version snapshots are configuration, read or written.
        path = path.rstrip('/')
        if method == 'OPTIONS':
            return False
        return (path in {'/api/config', '/api/test-pulse', '/_agent/api/settings'}
                or path.startswith('/api/agent/versions/') and path.endswith('/download'))

    # Direct routes of data and generation practices skip /use_practice caller checks.
    LOCAL_ONLY_PRACTICE_PATHS = frozenset({'/get_pulse_data', '/quote_pulse_data', '/quote_request',
                                           '/generate_phema', '/snapshot_phema', '/embeddings'})

    def _local_only_path(self, path: str) -> bool:
        """Pool storage and direct data-practice routes serve this host only; remote callers use /use_practice.

        Protocol practices such as Plaza's /register keep their own authentication.
        """
        path = path.rstrip('/') or '/'
        return path.startswith('/pool_') or path in self.LOCAL_ONLY_PRACTICE_PATHS

    @staticmethod
    def _is_local_request(request: Request) -> bool:
        # A forwarded request came through a proxy, even from loopback.
        if any(request.headers.get(name) for name in ('forwarded', 'x-forwarded-for', 'x-real-ip')):
            return False
        return (request.client.host if request.client else '') in {'127.0.0.1', '::1', 'localhost', 'testclient'}

    def _install_configuration_owner_guard(self):
        @self.app.middleware('http')
        async def configuration_owner_guard(request: Request, call_next):
            if self._local_only_path(request.url.path) and not self._is_local_request(request):
                return JSONResponse({'detail': 'Call this agent through /use_practice with Plaza authorization.'},
                                    status_code=403, headers={'Cache-Control': 'no-store'})
            if self._configuration_owner_required(request.url.path, request.method):
                try:
                    await self._authorize_shared_agent_settings_write(request)
                except HTTPException as exc:
                    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                        headers={'Cache-Control': 'no-store'})
            return await call_next(request)

    async def _authorize_shared_agent_settings_write(self, request: Request) -> Dict[str, Any]:
        """Only the verified owning account may configure an agent."""
        token = BaseAgent._extract_bearer_token(dict(request.headers))
        if not token:
            raise HTTPException(status_code=401, detail='Sign in with the agent owner account to change its configuration.')
        owner_id = self._configuration_owner_id()
        if not owner_id:
            raise HTTPException(status_code=403, detail='This agent has no configured owner. Configuration is read-only.')
        # A transport token or an administrator role does not prove ownership.
        if self.direct_auth_token and token == self.direct_auth_token:
            raise HTTPException(status_code=403, detail='A verified agent owner account is required.')
        plaza_ui_context = getattr(self, "_get_authenticated_ui_context", None)
        if callable(plaza_ui_context):
            _, _, profile = await run_in_threadpool(plaza_ui_context, request)
            verified = {'user_id': str(profile.get('id') or ''),
                        'username': str(profile.get('username') or ''),
                        'user_role': str(profile.get('role') or '')} if isinstance(profile, dict) else {}
        elif self.plaza_url:
            verified = await self._verify_plaza_user_token(caller_user_token=token, preferred_plaza_url=self.plaza_url)
        else:
            raise HTTPException(status_code=403, detail='Owner authentication is unavailable. Configuration is read-only.')
        if str(verified.get('user_id') or '') != owner_id:
            raise HTTPException(status_code=403, detail='Only this agent’s owner can change its configuration.')
        return {'auth_mode': 'plaza_ui', 'user_id': owner_id,
                'username': str(verified.get('username') or ''), 'user_role': str(verified.get('user_role') or '')}

    def _save_shared_agent_practice_permissions(self, permissions: Dict[str, Any]) -> Dict[str, Any]:
        """Persist shared practice permission settings when this agent supports it."""
        return self._save_shared_agent_settings({"practice_permissions": permissions})

    @staticmethod
    def _normalize_shared_no_bother(value: Any) -> Dict[str, Any]:
        """Normalize one editable no-bother time window."""
        source = dict(value or {}) if isinstance(value, dict) else {}
        return {
            "enabled": bool(source.get("enabled", False)),
            "start": str(source.get("start") or "22:00").strip() or "22:00",
            "end": str(source.get("end") or "07:00").strip() or "07:00",
            "timezone": str(source.get("timezone") or source.get("tz") or "UTC").strip() or "UTC",
        }

    @classmethod
    def _normalize_shared_delivery_settings(cls, delivery: Any) -> Dict[str, Any]:
        """Normalize editable store-forward and notification route settings."""
        if not isinstance(delivery, dict):
            return {}
        normalized = copy.deepcopy(delivery)
        store_forward = normalized.get("store_forward")
        if isinstance(store_forward, bool):
            store_forward = {"enabled": store_forward}
        elif not isinstance(store_forward, dict):
            store_forward = {}
        if store_forward or "store_forward" in normalized:
            try:
                poll_interval_sec = max(int(store_forward.get("poll_interval_sec") or store_forward.get("poll_interval_seconds") or 30), 0)
            except (TypeError, ValueError):
                poll_interval_sec = 30
            normalized["store_forward"] = {
                "enabled": bool(store_forward.get("enabled", False)),
                "poll_enabled": bool(store_forward.get("poll_enabled", store_forward.get("enabled", False))),
                "poll_interval_sec": poll_interval_sec,
                "payload_transport": str(store_forward.get("payload_transport") or "plaza").strip() or "plaza",
            }

        notification_defaults = normalized.get("notification_defaults")
        if not isinstance(notification_defaults, dict):
            notification_defaults = {}
        if notification_defaults or "notification_defaults" in normalized:
            summary_minutes = notification_defaults.get("summary_every_minutes")
            try:
                summary_minutes = max(float(summary_minutes or 0), 0.0)
            except (TypeError, ValueError):
                summary_minutes = 0.0
            normalized["notification_defaults"] = {
                "summary_every_minutes": summary_minutes,
                "no_bother": cls._normalize_shared_no_bother(notification_defaults.get("no_bother")),
            }

        notify = normalized.get("notify")
        if notify is None:
            notify = normalized.get("notifications")
        if isinstance(notify, list):
            normalized["notify"] = [dict(route) for route in notify if isinstance(route, dict) and str(route.get("provider") or "").strip()]
        elif "notify" in normalized or "notifications" in normalized:
            normalized["notify"] = []
        normalized.pop("notifications", None)
        return normalized

    def _save_shared_agent_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Persist shared settings when this agent supports it."""
        raw_settings = settings if isinstance(settings, dict) else {}
        permissions = raw_settings.get("practice_permissions")
        if permissions is None:
            permissions = self.practice_user_permissions
        normalized = self._normalize_practice_user_permissions(permissions)
        delivery_provided = "delivery" in raw_settings
        delivery = self._normalize_shared_delivery_settings(raw_settings.get("delivery")) if delivery_provided else None
        document = self._shared_agent_runtime_config_document()
        document["practice_permissions"] = copy.deepcopy(normalized)
        if delivery_provided:
            agent_card = document.get("agent_card")
            if not isinstance(agent_card, dict):
                agent_card = {}
                document["agent_card"] = agent_card
            agent_meta = agent_card.get("meta")
            if not isinstance(agent_meta, dict):
                agent_meta = {}
                agent_card["meta"] = agent_meta
            agent_meta["delivery"] = copy.deepcopy(delivery or {})

        config_path = self._shared_agent_config_path()
        persisted = False
        save_error = ""
        saver = getattr(self, "_save_config_document", None)
        saved_document: Dict[str, Any] = document

        if callable(saver):
            try:
                saved = saver(copy.deepcopy(document))
                if isinstance(saved, dict):
                    saved_document = saved
                persisted = bool(config_path)
            except HTTPException as exc:
                if exc.status_code != 400:
                    raise
                save_error = str(exc.detail or "")

        if not persisted and config_path and (not callable(saver) or save_error):
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(document, fh, indent=4)
            saved_document = document
            persisted = True

        if not persisted:
            try:
                self.raw_config = copy.deepcopy(saved_document)
            except Exception:
                pass

        self.practice_user_permissions = normalized
        if delivery_provided:
            meta = self.agent_card.get("meta")
            if not isinstance(meta, dict):
                meta = {}
                self.agent_card["meta"] = meta
            meta["delivery"] = copy.deepcopy(delivery or {})
            self._sync_connectivity_metadata()
        return {
            "config": saved_document,
            "config_path": config_path,
            "persisted": persisted,
            "save_error": save_error,
            "practice_permissions": copy.deepcopy(self.practice_user_permissions),
            "delivery": self._current_delivery_settings(),
        }

    def setup_routes(self):
        """Set up the routes."""
        @self.app.get("/health")
        async def health_check():
            """Route handler for GET /health."""
            return {
                "status": "ok",
                "agent": self.name,
                "version": str(self.AGENT_RUNTIME_VERSION),
                "protocol_version": str(self.AGENT_PROTOCOL_VERSION),
            }

        @self.app.get("/.well-known/prompits-address")
        async def plaza_address_proof(agent_id: str = "", probe: str = ""):
            """Answer Plaza's check that this agent listens at the address it registered (address_proof.py)."""
            from prompits.core.address_proof import answer
            reply = answer(self.plaza_token, str(self.agent_id or ""), str(self.agent_card.get("address") or ""),
                           agent_id, probe)
            if reply is None:
                raise HTTPException(status_code=404, detail="Not found")
            return reply

        @self.app.get("/api/readiness")
        async def agent_readiness(request: Request):
            """Route handler for GET /api/readiness."""
            return await run_in_threadpool(self.build_readiness_report, dict(request.query_params))

        @self.app.get("/readiness")
        async def legacy_agent_readiness(request: Request):
            """Route handler for GET /readiness."""
            return await run_in_threadpool(self.build_readiness_report, dict(request.query_params))

        @self.app.get("/api/agent/status")
        async def get_agent_runtime_status():
            """Route handler for GET /api/agent/status."""
            return await run_in_threadpool(self._public_agent_runtime_status)

        @self.app.get("/_agent/api/status")
        async def get_shared_agent_runtime_status():
            """Route handler for GET /_agent/api/status."""
            return await run_in_threadpool(self._public_agent_runtime_status)

        async def runtime_working_stream(request: Request):
            """Stream live runtime work snapshots as server-sent events."""
            last_payload = ""
            while not await request.is_disconnected():
                payload = self._public_agent_runtime_status()
                serialized = json.dumps(payload, sort_keys=True, default=str)
                if serialized != last_payload:
                    last_payload = serialized
                    yield f"event: runtime\n"
                    yield f"data: {serialized}\n\n"
                await asyncio.sleep(1)

        @self.app.get("/api/agent/working-stream")
        async def get_agent_working_stream(request: Request):
            """Route handler for GET /api/agent/working-stream."""
            return StreamingResponse(runtime_working_stream(request), media_type="text/event-stream")

        @self.app.get("/_agent/api/working-stream")
        async def get_shared_agent_working_stream(request: Request):
            """Route handler for GET /_agent/api/working-stream."""
            return StreamingResponse(runtime_working_stream(request), media_type="text/event-stream")

        @self.app.get("/api/agent/work-log")
        async def get_agent_work_log():
            """Route handler for GET /api/agent/work-log."""
            payload = await run_in_threadpool(self._public_agent_runtime_status)
            return {
                "status": "success",
                "agent": payload.get("agent"),
                "work_log": payload.get("work_log"),
                "work_log_list": payload.get("work_log_list", []),
                "work_history": payload.get("work_history"),
                "work_history_list": payload.get("work_history_list", []),
            }

        @self.app.get("/_agent/api/work-log")
        async def get_shared_agent_work_log():
            """Route handler for GET /_agent/api/work-log."""
            payload = await run_in_threadpool(self._public_agent_runtime_status)
            return {
                "status": "success",
                "agent": payload.get("agent"),
                "work_log": payload.get("work_log"),
                "work_log_list": payload.get("work_log_list", []),
                "work_history": payload.get("work_history"),
                "work_history_list": payload.get("work_history_list", []),
            }

        @self.app.get("/api/agent/version")
        async def get_agent_version():
            """Route handler for GET /api/agent/version."""
            return await run_in_threadpool(self._public_agent_version_history)

        @self.app.get("/api/agent/versions")
        async def get_agent_versions():
            """Route handler for GET /api/agent/versions."""
            return await run_in_threadpool(self._public_agent_version_history)

        @self.app.get("/_agent/api/version")
        async def get_shared_agent_version():
            """Route handler for GET /_agent/api/version."""
            return await run_in_threadpool(self._public_agent_version_history)

        @self.app.get("/api/agent/versions/{version_id}/download")
        async def download_agent_version(version_id: str):
            """Route handler for downloading a saved agent version snapshot."""
            try:
                record = await run_in_threadpool(self._agent_version_download_record, version_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Agent version not found") from exc
            filename = f"{self._safe_version_part(self.name)}-{self._safe_version_part(record.get('version'))}.json"
            return Response(
                content=json.dumps(record, indent=2, sort_keys=True),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @self.app.get("/_agent/settings", response_class=HTMLResponse)
        async def shared_agent_settings_page():
            """Route handler for GET /_agent/settings."""
            return HTMLResponse(self._render_shared_agent_settings_page())

        @self.app.get("/_agent/api/settings")
        async def get_shared_agent_settings(request: Request):
            """Route handler for GET /_agent/api/settings."""
            payload = self._build_shared_agent_settings_payload()
            try:
                await self._authorize_shared_agent_settings_write(request)
                can_configure = True
            except HTTPException:
                can_configure = False
            payload['authorization'].update(can_configure=can_configure, owner_only=True, save_requires_authorization=True)
            return payload

        @self.app.post("/_agent/api/settings")
        async def save_shared_agent_settings(request: Request):
            """Route handler for POST /_agent/api/settings."""
            await self._authorize_shared_agent_settings_write(request)
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Settings payload must be a JSON object.")
            raw_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
            permissions = raw_settings.get("practice_permissions") if isinstance(raw_settings, dict) else None
            if permissions is None and "practice_access" in raw_settings:
                permissions = raw_settings.get("practice_access")
            if permissions is None:
                permissions = {}
            if not isinstance(permissions, dict):
                raise HTTPException(status_code=400, detail="practice_permissions must be a JSON object.")
            raw_settings["practice_permissions"] = permissions
            saved = await run_in_threadpool(self._save_shared_agent_settings, raw_settings)
            response = self._build_shared_agent_settings_payload()
            response['authorization'].update(can_configure=True, owner_only=True, save_requires_authorization=True)
            response.update({
                "status": "success",
                "config_path": saved.get("config_path"),
                "persisted": saved.get("persisted", False),
                "save_error": saved.get("save_error", ""),
            })
            return response

        @self.app.get("/api/local-site-settings")
        async def get_local_site_settings():
            """Route handler for GET /api/local-site-settings."""
            return {"status": "success", "settings": self.site_settings}

        @self.app.get("/api/plaza_connection_status")
        async def get_plaza_connection_status():
            """Route handler for GET /api/plaza_connection_status."""
            return await run_in_threadpool(self.get_plaza_connection_status)

        @self.app.post("/mailbox")
        async def mailbox(message: Message):
            """Route handler for POST /mailbox."""
            result = None
            if hasattr(self, "receive"):
                result = await run_in_threadpool(self.receive, message)
                if inspect.isawaitable(result):
                    result = await result
            payload = {"status": "received"}
            if result is not None:
                payload["result"] = result
            return payload

        @self.app.post("/use_practice/{practice_id}")
        async def use_practice(practice_id: str, request: PracticeInvocationRequest):
            """Route handler for POST /use_practice/{practice_id}."""
            request_id = str(request.request_id or uuid.uuid4())
            progress_context = self._remote_progress_context_from_content(request.content)
            self._emit_remote_practice_progress(
                progress_context,
                practice_id=practice_id,
                request_id=request_id,
                status="running",
                detail="Remote practice request received.",
            )
            self.logger.debug(
                f"Received remote UsePractice request for '{practice_id}' "
                f"from '{request.sender}' to '{request.receiver}'"
            )
            try:
                inbound_protocol = self._assert_inbound_protocol_compatible(
                    caller_protocol_version=request.caller_protocol_version,
                    caller_name=str(request.caller_agent_name or request.sender or ""),
                )
            except HTTPException as exc:
                inbound_context = self._build_inbound_policy_context(
                    practice_id=practice_id,
                    request=request,
                    verified=None,
                )
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="inbound",
                    event="request",
                    practice_id=practice_id,
                    peer_agent_id=inbound_context.get("caller_agent_id", ""),
                    peer_name=inbound_context.get("caller_name", ""),
                    peer_address=inbound_context.get("caller_address", ""),
                    plaza_url=inbound_context.get("plaza_url", ""),
                    auth_mode=inbound_context.get("auth_mode", ""),
                    policy_allowed=False,
                    policy_reason="caller protocol incompatible",
                    outcome="protocol_rejected",
                    status_code=exc.status_code,
                    error=str(exc.detail or ""),
                    metadata={"caller_protocol_version": str(request.caller_protocol_version or "legacy")},
                )
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="protocol_rejected",
                    ok=False,
                    detail="Caller protocol version is incompatible.",
                    error=str(exc.detail or ""),
                    ended=True,
                )
                raise
            try:
                verified = await self._verify_remote_caller(
                    caller_agent_address=request.caller_agent_address,
                    caller_plaza_token=request.caller_plaza_token,
                    caller_direct_token=request.caller_direct_token,
                    caller_user_token=request.caller_user_token,
                )
            except HTTPException as exc:
                inbound_context = self._build_inbound_policy_context(
                    practice_id=practice_id,
                    request=request,
                    verified=None,
                )
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="inbound",
                    event="request",
                    practice_id=practice_id,
                    peer_agent_id=inbound_context.get("caller_agent_id", ""),
                    peer_name=inbound_context.get("caller_name", ""),
                    peer_address=inbound_context.get("caller_address", ""),
                    plaza_url=inbound_context.get("plaza_url", ""),
                    auth_mode=inbound_context.get("auth_mode", ""),
                    policy_allowed=False,
                    policy_reason="caller verification failed",
                    outcome="verification_failed",
                    status_code=exc.status_code,
                    error=str(exc.detail or ""),
                )
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="verification_failed",
                    ok=False,
                    detail="Caller verification failed.",
                    error=str(exc.detail or ""),
                    ended=True,
                )
                raise

            if practice_id.startswith("pool-") and verified.get("auth_mode") != "direct":
                # Pool operations read and write this agent's own storage; they are not practices for other agents.
                raise HTTPException(status_code=403, detail="Pool operations are available to this agent's operator only.")

            verified_agent_name = str(verified.get("agent_name") or request.sender or "").strip().lower()
            if verified_agent_name == "plaza" and not store_forward_summary_from_card(self.agent_card).get("enabled"):
                raise HTTPException(
                    status_code=403,
                    detail="This agent accepts direct calls only; Plaza store-forward is not enabled.",
                )

            inbound_context = self._build_inbound_policy_context(
                practice_id=practice_id,
                request=request,
                verified=verified,
            )
            policy_decision = self._evaluate_remote_use_practice_policy(
                direction="inbound",
                practice_id=practice_id,
                context=inbound_context,
            )
            self._record_remote_practice_audit(
                request_id=request_id,
                direction="inbound",
                event="request",
                practice_id=practice_id,
                peer_agent_id=inbound_context.get("caller_agent_id", ""),
                peer_name=inbound_context.get("caller_name", ""),
                peer_address=inbound_context.get("caller_address", ""),
                plaza_url=inbound_context.get("plaza_url", ""),
                auth_mode=inbound_context.get("auth_mode", ""),
                policy_allowed=policy_decision.get("allowed"),
                policy_reason=policy_decision.get("reason", ""),
                outcome="allowed" if policy_decision.get("allowed") else "denied",
                status_code=200 if policy_decision.get("allowed") else 403,
                metadata={
                    "matched_rule": policy_decision.get("matched_rule"),
                    "policy_mode": policy_decision.get("mode"),
                    "caller_protocol_version": inbound_protocol.get("protocol_version"),
                },
            )
            if not policy_decision.get("allowed"):
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="denied",
                    ok=False,
                    detail="Inbound policy denied this practice request.",
                    error=policy_decision.get("reason") or "Inbound remote practice denied by policy",
                    ended=True,
                )
                raise HTTPException(status_code=403, detail=policy_decision.get("reason") or "Inbound remote practice denied by policy")

            user_permission_decision = self._evaluate_practice_user_permission(
                practice_id=practice_id,
                verified=verified,
            )
            self._record_remote_practice_audit(
                request_id=request_id,
                direction="inbound",
                event="permission",
                practice_id=practice_id,
                peer_agent_id=inbound_context.get("caller_agent_id", ""),
                peer_name=inbound_context.get("caller_name", ""),
                peer_address=inbound_context.get("caller_address", ""),
                plaza_url=inbound_context.get("plaza_url", ""),
                auth_mode=inbound_context.get("auth_mode", ""),
                policy_allowed=user_permission_decision.get("allowed"),
                policy_reason=user_permission_decision.get("reason", ""),
                outcome="allowed" if user_permission_decision.get("allowed") else "denied",
                status_code=200 if user_permission_decision.get("allowed") else 403,
                metadata={
                    "matched_rule": user_permission_decision.get("matched_rule"),
                    "permission_mode": user_permission_decision.get("mode"),
                    "caller_user_id": inbound_context.get("caller_user_id", ""),
                    "caller_username": inbound_context.get("caller_username", ""),
                },
            )
            if not user_permission_decision.get("allowed"):
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="denied",
                    ok=False,
                    detail="User permission denied this practice request.",
                    error=user_permission_decision.get("reason") or "Practice denied by Plaza user permissions",
                    ended=True,
                )
                raise HTTPException(
                    status_code=403,
                    detail=user_permission_decision.get("reason") or "Practice denied by Plaza user permissions",
                )

            local_practice = next((p for p in self.practices if p.id == practice_id), None)
            bundled_practice = None
            bundled_practice_entry = None
            if local_practice is None:
                for candidate in self.practices:
                    execute_endpoint = getattr(candidate, "execute_callable_endpoint", None)
                    get_endpoints = getattr(candidate, "get_callable_endpoints", None)
                    if not callable(execute_endpoint) or not callable(get_endpoints):
                        continue
                    can_execute_endpoint = getattr(candidate, "can_execute_callable_endpoint", None)
                    if callable(can_execute_endpoint) and not can_execute_endpoint(practice_id):
                        continue
                    endpoints = get_endpoints()
                    matching_entry = next(
                        (
                            entry
                            for entry in endpoints or []
                            if isinstance(entry, dict) and entry.get("id") == practice_id
                        ),
                        None,
                    )
                    if matching_entry is not None:
                        bundled_practice = candidate
                        bundled_practice_entry = matching_entry
                        break
            if not local_practice:
                if bundled_practice is None:
                    self._record_remote_practice_audit(
                        request_id=request_id,
                        direction="inbound",
                        event="result",
                        practice_id=practice_id,
                        peer_agent_id=inbound_context.get("caller_agent_id", ""),
                        peer_name=inbound_context.get("caller_name", ""),
                        peer_address=inbound_context.get("caller_address", ""),
                        plaza_url=inbound_context.get("plaza_url", ""),
                        auth_mode=inbound_context.get("auth_mode", ""),
                        policy_allowed=True,
                        policy_reason=policy_decision.get("reason", ""),
                        outcome="failed",
                        status_code=404,
                        error=f"Practice '{practice_id}' not found",
                    )
                    self._emit_remote_practice_progress(
                        progress_context,
                        practice_id=practice_id,
                        request_id=request_id,
                        status="not_found",
                        ok=False,
                        detail="Practice was not found on the target agent.",
                        error=f"Practice '{practice_id}' not found",
                        ended=True,
                    )
                    raise HTTPException(status_code=404, detail=f"Practice '{practice_id}' not found")

            running_scope_id = ""
            try:
                if local_practice is not None:
                    credit_cost = self._practice_credit_point_cost(getattr(local_practice, "cost", 0))
                else:
                    credit_cost = self._practice_credit_point_cost(
                        bundled_practice_entry.get("cost") if isinstance(bundled_practice_entry, dict) else 0
                    )
                # A provider may price one request (its own quote); that price is what the payment scope must cover.
                request_cost = self.practice_request_cost(practice_id, request.content)
                if request_cost is not None:
                    credit_cost = self._practice_credit_point_cost(request_cost)
                payment_scope_context: Dict[str, Any] = {}
                scope_candidate = self._payment_scope_from_request(request, practice_id)
                generic_payment = scope_candidate.get("protocol") == "agent-payment/1"
                # A retry with a scope whose escrow-settled work already ran gets that result again once Plaza confirms
                # the delivery, without verifying the scope or running the paid work a second time.
                delivered_entry = None if generic_payment else self._held_delivery_for_request(
                    scope_candidate, practice_id, request.content)
                if delivered_entry is None and not generic_payment:
                    # One run per scope: a concurrent call with the same scope is asked to retry later.
                    running_scope_id = self._claim_payment_scope_run(scope_candidate)
                if delivered_entry is not None:
                    payment_scope_context = dict(delivered_entry["context"])
                elif generic_payment:
                    billing_config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
                    if not billing_config.get("enabled", True) or billing_config.get("scope_protocol") != "agent-payment/1":
                        raise HTTPException(501, "This provider has not enabled the generic payment adapter.")
                    payment_scope_context = await self._verify_remote_practice_payment_scope(
                        plaza_url=str(verified.get("plaza_url") or self.plaza_url or ""), request=request,
                        practice_id=practice_id, amount=credit_cost, request_id=request_id)
                elif credit_cost > 0:
                    billing_config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
                    scope_candidate = self._payment_scope_from_request(request, practice_id)
                    has_scope = bool(scope_candidate.get("scope_id") and scope_candidate.get("scope_token"))
                    billing_enabled = billing_config.get("enabled", True)
                    if billing_enabled and has_scope and billing_config.get("use_scope_when_present", True):
                        payment_scope_context = await self._verify_remote_practice_payment_scope(
                            plaza_url=str(verified.get("plaza_url") or self.plaza_url or ""),
                            request=request,
                            practice_id=practice_id,
                            amount=credit_cost,
                            request_id=request_id,
                        )
                    elif billing_enabled and billing_config.get("require_scope_for_paid_practices"):
                        payment_scope_context = await self._verify_remote_practice_payment_scope(
                            plaza_url=str(verified.get("plaza_url") or self.plaza_url or ""),
                            request=request,
                            practice_id=practice_id,
                            amount=credit_cost,
                            request_id=request_id,
                        )
                    elif billing_enabled and billing_config.get("legacy_fallback", True):
                        await self._charge_remote_practice_credit_points(
                            plaza_url=str(verified.get("user_plaza_url") or verified.get("plaza_url") or self.plaza_url or ""),
                            caller_user_token=str(request.caller_user_token or ""),
                            practice_id=practice_id,
                            amount=credit_cost,
                            request_id=request_id,
                            caller_agent_id=inbound_context.get("caller_agent_id", ""),
                            caller_agent_name=inbound_context.get("caller_name", ""),
                        )
                    elif billing_enabled:
                        raise HTTPException(
                            status_code=402,
                            detail="A Plaza payment scope is required for paid remote practices",
                        )
                elif request_cost is None:
                    billing_config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
                    # Priced in its quote, not on the practice. A call that isn't the owner's own must bring a scope
                    # covering that price (2026-11-14: unpaid llm_chat ran on the operator's key).
                    quoted_price = 0
                    if billing_config.get("enabled", True) and not self._caller_is_own_owner(verified):
                        quoted_price = self._quoted_price_or_refuse(practice_id, request.content)
                    if scope_candidate.get("scope_id") and scope_candidate.get("scope_token"):
                        # Marks the paid work started so the caller's capture pays it.
                        payment_scope_context = await self._verify_quoted_payment_scope(
                            plaza_url=str(verified.get("plaza_url") or self.plaza_url or ""),
                            request=request,
                            practice_id=practice_id,
                            request_id=request_id,
                            amount=quoted_price,
                        )
                    if quoted_price > 0 and not payment_scope_context:
                        raise HTTPException(
                            status_code=402,
                            detail="A Plaza payment scope covering this request's quoted price is required",
                        )
                practice_content = request.content
                if isinstance(practice_content, dict) and "_progress" in practice_content:
                    practice_content = dict(practice_content)
                    practice_content.pop("_progress", None)
                execution_content = self._inject_remote_caller_context_for_practice(
                    practice_id=practice_id,
                    content=practice_content,
                    request=request,
                    verified=verified,
                )
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="running",
                    detail="Remote practice is executing.",
                )
                if delivered_entry is not None:
                    result = delivered_entry["result"]
                elif payment_scope_context.get("replayed"):
                    result = payment_scope_context["replay_result"]
                elif bundled_practice is not None:
                    if generic_payment:
                        await self._generic_payment_adapter().validate_before_execution(payment_scope_context)
                    result = await run_in_threadpool(
                        bundled_practice.execute_callable_endpoint,
                        practice_id,
                        execution_content,
                    )
                else:
                    if generic_payment:
                        await self._generic_payment_adapter().validate_before_execution(payment_scope_context)
                    result = await self._execute_local_practice_async(local_practice, execution_content)
                # A caller that settles a whole run through its escrow's capture asks providers not to complete their
                # scopes: Plaza pays each quoted step when the run is captured. A refund still pays the steps Plaza
                # recorded as delivered.
                billing_config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
                settles_on_capture = (not generic_payment and isinstance(request.billing, Mapping)
                                      and request.billing.get("settlement") == "escrow-capture"
                                      and billing_config.get("allow_settlement_on_capture", True))
                settles_on_capture = settles_on_capture or bool(payment_scope_context.get("settle_on_capture"))
                settles_on_capture = settles_on_capture or delivered_entry is not None
                if payment_scope_context and not settles_on_capture and (generic_payment or self.remote_practice_billing.get("complete_scope_on_success", True)):
                    completion = {"billing_context": payment_scope_context, "practice_id": practice_id, "request_id": request_id}
                    if generic_payment:
                        completion["result"] = result
                    await self._complete_remote_practice_payment_scope(**completion)
                elif payment_scope_context and settles_on_capture and not generic_payment:
                    # The result is released only once Plaza confirms this scope delivered, so a refund instead of a
                    # capture still pays the work. Until then it is held for a retry with the same scope.
                    self._hold_delivery_result(payment_scope_context, practice_id, request.content, result)
                    await self._acknowledge_payment_scope_delivery(payment_scope_context, request_id, practice_id)
            except HTTPException as exc:
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="inbound",
                    event="result",
                    practice_id=practice_id,
                    peer_agent_id=inbound_context.get("caller_agent_id", ""),
                    peer_name=inbound_context.get("caller_name", ""),
                    peer_address=inbound_context.get("caller_address", ""),
                    plaza_url=inbound_context.get("plaza_url", ""),
                    auth_mode=inbound_context.get("auth_mode", ""),
                    policy_allowed=True,
                    policy_reason=policy_decision.get("reason", ""),
                    outcome="failed",
                    status_code=exc.status_code,
                    error=str(exc.detail or ""),
                )
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status=str(exc.status_code),
                    ok=False,
                    detail="Remote practice failed.",
                    error=str(exc.detail or ""),
                    ended=True,
                )
                raise
            except Exception as exc:
                self.logger.exception(f"Remote UsePractice '{practice_id}' failed: {exc}")
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="inbound",
                    event="result",
                    practice_id=practice_id,
                    peer_agent_id=inbound_context.get("caller_agent_id", ""),
                    peer_name=inbound_context.get("caller_name", ""),
                    peer_address=inbound_context.get("caller_address", ""),
                    plaza_url=inbound_context.get("plaza_url", ""),
                    auth_mode=inbound_context.get("auth_mode", ""),
                    policy_allowed=True,
                    policy_reason=policy_decision.get("reason", ""),
                    outcome="failed",
                    status_code=500,
                    error=str(exc),
                )
                self._emit_remote_practice_progress(
                    progress_context,
                    practice_id=practice_id,
                    request_id=request_id,
                    status="error",
                    ok=False,
                    detail="Remote practice failed.",
                    error=str(exc),
                    ended=True,
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            finally:
                if running_scope_id:
                    self._payment_scope_runs_in_flight().discard(running_scope_id)

            self._record_remote_practice_audit(
                request_id=request_id,
                direction="inbound",
                event="result",
                practice_id=practice_id,
                peer_agent_id=inbound_context.get("caller_agent_id", ""),
                peer_name=inbound_context.get("caller_name", ""),
                peer_address=inbound_context.get("caller_address", ""),
                plaza_url=inbound_context.get("plaza_url", ""),
                auth_mode=inbound_context.get("auth_mode", ""),
                policy_allowed=True,
                policy_reason=policy_decision.get("reason", ""),
                outcome="succeeded",
                status_code=200,
            )
            self.logger.debug(
                f"Completed remote UsePractice '{practice_id}' for "
                f"verified caller '{verified.get('agent_id')}'"
            )
            self._emit_remote_practice_progress(
                progress_context,
                practice_id=practice_id,
                request_id=request_id,
                status="success",
                ok=True,
                detail="Remote practice completed.",
                ended=True,
            )
            return {"status": "ok", "practice_id": practice_id, "result": result}

        # Lifecycle events to auto-register on startup
        @self.app.on_event("startup")
        def startup_event():
            """Handle startup event for the base agent."""
            if self.plaza_url and self.name != "Plaza" and self.plaza_auto_register:
                threading.Thread(target=self.register).start()

    def _start_plaza_heartbeat_thread(self) -> bool:
        # Dispatch explicitly to the base implementation when a worker supplies
        # its own dispatcher heartbeat starter. Keep the historical method hook.
        if type(self)._start_heartbeat_thread is not BaseAgent._start_heartbeat_thread:
            return BaseAgent._start_heartbeat_thread(self)
        return self._start_heartbeat_thread()

    def _start_heartbeat_thread(self) -> bool:
        """Internal helper to start the heartbeat thread."""
        with self._plaza_heartbeat_thread_lock:
            current_thread = self._plaza_heartbeat_thread
            if current_thread and current_thread.is_alive():
                return False
            heartbeat_thread = threading.Thread(
                target=(self._heartbeat_loop if type(self)._start_heartbeat_thread is BaseAgent._start_heartbeat_thread
                        else lambda: BaseAgent._heartbeat_loop(self)),
                daemon=True,
                name=f"{self.name}-heartbeat",
            )
            self._plaza_heartbeat_thread = heartbeat_thread
            if type(self)._start_heartbeat_thread is BaseAgent._start_heartbeat_thread:
                self._heartbeat_thread = heartbeat_thread
            heartbeat_thread.start()
            return True

    def _start_reconnect_thread(self, initial_delay: Optional[float] = None) -> bool:
        """Internal helper to start the reconnect thread."""
        with self._reconnect_thread_lock:
            current_thread = self._reconnect_thread
            if current_thread and current_thread.is_alive():
                return False
            reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                args=(self.PLAZA_RECONNECT_INTERVAL if initial_delay is None else float(initial_delay),),
                daemon=True,
                name=f"{self.name}-plaza-reconnect",
            )
            self._reconnect_thread = reconnect_thread
            reconnect_thread.start()
            return True

    def _has_active_reconnect_thread(self) -> bool:
        """Return whether the value has active reconnect thread."""
        with self._reconnect_thread_lock:
            current_thread = self._reconnect_thread
            return bool(current_thread and current_thread.is_alive())

    @staticmethod
    def _is_plaza_starting_response(response: Any) -> bool:
        """Return whether the value is a Plaza starting response."""
        if response is None or getattr(response, "status_code", None) != 503:
            return False
        try:
            body = response.json()
            if isinstance(body, dict) and str(body.get("detail", "")).strip().lower() == "starting":
                return True
        except Exception:
            pass
        return "starting" in str(getattr(response, "text", "")).lower()

    def _schedule_reconnect(self, reason: str, *, initial_delay: Optional[float] = None):
        """Internal helper to schedule the reconnect."""
        self._plaza_connection_error = str(reason or "").strip() or "Plaza connection lost"
        self.plaza_token = None
        self.token_expires_at = 0.0
        started = self._start_reconnect_thread(initial_delay=initial_delay)
        if started:
            self.logger.warning(
                f"Lost Plaza connection ({reason}). "
                f"Will retry registration every {self.PLAZA_RECONNECT_INTERVAL}s until shutdown."
            )

    def _reconnect_loop(self, initial_delay: float = 0.0):
        """Internal helper for reconnect loop."""
        delay = max(float(initial_delay), 0.0)
        while True:
            if not self.plaza_url:
                return
            if delay > 0:
                time.sleep(delay)
            if self.plaza_token and time.time() < (self.token_expires_at - 60):
                return
            response = self.register(start_reconnect_on_failure=False, request_retries=0)
            if response is not None and response.status_code == 200 and self.plaza_token:
                self.logger.info(f"Reconnected to Plaza at {self.plaza_url}.")
                return
            delay = self.PLAZA_RECONNECT_INTERVAL

    def register(
        self,
        *,
        start_reconnect_on_failure: bool = True,
        request_retries: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ):
        """Register this agent with the Plaza via HTTP."""
        if not self.plaza_url:
            return
        with self._register_lock:
            force_register = bool(getattr(self, "_force_plaza_register_once", False))
            if force_register:
                self._force_plaza_register_once = False
            if self.plaza_token and time.time() < (self.token_expires_at - 60) and not force_register:
                self._start_plaza_heartbeat_thread()
                return

            self._load_plaza_credentials_from_pool()
            self._sync_trusted_plaza_metadata()
            self._sync_connectivity_metadata()

            # Ensure our address is known in the card
            self.agent_card["host"] = self.host
            self.agent_card["port"] = self.port
            self.agent_card["address"] = self._resolve_advertised_address()

            try:
                attempt = 0
                while True:
                    has_stored_creds = bool(self.agent_id and self.api_key)
                    if has_stored_creds and self._credential_retry_after > time.time():
                        delay = max(self._credential_retry_after - time.time(), 0.0)
                        if delay > 0:
                            self.logger.warning(
                                f"Plaza rejected stored credentials. Waiting {int(delay)}s before retrying."
                            )
                            time.sleep(delay)

                    payload = self.build_register_payload(
                        plaza_url=self.plaza_url,
                        card=self.agent_card,
                        address=self.agent_card["address"],
                        expires_in=3600,
                        pit_type=self.agent_card.get("pit_type"),
                        pit_id=self.agent_id if has_stored_creds else None,
                        api_key=self.api_key if has_stored_creds else None,
                        accepts_inbound_from_plaza=self.agent_card.get("accepts_inbound_from_plaza"),
                    )
                    response = self._plaza_post(
                        "/register",
                        json=payload,
                        retries=request_retries,
                        timeout=request_timeout,
                    )
                    if response.status_code == 401 and has_stored_creds:
                        self.plaza_token = None
                        self.token_expires_at = 0.0
                        self._credential_retry_after = time.time() + self.PLAZA_REJECTED_CREDENTIAL_RETRY_DELAY
                        self.logger.warning(
                            f"Stored Plaza credentials were rejected. "
                            f"Retrying with the same credential after {self.PLAZA_REJECTED_CREDENTIAL_RETRY_DELAY}s."
                        )
                        if attempt >= 1:
                            return response
                        attempt += 1
                        continue
                    self._credential_retry_after = 0.0
                    break
                if response.status_code == 200:
                    data = response.json()
                    self.plaza_token = data.get("token")
                    self._plaza_registered_agent_name = str(payload.get("agent_name") or self.name)
                    self.token_expires_at = time.time() + data.get("expires_in", 3600)
                    self.agent_id = data.get("agent_id", self.agent_id)
                    self.api_key = data.get("api_key", self.api_key)
                    self.agent_card["agent_id"] = self.agent_id
                    self._refresh_pit_address()
                    self._save_plaza_credentials_to_pool()
                    self._mark_plaza_connection_alive()
                    self.logger.info(f"Successfully registered with Plaza at {self.plaza_url}. Token expires in {data.get('expires_in', 3600)}s")
                    self._start_plaza_heartbeat_thread()
                elif self._is_plaza_starting_response(response):
                    self.logger.info(f"Plaza at {self.plaza_url} is still starting. Deferring reconnect.")
                    if start_reconnect_on_failure:
                        self._schedule_reconnect("plaza starting")
                else:
                    self.logger.error(f"Registration failed: {response.text}")
                    if start_reconnect_on_failure:
                        self._schedule_reconnect(f"registration failed with status {response.status_code}")
                return response
            except Exception as e:
                self.logger.error(f"Failed to contact Plaza: {e}")
                if start_reconnect_on_failure:
                    self._schedule_reconnect(str(e))
                return None

    def _ensure_token_valid(self) -> Optional[Dict[str, str]]:
        """Ensures the token is valid, renewing it if it is close to expiry (within 60s)."""
        if not self.plaza_token:
            return None
            
        if time.time() > (self.token_expires_at - 60):
            try:
                response = self._plaza_post(
                    "/renew",
                    json={"agent_name": getattr(self, "_plaza_registered_agent_name", self.name), "expires_in": 3600},
                    headers={"Authorization": f"Bearer {self.plaza_token}"},
                )
                if response.status_code == 200:
                    data = response.json()
                    self.plaza_token = data.get("token")
                    self.token_expires_at = time.time() + data.get("expires_in", 3600)
                    self._plaza_connection_error = ""
                    self.logger.info(f"Token renewed successfully.")
                else:
                    self.logger.warning(f"Token renewal failed: {response.text}. Will attempt re-register with existing agent_id.")
                    self.plaza_token = None
                    self._schedule_reconnect(f"token renewal failed with status {response.status_code}")
            except Exception as e:
                self.logger.error(f"Token renewal request failed: {e}")
                self._schedule_reconnect(str(e))
                
        if self.plaza_token:
            return {"Authorization": f"Bearer {self.plaza_token}"}
        return None

    def send(self, receiver_addr: str, content: Any, msg_type: str = "message"):
        with self.observations.span(msg_type, kind="message", peer_agent_id=receiver_addr) as span:
            result = self._send_observed_message(receiver_addr, content, msg_type)
            span["outcome"] = "success" if result is not False else "error"
            self.observations.event("interaction", direction="outbound", phase="result", operation=msg_type,
                                    peer_agent_id=receiver_addr, status=span["outcome"])
            return result

    def _send_observed_message(self, receiver_addr: str, content: Any, msg_type: str = "message"):
        """Send a message to another agent via HTTP."""
        from prompits.core.observability import trace_headers
        target_url = receiver_addr
        target_card = None
        
        # Autocomplete URL if missing
        if not target_url.startswith("http"):
             # Try to lookup via Plaza
             resolved = self.lookup_agent_info(receiver_addr)
             if resolved:
                 target_url = resolved['card'].get('address')
                 target_card = resolved['card']
             else:
                 self.logger.error(f"Could not resolve address for {receiver_addr}")
                 return False

        if target_card:
            practices_list = target_card.get("practices", [])
            has_mailbox = any(p.get("id") == "mailbox" for p in practices_list)
            target_url = target_card.get("address")
        else:
            self.logger.error(f"No target card found for {receiver_addr}")
            return False
        try:
            self._assert_remote_protocol_compatible(
                target={
                    "agent_id": str(target_card.get("agent_id") or receiver_addr),
                    "card": target_card,
                    "url": target_url,
                }
            )
        except HTTPException as exc:
            self.logger.error(f"Cannot send message to {target_url}: {exc.detail}")
            return False
            
        message = Message(sender=self.name, receiver=receiver_addr, content=content, msg_type=msg_type)
        try:
            import json
            payload = json.loads(message.json())
            
            # Check if msg_type matches a specific practice endpoint
            practice_info = next((p for p in practices_list if p.get("id") == msg_type), None)
            
            if practice_info and "path" in practice_info:
                endpoint = practice_info["path"]
                resp = requests.post(f"{target_url}{endpoint}", json=payload, timeout=self.REMOTE_PRACTICE_REQUEST_TIMEOUT, headers=trace_headers())
            elif has_mailbox:
                resp = requests.post(f"{target_url}/mailbox", json=payload, timeout=self.REMOTE_PRACTICE_REQUEST_TIMEOUT, headers=trace_headers())
            else:
                self.logger.error(f"Agent {receiver_addr} has no communication practice or mailbox endpoint.")
                return False
                
            if resp.status_code == 200:
                try:
                    return resp.json()
                except:
                    return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to send message to {target_url}: {e}")
            return False

    def _heartbeat_loop(self):
        """Send heartbeats to Plaza while authenticated."""
        self.logger.info(f"Starting heartbeat loop...")
        while True:
            try:
                time.sleep(self.PLAZA_HEARTBEAT_INTERVAL)
                if not self.plaza_url: break
                
                headers = self._ensure_token_valid()
                if not headers:
                    if not self._has_active_reconnect_thread():
                        self._schedule_reconnect(
                            self._plaza_connection_error or "heartbeat waiting for plaza token",
                            initial_delay=self.PLAZA_RECONNECT_INTERVAL,
                        )
                    continue
                
                payload = {"agent_id": self.agent_id, "agent_name": self.name}
                try:
                    payload["observability"] = self.observations.snapshot()
                except Exception:
                    self.logger.warning("Observation collection failed; sending identity heartbeat.")
                response = self._plaza_post("/heartbeat", json=payload, headers=headers)
                if response.status_code == 401:
                    self.logger.warning(f"Heartbeat unauthorized (401). Forcing re-register.")
                    self._schedule_reconnect("heartbeat unauthorized (401)")
                elif self._is_plaza_starting_response(response):
                    self._schedule_reconnect("plaza starting")
                elif response.status_code == 200:
                    data = response.json()
                    self._mark_plaza_connection_alive()
                    for diagnostic in data.get("diagnostics_requests", [])[:5]:
                        try:
                            result = self.observations.store.logs(diagnostic.get("query") or {})
                            self._plaza_post("/api/observability/diagnostics/" + diagnostic["id"] + "/complete",
                                             json=result, headers=headers)
                        except Exception:
                            self.logger.warning("Diagnostic request could not be completed.")
                    if isinstance(data.get("site_settings"), dict):
                        self.site_settings = data["site_settings"]
                    if data.get("needs_register"):
                        self.logger.info("Plaza requested registration refresh after heartbeat.")
                        self._force_plaza_register_once = True
                        self._schedule_reconnect("plaza requested registration refresh", initial_delay=0)
                elif response.status_code != 200:
                    self.logger.warning(f"Heartbeat failed with status {response.status_code}: {response.text}")
                    self._schedule_reconnect(f"heartbeat failed with status {response.status_code}")
            except Exception as e:
                self.logger.warning(f"Heartbeat failed: {e}")
                self._schedule_reconnect(str(e))

    def lookup_agent_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve agent name to full info (including card) via Plaza."""
        if not self.plaza_url: 
            return None
        try:
            headers = self._ensure_token_valid()
            if not headers:
                return None
                
            # Prefer resolving by agent_id first (id-only identity), then fall back to name for compatibility.
            resp = self._plaza_get("/search", params={"agent_id": name}, headers=headers)
            if resp.status_code == 200:
                results = resp.json()
                if results and len(results) > 0:
                    return results[0]

            resp = self._plaza_get("/search", params={"name": name}, headers=headers)
            if resp.status_code == 200:
                results = resp.json()
                if results and len(results) > 0:
                    # Search returns a list of matches, we take the first one
                    return results[0]
            elif resp.status_code == 401:
                logger.warning(f"[{self.name}] Lookup unauthorized (401). Forcing re-register.")
                self.register()
        except Exception as e:
            logger.error(f"[{self.name}] Lookup info failed for {name}: {e}")
        return None

    def lookup_agent(self, name: str) -> Optional[str]:
        """Look up the agent."""
        info = self.lookup_agent_info(name)
        if info:
            return info['card'].get('address')
        return None

    def search(self, role: str = None, practice: str = None, tag: str = None, **kwargs: Any):
        """Search for other agents via the Plaza HTTP API."""
        if not self.plaza_url:
            return []
        try:
            headers = self._ensure_token_valid()
            if not headers:
                return []
                
            params = dict(kwargs)
            if role: params['role'] = role
            if practice: params['practice'] = practice
            if tag: params['tag'] = tag
            
            logger.debug(f"[{self.name}] Searching Plaza with params: {params}")
            resp = self._plaza_get("/search", params=params, headers=headers)
            if resp.status_code == 200:
                results = resp.json()
                logger.debug(f"[{self.name}] Plaza search returned {len(results)} results")
                return results
            elif resp.status_code == 401:
                logger.warning(f"[{self.name}] Search unauthorized (401). Forcing re-register.")
                self.register()
                
            return []
        except Exception as e:
            logger.error(f"[{self.name}] Search failed: {e}")
            return []

    def _execute_local_practice_sync(self, practice: Practice, content: Any) -> Any:
        """Execute local practice in sync context; bridge awaitables when no event loop is running."""
        if isinstance(content, dict):
            result = practice.execute(**content)
        elif content is None:
            result = practice.execute()
        else:
            result = practice.execute(content=content)

        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
                raise RuntimeError("Local practice returned an awaitable; call UsePractice(..., async_mode=True).")
            except RuntimeError as e:
                if "no running event loop" in str(e).lower():
                    return asyncio.run(result)
                raise
        return result

    async def _execute_local_practice_async(self, practice: Practice, content: Any) -> Any:
        """Execute local practice in async context with awaitable passthrough."""
        if inspect.iscoroutinefunction(practice.execute):
            if isinstance(content, dict):
                result = await practice.execute(**content)
            elif content is None:
                result = await practice.execute()
            else:
                result = await practice.execute(content=content)
            return result

        if isinstance(content, dict):
            result = await run_in_threadpool(practice.execute, **content)
        elif content is None:
            result = await run_in_threadpool(practice.execute)
        else:
            result = await run_in_threadpool(practice.execute, content=content)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _practice_path_from_card(card: Dict[str, Any], practice_id: str) -> str:
        """Resolve endpoint path from agent card metadata with compatibility fallbacks."""
        practices = card.get("practices", []) if isinstance(card, dict) else []
        entry = next((p for p in practices if p.get("id") == practice_id), None)
        if entry and entry.get("path"):
            path = str(entry["path"])
            return path if path.startswith("/") else f"/{path}"
        if practice_id == BaseAgent.MAILBOX_PRACTICE_ID:
            return "/mailbox"
        return f"/{practice_id.replace('-', '_')}"

    def _lookup_registered_remote_target(self, target_url: str) -> Optional[Dict[str, Any]]:
        """Resolve a direct URL to its registered Plaza identity."""
        normalized_url = self._normalize_url(target_url)
        if not normalized_url or not self.plaza_url:
            return None

        now = time.time()
        with self._remote_target_directory_cache_lock:
            cached = self._remote_target_directory_cache.get(normalized_url)
            if isinstance(cached, dict) and float(cached.get("expires_at") or 0) > now:
                target = cached.get("target")
                return copy.deepcopy(target) if isinstance(target, dict) else None

        headers = self._ensure_token_valid()
        if not headers:
            return None
        try:
            response = self._plaza_get("/search", headers=headers, retries=0, timeout=10)
        except Exception as exc:
            self.logger.debug("Could not resolve registered target %s: %s", normalized_url, exc)
            return None
        if response.status_code != 200:
            return None

        candidates = response.json()
        if not isinstance(candidates, list):
            return None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            card = candidate.get("card") if isinstance(candidate.get("card"), dict) else {}
            candidate_url = self._normalize_url(card.get("address") or candidate.get("address"))
            if candidate_url != normalized_url:
                continue
            agent_id = str(candidate.get("agent_id") or card.get("agent_id") or "").strip()
            if not agent_id:
                continue
            resolved_card = dict(card)
            resolved_card.setdefault("address", target_url)
            resolved_card.setdefault("agent_id", agent_id)
            target = {"agent_id": agent_id, "card": resolved_card, "url": target_url}
            with self._remote_target_directory_cache_lock:
                self._remote_target_directory_cache[normalized_url] = {
                    "expires_at": now + 60,
                    "target": copy.deepcopy(target),
                }
            return target
        return None

    def _resolve_remote_target(self, pit_address: Any) -> Optional[Dict[str, Any]]:
        """Resolve PitAddress into current searchable target card + URL."""
        if isinstance(pit_address, str):
            target_url = pit_address.strip()
            if target_url.startswith(("http://", "https://")):
                registered_target = self._lookup_registered_remote_target(target_url)
                if registered_target:
                    return registered_target
                return {
                    "agent_id": "",
                    "card": {"address": target_url, "practices": []},
                    "url": target_url,
                }

        if isinstance(pit_address, dict):
            target_url = str(
                pit_address.get("address")
                or pit_address.get("url")
                or pit_address.get("target_url")
                or ""
            ).strip()
            if target_url.startswith(("http://", "https://")):
                target_card = pit_address.get("card") if isinstance(pit_address.get("card"), dict) else {}
                resolved_card = dict(target_card or {})
                resolved_card.setdefault("address", target_url)
                if not isinstance(resolved_card.get("practices"), list) and isinstance(pit_address.get("practices"), list):
                    resolved_card["practices"] = pit_address.get("practices")
                target_agent_id = str(
                    pit_address.get("pit_id")
                    or pit_address.get("agent_id")
                    or resolved_card.get("agent_id")
                    or ""
                )
                if not target_agent_id:
                    registered_target = self._lookup_registered_remote_target(target_url)
                    if registered_target:
                        discovered_card = registered_target.get("card")
                        if isinstance(discovered_card, dict):
                            registered_target["card"] = {**discovered_card, **resolved_card}
                        return registered_target
                return {"agent_id": target_agent_id, "card": resolved_card, "url": target_url}

        target_address = self._coerce_pit_address(pit_address)
        if not target_address:
            return None
        target_agent_id = str(target_address.pit_id)
        target_info = self.lookup_agent_info(target_agent_id)
        if not target_info:
            return None
        target_card = target_info.get("card", {})
        target_url = target_card.get("address")
        if not target_url:
            return None
        return {"agent_id": target_agent_id, "card": target_card, "url": target_url}

    @staticmethod
    def _extract_bearer_token(headers: Optional[Dict[str, str]]) -> Optional[str]:
        """Internal helper to extract the bearer token."""
        if not headers:
            return None
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if not auth_header:
            return None
        prefix = "Bearer "
        if auth_header.startswith(prefix):
            return auth_header[len(prefix):]
        return auth_header

    def _resolve_caller_user_token(self) -> str:
        """Return an optional Plaza user credential for outbound practice calls."""
        meta = self.agent_card.get("meta") if isinstance(self.agent_card.get("meta"), dict) else {}
        for container in (self.agent_card, meta):
            if not isinstance(container, dict):
                continue
            for key in ("caller_user_token", "plaza_user_token", "user_token"):
                token = str(container.get(key) or "").strip()
                if token:
                    return token

        for env_name in (
            "PROMPITS_PLAZA_CALLER_USER_TOKEN",
            "PROMPITS_PLAZA_USER_TOKEN",
            "PLAZA_USER_TOKEN",
        ):
            token = str(os.getenv(env_name) or "").strip()
            if token:
                return token
        if self._owner_key_caller_user_token_enabled():
            for env_name in ("PROMPITS_PLAZA_OWNER_KEY", "PLAZA_OWNER_KEY"):
                token = str(os.getenv(env_name) or "").strip()
                if token:
                    return token
        return ""

    @classmethod
    def _owner_key_caller_user_token_enabled(cls) -> bool:
        """Return whether owner-key fallback is enabled for caller user tokens."""
        configured = cls._coerce_optional_bool(
            os.getenv("PROMPITS_USE_OWNER_KEY_AS_CALLER_USER_TOKEN")
        )
        return True if configured is None else bool(configured)

    @staticmethod
    def _copy_verification_payload(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a shallow copy of one cached verification payload."""
        return dict(value or {})

    def _get_cached_plaza_verification(
        self,
        cache_name: str,
        *,
        scope: str,
        plaza_url: str,
        token: str,
        ttl_seconds: int,
        subject_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Return one non-expired Plaza verification payload from local cache."""
        cache = getattr(self, cache_name, None)
        if not isinstance(cache, dict):
            return None
        key = self._verification_cache_key(scope, plaza_url, token, subject_id)
        now = time.time()
        with self._plaza_verification_cache_lock:
            entry = cache.get(key)
            if not isinstance(entry, dict):
                return None
            verified_at = float(entry.get("verified_at") or 0.0)
            if verified_at <= 0 or now - verified_at > float(ttl_seconds):
                cache.pop(key, None)
                return None
            payload = entry.get("payload")
            return self._copy_verification_payload(payload if isinstance(payload, dict) else {})

    def _set_cached_plaza_verification(
        self,
        cache_name: str,
        *,
        scope: str,
        plaza_url: str,
        token: str,
        payload: Dict[str, Any],
        subject_id: str = "",
    ) -> None:
        """Store one Plaza verification payload for short-lived local reuse."""
        cache = getattr(self, cache_name, None)
        if not isinstance(cache, dict):
            return
        key = self._verification_cache_key(scope, plaza_url, token, subject_id)
        with self._plaza_verification_cache_lock:
            cache[key] = {
                "verified_at": time.time(),
                "payload": self._copy_verification_payload(payload),
            }

    def _configured_ui_agent_keys(self) -> List[str]:
        """Return configured Plaza UI agent key secrets usable for user auth."""
        keys: List[str] = []
        for value in (
            os.getenv("PROMPITS_PLAZA_CALLER_USER_TOKEN"),
            os.getenv("PROMPITS_PLAZA_USER_TOKEN"),
            os.getenv("PLAZA_USER_TOKEN"),
            os.getenv("PROMPITS_PLAZA_OWNER_KEY"),
            os.getenv("PLAZA_OWNER_KEY"),
        ):
            token = str(value or "").strip()
            if token and token not in keys:
                keys.append(token)
        return keys

    @classmethod
    def _billing_text_value(cls, payload: Mapping[str, Any], *keys: str) -> str:
        """Return the first non-empty text value from a billing payload."""
        for key in keys:
            value = payload.get(key)
            text = cls._policy_string(value)
            if text:
                return text
        return ""

    @classmethod
    def _normalize_payment_scope_payload(cls, payload: Any) -> Dict[str, Any]:
        """Normalize payment-scope identifiers from caller-provided billing metadata."""
        if not isinstance(payload, Mapping):
            return {}
        normalized = {
            "scope_id": cls._billing_text_value(
                payload,
                "scope_id",
                "billing_scope_id",
                "payment_scope_id",
                "plaza_payment_scope_id",
            ),
            "scope_token": cls._billing_text_value(
                payload,
                "scope_token",
                "billing_scope_token",
                "payment_scope_token",
                "plaza_payment_scope_token",
            ),
            "quote_id": cls._billing_text_value(payload, "quote_id", "billing_quote_id"),
            "pulse_name": cls._billing_text_value(payload, "pulse_name", "billing_pulse_name"),
            "pulse_address": cls._billing_text_value(payload, "pulse_address", "billing_pulse_address"),
        }
        if payload.get("protocol") == "agent-payment/1":
            for key in ("protocol", "intent_id", "operation_ref", "resource_refs", "max_points"):
                if key in payload:
                    normalized[key] = copy.deepcopy(payload[key])
        return normalized if any(normalized.values()) else {}

    @classmethod
    def _extract_remote_billing_from_content(cls, content: Any) -> Tuple[Dict[str, Any], Any]:
        """Lift reserved `_billing` scope metadata out of a remote practice payload."""
        if not isinstance(content, dict):
            return {}, content
        billing_value = content.get("_billing")
        billing = cls._normalize_payment_scope_payload(billing_value)
        if not billing:
            return {}, content
        sanitized = dict(content)
        sanitized.pop("_billing", None)
        return billing, sanitized

    @classmethod
    def _request_content_value(cls, content: Any, key: str) -> str:
        """Find a simple request value in common remote-practice content shapes."""
        if not isinstance(content, Mapping):
            return ""
        direct = cls._policy_string(content.get(key))
        if direct:
            return direct
        for container_key in ("input_data", "params", "payload"):
            nested = content.get(container_key)
            if isinstance(nested, Mapping):
                nested_value = cls._policy_string(nested.get(key))
                if nested_value:
                    return nested_value
        return ""

    def _payment_scope_from_request(self, request: PracticeInvocationRequest, practice_id: str) -> Dict[str, Any]:
        """Resolve payment-scope metadata from the request envelope."""
        billing = self._normalize_payment_scope_payload(request.billing)
        top_level = self._normalize_payment_scope_payload(
            {
                "scope_id": request.billing_scope_id,
                "scope_token": request.billing_scope_token,
                "quote_id": request.billing_quote_id,
                "pulse_name": request.billing_pulse_name,
                "pulse_address": request.billing_pulse_address,
            }
        )
        resolved = {
            key: top_level.get(key) or billing.get(key) or ""
            for key in ("scope_id", "scope_token", "quote_id", "pulse_name", "pulse_address")
        }
        if not resolved.get("pulse_name"):
            resolved["pulse_name"] = self._request_content_value(request.content, "pulse_name")
        if not resolved.get("pulse_address"):
            resolved["pulse_address"] = self._request_content_value(request.content, "pulse_address")
        resolved["practice_id"] = practice_id
        if billing.get("protocol") == "agent-payment/1":
            for key in ("protocol", "intent_id", "operation_ref", "resource_refs", "max_points"):
                if key in billing:
                    resolved[key] = copy.deepcopy(billing[key])
        return resolved

    def _build_remote_practice_payload(self, practice_id: str, content: Any, target: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper to build the remote practice payload."""
        caller_headers = self._ensure_token_valid() if self.plaza_url else None
        caller_token = self._extract_bearer_token(caller_headers)
        caller_direct_token = self.direct_auth_token
        caller_user_token = self._resolve_caller_user_token() if self.plaza_url else ""
        billing_scope, payload_content = self._extract_remote_billing_from_content(content)
        payload = PracticeInvocationRequest(
            sender=str(self.agent_id or self.name),
            receiver=str(target.get("agent_id") or ""),
            content=payload_content,
            msg_type=practice_id,
            request_id=str(uuid.uuid4()),
            caller_agent_address=self.pit_address.to_dict(),
            caller_agent_name=self.name,
            caller_agent_url=self._normalize_url(self.agent_card.get("address")),
            caller_plaza_token=caller_token,
            caller_direct_token=caller_direct_token,
            caller_user_token=caller_user_token or None,
            caller_protocol_version=str(self.AGENT_PROTOCOL_VERSION),
            billing_scope_id=billing_scope.get("scope_id") or None,
            billing_scope_token=billing_scope.get("scope_token") or None,
            billing_quote_id=billing_scope.get("quote_id") or None,
            billing_pulse_name=billing_scope.get("pulse_name") or None,
            billing_pulse_address=billing_scope.get("pulse_address") or None,
            billing=billing_scope or None,
        )
        from prompits.core.observability import TRACE
        context = TRACE.get() or {}
        payload.trace_id, payload.span_id = context.get("trace_id"), context.get("span_id")
        result = payload.model_dump()
        if self.plaza_url and self._normalize_url(target.get('url')) != self._normalize_url(self.plaza_url):
            from prompits.core.call_grants import binding, CURRENT
            parent=CURRENT.get()
            grant_path='/api/call-grants'
            grant_payload={'audience':result['receiver'],'binding':binding('POST','/use_practice/'+practice_id,result),
                           'operation':self._request_content_value(payload_content,'pulse_name') or practice_id,
                           **({'parent':parent['token']} if parent else {})}
            if billing_scope.get('protocol')=='agent-payment/1':
                from prompits.core.call_grants import AUTH_FIELDS
                grant_path='/api/operation-grants'
                grant_payload={'audience':result['receiver'],'operation_ref':billing_scope.get('operation_ref'),
                    'resource_refs':billing_scope.get('resource_refs',[]),'intent_id':billing_scope.get('intent_id'),
                    'max_points':billing_scope.get('max_points'),'method':'POST','path':'/use_practice/'+practice_id,
                    'body':{k:v for k,v in result.items() if k not in AUTH_FIELDS},
                    **({'parent':parent['token']} if parent else {})}
            response=self._plaza_request('post',grant_path,headers={
                'X-Agent-Token':caller_token or '', 'Authorization':'Bearer '+(caller_user_token or caller_token or '')},
                json=grant_payload,timeout=10,retries=0)
            if response.status_code != 200:
                try:
                    response_detail = str(response.json().get("detail") or "").strip()
                except (AttributeError, TypeError, ValueError):
                    response_detail = ""
                self.logger.warning(
                    "Scoped remote authorization failed with Plaza status %s%s.",
                    response.status_code,
                    f": {response_detail}" if response_detail else "",
                )
                raise HTTPException(403, 'Scoped remote authorization is unavailable.')
            scoped=response.json()['token']
            result.update(caller_plaza_token=scoped,caller_user_token=scoped if parent and parent.get('user') or caller_user_token else None,caller_direct_token=None)
        return result

    @staticmethod
    def _unwrap_remote_practice_response(payload: Any) -> Any:
        """Internal helper for unwrap remote practice response."""
        if isinstance(payload, dict) and payload.get("status") == "ok" and "result" in payload:
            return payload["result"]
        return payload

    def _inject_remote_caller_context_for_practice(
        self,
        *,
        practice_id: str,
        content: Any,
        request: PracticeInvocationRequest,
        verified: Dict[str, Any],
    ) -> Any:
        """Internal helper for inject remote caller context for the practice."""
        if practice_id != "get_pulse_data" or not isinstance(content, dict):
            return content

        caller_pit_address = self._coerce_pit_address(request.caller_agent_address)
        caller_pit_id = caller_pit_address.pit_id if caller_pit_address else ""
        caller_context = {
            "agent_id": str(verified.get("agent_id") or caller_pit_id or ""),
            "agent_name": str(verified.get("agent_name") or request.sender or ""),
            "pit_id": str(caller_pit_id or ""),
            "pit_address": caller_pit_address.to_dict() if caller_pit_address else {},
            "plaza_url": str(verified.get("plaza_url") or ""),
            "auth_mode": str(verified.get("auth_mode") or ""),
            "sender": str(request.sender or ""),
            # Whether the call is this provider's owner's own (`_caller_is_own_owner`), e.g. for an LLM prompt limit.
            "own_owner": self._caller_is_own_owner(verified),
        }
        user_context = self._verified_user_from_context(verified)
        if user_context.get("id"):
            caller_context["user"] = user_context

        enriched = dict(content)
        injected = False
        for key in ("input_data", "params"):
            value = enriched.get(key)
            if not isinstance(value, dict):
                continue
            nested = dict(value)
            # The verified identity always replaces any caller-supplied _caller.
            nested["_caller"] = caller_context
            enriched[key] = nested
            injected = True

        if not injected:
            enriched["_caller"] = caller_context
        return enriched

    @staticmethod
    def _practice_credit_point_cost(value: Any) -> int:
        """Convert a practice cost value to whole Plaza credit points."""
        try:
            normalized = float(value or 0)
        except (TypeError, ValueError):
            return 0
        if normalized <= 0:
            return 0
        whole = int(normalized)
        return whole if normalized == whole else whole + 1

    async def _charge_remote_practice_credit_points(
        self,
        *,
        plaza_url: str,
        caller_user_token: str,
        practice_id: str,
        amount: int,
        request_id: str,
        caller_agent_id: str = "",
        caller_agent_name: str = "",
        reason: str = "",
        source: str = "remote_practice",
    ) -> Dict[str, Any]:
        """Spend Plaza credit points for one remote UsePractice execution."""
        normalized_plaza_url = self._normalize_url(plaza_url or self.plaza_url)
        token = str(caller_user_token or "").strip()
        if amount <= 0:
            return {}
        if not normalized_plaza_url:
            raise HTTPException(status_code=501, detail="Plaza credit point service is not configured")
        if not token:
            raise HTTPException(status_code=401, detail="A Plaza user token is required for paid remote practices")
        payload = {
            "action": "spend",
            "amount": amount,
            "caller_user_token": token,
            "reason": reason or f"Remote practice use: {practice_id}",
            "source": source or "remote_practice",
            "request_id": request_id,
            "metadata": {
                "practice_id": practice_id,
                "agent_id": str(self.agent_id or ""),
                "agent_name": str(self.name or ""),
                "caller_agent_id": caller_agent_id,
                "caller_agent_name": caller_agent_name,
            },
        }
        response = await self._plaza_request_async(
            "post",
            "/credits",
            plaza_url=normalized_plaza_url,
            json=payload,
        )
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                detail = data.get("detail") or data.get("message") or detail
            except Exception:
                pass
            raise HTTPException(status_code=response.status_code, detail=detail)
        return response.json() if response.content else {}

    def practice_request_cost(self, practice_id: str, content: Any) -> Optional[int]:
        """This request's Plaza-point price when the provider quotes requests individually; None uses the practice cost."""
        return None

    def quoted_request_price(self, practice_id: str, content: Any) -> Optional[int]:
        """Points this provider's own quote names for a request whose practice is free; 0 when it is free too."""
        return 0

    def _caller_is_own_owner(self, verified: Mapping[str, Any]) -> bool:
        """The provider's own owner pays nothing to itself: its agents (Plaza-verified same owner), the
        operator's direct token, or its owner's own signed-in account.

        A call grant names the calling agent; Plaza issues an onward hop only between agents of the same verified
        owner, and records `same_owner` as a root call's basis. The account a grant acts for may be another user's,
        when the owner's own service works on that user's request; the service decides what it spends.
        """
        mode = str(verified.get("auth_mode") or "")
        if mode in {"plaza", "direct"}:
            return True
        grant = verified.get("delegation") if isinstance(verified.get("delegation"), Mapping) else {}
        if mode == "scoped_call" and grant:
            from prompits.core.approved_access import SAME_OWNER
            try:
                onward = int(grant.get("depth") or 0) > 0
            except (TypeError, ValueError):
                onward = False
            if onward or grant.get("basis") == SAME_OWNER:
                return True
        owner = self._configuration_owner_id()
        return bool(owner) and str(verified.get("user_id") or "").strip() == owner

    def _quoted_price_or_refuse(self, practice_id: str, content: Any) -> int:
        try:
            return max(int(self.quoted_request_price(practice_id, content) or 0), 0)
        except Exception as exc:
            self.logger.warning(f"Could not price '{practice_id}' for an unpaid call: {exc}")
            raise HTTPException(status_code=402, detail="This request could not be priced; send a Plaza payment scope.")

    async def _verify_remote_practice_payment_scope(
        self,
        *,
        plaza_url: str,
        request: PracticeInvocationRequest,
        practice_id: str,
        amount: int,
        request_id: str,
        quoted: bool = False,
    ) -> Dict[str, Any]:
        """Verify a Plaza payment scope before executing a paid remote practice.

        `quoted` verifies a scope although the practice names no price, for a request its caller paid for by quote.
        """
        if (request.billing or {}).get("protocol") == "agent-payment/1":
            normalized_plaza_url = self._normalize_url(plaza_url or self.plaza_url)
            if not normalized_plaza_url or normalized_plaza_url != self._normalize_url(self.plaza_url):
                raise HTTPException(403, "Generic payments require this provider's registered Plaza.")
            return await self._generic_payment_adapter().prepare(request=request, practice_id=practice_id,
                                                                amount=amount, plaza_url=normalized_plaza_url)
        if amount <= 0 and not quoted:
            return {}
        billing = self._payment_scope_from_request(request, practice_id)
        scope_id = billing.get("scope_id")
        scope_token = billing.get("scope_token")
        if not scope_id or not scope_token:
            raise HTTPException(
                status_code=402,
                detail="A Plaza payment scope is required for paid remote practices",
            )
        normalized_plaza_url = self._normalize_url(plaza_url or self.plaza_url)
        if not normalized_plaza_url:
            raise HTTPException(status_code=501, detail="Plaza payment scope service is not configured")
        payload = {
            "scope_token": scope_token,
            "quote_id": billing.get("quote_id") or None,
            "practice_id": practice_id,
            "pulse_name": billing.get("pulse_name") or None,
            "pulse_address": billing.get("pulse_address") or None,
            # This execution's claim on the scope; Plaza's retries of the same verify keep it, any other is refused.
            "claim_id": secrets.token_hex(16),
        }
        response = await self._plaza_request_async(
            "post",
            f"/api/payment-scopes/{scope_id}/verify",
            plaza_url=normalized_plaza_url,
            json=payload,
        )
        scope_status = str((getattr(response, "headers", None) or {}).get("x-payment-scope-status") or "").strip().lower()
        if response.status_code == 409 and scope_status in {"running", "completed"}:
            # Plaza says this scope's work already started or was delivered, and no result is held here for it (a
            # restart or an evicted hold): answer definitely, never run it again.
            self.logger.warning(f"Payment scope {scope_id} already ran ({scope_status}); its result is not held here")
            raise PaymentScopeExecuted(str(scope_id), scope_status)
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                detail = data.get("detail") or data.get("message") or detail
            except Exception:
                pass
            raise HTTPException(status_code=response.status_code, detail=detail)
        verified = response.json() if response.content else {}
        verified_scope = verified.get("scope") if isinstance(verified.get("scope"), dict) else {}
        authorized_points = self._practice_credit_point_cost(verified_scope.get("max_points"))
        # A 0-point scope used to pass for a paid practice (2026-11-13): any scope must cover the price.
        if amount > 0 and authorized_points < amount:
            raise HTTPException(
                status_code=402,
                detail="Payment scope does not cover the paid remote practice cost",
            )
        # A scope that pays anyone pays this agent's owner: one made out to another agent would leave this work unpaid.
        if (amount > 0 or authorized_points > 0) and (
            not str(self.agent_id or "").strip()
            or str(verified_scope.get("provider_agent_id") or "").strip() != str(self.agent_id or "").strip()
        ):
            raise HTTPException(status_code=403, detail="Payment scope is made out to another agent")
        return {
            "scope_id": scope_id,
            "scope_token": scope_token,
            "quote_id": billing.get("quote_id") or "",
            "pulse_name": billing.get("pulse_name") or "",
            "pulse_address": billing.get("pulse_address") or "",
            "plaza_url": normalized_plaza_url,
            "amount": amount,
            "request_id": request_id,
            "verified": verified,
        }

    # Results of escrow-settled work wait here, keyed by payment scope, until Plaza confirms the delivery. A retry
    # with the same scope gets the held result once Plaza confirms, so paid work never runs twice. This is in-memory
    # state for active runs only: it is bounded and expires, and a provider restart forgets it.
    DELIVERY_RESULT_HOLD_SECONDS = 900
    DELIVERY_RESULT_HOLD_LIMIT = 256
    DELIVERY_ACK_ATTEMPTS = 4
    DELIVERY_ACK_BACKOFF_SECONDS = 0.5
    DELIVERY_ACK_RETRY_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def _payment_scope_runs_in_flight(self) -> set:
        running = getattr(self, "_payment_scope_running_ids", None)
        if running is None:
            running = self._payment_scope_running_ids = set()
        return running

    def _claim_payment_scope_run(self, scope: Mapping[str, Any]) -> str:
        """Mark this scope's work running; a second call with the same scope meanwhile is told to retry later."""
        scope_id = str(scope.get("scope_id") or "").strip()
        if not scope_id or not str(scope.get("scope_token") or "").strip():
            return ""
        running = self._payment_scope_runs_in_flight()
        if scope_id in running:
            raise HTTPException(
                status_code=409,
                detail="This payment scope's work is already running; retry the call with the same payment scope.",
                headers={"Retry-After": "2"},
            )
        running.add(scope_id)
        return scope_id

    def _held_delivery_results(self) -> Dict[str, Dict[str, Any]]:
        held = getattr(self, "_payment_scope_held_results", None)
        if held is None:
            held = self._payment_scope_held_results = {}
        now = time.monotonic()
        for scope_id in [key for key, entry in held.items() if entry["expires_at"] <= now]:
            held.pop(scope_id, None)
        return held

    def _delivery_billing_setting(self, key: str, default: Any) -> Any:
        config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
        value = config.get(key)
        return default if value is None else value

    @staticmethod
    def _delivery_request_fingerprint(practice_id: str, content: Any) -> str:
        if isinstance(content, dict) and "_progress" in content:
            content = {key: value for key, value in content.items() if key != "_progress"}
        try:
            encoded = json.dumps([practice_id, content], sort_keys=True, default=str)
        except Exception:
            encoded = repr([practice_id, content])
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _hold_delivery_result(self, context: Mapping[str, Any], practice_id: str, content: Any, result: Any) -> None:
        """Keep a finished result under its payment scope until Plaza confirms the delivery (and for retries after)."""
        scope_id = str(context.get("scope_id") or "").strip()
        if not scope_id:
            return
        held = self._held_delivery_results()
        ttl = float(self._delivery_billing_setting("delivery_result_hold_seconds", self.DELIVERY_RESULT_HOLD_SECONDS))
        held[scope_id] = {
            "context": dict(context),
            "scope_token": str(context.get("scope_token") or ""),
            "fingerprint": self._delivery_request_fingerprint(practice_id, content),
            "result": result,
            "expires_at": time.monotonic() + max(ttl, 1.0),
        }
        limit = int(self._delivery_billing_setting("delivery_result_hold_limit", self.DELIVERY_RESULT_HOLD_LIMIT))
        while len(held) > max(limit, 1):
            held.pop(min(held, key=lambda key: held[key]["expires_at"]), None)

    def _held_delivery_for_request(self, scope: Mapping[str, Any], practice_id: str, content: Any) -> Optional[Dict[str, Any]]:
        """The held result for this scope, when the request carries the same scope token and asks the same thing."""
        scope_id = str(scope.get("scope_id") or "").strip()
        scope_token = str(scope.get("scope_token") or "").strip()
        if not scope_id or not scope_token:
            return None
        entry = self._held_delivery_results().get(scope_id)
        if entry is None:
            return None
        if not secrets.compare_digest(entry["scope_token"].encode("utf-8"), scope_token.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid payment scope token")
        if entry["fingerprint"] != self._delivery_request_fingerprint(practice_id, content):
            raise HTTPException(status_code=409, detail="This payment scope was already used for another request")
        return entry

    async def _acknowledge_payment_scope_delivery(self, context: Mapping[str, Any], request_id: str, practice_id: str) -> Dict[str, Any]:
        """Record this scope's delivery at Plaza and return only once Plaza confirms the same scope recorded or paid.

        Transient failures are retried with backoff. If Plaza still has not confirmed, the call fails with a retryable
        503 and the result stays held, so a retry with the same scope re-sends the record and returns the held result
        without running the work again. A definite refusal (the scope or its escrow is closed, the token, scope or
        agent does not match) drops the held result and fails the call: the work cannot be paid, so it is not released.
        """
        scope_id, scope_token = str(context.get("scope_id") or "").strip(), str(context.get("scope_token") or "").strip()
        if not scope_id or not scope_token:
            return {}
        plaza_url = self._normalize_url(context.get("plaza_url") or self.plaza_url)
        payload = {"scope_token": scope_token, "scope_id": scope_id, "request_id": request_id,
                   "agent_id": str(self.agent_id or ""), "metadata": {"practice_id": practice_id}}
        attempts = max(int(self._delivery_billing_setting("delivery_ack_attempts", self.DELIVERY_ACK_ATTEMPTS)), 1)
        backoff = max(float(self._delivery_billing_setting("delivery_ack_backoff_seconds", self.DELIVERY_ACK_BACKOFF_SECONDS)), 0.0)
        last_problem = ""
        for attempt in range(attempts):
            if attempt and backoff:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
            try:
                response = await self._plaza_request_async(
                    "post", f"/api/payment-scopes/{scope_id}/delivered", plaza_url=plaza_url, json=payload, retries=0)
            except Exception as exc:
                last_problem = str(exc) or exc.__class__.__name__
                self.logger.warning(f"Could not record delivery of scope {scope_id} (attempt {attempt + 1}/{attempts}): {last_problem}")
                continue
            status_code = int(getattr(response, "status_code", 0) or 0)
            if 200 <= status_code < 300:
                try:
                    confirmed = response.json()
                except Exception:
                    confirmed = {}
                if (isinstance(confirmed, dict) and str(confirmed.get("scope_id") or "") == scope_id
                        and str(confirmed.get("scope_status") or "") in {"completed", "paid"}):
                    return confirmed
                last_problem = "Plaza's answer did not confirm this scope"
                self.logger.warning(f"Plaza did not confirm delivery of scope {scope_id} (attempt {attempt + 1}/{attempts})")
                continue
            detail = getattr(response, "text", "")
            try:
                data = response.json()
                detail = (data.get("detail") or data.get("message") or detail) if isinstance(data, dict) else detail
            except Exception:
                pass
            if not 400 <= status_code < 500 or status_code in self.DELIVERY_ACK_RETRY_STATUS_CODES:
                last_problem = f"{status_code}: {detail}"
                self.logger.warning(f"Plaza did not record delivery of scope {scope_id} (attempt {attempt + 1}/{attempts}): {last_problem}")
                continue
            self._held_delivery_results().pop(scope_id, None)
            self.logger.warning(f"Plaza refused the delivery record of scope {scope_id}: {status_code}: {detail}")
            raise HTTPException(status_code=409,
                                detail=f"Plaza refused to record delivery of payment scope {scope_id}: {detail}")
        raise HTTPException(
            status_code=503,
            detail=(f"Plaza has not confirmed delivery of payment scope {scope_id} ({last_problem}). "
                    "Retry the call with the same payment scope to receive the result."),
            headers={"Retry-After": "5"},
        )

    async def _verify_quoted_payment_scope(
        self,
        *,
        plaza_url: str,
        request: PracticeInvocationRequest,
        practice_id: str,
        request_id: str,
        amount: int = 0,
    ) -> Dict[str, Any]:
        """Tell Plaza this provider started a request its caller paid for through a payment scope.

        A provider may price its work in its quotes rather than on the practice, so the practice names no price. Its
        caller still reserves the quoted points and hands the provider a scope. Verifying that scope marks the work
        started, so when the caller captures its escrow Plaza pays the provider's registered owner, and when it refunds
        Plaza pays no one. The provider leaves such a scope for the caller's capture to settle, unless its billing
        config refuses settlement on capture.

        With `amount` (the quoted price), the scope must cover it. A request with no confirmed scope runs unpaid only
        when its quote is 0 or the caller is the provider's own owner; the caller refuses anything else.
        """
        billing_config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
        if not billing_config.get("enabled", True) or not billing_config.get("use_scope_when_present", True):
            return {}
        try:
            context = await self._verify_remote_practice_payment_scope(
                plaza_url=plaza_url,
                request=request,
                practice_id=practice_id,
                amount=amount,
                request_id=request_id,
                quoted=True,
            )
        except PaymentScopeExecuted:
            raise  # Its paid work already ran: never run it again, unpaid or not.
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else exc
            self.logger.warning(f"Payment scope for '{practice_id}' was not confirmed; running it unpaid: {detail}")
            return {}
        if not context:
            return {}
        return {**context, "settle_on_capture": billing_config.get("allow_settlement_on_capture", True)}

    def _generic_payment_adapter(self):
        """Lazy opt-in helper; the owning provider supplies issuance and ACL checks."""
        config = self.remote_practice_billing if isinstance(self.remote_practice_billing, dict) else {}
        if not config.get("enabled", True) or config.get("scope_protocol") != "agent-payment/1":
            raise HTTPException(501, "Generic provider payments are not enabled.")
        cached = getattr(self, "_provider_payment_adapter", None)
        if cached is not None:
            return cached
        path = config.get("provider_intent_path") or os.environ.get("PROMPITS_PROVIDER_PAYMENT_INTENTS_PATH")
        if not path:
            raise HTTPException(503, "Configure durable provider payment intent storage.")
        from prompits.core.provider_payments import ProviderPaymentAdapter
        self._provider_payment_adapter = ProviderPaymentAdapter(self, path, getattr(self, "remote_practice_payment_authorizer", None))
        return self._provider_payment_adapter

    async def _complete_remote_practice_payment_scope(
        self,
        *,
        billing_context: Dict[str, Any],
        practice_id: str,
        request_id: str,
        result: Any = None,
    ) -> Dict[str, Any]:
        """Complete a verified Plaza payment scope after successful execution."""
        if not billing_context:
            return {}
        if billing_context.get("protocol") == "agent-payment/1":
            return await self._generic_payment_adapter().complete(billing_context, result)
        scope_id = str(billing_context.get("scope_id") or "").strip()
        scope_token = str(billing_context.get("scope_token") or "").strip()
        plaza_url = self._normalize_url(billing_context.get("plaza_url") or self.plaza_url)
        if not scope_id or not scope_token:
            return {}
        payload = {
            "scope_token": scope_token,
            "request_id": request_id,
            "metadata": {
                "practice_id": practice_id,
                "agent_id": str(self.agent_id or ""),
                "agent_name": str(self.name or ""),
            },
        }
        response = await self._plaza_request_async(
            "post",
            f"/api/payment-scopes/{scope_id}/complete",
            plaza_url=plaza_url,
            json=payload,
        )
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                detail = data.get("detail") or data.get("message") or detail
            except Exception:
                pass
            raise HTTPException(status_code=response.status_code, detail=detail)
        return response.json() if response.content else {}

    async def _verify_plaza_user_token(
        self,
        *,
        caller_user_token: str,
        preferred_plaza_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify a Plaza UI user token against trusted Plazas."""
        token = str(caller_user_token or "").strip()
        from prompits.core.call_grants import PREFIX, verified_context
        if token.startswith(PREFIX): return verified_context()
        if not token:
            return {}

        plazas = self._resolve_trusted_plaza_urls()
        preferred = self._normalize_url(preferred_plaza_url)
        if preferred and preferred in plazas:
            plazas = [preferred] + [plaza for plaza in plazas if plaza != preferred]

        if not plazas:
            raise HTTPException(status_code=401, detail="Trusted Plaza verification is not configured")

        def _verified_user_payload(auth_payload: Dict[str, Any], *, plaza_url: str, auth_mode: str) -> Dict[str, Any]:
            user = auth_payload.get("user") if isinstance(auth_payload.get("user"), dict) else {}
            user_id = str(auth_payload.get("user_id") or user.get("id") or "").strip()
            return {
                "user_id": user_id,
                "username": str(auth_payload.get("username") or user.get("username") or "").strip(),
                "email": str(auth_payload.get("email") or user.get("email") or "").strip().lower(),
                "display_name": str(auth_payload.get("display_name") or user.get("display_name") or "").strip(),
                "user_role": str(auth_payload.get("role") or user.get("role") or "").strip().lower(),
                "user_auth_mode": auth_mode,
                "user_plaza_url": plaza_url,
                "user": user or {
                    "id": user_id,
                    "username": str(auth_payload.get("username") or "").strip(),
                    "email": str(auth_payload.get("email") or "").strip().lower(),
                    "role": str(auth_payload.get("role") or "").strip().lower(),
                },
            }

        configured_ui_agent_keys = set(self._configured_ui_agent_keys())
        for plaza_url in plazas:
            cached = self._get_cached_plaza_verification(
                "_plaza_user_verification_cache",
                scope="user",
                plaza_url=plaza_url,
                token=token,
                ttl_seconds=self.PLAZA_USER_VERIFICATION_CACHE_TTL,
            )
            if cached and not token.startswith('pc-access-'):
                # Delegated client grants can be revoked independently of the
                # user's browser session; check Plaza for every such call.
                return cached

            attempt_order = ["ui_user", "ui_agent_key"]
            if token in configured_ui_agent_keys:
                attempt_order = ["ui_agent_key", "ui_user"]

            for attempt in attempt_order:
                if attempt == "ui_user":
                    try:
                        response = await self._plaza_request_async(
                            "post",
                            "/authenticate",
                            plaza_url=plaza_url,
                            headers={"Authorization": f"Bearer {token}"},
                        )
                    except Exception as exc:
                        logger.debug(
                            f"[{self.name}] Failed Plaza UI user verification against '{plaza_url}': {exc}"
                        )
                        continue

                    if response.status_code != 200:
                        logger.debug(
                            f"[{self.name}] Plaza '{plaza_url}' rejected UI user token with status {response.status_code}"
                        )
                        continue

                    auth_payload = response.json() if response.content else {}
                    subject_type = str(auth_payload.get("subject_type") or "").strip().lower()
                    auth_mode = str(auth_payload.get("auth_mode") or "").strip().lower()
                    if subject_type != "user" or auth_mode != "ui_user":
                        continue

                    verified_user = _verified_user_payload(
                        auth_payload,
                        plaza_url=plaza_url,
                        auth_mode="plaza_ui",
                    )
                    if verified_user.get("user_id"):
                        self._set_cached_plaza_verification(
                            "_plaza_user_verification_cache",
                            scope="user",
                            plaza_url=plaza_url,
                            token=token,
                            payload=verified_user,
                        )
                        return verified_user
                    continue

                try:
                    response = await self._plaza_request_async(
                        "post",
                        "/api/ui_agent_keys/authenticate",
                        plaza_url=plaza_url,
                        json={"secret": token},
                    )
                except Exception as exc:
                    logger.debug(
                        f"[{self.name}] Failed Plaza owner-key verification against '{plaza_url}': {exc}"
                    )
                    continue

                if response.status_code != 200:
                    logger.debug(
                        f"[{self.name}] Plaza '{plaza_url}' rejected owner key with status {response.status_code}"
                    )
                    continue

                auth_payload = response.json() if response.content else {}
                subject_type = str(auth_payload.get("subject_type") or "").strip().lower()
                auth_mode = str(auth_payload.get("auth_mode") or "").strip().lower()
                if subject_type != "user" or auth_mode != "ui_agent_key":
                    continue

                verified_user = _verified_user_payload(
                    auth_payload,
                    plaza_url=plaza_url,
                    auth_mode="ui_agent_key",
                )
                if verified_user.get("user_id"):
                    self._set_cached_plaza_verification(
                        "_plaza_user_verification_cache",
                        scope="user",
                        plaza_url=plaza_url,
                        token=token,
                        payload=verified_user,
                    )
                    return verified_user

        raise HTTPException(status_code=401, detail="Caller Plaza user verification failed")

    async def _verify_remote_caller(
        self,
        caller_agent_address: Any,
        caller_plaza_token: Optional[str],
        caller_direct_token: Optional[str] = None,
        caller_user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal helper for verify remote caller."""
        from prompits.core.call_grants import PREFIX, verified_context
        if str(caller_plaza_token or '').startswith(PREFIX):
            context=verified_context()
            if str((caller_agent_address or {}).get('pit_id')) != context['agent_id']:
                raise HTTPException(403, 'Scoped caller identity mismatch.')
            return context
        caller_pit_address = self._coerce_pit_address(caller_agent_address)
        if not caller_pit_address or not caller_pit_address.pit_id:
            logger.warning(f"[{self.name}] Rejecting remote UsePractice request without caller PitAddress.")
            raise HTTPException(status_code=401, detail="Caller agent address is required")

        verified: Optional[Dict[str, Any]] = None

        if self.direct_auth_token and caller_direct_token == self.direct_auth_token:
            logger.debug(
                f"[{self.name}] Verified remote caller '{caller_pit_address.pit_id}' "
                "through direct shared token."
            )
            verified = {
                "agent_id": str(caller_pit_address.pit_id),
                "agent_name": None,
                "plaza_url": None,
                "auth_mode": "direct",
            }

        if verified is None and caller_plaza_token:
            plazas = self._resolve_trusted_plaza_urls()

            for plaza_url in plazas:
                cached = self._get_cached_plaza_verification(
                    "_plaza_agent_verification_cache",
                    scope="agent",
                    plaza_url=plaza_url,
                    token=caller_plaza_token,
                    subject_id=str(caller_pit_address.pit_id),
                    ttl_seconds=self.PLAZA_AGENT_VERIFICATION_CACHE_TTL,
                )
                if cached:
                    verified = cached
                    break
                try:
                    response = await self._plaza_request_async(
                        "post",
                        "/authenticate",
                        plaza_url=plaza_url,
                        headers={"Authorization": f"Bearer {caller_plaza_token}"},
                    )
                except Exception as exc:
                    logger.debug(
                        f"[{self.name}] Failed caller verification against Plaza '{plaza_url}' "
                        f"for '{caller_pit_address.pit_id}': {exc}"
                    )
                    continue

                if response.status_code != 200:
                    logger.debug(
                        f"[{self.name}] Plaza '{plaza_url}' rejected caller "
                        f"'{caller_pit_address.pit_id}' with status {response.status_code}"
                    )
                    continue

                auth_payload = response.json() if response.content else {}
                subject_type = str(auth_payload.get("subject_type") or "").strip().lower()
                if subject_type and subject_type != "agent":
                    continue
                authenticated_agent_id = str(auth_payload.get("agent_id") or "")
                if authenticated_agent_id != str(caller_pit_address.pit_id):
                    logger.warning(
                        f"[{self.name}] Caller verification mismatch: Plaza '{plaza_url}' authenticated "
                        f"'{authenticated_agent_id}' but request claimed '{caller_pit_address.pit_id}'."
                    )
                    continue

                logger.debug(
                    f"[{self.name}] Verified remote caller '{caller_pit_address.pit_id}' through Plaza '{plaza_url}'."
                )
                verified = {
                    "agent_id": authenticated_agent_id,
                    "agent_name": auth_payload.get("agent_name"),
                    "plaza_url": plaza_url,
                    "auth_mode": "plaza",
                }
                self._set_cached_plaza_verification(
                    "_plaza_agent_verification_cache",
                    scope="agent",
                    plaza_url=plaza_url,
                    token=caller_plaza_token,
                    subject_id=authenticated_agent_id,
                    payload=verified,
                )
                break

            if not plazas:
                logger.warning(
                    f"[{self.name}] Rejecting remote UsePractice request from '{caller_pit_address.pit_id}' "
                    "because no trusted Plaza URLs are configured for token verification."
                )
                raise HTTPException(status_code=401, detail="Trusted Plaza verification is not configured")

        if verified is None and self.direct_auth_token:
            logger.warning(
                f"[{self.name}] Rejecting remote UsePractice request from '{caller_pit_address.pit_id}' "
                "because direct shared token verification failed."
            )
            raise HTTPException(status_code=401, detail="Caller direct token is invalid")

        if verified is None and not caller_plaza_token:
            logger.warning(
                f"[{self.name}] Rejecting remote UsePractice request from '{caller_pit_address.pit_id}' "
                "without a Plaza token."
            )
            raise HTTPException(status_code=401, detail="Caller plaza token is required")

        if verified is None:
            logger.warning(
                f"[{self.name}] Rejecting remote UsePractice request because caller "
                f"'{caller_pit_address.pit_id}' failed Plaza verification."
            )
            raise HTTPException(status_code=401, detail="Caller verification failed")

        if verified.get("auth_mode") == "plaza":
            # A Plaza agent token proves identity only. Another owner's agent,
            # including one the Plaza owner approved, calls through a call grant.
            await self._require_same_owner_caller(verified, str(caller_plaza_token or ""))

        if caller_user_token:
            user_verified = await self._verify_plaza_user_token(
                caller_user_token=caller_user_token,
                preferred_plaza_url=verified.get("plaza_url"),
            )
            verified.update(user_verified)

        return verified

    async def _require_same_owner_caller(self, verified: Dict[str, Any], caller_plaza_token: str) -> None:
        """Accept a raw Plaza agent token only when Plaza confirms the caller shares this agent's owner."""
        caller, me = str(verified.get("agent_id") or ""), str(getattr(self, "agent_id", "") or "")
        plaza_url = str(verified.get("plaza_url") or "")
        if not isinstance(getattr(self, "_plaza_owner_peer_cache", None), dict):
            self._plaza_owner_peer_cache = {}
        if self._get_cached_plaza_verification("_plaza_owner_peer_cache", scope="owner-peer", plaza_url=plaza_url,
                                               token=caller_plaza_token, subject_id=caller,
                                               ttl_seconds=self.PLAZA_AGENT_VERIFICATION_CACHE_TTL):
            return
        accepted = False
        if caller and me:
            try:
                response = await self._plaza_request_async(
                    "get", "/api/agent-peer-connection", plaza_url=plaza_url or None, params={"peer": me},
                    headers={"Authorization": f"Bearer {caller_plaza_token}"})
                value = response.json() if response.status_code == 200 and response.content else {}
                accepted = (value.get("schema") == "owner-peer-connection/1" and value.get("actor") == caller
                            and value.get("peer") == me and value.get("accepted") is True
                            and value.get("reason") == "same_registered_owner")
            except Exception as exc:
                logger.debug(f"[{self.name}] Owner check for caller '{caller}' failed: {exc}")
        if not accepted:
            raise HTTPException(status_code=403, detail="Another owner's agent must call through a Plaza call grant.")
        self._set_cached_plaza_verification("_plaza_owner_peer_cache", scope="owner-peer", plaza_url=plaza_url,
                                            token=caller_plaza_token, subject_id=caller, payload={"accepted": True})

    def _invoke_remote_practice_sync(self, practice_id: str, content: Any, pit_address: Any, timeout: int = 240) -> Any:
        """Invoke another agent's practice synchronously via HTTP POST."""
        from prompits.core.observability import trace_headers
        target = self._resolve_remote_target(pit_address)
        if not target:
            raise ValueError(f"Unable to resolve remote target from pit_address: {pit_address}")
        payload = self._build_remote_practice_payload(practice_id=practice_id, content=content, target=target)
        request_id = str(payload.get("request_id") or uuid.uuid4())
        outbound_context = self._build_outbound_policy_context(practice_id=practice_id, target=target)
        policy_decision = self._evaluate_remote_use_practice_policy(
            direction="outbound",
            practice_id=practice_id,
            context=outbound_context,
        )
        self._record_remote_practice_audit(
            request_id=request_id,
            direction="outbound",
            event="request",
            practice_id=practice_id,
            peer_agent_id=outbound_context.get("target_agent_id", ""),
            peer_name=outbound_context.get("target_name", ""),
            peer_address=outbound_context.get("target_address", ""),
            plaza_url=outbound_context.get("plaza_url", ""),
            policy_allowed=policy_decision.get("allowed"),
            policy_reason=policy_decision.get("reason", ""),
            outcome="allowed" if policy_decision.get("allowed") else "denied",
            status_code=200 if policy_decision.get("allowed") else 403,
            metadata={"matched_rule": policy_decision.get("matched_rule"), "policy_mode": policy_decision.get("mode")},
        )
        if not policy_decision.get("allowed"):
            raise HTTPException(status_code=403, detail=policy_decision.get("reason") or "Outbound remote practice denied by policy")
        remote_protocol = self._assert_remote_protocol_compatible(target=target)
        logger.debug(
            f"[{self.name}] Invoking remote practice '{practice_id}' on '{target['agent_id']}' "
            f"via {target['url']}/use_practice/{practice_id}"
        )
        try:
            response = requests.post(f"{target['url']}/use_practice/{practice_id}", json=payload, timeout=timeout, headers=trace_headers())
        except Exception as exc:
            self._record_remote_practice_audit(
                request_id=request_id,
                direction="outbound",
                event="result",
                practice_id=practice_id,
                peer_agent_id=outbound_context.get("target_agent_id", ""),
                peer_name=outbound_context.get("target_name", ""),
                peer_address=outbound_context.get("target_address", ""),
                plaza_url=outbound_context.get("plaza_url", ""),
                policy_allowed=True,
                policy_reason=policy_decision.get("reason", ""),
                outcome="failed",
                error=str(exc),
                metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
            )
            raise
        if response.status_code != 200:
            self._record_remote_practice_audit(
                request_id=request_id,
                direction="outbound",
                event="result",
                practice_id=practice_id,
                peer_agent_id=outbound_context.get("target_agent_id", ""),
                peer_name=outbound_context.get("target_name", ""),
                peer_address=outbound_context.get("target_address", ""),
                plaza_url=outbound_context.get("plaza_url", ""),
                policy_allowed=True,
                policy_reason=policy_decision.get("reason", ""),
                outcome="failed",
                status_code=response.status_code,
                error=response.text,
                metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
            )
            raise HTTPException(status_code=response.status_code, detail=response.text)
        self._record_remote_practice_audit(
            request_id=request_id,
            direction="outbound",
            event="result",
            practice_id=practice_id,
            peer_agent_id=outbound_context.get("target_agent_id", ""),
            peer_name=outbound_context.get("target_name", ""),
            peer_address=outbound_context.get("target_address", ""),
            plaza_url=outbound_context.get("plaza_url", ""),
            policy_allowed=True,
            policy_reason=policy_decision.get("reason", ""),
            outcome="succeeded",
            status_code=response.status_code,
            metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
        )
        try:
            return self._unwrap_remote_practice_response(response.json())
        except Exception:
            return response.text

    async def _invoke_remote_practice_async(self, practice_id: str, content: Any, pit_address: Any, timeout: int = 240) -> Any:
        """Invoke another agent's practice asynchronously via `httpx`."""
        import httpx
        from prompits.core.observability import trace_headers

        target = await run_in_threadpool(self._resolve_remote_target, pit_address)
        if not target:
            raise ValueError(f"Unable to resolve remote target from pit_address: {pit_address}")
        payload = await run_in_threadpool(
            self._build_remote_practice_payload,
            practice_id=practice_id,
            content=content,
            target=target,
        )
        request_id = str(payload.get("request_id") or uuid.uuid4())
        outbound_context = self._build_outbound_policy_context(practice_id=practice_id, target=target)
        policy_decision = self._evaluate_remote_use_practice_policy(
            direction="outbound",
            practice_id=practice_id,
            context=outbound_context,
        )
        self._record_remote_practice_audit(
            request_id=request_id,
            direction="outbound",
            event="request",
            practice_id=practice_id,
            peer_agent_id=outbound_context.get("target_agent_id", ""),
            peer_name=outbound_context.get("target_name", ""),
            peer_address=outbound_context.get("target_address", ""),
            plaza_url=outbound_context.get("plaza_url", ""),
            policy_allowed=policy_decision.get("allowed"),
            policy_reason=policy_decision.get("reason", ""),
            outcome="allowed" if policy_decision.get("allowed") else "denied",
            status_code=200 if policy_decision.get("allowed") else 403,
            metadata={"matched_rule": policy_decision.get("matched_rule"), "policy_mode": policy_decision.get("mode")},
        )
        if not policy_decision.get("allowed"):
            raise HTTPException(status_code=403, detail=policy_decision.get("reason") or "Outbound remote practice denied by policy")
        remote_protocol = await run_in_threadpool(self._assert_remote_protocol_compatible, target=target)
        logger.debug(
            f"[{self.name}] Invoking remote async practice '{practice_id}' on '{target['agent_id']}' "
            f"via {target['url']}/use_practice/{practice_id}"
        )
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{target['url']}/use_practice/{practice_id}", json=payload, timeout=timeout, headers=trace_headers())
            except Exception as exc:
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="outbound",
                    event="result",
                    practice_id=practice_id,
                    peer_agent_id=outbound_context.get("target_agent_id", ""),
                    peer_name=outbound_context.get("target_name", ""),
                    peer_address=outbound_context.get("target_address", ""),
                    plaza_url=outbound_context.get("plaza_url", ""),
                    policy_allowed=True,
                    policy_reason=policy_decision.get("reason", ""),
                    outcome="failed",
                    error=str(exc),
                    metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
                )
                raise
            if response.status_code >= 400:
                self._record_remote_practice_audit(
                    request_id=request_id,
                    direction="outbound",
                    event="result",
                    practice_id=practice_id,
                    peer_agent_id=outbound_context.get("target_agent_id", ""),
                    peer_name=outbound_context.get("target_name", ""),
                    peer_address=outbound_context.get("target_address", ""),
                    plaza_url=outbound_context.get("plaza_url", ""),
                    policy_allowed=True,
                    policy_reason=policy_decision.get("reason", ""),
                    outcome="failed",
                    status_code=response.status_code,
                    error=response.text,
                    metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
                )
            response.raise_for_status()
            self._record_remote_practice_audit(
                request_id=request_id,
                direction="outbound",
                event="result",
                practice_id=practice_id,
                peer_agent_id=outbound_context.get("target_agent_id", ""),
                peer_name=outbound_context.get("target_name", ""),
                peer_address=outbound_context.get("target_address", ""),
                plaza_url=outbound_context.get("plaza_url", ""),
                policy_allowed=True,
                policy_reason=policy_decision.get("reason", ""),
                outcome="succeeded",
                status_code=response.status_code,
                metadata={"remote_protocol_version": remote_protocol.get("protocol_version")},
            )
            if not response.content:
                return {}
            return self._unwrap_remote_practice_response(response.json())

    async def UsePracticeAsync(
        self,
        practice_id: str,
        content: Any = None,
        pit_address: Any = None,
        timeout: int = 240
    ) -> Any:
        """Use the practice async."""
        local_practice = next((p for p in self.practices if p.id == practice_id), None)
        target_pit_address = self._coerce_pit_address(pit_address)
        if pit_address is None or (
            target_pit_address and str(target_pit_address.pit_id) == str(self.pit_address.pit_id)
        ):
            if not local_practice:
                raise ValueError(f"Local practice '{practice_id}' not found")
            return await self._execute_local_practice_async(local_practice, content)

        with self.observations.span(practice_id, kind="remote_call"):
            return await self._invoke_remote_practice_async(
                practice_id=practice_id, content=content, pit_address=pit_address, timeout=timeout
            )

    def UsePractice(
        self,
        practice_id: str,
        content: Any = None,
        pit_address: Any = None,
        async_mode: bool = False,
        timeout: int = 240
    ) -> Any:
        """Use the practice."""
        if async_mode:
            return self.UsePracticeAsync(
                practice_id=practice_id,
                content=content,
                pit_address=pit_address,
                timeout=timeout
            )

        local_practice = next((p for p in self.practices if p.id == practice_id), None)
        target_pit_address = self._coerce_pit_address(pit_address)
        if pit_address is None or (
            target_pit_address and str(target_pit_address.pit_id) == str(self.pit_address.pit_id)
        ):
            if not local_practice:
                raise ValueError(f"Local practice '{practice_id}' not found")
            return self._execute_local_practice_sync(local_practice, content)

        with self.observations.span(practice_id, kind="remote_call"):
            return self._invoke_remote_practice_sync(
                practice_id=practice_id, content=content, pit_address=pit_address, timeout=timeout
            )

    @abstractmethod
    def receive(self, message: Message):
        """Handle incoming messages."""
        pass

    @abstractmethod
    def run(self):
        """Legacy run method, now we use uvicorn to run app."""
        pass
