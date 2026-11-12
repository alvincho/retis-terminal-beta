"""Authenticated, product-neutral resource registry endpoints."""
import os
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .resource_registry import ResourceRegistry, MAX_BYTES


def resource_registry(plaza):
    cached = getattr(plaza, "_resource_registry", None)
    if cached is not None:
        return cached
    config = plaza.raw_config.get("plaza", {}).get("resource_registry", {})
    path = os.environ.get("PROMPITS_RESOURCE_REGISTRY_PATH") or config.get("path")
    pool = getattr(plaza, "pool", None)
    db_path = str(getattr(pool, "db_path", "") or "")
    if not path and db_path and db_path != ":memory:" and not db_path.startswith("file:"):
        path = str(Path(db_path).with_name("resources.sqlite"))
    if not path and getattr(pool, "root_path", None):
        path = str(Path(pool.root_path) / "resources.sqlite")
    if not path:
        raise HTTPException(503, "Configure persistent resource registry storage.")
    plaza._resource_registry = ResourceRegistry(path, reserved=config.get("reserved_namespaces", []),
                                               validators=getattr(plaza, "resource_validators", {}),
                                               comparators=getattr(plaza, "resource_comparators", {}))
    return plaza._resource_registry


def mount_resource_routes(plaza):
    async def body(request):
        try:
            if len(await request.body()) > MAX_BYTES:
                raise HTTPException(413, "Resource request exceeds 512 KB.")
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Provide a JSON object.") from exc

    async def perform(request, operation):
        def action():
            profile = plaza._require_ui_user(request)
            try:
                return operation(resource_registry(plaza), profile)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(404, "Resource unavailable.") from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return await run_in_threadpool(action)

    @plaza.app.get("/api/resource-namespaces")
    async def namespaces(request: Request):
        return await perform(request, lambda r, u: {"namespaces": r.namespaces()})

    @plaza.app.post("/api/resource-namespaces")
    async def claim(request: Request):
        data = await body(request)
        return await perform(request, lambda r, u: r.claim(data.get("namespace"), u["id"], admin=u.get("role") == "admin"))

    @plaza.app.post("/api/resource-namespaces/delegates")
    async def delegate(request: Request):
        data = await body(request)
        if type(data.get("enabled", True)) is not bool:
            raise HTTPException(422, "enabled must be boolean.")
        return await perform(request, lambda r, u: r.delegate(data.get("namespace"), u["id"], data.get("delegate"), enabled=data.get("enabled", True)))

    @plaza.app.get("/api/resources")
    async def search(request: Request, q: str = "", namespace: str = "", family: str = "", limit: int = 50, offset: int = 0):
        return await perform(request, lambda r, u: r.search(u["id"], query=q, namespace=namespace, family=family, limit=limit, offset=offset))

    @plaza.app.post("/api/resources")
    async def publish(request: Request):
        data = await body(request)
        def register(r, u):
            value = r.validate(data.get("resource"))
            if value["resource_type"] == "operation-contract":
                from .plaza_ownership import agent_owner
                owner = agent_owner(plaza, value["publisher"]["agentId"])
                if str(owner["id"]) != u["id"]:
                    raise PermissionError("Only the registered agent owner may publish its operation contracts.")
            return r.publish(value, u["id"], visibility=data.get("visibility", "public"), audience=data.get("audience", []))
        return await perform(request, register)

    @plaza.app.post("/api/operation-contracts/resolve")
    async def resolve(request: Request):
        data = await body(request)
        def inspect(r, u):
            from .operation_contracts import resolve_operation
            from .plaza_ownership import agent_owner
            result = resolve_operation(r, data.get("ref"), u["id"],
                                       source_mode=data.get("sourceMode", "parameters"),
                                       granted_scopes=data.get("grantedScopes", []))
            if str(agent_owner(plaza, result["publisher"]["agentId"])["id"]) != result["owner"]:
                raise HTTPException(409, "Operation publisher ownership changed; obtain a new contract publication.")
            return result
        return await perform(request, inspect)

    @plaza.app.post("/api/resource-validation")
    async def validate(request: Request):
        data = await body(request)
        def check(r, u):
            value = r.validate(data.get("resource"))
            if value.get("dependencies"):
                r.bundle(value["dependencies"], u["id"])
            return {"valid": True, "resource": value}
        return await perform(request, check)

    @plaza.app.post("/api/resource-bundles")
    async def bundle(request: Request):
        data = await body(request)
        return await perform(request, lambda r, u: r.bundle(data.get("refs"), u["id"]))

    @plaza.app.get("/api/resources/{id}/{version}")
    async def read(id: str, version: str, request: Request, digest: str = ""):
        return await perform(request, lambda r, u: r.get(id, version, u["id"], expected_digest=digest or None))

    @plaza.app.post("/api/resources/{id}/{version}/deprecate")
    async def deprecate(id: str, version: str, request: Request):
        return await perform(request, lambda r, u: r.deprecate(id, version, u["id"]))

    @plaza.app.get("/api/resource-proposals")
    async def proposals(request: Request):
        return await perform(request, lambda r, u: {"proposals": r.proposals(u["id"])})

    @plaza.app.post("/api/resource-proposals")
    async def propose(request: Request):
        data = await body(request)
        return await perform(request, lambda r, u: r.propose(data.get("resource"), u["id"]))

    @plaza.app.get("/api/resources/{id}/{version}/evidence")
    async def evidence(id: str, version: str, request: Request):
        return await perform(request, lambda r, u: {"records": r.evidence(id, version, u["id"])})

    @plaza.app.post("/api/resource-evidence")
    async def attest(request: Request):
        data = await body(request)
        return await perform(request, lambda r, u: r.attest(data, u["id"], admin=u.get("role") == "admin"))

    @plaza.app.post("/api/resource-evidence/{id}/revoke")
    async def revoke(id: str, request: Request):
        return await perform(request, lambda r, u: r.revoke_evidence(id, u["id"], admin=u.get("role") == "admin"))
