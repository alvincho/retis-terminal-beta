"""Portable composition profile on the existing Phema model.

Authored content is distinct from resolved snapshots and host source bindings.
The shared layout grammar is validated here. Other application grammars remain
opaque and are validated by their consumer.
"""
from __future__ import annotations

import copy
import re
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from prompits.core.capabilities import canonical, digest, validate_requirement, negotiate, consolidate_requirements
from .phema import Phema

PROFILE = 'phemacast:composition/1'
NODE = 'phemacast:composition'
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
KINDS = {'pulse', 'api', 'channel', 'phemar-snapshot', 'phemar-generate', 'asset'}
TRIGGERS = {'open', 'change', 'manual', 'interval', 'stream'}
MAX_DEPTH = 8
MAX_COMPONENTS = 64


from .schema_security import check_schema as schema_check, validate as bounded_validate


def validate_reference(ref):
    if not isinstance(ref, dict) or not ID.fullmatch(str(ref.get('id', ''))):
        raise ValueError('Reference requires an identifier.')
    if ref.get('kind') not in KINDS: raise ValueError('Unsupported reference kind.')
    if type(ref.get('required', True)) is not bool: raise ValueError('Reference required must be boolean.')
    target = ref.get('target')
    if not isinstance(target, dict): raise ValueError('Reference requires a registered target.')
    allowed = {'pulse': {'agentId', 'pulse'}, 'api': {'agentId', 'operation'}, 'channel': {'agentId', 'pulse'},
               'phemar-snapshot': {'agentId', 'snapshotId'}, 'phemar-generate': {'agentId', 'phemaId'},
               'asset': {'assetId', 'version'}}[ref['kind']]
    if set(target) != allowed or any(not isinstance(v, str) or not v or len(v) > 200 for v in target.values()):
        raise ValueError('Use registered identities; credentials and arbitrary endpoints are not portable targets.')
    if not isinstance(ref.get('inputs', {}), dict): raise ValueError('Reference inputs must be an object.')
    def inspect_input(value, depth=0):
        if depth > 12: raise ValueError('Reference inputs are too deeply nested.')
        if isinstance(value, dict):
            for key, child in value.items():
                if re.sub(r'[^a-z]', '', key.lower()) in {'apikey','secret','password','accesstoken','refreshtoken','authorization','bearertoken','sessioncookie','cookie','credential','clientsecret','proto','constructor','prototype'}:
                    raise ValueError('Credentials belong to the host, not Phema inputs.')
                inspect_input(child, depth+1)
        elif isinstance(value, list):
            for child in value: inspect_input(child, depth+1)
    inspect_input(ref.get('inputs', {}))
    deps = ref.get('dependsOn', [])
    if not isinstance(deps, list) or any(not isinstance(x, str) or not ID.fullmatch(x) for x in deps): raise ValueError('Invalid dependencies.')
    refresh = ref.get('refresh', {'trigger': 'open'})
    if not isinstance(refresh, dict) or refresh.get('trigger') not in TRIGGERS: raise ValueError('Invalid reference refresh policy.')
    if refresh['trigger'] == 'interval' and (type(refresh.get('seconds')) is not int or not 60 <= refresh['seconds'] <= 86400): raise ValueError('Interval must be 60–86400 seconds.')
    # A live stream resolves at open, then the host pushes each delivery into this reference's result.
    if refresh['trigger'] == 'stream' and ref['kind'] != 'pulse': raise ValueError('Only Pulse references can stream.')
    for key, default, maximum in [('timeoutSeconds', 20, 120), ('maxAgeSeconds', 0, 31536000)]:
        val = ref.get(key, default)
        if type(val) not in (float, int) or not 0 <= val <= maximum or key == 'timeoutSeconds' and val == 0: raise ValueError('Invalid ' + key)
    if ref.get('onFailure', 'retain') not in {'retain', 'unavailable'}: raise ValueError('Invalid failure policy.')
    schema_check(ref.get('outputSchema', {}))
    interaction=ref.get('interaction')
    if interaction is not None:
        if not isinstance(interaction,dict):raise ValueError('Invalid interaction contract.')
        schema_check(interaction.get('inputSchema', {}))
        if ref.get('refresh',{}).get('trigger')!='manual':raise ValueError('Interactive references require manual execution.')
    selection = ref.get('select')
    if selection is not None:
        if not isinstance(selection, dict) or set(selection) - {'path', 'orderBy', 'idField', 'limit', 'where'}: raise ValueError('Invalid selection.')
        if type(selection.get('limit', 1)) is not int or not 1 <= selection.get('limit', 1) <= 100: raise ValueError('Selection limit must be 1–100.')
        if not isinstance(selection.get('where', {}), dict): raise ValueError('Selection filters must be an object.')
        for key in ('path', 'orderBy', 'idField'):
            if key in selection and (not isinstance(selection[key], str) or len(selection[key]) > 128): raise ValueError('Invalid selection field.')
    return ref


