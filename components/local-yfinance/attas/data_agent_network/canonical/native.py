"""Exact provider-defined models for existing endpoint contracts.

These preserve the provider's documented wire fields. They deliberately
make no claim that a similarly named field has the stronger finance semantics
of a separately published model. Cross-provider equivalence needs a tested map.
"""
from __future__ import annotations
import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import time

from prompits.core.capabilities import digest
from prompits.core.resource_registry import reference
from prompits.core.pulse_models import inspect_schema, validate_payload, validate_pds, resource_uri
from ..scaffold.fields import enrich_dataset_fields, fields_from_schema
from ..scaffold.schema_contract import normalize_schema, array_contract


# Each provider mapping's execution fingerprint covers this file and
# schema_contract.py. Advance this major version whenever either changes;
# published versions are immutable. 2.0.0: pulse models moved to prompits.
NATIVE_MAPPING_VERSION = '2.0.0'


@lru_cache(maxsize=64)
def contract_overlays(vendor):
    path = Path(__file__).parent / 'contracts' / (vendor + '.json')
    return json.loads(path.read_text())['endpoints'] if path.is_file() else {}


def namespace(vendor):
    return 'ai.attas.vendor.' + re.sub('[^a-z0-9-]', '-', vendor.lower())


def resources_for(vendor, endpoint, entry=None):
    model,mapping,_=resource_bundle(vendor,endpoint,entry)
    return model,mapping


def split_contract(schema, ns, key, common):
    """Factor oversized public contracts into immutable schema dependencies."""
    dependencies=[]; resources=[]; definitions=schema.get('$defs', {})
    def size(value):return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())
    def slots(node):
        result=[]
        for name in ('properties','patternProperties','dependentSchemas'):
            result.extend((node[name],k,v) for k,v in node.get(name,{}).items() if isinstance(v,dict))
        for name in ('items','additionalProperties','contains','not','if','then','else'):
            if isinstance(node.get(name),dict):result.append((node,name,node[name]))
        for name in ('anyOf','allOf','oneOf','prefixItems'):
            result.extend((node[name],i,v) for i,v in enumerate(node.get(name,[])) if isinstance(v,dict))
        return result
    def reduce(node):
        for parent,name,child in slots(node):reduce(child)
        if size(node)<400_000:return
        for parent,name,child in sorted(slots(node),key=lambda slot:size(slot[2]),reverse=True):
            if size(child)<2048:continue
            value=copy.deepcopy(child)
            if definitions:value['$defs']=definitions
            own_refs=[ref for ref in dependencies if resource_uri(ref) in json.dumps(value)]
            document={**common,'id':ns+'.schema.'+key[:80]+'.'+digest(value)[:20],
                'title':'Provider contract component','description':'Exact reusable component of a provider response schema.',
                'dependencies':own_refs,'interface':{'schema_language':'json-schema-2020-12',
                    'request_schema':{'type':'object'},'response_schema':value}}
            validate_pds(document)
            from prompits.core.resource_registry import validate_resource
            validate_resource(document)
            ref=reference(document);dependencies.append(ref);resources.append(document)
            parent[name]={'$ref':resource_uri(ref)}
            if size(node)<400_000:break
    output=copy.deepcopy(schema);reduce(output)
    direct=[ref for ref in dependencies if resource_uri(ref) in json.dumps(output)]
    return output,direct,resources


def resource_bundle(vendor, endpoint, entry=None):
    entry = entry or enrich_dataset_fields({'dataset_id':endpoint.endpoint_id,'name':endpoint.name,
        'description':endpoint.description,'data_schema':dict(endpoint.response_schema),
        'fields':fields_from_schema(endpoint.response_schema)})
    # Preserve provider wire fields. The legacy alias view can conflate fields
    # (for example date and time); it must not become a stronger model contract.
    original = {'response':dict(endpoint.response_schema),
                'request':{'type':'object','properties':{p.name:dict(p.schema) for p in endpoint.parameters},
                           'required':[p.name for p in endpoint.parameters if p.required]}}
    overlay = contract_overlays(vendor).get(endpoint.endpoint_id, {})
    if overlay and overlay.get('originalDigest') != digest(original):
        raise ValueError('Provider schema changed; rebuild its exact dependency bundle.')
    native_schema = overlay.get('response') or normalize_schema(original['response'])
    request_schema = overlay.get('request') or normalize_schema(original['request'])
    response_schema = array_contract(native_schema)
    inspect_schema(response_schema); inspect_schema(request_schema)
    ns=namespace(vendor); key=re.sub('[^A-Za-z0-9_-]','_',endpoint.endpoint_id)[:140]
    contract={'response':response_schema,'request':request_schema,'operation':endpoint.operation}
    # Published resources are immutable: a vendor renaming or redescribing an endpoint publishes a new one.
    suffix=digest({**contract,'title':endpoint.name,'description':endpoint.description,'documentation':endpoint.documentation_url})[:16]
    model={'pds_version':'0.2.0','resource_type':'pulse_definition','namespace':ns,
        'id':ns+'.dataset.'+key+'.'+suffix,'version':'1.0.0','title':endpoint.name,
        'description':endpoint.description or 'Provider-defined observations for '+endpoint.endpoint_id,
        'pulse_class':'fact','status':'stable',
        'concept':{'definition':'Provider-defined '+endpoint.endpoint_id+' observations under the exact documented response contract. Legacy field names do not assert cross-provider equivalence.',
                   'entity_types':['provider_observation'],'dimensions':['provider-contract','request-parameters']},
        'interface':{'schema_language':'json-schema-2020-12','request_schema':request_schema,
                    'response_schema':response_schema},
        'extensions':{'ai.attas:semanticLevel':'provider-defined','ai.attas:documentation':endpoint.documentation_url,
                       'ai.attas:endpointId':endpoint.endpoint_id}}
    extra=[]
    if len(json.dumps(model).encode())>450_000:
        common={k:model[k] for k in ('pds_version','resource_type','namespace','version','pulse_class','status','concept')}
        response,dependencies,extra=split_contract(response_schema,ns,key,common)
        model['interface']['response_schema']=response;model['dependencies']=dependencies
    implementation=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).parents[1].joinpath('scaffold/schema_contract.py').read_bytes()).hexdigest()
    mapping={'pds_version':'0.2.0','resource_type':'pulse_mapping','namespace':ns,
        'id':ns+'.mapping.'+key+'.'+suffix,'version':NATIVE_MAPPING_VERSION,'source_system':vendor,
        'target_ref':reference(model),'dependencies':[reference(model)],'relation':'implements',
        'mapping_type':'provider-contract','field_map':{'payload':{'path':'data','relation':'equivalent_match'}},
        'execution':{'kind':'registered','method':'ai.attas.native:'+vendor,'version':'1.0.0','implementationDigest':implementation},
        'coverage':'Preserves native contract semantics; this is not verified equivalence to a financial concept.',
        'extensions':{'ai.attas:endpointId':endpoint.endpoint_id}}
    validate_pds(model);validate_pds(mapping)
    return model,mapping,extra


