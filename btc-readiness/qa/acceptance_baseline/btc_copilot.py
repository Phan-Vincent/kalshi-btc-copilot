#!/usr/bin/env python3
"""Read-only BTC 15M research monitor. All external HTTP requests are GET.

No order functions. Probabilities are uncalibrated model estimates, not established edge.
"""
import argparse
import copy
from btc_copilot_research import load_settings, version, previews, blockers, Audit, SOURCE_TEXT
from btc_copilot_evidence import Study, candidates, report as evidence_report
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import signal
import statistics
import tempfile
import threading
import time
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from kalshi_readonly import KalshiReadOnly, ReadError, NoRedirect, dump, summarize_book

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "runtime"
from study_policy import launch_guard
SERIES = "KXBTC15M"
PACIFIC = ZoneInfo("America/Los_Angeles")
D = Decimal


def dt(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ReadError('Timestamp timezone unavailable')
    return result


def stamp(epoch=None):
    d = datetime.fromtimestamp(epoch or time.time(), timezone.utc)
    return {"utc": d.isoformat(), "pacific": d.astimezone(PACIFIC).isoformat()}


def atomic(path, value):
    path = Path(path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise ReadError('Linked output destination rejected')
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(value if isinstance(value, str) else dump(value) + "\n")
            f.flush();os.fsync(f.fileno())
        # replace never follows a destination link installed after the check.
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def append(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(value, default=str, ensure_ascii=False) + "\n")


def spot_get():
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    req = Request(url, method="GET", headers={"User-Agent": "BTCReadOnlyCopilot/1.0", "Cache-Control": "no-cache"})
    with build_opener(NoRedirect()).open(req, timeout=10) as r:
        raw = r.read().decode()
        return {"data": json.loads(raw, parse_float=D), "raw_json": raw,
                "retrieved_at": stamp(), "source": "Coinbase Exchange BTC-USD"}


def ticks_from_response(envelope):
    upstream = envelope["data"]["data"]
    if upstream.get('error'):
        raise ReadError('BRTI upstream reports unavailable data')
    payload = upstream["payload"]
    if not isinstance(payload, list):
        raise ReadError("Unexpected BRTI payload")
    rows = {}
    for item in payload:
        # Invalid/amended source data must not silently become a trade signal.
        if item.get("amendTime") or item.get("repeatOfPreviousValue"):
            raise ReadError("BRTI amendment or repeated-value flag")
        milliseconds = item['time']
        if type(milliseconds) is not int or milliseconds <= 0 or milliseconds % 1000:
            raise ReadError('Expected positive per-second BRTI timestamps')
        epoch = milliseconds / 1000
        price = D(item["value"])
        if price <= 0 or not price.is_finite():
            raise ReadError("Invalid BRTI value")
        if epoch in rows:raise ReadError('Duplicate BRTI observation timestamp')
        rows[epoch] = price
    result = sorted(rows.items())
    if len(result) < 3000 or result[-1][0]-result[0][0] < 3599:
        raise ReadError("Insufficient one-hour BRTI coverage")
    return result


def minute_bars(ticks):
    groups = {}
    for ts, price in ticks:
        groups.setdefault(int(ts // 60) * 60, []).append(price)
    latest = ticks[-1][0]
    return [{"time": k, "open": v[0], "high": max(v), "low": min(v), "close": v[-1]}
            for k, v in sorted(groups.items()) if len(v) >= 55 and k + 60 <= latest]


def nearest_price(ticks, seconds):
    target = ticks[-1][0] - seconds
    ts, value = min(ticks, key=lambda x: abs(x[0]-target))
    if abs(ts-target) > 1:
        raise ReadError('Requested lookback unavailable')
    return value, ticks[-1][0]-ts


def structure(ticks):
    price = ticks[-1][1]
    changes = {}
    for seconds, label in ((60,"1m"),(180,"3m"),(300,"5m"),(900,"15m"),(3600,"1h")):
        old, coverage = nearest_price(ticks, seconds)
        changes[label] = {"dollars": price-old, "bps": (price/old-1)*10000,
                          "coverage_seconds": coverage}
    bars = minute_bars(ticks)
    if len(bars) < 50:
        raise ReadError("Insufficient completed minute bars")
    increments = [float(b["close"]-a["close"]) for a,b in zip(bars,bars[1:])]
    sec_increments = [float(b[1]-a[1]) for a,b in zip(ticks[-900:],ticks[-899:])]
    sigma = max(statistics.stdev(increments)/math.sqrt(60),
                statistics.stdev(increments[-15:])/math.sqrt(60),
                statistics.stdev(sec_increments), 0.10)
    hour_z = float(changes["1h"]["dollars"]) / (sigma*math.sqrt(changes["1h"]["coverage_seconds"]))
    bias = "UP" if hour_z > 0.65 else "DOWN" if hour_z < -0.65 else "NEUTRAL"
    pivot_highs, pivot_lows = [], []
    for i in range(2,len(bars)-2):
        window = bars[i-2:i+3]
        if bars[i]["high"] > max(b["high"] for b in window if b is not bars[i]):
            pivot_highs.append(bars[i])
        if bars[i]["low"] < min(b["low"] for b in window if b is not bars[i]):
            pivot_lows.append(bars[i])
    high_pattern = "higher highs" if len(pivot_highs)>1 and pivot_highs[-1]["high"]>pivot_highs[-2]["high"] else "lower highs" if len(pivot_highs)>1 else "unconfirmed highs"
    low_pattern = "higher lows" if len(pivot_lows)>1 and pivot_lows[-1]["low"]>pivot_lows[-2]["low"] else "lower lows" if len(pivot_lows)>1 else "unconfirmed lows"
    resistance = pivot_highs[-1]["high"] if pivot_highs else max(b["high"] for b in bars[-20:-3])
    support = pivot_lows[-1]["low"] if pivot_lows else min(b["low"] for b in bars[-20:-3])
    recent_high = max(b["high"] for b in bars[-3:])
    recent_low = min(b["low"] for b in bars[-3:])
    bounce = recent_high-min(b["low"] for b in bars[-10:-3])
    pullback = max(b["high"] for b in bars[-10:-3])-recent_low
    one, three = changes["1m"]["dollars"], changes["3m"]["dollars"]
    min_move = D(str(sigma*math.sqrt(60)*0.3))
    bearish = bias=="DOWN" and one<0 and three<0 and bounce>min_move and price<resistance and (high_pattern=="lower highs" or recent_high>=resistance)
    bullish = bias=="UP" and one>0 and three>0 and pullback>min_move and price>support and (low_pattern=="higher lows" or recent_low<=support)
    setup = "bearish bounce failed / downside resumed" if bearish else "bullish pullback reclaimed / upside resumed" if bullish else "no confirmed trend-failure setup"
    last = bars[-1]
    bar_range = last["high"]-last["low"]
    upper_wick = (last["high"]-max(last["open"],last["close"]))/bar_range if bar_range else D(0)
    lower_wick = (min(last["open"],last["close"])-last["low"])/bar_range if bar_range else D(0)
    velocity_now = float(price-nearest_price(ticks,30)[0])/30
    velocity_prior = float(nearest_price(ticks,30)[0]-nearest_price(ticks,60)[0])/30
    return {"changes": changes,"sigma_dollars_sqrt_second":sigma,"hour_trend_z":hour_z,
            "bias":bias,"structure":high_pattern+" / "+low_pattern,
            "resistance":resistance,"support":support,"local_high_5m":max(b["high"] for b in bars[-5:]),
            "local_low_5m":min(b["low"] for b in bars[-5:]),"setup":setup,
            "setup_side":"DOWN" if bearish else "UP" if bullish else None,
            "last_completed_bar":last,"upper_wick_fraction":upper_wick,"lower_wick_fraction":lower_wick,
            "velocity_dollars_per_second":velocity_now,"acceleration_dollars_per_second2":(velocity_now-velocity_prior)/30,
            "failed_breakout":recent_high>=resistance and price<resistance,
            "failed_breakdown":recent_low<=support and price>support,
            "bars":bars}


def window_samples(ticks, boundary, inclusive):
    if inclusive:
        return [p for ts,p in ticks if boundary-60 < ts <= boundary]
    return [p for ts,p in ticks if boundary-60 <= ts < boundary]


def probability(ticks, strike, close, st, uncertainty_pp=7):
    """Gaussian dollar diffusion of the final 60 one-second prices, with sensitivities.

    Covariance of future observations is sigma^2*min(dt_i,dt_j). Known
    observations keep their actual weights. Both documented boundary conventions
    are evaluated. Sensitivities are not a calibrated confidence interval.
    """
    last_ts, price = ticks[-1]
    sigma = st["sigma_dollars_sqrt_second"]
    raw_drift = float(st["changes"]["5m"]["dollars"])/300 * 0.10
    drift = max(-sigma/math.sqrt(60)*0.3,min(sigma/math.sqrt(60)*0.3,raw_drift))
    results = []
    central = []
    by_second = {int(ts):float(p) for ts,p in ticks}
    for inclusive in (False,True):
        points = list(range(int(close)-59,int(close)+1)) if inclusive else list(range(int(close)-60,int(close)))
        known = [by_second[t] for t in points if t <= last_ts and t in by_second]
        past_expected = sum(t <= last_ts for t in points)
        if past_expected != len(known):
            raise ReadError("Missing BRTI observations in settlement window")
        future = [t-last_ts for t in points if t>last_ts]
        cov = sum(min(a,b) for a in future for b in future)/3600
        for vol_mult in (0.75,1.0,1.5):
            for slope in (0.0,drift,-abs(drift),abs(drift)):
                mean = (sum(known)+len(future)*float(price)+slope*sum(future))/60
                sd = sigma*vol_mult*math.sqrt(max(cov,0))
                threshold = float(strike)-0.005  # cent-rounded equality resolves UP
                p = 0.5*(1+math.erf((mean-threshold)/(max(sd,0.001)*math.sqrt(2))))
                p = max(0.005,min(0.995,p))
                results.append(p)
                if vol_mult==1 and slope==drift:
                    central.append(p)
    estimate = sum(central)/len(central)
    return {"p_up":estimate,"p_down":1-estimate,
            "up_sensitivity_low":max(0,min(results)-uncertainty_pp/100),"up_sensitivity_high":min(1,max(results)+uncertainty_pp/100),
            "calibrated":False,"uncertainty_reserve_pp":uncertainty_pp,
            "model":"60-second settlement-average Gaussian diffusion; shrunk 5m drift; 0.75–1.5x volatility and zero/opposed drift sensitivities",
            "drift_dollars_per_second":drift}


def taker_fee(price, multiplier):
    # Conservative whole-cent ceiling for one contract covers both direct-member
    # centicent settlement and non-direct rounding. Not an account-specific quote.
    price,multiplier=D(str(price)),D(str(multiplier))
    if not price.is_finite() or not 0<=price<=1 or not multiplier.is_finite() or multiplier<0:
        raise ReadError('Invalid fee price or multiplier')
    return (D("0.07")*multiplier*price*(1-price)).quantize(D("0.01"),rounding=ROUND_CEILING)


def grid_prices(market):
    found = set()
    iterations = 0
    for band in market.get("price_ranges",[]):
        start,end,step = map(D,(band["start"],band["end"],band["step"]))
        if not all(p.is_finite() for p in (start,end,step)) or not 0<=start<=end<=1 or not 0<step<=1:
            raise ReadError("Invalid price grid")
        if (end-start)/step > 20000:
            raise ReadError('Price grid exceeds bounded resolution')
        p=start
        while p<=end:
            iterations+=1
            if iterations>20000:raise ReadError('Price grid iteration limit exceeded')
            if 0<p<1:found.add(p)
            p+=step
    if not found:
        raise ReadError("Missing price grid")
    return sorted(found)


def profit_target(market, entry, multiplier, reserve=D('.03')):
    entry,reserve=D(str(entry)),D(str(reserve))
    if not reserve.is_finite() or reserve<0:raise ReadError('Invalid profit reserve')
    entry_fee=taker_fee(entry,multiplier)
    return next((g for g in grid_prices(market) if g-entry-entry_fee-taker_fee(g,multiplier)>=reserve),None)


def validated_trades(rows, ticker, now):
    if not isinstance(rows,list):raise ReadError('Invalid trades schema')
    seen=set();result=[]
    for row in rows:
        identity=row.get('trade_id')
        quantity=D(str(row.get('count_fp')))
        epoch=dt(row['created_time']).timestamp()
        if not isinstance(identity,str) or not identity or identity in seen or row.get('ticker')!=ticker:
            raise ReadError('Trade identity missing, duplicated or mismatched')
        if not quantity.is_finite() or quantity<=0 or not now-180<=epoch<=now:
            raise ReadError('Invalid trade quantity or timestamp')
        for field in ('yes_price_dollars','no_price_dollars'):
            price=D(str(row[field]))
            if not price.is_finite() or not 0<=price<=1:raise ReadError('Invalid trade price')
        if D(str(row['yes_price_dollars']))+D(str(row['no_price_dollars']))!=1:
            raise ReadError('Trade binary prices inconsistent')
        seen.add(identity);result.append(row)
    return sorted(result,key=lambda x:dt(x['created_time']).timestamp())


def validated_candles(rows, now):
    if not isinstance(rows,list):raise ReadError('Invalid candle schema')
    result=[];seen=set();last_end=None
    for row in rows:
        end=row['end_period_ts']
        if type(end) is not int or end%60 or end in seen or not now-960<=end<=(int(now)//60+1)*60:
            raise ReadError('Candle ordering, timeframe, closure or timestamp invalid')
        if last_end is not None and end<=last_end:raise ReadError('Candle timestamps unordered')
        last_end=end
        for field in ('volume_fp','open_interest_fp'):
            number=D(str(row[field]))
            if not number.is_finite() or number<0:raise ReadError('Invalid candle volume or interest')
        for field in ('price','yes_bid','yes_ask'):
            values=[row[field][key+'_dollars'] for key in ('open','high','low','close')]
            if all(v is None for v in values):continue # Explicit no-trade candle, not invented prices.
            if any(v is None for v in values):raise ReadError('Incomplete candle OHLC')
            o,h,l,c=map(D,values)
            if not all(v.is_finite() and 0<=v<=1 for v in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:
                raise ReadError('Invalid candle OHLC')
        seen.add(end)
        if end<=now:result.append(row) # A current forming candle is never published as closed.
    return result


def price_ceiling(market, probability_floor, multiplier, reserve, slippage=D("0.005")):
    prices = [p for p in grid_prices(market) if p+taker_fee(p,multiplier)+slippage+reserve <= D(str(probability_floor))]
    return max(prices) if prices else None


def verified_market(m, now):
    return (m.get("status")=="active" and m.get("market_type")=="binary" and
            m.get("event_ticker","").startswith(SERIES+"-") and
            m.get("strike_type")=="greater_or_equal" and D(m.get("notional_value_dollars","0"))==1 and
            dt(m["open_time"]).timestamp()<=now<dt(m["close_time"]).timestamp() and
            abs((dt(m["close_time"])-dt(m["open_time"])).total_seconds()-900)<1 and
            "sixty seconds" in m.get("rules_primary","") and "BRTI" in m.get("rules_primary","") and
            "at least" in m.get("rules_primary","") and D(str(m.get("floor_strike",0)))>0)


def fill_ledger(fills, ticker):
    """Reconstruct directional inventory without interpreting legacy action/side.

    A NO-direction trade closes YES inventory at 1 minus its NO leg price and
    vice versa. Only complete, ticker-filtered histories can verify basis.
    """
    quantity=D(0);basis=None;history=[];seen=set()
    for f in sorted(fills,key=lambda x:(x.get('created_time',''),x.get('fill_id',''))):
        if f.get('ticker',f.get('market_ticker'))!=ticker:continue
        if not f.get('fill_id'):raise ReadError('Fill identifier missing; basis unavailable')
        if f.get('fill_id') in seen:continue
        seen.add(f.get('fill_id'))
        side=f.get('outcome_side')
        if side not in ('yes','no'):raise ReadError('Fill direction cannot be verified')
        q=D(f['count_fp']);price=D(f['yes_price_dollars'] if side=='yes' else f['no_price_dollars'])
        if not q.is_finite() or q<=0 or not price.is_finite() or not 0<=price<=1:raise ReadError('Invalid fill price or quantity')
        signed=q if side=='yes' else -q
        before=quantity
        if not quantity or quantity*signed>0:
            basis=((abs(quantity)*basis if basis is not None else D(0))+q*price)/(abs(quantity)+q)
            action='ENTRY' if not quantity else 'ADD'
        else:
            action='EXIT' if abs(signed)==abs(quantity) else 'REDUCE' if abs(signed)<abs(quantity) else 'EXIT_AND_REVERSE'
            if abs(signed)>abs(quantity):basis=price
            elif abs(signed)==abs(quantity):basis=None
        quantity+=signed
        history.append({'at':stamp(dt(f['created_time']).timestamp()),'outcome_side':side,
                        'quantity_fp':q,'outcome_price':price,'inventory_before':before,'inventory_after':quantity,
                        'observed_action':action,'fill_id':f.get('fill_id')})
    return {'quantity':quantity,'average_entry_price':basis,'actions':history,
            'entry_price_excludes_fees':True}


class Copilot:
    def __init__(self, positions=False):
        launch_guard(ROOT,time.time())
        self.client=KalshiReadOnly(config=ROOT.parent/'kalshi_readonly_config.json')
        self.positions=positions
        self.state_path=WORK/"btc_copilot_state.json"
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else {"contracts":{},"previous":None}
        self.raw={}

    def collect(self, replay_health=None):
        if replay_health is None:launch_guard(ROOT,time.time())
        try:self.settings=load_settings(ROOT/'btc_copilot_settings.json')
        except (ValueError,KeyError,OSError) as error:raise ReadError('Invalid copilot settings; guidance unavailable') from error
        if replay_health is None:self.settings['fee_verified_at']=json.loads((ROOT/'activation_approval.json').read_text())['fee_verified_at_utc']
        settings=self.settings
        slip=D(str(settings['slippage_reserve_cents']))/100
        minimum_edge=D(str(settings['minimum_conservative_net_edge_pp']))
        self.prior=copy.deepcopy(self.state.get('previous'))
        now=replay_health.get('collection_epoch',time.time()) if replay_health is not None else time.time()
        collection_epoch=now
        discovery=self.client.pages('/markets',{'series_ticker':SERIES,'status':'open','limit':100},max_pages=3)
        if not discovery['complete']:
            raise ReadError('Active contract discovery was truncated')
        eligible=[m for p in discovery['pages'] for m in p['data'].get('markets',[]) if verified_market(m,now)]
        if len(eligible)!=1:
            raise ReadError('Cannot verify exactly one active BTC 15-minute contract')
        candidate=eligible[0];t=candidate['ticker'];e=candidate['event_ticker']
        jobs={
            'market':lambda:self.client.market(t),
            'event':lambda:self.client.event(e),
            'series':lambda:self.client.get('/series/'+SERIES),
            'book':lambda:self.client.orderbook(t),
            'benchmark':lambda:self.client.get('/cfbenchmarks/values',{'id':'BRTI'}),
            'trades':lambda:self.client.get('/markets/trades',{'ticker':t,'min_ts':int(now)-180,'limit':1000}),
            'candles':lambda:self.client.get('/series/'+SERIES+'/markets/'+t+'/candlesticks',{'start_ts':int(now)-900,'end_ts':int(now),'period_interval':1}),
            'spot':spot_get,
            'exchange':lambda:self.client.get('/exchange/status'),
        }
        if self.positions:
            jobs['position']=lambda:self.client.get('/portfolio/positions',{'ticker':t,'count_filter':'position','limit':100})
            jobs['fills']=lambda:self.client.pages('/portfolio/fills',{'ticker':t,'limit':1000},max_pages=3)
        errors={};raw={};timing={}
        def measured(name,fn):
            start=time.time();mono=time.monotonic()
            try:return fn()
            finally:timing[name]={'started_at_epoch':start,'finished_at_epoch':time.time(),'latency_seconds':time.monotonic()-mono}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures={k:pool.submit(measured,k,f) for k,f in jobs.items()}
            for k,f in futures.items():
                try:raw[k]=f.result()
                except Exception:errors[k]='GET failed or response unavailable'
        self.raw=raw
        essential=('market','event','series','book','benchmark','trades','candles','spot','exchange')
        if any(k not in raw for k in essential):
            raise ReadError('Required data unavailable: '+', '.join(k for k in essential if k not in raw))
        for key in essential:
            envelope=raw[key];body=envelope.get('data')
            if envelope.get('error') or (isinstance(body,dict) and (body.get('error') or (isinstance(body.get('data'),dict) and body['data'].get('error')))):
                raise ReadError(key+' returned an upstream error')
        now=replay_health.get('analysis_epoch',time.time()) if replay_health is not None else time.time()
        m=raw['market']['data']['market']
        if m.get('ticker')!=t or m.get('event_ticker')!=e:raise ReadError('Market identity changed during collection')
        if raw['event']['data']['event'].get('event_ticker')!=e:raise ReadError('Event identity mismatch')
        if not verified_market(m,now):
            raise ReadError('Contract rolled or became inactive during retrieval')
        if raw['event']['data']['event'].get('series_ticker')!=SERIES:
            raise ReadError('Event series verification failed')
        s=raw['series']['data']['series']
        if s.get('ticker')!=SERIES:raise ReadError('Series identity mismatch')
        if s.get('frequency')!='fifteen_min' or s.get('fee_type')!='quadratic':
            raise ReadError('Unexpected series frequency or fee rules')
        ticks=ticks_from_response(raw['benchmark']);st=structure(ticks)
        strike=D(str(m['floor_strike']));close=dt(m['close_time']).timestamp()
        flags=['Uncalibrated probability model; edge is estimated, not proven']
        veto=[]
        raw_age=now-ticks[-1][0]
        if raw_age>10 or raw_age < 0:veto.append('BRTI source timestamp stale or in future')
        gaps=[b[0]-a[0] for a,b in zip(ticks,ticks[1:])]
        if max(gaps)>3:veto.append('BRTI history has missing ticks')
        observed_at={k:stamp(dt(v['retrieved_at']).timestamp()) for k,v in raw.items() if isinstance(v.get('retrieved_at'),str)}
        for k,v in raw.items():
            if isinstance(v.get('request_started_at'),str) and now-dt(v['request_started_at']).timestamp()>15:
                veto.append(k+' request exceeded freshness budget')
        spot=raw['spot']['data'];spot_age=now-dt(spot['time']).timestamp()
        spot_price=D(str(spot['price']))
        if not spot_price.is_finite() or spot_price<=0:raise ReadError('Invalid Coinbase spot price')
        if spot_age>10 or spot_age < 0:veto.append('Coinbase source timestamp stale or in future')
        basis=D(spot['price'])-ticks[-1][1]
        if abs(basis/ticks[-1][1])*10000>5:veto.append('Coinbase/BRTI divergence exceeds 5 bps')
        ex=raw['exchange']['data']
        if not ex.get('exchange_active') or not ex.get('trading_active'):veto.append('Exchange paused')
        shards=ex.get('exchange_index_statuses',[])
        shard=next((x for x in shards if x.get('exchange_index')==m.get('exchange_index')),None)
        if shard and not shard.get('trading_active'):veto.append('Contract exchange shard paused')
        if replay_health is not None:timing=replay_health['requests']
        finishes=[timing[k]['finished_at_epoch'] for k in essential]
        skew=max(finishes)-min(finishes)
        if skew>float(settings['maximum_snapshot_skew_seconds']):veto.append('Source snapshot skew exceeds freshness budget')
        if any(timing[k]['latency_seconds']>15 for k in essential):veto.append('Source retrieval exceeded freshness budget')
        data_faults=list(veto)
        remaining=close-now
        if remaining<float(settings['final_entry_cutoff_seconds']):veto.append('Under configured entry cutoff seconds remaining; settlement-window entry veto')
        fee_settings=settings
        if D(fee_settings['taker_coefficient'])!=D('0.07'):
            veto.append('Fee coefficient differs from implemented policy; reverify implementation')
        fee_age=now-dt(fee_settings['fee_verified_at']).timestamp()
        if fee_age< -300 or fee_age>86400:
            veto.append('Fee schedule verification over 24h old; refresh official fee policy')
        anchor_matches=[]
        for inclusive in (False,True):
            ps=window_samples(ticks,dt(m['open_time']).timestamp(),inclusive)
            if len(ps)==60 and abs(sum(ps)/60-strike)<D('0.0051'):anchor_matches.append('inclusive' if inclusive else 'exclusive')
        if not anchor_matches:
            veto.append('Target could not be reconciled to opening BRTI average')
            data_faults.append(veto[-1])
        for ladder in raw['book']['data'].get('orderbook_fp',{}).values():
            if not isinstance(ladder,list):raise ReadError('Unexpected order-book structure')
            for price,quantity in ladder:
                price,quantity=D(price),D(quantity)
                if not price.is_finite() or not quantity.is_finite() or not 0<=price<=1 or quantity<0:raise ReadError('Invalid order-book price or quantity')
        book=summarize_book(raw['book']['data']);model=probability(ticks,strike,close,st,float(settings['model_uncertainty_reserve_pp']))
        if any(book[x+'_spread_dollars'] is not None and book[x+'_spread_dollars']<0 for x in ('yes','no')):
            data_faults.append('Crossed order book; guidance unavailable');veto.append(data_faults[-1])
        midpoint=book['yes_midpoint_estimate_dollars']
        up_depth=book['yes_bid_depth_within_5c_fp'];down_depth=book['no_bid_depth_within_5c_fp']
        imbalance=float((up_depth-down_depth)/(up_depth+down_depth)) if up_depth is not None and down_depth is not None and up_depth+down_depth>0 else None
        trades=validated_trades(raw['trades']['data']['trades'],t,now)
        candles=validated_candles(raw['candles']['data']['candlesticks'],now)
        trade_flow={'sample_count':len(trades),'truncated':bool(raw['trades']['data'].get('cursor')),
                    'volume_fp':sum((D(str(t.get('count_fp','0'))) for t in trades),D(0)),
                    'direction':'unknown; no unverified aggressor inference'}
        if trade_flow['truncated']:
            data_faults.append('Recent trade window incomplete; cursor remains')
            veto.append(data_faults[-1])
        known_sides=[t for t in trades if t.get('taker_outcome_side') in ('yes','no')]
        if known_sides:
            yes=sum((D(str(t.get('count_fp','0'))) for t in known_sides if t['taker_outcome_side']=='yes'),D(0))
            no=sum((D(str(t.get('count_fp','0'))) for t in known_sides if t['taker_outcome_side']=='no'),D(0))
            trade_flow.update({'yes_taker_count_fp':yes,'no_taker_count_fp':no,'direction':'UP' if yes>no else 'DOWN' if no>yes else 'BALANCED'})
        if trades:
            trade_flow['first_observed_at']=stamp(dt(trades[0]['created_time']).timestamp())
            trade_flow['last_observed_at']=stamp(dt(trades[-1]['created_time']).timestamp())
        prev=self.state.get('previous');same=prev and prev.get('ticker')==m['ticker'] and 0<=now-prev.get('epoch',0)<60
        confirmed=(same and prev.get('confirmation_valid') is True and not prev.get('data_quality_faults') and not prev.get('operational_vetoes') and
                   prev.get('source_epoch',math.inf)<ticks[-1][0] and now-prev.get('epoch',now)>=5)
        changes=['First observation for this contract; entry requires a later confirming snapshot']
        if same:
            changes=[f"BRTI moved ${float(ticks[-1][1])-prev['btc']:+.2f}; strike distance changed by the same amount",
                     f"UP ask changed {(float(book['yes_ask_dollars'])-prev['up_ask'])*100:+.2f}c" if book['yes_ask_dollars'] is not None and prev.get('up_ask') is not None else 'UP ask became unavailable',
                     f"P(UP) changed {(model['p_up']-prev['p_up'])*100:+.1f}pp",
                     f"Setup: {prev['setup']} → {st['setup']}",
                     f"5c book imbalance changed {imbalance-prev['imbalance']:+.3f}" if imbalance is not None and prev.get('imbalance') is not None else 'Book imbalance unavailable']
            if book['no_ask_dollars'] is not None and prev.get('down_ask') is not None:
                changes.append(f"DOWN ask changed {(float(book['no_ask_dollars'])-prev['down_ask'])*100:+.2f}c")
            delta_velocity=st['velocity_dollars_per_second']-prev.get('velocity',st['velocity_dollars_per_second'])
            changes.append(f"30s BTC velocity changed {delta_velocity:+.3f} dollars/s; {'upward bounce gaining velocity' if delta_velocity>0 else 'upward bounce losing velocity' if delta_velocity<0 else 'velocity unchanged'}")
            if book['yes_ask_dollars'] is not None and prev.get('up_ask') is not None:
                lag=(model['p_up']-prev['p_up'])-(float(book['yes_ask_dollars'])-prev['up_ask'])
                changes.append(f"Model probability change minus UP ask change: {lag*100:+.1f}pp (estimated repricing gap, not established inefficiency)")
        operational_vetoes=list(veto)
        side=st['setup_side'];mult=D(str(s['fee_multiplier']))
        if not mult.is_finite() or mult<0:raise ReadError('Invalid series fee multiplier')
        selected=None;prices={}
        for direction,prefix,p_est,p_floor in [('UP','yes',model['p_up'],model['up_sensitivity_low']),('DOWN','no',model['p_down'],1-model['up_sensitivity_high'])]:
            ask=book[prefix+'_ask_dollars'];bid=book[prefix+'_bid_dollars']
            fee=taker_fee(ask,mult) if ask is not None else None
            prices[direction]={'ask':ask,'bid':bid,'fee_per_contract':fee,
                              'gross_model_edge_pp':(p_est-float(ask))*100 if ask is not None else None,
                              'net_model_edge_pp':(p_est-float(ask+fee+slip))*100 if ask is not None else None,
                              'conservative_net_edge_pp':(p_floor-float(ask+fee+slip))*100 if ask is not None else None,
                              'maximum_acceptable_price':price_ceiling(m,p_floor,mult,minimum_edge/100,slip),
                              'edge_disappears_above':price_ceiling(m,p_floor,mult,D(0),slip)}
            ceiling=prices[direction]['maximum_acceptable_price']
            prices[direction]['estimated_net_edge_at_maximum_pp']=(p_est-float(ceiling+taker_fee(ceiling,mult)+slip))*100 if ceiling is not None else None
            prices[direction]['conservative_net_edge_at_maximum_pp']=(p_floor-float(ceiling+taker_fee(ceiling,mult)+slip))*100 if ceiling is not None else None
        if side:
            prefix='yes' if side=='UP' else 'no';p=prices[side]
            if book[prefix+'_ask_dollars'] is None or book[prefix+'_bid_dollars'] is None:veto.append('Missing executable bid/ask')
            elif book[prefix+'_spread_dollars']<0 or book[prefix+'_spread_dollars']>D('0.04'):veto.append('Crossed book or spread over 4c')
            if book[prefix+'_ask_size_fp'] is None or book[prefix+'_ask_size_fp']<1:veto.append('Less than one contract at executable ask')
            if not confirmed or prev.get('setup_side')!=side:veto.append('Setup not confirmed across fresh healthy snapshots')
            if p['conservative_net_edge_pp'] is None or p['conservative_net_edge_pp']<float(minimum_edge):veto.append('Insufficient edge after fees, slippage and model uncertainty')
            if imbalance is not None and ((side=='UP' and imbalance<-.35) or (side=='DOWN' and imbalance>.35)):veto.append('Displayed depth materially conflicts with direction')
            if trade_flow['direction'] in ('UP','DOWN') and trade_flow['direction']!=side:veto.append('Recent taker flow conflicts with direction')
            if abs(float(ticks[-1][1]-strike))<st['sigma_dollars_sqrt_second']*math.sqrt(60)*0.25:veto.append('BTC chopping too close to strike')
            if not veto:selected=side
        else:veto.append('No confirmed one-hour-trend / countertrend-failure setup')
        position={'status':'unknown','action':'not applicable; if holding, re-evaluate on fresh data; no ADD'}
        if 'position' in raw:
            rows=raw['position']['data'].get('market_positions',[])
            if raw['position']['data'].get('cursor'):position={'status':'unknown','action':'unverified position pagination; no ADD'}
            else:
                qty=sum((D(r.get('position_fp','0')) for r in rows if r.get('ticker')==m['ticker']),D(0))
                held='UP' if qty>0 else 'DOWN' if qty<0 else None
                position={'status':'flat' if qty==0 else 'held','side':held,'quantity_fp':abs(qty),'action':'not applicable — flat'}
                if 'fills' in raw and raw['fills']['complete']:
                    ledger=fill_ledger([f for p in raw['fills']['pages'] for f in p['data'].get('fills',[])],m['ticker'])
                    if abs(ledger['quantity']-qty)<D('0.005'):
                        position['average_entry_price']=ledger['average_entry_price']
                        position['fills_verified']=True
                        position['fill_actions']=ledger['actions']
                    else:
                        position['fills_verified']=False
                        flags.append('Position/fill inventory disagrees; basis unavailable')
                if held:
                    invalid=(held=='UP' and st['last_completed_bar']['close']<st['support']) or (held=='DOWN' and st['last_completed_bar']['close']>st['resistance'])
                    pos_edge=prices[held]['conservative_net_edge_pp']
                    held_bid=prices[held]['bid']
                    held_floor=model['up_sensitivity_low'] if held=='UP' else 1-model['up_sensitivity_high']
                    liquidation_edge=(held_floor-float(held_bid-taker_fee(held_bid,mult)))*100 if held_bid is not None else None
                    position['hold_vs_liquidation_edge_pp']=liquidation_edge
                    hard_data_fault=bool(data_faults)
                    position['action']='GUIDANCE UNAVAILABLE' if hard_data_fault or liquidation_edge is None else 'EXIT' if invalid else 'REDUCE' if liquidation_edge<=0 else 'HOLD'
                    position['guidance_valid']=not hard_data_fault and liquidation_edge is not None
                    # No averaging down. ADD needs held-position evidence in an
                    # earlier snapshot, new completed structure and stronger edge.
                    if (not hard_data_fault and liquidation_edge is not None and same and position.get('fills_verified') and prev.get('position_side')==held and selected==held and
                        st['last_completed_bar']['time']>prev.get('bar_time',0) and
                        ((held=='UP' and float(ticks[-1][1])>prev.get('local_high',math.inf) and st['structure'].endswith('higher lows')) or
                         (held=='DOWN' and float(ticks[-1][1])<prev.get('local_low',-math.inf) and st['structure'].startswith('lower highs'))) and
                        pos_edge>prev.get('held_edge',0)+3):position['action']='ADD'
        direction=selected or side or position.get('side')
        invalidation=(f"BRTI minute closes below reclaimed support ${st['support']:,.2f}" if direction=='UP' else
                      f"BRTI minute closes above failed-reclaim resistance ${st['resistance']:,.2f}" if direction=='DOWN' else
                      f"Need a failed reclaim near ${st['resistance']:,.2f} or a successful support reclaim near ${st['support']:,.2f}; no active thesis")
        entry=prices[selected] if selected else None
        profit=None
        if selected and entry['ask'] is not None:
            profit=profit_target(m,entry['ask'],mult)
        elif position.get('side') and position.get('average_entry_price') is not None:
            paid=position['average_entry_price']
            max_exit_fee=(D('0.07')*mult*D('0.25')).quantize(D('0.01'),rounding=ROUND_CEILING)
            profit=next((g for g in grid_prices(m) if g>=paid+taker_fee(paid,mult)+max_exit_fee+D('0.03')),None)
        position['original_thesis']='User entry thesis not provided; management uses current observed BRTI structure'
        if self.positions and 'position' in errors:flags.append('Account position read failed; holdings unknown')
        if self.positions and 'fills' in errors:flags.append('Fill read unavailable; actual entry basis not verified')
        if trade_flow['truncated']:flags.append('Recent trade sample truncated at 1000 records; flow is a partial window')
        flags+=veto
        snapshot={
            'timestamp':stamp(now),'epoch':now,'collection_epoch':collection_epoch,'analysis_epoch':now,'valid_until_epoch':min(now+20,close,ticks[-1][0]+20,dt(spot['time']).timestamp()+20,min(timing[k]['started_at_epoch'] for k in essential)+20),'ticker':m['ticker'],'event_ticker':m['event_ticker'],
            'question':m['title'],'strike':strike,'close_time':m['close_time'],'open_time':m['open_time'],'time_remaining_seconds':max(0,remaining),
            'btc':ticks[-1][1],'btc_source':'CF Benchmarks BRTI via authenticated Kalshi REST passthrough',
            'btc_observed_at':stamp(ticks[-1][0]),'btc_age_seconds':raw_age,'spot':D(spot['price']),
            'spot_source':'Coinbase Exchange BTC-USD (cross-check only)','spot_observed_at':stamp(dt(spot['time']).timestamp()),
            'basis_dollars':basis,'distance_dollars':ticks[-1][1]-strike,'distance_bps':(ticks[-1][1]/strike-1)*10000,
            'book':book,'model':model,'structure':st,'initial_bias':st['bias'],'decision':selected or 'NO TRADE',
            'entry':entry,'side_evaluations':prices,'position':position,'profit_taking_bid_zone':profit,
            'invalidation':invalidation,'data_quality_faults':data_faults,'entry_blockers':blockers(veto),
            'retrieval_health':{'requests':timing,'snapshot_skew_seconds':skew},
            'model_version':version(settings),'confidence':'Low','risk_flags':list(dict.fromkeys(flags)),
            'why':[f"1h BRTI move ${st['changes']['1h']['dollars']:+.2f}; trend {st['bias']} (z={st['hour_trend_z']:+.2f})",
                   f"{st['structure']}; {st['setup']}",
                   f"1m / 3m moves ${st['changes']['1m']['dollars']:+.2f} / ${st['changes']['3m']['dollars']:+.2f}",
                   f"{side or 'Both sides'}: executable ask assessed after taker fee, {settings['slippage_reserve_cents']}c slippage reserve and sensitivity haircut",
                   'Entry vetoes: '+('; '.join(veto) if veto else 'none')],
            'changes':changes,'volume_fp':m.get('volume_fp'),'open_interest_fp':m.get('open_interest_fp'),
            'last_trade_price_dollars':m.get('last_price_dollars'),'recent_trades':trade_flow,
            'kalshi_recent_candlesticks':candles[-5:],
            'observation_times':observed_at,'rules_primary':m['rules_primary'],'rules_secondary':m['rules_secondary'],
            'target_boundary_matches':anchor_matches,'settlement_rule':'Final 60 BRTI observations averaged and rounded to 2 decimals; at least target resolves UP',
            'fee_multiplier':mult,'fee_schedule_checked_on':fee_settings['fee_verified_at'],'book_imbalance_5c':imbalance,
            'chart':[{'t':ts,'p':p} for ts,p in ticks[::5]],
        }
        snapshot['operational_vetoes']=operational_vetoes
        zero_st=copy.deepcopy(st);zero_st['changes']['5m']['dollars']=D(0)
        snapshot['drift_free_model']=probability(ticks,strike,close,zero_st,float(settings['model_uncertainty_reserve_pp']))
        snapshot['shadow_strategies']=candidates(snapshot,prev,settings)
        snapshot['shadow_decision']=snapshot['decision']
        snapshot['shadow_entry']=snapshot['entry']
        snapshot['position']['shadow_action']=snapshot['position']['action']
        snapshot['position']['action']='GUIDANCE UNAVAILABLE — strategy not validated'
        snapshot['position']['guidance_valid']=False
        snapshot['decision']='NO TRADE';snapshot['entry']=None;snapshot['profit_taking_bid_zone']=None
        snapshot['invalidation']='Experimental structural level only; not an exit instruction. '+snapshot['invalidation']
        snapshot['validation']={'status':'UNVALIDATED_SHADOW_ONLY','demonstrated_edge':False,
                                'reason':'No strategy has passed a frozen prospective evaluation; no entry, hold, add or exit advice is enabled.'}
        snapshot['entry_blockers'].insert(0,{'reason':'No prospectively validated strategy','next_condition':'Complete frozen out-of-sample evaluation and independent review.'})
        snapshot['risk_flags'].append('SHADOW ONLY: modeled value is not validated trading advice')
        snapshot['why'].append(snapshot['validation']['reason'])
        snapshot['quantity_previews']=previews(raw,snapshot,settings)
        available=time.time() if replay_health is None else replay_health.get('available_epoch',snapshot['epoch'])
        if available<now or available>=snapshot['valid_until_epoch'] or close-available<float(settings['final_entry_cutoff_seconds']):
            raise ReadError('Decision completion outside valid observation window')
        snapshot['observation_monotonic']=time.monotonic()
        snapshot['epoch']=available;snapshot['timestamp']=stamp(available)
        snapshot['time_remaining_seconds']=close-available
        self.record(snapshot)
        return snapshot

    def record(self,s):
        t=s['ticker'];contracts=self.state['contracts']
        if t not in contracts:
            contracts[t]={'ticker':t,'initial_bias':s['initial_bias'],'first_observed_at':s['timestamp'],
                          'close_time':s['close_time'],'strike':str(s['strike']),'entry_signal':None,'observations':0,'journaled':False}
        r=contracts[t];r['observations']+=1;r['last_observed_at']=s['timestamp']
        if s['position'].get('fills_verified'):
            r['actual_fill_actions']=s['position']['fill_actions']
            r['last_verified_position']=s['position']
            first=next((f for f in s['position']['fill_actions'] if f['observed_action']=='ENTRY'),None)
            if first and 'actual_first_entry_price' not in r:
                r['actual_first_entry_price']=str(first['outcome_price'])
                r['actual_first_entry_side']='UP' if first['outcome_side']=='yes' else 'DOWN'
                r['actual_min_observed_bid']=None;r['actual_max_observed_bid']=None
        if r.get('actual_first_entry_side'):
            actual_bid=s['book']['yes_bid_dollars' if r['actual_first_entry_side']=='UP' else 'no_bid_dollars']
            if actual_bid is not None:
                r['actual_min_observed_bid']=str(min(D(r['actual_min_observed_bid']),actual_bid)) if r.get('actual_min_observed_bid') is not None else str(actual_bid)
                r['actual_max_observed_bid']=str(max(D(r['actual_max_observed_bid']),actual_bid)) if r.get('actual_max_observed_bid') is not None else str(actual_bid)
        if s['decision'] in ('UP','DOWN') and r['entry_signal'] is None:
            r['entry_signal']=s['decision'];r['hypothetical_entry_price']=str(s['entry']['ask']);r['signal_at']=s['timestamp']
            r['min_observed_bid']=str(s['entry']['bid']);r['max_observed_bid']=str(s['entry']['bid'])
        if r['entry_signal']:
            bid=s['book']['yes_bid_dollars' if r['entry_signal']=='UP' else 'no_bid_dollars']
            if bid is not None:
                r['min_observed_bid']=str(min(D(r['min_observed_bid']),bid));r['max_observed_bid']=str(max(D(r['max_observed_bid']),bid))
        previous=self.state.get('previous')
        self.state['previous']={'ticker':t,'epoch':s['epoch'],'btc':float(s['btc']),
            'confirmation_valid':not s['operational_vetoes'] and not s['data_quality_faults'],
            'data_quality_faults':list(s['data_quality_faults']),'operational_vetoes':list(s['operational_vetoes']),
            'source_epoch':dt(s['btc_observed_at']['utc']).timestamp(),
            'up_ask':float(s['book']['yes_ask_dollars']) if s['book']['yes_ask_dollars'] is not None else None,
            'down_ask':float(s['book']['no_ask_dollars']) if s['book']['no_ask_dollars'] is not None else None,
            'velocity':s['structure']['velocity_dollars_per_second'],
            'local_high':float(s['structure']['local_high_5m']),'local_low':float(s['structure']['local_low_5m']),
            'p_up':s['model']['p_up'],'setup':s['structure']['setup'],'setup_side':s['structure']['setup_side'],
            'imbalance':s['book_imbalance_5c'],'bar_time':s['structure']['last_completed_bar']['time'],
            'position_side':s['position'].get('side'),'held_edge':s['side_evaluations'].get(s['position'].get('side'),{}).get('conservative_net_edge_pp') or 0}
        # Study002 outcome reconciliation is separate from fresh observation collection.
        # Original study001 journals are untouched; durable study002 outcome_events are authoritative.
        self.state['contracts']={k:v for k,v in contracts.items() if dt(v['close_time']).timestamp()>s['epoch']-172800}
        atomic(self.state_path,self.state)
        compact={k:v for k,v in s.items() if k not in ('chart',)}
        compact['structure']={k:v for k,v in s['structure'].items() if k!='bars'}
        append(WORK/'btc_copilot_observations.jsonl',compact)


def report(s):
    def cents(p):return 'unavailable' if p is None else f"{D(str(p))*100:.2f}c"
    b=s['book'];mod=s['model'];entry=s['entry'];evaluations=s['side_evaluations']
    held_eval=evaluations.get(s['position'].get('side'))
    text=f"""BTC 15M COPILOT — {s['timestamp']['utc']} / {s['timestamp']['pacific']}

Contract: {s['ticker']}
Strike: ${D(str(s['strike'])):,.2f}; UP means final BRTI minute average >= strike
Time remaining: {int(s['time_remaining_seconds']//60)}m {int(s['time_remaining_seconds']%60)}s
BTC: ${D(str(s['btc'])):,.2f} — BRTI, source {s['btc_observed_at']['utc']} / {s['btc_observed_at']['pacific']}
Distance to strike: ${D(str(s['distance_dollars'])):+.2f} / {D(str(s['distance_bps'])):+.2f} bps

Kalshi:
UP bid / ask: {cents(b['yes_bid_dollars'])} / {cents(b['yes_ask_dollars'])} (ask size {b['yes_ask_size_fp']})
DOWN bid / ask: {cents(b['no_bid_dollars'])} / {cents(b['no_ask_dollars'])} (ask size {b['no_ask_size_fp']})
Spread: {cents(b['yes_spread_dollars'])}; book retrieved {s['observation_times'].get('book',{}).get('utc','unknown')}

MODEL
P(UP): {mod['p_up']*100:.1f}% / P(DOWN): {mod['p_down']*100:.1f}% — uncalibrated estimates
UP sensitivity range: {mod['up_sensitivity_low']*100:.1f}–{mod['up_sensitivity_high']*100:.1f}% (not a confidence interval)

DECISION: {s['decision']}

ENTRY:
Maximum acceptable price: {cents(entry['maximum_acceptable_price']) if entry else 'none — abstain'}
Estimated edge at maximum price: {f"{entry['estimated_net_edge_at_maximum_pp']:+.1f}pp central net / {entry['conservative_net_edge_at_maximum_pp']:+.1f}pp conservative net" if entry and entry['estimated_net_edge_at_maximum_pp'] is not None else 'not applicable — no entry'}
Estimated edge at current ask: {f"{entry['gross_model_edge_pp']:+.1f}pp gross; {entry['conservative_net_edge_pp']:+.1f}pp after conservative costs/uncertainty" if entry else f"UP {evaluations['UP']['net_model_edge_pp']:+.1f}pp / DOWN {evaluations['DOWN']['net_model_edge_pp']:+.1f}pp central-model net; entry gates still apply" if evaluations['UP']['net_model_edge_pp'] is not None and evaluations['DOWN']['net_model_edge_pp'] is not None else 'unavailable'}

WHY:
"""
    text+='\n'.join('- '+w for w in s['why'])
    text+=f"\n\nINVALIDATION: {s['invalidation']}\n\nPOSITION MANAGEMENT: {s['position']['action']}"
    text+=f"\nObserved position: {s['position'].get('side') or s['position']['status']}; original entry thesis not supplied"
    text+=f"\nFirst profit-taking zone (executable bid): {cents(s['profit_taking_bid_zone']) if s['profit_taking_bid_zone'] else 'unavailable — no verified entry basis or signal'}"
    text+=f"\nStructural exit: {s['invalidation']}"
    text+=f"\nMaximum price where current conservative entry edge disappears: {cents(entry['edge_disappears_above']) if entry else cents(held_eval['edge_disappears_above']) if held_eval else 'not applicable; per-side ceilings are in the data file'}"
    text+='\n\nCHANGED:\n'+'\n'.join('- '+w for w in s['changes'])
    text+='\n\nENTRY BLOCKERS: '+('; '.join(x['reason']+' Next: '+x['next_condition'] for x in s.get('entry_blockers',[])) or 'none')
    text+='\nMODEL VERSION: '+s.get('model_version','unknown')
    text+='\n\nCONFIDENCE: '+s['confidence']+'\nRISK FLAGS: '+'; '.join(s['risk_flags'])
    return text+'\n'


class View(BaseHTTPRequestHandler):
    def do_GET(self):
        expected={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in expected:
            self.send_error(403);return
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in expected}:
            self.send_error(403);return
        allowed={'/':ROOT/'btc_copilot.html','/latest.json':ROOT/'btc_copilot_latest.json','/latest.txt':ROOT/'btc_copilot_latest.txt'}
        path=self.path.split('?')[0]
        if path not in allowed or not allowed[path].exists():self.send_error(404);return
        data=allowed[path].read_bytes()
        self.send_response(200)
        self.send_header('Content-Type','application/json' if path.endswith('.json') else 'text/html; charset=utf-8' if path=='/' else 'text/plain; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass


def reconcile_background(study,client):
    class GuardedPublicClient:
        def market(self,ticker):
            launch_guard(ROOT,time.time())
            return client.market(ticker)
    from request_pacing import background_reads
    with background_reads():study.settle(GuardedPublicClient(),time.time())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--watch',action='store_true');p.add_argument('--interval',type=float,default=15)
    p.add_argument('--port',type=int,default=8767);p.add_argument('--positions',action='store_true')
    args=p.parse_args()
    if not math.isfinite(args.interval) or args.interval!=15:raise SystemExit('Study002 requires exactly 15 seconds')
    raise SystemExit('NOT ACTIVATED: offline remediation candidate; collector launch requires a separate reviewed release')
    launch_guard(ROOT,time.time())
    if args.port!=8767 or args.positions:raise SystemExit('Study002 uses port8767 and public research only')
    WORK.mkdir(exist_ok=True)
    lock=open(WORK/'btc_copilot.lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('A copilot monitor is already running')
    c=Copilot(args.positions)
    audit=Audit(WORK/"btc_copilot_audit.sqlite3")
    study=Study(WORK/"btc_copilot_study.sqlite3",ROOT/"btc_copilot_protocol.json",version(load_settings(ROOT/"btc_copilot_settings.json")),SOURCE_TEXT)
    if args.watch:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),View)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        atomic(WORK/'btc_copilot_runtime.json',{'pid':os.getpid(),'url':f'http://127.0.0.1:{args.port}/','interval_seconds':args.interval,'started_at':stamp(),'positions_enabled':args.positions})
        print(f'Read-only BTC copilot running at http://127.0.0.1:{args.port}/',flush=True)
    settlement_pool=ThreadPoolExecutor(max_workers=1)
    settlement_future=None
    while True:
        launch_guard(ROOT,time.time())
        start=time.monotonic()
        try:
            s=c.collect()
            audit.record(s,c.raw,c.prior,c.settings,ROOT/'btc_copilot_journal.jsonl')
            s['scorecard']=audit.scorecard()
            if time.time()>=s['valid_until_epoch']:raise ReadError('Observation expired during persistence')
            study.record(s,c.raw,c.prior,c.settings)
            s['study']=study.status(time.time())
            if not hasattr(study,'last_report') or time.time()-study.last_report>=300:
                atomic(ROOT/'btc_copilot_evidence_report.json',evidence_report(study.path,time.time()))
                study.last_report=time.time()
            atomic(ROOT/'btc_copilot_latest.json',s);atomic(ROOT/'btc_copilot_latest.txt',report(s))
            # Never retain raw private-position responses in published data.
            atomic(WORK/'btc_copilot_raw_latest.json',{k:v for k,v in c.raw.items() if k not in ('position','fills')})
            print(s['timestamp']['utc']+' '+s['ticker']+' '+s['decision'],flush=True)
        except Exception as error:
            msg=str(error) if isinstance(error,ReadError) else 'Data retrieval or model processing failed'
            try:audit.gap(time.time(),msg)
            except Exception:msg='Research storage unavailable; guidance disabled'
            failure={'timestamp':stamp(),'epoch':time.time(),'valid_until_epoch':time.time(),
                     'decision':'NO TRADE','risk_flags':[msg],'confidence':'Low','error':True}
            atomic(ROOT/'btc_copilot_latest.json',failure)
            atomic(ROOT/'btc_copilot_latest.txt','BTC 15M COPILOT — '+failure['timestamp']['utc']+'\nDECISION: NO TRADE\nRISK FLAGS: '+msg+'\n')
            print('NO TRADE — '+msg,flush=True)
        finally:
            try:
                if args.watch and (settlement_future is None or settlement_future.done()):
                    if settlement_future is not None:
                        finished=settlement_future;settlement_future=None
                        finished.result()
                    settlement_future=settlement_pool.submit(reconcile_background,study,c.client)
            except Exception:
                try:audit.gap(time.time(),'Public outcome reconciliation failed; retry next cycle')
                except Exception:pass
        if not args.watch:break
        time.sleep(max(0.1,args.interval-(time.monotonic()-start)))


if __name__=='__main__':main()
