"""Own-key requests, sent by this Terminal straight to the vendor.

The source agent publishes the vendor's API spec and stream protocol; the Data
Consultant publishes the declarative mappings from vendor replies to canonical
models (a source knows no canonical model). This module only runs those
publications, and only against the vendor hosts pinned when the user saved the
key. The key is never sent to an agent, to Plaza, or to any host other than a
pinned vendor origin. Nothing here knows a particular vendor.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import ssl
import time
from urllib.parse import quote, urlsplit

import certifi
import httpx
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from prompits.core.capabilities import digest
from prompits.core.pulse_mapping import execute_mapping, path_value

STREAM = 'phemacast:stream'
MAPPINGS_PULSE = 'data_model_mappings'
MAPPINGS_SCHEMA = 'phemacast:model-mappings/1'
MAX_BYTES = 8_000_000
TIMEOUT = 30
HEADER = re.compile(r'[A-Za-z0-9-]{1,64}')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,64}')
OPERATION = re.compile(r'/[A-Za-z0-9/_.~{}-]{0,300}')
RESERVED = {'host', 'cookie', 'origin', 'connection', 'content-length', 'content-type', 'transfer-encoding', 'proxy-authorization'}
INPUTS = {'endpoint_id', 'dataset_id', 'parameters', 'target_model', 'model_request', 'mapping_ref', 'mapping_chain'}
CREDENTIAL = '{credential}'
UNPINNED = 'This request is not addressed to a vendor host pinned for your key. Save the key again to accept the source’s current vendor host.'


def tls():
    return ssl.create_default_context(cafile=certifi.where())


def redact(value, key):
    """Remove an echoed key from any vendor value before it leaves this module."""
    if not key:
        return value
    return json.loads(json.dumps(value, allow_nan=False, default=str).replace(key, '[redacted]'))


def key_slot(endpoint):
    """Where the published spec says the vendor expects the key, or None."""
    auth = endpoint.get('authentication') if isinstance(endpoint.get('authentication'), dict) else {}
    header = auth.get('header')
    if isinstance(header, str):
        return {'in': 'header', 'name': header, 'prefix': ''} if HEADER.fullmatch(header) and header.lower() not in RESERVED else None
    parameter = auth.get('parameter') or auth.get('query')
    if isinstance(parameter, str):
        return {'in': 'query', 'name': parameter, 'prefix': ''} if NAME.fullmatch(parameter) else None
    if 'bearer' in (auth.get('provider_scheme'), auth.get('scheme')) or auth.get('scheme') == 'optional_bearer':
        return {'in': 'header', 'name': 'Authorization', 'prefix': 'Bearer '}
    return None


def key_required(endpoint):
    auth = endpoint.get('authentication') if isinstance(endpoint.get('authentication'), dict) else {}
    return auth.get('required') is not False


def pinned(url, hosts, scheme):
    """A pinned vendor origin: exact scheme and host, default port, no user info."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if (not parts or parts.scheme != scheme or parts.hostname not in set(hosts or ())
            or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment):
        raise HTTPException(409, UNPINNED)
    return url


def parameter_specs(endpoint, slot):
    specs = {p['name']: p for p in endpoint.get('parameters') or [] if isinstance(p, dict) and isinstance(p.get('name'), str)}
    if slot and slot['name'] in specs and specs[slot['name']].get('location', 'query') == slot['in']:
        specs.pop(slot['name'])  # The host fills the key slot; a Phema cannot.
    return specs