def installed_native(source, refs=None):
    snapshot=getattr(source,'spec_snapshot',None)
    identity=getattr(snapshot,'content_hash',None) or str(id(snapshot))
    cached=getattr(source,'_native_models',None)
    if cached and cached[0]==identity and not refs:return cached[1]
    entries={e['dataset_id']:e for e in getattr(source,'data_catalog',[]) or []}
    output=[]
    for endpoint in getattr(snapshot,'endpoints',()):
        if refs:
            key = re.sub('[^A-Za-z0-9_-]','_',endpoint.endpoint_id)[:140]
            prefixes = tuple(namespace(source.vendor_id)+'.'+kind+'.'+key+'.' for kind in ('dataset','mapping'))
            if not any(str(ref.get('id','')).startswith(prefixes) for ref in refs):continue
        try:
            model,mapping,dependencies=resource_bundle(source.vendor_id,endpoint,entries.get(endpoint.endpoint_id))
            output.append({'model':model,'mapping':mapping,'dependencies':dependencies,'endpoint':endpoint})
        except (ValueError,TypeError):
            # Unsupported/unfinished vendor schemas remain in the ordinary raw
            # catalog and in the acceptance blockers matrix.
            continue
    if not refs:source._native_models=(identity,output)
    return output


def support(entry):
    model,mapping=entry['model'],entry['mapping']
    fields=list(model['interface']['response_schema']['items'].get('properties',{}))
    return {'modelRef':reference(model),'mappingRef':reference(mapping),'fields':fields[:100],
            'delivery':['snapshot'],'verification':'declared','execution':'source-owned',
            'semanticLevel':'provider-defined','endpointId':entry['endpoint'].endpoint_id,
            'implementationDigest':mapping['execution']['implementationDigest']}


def execute_native(source, request, auth_context):
    entry=next((e for e in installed_native(source,[request['target_model']]) if reference(e['model'])==request['target_model']),None)
    if not entry or request.get('mapping_ref')!=reference(entry['mapping']):
        raise ValueError('The exact provider-defined model and mapping are not installed.')
    endpoint=entry['endpoint']
    if not source.is_endpoint_executable(endpoint):
        raise ValueError('Provider endpoint is not executable; check SDK and product entitlement.')
    query=request.get('model_request') or {};arguments=query.get('arguments') or {}
    fields=query.get('fields') or support(entry)['fields']
    allowed=set(support(entry)['fields'])
    if any(f not in allowed for f in fields):raise ValueError('Fields are outside this exact provider model.')
    validate_payload(entry['model'],arguments,direction='request_schema')
    raw=source.data_fetch({'endpoint_id':endpoint.endpoint_id,'parameters':arguments,'auth_context':auth_context})
    data=raw.get('data')
    rows=data if isinstance(data,list) else [data] if isinstance(data,dict) else [{'value':data}] if data is not None else []
    rows=[row if isinstance(row,dict) else {'value':row} for row in rows]
    if len(rows)>10000:raise ValueError('Canonical result exceeds 10,000 observations; page the endpoint request.')
    issues=[]
    if raw.get('status')=='completed':
        validate_payload(entry['model'],rows,[{'ref':reference(r),'resource':r} for r in entry.get('dependencies',[])])
    else:
        rows=[];issues=[{'status':raw.get('status'),'reason':raw.get('error') or 'Provider request failed.'}]
    received=time.time()
    envelope={'schema':'canonical-result/1','modelRef':reference(entry['model']),'mappingRefs':[reference(entry['mapping'])],
              'payload':rows,'sourceId':source.vendor_id,'receivedAt':received,'endpointIds':[endpoint.endpoint_id],
              'verification':'declared','semanticLevel':'provider-defined','implementationDigest':entry['mapping']['execution']['implementationDigest'],
              'missingFields':sorted(f for f in fields if not rows or any(row.get(f) is None for row in rows)),
              'lineage':[{f:{'status':'available' if row.get(f) is not None else 'missing','sourceId':source.vendor_id,
                            'sourceField':f,'receivedAt':received,'unit':'provider-defined'} for f in fields} for row in rows],
              'issues':issues,'costs':[raw.get('cost',{})]}
    return {**raw,'canonical_result':envelope}