def composition(phema):
    if not isinstance(phema, dict) or not isinstance(phema.get('meta'), dict) or phema['meta'].get('profile') != PROFILE:
        raise ValueError('Expected a composition Phema.')
    if not isinstance(phema.get('sections'), list) or any(not isinstance(s, dict) or not isinstance(s.get('content'), list) for s in phema['sections']):
        raise ValueError('Expected Phema sections with content arrays.')
    nodes = [n for s in phema.get('sections', []) for n in s.get('content', []) if isinstance(n, dict) and n.get('type') == NODE]
    if len(nodes) != 1: raise ValueError('A Phema has exactly one canonical composition.')
    return nodes[0]


def validate_composition(phema, *, _ancestors=(), _count=None):
    if _count is None:
        try: size = len(canonical(phema).encode())
        except (ValueError, TypeError, RecursionError) as exc: raise ValueError('Phema must be an acyclic JSON document.') from exc
        if size > 500_000: raise ValueError('Composition exceeds 500 KB.')
        _count = [0, 0]
    if len(_ancestors) > MAX_DEPTH: raise ValueError('Phema nesting exceeds eight levels.')
    if not isinstance(phema, dict) or not isinstance(phema.get('id'), str) or not ID.fullmatch(phema['id']): raise ValueError('Phema requires a valid identity.')
    if phema.get('phema_id', phema['id']) != phema['id']: raise ValueError('Phema identities must agree.')
    if phema['id'] in _ancestors: raise ValueError('Cyclic nested Phema identity.')
    Phema.from_dict(phema)
    node = composition(phema)
    if node.get('version') != '1.0.0': raise ValueError('Unsupported composition version.')
    presentation = node.get('presentation')
    if not isinstance(presentation, dict) or not isinstance(presentation.get('grammar'), str) or ':' not in presentation['grammar'] or not isinstance(presentation.get('document'), dict):
        raise ValueError('A namespaced presentation grammar and document are required.')
    schema_check(phema.get('input_schema', {}))
    caps = node.get('capabilities', [])
    if not isinstance(caps, list) or len(caps) > 128: raise ValueError('Invalid capability requirements.')
    for cap in caps: validate_requirement(cap)
    from .composition_permissions import manifest, validate_manifest
    validate_manifest(manifest(phema))
    refs = node.get('references', [])
    if not isinstance(refs, list) or len(refs) > 64: raise ValueError('A composition supports up to 64 references.')
    ids = set()
    for ref in refs:
        validate_reference(ref)
        if ref['id'] in ids: raise ValueError('Duplicate reference.')
        ids.add(ref['id'])
    graph = {ref['id']: dependencies(ref) for ref in refs}
    visiting, visited = set(), set()
    def visit(id):
        if id in visiting: raise ValueError('Cyclic content references.')
        if id not in graph: raise ValueError('Reference dependency does not exist: ' + id)
        if id in visited: return
        visiting.add(id)
        for dep in graph[id]: visit(dep)
        visiting.remove(id); visited.add(id)
    for id in graph: visit(id)
    _count[1] += len(refs)
    if _count[1] > 256: raise ValueError('A Phema tree supports up to 256 references.')
    children = node.get('components', [])
    if not isinstance(children, list): raise ValueError('Phema components must be an array.')
    child_ids = set()
    for child in children:
        if not isinstance(child, dict) or not ID.fullmatch(str(child.get('id', ''))) or child['id'] in child_ids:
            raise ValueError('Nested components require unique identifiers within their parent.')
        child_ids.add(child['id']); _count[0] += 1
        if _count[0] > MAX_COMPONENTS: raise ValueError('A Phema tree supports up to 64 child components.')
        if child.get('type') != 'phema': raise ValueError('A nested component has type phema.')
        if type(child.get('required', True)) is not bool: raise ValueError('Component required must be boolean.')
        if not isinstance(child.get('inputs', {}), dict): raise ValueError('Child parameters must be an object.')
        # Children receive explicit literal / parent parameter bindings. Result
        # references stay local to each Phema, so identical sibling IDs are safe.
        validate_reference({'id':'parameters','kind':'asset','target':{'assetId':'parameters','version':'1'},'inputs':child.get('inputs', {})})
        if dependencies({'inputs':child.get('inputs', {})}): raise ValueError('Child inputs use parent parameters; result references are local to each Phema.')
        validate_composition(child.get('phema'), _ancestors=(*_ancestors, phema['id']), _count=_count)
    from .layout import GRAMMAR, validate_layout, layout_requirements
    if presentation['grammar'] == GRAMMAR:
        document = validate_layout(presentation['document'])
        if document['id'] != phema['id']:
            raise ValueError('Phema identity must match its layout identity.')
        expected_schema = {'type': 'object', 'properties': document['design']['params'], 'additionalProperties': False}
        if phema.get('input_schema') != expected_schema:
            raise ValueError('Phema parameters must match its layout contract.')
        declared = {cap['id'] for cap in caps}
        if any(cap['id'] not in declared for cap in layout_requirements(document)):
            raise ValueError('Phema omits a required layout capability.')
        for surface in document['design']['layout']['children']:
            if surface.get('referenceId') and surface['referenceId'] not in ids:
                raise ValueError('Layout reference is not declared.')
            if surface.get('componentId') and surface['componentId'] not in child_ids:
                raise ValueError('Layout child Phema is not declared.')
            if surface['renderer']['id'] == 'phemacast:nesting' and not surface.get('componentId'):
                raise ValueError('Nested layout surface requires a child Phema.')
    publication = phema.get('meta', {}).get('publication')
    if publication and publication.get('contentHash') != content_hash(phema): raise ValueError('Phema content hash does not match its publication.')
    return copy.deepcopy(phema)


