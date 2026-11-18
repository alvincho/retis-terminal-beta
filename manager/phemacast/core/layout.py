"""Source-independent layout grammar for composition Phemas.

Renderers are named capabilities. Application semantics live in the renderer
contract and namespaced extensions, never in the layout engine.
"""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from prompits.core.capabilities import validate_requirement
from .composition_contracts import MEDIA_SCHEMAS, CUSTOM_SCHEMAS
from .composition_media import validate_media, validate_custom
from .display_format import EXTENSION as FIELD_FORMATS, validate_field_formats

GRAMMAR = 'phemacast:layout/1'
CAPABILITY = {'id': 'phemacast:layout', 'version': '1.0.0'}
SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas/phema/layout.schema.json'


def layout_schema():
    return json.loads(SCHEMA_PATH.read_text())


def overlaps(a, b):
    epsilon = 1e-7
    return (a['column'] < b['column'] + b['columnSpan'] - epsilon
            and b['column'] < a['column'] + a['columnSpan'] - epsilon
            and a['row'] < b['row'] + b['rowSpan'] - epsilon
            and b['row'] < a['row'] + a['rowSpan'] - epsilon)


def validate_layout(document):
    """Validate structure and local bindings without selecting any data source."""
    from .composition import schema_check
    errors = list(Draft202012Validator(layout_schema()).iter_errors(document))
    if errors:
        raise ValueError('Invalid Phemacast layout: ' + errors[0].message)
    from .ui_localization import validate_ui_catalog
    validate_ui_catalog(document.get('extensions', {}).get('phemacast:ui'))
    design = document['design']
    defaults={}
    for key,value in design['params'].items():
        schema_check(value)
        if 'default' in value:
            defaults[key]=value['default']
    if defaults:
        from .schema_security import validate
        validate({'type':'object','properties':design['params']},defaults)
    grid = design['layout']
    children = grid['children']
    requirements = design['dataRequirements']
    from prompits.core.resource_registry import validate_ref
    for requirement in requirements:
        if requirement.get('modelRef'):
            validate_ref(requirement['modelRef'])
        if set(requirement.get('optionalFields', [])) - set(requirement['fields']):
            raise ValueError('Optional fields must belong to the requested model fields.')
    for label, values in [('surface', children), ('requirement', requirements)]:
        if len({v['id'] for v in values}) != len(values):
            raise ValueError('Duplicate layout ' + label + ' identifier.')
    requirement_ids = {r['id'] for r in requirements}
    tabs = grid.get('tabbedView', {}).get('tabs', [])
    tab_ids = {t['id'] for t in tabs}
    if len(tab_ids) != len(tabs):
        raise ValueError('Duplicate layout tab identifier.')
    view = grid.get('tabbedView')
    positioned = children + ([view] if view else [])
    if view and view['id'] in {n['id'] for n in children}:
        raise ValueError('Tab container and surface identifiers must differ.')
    if isinstance(grid['columns'], int):
        if grid.get('areas'):
            raise ValueError('A numeric grid uses positions, not named areas.')
        for node in positioned:
            p = node.get('position')
            if not p or 'area' in node or p['column'] + p['columnSpan'] > grid['columns'] + 1 + 1e-7 or p['row'] + p['rowSpan'] > 25 + 1e-7:
                raise ValueError('Layout surface position is outside its grid.')
        for index, node in enumerate(positioned):
            if any(node.get('tabId') == other.get('tabId') and overlaps(node['position'], other['position']) for other in positioned[:index]):
                raise ValueError('Layout surfaces overlap in the same tab.')
    else:
        areas = grid.get('areas', [])
        if view or not areas or len({len(row) for row in areas}) != 1 or len(areas[0]) != len(grid['columns'].split()):
            raise ValueError('Named grid areas must match the declared columns.')
        cells = {area: [(x, y) for y, row in enumerate(areas) for x, value in enumerate(row) if value == area] for area in {a for row in areas for a in row}}
        for points in cells.values():
            if len(points) != (max(x for x, y in points) - min(x for x, y in points) + 1) * (max(y for x, y in points) - min(y for x, y in points) + 1):
                raise ValueError('Named grid areas must be rectangular.')
        if {n.get('area') for n in children} != set(cells) or len(children) != len(cells) or any('position' in n for n in children):
            raise ValueError('Each named grid area must have exactly one surface.')
    for node in children:
        validate_requirement(node['renderer'])
        if FIELD_FORMATS in node.get('extensions', {}):
            validate_field_formats(node['extensions'][FIELD_FORMATS])
        if node.get('tabId') and node['tabId'] not in tab_ids:
            raise ValueError('Surface refers to an undeclared tab.')
        if node.get('requirementId') and node['requirementId'] not in requirement_ids:
            raise ValueError('Surface refers to an undeclared data requirement.')
        if any(r not in requirement_ids for r in node.get('supplementalRequirementIds', [])):
            raise ValueError('Surface refers to an undeclared supplemental requirement.')
        renderer = node['renderer']['id']
        if node['renderer'].get('required') is False and not node.get('fallback'):
            raise ValueError('An optional renderer requires a visible fallback.')
        if node.get('fallback') is not None:
            validate_media(node['fallback'], 'text')
        if renderer == 'phemacast:pulse-chat':
            from .pulse_chat import validate_config
            validate_config(node.get('media'))
            bound = next((r for r in requirements if r['id']==node.get('requirementId')), None)
            if bound and bound['mode'] != 'interactive':
                raise ValueError('Pulse chat requirements must wait for user interaction.')
            if node.get('referenceId'):
                raise ValueError('Pulse chat executes only on explicit submission, not through a deferred reference.')
        if node.get('media') is not None:
            kind = renderer.removeprefix('phemacast:media.')
            if kind in MEDIA_SCHEMAS:
                validate_media(node['media'], kind)
            elif renderer in CUSTOM_SCHEMAS:
                validate_custom(node['media'], node['renderer'])
    return deepcopy(document)


