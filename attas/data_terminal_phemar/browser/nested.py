"""Ephemeral execution projection; the authored Phema tree remains canonical."""
import copy
import hashlib
from phemacast.core.composition import composition, component_parameters
from ..phema_transfer import to_entry


def project(phema, overrides=None, parameter_values=None):
    root = to_entry(phema)
    layouts = {}
    requirements = root['design']['dataRequirements']
    interactions = []
    from jsonschema import Draft202012Validator
    from phemacast.core.composition_resolution import bind
    def bind_requirements(entry, params):
        for req in entry['design']['dataRequirements']:
            bindings = req.get('parameterBindings', {})
            if not isinstance(bindings,dict) or set(bindings)-{'instruments','arguments'}:
                raise ValueError('Parameter bindings may bind instruments and arguments only.')
            if bindings: req.update(bind(bindings, params, {}))

    def visit(current, params, path):
        for component in composition(current).get('components', []):
            child_path = '/'.join(filter(None, (path, component['id'])))
            child = to_entry(component['phema'])
            bound = component_parameters(component, params)
            prefix = 'nested-' + hashlib.sha256(child_path.encode()).hexdigest()[:20] + '-'
            scope = lambda id: prefix + hashlib.sha256(id.encode()).hexdigest()[:20]
            for key, value in bound.items():
                if key in child['design']['params']: child['design']['params'][key]['default'] = copy.deepcopy(value)
            bind_requirements(child, bound)
            for req in child['design']['dataRequirements']:
                req['id'] = scope(req['id'])
                if req.get('dependsOn'): req['dependsOn'] = scope(req['dependsOn'])
                if 'instruments' in req and req.get('binding') != 'fixed' and not req.get('parameterBindings'): req['instruments'] = bound.get('tickers', [bound.get('ticker')] if bound.get('ticker') else req['instruments'])
                requirements.append(req)
            for node in child['design']['layout']['children']:
                node['id'] = scope(node['id'])
                if node.get('requirementId'): node['requirementId'] = scope(node['requirementId'])
                if node.get('supplementalRequirementIds'): node['supplementalRequirementIds'] = [scope(id) for id in node['supplementalRequirementIds']]
                if node.get('referenceId'): node['referenceId'] = child_path + '/' + node['referenceId']
                if node['type'] == 'phemar' or node.get('template') == 'pulse-chat': interactions.append(node)
            layouts[child_path] = child
            visit(component['phema'], bound, child_path)
    params = {k:v['default'] for k,v in root['design']['params'].items() if 'default' in v}
    if parameter_values:
        if not isinstance(parameter_values,dict): raise ValueError('Phema parameters must be an object.')
        params.update(copy.deepcopy(parameter_values))
    Draft202012Validator(phema.get('input_schema',{})).validate(params)
    for key,value in params.items(): root['design']['params'][key]['default'] = value
    bind_requirements(root, params)
    visit(phema, params, '')
    if len(requirements) > 128: raise ValueError('A Browser supports up to 128 datasets across its Phema tree.')
    for requirement in requirements:
        if requirement.get('editableInBrowser') and requirement['id'] in (overrides or {}):
            tickers=overrides[requirement['id']]
            import re
            if not isinstance(tickers,list) or not 1<=len(tickers)<=12 or any(not isinstance(t,str) or len(t)>40 or not re.fullmatch(r'\^?[A-Z0-9]+(?:[.-][A-Z0-9]+)*(?:=[A-Z]+)?',t) for t in tickers): raise ValueError('Provide 1–12 Yahoo-style tickers.')
            requirement['instruments']=list(dict.fromkeys(tickers))
    for child in layouts.values():
        for node in child['design']['layout']['children']:
            primary=next((r for r in requirements if r['id']==node.get('requirementId')),{})
            if primary.get('editableInBrowser') and primary['id'] in (overrides or {}):
                for req in requirements:
                    if req['id'] in node.get('supplementalRequirementIds',[]) and (req.get('schema') == 'company.profile.v1' or not node.get('underlyingQuotes')):
                        req['instruments']=primary['instruments'][:]
    return root, layouts, interactions


def visible_requirements(entry, layouts, tabs):
    """Only visible nested pages poll; dependency inputs remain in the plan."""
    if not entry['design'].get('layout'):
        return {r['id'] for r in entry['design']['dataRequirements']}
    if not isinstance(tabs,dict) or len(tabs)>64 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in tabs.items()):
        raise ValueError('Layout tabs must be a bounded view selection.')
    selected = set()
    def visit(current, path=''):
        layout=current['design']['layout']; view=layout.get('tabbedView')
        active=next((t['id'] for t in view['tabs'] if t['id']==tabs.get(path or 'root')),view['tabs'][0]['id']) if view else None
        for node in layout['children']:
            if node.get('tabId') and node['tabId']!=active: continue
            selected.update([node['requirementId']] if node.get('requirementId') else [])
            selected.update(node.get('supplementalRequirementIds',[]))
            if node.get('componentId'):
                child_path='/'.join(filter(None,(path,node['componentId'])))
                if child_path in layouts: visit(layouts[child_path],child_path)
    visit(entry)
    requirements={r['id']:r for r in entry['design']['dataRequirements']}
    for _ in range(len(requirements)):
        needed={requirements[id]['dependsOn'] for id in selected if id in requirements and requirements[id].get('dependsOn')}
        if needed<=selected:break
        selected.update(needed)
    return selected
