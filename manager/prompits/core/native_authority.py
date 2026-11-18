"""Native operation authority read from an agent's current Plaza registration.

An agent registers `meta.native_authority` in its card: the path of its operation ACL, its Ed25519 signing key, the
path that serves its operation contracts and the contract references it serves now. Plaza keeps no copy and pins
nothing about the agent. On each operation grant it decides whether it trusts the agent (registered now, not
suspended or blocked, owner account active, address proven, heartbeat live), then calls the ACL at the address it
proved the agent answers at and verifies the signed answer with the key in the current registration.

Plaza's requests carry an HMAC keyed by the agent's current Plaza token over the agent id, method, path, body digest,
a one-time nonce and a short expiry, so only Plaza can ask and no reusable secret leaves it. The same check guards an
agent's contract path. An agent may name a same-owner agent in `decidedBy` to answer for it; that agent's own key,
path and proven address are used.

Both sides live here: Plaza uses `RegisteredNativeAuthority` and `ensure_contract`; agents use `card_field`,
`signing_key`, `PlazaRequestCheck` and `entitlement`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import stat
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Callable, Optional

import httpx
from fastapi import HTTPException, Request

from .capabilities import canonical, digest
from .resource_registry import reference, validate_ref

LOGGER = logging.getLogger(__name__)

SCHEMA = "native-authority/1"
REQUEST_SCHEMA = "plaza-native-request/1"
ENTITLEMENT = "native-operation-entitlement/1"
MAX_OPERATIONS = 64
# Plaza's request is valid this long; the agent refuses older, later and repeated ones.
REQUEST_SECONDS = 60
# An agent that has not heartbeated this long is not called (prompits.agents.base PLAZA_CONNECTION_ACTIVE_WINDOW_SEC).
LIVE_SECONDS = 60
MAX_ANSWER_BYTES = 128_000
MAX_CONTRACTS_BYTES = 2_000_000
HEADER_AGENT, HEADER_NONCE, HEADER_EXPIRES, HEADER_SIGNATURE = (
    "X-Plaza-Agent", "X-Plaza-Nonce", "X-Plaza-Expires", "X-Plaza-Signature")
# Contracts of a registered agent live under Plaza's reserved `plaza.agents.<agent>` namespace, which no account can
# claim first; an owner may also publish under a namespace it already holds.
AGENT_NAMESPACE = "plaza.agents"
SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./:-]{0,199}$")
KEY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def valid_path(value) -> bool:
    return (isinstance(value, str) and bool(SAFE_PATH.fullmatch(value)) and not value.startswith("//")
            and ".." not in value)


def agent_namespace(agent_id: str, recipe) -> str:
    """The namespace a registered agent publishes `recipe`'s contracts under: one per agent and per contract content.

    Contracts are versioned by content: an unchanged recipe keeps its namespace, and with it every reference, across
    restarts and releases; a changed one gets a new namespace. The agent part keeps two agents apart.
    """
    return f"{AGENT_NAMESPACE}.{digest(str(agent_id))[:16]}.{digest(recipe)[:16]}"


def recipe_digest(recipe) -> str:
    """A contract's `publisher.implementationDigest`: the digest of the definitions it was built from."""
    return digest(recipe)


def content_versioned(agent_id: str, build: Callable[[str, str], dict]) -> dict:
    """An agent's contracts, versioned by their content: `build(namespace, implementation_digest)` -> contracts.

    The contracts are built once under a placeholder namespace; the digest of that draft names the real namespace and
    is every contract's implementation digest. So an unchanged set keeps all its references across restarts, code
    changes and releases, and a change to what any contract says gives the set new ones. The set shares one digest
    because a Phema binds an operation with its receipt only when both come from the same build (pulse_forms.py).
    """
    draft = build(AGENT_NAMESPACE + ".draft.contents", "0" * 64)
    content = digest(draft)
    return build(f"{AGENT_NAMESPACE}.{digest(str(agent_id))[:16]}.{content[:16]}", content)


