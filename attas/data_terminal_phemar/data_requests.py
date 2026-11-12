"""Answer a processor's data requests for this Terminal's user.

A processor never fetches market data. When it answers a call with a data
request (phemacast.core.data_requests), this Terminal fetches every item as the
user: with the user's own key straight from the vendor, mapped with the Data
Consultant's mappings, or from the source agent on the user's account. Then it
calls the processor again with the answers. Nothing here knows a processor's
business or a particular vendor.
"""
import asyncio
import logging

import httpx
from fastapi import HTTPException

from phemacast.core.data_requests import (LLM_GENERATION, MAX_ROUNDS, MODEL_DATA, SUPPLIED, answer, bounded, checked_request,
                                           is_request)

LOG = logging.getLogger(__name__)


def reason(error):
    detail = getattr(error, 'detail', None)
    return str(detail if isinstance(detail, str) else error)[:300] or type(error).__name__


async def source_data(network, store, user, agent_id, pulse, parameters):
    """One source call, for this user, from exactly the source the processor named: own key to the vendor, else
    that source on the user's account, or the user's own paired copy when that is what it named.

    A processor's source is one its user chose, so a source on Plaza is never swapped for the paired copy here
    (a layout's automatic panels do prefer the copy, content_references.py)."""
    from .source_keys import route, source_of
    from . import local_sources
    agent = await network.agent(agent_id, user['token'])
    await network.require_trusted(agent, user['token'])
    own = route(store, user, agent) if pulse == 'data_fetch' else None
    if own:
        from .direct_vendor import resolve_own_key
        result = await resolve_own_key(network, agent, user, parameters, own)
        connection = 'own'
    elif agent.get('localId'):
        # The user's own paired copy is called directly; Plaza never prices or pays a local resource.
        result = await local_sources.call(network, user, agent, pulse, parameters)
        connection = 'local'
    else:
        result = await network.execute(agent, pulse, parameters, user, store)
        connection = 'source'
    if not isinstance(result, dict) or result.get('status') in {'failed', 'error', 'unavailable', 'auth_required', 'credential_required'}:
        error = (result or {}).get('error') if isinstance(result, dict) else ''
        raise HTTPException(502, (agent.get('name') or agent_id) + ': ' + (error or 'the source did not provide the requested data.'))
    billing = result.pop('_billing', {}) if isinstance(result.get('_billing'), dict) else {}
    result.pop('_connection', None)
    canonical = result.get('canonical_result') if isinstance(result.get('canonical_result'), dict) else {}
    if parameters.get('target_model') and isinstance((result.get('canonical_data') or {}).get('items'), list):
        # A canonical model request is answered by its canonical rows; the source's raw rows and schemas stay here.
        result = {'status': result.get('status'), 'canonical_data': {'items': result['canonical_data']['items']},
                  'canonical_result': {k: canonical[k] for k in ('schema', 'modelRef', 'mappingRefs', 'sourceId', 'execution',
                                                                 'missingFields', 'receivedAt', 'endpointIds', 'verification') if k in canonical}}
    provenance = {'agentId': agent['id'], 'sourceId': source_of(agent).get('source_id', ''), 'connection': connection,
                  'billing': billing, **({'mappingRefs': canonical['mappingRefs']} if canonical.get('mappingRefs') else {})}
    return result, provenance


async def llm_generation(network, store, user, item):
    """One generation with this user's own key for an LLM provider the named agent publishes.

    The key goes only to the provider host pinned when it was saved; the agent is asked for nothing but its
    published endpoint. Using the site's model instead is an ordinary model-data call to that agent."""
    from .direct_vendor import run_llm
    from .source_keys import llm_key, llm_provider
    agent = await network.agent(item['agentId'], user['token'])
    await network.require_trusted(agent, user['token'])
    provider = llm_provider(agent, 'llm.' + item['provider'])
    if provider is None:
        raise HTTPException(409, (agent.get('name') or item['agentId']) + ' does not publish that LLM provider.')
    key, hosts = llm_key(store, user, agent, provider)
    result = await run_llm(provider, item['model'], item['messages'], item.get('maxTokens', 4096), key, hosts)
    provenance = {'agentId': agent['id'], 'sourceId': 'llm.' + provider['id'], 'connection': 'own',
                  'billing': {'points': 0, 'state': 'own-key'}}
    return result, provenance


