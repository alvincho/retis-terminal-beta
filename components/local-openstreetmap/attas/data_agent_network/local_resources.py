"""Finance adapters for the generic owner-approved local resource protocol."""
import copy
import logging
import re
import time
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from phemacast.network.service import install_local_resource

# One line per data request in the source's own log, so an error there has context. It names the model or dataset,
# how many instruments and rows, the outcome and the time, never the instruments, values or keys.
LOG = logging.getLogger('attas.local_source')
# An address in an error keeps only its host: its path and query can name instruments or carry a session crumb.
ADDRESS = re.compile(r'(https?://[^\s/?#]+)[^\s]*')


def request_summary(pulse, parameters):
    """`data_fetch market.history.v1, 2 instruments`: what was asked, without what for."""
    target = parameters.get('target_model') or parameters.get('endpoint_id') or parameters.get('dataset_id') or ''
    if isinstance(target, dict):
        target = target.get('id') or ''
    request = parameters.get('model_request') if isinstance(parameters.get('model_request'), dict) else {}
    instruments = request.get('instruments')
    count = len(instruments) if isinstance(instruments, list) else None
    return ' '.join(filter(None, [pulse, str(target)[:120]])) + (f', {count} instrument{"s" * (count != 1)}' if count is not None else '')


def outcome(result):
    """(failed, text) for a source's answer: its status, rows and error, with no address query."""
    if not isinstance(result, dict):
        return False, 'answered'
    status = str(result.get('status') or 'answered')
    rows = (result.get('canonical_data') or {}).get('items') if isinstance(result.get('canonical_data'), dict) else None
    error = ADDRESS.sub(r'\1/…', str(result.get('error') or ''))[:300]
    failed = status in ('failed', 'not_executable', 'error') or bool(error)
    return failed, status + (f', {len(rows)} rows' if isinstance(rows, list) else '') + (': ' + error if error else '')


def public_card(agent):
    # Do not expose owner accounts, arbitrary card extensions, or credentials.
    meta = (agent.agent_card or {}).get('meta', {})
    result = {key: copy.deepcopy(meta[key]) for key in ('data_source', 'data_consultant_runtime', 'supported_pulses', 'model_outputs') if key in meta}
    return {'meta': result}


def source_local(source):
    config = source._config_data.get('local_network')
    if not config or not config.get('acceptLocalConnections'):return
    handlers = {}
    for pulse in ('data_spec', 'data_availability', 'schema_query', 'data_fetch'):
        if pulse == 'data_fetch' and not config.get('allowExecution'):continue
        async def operation(parameters, context, pulse=pulse):
            if any(k in parameters for k in ('auth_context', 'connection_id', 'api_key', 'caller_user_token', 'billing')):
                raise HTTPException(422, 'Local clients cannot select another client’s credentials or payment authority.')
            started, summary = time.monotonic(), request_summary(pulse, parameters)
            try:
                result = await run_in_threadpool(source.fetch_pulse_payload, pulse, parameters, {})
            except (ValueError, TypeError, KeyError):
                LOG.warning('%s: refused as invalid after %.1f s', summary, time.monotonic() - started)
                raise HTTPException(422, 'Invalid source request.')
            except Exception as error:
                LOG.warning('%s: %s after %.1f s', summary, type(error).__name__, time.monotonic() - started)
                raise
            failed, text = outcome(result)
            if failed or pulse == 'data_fetch':
                LOG.log(logging.WARNING if failed else logging.INFO, '%s: %s in %.1f s', summary, text, time.monotonic() - started)
            if pulse == 'data_fetch' and isinstance(result, dict):
                result = {**result, '_billing': {'points': 0, 'state': 'local-owner-grant', 'basis': 'resource-owner-approved-local-execution'}}
            return result
        handlers[pulse] = operation
    streams = {}
    if config.get('allowExecution') and source.vendor_id in ('yfinance', 'fugle'):
        async def quote_stream(parameters, context):
            if set(parameters) - {'schema', 'symbols', 'sequence'}:raise HTTPException(422, 'Unsupported stream parameters.')
            if source.vendor_id == 'yfinance':
                from .provider_runtime.yfinance import yahoo_events, symbols, SCHEMA
                if parameters.get('schema') != SCHEMA:raise HTTPException(422, 'Unsupported quote schema.')
                events = yahoo_events(symbols(parameters.get('symbols')), parameters.get('sequence'))
            else:
                import os
                from .provider_runtime.fugle import fugle_events, stream_symbols, FUGLE_STREAM_SCHEMAS
                if parameters.get('schema') not in FUGLE_STREAM_SCHEMAS:raise HTTPException(422, 'Unsupported quote schema.')
                # Only an operator who owns the data serves it with their own key;
                # everyone else's Terminal calls Fugle with its user's key.
                if (getattr(source, '_config_data', None) or {}).get('operator_owned_data') is not True:raise HTTPException(409, 'Connect your own Fugle key in your Terminal; it calls Fugle directly.')
                connection = source._credential_store.resolve_source('fugle') if source._credential_store else None
                key = (connection or {}).get('api_key') or os.environ.get('FUGLE_MARKETDATA_API_KEY', '')
                if not key:raise HTTPException(401, 'Resource owner must configure the vendor entitlement.')
                events = fugle_events(stream_symbols(parameters.get('symbols')), parameters['schema'], key, parameters.get('sequence'))
            try:
                async for event in events:yield event
            finally:await events.aclose()
        streams['quotes'] = quote_stream
    source._local_resource = install_local_resource(source.app, config, lambda: public_card(source), handlers, streams=streams)


def __getattr__(name):
    # consultant_local belongs to the Data Consultant (attas.data_consultant_phemar.local_resource); this alias keeps
    # old imports working without making a source load the Consultant.
    if name == 'consultant_local':
        from attas.data_consultant_phemar.local_resource import consultant_local
        return consultant_local
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
