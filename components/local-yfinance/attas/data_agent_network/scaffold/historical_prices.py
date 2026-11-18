"""Source-owned, explicit mappings for provider historical price formats.

These mappings do not claim canonical model support. They publish a separate
Pulse with a pinned mapping and ordered numeric observations for replay.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import jsonschema
from ..timezones import TIMEZONE_SCHEMA, normalize_request

IMPLEMENTATION_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

OHLCV = {key: key for key in ('open', 'high', 'low', 'close', 'volume')}

# Request and response bindings are deliberate provider contracts, never inferred
# from an arbitrary payload. Date-only bars use their provider session date.
MAPPINGS = {
    'alpha_vantage': dict(endpoint='alpha_vantage.time_series_daily', shape='dated_object',
        rows='Time Series (Daily)', timestamp='', fields={key: str(i)+'. '+key for i, key in enumerate(OHLCV, 1)},
        instrument='symbol', defaults={'function': 'TIME_SERIES_DAILY', 'outputsize': 'full', 'datatype': 'json'},
        intervals={'1d': {}}, date_only=True),
    'finmind': dict(endpoint='finmind.dataset.TaiwanStockPrice', shape='rows', rows='data', timestamp='date',
        fields={**OHLCV, 'high': 'max', 'low': 'min', 'volume': 'Trading_Volume'},
        instrument='data_id', row_instrument='stock_id', taiwan_symbol=True,
        start='start_date', end='end_date', defaults={'dataset': 'TaiwanStockPrice'},
        intervals={'1d': {}}, date_only=True),
    'finnhub': dict(endpoint='finnhub.stock_candles', shape='columns', rows='', timestamp='t',
        fields={'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c', 'volume': 'v'},
        instrument='symbol', start='from', end='to', bounds='seconds', timestamp_unit='seconds', defaults={},
        intervals={v: {'resolution': v} for v in ('1', '5', '15', '30', '60', 'D', 'W', 'M')}),
    'twelve_data': dict(endpoint='twelve_data.time_series', shape='rows', rows='values', timestamp='datetime',
        fields=OHLCV, instrument='symbol', start='start_date', end='end_date',
        defaults={'order': 'ASC', 'timezone': 'UTC', 'outputsize': 5000, 'format': 'JSON', 'adjust': 'none'},
        intervals={v: {'interval': v} for v in ('1min', '5min', '15min', '30min', '45min', '1h', '2h', '4h', '8h', '1day', '1week', '1month')},
        capacity=5000),
    'massive': dict(endpoint='massive.get_aggs', shape='rows', rows='results', timestamp='t',
        fields={'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c', 'volume': 'v'},
        instrument='ticker', start='from_', end='to', bounds='milliseconds', timestamp_unit='milliseconds',
        defaults={'sort': 'asc', 'limit': 50000, 'adjusted': False},
        intervals={v: {'multiplier': n, 'timespan': unit} for v, n, unit in
                   [('1m', 1, 'minute'), ('5m', 5, 'minute'), ('15m', 15, 'minute'), ('1h', 1, 'hour'),
                    ('1d', 1, 'day'), ('1wk', 1, 'week'), ('1mo', 1, 'month')]}),
    'eodhd': dict(endpoint='eodhd.geteoddatabyticker', shape='rows', rows='items', timestamp='date',
        fields={**OHLCV, 'adjustedClose': 'adjusted_close'}, instrument='ticker', start='from', end='to',
        defaults={'fmt': 'json'}, intervals={v: {'period': v} for v in ('d', 'w', 'm')}, date_only=True),
    'tiingo': dict(endpoint='tiingo.daily_prices', shape='rows', rows='items', timestamp='date',
        fields={**OHLCV, 'adjustedClose': 'adjClose'}, instrument='ticker', start='startDate', end='endDate',
        defaults={}, intervals={v: {'resampleFreq': v} for v in ('daily', 'weekly', 'monthly', 'annually')}, date_only=True),
    'intrinio': dict(endpoint='intrinio.get_security_stock_prices', shape='rows', rows='stock_prices', timestamp='date',
        fields={**OHLCV, 'adjustedClose': 'adj_close'}, instrument='identifier', start='start_date', end='end_date',
        defaults={'page_size': 10000}, intervals={v: {'frequency': v} for v in ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')}, date_only=True),
    'financial_modeling_prep': dict(endpoint='financial_modeling_prep.stable_historical_price_eod_full',
        shape='rows', rows='items', timestamp='date', fields=OHLCV, instrument='symbol',
        start='from', end='to', defaults={}, intervals={'1d': {}}, date_only=True),
    'coingecko': dict(endpoint='coingecko.coins_id_market_chart_range', shape='rows', rows='prices', timestamp='0',
        fields={'price': '1'}, instrument='id', start='from', end='to', bounds='seconds', timestamp_unit='milliseconds',
        defaults={'vs_currency': 'usd'}, intervals={'auto': {}}),
}

REF_SCHEMA = {'type': 'object', 'properties': {k: {'type': 'string', 'minLength': 1} for k in ('id', 'version', 'digest')},
              'required': ['id', 'version', 'digest'], 'additionalProperties': False}
INPUT_SCHEMA = {'type': 'object', 'properties': {
    'timezone': TIMEZONE_SCHEMA,
    'instrument': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'^\S+$'},
    'start': {'type': 'string', 'format': 'date-time'}, 'end': {'type': 'string', 'format': 'date-time'},
    'interval': {'type': 'string', 'minLength': 1}, 'mapping_ref': REF_SCHEMA,
    'auth_context': {'type': 'object', 'properties': {'connection_id': {'type': 'string'}}, 'additionalProperties': False},
}, 'required': ['instrument', 'start', 'end', 'interval', 'mapping_ref'], 'additionalProperties': False}
OUTPUT_SCHEMA = {'type': 'object', 'properties': {
    'status': {'enum': ['completed', 'failed', 'authentication_required', 'not_executable']},
    'request_timezone': TIMEZONE_SCHEMA,
    'source_id': {'type': 'string'}, 'endpoint_id': {'type': 'string'}, 'mapping_ref': REF_SCHEMA,
    'items': {'type': 'array', 'maxItems': 10000, 'items': {'type': 'object', 'properties': {
        'instrument': {'type': 'string'}, 'timestamp': {'type': 'string', 'format': 'date-time'},
        **{k: {'type': ['number', 'null']} for k in (*OHLCV, 'adjustedClose', 'price')},
    }, 'required': ['instrument', 'timestamp'], 'additionalProperties': False}},
    'warnings': {'type': 'array', 'items': {'type': 'string'}}, 'error': {'type': 'string'},
}, 'required': ['status', 'source_id', 'endpoint_id', 'mapping_ref', 'items', 'warnings', 'error'], 'additionalProperties': False}
PULSE = {'name': 'historical_prices', 'pulse_name': 'historical_prices',
         'pulse_address': 'plaza://pulse/attas/historical_prices',
         'description': 'Fetch ordered historical prices through an explicit source-owned mapping.',
         'input_schema': INPUT_SCHEMA, 'output_schema': OUTPUT_SCHEMA, 'cost': 1, 'cost_points': 1,
         'tags': ['attas', 'data-source', 'history']}


def pulse(source):
    """Only advertise fields implemented by this source's installed mapping."""
    result = copy.deepcopy(PULSE)
    row = result['output_schema']['properties']['items']['items']
    allowed = {'instrument', 'timestamp', *MAPPINGS[source.vendor_id]['fields']}
    row['properties'] = {k: v for k, v in row['properties'].items() if k in allowed}
    result['input_schema']['properties']['interval']['enum'] = list(MAPPINGS[source.vendor_id]['intervals'])
    result['input_schema']['properties']['mapping_ref'] = {'const': mapping_ref(source.vendor_id)}
    return result


