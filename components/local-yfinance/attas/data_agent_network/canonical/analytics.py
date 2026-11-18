"""Bounded, reproducible source-side research methods; no future observations."""
import math
from prompits.core.capabilities import digest


def calculate(rows, ticker, schema, arguments):
    def integer(key, default, minimum, maximum):
        value = arguments.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'{key} must be an integer between {minimum} and {maximum}.')
        return value
    fast, slow, period = integer('fast', 5, 2, 100), integer('slow', 20, 3, 250), integer('rsiPeriod', 14, 2, 100)
    if fast >= slow: raise ValueError('The fast window must be shorter than the slow window.')
    fee, slippage = arguments.get('feeBps', 15), arguments.get('slippageBps', 5)
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in (fee, slippage)):
        raise ValueError('Costs must be finite basis points from zero to 1000 per side.')
    ordered = sorted(rows, key=lambda r: str(r.get('timestamp','')))
    if len(ordered) > 10000 or len({r.get('timestamp') for r in ordered}) != len(ordered):
        raise ValueError('Analytics require distinct, bounded observation times.')
    if len(ordered) < max(slow, period + 1) + 1:
        raise ValueError(f'Insufficient history: need at least {max(slow, period+1)+1} observations.')
    for row in ordered:
        if not row.get('timestamp') or any(type(row.get(f)) not in (int,float) or not math.isfinite(row[f]) or row[f] <= 0 for f in ('open','close')):
            raise ValueError('Analytics require positive finite opening and closing prices for every bar.')
    method = {'id':'ai.attas.analytics.trailing-research','version':'1.0.0',
        'fast':fast,'slow':slow,'rsiPeriod':period,'rsiMethod':'arithmetic trailing gains/losses',
        'feeBpsPerSide':fee,'slippageBpsPerSide':slippage,
        'execution':'Prior close signal executes at next observed open; long/cash, no leverage.',
        'priceBasis':'raw prices; dividends and corporate actions excluded',
        'universe':'requested instruments; not a survivorship-free universe',
        'inputDigest':digest(ordered), 'inputCount':len(ordered),
        'firstObservation':ordered[0]['timestamp'],'lastObservation':ordered[-1]['timestamp']}
    closes = [r['close'] for r in ordered]; result=[]
    position, desired, cash, quantity, peak, trades = 0, 0, 1.0, 0.0, 1.0, 0
    for i, row in enumerate(ordered):
        # Execute yesterday's decision before observing today's closing price.
        if desired != position:
            rate = (fee + slippage) / 10000
            if desired:
                quantity, cash = cash / (row['open'] * (1 + rate)), 0.0
            else:
                cash, quantity = quantity * row['open'] * (1 - rate), 0.0
            position = desired; trades += 1
        equity = cash + quantity * row['close']; peak = max(peak, equity)
        if i < max(slow-1, period): continue
        a = sum(closes[i-fast+1:i+1]) / fast; b = sum(closes[i-slow+1:i+1]) / slow
        changes = [closes[j]-closes[j-1] for j in range(i-period+1, i+1)]
        gains, losses = sum(max(d,0) for d in changes), sum(max(-d,0) for d in changes)
        rsi = 50.0 if gains == losses == 0 else 100.0 if losses == 0 else 100 - 100/(1+gains/losses)
        desired = int(a > b)
        signal = 'above-sma' if desired else 'below-sma'
        condition = arguments.get('condition','above-sma')
        if condition not in ('above-sma','below-sma','rsi-below','rsi-above','price-above','price-below'):
            raise ValueError('Unsupported research condition.')
        threshold = arguments.get('threshold', 30)
        if type(threshold) not in (int,float) or not math.isfinite(threshold): raise ValueError('Condition threshold must be finite.')
        matched = {'above-sma':a>b,'below-sma':a<b,'rsi-below':rsi<threshold,'rsi-above':rsi>threshold,
                   'price-above':row['close']>threshold,'price-below':row['close']<threshold}[condition]
        result.append({'ticker':ticker,'timestamp':row['timestamp'],'close':row['close'],'smaFast':a,'smaSlow':b,
            'rsi':rsi,'signal':signal,'matched':matched,'returnPercent':(equity-1)*100,
            'drawdownPercent':(equity/peak-1)*100,'tradeCount':trades,
            'method':{**method,'condition':condition,'threshold':threshold}})
    return result[-1:] if schema in ('research.screen.v1','research.alerts.v1') else result