def layout_requirements(document):
    grid = document['design']['layout']
    extended_tabs = len(grid.get('tabbedView', {}).get('tabs', [])) > 8
    requirements = [
        {'id': 'phemacast:composition', 'version': '1.1.0'},
        {**CAPABILITY, **({'version': '1.1.0'} if extended_tabs else {})},
        {'id': 'phemacast:grid', 'version': '1.1.0', 'limits': {'surfaces': len(grid['children'])}},
    ]
    if grid.get('tabbedView'):
        requirements.append({'id': 'phemacast:tabs', 'version': '1.2.0' if extended_tabs else '1.1.0', 'limits': {'tabs': len(grid['tabbedView']['tabs'])}})
    for node in grid['children']:
        requirements.append(deepcopy(node['renderer']))
        if node.get('fallback'):
            requirements.append({'id': 'phemacast:media.text', 'version': '1.1.0'})
        if node['renderer']['id'] == 'phemacast:media.scene':
            media = node.get('media', {})
            features = ([media['asset']['format']] if media.get('asset') else []) + (['timeline'] if media.get('timeline') else []) + (['mesh'] if any(o.get('shape') == 'mesh' for o in media.get('objects', [])) else [])
            if features:
                requirements.append({'id': 'phemacast:media.scene', 'version': '1.1.0', 'features': features})
    # Keep exact declarations stable across Python and JavaScript roundtrips.
    return [r for index, r in enumerate(requirements) if r not in requirements[:index]]


def from_layout(document, *, references=(), components=(), extensions=None, capabilities=(), owner=''):
    """Create a generic authored Phema; hosts resolve its declared inputs later."""
    from .composition import PROFILE, NODE, validate_composition
    from .phema import Phema
    document = validate_layout(document)
    requirements = layout_requirements(document)
    requirements += [{'id': 'phemacast:resolve.' + kind, 'version': '1.0.0'} for kind in sorted({r['kind'] for r in references})]
    requirements += deepcopy(list(capabilities))
    content = {'type': NODE, 'version': '1.0.0', 'presentation': {'grammar': GRAMMAR, 'document': document}, 'capabilities': requirements, 'references': deepcopy(list(references)), 'extensions': deepcopy(extensions or {})}
    if components:
        content['components'] = deepcopy(list(components))
    return validate_composition(Phema(name=document['name'], description=document['description'], phema_id=document['id'], owner=owner,
        input_schema={'type': 'object', 'properties': deepcopy(document['design']['params']), 'additionalProperties': False},
        sections=[{'name': 'Composition', 'content': [content]}], meta={'profile': PROFILE}, resolution_mode='dynamic').to_dict())
