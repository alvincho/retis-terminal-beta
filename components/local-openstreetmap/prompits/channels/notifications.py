"""
Notification-only delivery adapters for Plaza.

This module intentionally treats email, SMS, IM, and webhook channels as wake-up
lanes. Payloads and results stay in Plaza; providers receive a small, scrubbed
notification that tells the agent or operator which exchange to fetch.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import json
import logging
import os
import re
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import unquote_to_bytes, urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


logger = logging.getLogger(__name__)

NOTIFICATION_PROVIDER_KINDS = (
    "email",
    "email_aws_ses",
    "sms",
    "sms_aws_sns",
    "webhook",
    "generic_webhook",
    "slack",
    "telegram",
    "discord",
)

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "body",
    "content",
    "credential",
    "credentials",
    "data",
    "file",
    "files",
    "input",
    "message_payload",
    "password",
    "payload",
    "raw",
    "result",
    "secret",
    "token",
}

_SAFE_EVENT_KEYS = {
    "event_type",
    "exchange_id",
    "request_id",
    "practice_id",
    "practice_name",
    "sender",
    "sender_id",
    "sender_type",
    "caller",
    "caller_id",
    "receiver",
    "receiver_id",
    "title",
    "subject",
    "summary",
    "message",
    "message_style",
    "render_mode",
    "severity",
    "status",
    "subject_prefix",
    "created_at",
    "expires_at",
    "plaza_url",
    "site_origin",
    "throttle_key",
}

_MAX_NOTIFICATION_ATTACHMENTS = 5
_MAX_NOTIFICATION_ATTACHMENT_CHARS = 2_000_000
_MAX_NOTIFICATION_ATTACHMENT_BYTES = 18_000_000

_DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


@dataclass
class NotificationBucket:
    """In-memory summary bucket for throttled or quiet-hours notifications."""

    key: str
    provider_id: str
    provider: dict[str, Any]
    route: dict[str, Any]
    receiver_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    first_at: float = 0.0
    last_at: float = 0.0
    due_at: float = 0.0
    reason: str = "throttled"


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        return list(value)
    return []


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_config_value(value: Any) -> Any:
    """Resolve simple environment variable placeholders used by agent configs."""
    if isinstance(value, Mapping):
        env_name = value.get("env") or value.get("name")
        env_names = value.get("envs")
        fallback = value.get("value", value.get("fallback"))
        if env_name:
            resolved = os.getenv(str(env_name))
            if resolved not in (None, ""):
                return resolved
            return _resolve_config_value(fallback)
        if isinstance(env_names, Sequence) and not isinstance(env_names, (str, bytes, bytearray, Mapping)):
            for candidate in env_names:
                resolved = os.getenv(str(candidate))
                if resolved not in (None, ""):
                    return resolved
            return _resolve_config_value(fallback)
        if "value" in value:
            return value.get("value")
        return copy.deepcopy(dict(value))

    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.startswith("env:"):
            return os.getenv(trimmed[4:].strip())
        if trimmed.startswith("${") and trimmed.endswith("}"):
            return os.getenv(trimmed[2:-1].strip())
    return value


def _resolved_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        if key in mapping:
            text = str(_resolve_config_value(mapping.get(key)) or "").strip()
            if text:
                return text
    return ""


def _resolved_list(mapping: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = _resolve_config_value(mapping.get(key))
        if isinstance(raw, str):
            values.extend(part.strip() for part in raw.split(",") if part.strip())
        else:
            values.extend(str(item).strip() for item in _coerce_list(raw) if str(item).strip())
    return values


def _resolved_optional_text(mapping: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        if key in mapping:
            return str(_resolve_config_value(mapping.get(key)) or "").strip()
    return None


def _direct_render_mode(event: Mapping[str, Any]) -> str:
    return _first_text(event.get("render_mode"), event.get("message_style")).lower().replace("-", "_")


def _absolute_notification_url(value: str, base_url: str) -> str:
    text = value.strip()
    if not text or not base_url or not text.startswith("/"):
        return text
    return urljoin(f"{base_url.rstrip('/')}/", text)


def _absolute_direct_message_links(text: str, event: Mapping[str, Any]) -> str:
    base_url = _first_text(event.get("site_origin"))
    if not base_url:
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping):
            base_url = _first_text(metadata.get("site_origin"), metadata.get("origin"))
    if not base_url:
        return text

    lines: list[str] = []
    for line in str(text or "").splitlines():
        match = re.match(r"^([^:\n]{1,80}:\s*)(/[^\s<>]+)(.*)$", line)
        if not match:
            lines.append(line)
            continue
        label = match.group(1).lower()
        if not any(keyword in label for keyword in ("url", "link", "media", "preview")):
            lines.append(line)
            continue
        lines.append(f"{match.group(1)}{_absolute_notification_url(match.group(2), base_url)}{match.group(3)}")
    return "\n".join(lines)


def _allows_direct_attachments(event: Mapping[str, Any]) -> bool:
    return _direct_render_mode(event) in {"direct", "message", "message_only"}


def _attachment_items(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return [value]
    return _coerce_list(value)


def _attachment_filename(value: Any, index: int) -> str:
    filename = _first_text(value, f"attachment-{index}.txt")
    filename = os.path.basename(filename.replace("\x00", "")).strip()
    return filename or f"attachment-{index}.txt"


def _attachment_content(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Mapping) or isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value or "")


def _data_url_parts(value: Any) -> tuple[str, str]:
    data_url = _first_text(value)
    if not data_url.lower().startswith("data:") or "," not in data_url:
        return "", ""
    header, payload = data_url.split(",", 1)
    content_type = header[5:].split(";", 1)[0].strip().lower()
    return content_type, payload


def _decode_base64_payload(value: Any) -> bytes:
    encoded = _first_text(value).strip()
    if not encoded:
        return b""
    try:
        return base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _attachment_binary_content(item: Mapping[str, Any]) -> tuple[bytes, str]:
    data_url_type, data_url_payload = _data_url_parts(item.get("data_url") or item.get("dataUrl"))
    if data_url_payload:
        if ";base64" in _first_text(item.get("data_url") or item.get("dataUrl")).split(",", 1)[0].lower():
            return _decode_base64_payload(data_url_payload), data_url_type
        return unquote_to_bytes(data_url_payload), data_url_type

    for key in ("content_base64", "contentBase64", "base64", "data_base64", "dataBase64"):
        decoded = _decode_base64_payload(item.get(key))
        if decoded:
            return decoded, ""
    return b"", ""


def _attachment_content_type(value: Any) -> str:
    content_type = _first_text(value, "text/plain").split(";", 1)[0].strip().lower()
    if "/" not in content_type:
        return "text/plain"
    maintype, subtype = content_type.split("/", 1)
    maintype = "".join(ch for ch in maintype if ch.isalnum() or ch in {"-", "+", "."})
    subtype = "".join(ch for ch in subtype if ch.isalnum() or ch in {"-", "+", "."})
    if not maintype or not subtype:
        return "text/plain"
    return f"{maintype}/{subtype}"


def _notification_attachments(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for index, item in enumerate(_attachment_items(event.get("attachments")), start=1):
        if not isinstance(item, Mapping):
            continue
        binary_content, data_url_content_type = _attachment_binary_content(item)
        if binary_content:
            if len(binary_content) > _MAX_NOTIFICATION_ATTACHMENT_BYTES:
                continue
            attachments.append(
                {
                    "filename": _attachment_filename(item.get("filename") or item.get("name"), index),
                    "content": binary_content,
                    "content_type": _attachment_content_type(
                        item.get("content_type") or item.get("mime_type") or data_url_content_type
                    ),
                }
            )
            if len(attachments) >= _MAX_NOTIFICATION_ATTACHMENTS:
                break
            continue

        raw_content = item.get("content")
        if raw_content is None:
            raw_content = item.get("text")
        if raw_content is None:
            raw_content = item.get("body")
        if isinstance(raw_content, (bytes, bytearray)):
            content_bytes = bytes(raw_content)
            if len(content_bytes) > _MAX_NOTIFICATION_ATTACHMENT_BYTES:
                continue
            attachments.append(
                {
                    "filename": _attachment_filename(item.get("filename") or item.get("name"), index),
                    "content": content_bytes,
                    "content_type": _attachment_content_type(item.get("content_type") or item.get("mime_type")),
                }
            )
            if len(attachments) >= _MAX_NOTIFICATION_ATTACHMENTS:
                break
            continue
        content = _attachment_content(raw_content).strip()
        if not content:
            continue
        if len(content) > _MAX_NOTIFICATION_ATTACHMENT_CHARS:
            content = content[:_MAX_NOTIFICATION_ATTACHMENT_CHARS]
        attachments.append(
            {
                "filename": _attachment_filename(item.get("filename") or item.get("name"), index),
                "content": content,
                "content_type": _attachment_content_type(item.get("content_type") or item.get("mime_type")),
            }
        )
        if len(attachments) >= _MAX_NOTIFICATION_ATTACHMENTS:
            break
    return attachments


def _mime_parts(content_type: str) -> tuple[str, str]:
    content_type = _attachment_content_type(content_type)
    maintype, subtype = content_type.split("/", 1)
    return maintype or "text", subtype or "plain"


def _provider_kind(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "aws_ses": "email_aws_ses",
        "aws_ses_email": "email_aws_ses",
        "email_aws_ses": "email_aws_ses",
        "email_ses": "email_aws_ses",
        "generic": "generic_webhook",
        "http": "generic_webhook",
        "http_webhook": "generic_webhook",
        "ses": "email_aws_ses",
        "sesv2": "email_aws_ses",
        "webhook": "generic_webhook",
        "sms_generic": "sms",
        "sms_webhook": "sms",
        "twilio": "sms_twilio",
        "twilio_sms": "sms_twilio",
        "aws_sns": "sms_aws_sns",
    }
    if raw in aliases:
        return aliases[raw]
    if "slack" in raw:
        return "slack"
    if "telegram" in raw:
        return "telegram"
    if "discord" in raw:
        return "discord"
    if "email" in raw or "mail" in raw:
        return "email"
    if "sms" in raw:
        return "sms"
    if "webhook" in raw:
        return "generic_webhook"
    return raw


def normalize_notification_providers(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize provider config from dict or list forms."""
    providers: dict[str, dict[str, Any]] = {}
    if isinstance(value, Mapping):
        iterable = []
        for provider_id, provider_config in value.items():
            provider = _coerce_dict(provider_config)
            provider.setdefault("id", str(provider_id))
            iterable.append(provider)
    else:
        iterable = [_coerce_dict(item) for item in _coerce_list(value)]

    for provider in iterable:
        provider_id = _first_text(provider.get("id"), provider.get("name"), provider.get("provider"))
        if not provider_id:
            continue
        provider_type = _provider_kind(
            provider.get("type")
            or provider.get("kind")
            or provider.get("channel")
            or provider.get("provider_type")
            or provider_id
        )
        if not provider_type:
            continue
        normalized = copy.deepcopy(provider)
        normalized["id"] = provider_id
        normalized["type"] = provider_type
        normalized["enabled"] = _coerce_bool(provider.get("enabled"), default=True)
        providers[provider_id] = normalized
    return providers


