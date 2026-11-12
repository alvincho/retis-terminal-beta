"""Stable background Plaza registration and reconnect lifecycle for any Phemar.

Each agent supplies its own Plaza card through ``card_factory`` and its own name;
this module carries only the generic registration, credential and heartbeat lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx


LOGGER = logging.getLogger(__name__)
AGENT_NAME = "Phemar"
BACKOFF_SECONDS = (1, 2, 4, 8, 15, 30)
# A Plaza that has just started can refuse a saved identity for a short while and then accept it,
# as tw-001 did each time it was turned on (2026-11-04). Only a refusal that lasts longer needs repair.
REJECTION_GRACE_SECONDS = 300
PORTABLE_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")


class CredentialError(RuntimeError):
    """Saved Plaza credentials exist but cannot be safely reused."""


@dataclass(frozen=True)
class Credentials:
    agent_id: str
    api_key: str


def valid_portable_id(value: object) -> bool:
    return isinstance(value, str) and PORTABLE_ID.fullmatch(value) is not None


class CredentialStore:
    """Atomically persist only the stable Plaza identity, never bearer tokens."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Credentials | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CredentialError("Saved Plaza credentials are unreadable or malformed.") from exc
        if not isinstance(raw, dict):
            raise CredentialError("Saved Plaza credentials must be a JSON object.")
        agent_id = raw.get("agent_id")
        api_key = raw.get("api_key")
        if not valid_portable_id(agent_id) or not isinstance(api_key, str) or not api_key.strip():
            raise CredentialError("Saved Plaza credentials require a portable agent_id and nonblank api_key.")
        return Credentials(agent_id, api_key.strip())

    def save(self, credentials: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        payload = json.dumps(
            {"agent_id": credentials.agent_id, "api_key": credentials.api_key},
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(self.path.parent, flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()


class PlazaClient:
    """Maintain one reusable Phemar identity across reconnects.

    The agent names itself (``agent_name``) and builds its own Plaza card
    (``card_factory``), so every Phemar shares the same hardened reconnect and
    credential lifecycle under its own identity. A subclass may set
    ``default_agent_name`` for callers that leave the name out.
    """

    default_agent_name = AGENT_NAME

    def __init__(
        self,
        plaza_url: str,
        public_url: str,
        credential_path: Path,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
        agent_name: str | None = None,
        card_factory: Callable[[str], dict[str, Any]],
    ):
        self.plaza_url = str(plaza_url).rstrip("/")
        self.public_url = str(public_url).rstrip("/")
        self.credentials = CredentialStore(credential_path)
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(base_url=self.plaza_url, timeout=httpx.Timeout(5.0))
        )
        self._clock = clock
        default_name = self.default_agent_name
        self.agent_name = str(agent_name or default_name).strip() or default_name
        self._card_factory = card_factory
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._token: str | None = None
        self._token_deadline = 0.0
        self._agent_id: str | None = None
        self._pending_credentials: Credentials | None = None
        self._last_result = "idle"
        self._rejected_since: float | None = None
        try:
            saved = self.credentials.load()
        except CredentialError:
            saved = None
            self._last_result = "configuration_error"
        if saved is not None:
            self._agent_id = saved.agent_id
        from prompits.core.asgi_observability import ASGIObservability
        self.telemetry = ASGIObservability(self)

    @property
    def registered(self) -> bool:
        return bool(self._token and self._clock() < self._token_deadline)

    @property
    def agent_id(self) -> str | None:
        return self._agent_id

    @property
    def state(self) -> str:
        return "registered" if self.registered else self._last_result

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = self._client_factory()
        task_name = re.sub(r"[^a-z0-9]+", "-", self.agent_name.casefold()).strip("-")
        self._task = asyncio.create_task(
            self._run(),
            name=f"{task_name or 'phemar'}-plaza-lifecycle",
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def forget(self) -> bool:
        """Leave Plaza's directory for good: stop heartbeating, then deregister with this agent's own token.

        Stopping first matters, because Plaza asks a heartbeating agent it no longer knows to register again.
        False when Plaza could not be told; it then retires the silent entry by itself.
        """
        token = self._token
        await self.stop()
        self._token = None
        if not token:
            return False
        client = self._client_factory()
        try:
            response = await client.post("/deregister", headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError:
            return False
        finally:
            await client.aclose()
        return response.status_code == 200

    def _registration_payload(self, saved: Credentials | None) -> dict:
        payload = {
            "agent_name": self.agent_name,
            "address": self.public_url,
            "expires_in": 3600,
            "pit_type": "Phemar",
            "accepts_inbound_from_plaza": True,
            "card": self._card_factory(self.public_url),
        }
        if saved is not None:
            payload.update({"agent_id": saved.agent_id, "api_key": saved.api_key})
        owner_key = (os.environ.get("PROMPITS_PLAZA_OWNER_KEY") or os.environ.get("PLAZA_OWNER_KEY") or "").strip()
        if owner_key:
            # Card owner fields are descriptive; the operator's key lets Plaza verify the owner,
            # so the owner's own agents trust this one without any trust-list entry.
            payload["owner_key"] = owner_key
        return payload

    @staticmethod
    def _positive_integer(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    async def register_once(self) -> str:
        """Register or reconnect once and classify retryable failures."""
        assert self._client is not None
        try:
            saved = self.credentials.load()
        except CredentialError as exc:
            LOGGER.error("Plaza registration stopped: %s", exc)
            self._last_result = "configuration_error"
            return self._last_result

        if self._pending_credentials is not None:
            if saved is not None and saved != self._pending_credentials:
                LOGGER.error("Pending Plaza credentials conflict with the credential file.")
                self._last_result = "configuration_error"
                return self._last_result
            try:
                self.credentials.save(self._pending_credentials)
            except OSError as exc:
                LOGGER.error("Could not persist pending Plaza credentials: %s", type(exc).__name__)
                self._last_result = "configuration_error"
                return self._last_result
            saved = self._pending_credentials
            self._pending_credentials = None

        try:
            response = await self._client.post("/register", json=self._registration_payload(saved))
        except httpx.RequestError as exc:
            LOGGER.warning("Plaza registration unavailable: %s", type(exc).__name__)
            self._last_result = "retry"
            return self._last_result
        if response.status_code == 401 and saved is not None:
            now = self._clock()
            if self._rejected_since is None:
                self._rejected_since = now
            if now - self._rejected_since < REJECTION_GRACE_SECONDS:
                LOGGER.warning("Plaza has not accepted the saved identity yet; this is usual for a few minutes "
                               "after the Plaza starts. Trying again in about 30 seconds.")
            else:
                LOGGER.error("Plaza has rejected the saved identity for %d minutes; it was preserved for operator repair.",
                             int((now - self._rejected_since) // 60))
            self._last_result = "unauthorized"
            return self._last_result
        if response.status_code == 429 or response.status_code >= 500:
            self._last_result = "retry"
            return self._last_result
        if response.status_code != 200:
            self._last_result = "configuration_error"
            return self._last_result
        try:
            body = response.json()
        except ValueError:
            self._last_result = "configuration_error"
            return self._last_result

        token = body.get("token") if isinstance(body, dict) else None
        agent_id = body.get("agent_id") if isinstance(body, dict) else None
        api_key = body.get("api_key") if isinstance(body, dict) else None
        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        if (
            not isinstance(token, str)
            or not token.strip()
            or not valid_portable_id(agent_id)
            or not isinstance(api_key, str)
            or not api_key.strip()
            or not self._positive_integer(expires_in)
        ):
            self._last_result = "configuration_error"
            return self._last_result

        current = Credentials(agent_id, api_key.strip())
        if saved is not None and current != saved:
            LOGGER.error("Plaza attempted to replace the saved identity; the response was rejected.")
            self._last_result = "configuration_error"
            return self._last_result
        if saved is None:
            self._pending_credentials = current
            self._agent_id = current.agent_id
            try:
                self.credentials.save(current)
            except OSError as exc:
                LOGGER.error("Could not persist Plaza credentials: %s", type(exc).__name__)
                self._last_result = "configuration_error"
                return self._last_result
            self._pending_credentials = None

        self._agent_id = current.agent_id
        self._token = token.strip()
        self._token_deadline = self._clock() + expires_in
        self._last_result = "success"
        self._rejected_since = None
        LOGGER.info("Registered %s with Plaza.", self.agent_name)
        return self._last_result

    async def renew_once(self) -> str:
        assert self._client is not None and self._token is not None
        try:
            response = await self._client.post(
                "/renew",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"agent_name": self.agent_name, "expires_in": 3600},
            )
        except httpx.RequestError:
            return "retry"
        if response.status_code == 401:
            self._token = None
            return "reregister"
        if response.status_code == 429 or response.status_code >= 500:
            return "retry"
        if response.status_code != 200:
            return "configuration_error"
        try:
            body = response.json()
        except ValueError:
            return "configuration_error"
        token = body.get("token") if isinstance(body, dict) else None
        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip() or not self._positive_integer(expires_in):
            return "configuration_error"
        self._token = token.strip()
        self._token_deadline = self._clock() + expires_in
        return "success"

    async def heartbeat_once(self) -> str:
        assert self._client is not None and self._token is not None and self._agent_id is not None
        try:
            response = await self._client.post(
                "/heartbeat",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"agent_id": self._agent_id, "agent_name": self.agent_name,
                      "observability": self.telemetry.observations.snapshot()},
            )
        except httpx.RequestError:
            return "retry"
        if response.status_code == 401:
            self._token = None
            return "reregister"
        if response.status_code == 429 or response.status_code >= 500:
            return "retry"
        if response.status_code != 200:
            return "configuration_error"
        try:
            body = response.json()
        except ValueError:
            return "configuration_error"
        if not isinstance(body, dict) or body.get("status") != "ok":
            return "configuration_error"
        for diagnostic in body.get('diagnostics_requests', [])[:5]:
            try:
                await self._client.post('/api/observability/diagnostics/'+diagnostic['id']+'/complete',
                    headers={'Authorization':'Bearer '+self._token},
                    json=self.telemetry.observations.store.logs(diagnostic.get('query') or {}))
            except (httpx.HTTPError, KeyError, ValueError):
                LOGGER.warning('Diagnostic retrieval temporarily unavailable.')
        if body.get("needs_register") is True:
            self._token = None
            return "reregister"
        return "success"

    async def list_active_personas(self) -> list[dict[str, Any]]:
        """Read the public live Persona catalog without coupling it to registration state."""
        client = self._client
        owns_client = client is None
        if client is None:
            client = self._client_factory()
        try:
            response = await client.get(
                "/api/plazas_status",
                params={"pit_type": "Persona", "live_only": "1", "compact": "1"},
            )
            response.raise_for_status()
            document = response.json()
        finally:
            if owns_client:
                await client.aclose()
        if not isinstance(document, dict) or not isinstance(document.get("plazas"), list):
            raise ValueError("Plaza returned an invalid live Persona catalog.")
        rows: list[dict[str, Any]] = []
        for plaza in document["plazas"]:
            if not isinstance(plaza, dict) or not isinstance(plaza.get("agents"), list):
                continue
            for agent in plaza["agents"]:
                if isinstance(agent, dict):
                    rows.append(agent)
        return rows

    async def _sleep_backoff(self, failures: int, *, slow: bool = False) -> None:
        base = 30 if slow else BACKOFF_SECONDS[min(failures, len(BACKOFF_SECONDS) - 1)]
        await asyncio.sleep(random.uniform(base * 0.8, base * 1.2))

    async def _run(self) -> None:
        failures = 0
        while True:
            if not self.registered:
                result = await self.register_once()
                if result == "success":
                    failures = 0
                    continue
                await self._sleep_backoff(
                    failures,
                    slow=result in {"configuration_error", "unauthorized"},
                )
                failures = min(failures + 1, len(BACKOFF_SECONDS) - 1)
                continue

            if self._token_deadline - self._clock() <= 60:
                result = await self.renew_once()
            else:
                result = await self.heartbeat_once()
            self._last_result = result
            if result == "success":
                failures = 0
                await asyncio.sleep(15)
            elif result == "reregister":
                failures = 0
            else:
                await self._sleep_backoff(failures, slow=result == "configuration_error")
                failures = min(failures + 1, len(BACKOFF_SECONDS) - 1)


__all__ = [
    "AGENT_NAME",
    "BACKOFF_SECONDS",
    "REJECTION_GRACE_SECONDS",
    "CredentialError",
    "CredentialStore",
    "Credentials",
    "PlazaClient",
    "valid_portable_id",
]
