"""Lossless DBL adapter. Financial semantics stay outside the platform grammar."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: app_consultant. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
from copy import deepcopy
import re
from phemacast.core.layout import GRAMMAR, validate_layout
from phemacast.core.composition_contracts import MEDIA_SCHEMAS

# A panel's display formats, kept as the panel's fieldFormats in a working entry (as layout-document.js does).
FIELD_FORMATS = 'phemacast:fieldFormats'

SURFACE_FIELDS = ('id', 'title', 'position', 'area', 'tabId', 'requirementId', 'supplementalRequirementIds', 'fieldMappings', 'referenceId', 'componentId', 'media', 'fallback')
REQUIREMENT_FIELDS = ('id', 'label', 'fields', 'mode', 'essential', 'schema', 'arguments', 'currency', 'timezone', 'frequency', 'units', 'maxAgeSeconds', 'refreshSeconds', 'refreshMode', 'limit', 'dependsOn', 'modelRef', 'modelContract', 'optionalFields', 'join', 'allowLossy')


def renderer(node):
    if node.get('template') == 'custom':
        return {**deepcopy(node['capability']), 'required': not bool(node.get('fallback'))}
    id = ('phemacast:pulse-chat' if node['type'] == 'phemar' or node.get('template') == 'pulse-chat' else
          'phemacast:nesting' if node['template'] == 'phema' else
          ('phemacast:media.' if node['template'] in MEDIA_SCHEMAS else 'attas:display.') + node['template'])
    return {'id': id, 'version': '1.0.0'}


def parameter_schema(params):
    def convert(value):
        value = deepcopy(value)
        if value.get('type') == 'ticker':
            value['type'] = 'string'
        value.pop('notation', None)
        if 'items' in value:
            value['items'] = convert(value['items'])
        return value
    return {key: convert(value) for key, value in params.items()}


def to_document(entry):
    if entry.get('baseLayoutId') == 'content':
        return content_document(entry)
    design = deepcopy(entry['design'])
    meta = {k: deepcopy(v) for k, v in entry.items() if k not in ('id', 'name', 'description', 'design', 'composition')}
    meta.update(schemaVersion=design['schemaVersion'], params=deepcopy(design['params']))
    design['schemaVersion'] = GRAMMAR
    design['params'] = parameter_schema(design['params'])
    for node in design['layout']['children']:
        formats = node.pop('fieldFormats', None)
        extras = {k: node.pop(k) for k in list(node) if k not in SURFACE_FIELDS}
        node['renderer'] = renderer({**node, **extras})
        node['extensions'] = {'attas:surface': extras, **({FIELD_FORMATS: formats} if formats is not None else {})}
    for req in design['dataRequirements']:
        extras = {k: req.pop(k) for k in list(req) if k not in REQUIREMENT_FIELDS}
        if extras:
            req['extensions'] = {'attas:requirement': extras}
    return validate_layout({**{k: deepcopy(entry[k]) for k in ('id', 'name', 'description')}, 'design': design, 'extensions': {'attas:layout': meta, **({'phemacast:ui':deepcopy(entry['extensions']['phemacast:ui'])} if entry.get('extensions', {}).get('phemacast:ui') else {})}})


def from_document(document):
    document = validate_layout(document)
    meta = deepcopy(document.get('extensions', {}).get('attas:layout'))
    if not isinstance(meta, dict):
        return content_entry(document)
    design = document['design']
    original_params = meta.pop('params')
    if parameter_schema(original_params) != design['params']:
        raise ValueError('Layout parameter hints conflict with the generic contract.')
    design['params'] = original_params
    design['schemaVersion'] = meta.pop('schemaVersion')
    for node in design['layout']['children']:
        declared = node.pop('renderer')
        extensions = node.pop('extensions', {})
        extras = extensions.get('attas:surface', {})
        if extensions.get(FIELD_FORMATS) is not None:
            node['fieldFormats'] = extensions[FIELD_FORMATS]
        expected = renderer({**node, **extras})
        legacy = extras.get('type') == 'phemar' and declared == {'id':'attas:persona-discussion','version':'1.0.0'}
        if any(k in SURFACE_FIELDS for k in extras) or (expected != declared and not legacy):
            raise ValueError('Application surface hints conflict with the generic renderer.')
        node.update(extras)
    for req in design['dataRequirements']:
        extras = req.pop('extensions', {}).get('attas:requirement', {})
        if any(k in REQUIREMENT_FIELDS for k in extras):
            raise ValueError('Application requirement hints cannot override shared fields.')
        req.update(extras)
    if set(meta) - {'baseLayoutId', 'command', 'extensions'}:
        raise ValueError('Unsupported application layout hints.')
    # The layout kind becomes a CSS class in the Terminal page, so it is a plain kind name or nothing.
    if 'baseLayoutId' in meta and not (isinstance(meta['baseLayoutId'], str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', meta['baseLayoutId'])):
        raise ValueError('Unsupported application layout hints.')
    if document.get('extensions', {}).get('phemacast:ui'):
        meta.setdefault('extensions', {})['phemacast:ui'] = deepcopy(document['extensions']['phemacast:ui'])
    return {**{k: document[k] for k in ('id', 'name', 'description', 'design')}, **meta}


def content_entry(document):
    """A neutral working projection for content with no financial application hints."""
    design = deepcopy(document['design'])
    design['schemaVersion'] = 'dbl/0.4' if isinstance(design['layout']['columns'], int) else 'dbl/0.1'
    for node in design['layout']['children']:
        cap = node.pop('renderer')
        # Without them a panel shows raw values: +0.31% as 0.3145, a date as a date and time.
        formats = (node.pop('extensions', None) or {}).get(FIELD_FORMATS)
        if formats is not None:
            node['fieldFormats'] = formats
        kind = cap['id'].removeprefix('phemacast:media.')
        if cap['id'] == 'phemacast:nesting':
            node.update(type='template', template='phema')
        elif cap['id'] == 'phemacast:pulse-chat':
            node.update(type='template', template='pulse-chat')
        elif kind in MEDIA_SCHEMAS:
            node.update(type='template', template=kind)
        else:
            node.update(type='template', template='custom', capability=cap)
    for req in design['dataRequirements']:
        req.pop('extensions', None)
    return {**{k: document[k] for k in ('id', 'name', 'description')}, 'baseLayoutId':'content', 'command':'CONTENT', 'design':design,
            'extensions':{'phemacast:layout':deepcopy(document)}}


def content_document(entry):
    """Reapply local edits while preserving opaque extension and renderer contracts."""
    original = deepcopy(entry.get('extensions', {}).get('phemacast:layout', {}))
    document = {**original, **{k:deepcopy(entry[k]) for k in ('id','name','description')}, 'design':deepcopy(entry['design'])}
    design = document['design']
    design['schemaVersion'] = GRAMMAR
    previous = {n['id']:n for n in original.get('design', {}).get('layout', {}).get('children', [])}
    for node in design['layout']['children']:
        old = previous.get(node['id'], {})
        cap = renderer(node)
        if old.get('renderer', {}).get('id') == cap['id']:
            cap = old['renderer']
        formats = node.get('fieldFormats')
        for key in list(node):
            if key not in SURFACE_FIELDS:
                node.pop(key)
        node['renderer'] = cap
        if old.get('extensions'):
            node['extensions'] = deepcopy(old['extensions'])
        if formats is not None:
            node['extensions'] = {**node.get('extensions', {}), FIELD_FORMATS: formats}
        elif node.get('extensions'):
            node['extensions'].pop(FIELD_FORMATS, None)
            if not node['extensions']:
                node.pop('extensions')
    previous = {r['id']:r for r in original.get('design', {}).get('dataRequirements', [])}
    for req in design['dataRequirements']:
        if previous.get(req['id'], {}).get('extensions'):
            req['extensions'] = previous[req['id']]['extensions']
    return validate_layout(document)