def checked(specs, parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(specs):
        raise HTTPException(422, 'Unsupported parameters for this vendor endpoint.')
    schema = {'type': 'object', 'additionalProperties': False,
              'properties': {name: dict(spec.get('schema') or {}) for name, spec in specs.items()},
              'required': [name for name, spec in specs.items() if spec.get('required') and spec.get('default') is None]}
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(parameters)
    except ValidationError:
        raise HTTPException(422, 'Parameters do not match the vendor endpoint specification.') from None
    return parameters


def rest_request(endpoint, parameters, key, hosts):
    """The exact request the published spec describes, with the key in its slot."""
    extensions = endpoint.get('extensions') if isinstance(endpoint.get('extensions'), dict) else {}
    base, method = extensions.get('attas.base_url'), str(extensions.get('attas.http_method') or 'GET').upper()
    operation = endpoint.get('operation')
    if endpoint.get('transport') != 'https' or not isinstance(base, str) or method not in ('GET', 'POST'):
        raise HTTPException(409, 'The source does not publish a direct HTTPS request for this endpoint.')
    if not isinstance(operation, str) or not OPERATION.fullmatch(operation) or '..' in operation.split('/'):
        raise HTTPException(409, 'The source publishes an unsupported operation path.')
    pinned(base, hosts, 'https')
    slot = key_slot(endpoint)
    specs = parameter_specs(endpoint, slot)
    path, query, body = operation, {}, {}
    for name, value in checked(specs, parameters).items():
        location = specs[name].get('location', 'query')
        if location == 'path':
            if isinstance(value, (dict, list, bool)) or value is None:
                raise HTTPException(422, 'Path parameters must be plain values.')
            path = path.replace('{' + name + '}', quote(str(value), safe=''))
        elif location == 'query':
            query[name] = ('true' if value else 'false') if isinstance(value, bool) else value
        elif location == 'body' and method == 'POST':
            body[name] = value
        else:
            raise HTTPException(422, 'Unsupported parameter location.')
    if re.search(r'\{[^}]*\}', path):
        raise HTTPException(422, 'A required path parameter is missing.')
    headers = {'Accept': 'application/json'}
    if key and slot:
        if slot['in'] == 'header':
            headers[slot['name']] = slot['prefix'] + key
        else:
            query[slot['name']] = key
    elif key_required(endpoint):
        raise HTTPException(409, 'Add your key for this source in Terminal settings → Data sources.')
    return method, base.rstrip('/') + path, query, headers, body


async def fetch_endpoint(endpoint, parameters, key, hosts, *, transport=None):
    method, url, query, headers, body = rest_request(endpoint, parameters, key, hosts)
    return await send(method, url, query, headers, body, key, endpoint.get('endpoint_id'), transport=transport)


async def send(method, url, query, headers, body, key, endpoint_id, *, transport=None):
    """One vendor request and its JSON answer, the key redacted from anything returned."""
    # No redirects, no environment proxies, only the public CA bundle.
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False, verify=tls(), transport=transport) as client:
        try:
            async with client.stream(method, url, params=query, headers=headers, **({'json': body} if body else {})) as response:
                if 300 <= response.status_code < 400:
                    raise HTTPException(502, 'The vendor redirected the request. Own keys are never sent after a redirect.')
                raw, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(502, 'The vendor response exceeds 8 MB.')
                    raw.append(chunk)
                status = response.status_code
        except httpx.HTTPError:
            # httpx messages can contain the request URL, and so a query-string key.
            raise HTTPException(502, 'The vendor request could not be completed.') from None
    text = b''.join(raw).decode('utf-8', 'replace')
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not 200 <= status < 300:
        detail = data.get('message') if isinstance(data, dict) and isinstance(data.get('message'), str) else text if data is None else ''
        error = f'The vendor returned HTTP {status}.' + (' ' + ' '.join(detail.split())[:200] if detail.strip() else '')
        return redact({'status': 'failed', 'endpoint_id': endpoint_id, 'http_status': status, 'error': error}, key)
    if data is None:
        return {'status': 'failed', 'endpoint_id': endpoint_id, 'error': 'The vendor did not return JSON.'}
    return redact({'status': 'completed', 'endpoint_id': endpoint_id, 'data': data}, key)


# An LLM provider's endpoint, as an agent publishes it (meta.llm_providers): the Terminal fills a body template
# with the messages and calls the provider with the user's own key. No vendor is named here.
LLM_TIMEOUT = 120
LLM_PLACEHOLDERS = ('$model', '$max_tokens', '$system', '$messages', '$messages_with_system')
CREDENTIAL_HEADERS = {'authorization', 'x-api-key', 'api-key', 'proxy-authorization', 'cookie'}


def llm_request(provider, model, messages, max_tokens, key, hosts):
    """The exact request a published LLM endpoint describes, with the key in its slot."""
    endpoint = provider.get('endpoint') if isinstance(provider.get('endpoint'), dict) else {}
    url, body = endpoint.get('url'), endpoint.get('body')
    if endpoint.get('method', 'POST') != 'POST' or not isinstance(url, str) or not isinstance(body, dict) or len(body) > 20:
        raise HTTPException(409, 'The agent does not publish an LLM endpoint your Terminal can call.')
    if model not in {m.get('id') for m in provider.get('models') or [] if isinstance(m, dict)}:
        raise HTTPException(422, 'Choose one of the models this provider offers.')
    pinned(url, hosts, 'https')
    slot = key_slot(endpoint)
    if not key or slot is None or slot['in'] != 'header':
        raise HTTPException(409, 'The LLM endpoint takes no key in a header your Terminal can fill.')
    headers = {'Accept': 'application/json', slot['name']: slot['prefix'] + key}
    for name, value in (endpoint.get('headers') or {}).items():
        if (not isinstance(name, str) or not HEADER.fullmatch(name) or name.lower() in RESERVED | CREDENTIAL_HEADERS
                or not isinstance(value, str) or len(value) > 200 or any(c in value for c in '\r\n\0')):
            raise HTTPException(409, 'The LLM endpoint publishes an unsupported header.')
        headers[name] = value
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    turns = [m for m in messages if m['role'] != 'system']
    values = {'$model': model, '$max_tokens': max_tokens, '$system': system, '$messages': turns,
              '$messages_with_system': ([{'role': 'system', 'content': system}] if system else []) + turns}
    filled = {}
    for name, value in body.items():
        if isinstance(value, str) and value.startswith('$'):
            if value not in LLM_PLACEHOLDERS:
                raise HTTPException(409, 'The LLM endpoint publishes an unknown placeholder.')
            if value == '$system' and not system:
                continue
            filled[name] = values[value]
        else:
            filled[name] = copy.deepcopy(value)
    try:
        timeout = min(float(endpoint.get('timeoutSeconds') or LLM_TIMEOUT), LLM_TIMEOUT)
    except (TypeError, ValueError):
        timeout = LLM_TIMEOUT
    return url, headers, filled, max(1.0, timeout)