def dependencies(ref):
    found = set(ref.get('dependsOn', []))
    def walk(value):
        if isinstance(value, dict):
            if '$result' in value:
                result = value['$result']
                if not isinstance(result, dict) or not isinstance(result.get('reference'), str): raise ValueError('Result binding requires a reference.')
                found.add(result['reference'])
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(ref.get('inputs', {}))
    walk(ref.get('select', {}))
    return found


def content_hash(phema):
    value = copy.deepcopy(phema)
    value.get('meta', {}).pop('publication', None)
    return digest(value)


def publish(phema, publisher, revision):
    value = validate_composition(phema)
    if not publisher or type(revision) is not int or revision < 1: raise ValueError('Publisher and positive revision are required.')
    value['meta']['publication'] = {'publisher': publisher, 'revision': revision, 'contentHash': content_hash(value)}
    return value


def compatible(phema, advertisement):
    value = validate_composition(phema)
    requirements = []
    for path, current in walk_compositions(value):
        requirements.extend(composition(current).get('capabilities', []))
        from .layout import GRAMMAR, layout_requirements
        presentation = composition(current)['presentation']
        if presentation['grammar'] == GRAMMAR:
            requirements.extend(layout_requirements(presentation['document']))
    if composition(value).get('components'):
        paths = [path for path, _ in walk_compositions(value) if path]
        requirements.append({'id':'phemacast:nesting','version':'1.0.0','limits':{'depth':max(len(p.split('/')) for p in paths),'components':len(paths)}})
    return negotiate(consolidate_requirements(requirements), advertisement)


def walk_compositions(phema, path=''):
    """Traverse the authored tree without flattening its portable representation."""
    yield path, phema
    for child in composition(phema).get('components', []):
        yield from walk_compositions(child['phema'], '/'.join(filter(None, (path, child['id']))))


def reference_paths(phema):
    return {'/'.join(filter(None, (path, r['id']))): r for path, current in walk_compositions(phema) for r in composition(current).get('references', [])}


def component_parameters(component, parent_parameters):
    from .composition_resolution import bind
    schema = component['phema'].get('input_schema', {})
    defaults = {k:copy.deepcopy(v['default']) for k,v in schema.get('properties', {}).items() if 'default' in v}
    parameters = {**defaults, **bind(component.get('inputs', {}), parent_parameters, {})}
    bounded_validate(schema, parameters)
    return parameters
