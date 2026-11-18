"""Source-side financial payload conversion; compatibility names remain available.

Provider fields retain their original values alongside canonical projections.
"""
import math
import re
import time
from datetime import datetime, timezone
from .earnings import earnings_rows

CANONICAL_SCHEMAS={"market.quotes.v1","market.history.v1","company.profile.v1","company.financials.v1","research.reports.v1","research.news.v1","company.peers.v1","company.earnings.v1","company.estimates.v1","portfolio.holdings.v1","market.fx.v1"}

def history_window(rows, chart_range, now=None):
    days = {'1D': 1, '1W': 7, '1M': 31, '3M': 93, '1Y': 365}[chart_range]
    cutoff = datetime.fromtimestamp((time.time() if now is None else now) - days * 86400, timezone.utc).date().isoformat()
    return sorted((row for row in rows if str(row.get('timestamp', '')) >= cutoff), key=lambda row: str(row.get('timestamp', '')))

def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if str(k).lower() not in ('api_key','apikey','password','access_token','refresh_token','scope_token','reservation_token')}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value

def normalize(data, ticker, requirement):
    """Explicit UI mappings; preserve native fields and missing-value semantics."""
    canonical=not requirement.get('schema') or requirement['schema'] in CANONICAL_SCHEMAS
    if not canonical:
        roots={f.split('.')[0] for f in requirement.get('fields',[])}
        for _ in range(4):
            if not isinstance(data,dict) or roots.intersection(data):break
            inner=next((data[k] for k in ('data','result','output','content') if isinstance(data.get(k),(dict,list))),None)
            if inner is None:break
            data=inner
    if not isinstance(data, dict):
        data = {'items': data if isinstance(data,list) else [data]}
    if canonical and isinstance(data.get('Global Quote'), dict):
        quote = data['Global Quote']
        data = {'ticker': quote.get('01. symbol'), 'last': number(quote.get('05. price')), 'changePercent': number(str(quote.get('10. change percent', '')).rstrip('%')), 'volume': number(quote.get('06. volume')), 'observedAt': quote.get('07. latest trading day')}
    daily = next((v for k, v in data.items() if k.startswith('Time Series') and isinstance(v, dict)), None)
    if canonical and daily is not None:
        data = {'items': [{'timestamp': date, 'open':number(row.get('1. open')), 'high':number(row.get('2. high')), 'low':number(row.get('3. low')), 'close': number(row.get('4. close')), 'volume': number(row.get('5. volume')), 'ticker': ticker} for date, row in sorted(daily.items())]}
    aliases = {'symbol': 'ticker', 'Symbol': 'ticker', 'Name': 'name', 'Sector': 'sector', 'Currency': 'currency', 'MarketCapitalization': 'marketCap', 'PERatio': 'priceEarnings', 'shortName': 'name', 'longName': 'name', 'regularMarketPrice': 'last', 'currentPrice': 'last', 'regularMarketChangePercent': 'changePercent', 'regularMarketVolume': 'volume', 'trailingPE': 'priceEarnings', 'operatingMargins': 'operatingMargin', 'Open':'open', 'High':'high', 'Low':'low', 'Close': 'close', 'Adj Close': 'adjustedClose', 'adjusted_close': 'adjustedClose', 'Volume': 'volume', 'Date': 'timestamp', 'Datetime': 'timestamp', 'index': 'timestamp'}
    if not canonical:aliases={}
    rows = next((data[k] for k in ('records','items','data','rows') if isinstance(data.get(k),list)),[data])
    result = []
    for row in rows[:2000]:
        if not isinstance(row, dict):
            result.append({'value': row, 'ticker': ticker})
            continue
        row = clean(row)
        mapped = {**row, **{aliases.get(k, k): v for k, v in row.items()}}
        if canonical and 'change' in requirement.get('fields',[]) and mapped.get('change') is None:
            change=number(row.get('regularMarketChange'))
            last,previous=number(row.get('regularMarketPrice')),number(row.get('regularMarketPreviousClose'))
            if change is None and last is not None and previous is not None:change=round(last-previous,10)
            if change is not None:mapped['change']=change
        if requirement.get('fields') == ['value'] and 'value' not in mapped:
            mapped['value'] = row
        for field in requirement.get('fields',[]):
            if '.' in field:
                value=row
                for key in field.split('.'):
                    value=value.get(key) if isinstance(value,dict) else None
                mapped[field]=value
        mapped.setdefault('ticker', ticker)
        english=row.get('nameEn') or row.get('longName') or row.get('shortName') or row.get('Name')
        if isinstance(english,str) and not re.search(r'[\u3400-\u9fff]',english):mapped['nameEn']=english
        for native,canonical_name in {'trailingEps':'epsTtm','EPS':'epsTtm','priceToBook':'priceBook','PriceToBookRatio':'priceBook','52WeekHigh':'fiftyTwoWeekHigh','52WeekLow':'fiftyTwoWeekLow'}.items():
            if native in row:mapped[canonical_name]=number(row[native])
        if 'dividendYield' in row:mapped['dividendYieldPercent']=number(row['dividendYield'])
        if 'DividendYield' in row:
            ratio=number(row['DividendYield']);mapped['dividendYieldPercent']=ratio*100 if ratio is not None else None
        result.append(mapped)
    return result

