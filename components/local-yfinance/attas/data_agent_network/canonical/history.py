"""Chart span and observation interval are separate provider request dimensions."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException


def history_parameters(source, span, now=None):
    interval={'1D':'1m','1W':'5m'}.get(span,'1d')
    if source=='yfinance':
        return {'period':{'1D':'1d','1W':'5d','1M':'1mo','3M':'3mo','1Y':'1y'}[span], 'interval':interval,'auto_adjust':False}
    if source=='alpha_vantage':
        return {'interval':'1min' if span=='1D' else '5min','outputsize':'full'} if span in ('1D','1W') else {}
    if source=='fugle':
        today=(now or datetime.now(ZoneInfo('Asia/Taipei'))).date()
        # Include non-trading days, then select actual sessions from the response.
        days={'1D':7,'1W':10,'1M':31,'3M':93,'1Y':364}[span]
        return {'from':(today-timedelta(days=days)).isoformat(),'to':today.isoformat(),
                'timeframe':{'1D':'1','1W':'5'}.get(span,'D'),'sort':'asc','fields':'open,high,low,close,volume'}
    return {}


def history_observations(rows, span):
    rows=sorted(rows,key=lambda row:str(row.get('timestamp','')))
    if span not in ('1D','1W'):return rows
    # Never present daily observations as intraday bars, even if a provider
    # ignores an interval parameter or the account lacks an intraday entitlement.
    timed=[r for r in rows if len(str(r.get('timestamp','')))>10]
    if rows and not timed:raise HTTPException(409,'Source returned daily bars; the requested intraday interval is unavailable.')
    days=sorted({str(r['timestamp'])[:10] for r in timed})[-(1 if span=='1D' else 5):]
    return [r for r in timed if str(r['timestamp'])[:10] in days]


def interval_label(span):
    return {'1D':'1-minute bars · latest trading session','1W':'5-minute bars · latest 5 trading sessions'}.get(span,'Daily bars · selected date range')
