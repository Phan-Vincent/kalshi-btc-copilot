#!/usr/bin/env python3
"""Real-time BTC technical-analysis view for the Kalshi KXBTC15M series.

Paper only: no orders, no account access, no credentials. Every external
call is an unauthenticated GET to public endpoints (exchange tickers, Coinbase
candles, Kalshi public market data). The tally remains descriptive; the single
experimental scalp trigger is unvalidated and paper-scored. The Study 005
advice gate is unaffected.
"""
import argparse
from collections import deque
from decimal import Decimal, InvalidOperation
import json
import math
import os
import queue
import statistics
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from stream_flow import OrderFlow, parse_coinbase, parse_coinbase_spot, parse_kraken, MalformedMessage
from scalp_signal import ScalpEngine

HERE = Path(__file__).resolve().parent
COINBASE = "https://api.exchange.coinbase.com/products/BTC-USD"
# The Exchange candles endpoint lags several minutes; the Advanced Trade public
# market endpoint is current and includes the forming bar.
CANDLES = "https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD/candles"
# Fetches: key -> (granularity, seconds, pages of 300 bars). Coinbase has no 10m
# granularity, so 10m is aggregated from the two pages of 5m bars.
FETCHES = {"1m": ("ONE_MINUTE", 60, 1), "5m": ("FIVE_MINUTE", 300, 2),
           "15m": ("FIFTEEN_MINUTE", 900, 1), "1h": ("ONE_HOUR", 3600, 1)}
DERIVED = {"10m": ("5m", 600)}
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
UA = {"User-Agent": "BTC-TA-Copilot/1.0 (read-only)", "Cache-Control": "no-cache"}
TIMEFRAMES = {"1m": 60, "5m": 300, "10m": 600, "15m": 900, "1h": 3600}
MAX_BARS = 300
# Seconds between candle refreshes per timeframe; the forming bar is also
# patched from the ticker on every spot poll.
CANDLE_REFRESH = {"1m": 10, "5m": 30, "15m": 60, "1h": 120}
SPOT_REFRESH = 2
KALSHI_REFRESH = 2
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
KRAKEN_WS = "wss://ws.kraken.com/v2"
LARGE_TRADE_USD = float(os.environ.get("TA_LARGE_TRADE_USD", "25000"))
STALE_AFTER = 20
VENUE_MAX_AGE = 10
VENUE_MAX_DEVIATION = 0.005
# Kalshi window freshness. `kalshi_stale` means "no successful fetch for the
# displayed window for this long"; a just-promoted window gets a short grace
# because its first metadata can only come from the 30 s listing prefetch.
KALSHI_STALE_AFTER = 10
KALSHI_PROMOTION_GRACE = 5
# Served on every response. The page needs the unpkg chart script, its inline
# stylesheet, the data-URL favicon and same-origin fetch/EventSource only.
CSP = ("default-src 'none'; script-src 'self' https://unpkg.com; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = (("Content-Security-Policy", CSP),
                    ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"))
VENUE_URLS = {
    "Kraken": "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
    "Bitstamp": "https://www.bitstamp.net/api/v2/ticker/btcusd/",
    "Gemini": "https://api.gemini.com/v1/pubticker/btcusd",
}


def get_json(url, params=None, timeout=8):
    if params:
        url += "?" + urlencode(params)
    with urlopen(Request(url, method="GET", headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite upstream number")
    return number


def optional_float(value):
    return None if value in (None, "") else finite_float(value)


def validate_market(market):
    parse_ts(market["open_time"])
    parse_ts(market["close_time"])
    for name in ("floor_strike", "yes_bid_dollars", "yes_ask_dollars",
                 "no_bid_dollars", "no_ask_dollars"):
        optional_float(market.get(name))
    return market


def venue_quote(name, data, fetched_at):
    """Return last trade and its best available age reference."""
    if name == "Coinbase":
        price, stamp = finite_float(data["price"]), parse_ts(data["time"])
    elif name == "Kraken":
        if data.get("error"):
            raise ValueError(f"Kraken errors: {data['error']}")
        price, stamp = finite_float(next(iter(data["result"].values()))["c"][0]), fetched_at
    elif name == "Bitstamp":
        price, stamp = finite_float(data["last"]), finite_float(data["timestamp"])
    elif name == "Gemini":
        # Gemini's pubticker has no quote timestamp; volume.timestamp is not
        # a last-trade timestamp. Age here means time since successful fetch.
        price, stamp = finite_float(data["last"]), fetched_at
    else:
        raise ValueError(f"unknown venue: {name}")
    if not math.isfinite(price) or price <= 0 or not math.isfinite(stamp) or stamp > fetched_at + 2:
        raise ValueError(f"invalid {name} ticker")
    return {"price": price, "at": stamp, "age_basis": "ticker" if name in ("Coinbase", "Bitstamp") else "fetch"}


def composite_quote(venues, now):
    fresh = [v["price"] for v in venues.values()
             if v and -2 <= now - v["at"] <= VENUE_MAX_AGE]
    midpoint = statistics.median(fresh) if fresh else None
    details, included = {}, []
    for name in ("Coinbase", "Kraken", "Bitstamp", "Gemini"):
        v = venues.get(name)
        delta = now - v["at"] if v else None
        age = max(0, delta) if v else None
        reason = ("unavailable" if not v else "future timestamp" if delta < -2
                  else "stale" if age > VENUE_MAX_AGE
                  else "outlier" if midpoint and abs(v["price"] - midpoint) / midpoint > VENUE_MAX_DEVIATION
                  else None)
        details[name] = {"price": v["price"] if v else None, "age": age,
                         "age_basis": v["age_basis"] if v else None,
                         "included": reason is None, "excluded_reason": reason}
        if reason is None:
            included.append(v["price"])
    return (statistics.median(included) if included else None), details


def rolling_average(samples, now):
    """Require 60 contiguous one-second samples before showing a 60s mean."""
    recent = [(at, price) for at, price in samples if now - 59 <= at <= now]
    if (len(recent) != 60 or [at for at, _ in recent] != list(range(now - 59, now + 1))
            or any(price is None for _, price in recent)):
        return None
    return statistics.fmean(price for _, price in recent)


def window_distance(strike, composite, seconds_left, sigma_1m, close_ts, samples):
    """Distance and Brownian proxy scale for the final 60-second mean."""
    source = "composite" if seconds_left > 60 else "projected 60s average"
    result = {"distance_source": source, "distance": None, "distance_sigmas": None,
              "distance_sd_usd": None, "sigma_1m_usd": None,
              "sigma_model": "brownian settlement-average proxy",
              "observed_seconds": 0, "missing_seconds": 0}
    if composite is None or strike is None:
        return result
    strike = finite_float(strike)
    if seconds_left > 60:
        reference = composite
        factor = (seconds_left - 60) / 60 + 1 / 3
    else:
        start, now = close_ts - 60, close_ts - seconds_left
        observed = {int(p["time"]): p["value"] for p in samples
                    if start <= p["time"] < now and p["value"] is not None}
        expected = min(60, max(0, math.ceil(now - start)))
        result["observed_seconds"] = len(observed)
        result["missing_seconds"] = max(0, expected - len(observed))
        reference = (sum(observed.values()) + composite * (60 - len(observed))) / 60
        factor = (seconds_left / 60) ** 3 / 3
    sigma_usd = sigma_1m * reference if sigma_1m is not None else None
    sd = sigma_usd * math.sqrt(factor) if sigma_usd is not None else None
    distance = reference - strike
    result.update(distance=distance, distance_sd_usd=sd, sigma_1m_usd=sigma_usd,
                  distance_sigmas=distance / sd if sd and sd > 0 else None)
    return result


def positive_decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return result if result.is_finite() and result > 0 else None


def book_context(data):
    """Kalshi publishes YES and NO bids; the opposite bid is the ask."""
    book = data["orderbook_fp"]
    def top(side):
        levels = []
        for row in book.get(side, []):
            if len(row) != 2:
                continue
            price, count = positive_decimal(row[0]), positive_decimal(row[1])
            if price is not None and count is not None and price < 1:
                levels.append((price, count))
        return max(levels, default=None)
    yes, no = top("yes_dollars"), top("no_dollars")
    yes_ask = (Decimal(1) - no[0], no[1]) if no else None
    no_ask = (Decimal(1) - yes[0], yes[1]) if yes else None
    def show(level):
        return {"price": str(level[0]), "count": str(level[1])} if level else None
    mid = (yes[0] + yes_ask[0]) / 2 if yes and yes_ask else None
    return {"yes_bid": show(yes), "yes_ask": show(yes_ask),
            "no_bid": show(no), "no_ask": show(no_ask),
            "yes_mid": float(mid) if mid is not None else None}


def kalshi_trades(data, ticker, open_ts, close_ts):
    rows = []
    for trade in data.get("trades", []):
        if trade.get("ticker") != ticker:
            continue
        try:
            at = parse_ts(trade["created_time"])
        except (KeyError, ValueError, TypeError):
            continue
        if not open_ts <= at < close_ts:
            continue
        price = positive_decimal(trade.get("yes_price_dollars"))
        count = positive_decimal(trade.get("count_fp"))
        if price is None or count is None:
            continue
        rows.append({"time": trade["created_time"], "yes_price": str(price),
                     "count": str(count), "taker_side": trade.get("taker_side"),
                     "is_block_trade": bool(trade.get("is_block_trade"))})
    rows.sort(key=lambda row: row["time"], reverse=True)
    return rows[:30]


# ---------------------------------------------------------------- indicators
# All series functions return a list aligned with the input, None where the
# indicator is not yet defined.

def sma(values, n):
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values, n):
    out = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None or len(values) - start < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n

    def value(g, l):
        return 50.0 if g == 0 and l == 0 else 100.0 if l == 0 else 100 - 100 / (1 + g / l)

    out[n] = value(ag, al)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = value(ag, al)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(closes, n=20, k=2.0):
    mid = sma(closes, n)
    upper, lower = [None] * len(closes), [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1:i + 1]
        sd = math.sqrt(sum((x - mid[i]) ** 2 for x in window) / n)
        upper[i], lower[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, upper, lower


def atr(highs, lows, closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    tr = [highs[0] - lows[0]] + [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))]
    prev = sum(tr[1:n + 1]) / n
    out[n] = prev
    for i in range(n + 1, len(closes)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def stochastic(highs, lows, closes, n=14, d=3):
    k = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        hh, ll = max(highs[i - n + 1:i + 1]), min(lows[i - n + 1:i + 1])
        k[i] = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
    dline = [None] * len(closes)
    for i in range(n + d - 2, len(closes)):
        dline[i] = sum(k[i - d + 1:i + 1]) / d
    return k, dline


def anchored_vwap(bars, anchor):
    out, pv, vol = [None] * len(bars), 0.0, 0.0
    for i, b in enumerate(bars):
        if b["time"] < anchor:
            continue
        typical = (b["high"] + b["low"] + b["close"]) / 3
        pv += typical * b["volume"]
        vol += b["volume"]
        out[i] = pv / vol if vol else typical
    return out


def pivots(bars, k=2):
    """Fractal swing highs/lows on closed bars (the forming bar is excluded)."""
    highs, lows = [], []
    closed = bars[:-1]
    for i in range(k, len(closed) - k):
        window = closed[i - k:i + k + 1]
        if closed[i]["high"] == max(b["high"] for b in window):
            highs.append(closed[i]["high"])
        if closed[i]["low"] == min(b["low"] for b in window):
            lows.append(closed[i]["low"])
    return highs, lows


def realized_sigma(closes, lookback=60):
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - lookback), len(closes))]
    if len(rets) < 10:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))