async def source_list(network, store, user, models):
    """The sources this user can draw each named model from, with the Data Consultant's mappings."""
    from .direct_vendor import model_mappings
    from .source_keys import route, say, source_of
    mappings = await model_mappings(network, user['token'])
    listed = {s['sourceId']: s for s in mappings['sources'] if isinstance(s, dict) and isinstance(s.get('sourceId'), str)}
    rows = []
    for agent in await network.directory(user['token']):
        profile = source_of(agent)
        offered = listed.get(profile.get('source_id'))
        kept = [m for m in (offered or {}).get('models') or [] if isinstance(m, dict) and m.get('schema') in models]
        if not kept:
            continue
        try:
            await network.require_trusted(agent, user['token'])
        except HTTPException:
            continue
        try:
            own = route(store, user, agent)
            credential = 'own' if own else 'none'
        except HTTPException:
            own, credential = None, 'missing'
        access = (profile.get('connectivity') or {}).get('data_access') or {}
        executes = access.get('status') != 'unavailable' and access.get('attempt_ready') is not False

        def usable(model):
            # A Terminal-run mapping needs the user's key; a source-run model needs a source that executes.
            return bool(own and own.get('key')) if model.get('execution') == 'terminal' else credential == 'none' and executes
        # A processor offers only the bar sizes, and the history per bar size, the source provides.
        models_out = [{k: m[k] for k in ('schema', 'modelRef', 'fields', 'intervals', 'defaultInterval', 'historyDays', 'execution', 'delivery') if k in m}
                      for m in kept if usable(m)]
        rows.append({'agentId': agent['id'], 'name': agent.get('name') or profile.get('source_name') or agent['id'],
                     'sourceId': profile.get('source_id'), 'credential': credential, 'available': bool(models_out),
                     # The user's own paired copy, so a processor can offer it first.
                     **({'local': True} if agent.get('localId') else {}),
                     **({} if models_out else {'reason': say(store, user, 'Add your key in Terminal settings → Data sources.') if credential == 'missing'
                                               else 'This source cannot serve this model now.'}),
                     'models': models_out})
    return rows


async def answer_items(network, store, user, request):
    items = checked_request(request)
    semaphore = asyncio.Semaphore(4)

    async def one(item):
        async with semaphore:
            try:
                if item['kind'] == MODEL_DATA:
                    payload, provenance = await source_data(network, store, user, item['agentId'], item['pulse'], item['parameters'])
                    return item['id'], answer(item, status='completed', payload=payload, provenance=provenance)
                if item['kind'] == LLM_GENERATION:
                    payload, provenance = await llm_generation(network, store, user, item)
                    return item['id'], answer(item, status='completed', payload=payload, provenance=provenance)
                return item['id'], answer(item, status='completed', sources=await source_list(network, store, user, item['models']))
            except (HTTPException, httpx.HTTPError, ValueError, KeyError) as error:
                LOG.warning('Data request item %s (%s) failed: %s', item['id'], item['kind'], reason(error)[:200])
                return item['id'], answer(item, status='failed', error=reason(error))
    return bounded(dict(await asyncio.gather(*(one(item) for item in items))))


async def run(network, store, user, agent, pulse, inputs, result):
    """Supply each data request the processor answers with, then call it again (bounded rounds)."""
    for _ in range(MAX_ROUNDS):
        if not is_request(result):
            return result
        supplied = {**(inputs.get(SUPPLIED) or {}), **await answer_items(network, store, user, result)}
        inputs = {**inputs, SUPPLIED: bounded(supplied)}
        result = await network.execute(agent, pulse, inputs, user, store)
    if is_request(result):
        raise HTTPException(502, 'The agent kept asking for more data; nothing was computed.')
    return result
