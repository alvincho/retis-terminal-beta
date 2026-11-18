"""Close provider schema references and translate documented schema dialects.

No network resolution is allowed during validation or execution. Callers supply
the official specification document when migrating a legacy endpoint snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter


def normalize_schema(schema, document=None):
    document = document if document is not None else schema
    definitions, pending = {}, {}

    def target(ref):
        value = document
        if ref == '#': return value
        if not ref.startswith('#/'):
            if isinstance(document, dict) and ref in document.get('schemas', {}):
                return document['schemas'][ref]
            raise ValueError('Provider schema reference is not bundled: ' + ref)
        try:
            for part in ref[2:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError('Provider schema dependency is missing: ' + ref) from None
        return value

    def visit(node, depth=0):
        if depth > 40:
            raise ValueError('Provider schema nesting exceeds 40 levels.')
        if isinstance(node, bool): return node
        if not isinstance(node, dict): raise ValueError('Provider schema must be an object or boolean.')
        result = copy.deepcopy(node)
        for annotation in ('description', 'title', '$comment'):
            if result.get(annotation, '') is None: result.pop(annotation)
        if isinstance(result.get('examples'), dict):
            result['examples'] = [v.get('value') if isinstance(v,dict) else v for v in result['examples'].values()]
        if '$ref' in result:
            ref = result['$ref']
            key = 'ref_' + hashlib.sha256(ref.encode()).hexdigest()[:20]
            if key not in pending:
                pending[key] = target(ref)
            result['$ref'] = '#/$defs/' + key
        # Google Discovery marks individual parameter schemas as required.
        # Their parent contract already carries the required parameter names.
        if isinstance(result.get('required'), bool): result.pop('required')
        elif isinstance(result.get('required'), list): result['required'] = list(dict.fromkeys(result['required']))
        # Swagger primitive aliases retain their actual wire representation.
        primitive = result.get('type')
        if primitive == 'any': result.pop('type')
        if isinstance(primitive, str) and primitive in ('date', 'datetime', 'date-time', 'int32', 'int64', 'float', 'double', 'file'):
            result['type'] = {'date':'string','datetime':'string','date-time':'string','file':'string',
                              'int32':'integer','int64':'integer','float':'number','double':'number'}[primitive]
            result.setdefault('format', {'datetime':'date-time','file':'binary'}.get(primitive, primitive))
        for bound in ('minimum', 'maximum'):
            exclusive = 'exclusive' + bound.title()
            if isinstance(result.get(exclusive), bool):
                if result.pop(exclusive) and bound in result: result[exclusive] = result.pop(bound)
        for key in ('properties', 'patternProperties', 'dependentSchemas'):
            if isinstance(result.get(key), dict): result[key] = {k:visit(v,depth+1) for k,v in result[key].items()}
        for key in ('items', 'additionalProperties', 'contains', 'not', 'if', 'then', 'else', 'propertyNames', 'unevaluatedProperties', 'unevaluatedItems'):
            if isinstance(result.get(key), (dict, bool)): result[key] = visit(result[key],depth+1)
        for key in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
            if isinstance(result.get(key), list): result[key] = [visit(v,depth+1) for v in result[key]]
        # Definitions are bundled on demand; leaving the old root would make
        # relative references ambiguous when the result is wrapped as an array.
        result.pop('$defs', None); result.pop('definitions', None)
        if result.pop('nullable', False):
            result = {'anyOf':[result, {'type':'null'}]}
        return result

    output = visit(schema)
    while set(pending) - set(definitions):
        if len(pending)>512: raise ValueError('Provider schema exceeds 512 dependencies.')
        key = next(k for k in pending if k not in definitions)
        definitions[key] = visit(pending[key])
    if definitions:
        output = {**output, '$defs':definitions}
    return compact_schema(output)


def compact_schema(schema):
    """Intern repeated sub-schemas, preserving constraints and annotations.

    Some vendor contracts repeat a complete object hundreds of times. Local
    references retain that contract without enlarging registry request limits.
    Instance data in defaults/examples is deliberately never rewritten.
    """
    maps=('properties','patternProperties','dependentSchemas','$defs')
    singles=('items','additionalProperties','contains','not','if','then','else','propertyNames','unevaluatedProperties','unevaluatedItems')
    lists=('allOf','anyOf','oneOf','prefixItems')
    counts=Counter(); values={}
    def children(node,fn):
        value=dict(node)
        for key in maps:
            if isinstance(value.get(key),dict):value[key]={k:fn(v) for k,v in value[key].items()}
        for key in singles:
            if isinstance(value.get(key),(dict,bool)):value[key]=fn(value[key])
        for key in lists:
            if isinstance(value.get(key),list):value[key]=[fn(v) for v in value[key]]
        return value
    def measure(node):
        if not isinstance(node,dict):return node
        raw=json.dumps(node,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        if len(raw)>160:
            key=hashlib.sha256(raw.encode()).hexdigest()[:24];counts[key]+=1;values[key]=node
        children(node,measure);return node
    measure(schema)
    chosen={key for key,count in counts.items() if count>1}
    if not chosen:return schema
    defs={};active=set()
    def rewrite(node):
        if not isinstance(node,dict):return node
        raw=json.dumps(node,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        key=hashlib.sha256(raw.encode()).hexdigest()[:24] if len(raw)>160 else ''
        if key in chosen:
            name='shared_'+key
            if name not in defs and key not in active:
                active.add(key);defs[name]=children(node,rewrite);active.remove(key)
            return {'$ref':'#/$defs/'+name}
        return children(node,rewrite)
    output=children(schema,rewrite)
    output['$defs']={**output.get('$defs',{}),**defs}
    return output


def array_contract(schema):
    """Wrap observations without breaking their root-local JSON references."""
    definitions = schema.get('$defs', {})
    payload = {k:v for k,v in schema.items() if k != '$defs'}
    if payload.get('type') == 'array':
        result = {**payload, 'maxItems':min(payload.get('maxItems',10000),10000)}
        item = result.get('items', {})
        if isinstance(item, dict) and item.get('type') not in (None, 'object'):
            result['items'] = {'type':'object','properties':{'value':item},'required':['value']}
    else:
        if payload.get('type') not in (None, 'object'):
            payload = {'type':'object','properties':{'value':payload},'required':['value']}
        result = {'type':'array','maxItems':10000,'items':payload}
    if definitions: result['$defs'] = definitions
    return result
