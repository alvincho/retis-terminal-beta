"""Shared Phemacast layouts with a backward-compatible Attas DBL adapter."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: app_consultant. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
from __future__ import annotations
import copy
from fastapi import HTTPException
from phemacast.core.phema import Phema
from phemacast.core.composition import PROFILE, NODE, composition, validate_composition, compatible as platform_compatible, publish, walk_compositions, component_parameters
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from prompits.core.capabilities import negotiate, validate_requirement, consolidate_requirements
from phemacast.core.composition_media import MEDIA, validate_media, validate_custom
from phemacast.core.composition_contracts import CUSTOM_SCHEMAS
from phemacast.core.layout import GRAMMAR, CAPABILITY, from_layout, layout_requirements
from .layout_document import to_document, from_document


LEGACY_GRAMMAR = 'attas:dbl/1'


def advertisement():
    """Only installed renderers are advertised; resolver availability is separate."""
    from .build_identity import implementation_hash
    from phemacast.core.renderer_plugins import installed_renderers
    return {'implementationHash':implementation_hash(),'schema': 'capability-advertisement/1', 'capabilities': [
        *(r['capability'] for r in installed_renderers()),
        *([dict(__import__('phemacast.operation_forms_candidate.pulse_forms',fromlist=['CAPABILITY']).JOB_CAPABILITY
                if __import__('os').environ.get('ATTAS_NATIVE_JOB_FORMS')=='1' else
                __import__('phemacast.operation_forms_candidate.pulse_forms',fromlist=['CAPABILITY']).CAPABILITY)]
          if __import__('os').environ.get('ATTAS_NATIVE_OPERATION_FORMS')=='1' else []),
        *([dict(__import__('phemacast.agent_view.contracts',fromlist=['CONFINED_CAPABILITY']).CONFINED_CAPABILITY)] if __import__('os').environ.get('ATTAS_AGENT_VIEW_CONFIGURATION') else []),
        {'id': 'phemacast:composition', 'version': '1.1.0'},
        {'id': 'phemacast:permissions', 'version': '1.0.0'},
        {**CAPABILITY, 'version': '1.1.0'},
        {'id': 'phemacast:nesting', 'version': '1.1.0', 'limits': {'depth': 8, 'components': 64}},
        {'id': 'phemacast:grid', 'version': '1.1.0', 'limits': {'surfaces': 12, 'columns': 12}},
        {'id': 'phemacast:tabs', 'version': '1.2.0', 'limits': {'tabs': 12}},
        {'id': 'phemacast:pulse-chat', 'version': '1.0.0'},
        *({'id': 'phemacast:media.' + name, 'version': '1.1.0', **({'features':['mesh','timeline','gltf','glb']} if name=='scene' else {})} for name in sorted(MEDIA)),
        *({'id': 'phemacast:resolve.' + name, 'version': '1.1.0'} for name in ('pulse','api','channel','phemar-snapshot','phemar-generate','asset')),
        *({'id': id, 'version':'1.1.0'} for id in CUSTOM_SCHEMAS),
    ]}


def requirements(entry, grammar=GRAMMAR):
    if grammar == GRAMMAR:
        return layout_requirements(to_document(entry)) + [
            {'id': 'phemacast:resolve.' + kind, 'version': '1.0.0'}
            for kind in sorted({r['kind'] for r in entry.get('composition', {}).get('references', [])})]
    layout = entry['design']['layout']
    result = [
        {'id': 'phemacast:composition', 'version': '1.0.0'},
        {'id': 'phemacast:grid', 'version': '1.0.0', 'limits': {'surfaces': len(layout['children'])}},
        {'id': 'attas:dbl', 'version': '1.0.0', 'features': [entry['design']['schemaVersion']]},
    ]
    if layout.get('tabbedView'): result.append({'id': 'phemacast:tabs', 'version': '1.0.0', 'limits': {'tabs': len(layout['tabbedView']['tabs'])}})
    for id in sorted({'attas:persona-discussion' if n['type'] == 'phemar' else ('phemacast:nesting' if n['template']=='phema' else 'phemacast:media.text' if n['template']=='custom' else ('phemacast:media.' if n['template'] in MEDIA else 'attas:display.') + n['template']) for n in layout['children']}):
        result.append({'id': id, 'version': '1.0.0'})
    for node in layout['children']:
        media=node.get('media',{})
        if node.get('template')=='scene':
            features=[]
            if media.get('timeline'):features.append('timeline')
            if media.get('asset'):features.append(media['asset']['format'])
            if any(o.get('shape')=='mesh' for o in media.get('objects',[])):features.append('mesh')
            if features:result.append({'id':'phemacast:media.scene','version':'1.1.0','features':features})
        if node.get('template')=='custom':
            validate_requirement(node.get('capability'))
            result.append({**node['capability'],'required':not bool(node.get('fallback'))})
    for kind in sorted({r['kind'] for r in entry.get('composition', {}).get('references', [])}):
        result.append({'id': 'phemacast:resolve.' + kind, 'version': '1.0.0'})
    return result


def from_entry(entry, *, owner='', publisher=None, revision=None, secure=False):
    """Lossless migration; provider choices remain in Browser instance bindings."""
    options = copy.deepcopy(entry.get('composition', {}))
    options['capabilities'] = [c for c in options.get('capabilities', []) if not obsolete_requirement(c)]
    phema = from_layout(to_document(entry), owner=owner, references=options.get('references', []),
                        components=options.get('components', []), extensions=options.get('extensions', {}), capabilities=options.get('capabilities', []))
    if secure:
        from .phema_security import upgrade
        phema=upgrade(phema)
    return publish(phema,publisher,revision) if publisher else validate_composition(phema)


def to_entry(phema):
    value = validate_composition(phema)
    node = composition(value)
    grammar = node['presentation']['grammar']
    if grammar not in (GRAMMAR, LEGACY_GRAMMAR): raise ValueError('This Browser does not implement the presentation grammar.')
    entry = from_document(node['presentation']['document']) if grammar == GRAMMAR else copy.deepcopy(node['presentation']['document'])
    if entry.get('id') != value['id']: raise ValueError('Phema identity must match its layout identity.')
    if node.get('references') or node.get('extensions'):
        entry['composition'] = {'references': copy.deepcopy(node.get('references', [])), 'extensions': copy.deepcopy(node.get('extensions', {}))}
    if node.get('components'):
        entry.setdefault('composition', {'references':[], 'extensions':{}})['components'] = copy.deepcopy(node['components'])
    refs={r['id'] for r in node.get('references', [])}
    base=(layout_requirements(node['presentation']['document']) + [{'id':'phemacast:resolve.'+kind,'version':'1.0.0'} for kind in sorted({r['kind'] for r in node.get('references',[])})]) if grammar == GRAMMAR else requirements(entry, grammar)
    extra=[copy.deepcopy(cap) for cap in node.get('capabilities',[]) if cap not in base]
    if extra:entry.setdefault('composition',{'references':[],'extensions':{}})['capabilities']=extra
    for child in entry['design']['layout']['children']:
        if child.get('template') == 'phema':
            if child.get('componentId') not in {c['id'] for c in node.get('components', [])}: raise ValueError('Nested Phema component is not declared.')
        if child.get('referenceId') and child['referenceId'] not in refs: raise ValueError('Media reference is not declared.')
        if child.get('template') == 'pulse-chat':
            from phemacast.core.pulse_chat import validate_config
            validate_config(child.get('media'))
        elif child.get('media') is not None: validate_custom(child['media'], child.get('capability',{})) if child.get('template')=='custom' else validate_media(child['media'], child.get('template'))
        if child.get('fallback') is not None: validate_media(child['fallback'], 'text')
    declared = {r['id']: r for r in node['capabilities']}
    for needed in base:
        if needed['id'] not in declared: raise ValueError('Phema omits a required renderer capability.')
    return entry


def obsolete_requirement(cap):
    if cap.get('version') not in ('1.0.0', '1.1.0'):
        return False
    if cap.get('id') == 'attas:dbl':
        return not set(cap.get('features', [])) - {'dbl/0.1','dbl/0.2','dbl/0.3','dbl/0.4'} and not cap.get('limits')
    return cap.get('id') == 'attas:persona-discussion' and not cap.get('features') and not cap.get('limits')


def negotiation_phema(phema):
    """Migrate only a transient capability view; never rewrite a publication/hash."""
    value = validate_composition(phema)
    if not any(obsolete_requirement(c) for _, current in walk_compositions(value) for c in composition(current).get('capabilities', [])):
        return value
    entry = to_entry(value)
    for component in entry.get('composition', {}).get('components', []):
        component['phema'] = negotiation_phema(component['phema'])
    return from_entry(entry)


def compatible(phema, advertised):
    return platform_compatible(negotiation_phema(phema), advertised)


async def accept(body, *, check_capabilities=True):
    from .contracts import layout_operation
    try:
        if not isinstance(body, dict): raise ValueError('Expected a Phema request object.')
        if body.get('phema') is not None:
            phema = validate_composition(body['phema'])
            entry = to_entry(phema)
            if 'entry' in body and body['entry'] != entry: raise ValueError('Conflicting Phema and legacy layout representations.')
        else:
            entry = body.get('entry')
            await layout_operation('validate', entry=entry)
            phema = from_entry(entry, secure=True)
        entry = to_entry(phema)
        entry = (await layout_operation('validate', entry=entry))['entry']
        async def validate_tree(current, params):
            from phemacast.core.schema_security import validate_async
            await validate_async(current.get('input_schema', {}), params)
            for component in composition(current).get('components', []):
                child = to_entry(component['phema'])
                await layout_operation('validate', entry=child)
                await validate_tree(component['phema'], component_parameters(component, params))
        await validate_tree(phema, {k:v['default'] for k,v in entry['design']['params'].items() if 'default' in v})
        from .browser.nested import project
        project(phema, body.get('instrumentOverrides'))
        if check_capabilities:
            result = compatible(phema, body.get('capabilities') or advertisement())
            host = compatible(phema, advertisement())
            derived = negotiate(consolidate_requirements([cap for _, current in walk_compositions(phema) for cap in requirements(to_entry(current))]), body.get('capabilities') or advertisement())
            if not derived['compatible']: raise HTTPException(422, {'message':'Missing renderer capabilities.', 'compatibility':derived})
            if not result['compatible'] or not host['compatible']:
                raise HTTPException(422, {'message': 'Required Phema capabilities are unavailable.', 'compatibility': result, 'host': host})
        return phema, entry
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc


def hydrate(record):
    """Legacy read projection; persisted records retain only the Phema."""
    if record.get('phema'): return {**record, 'entry': to_entry(record['phema'])}
    return record
