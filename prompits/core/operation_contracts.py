"""Publisher-declared operation effects, pinned contracts and verifier evidence.

This describes transport and authority, never application execution policy.
Verification is evidence about one build, not permission to invoke an operation.
"""
import copy
import re

from .capabilities import validate_definition
from .resource_registry import HASH, validate_ref

FAMILY = "operation-contract"
EFFECTS = {"writesState", "externalSend", "paidExecution", "startsJob", "opensStream"}
PATH = re.compile(r"^/[A-Za-z0-9_./{}:-]{1,255}$")


def validate_operation(value):
    if value.get('inputBinding', 'body') not in ('body', 'http-envelope'):
        raise ValueError('Operation input binding must be body or http-envelope.')
    publisher = value.get("publisher")
    if not isinstance(publisher, dict) or set(publisher) != {"agentId", "implementationDigest"}:
        raise ValueError("Operation publisher must identify its agent and implementation digest.")
    if not isinstance(publisher["agentId"], str) or not 1 <= len(publisher["agentId"]) <= 200:
        raise ValueError("Operation publisher agent identity is required.")
    if not HASH.fullmatch(str(publisher["implementationDigest"])):
        raise ValueError("Operation implementation digest must be SHA-256.")
    name = value.get("operation")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", name):
        raise ValueError("A stable operation name is required.")
    transport = value.get("transport")
    if (not isinstance(transport, dict) or set(transport) != {"method", "path"}
            or transport["method"] not in ("GET", "POST", "PATCH", "PUT", "DELETE")
            or not PATH.fullmatch(str(transport["path"])) or ".." in transport["path"]
            or transport["path"].startswith("//")):
        raise ValueError("Operation transport must be an agent-relative method and path.")
    effects = value.get("effects")
    if not isinstance(effects, dict) or set(effects) != EFFECTS or any(type(v) is not bool for v in effects.values()):
        raise ValueError("Declare every operation effect as a boolean.")
    constraints = value.get("constraints")
    if (not isinstance(constraints, dict) or set(constraints) != {"idempotency", "expectedRevision", "recovery", "statusPath"}
            or constraints["idempotency"] not in ("required", "none")
            or type(constraints["expectedRevision"]) is not bool
            or constraints["recovery"] not in ("observe-only", "resume-by-id")
            or not PATH.fullmatch(str(constraints["statusPath"]))
            or ".." in constraints["statusPath"] or constraints["statusPath"].startswith("//")):
        raise ValueError("Declare operation idempotency, revision, status and recovery constraints.")
    if any(effects.values()) and constraints["idempotency"] != "required":
        raise ValueError("Operations with effects require idempotency.")
    if effects["writesState"] and not constraints["expectedRevision"]:
        raise ValueError("State mutations require an expected revision.")
    if transport["method"] == "GET" and any(effects[k] for k in EFFECTS - {"opensStream"}):
        raise ValueError("GET operations cannot declare mutations or new executions.")
    scopes = value.get("requiredScopes")
    if (not isinstance(scopes, list) or len(scopes) > 64
            or len(set(str(x) for x in scopes)) != len(scopes)
            or any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in scopes)):
        raise ValueError("Declare bounded, unique required permission scopes.")
    if any(effects.values()) and not scopes:
        raise ValueError("Operations with effects require explicit permission scopes.")
    # Reuse the generic local-schema validation; no remote loading or execution.
    validate_definition({"id": "operation:contract", "version": value["version"],
                         "description": value.get("description"), "configurationSchema": {},
                         "inputSchema": value.get("inputSchema"), "outputSchema": value.get("outputSchema")})
    downstream = value.get("downstream", [])
    if not isinstance(downstream, list) or len(downstream) > 64:
        raise ValueError("Declare at most 64 immutable downstream operations.")
    for ref in downstream:
        validate_ref(ref)
    if len({(r["id"], r["version"], r["digest"]) for r in downstream}) != len(downstream):
        raise ValueError("Downstream operation references must be unique.")
    return copy.deepcopy(value)


def resolve_operation(registry, ref, actor, *, source_mode="parameters", granted_scopes=()):
    validate_ref(ref)
    if source_mode not in ("parameters", "snapshot"):
        raise ValueError("Source mode must be parameters or snapshot.")
    if (not isinstance(granted_scopes, (list, tuple)) or len(granted_scopes) > 64
            or any(not isinstance(x, str) for x in granted_scopes)):
        raise ValueError("Granted permission scopes must be strings.")
    record = registry.get(ref["id"], ref["version"], actor, expected_digest=ref["digest"])
    value = record["resource"]
    if value["resource_type"] != FAMILY:
        raise ValueError("Reference does not identify an operation contract.")
    validate_operation(value)
    evidence = [e for e in registry.evidence(ref["id"], ref["version"], actor)
                if e["trusted"] and e["passed"] and not e["revoked"]
                and e["ref"] == ref and e["implementationDigest"] == value["publisher"]["implementationDigest"]]
    missing = sorted(set(value["requiredScopes"]) - set(granted_scopes))
    return {"schema": "operation-resolution/1", "ref": record["ref"], "owner": record["owner"],
            "publisher": value["publisher"], "operation": value["operation"],
            "transport": value["transport"], "effects": value["effects"],
            "inputBinding": value.get("inputBinding", "body"),
            "constraints": value["constraints"], "requiredScopes": value["requiredScopes"],
            "downstream": value.get("downstream", []),
            "verification": "tested" if evidence else "declared",
            "evidence": [{"id": e["id"], "level": e["level"]} for e in evidence],
            "sourceMode": source_mode, "missingScopes": missing,
            "bindingCompatible": source_mode == "parameters" and not missing,
            "invocationAllowed": False if source_mode == "snapshot" else None,
            "requiresAgentAuthorization": True,
            "authorizationBasis": "caller-supplied-scopes-must-be-enforced-by-executing-agent"}
