"""Declarative authority requests, bound to host approval rather than content trust."""
import copy
import ipaddress
from urllib.parse import urlsplit
from prompits.core.capabilities import digest

KEY='phemacast:permissions'
EFFECTS={'read','generate','action','send','payment'}
CLASSES={'public','private','confidential'}


def requirement_permissions(requirements, declared=()):
    """Preserve declared disclosure ceilings without inferring domain schemas.

    Unknown requirements and expanded public field sets require private-data
    review. Execution transports still verify the owning operation's actual
    classification; this projection cannot downgrade an operation contract.
    """
    by_id = {rule['id']: rule for rule in declared}
    result = []
    for requirement in requirements:
        rule = by_id.get(requirement['id'], {})
        fields = list(requirement.get('fields', []))
        classification = rule.get('classification', 'private')
        if classification not in CLASSES:
            raise ValueError('Unknown disclosure classification.')
        if classification == 'public' and not set(fields) <= set(rule.get('fields', [])):
            classification = 'private'
        result.append({'id': requirement['id'], 'effect': rule.get('effect', 'read'),
                       'fields': fields, 'classification': classification})
    return result


def manifest(phema):
    from .composition import composition
    return composition(phema).get('extensions',{}).get(KEY)


def validate_manifest(value):
    if value is None:return
    if not isinstance(value,dict) or set(value)-{'version','references','dataFlows','mediaOrigins','localOrigins','requirements','privateFields','plugins','childAuthority'} or value.get('version')!='1':
        raise ValueError('Invalid Phema permission manifest.')
    for key in ('references','dataFlows','requirements','privateFields','plugins'):
        if not isinstance(value.get(key,[]),list) or len(value.get(key,[]))>256:
            raise ValueError('Permission declarations exceed limits.')
    ids=set()
    for item in value.get('references',[]):
        if not isinstance(item,dict) or set(item)-{'id','effect','classification','maxPoints'} or item.get('effect') not in EFFECTS or item.get('classification') not in CLASSES:
            raise ValueError('Reference requires an effect and data classification.')
        if not isinstance(item.get('id'),str) or not item['id'] or len(item['id'])>100 or item['id'] in ids:
            raise ValueError('Invalid or duplicate authority identifier.')
        ids.add(item['id'])
        if type(item.get('maxPoints',0)) is not int or not 0<=item.get('maxPoints',0)<=100000:
            raise ValueError('Invalid permission payment ceiling.')
    for flow in value.get('dataFlows',[]):
        if not isinstance(flow,dict) or set(flow)!={'from','to','path'} or any(not isinstance(v,str) or len(v)>200 for v in flow.values()) or flow['path'] and not flow['path'].startswith('/'):
            raise ValueError('Declare each reference field transfer.')
    for requirement in value.get('requirements',[]):
        if not isinstance(requirement,dict) or set(requirement)!={'id','effect','fields','classification'} or requirement.get('effect') not in EFFECTS or requirement.get('classification') not in CLASSES or not isinstance(requirement.get('id'),str):
            raise ValueError('Invalid requirement authority.')
        if not isinstance(requirement.get('fields'),list) or len(requirement['fields'])>128 or any(not isinstance(v,str) or len(v)>200 for v in requirement['fields']):raise ValueError('Invalid field authority.')
    for key in ('privateFields','plugins'):
        if any(not isinstance(v,str) or len(v)>200 for v in value.get(key,[])):raise ValueError('Invalid permission names.')
    children=value.get('childAuthority',{})
    if not isinstance(children,dict) or len(children)>128:raise ValueError('Invalid child authority.')
    for child,ceiling in children.items():
        if not isinstance(child,str) or not isinstance(ceiling,dict) or set(ceiling)!={'effects','classifications'} or not isinstance(ceiling['effects'],list) or not isinstance(ceiling['classifications'],list) or set(ceiling['effects'])-EFFECTS or set(ceiling['classifications'])-CLASSES:
            raise ValueError('Invalid child authority ceiling.')
    for key in ('mediaOrigins','localOrigins'):
        origins=value.get(key,[])
        if not isinstance(origins,list) or len(origins)>64:raise ValueError('Invalid resource origin grants.')
        for origin in origins:
            parsed=urlsplit(origin)
            if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('Resource permissions use exact origins.')
            if parsed.scheme=='http' and key!='localOrigins':raise ValueError('Public media origins require HTTPS.')
            try:private=not ipaddress.ip_address(parsed.hostname).is_global
            except ValueError:private=parsed.hostname=='localhost' or parsed.hostname.endswith('.local')
            if private and key!='localOrigins':raise ValueError('Private network media needs a local origin declaration.')


def result_flows(ref):
    result=[]
    def walk(value):
        if isinstance(value,dict):
            if set(value)=={'$result'}:
                source=value['$result'];result.append({'from':source['reference'],'to':ref['id'],'path':source.get('path','')})
            else:
                for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    walk(ref.get('inputs',{}));walk(ref.get('select',{}))
    return result


def descriptor(phema):
    from .composition import walk_compositions, composition
    nodes=[]
    for path,current in walk_compositions(phema):
        node=composition(current)
        nodes.append({'path':path,'permissions':manifest(current),
            'references':[{k:v for k,v in ref.items() if k not in {'refresh','maxAgeSeconds','timeoutSeconds','onFailure','required','outputSchema'}} for ref in node.get('references',[])],
            'children':[c['id'] for c in node.get('components',[])]})
    return {'version':'1','nodes':nodes}


def fingerprint(phema):
    # Bind consent to the complete authored tree, including literal inputs,
    # schemas, media and finance mappings. Publication timestamps are not authority.
    from .composition import walk_compositions
    value=copy.deepcopy(phema)
    for _,node in walk_compositions(value):node.get('meta',{}).pop('publication',None)
    return digest(value)


def authorize(phema, ref, context, approved):
    permissions=manifest(phema)
    if not permissions or not approved or approved.get('approved') is not True:raise PermissionError('Phema permission review required.')
    rule=next((r for r in permissions.get('references',[]) if r['id']==ref['id']),None)
    if not rule:raise PermissionError('Reference has no authority declaration.')
    if context.get('execute',True) and rule['effect']!='read' and context.get('trigger')!='manual':
        raise PermissionError('This effect requires explicit execution.')
    if rule['classification']!='public' and not approved.get('privateData'):
        raise PermissionError('Private data permission required.')
    declared=permissions.get('dataFlows',[])
    for flow in result_flows(ref):
        if flow not in declared:raise PermissionError('Undeclared cross-reference field transfer.')
        source=next((r for r in permissions.get('references',[]) if r['id']==flow['from']),{})
        if source.get('classification','private')!='public' and not approved.get('privateData'):
            raise PermissionError('Private data sharing permission required.')
    # A child may only use authority explicitly included in every ancestor.
    for ceiling in context.get('ancestorPermissions',[]):
        if rule['effect'] not in ceiling.get('effects',[]) or rule['classification'] not in ceiling.get('classifications',[]):
            raise PermissionError('Child exceeds parent authority.')
    return copy.deepcopy(rule)