def last(series):
    return next((v for v in reversed(series) if v is not None), None)


def analyze(bars, price, anchor, vwap_label="VWAP"):
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    e9, e21, e50, e200 = (ema(closes, n) for n in (9, 21, 50, 200))
    r = rsi(closes)
    m_line, m_sig, m_hist = macd(closes)
    bb_mid, bb_up, bb_lo = bollinger(closes)
    a = atr(highs, lows, closes)
    st_k, st_d = stochastic(highs, lows, closes)
    vw = anchored_vwap(bars, anchor)
    sw_hi, sw_lo = pivots(bars)

    v = {"ema9": last(e9), "ema21": last(e21), "ema50": last(e50), "ema200": last(e200),
         "rsi": last(r), "macd": last(m_line), "macd_signal": last(m_sig), "macd_hist": last(m_hist),
         "bb_mid": last(bb_mid), "bb_upper": last(bb_up), "bb_lower": last(bb_lo), "atr": last(a),
         "stoch_k": last(st_k), "stoch_d": last(st_d), "vwap": last(vw)}
    hist_vals = [x for x in m_hist if x is not None]
    v["macd_hist_prev"] = hist_vals[-2] if len(hist_vals) > 1 else None
    if v["bb_upper"] is not None and v["bb_upper"] != v["bb_lower"]:
        v["bb_pctb"] = (price - v["bb_lower"]) / (v["bb_upper"] - v["bb_lower"])
        v["bb_width_pct"] = (v["bb_upper"] - v["bb_lower"]) / v["bb_mid"] * 100
    v["resistance"] = min((h for h in sw_hi if h > price), default=None)
    v["support"] = max((l for l in sw_lo if l < price), default=None)

    readings = []

    def read(name, state, detail):
        readings.append({"name": name, "state": state, "detail": detail})

    if None not in (v["ema21"], v["ema50"]):
        if price > v["ema21"] > v["ema50"]:
            read("EMA stack", "bull", "price > EMA21 > EMA50")
        elif price < v["ema21"] < v["ema50"]:
            read("EMA stack", "bear", "price < EMA21 < EMA50")
        else:
            read("EMA stack", "neutral", "mixed ordering")
    if None not in (v["ema9"], v["ema21"]):
        read("EMA 9/21", "bull" if v["ema9"] > v["ema21"] else "bear",
             f"EMA9 {'above' if v['ema9'] > v['ema21'] else 'below'} EMA21")
    if v["ema200"] is not None:
        read("EMA 200", "bull" if price > v["ema200"] else "bear",
             f"price {'above' if price > v['ema200'] else 'below'} EMA200")
    if v["rsi"] is not None:
        x = v["rsi"]
        # Extremes are shown as stretched rather than counted as reversal calls.
        if x >= 70:
            read("RSI 14", "neutral", f"{x:.1f} overbought (stretched)")
        elif x <= 30:
            read("RSI 14", "neutral", f"{x:.1f} oversold (stretched)")
        else:
            read("RSI 14", "bull" if x > 55 else "bear" if x < 45 else "neutral", f"{x:.1f}")
    if v["macd_hist"] is not None:
        rising = v["macd_hist_prev"] is not None and v["macd_hist"] > v["macd_hist_prev"]
        h = v["macd_hist"]
        read("MACD", "neutral" if abs(h) < 1e-9 * price else "bull" if h > 0 else "bear",
             f"hist {'+' if v['macd_hist'] > 0 else ''}{v['macd_hist']:.2f}, {'rising' if rising else 'falling'}")
    if v.get("bb_pctb") is not None:
        b = v["bb_pctb"]
        read("Bollinger %B", "neutral",
             f"{b:.2f}" + (" above upper (stretched)" if b > 1 else " below lower (stretched)" if b < 0 else ""))
    if None not in (v["stoch_k"], v["stoch_d"]):
        diff = v["stoch_k"] - v["stoch_d"]
        read("Stoch 14,3", "neutral" if abs(diff) < 0.5 else "bull" if diff > 0 else "bear",
             f"%K {v['stoch_k']:.0f} / %D {v['stoch_d']:.0f}")
    if v["vwap"] is not None:
        read(vwap_label, "bull" if price > v["vwap"] else "bear",
             f"price {'above' if price > v['vwap'] else 'below'} ${v['vwap']:,.2f}")
    bulls = sum(x["state"] == "bull" for x in readings)
    bears = sum(x["state"] == "bear" for x in readings)
    tally = {"bull": bulls, "bear": bears, "neutral": len(readings) - bulls - bears,
             "lean": "bullish" if bulls - bears >= 2 else "bearish" if bears - bulls >= 2 else "mixed"}

    def series(values):
        return [{"time": b["time"], "value": round(x, 4)} for b, x in zip(bars, values) if x is not None]

    chart = {"candles": [{k: b[k] for k in ("time", "open", "high", "low", "close")} for b in bars],
             "volume": [{"time": b["time"], "value": b["volume"],
                         "up": b["close"] >= b["open"]} for b in bars],
             "ema9": series(e9), "ema21": series(e21), "ema50": series(e50),
             "bb_upper": series(bb_up), "bb_lower": series(bb_lo), "vwap": series(vw),
             "rsi": series(r), "macd": series(m_line), "macd_signal": series(m_sig),
             "macd_hist": series(m_hist)}
    return {"values": v, "readings": readings, "tally": tally, "chart": chart}