async def run_llm(provider, model, messages, max_tokens, key, hosts, *, transport=None):
    """One generation with the user's own key: no redirects, no environment proxies, only the public CA bundle."""
    url, headers, body, timeout = llm_request(provider, model, messages, max_tokens, key, hosts)
    endpoint = provider['endpoint']
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, verify=tls(), transport=transport) as client:
        try:
            async with client.stream('POST', url, headers=headers, json=body) as response:
                if 300 <= response.status_code < 400:
                    raise HTTPException(502, 'The provider redirected the request. Own keys are never sent after a redirect.')
                raw, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(502, 'The provider response exceeds 8 MB.')
                    raw.append(chunk)
                status = response.status_code
        except httpx.HTTPError:
            raise HTTPException(502, 'The provider request could not be completed.') from None
    text = b''.join(raw).decode('utf-8', 'replace')
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not 200 <= status < 300:
        detail = path_value(data, endpoint['error']) if isinstance(data, dict) and isinstance(endpoint.get('error'), str) else None
        detail = detail if isinstance(detail, str) else ''
        error = f'The provider returned HTTP {status}.' + (' ' + ' '.join(detail.split())[:200] if detail.strip() else '')
        raise HTTPException(502, redact(error, key))
    generated = path_value(data, endpoint.get('text')) if isinstance(data, dict) and isinstance(endpoint.get('text'), str) else None
    if not isinstance(generated, str) or not generated.strip():
        raise HTTPException(502, 'The provider returned no text.')
    usage = endpoint.get('usage') if isinstance(endpoint.get('usage'), dict) else {}
    counts = {k: path_value(data, v) for k, v in usage.items() if isinstance(v, str)}
    return redact({'status': 'completed', 'text': generated.strip(), 'provider': provider['id'], 'model': model,
                   'usage': {k: v for k, v in counts.items() if isinstance(v, int)}}, key)


# Published specs are metadata. They are cached briefly per agent and carry no key.
SPECS = {}


async def published(network, agent, token, endpoint_id=''):
    cache = (agent['id'], agent.get('address'), endpoint_id)
    hit = SPECS.get(cache)
    if hit and hit[0] > time.monotonic():
        return copy.deepcopy(hit[1])
    records, seen = [], set()
    for offset in range(0, 5000, 500):
        response = await network.pulse(agent, 'data_spec', {'limit': 500, 'offset': offset, **({'endpoint_id': endpoint_id} if endpoint_id else {})}, token)
        page = response.get('endpoints', []) if isinstance(response, dict) else []
        fresh = [e for e in page if isinstance(e, dict) and e.get('endpoint_id') not in seen]
        records.extend(fresh)
        seen.update(e.get('endpoint_id') for e in fresh)
        if not fresh or len(page) < 500 or offset + len(page) >= int(response.get('total') or 0):
            break
    if len(SPECS) > 64:
        SPECS.clear()
    SPECS[cache] = (time.monotonic() + 60, copy.deepcopy(records))
    return records


# The Data Consultant's mappings are versioned and immutable; one answer is reused briefly.
MAPPINGS = {}


def publishes(agent, pulse):
    return any(isinstance(p, dict) and (p.get('pulse_name') or p.get('name')) == pulse
               for p in (agent.get('meta') or {}).get('supported_pulses') or [])


async def model_mappings(network, token):
    """The vendor-to-canonical mappings the Data Consultant publishes (free)."""
    consultant = next((a for a in await network.directory(token) if publishes(a, MAPPINGS_PULSE)), None)
    if consultant is None:
        raise HTTPException(409, 'No Data Consultant publishes how vendor data maps to models. Your key was not sent anywhere.')
    hit = MAPPINGS.get(consultant['id'])
    if hit and hit[0] > time.monotonic():
        return copy.deepcopy(hit[1])
    answer = await network.pulse(consultant, MAPPINGS_PULSE, {}, token)
    if not isinstance(answer, dict) or answer.get('schema') != MAPPINGS_SCHEMA or not isinstance(answer.get('sources'), list):
        raise HTTPException(502, 'The Data Consultant returned no usable mappings.')
    if len(MAPPINGS) > 16:
        MAPPINGS.clear()
    MAPPINGS[consultant['id']] = (time.monotonic() + 600, copy.deepcopy(answer))
    return answer


def same_model(left, right):
    return isinstance(left, dict) and isinstance(right, dict) and bool(left.get('id')) and \
        (left.get('id'), left.get('version')) == (right.get('id'), right.get('version'))


async def mapped_endpoints(network, agent, token, source_id, target_model, transport):
    """Every source endpoint and Data Consultant mapping that answers this model, in the Consultant's order."""
    from prompits.core.pulse_mapping import validate_execution
    mappings = await model_mappings(network, token)
    source = next((s for s in mappings['sources'] if isinstance(s, dict) and s.get('sourceId') == source_id), {})
    choices = []
    for model in source.get('models') or []:
        if not isinstance(model, dict) or not same_model(model.get('modelRef'), target_model):
            continue
        for projection in model.get('projections') or []:
            if not isinstance(projection, dict) or projection.get('transport') != transport or not isinstance(projection.get('endpointId'), str):
                continue
            endpoint = next((e for e in await published(network, agent, token, projection['endpointId'])
                             if e.get('endpoint_id') == projection['endpointId'] and e.get('transport') == transport), None)
            if endpoint is None:
                continue
            try:
                validate_execution((projection.get('mapping') or {}).get('execution'))
            except (ValueError, TypeError, KeyError):
                raise HTTPException(502, 'The Data Consultant published a mapping this Terminal cannot run.') from None
            choices.append((endpoint, projection))
    return choices