def extract_notification_config(config: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Extract provider catalog and defaults from Plaza-style config sections."""
    providers: dict[str, dict[str, Any]] = {}
    settings: dict[str, Any] = {}
    candidate_sections: list[Mapping[str, Any]] = []
    root = _coerce_dict(config)
    if root:
        candidate_sections.append(root)
    properties = _coerce_dict(root.get("properties"))
    if properties:
        candidate_sections.append(properties)
        plaza_props = _coerce_dict(properties.get("plaza"))
        if plaza_props:
            candidate_sections.append(plaza_props)
    for key in ("plaza", "plaza_properties", "notifications", "delivery"):
        section = _coerce_dict(root.get(key))
        if section:
            candidate_sections.append(section)

    for section in candidate_sections:
        for provider_key in ("notification_providers", "delivery_providers", "providers"):
            section_providers = normalize_notification_providers(section.get(provider_key))
            providers.update(section_providers)
        for settings_key in ("notification_settings", "settings", "notification_defaults"):
            section_settings = _coerce_dict(section.get(settings_key))
            if section_settings:
                settings.update(copy.deepcopy(section_settings))
        if "summary_every_minutes" in section or "no_bother" in section or "quiet_hours" in section:
            for key in ("summary_every_minutes", "no_bother", "quiet_hours", "do_not_disturb"):
                if key in section:
                    settings[key] = copy.deepcopy(section[key])

    return providers, settings


def scrub_notification_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a payload-safe notification event without raw practice content."""
    safe: dict[str, Any] = {}
    for key, value in event.items():
        normalized_key = str(key).strip()
        if normalized_key.lower() in _SENSITIVE_KEYS:
            continue
        if normalized_key in _SAFE_EVENT_KEYS:
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[normalized_key] = value
            elif isinstance(value, (list, tuple)):
                safe[normalized_key] = [str(item) for item in value[:10]]
            else:
                safe[normalized_key] = str(value)
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping):
        safe_metadata = _scrub_metadata(metadata, depth=2)
        if safe_metadata:
            safe["metadata"] = safe_metadata
    if _allows_direct_attachments(event):
        attachments = _notification_attachments(event)
        if attachments:
            safe["attachments"] = attachments
    return safe


