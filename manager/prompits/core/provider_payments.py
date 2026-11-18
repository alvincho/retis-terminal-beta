"""Opt-in token-free provider payments with durable execution receipts.

The executing owner supplies quote/resource authorization. Hashes only bind its
decision to exact input; they never prove a quote's issuer, price or entitlement.
"""
import copy
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote as urlquote

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from .call_grants import CURRENT
from .capabilities import canonical, digest
from .resource_registry import validate_ref

PROTOCOL = "agent-payment/1"


def execution_input(content):
    if isinstance(content, dict):
        return {k: copy.deepcopy(v) for k, v in content.items() if k not in ("_billing", "_progress")}
    return copy.deepcopy(content)


class ProviderReceipts:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_payment_intents(
                id TEXT PRIMARY KEY, principal TEXT, actor TEXT, intent TEXT, fingerprint TEXT,
                payload TEXT, state TEXT, start_revision INTEGER, complete_revision INTEGER,
                result TEXT, result_hash TEXT, updated REAL, UNIQUE(principal,actor,intent))""")
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def begin(self, scope_id, identity):
        fingerprint = digest(identity)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM provider_payment_intents WHERE id=? OR (principal=? AND actor=? AND intent=?)",
                             (scope_id, identity["principal"], identity["actor"], identity["intent"])).fetchone()
            if row:
                if row["id"] != scope_id or row["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Provider intent was reused with different scope, authority or input.")
                return dict(row), False
            db.execute("INSERT INTO provider_payment_intents VALUES(?,?,?,?,?,?,?,NULL,NULL,'','',?)",
                       (scope_id, identity["principal"], identity["actor"], identity["intent"], fingerprint,
                        canonical(identity), "preparing", time.time()))
            return dict(db.execute("SELECT * FROM provider_payment_intents WHERE id=?", (scope_id,)).fetchone()), True

    def read(self, scope_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM provider_payment_intents WHERE id=?", (scope_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Provider intent receipt unavailable.")
        return dict(row)

    def update(self, scope_id, **values):
        allowed = {"state", "start_revision", "complete_revision", "result", "result_hash"}
        if set(values) - allowed:
            raise ValueError("Unknown provider receipt fields.")
        values["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE provider_payment_intents SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                       (*values.values(), scope_id))


class ProviderPaymentAdapter:
    def __init__(self, agent, path, authorizer):
        if not callable(authorizer):
            raise HTTPException(503, "Provider quote/resource authorizer is not configured.")
        self.agent, self.receipts, self.authorizer = agent, ProviderReceipts(path), authorizer

    async def _call(self, method, path, *, plaza_url, body=None):
        kwargs = {"plaza_url": plaza_url, "headers": self.agent._ensure_token_valid(), "timeout": 15, "retries": 0}
        if body is not None:
            kwargs["json"] = body
        try:
            response = await self.agent._plaza_request_async(method, path, **kwargs)
        except Exception as exc:
            raise HTTPException(409, "Plaza payment acknowledgement is uncertain; observe status before retrying.") from exc
        if response.status_code >= 400:
            raise HTTPException(response.status_code, "Plaza provider payment requires authorized reconciliation.")
        return response.json()

    async def _authority(self, request, practice_id, amount, scope_id, plaza_url, *, phase="authorize"):
        grant = CURRENT.get()
        transport = (grant or {}).get("transport", {})
        authority = (grant or {}).get("authority", {})
        billing = request.billing or {}
        agent_id = str(self.agent.agent_id)
        operation = self.agent._request_content_value(request.content, "pulse_name") or practice_id
        if (not grant or grant.get("audience") != agent_id or grant.get("operation") != operation
                or grant.get("binding") != transport.get("binding")
                or transport.get("method") != "POST" or transport.get("path") != "/use_practice/" + practice_id
                or authority.get("schema") != "operation-call/1"
                or authority.get("intentId") != billing.get("intent_id")
                or authority.get("operationRef") != billing.get("operation_ref")
                or authority.get("resourceRefs") != billing.get("resource_refs", [])):
            raise HTTPException(403, "Provider payment requires an exact consumed operation grant.")
        validate_ref(authority["operationRef"])
        user_id = str((grant.get("user") or {}).get("id") or "")
        if not user_id:
            raise HTTPException(403, "Provider payment requires the original verified user.")
        status = await self._call("get", "/api/payments/scopes/" + urlquote(scope_id, safe=""), plaza_url=plaza_url)
        scope = status.get("scope", {})
        if (scope.get("provider_agent_id") != agent_id or status.get("consumer_user_id") != user_id
                or scope.get("controller_agent_id") != grant.get("rootActor")
                or scope.get("practice_id") != practice_id
                or scope.get("pulse_name", "") != self.agent._request_content_value(request.content, "pulse_name")):
            raise HTTPException(403, "Payment scope differs from the original user, controller or operation.")
        content = execution_input(request.content)
        if isinstance(content, dict) and ("_caller" in content or any(
                isinstance(content.get(key), dict) and "_caller" in content[key] for key in ("params", "input_data"))):
            raise HTTPException(403, "Caller authority cannot be supplied inside operation input.")
        check = {"principal": copy.deepcopy(grant["user"]), "serviceActorId": grant["actor"],
            "controllerAgentId": grant["rootActor"], "authority": copy.deepcopy(authority),
            "scope": copy.deepcopy(scope), "practiceId": practice_id, "input": content,
            "advertisedCost": amount, "phase": phase}
        decision = (await self.authorizer(check) if inspect.iscoroutinefunction(self.authorizer)
                    else await run_in_threadpool(self.authorizer, check))
        if inspect.isawaitable(decision):
            decision = await decision
        input_digest = digest(content)
        quoted = scope.get("quote", {})
        # The callback must consult the owner's issuance/price/ACL state itself.
        # An echoed digest or a caller-supplied quote is insufficient authority.
        if (not isinstance(decision, dict) or decision.get("authorized") is not True
                or decision.get("issuerAgentId") != agent_id or decision.get("quoteId") != scope.get("quote_id")
                or type(decision.get("points")) is not int or decision["points"] != scope.get("max_points")
                or type(authority.get("maxPoints")) is not int
                or decision["points"] > authority["maxPoints"]
                or decision["points"] > 0 and authority.get("effects", {}).get("paidExecution") is not True
                or decision.get("inputDigest") != input_digest
                or decision.get("operationRef") != authority["operationRef"]
                or decision.get("resourceRefs") != authority["resourceRefs"]
                or not set(decision.get("verified", [])).issuperset({"issuer", "price", "parameters", "expiry", "resource-acl"})):
            raise HTTPException(403, "The executing owner has not verified this exact quote and resource authority.")
        try:
            expiry = datetime.fromisoformat(decision["validUntil"].replace("Z", "+00:00"))
            if (expiry.tzinfo is None or phase == "authorize" and expiry <= datetime.now(timezone.utc)
                    or decision["validUntil"] != quoted.get("valid_until")):
                raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(403, "Provider-issued quote is missing, expired or differs from the scope.") from exc
        identity = {"principal": user_id, "actor": grant["rootActor"], "serviceActor": grant["actor"],
                    "intent": billing["intent_id"], "scope": scope_id, "quote": scope["quote_id"],
                    "provider": agent_id, "operation": operation, "operationRef": authority["operationRef"],
                    "resourceRefs": authority["resourceRefs"], "inputDigest": input_digest,
                    "points": decision["points"], "maxPoints": authority["maxPoints"], "plaza": plaza_url}
        return status, identity

    async def prepare(self, *, request, practice_id, amount, plaza_url):
        billing = request.billing or {}
        scope_id = billing.get("scope_id")
        if (billing.get("protocol") != PROTOCOL or not isinstance(scope_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", scope_id)
                or not isinstance(billing.get("intent_id"), str) or not 1 <= len(billing["intent_id"]) <= 200):
            raise HTTPException(422, "A stable generic payment scope and intent are required.")
        try:
            await run_in_threadpool(self.receipts.read, scope_id)
            phase = "reconcile"
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            phase = "authorize"
        status, identity = await self._authority(request, practice_id, amount, scope_id, plaza_url, phase=phase)
        receipt, fresh = await run_in_threadpool(self.receipts.begin, scope_id, identity)
        if not fresh:
            if receipt["result_hash"] and status["scope"].get("status") == "paid" and status["scope"].get("result_hash") == receipt["result_hash"]:
                await run_in_threadpool(self.receipts.update, scope_id, state="settled")
                return {"protocol": PROTOCOL, "scope_id": scope_id, "plaza_url": plaza_url,
                        "replayed": True, "replay_result": json.loads(receipt["result"])}
            raise HTTPException(409, "Provider intent is running or uncertain; reconcile without executing it again.")
        if status["scope"].get("status") != "authorized":
            await run_in_threadpool(self.receipts.update, scope_id, state="uncertain")
            raise HTTPException(409, "Payment execution already started; no provider replay is permitted.")
        revision = status["revision"]
        await run_in_threadpool(self.receipts.update, scope_id, start_revision=revision)
        try:
            started = await self._call("post", "/api/payments/scopes/" + urlquote(scope_id, safe="") + "/verify",
                                       plaza_url=plaza_url, body={"expected_revision": revision})
            if started.get("execution_allowed") is not True or started.get("execution_id") != scope_id:
                raise HTTPException(409, "Plaza did not grant a new provider execution.")
            await run_in_threadpool(self.receipts.update, scope_id, state="started", complete_revision=started["revision"])
        except BaseException:
            await run_in_threadpool(self.receipts.update, scope_id, state="uncertain")
            raise
        return {"protocol": PROTOCOL, "scope_id": scope_id, "plaza_url": plaza_url, "replayed": False}

    async def complete(self, context, result):
        scope_id, plaza_url = context["scope_id"], context["plaza_url"]
        if context.get("replayed"):
            await self.validate_before_execution(context)
            return {"status": "already_settled", "execution_replay_allowed": False}
        receipt = await run_in_threadpool(self.receipts.read, scope_id)
        if receipt["state"] != "started":
            raise HTTPException(409, "Provider receipt does not permit another completion.")
        payload = canonical(result)
        if len(payload.encode()) > 4_000_000:
            raise HTTPException(413, "Provider result exceeds durable receipt limit.")
        result_hash = digest(result)
        await run_in_threadpool(self.receipts.update, scope_id, state="awaiting-settlement", result=payload, result_hash=result_hash)
        try:
            response = await self._call("post", "/api/payments/scopes/" + urlquote(scope_id, safe="") + "/complete",
                plaza_url=plaza_url, body={"expected_revision": receipt["complete_revision"], "result_hash": result_hash})
            if response.get("scope", {}).get("status") != "paid" or response["scope"].get("result_hash") != result_hash:
                raise HTTPException(409, "Plaza has not confirmed the exact provider result.")
            await run_in_threadpool(self.receipts.update, scope_id, state="settled")
        except BaseException:
            await run_in_threadpool(self.receipts.update, scope_id, state="uncertain")
            raise
        # Payment for an already authorized effect can finish, while delivery
        # of protected results still requires the current original-user grant.
        await self.validate_before_execution(context)
        return response

    async def validate_before_execution(self, context):
        grant = CURRENT.get() or {}
        active = await self._call("post", "/api/call-grants/status", plaza_url=context["plaza_url"], body={"token": grant.get("token", "")})
        if active.get("active") is not True or (active.get("user") or {}).get("id") != (grant.get("user") or {}).get("id"):
            raise HTTPException(403, "Provider execution authority is no longer active.")

    async def reconcile(self, *, request, practice_id, amount, plaza_url):
        """Status-only repair after fresh exact authorization; no automatic transfer."""
        scope_id = (request.billing or {}).get("scope_id", "")
        status, identity = await self._authority(request, practice_id, amount, scope_id, plaza_url, phase="reconcile")
        receipt = await run_in_threadpool(self.receipts.read, scope_id)
        if receipt["fingerprint"] != digest(identity):
            raise HTTPException(403, "Provider receipt belongs to different authority or input.")
        confirmed = bool(receipt["result_hash"] and status["scope"].get("status") == "paid"
                         and status["scope"].get("result_hash") == receipt["result_hash"])
        if confirmed:
            await run_in_threadpool(self.receipts.update, scope_id, state="settled")
        await self.validate_before_execution({"scope_id": scope_id, "plaza_url": plaza_url})
        return {"scope_id": scope_id, "state": "settled" if confirmed else "uncertain",
                "execution_replay_allowed": False, "result_available": confirmed}