def covers(projection, ticker, bindings):
    """Whether this mapping's instrument rule turns the ticker into a vendor parameter."""
    try:
        instrument_parameters(projection, ticker, bindings)
    except HTTPException as exc:
        if exc.status_code == 422:
            return False
        raise
    return True


def choice_for(choices, tickers, bindings):
    """The first mapping covering every ticker; the first mapping reports a ticker none covers."""
    return next((c for c in choices if all(covers(c[1], t, bindings) for t in tickers)), choices[0])


def as_date(value, round_up=False):
    """An ISO date or instant as a UTC calendar date; an end inside a day keeps that whole day."""
    from datetime import datetime, timedelta, timezone
    try:
        instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(422, 'Request dates must be ISO dates or times.') from None
    if instant.tzinfo is not None:
        instant = instant.astimezone(timezone.utc)
    if round_up and (instant.hour, instant.minute, instant.second, instant.microsecond) != (0, 0, 0, 0):
        instant += timedelta(days=1)
    return instant.date().isoformat()


def argument_parameters(projection, arguments):
    """Canonical request arguments (start, end, interval) as the vendor parameters the mapping names."""
    rules = projection.get('arguments') if isinstance(projection.get('arguments'), dict) else {}
    if arguments is None:
        return {}
    if not isinstance(arguments, dict) or len(arguments) > 16:
        raise HTTPException(422, 'Model request arguments must be an object.')
    output = {}
    for name, value in arguments.items():
        rule = rules.get(name)
        if not isinstance(rule, dict) or not isinstance(rule.get('parameter'), str) or not NAME.fullmatch(rule['parameter']):
            raise HTTPException(422, 'This source answers no ' + str(name)[:40] + ' argument for this model.')
        if isinstance(rule.get('values'), dict):
            if not isinstance(value, str) or value not in rule['values']:
                raise HTTPException(422, str(value)[:40] + ' is not an interval this source offers.')
            value = rule['values'][value]
        if rule.get('format') == 'date':
            value = as_date(value, rule.get('roundUp') is True)
        output[rule['parameter']] = value
    return output


async def model_resource(network, ref, token):
    from phemacast.core.model_execution import verify_bundle
    from prompits.core.capabilities import canonical
    bundle = await network.schema_query({'refs': [ref]}, token)
    records = bundle.get('records') if isinstance(bundle, dict) else None
    try:
        index = verify_bundle(records, [ref])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, 'The canonical model definition is unavailable from Plaza.') from None
    return index[canonical(ref)]['resource'], records


def instrument_parameters(projection, ticker, bindings):
    spec = projection.get('instrument') if isinstance(projection.get('instrument'), dict) else {}
    name = spec.get('parameter')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise HTTPException(409, 'The source’s mapping does not say how instruments become vendor parameters.')
    value = next((b['symbol'] for b in bindings or [] if isinstance(b, dict) and b.get('instrument') == ticker
                  and b.get('type') in (spec.get('bindingTypes') or []) and isinstance(b.get('symbol'), str)), None)
    if value is None:
        pattern = spec.get('pattern')
        if isinstance(pattern, str) and len(pattern) <= 200:
            match = re.fullmatch(pattern, ticker)
            if not match:
                raise HTTPException(422, ticker + ' is outside this source’s coverage.')
            value = match.group(int(spec.get('group') or 0))
        else:
            value = ticker
    return {**copy.deepcopy(projection.get('parameters') or {}), name: value}


def table_view(result, table):
    """Deliver rows in the generic table shape a panel asked for (columns, sort, limit)."""
    if table is None:
        return result
    invalid = HTTPException(422, 'A table names 1-50 unique columns (key and label), and may sort by one of them and keep 1-10000 rows.')
    if not isinstance(table, dict) or set(table) - {'columns', 'sort', 'limit'}:
        raise invalid
    columns, sort, limit = table.get('columns'), table.get('sort') or {}, table.get('limit', 10000)
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 50
            or any(not isinstance(c, dict) or set(c) != {'key', 'label'} or not isinstance(c['key'], str) or not isinstance(c['label'], str) for c in columns)
            or type(limit) is not int or not 1 <= limit <= 10000 or not isinstance(sort, dict)
            or (sort and sort.get('key') not in [c['key'] for c in columns])):
        raise invalid
    rows = [{c['key']: copy.deepcopy(row.get(c['key'])) for c in columns} for row in (result.get('data') or {}).get('items') or []]
    if sort:
        def order(row):
            value = row[sort['key']]
            return (0, value, '') if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, 0, str(value))
        present = sorted((r for r in rows if r[sort['key']] is not None), key=order, reverse=sort.get('descending') is True)
        rows = present + [r for r in rows if r[sort['key']] is None]
    return {**result, 'data': {'columns': copy.deepcopy(columns), 'rows': rows[:limit]}}


