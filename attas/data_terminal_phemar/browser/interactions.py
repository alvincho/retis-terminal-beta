"""Host error adapter for the shared Phemacast Pulse chat contract."""
from fastapi import HTTPException
from phemacast.core.pulse_chat import prepare_parameters


def interaction_parameters(schema, configured, submitted, text, context, history, config=None):
    try:
        return prepare_parameters(schema, configured, submitted, text, context, history, config)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


async def submit(engine, instance, user, body):
    """Resolve a current registered target, then use the host's authorized transport."""
    import json
    import time
    from phemacast.core.pulse_chat import validate_config
    if instance.get('sourceMode') == 'snapshot':
        raise HTTPException(409, 'Snapshot mode is read-only. Switch to parameters to run a Pulse.')
    children = [n for n in [*instance['entry']['design']['layout']['children'], *instance.get('_nestedInteractions', [])]
                if n.get('type') == 'phemar' or n.get('template') == 'pulse-chat']
    child = next((n for n in children if n['id'] == body.get('nodeId')), None) if body.get('nodeId') else next(iter(children), None)
    if not child:
        raise HTTPException(403, 'This layout has no matching Pulse chat.')
    pending = instance.setdefault('_chatPending', set())
    if child['id'] in pending:
        raise HTTPException(409, 'This Pulse chat already has a request in progress.')
    pending.add(child['id'])
    try:
        try:
            config = validate_config(child.get('media'))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        agent = await engine.network.agent(body.get('agentId'), user['token'])
        await engine.network.require_trusted(agent, user['token'])
        if agent.get('availability') in ('offline', 'unavailable'):
            raise HTTPException(409, 'This Pulser is unavailable.')
        catalog = await engine.network.pulse_catalog(agent, user['token']) if hasattr(engine.network, 'pulse_catalog') else await engine.network.catalog(user['token'])
        # Preserve a deliberately pinned legacy contract; new Pulse chat is selectable by default.
        pinned = child.get('pulseId') if child.get('type') == 'phemar' else None
        pulse_id = pinned or body.get('pulseId') or child.get('pulseId')
        pulse_name = body.get('pulseName') or config.get('target', {}).get('pulse')
        if not pulse_id and not pulse_name:
            raise HTTPException(422, 'Select the publisher and Pulse before sending a message.')
        selected = next((p for p in catalog['pulses'] if p['publisherId'] == agent['id'] and
                         (p['id'] == pulse_id if pulse_id else p.get('provenance', {}).get('endpointId') == pulse_name)), None)
        if not selected or selected.get('executable') is False or selected.get('activity') == 'inactive':
            raise HTTPException(403, 'The selected Pulse is unavailable from this connected Pulser.')
        pulse_name = selected['provenance']['endpointId']
        if body.get('pulseName') and body['pulseName'] != pulse_name:
            raise HTTPException(409, 'The selected Pulse changed; select it again.')
        if config.get('allowTargetSelection') is False and config['target'] != {'agentId': agent['id'], 'pulse': pulse_name}:
            raise HTTPException(403, 'This layout fixes the Pulser and Pulse for this chat.')
        requirement = next((r for r in instance['entry']['design']['dataRequirements'] if r['id'] == child.get('requirementId')), {})
        target_matches = not config.get('target') or config['target'] == {'agentId':agent['id'], 'pulse':pulse_name}
        same_contract = child['pulseId'] == selected['id'] if child.get('pulseId') else target_matches
        if not target_matches:
            config = {k:v for k,v in config.items() if k not in ('arguments','messageField','historyField','contextField')}
        if pinned and requirement.get('schema') and selected['schema'] != requirement['schema']:
            raise HTTPException(409, 'The publisher changed the Pulse contract; review this template in App Consultant.')
        if not any(p.get('id') == 'get_pulse_data' for p in agent['card'].get('practices', [])):
            raise HTTPException(409, 'This Pulser has no authorized Pulse execution practice.')
        schema = selected.get('inputSchema') or {'type':'object'}
        history = [m for m in instance['messages'] if m.get('nodeId') == child['id'] and m.get('agentId') == agent['id'] and m.get('pulseName') == pulse_name]
        context_entry = next((entry for entry in instance.get('nestedLayouts', {}).values() if any(n['id']==child['id'] for n in entry['design']['layout']['children'])), instance['entry'])
        context = {k:v['default'] for k,v in context_entry['design'].get('params', {}).items() if 'default' in v}
        parameters = interaction_parameters(schema, requirement.get('arguments', {}) if same_contract else {}, body.get('arguments', {}), body.get('text', ''), context, history, config)
        response = await engine.network.execute(agent, pulse_name, parameters,
            {**user, 'refresh_requested':True}, engine.store)
        output = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        provenance = {'nodeId':child['id'], 'agentId':agent['id'], 'pulseName':pulse_name, 'pulseId':selected['id']}
        instance['messages'].extend([{'role':'user', 'text':body.get('text') or json.dumps(parameters, ensure_ascii=False), **provenance},
                                     {'role':'assistant', 'text':output, **provenance}])
        instance['messages'] = instance['messages'][-40:]
        instance['sequence'] += 1
        return {'messages':[m for m in instance['messages'] if m.get('nodeId') == child['id']]}
    finally:
        pending.discard(child['id'])
