"""Plaza's proof that a registered agent answers at the address it registered.

The address in a registration is a claim. A loopback address means "this machine" only where Plaza runs, and an
agent registered through a public edge can name any loopback port. So Plaza asks the claimed address for a proof
bound to the agent's current Plaza token and records the answer. Only the process that holds the token and listens
at the address can give it. An edge that routes requests to registered agents routes only to verified addresses.

Agents answer at PATH; prompits.agents.base.BaseAgent does this for every agent built on it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx

SCHEMA = "plaza-address-proof/1"
PATH = "/.well-known/prompits-address"
# Seconds before each try: the agent stores its token only once Plaza's registration answer reaches it.
RETRY_DELAYS = (1.0, 3.0, 10.0, 30.0)
MAX_ANSWER_BYTES = 4096
# A failed check is tried again on the agent's heartbeats, at most this often: an agent still starting, or a Plaza
# busy at the time, would otherwise stay unverified until it registered again.
RECHECK_SEC = 60.0


def origin_of(address: str) -> str:
    return str(address or "").strip().rstrip("/")


def proof(token: str, agent_id: str, origin: str, probe: str) -> str:
    """The answer for one probe: an HMAC keyed by the agent's current Plaza token over its id, origin and the probe."""
    message = "\n".join((SCHEMA, agent_id, origin_of(origin), probe)).encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()


def valid_probe(value: object) -> bool:
    return (isinstance(value, str) and 16 <= len(value) <= 128 and value.isascii()
            and all(character.isalnum() or character in "-_" for character in value))


def answer(token: Optional[str], own_agent_id: Optional[str], own_origin: str, agent_id: str, probe: str) -> Optional[dict]:
    """An agent's reply to Plaza's probe, or None when it is not the agent asked about or holds no token."""
    if not token or not own_agent_id or agent_id != own_agent_id or not valid_probe(probe):
        return None
    return {"schema": SCHEMA, "agent_id": agent_id, "proof": proof(token, agent_id, own_origin, probe)}


def checkable(origin: str) -> bool:
    try:
        url = urlsplit(origin)
        return (url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
                and url.path in ("", "/") and not url.query and not url.fragment)
    except ValueError:
        return False