def mapping_ref(vendor):
    # Fingerprint the implementation as well as the declared request/field map.
    raw = IMPLEMENTATION_DIGEST.encode() + json.dumps(MAPPINGS[vendor], sort_keys=True).encode()
    return {'id': 'attas.history.'+vendor, 'version': '2.0.0', 'digest': hashlib.sha256(raw).hexdigest()}


def installed(source):
    definition = MAPPINGS.get(source.vendor_id)
    snapshot = getattr(source, 'spec_snapshot', None)
    endpoint = next((e for e in snapshot.endpoints if definition and e.endpoint_id == definition['endpoint']), None) if snapshot else None
    if not endpoint or not source.is_endpoint_executable(endpoint):
        return None
    supplied = {definition['instrument'], *definition['defaults']}
    supplied.update(definition.get(k) for k in ('start', 'end'))
    supplied.update(k for params in definition['intervals'].values() for k in params)
    if {p.name for p in endpoint.parameters if p.required} - supplied:
        return None
    return definition, endpoint


def advertisements(source):
    available = installed(source)
    if not available:
        return []
    definition, endpoint = available
    intervals = list(definition['intervals'])
    return [{'pulse_name': 'historical_prices', 'endpoint_name': endpoint.name,
             'parameters': {'mapping_ref': mapping_ref(source.vendor_id)},
             'request_paths': {key: key for key in ('instrument', 'start', 'end', 'interval', 'timezone')},
             'mapping': {'rows': 'items', 'instrument': 'instrument', 'timestamp': 'timestamp',
                         'fields': {key: key for key in definition['fields']}},
             'intervals': intervals, 'default_interval': next((v for v in ('1d', 'D', '1day', 'd', 'daily') if v in intervals), intervals[0]),
             'instrument_array': False, 'date_only': False}]


def at(value, path):
    for part in path.split('.') if path else ():
        value = value[part]
    return value


