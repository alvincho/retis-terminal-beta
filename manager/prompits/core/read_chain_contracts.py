"""Closed read-plan contracts. Content hashes never replace owner authority."""
from copy import deepcopy
import json
import re

from jsonschema import Draft202012Validator

from .capabilities import canonical, digest
from .operation_input import project_operation_input
from .resource_registry import validate_ref

SCHEMA = 'operation-read-chain/1'
PREFIX = 'pr-read-'
API = '/api/read-operation-grants/v1'
ID = {'type': 'string', 'minLength': 1, 'maxLength': 200}
HASH = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
REF = {'type': 'object', 'properties': {'id': ID, 'version': {'type': 'string', 'pattern': r'^\d+\.\d+\.\d+$'}, 'digest': HASH},
       'required': ['id', 'version', 'digest'], 'additionalProperties': False}
POINTER = {'type': 'string', 'minLength': 1, 'maxLength': 200, 'pattern': '^/'}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


def refs(limit=64):
    return {'type': 'array', 'minItems': 1, 'maxItems': limit, 'uniqueItems': True, 'items': REF}


MEMBER = obj({'agentId': ID, 'resourceRef': REF, 'revision': {'type': 'integer', 'minimum': 0}})
RESOURCE = obj({'ref': REF, 'parentRef': {'anyOf': [REF, {'type': 'null'}]}, 'revision': {'type': 'integer', 'minimum': 0}})
PROOF_SCHEMA = obj({
    'schema': {'const': 'read-authority-proof/1'},
    'issuer': obj({'agentId': ID, 'implementationDigest': HASH}), 'subjectUserId': ID, 'resourceOwnerId': ID,
    'resources': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': RESOURCE},
    'members': {'type': 'array', 'maxItems': 16, 'items': MEMBER},
    'revisions': {'type': 'object', 'minProperties': 1, 'maxProperties': 16, 'additionalProperties': {'type': 'integer', 'minimum': 0}},
    'observedAt': {'type': 'number'}, 'expiresAt': {'type': 'number'}, 'content': {'type': 'object'},
})
NODE_SCHEMA = obj({
    'id': ID, 'parent': {'anyOf': [ID, {'type': 'null'}]}, 'audience': ID, 'operationRef': REF,
    'intentId': ID, 'input': obj({'path': {'type': 'object'}, 'query': {'type': 'object'}, 'body': {'type': 'null'}}, ['path', 'query']),
    'resourceRefs': refs(), 'proofRefs': refs(32), 'member': {'anyOf': [MEMBER, {'type': 'null'}]},
    'inputFields': {'type': 'array', 'maxItems': 64, 'uniqueItems': True, 'items': POINTER},
    'inputFlows': {'type': 'array', 'maxItems': 64, 'items': obj({'proofRef': REF, 'from': POINTER, 'to': POINTER})},
    'outputFields': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True, 'items': POINTER},
    'outputResources': {'type': 'array', 'maxItems': 64, 'items': obj({'pointer': POINTER, 'refs': refs()})},
    'maxCalls': {'type': 'integer', 'minimum': 1, 'maximum': 16},
    'maxOutputBytes': {'type': 'integer', 'minimum': 2, 'maximum': 300000},
    'maxRows': {'type': 'integer', 'minimum': 0, 'maximum': 10000},
    'maxPoints': {'type': 'integer', 'const': 0},
})
PLAN_SCHEMA = obj({
    'schema': {'const': SCHEMA}, 'subjectUserId': ID, 'rootServiceAgentId': ID, 'manifestDigest': HASH,
    'intentId': ID, 'rootNode': ID, 'expiresAt': {'type': 'number'},
    'nodes': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': NODE_SCHEMA},
    'maxCallsTotal': {'type': 'integer', 'minimum': 1, 'maximum': 64},
    'maxMembers': {'type': 'integer', 'minimum': 0, 'maximum': 16},
})


def validate(schema, value, limit=128000):
    if len(canonical(value).encode()) > limit or list(Draft202012Validator(schema).iter_errors(value)):
        raise PermissionError('Read authority does not conform to its closed bounded schema.')
    return deepcopy(value)


def parse_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise PermissionError('Duplicate JSON read authority/result fields denied.')
            result[key] = value
        return result
    def constant(value): raise PermissionError('Nonfinite JSON read authority/result denied.')
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/') or re.search(r'~(?![01])', path):
        raise PermissionError('Canonical nonempty JSON pointer required.')
    try:
        for part in path.split('/')[1:]:
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', key): raise KeyError(key)
                value = value[int(key)]
            else: value = value[key]
        return deepcopy(value)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PermissionError('Read field/resource pointer is unavailable.') from exc


def leaves(value, prefix=''):
    if isinstance(value, dict):
        return set().union(*(leaves(v, prefix + '/' + k.replace('~', '~0').replace('/', '~1')) for k, v in value.items())) if value else {prefix}
    if isinstance(value, list):
        return set().union(*(leaves(v, prefix + '/' + str(i)) for i, v in enumerate(value))) if value else {prefix}
    return {prefix}


