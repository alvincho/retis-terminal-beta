"""Explicit financial adapter coverage owned by the Data Sources."""
ADAPTERS = {
    'yfinance': {
        'Ticker.info': {'market.quotes.v1':{'ticker','last','changePercent','volume','observedAt','currency'}, 'company.profile.v1':{'ticker','name','nameEn','sector','currency','marketCap','epsTtm','priceEarnings','priceBook','dividendYieldPercent','fiftyTwoWeekHigh','fiftyTwoWeekLow'}, 'company.peers.v1':{'ticker','period','marketCap','priceEarnings','revenueGrowth','operatingMargin'}},
        'Ticker.history': {'market.history.v1':{'ticker','timestamp','open','high','low','close','adjustedClose','volume'}},
        'Search.all': {'research.news.v1':{'headline','publishedAt','instrumentReferences','url'}},
        'Ticker.income_stmt': {'company.financials.v1':{'period','revenue','netIncome'}},
        'Ticker.cashflow': {'company.financials.v1':{'period','freeCashFlow'}},
        'Ticker.earnings_dates': {'company.earnings.v1':{'ticker','eventAt','reportedEps'},'company.estimates.v1':{'ticker','estimatedEps'}},
        'Ticker.quarterly_income_stmt': {'company.earnings.v1':{'ticker','period','reportedRevenue','reportedEps'}},
        'Ticker.earnings_estimate': {'company.estimates.v1':{'ticker','period','asOf','estimatedEps'}},
        'Ticker.revenue_estimate': {'company.estimates.v1':{'ticker','period','asOf','estimatedRevenue'}},
    },
    'alpha_vantage': {
        'GLOBAL_QUOTE': {'market.quotes.v1':{'ticker','last','changePercent','volume','observedAt'}},
        'OVERVIEW': {'company.profile.v1':{'ticker','name','nameEn','sector','currency','marketCap','epsTtm','priceEarnings','priceBook','dividendYieldPercent','fiftyTwoWeekHigh','fiftyTwoWeekLow'},'company.peers.v1':{'ticker','marketCap','priceEarnings'}},
        'TIME_SERIES_DAILY': {'market.history.v1':{'ticker','timestamp','open','high','low','close','volume'}},
        'TIME_SERIES_INTRADAY': {'market.history.v1':{'ticker','timestamp','open','high','low','close','volume'}},
        'NEWS_SENTIMENT': {'research.news.v1':{'ticker','headline','publishedAt','url','summary','publisher'}},
    },
    'fugle': {
        '/intraday/ticker/{symbol}': {'company.profile.v1':{'ticker','nameZh','nameEn'}},
        '/historical/candles/{symbol}': {'market.history.v1':{'ticker','timestamp','open','high','low','close','volume'}},
        '/intraday/quote/{symbol}': {'market.quotes.v1':{'ticker','last','changePercent','volume','observedAt','currency'}},
    },
}

SCHEMAS={'quotes':'market.quotes.v1','history':'market.history.v1','profile':'company.profile.v1','metrics':'company.peers.v1','financials':'company.financials.v1','news':'research.news.v1','event':'company.earnings.v1','estimates':'company.estimates.v1'}

FUGLE_STREAM_MAPPINGS = {
    f'streaming:{product}:{channel}': ('market.quotes.v1' if (product,channel)==('stock','aggregates') else f'fugle.{product}.{channel}.v1')
    for product, channels in [('stock',('trades','candles','books','aggregates','indices')),('futopt',('trades','candles','books','aggregates'))]
    for channel in channels
}

# These dimensions are produced by the source adapter, including explicit
# provider-unspecified values where the public contract does not state a unit.
for source,operation in [('yfinance','Ticker.info'),('alpha_vantage','GLOBAL_QUOTE'),('fugle','/intraday/quote/{symbol}')]:
    if source != 'alpha_vantage':
        ADAPTERS[source][operation]['market.quotes.v1'].update({'priceKind','volumeUnit','volumeScope'})
ADAPTERS['yfinance']['Ticker.info']['market.quotes.v1'].update({'change','exchange'})
ADAPTERS['fugle']['/intraday/quote/{symbol}']['market.quotes.v1'].update({'change','bid','ask','sourceUpdatedAt','market','exchange'})
ADAPTERS['yfinance']['Ticker.info']['company.profile.v1'].update({'industry','country','website','description','epsBasis'})
ADAPTERS['alpha_vantage']['OVERVIEW']['company.profile.v1'].add('epsBasis')
ADAPTERS['fugle']['/intraday/ticker/{symbol}']['company.profile.v1']={'ticker','nameZh'}
for source,operation in [('yfinance','Ticker.history'),('fugle','/historical/candles/{symbol}')]:
    ADAPTERS[source][operation]['market.history.v1'].update({'volumeUnit','volumeScope','adjustmentBasis','currency'})
ADAPTERS['yfinance']['Search.all']['research.news.v1'].update({'ticker','publisher'})
ADAPTERS['fugle']['/intraday/quote/{symbol}']['market.orderbook.v1']={'ticker','level','bid','ask','bidSize','askSize','volumeUnit','observedAt'}
ADAPTERS['fugle']['/intraday/trades/{symbol}']={'market.trades.v1':{'ticker','last','priceKind','volume','volumeUnit','volumeScope','observedAt'}}

from .research_sources import OPERATIONS, fields_for
from .workspace import coverage as workspace_coverage
ADAPTERS['lseg_workspace'] = workspace_coverage()
for source, schemas in OPERATIONS.items():
    for schema, operation in schemas.items():
        ADAPTERS.setdefault(source, {}).setdefault('/data' if source == 'finmind' else operation, {})[schema] = set(fields_for(source, schema))