def store_forward_summary_from_card(card: Any) -> dict[str, Any]:
    """Return normalized store-forward capability metadata from an agent card."""
    card_mapping = _coerce_dict(card)
    meta = _coerce_dict(card_mapping.get("meta"))
    delivery = _coerce_dict(card_mapping.get("delivery") or meta.get("delivery"))
    candidates = [
        ("card.store_forward", card_mapping.get("store_forward")),
        ("meta.store_forward", meta.get("store_forward")),
        ("delivery.store_forward", delivery.get("store_forward")),
    ]
    explicit_enabled = None
    configured = False
    raw_config: dict[str, Any] = {}

    for source, value in candidates:
        if value is None:
            continue
        configured = True
        if isinstance(value, Mapping):
            raw_config = copy.deepcopy(dict(value))
            if "enabled" in value:
                explicit_enabled = _coerce_bool(value.get("enabled"), default=False)
            elif "poll_enabled" in value:
                explicit_enabled = _coerce_bool(value.get("poll_enabled"), default=False)
            else:
                explicit_enabled = True
            raw_config.setdefault("source", source)
            break
        explicit_enabled = _coerce_bool(value, default=False)
        raw_config = {"enabled": explicit_enabled, "source": source}
        break

    configured_hint = None
    for source in (card_mapping, meta, delivery):
        if "store_forward_configured" in source:
            configured_hint = _coerce_bool(source.get("store_forward_configured"), default=False)
            configured = bool(configured or configured_hint)
            raw_config.setdefault("store_forward_configured", source.get("store_forward_configured"))
            break

    if explicit_enabled is None:
        for key in ("store_forward_enabled", "accepts_store_forward", "accepts_store_forward_practice"):
            for source in (card_mapping, meta, delivery):
                if key not in source:
                    continue
                flag_enabled = _coerce_bool(source.get(key), default=False)
                if key != "store_forward_enabled" or flag_enabled or configured_hint:
                    configured = True
                    explicit_enabled = flag_enabled
                    raw_config.setdefault(key, source.get(key))
                    break
            if explicit_enabled is not None:
                break

    poll_enabled = _coerce_bool(raw_config.get("poll_enabled"), default=bool(explicit_enabled))
    poll_interval = int(max(_coerce_float(raw_config.get("poll_interval_sec") or raw_config.get("poll_interval_seconds"), default=30), 0))
    payload_transport = _first_text(raw_config.get("payload_transport"), delivery.get("payload_transport"), "plaza")
    notify_routes = delivery.get("notify") or delivery.get("notifications") or delivery.get("notification_routes")
    notify_route_count = len(_coerce_list(notify_routes))
    enabled = bool(explicit_enabled) if explicit_enabled is not None else False
    return {
        "configured": bool(configured),
        "enabled": enabled,
        "poll_enabled": poll_enabled,
        "poll_interval_sec": poll_interval,
        "payload_transport": payload_transport or "plaza",
        "notify_route_count": notify_route_count,
        "config": raw_config,
    }


def _scrub_metadata(value: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    if depth < 0:
        return {}
    safe: dict[str, Any] = {}
    for key, raw_value in value.items():
        key_text = str(key)
        if key_text.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(raw_value, Mapping):
            nested = _scrub_metadata(raw_value, depth=depth - 1)
            if nested:
                safe[key_text] = nested
        elif isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            safe[key_text] = raw_value
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray)):
            safe[key_text] = [str(item) for item in list(raw_value)[:10]]
        else:
            safe[key_text] = str(raw_value)
    return safe