def requested_model(inputs, model):
    query = inputs.get('model_request') if isinstance(inputs.get('model_request'), dict) else {}
    tickers = query.get('instruments')
    if (not isinstance(tickers, list) or not 1 <= len(tickers) <= 12
            or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,39}', t) for t in tickers)):
        raise HTTPException(422, 'Provide one to twelve valid listing tickers.')
    fields = query.get('fields', list(model.get('fields') or {}))
    if not isinstance(fields, list) or not fields or any(f not in (model.get('fields') or {}) for f in fields):
        raise HTTPException(422, 'Requested fields must belong to the declared model.')
    return query, tickers, fields


def projected(projection, payload, parameters, ticker, fields):
    match = projection.get('match') if isinstance(projection.get('match'), dict) else None
    if match and str(path_value(payload, match.get('path')) or '').upper() != str(parameters.get(match.get('parameter')) or '').upper():
        raise ValueError('Returned listing does not match the requested instrument.')
    output = execute_mapping(projection['mapping'], payload, {**parameters, 'instrument': ticker})
    rows = [{f: row[f] for f in fields if f in row} for row in output['rows']]
    return rows, output['lineage'], output['issues']


def model_result(inputs, model, used, source_id, rows, lineage, issues, received):
    """`used` lists each (endpoint, mapping) that answered at least one ticker, in first-use order."""
    from prompits.core.resource_registry import reference
    _, _, fields = requested_model(inputs, model)
    missing = sorted(f for f in fields if not rows or any(r.get(f) is None for r in rows))
    mappings = [{'id': p.get('id'), 'version': p.get('version'), 'digest': digest(p['mapping'])} for _, p in used]
    canonical = {'schema': 'canonical-result/1', 'modelRef': reference(model), 'mappingRefs': mappings, 'payload': rows,
                 'lineage': lineage, 'missingFields': missing, 'issues': issues, 'receivedAt': received,
                 'endpointIds': list(dict.fromkeys(e.get('endpoint_id') for e, _ in used)), 'verification': 'declared',
                 'sourceId': source_id, 'execution': 'terminal-direct-vendor'}
    return {'status': 'completed' if rows else 'failed', 'dataset_id': 'model:' + str(model.get('id')),
            'data': {'items': rows}, 'canonical_data': {'items': rows}, 'canonical_result': canonical,
            'warnings': [i.get('reason', '') for i in issues],
            'error': '' if rows else next((f"{i.get('ticker', '')}: {i['reason']}" for i in issues if i.get('reason')), 'No vendor observations satisfy this model request.')}


async def fetch_model(choices, inputs, own, model, records, source_id, *, transport=None):
    """Each ticker goes to the first mapping that covers it (a source may map stocks and futures separately)."""
    from prompits.core.pulse_models import validate_payload
    query, tickers, fields = requested_model(inputs, model)
    rows, lineage, issues, received, used = [], [], [], time.time(), []
    for ticker in tickers:
        endpoint, projection = choice_for(choices, [ticker], query.get('instrumentBindings'))
        if (endpoint, projection) not in used:
            used.append((endpoint, projection))
        parameters = {**instrument_parameters(projection, ticker, query.get('instrumentBindings')),
                      **argument_parameters(projection, query.get('arguments'))}
        result = await fetch_endpoint(endpoint, parameters, own['key'], own['hosts'], transport=transport)
        if result['status'] != 'completed':
            issues.append({'ticker': ticker, 'status': result['status'], 'reason': result.get('error', '')})
            continue
        try:
            found, trace, problems = projected(projection, result['data'], parameters, ticker, fields)
        except (ValueError, TypeError, KeyError) as exc:
            issues.append({'ticker': ticker, 'status': 'invalid', 'reason': str(exc)})
            continue
        rows.extend(found)
        lineage.extend(trace)
        issues.extend({'ticker': ticker, **problem} for problem in problems)
    try:
        validate_payload(model, rows, records)
    except ValueError as exc:
        raise HTTPException(502, 'Mapped vendor data does not match the canonical model: ' + str(exc)[:200]) from None
    result = model_result(inputs, model, used, source_id, rows, lineage, issues, received)
    return table_view(redact(result, own['key']), query.get('table'))


CALL_PLAN_PULSE = 'data_call_plan'
MAX_PLANNED_CALLS = 20
MAX_PLANNED_ROWS = 50_000


async def call_plan(network, agent, token, request, failed=None):
    """The Data Consultant's plan (data_call_plan) for one canonical history request; None without that pulse."""
    consultant = next((a for a in await network.directory(token) if publishes(a, CALL_PLAN_PULSE)), None)
    if consultant is None:
        return None
    params = {'agentId': agent['id'], 'model_request': request}
    if failed:
        params.update(failed=failed, recipe='fallback')
    answer = await network.pulse(consultant, CALL_PLAN_PULSE, params, token)
    return answer if isinstance(answer, dict) and (answer.get('calls') or answer.get('error')) else None


