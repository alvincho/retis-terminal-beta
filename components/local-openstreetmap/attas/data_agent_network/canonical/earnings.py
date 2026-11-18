"""Explicit YFinance statement/event and forecast-period joins.

Event dates are linked only when a single later announcement within 120 days
has the same reported EPS (to cents) as the quarterly diluted EPS. Ambiguous
or non-GAAP mismatches remain missing. Forecasts join on Yahoo's period label;
asOf is the retrieval time, not an invented historical consensus timestamp.
"""
from datetime import datetime

def earnings_rows(rows,fields):
    if 'estimatedRevenue' in fields:
        periods={}
        for row in rows:
            operation=row.get('_operation','')
            if operation not in ('Ticker.earnings_estimate','Ticker.revenue_estimate'):continue
            period=row.get('period') or row.get('timestamp')
            if not period:continue
            value=periods.setdefault((row.get('ticker'),period),{'ticker':row.get('ticker'),'period':period,'asOf':row['_retrievedAt'],'asOfBasis':'retrieval-time snapshot'})
            value['estimatedEps' if operation=='Ticker.earnings_estimate' else 'estimatedRevenue']=row.get('avg')
        return list(periods.values())
    periods={};events=[]
    for row in rows:
        if row.get('_operation')=='Ticker.earnings_dates':events.append(row);continue
        if row.get('_operation')!='Ticker.quarterly_income_stmt':continue
        field={'Total Revenue':'reportedRevenue','Diluted EPS':'reportedEps'}.get(row.get('timestamp'))
        if not field:continue
        for period,value in row.items():
            if period[:4].isdigit():
                target=periods.setdefault((row.get('ticker'),period),{'ticker':row.get('ticker'),'period':period[:10]})
                target[field]=value
    result=[]
    for row in periods.values():
        if all(row.get(field) is None for field in ('reportedRevenue','reportedEps')):continue
        matches=[]
        for event in events:
            try:
                when=event.get('Earnings Date') or event.get('timestamp')
                days=(datetime.fromisoformat(when).date()-datetime.fromisoformat(row['period']).date()).days
                equal=round(float(event['Reported EPS']),2)==round(float(row['reportedEps']),2)
                if event.get('ticker')==row['ticker'] and 0<days<=120 and equal:matches.append(when)
            except (ValueError,TypeError,KeyError):continue
        row['eventAt']=matches[0] if len(matches)==1 else None
        row['eventDateBasis']='unique announcement matching quarterly EPS and reporting window' if len(matches)==1 else 'unresolved'
        result.append(row)
    return result
