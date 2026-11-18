"""
Built-in schema declarations for `prompits.core.init_schema`.

Prompits provides the core HTTP-native agent runtime, Plaza coordination layer, and
pool/practice infrastructure for FinMAS. Within Prompits, the core package defines the
shared abstractions that the rest of the runtime builds on.

Important callables in this file include `agent_practices_schema_dict`,
`agent_practices_table_schema`, `builtin_schema_cards`, and
`pulse_pulser_pairs_table_schema`, which capture the primary workflow implemented by
the module.
"""

from __future__ import annotations

from typing import Any, Dict, List

from prompits.core.schema import TableSchema


# Stable IDs for built-in schema pits.
SCHEMA_ID_PLAZA_CREDENTIALS = "3d07cb8a-11a7-4f7a-b95f-9f6ab3547a01"
SCHEMA_ID_PLAZA_LOGIN_HISTORY = "ac42f84f-f4e0-499e-9ee0-a7c127835d4b"
SCHEMA_ID_PLAZA_DIRECTORY = "27d8df89-0f4d-4d0d-bba8-cc9de4dd08f3"
SCHEMA_ID_AGENT_PRACTICES = "d8a3c4c9-68d1-447f-9f50-cb65d5762446"
SCHEMA_ID_PULSE_PULSER_PAIRS = "60b9df10-00b7-4a1b-9080-a1c66fe8e4f5"
SCHEMA_ID_PLAZA_UI_USERS = "0c9fd25c-a465-47a7-bca7-7f512c5606d8"
SCHEMA_ID_PLAZA_UI_AGENT_KEYS = "7e2d6f8c-32b8-4ec6-b1f4-8d9a2c64ef10"
SCHEMA_ID_PLAZA_CREDIT_LEDGER = "1166f892-42a4-4fe8-8f82-4d66be28a8d1"
SCHEMA_ID_PLAZA_TRUST_ENTRIES = "3ca8e12a-3a19-43e7-9f1a-e5f103b7c0da"
SCHEMA_ID_PLAZA_UI_VISIT_LOGS = "2a8f4b12-2f3d-4f31-a277-6b8ddf9980e1"
SCHEMA_ID_PLAZA_SITE_SETTINGS = "7b583ce5-32c6-4b05-a46e-2c445b4c4b28"
SCHEMA_ID_PLAZA_REPORT_ESCROWS = "bb5a9468-9670-4ea1-a813-b24ef5e076cf"
SCHEMA_ID_PLAZA_PAYMENT_SCOPES = "fa0f9f62-5ce9-4fef-b0f5-7dc74e03dd82"


def plaza_credentials_schema_dict() -> Dict[str, Any]:
    """Handle Plaza credentials schema dict."""
    return {
        "name": "plaza_credentials",
        "description": "Persisted Plaza credentials by agent and plaza URL",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "plaza_url": {"type": "string"},
            "agent_id": {"type": "string"},
            "agent_name": {"type": "string"},
            "api_key": {"type": "string"},
            "updated_at": {"type": "datetime"},
        },
    }


def plaza_login_history_schema_dict() -> Dict[str, Any]:
    """Handle Plaza login history schema dict."""
    return {
        "name": "plaza_login_history",
        "description": "Login/relogin events by agent_id",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "agent_id": {"type": "string"},
            "agent_name": {"type": "string"},
            "address": {"type": "string"},
            "event": {"type": "string"},
            "timestamp": {"type": "float"},
        },
    }


def plaza_directory_schema_dict() -> Dict[str, Any]:
    """Handle Plaza directory schema dict."""
    return {
        "name": "plaza_directory",
        "description": "Plaza directory entries",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "agent_id": {"type": "string"},
            "name": {"type": "string"},
            "type": {"type": "string"},
            "description": {"type": "string"},
            "owner": {"type": "string"},
            "address": {"type": "string"},
            "meta": {"type": "json"},
            "card": {"type": "json"},
            "trust_level": {"type": "string"},
            "trust_scope": {"type": "string"},
            "trust_source": {"type": "string"},
            "trusted": {"type": "boolean"},
            "updated_at": {"type": "datetime"},
        },
    }