async def fetch_planned(call, key, hosts, *, transport=None):
    """One call of a plan, run as written: HTTPS GET to a host pinned when the key was saved, the key where the plan says."""
    query = call.get('query') if isinstance(call.get('query'), dict) else None
    credential = call.get('credential') if isinstance(call.get('credential'), dict) else {}
    if (call.get('transport') != 'https' or call.get('method') != 'GET' or query is None or len(query) > 32
            or any(not NAME.fullmatch(str(k)) or isinstance(v, (dict, list)) or v is None for k, v in query.items())
            or credential.get('in') not in ('header', 'query') or not NAME.fullmatch(str(credential.get('name') or ''))):
        raise HTTPException(409, 'The Data Consultant planned a call your Terminal cannot run. Your key was not sent anywhere.')
    url = pinned(call.get('url'), hosts, 'https')
    query = {k: ('true' if v else 'false') if isinstance(v, bool) else v for k, v in query.items()}
    headers = {'Accept': 'application/json'}
    if credential['in'] == 'header':
        headers[credential['name']] = key
    else:
        query[credential['name']] = key
    return await send('GET', url, query, headers, {}, key, call.get('endpointId'), transport=transport)


def plan_projections(mappings, source_id, target_model):
    """The Data Consultant's reply mappings for this source and model, by vendor endpoint."""
    from prompits.core.pulse_mapping import validate_execution
    source = next((s for s in mappings['sources'] if isinstance(s, dict) and s.get('sourceId') == source_id), {})
    projections = {}
    for model in source.get('models') or []:
        if isinstance(model, dict) and same_model(model.get('modelRef'), target_model):
            for projection in model.get('projections') or []:
                if isinstance(projection, dict) and projection.get('transport') == 'https' and isinstance(projection.get('endpointId'), str):
                    try:
                        validate_execution((projection.get('mapping') or {}).get('execution'))
                    except (ValueError, TypeError, KeyError):
                        raise HTTPException(502, 'The Data Consultant published a mapping this Terminal cannot run.') from None
                    projections.setdefault(projection['endpointId'], projection)
    return projections


async def fetch_plan(network, agent, user, inputs, own, model, records, source_id, *, transport=None):
    """A dated history request answered by running the Data Consultant's plan; None leaves it to the older path.

    The Terminal knows nothing of the vendor: the plan names each call, the Consultant's mapping reads each
    reply. Each (ticker, time) is kept once; a ticker whose later window failed is left out, not shown with
    a gap. A generated plan that failed is reported once and the reviewed recipe's plan runs instead.
    """
    from prompits.core.pulse_models import validate_payload
    query, tickers, fields = requested_model(inputs, model)
    arguments = query.get('arguments') if isinstance(query.get('arguments'), dict) else {}
    if not arguments.get('start') or not arguments.get('end'):
        return None
    projections = plan_projections(await model_mappings(network, user['token']), source_id, inputs['target_model'])
    if not projections:
        return None
    request = {'instruments': tickers, 'start': arguments['start'], 'end': arguments['end'], 'interval': arguments.get('interval'),
               **({'instrumentBindings': query['instrumentBindings']} if query.get('instrumentBindings') is not None else {})}
    planned = await call_plan(network, agent, user['token'], request)
    if planned is None:
        return None
    for attempt in (0, 1):
        if planned.get('error'):
            raise HTTPException(422, str(planned['error'])[:300])
        calls = planned.get('calls') if isinstance(planned.get('calls'), list) else []
        if not calls or len(calls) > MAX_PLANNED_CALLS:
            raise HTTPException(502, 'The Data Consultant returned a plan your Terminal cannot run.')
        by_ticker, lineage, issues, used, received, first_failure = {}, [], [], [], time.time(), ''
        for call in calls:
            ticker = str(call.get('instrument') or '')
            projection = projections.get(str(call.get('endpointId') or ''))
            if ticker not in tickers or projection is None:
                raise HTTPException(502, 'The Data Consultant planned a call it publishes no mapping for.')
            state = by_ticker.setdefault(ticker, {'rows': {}, 'broken': ''})
            result = await fetch_planned(call, own['key'], own['hosts'], transport=transport)
            if result['status'] != 'completed':
                first_failure = first_failure or result.get('error', '')
                state['broken'] = state['broken'] or result.get('error', '') or 'The vendor request failed.'
                continue
            parameters = call.get('parameters') if isinstance(call.get('parameters'), dict) else dict(call.get('query') or {})
            try:
                found, trace, problems = projected(projection, result['data'], parameters, ticker, fields)
            except (ValueError, TypeError, KeyError) as exc:
                state['broken'] = str(exc)
                continue
            if (call['endpointId'], projection.get('id')) not in [(e['endpoint_id'], p.get('id')) for e, p in used]:
                used.append(({'endpoint_id': call['endpointId']}, projection))
            for row in found:
                state['rows'].setdefault((row.get('ticker'), str(row.get('timestamp'))), row)
            lineage.extend(trace)
            issues.extend({'ticker': ticker, **problem} for problem in problems)
        generated = (planned.get('recipe') or {}).get('kind') == 'generated'
        if attempt == 0 and generated and (first_failure or any(s['broken'] for s in by_ticker.values())):
            retry = await call_plan(network, agent, user['token'], request,
                                    failed={'recipe': (planned.get('recipe') or {}).get('hash'),
                                            'error': first_failure or next(s['broken'] for s in by_ticker.values() if s['broken'])})
            if retry is not None:
                planned = retry
                continue
        break
    rows = []
    for ticker in tickers:
        state = by_ticker.get(ticker) or {'rows': {}, 'broken': ''}
        if state['broken']:
            # Never a history with a hole in it: the ticker is left out and says why.
            issues.append({'ticker': ticker, 'status': 'incomplete' if state['rows'] else 'failed',
                           'reason': ('Part of this history could not be read: ' if state['rows'] else '') + state['broken'][:200]})
            continue
        rows.extend(sorted(state['rows'].values(), key=lambda row: str(row.get('timestamp'))))
    if len(rows) > MAX_PLANNED_ROWS:
        raise HTTPException(502, 'The vendor returned more rows than one request may carry.')
    issues.extend({'ticker': n.get('instrument', ''), 'status': 'note', 'reason': n.get('note', '')}
                  for n in planned.get('notes') or [] if isinstance(n, dict))
    try:
        validate_payload(model, rows, records)
    except ValueError as exc:
        raise HTTPException(502, 'Mapped vendor data does not match the canonical model: ' + str(exc)[:200]) from None
    result = model_result(inputs, model, used, source_id, rows, lineage, issues, received)
    result['canonical_result']['plan'] = {'recipe': planned.get('recipe'), 'calls': len(planned.get('calls') or [])}
    return table_view(redact(result, own['key']), query.get('table'))