def covered(value, fields):
    return all(any(p == f or p.startswith(f + '/') for f in fields) for p in leaves(value))


def validate_plan(value):
    plan = validate(PLAN_SCHEMA, value)
    nodes = {n['id']: n for n in plan['nodes']}
    if len(nodes) != len(plan['nodes']) or plan['rootNode'] not in nodes:
        raise PermissionError('Read plan nodes must be unique and have one declared root.')
    root = nodes[plan['rootNode']]
    if root['parent'] is not None or root['maxCalls'] != 1:
        raise PermissionError('Root read dispatch is single-use.')
    unique = set()
    for n in nodes.values():
        for ref in n['resourceRefs'] + n['proofRefs']: validate_ref(ref)
        if not covered(n['input'], n['inputFields']):
            raise PermissionError('Read input exceeds explicitly permitted fields.')
        for p in n['inputFields'] + n['outputFields']:
            if re.search(r'~(?![01])', p): raise PermissionError('Noncanonical field policy.')
        ancestry = set(); current = n
        while current['parent'] is not None:
            if current['id'] in ancestry or current['parent'] not in nodes or len(ancestry) >= 4:
                raise PermissionError('Read ancestry is cyclic, missing or exceeds four hops.')
            ancestry.add(current['id']); parent = nodes[current['parent']]
            if any(r not in parent['resourceRefs'] for r in current['resourceRefs']):
                raise PermissionError('Child resource transfer exceeds its exact parent.')
            if any(r not in parent['proofRefs'] for r in current['proofRefs']):
                raise PermissionError('Child proof transfer exceeds its exact parent.')
            current = parent
        if current['id'] != root['id']:
            raise PermissionError('Read plan has disconnected roots.')
        key = (n['audience'], canonical(n['operationRef']), digest(n['input']))
        if key in unique: raise PermissionError('Duplicate read nodes cannot multiply call ceilings.')
        unique.add(key)
        if n['member'] and (n['member']['agentId'] != n['audience'] or n['member']['resourceRef'] not in n['resourceRefs']):
            raise PermissionError('Member target differs from its exact resource/audience.')
        for f in n['inputFlows']:
            if f['proofRef'] not in n['proofRefs'] or not any(f['to'] == p or f['to'].startswith(p + '/') for p in n['inputFields']):
                raise PermissionError('Read proof field transfer exceeds node policy.')
        for f in n['outputResources']:
            if any(r not in n['resourceRefs'] for r in f['refs']) or not any(f['pointer'] == p or f['pointer'].startswith(p + '/') for p in n['outputFields']):
                raise PermissionError('Read output resource transfer exceeds node policy.')
    if plan['maxCallsTotal'] > sum(n['maxCalls'] for n in nodes.values()):
        raise PermissionError('Total read ceiling exceeds the closed plan.')
    if len({n['member']['agentId'] for n in nodes.values() if n['member']}) > plan['maxMembers']:
        raise PermissionError('Member read count exceeds the plan.')
    return plan


def read_wire(resource, node):
    wire = project_operation_input(resource, node['input'])
    if resource.get('inputBinding') != 'http-envelope' or wire['method'] != 'GET' or wire['body'] is not None:
        raise PermissionError('Read-chain v1 admits bodyless explicit GET envelopes only; HEAD is unsupported.')
    return wire


def validate_output(resource, node, value):
    raw = canonical(value).encode()
    if len(raw) > node['maxOutputBytes'] or not covered(value, node['outputFields']):
        raise PermissionError('Read result exceeds its field/byte ceilings.')
    Draft202012Validator(resource['outputSchema']).validate(value)
    def rows(v):
        if isinstance(v, list): return len(v) + sum(rows(i) for i in v)
        if isinstance(v, dict): return sum(rows(i) for i in v.values())
        return 0
    if rows(value) > node['maxRows']: raise PermissionError('Read result exceeds cumulative row ceiling.')
    for rule in node['outputResources']:
        values = pointer(value, rule['pointer'])
        values = values if isinstance(values, list) else [values]
        if len(values) > len(rule['refs']) or any(validate_ref(v) not in rule['refs'] for v in values):
            raise PermissionError('Read result transfers an undeclared resource.')
    def resource_fields(v, path=''):
        if isinstance(v, dict):
            if set(v) == {'id', 'version', 'digest'}:
                validate_ref(v)
                if not any((path == r['pointer'] or path.startswith(r['pointer'] + '/')) and v in r['refs'] for r in node['outputResources']):
                    raise PermissionError('Every returned exact resource reference needs an explicit field transfer.')
            else:
                for key, item in v.items():resource_fields(item, path + '/' + key.replace('~','~0').replace('/','~1'))
        elif isinstance(v, list):
            for index, item in enumerate(v):resource_fields(item, path + '/' + str(index))
    resource_fields(value)
    return value
