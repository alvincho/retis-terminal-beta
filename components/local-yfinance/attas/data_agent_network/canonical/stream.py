"""Source relay envelopes reuse the snapshot model, units and time semantics."""
import time
from prompits.core.resource_registry import reference
from .catalog import profile
from .service import mapping_resource


def model_stream_event(source, event, schema='market.quotes.v1', *, stream_id=None):
    model = profile(schema); mapping = mapping_resource(source, schema)
    if not model or not mapping: return event
    ref = reference(model)
    rows = event.get('rows', [])
    fields = model['fields']
    received = time.time()
    envelope = {'schema':'model-stream/1','modelRef':ref,'mappingRefs':[reference(mapping)],
                'streamId':stream_id or source+':'+ref['digest'],'sequence':event['sequence'],
                'kind':'snapshot' if event.get('snapshot',True) else 'delta','keys':['ticker'],
                'identityDimensions':['currency'],'payload':rows,'sourceId':source,
                'eventAt':max((r.get('observedAt') or '' for r in rows),default='') or None,
                'receivedAt':received,'replay':False,'resynchronize':'snapshot-on-gap',
                'lineage':[{f:{'sourceId':source,'modelFieldRef':fields[f], 'observedAt':row.get('observedAt'),
                               'receivedAt':received,'status':'available' if row.get(f) is not None else 'missing',
                               **({'unit':row.get('volumeUnit')} if f=='volume' else {})} for f in fields} for row in rows]}
    return {**event, 'canonical_stream':envelope}