def aggregate(bars, width):
    """Merge sorted bars into `width`-second bars aligned to the epoch, dropping a
    leading group that doesn't start on the boundary (it would be partial)."""
    groups = {}
    for b in bars:
        groups.setdefault(b["time"] // width * width, []).append(b)
    out = []
    for start, g in sorted(groups.items()):
        if not out and g[0]["time"] != start:
            continue
        out.append({"time": start, "open": g[0]["open"], "close": g[-1]["close"],
                    "high": max(b["high"] for b in g), "low": min(b["low"] for b in g),
                    "volume": sum(b["volume"] for b in g)})
    return out


# ------------------------------------------------------------------ collector

class Feed:
    def __init__(self):
        self.lock = threading.Lock()
        self.bars = {tf: [] for tf in TIMEFRAMES}
        self.bars_at = {tf: 0.0 for tf in TIMEFRAMES}
        self.spot = None
        self.spot_at = 0.0
        self.market = None
        self.market_at = 0.0
        self.last_market_ticker = None
        # A new window's first metadata comes from the 30 s listing prefetch.
        # `market_promoted_at` starts the short stale grace, `market_wake` lets
        # the sampler ask the market loop for a direct per-ticker refresh now,
        # and `direct_ticker`/`direct_at` keep those GETs to one per 2 s.
        self.market_promoted_at = 0.0
        self.market_wake = threading.Event()
        self.direct_ticker = None
        self.direct_at = 0.0
        self.upcoming_markets = {}
        self.upcoming_at = 0.0
        self.captured_tickers = set()
        self.errors = {}
        self.venues = {name: None for name in ("Coinbase", *VENUE_URLS)}
        self.composite = None
        self.composite_at = 0.0
        self.composite_details = {}
        self.samples = deque(maxlen=60)
        self.average_60s = None
        self.tally_logger = None
        self.scalp_engine = None
        self.scalp_last_bucket = None
        self.feed_modes = {"Coinbase": "REST fallback", "Kraken": "REST fallback"}
        self.socket_connected = {"Coinbase": False, "Kraken": False}
        self.stream_last_at = {"Coinbase": 0.0, "Kraken": 0.0}
        self.stream_trade_at = {"Coinbase": 0.0, "Kraken": 0.0}
        self.flow = OrderFlow(large_trade_usd=LARGE_TRADE_USD)
        self.flow_gap_window = int(time.time() // 900) * 900
        self.window_path = deque(maxlen=930)
        self.window_path_open = None
        self.book = None
        self.book_at = 0.0
        self.kalshi_recent_trades = []
        self.yes_mid_path = deque(maxlen=460)
        self.context_ticker = None

    def _note(self, key, exc):
        now = iso(time.time())
        first = self.errors.get(key, {}).get("first_at", now)
        self.errors[key] = {"error": f"{type(exc).__name__}: {exc}", "at": now, "first_at": first}

    def poll_candles(self, tf):
        granularity, sec, pages = FETCHES[tf]
        end, found = int(time.time()), {}
        for _ in range(pages):
            rows = get_json(CANDLES, {"granularity": granularity, "start": end - 299 * sec, "end": end})
            for r in rows["candles"]:
                found[int(r["start"])] = {"time": int(r["start"]), "low": finite_float(r["low"]),
                                          "high": finite_float(r["high"]), "open": finite_float(r["open"]),
                                          "close": finite_float(r["close"]), "volume": finite_float(r["volume"])}
            end -= 300 * sec
        bars = sorted(found.values(), key=lambda b: b["time"])
        derived = {name: aggregate(bars, width) for name, (src, width) in DERIVED.items() if src == tf}
        with self.lock:
            now = time.time()
            self.bars[tf], self.bars_at[tf] = bars[-MAX_BARS:], now
            for name, agg in derived.items():
                self.bars[name], self.bars_at[name] = agg[-MAX_BARS:], now
            self.errors.pop(f"candles_{tf}", None)

    def poll_spot(self):
        t = get_json(f"{COINBASE}/ticker")
        fetched_at = time.time()
        spot = parse_coinbase_spot(t, "BTC-USD", fetched_at)
        venue = venue_quote("Coinbase", t, fetched_at)
        self.update_spot(spot.price, spot.bid, spot.ask, t.get("time"), venue, fetched_at)

    def update_spot(self, price, bid, ask, stamp, venue, fetched_at):
        price, bid, ask = finite_float(price), finite_float(bid), finite_float(ask)
        if min(price, bid, ask) <= 0:
            raise ValueError("nonpositive spot quote")
        with self.lock:
            self.spot, self.spot_at = {"price": price, "bid": bid, "ask": ask,
                                       "time": stamp}, fetched_at
            self.venues["Coinbase"] = venue
            self.errors.pop("spot", None)
            # A delayed ticker is visible as stale, but cannot patch current bars.
            if fetched_at - venue["at"] > VENUE_MAX_AGE:
                return
            # Patch each timeframe's forming bar (or open a new one) with the tick.
            now = time.time()
            for tf, sec in TIMEFRAMES.items():
                bars = self.bars[tf]
                if not bars:
                    continue
                start = int(now // sec) * sec
                if bars[-1]["time"] == start:
                    b = bars[-1]
                    b["close"], b["high"], b["low"] = price, max(b["high"], price), min(b["low"], price)
                elif bars[-1]["time"] < start:
                    bars.append({"time": start, "open": price, "high": price, "low": price,
                                 "close": price, "volume": 0.0})

    def poll_venue(self, name):
        fetched = get_json(VENUE_URLS[name], timeout=5)
        quote = venue_quote(name, fetched, time.time())
        with self.lock:
            self.venues[name] = quote
            self.errors.pop(f"venue_{name.lower()}", None)

    def stream_active(self, name):
        with self.lock:
            active = self.socket_connected[name] and time.time() - self.stream_last_at[name] < 10
            if name == "Coinbase":
                active = active and time.time() - self.stream_trade_at["Coinbase"] <= VENUE_MAX_AGE
            return active

    def handle_coinbase_message(self, message):
        data = json.loads(message) if isinstance(message, str) else message
        now = time.time()
        if data.get("product_id") != "BTC-USD":
            return
        if data.get("type") == "ticker":
            spot = parse_coinbase_spot(data, "BTC-USD", now)
            venue = venue_quote("Coinbase", data, now)
            self.update_spot(spot.price, spot.bid, spot.ask, data.get("time"), venue, now)
            with self.lock:
                self.stream_last_at["Coinbase"] = now
                self.stream_trade_at["Coinbase"] = venue["at"]
                self.feed_modes["Coinbase"] = "WebSocket"
        elif data.get("type") == "match":
            trades = parse_coinbase(data, "BTC-USD")
            with self.lock:
                for trade in trades:
                    if trade.timestamp > now + 2 or now - trade.timestamp > 20:
                        continue
                    self.flow.add_trade(trade)

    def handle_kraken_message(self, message):
        data = json.loads(message) if isinstance(message, str) else message
        trades = parse_kraken(data, "BTC/USD")
        if not trades:
            return
        now = time.time()
        with self.lock:
            for trade in trades:
                if trade.timestamp > now + 2 or now - trade.timestamp > 20:
                    continue
                self.flow.add_trade(trade)
                self.venues["Kraken"] = {"price": trade.price, "at": trade.timestamp, "age_basis": "trade"}
                self.stream_last_at["Kraken"] = now
                self.feed_modes["Kraken"] = "WebSocket"
                self.errors.pop("venue_kraken", None)

    def run_stream(self, name):
        try:
            from websockets.sync.client import connect
        except ImportError:
            # The Mac has no websockets; REST remains usable, and recorded
            # message tests still run. Acer system Python has websockets 16.1.
            return
        url = COINBASE_WS if name == "Coinbase" else KRAKEN_WS
        subscription = ({"type": "subscribe", "product_ids": ["BTC-USD"],
                         "channels": ["matches", "ticker"]} if name == "Coinbase" else
                        {"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"],
                                                            "snapshot": False}})
        delay = 1
        while True:
            try:
                with connect(url, open_timeout=8, ping_interval=20, ping_timeout=20,
                             max_size=1_000_000) as socket:
                    socket.send(json.dumps(subscription))
                    with self.lock:
                        self.socket_connected[name] = True
                    delay = 1
                    while True:
                        message = socket.recv(timeout=30)
                        try:
                            if name == "Coinbase":
                                self.handle_coinbase_message(message)
                            else:
                                self.handle_kraken_message(message)
                        except (ValueError, KeyError, TypeError, MalformedMessage):
                            # One malformed public frame cannot terminate the feed.
                            continue
            except Exception:
                with self.lock:
                    self.socket_connected[name] = False
                    self.feed_modes[name] = "REST fallback"
                    self.flow_gap_window = int(time.time() // 900) * 900
                time.sleep(delay)
                delay = min(delay * 2, 30)

    def sample_composite(self, at=None):
        at = time.time() if at is None else at
        with self.lock:
            value, details = composite_quote(self.venues, at)
            self.composite, self.composite_details = value, details
            self.composite_at = at if value is not None else 0.0
            if self.samples and self.samples[-1][0] == int(at):
                self.samples.pop()
            self.samples.append((int(at), value))
            self.average_60s = rolling_average(self.samples, int(at))
            opened = int(at // 900) * 900
            if self.window_path_open != opened:
                self.window_path_open = opened
                self.window_path.clear()
                self.flow_gap_window = None if all(self.socket_connected.values()) else opened
            if value is not None:
                self.window_path.append({"time": int(at), "value": value})
            self.flow.advance(at)
            if not self.market or parse_ts(self.market["close_time"]) <= at:
                current = [m for m in self.upcoming_markets.values()
                           if parse_ts(m["open_time"]) <= at < parse_ts(m["close_time"])]
                if current:
                    promoted = min(current, key=lambda m: m["close_time"])
                    if not self.market or self.market["ticker"] != promoted["ticker"]:
                        # The prefetched metadata is up to 30 s old and may have
                        # no target or quotes yet. Ask the market loop for the
                        # direct per-ticker refresh now instead of waiting out
                        # its current 2 s cycle.
                        self.market_promoted_at = at
                        self.market_wake.set()
                        self.market_at = self.upcoming_at
                    self.market = promoted
            for market in self.upcoming_markets.values():
                try:
                    delay = at - parse_ts(market["open_time"])
                    if 0 <= delay < 3:
                        self._capture_open(market, at)
                except Exception as exc:
                    self._note("tally_log", exc)

    def _capture_open(self, market, at):
        ticker = market["ticker"]
        if not self.tally_logger or ticker in self.captured_tickers:
            return
        # A market can remain initialized at its scheduled open. Never turn
        # its placeholder 0.0000 quotes into observed opening bid/ask.
        current = self.market if self.market and self.market["ticker"] == ticker else None
        active = current and current.get("status") == "active" and abs(at - self.market_at) <= 3
        observed = current if active else market
        opening_market = dict(observed)
        if not active:
            opening_market["yes_bid_dollars"] = None
            opening_market["yes_ask_dollars"] = None
        snapshot = {"market": opening_market, "captured_at": at,
                    "market_observed_at": self.market_at if active else None,
                    "composite": self.composite,
                    "coinbase_spot": self.spot["price"] if self.spot else None,
                    "bars": {tf: [dict(b) for b in rows] for tf, rows in self.bars.items()}}
        if self.tally_logger.submit(snapshot):
            self.captured_tickers.add(ticker)

    def poll_upcoming(self):
        now = time.time()
        data = get_json(f"{KALSHI}/markets", {"series_ticker": SERIES,
                                              "min_close_ts": int(now),
                                              "max_close_ts": int(now) + 1800,
                                              "limit": 20})
        markets = {m["ticker"]: validate_market(m) for m in data.get("markets", [])
                   if now - 60 <= parse_ts(m["open_time"]) <= now + 1800}
        with self.lock:
            self.upcoming_markets = markets
            self.upcoming_at = now
            self.errors.pop("kalshi_upcoming", None)

    def run_upcoming(self):
        while True:
            try:
                self.poll_upcoming()
            except Exception as exc:
                with self.lock:
                    self._note("kalshi_upcoming", exc)
            time.sleep(30)

    def run_venue(self, name):
        while True:
            if name != "Kraken" or not self.stream_active(name):
                try:
                    self.poll_venue(name)
                except Exception as exc:
                    with self.lock:
                        self._note(f"venue_{name.lower()}", exc)
            time.sleep(2)

    def run_composite(self):
        while True:
            try:
                self.sample_composite()
            except Exception as exc:
                with self.lock:
                    self._note("composite", exc)
            # Wake just after the next wall-clock second. Sleeping "1 s minus
            # elapsed" drifts and eventually skips a second, which blanks the
            # 60 s average (it requires every second) for a full minute.
            now = time.time()
            time.sleep(math.floor(now) + 1.05 - now)

    def direct_market(self, ticker, now):
        """Fetch a ticker at most once per two seconds, including failed attempts."""
        with self.lock:
            if ticker == self.direct_ticker and now - self.direct_at < KALSHI_REFRESH:
                return None, None
            self.direct_ticker, self.direct_at = ticker, now
        detail = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")
        if detail.get("market", {}).get("ticker") != ticker:
            raise ValueError("Kalshi ticker detail mismatch")
        return validate_market(detail["market"]), time.time()

    def poll_market(self):
        now = time.time()
        with self.lock:
            current = dict(self.market) if self.market and parse_ts(self.market["open_time"]) <= now < parse_ts(self.market["close_time"]) else None
            current_at = self.market_at
            needs_direct = (current and self.market_promoted_at and
                            (self.direct_ticker != current["ticker"] or
                             self.direct_at < self.market_promoted_at))
        direct, direct_at = None, None
        direct_error = None
        if needs_direct:
            try:
                direct, direct_at = self.direct_market(current["ticker"], now)
            except Exception as exc:
                direct_error = exc
        listing_error = None
        try:
            data = get_json(f"{KALSHI}/markets", {"series_ticker": SERIES, "status": "open", "limit": 20})
        except Exception as exc:
            data, listing_error = {"markets": []}, exc
        live = [validate_market(m) for m in data.get("markets", [])
                if parse_ts(m["open_time"]) <= now < parse_ts(m["close_time"])]
        live.sort(key=lambda m: m["close_time"])
        selected = live[0] if live else None
        selected_at = time.time() if selected else 0.0
        refreshed = selected is not None
        from_prefetch = False
        if not selected:
            with self.lock:
                upcoming = [dict(m) for m in self.upcoming_markets.values()
                            if parse_ts(m["open_time"]) <= now < parse_ts(m["close_time"])]
                upcoming_at = self.upcoming_at
            upcoming.sort(key=lambda m: m["close_time"])
            prefetched = upcoming[0] if upcoming else None
            if current and (not prefetched or current_at >= upcoming_at):
                selected, selected_at = current, current_at
            else:
                selected, selected_at = prefetched, upcoming_at
                refreshed = bool(selected and upcoming_at > current_at)
                from_prefetch = selected is not None
        if selected and direct and direct["ticker"] == selected["ticker"]:
            selected, selected_at = direct, direct_at
            refreshed = True
            from_prefetch = False
        elif selected and not live:
            try:
                detail, fetched_at = self.direct_market(selected["ticker"], now)
                if detail is not None:
                    selected, selected_at = detail, fetched_at
                    refreshed = True
                    from_prefetch = False
            except Exception as exc:
                direct_error = exc
        with self.lock:
            old_ticker = self.last_market_ticker
            self.market, self.market_at = selected, selected_at
            if self.market and self.market["ticker"] != old_ticker:
                if from_prefetch:
                    if not (current and current["ticker"] == selected["ticker"]
                            and self.market_promoted_at):
                        self.market_promoted_at = now
                elif refreshed:
                    self.market_promoted_at = 0.0
            if direct_error or listing_error:
                self._note("kalshi", direct_error or listing_error)
            elif refreshed:
                self.errors.pop("kalshi", None)
            if self.market:
                self.last_market_ticker = self.market["ticker"]
            if self.tally_logger and self.market and self.market["ticker"] != old_ticker:
                # Fallback if the scheduled capture lacked upcoming metadata.
                # Preserve the actual capture delay for the research log.
                open_at = parse_ts(self.market["open_time"])
                limit = 90 if old_ticker else 10
                if 0 <= now - open_at <= limit:
                    self._capture_open(self.market, now)

    def run_market(self):
        while True:
            self.market_wake.clear()
            try:
                self.poll_market()
            except Exception as exc:
                with self.lock:
                    self._note("kalshi", exc)
            self.market_wake.wait(KALSHI_REFRESH)

    def poll_market_context(self):
        with self.lock:
            market = dict(self.market) if self.market else None
        if not market or parse_ts(market["close_time"]) <= time.time():
            with self.lock:
                self.context_ticker = None
                self.book = None
                self.kalshi_recent_trades = []
                self.yes_mid_path.clear()
            return
        ticker = market["ticker"]
        opened, closed = parse_ts(market["open_time"]), parse_ts(market["close_time"])
        book = book_context(get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}/orderbook",
                                     {"depth": 5}))
        book_observed_at = time.time()
        trades = kalshi_trades(get_json(f"{KALSHI}/markets/trades",
                                        {"ticker": ticker, "min_ts": int(opened), "limit": 100}),
                               ticker, opened, closed)
        now = time.time()
        with self.lock:
            if not self.market or self.market["ticker"] != ticker:
                return
            if self.context_ticker != ticker:
                self.context_ticker = ticker
                self.yes_mid_path.clear()
            self.book, self.book_at = book, book_observed_at
            self.kalshi_recent_trades = trades
            if book["yes_mid"] is not None:
                self.yes_mid_path.append({"time": int(now), "value": book["yes_mid"]})
            self.errors.pop("kalshi_context", None)

    def run_market_context(self):
        next_at = 0.0
        while True:
            wait = next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_at = time.monotonic() + KALSHI_REFRESH
            try:
                self.poll_market_context()
            except Exception as exc:
                with self.lock:
                    self._note("kalshi_context", exc)

    def run(self):
        due = {}
        while True:
            now = time.time()
            jobs = [] if self.stream_active("Coinbase") else [("spot", SPOT_REFRESH, self.poll_spot)]
            jobs += [(f"candles_{tf}", CANDLE_REFRESH[tf], lambda tf=tf: self.poll_candles(tf))
                     for tf in FETCHES]
            for key, every, fn in jobs:
                if now >= due.get(key, 0):
                    try:
                        fn()
                        due[key] = now + every
                    except Exception as exc:  # keep collecting; surface the error in the UI
                        with self.lock:
                            self._note(key, exc)
                        due[key] = now + min(every, 5)
            time.sleep(0.5)

    def state(self, tf):
        now = time.time()
        with self.lock:
            bars = {k: [dict(b) for b in v] for k, v in self.bars.items()}
            spot, spot_at = dict(self.spot) if self.spot else None, self.spot_at
            market, market_at = self.market, self.market_at
            promoted_at = self.market_promoted_at
            errors = dict(self.errors)
            composite, composite_at = self.composite, self.composite_at
            average_60s = self.average_60s
            fresh_composite, venues = composite_quote(self.venues, now)
            modes = {"Bitstamp": "REST", "Gemini": "REST"}
            modes.update({name: ("WebSocket" if self.socket_connected[name] and
                                 now - self.stream_last_at[name] < 10 and
                                 (name != "Coinbase" or now - self.stream_trade_at[name] <= VENUE_MAX_AGE)
                                 else "REST fallback")
                          for name in self.feed_modes})
            flow = self.flow.snapshot(now)
            flow["partial_window"] = (flow["partial_window"] or
                                       self.flow_gap_window == flow["window"]["open_epoch"])
            path = list(self.window_path)
            book = dict(self.book) if self.book else None
            book_at = self.book_at
            context_ticker = self.context_ticker
            recent_trades = list(self.kalshi_recent_trades)
            mid_path = list(self.yes_mid_path)
        if not spot or not bars["1m"]:
            return {"ready": False, "stale": True, "kalshi_stale": True,
                    "errors": errors, "server_time": iso(now), "scalp": None}
        price = spot["price"]
        if not composite_at or now - composite_at > 2:
            composite = fresh_composite
            average_60s = None

        window = None
        anchor = int(now // 900) * 900
        if market and parse_ts(market["open_time"]) <= now < parse_ts(market["close_time"]):
            open_ts, close_ts = parse_ts(market["open_time"]), parse_ts(market["close_time"])
            anchor = int(open_ts)
            strike = optional_float(market.get("floor_strike"))
            yb, ya = market.get("yes_bid_dollars"), market.get("yes_ask_dollars")
            quotes_ready = market.get("status") != "initialized"
            window = {"ticker": market["ticker"], "strike": strike, "open_time": market["open_time"],
                      "close_time": market["close_time"], "seconds_left": max(0.0, close_ts - now),
                      "yes_bid": optional_float(yb) if quotes_ready else None,
                      "yes_ask": optional_float(ya) if quotes_ready else None,
                      "no_bid": optional_float(market.get("no_bid_dollars")) if quotes_ready else None,
                      "no_ask": optional_float(market.get("no_ask_dollars")) if quotes_ready else None,
                      "volume": market.get("volume_fp"), "age": now - market_at}
            window_points = [point for point in path if open_ts <= point["time"] < close_ts]
            window["price_path"] = window_points
            window["open_price"] = window_points[0]["value"] if window_points else None
            window["high_since_open"] = max((p["value"] for p in window_points), default=None)
            window["low_since_open"] = min((p["value"] for p in window_points), default=None)
            window["path_partial"] = not window_points or window_points[0]["time"] > open_ts + 2
            window["orderbook"] = book if context_ticker == market["ticker"] else None
            window["orderbook_age"] = now - book_at if context_ticker == market["ticker"] and book else None
            shown_book = window["orderbook"] if window["orderbook_age"] is not None and window["orderbook_age"] <= 10 else None
            window["display_quotes"] = {}
            for side in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
                level = shown_book.get(side) if shown_book else None
                window["display_quotes"][side] = (
                    {"price": level["price"], "source": "book", "age": window["orderbook_age"]}
                    if level else {"price": window[side], "source": "market summary", "age": window["age"]})
            window["recent_trades"] = recent_trades if context_ticker == market["ticker"] else []
            window["yes_mid_path"] = mid_path if context_ticker == market["ticker"] else []
            sig = realized_sigma([b["close"] for b in bars["1m"]])
            window.update(window_distance(strike, composite, window["seconds_left"], sig,
                                          close_ts, window_points))

        frames = {}
        for name, b in bars.items():
            if len(b) < 30:
                continue
            a = (analyze(b, price, anchor, "VWAP (window)") if name == "1m"
                 else analyze(b, price, b[0]["time"], "VWAP (loaded range)"))
            if name == "1m":
                a["values"]["close"] = b[-1]["close"]
                a["values"]["close_prev"] = b[-2]["close"] if len(b) > 1 else None
            frames[name] = a if name == tf else {k: a[k] for k in ("values", "readings", "tally")}
        m1 = bars["1m"]

        def ref(minutes):
            target = int(now // 60) * 60 - minutes * 60
            return next((b["open"] for b in m1 if b["time"] >= target), None)

        grace_left = max(0.0, promoted_at + KALSHI_PROMOTION_GRACE - now) if promoted_at else 0.0
        return {"ready": True, "server_time": iso(now), "timeframe": tf,
                "ref_15m": ref(15), "ref_1h": ref(60),
                "spot": spot, "spot_age": max(now - spot_at, now - parse_ts(spot["time"])),
                "composite": composite, "composite_age": now - composite_at if composite_at else None,
                "average_60s": average_60s, "venues": venues,
                "feed_modes": modes, "order_flow": flow,
                "candle_age": {k: now - v for k, v in self.bars_at.items()},
                "stale": now - spot_at > STALE_AFTER or now - parse_ts(spot["time"]) > VENUE_MAX_AGE
                or composite is None,
                # Freshness of the displayed window: time since the last
                # successful Kalshi fetch that carried it, with a short grace
                # for a just-promoted window whose only data is the prefetch.
                # `window["age"]` stays the age of that metadata.
                "kalshi_stale": window is None or (window["age"] > KALSHI_STALE_AFTER
                                                   and grace_left <= 0),
                "kalshi_grace_left": grace_left,
                "window": window, "frames": frames, "scalp": None,
                "errors": errors, "vwap_anchor": iso(anchor)}


def opening_record(snapshot):
    market = snapshot["market"]
    frames = {}
    price = snapshot["coinbase_spot"]
    if price is not None:
        for tf, bars in snapshot["bars"].items():
            if len(bars) < 30:
                continue
            anchor = int(parse_ts(market["open_time"])) if tf == "1m" else bars[0]["time"]
            result = analyze(bars, price, anchor)
            frames[tf] = {**result["tally"], "rsi": result["values"]["rsi"],
                          "macd_hist": result["values"]["macd_hist"]}
    return {"type": "open", "ticker": market["ticker"], "open_time": market["open_time"],
            "close_time": market["close_time"], "captured_at": iso(snapshot["captured_at"]),
            "capture_delay_seconds": round(snapshot["captured_at"] - parse_ts(market["open_time"]), 3),
            "market_status_at_capture": market.get("status"),
            "quote_observed_at": (iso(snapshot["market_observed_at"])
                                  if snapshot.get("market_observed_at") is not None else None),
            "target_observed_at": (iso(snapshot["market_observed_at"])
                                   if market.get("floor_strike") is not None and snapshot.get("market_observed_at") is not None
                                   else None),
            "target": market.get("floor_strike"), "composite_spot": snapshot["composite"],
            "timeframes": frames, "yes_bid": market.get("yes_bid_dollars"),
            "yes_ask": market.get("yes_ask_dollars")}


def settled_record(ticker, market, now):
    if market.get("result") not in ("yes", "no"):
        return None
    record = {"type": "settled", "ticker": ticker, "recorded_at": iso(now),
              "result": market["result"]}
    if market.get("expiration_value") is not None:
        record["expiration_value"] = market["expiration_value"]
    return record


class TallyLogger:
    """Append opening/settlement research records off the serving thread."""
    def __init__(self, state_dir, feed):
        self.path = state_dir / "tally_log.jsonl"
        self.pending_path = state_dir / "tally_pending.json"
        self.feed = feed
        self.queue = queue.Queue(maxsize=32)
        self.opened = {}
        self.settled = set()
        self.pending_open = {}
        self.pending_snapshots = {}

    def submit(self, snapshot):
        try:
            self.queue.put_nowait(snapshot)
            return True
        except queue.Full:
            self.feed._note("tally_log", RuntimeError("opening queue full"))
            return False

    def prepare(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    if row.get("type") == "open":
                        self.opened[row["ticker"]] = row
                    elif row.get("type") == "settled":
                        self.settled.add(row["ticker"])
        except FileNotFoundError:
            pass
        if self.path.exists():
            os.chmod(self.path, 0o600)
        try:
            rows = json.loads(self.pending_path.read_text(encoding="utf-8"))
            self.pending_open = {ticker: row for ticker, row in rows.items()
                                 if ticker not in self.opened}
            os.chmod(self.pending_path, 0o600)
        except FileNotFoundError:
            pass

    def persist_pending(self):
        payload = json.dumps(self.pending_open, separators=(",", ":"), allow_nan=False).encode()
        temp = self.pending_path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(payload)
        finally:
            os.close(fd)
        os.replace(temp, self.pending_path)

    def accept_snapshot(self, snapshot):
        row = opening_record(snapshot)
        ticker = row["ticker"]
        if ticker in self.opened:
            return
        if ticker in self.pending_open:
            self.persist_pending()
            return
        if row["target"] is None:
            self.pending_open[ticker] = row
            self.persist_pending()
        else:
            self.append(row)
            self.opened[ticker] = row

    def hydrate_pending(self, now):
        for ticker, row in list(self.pending_open.items()):
            try:
                market = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")["market"]
                if market.get("floor_strike") is None:
                    continue
                complete = {**row, "target": market["floor_strike"],
                            "target_observed_at": iso(time.time()), "market_status_at_target": market.get("status")}
                self.append(complete)
                self.opened[ticker] = complete
                del self.pending_open[ticker]
                self.persist_pending()
                with self.feed.lock:
                    self.feed.errors.pop("tally_open", None)
            except Exception as exc:
                with self.feed.lock:
                    self.feed._note("tally_open", exc)

    def append(self, row):
        payload = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "ab", closefd=False) as handle:
                handle.write(payload)
        finally:
            os.close(fd)

    def settle_pending(self, now):
        for ticker, row in list(self.opened.items()):
            if ticker in self.settled:
                continue
            try:
                if parse_ts(row["close_time"]) > now:
                    continue
                market = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")["market"]
                record = settled_record(ticker, market, now)
                if record is None:
                    continue
                self.append(record)
                self.settled.add(ticker)
                with self.feed.lock:
                    self.feed.errors.pop("tally_settle", None)
            except Exception as exc:
                with self.feed.lock:
                    self.feed._note("tally_settle", exc)

    def run(self):
        try:
            self.prepare()
        except Exception as exc:
            with self.feed.lock:
                self.feed._note("tally_log", exc)
        next_settle = next_hydrate = 0
        while True:
            try:
                snapshot = self.queue.get(timeout=1)
                ticker = snapshot["market"]["ticker"]
                if ticker not in self.opened:
                    self.pending_snapshots[ticker] = snapshot
            except queue.Empty:
                pass
            for ticker, snapshot in list(self.pending_snapshots.items()):
                try:
                    self.accept_snapshot(snapshot)
                    del self.pending_snapshots[ticker]
                    with self.feed.lock:
                        self.feed.errors.pop("tally_log", None)
                except Exception as exc:
                    with self.feed.lock:
                        self.feed._note("tally_log", exc)
            if time.time() >= next_hydrate:
                next_hydrate = time.time() + 5
                self.hydrate_pending(time.time())
            if time.time() < next_settle:
                continue
            next_settle = time.time() + 60
            self.settle_pending(time.time())


FEED = Feed()


# A full state is ~160 KB, almost all chart series. The stream sends it on
# connect, when the Kalshi window changes, and every STREAM_FULL_EVERY seconds
# as a resync; other events carry only the last STREAM_TAIL points of each
# series (new and revised bars are always among them), merged by time.
STREAM_INTERVAL = 1.0
STREAM_FULL_EVERY = 60
STREAM_TAIL = 5
WINDOW_PATHS = ("price_path", "yes_mid_path")
STATE_CACHE = {}
STATE_CACHE_LOCK = threading.Lock()
SSE_LIMIT = 12
SSE_SLOTS = threading.BoundedSemaphore(SSE_LIMIT)


def cached_state(tf):
    with STATE_CACHE_LOCK:
        bucket = int(time.time())
        prior = STATE_CACHE.get(tf)
        if prior and prior[0] == bucket:
            state = dict(prior[1])
            current = time.time()
            elapsed = max(0, current - parse_ts(state["server_time"])) if "server_time" in state else 0
            state["server_time"] = iso(current)
            if state.get("ready"):
                state["spot_age"] += elapsed
                state["stale"] = state["stale"] or state["spot_age"] > VENUE_MAX_AGE
                if state.get("window"):
                    window = dict(state["window"])
                    if current >= parse_ts(window["close_time"]):
                        state["window"] = None
                        state["kalshi_stale"] = True
                    else:
                        window["age"] += elapsed
                        window["seconds_left"] = max(0, parse_ts(window["close_time"]) - current)
                        if window.get("orderbook_age") is not None:
                            window["orderbook_age"] += elapsed
                        window["display_quotes"] = {
                            name: ({"price": window.get(name), "source": "market summary", "age": window["age"]}
                                   if quote["source"] == "book" and quote["age"] + elapsed > 10
                                   else {**quote, "age": quote["age"] + elapsed})
                            for name, quote in window.get("display_quotes", {}).items()}
                        state["window"] = window
                        grace_left = max(0.0, state.get("kalshi_grace_left", 0.0) - elapsed)
                        state["kalshi_grace_left"] = grace_left
                        state["kalshi_stale"] = window["age"] > KALSHI_STALE_AFTER and grace_left <= 0
            return state
        state = FEED.state(tf)
        engine = FEED.scalp_engine
        if engine is not None:
            try:
                if FEED.scalp_last_bucket != bucket:
                    state["scalp"] = engine.step(state, time.time())
                    FEED.scalp_last_bucket = bucket
                else:
                    state["scalp"] = engine.snapshot_view(time.time())
            except Exception as exc:
                state["scalp"] = {"experimental": True, "paper": True, "validated": False,
                                  "state": "unavailable", "label": "FLAT · unavailable",
                                  "errors": [f"{type(exc).__name__}: {exc}"]}
        STATE_CACHE[tf] = (bucket, state)
        return state


def run_scalp_clock():
    """Keep paper scoring at one-second cadence even with no browser clients."""
    while True:
        try:
            cached_state("1m")
        except Exception as exc:
            engine = FEED.scalp_engine
            if engine is not None:
                with engine.lock:
                    engine.note(f"paper clock: {type(exc).__name__}: {exc}")
        time.sleep(max(0.05, 1.0 - time.time() % 1.0))


def run_scalp_settlement():
    """Poll only a held market's public result after close; no account routes."""
    while True:
        engine = FEED.scalp_engine
        ticker = engine.settlement_due(time.time()) if engine else None
        if ticker:
            try:
                market = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")["market"]
                result = str(market.get("result") or "").lower()
                if result in ("yes", "no"):
                    engine.settle(ticker, result, time.time())
            except Exception as exc:
                with engine.lock:
                    engine.note(f"public settlement GET: {type(exc).__name__}: {exc}")
        time.sleep(10)


def note_state_error(exc):
    with FEED.lock:
        FEED._note("state_serialization", exc)


def clear_state_error():
    with FEED.lock:
        FEED.errors.pop("state_serialization", None)


def stream_key(state, tf):
    return state.get("ready"), tf, (state.get("window") or {}).get("ticker")


def tail_state(state, tf):
    out = dict(state)
    frames = dict(out.get("frames") or {})
    if tf in frames and frames[tf].get("chart"):
        frame = dict(frames[tf])
        frame["chart"] = {k: v[-STREAM_TAIL:] for k, v in frame["chart"].items()}
        frame["chart_tail"] = True
        frames[tf] = frame
        out["frames"] = frames
    if out.get("window"):
        window = dict(out["window"])
        for k in WINDOW_PATHS:
            if isinstance(window.get(k), list):
                window[k] = window[k][-STREAM_TAIL:]
        window["paths_tail"] = True
        out["window"] = window
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_response(self, code, message=None):
        super().send_response(code, message)
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/stream":
            tf = parse_qs(url.query).get("tf", ["1m"])[0]
            tf = tf if tf in TIMEFRAMES else "1m"
            if not SSE_SLOTS.acquire(blocking=False):
                self.send_error(503, "SSE client limit reached")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                last_key, last_full = None, 0.0
                while True:
                    try:
                        state = cached_state(tf)
                        key, now = stream_key(state, tf), time.monotonic()
                        full = key != last_key or now - last_full >= STREAM_FULL_EVERY
                        payload = state if full else tail_state(state, tf)
                        body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
                    except ValueError as exc:
                        note_state_error(exc)
                        time.sleep(STREAM_INTERVAL)
                        continue
                    clear_state_error()
                    self.wfile.write(("event: state\ndata: " + body + "\n\n").encode())
                    self.wfile.flush()
                    if full:
                        last_key, last_full = key, now
                    time.sleep(STREAM_INTERVAL)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                SSE_SLOTS.release()
            return
        if url.path == "/":
            body, ctype = (HERE / "index.html").read_bytes(), "text/html; charset=utf-8"
        elif url.path == "/app.js":
            body, ctype = (HERE / "app.js").read_bytes(), "application/javascript; charset=utf-8"
        elif url.path == "/api/state":
            tf = parse_qs(url.query).get("tf", ["1m"])[0]
            tf = tf if tf in TIMEFRAMES else "1m"
            try:
                body = json.dumps(cached_state(tf), allow_nan=False).encode()
            except ValueError as exc:
                note_state_error(exc)
                body = json.dumps({"ready": False, "stale": True,
                                   "errors": dict(FEED.errors), "server_time": iso(time.time())},
                                  allow_nan=False).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            clear_state_error()
            ctype = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()
    FEED.scalp_engine = ScalpEngine(HERE / "state")
    threading.Thread(target=run_scalp_clock, daemon=True).start()
    threading.Thread(target=run_scalp_settlement, daemon=True).start()
    FEED.tally_logger = TallyLogger(HERE / "state", FEED)
    threading.Thread(target=FEED.tally_logger.run, daemon=True).start()
    for name in VENUE_URLS:
        threading.Thread(target=FEED.run_venue, args=(name,), daemon=True).start()
    threading.Thread(target=FEED.run_upcoming, daemon=True).start()
    threading.Thread(target=FEED.run_market, daemon=True).start()
    threading.Thread(target=FEED.run_market_context, daemon=True).start()
    threading.Thread(target=FEED.run_composite, daemon=True).start()
    threading.Thread(target=FEED.run, daemon=True).start()
    for name in ("Coinbase", "Kraken"):
        threading.Thread(target=FEED.run_stream, args=(name,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BTC TA copilot (read-only, public data) at http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