def stamp(result):
    result['_billing'] = {'points': 0, 'state': 'free', 'basis': 'direct-user-vendor-connection'}
    result['_connection'] = {'mode': 'own', 'transport': 'direct-vendor'}
    return result


async def resolve_own_key(network, agent, user, inputs, own, *, transport=None):
    """Answer one data_fetch reference from the vendor itself; the agent sees no request."""
    if not isinstance(inputs, dict) or set(inputs) - INPUTS:
        raise HTTPException(422, 'Unsupported data request fields.')
    source_id = own['source']
    if inputs.get('target_model'):
        model, records = await model_resource(network, inputs['target_model'], user['token'])
        planned = await fetch_plan(network, agent, user, inputs, own, model, records, source_id, transport=transport)
        if planned is not None:
            return stamp(planned)
        choices = await mapped_endpoints(network, agent, user['token'], source_id, inputs['target_model'], 'https')
        if not choices:
            raise HTTPException(409, 'The Data Consultant publishes no mapping your Terminal can run for this source and model. Your key was not sent anywhere.')
        return stamp(await fetch_model(choices, inputs, own, model, records, source_id, transport=transport))
    endpoint_id = inputs.get('endpoint_id') or inputs.get('dataset_id')
    if not isinstance(endpoint_id, str) or not endpoint_id:
        raise HTTPException(422, 'Name the vendor endpoint or the canonical model.')
    endpoint = next((e for e in await published(network, agent, user['token'], endpoint_id) if e.get('endpoint_id') == endpoint_id), None)
    if endpoint is None:
        raise HTTPException(409, 'The source no longer publishes this vendor endpoint.')
    if endpoint.get('transport') == 'websocket':
        # A stream endpoint answers through the live connection, not a request.
        return stamp({'status': 'completed', 'endpoint_id': endpoint_id, 'data': {'status': 'waiting', 'rows': []}})
    return stamp(await fetch_endpoint(endpoint, inputs.get('parameters') or {}, own['key'], own['hosts'], transport=transport))


def fill(template, values, key=''):
    """Replace exact "{name}" strings; an absent optional value drops its field."""
    if isinstance(template, dict):
        output = {}
        for name, child in template.items():
            value = fill(child, values, key)
            if value is not None:
                output[name] = value
        return output
    if isinstance(template, list):
        return [fill(child, values, key) for child in template]
    if isinstance(template, str) and re.fullmatch(r'\{[A-Za-z0-9_.-]{1,64}\}', template):
        return key if template == CREDENTIAL else values.get(template[1:-1])
    return template


def credential_only_in_auth(protocol):
    def mentions(value):
        return CREDENTIAL in json.dumps(value)
    return mentions((protocol.get('auth') or {}).get('send')) and not any(
        mentions(protocol.get(part)) for part in ('subscribe', 'data', 'rejected')) and not mentions(protocol.get('url'))


def stream_protocol(endpoint, hosts):
    protocol = (endpoint.get('extensions') or {}).get(STREAM)
    if endpoint.get('transport') != 'websocket' or not isinstance(protocol, dict):
        raise HTTPException(409, 'The source does not publish a stream protocol for this endpoint.')
    pinned(protocol.get('url'), hosts, 'wss')
    if not credential_only_in_auth(protocol):
        raise HTTPException(409, 'The published stream protocol may place the key only in its authentication message.')
    each = (protocol.get('subscribe') or {}).get('each')
    limit = protocol.get('maxSymbols', 1)
    if not isinstance(each, str) or not NAME.fullmatch(each) or type(limit) is not int or not 1 <= limit <= 50:
        raise HTTPException(409, 'The published stream protocol is incomplete.')
    return protocol


def matches(message, pattern):
    return isinstance(pattern, dict) and bool(pattern) and all(path_value(message, k) == v for k, v in pattern.items())


