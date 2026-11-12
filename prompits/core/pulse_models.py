"""Portable, versioned Pulse definitions and model-management operations.

PDS resources describe meaning. Profiles constrain it; mappings implement it;
catalogs package it. Schema discovery has its own stable interface, independent
of the model being queried. Existing 0.1 documents remain readable.
"""
from __future__ import annotations

import copy
from functools import lru_cache

from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from prompits.core.capabilities import canonical, digest
from prompits.core.resource_registry import reference, validate_ref
from .pulse_schemas import SCHEMAS

RESOURCE_TYPES = ("pulse_definition", "pulse_profile", "pulse_mapping", "pulse_catalog")
SCHEMA_QUERY_PULSE = {
    "name": "schema_query", "pulse_name": "schema_query", "pulse_address": "local://schema_query",
    "description": "Search or resolve versioned Pulse resources and exact dependency bundles.",
    "input_schema": {"type": "object", "properties": {
        "refs": {"type": "array", "maxItems": 64, "items": {"type": "object"}},
        "query": {"type": "string"}, "namespace": {"type": "string"},
        "resource_type": {"enum": list(RESOURCE_TYPES)},
        "include_support": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "offset": {"type": "integer", "minimum": 0}}},
    "output_schema": {"type": "object", "required": ["schema", "records"], "properties": {
        "schema": {"const": "pulse-schema-query/1"}, "records": {"type": "array"},
        "total": {"type": "integer"}, "bundleDigest": {"type": "string"}}},
    "cost": {"amount": 0, "currency": "plaza_point", "model": "free", "unit": "request"},
}


def meta_schema(version):
    if version not in ("0.1.0", "0.2.0"):
        raise ValueError("Unsupported PDS format version.")
    return SCHEMAS["pds-" + ".".join(version.split(".")[:2])]


def resource_uri(ref):
    validate_ref(ref)
    return "urn:resource:" + ref["id"] + "@" + ref["version"] + ":" + ref["digest"]


@lru_cache(maxsize=5)
def runtime_validator(kind):
    schema=SCHEMAS['runtime-1']
    if kind not in schema['$defs']:raise ValueError('Unknown model runtime contract.')
    return Draft202012Validator({**schema,'$ref':'#/$defs/'+kind})


def validate_envelope(kind, value):
    error=next(runtime_validator(kind).iter_errors(value),None)
    if error:raise ValueError('Invalid '+kind+' at /'+'/'.join(map(str,error.absolute_path))+': '+error.message[:200])
    return value


