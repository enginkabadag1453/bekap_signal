"""User-supplied BEKAP AUTO rules; no simulated purchases or cost dashboard.

BEKAPTREND logic attribution: © KodlamaDestegi, MPL-2.0
https://mozilla.org/MPL/2.0/
Calculations are a local port, not certified TradingView output.
"""
import json
from pathlib import Path

VERSION = 'bekap-auto-user-source-1'
PARAMETERS = json.loads(Path(__file__).with_name('bekap_params.json').read_text(encoding='utf-8'))
DEFAULT = dict(buy=40.0, tp=65.0, overbought=70.0, drop=.025, rise=.03, horizon=10, filter='NONE')


def profile(symbol, market):
    # Exact instrument aliases only: SPY is not SPX; GLD is not GOLD.
    ticker = {'BTC': 'BTCUSD', 'BTC-USD': 'BTCUSD', 'ETH-USD': 'ETHUSD'}.get(symbol, symbol)
    if market == 'XIST' and ticker.endswith('.IS'):
        ticker = ticker[:-3]
    elif ticker.endswith('.TO'):
        ticker = ticker[:-3]
    return {'ticker': ticker, 'custom': ticker in PARAMETERS, **PARAMETERS.get(ticker, DEFAULT)}


def rma(values, length):
    out, seed, previous = [], [], None
    for value in values:
        if value is not None:
            if previous is None:
                seed.append(value)
                if len(seed) == length:
                    previous = sum(seed) / length
            else:
                previous = value / length + (1 - 1 / length) * previous
        out.append(previous)
    return out


def rsi(closes, length=14):
    deltas = [None] + [b-a for a,b in zip(closes,closes[1:])] if closes else []
    gains = rma([None if d is None else max(d,0) for d in deltas], length)
    losses = rma([None if d is None else max(-d,0) for d in deltas], length)
    return [None if g is None or l is None or g == l == 0 else
            100.0 if l == 0 else 100-100/(1+g/l) for g,l in zip(gains,losses)]


def ema(values, length=200):
    out, previous = [], None
    alpha = 2 / (length+1)
    for value in values:
        previous = value if previous is None else alpha*value+(1-alpha)*previous
        out.append(previous)
    return out


def adx(bars, length=14):
    tr, up_moves, down_moves = [None], [None], [None]
    for a,b in zip(bars,bars[1:]):
        h,l,c = float(b['high']),float(b['low']),float(a['close'])
        up,down = h-float(a['high']),float(a['low'])-l
        tr.append(max(h-l,abs(h-c),abs(l-c)))
        up_moves.append(up if up>down and up>0 else 0.0)
        down_moves.append(down if down>up and down>0 else 0.0)
    dx, last_plus, last_minus = [], None, None
    for t,u,d in zip(rma(tr,length),rma(up_moves,length),rma(down_moves,length)):
        if t is not None and t != 0:
            last_plus,last_minus = 100*u/t,100*d/t
        if last_plus is None:
            dx.append(None)
        else:
            total = last_plus+last_minus
            dx.append(abs(last_plus-last_minus)/(total if total else 1))
    return [None if d is None else 100*d for d in rma(dx,length)][:len(bars)]


def signal_states(closes, rsis, emas, params):
    """Preserve Pine statement order and strict comparisons; ADX is not a filter."""
    last_buy = last_tp = base = None
    passed50 = passed70 = divergence = False
    result = []
    for i,(price,r,e) in enumerate(zip(closes,rsis,emas)):
        buy = tp = False
        buy_diff = tp_diff = None
        p = rsis[i-1] if i else None
        pp = rsis[i-2] if i>1 else None
        if r is not None:
            if r>50: passed50=True
            if r>params['overbought']: passed70=True
            turn_up = p is not None and pp is not None and r>p and p<=pp
            turn_down = p is not None and pp is not None and r<p and p>=pp
            allowed = params['filter']=='NONE' or (params['filter']=='ABOVE' and price>e) or (params['filter']=='BELOW' and price<e)
            if p is not None and p<params['buy'] and turn_up and allowed:
                buy = last_buy is None or passed50 or price<last_buy*(1-params['drop'])
            if buy:
                buy_diff = (price-last_tp)/last_tp*100 if last_tp is not None else None
                last_buy,passed50,divergence,base = price,False,False,None
            crossunder = p is not None and r<params['tp'] and p>=params['tp']
            if passed70 and crossunder:
                tp,passed70,divergence,base = True,False,True,price
            elif not passed70 and turn_down and divergence and price>base*(1+params['rise']):
                tp,base = True,price
            if tp:
                tp_diff = (price-last_buy)/last_buy*100 if last_buy is not None else None
                last_tp = price
        result.append(dict(buy=buy,tp=tp,last_buy=last_buy,last_tp=last_tp,buy_diff=buy_diff,tp_diff=tp_diff))
    return result


def calculate(bars, symbol, market):
    params = profile(symbol,market)
    closes = [float(b['close']) for b in bars]
    rsis,emas,adxs = rsi(closes),ema(closes),adx(bars)
    states = signal_states(closes,rsis,emas,params)
    rows=[]
    for i,(b,r,e,a,s) in enumerate(zip(bars,rsis,emas,adxs,states)):
        cross_up = cross_down = False
        if i and None not in (r,a,rsis[i-1],adxs[i-1]):
            cross_up = r>a and rsis[i-1]<=adxs[i-1]
            cross_down = r<a and rsis[i-1]>=adxs[i-1]
        rows.append({'date':b['date'],'rsi':r,'ema200':e,'adx':a,
                     'cross_up':cross_up,'cross_down':cross_down,**s})
    return {'version':VERSION,'profile':params,'basis':'Sağlayıcının OHLC kapanışı; adjusted_close kullanılmaz.',
            'history_start':bars[0]['date'] if bars else None,'bar_count':len(bars),
            'equivalence':'TradingView ile mum bazında dış karşılaştırma bekliyor.',
            'rows':rows}
