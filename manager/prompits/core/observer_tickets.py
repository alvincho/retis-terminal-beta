"""Short-lived tickets Plaza signs so its observer knows who sent a monitoring report.

Plaza signs a ticket for one agent and one observer with its own Ed25519 key. The agent attaches the ticket to every
report it sends to the observer; the observer checks the signature with Plaza's public key, so it learns the sender
without asking Plaza for each report and never takes an agent's word for its own identity. A ticket names exactly
one agent and one observer and is good for about five minutes.

Plaza uses `issue_ticket`; the observer uses `verify_ticket` and `public_key_record`.
"""
from __future__ import annotations

import base64
import math
import time
from typing import Mapping

from .capabilities import canonical
from .native_authority import KEY_ID, key_id_for, public_key_bytes

SCHEMA = "prompits.observer-ticket/1"
TICKET_SECONDS = 300
# The longest life a ticket may claim, and how far ahead of the observer's clock Plaza's may run.
MAX_TICKET_SECONDS = 600
CLOCK_SKEW_SECONDS = 5
MAX_ID_LENGTH = 200
CLAIMS = frozenset({"schema", "agentId", "observerId", "keyId", "issuedAt", "expiresAt"})


class UnknownTicketKey(PermissionError):
    """The ticket is signed with a key the observer does not hold; `key_id` names it."""

    def __init__(self, key_id: str):
        super().__init__("The ticket is signed with an unknown Plaza key.")
        self.key_id = key_id


def _identity(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_ID_LENGTH and value.isprintable()


def issue_ticket(private_key, *, agent_id: str, observer_id: str, clock=time.time, seconds: float = TICKET_SECONDS) -> dict:
    """Plaza's signed ticket: `agent_id` may send reports to `observer_id` until it expires."""
    if not _identity(agent_id) or not _identity(observer_id):
        raise ValueError("A ticket names one agent and one observer.")
    if not 1 <= seconds <= MAX_TICKET_SECONDS:
        raise ValueError("A ticket lives at most ten minutes.")
    # Whole seconds: the ticket passes through agents unchanged, and integers read back the same in any language.
    now = int(clock())
    claims = {"schema": SCHEMA, "agentId": agent_id, "observerId": observer_id,
              "keyId": key_id_for(public_key_bytes(private_key)), "issuedAt": now, "expiresAt": now + int(seconds)}
    return {"claims": claims, "signature": base64.b64encode(private_key.sign(canonical(claims).encode())).decode()}


def public_key_record(private_key) -> dict:
    """What Plaza publishes so its observer can check tickets: `{keyId, publicKey}`."""
    public = public_key_bytes(private_key)
    return {"keyId": key_id_for(public), "publicKey": base64.b64encode(public).decode()}


def read_public_key(record) -> tuple:
    """`(keyId, public key bytes)` from Plaza's published record; raises ValueError unless the two agree."""
    if not isinstance(record, dict) or set(record) != {"keyId", "publicKey"}:
        raise ValueError("Plaza's observer key must be {keyId, publicKey}.")
    try:
        public = base64.b64decode(record["publicKey"], validate=True)
    except (TypeError, ValueError):
        public = b""
    if len(public) != 32 or record["keyId"] != key_id_for(public):
        raise ValueError("Plaza's observer key does not match its key id.")
    return record["keyId"], public


def verify_ticket(ticket, *, observer_id: str, keys: Mapping[str, bytes], clock=time.time) -> dict:
    """The ticket's claims once it is Plaza's, for this observer, and current; raises PermissionError otherwise.

    `keys` maps Plaza's key ids to public keys. A well-formed ticket under a key not in `keys` raises
    UnknownTicketKey, so the caller can fetch Plaza's current key and try once more.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(ticket, dict) or set(ticket) != {"claims", "signature"} or not isinstance(ticket["signature"], str):
        raise PermissionError("A signed observer ticket is required.")
    c = ticket["claims"]
    now = clock()
    if (not math.isfinite(now) or not isinstance(c, dict) or set(c) != CLAIMS or c["schema"] != SCHEMA
            or not _identity(c["agentId"]) or not _identity(c["observerId"])
            or not isinstance(c["keyId"], str) or not KEY_ID.fullmatch(c["keyId"])
            or type(c["issuedAt"]) not in (int, float) or type(c["expiresAt"]) not in (int, float)
            or not math.isfinite(c["issuedAt"]) or not math.isfinite(c["expiresAt"])
            or not c["issuedAt"] < c["expiresAt"] <= c["issuedAt"] + MAX_TICKET_SECONDS):
        raise PermissionError("The observer ticket is malformed.")
    if c["observerId"] != observer_id:
        raise PermissionError("The observer ticket is for another observer.")
    if c["issuedAt"] > now + CLOCK_SKEW_SECONDS:
        raise PermissionError("The observer ticket is not valid yet.")
    if c["expiresAt"] <= now:
        raise PermissionError("The observer ticket has expired.")
    public = keys.get(c["keyId"])
    if public is None:
        raise UnknownTicketKey(c["keyId"])
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            base64.b64decode(ticket["signature"], validate=True), canonical(c).encode())
    except (InvalidSignature, ValueError, TypeError):
        raise PermissionError("The observer ticket signature is invalid.") from None
    return dict(c)