def date(value):
    if not isinstance(value, str):
        raise ValueError('Historical timestamps must be ISO date or time strings.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def observations(payload, definition, instrument):
    if isinstance(payload, dict):
        if any(payload.get(key) for key in ('next_page', 'next_page_url', 'next_url')):
            raise ValueError('Historical response is paginated; narrow the requested time range.')
        if payload.get('status') == 'error' or payload.get('s') not in (None, 'ok', 'no_data'):
            raise ValueError('Provider rejected the historical request.')
        if payload.get('s') == 'no_data':
            return []
        if payload.get('symbol') and payload['symbol'].upper() != instrument.upper():
            raise ValueError('Historical response belongs to another instrument.')
    raw = at(payload, definition['rows'])
    if definition['shape'] == 'dated_object':
        raw = [{'date': day, **row} for day, row in raw.items()]
    elif definition['shape'] == 'columns':
        columns = [definition['timestamp'], *definition['fields'].values()]
        if any(not isinstance(raw.get(k), list) for k in columns) or len({len(raw[k]) for k in columns}) != 1:
            raise ValueError('Historical candle arrays must have equal lengths.')
        raw = [{k: raw[k][i] for k in columns} for i in range(len(raw[columns[0]]))]
    if not isinstance(raw, list) or len(raw) > 10000:
        raise ValueError('Historical response must contain at most 10000 rows; narrow the time range.')
    if definition.get('capacity') and len(raw) >= definition['capacity']:
        raise ValueError('Historical response reached the provider row limit; narrow the time range.')
    result = []
    for row in raw:
        if not isinstance(row, (dict, list)):
            raise ValueError('Historical observations must contain mapped rows.')
        get = (lambda key: row[int(key)]) if isinstance(row, list) else (lambda key: row.get(key))
        expected = re.sub(r'\.(TW|TWO)$', '', instrument) if definition.get('taiwan_symbol') else instrument
        if definition.get('row_instrument') and str(row[definition['row_instrument']]) != expected:
            raise ValueError('Historical response belongs to another instrument.')
        value = get(definition['timestamp'] or 'date')
        unit = definition.get('timestamp_unit')
        if isinstance(value, bool):
            raise ValueError('Historical timestamps cannot be boolean values.')
        timestamp = datetime.fromtimestamp(float(value)/(1000 if unit == 'milliseconds' else 1), timezone.utc) if unit else date(value)
        output = {'instrument': instrument, 'timestamp': timestamp.isoformat()}
        for key, path in definition['fields'].items():
            value = get(path)
            if value is None:
                output[key] = None
                continue
            if isinstance(value, bool):
                raise ValueError('Historical values must be finite numbers.')
            number = float(value)
            if not math.isfinite(number):
                raise ValueError('Historical values must be finite numbers.')
            output[key] = number
        if output.get('close', output.get('price')) is None:
            raise ValueError('Historical mapping requires a price for every observation.')
        result.append(output)
    result.sort(key=lambda row: row['timestamp'])
    if len({row['timestamp'] for row in result}) != len(result):
        raise ValueError('Historical response contains duplicate timestamps.')
    return result


def execute(source, request):
    request = normalize_request(request)
    jsonschema.validate(request, INPUT_SCHEMA, format_checker=jsonschema.FormatChecker())
    available = installed(source)
    if not available:
        raise ValueError('No installed historical price mapping on this source.')
    definition, endpoint = available
    if request['mapping_ref'] != mapping_ref(source.vendor_id):
        raise ValueError('Select the currently published historical data mapping.')
    if request['interval'] not in definition['intervals']:
        raise ValueError('The selected historical interval is not supported.')
    start, end = date(request['start']), date(request['end'])
    if start >= end:
        raise ValueError('Historical start must be before end.')
    instrument = request['instrument']
    symbol = instrument
    if definition.get('taiwan_symbol'):
        if not re.fullmatch(r'[0-9]{4,6}\.(TW|TWO)', instrument):
            raise ValueError('This historical mapping supports Taiwan TW/TWO listings.')
        symbol = re.sub(r'\.(TW|TWO)$', '', instrument)
    parameters = {**copy.deepcopy(definition['defaults']), **definition['intervals'][request['interval']], definition['instrument']: symbol}
    for key, bound in (('start', start), ('end', end)):
        if not definition.get(key):
            continue
        mode = definition.get('bounds')
        parameters[definition[key]] = int(bound.timestamp()*(1000 if mode == 'milliseconds' else 1)) if mode else bound.date().isoformat() if definition.get('date_only') else bound.isoformat()
    response = source.execute_endpoint(endpoint, parameters, request.get('auth_context', {}))
    source._record_data_access_result(response)
    result = {'status': response.status if response.status in OUTPUT_SCHEMA['properties']['status']['enum'] else 'failed', 'source_id': source.vendor_id, 'endpoint_id': endpoint.endpoint_id,
              'mapping_ref': request['mapping_ref'], 'items': [], 'warnings': list(response.warnings), 'error': response.error}
    if response.status == 'completed':
        try:
            result['items'] = [row for row in observations(response.data, definition, instrument) if start <= date(row['timestamp']) < end]
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, OSError) as exc:
            result.update(status='failed', error='Historical mapping failed: '+str(exc))
    if 'timezone' in request:result['request_timezone']=request['timezone']
    jsonschema.validate(result, OUTPUT_SCHEMA, format_checker=jsonschema.FormatChecker())
    return result
