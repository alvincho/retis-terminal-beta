"""Per-Browser policies persist in server workspaces, outside publications."""
import copy
from fastapi import HTTPException
from phemacast.core.composition import walk_compositions, validate_reference, reference_paths

POLICY_FIELDS={'refresh','maxAgeSeconds','timeoutSeconds','onFailure'}

def validate_overrides(phema,values):
    if not isinstance(values,dict) or len(values)>256:raise HTTPException(422,'Invalid content refresh overrides.')
    refs=reference_paths(phema);result={}
    for path,policy in values.items():
        if path not in refs:continue
        from .source_choice import FIELD, split
        if not isinstance(policy,dict) or set(policy)-POLICY_FIELDS-{FIELD}:raise HTTPException(422,'Invalid content policy.')
        try:validate_reference(split({**refs[path],**policy})[0])
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        result[path]=copy.deepcopy(policy)
    return result

def effective(phema,overrides):
    result=copy.deepcopy(phema)
    for path,current in walk_compositions(result):
        current.get('meta',{}).pop('publication',None)
        for node in current['sections']:
            for composition in node['content']:
                if composition.get('type')!='phemacast:composition':continue
                for ref in composition.get('references',[]):
                    key='/'.join(filter(None,(path,ref['id'])))
                    if key in overrides:ref.update(copy.deepcopy(overrides[key]))
    return result
