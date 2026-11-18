"""Pinned model compatibility, bounded mapping chains and result identities."""
from __future__ import annotations

import copy
import time
import math

from prompits.core.capabilities import canonical, digest
from prompits.core.resource_registry import reference, validate_ref
from prompits.core.pulse_models import compare_resources, validate_payload, validate_pds
from prompits.core.pulse_mapping import execute_mapping


def verify_bundle(records, roots):
    if not isinstance(records, list) or not 1 <= len(records) <= 256:
        raise ValueError('A bundle contains one to 256 resources.')
    if len(canonical(records).encode()) > 4_000_000:
        raise ValueError('A model bundle exceeds 4 MB.')
    index = {}
    for record in records:
        value = validate_pds(record['resource'])
        ref = validate_ref(record['ref'])
        if reference(value) != ref:
            raise ValueError('Model bundle content digest mismatch.')
        key = canonical(ref)
        if key in index:
            raise ValueError('Duplicate resource in model bundle.')
        index[key] = record
    active, seen = set(), set()
    def visit(ref):
        key = canonical(validate_ref(ref))
        if key in active or len(active) >= 32:
            raise ValueError('Cyclic or excessive model dependencies.')
        if key in seen:
            return
        if key not in index:
            raise ValueError('Exact model dependency is missing.')
        active.add(key)
        for child in index[key]['resource'].get('dependencies', []):
            visit(child)
        active.remove(key); seen.add(key)
    for root in roots:
        visit(root)
    # Unreachable extras can be independently requested, but cannot smuggle an
    # incomplete or differently-versioned graph into an installed package.
    for record in records:
        visit(record['ref'])
    return index


def compatibility(source, target, mappings=(), *, allow_lossy=False):
    """Relations alone are never proof of executable equivalence."""
    if reference(source) == reference(target):
        return {'kind': 'exact', 'compatible': True, 'chain': []}
    if source['id'] == target['id']:
        comparison = compare_resources(source, target)
        if comparison['compatible']:
            return {'kind': 'compatible-version', 'compatible': True, 'chain': [], 'comparison': comparison}
    for mapping in mappings:
        if mapping.get('source_model_ref') == reference(source) and mapping.get('target_ref') == reference(target):
            relation = mapping.get('relation')
            if relation == 'close_match' and not allow_lossy:
                return {'kind': 'approximate', 'compatible': False, 'reason': 'Approximate meaning requires an explicit lossy policy.'}
            if relation not in ('equivalent_match', 'implements', 'close_match') or not mapping.get('execution'):
                continue
            return {'kind': 'conditional', 'compatible': True, 'chain': [reference(mapping)],
                    'conditions': copy.deepcopy(mapping.get('preconditions', mapping.get('extensions', {}).get('phemacast:preconditions', {}))),
                    'lossy': relation == 'close_match'}
    return {'kind': 'incompatible', 'compatible': False, 'reason': 'No installed executable mapping satisfies the target.'}


def execute_chain(chain, payload, records, *, parameters=None, allow_lossy=False):
    if not isinstance(chain, list) or not 1 <= len(chain) <= 3:
        raise ValueError('A mapping chain contains one to three steps.')
    index = verify_bundle(records, chain)
    rows, previous, provenance, issues = copy.deepcopy(payload), None, [], []
    for ref in chain:
        mapping = index[canonical(ref)]['resource']
        if mapping.get('resource_type') != 'pulse_mapping' or not mapping.get('source_model_ref'):
            raise ValueError('A model-to-model mapping must identify its input and output models.')
        if previous and mapping['source_model_ref'] != previous:
            raise ValueError('Mapping chain has mismatched intermediate models.')
        if mapping.get('relation') not in ('equivalent_match', 'implements') and not (allow_lossy and mapping.get('relation') == 'close_match'):
            raise ValueError('The mapping relation is not permitted by this request.')
        # This path is declarative only. Installed native methods are dispatched
        # by the source host, never by names found in a registry resource.
        if mapping['execution']['kind'] != 'projection':
            raise ValueError('Model chains accept bounded declarative projections only.')
        incoming = index[canonical(mapping['source_model_ref'])]['resource']
        outgoing = index[canonical(mapping['target_ref'])]['resource']
        validate_payload(incoming, rows, records)
        conditions = mapping.get('extensions', {}).get('phemacast:preconditions', {})
        if not isinstance(conditions, dict) or len(conditions) > 20:
            raise ValueError('Invalid mapping preconditions.')
        for field, allowed in conditions.items():
            if not isinstance(allowed, list) or not allowed or any(row.get(field) not in allowed for row in rows):
                raise ValueError('Mapping precondition failed: ' + field)
        output = execute_mapping(mapping, rows, parameters)
        rows = output['rows']; issues.extend(output['issues'])
        validate_payload(outgoing, rows, records)
        provenance.append({'mappingRef': ref, 'lineage': output['lineage']})
        previous = mapping['target_ref']
    return {'modelRef': previous, 'payload': rows, 'mappingRefs': chain, 'lineage': provenance, 'issues': issues}


