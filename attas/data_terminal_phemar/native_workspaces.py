"""Discover inert workspace entries declared by actual registered publishers."""
from phemacast.core.composition import validate_composition,composition,reference_paths
from phemacast.operation_forms_candidate.pulse_forms import binding_of
from prompits.core.operation_contracts import EFFECTS


def entry_phema(agent,user):
    meta=agent.get('meta',{})
    owners=[value for value in (meta.get('owner_user_id'),agent.get('card',{}).get('owner_user_id')) if value]
    if not user:return None
    owned=bool(owners) and all(value==user['id'] for value in owners)
    # An entry is one read-only practice call, so Plaza's current decision that this
    # account may call the agent's practices is enough. UI and configuration stay owner-only.
    approved=(agent.get('_plazaTrusted') is True and agent.get('_plazaScope')=='practice'
              and agent.get('_plazaViewer')==user['id'])
    if not (owned or approved):return None
    value=meta.get('native_phema_entry')
    if not isinstance(value,dict):return None
    try:
        value=validate_composition(value)
        if composition(value).get('extensions',{}).get('phemacast:source_mode')!='parameters':return None
        refs=reference_paths(value)
        if len(refs)!=1:return None
        ref=next(iter(refs.values()))
        if (ref.get('kind')!='pulse' or ref['target']['agentId']!=agent['id'] or ref.get('inputs')
                or ref.get('refresh')!={'trigger':'manual'} or not binding_of(ref)
                or ref.get('extensions',{}).get('phemacast:operation-effects')!={k:False for k in EFFECTS}
                or ref.get('interaction',{}).get('inputSchema')!={'type':'object','properties':{},'additionalProperties':False}):return None
        return value
    except (ValueError,KeyError,TypeError):return None


def mount_native_workspaces(app,principal,network):
    from fastapi import Request
    @app.get('/api/agent-workspaces')
    async def entries(request:Request):
        user=await principal(request)
        result=[]
        for agent in await network.trusted_agents(user['token']):
            value=entry_phema(agent,user)
            if value:result.append({'name':str(agent['name'])[:150],'phema':value,'source_mode':'parameters'})
            if len(result)>=50:break
        return {'entries':result}
