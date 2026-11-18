"""What a source publishes so a user's Terminal can call its vendor with the user's own key.

Users' keys never reach a source agent. A source only describes its vendor's
API: each endpoint's base URL, key slot and parameters, a stream protocol for
WebSocket channels, and declarative mappings. The card summary below names the
vendor hosts a Terminal pins when its user saves a key.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

PROTOCOL = "phemacast:direct-vendor/1"
STREAM = "phemacast:stream"
PROJECTIONS = "phemacast:model-projections"


def declares_key(authentication: Mapping[str, Any] | None) -> bool:
    auth = authentication if isinstance(authentication, Mapping) else {}
    return bool(auth.get("header") or auth.get("parameter") or auth.get("query")
                or "bearer" in (auth.get("provider_scheme"), auth.get("scheme")) or auth.get("scheme") == "optional_bearer")


def target(transport: str, extensions: Mapping[str, Any]) -> str:
    if transport == "websocket":
        protocol = extensions.get(STREAM)
        return str(protocol.get("url") or "") if isinstance(protocol, Mapping) else ""
    return str(extensions.get("attas.base_url") or "")


def summary(endpoints: Iterable[tuple[str, Mapping[str, Any] | None, Mapping[str, Any]]]) -> dict[str, Any]:
    """Summarize (transport, authentication, extensions) rows for the source card."""
    hosts, required, optional, streams = set(), False, False, False
    for transport, authentication, extensions in endpoints:
        if not declares_key(authentication):
            continue
        url = urlsplit(target(transport, extensions))
        if url.scheme != ("wss" if transport == "websocket" else "https") or not url.hostname:
            continue
        hosts.add(url.hostname.lower())
        streams = streams or transport == "websocket"
        if (authentication or {}).get("required") is False:
            optional = True
        else:
            required = True
    credential = "required" if required else "optional" if optional else "none"
    return {"protocol": PROTOCOL, "hosts": sorted(hosts), "credential": credential, "streams": streams}