def connector(url, **options):
    from websockets.asyncio.client import connect

    class Direct(connect):
        def process_redirect(self, exc):
            return exc  # Never authenticate after a WebSocket redirect.
    return Direct(url, ssl=tls(), proxy=None, **options)


async def stream_events(endpoint, parameters, key, hosts, *, connect=connector):
    """Yield {'status','rows'} snapshots from one published vendor stream."""
    protocol = stream_protocol(endpoint, hosts)
    each, limit = protocol['subscribe']['each'], protocol.get('maxSymbols', 1)
    values = dict(parameters or {})
    symbols = values.pop(each, None)
    symbols = [symbols] if isinstance(symbols, str) else symbols
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= limit or any(not isinstance(s, str) or not NAME.fullmatch(s) for s in symbols):
        raise HTTPException(422, f'Subscribe to between 1 and {limit} symbols.')
    specs = {p['name'] for p in endpoint.get('parameters') or [] if isinstance(p, dict)}
    if set(values) - specs:
        raise HTTPException(422, 'Unsupported parameters for this vendor stream.')
    if key_required(endpoint) and not key:
        raise HTTPException(409, 'Add your key for this source in Terminal settings → Data sources.')
    size = protocol.get('maxMessageBytes', 500_000)
    size = size if type(size) is int and 1_000 <= size <= 4_000_000 else 500_000
    data = protocol.get('data') or {}
    latest = {symbol: {} for symbol in dict.fromkeys(symbols)}

    def message(raw):
        if len(raw) > size:
            raise ValueError('Oversized vendor stream message.')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Unexpected vendor stream message.')
        return value
    async with connect(protocol['url'], max_size=size, open_timeout=15, ping_interval=20, ping_timeout=20) as socket:
        auth = protocol.get('auth') or {}
        if auth.get('send') is not None:
            await socket.send(json.dumps(fill(auth['send'], values, key)))
            for _ in range(20):
                reply = message(await asyncio.wait_for(socket.recv(), 15))
                if matches(reply, auth.get('rejected') or protocol.get('rejected')):
                    raise PermissionError('The vendor rejected the key or the stream entitlement.')
                if matches(reply, auth.get('accepted')):
                    break
            else:
                raise TimeoutError('Vendor stream authentication timed out.')
        for symbol in latest:
            await socket.send(json.dumps(fill(protocol['subscribe']['send'], {**values, each: symbol})))
        yield {'status': 'waiting', 'rows': []}
        async for raw in socket:
            reply = message(raw)
            if matches(reply, protocol.get('rejected')):
                raise ConnectionError('The vendor rejected the subscription or closed the stream.')
            if not matches(reply, data.get('match')):
                continue
            payload = path_value(reply, data.get('payload') or '')
            symbol = str(path_value(payload, data.get('symbol') or each) or '') if isinstance(payload, dict) else ''
            if symbol not in latest:
                continue
            latest[symbol] = redact(payload, key)
            yield {'status': 'live', 'rows': [row for row in latest.values() if row]}


async def live_values(network, agent, user, inputs, own, *, connect=connector):
    """Stream deliveries for one reference: vendor rows, or canonical rows when a model is named."""
    if not isinstance(inputs, dict) or set(inputs) - INPUTS:
        raise HTTPException(422, 'Unsupported data request fields.')
    if inputs.get('target_model'):
        choices = await mapped_endpoints(network, agent, user['token'], own['source'], inputs['target_model'], 'websocket')
        if not choices:
            raise HTTPException(409, 'The Data Consultant publishes no live mapping your Terminal can run for this source and model.')
        model, records = await model_resource(network, inputs['target_model'], user['token'])
        query, tickers, fields = requested_model(inputs, model)
        # One connection serves one mapping: the first that covers every streamed ticker.
        endpoint, projection = choice_for(choices, tickers, query.get('instrumentBindings'))
        symbols = {instrument_parameters(projection, t, query.get('instrumentBindings'))[projection['instrument']['parameter']]: t for t in tickers}
        parameters = {**copy.deepcopy(projection.get('parameters') or {}), projection['instrument']['parameter']: list(symbols)}
        from prompits.core.pulse_models import validate_payload
        async for event in stream_events(endpoint, parameters, own['key'], own['hosts'], connect=connect):
            rows = []
            for payload in event['rows']:
                symbol = str(path_value(payload, (projection.get('match') or {}).get('path') or 'symbol') or '')
                if symbol in symbols:
                    rows.extend(projected({k: v for k, v in projection.items() if k != 'match'}, payload,
                                          {projection['instrument']['parameter']: symbol}, symbols[symbol], fields)[0])
            validate_payload(model, rows, records)
            yield table_view(stamp({'status': event['status'], 'data': {'items': rows}, 'mode': 'stream'}), query.get('table'))
        return
    endpoint_id = inputs.get('endpoint_id') or inputs.get('dataset_id')
    endpoint = next((e for e in await published(network, agent, user['token'], endpoint_id or '') if e.get('endpoint_id') == endpoint_id), None)
    if endpoint is None:
        raise HTTPException(409, 'The source no longer publishes this vendor stream.')
    async for event in stream_events(endpoint, inputs.get('parameters') or {}, own['key'], own['hosts'], connect=connect):
        yield stamp({'status': event['status'], 'data': {'rows': event['rows']}, 'mode': 'stream'})