def inspect_schema(schema, dependencies=()):
    allowed = {resource_uri(ref) for ref in dependencies}
    anchors = set()
    def children(node):
        # Values in examples/defaults are instance data, including objects
        # containing $ref. They must never be followed as schema dependencies.
        if not isinstance(node,dict):return []
        values=[]
        for key in ('properties','patternProperties','$defs','definitions','dependentSchemas'):
            if isinstance(node.get(key),dict):values.extend(node[key].values())
        for key in ('items','additionalProperties','contains','not','if','then','else','propertyNames','unevaluatedProperties','unevaluatedItems'):
            if isinstance(node.get(key),(dict,bool)):values.append(node[key])
        for key in ('allOf','anyOf','oneOf','prefixItems'):
            if isinstance(node.get(key),list):values.extend(node[key])
        return values
    def anchor_names(value, depth=0):
        if depth>40:raise ValueError('Schema nesting exceeds 40 levels.')
        if isinstance(value,dict):
            anchors.update(value[k] for k in ('$anchor','$dynamicAnchor') if isinstance(value.get(k),str))
            for child in children(value):anchor_names(child, depth+1)
    anchor_names(schema)
    def visit(node, depth=0):
        if depth > 40:
            raise ValueError("Schema nesting exceeds 40 levels.")
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in node:
                    target = node[key]
                    if not isinstance(target, str) or (not target.startswith("#") and target.split("#", 1)[0] not in allowed):
                        raise ValueError("Schema references must be local or exact declared registry dependencies.")
                    if target.startswith('#/'):
                        cursor=schema
                        try:
                            for part in target[2:].split('/'):
                                part=part.replace('~1','/').replace('~0','~')
                                cursor=cursor[int(part)] if isinstance(cursor,list) else cursor[part]
                        except (KeyError,TypeError,ValueError,IndexError):
                            raise ValueError('Local schema dependency is missing: '+target) from None
                    elif target.startswith('#') and len(target)>1 and target[1:] not in anchors:
                        raise ValueError('Schema anchor is not declared: '+target)
            for value in children(node):
                visit(value, depth + 1)
    visit(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ValueError("Invalid JSON Schema contract.") from exc


def validate_pds(value):
    if not isinstance(value, dict):
        raise ValueError("A PDS resource object is required.")
    schema = meta_schema(value.get("pds_version"))
    if value.get('resource_type') in RESOURCE_TYPES:
        schema = {**schema, 'oneOf': [{'$ref': '#/$defs/' + value['resource_type']}]}
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        error = errors[0]
        while error.context:
            error = error.context[0]
        raise ValueError("Invalid PDS resource at /" + '/'.join(str(p) for p in error.absolute_path) + ': ' + error.message[:300])
    dependencies = value.get("dependencies", [])
    interface = value.get("interface", {})
    for key in ("request_schema", "response_schema"):
        if key in interface:
            inspect_schema(interface[key], dependencies)
    if value["pds_version"] == "0.1.0":
        return copy.deepcopy(value)
    declared = {canonical(validate_ref(r)) for r in dependencies}
    refs = [value[k] for k in ("base_ref", "target_ref", "source_model_ref") if k in value]
    refs.extend(value.get("fields", {}).values())
    if value["resource_type"] == "pulse_catalog":
        refs.extend(item["ref"] for item in value["items"])
        if value.get("imports"):
            raise ValueError("PDS 0.2 catalogs use exact dependencies instead of unresolved imports.")
    if any(canonical(validate_ref(ref)) not in declared for ref in refs):
        raise ValueError("Every referenced resource must be an exact declared dependency.")
    if value["resource_type"] == "pulse_mapping" and "execution" in value:
        from .pulse_mapping import validate_execution
        validate_execution(value["execution"])
    if value["resource_type"] == "pulse_definition" and value.get("pulse_class") in ("analytic", "artifact"):
        derivation = value.get("derivation") or {}
        if not derivation.get("method_type") or not derivation.get("input_pulse_ids"):
            raise ValueError("Analytic and artifact definitions require method and input lineage.")
    return copy.deepcopy(value)


def compare_resources(before, after):
    """Conservative compatibility; semantic stability is stronger than semver."""
    validate_pds(before); validate_pds(after)
    changed = []
    def diff(left, right, path=""):
        if len(changed) >= 200 or left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                diff(left.get(key), right.get(key), path + "/" + key.replace("~", "~0").replace("/", "~1"))
        else:
            changed.append(path or "/")
    diff(before, after)
    semantic = before.get("resource_type") != after.get("resource_type")
    if before["resource_type"] == "pulse_definition":
        semantic |= before.get("pulse_class") != after.get("pulse_class")
        keys = ("definition", "entity_types", "identifiers", "dimensions", "units", "time_semantics")
        semantic |= any(before["concept"].get(k) != after["concept"].get(k) for k in keys)
    def incompatible(old, new):
        if old == new:
            return False
        if not isinstance(old, dict) or not isinstance(new, dict):
            return True
        op, np = old.get("properties", {}), new.get("properties", {})
        if set(old.get("required", [])) != set(new.get("required", [])):
            return True
        if any(k not in np or incompatible(op[k], np[k]) for k in op):
            return True
        if set(np) - set(op) and old.get("additionalProperties") is False:
            return True
        if 'items' in old or 'items' in new:
            if incompatible(old.get('items'), new.get('items')):
                return True
        return {k: v for k, v in old.items() if k not in ("properties", "description", "title", 'items')} != {k: v for k, v in new.items() if k not in ("properties", "description", "title", 'items')}
    breaking = bool(semantic or any(incompatible(before.get("interface", {}).get(k, {}), after.get("interface", {}).get(k, {}))
                                    for k in ("request_schema", "response_schema")))
    if before["resource_type"] == "pulse_profile":
        breaking |= any(before.get(k) != after.get(k) for k in ("base_ref", "constraints"))
        breaking |= any(after.get('fields', {}).get(k) != v for k, v in before.get('fields', {}).items())
    if before["resource_type"] == "pulse_mapping":
        breaking |= any(before.get(k) != after.get(k) for k in ("target_ref", "source_model_ref", "relation", "execution"))
    return {"semanticChange": bool(semantic), "breaking": bool(breaking), "compatible": not breaking, "changes": changed}


def schema_query(registry, request, actor=""):
    if not isinstance(request, dict):
        raise ValueError("Schema query must be an object.")
    if request.get("refs"):
        bundle = registry.bundle(request["refs"], actor)
        return {"schema": "pulse-schema-query/1", "records": bundle["records"], "total": len(bundle["records"]),
                "bundleDigest": bundle["digest"], "roots": bundle["roots"]}
    kind = request.get("resource_type", "")
    if kind and kind not in RESOURCE_TYPES:
        raise ValueError("Unsupported PDS resource type.")
    result = registry.search(actor, query=request.get("query", ""), namespace=request.get("namespace", ""),
                             family=kind, limit=request.get("limit", 50), offset=request.get("offset", 0))
    return {"schema": "pulse-schema-query/1", **result}


def query_installed(resources, request):
    """Read a source's public installed bundle; this is not registry endorsement."""
    if not isinstance(request, dict):
        raise ValueError("Schema query must be an object.")
    index={(r['id'],r['version']):r for r in resources}
    refs=request.get('refs')
    if refs:
        if not isinstance(refs,list) or len(refs)>64:
            raise ValueError('At most 64 exact roots are supported.')
        seen,active,records=set(),set(),[]
        def visit(ref):
            validate_ref(ref);key=(ref['id'],ref['version'])
            if key in active or len(active)>32:
                raise ValueError('Invalid or excessive dependency graph.')
            value=index.get(key)
            if value is None or reference(value)!=ref:
                raise ValueError('Requested exact resource is not installed.')
            if key in seen:return
            if len(seen)>=256:raise ValueError('Invalid or excessive dependency graph.')
            active.add(key)
            for child in value.get('dependencies',[]):visit(child)
            active.remove(key);seen.add(key)
            records.append({'resource':copy.deepcopy(value),'ref':ref,'verification':'declared'})
        for ref in refs:visit(ref)
        return {'schema':'pulse-schema-query/1','records':records,'total':len(records),'roots':refs,
                'bundleDigest':digest([r['ref'] for r in records]),'sourceMode':'installed-bundle'}
    limit,offset=request.get('limit',50),request.get('offset',0)
    if type(limit) is not int or not 1<=limit<=200 or type(offset) is not int or offset<0:
        raise ValueError('Invalid schema query page.')
    query=str(request.get('query','')).lower();namespace=request.get('namespace');kind=request.get('resource_type')
    matches=[r for r in resources if (not kind or r['resource_type']==kind) and (not namespace or r['namespace']==namespace or r['namespace'].startswith(namespace+'.')) and (not query or query in canonical(r).lower())]
    return {'schema':'pulse-schema-query/1','records':[{'resource':copy.deepcopy(r),'ref':reference(r),'verification':'declared'} for r in matches[offset:offset+limit]],
            'total':len(matches),'sourceMode':'installed-bundle'}


def validate_payload(resource, payload, bundle=(), *, direction="response_schema"):
    """Validate a result without allowing remote reference retrieval."""
    def bounded(value, depth=0):
        if depth>40:raise ValueError('Payload nesting exceeds 40 levels.')
        if isinstance(value,dict):
            for child in value.values():bounded(child,depth+1)
        elif isinstance(value,list):
            if len(value)>10000:raise ValueError('Payload exceeds 10,000 observations.')
            for child in value:bounded(child,depth+1)
    bounded(payload)
    if len(canonical(payload).encode())>4_000_000:raise ValueError('Payload exceeds 4 MB.')
    schema = resource.get("interface", {}).get(direction)
    if schema is None:
        raise ValueError("Resource does not declare this interface.")
    inspect_schema(schema, resource.get("dependencies", []))
    def denied(uri):
        raise ValueError("Undeclared schema dependency: " + uri)
    resolver = Registry(retrieve=denied)
    for record in bundle:
        dependency = record.get("resource", record)
        validate_pds(dependency)
        ref = reference(dependency)
        nested = dependency.get("interface", {}).get(direction, {})
        resolver = resolver.with_resource(resource_uri(ref), Resource.from_contents(nested, default_specification=DRAFT202012))
    try:
        error = next(Draft202012Validator(schema, registry=resolver).iter_errors(payload),None)
    except Exception as exc:
        raise ValueError("Unresolved or invalid schema dependency.") from exc
    if error:
        raise ValueError("Payload violates declared model: " + error.message[:300])
    return payload


def install_model_registry(plaza):
    """Install the PDS extension above the generic Plaza resource storage."""
    from starlette.concurrency import run_in_threadpool
    from prompits.core.resource_routes import resource_registry

    plaza.resource_validators = {**getattr(plaza, "resource_validators", {}), **{k: validate_pds for k in RESOURCE_TYPES}}
    plaza.resource_comparators = {**getattr(plaza, "resource_comparators", {}), **{k: compare_resources for k in RESOURCE_TYPES}}
    if getattr(plaza, "_resource_registry", None):
        plaza._resource_registry.validators.update(plaza.resource_validators)
        plaza._resource_registry.comparators.update(plaza.resource_comparators)

    async def execute(request, callback):
        if len(await request.body()) > 512_000:
            raise HTTPException(413, "Model request exceeds 512 KB.")
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Provide a JSON object.")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        def run():
            user = plaza._require_ui_user(request)
            try:
                return callback(resource_registry(plaza), data, user["id"])
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(404, "Model unavailable.") from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return await run_in_threadpool(run)

    @plaza.app.post("/api/schema-query")
    async def query(request: Request):
        def portable_query(registry, data, actor):
            result = schema_query(registry, data, actor)
            if data.get('includeCanonical') is True:
                # JSON decoders in other runtimes erase distinctions such as
                # Python's 1.0 versus 1. Return the exact digest input on request.
                from prompits.core.capabilities import canonical
                result['records'] = [{**row, 'canonicalPayload': canonical(row['resource'])}
                                     for row in result.get('records', [])]
            return result
        return await execute(request, portable_query)

    @plaza.app.post("/api/model-comparison")
    async def compare(request: Request):
        return await execute(request, lambda registry, data, actor: compare_resources(data.get("before"), data.get("after")))