def verify_vectors(chain, vectors, records):
    """Verify the entire chain; pairwise evidence is insufficient."""
    if not isinstance(vectors, list) or not 1 <= len(vectors) <= 50:
        raise ValueError('Provide one to 50 chain test vectors.')
    if not any(not v.get('reject') and v.get('expected') for v in vectors if isinstance(v,dict)):
        raise ValueError('Chain evidence requires a nonempty successful example.')
    cases = []
    for vector in vectors:
        if not isinstance(vector, dict) or not vector.get('name') or 'input' not in vector:
            raise ValueError('Each vector needs a name and input.')
        try:
            result = execute_chain(chain, vector['input'], records, parameters=vector.get('parameters'), allow_lossy=vector.get('allowLossy', False))
            passed = not vector.get('reject') and result['payload'] == vector.get('expected') and not result['issues']
        except (ValueError, TypeError, KeyError):
            passed = vector.get('reject') is True
        cases.append({'name': vector['name'], 'passed': passed})
    if not all(case['passed'] for case in cases):
        raise ValueError('Mapping chain test vectors failed: ' + ', '.join(c['name'] for c in cases if not c['passed']))
    return {'schema': 'mapping-chain-evidence/1', 'chain': chain, 'cases': cases, 'vectorsDigest': digest(vectors), 'level': 'fixture-verified'}


def execution_context(user, caller, *, source_owner='', connection=None, policy=None, lifetime=60):
    """No bearer, API key or reusable secret is serialized in execution plans."""
    if not user.get('id'):
        raise PermissionError('An authenticated execution user is required.')
    connection = connection or {}
    policy = policy or {}
    now = time.time()
    value = {'schema': 'execution-context/1', 'requestUser': user['id'], 'tenant': user.get('tenant', user['id']),
             'callerAgent': caller, 'sourceOwner': source_owner, 'credentialRef': connection.get('id'),
             'credentialOwner': user['id'] if connection else source_owner, 'billingUser': user['id'],
             'audience': [user['id']], 'policyDigest': digest(policy), 'authorizationVersion': user.get('authorizationVersion', 'session-verified'),
             'createdAt': now, 'expiresAt': now + min(max(lifetime, 1), 300)}
    from prompits.core.pulse_models import validate_envelope
    return validate_envelope('ExecutionContext',{**value, 'digest': digest(value)})


def resolution_plan(requirement, candidates, context, *, rejected=()):
    validate_ref(requirement['modelRef'])
    fields = requirement.get('fields', [])
    optional = set(requirement.get('optionalFields', []))
    required = set(fields) - optional
    ordered = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        cost=item.get('estimatedPlazaPoints')
        if cost is not None and (type(cost) not in (int,float) or not math.isfinite(cost) or cost<0):
            continue
        missing = required - set(item.get('fields', []))
        item['missingRequiredFields'] = sorted(missing)
        ordered.append(item)
    ordered.sort(key=lambda c: (len(c['missingRequiredFields']), c.get('priorityTier', 999),
                                c.get('estimatedPlazaPoints') if type(c.get('estimatedPlazaPoints')) in (int, float) else float('inf'),
                                c.get('sourceId', ''), canonical(c.get('mappingRefs', []))))
    value = {'schema': 'resolution-plan/1', 'requirement': copy.deepcopy(requirement), 'executionContext': context,
             'candidates': ordered, 'rejected': list(rejected), 'createdAt': context['createdAt'], 'expiresAt': context['expiresAt']}
    from prompits.core.pulse_models import validate_envelope
    return validate_envelope('ResolutionPlan',{**value, 'id': digest(value)})
