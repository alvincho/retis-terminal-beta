"""Documented LSEG Data Library desktop operations, separate from cloud RDP."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from .models import EndpointParameterSpec, EndpointSpec, EndpointSpecSnapshot, VendorSpecSource

VENDOR = 'lseg_workspace'
DOCS = 'https://cdn.refinitiv.com/public/lseg-lib-python-doc/2.0.0.2/book/en/'
QUICKSTART = 'https://developers.lseg.com/en/api-catalog/lseg-data-platform/lseg-data-library-for-python/quick-start'
STR = {'type': 'string', 'minLength': 1, 'maxLength': 4096}
STRINGS = {'oneOf': [STR, {'type': 'array', 'items': STR, 'minItems': 1, 'maxItems': 100}]}
UNIVERSE = {'oneOf': [STR, {'type': 'array', 'items': STR, 'minItems': 1, 'maxItems': 25}]}
TABLE = {'type': 'object', 'properties': {
    'rows': {'type': 'array', 'items': {'type': 'object'}},
    'columns': {'type': 'array', 'items': {'type': 'string'}},
    'columnPaths': {'type': 'object'}, 'indexNames': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['rows', 'columns', 'columnPaths', 'indexNames']}
COST = {'model': 'subscription', 'amount': 0, 'currency': 'USD', 'unit': 'request',
        'provider_cost': 'Covered only by the source owner’s licensed Workspace entitlements; not a free public feed.',
        'notes': ['No per-request Plaza charge configured. Vendor usage limits and entitlements apply.']}


def param(name, schema=STR, required=False, description=''):
    return EndpointParameterSpec(name=name, schema=schema, required=required, description=description)


COMMON = (param('universe', UNIVERSE, True, 'LSEG instrument identifiers (RICs), for example 2330.TW or NVDA.O.'), param('fields', STRINGS))
PARAMETERS = param('parameters', {'oneOf': [STR, {'type': 'object'}]})
TIME = (param('start'), param('end'))
COUNT = param('count', {'type': 'integer', 'minimum': 1, 'maximum': 10000})


def endpoints():
    definitions = [
        ('session.status', 'Desktop connection status', 'Connection', (),
         'Check the local desktop session; this does not verify dataset entitlement.',
         'sections/access-layer/access/open-session-function.html',
         {'type': 'object', 'properties': {'session': STR, 'connected': {'type': 'boolean'}, 'dataEntitlementVerified': {'type': 'boolean'}}}),
        ('get_data', 'Quotes, fundamentals and reference fields', 'Market and company data', (COMMON[0], param('fields', STRINGS, True), PARAMETERS),
         'Retrieve requested vendor fields and preserve missing values. Field availability depends on the subscribed Workspace package.',
         'sections/access-layer/access/get-data-function.html', TABLE),
        ('get_history', 'Historical prices and fundamental series', 'History',
         (*COMMON, param('interval', {'type': 'string', 'enum': ['tick','minute','1min','5min','10min','30min','60min','hourly','1h','daily','1d','1D','7D','7d','weekly','1W','monthly','1M','quarterly','3M','6M','yearly','12M','1Y']}),
          *TIME, COUNT, param('adjustments'), PARAMETERS),
         'Historical data retains timestamps and instrument/field column paths. Intraday availability is entitlement dependent.',
         'sections/access-layer/access/get-history-function.html', TABLE),
        ('news.get_headlines', 'News headlines', 'News',
         (param('query', STR, True), param('count', {'type': 'integer', 'minimum': 1, 'maximum': 100}), *TIME,
          param('order_by', {'type': 'string', 'enum': ['newToOld','oldToNew']})),
         'Retrieve entitled headlines and story identifiers using a Workspace news query.',
         'index.html', TABLE),
        ('news.get_story', 'News story', 'News',
         (param('story_id', STR, True), param('format', {'type': 'string', 'enum': ['Text','Html']})),
         'Retrieve one entitled story; text is the default. HTML is provider content, not trusted application markup.',
         'index.html', {'type': 'object', 'properties': {'storyId': STR, 'format': STR, 'content': {'type': 'string'}}}),
        ('discovery.search', 'Instrument and company search', 'Discovery',
         tuple(param(k) for k in ['query','view','filter','order_by','boost','select','group_by','scope','terms','navigators']) +
         (param('top', {'type': 'integer', 'minimum': 1, 'maximum': 1000}), param('skip', {'type': 'integer', 'minimum': 0, 'maximum': 10000})),
         'Search instrument metadata to resolve the correct listing; no guessed exchange suffix.', 'index.html', TABLE),
        ('discovery.convert_symbols', 'Symbol conversion', 'Symbology',
         (param('symbols', UNIVERSE, True), param('from_symbol_type'), param('to_symbol_types', STRINGS), param('preferred_country_code')),
         'Convert explicit identifier types using the vendor symbology service.', 'index.html', TABLE),
        ('open_pricing_stream', 'Bounded pricing stream', 'Streaming',
         (*COMMON, param('service'), param('duration_seconds', {'type': 'number', 'minimum': .1, 'maximum': 10}),
          param('max_events', {'type': 'integer', 'minimum': 1, 'maximum': 500})),
         'Collect real provider refresh/update events for up to ten seconds, then close the subscription. Not a persistent browser stream.',
         'index.html', {'type': 'object', 'properties': {'events': {'type': 'array', 'items': {'type': 'object'}},
          'snapshot': TABLE, 'bounded': {'const': True}, 'durationSeconds': {'type': 'number'}, 'eventCount': {'type': 'integer'}}}),
    ]
    return tuple(EndpointSpec(
        endpoint_id=VENDOR+'.'+operation, vendor_id=VENDOR, name=name, description=description,
        category=category, transport='python', operation=operation, parameters=parameters,
        response_schema=schema, authentication={'required': True, 'type': 'desktop-session',
            'description': 'Use the source owner’s signed-in Workspace desktop on this Mac. Never submit a password or token in a pulse.'},
        cost=COST, quality={'notes': ['Documented operation; each instrument, field and content entitlement requires a live check.']},
        documentation_url=DOCS+path, executable=True, pulse_name='data_fetch',
        extensions={'ai.attas:localOnly': True, 'ai.attas:session': 'desktop.workspace'},
    ) for operation, name, category, parameters, description, path, schema in definitions)


def spec_sources():
    return (VendorSpecSource(source_id='lseg-workspace-official-library', vendor_id=VENDOR,
                            kind='website', location=DOCS+'index.html', media_type='text/html',
                            description='Official Data Library desktop access reference; SDK 2.0 and 2.1 header compatibility.',
                            options={'allowed_hosts': ['cdn.refinitiv.com']}),)


class WorkspaceSpecParser:
    VERSION = '1'

    def can_parse(self, source):
        return source.vendor_id == VENDOR

    def supports(self, source):  # ingestion selects parsers by supports(), like every other parser
        return self.can_parse(source)

    def parse(self, source, content):
        if not self.can_parse(source):
            raise ValueError('Workspace parser requires lseg_workspace.')
        return endpoints()


def bundled_snapshot():
    entries = endpoints()
    return EndpointSpecSnapshot(vendor_id=VENDOR, retrieved_at=datetime.now(timezone.utc).isoformat(),
        content_hash=hashlib.sha256(json.dumps([asdict(e) for e in entries], sort_keys=True).encode()).hexdigest(),
        parser_version='WorkspaceSpecParser:1', sources=spec_sources(), endpoints=entries,
        warnings=('Desktop session access is local and owner-scoped; platform API credentials are not interchangeable.',))