def agent_practices_schema_dict() -> Dict[str, Any]:
    """Handle agent practices schema dict."""
    return {
        "name": "agent_practices",
        "description": "Persisted practice metadata by agent and practice id",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "agent_name": {"type": "string"},
            "practice_id": {"type": "string"},
            "practice_name": {"type": "string"},
            "practice_description": {"type": "string"},
            "practice_data": {"type": "json"},
            "is_deleted": {"type": "boolean"},
            "updated_at": {"type": "datetime"},
        },
    }


def pulse_pulser_pairs_schema_dict() -> Dict[str, Any]:
    """Handle pulse pulser pairs schema dict."""
    return {
        "name": "pulse_pulser_pairs",
        "description": "Pulse-to-pulser availability index for fast pulser lookup.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "pulse_id": {"type": "string"},
            "pulse_directory_id": {"type": "string"},
            "pulse_name": {"type": "string"},
            "pulse_address": {"type": "string"},
            "pulse_definition": {"type": "json"},
            "status": {"type": "string"},
            "is_complete": {"type": "boolean"},
            "completion_status": {"type": "string"},
            "completion_errors": {"type": "json"},
            "pulser_id": {"type": "string"},
            "pulser_directory_id": {"type": "string"},
            "pulser_name": {"type": "string"},
            "pulser_address": {"type": "string"},
            "input_schema": {"type": "json"},
            "cost_points": {"type": "integer"},
            "pricing": {"type": "json"},
            "cost_calculation": {"type": "json"},
            "updated_at": {"type": "datetime"},
        },
    }


def plaza_ui_users_schema_dict() -> Dict[str, Any]:
    """Handle Plaza UI users schema dict."""
    return {
        "name": "plaza_ui_users",
        "description": "Plaza UI users authenticated through Supabase Auth with local role assignments.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "username": {"type": "string"},
            "email": {"type": "string"},
            "display_name": {"type": "string"},
            "credit_points": {"type": "integer"},
            "profile_public": {"type": "boolean"},
            "public_email": {"type": "boolean"},
            "role": {"type": "string"},
            "status": {"type": "string"},
            "auth_provider": {"type": "string"},
            "email_confirmed": {"type": "boolean"},
            "email_confirmed_at": {"type": "datetime"},
            "newsletter": {"type": "boolean"},
            "newsletter_updated_at": {"type": "datetime"},
            "newsletter_language": {"type": "string"},
            "preferences": {"type": "json"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
            "last_sign_in_at": {"type": "datetime"},
        },
    }


def plaza_newsletter_tokens_schema_dict() -> Dict[str, Any]:
    """Each account's newsletter unsubscribe token: the secret in its emails' unsubscribe link."""
    return {
        "name": "plaza_newsletter_tokens",
        "description": "Per-account tokens for one-click newsletter unsubscribe links. A token only turns the newsletter off.",
        "primary_key": ["user_id"],
        "rowSchema": {
            "user_id": {"type": "string"},
            "token": {"type": "string"},
            "created_at": {"type": "datetime"},
        },
    }


def plaza_ui_agent_keys_schema_dict() -> Dict[str, Any]:
    """Handle Plaza UI agent keys schema dict."""
    return {
        "name": "plaza_ui_agent_keys",
        "description": "Named Plaza owner keys that let UI users claim ownership for registered agents.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "user_id": {"type": "string"},
            "username": {"type": "string"},
            "display_name": {"type": "string"},
            "email": {"type": "string"},
            "name": {"type": "string"},
            "secret": {"type": "string"},
            "status": {"type": "string"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
            "last_used_at": {"type": "datetime"},
        },
    }


def plaza_credit_ledger_schema_dict() -> Dict[str, Any]:
    """Handle Plaza credit ledger schema dict."""
    return {
        "name": "plaza_credit_ledger",
        "description": "Durable ledger of Plaza credit point balance changes.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "user_id": {"type": "string"},
            "username": {"type": "string"},
            "email": {"type": "string"},
            "action": {"type": "string"},
            "delta": {"type": "integer"},
            "previous_credit_points": {"type": "integer"},
            "credit_points": {"type": "integer"},
            "reason": {"type": "string"},
            "source": {"type": "string"},
            "actor_user_id": {"type": "string"},
            "actor_username": {"type": "string"},
            "request_id": {"type": "string"},
            "practice_id": {"type": "string"},
            "agent_id": {"type": "string"},
            "agent_name": {"type": "string"},
            "metadata": {"type": "json"},
            "created_at": {"type": "datetime"},
        },
    }


