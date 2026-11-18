"""Source-owned canonical request planning and execution.

Browser callers specify meaning and instruments. Operation selection, vendor
parameters, unit conversion and statement joins stay at this boundary.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import time

from prompits.core.capabilities import digest
from prompits.core.resource_registry import reference, validate_ref
from prompits.core.pulse_models import validate_payload, validate_pds
from .catalog import profile, PROFILES
from .coverage import ADAPTERS
from .finance import clean, normalize, semantic_rows, number, history_window
from .history import history_parameters, history_observations


# Published mappings are immutable, including their execution fingerprint.
# Advance this major version whenever the fingerprint or execution contract
# changes, then seed the registry before restarting sources. Never reuse a
# published version for a different implementation. 10.0.0: unknown listings
# and listings without tagged news are reported per instrument. 11.0.0: listings
# without statements, and peers measured at different period ends, are noted.
# 12.0.0: a requested code that cannot be a listing is noted as invalid.
# 13.0.0: a dated history request runs the Data Consultant's plan (never the source's own planner), and its
# rows are kept once per ticker and time, with the Consultant's notes.
# 14.0.0: a comment reworded for the public repository's scan changed the fingerprint (2026-11-18); the local
# site's start-up then refused 13.0.0 ("Published content and audience are immutable"). tests/
# test_mapping_fingerprint.py records each version's fingerprint, so an edit without a new version fails a test.
MAPPING_VERSION = '14.0.0'

# Closed per-instrument notes a table may show beside its rows; upstream error
# text never becomes a note.
NOTE_STATUSES = ('not-found', 'no-news', 'no-financials', 'mixed-periods', 'invalid')
# A requested code that cannot be a listing (say, with a quote in it) is noted and never sent to the vendor;
# it used to fail the whole request with HTTP 500 (a private report, 2026-11-10).
INVALID_LISTING = 'Not a valid listing code. Codes use letters, digits and . ^ = _ -, such as AAPL, ^GSPC or 2330.TW.'
LISTING_CODE = r'[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,39}'
LISTING_NOT_FOUND = 'No data for this listing.'
TAIWAN_LISTING_NOT_FOUND = 'No data for this listing. Taiwan OTC listings end in .TWO, listed ones in .TW.'
NO_TAGGED_NEWS = 'No news is tagged with this listing.'
NO_FINANCIALS = 'No financial statements from this source for this listing.'
MIXED_PERIODS = 'Rows compare different reporting periods; see Period.'


@lru_cache(maxsize=1)
def implementation_digest():
    root = Path(__file__).parent
    files=[root/name for name in ('service.py','finance.py','earnings.py','history.py','coverage.py','stream.py','research_sources.py','research_models.py','analytics.py','workspace.py')]
    files.extend(root.parent/'provider_runtime'/name for name in ('yfinance.py','fugle.py','stream_hub.py'))
    return hashlib.sha256(b''.join(file.read_bytes() for file in files)).hexdigest()


def mapping_resource(source_id, schema):
    target = profile(schema)
    if not target:
        return None
    covered = sorted({f for schemas in ADAPTERS.get(source_id, {}).values() for f in schemas.get(schema, ())})
    if not covered:
        return None
    ref = reference(target)
    mapping = {'pds_version':'0.2.0','resource_type':'pulse_mapping','namespace':'ai.attas.mapping',
               'id':'ai.attas.mapping.'+source_id+'.'+schema,'version':MAPPING_VERSION,'target_ref':ref,'dependencies':[ref],
               'source_system':source_id,'relation':'implements','mapping_type':'source-execution',
               'field_map':{f:{'target':f} for f in covered},
               'execution':{'kind':'registered','method':'ai.attas.source:'+source_id,'version':'1.0.0','implementationDigest':implementation_digest()},
               'coverage':'Only declared operations and returned field evidence; missing or unspecified semantics remain explicit.',
               'extensions':{'ai.attas:legacySchema':schema,'ai.attas:fields':covered}}
    validate_pds(mapping)
    return mapping


def installed_support(source_id):
    schemas=sorted({schema for models in ADAPTERS.get(source_id,{}).values() for schema in models})
    output=[]
    for schema in schemas:
        mapping=mapping_resource(source_id,schema)
        if mapping:
            output.append({'modelRef':mapping['target_ref'],'mappingRef':reference(mapping),
                           'fields':mapping['extensions']['ai.attas:fields'],'legacySchema':schema,
                           'delivery':['snapshot','stream'] if source_id in ('yfinance','fugle') and schema=='market.quotes.v1' else ['snapshot'],
                           'verification':'declared','implementationDigest':implementation_digest()})
    return output


def endpoint_support(source_id, endpoint):
    operation = str(endpoint.operation).removeprefix('yfinance.')
    if source_id == 'fugle' and not endpoint.endpoint_id.startswith('fugle.stock.'):
        return []
    output = []
    for schema, fields in ADAPTERS.get(source_id, {}).get(operation, {}).items():
        mapping = mapping_resource(source_id, schema)
        if mapping:
            output.append({'modelRef':mapping['target_ref'],'mappingRef':reference(mapping),'fields':sorted(fields),
                           'delivery':['snapshot'],'verification':'declared','implementationDigest':implementation_digest(),
                           'execution':'source-owned','legacySchema':schema})
    return output


def resolve_target(ref):
    validate_ref(ref)
    for schema in PROFILES:
        model = profile(schema)
        if ref['id'] == model['id']:
            if reference(model) != ref:
                raise ValueError('The exact requested model version or digest is not installed.')
            return schema, model
    raise ValueError('The requested canonical model is not installed on this source.')


def operations(source_id, schema, fields, span='1M'):
    if source_id == 'yfinance':
        if schema == 'market.history.v1': return ['Ticker.history'] + (['Ticker.info'] if {'currency','volumeUnit'} & fields else [])
        # Yahoo retired the ticker news stream behind Ticker.news (404, read as []).
        if schema == 'research.news.v1': return ['Search.all']
        if schema == 'company.financials.v1': return ['Ticker.income_stmt','Ticker.cashflow','Ticker.info']
        if schema == 'company.estimates.v1' and 'estimatedRevenue' in fields: return ['Ticker.earnings_estimate','Ticker.revenue_estimate']
        if schema == 'company.earnings.v1' and 'reportedRevenue' in fields: return ['Ticker.earnings_dates','Ticker.quarterly_income_stmt']
        if schema in ('company.earnings.v1','company.estimates.v1'): return ['Ticker.earnings_dates']
        if schema in ('market.quotes.v1','company.profile.v1','company.peers.v1'): return ['Ticker.info']
    if source_id == 'alpha_vantage':
        return {'market.history.v1':['TIME_SERIES_INTRADAY' if span in ('1D','1W') else 'TIME_SERIES_DAILY'],
                'market.quotes.v1':['GLOBAL_QUOTE'],'company.profile.v1':['OVERVIEW'],'company.peers.v1':['OVERVIEW'],
                'research.news.v1':['NEWS_SENTIMENT']}.get(schema, [])
    if source_id == 'fugle':
        return {'market.history.v1':['fugle.stock.historical.candles.by_symbol'],
                'market.quotes.v1':['fugle.stock.intraday.quote.by_symbol'],
                'company.profile.v1':['fugle.stock.intraday.ticker.by_symbol'],
                'market.orderbook.v1':['fugle.stock.intraday.quote.by_symbol'],
                'market.trades.v1':['fugle.stock.intraday.trades.by_symbol']}.get(schema, [])
    return []


def model_plan(source, request):
    schema, model = resolve_target(request.get('target_model'))
    query = request.get('model_request') or {}
    if not isinstance(query, dict): raise ValueError('Model request must be an object.')
    tickers = query.get('instruments', [])
    if not isinstance(tickers, list) or not 1 <= len(tickers) <= 12 or any(not isinstance(t, str) or not t for t in tickers):
        raise ValueError('Provide one to twelve valid listing tickers.')
    # Codes that cannot be listings are answered with a note instead of failing the whole request with HTTP 500.
    invalid = [t for t in tickers if not re.fullmatch(LISTING_CODE, t)]
    if invalid and query.get('instrumentBindings') is not None:
        raise ValueError('Provide one to twelve valid listing tickers.')
    tickers = [t for t in tickers if t not in invalid]
    bindings = query.get('instrumentBindings')
    symbols = {}
    if bindings is not None:
        if not isinstance(bindings,list) or len(bindings)!=len(tickers):
            raise ValueError('Provide one source symbol binding per requested instrument.')
        from ..symbology_config import configured
        accepted = configured(source.vendor_id, getattr(source,'_config_data',{}).get('source_profile', {}))['input_types']
        for binding in bindings:
            if (not isinstance(binding,dict) or set(binding)!={'instrument','symbol','type'} or binding['instrument'] not in tickers
                    or binding['instrument'] in symbols or binding['type'] not in accepted
                    or not isinstance(binding['symbol'],str) or not re.fullmatch(r'[A-Za-z0-9.^=_/-]{1,80}',binding['symbol'])):
                raise ValueError('Invalid source symbol binding or unsupported identifier type.')
            symbols[binding['instrument']] = binding['symbol']
        if len(set(symbols.values())) != len(symbols):raise ValueError('Multiple listings map to the same source symbol.')
    if source.vendor_id == 'fugle' and bindings is None and any(not re.fullmatch(r'[0-9]{4,6}\.(TW|TWO)', t) for t in tickers):
        raise ValueError('This Fugle stock model supports Taiwan TW/TWO listings only.')
    if source.vendor_id == 'fugle' and bindings is not None and any(
            b['type']!='TW_LOCAL' or not re.fullmatch(r'[0-9]{4,6}[A-Z]?', b['symbol']) for b in bindings):
        raise ValueError('This Fugle stock model requires native Taiwan stock symbol bindings.')
    fields = query.get('fields', list(model.get('fields', {})))
    if not isinstance(fields, list) or not fields or len(fields)>100 or any(f not in model.get('fields', {}) for f in fields):
        raise ValueError('Requested fields must belong to the declared model.')
    span = query.get('range', '1M')
    if span not in ('1D','1W','1M','3M','1Y'): raise ValueError('Unsupported history span.')
    mapping = mapping_resource(source.vendor_id, schema)
    if not mapping: raise ValueError('No installed source mapping for this model.')
    if request.get('mapping_ref') and request['mapping_ref'] != reference(mapping):
        raise ValueError('Requested mapping is not the installed immutable version.')
    if source.vendor_id == 'lseg_workspace':
        from .workspace import steps as workspace_steps
        if bindings is not None and any(b['type']!='RIC' for b in bindings):raise ValueError('Workspace price models require RIC bindings.')
        steps = workspace_steps(source, schema, tickers, fields, span, query.get('arguments', {}), symbols if bindings is not None else None)
        return {'schema':schema,'model':model,'mapping':mapping,'query':query,'fields':fields,'steps':steps,'range':span,'invalid':invalid}
    steps = []
    endpoints = list(source.spec_snapshot.endpoints) if source.spec_snapshot else []
    for operation in operations(source.vendor_id, schema, set(fields), span):
        endpoint = next((e for e in endpoints if e.endpoint_id==operation or e.operation.removeprefix('yfinance.')==operation), None)
        if endpoint is None or not source.is_endpoint_executable(endpoint):
            raise ValueError('Required source operation is unavailable: '+operation)
        for ticker in tickers:
            params = dict(query.get('arguments', {}))
            params['symbol'] = symbols.get(ticker) or (re.sub(r'\.(TW|TWO)$','',ticker) if source.vendor_id=='fugle' else ticker)
            if operation == 'Ticker.history' or operation == 'TIME_SERIES_INTRADAY' or 'historical.candles' in operation:
                defaults=history_parameters(source.vendor_id,span)
                if any(params.get(key) for key in ('start','end','from','to')):
                    defaults.pop('period',None);defaults.pop('from',None);defaults.pop('to',None)
                    params.pop('period',None)
                for key,value in defaults.items():params.setdefault(key,value)
                # yfinance accepts date strings or epoch seconds, but rejects
                # ISO datetimes. Preserve the exact UTC bounds supplied by a
                # historical replay instead of discarding its time of day.
                if source.vendor_id=='yfinance' and operation=='Ticker.history':
                    for key in ('start','end'):
                        value=params.get(key)
                        if isinstance(value,str) and 'T' in value:
                            date=datetime.fromisoformat(value.replace('Z','+00:00'))
                            if date.tzinfo is None:raise ValueError('Historical datetime bounds require a timezone.')
                            params[key]=int(date.timestamp())
            if operation == 'NEWS_SENTIMENT':
                params.pop('symbol',None);params.update(tickers=symbols.get(ticker,ticker),sort='LATEST',limit=50)
            if operation == 'Search.all':
                params={'query':params['symbol']}
            steps.append({'ticker':ticker,'endpoint':endpoint,'parameters':params,'operation':operation})
    # Only codes that cannot be listings: nothing to ask the vendor; the answer is their notes.
    if not steps and not (invalid and not tickers): raise ValueError('No source execution plan is available.')
    return {'schema':schema,'model':model,'mapping':mapping,'query':query,'fields':fields,'steps':steps,'range':span,'invalid':invalid}


def _epoch(value):
    try:
        raw=float(value)
        divisor=1_000_000 if raw>10_000_000_000_000 else 1000 if raw>10_000_000_000 else 1
        return datetime.fromtimestamp(raw/divisor,timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError,OSError): return None


def search_news(payload, step):
    """Yahoo search news is fuzzy; keep only items Yahoo tags with the listing,
    or, on an issuer retry, with a listing Yahoo names for that same issuer."""
    accepted={step.get('listing',step['parameters']['query']).upper()}
    if step.get('issuer'):
        accepted|={str(q.get('symbol','')).upper() for q in payload.get('quotes') or []
                   if isinstance(q,dict) and q.get('longname')==step['issuer']}
    rows=[]
    for item in payload.get('news') or []:
        if not isinstance(item,dict): continue
        tags=[t for t in dict.fromkeys(item.get('relatedTickers') or []) if isinstance(t,str)]
        if accepted.isdisjoint(t.upper() for t in tags): continue
        rows.append({**clean(item),'ticker':step['ticker'],'headline':item.get('title'),
                     'publishedAt':_epoch(item.get('providerPublishTime')),'publisher':item.get('publisher'),
                     'url':item.get('link'),'instrumentReferences':tags,'_query':step['parameters']['query']})
    return rows


def issuer_step(payload, step):
    """Yahoo search indexes no news under some listings, such as Taiwan symbols.
    Retry once by the issuer name Yahoo returned for that exact listing."""
    if step['operation']!='Search.all' or 'issuer' in step: return None
    listing=step['parameters']['query'].upper()
    quote=next((q for q in payload.get('quotes') or [] if isinstance(q,dict) and str(q.get('symbol','')).upper()==listing),{})
    # Yahoo matches news to the short name; the long name identifies the issuer's listings.
    if not quote.get('longname'): return None
    return {**step,'parameters':{'query':quote.get('shortname') or quote['longname']},'listing':listing,'issuer':quote['longname']}


def converted_rows(source_id, payload, step, plan):
    ticker, operation = step['ticker'], step['operation']
    schema, fields = plan['schema'], plan['fields']
    if source_id == 'lseg_workspace':
        from .workspace import convert
        return convert(payload, step, schema)
    if isinstance(payload, dict) and payload.get('symbol'):
        actual = str(payload['symbol']).upper()
        expected = str(step['parameters'].get('symbol') or ticker).upper()
        if actual != expected: raise ValueError('Returned listing does not match the requested instrument.')
    requirement={'schema':schema,'fields':fields}
    rows = normalize(payload,ticker,requirement)
    if source_id == 'fugle':
        if schema == 'market.quotes.v1':
            total=payload.get('total') or {};trial=payload.get('isTrial') is True
            rows=[{**clean(payload),'ticker':ticker,'last':payload.get('lastPrice',payload.get('closePrice')),
                   'priceKind':'trial' if trial else 'close' if payload.get('isClose') else 'trade',
                   'change':payload.get('change'),'changePercent':payload.get('changePercent'),
                   'volume':total.get('tradeVolume',payload.get('volume')),'volumeUnit':'provider-unspecified',
                   'volumeScope':'session-cumulative','currency':'TWD',
                   'observedAt':_epoch(((payload.get('lastTrial') if trial else payload.get('lastTrade')) or {}).get('time')),
                   'sourceUpdatedAt':_epoch(payload.get('lastUpdated')),
                   'bid':(payload.get('bids') or [{}])[0].get('price'),'ask':(payload.get('asks') or [{}])[0].get('price')}]
        elif schema == 'company.profile.v1':
            rows=[{**clean(payload),'ticker':ticker,'nameZh':payload.get('name'),'nameEn':payload.get('nameEn')}]
        elif schema == 'market.history.v1':
            timeframe=str(payload.get('timeframe') or step['parameters'].get('timeframe',''))
            intraday=timeframe.isdigit()
            market=str(payload.get('market','')).upper();kind=str(payload.get('type','')).upper()
            standard=market in ('TSE','OTC') and kind in ('EQUITY','ETF','ETN','WARRANT')
            emerging=market in ('ESB','EMERGING')
            for row in rows:
                row.update(ticker=ticker,timestamp=row.get('date',row.get('timestamp')),adjustmentBasis='raw',volumeScope='interval')
                native=number(row.get('volume'))
                if native is not None and standard and intraday:
                    row.update(volume=native*1000,volumeUnit='shares',nativeVolume=native,nativeVolumeUnit='lots')
                elif emerging or standard:
                    row['volumeUnit']='shares'
                else:
                    row['volumeUnit']='provider-unspecified'
                row['currency']='TWD'
        elif schema == 'market.orderbook.v1':
            rows=[]
            for i in range(min(5,max(len(payload.get('bids',[])),len(payload.get('asks',[]))))):
                bid=(payload.get('bids',[])+[{}]*5)[i];ask=(payload.get('asks',[])+[{}]*5)[i]
                rows.append({'ticker':ticker,'level':i+1,'bid':bid.get('price'),'bidSize':bid.get('size'),
                             'ask':ask.get('price'),'askSize':ask.get('size'),'volumeUnit':'provider-unspecified',
                             'observedAt':_epoch(payload.get('lastUpdated'))})
        elif schema == 'market.trades.v1':
            rows=[{**clean(row),'ticker':ticker,'last':row.get('price'),
                   'priceKind':'trial' if step['parameters'].get('isTrial') is True else 'trade',
                   'volume':row.get('size'),'volumeScope':'single-trade',
                   'volumeUnit':'shares' if step['parameters'].get('type')=='oddlot' else 'provider-unspecified',
                   'observedAt':_epoch(row.get('time'))} for row in payload.get('data',[])]
    if operation == 'NEWS_SENTIMENT':
        rows=[{**clean(row),'ticker':ticker,'headline':row.get('title'),
               'publishedAt':datetime.strptime(row['time_published'],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).isoformat() if row.get('time_published') else None,
               'publisher':row.get('source'),'url':row.get('url'),'summary':row.get('summary')} for row in payload.get('feed',[])]
    if operation == 'Search.all':
        rows=search_news(payload,step)
    if operation == 'TIME_SERIES_DAILY' and plan.get('range'):
        rows=history_window(rows,plan['range'])
    for row in rows:
        row['_operation']=operation
        row['_retrievedAt']=datetime.now(timezone.utc).isoformat()
        if source_id == 'yfinance':
            unit = 'shares' if str(row.get('quoteType','')).upper() in ('EQUITY','ETF') else 'provider-unspecified'
            if schema == 'market.quotes.v1': row.update(priceKind='provider-price',volumeUnit=unit,volumeScope='session-cumulative')
            if schema == 'market.history.v1': row.update(volumeUnit=unit,volumeScope='interval',adjustmentBasis='raw')
            if schema == 'company.profile.v1':
                row.update(description=row.get('longBusinessSummary'),epsBasis='source-unspecified')
        if 'epsTtm' in row and 'epsBasis' not in row: row['epsBasis']='source-unspecified'
        if schema == 'company.financials.v1': row['periodType']='annual'
    return rows


def execute_model(source, request, auth_context):
    from .native import namespace, execute_native
    if str((request.get('target_model') or {}).get('id','')).startswith(namespace(source.vendor_id)+'.dataset.'):
        return execute_native(source, request, auth_context)
    if not any(reference(profile(s)) == request.get('target_model') for s in PROFILES):
        from .installed import execute_custom
        return execute_custom(source, request, auth_context)
    from .research_sources import owns, execute
    schema, _ = resolve_target(request['target_model'])
    if owns(source.vendor_id, schema):
        return execute(source, request, auth_context, schema)
    plan=consultant_history_plan(source,request) or model_plan(source,request)
    result=_run_plan(source,request,auth_context,plan)
    consultant=plan.get('consultant') or {}
    if consultant.get('kind')=='generated' and result.get('status')!='completed':
        # A plan the Consultant's LLM wrote failed here: report it and run the reviewed recipe's plan once.
        try:
            retry=consultant_history_plan(source,request,failed={'recipe':consultant.get('hash'),'error':result.get('error') or ''})
        except ValueError:
            retry=None  # the reviewed recipe's plan could not be had: the failed result stands
        if retry is not None:
            result=_run_plan(source,request,auth_context,retry)
    return result


CONSULTANT_UNREACHABLE = ('The Data Consultant, which plans history requests for this source, could not be reached. '
                          'Try again shortly.')


def consultant_history_plan(source, request, failed=None):
    """The Data Consultant's plan for a dated history request this source runs itself, as this module's steps.

    Only the Consultant plans a dated history request: without its plan the request fails with a clear
    message, and the source never plans it itself. Its refusal (a range this source does not keep, an
    instrument it does not cover) is the answer. None only for what the Consultant does not plan: another
    model, a request without dates, or a stand-in source that cannot ask it.
    """
    ask = getattr(source, 'consultant_plan', None)
    if not callable(ask):
        return None
    schema, model = resolve_target(request.get('target_model'))
    query = request.get('model_request') or {}
    arguments = query.get('arguments') if isinstance(query, dict) and isinstance(query.get('arguments'), dict) else {}
    if schema != 'market.history.v1' or not arguments.get('start') or not arguments.get('end'):
        return None
    fields = query.get('fields', list(model.get('fields', {})))
    if not isinstance(fields, list) or not fields or len(fields)>100 or any(f not in model.get('fields', {}) for f in fields):
        raise ValueError('Requested fields must belong to the declared model.')
    mapping = mapping_resource(source.vendor_id, schema)
    if not mapping:
        raise ValueError('No installed source mapping for this model.')
    canonical = {'instruments': query.get('instruments'), 'start': arguments['start'], 'end': arguments['end'],
                 'interval': arguments.get('interval'),
                 **({'instrumentBindings': query['instrumentBindings']} if query.get('instrumentBindings') is not None else {})}
    reply = ask(canonical, failed)
    if not isinstance(reply, dict):
        raise ValueError(CONSULTANT_UNREACHABLE)
    if reply.get('error'):
        raise ValueError(str(reply['error'])[:300])
    endpoints = {e.endpoint_id: e for e in (source.spec_snapshot.endpoints if source.spec_snapshot else ())}
    known = operations(source.vendor_id, schema, set(fields), '1M')
    def named(endpoint):
        return next((op for op in known if endpoint.endpoint_id == op
                     or endpoint.operation.removeprefix(source.vendor_id + '.') == op), None)
    steps = []
    for call in reply.get('calls') or []:
        endpoint = endpoints.get(str(call.get('endpointId') or ''))
        if endpoint is None or not source.is_endpoint_executable(endpoint) or named(endpoint) is None:
            raise ValueError('The Data Consultant planned a call this source does not offer.')
        parameters = dict(call.get('parameters') or {})
        if set(parameters) - {p.name for p in endpoint.parameters}:
            raise ValueError('The Data Consultant planned parameters this source does not declare.')
        steps.append({'ticker': str(call.get('instrument') or ''), 'endpoint': endpoint, 'parameters': parameters,
                      'operation': named(endpoint)})
    if not steps:
        raise ValueError(CONSULTANT_UNREACHABLE)
    # The metadata calls this source's conversion joins onto the bars (yfinance's Ticker.info: currency, units).
    planned = {id(s['endpoint']) for s in steps}
    for operation in known:
        endpoint = next((e for e in endpoints.values() if named(e) == operation and id(e) not in planned), None)
        if endpoint is None or operation == steps[0]['operation']:
            continue
        declared = {p.name for p in endpoint.parameters}
        for ticker, first in {s['ticker']: s for s in reversed(steps)}.items():
            steps.append({'ticker': ticker, 'endpoint': endpoint, 'operation': operation,
                          'parameters': {k: v for k, v in first['parameters'].items() if k in declared}})
    notes = [{'endpointId': '', 'ticker': n.get('instrument', ''), 'status': 'note', 'reason': n.get('note', '')}
             for n in reply.get('notes') or [] if isinstance(n, dict)]
    return {'schema': schema, 'model': model, 'mapping': mapping, 'query': query, 'fields': fields, 'steps': steps,
            'range': None, 'invalid': [], 'notes': notes,
            'consultant': {'kind': (reply.get('recipe') or {}).get('kind'), 'hash': (reply.get('recipe') or {}).get('hash')}}


def _run_plan(source, request, auth_context, plan):
    rows, failures, endpoints, costs = [], [], [], []
    received=time.time()
    steps=list(plan['steps'])
    for step in steps:  # an issuer retry is appended and runs in this loop
        result=source.execute_endpoint(step['endpoint'],step['parameters'],auth_context)
        source._record_data_access_result(result)
        endpoints.append(step['endpoint'].endpoint_id);costs.append(dict(result.cost))
        if result.status!='completed':
            failures.append({'endpointId':step['endpoint'].endpoint_id,'ticker':step['ticker'],'status':result.status,'reason':result.error or 'Source data unavailable.'})
            continue
        try:
            payload=clean(result.data)
            converted=converted_rows(source.vendor_id,payload,step,plan)
            rows.extend(converted)
            # Yahoo answers an unknown listing, such as an OTC code with .TW, with an empty quote.
            if source.vendor_id=='yfinance' and step['operation']=='Ticker.info' and not (isinstance(payload,dict) and payload.get('quoteType')):
                taiwan=re.search(r'\.TWO?$',step['ticker'],re.IGNORECASE)
                failures.append({'endpointId':step['endpoint'].endpoint_id,'ticker':step['ticker'],'status':'not-found',
                                 'reason':TAIWAN_LISTING_NOT_FOUND if taiwan else LISTING_NOT_FOUND})
            retry=None if converted else issuer_step(payload,step)
            if retry: steps.append(retry)
        except (ValueError,TypeError,OverflowError) as exc:
            failures.append({'endpointId':step['endpoint'].endpoint_id,'ticker':step['ticker'],'status':'invalid','reason':str(exc)})
    if source.vendor_id == 'yfinance' and plan['schema'] == 'market.history.v1':
        metadata = {row['ticker']:row for row in rows if row.get('_operation')=='Ticker.info'}
        for row in rows:
            info = metadata.get(row.get('ticker'), {})
            if row.get('_operation')=='Ticker.history':
                if str(info.get('quoteType','')).upper() in ('EQUITY','ETF'): row['volumeUnit']='shares'
                if info.get('currency'): row['currency']=info['currency']
    # An empty news search still answers the request: no news is tagged with the listing.
    answered=plan['schema']=='research.news.v1' and not failures
    if answered:
        found={row.get('ticker') for row in rows}
        failures=[{'endpointId':plan['steps'][0]['endpoint'].endpoint_id,'ticker':ticker,'status':'no-news','reason':NO_TAGGED_NEWS}
                  for ticker in dict.fromkeys(step['ticker'] for step in plan['steps']) if ticker not in found]
    rows=semantic_rows(rows,{'fields':plan['fields']})
    if plan['schema']=='market.history.v1' and plan.get('range'): rows=history_observations(rows,plan['range'])
    if plan.get('consultant'):
        # Windows of one request may meet at a bar; each (ticker, time) is kept once, in time order.
        kept={}
        for row in rows: kept.setdefault((row.get('ticker'),str(row.get('timestamp'))),row)
        rows=sorted(kept.values(),key=lambda row:(str(row.get('ticker')),str(row.get('timestamp'))))
        failures=failures+list(plan.get('notes') or [])
    # Join statement metadata only on the listing. Never join observations by
    # row number or inject another listing's currency or timestamps.
    if plan['schema']=='company.financials.v1':
        for row in rows: row.setdefault('periodType','annual')
    rows=clean(rows)
    # A listing the source has no statements for is named under the table instead of silently missing.
    # The note never guesses why (market, listing type); it says only what this source returned.
    if plan['schema'] in ('company.financials.v1','company.peers.v1'):
        noted={f.get('ticker') for f in failures if f.get('status')=='not-found'}
        found={row.get('ticker') for row in rows if row.get('period')}
        failures=failures+[{'endpointId':plan['steps'][0]['endpoint'].endpoint_id,'ticker':ticker,'status':'no-financials','reason':NO_FINANCIALS}
                           for ticker in dict.fromkeys(step['ticker'] for step in plan['steps']) if ticker not in found and ticker not in noted]
    # Peers measured at different period ends are labelled, never presented as one period.
    if plan['schema']=='company.peers.v1' and len({row.get('period') for row in rows if row.get('period')})>1:
        failures=failures+[{'endpointId':plan['steps'][0]['endpoint'].endpoint_id,'ticker':'','status':'mixed-periods','reason':MIXED_PERIODS}]
    # Each code that cannot be a listing is named under the table; a long one is shortened for display.
    failures=[{'endpointId':'','ticker':code if len(code)<=40 else code[:39]+'…','status':'invalid','reason':INVALID_LISTING}
              for code in dict.fromkeys(plan.get('invalid',[]))]+failures
    answered=answered or (bool(plan.get('invalid')) and not plan['steps'])  # an empty table that its notes explain
    validate_payload(plan['model'],rows)
    missing=sorted(f for f in plan['fields'] if not rows or any(r.get(f) is None for r in rows))
    lineage=[{f:{'status':'available' if row.get(f) is not None else 'missing','sourceId':source.vendor_id,
                 'modelFieldRef':plan['model']['fields'][f],'observedAt':row.get('observedAt',row.get('timestamp',row.get('period'))),
                 'receivedAt':received,'unit':row.get('volumeUnit') if f=='volume' else None} for f in plan['fields']} for row in rows]
    canonical={'schema':'canonical-result/1','modelRef':reference(plan['model']),'mappingRefs':[reference(plan['mapping'])],
               'payload':rows,'lineage':lineage,'missingFields':missing,'issues':failures,'receivedAt':received,
               'implementationDigest':implementation_digest(),'endpointIds':sorted(set(endpoints)),
               'verification':'declared','sourceId':source.vendor_id,'costs':costs}
    from attas.data_agent_network.scaffold.fields import build_data_fetch_contract
    data_schema={'type':'object','properties':{'items':plan['model']['interface']['response_schema']}}
    contract=build_data_fetch_contract({'dataset_id':'model:'+plan['model']['id'],'pulse_name':'data_fetch',
                                        'data_schema':data_schema}, {'items':rows})
    return {'status':'completed' if rows or answered else 'failed','source':source._public_source(),'backend_id':source.vendor_id,
            'dataset_id':'model:'+plan['model']['id'],'data':{'items':rows},'data_schema':data_schema,**contract,'canonical_data':{'items':rows},
            'canonical_result':canonical,'warnings':[r['reason'] for r in failures],
            'error':'' if rows or answered else 'No source observations satisfy this model request.'}
