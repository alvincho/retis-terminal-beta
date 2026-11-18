"""Explicit Workspace field bindings. Layouts continue using canonical fields."""
from datetime import datetime, timedelta, timezone
import re

from .finance import number

FIELDS = {
    'market.quotes.v1': {
        'last': 'TR.PriceClose', 'changePercent': 'TR.PricePctChg1D', 'volume': 'TR.Volume',
        'currency': 'TR.PriceClose.currency', 'observedAt': 'TR.PriceClose.date',
    },
    'company.profile.v1': {
        'name': 'TR.CommonName', 'nameEn': 'TR.CommonName', 'sector': 'TR.TRBCEconomicSector',
        'industry': 'TR.TRBCIndustry', 'country': 'TR.HeadquartersCountry', 'website': 'TR.Website',
        'description': 'TR.BusinessSummary', 'marketCap': 'TR.CompanyMarketCap',
        'priceEarnings': 'TR.PE', 'priceBook': 'TR.PriceToBVPerShare',
        'dividendYieldPercent': 'TR.DividendYield', 'currency': 'TR.PriceClose.currency',
        'fiftyTwoWeekHigh': 'TR.Price52WeekHigh', 'fiftyTwoWeekLow': 'TR.Price52WeekLow',
    },
    'market.history.v1': {'open':'OPEN_PRC','high':'HIGH_1','low':'LOW_1','close':'TRDPRC_1','volume':'ACVOL_UNS'},
    'research.news.v1': {'headline':'headline','publishedAt':'versionCreated','publisher':'sourceCode'},
}
NUMBERS = {'last','change','changePercent','volume','open','high','low','close','marketCap','priceEarnings',
           'priceBook','dividendYieldPercent','fiftyTwoWeekHigh','fiftyTwoWeekLow'}
DEFAULT_SYMBOLS = {'NVDA':'NVDA.O','AAPL':'AAPL.O','MSFT':'MSFT.O','TSM':'TSM.N','^TWII':'.TWII'}


def coverage():
    result = {'get_data': {}, 'get_history': {}, 'news.get_headlines': {}}
    for schema, fields in FIELDS.items():
        operation = 'get_history' if schema == 'market.history.v1' else 'news.get_headlines' if schema == 'research.news.v1' else 'get_data'
        result[operation][schema] = set(fields) | {'ticker'}
    result['get_data']['market.quotes.v1'].update({'priceKind','volumeUnit','volumeScope'})
    result['get_history']['market.history.v1'].update({'timestamp','adjustmentBasis','volumeUnit','volumeScope'})
    result['news.get_headlines']['research.news.v1'].add('instrumentReferences')
    return result


def ric_for(source, ticker, arguments):
    # Native operations accept vendor IDs directly. Canonical inputs use Yahoo
    # listing tickers; never append a guessed US exchange suffix.
    settings = source._config_data.get('lseg_workspace', {})
    configured = {**DEFAULT_SYMBOLS, **settings.get('symbol_map', {})}
    overrides = arguments.get('ric_map', {})
    ric = overrides.get(ticker) or configured.get(ticker)
    if not ric and re.fullmatch(r'\d{4,6}[A-Z]?\.(TW|TWO)', ticker):
        ric = ticker
    if not isinstance(ric, str) or not re.fullmatch(r'[A-Za-z0-9.^=_-]{1,80}', ric):
        raise ValueError('Resolve this ticker with Workspace search and configure its exact RIC mapping: '+ticker)
    return ric


def steps(source, schema, tickers, fields, span, arguments, symbol_bindings=None):
    operation = 'get_history' if schema == 'market.history.v1' else 'news.get_headlines' if schema == 'research.news.v1' else 'get_data'
    endpoint = next(e for e in source.spec_snapshot.endpoints if e.operation == operation)
    output = []
    for ticker in tickers:
        ric = symbol_bindings[ticker] if symbol_bindings is not None else ric_for(source, ticker, arguments)
        if schema == 'research.news.v1':
            params = {'query': 'R:'+ric, 'count': 50}
        else:
            requested = list(dict.fromkeys(FIELDS[schema][f] for f in fields if f in FIELDS[schema]))
            # An identifier-only request still needs a non-empty data request.
            requested = requested or [next(iter(FIELDS[schema].values()))]
            params = {'universe': [ric], 'fields': requested}
            if schema == 'market.history.v1':
                days = {'1D':7,'1W':10,'1M':40,'3M':110,'1Y':390}[span]
                params.update(interval='1min' if span=='1D' else '5min' if span=='1W' else '1D',
                    start=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(), count=10000, adjustments='unadjusted')
        output.append({'ticker':ticker, 'ric':ric, 'endpoint':endpoint, 'parameters':params, 'operation':operation})
    return output


def convert(payload, step, schema):
    records = payload.get('rows', [])
    rows = []
    for original in records:
        # HeaderType.NAME returns provider field codes with SDK-normalized case.
        record = {str(k).casefold(): v for k,v in original.items()}
        instrument = record.get('instrument')
        if instrument is not None and str(instrument).casefold() != step['ric'].casefold():
            raise ValueError('Workspace returned a different instrument from the requested listing.')
        row = {'ticker': step['ticker']}
        for field, native in FIELDS[schema].items():
            value = record.get(native.casefold())
            # Single-instrument historical frames may have a RIC column level.
            if value is None:
                value = record.get((step['ric']+'|'+native).casefold())
            if field in NUMBERS:
                value = number(value)
            if value is not None:
                row[field] = value
        if schema == 'market.quotes.v1':
            # This canonical snapshot is the latest provider close, including
            # closed markets. Live ticks use the separate streaming operation.
            row.update(priceKind='close', volumeUnit='provider-unspecified', volumeScope='session-cumulative')
        if schema == 'market.history.v1':
            row.update(timestamp=record.get('timestamp',record.get('date',record.get('index'))),
                       adjustmentBasis='raw', volumeUnit='provider-unspecified', volumeScope='interval')
        if schema == 'research.news.v1':
            row['publishedAt'] = row.get('publishedAt',record.get('date',record.get('index')))
            row['instrumentReferences'] = [step['ticker']]
            # Story IDs remain provenance, never fabricated public article URLs.
            if record.get('storyid'):
                row['_storyId'] = record['storyid']
        row['_operation'] = step['operation']
        row['_retrievedAt'] = datetime.now(timezone.utc).isoformat()
        rows.append({k:v for k,v in row.items() if v is not None})
    return rows
