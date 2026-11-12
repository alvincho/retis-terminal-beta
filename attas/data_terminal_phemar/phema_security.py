"""Generic host classification and server-backed Phema permission reviews."""
import copy
import ipaddress
from urllib.parse import urlsplit
from fastapi import HTTPException
from phemacast.core.composition import composition, walk_compositions
from phemacast.core.composition_permissions import KEY, manifest, descriptor, fingerprint, result_flows, authorize, requirement_permissions

# Read/action effects come from each exact native operation contract.
# Unresolved legacy operations remain manual actions.


def effect(ref):
    native=ref.get('extensions',{})
    claimed=native.get('phemacast:operation-effects')
    fields={'writesState','externalSend','paidExecution','startsJob','opensStream'}
    if ('phemacast:operation-binding' in native and isinstance(claimed,dict)
            and set(claimed)==fields and all(type(v) is bool for v in claimed.values())):
        # Presentation permission only. NativeForms separately re-resolves
        # the immutable operation and refuses mismatched effects/privileges.
        return 'action' if any(claimed.values()) else 'read'
    if ref['kind'] in {'asset','phemar-snapshot'}:return 'read'
    if ref['kind']=='phemar-generate':return 'generate'
    return 'action'



def upgrade(phema):
    """Make a new authored revision; never rewrite an immutable publication."""
    value=copy.deepcopy(phema)
    for _,current in reversed(list(walk_compositions(value))):
        current.get('meta',{}).pop('publication',None)
        node=composition(current);old=manifest(current) or {}
        if not any(c['id']=='phemacast:permissions' for c in node['capabilities']):
            node['capabilities'].append({'id':'phemacast:permissions','version':'1.0.0'})
        origins=set(old.get('mediaOrigins',[]));local=set(old.get('localOrigins',[]))
        def scan(item):
            if isinstance(item,dict):
                for key,val in item.items():
                    if key in ('url','poster') and isinstance(val,str):
                        parsed=urlsplit(val)
                        if parsed.scheme in ('http','https') and parsed.hostname and not parsed.username:
                            origin=parsed.scheme+'://'+parsed.netloc
                            try:private=not ipaddress.ip_address(parsed.hostname).is_global
                            except ValueError:private=parsed.hostname=='localhost' or parsed.hostname.endswith('.local')
                            if private:local.add(origin)
                            else:origins.add(origin)
                    scan(val)
            elif isinstance(item,list):
                for child in item:scan(child)
        scan(node['presentation']['document'])
        refs=[];flows=[]
        for ref in node.get('references',[]):
            scan(ref.get('inputs',{}))
            if ref.get('inputs',{}).get('platform')=='youtube':
                origins.update({'https://www.youtube.com','https://youtu.be','https://www.youtube-nocookie.com'})
            prior=next((r for r in old.get('references',[]) if r['id']==ref['id']),{})
            private=ref['kind'] in {'asset','phemar-snapshot'} or effect(ref)=='action'
            refs.append({'id':ref['id'],'effect':effect(ref),'classification':'private' if private else prior.get('classification','public'),'maxPoints':prior.get('maxPoints',0)})
            flows+=result_flows(ref)
            # Actions run only when the viewer asks; a live stream also starts that way.
            if effect(ref)!='read' and ref.get('refresh',{}).get('trigger')!='stream':ref['refresh']={'trigger':'manual'}
        document=node['presentation']['document']
        inputs=document.get('design',{}).get('dataRequirements',[])
        requirements=requirement_permissions(inputs,old.get('requirements',[]))
        private_fields=sorted(set(old.get('privateFields',[])) | {
            field for rule in requirements if rule['classification']!='public' for field in rule['fields']})
        node.setdefault('extensions',{})[KEY]={
            'version':'1','references':refs,'dataFlows':flows,'mediaOrigins':sorted(origins),'localOrigins':sorted(local),
            'requirements':requirements,'privateFields':private_fields,
            'childAuthority':{c['id']:{'effects':sorted({r['effect'] for _,p in walk_compositions(c['phema']) for key in ('references','requirements') for r in (manifest(p) or {}).get(key,[])}),'classifications':sorted({r['classification'] for _,p in walk_compositions(c['phema']) for key in ('references','requirements') for r in (manifest(p) or {}).get(key,[])})} for c in node.get('components',[])},
            'plugins':[id for s in document.get('design',{}).get('layout',{}).get('children',[]) if (id:=(s.get('renderer') or s.get('capability') or {}).get('id','')).startswith(('attas:strategy','attas:display.globe'))]}
    return value


def grant_key(user,phema):return user.get('terminalWindow','')+':'+fingerprint(phema)


def binding_scope(instance):
    if '_dataSelections' not in instance and instance.get('security',{}).get('authorityBindings') is not None:
        return instance['security']['authorityBindings']
    # Global provider preferences do not bind a closed operation-only Phema.
    # Its publisher, operation and inputs are bound by each exact reference.
    requirements=instance.get('entry',{}).get('design',{}).get('dataRequirements',[])
    selections=instance.get('_dataSelections',{}) if requirements else {}
    return {'fieldBindings':instance.get('fieldBindings',{}),'sourceSelections':selections}


def approval(store,user,phema,bindings=None):
    from prompits.core.capabilities import digest
    import time
    proof=user.get('_nativeTrust') or {}
    if (proof.get('actor')==user['id'] and proof.get('window')==user.get('terminalWindow','')
            and proof.get('fingerprint')==fingerprint(phema) and proof.get('expires',0)>time.time()
            and (bindings is None or bindings=={'fieldBindings':{},'sourceSelections':{}})):
        return {'approved':True,'privateData':True,'localNetwork':False,'basis':'current-plaza-agent-trust'}
    try:
        result=store.get(user['id'],'phema-permission',grant_key(user,phema))
        if bindings is not None and result.get('bindingsHash')!=digest(bindings):return None
        return result
    except HTTPException:return None


def review(store,user,phema,body=None,bindings=None):
    if any(not manifest(p) for _,p in walk_compositions(phema)):
        raise HTTPException(409,'Save a new Phema revision with authority declarations before approving.')
    from prompits.core.capabilities import digest
    scope=fingerprint(phema) if bindings is None else digest([fingerprint(phema),bindings])
    if body is not None:
        if body.get('revoke'):
            store.delete(user['id'],'phema-permission',grant_key(user,phema))
        else:
            if body.get('fingerprint')!=scope or body.get('approve') is not True or any(type(body.get(k,False)) is not bool for k in ('privateData','localNetwork')):
                raise HTTPException(409,'Review the current Phema authority before approving.')
            store.put(user['id'],'phema-permission',grant_key(user,phema),{'approved':True,'privateData':body.get('privateData',False),'localNetwork':body.get('localNetwork',False),**({'bindingsHash':digest(bindings)} if bindings is not None else {})})
    return {'fingerprint':scope,'permissions':descriptor(phema),'runtimeBindings':bindings,'approval':approval(store,user,phema,bindings)}


def check(store,user,root,current,ref,context):
    approved=approval(store,user,root)
    rule=authorize(current,ref,context,approved)
    if rule['effect']!=effect(ref):raise PermissionError('Authored effect differs from host operation classification.')
    if (ref['kind'] in {'asset','phemar-snapshot'} or effect(ref)=='action') and rule['classification']=='public':
        raise PermissionError('Owner-scoped assets and snapshots require private data authority.')
    return rule
