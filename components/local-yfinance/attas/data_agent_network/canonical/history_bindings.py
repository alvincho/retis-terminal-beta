"""Replay bindings owned by the sources with installed canonical adapters."""
from attas.data_agent_network.scaffold.history import PRICE_FIELDS


def advertisements(profile):
    vendor = profile.get('source_id') or profile.get('vendor_id')
    support = next((s for s in profile.get('modelSupport', []) if s.get('legacySchema') == 'market.history.v1'), None)
    if not support or vendor not in ('yfinance', 'fugle'):
        return []
    fields = [f for f in support.get('fields', []) if f in PRICE_FIELDS or f == 'volume']
    if not PRICE_FIELDS.intersection(fields):
        return []
    intervals = {'yfinance': ['1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo'],
                 'fugle': ['1', '5', '10', '15', '30', '60', 'D', 'W', 'M']}[vendor]
    return [{'pulse_name': 'data_fetch',
        'parameters': {'target_model': support['modelRef'], 'model_request': {
            'fields': ['ticker', 'timestamp', *fields], 'range': '1Y', 'arguments': {}}},
        'mapping': {'rows': 'canonical_data.items', 'instrument': 'ticker', 'timestamp': 'timestamp',
                    'fields': {f: f for f in fields}},
        'request_paths': {'timezone':'timezone', 'instrument': 'model_request.instruments',
            'start': 'model_request.arguments.'+('from' if vendor == 'fugle' else 'start'),
            'end': 'model_request.arguments.'+('to' if vendor == 'fugle' else 'end'),
            'interval': 'model_request.arguments.'+('timeframe' if vendor == 'fugle' else 'interval')},
        'instrument_array': True, 'date_only': vendor == 'fugle', 'intervals': intervals,
        'default_interval': {'yfinance': '1d', 'fugle': 'D'}[vendor]}]