def _parse_time_minutes(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _timezone_for(value: Any):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except ZoneInfoNotFoundError:
        return None


def _quiet_days(value: Any) -> Optional[set[int]]:
    entries = _coerce_list(value)
    if not entries and value not in (None, ""):
        entries = [value]
    days: set[int] = set()
    for entry in entries:
        if isinstance(entry, int) and 0 <= entry <= 6:
            days.add(entry)
            continue
        text = str(entry).strip().lower()
        if text in _DAY_ALIASES:
            days.add(_DAY_ALIASES[text])
    return days or None


class NotificationDispatcher:
    """Send or summarize notification-only provider messages."""

    def __init__(
        self,
        provider_configs: Any = None,
        settings: Optional[Mapping[str, Any]] = None,
        *,
        http_client_factory: Optional[Callable[[], Any]] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ):
        self.providers = normalize_notification_providers(provider_configs)
        self.settings = copy.deepcopy(dict(settings or {}))
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=10.0))
        self._now_fn = now_fn or time.time
        self._summary_buckets: dict[str, NotificationBucket] = {}

    def configure(
        self,
        *,
        provider_configs: Any = None,
        settings: Optional[Mapping[str, Any]] = None,
        merge: bool = True,
    ) -> None:
        """Apply provider/default settings from Plaza config."""
        if provider_configs is not None:
            providers = normalize_notification_providers(provider_configs)
            if merge:
                self.providers.update(providers)
            else:
                self.providers = providers
        if settings is not None:
            if merge:
                self.settings.update(copy.deepcopy(dict(settings)))
            else:
                self.settings = copy.deepcopy(dict(settings))

    def configure_from_config(self, config: Any, *, merge: bool = True) -> None:
        providers, settings = extract_notification_config(config)
        self.configure(provider_configs=providers, settings=settings, merge=merge)

    async def submit(
        self,
        event: Mapping[str, Any],
        *,
        receiver_card: Optional[Mapping[str, Any]] = None,
        routes: Any = None,
        requested_providers: Any = None,
        force: bool = False,
        allow_during_no_bother: bool = False,
    ) -> dict[str, Any]:
        """Send or queue notifications for one Plaza event."""
        safe_event = scrub_notification_event(event)
        event_type = str(safe_event.get("event_type") or event.get("event_type") or "notification")
        resolved_routes = self._resolve_routes(
            receiver_card=receiver_card,
            routes=routes,
            requested_providers=requested_providers,
            event_type=event_type,
        )
        if not resolved_routes:
            return {"status": "skipped", "reason": "no_notification_routes", "deliveries": []}

        deliveries: list[dict[str, Any]] = []
        for route in resolved_routes:
            provider_id = str(route.get("provider") or route.get("provider_id") or "").strip()
            provider = self.providers.get(provider_id)
            if not provider:
                deliveries.append(
                    {
                        "provider": provider_id,
                        "status": "failed",
                        "detail": "Notification provider is not configured.",
                    }
                )
                continue
            if not _coerce_bool(provider.get("enabled"), default=True):
                deliveries.append(
                    {
                        "provider": provider_id,
                        "type": provider.get("type"),
                        "status": "skipped",
                        "detail": "Notification provider is disabled.",
                    }
                )
                continue

            if self._is_quiet_now(route, provider) and not allow_during_no_bother:
                deliveries.append(self._queue_summary(provider, route, safe_event, reason="no_bother"))
                continue

            interval_seconds = self._summary_interval_seconds(route, provider)
            if interval_seconds > 0 and not force:
                deliveries.append(self._queue_summary(provider, route, safe_event, reason="throttled"))
                continue

            deliveries.append(await self._send(provider, route, safe_event))

        return {"status": self._overall_status(deliveries), "deliveries": deliveries}

    async def flush_due_summaries(self, *, force: bool = False) -> dict[str, Any]:
        """Send summary messages for throttled buckets whose window is due."""
        now = self._now_fn()
        due_keys: list[str] = []
        for key, bucket in list(self._summary_buckets.items()):
            if not bucket.events:
                due_keys.append(key)
                continue
            if not force and bucket.due_at > now:
                continue
            if not force and self._is_quiet_now(bucket.route, bucket.provider):
                continue
            due_keys.append(key)

        deliveries: list[dict[str, Any]] = []
        for key in due_keys:
            bucket = self._summary_buckets.pop(key, None)
            if not bucket or not bucket.events:
                continue
            summary_event = self._summary_event(bucket)
            deliveries.append(await self._send(bucket.provider, bucket.route, summary_event, bucket=bucket))

        return {"status": self._overall_status(deliveries), "deliveries": deliveries}

    def pending_summary_count(self) -> int:
        """Return the number of active summary buckets."""
        return len(self._summary_buckets)

    def _resolve_routes(
        self,
        *,
        receiver_card: Optional[Mapping[str, Any]],
        routes: Any,
        requested_providers: Any,
        event_type: str,
    ) -> list[dict[str, Any]]:
        explicit_routes = [_coerce_dict(route) for route in _coerce_list(routes)]
        provider_routes = self._routes_from_requested_providers(requested_providers)
        card_routes = self._routes_from_card(receiver_card, event_type=event_type)
        resolved = explicit_routes or provider_routes or card_routes
        return [route for route in resolved if _coerce_bool(route.get("enabled"), default=True)]

    def _routes_from_requested_providers(self, value: Any) -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        if isinstance(value, str):
            value = [value]
        for item in _coerce_list(value):
            if isinstance(item, Mapping):
                route = copy.deepcopy(dict(item))
                provider_id = _first_text(route.get("provider"), route.get("provider_id"), route.get("id"), route.get("name"))
                if provider_id:
                    route["provider"] = provider_id
                    routes.append(route)
                continue
            provider_id = str(item or "").strip()
            if provider_id:
                routes.append({"provider": provider_id})
        return routes

    def _routes_from_card(self, receiver_card: Optional[Mapping[str, Any]], *, event_type: str) -> list[dict[str, Any]]:
        card = _coerce_dict(receiver_card)
        meta = _coerce_dict(card.get("meta"))
        delivery = _coerce_dict(meta.get("delivery") or card.get("delivery"))
        defaults = _coerce_dict(delivery.get("notification_defaults") or delivery.get("defaults"))
        raw_routes = (
            delivery.get("notify")
            or delivery.get("notifications")
            or delivery.get("notification_routes")
            or meta.get("notify")
        )
        if isinstance(raw_routes, Mapping):
            raw_routes = raw_routes.get("routes") or raw_routes.get("providers") or []

        routes = []
        for route_value in _coerce_list(raw_routes):
            route = _coerce_dict(route_value)
            provider_id = _first_text(route.get("provider"), route.get("provider_id"), route.get("id"), route.get("name"))
            if not provider_id:
                continue
            if not self._event_matches(route.get("events") or route.get("event_types"), event_type):
                continue
            route = {**defaults, **route}
            route["provider"] = provider_id
            routes.append(route)
        return routes

    def _event_matches(self, configured_events: Any, event_type: str) -> bool:
        events = [str(item).strip() for item in _coerce_list(configured_events) if str(item).strip()]
        if not events:
            return True
        event_type = str(event_type or "").strip()
        for candidate in events:
            if candidate in {"*", "all"}:
                return True
            if candidate == event_type:
                return True
            if candidate.endswith(".*") and event_type.startswith(candidate[:-1]):
                return True
        return False

    def _summary_interval_seconds(self, route: Mapping[str, Any], provider: Mapping[str, Any]) -> float:
        throttle = _coerce_dict(route.get("throttle") or provider.get("throttle") or self.settings.get("throttle"))
        if throttle and _coerce_bool(throttle.get("enabled"), default=True) is False:
            return 0.0
        minutes = (
            route.get("summary_every_minutes")
            if "summary_every_minutes" in route
            else provider.get("summary_every_minutes")
            if "summary_every_minutes" in provider
            else throttle.get("summary_every_minutes")
            if "summary_every_minutes" in throttle
            else self.settings.get("summary_every_minutes")
        )
        return max(_coerce_float(minutes, default=0.0), 0.0) * 60.0

    def _quiet_configs(self, route: Mapping[str, Any], provider: Mapping[str, Any]) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []
        for source in (self.settings, provider, route):
            for key in ("no_bother", "quiet_hours", "do_not_disturb"):
                raw = source.get(key) if isinstance(source, Mapping) else None
                if isinstance(raw, Mapping):
                    configs.append(copy.deepcopy(dict(raw)))
                else:
                    configs.extend(copy.deepcopy(_coerce_dict(item)) for item in _coerce_list(raw))
        return [config for config in configs if _coerce_bool(config.get("enabled"), default=True)]

    def _is_quiet_now(self, route: Mapping[str, Any], provider: Mapping[str, Any]) -> bool:
        now_epoch = self._now_fn()
        for config in self._quiet_configs(route, provider):
            start = _parse_time_minutes(config.get("start") or config.get("from"))
            end = _parse_time_minutes(config.get("end") or config.get("to") or config.get("until"))
            if start is None or end is None or start == end:
                continue
            tz = _timezone_for(config.get("timezone") or config.get("tz"))
            local_now = datetime.fromtimestamp(now_epoch, tz=tz).astimezone(tz) if tz else datetime.fromtimestamp(now_epoch).astimezone()
            days = _quiet_days(config.get("days") or config.get("weekdays"))
            if days is not None and local_now.weekday() not in days:
                continue
            minute = local_now.hour * 60 + local_now.minute
            if start < end:
                if start <= minute < end:
                    return True
            elif minute >= start or minute < end:
                return True
        return False

    def _queue_summary(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        provider_id = str(provider.get("id") or route.get("provider") or "").strip()
        receiver_id = str(event.get("receiver_id") or event.get("receiver") or "").strip()
        key = _first_text(
            route.get("throttle_key"),
            route.get("summary_key"),
            event.get("throttle_key"),
            f"{receiver_id}:{provider_id}",
        )
        now = self._now_fn()
        interval_seconds = self._summary_interval_seconds(route, provider)
        due_at = now + interval_seconds if interval_seconds > 0 else now
        bucket = self._summary_buckets.get(key)
        if bucket is None:
            bucket = NotificationBucket(
                key=key,
                provider_id=provider_id,
                provider=copy.deepcopy(dict(provider)),
                route=copy.deepcopy(dict(route)),
                receiver_id=receiver_id,
                first_at=now,
                due_at=due_at,
                reason=reason,
            )
            self._summary_buckets[key] = bucket
        bucket.events.append(copy.deepcopy(dict(event)))
        bucket.last_at = now
        bucket.reason = reason if bucket.reason != "no_bother" else bucket.reason
        if reason == "no_bother" and bucket.due_at < now:
            bucket.due_at = now
        return {
            "provider": provider_id,
            "type": provider.get("type"),
            "status": "queued_summary",
            "reason": reason,
            "summary_key": key,
            "pending": len(bucket.events),
            "due_at": bucket.due_at,
        }

    async def _send(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        bucket: Optional[NotificationBucket] = None,
    ) -> dict[str, Any]:
        provider_type = _provider_kind(provider.get("type"))
        provider_id = str(provider.get("id") or route.get("provider") or provider_type).strip()
        text = self._render_text(event, bucket=bucket)
        try:
            if provider_type == "slack":
                return await self._send_slack(provider_id, provider, route, event, text)
            if provider_type == "telegram":
                return await self._send_telegram(provider_id, provider, route, event, text)
            if provider_type == "discord":
                return await self._send_discord(provider_id, provider, route, event, text)
            if provider_type in {"generic_webhook", "webhook"}:
                return await self._send_webhook(provider_id, provider, route, event, text)
            if provider_type == "email_aws_ses":
                return await self._send_aws_ses_email(provider_id, provider, route, event, text)
            if provider_type == "email":
                return await self._send_email(provider_id, provider, route, event, text)
            if provider_type.startswith("sms"):
                return await self._send_sms(provider_id, provider, route, event, text)
            return {
                "provider": provider_id,
                "type": provider_type,
                "status": "failed",
                "detail": f"Unsupported notification provider type '{provider_type}'.",
            }
        except Exception as exc:
            logger.warning("Notification provider '%s' failed: %s", provider_id, exc)
            return {
                "provider": provider_id,
                "type": provider_type,
                "status": "failed",
                "detail": str(exc),
            }

    async def _send_slack(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        url = _resolved_text(route, "webhook_url", "url") or _resolved_text(provider, "webhook_url", "url")
        if not url:
            return self._missing_target(provider_id, provider, "webhook_url")
        return await self._post_json(provider_id, provider, url, {"text": text[:4000]})

    async def _send_discord(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        url = _resolved_text(route, "webhook_url", "url") or _resolved_text(provider, "webhook_url", "url")
        if not url:
            return self._missing_target(provider_id, provider, "webhook_url")
        return await self._post_json(provider_id, provider, url, {"content": text[:1900]})

    async def _send_webhook(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        url = _resolved_text(route, "webhook_url", "url") or _resolved_text(provider, "webhook_url", "url")
        if not url:
            return self._missing_target(provider_id, provider, "url")
        notification = copy.deepcopy(dict(event))
        notification.pop("attachments", None)
        payload = {
            "text": text,
            "event_type": event.get("event_type"),
            "exchange_id": event.get("exchange_id"),
            "notification": notification,
        }
        headers = _coerce_dict(provider.get("headers"))
        route_headers = _coerce_dict(route.get("headers"))
        headers.update(route_headers)
        headers = {str(key): str(_resolve_config_value(value) or "") for key, value in headers.items()}
        return await self._post_json(provider_id, provider, url, payload, headers=headers or None)

    async def _send_telegram(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        token = _resolved_text(route, "bot_token", "token") or _resolved_text(provider, "bot_token", "token")
        chat_id = _resolved_text(route, "chat_id", "to", "recipient") or _resolved_text(provider, "chat_id", "to", "recipient")
        if not token or not chat_id:
            return self._missing_target(provider_id, provider, "bot_token/chat_id")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }
        thread_id = _resolved_text(route, "message_thread_id", "thread_id") or _resolved_text(provider, "message_thread_id", "thread_id")
        if thread_id:
            payload["message_thread_id"] = thread_id
        return await self._post_json(provider_id, provider, url, payload)

    async def _send_email(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        recipients = _resolved_list(route, "to", "recipients", "recipient", "email") or _resolved_list(
            provider, "to", "recipients", "recipient", "email"
        )
        bcc_recipients = _resolved_list(route, "bcc", "bcc_recipients", "bcc_addresses") or _resolved_list(
            provider, "bcc", "bcc_recipients", "bcc_addresses"
        )
        smtp_host = _resolved_text(provider, "smtp_host", "host")
        from_address = _resolved_text(route, "from", "from_email") or _resolved_text(provider, "from", "from_email", "sender")
        if not recipients or not smtp_host or not from_address:
            return self._missing_target(provider_id, provider, "smtp_host/from/to")

        await asyncio.to_thread(self._send_email_sync, provider, route, recipients, bcc_recipients, from_address, event, text)
        bcc_detail = f" and {len(bcc_recipients)} BCC recipient(s)" if bcc_recipients else ""
        return {"provider": provider_id, "type": "email", "status": "sent", "detail": f"Queued email to {len(recipients)} recipient(s){bcc_detail}."}

    def _send_email_sync(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        recipients: list[str],
        bcc_recipients: list[str],
        from_address: str,
        event: Mapping[str, Any],
        text: str,
    ) -> None:
        smtp_host = _resolved_text(provider, "smtp_host", "host")
        smtp_port = int(_coerce_float(_resolve_config_value(provider.get("smtp_port") or provider.get("port")), default=465 if _coerce_bool(provider.get("use_ssl"), default=False) else 587))
        username = _resolved_text(provider, "username", "user")
        password = _resolved_text(provider, "password")
        use_ssl = _coerce_bool(provider.get("use_ssl"), default=False)
        use_tls = _coerce_bool(provider.get("use_tls"), default=not use_ssl)

        message = self._build_email_message(provider, route, recipients, from_address, event, text)

        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(smtp_host, smtp_port, timeout=float(_coerce_float(provider.get("timeout"), default=10.0))) as smtp:
            if use_tls and not use_ssl:
                smtp.starttls()
            if username or password:
                smtp.login(username, password)
            refused = smtp.send_message(message, to_addrs=[*recipients, *bcc_recipients])
            if refused:
                refused_recipients = ", ".join(str(recipient) for recipient in refused)
                raise RuntimeError(f"SMTP refused recipient(s): {refused_recipients}")

    async def _send_aws_ses_email(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        recipients = _resolved_list(route, "to", "recipients", "recipient", "email") or _resolved_list(
            provider, "to", "recipients", "recipient", "email"
        )
        bcc_recipients = _resolved_list(route, "bcc", "bcc_recipients", "bcc_addresses") or _resolved_list(
            provider, "bcc", "bcc_recipients", "bcc_addresses"
        )
        from_address = _resolved_text(route, "from", "from_email") or _resolved_text(
            provider, "from", "from_email", "sender", "source"
        )
        if not recipients or not from_address:
            return self._missing_target(provider_id, provider, "from/to")

        def _send():
            import boto3

            kwargs = {}
            region = _resolved_text(provider, "region", "region_name", "ses_region")
            if region:
                kwargs["region_name"] = region
            client = boto3.client("sesv2", **kwargs)
            subject = self._email_subject(provider, route, event)
            attachments = _notification_attachments(event)
            request: dict[str, Any] = {
                "FromEmailAddress": from_address,
                "Destination": {"ToAddresses": recipients},
            }
            if bcc_recipients:
                request["Destination"]["BccAddresses"] = bcc_recipients
            if attachments:
                message = self._build_email_message(provider, route, recipients, from_address, event, text)
                request["Content"] = {"Raw": {"Data": message.as_bytes()}}
            else:
                request["Content"] = {
                    "Simple": {
                        "Subject": {"Data": subject},
                        "Body": {"Text": {"Data": text}},
                    }
                }
            reply_to = _resolved_list(route, "reply_to", "reply_to_addresses") or _resolved_list(
                provider, "reply_to", "reply_to_addresses"
            )
            if reply_to:
                request["ReplyToAddresses"] = reply_to
            configuration_set = _resolved_text(provider, "configuration_set", "configuration_set_name")
            if configuration_set:
                request["ConfigurationSetName"] = configuration_set
            return client.send_email(**request)

        await asyncio.to_thread(_send)
        return {"provider": provider_id, "type": "email_aws_ses", "status": "sent", "detail": f"Sent SES email to {len(recipients)} recipient(s)."}

    def _email_subject(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> str:
        subject_prefix = self._subject_prefix(provider, route, event)
        subject = _first_text(event.get("subject"), event.get("title"), event.get("event_type"), "Notification")
        return f"{subject_prefix} {subject}".strip() if subject_prefix else subject

    def _build_email_message(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        recipients: list[str],
        from_address: str,
        event: Mapping[str, Any],
        text: str,
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = self._email_subject(provider, route, event)
        message["From"] = from_address
        message["To"] = ", ".join(recipients)
        reply_to = _resolved_list(route, "reply_to", "reply_to_addresses") or _resolved_list(
            provider, "reply_to", "reply_to_addresses"
        )
        if reply_to:
            message["Reply-To"] = ", ".join(reply_to)
        message.set_content(str(text or ""))
        for attachment in _notification_attachments(event):
            maintype, subtype = _mime_parts(attachment.get("content_type", "text/plain"))
            filename = attachment.get("filename") or "attachment.txt"
            content = attachment.get("content", "")
            if maintype == "text":
                if isinstance(content, (bytes, bytearray)):
                    content = bytes(content).decode("utf-8", errors="replace")
                message.add_attachment(content, subtype=subtype, filename=filename)
            else:
                payload = bytes(content) if isinstance(content, (bytes, bytearray)) else str(content or "").encode("utf-8")
                message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
        return message

    def _subject_prefix(
        self,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> str:
        prefix = _resolved_optional_text(route, "subject_prefix", "email_subject_prefix")
        if prefix is None:
            prefix = _resolved_optional_text(event, "subject_prefix", "email_subject_prefix")
        if prefix is None:
            prefix = _resolved_optional_text(provider, "subject_prefix", "email_subject_prefix")
        return "[Plaza]" if prefix is None else prefix

    async def _send_sms(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        provider_type = _provider_kind(provider.get("type"))
        sms_text = self._sms_text(text)
        if provider_type == "sms_twilio":
            return await self._send_twilio_sms(provider_id, provider, route, sms_text)
        if provider_type == "sms_aws_sns":
            return await self._send_aws_sns_sms(provider_id, provider, route, sms_text)

        url = _resolved_text(route, "webhook_url", "url") or _resolved_text(provider, "webhook_url", "url")
        to_number = _resolved_text(route, "to", "phone", "phone_number") or _resolved_text(provider, "to", "phone", "phone_number")
        if not url:
            return self._missing_target(provider_id, provider, "url")
        payload = {"to": to_number, "text": sms_text, "notification": copy.deepcopy(dict(event))}
        return await self._post_json(provider_id, provider, url, payload)

    async def _send_twilio_sms(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        account_sid = _resolved_text(provider, "account_sid", "sid")
        auth_token = _resolved_text(provider, "auth_token", "token")
        from_number = _resolved_text(route, "from", "from_number") or _resolved_text(provider, "from", "from_number")
        to_number = _resolved_text(route, "to", "phone", "phone_number") or _resolved_text(provider, "to", "phone", "phone_number")
        if not account_sid or not auth_token or not from_number or not to_number:
            return self._missing_target(provider_id, provider, "account_sid/auth_token/from/to")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        return await self._post_form(
            provider_id,
            provider,
            url,
            {"From": from_number, "To": to_number, "Body": text},
            auth=httpx.BasicAuth(account_sid, auth_token),
        )

    async def _send_aws_sns_sms(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        route: Mapping[str, Any],
        text: str,
    ) -> dict[str, Any]:
        phone_number = _resolved_text(route, "to", "phone", "phone_number") or _resolved_text(provider, "to", "phone", "phone_number")
        if not phone_number:
            return self._missing_target(provider_id, provider, "phone_number")

        def _publish():
            import boto3

            kwargs = {}
            region = _resolved_text(provider, "region", "region_name")
            if region:
                kwargs["region_name"] = region
            client = boto3.client("sns", **kwargs)
            return client.publish(PhoneNumber=phone_number, Message=text)

        await asyncio.to_thread(_publish)
        return {"provider": provider_id, "type": "sms_aws_sns", "status": "sent", "detail": "Published SMS through AWS SNS."}

    async def _post_json(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        client = self._http_client_factory()
        try:
            response = await client.post(url, json=dict(payload), headers=dict(headers or {}))
            response.raise_for_status()
            return {"provider": provider_id, "type": provider.get("type"), "status": "sent", "detail": "Notification sent."}
        finally:
            close = getattr(client, "aclose", None)
            if callable(close):
                await close()

    async def _post_form(
        self,
        provider_id: str,
        provider: Mapping[str, Any],
        url: str,
        data: Mapping[str, Any],
        *,
        auth: Any = None,
    ) -> dict[str, Any]:
        client = self._http_client_factory()
        try:
            response = await client.post(url, data=dict(data), auth=auth)
            response.raise_for_status()
            return {"provider": provider_id, "type": provider.get("type"), "status": "sent", "detail": "Notification sent."}
        finally:
            close = getattr(client, "aclose", None)
            if callable(close):
                await close()

    def _render_text(self, event: Mapping[str, Any], *, bucket: Optional[NotificationBucket] = None) -> str:
        if bucket is not None:
            return self._render_summary_text(bucket)
        render_mode = _first_text(event.get("render_mode"), event.get("message_style")).lower().replace("-", "_")
        if render_mode in {"direct", "message", "message_only"}:
            direct_text = _first_text(event.get("message"), event.get("summary"))
            if direct_text:
                return _absolute_direct_message_links(direct_text, event)
        title = _first_text(event.get("title"), event.get("subject"), event.get("event_type"), "Plaza notification")
        lines = [f"[Plaza] {title}"]
        for label, key in (
            ("Event", "event_type"),
            ("Exchange", "exchange_id"),
            ("Practice", "practice_name"),
            ("Practice ID", "practice_id"),
            ("From", "sender"),
            ("To", "receiver"),
            ("Status", "status"),
            ("Severity", "severity"),
            ("Expires", "expires_at"),
        ):
            value = _first_text(event.get(key))
            if value:
                lines.append(f"{label}: {value}")
        summary = _first_text(event.get("summary"), event.get("message"))
        if summary:
            lines.append(f"Summary: {summary}")
        plaza_url = _first_text(event.get("plaza_url"))
        if plaza_url:
            lines.append(f"Plaza: {plaza_url}")
        lines.append("Fetch the payload or result from Plaza over the authenticated agent channel.")
        return "\n".join(lines)

    def _render_summary_text(self, bucket: NotificationBucket) -> str:
        receiver = _first_text(bucket.events[-1].get("receiver"), bucket.receiver_id, "agent")
        lines = [f"[Plaza] {len(bucket.events)} notification(s) for {receiver}"]
        lines.append("Summary window batched these events:")
        for event in bucket.events[:10]:
            title = _first_text(event.get("title"), event.get("event_type"), "notification")
            exchange_id = _first_text(event.get("exchange_id"))
            sender = _first_text(event.get("sender"))
            suffix = []
            if exchange_id:
                suffix.append(f"exchange {exchange_id}")
            if sender:
                suffix.append(f"from {sender}")
            lines.append(f"- {title}" + (f" ({', '.join(suffix)})" if suffix else ""))
        if len(bucket.events) > 10:
            lines.append(f"- ... and {len(bucket.events) - 10} more")
        plaza_url = _first_text(bucket.events[-1].get("plaza_url"))
        if plaza_url:
            lines.append(f"Plaza: {plaza_url}")
        lines.append("Fetch payloads and results from Plaza over the authenticated agent channel.")
        return "\n".join(lines)

    def _summary_event(self, bucket: NotificationBucket) -> dict[str, Any]:
        latest = dict(bucket.events[-1])
        latest.pop("attachments", None)
        latest["event_type"] = "notification.summary"
        latest["title"] = f"{len(bucket.events)} Plaza notification(s)"
        latest["summary"] = f"Summary of {len(bucket.events)} queued notification(s)."
        latest["status"] = "summary"
        return latest

    def _sms_text(self, text: str) -> str:
        compact = " ".join(str(text or "").split())
        return compact[:480]

    def _missing_target(self, provider_id: str, provider: Mapping[str, Any], target: str) -> dict[str, Any]:
        return {
            "provider": provider_id,
            "type": provider.get("type"),
            "status": "failed",
            "detail": f"Missing {target} for notification provider.",
        }

    def _overall_status(self, deliveries: list[Mapping[str, Any]]) -> str:
        if not deliveries:
            return "skipped"
        statuses = {str(delivery.get("status") or "") for delivery in deliveries}
        if statuses <= {"sent"}:
            return "sent"
        if statuses <= {"queued_summary"}:
            return "queued_summary"
        if statuses <= {"skipped"}:
            return "skipped"
        if "sent" in statuses or "queued_summary" in statuses:
            return "partial"
        return "failed"


__all__ = [
    "NOTIFICATION_PROVIDER_KINDS",
    "NotificationDispatcher",
    "extract_notification_config",
    "normalize_notification_providers",
    "scrub_notification_event",
    "store_forward_summary_from_card",
]