def validate_card(value) -> dict:
    """Plaza's reading of a card's `meta.native_authority`; raises ValueError for anything else."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("native_authority must be a native-authority/1 object.")
    delegated = "decidedBy" in value
    allowed = {"schema", "operations", "contracts", "decidedBy"} if delegated else {
        "schema", "operations", "contracts", "path", "keyId", "publicKey"}
    if set(value) - allowed or not {"schema", "operations"} <= set(value):
        raise ValueError("native_authority has unknown or missing fields.")
    if delegated:
        if not isinstance(value["decidedBy"], str) or not 1 <= len(value["decidedBy"]) <= 200:
            raise ValueError("native_authority.decidedBy must name an agent.")
    else:
        if not {"path", "keyId", "publicKey"} <= set(value):
            raise ValueError("native_authority needs its ACL path, key id and public key.")
        if not valid_path(value["path"]):
            raise ValueError("native_authority.path must be an agent-relative path.")
        if not isinstance(value["keyId"], str) or not KEY_ID.fullmatch(value["keyId"]):
            raise ValueError("native_authority.keyId is invalid.")
        try:
            key = base64.b64decode(value["publicKey"], validate=True)
        except (TypeError, ValueError):
            key = b""
        if len(key) != 32:
            raise ValueError("native_authority.publicKey must be a base64 Ed25519 key.")
    if "contracts" in value and not valid_path(value["contracts"]):
        raise ValueError("native_authority.contracts must be an agent-relative path.")
    operations = value["operations"]
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise ValueError("native_authority.operations must list at most 64 contract references.")
    for ref in operations:
        validate_ref(ref)
    if len({canonical(ref) for ref in operations}) != len(operations):
        raise ValueError("native_authority.operations must not repeat a reference.")
    if operations and "contracts" not in value:
        raise ValueError("native_authority.contracts must say where its contracts are served.")
    return deepcopy(value)


def card_authority(card) -> Optional[dict]:
    """A card's validated native authority, or None when it has none or an invalid one."""
    meta = (card or {}).get("meta") if isinstance(card, dict) else None
    value = meta.get("native_authority") if isinstance(meta, dict) else None
    if value is None:
        return None
    try:
        return validate_card(value)
    except (ValueError, TypeError):
        return None


