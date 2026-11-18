"""Source-owned Taiwan research and analytic adapters with explicit coverage."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import copy
import re
import time

from prompits.core.resource_registry import reference
from prompits.core.pulse_models import validate_payload
from .research_models import PROFILES, ANALYTIC_SCHEMAS
from .finance import clean, number as native_number

def number(value):
    if isinstance(value,bool): return None
    return native_number(value.replace(',','').strip() if isinstance(value,str) else value)

OPERATIONS = {
 'twse': {'market.rankings.v1':'/exchangeReport/STOCK_DAY_ALL', 'market.quotes.v1':'/exchangeReport/STOCK_DAY_ALL',
          'company.profile.v1':'/exchangeReport/BWIBBU_ALL', 'market.indices.v1':'/exchangeReport/MI_INDEX',
          'company.events.v1':'/exchangeReport/TWT48U_ALL','company.distributions.v1':'/exchangeReport/TWT48U_ALL'},
 'tpex': {'market.rankings.v1':'/tpex_mainboard_daily_close_quotes','market.quotes.v1':'/tpex_mainboard_daily_close_quotes',
          'company.profile.v1':'/tpex_mainboard_peratio_analysis','market.indices.v1':'/tpex_index',
          'company.events.v1':'/tpex_exright_prepost','company.distributions.v1':'/tpex_exright_prepost'},
 'finmind': {'company.institutional.v1':'TaiwanStockInstitutionalInvestorsBuySell',
             'company.margin.v1':'TaiwanStockMarginPurchaseShortSale',
             'fund.holdings.v1':'TaiwanStockActiveETFHolding',
             'company.distributions.v1':'TaiwanStockDividend', 'company.events.v1':'TaiwanStockDividend',
             'company.statement-items.v1':'TaiwanStockFinancialStatements'},
 'yfinance': {'fund.holdings.v1':'Ticker.funds_data',
              **{schema:'Ticker.history' for schema in ANALYTIC_SCHEMAS}},
}


def fields_for(source, schema):
    if schema == 'company.profile.v1': return ['ticker','nameZh','priceEarnings','priceBook','dividendYieldPercent']
    if schema == 'market.quotes.v1': return ['ticker','last','change','changePercent','volume','volumeUnit','volumeScope','currency','observedAt','priceKind']
    fields = list(PROFILES[schema])
    if source in ('twse','tpex') and schema in ('company.events.v1','company.distributions.v1'):
        fields.remove('paymentDate')
    if source == 'yfinance' and schema == 'fund.holdings.v1':
        fields = ['ticker','holdingId','holdingName','weightPercent']
    return fields


def owns(source, schema): return schema in OPERATIONS.get(source,{})


def date_value(value):
    if not value: return None
    value = str(value).strip()
    if re.fullmatch(r'\d{7}', value): return date(int(value[:3])+1911,int(value[3:5]),int(value[5:])).isoformat()
    if re.fullmatch(r'\d{8}', value): return date(int(value[:4]),int(value[4:6]),int(value[6:])).isoformat()
    if re.fullmatch(r'\d{2,3}/\d{1,2}/\d{1,2}', value):
        y,m,d = map(int,value.split('/')); return date(y+1911,m,d).isoformat()
    return date.fromisoformat(value.replace('/','-')[:10]).isoformat()


def convert(source, schema, payload, ticker, args, source_symbol=None):
    records = payload.get('top_holdings',payload.get('data',payload.get('items',payload))) if isinstance(payload,dict) else payload
    if not isinstance(records,list):
        if source == 'yfinance' and isinstance(records,dict): records = [dict(row,holdingId=key) for key,row in records.items() if isinstance(row,dict)]
        else: raise ValueError('The research endpoint did not return observation records.')
    result=[]
    for r in records:
        if not isinstance(r,dict): raise ValueError('Research rows must be objects.')
        if source in ('twse','tpex'):
            code = r.get('Code',r.get('SecuritiesCompanyCode'))
            listing = str(code)+('.TW' if source=='twse' else '.TWO') if code else None
            if schema != 'market.indices.v1' and not listing: continue
            if schema != 'market.rankings.v1' and schema != 'market.indices.v1':
                if (str(code)!=source_symbol if source_symbol is not None else listing!=ticker):continue
                listing=ticker
            when = date_value(r.get('Date',r.get('日期')))
            row={'ticker':listing,'nameZh':r.get('Name',r.get('CompanyName')), 'currency':'TWD','observedAt':when}
            if schema in ('market.rankings.v1','market.quotes.v1'):
                last,delta=number(r.get('ClosingPrice',r.get('Close'))),number(r.get('Change'))
                row.update(last=last,change=delta,changePercent=100*delta/(last-delta) if last is not None and delta is not None and last!=delta else None,
                           volume=number(r.get('TradeVolume',r.get('TradingShares'))),volumeUnit='shares',volumeScope='session-cumulative',
                           turnover=number(r.get('TradeValue',r.get('TransactionAmount'))),priceKind='close')
            elif schema=='company.profile.v1':
                row.update(priceEarnings=number(r.get('PEratio',r.get('PriceEarningRatio'))),priceBook=number(r.get('PBratio',r.get('PriceBookRatio'))),dividendYieldPercent=number(r.get('DividendYield',r.get('YieldRatio'))))
            elif schema=='market.indices.v1':
                if source=='twse':
                    if r.get('指數') != '發行量加權股價指數': continue
                    row.update(ticker='^TWII' if r.get('指數')=='發行量加權股價指數' else 'TWSE:'+str(r.get('指數')),
                               name=r.get('指數'),last=number(r.get('收盤指數')),change=number(r.get('漲跌點數')),changePercent=number(r.get('漲跌百分比')))
                    if str(r.get('漲跌','')).strip()=='-' and row['change'] is not None: row['change']=-abs(row['change'])
                else:
                    last,delta=number(r.get('Close')),number(r.get('Change'))
                    row.update(ticker='^TWOII',name='櫃買指數',last=last,change=delta,
                               changePercent=100*delta/(last-delta) if last is not None and delta is not None and last!=delta else None)
            else:
                row.update(eventAt=date_value(r.get('Date',r.get('ExRrightsExDividendDate'))),eventType='ex-dividend',
                           headline=r.get('Exdividend',r.get('ExRrightsExDividend')),cashDividend=number(r.get('CashDividend')))
        elif source=='finmind':
            if str(r.get('stock_id')) != (source_symbol if source_symbol is not None else ticker.split('.')[0]): continue
            row={'ticker':ticker,'timestamp':date_value(r.get('date'))}
            if schema=='company.institutional.v1':
                buy,sell=number(r.get('buy')),number(r.get('sell'))
                row.update(investor=r.get('name'),buyVolume=buy,sellVolume=sell,netVolume=buy-sell if buy is not None and sell is not None else None,volumeUnit='shares')
            elif schema=='company.margin.v1': row.update(marginBalance=number(r.get('MarginPurchaseTodayBalance')),shortBalance=number(r.get('ShortSaleTodayBalance')),volumeUnit='lots')
            elif schema=='fund.holdings.v1': row.update(asOf=row['timestamp'],holdingId=r.get('component_stock_id'),holdingName=r.get('component_stock_name'),weightPercent=number(r.get('weight')),shares=number(r.get('shares')),assetType=r.get('asset_type'),currency=r.get('currency'))
            elif schema=='company.statement-items.v1': row.update(period=row['timestamp'],metric=r.get('type'),name=r.get('origin_name'),value=number(r.get('value')),unit='provider-unspecified',periodType='source-reported')
            else: row.update(eventAt=date_value(r.get('CashExDividendTradingDate')),eventType='ex-dividend',headline='Cash distribution',cashDividend=number(r.get('CashEarningsDistribution')),paymentDate=date_value(r.get('CashDividendPaymentDate')),currency='TWD')
        else:
            weight=number(r.get('Holding Percent',r.get('holdingPercent')))
            row={'ticker':ticker,'holdingId':r.get('holdingId',r.get('Symbol',r.get('symbol',r.get('index')))),
                 'holdingName':r.get('Name',r.get('name')), 'weightPercent':weight*100 if weight is not None else None}
        result.append({k:v for k,v in row.items() if v is not None})
    if schema=='market.rankings.v1':
        sort=args.get('sort','turnover');direction=args.get('direction','descending')
        if sort not in ('turnover','volume','changePercent') or direction not in ('ascending','descending'): raise ValueError('Unsupported market ranking order.')
        limit=args.get('limit',30)
        if type(limit) is not int or not 1<=limit<=500:raise ValueError('Ranking limit must be between 1 and 500.')
        result=sorted((r for r in result if r.get(sort) is not None),key=lambda r:((-r[sort] if direction=='descending' else r[sort]),r['ticker']))[:limit]
        for i,row in enumerate(result): row.update(rank=i+1,method={'sort':sort,'direction':direction,'universe':source,'limit':limit})
    return result


def execute(source, request, auth_context, schema):
    from .catalog import profile
    from .service import mapping_resource, implementation_digest
    model=profile(schema);mapping=mapping_resource(source.vendor_id,schema)
    if request.get('target_model')!=reference(model) or request.get('mapping_ref') not in (None,reference(mapping)):
        raise ValueError('Research model or mapping does not match the installed version.')
    query=request.get('model_request') or {}; fields=query.get('fields',fields_for(source.vendor_id,schema)); args=query.get('arguments',{})
    if not isinstance(args,dict) or not fields or any(f not in model['fields'] for f in fields):raise ValueError('Invalid research fields or arguments.')
    tickers=query.get('instruments',[])
    if not isinstance(tickers,list) or not 1<=len(tickers)<=12 or any(not isinstance(t,str) or not re.fullmatch(r'\^?[A-Z0-9]+(?:[.-][A-Z0-9]+)*(?:=[A-Z]+)?',t) for t in tickers):
        raise ValueError('Provide 1–12 Yahoo-style tickers.')
    unsupported = []
    symbols = {}
    if query.get('instrumentBindings') is not None:
        bindings=query['instrumentBindings']
        from ..symbology_config import configured
        declaration=configured(source.vendor_id,getattr(source,'_config_data',{}).get('source_profile',{}))
        accepted=declaration['input_types'] or declaration['output_types']
        if not isinstance(bindings,list) or len(bindings)!=len(tickers):raise ValueError('Provide one symbol binding per requested instrument.')
        for binding in bindings:
            if (not isinstance(binding,dict) or set(binding)!={'instrument','symbol','type'} or binding['instrument'] not in tickers
                    or binding['instrument'] in symbols or binding['type'] not in accepted
                    or not isinstance(binding['symbol'],str) or not re.fullmatch(r'[A-Za-z0-9.^=_/-]{1,80}',binding['symbol'])):
                raise ValueError('Invalid research source symbol binding.')
            if source.vendor_id in ('twse','tpex','finmind') and binding['type']!='TW_LOCAL':raise ValueError('Taiwan research requires local symbol bindings.')
            symbols[binding['instrument']]=binding['symbol']
        if len(set(symbols.values()))!=len(symbols):raise ValueError('Multiple listings map to the same source symbol.')
    if source.vendor_id in ('twse','tpex','finmind') and schema not in ('market.indices.v1','market.rankings.v1'):
        endings=('.TW',) if source.vendor_id=='twse' else ('.TWO',) if source.vendor_id=='tpex' else ('.TW','.TWO')
        unsupported = [t for t in tickers if not t.endswith(endings)]
        tickers = [t for t in tickers if t.endswith(endings)]
        if not tickers:raise ValueError('The selected source does not cover these listings.')
    if schema in ('market.rankings.v1','market.indices.v1'):
        expected = '^TWII' if source.vendor_id == 'twse' else '^TWOII'
        if tickers != [expected]: raise ValueError('This source does not cover the requested market universe.')
    operation=OPERATIONS[source.vendor_id][schema];actual='/data' if source.vendor_id=='finmind' else operation
    endpoint=next((e for e in source.spec_snapshot.endpoints if e.operation.removeprefix('yfinance.')==actual),None)
    if not endpoint or not source.is_endpoint_executable(endpoint):raise ValueError('Research operation is not installed: '+operation)
    rows=[];issues=[{'ticker':t,'status':'unsupported','reason':'Listing is outside this source market.'} for t in unsupported];costs=[]
    for ticker in tickers[:1] if schema in ('market.rankings.v1','market.indices.v1') else tickers:
        params={}
        if source.vendor_id=='finmind':
            start=args.get('startDate',(date.today()-timedelta(days=365)).isoformat());end=args.get('endDate',date.today().isoformat())
            if not 0<=(date.fromisoformat(end)-date.fromisoformat(start)).days<=3653:raise ValueError('Research date range must be within ten years.')
            params={'dataset':operation,'data_id':symbols.get(ticker,ticker.split('.')[0]),'start_date':start,'end_date':end}
        elif source.vendor_id=='yfinance':
            params={'symbol':symbols.get(ticker,ticker)}
            if schema in ANALYTIC_SCHEMAS: params.update(period='2y',interval='1d',auto_adjust=False)
        fetched=source.execute_endpoint(endpoint,params,auth_context);source._record_data_access_result(fetched);costs.append(dict(fetched.cost))
        if fetched.status!='completed':
            issues.append({'ticker':ticker,'status':fetched.status,'reason':fetched.error or 'Provider did not return data.'});continue
        try:
            if schema in ANALYTIC_SCHEMAS:
                from .analytics import calculate
                from .finance import normalize
                bars=normalize(clean(fetched.data),ticker,{'schema':'market.history.v1','fields':['ticker','timestamp','open','close']})
                rows.extend(calculate(bars,ticker,schema,args))
            else: rows.extend(convert(source.vendor_id,schema,clean(fetched.data),ticker,args,symbols.get(ticker)))
        except (ValueError,TypeError,KeyError) as exc: issues.append({'ticker':ticker,'status':'invalid','reason':str(exc)})
    # Strict display projection: no provider fields or unrequested hidden data.
    rows=[{f:r[f] for f in fields if f in r} for r in rows]
    validate_payload(model,rows)
    received=time.time();missing=[f for f in fields if not rows or any(r.get(f) is None for r in rows)]
    envelope={'schema':'canonical-result/1','modelRef':reference(model),'mappingRefs':[reference(mapping)],
        'payload':rows,'missingFields':missing,'issues':issues,'sourceId':source.vendor_id,'receivedAt':received,
        'implementationDigest':implementation_digest(),'verification':'declared','endpointIds':[endpoint.endpoint_id],
        'costs':costs,'lineage':[{f:{'sourceId':source.vendor_id,'endpointId':endpoint.endpoint_id,'operation':operation,
            'status':'available' if f in row else 'missing','modelFieldRef':model['fields'][f],'receivedAt':received} for f in fields} for row in rows]}
    failed = not rows and bool(issues)
    return {'status':'failed' if failed else 'completed','canonical_result':envelope,'canonical_data':{'items':rows},
            'data':{'items':rows},'warnings':[i['reason'] for i in issues], 'error':'No research observations available.' if failed else ''}