def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None

def semantic_rows(rows, requirement):
    fields = set(requirement['fields'])
    if fields & {'reportedRevenue','estimatedRevenue'} and any(r.get('_operation') for r in rows):
        return earnings_rows(rows,fields)
    currencies = {row.get('ticker'):row['currency'] for row in rows if row.get('currency')}
    if fields & {'revenue', 'netIncome', 'freeCashFlow'}:
        # Statements use reporting currency, which can differ from the listing.
        currencies={row.get('ticker'):row['financialCurrency'] for row in rows if row.get('financialCurrency')}
        periods = {}
        names = {'Total Revenue': 'revenue', 'Net Income': 'netIncome', 'Free Cash Flow': 'freeCashFlow'}
        for row in rows:
            metric = names.get(row.get('timestamp'))
            if not metric:
                continue
            for period, value in row.items():
                if period[:4].isdigit():
                    entry = periods.setdefault((row.get('ticker'), period), {'ticker': row.get('ticker'), 'period': period[:10], 'currency': currencies.get(row.get('ticker'))})
                    entry[metric] = value
        # Yahoo retains placeholder date columns beyond its statement coverage.
        # Drop only completely absent periods, retaining partially missing ones.
        return [row for row in periods.values() if any(row.get(f) is not None for f in ('revenue','netIncome','freeCashFlow'))]
    for row in rows:
        if 'currency' in fields:
            row.setdefault('currency', currencies.get(row.get('ticker')))
        content = row.get('content')
        if isinstance(content, dict) and 'headline' in fields:
            row.update(headline=content.get('title'), publishedAt=content.get('pubDate'), url=(content.get('canonicalUrl') or {}).get('url'), instrumentReferences=[row.get('ticker')],summary=content.get('summary'),publisher=(content.get('provider') or {}).get('displayName'))
        if 'headline' in fields and not isinstance(content,dict):
            published=row.get('publishedAt',row.get('providerPublishTime'))
            if isinstance(published,(int,float)):published=datetime.fromtimestamp(published,timezone.utc).isoformat()
            row.update(headline=row.get('headline') or row.get('title'),publishedAt=published,url=row.get('url') or row.get('link'),publisher=row.get('publisher'),summary=row.get('summary'))
        if 'last' in fields and 'regularMarketTime' in row:
            row['observedAt'] = datetime.fromtimestamp(row['regularMarketTime'], timezone.utc).isoformat()
        if 'period' in fields and 'mostRecentQuarter' in row:
            row['period'] = datetime.fromtimestamp(row['mostRecentQuarter'], timezone.utc).date().isoformat()
        if 'EPS Estimate' in row or 'Reported EPS' in row:
            row.update(eventAt=row.get('timestamp'), estimatedEps=row.get('EPS Estimate'), reportedEps=row.get('Reported EPS'), surprisePercent=row.get('Surprise(%)'))
    if fields & {'close', 'adjustedClose'}:
        rows = [row for row in rows if row.get('close') is not None or row.get('adjustedClose') is not None]
    return rows