def public_key_bytes(private_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_for(public_key: bytes) -> str:
    return "ed25519:" + hashlib.sha256(public_key).hexdigest()[:16]


def card_field(*, operations, contracts: str, path: Optional[str] = None, private_key=None,
               decided_by: Optional[str] = None) -> dict:
    """The `meta.native_authority` an agent registers: its own ACL and key, or the same-owner agent deciding for it."""
    value = {"schema": SCHEMA, "operations": [reference(op) if "resource_type" in op else validate_ref(op)
                                              for op in operations], "contracts": contracts}
    if decided_by:
        value["decidedBy"] = decided_by
    else:
        public = public_key_bytes(private_key)
        value.update(path=path, keyId=key_id_for(public), publicKey=base64.b64encode(public).decode())
    return validate_card(value)


def signing_key(state_dir, name: str = "native-signing-key.pem", *, path=None):
    """The agent's own Ed25519 signing key, made in its state folder on first start and kept there (mode 600)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    location = Path(path) if path else Path(state_dir) / name
    location = location.expanduser().resolve()
    if not location.exists():
        location.parent.mkdir(parents=True, exist_ok=True)
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        try:
            descriptor = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # another process made it first; read theirs
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(pem)
    info = location.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise ValueError("The native signing key must be an owned mode-600 file.")
    key = serialization.load_pem_private_key(location.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("The native signing key must be Ed25519.")
    return key


def request_signature(token: str, agent_id: str, method: str, path: str, body: bytes, nonce: str, expires: int) -> str:
    message = "\n".join((REQUEST_SCHEMA, agent_id, method.upper(), path, hashlib.sha256(body).hexdigest(),
                         nonce, str(expires))).encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()


def request_headers(token: str, agent_id: str, method: str, path: str, body: bytes = b"", *, clock=time.time) -> dict:
    """Plaza's headers for one request to an agent: valid once, for REQUEST_SECONDS."""
    nonce, expires = secrets.token_urlsafe(24), int(clock()) + REQUEST_SECONDS
    return {HEADER_AGENT: agent_id, HEADER_NONCE: nonce, HEADER_EXPIRES: str(expires),
            HEADER_SIGNATURE: request_signature(token, agent_id, method, path, body, nonce, expires)}


class PlazaRequestCheck:
    """An agent's check that a request comes from its Plaza: an HMAC with its current Plaza token, used once.

    `token()` and `agent_id()` return the agent's current values. A token the agent held until moments ago stays
    accepted for REQUEST_SECONDS, for a request Plaza signed while renewing it.
    """

    def __init__(self, token: Callable[[], Optional[str]], agent_id: Callable[[], Optional[str]], *, clock=time.time):
        self.token, self.agent_id, self.clock = token, agent_id, clock
        self.seen: dict = {}
        self.previous: list = []
        self.current: Optional[str] = None
        self.lock = threading.Lock()

    def _tokens(self, now):
        token = self.token()
        with self.lock:
            if token != self.current:
                if self.current:
                    self.previous.append((self.current, now))
                self.current = token
            self.previous = [(old, at) for old, at in self.previous[-4:] if now - at <= REQUEST_SECONDS]
            return [t for t in [self.current, *[old for old, _ in self.previous]] if t]

    def verify(self, method: str, path: str, headers, body: bytes) -> None:
        now = self.clock()
        agent, nonce = headers.get(HEADER_AGENT, ""), headers.get(HEADER_NONCE, "")
        signature, expires = headers.get(HEADER_SIGNATURE, ""), headers.get(HEADER_EXPIRES, "")
        own = self.agent_id()
        try:
            expires = int(expires)
        except (TypeError, ValueError):
            raise PermissionError("Plaza's request is required.") from None
        if (not own or agent != own or not isinstance(nonce, str) or not 16 <= len(nonce) <= 128
                or not nonce.isascii() or not all(c.isalnum() or c in "-_" for c in nonce)
                or not now < expires <= now + REQUEST_SECONDS + 1):
            raise PermissionError("Plaza's request is required.")
        if not any(hmac.compare_digest(str(signature), request_signature(token, own, method, path, body, nonce, expires))
                   for token in self._tokens(now)):
            raise PermissionError("Plaza's request is required.")
        with self.lock:
            self.seen = {key: until for key, until in self.seen.items() if until > now}
            if nonce in self.seen or len(self.seen) >= 100_000:
                raise PermissionError("Plaza's request was already used.")
            self.seen[nonce] = expires


def entitlement(private_key, publisher: str, challenge: str, context, decision, *, clock=time.time) -> dict:
    """The signed answer an agent's ACL returns to Plaza for one checked context."""
    now = clock()
    claims = {"schema": ENTITLEMENT, "publisherAgentId": publisher, "keyId": key_id_for(public_key_bytes(private_key)),
              "challenge": challenge, "contextDigest": digest(context), "checkedAt": now, "expiresAt": now + 30,
              "scopes": list(decision["scopes"]), "downstream": deepcopy(decision.get("downstream") or [])}
    return {"claims": claims, "signature": base64.b64encode(private_key.sign(canonical(claims).encode())).decode()}


def verify_entitlement(packet, *, publisher: str, key_id: str, public_key: bytes, challenge: str, context,
                       clock=time.time) -> dict:
    """Plaza's check of an agent's signed answer; returns the decision or raises PermissionError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(packet, dict) or set(packet) != {"claims", "signature"}:
        raise PermissionError("A signed native entitlement is required.")
    c = packet["claims"]
    keys = {"schema", "publisherAgentId", "keyId", "challenge", "contextDigest", "checkedAt", "expiresAt", "scopes",
            "downstream"}
    now = clock()
    if (not math.isfinite(now) or not isinstance(c, dict) or set(c) != keys or c["schema"] != ENTITLEMENT
            or c["publisherAgentId"] != publisher or c["keyId"] != key_id or c["challenge"] != challenge
            or c["contextDigest"] != digest(context)
            or type(c["checkedAt"]) not in (int, float) or type(c["expiresAt"]) not in (int, float)
            or not math.isfinite(c["checkedAt"]) or not math.isfinite(c["expiresAt"])
            or not now - 2 <= c["checkedAt"] <= now + 1 or not now < c["expiresAt"] <= now + 60
            or not isinstance(c["scopes"], list) or len(c["scopes"]) > 128
            or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in c["scopes"])
            or not isinstance(c["downstream"], list) or len(c["downstream"]) > 64):
        raise PermissionError("The signed native entitlement differs from this request.")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            base64.b64decode(packet["signature"], validate=True), canonical(c).encode())
    except (InvalidSignature, ValueError, TypeError):
        raise PermissionError("The native entitlement signature is invalid.") from None
    return {"authorized": True, "scopes": deepcopy(c["scopes"]), "downstream": deepcopy(c["downstream"])}


def _read_bounded(response, limit):
    data = bytearray()
    for chunk in response.iter_bytes():
        if len(data) + len(chunk) > limit:
            raise PermissionError("The agent's answer exceeds its bounds.")
        data.extend(chunk)
    return bytes(data)


class RegisteredNativeAuthority:
    """Plaza's per-grant check and call of an agent's ACL, from the agent's current registration only."""

    def __init__(self, plaza, *, client_factory=None, timeout: float = 5.0, clock=time.time):
        self.plaza, self.timeout, self.clock = plaza, timeout, clock
        self.client_factory = client_factory or (lambda: httpx.Client(trust_env=False, follow_redirects=False,
                                                                      timeout=self.timeout))
        self.proved_at: dict = {}
        self.fetched_at: dict = {}
        self.locks: dict = {}
        self.lock = threading.Lock()

    def agent_lock(self, agent_id: str) -> threading.Lock:
        with self.lock:
            if len(self.locks) > 4096:
                self.locks = {k: v for k, v in self.locks.items() if v.locked()}
            return self.locks.setdefault(agent_id, threading.Lock())

    def state(self):
        practice = self.plaza._get_plaza_practice()
        state = getattr(practice, "state", None)
        if state is None:
            raise PermissionError("Plaza's agent registry is unavailable.")
        return state

    def trusted(self, agent_id: str) -> dict:
        """The agent's current card and address once Plaza trusts it now; raises PermissionError otherwise."""
        state = self.state()
        card, address = state.agent_cards.get(agent_id), state.registry.get(agent_id)
        if not isinstance(card, dict) or not address:
            raise PermissionError("The agent is not registered now.")
        blocker = getattr(state, "agent_block_reason", None)
        if blocker and blocker(agent_id, card):
            raise PermissionError("The agent is suspended or its owner is blocked.")
        from .plaza_ownership import agent_owner
        try:
            owner = agent_owner(self.plaza, agent_id)
        except Exception:
            raise PermissionError("The agent's registered owner is unavailable or disabled.") from None
        return {"card": card, "address": str(address), "owner": str(owner["id"])}

    def live(self, agent_id: str, address: str) -> None:
        """The agent has a live heartbeat and answered at this registry address with a proof bound to its token."""
        state = self.state()
        try:
            last = float(state.last_active.get(agent_id) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if self.clock() - last > LIVE_SECONDS:
            raise PermissionError("The agent has no live heartbeat.")
        proofs = getattr(state, "address_proofs", None)
        if proofs is None:
            raise PermissionError("Plaza cannot check the agent's address.")
        if proofs.view(agent_id, address)["verified"]:
            return
        # Proofs live in memory: after a Plaza restart (or a new address) check now rather than wait for the retry.
        # Calls made meanwhile wait for that one check; a failed check is not repeated for a few seconds.
        with self.agent_lock(agent_id):
            if proofs.view(agent_id, address)["verified"]:
                return
            if self.clock() - self.proved_at.get(agent_id, 0) >= 5:
                self.proved_at[agent_id] = self.clock()
                if proofs.prove_now(agent_id, address):
                    return
        raise PermissionError("The agent has not proven its registered address.")

    def authority(self, audience: str, ref) -> dict:
        """Who decides for `audience`'s operation `ref`, from the current registrations: card, key, path, address."""
        agent = self.trusted(audience)
        authority = card_authority(agent["card"])
        if authority is None:
            raise PermissionError("The agent registers no native operation authority.")
        if ref not in authority["operations"]:
            raise PermissionError("The agent's current registration does not offer this operation.")
        decider, decided = audience, authority
        if "decidedBy" in authority:
            decider = authority["decidedBy"]
            other = self.trusted(decider)
            # Only a same-owner agent may answer for another; any agent could otherwise point at any other.
            if other["owner"] != agent["owner"]:
                raise PermissionError("A native authority may be decided only by a same-owner agent.")
            decided = card_authority(other["card"])
            if decided is None or "decidedBy" in decided:
                raise PermissionError("The deciding agent registers no native operation authority of its own.")
            agent = other
        self.live(decider, agent["address"])
        from .address_proof import checkable, origin_of
        origin = origin_of(agent["address"])
        if not checkable(origin):
            raise PermissionError("The agent's registered address cannot be called.")
        return {"decider": decider, "origin": origin, "path": decided["path"], "keyId": decided["keyId"],
                "publicKey": base64.b64decode(decided["publicKey"]), "operations": authority["operations"]}

    def signed_post(self, agent_id: str, url_path: str, origin: str, payload) -> object:
        token = self.state().current_token_for_agent(agent_id)
        if not token:
            raise PermissionError("The agent holds no current Plaza token.")
        raw = canonical(payload).encode()
        if len(raw) > MAX_ANSWER_BYTES:
            raise PermissionError("The native authority context exceeds its bounds.")
        headers = {**request_headers(token, agent_id, "POST", url_path, raw, clock=self.clock),
                   "Content-Type": "application/json", "Accept": "application/json"}
        from .read_chain_contracts import parse_json
        with self.client_factory() as client:
            with client.stream("POST", origin + url_path, content=raw, headers=headers) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise PermissionError(f"The agent's native authority answered {response.status_code}.")
                return parse_json(_read_bounded(response, MAX_ANSWER_BYTES))

    def authorize(self, audience: str, context) -> dict:
        try:
            found = self.authority(audience, context["operation"]["ref"])
            challenge = secrets.token_urlsafe(20)
            packet = self.signed_post(found["decider"], found["path"], found["origin"],
                                      {"context": deepcopy(context), "challenge": challenge})
            return verify_entitlement(packet, publisher=audience, key_id=found["keyId"], public_key=found["publicKey"],
                                      challenge=challenge, context=context, clock=self.clock)
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("The agent's current native authority is unavailable.") from None

    def accepted(self, publisher: str, ref) -> bool:
        """Whether Plaza accepts this contract now: its publisher's current registration offers it."""
        try:
            agent = self.trusted(publisher)
        except PermissionError:
            return False
        authority = card_authority(agent["card"])
        return bool(authority and ref in authority["operations"])

    def advertisers(self, ref) -> list:
        """The registered agents whose current card offers `ref`; only its publisher can supply the exact contract."""
        state = self.state()
        return [agent_id for agent_id, card in list(state.agent_cards.items())
                if (authority := card_authority(card)) and ref in authority["operations"]]

    def fetch(self, agent_id: str, ref):
        """Read the agent's advertised contracts from its proven address, with Plaza's signed request."""
        agent = self.trusted(agent_id)
        authority = card_authority(agent["card"])
        if not authority or ref not in authority["operations"]:
            raise PermissionError("The agent's current registration does not offer this contract.")
        # One read per registration every few seconds: a ref the agent offers but does not serve is not retried at
        # once, while a new registration's contracts are read at once.
        offered_key = (agent_id, digest(authority["operations"]))
        if self.clock() - self.fetched_at.get(offered_key, 0) < 3:
            raise PermissionError("The agent's contracts were read moments ago; try again shortly.")
        if len(self.fetched_at) > 4096:
            self.fetched_at = {k: v for k, v in self.fetched_at.items() if self.clock() - v < 3}
        self.fetched_at[offered_key] = self.clock()
        self.live(agent_id, agent["address"])
        from .address_proof import origin_of
        from .read_chain_contracts import parse_json
        token = self.state().current_token_for_agent(agent_id)
        if not token:
            raise PermissionError("The agent holds no current Plaza token.")
        path = authority["contracts"]
        with self.client_factory() as client:
            with client.stream("GET", origin_of(agent["address"]) + path,
                               headers={**request_headers(token, agent_id, "GET", path, b"", clock=self.clock),
                                        "Accept": "application/json"}) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise PermissionError("The agent's contracts are unavailable.")
                value = parse_json(_read_bounded(response, MAX_CONTRACTS_BYTES))
        rows = []
        for key in ("operations", "retained"):  # current contracts, and earlier ones the agent still serves
            found = value.get(key) if isinstance(value, dict) else None
            rows += list(found.values()) if isinstance(found, dict) else found if isinstance(found, list) else []
        offered = {canonical(r) for r in authority["operations"]}
        return agent, [row for row in rows if isinstance(row, dict) and canonical(reference(row)) in offered]


def registered_authority(plaza) -> RegisteredNativeAuthority:
    cached = getattr(plaza, "_registered_native_authority", None)
    if cached is None:
        cached = plaza._registered_native_authority = RegisteredNativeAuthority(plaza)
    return cached


def authorizer_for(plaza, audience: str):
    """The ACL Plaza asks for `audience`: one composed into this Plaza process by code, else the registered one."""
    composed = (getattr(plaza, "operation_grant_authorizers", None) or {}).get(audience)
    if callable(composed):
        return composed
    return lambda context: registered_authority(plaza).authorize(audience, context)


def accepted(plaza, publisher: str, ref) -> bool:
    composed = (getattr(plaza, "operation_grant_authorizers", None) or {}).get(publisher)
    return callable(composed) or registered_authority(plaza).accepted(publisher, ref)


def namespace_allowed(registry, namespace: str, agent_id: str, owner: str) -> bool:
    """An agent's contracts go under its own `plaza.agents.<agent>` namespace or one its owner already holds."""
    own = f"{AGENT_NAMESPACE}.{digest(str(agent_id))[:16]}"
    if namespace == own or namespace.startswith(own + "."):
        return True
    rows = [r for r in registry.namespaces() if namespace == r["namespace"] or namespace.startswith(r["namespace"] + ".")]
    return bool(rows) and max(rows, key=lambda r: len(r["namespace"]))["owner"] == owner


def _held(registry, ref) -> Optional[bool]:
    try:
        validate_ref(ref)
        registry.get(ref["id"], ref["version"], "", expected_digest=ref["digest"])
        return True
    except KeyError:
        return None
    except (ValueError, TypeError):
        return False


def ensure_contract(plaza, registry, ref) -> bool:
    """Store an advertised contract Plaza does not hold yet, read from its publisher; True when it holds it now.

    Contracts come only from the agent whose current registration offers the exact reference, at the address Plaza
    proved it answers at. Each is stored for the agent's registered owner, publicly, and is immutable. Calls made
    while one read is under way wait for it.
    """
    held = _held(registry, ref)
    if held is not None:
        return held
    authority = registered_authority(plaza)
    own_prefix = lambda agent_id: f"{AGENT_NAMESPACE}.{digest(str(agent_id))[:16]}"
    for agent_id in authority.advertisers(ref):
        with authority.agent_lock("contracts:" + agent_id):
            if _held(registry, ref):
                return True
            try:
                agent, rows = authority.fetch(agent_id, ref)
            except PermissionError as exc:
                LOGGER.info("Plaza could not read agent %s's contracts: %s", agent_id, exc)
                continue
            for row in rows:
                try:
                    value = registry.validate(row)
                    if (value.get("resource_type") != "operation-contract" or value["publisher"]["agentId"] != agent_id
                            or not namespace_allowed(registry, value["namespace"], agent_id, agent["owner"])):
                        continue
                    own = own_prefix(agent_id)
                    if value["namespace"] == own or value["namespace"].startswith(own + "."):
                        registry.claim(value["namespace"], agent["owner"], admin=True)
                    registry.publish(value, agent["owner"], visibility="public")
                except (PermissionError, ValueError, TypeError, KeyError) as exc:
                    LOGGER.info("Plaza did not store a contract from agent %s: %s", agent_id, exc)
            if _held(registry, ref):
                return True
    return False


def describe_change(old, new) -> Optional[str]:
    """A short note when a registration changes its native authority's key, path, decider or contracts."""
    before, after = card_authority(old), card_authority(new)
    if before == after:
        return None
    if not before or not after:
        return "native authority " + ("added" if after else "removed")
    notes = []
    for key, label in (("keyId", "key"), ("path", "path"), ("decidedBy", "decided by"), ("contracts", "contracts path")):
        if before.get(key) != after.get(key):
            notes.append(f"{label} {before.get(key) or '-'} -> {after.get(key) or '-'}")
    old_refs = {canonical(r) for r in before["operations"]}
    new_refs = {canonical(r) for r in after["operations"]}
    if old_refs != new_refs:
        notes.append(f"operations {len(new_refs - old_refs)} new, {len(old_refs - new_refs)} withdrawn")
    if before.get("publicKey") != after.get("publicKey") and before.get("keyId") == after.get("keyId"):
        notes.append("public key changed")
    return "native authority changed: " + "; ".join(notes) if notes else None


def mount_acl(app, path: str, *, check: PlazaRequestCheck, private_key, publisher: Callable[[], str], decide,
              name: str = "native_authority"):
    """Mount an agent's operation ACL at `path`: Plaza's signed request in, the agent's signed decision out.

    `decide(context)` is the owning agent's own check; it returns {'scopes', 'downstream'} or raises. Nothing else
    can ask: the request must carry Plaza's HMAC with the agent's current token, once.
    """
    from starlette.concurrency import run_in_threadpool
    from .read_chain_contracts import parse_json

    from fastapi.responses import JSONResponse
    private = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Authorization", "X-Content-Type-Options": "nosniff"}

    def refuse(status, detail):
        return HTTPException(status, detail, headers=private)

    async def native_authority(request: Request):
        raw = await request.body()
        if len(raw) > MAX_ANSWER_BYTES:
            raise refuse(413, "The native authority context exceeds its bounds.")
        try:
            check.verify("POST", request.url.path, request.headers, raw)
        except PermissionError:
            raise refuse(401, "Plaza's signed request is required.") from None
        try:
            packet = parse_json(raw)
        except (ValueError, PermissionError):
            packet = None
        if not isinstance(packet, dict) or set(packet) != {"context", "challenge"} or not isinstance(packet["challenge"], str):
            raise refuse(422, "A native authority challenge is required.")
        try:
            decision = await run_in_threadpool(decide, packet["context"])
        except Exception:  # any failure of the owner's own check refuses
            raise refuse(403, "The owning agent refused this operation for this account.") from None
        if not isinstance(decision, dict) or decision.get("authorized") is False:
            raise refuse(403, "The owning agent refused this operation for this account.")
        # The answer names the operation's publisher: this agent, or a same-owner agent it decides for (decidedBy),
        # whose operations `decide` alone admits.
        operation = packet["context"].get("operation") if isinstance(packet["context"], dict) else None
        named = ((operation or {}).get("publisher") or {}).get("agentId") if isinstance(operation, dict) else None
        return JSONResponse(entitlement(private_key, str(named or publisher()), packet["challenge"], packet["context"],
                                        decision), headers=private)

    app.add_api_route(path, native_authority, methods=["POST"], name=name)
    return native_authority


def offer(app, card, *, path: str, key, token: Callable[[], Optional[str]], agent_id: Callable[[], Optional[str]],
          decide, operations, contracts: Optional[str] = None, part: str = "default", listed: bool = True) -> dict:
    """Offer `operations` (contract documents), decided by `decide`, through the agent's one native authority.

    An agent registers one ACL path and key. Several parts of one agent may offer operations: the first call
    mounts the ACL at `path` and the contracts at `contracts` (default `path + '/contracts'`); each `part` is
    asked only for its own operations, and calling again for a part replaces them. The card's
    `meta.native_authority` lists every part's operations, except a part with `listed=False`: operations of a
    same-owner agent this one decides for, which that agent's own card offers with `decidedBy`.
    """
    offers = app.state.__dict__.setdefault("native_authority_offers", {})
    entry = offers.get("current")
    if entry is None:
        entry = offers["current"] = {"path": path, "key": key, "parts": {}, "unlisted": set(),
                                     "contracts": contracts or path.rstrip("/") + "/contracts"}

        def combined(context):
            ref = context["operation"]["ref"]
            for refs, decider, _ in list(entry["parts"].values()):
                if ref in refs:
                    return decider(context)
            raise PermissionError("This agent does not decide that operation.")

        mount_acl(app, path, check=PlazaRequestCheck(token, agent_id), private_key=key,
                  publisher=lambda: str(agent_id() or ""), decide=combined)

        async def served_contracts():
            return {"operations": [op for name, (_, _, documents) in list(entry["parts"].items())
                                   if name not in entry["unlisted"] for op in documents]}
        app.add_api_route(entry["contracts"], served_contracts, methods=["GET"])
    elif public_key_bytes(key) != public_key_bytes(entry["key"]):
        LOGGER.warning("A second native authority key on one agent is ignored; its first key signs every operation.")
    documents = [deepcopy(op) for op in operations]
    entry["parts"][part] = ([reference(op) for op in documents], decide, documents)
    (entry["unlisted"].discard if listed else entry["unlisted"].add)(part)
    refs = []
    for name, (_, _, docs) in entry["parts"].items():
        if name not in entry["unlisted"]:
            refs += [reference(op) for op in docs if reference(op) not in refs]
    field = card_field(operations=refs[:MAX_OPERATIONS], contracts=entry["contracts"], path=entry["path"],
                       private_key=entry["key"])
    if isinstance(card, dict):
        card.setdefault("meta", {})["native_authority"] = field
    return field
