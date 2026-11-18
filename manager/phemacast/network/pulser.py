"""Expose selected Pulser operations as a local resource this computer's Terminal uses (no pairing)."""
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from .service import install_local_resource


def install_pulser_local(pulser, *, views=None, phemas=None):
    """`views` (release digest -> folder) serves the pulser's agent views; the card's `phemacast_agent_views`
    catalog is described with them. `phemas` (id -> {name, revision, phema}) are Phemas the pulser ships, which
    the Terminal keeps in its own library."""
    config = pulser.raw_config.get('local_network')
    if not config:
        return None
    allowed = set(config.get('pulses', []))

    def describe():
        public_fields = {'name', 'description', 'input_schema', 'output_schema', 'tags'}
        pulses = [
            {k: v for k, v in p.items() if k in public_fields}
            for p in pulser.supported_pulses if p.get('name') in allowed]
        meta = {'supported_pulses': pulses}
        catalog = ((getattr(pulser, 'agent_card', None) or {}).get('meta') or {}).get('phemacast_agent_views')
        if views is not None and catalog:
            meta['phemacast_agent_views'] = catalog
        return {'name': pulser.name, 'pulses': pulses, 'meta': meta}

    async def schema(parameters, caller):
        return describe()

    async def fetch(parameters, caller):
        if not config.get('allowExecution'):
            raise HTTPException(403, 'Local execution is disabled.')
        if set(parameters) != {'pulse_name', 'params'} or parameters.get('pulse_name') not in allowed or not isinstance(parameters.get('params'), dict):
            raise HTTPException(422, 'Select a shared pulse and its parameters.')
        params = parameters['params']
        if any(k in params for k in ('api_key', 'openai_api_key', '_caller', 'auth_context', 'caller_user_token')):
            raise HTTPException(422, 'Provider credentials and caller overrides are not accepted.')
        return await run_in_threadpool(pulser.get_pulse_data, input_data=params, pulse_name=parameters['pulse_name'])

    handlers = {'schema_query': schema, 'fetch': fetch}
    # Terminal pulse execution addresses operations by the advertised pulse name.
    # Keep the generic fetch envelope for direct local clients as well.
    if config.get('allowExecution'):
        for pulse_name in allowed:
            if pulse_name in handlers:
                raise ValueError('Shared pulse name conflicts with a local-resource operation.')
            async def execute(parameters, caller, pulse_name=pulse_name):
                return await fetch({'pulse_name': pulse_name, 'params': parameters}, caller)
            handlers[pulse_name] = execute
    return install_local_resource(pulser.app, config, describe, handlers, views=views, phemas=phemas)