def plaza_trust_entries_schema_dict() -> Dict[str, Any]:
    """Handle Plaza user-managed trust entries schema dict."""
    return {
        "name": "plaza_trust_entries",
        "description": "Per-user trust decisions for Plaza agents and owners.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "user_id": {"type": "string"},
            "username": {"type": "string"},
            "display_name": {"type": "string"},
            "email": {"type": "string"},
            "scope": {"type": "string"},
            "subject": {"type": "string"},
            "label": {"type": "string"},
            "trust_level": {"type": "string"},
            "status": {"type": "string"},
            "notes": {"type": "string"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
    }


def plaza_report_escrows_schema_dict() -> Dict[str, Any]:
    """Handle Plaza report escrow schema dict."""
    return {
        "name": "plaza_report_escrows",
        "description": "Report-level Plaza point escrow reservations and final capture/refund state.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "request_id": {"type": "string"},
            "consumer_user_id": {"type": "string"},
            "consumer_username": {"type": "string"},
            "controller_agent_id": {"type": "string"},
            "controller_agent_name": {"type": "string"},
            "controller_owner_user_id": {"type": "string"},
            "controller_owner_username": {"type": "string"},
            "reserved_points": {"type": "integer"},
            "platform_points": {"type": "integer"},
            "service_points": {"type": "integer"},
            "status": {"type": "string"},
            "visibility": {"type": "string"},
            "reservation_token": {"type": "string"},
            "quotes": {"type": "json"},
            "metadata": {"type": "json"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
            "valid_until": {"type": "datetime"},
        },
    }


def plaza_payment_scopes_schema_dict() -> Dict[str, Any]:
    """Handle Plaza provider payment scope schema dict."""
    return {
        "name": "plaza_payment_scopes",
        "description": "Scoped provider payments authorized under a report escrow.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "request_id": {"type": "string"},
            "escrow_id": {"type": "string"},
            "parent_scope_id": {"type": "string"},
            "scope_token": {"type": "string"},
            "payer_user_id": {"type": "string"},
            "payer_username": {"type": "string"},
            "provider_user_id": {"type": "string"},
            "provider_username": {"type": "string"},
            "provider_agent_id": {"type": "string"},
            "provider_agent_name": {"type": "string"},
            "controller_agent_id": {"type": "string"},
            "controller_agent_name": {"type": "string"},
            "quote_id": {"type": "string"},
            "practice_id": {"type": "string"},
            "pulse_name": {"type": "string"},
            "pulse_address": {"type": "string"},
            "max_points": {"type": "integer"},
            "status": {"type": "string"},
            "quote": {"type": "json"},
            "metadata": {"type": "json"},
            "result_hash": {"type": "string"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
            "valid_until": {"type": "datetime"},
            "paid_at": {"type": "datetime"},
        },
    }


def plaza_ui_visit_logs_schema_dict() -> Dict[str, Any]:
    """Handle Plaza UI site-wide visit log schema dict."""
    return {
        "name": "plaza_ui_visit_logs",
        "description": "Site-wide Attas UI visit events scoped by build.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "build_id": {"type": "string"},
            "session_id": {"type": "string"},
            "visitor_id": {"type": "string"},
            "page": {"type": "string"},
            "page_label": {"type": "string"},
            "path": {"type": "string"},
            "hash": {"type": "string"},
            "title": {"type": "string"},
            "started_at": {"type": "datetime"},
            "ended_at": {"type": "datetime"},
            "duration_ms": {"type": "integer"},
            "referrer": {"type": "string"},
            "user_agent": {"type": "string"},
            "language": {"type": "string"},
            "languages": {"type": "json"},
            "locale": {"type": "string"},
            "country_code": {"type": "string"},
            "country_name": {"type": "string"},
            "timezone": {"type": "string"},
            "browser_name": {"type": "string"},
            "browser_version": {"type": "string"},
            "os_name": {"type": "string"},
            "os_version": {"type": "string"},
            "device_type": {"type": "string"},
            "platform": {"type": "string"},
            "viewport_width": {"type": "integer"},
            "viewport_height": {"type": "integer"},
            "screen_width": {"type": "integer"},
            "screen_height": {"type": "integer"},
            "color_scheme": {"type": "string"},
            "hardware_concurrency": {"type": "integer"},
            "device_memory": {"type": "float"},
            "site_origin": {"type": "string"},
            "deployment_scope": {"type": "string"},
            "user_id": {"type": "string"},
            "user_name": {"type": "string"},
            "user_role": {"type": "string"},
            "ip_address": {"type": "string"},
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
    }


def plaza_site_settings_schema_dict() -> Dict[str, Any]:
    """Handle Plaza site settings schema dict."""
    return {
        "name": "plaza_site_settings",
        "description": "Durable Plaza site-wide settings.",
        "primary_key": ["id"],
        "rowSchema": {
            "id": {"type": "string"},
            "settings": {"type": "json"},
            "updated_at": {"type": "datetime"},
        },
    }


def plaza_credentials_table_schema() -> TableSchema:
    """Return the Plaza credentials table schema."""
    return TableSchema(plaza_credentials_schema_dict())


def plaza_login_history_table_schema() -> TableSchema:
    """Return the Plaza login history table schema."""
    return TableSchema(plaza_login_history_schema_dict())


def plaza_directory_table_schema() -> TableSchema:
    """Return the Plaza directory table schema."""
    return TableSchema(plaza_directory_schema_dict())


def agent_practices_table_schema() -> TableSchema:
    """Return the agent practices table schema."""
    return TableSchema(agent_practices_schema_dict())


def pulse_pulser_pairs_table_schema() -> TableSchema:
    """Return the pulse pulser pairs table schema."""
    return TableSchema(pulse_pulser_pairs_schema_dict())


def plaza_ui_users_table_schema() -> TableSchema:
    """Return the Plaza UI users table schema."""
    return TableSchema(plaza_ui_users_schema_dict())


def plaza_newsletter_tokens_table_schema() -> TableSchema:
    """Return the Plaza newsletter unsubscribe tokens table schema."""
    return TableSchema(plaza_newsletter_tokens_schema_dict())


def plaza_ui_agent_keys_table_schema() -> TableSchema:
    """Return the Plaza UI agent keys table schema."""
    return TableSchema(plaza_ui_agent_keys_schema_dict())


def plaza_credit_ledger_table_schema() -> TableSchema:
    """Return the Plaza credit ledger table schema."""
    return TableSchema(plaza_credit_ledger_schema_dict())


def plaza_report_escrows_table_schema() -> TableSchema:
    """Return the Plaza report escrows table schema."""
    return TableSchema(plaza_report_escrows_schema_dict())


def plaza_payment_scopes_table_schema() -> TableSchema:
    """Return the Plaza payment scopes table schema."""
    return TableSchema(plaza_payment_scopes_schema_dict())


def plaza_trust_entries_table_schema() -> TableSchema:
    """Return the Plaza trust entries table schema."""
    return TableSchema(plaza_trust_entries_schema_dict())


def plaza_ui_visit_logs_table_schema() -> TableSchema:
    """Return the Plaza UI visit logs table schema."""
    return TableSchema(plaza_ui_visit_logs_schema_dict())


def plaza_site_settings_table_schema() -> TableSchema:
    """Return the Plaza site settings table schema."""
    return TableSchema(plaza_site_settings_schema_dict())


def builtin_schema_cards(plaza_url: str) -> List[Dict[str, Any]]:
    """Return the builtin schema cards."""
    normalized_plaza = (plaza_url or "").rstrip("/")
    return [
        {
            "schema_id": SCHEMA_ID_PLAZA_CREDENTIALS,
            "name": "Schema: plaza_credentials",
            "card": {
                "name": "Schema: plaza_credentials",
                "description": "Built-in table schema for persisted Plaza credentials.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_CREDENTIALS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_credentials", "schema_kind": "table", "schema": plaza_credentials_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_LOGIN_HISTORY,
            "name": "Schema: plaza_login_history",
            "card": {
                "name": "Schema: plaza_login_history",
                "description": "Built-in table schema for Plaza login/relogin history.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_LOGIN_HISTORY, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_login_history", "schema_kind": "table", "schema": plaza_login_history_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_DIRECTORY,
            "name": "Schema: plaza_directory",
            "card": {
                "name": "Schema: plaza_directory",
                "description": "Built-in table schema for the Plaza searchable directory.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_DIRECTORY, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_directory", "schema_kind": "table", "schema": plaza_directory_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_AGENT_PRACTICES,
            "name": "Schema: agent_practices",
            "card": {
                "name": "Schema: agent_practices",
                "description": "Built-in table schema for persisted agent practice metadata.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table"],
                "pit_address": {"pit_id": SCHEMA_ID_AGENT_PRACTICES, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "agent_practices", "schema_kind": "table", "schema": agent_practices_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PULSE_PULSER_PAIRS,
            "name": "Schema: pulse_pulser_pairs",
            "card": {
                "name": "Schema: pulse_pulser_pairs",
                "description": "Built-in table schema for pulse-to-pulser availability records.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table"],
                "pit_address": {"pit_id": SCHEMA_ID_PULSE_PULSER_PAIRS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "pulse_pulser_pairs", "schema_kind": "table", "schema": pulse_pulser_pairs_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_UI_USERS,
            "name": "Schema: plaza_ui_users",
            "card": {
                "name": "Schema: plaza_ui_users",
                "description": "Built-in table schema for Plaza UI users and role assignments.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "auth"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_UI_USERS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_ui_users", "schema_kind": "table", "schema": plaza_ui_users_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_UI_AGENT_KEYS,
            "name": "Schema: plaza_ui_agent_keys",
            "card": {
                "name": "Schema: plaza_ui_agent_keys",
                "description": "Built-in table schema for Plaza UI user-managed agent owner keys.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "auth"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_UI_AGENT_KEYS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_ui_agent_keys", "schema_kind": "table", "schema": plaza_ui_agent_keys_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_CREDIT_LEDGER,
            "name": "Schema: plaza_credit_ledger",
            "card": {
                "name": "Schema: plaza_credit_ledger",
                "description": "Built-in table schema for Plaza credit point usage and top-up history.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "auth", "credits"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_CREDIT_LEDGER, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_credit_ledger", "schema_kind": "table", "schema": plaza_credit_ledger_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_REPORT_ESCROWS,
            "name": "Schema: plaza_report_escrows",
            "card": {
                "name": "Schema: plaza_report_escrows",
                "description": "Built-in table schema for report escrow reservations.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "billing", "escrow"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_REPORT_ESCROWS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_report_escrows", "schema_kind": "table", "schema": plaza_report_escrows_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_PAYMENT_SCOPES,
            "name": "Schema: plaza_payment_scopes",
            "card": {
                "name": "Schema: plaza_payment_scopes",
                "description": "Built-in table schema for scoped provider payments.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "billing", "scope"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_PAYMENT_SCOPES, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_payment_scopes", "schema_kind": "table", "schema": plaza_payment_scopes_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_TRUST_ENTRIES,
            "name": "Schema: plaza_trust_entries",
            "card": {
                "name": "Schema: plaza_trust_entries",
                "description": "Built-in table schema for user-managed Plaza agent and owner trust.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "auth", "trust"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_TRUST_ENTRIES, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_trust_entries", "schema_kind": "table", "schema": plaza_trust_entries_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_UI_VISIT_LOGS,
            "name": "Schema: plaza_ui_visit_logs",
            "card": {
                "name": "Schema: plaza_ui_visit_logs",
                "description": "Built-in table schema for site-wide UI visit analytics by build.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "analytics"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_UI_VISIT_LOGS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_ui_visit_logs", "schema_kind": "table", "schema": plaza_ui_visit_logs_schema_dict()},
            },
        },
        {
            "schema_id": SCHEMA_ID_PLAZA_SITE_SETTINGS,
            "name": "Schema: plaza_site_settings",
            "card": {
                "name": "Schema: plaza_site_settings",
                "description": "Built-in table schema for Plaza site-wide settings.",
                "pit_type": "Schema",
                "owner": "Plaza",
                "tags": ["schema", "system", "table", "settings"],
                "pit_address": {"pit_id": SCHEMA_ID_PLAZA_SITE_SETTINGS, "plazas": [normalized_plaza] if normalized_plaza else []},
                "meta": {"schema_name": "plaza_site_settings", "schema_kind": "table", "schema": plaza_site_settings_schema_dict()},
            },
        },
    ]
