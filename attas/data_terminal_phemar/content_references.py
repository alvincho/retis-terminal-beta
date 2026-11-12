"""Terminal host bindings for portable references. Every target is authorized here."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
from urllib.parse import quote
from fastapi import HTTPException
from phemacast.core.composition import validate_reference
from phemacast.core.composition_media import validate_media


async def resolve_reference(network, store, user, ref, inputs):
    validate_reference(ref)
    target, kind = ref['target'], ref['kind']
    if kind == 'api' and target.get('agentId') == 'plaza':
        raise HTTPException(410, 'Open this content through its owning agent workspace. Internal Plaza operations are not container content sources.')
    if kind == 'asset':
        record = store.get(user['id'], 'composition-asset', target['assetId'] + ':' + target['version'])
        value = validate_media(record['value'], record['kind'])
        return {'value': value, 'provenance': {'assetId': target['assetId'], 'version': target['version'], 'owner': user['id']}}
    agent = await network.agent(target['agentId'], user['token'])
    await network.require_trusted(agent, user['token'])
    provenance = {'agentId': agent['id'], 'kind': kind}
    if kind == 'phemar-snapshot':
        response = await network.agent_request(agent,user['token'],'GET','/api/phema-snapshots/' + quote(target['snapshotId'],safe=''),timeout=20)
        response.raise_for_status()
        snapshot = response.json().get('snapshot')
        if not isinstance(snapshot, dict): raise HTTPException(422, 'Publisher did not return a snapshot.')
        # Older publishers expose snapshot APIs without row-level access checks.
        # Require a public snapshot or authoritative owner-use verification.
        if snapshot.get('visibility', snapshot.get('meta', {}).get('visibility')) != 'public':
            await require_owner(network, user, agent['id'])
        return {'value': snapshot, 'provenance': {**provenance, 'snapshotId': target['snapshotId']}}
    if kind == 'phemar-generate':
        # Legacy Phemar generation has no Terminal settlement adapter. Permit
        # verified owner execution only; nested quoted publishers must be owned
        # too. Public paid generators can expose an ordinary billable Pulse.
        await require_owner(network, user, agent['id'])
        body = {'phema_id': target['phemaId'], 'params': inputs}
        response = await network.agent_request(agent,user['token'],'POST','/api/phemas/quote',json=body,timeout=20)
        response.raise_for_status(); estimate = response.json()
        if estimate.get('status') != 'quoted' or not isinstance(estimate.get('quotes'), list): raise HTTPException(409, 'Generation quote is unavailable.')
        for row in estimate['quotes']:
            if row.get('cost_points', 0): await require_owner(network, user, row.get('agent_id', ''))
        response = await network.agent_request(agent,user['token'],'POST','/api/generate-phema',json=body,timeout=ref.get('timeoutSeconds',20))
        response.raise_for_status()
        data = response.json()
        if data.get('status') not in (None, 'success', 'completed'): raise HTTPException(502, 'Phemar generation did not complete.')
        return {'value': data, 'provenance': {**provenance, 'phemaId': target['phemaId'], 'ownerUse': True}}
    if kind == 'api':
        raise HTTPException(410, 'Use an owner-published typed operation reference.')
    from .source_choice import FIELD, model_of
    choice = ref.get(FIELD) if kind == 'pulse' and target['pulse'] == 'data_fetch' else None
    if choice:
        # The viewer chose this panel's source in the Browser: ask it for the same model, at the version it serves.
        chosen = agent if choice == agent['id'] else await network.agent(choice, user['token'])
        await network.require_trusted(chosen, user['token'])
        model = model_of(chosen, inputs.get('target_model'))
        if not model:
            raise HTTPException(409, (chosen.get('name') or 'The chosen source') + ' does not serve this panel’s data. Choose another data source.')
        agent, inputs = chosen, {**inputs, 'target_model': model}
        provenance = {**provenance, 'agentId': agent['id']}
    from .source_keys import route as own_key_route
    # A user's key never leaves this Terminal: own-key data requests go to the
    # vendor directly, and the source agent receives no request at all.
    own = own_key_route(store, user, agent) if kind == 'pulse' and target['pulse'] == 'data_fetch' else None
    from . import local_sources
    # The user's own paired copy of this source answers in its place, at no cost; a copy that is down is reported, never bypassed.
    # A source the viewer chose is the one asked: only the automatic choice moves to their paired copy.
    local = None if own or kind != 'pulse' or choice else await local_sources.local_copy(network, user, agent, target['pulse'], inputs)
    if own:
        from .direct_vendor import resolve_own_key
        result = await resolve_own_key(network, agent, user, inputs, own)
        if result.get('status') != 'completed':
            raise HTTPException(502, result.get('error') or 'The vendor did not provide the requested data.')
    elif local:
        result = await local_sources.call(network, user, local, target['pulse'], inputs)
        provenance = {**provenance, 'agentId': local['id'], 'connection': 'local'}
    elif agent.get('localId'):
        result = await local_sources.call(network, user, agent, target['pulse'], inputs)
        provenance = {**provenance, 'connection': 'local'}
    else:
        # A processor never fetches data: this Terminal supplies what it asks for, then calls it again. The whole run
        # is one payment outcome: its paid steps are captured only when it returns a result, else all refunded.
        from .data_requests import run as supply_data
        from . import run_escrow

        async def work():
            first = await network.execute(agent, target['pulse'], inputs, user, store)
            return await supply_data(network, store, user, agent, target['pulse'], inputs, first)
        result = await run_escrow.run(user, work)
    if not isinstance(result, dict) or result.get('status') in {'failed','error','unavailable','auth_required','credential_required'}:
        raise HTTPException(502, 'Registered source did not provide the requested content.')
    value = result.get('data', result.get('result', result.get('output', result)))
    if kind=='channel' and isinstance(value,dict) and isinstance(value.get('items'),list):
        value={**value,'items':[{**item,'publishedAt':item.get('publishedAt') or item.get('published_at'),'updatedAt':item.get('updatedAt') or item.get('updated_at')} for item in value['items'] if isinstance(item,dict)]}
    return {'value': value, 'provenance': {**provenance, 'source':value.get('source',{}) if isinstance(value,dict) else {}, 'billing': result.get('_billing', {})}}


async def require_owner(network, user, agent_id):
    response = await network.client.get(network.plaza + '/api/payments/payees/' + quote(agent_id, safe=''), headers={'Authorization': 'Bearer ' + user['token']}, timeout=20)
    response.raise_for_status(); payee = response.json()
    if payee.get('agentId') != agent_id or payee.get('isOwner') is not True or payee.get('basis') != 'registered-agent-owner':
        raise HTTPException(403, 'This legacy Phemar operation requires verified owner access; use a billable Pulse for another owner.')


async def live_reference(network, store, user, ref, inputs, deliver):
    """Push one stream reference's own-key vendor deliveries until it ends.

    Only own-key streams are live; any other stream reference keeps the value it
    resolved at open. The key choice is checked again before every delivery.
    """
    from .source_keys import route as own_key_route
    from .direct_vendor import live_values
    agent = await network.agent(ref['target']['agentId'], user['token'])
    await network.require_trusted(agent, user['token'])
    own = own_key_route(store, user, agent) if ref['kind'] == 'pulse' and ref['target']['pulse'] == 'data_fetch' else None
    if not own:
        return
    version = (own['record'] or {}).get('credentialVersion')
    async for value in live_values(network, agent, user, inputs, own):
        try:
            current = own_key_route(store, user, agent)
        except HTTPException:
            current = None
        if not current or (current['record'] or {}).get('credentialVersion') != version:
            await deliver({'status': 'unavailable', 'rows': [], 'error': 'Your key for this source changed or was removed.'})
            return
        # A delivery replaces the reference's value, in the same shape a fetch returns.
        await deliver(value.get('data', value))