class AddressProofs:
    """Plaza's records of which agents answered at their registered address.

    `token_for(agent_id)` returns the agent's current Plaza token, or None. Records live in memory: after a Plaza
    restart every agent registers again and is checked again.
    """

    def __init__(self, token_for: Callable[[str], Optional[str]], *, delays=RETRY_DELAYS, timeout: float = 3.0,
                 client_factory: Optional[Callable[[], httpx.Client]] = None, clock=time.time, sleep=time.sleep,
                 recheck_after: float = RECHECK_SEC):
        self.token_for = token_for
        self.delays = tuple(delays)
        self.recheck_after = recheck_after
        self.timeout = timeout
        self.client_factory = client_factory or (lambda: httpx.Client(trust_env=False, follow_redirects=False,
                                                                      timeout=self.timeout))
        self.clock, self.sleep = clock, sleep
        self.records: Dict[str, dict] = {}
        self.generations: Dict[str, int] = {}
        self.lock = threading.Lock()

    def view(self, agent_id: str, address: str) -> dict:
        """Plaza's current answer for one directory row, always rebuilt here; never a publisher's own claim."""
        origin = origin_of(address)
        record = self.records.get(agent_id) or {}
        verified = bool(record.get("verified") and record.get("origin") == origin and origin)
        return {"schema": SCHEMA, "agent": agent_id, "origin": origin, "verified": verified,
                "verifiedAt": record.get("verifiedAt") if verified else None,
                "reason": "" if verified else str(record.get("reason") or "unchecked")}

    def forget(self, agent_id: str) -> None:
        with self.lock:
            self.records.pop(agent_id, None)
            self.generations[agent_id] = self.generations.get(agent_id, 0) + 1

    def check(self, agent_id: str, address: str, *, inbound: bool, wait: bool = False) -> None:
        """Start checking `address` for `agent_id`. A proof already given for the same address stays until a check
        fails; a new address starts unverified. Outbound-only agents take no inbound calls and are never checked."""
        origin = origin_of(address)
        with self.lock:
            generation = self.generations.get(agent_id, 0) + 1
            self.generations[agent_id] = generation
            previous = self.records.get(agent_id) or {}
            kept = previous if previous.get("origin") == origin and previous.get("verified") else None
            if not agent_id or not inbound or not checkable(origin):
                self.records[agent_id] = {"origin": origin, "verified": False,
                                          "reason": "outbound-only" if not inbound else "invalid-address"}
                return
            self.records[agent_id] = kept or {"origin": origin, "verified": False, "reason": "checking"}
        if wait:
            self._run(agent_id, origin, generation)
        else:
            threading.Thread(target=self._run, args=(agent_id, origin, generation), daemon=True,
                             name="plaza-address-proof").start()

    def recheck(self, agent_id: str, address: str, *, inbound: bool) -> bool:
        """Check again an address whose last check failed, at most once per `recheck_after` (called on heartbeats).
        Verified addresses, checks in progress, and outbound-only or unusable addresses are left alone."""
        with self.lock:
            record = self.records.get(agent_id) or {}
            if (not agent_id or not inbound or record.get("verified") or record.get("reason") in ("checking", "outbound-only", "invalid-address")
                    or record.get("origin") not in (None, origin_of(address))
                    or self.clock() - float(record.get("checkedAt") or 0) < self.recheck_after):
                return False
        self.check(agent_id, address, inbound=inbound)
        return True

    def prove_now(self, agent_id: str, address: str) -> bool:
        """Check `address` once, now, for a caller that needs the proof at once (native_authority.py): after a Plaza
        restart, proofs are gone until the background check or the agent's next renewal runs again."""
        origin = origin_of(address)
        if not agent_id or not checkable(origin):
            return False
        with self.lock:
            generation = self.generations.get(agent_id, 0)
        ok, reason = self._probe(agent_id, origin)
        with self.lock:
            if self._current(agent_id, generation) and ok:
                self.records[agent_id] = {"origin": origin, "verified": True, "verifiedAt": self.clock(), "reason": ""}
        return ok

    def _current(self, agent_id: str, generation: int) -> bool:
        return self.generations.get(agent_id) == generation

    def _run(self, agent_id: str, origin: str, generation: int) -> None:
        reason = "unchecked"
        for delay in self.delays:
            if delay:
                self.sleep(delay)
            if not self._current(agent_id, generation):
                return
            ok, reason = self._probe(agent_id, origin)
            if ok:
                with self.lock:
                    if self._current(agent_id, generation):
                        self.records[agent_id] = {"origin": origin, "verified": True, "verifiedAt": self.clock(),
                                                  "reason": ""}
                return
        with self.lock:
            if self._current(agent_id, generation):
                self.records[agent_id] = {"origin": origin, "verified": False, "reason": reason, "checkedAt": self.clock()}

    def _probe(self, agent_id: str, origin: str):
        token = self.token_for(agent_id)
        if not token:
            return False, "no-token"
        probe = secrets.token_urlsafe(24)
        try:
            with self.client_factory() as client:
                response = client.get(origin + PATH, params={"agent_id": agent_id, "probe": probe})
        except httpx.HTTPError:
            return False, "unreachable"
        if response.status_code != 200 or len(response.content) > MAX_ANSWER_BYTES:
            return False, "no-proof"
        try:
            data = response.json()
        except ValueError:
            return False, "no-proof"
        expected = proof(token, agent_id, origin, probe)
        given = data.get("proof") if isinstance(data, dict) else None
        ok = (isinstance(data, dict) and data.get("schema") == SCHEMA and data.get("agent_id") == agent_id
              and isinstance(given, str) and hmac.compare_digest(given, expected))
        return (True, "") if ok else (False, "wrong-proof")
