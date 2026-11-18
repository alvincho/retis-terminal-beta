"""Historical price bindings derived from a source's typed endpoint contract."""
import copy
import re

PRICE_FIELDS = {'open', 'high', 'low', 'close', 'last', 'price', 'adjustedClose', 'bid', 'ask'}


def valid_history_binding(binding):
    """A published historical input is unusable without its complete mapping."""
    if not isinstance(binding, dict) or not isinstance(binding.get('parameters'), dict):
        return False
    mapping = binding.get('mapping')
    paths = binding.get('request_paths')
    if not isinstance(mapping, dict) or not isinstance(paths, dict):
        return False
    fields = mapping.get('fields')
    if not isinstance(fields, dict) or not fields:
        return False
    def path(value, root=False):
        return isinstance(value, str) and len(value) <= 256 and (value == '' and root or
            bool(re.fullmatch(r'[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*', value)) and
            not {'__proto__', 'prototype', 'constructor'}.intersection(value.split('.')))
    return (path(mapping.get('rows'), True) and path(mapping.get('timestamp')) and
            ('instrument' not in mapping or path(mapping['instrument'])) and
            all(path(value) for value in fields.values()) and
            all(path(paths.get(key)) for key in ('start', 'end', 'interval')) and
            ('instrument' not in paths or path(paths['instrument'])))


def history_mapping(schema, path='', depth=0):
    if not isinstance(schema, dict) or depth > 8:
        return None
    if schema.get('type') == 'array':
        props = schema.get('items', {}).get('properties', {})
        fields = [key for key, value in props.items()
                  if isinstance(value, dict) and value.get('type') in ('number', 'integer')
                  ]
        date = next((key for key in ('availableAt', 'timestamp', 'observedAt', 'date', 'time')
                     if isinstance(props.get(key), dict) and props[key].get('type') == 'string'), None)
        instrument = next((key for key in ('instrument', 'ticker', 'symbol') if key in props), None)
        if date and fields:
            return {'rows': path, 'timestamp': date,
                    **({'instrument': instrument} if instrument else {}),
                    'fields': {field: field for field in fields}}
    for key, value in schema.get('properties', {}).items():
        found = history_mapping(value, (path + '.' if path else '') + key, depth + 1)
        if found:
            return found
    return None


def history_input(input_schema, output_schema, *, request_prefix='', row_prefix=''):
    """Only offer contracts whose required inputs the history form can supply."""
    props = input_schema.get('properties', {})
    start = next((key for key in ('start', 'start_time', 'startDate', 'start_date', 'from') if key in props), None)
    end = next((key for key in ('end', 'end_time', 'endDate', 'end_date', 'to') if key in props), None)
    interval = next((key for key in ('interval', 'timeframe') if key in props), None)
    instrument = next((key for key in ('instrument', 'ticker', 'symbol', 'instruments') if key in props), None)
    mapping = history_mapping(output_schema, row_prefix)
    if not (start and end and interval and mapping):
        return None
    defaults = {key: copy.deepcopy(value['default']) for key, value in props.items() if 'default' in value}
    if set(input_schema.get('required', [])) - {start, end, interval, instrument} - defaults.keys():
        return None
    choices = props[interval].get('enum', [])
    paths = {'start': start, 'end': end, 'interval': interval,
             **({'instrument': instrument} if instrument else {})}
    return {'parameters': defaults, 'mapping': mapping,
            'request_paths': {key: request_prefix + path for key, path in paths.items()},
            'instrument_array': bool(instrument and props[instrument].get('type') == 'array'),
            'date_only': props[start].get('format') == props[end].get('format') == 'date',
            'intervals': list(choices),
            'default_interval': props[interval].get('default') or (choices[0] if choices else '1d')}


def endpoint_history_inputs(source):
    """Publish native history without claiming an unimplemented canonical model."""
    snapshot = getattr(source, 'spec_snapshot', None)
    result = []
    for endpoint in snapshot.endpoints if snapshot else ():
        if not source.is_endpoint_executable(endpoint):
            continue
        binding = history_input(source._endpoint_input_schema(endpoint), dict(endpoint.response_schema),
                                request_prefix='parameters.', row_prefix='data')
        if binding:
            binding['request_paths']['timezone']='timezone'
            result.append({**binding, 'pulse_name': 'data_fetch', 'endpoint_name': endpoint.name,
                           'parameters': {'endpoint_id': endpoint.endpoint_id,
                                          'parameters': binding['parameters']}})
    return result
