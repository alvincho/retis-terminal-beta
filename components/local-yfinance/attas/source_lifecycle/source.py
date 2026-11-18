"""Authenticated source observation and drain, disabled unless explicitly configured."""
from __future__ import annotations

from dataclasses import asdict
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from prompits.core.file_lock import try_exclusive  # fcntl alone has no Windows build
from .archive import digest

PREFIX = "/internal/source-lifecycle"


def instance_lock(source):
    """File-backed launchers share a lifetime lock, including direct launches."""
    config_path = getattr(source, "config_path", None) or os.environ.get("PROMPITS_AGENT_CONFIG")
    if not config_path:
        return None
    path = Path(config_path).resolve()
    lock_path = path.with_suffix(path.suffix + ".instance.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock = os.fdopen(fd, "r+")
    try:
        try_exclusive(lock.fileno())
    except BlockingIOError as error:
        lock.close()
        raise RuntimeError("This source configuration already has a running instance.") from error
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def authorize(request, token):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not token or not hmac.compare_digest(supplied, token):
        raise HTTPException(403, "Lifecycle authentication required.")


def credential_state(source, env_names):
    """Any configured value retains the source; read failures stay unknown."""
    environment = "configured" if any(str(os.environ.get(name, "")).strip() for name in env_names) else "absent"
    store = getattr(source, "_credential_store", None)
    try:
        settings = "configured" if store and store.connection_metadata(source.vendor_id) else "absent"
        raw = getattr(source, "_config_data", {})
        if source.vendor_id == "bloomberg" and (raw.get("bloomberg") or getattr(source, "_bloomberg_client", None) or importlib.util.find_spec("blpapi")):
            settings = "configured"
        enterprise = raw.get("enterprise_delivery") or {}
        if ((raw.get("enterprise_stream") or {}).get("driver") or enterprise.get("driver")
                or enterprise.get("stream_driver") or enterprise.get("datasets")
                or enterprise.get("bloomberg")):
            # Owner-selected SDK adapters can carry session authentication
            # outside the encrypted API-key store. Never infer absence there.
            settings = "unknown" if settings == "absent" else settings
    except Exception:
        settings = "unknown"
    state = "configured" if "configured" in (settings, environment) else "unknown" if "unknown" in (settings, environment) else "absent"
    return {"state": state, "settings": settings, "environment": environment}


def pending_jobs(source):
    """Persisted asynchronous receipts include unresolved submission outcomes."""
    workflow = getattr(source, "_workflow", None)
    if workflow is None:
        return 0
    if not workflow.lock.acquire(blocking=False):
        return None
    try:
        rows = workflow.db.execute("SELECT payload FROM receipts").fetchall()
        return sum(json.loads(workflow.cipher.decrypt(row[0])).get("state") not in {"completed", "rejected", "cancelled"} for row in rows)
    except Exception:
        return None
    finally:
        workflow.lock.release()


class DrainMiddleware:
    def __init__(self, app, observer):
        self.app, self.observer = app, observer

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or scope.get("path", "").startswith(PREFIX) or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        if self.observer.drained_until > time.monotonic():
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1013})
            return await JSONResponse({"detail": "Source is draining; retry shortly."}, 503)(scope, receive, send)
        self.observer.active += 1
        try:
            # The count spans the complete response, including SSE and cleanup.
            await self.app(scope, receive, send)
        finally:
            self.observer.active -= 1


class SourceObserver:
    def __init__(self, source, config, token):
        self.source, self.config, self.token = source, config, token
        self.active = 0
        self.drained_until = 0
        self.ticket = ""

    def catalog(self):
        source = self.source
        if source.spec_snapshot is None:
            raise HTTPException(409, "No specification snapshot is available.")
        snapshot = asdict(source.spec_snapshot)
        endpoints = list(source.spec_snapshot.endpoints)
        known = {endpoint["endpoint_id"] for endpoint in snapshot["endpoints"]}
        for entry in source.data_catalog:
            if entry["dataset_id"] not in known:
                endpoint = source.resolve_endpoint(endpoint_id=entry["dataset_id"])
                snapshot["endpoints"] = (*snapshot["endpoints"], asdict(endpoint))
                endpoints.append(endpoint)
                known.add(entry["dataset_id"])
        payload = {"source_id": source.vendor_id, "source": source._public_source(),
                   "snapshot": snapshot,
                   "datasets": [source._catalog_sync_entry(item) for item in source.data_catalog],
                   "endpoint_records": [source._endpoint_payload(endpoint) for endpoint in endpoints],
                   "additional_endpoint_records": getattr(source, "additional_catalog_endpoints", lambda: [])(),
                   "endpoint_count": len(source.data_catalog),
                   "catalog_fingerprint": source._catalog_fingerprint(),
                   "card": source.agent_card}
        # JSON-normalize tuples before hashing and archive validation.
        payload = json.loads(json.dumps(payload, default=str))
        return {"catalog": payload, "revision": digest(payload)}

    def status(self):
        return {"release": self.config["release"], "source_id": self.source.vendor_id,
                "pid": os.getpid(), "active_requests": self.active,
                "pending_jobs": pending_jobs(self.source),
                "credentials": credential_state(self.source, self.config["credential_env"]),
                "draining": self.drained_until > time.monotonic()}


def install_source_lifecycle(source):
    config = source._config_data.get("lifecycle")
    if not config:
        return
    if not config.get("release") or not isinstance(config.get("credential_env"), list):
        raise ValueError("Explicit lifecycle ownership and credential environment inventory are required.")
    token = os.environ.get("ATTAS_LIFECYCLE_TOKEN", "")
    if len(token) < 32:
        raise ValueError("Lifecycle token is missing.")
    observer = SourceObserver(source, config, token)
    observer.instance_lock = instance_lock(source)
    if observer.instance_lock is not None:
        source.app.add_event_handler("shutdown", observer.instance_lock.close)
    source.lifecycle_observer = observer
    source.app.add_middleware(DrainMiddleware, observer=observer)

    @source.app.get(PREFIX + "/status")
    async def status(request: Request):
        authorize(request, token)
        return observer.status()

    @source.app.get(PREFIX + "/catalog")
    async def catalog(request: Request):
        authorize(request, token)
        return observer.catalog()

    @source.app.post(PREFIX + "/drain")
    async def drain(request: Request):
        authorize(request, token)
        state = observer.status()
        if state["active_requests"] != 0 or state["pending_jobs"] != 0:
            raise HTTPException(409, "Source is busy or job state is unknown.")
        if config.get("policy") != "public_on_demand" and state["credentials"]["state"] != "absent":
            raise HTTPException(409, "Source credentials are configured or unknown.")
        if config.get("policy") == "always":
            raise HTTPException(409, "This source must remain running.")
        observer.ticket = secrets.token_hex(24)
        observer.drained_until = time.monotonic() + 45
        return {**observer.status(), **observer.catalog(), "ticket": observer.ticket}

    @source.app.post(PREFIX + "/resume")
    async def resume(request: Request):
        authorize(request, token)
        observer.drained_until = 0
        observer.ticket = ""
        return observer.status()
