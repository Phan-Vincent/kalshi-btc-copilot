#!/usr/bin/env python3
"""Real-time BTC technical-analysis view for the Kalshi KXBTC15M series.

Paper only: no orders, no account access, no credentials. Every external
call is an unauthenticated GET to public endpoints (exchange tickers, Coinbase
candles, Kalshi public market data). The tally remains descriptive. The
experimental paper scalp trigger was retired on 2026-10-03. The Study 005
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
import sys
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

import sr_levels

from tally_report import parse_iso_ts, record_problem
from stream_flow import OrderFlow, parse_coinbase, parse_coinbase_spot, parse_kraken, MalformedMessage

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
# Candle history quality, per timeframe. A frame is `stale` when its newest
# fetched bar ended more than one bar plus HISTORY_SLACK seconds ago (the
# ticker can patch the forming bar but cannot stand in for missing history),
# and has a `gap` when bars are missing among its last HISTORY_GAP_BARS. The
# ticker may open at most TICKER_BAR_LIMIT bars past the newest fetched bar.
HISTORY_SLACK = 120
HISTORY_GAP_BARS = 60
TICKER_BAR_LIMIT = 3
# Seconds between candle refreshes per timeframe; the forming bar is also
# patched from the ticker on every spot poll.
CANDLE_REFRESH = {"1m": 10, "5m": 30, "15m": 60, "1h": 120}
SPOT_REFRESH = 2
KALSHI_REFRESH = 2
KALSHI_TRADES_LIMIT = 100
# Trades pages per context poll (2 s): enough to bridge a burst back to the
# trades already held, without unbounded requests in a busy window.
KALSHI_TRADE_PAGES = 4
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
# Tally families. The legacy bull/bear/lean count is kept unchanged; families
# give one vote each to trend, momentum and (1m only) order flow, so five
# readings that all measure trend no longer outvote everything else.
TREND_READINGS = ("EMA stack", "EMA 9/21", "EMA 200", "MACD")  # plus the VWAP reading
TREND_MAJORITY = 3            # trend family votes when 3+ of its 5 readings agree
FLOW_FAMILY_THRESHOLD = 0.20  # 60 s taker imbalance at or beyond +/-0.20 votes
FLOW_MIN_COVERAGE_SECONDS = 60  # flow votes only on a full 60 s of the current window
FAMILY_LEAN_MARGIN = 2        # family_lean needs |family_net| >= 2
PROXY_RECORD_WINDOWS = 200    # proxy-vs-settlement readout uses the latest 200 windows
PROXY_RECORD_MIN = 20         # below this, the readout says "too few to judge"
MAX_MEMORY_WINDOWS = 500      # settled windows kept in memory before archiving into running counters
MINUTE_CAPTURE_SLACK = 2.0    # a minute row is written only within 2 s of :00
KALSHI_PROMOTION_GRACE = 5
EXCHANGE_STATUS_REFRESH = 30  # seconds between /exchange/status probes when no market is listed
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


def derived_window_ticker(now):
    """Ticker named for the current slot's New York close."""
    slot = int(now // 900) * 900
    close = datetime.fromtimestamp(slot + 900, ZoneInfo("America/New_York"))
    month = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")[close.month - 1]
    return SERIES + "-" + close.strftime("%y") + month + close.strftime("%d%H%M-%M")


def parse_ts(text):
    return parse_iso_ts(text)


def validate_candles(bars, sec, now):
    """Raise ValueError if any bar can't be real OHLCV; the caller then keeps its last good history."""
    if not bars:
        raise ValueError("empty candle response")
    for b in bars:
        o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
        if min(o, h, lo, c) <= 0:
            raise ValueError(f"nonpositive price in candle {b['time']}")
        if not lo <= min(o, c) <= max(o, c) <= h:
            raise ValueError(f"inconsistent OHLC in candle {b['time']}")
        if b["volume"] < 0:
            raise ValueError(f"negative volume in candle {b['time']}")
        if b["time"] % sec or b["time"] > now + sec:
            raise ValueError(f"misaligned candle time {b['time']}")


def history_quality(bars, sec, fetched_last, now):
    """Freshness and continuity of one timeframe's candles (see HISTORY_SLACK)."""
    lag = None if fetched_last is None else max(0.0, now - fetched_last - sec)
    recent = bars[-HISTORY_GAP_BARS:]
    return {"lag_seconds": None if lag is None else round(lag, 1),
            "stale": lag is None or lag > sec + HISTORY_SLACK,
            "gap": any(b["time"] - a["time"] != sec for a, b in zip(recent, recent[1:]))
            or any(b.get("partial") for b in recent)}


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
                         "included": reason is None, "excluded_reason": reason,
                         "basis_usd": None}
        if reason is None:
            included.append(v["price"])
    composite = statistics.median(included) if included else None
    if composite is not None:
        for item in details.values():
            if item["price"] is not None:
                item["basis_usd"] = round(item["price"] - composite, 2)
    return composite, details


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
              "observed_seconds": 0, "missing_seconds": 0,
              "observed_mean": None, "required_remaining_avg": None,
              "required_remaining_move": None, "required_remaining_seconds": None,
              "required_drift_per_sec": None}
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
        # A second is due once it has ended. The sampler stamps second t just
        # after t begins, so the second in progress is never "missing", and an
        # early sample of it can't hide an older gap.
        expected = min(60, max(0, math.floor(now - start)))
        obs_sum = sum(observed.values())
        rem = 60 - len(observed)
        result["observed_seconds"] = len(observed)
        result["missing_seconds"] = sum(int(start + k) not in observed for k in range(expected))
        result["observed_mean"] = obs_sum / len(observed) if observed else None
        remaining = 60 - expected
        known_sum = sum(v for t, v in observed.items() if t < start + expected) + composite * result["missing_seconds"]
        if remaining >= 5:
            req = (60 * strike - known_sum) / remaining
            result["required_remaining_avg"] = req
            result["required_remaining_move"] = req - composite
            result["required_remaining_seconds"] = remaining
            result["required_drift_per_sec"] = (req - composite) / remaining
        reference = (obs_sum + composite * rem) / 60
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


def is_exchange_closed(status):
    """True when Kalshi's /exchange/status explicitly reports an inactive exchange or trading pause."""
    if not isinstance(status, dict):
        return False
    body = status.get("data") if isinstance(status.get("data"), dict) else status
    ea, ta_flag = body.get("exchange_active"), body.get("trading_active")
    return type(ea) is bool and type(ta_flag) is bool and (not ea or not ta_flag)


def is_exchange_open(status):
    if not isinstance(status, dict):
        return False
    body = status.get("data") if isinstance(status.get("data"), dict) else status
    return body.get("exchange_active") is True and body.get("trading_active") is True


def book_context(data):
    """Kalshi publishes YES and NO bids; the opposite bid is the ask."""
    book = data["orderbook_fp"]
    def ladder(side):
        levels = []
        for row in book.get(side, []):
            if len(row) != 2:
                continue
            price, count = positive_decimal(row[0]), positive_decimal(row[1])
            if price is not None and count is not None and price < 1:
                levels.append((price, count))
        levels.sort(key=lambda item: item[0], reverse=True)
        return levels[:5]
    yes_levels, no_levels = ladder("yes_dollars"), ladder("no_dollars")
    yes = yes_levels[0] if yes_levels else None
    no = no_levels[0] if no_levels else None
    yes_asks = [(Decimal(1) - p, c) for p, c in no_levels]
    no_asks = [(Decimal(1) - p, c) for p, c in yes_levels]
    yes_ask = yes_asks[0] if yes_asks else None
    no_ask = no_asks[0] if no_asks else None
    def show(level):
        return {"price": str(level[0]), "count": str(level[1])} if level else None
    mid = (yes[0] + yes_ask[0]) / 2 if yes and yes_ask else None
    spread = yes_ask[0] - yes[0] if yes and yes_ask else None
    def imbalance(a, b):
        total = a + b
        return float((a - b) / total) if total else None
    top_imbalance = imbalance(yes[1] if yes else 0, no[1] if no else 0)
    depth_imbalance = imbalance(sum(c for _, c in yes_levels), sum(c for _, c in no_levels))
    microprice = ((yes[0] * no[1] + yes_ask[0] * yes[1]) / (yes[1] + no[1])
                  if yes and no else None)
    return {"yes_bid": show(yes), "yes_ask": show(yes_ask),
            "no_bid": show(no), "no_ask": show(no_ask),
            "yes_bids": [show(l) for l in yes_levels],
            "yes_asks": [show(l) for l in yes_asks],
            "no_bids": [show(l) for l in no_levels],
            "no_asks": [show(l) for l in no_asks],
            "yes_spread": float(spread) if spread is not None else None,
            "yes_mid": float(mid) if mid is not None else None,
            "top_imbalance": top_imbalance, "depth_imbalance": depth_imbalance,
            "microprice": float(microprice) if microprice is not None else None,
            "micro_skew_cents": float((microprice - mid) * 100) if microprice is not None else None}


def kalshi_tape_summary(trades, now):
    """Observed public trades in this window; malformed rows contribute nothing."""
    totals = {"contracts": 0.0, "trade_count": 0, "yes_taker": 0.0, "no_taker": 0.0,
              "price_times_count": 0.0}
    recent = totals.copy()
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        try:
            at = parse_ts(trade["time"])
            count = float(trade["count"])
            price = float(trade["yes_price"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(at) and math.isfinite(count) and math.isfinite(price)
                and count > 0 and 0 < price < 1 and at <= now):
            continue
        buckets = (totals, recent) if now - at <= 60 else (totals,)
        if any(not (math.isfinite(bucket["contracts"] + count) and
                    math.isfinite(bucket["price_times_count"] + price * count))
               for bucket in buckets):
            continue
        for bucket in buckets:
            bucket["contracts"] += count
            bucket["trade_count"] += 1
            bucket["price_times_count"] += price * count
            if trade.get("taker_side") == "yes":
                bucket["yes_taker"] += count
            elif trade.get("taker_side") == "no":
                bucket["no_taker"] += count
    def show(bucket):
        side_total = bucket["yes_taker"] + bucket["no_taker"]
        return {"contracts": bucket["contracts"], "trade_count": bucket["trade_count"],
                "taker_imbalance": ((bucket["yes_taker"] - bucket["no_taker"]) / side_total
                                    if side_total else None),
                "yes_vwap": (bucket["price_times_count"] / bucket["contracts"]
                             if bucket["contracts"] else None)}
    return {"last_60s": show(recent), "window": show(totals)}


def kalshi_trades(data, ticker, open_ts, close_ts):
    return [{k: v for k, v in row.items() if k != "trade_id"}
            for row in kalshi_trade_rows(data, ticker, open_ts, close_ts)[:30]]


def tape_reach(tape):
    """Newest time up to which the tape holds every trade, or None."""
    if not tape or tape["from"] is None:
        return None
    return max((parse_ts(row["time"]) for row in tape["rows"].values()), default=tape["to"])


def fetch_trades(ticker, opened, reach):
    """Newest trades since open, paging back with the cursor until a page
    reaches `reach`, ends the window's trades, or KALSHI_TRADE_PAGES is hit.
    Returns the raw trades and whether older ones may be left unfetched."""
    trades, cursor = [], None
    for _ in range(KALSHI_TRADE_PAGES):
        params = {"ticker": ticker, "min_ts": int(opened), "limit": KALSHI_TRADES_LIMIT}
        if cursor:
            params["cursor"] = cursor
        data = get_json(f"{KALSHI}/markets/trades", params)
        page = data.get("trades") or []
        trades += page
        cursor = data.get("cursor")
        if len(page) < KALSHI_TRADES_LIMIT or not isinstance(cursor, str) or not cursor:
            return trades, False
        times = []
        for trade in page:
            try:
                times.append(parse_ts(trade["created_time"]))
            except (KeyError, TypeError, ValueError):
                pass
        if reach is not None and times and min(times) <= reach:
            return trades, True
    return trades, True


def merge_tape(tape, rows, full, open_ts, fetched_at):
    """Fold one trades fetch (see fetch_trades) into the window's tape. A fetch
    that ran out of trades holds every trade since open. Otherwise it reaches
    back only to its oldest row; if the tape doesn't reach that far, trades in
    between were missed and the tape restarts there."""
    held = dict(tape["rows"]) if tape else {}
    covered_from = tape["from"] if tape else None
    rows = [row for row in rows if row["trade_id"]]
    if not full:
        covered_from = open_ts
    elif rows:
        oldest = min(parse_ts(row["time"]) for row in rows)
        reach = tape_reach(tape)
        if reach is None or oldest > reach:
            held, covered_from = {}, oldest
    else:
        return tape
    held.update((row["trade_id"], row) for row in rows)
    return {"rows": held, "from": covered_from, "to": fetched_at}


def kalshi_trade_rows(data, ticker, open_ts, close_ts):
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
        trade_id = trade.get("trade_id")
        rows.append({"time": trade["created_time"], "yes_price": str(price),
                     "count": str(count), "taker_side": trade.get("taker_side"),
                     "is_block_trade": bool(trade.get("is_block_trade")),
                     "trade_id": trade_id if isinstance(trade_id, str) else None})
    rows.sort(key=lambda row: row["time"], reverse=True)
    return rows


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
    points = sr_levels.pivot_points(bars, k)
    return ([p for _, p, side in points if side == "high"],
            [p for _, p, side in points if side == "low"])


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
    trailing_atr = [x for x in a if x is not None][-60:]
    median_atr = statistics.median(trailing_atr) if len(trailing_atr) == 60 else None
    atr_ratio = trailing_atr[-1] / median_atr if median_atr and median_atr > 0 else None
    st_k, st_d = stochastic(highs, lows, closes)
    vw = anchored_vwap(bars, anchor)
    sw_hi, sw_lo = pivots(bars)

    v = {"ema9": last(e9), "ema21": last(e21), "ema50": last(e50), "ema200": last(e200),
         "rsi": last(r), "macd": last(m_line), "macd_signal": last(m_sig), "macd_hist": last(m_hist),
         "bb_mid": last(bb_mid), "bb_upper": last(bb_up), "bb_lower": last(bb_lo), "atr": last(a),
         "atr_ratio": atr_ratio,
         "vol_regime": ("expanding" if atr_ratio >= 1.3 else "compressed" if atr_ratio <= 0.7 else "normal")
         if atr_ratio is not None else None,
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


def reading_family(reading):
    """Family a reading votes in: trend, momentum, or context (never counted)."""
    name = reading["name"]
    if name in TREND_READINGS or name.startswith("VWAP"):
        return "trend"
    if name == "RSI 14" and "stretched" not in reading["detail"]:
        return "momentum"
    return "context"


def flow_family_vote(flow_imbalance, flow_coverage=None):
    """The 1m flow vote, or neutral until the window has a full 60 s of flow.

    The tracker resets at each 15-minute window open, so its "60 s" imbalance
    is only a 60 s sample once that much of the window has elapsed;
    ``flow_coverage`` is those elapsed seconds.
    """
    if (flow_imbalance is None or flow_coverage is None
            or flow_coverage < FLOW_MIN_COVERAGE_SECONDS):
        return "neutral"
    return ("bull" if flow_imbalance >= FLOW_FAMILY_THRESHOLD
            else "bear" if flow_imbalance <= -FLOW_FAMILY_THRESHOLD else "neutral")


def family_tally(readings, flow_imbalance=None, with_flow=False, flow_coverage=None):
    """One vote per family; the legacy tally is left untouched.

    Trend votes with the side that has TREND_MAJORITY or more of its readings.
    Momentum is RSI 14 inside its 30-70 range (stretched RSI is context).
    Flow, on 1m only, is the 60 s taker imbalance against FLOW_FAMILY_THRESHOLD,
    once FLOW_MIN_COVERAGE_SECONDS of the current window have passed; no flow
    data, and an under-covered window, both count as neutral.
    """
    trend = [r["state"] for r in readings if reading_family(r) == "trend"]
    votes = {"trend": ("bull" if trend.count("bull") >= TREND_MAJORITY
                       else "bear" if trend.count("bear") >= TREND_MAJORITY else "neutral")}
    rsi_reading = next((r for r in readings if reading_family(r) == "momentum"), None)
    votes["momentum"] = rsi_reading["state"] if rsi_reading else "neutral"
    if with_flow:
        votes["flow"] = flow_family_vote(flow_imbalance, flow_coverage)
    net = sum(v == "bull" for v in votes.values()) - sum(v == "bear" for v in votes.values())
    family = {"votes": votes, "net": net,
              "lean": ("bullish" if net >= FAMILY_LEAN_MARGIN
                       else "bearish" if net <= -FAMILY_LEAN_MARGIN else "mixed")}
    if with_flow:
        covered = 0.0 if flow_coverage is None else flow_coverage
        family["flow_coverage_seconds"] = min(covered, FLOW_MIN_COVERAGE_SECONDS)
        family["flow_ready"] = covered >= FLOW_MIN_COVERAGE_SECONDS
    return family


def frame_family(result, name, flow_imbalance, flow_coverage=None):
    for reading in result["readings"]:
        reading["family"] = reading_family(reading)
    result["family"] = family_tally(result["readings"], flow_imbalance,
                                    with_flow=name == "1m", flow_coverage=flow_coverage)
    return result


def aggregate(bars, width, source=None):
    """Merge sorted bars into `width`-second bars aligned to the epoch, dropping a
    leading group that doesn't start on the boundary (it would be partial).

    With ``source`` (the input bar width), a bucket missing any of its source
    bars is marked ``"partial": True``; the last bucket is exempt while its
    missing bars are still in the future (it is the forming bar). The gap check
    in ``history_quality`` counts partial bars.
    """
    groups = {}
    for b in bars:
        groups.setdefault(b["time"] // width * width, []).append(b)
    out = []
    last_source = bars[-1]["time"] if bars else None
    for start, g in sorted(groups.items()):
        if not out and g[0]["time"] != start:
            continue
        bar = {"time": start, "open": g[0]["open"], "close": g[-1]["close"],
               "high": max(b["high"] for b in g), "low": min(b["low"] for b in g),
               "volume": sum(b["volume"] for b in g)}
        if source:
            expected = {t for t in range(start, start + width, source) if t <= last_source}
            if not expected <= {b["time"] for b in g}:
                bar["partial"] = True
        out.append(bar)
    return out


# ------------------------------------------------------------------ collector

class Feed:
    def __init__(self):
        self.lock = threading.Lock()
        self.bars = {tf: [] for tf in TIMEFRAMES}
        self.bars_at = {tf: 0.0 for tf in TIMEFRAMES}
        self.fetched_last = {tf: None for tf in TIMEFRAMES}  # newest fetched bar's start
        self.spot = None
        self.spot_at = 0.0
        self.market = None
        self.market_at = 0.0
        self.market_discovery_source = None
        self.last_market_ticker = None
        # A new window's first metadata comes from the 30 s listing prefetch.
        # `market_promoted_at` starts the short stale grace, `market_wake` lets
        # the sampler ask the market loop for a direct per-ticker refresh now,
        # and `direct_ticker`/`direct_at` keep those GETs to one per 2 s.
        self.market_promoted_at = 0.0
        self.market_wake = threading.Event()
        self.direct_ticker = None
        self.direct_at = 0.0
        self.exchange_closed = False
        self.exchange_status_at = 0.0
        self.exchange_closed_probe = None
        self.exchange_open_probe = False
        self.upcoming_markets = {}
        self.upcoming_at = 0.0
        self.captured_tickers = set()
        self.errors = {}
        self.venues = {name: None for name in ("Coinbase", *VENUE_URLS)}
        self.composite = None
        self.composite_at = 0.0
        self.composite_details = {}
        self.samples = deque(maxlen=60)
        self.close_boundary = None  # last 15-minute boundary the sampler saw
        self.average_60s = None
        self.tally_logger = None
        self.feed_modes = {"Coinbase": "REST fallback", "Kraken": "REST fallback"}
        self.socket_connected = {"Coinbase": False, "Kraken": False}
        self.stream_last_at = {"Coinbase": 0.0, "Kraken": 0.0}
        # Receipt time of the last message vs time of the newest trade seen:
        # a replayed or out-of-order frame is activity, not fresh data.
        self.stream_trade_at = {"Coinbase": 0.0, "Kraken": 0.0}
        self.flow = OrderFlow(large_trade_usd=LARGE_TRADE_USD)
        self.flow_gap_window = int(time.time() // 900) * 900
        self.window_path = deque(maxlen=930)
        self.window_path_open = None
        self.book = None
        self.book_at = 0.0
        self.kalshi_recent_trades = []
        # Every public trade seen in the context window, with the time from
        # which it is known to be complete (see merge_tape).
        self.kalshi_tape = None
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
            end -= 299 * sec  # the next page ends where this one starts: no bar falls between them
        bars = sorted(found.values(), key=lambda b: b["time"])
        validate_candles(bars, sec, time.time())
        derived = {name: aggregate(bars, width, sec) for name, (src, width) in DERIVED.items() if src == tf}
        with self.lock:
            now = time.time()
            held = self.fetched_last[tf]
            if held is not None and bars[-1]["time"] < held:
                raise ValueError("candle response older than held history")
            self.bars[tf], self.bars_at[tf], self.fetched_last[tf] = bars[-MAX_BARS:], now, bars[-1]["time"]
            for name, agg in derived.items():
                self.bars[name], self.bars_at[name] = agg[-MAX_BARS:], now
                self.fetched_last[name] = agg[-1]["time"] if agg else None
            self.errors.pop(f"candles_{tf}", None)

    def poll_spot(self):
        t = get_json(f"{COINBASE}/ticker")
        fetched_at = time.time()
        spot = parse_coinbase_spot(t, "BTC-USD", fetched_at)
        venue = venue_quote("Coinbase", t, fetched_at)
        self.update_spot(spot.price, spot.bid, spot.ask, t.get("time"), venue, fetched_at)

    def update_spot(self, price, bid, ask, stamp, venue, fetched_at):
        price = finite_float(price)
        bid = optional_float(bid)
        ask = optional_float(ask)
        if price <= 0 or (bid is not None and bid <= 0) or (ask is not None and ask <= 0):
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
                elif bars[-1]["time"] < start and start - (self.fetched_last[tf] or start) <= TICKER_BAR_LIMIT * sec:
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
            now = time.time()
            return (self.socket_connected[name] and now - self.stream_last_at[name] < 10
                    and now - self.stream_trade_at[name] <= VENUE_MAX_AGE)

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
            self.stream_last_at["Kraken"] = now
            for trade in trades:
                if trade.timestamp > now + 2 or now - trade.timestamp > 20:
                    continue
                self.flow.add_trade(trade)
                self.stream_trade_at["Kraken"] = max(self.stream_trade_at["Kraken"], trade.timestamp)
                current = self.venues.get("Kraken")
                if current and trade.timestamp < current["at"]:
                    continue  # replayed or out of order: never move the quote back
                self.venues["Kraken"] = {"price": trade.price, "at": trade.timestamp, "age_basis": "trade"}
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
        close_row = None
        with self.lock:
            value, details = composite_quote(self.venues, at)
            self.composite, self.composite_details = value, details
            self.composite_at = at if value is not None else 0.0
            boundary = int(at // 900) * 900
            if self.close_boundary is not None and boundary > self.close_boundary:
                close_row = self._close_proxy(boundary, at)
            self.close_boundary = boundary
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
                    self.market_discovery_source = "prefetch"
            for market in self.upcoming_markets.values():
                try:
                    delay = at - parse_ts(market["open_time"])
                    if 0 <= delay < 3:
                        self._capture_open(market, at)
                except Exception as exc:
                    self._note("tally_log", exc)
        if close_row and self.tally_logger:
            self.tally_logger.submit(close_row)

    def _close_proxy(self, boundary, at):
        """Our 60 s composite mean over the settlement minute B-60..B-1, or None.

        Called on the first sample at or after boundary B, before that sample
        is appended, so the deque still holds the closing minute.
        """
        markets = ([self.market] if self.market else []) + list(self.upcoming_markets.values())
        market = next((m for m in markets if parse_ts(m["close_time"]) == boundary), None)
        if market is None:
            return None
        prices = [price for t, price in self.samples if boundary - 60 <= t < boundary and price is not None]
        return {"type": "close", "ticker": market["ticker"], "close_time": market["close_time"],
                "proxy_avg_60s": statistics.fmean(prices) if prices else None,
                "samples": len(prices), "captured_at": iso(at)}

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
                    "flow_imbalance": self.flow.rolling(at).get("imbalance"),
                    "flow_coverage": (min(FLOW_MIN_COVERAGE_SECONDS,
                                          max(0.0, at - self.flow.window_open))
                                      if self.flow.window_open is not None else None),
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
        discovery_source = "listing" if selected else None
        refreshed = selected is not None
        from_prefetch = False
        if not selected:
            known_open = False
            with self.lock:
                upcoming = [dict(m) for m in self.upcoming_markets.values()
                            if parse_ts(m["open_time"]) <= now < parse_ts(m["close_time"])]
                upcoming_at = self.upcoming_at
            upcoming.sort(key=lambda m: m["close_time"])
            prefetched = upcoming[0] if upcoming else None
            if current and (not prefetched or current_at >= upcoming_at):
                selected, selected_at = current, current_at
                discovery_source = "cached"
            else:
                selected, selected_at = prefetched, upcoming_at
                discovery_source = "prefetch" if selected else None
                refreshed = bool(selected and upcoming_at > current_at)
                from_prefetch = selected is not None
        if selected and direct and direct["ticker"] == selected["ticker"]:
            selected, selected_at = direct, direct_at
            refreshed = True
            from_prefetch = False
            discovery_source = "direct"
        elif selected and not live:
            try:
                detail, fetched_at = self.direct_market(selected["ticker"], now)
                if detail is not None:
                    selected, selected_at = detail, fetched_at
                    refreshed = True
                    from_prefetch = False
                    discovery_source = "direct"
            except Exception as exc:
                direct_error = exc
        closed = False
        if not selected:
            with self.lock:
                cached_closed = self.exchange_closed_probe
                cached_open = self.exchange_open_probe
                status_age = now - self.exchange_status_at if self.exchange_status_at else float("inf")
            if 0 <= status_age < EXCHANGE_STATUS_REFRESH:
                closed = cached_closed is True
                known_open = cached_open is True
            elif not listing_error:
                try:
                    status = get_json(f"{KALSHI}/exchange/status")
                    closed = is_exchange_closed(status)
                    known_open = is_exchange_open(status)
                except Exception:
                    closed = False
                    known_open = False
                with self.lock:
                    self.exchange_status_at = now
                    self.exchange_closed_probe = closed
                    self.exchange_open_probe = known_open
            if known_open:
                slot = int(now // 900) * 900
                ticker = derived_window_ticker(now)
                try:
                    detail, fetched_at = self.direct_market(ticker, now)
                    if (detail and detail["ticker"] == ticker and
                            parse_ts(detail["open_time"]) == slot and
                            parse_ts(detail["close_time"]) == slot + 900):
                        selected, selected_at = detail, fetched_at
                        refreshed = True
                        discovery_source = "derived"
                except Exception as exc:
                    direct_error = exc
        with self.lock:
            old_ticker = self.last_market_ticker
            self.market, self.market_at = selected, selected_at
            self.market_discovery_source = discovery_source
            self.exchange_closed = bool(not self.market and closed)
            if self.market and self.market["ticker"] != old_ticker:
                if from_prefetch:
                    if not (current and current["ticker"] == selected["ticker"]
                            and self.market_promoted_at):
                        self.market_promoted_at = now
                elif refreshed:
                    self.market_promoted_at = 0.0
            if direct_error or listing_error:
                self._note("kalshi", direct_error or listing_error)
            elif refreshed or self.exchange_closed:
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
                self.kalshi_tape = None
                self.yes_mid_path.clear()
            return
        ticker = market["ticker"]
        opened, closed = parse_ts(market["open_time"]), parse_ts(market["close_time"])
        book = book_context(get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}/orderbook",
                                     {"depth": 5}))
        book_observed_at = time.time()
        # Store the book before the trades GET: a failed trades call must not
        # throw away a fresh orderbook, which refreshes on its own 2 s cadence.
        with self.lock:
            if not self.market or self.market["ticker"] != ticker:
                return
            if self.context_ticker != ticker:
                self.context_ticker = ticker
                self.kalshi_tape = None
                self.yes_mid_path.clear()
            self.book, self.book_at = book, book_observed_at
            if book["yes_mid"] is not None:
                self.yes_mid_path.append({"time": int(book_observed_at), "value": book["yes_mid"]})
        with self.lock:
            reach = tape_reach(self.kalshi_tape)
        raw, full = fetch_trades(ticker, opened, reach)
        fetched_at = time.time()
        rows = kalshi_trade_rows({"trades": raw}, ticker, opened, closed)
        trades = [{k: v for k, v in row.items() if k != "trade_id"} for row in rows[:30]]
        with self.lock:
            if not self.market or self.market["ticker"] != ticker or self.context_ticker != ticker:
                return
            self.kalshi_recent_trades = trades
            self.kalshi_tape = merge_tape(self.kalshi_tape, rows, full, opened, fetched_at)
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

    def run_spot(self):
        """REST spot fallback, on its own thread so slow candle requests can't delay it."""
        next_at = 0.0
        while True:
            wait = next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_at = time.monotonic() + SPOT_REFRESH
            if self.stream_active("Coinbase"):
                continue
            try:
                self.poll_spot()
            except Exception as exc:
                with self.lock:
                    self._note("spot", exc)

    def run(self):
        """Candle refreshes. Spot fallback is run_spot."""
        due = {}
        while True:
            now = time.time()
            jobs = [(f"candles_{tf}", CANDLE_REFRESH[tf], lambda tf=tf: self.poll_candles(tf))
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
            fetched_last = dict(self.fetched_last)
            spot, spot_at = dict(self.spot) if self.spot else None, self.spot_at
            market, market_at = self.market, self.market_at
            discovery_source = self.market_discovery_source
            promoted_at = self.market_promoted_at
            exchange_closed = self.exchange_closed
            errors = dict(self.errors)
            composite, composite_at = self.composite, self.composite_at
            average_60s = self.average_60s
            fresh_composite, venues = composite_quote(self.venues, now)
            modes = {"Bitstamp": "REST", "Gemini": "REST"}
            modes.update({name: ("WebSocket" if self.socket_connected[name] and
                                 now - self.stream_last_at[name] < 10 and
                                 now - self.stream_trade_at[name] <= VENUE_MAX_AGE
                                 else "REST fallback")
                          for name in self.feed_modes})
            flow = self.flow.snapshot(now)
            flow["partial_window"] = (flow["partial_window"] or
                                       self.flow_gap_window == flow["window"]["open_epoch"])
            # The flow tracker resets at each 15-minute boundary, so its "60 s"
            # imbalance only covers the elapsed part of this window until the
            # first minute has passed.
            flow_open = flow["window"].get("open_epoch")
            flow_coverage = (min(FLOW_MIN_COVERAGE_SECONDS, max(0.0, now - flow_open))
                             if flow_open is not None else None)
            path = list(self.window_path)
            book = dict(self.book) if self.book else None
            book_at = self.book_at
            context_ticker = self.context_ticker
            recent_trades = list(self.kalshi_recent_trades)
            tape_rows = list(self.kalshi_tape["rows"].values()) if self.kalshi_tape else []
            tape_from = self.kalshi_tape["from"] if self.kalshi_tape else None
            mid_path = list(self.yes_mid_path)
        if not spot or not bars["1m"]:
            return {"ready": False, "stale": True, "kalshi_stale": True,
                    "exchange_closed": False, "errors": errors, "server_time": iso(now)}
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
                      "discovery_source": discovery_source,
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
            if context_ticker != market["ticker"]:
                tape_rows, tape_from = [], None
            window["tape_summary"] = {**kalshi_tape_summary(tape_rows, now),
                                      "covered_from": iso(tape_from) if tape_from is not None else None,
                                      "complete_window": tape_from is not None and tape_from <= open_ts,
                                      "complete_60s": tape_from is not None and tape_from <= now - 60}
            window["yes_mid_path"] = mid_path if context_ticker == market["ticker"] else []
            sig = realized_sigma([b["close"] for b in bars["1m"]])
            window.update(window_distance(strike, composite, window["seconds_left"], sig,
                                          close_ts, window_points))

        frames, history = {}, {}
        for name, b in bars.items():
            history[name] = history_quality(b, TIMEFRAMES[name], fetched_last[name], now)
        # A derived frame is never fresher than the frame it aggregates.
        for name, (src, _) in DERIVED.items():
            own, source = history[name], history[src]
            own["stale"] = own["stale"] or source["stale"]
            own["gap"] = own["gap"] or source["gap"]
            own["lag_seconds"] = (None if own["lag_seconds"] is None or source["lag_seconds"] is None
                                  else max(own["lag_seconds"], source["lag_seconds"]))
        ref = composite if composite is not None else price
        for name, b in bars.items():
            if len(b) < 30:
                continue
            a = (analyze(b, price, anchor, "VWAP (window)") if name == "1m"
                 else analyze(b, price, b[0]["time"], "VWAP (loaded range)"))
            if name == "1m":
                a["values"]["close"] = b[-1]["close"]
                a["values"]["close_prev"] = b[-2]["close"] if len(b) > 1 else None
            frame_family(a, name, (flow.get("one_minute") or {}).get("imbalance"), flow_coverage)
            a["levels"] = sr_levels.levels(name, bars, history, ref, a["values"]["atr"],
                                          vwap=a["values"]["vwap"], target=window["strike"] if window else None,
                                          target_price=ref, window_open=window["open_price"] if window and not window["path_partial"] else None,
                                          now=now, price_basis="composite" if composite is not None else "spot")
            a["history"] = history[name]
            frames[name] = a if name == tf else {k: a[k] for k in ("values", "readings", "tally", "family", "history", "levels")}
        m1 = bars["1m"]

        def ref(minutes):
            target = int(now // 60) * 60 - minutes * 60
            return next((b["open"] for b in m1 if b["time"] == target), None)

        grace_left = max(0.0, promoted_at + KALSHI_PROMOTION_GRACE - now) if promoted_at else 0.0
        return {"ready": True, "server_time": iso(now), "timeframe": tf,
                "ref_15m": ref(15), "ref_1h": ref(60),
                "spot": spot, "spot_age": max(now - spot_at, now - parse_ts(spot["time"])),
                "composite": composite, "composite_age": now - composite_at if composite_at else None,
                "average_60s": average_60s, "venues": venues,
                "feed_modes": modes, "order_flow": flow,
                "candle_age": {k: now - v for k, v in self.bars_at.items()},
                # Data quality per timeframe, apart from fetch success: stale
                # or gapped history still yields readings, so it is flagged.
                "history": history,
                "history_stale": [k for k in TIMEFRAMES if history[k]["stale"]],
                "frames_unavailable": [k for k in TIMEFRAMES if k not in frames],
                "watcher_age": watcher_age(now),
                "stale": now - spot_at > STALE_AFTER or now - parse_ts(spot["time"]) > VENUE_MAX_AGE
                or composite is None,
                # Freshness of the displayed window: time since the last
                # successful Kalshi fetch that carried it, with a short grace
                # for a just-promoted window whose only data is the prefetch.
                # `window["age"]` stays the age of that metadata.
                "exchange_closed": bool(window is None and exchange_closed),
                "kalshi_stale": False if (window is None and exchange_closed) else (
                    window is None or (window["age"] > KALSHI_STALE_AFTER and grace_left <= 0)),
                "kalshi_grace_left": grace_left,
                "window": window, "frames": frames,
                "tally_record": self.tally_logger.record if self.tally_logger else None,
                "proxy_record": self.tally_logger.proxy if self.tally_logger else None,
                "errors": errors, "vwap_anchor": iso(anchor)}


def opening_record(snapshot):
    market = snapshot["market"]
    frames = {}
    price = snapshot["coinbase_spot"]
    # Capture lands within 3 s of the window open, so the 1m flow has well
    # under 60 s of coverage and its vote is always neutral. The measured
    # imbalance is still recorded below as context.
    coverage = snapshot.get("flow_coverage") or 0.0
    if price is not None:
        for tf, bars in snapshot["bars"].items():
            if len(bars) < 30:
                continue
            anchor = int(parse_ts(market["open_time"])) if tf == "1m" else bars[0]["time"]
            result = frame_family(analyze(bars, price, anchor), tf,
                                  snapshot.get("flow_imbalance"), 0)
            frames[tf] = {**result["tally"], "rsi": result["values"]["rsi"],
                          "macd_hist": result["values"]["macd_hist"],
                          "family": result["family"]["votes"], "family_net": result["family"]["net"],
                          "family_lean": result["family"]["lean"]}
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
            "yes_ask": market.get("yes_ask_dollars"),
            "flow_imbalance_1m": snapshot.get("flow_imbalance"),
            "flow_coverage_seconds": coverage,
            "sigma_1m_usd": opening_sigma_usd(snapshot)}


def opening_sigma_usd(snapshot):
    bars, price = snapshot["bars"].get("1m") or [], snapshot["coinbase_spot"]
    sigma = realized_sigma([b["close"] for b in bars]) if bars else None
    return sigma * price if sigma is not None and price is not None else None


def minute_record(state, now):
    """One in-window research row per whole minute (minutes 1-14), or None.

    Outcomes are not recorded here; the report derives them from later rows.
    """
    window = state.get("window") if state.get("ready") else None
    if not window:
        return None
    open_ts = parse_ts(window["open_time"])
    minute = int((now - open_ts) // 60)
    if not 1 <= minute <= 14 or now - (open_ts + minute * 60) > MINUTE_CAPTURE_SLACK:
        return None

    def quote(side):
        q = (window.get("display_quotes") or {}).get(side)
        if state.get("kalshi_stale") or not q or q.get("price") is None or q["age"] > KALSHI_STALE_AFTER:
            return None
        price = optional_float(q["price"])  # book levels arrive as decimal strings
        return None if price is None else {"price": price, "age": round(q["age"], 2), "source": q["source"]}

    def compact_levels(data):
        if data is None:
            return None
        support = next(iter(data.get("support") or []), {})
        resistance = next(iter(data.get("resistance") or []), {})
        target = data.get("target")
        out = {"support": support.get("mid"), "support_touches": support.get("touches"),
               "support_dist_atr": support.get("distance_atr"), "resistance": resistance.get("mid"),
               "resistance_touches": resistance.get("touches"),
               "resistance_dist_atr": resistance.get("distance_atr"), "at_price": bool(data.get("at_price")),
               "zones_to_target": target.get("count") if target is not None else None,
               "target_in_zone": bool(target.get("in_zone")) if target is not None else None}
        return {k: v if isinstance(v, (int, float)) and math.isfinite(v) else None for k, v in out.items()}

    frames = {}
    for tf, frame in (state.get("frames") or {}).items():
        family = frame.get("family") or {}
        frames[tf] = {**frame["tally"], "rsi": frame["values"].get("rsi"),
                      "macd_hist": frame["values"].get("macd_hist"),
                      "family": family.get("votes"), "family_net": family.get("net"),
                      "family_lean": family.get("lean"), "levels": compact_levels(frame.get("levels"))}
    return {"type": "minute", "ticker": window["ticker"], "minute": minute,
            "captured_at": iso(now), "seconds_left": round(window["seconds_left"], 2),
            "composite": state.get("composite"), "average_60s": state.get("average_60s"),
            "target": window.get("strike"), "sigma_1m_usd": window.get("sigma_1m_usd"),
            "yes_bid": quote("yes_bid"), "yes_ask": quote("yes_ask"),
            "flow_imbalance_1m": ((state.get("order_flow") or {}).get("one_minute") or {}).get("imbalance"),
            "stale": state.get("stale"), "kalshi_stale": state.get("kalshi_stale"),
            "history_stale": state.get("history_stale"),
            "timeframes": frames}


# Kalshi can publish `result` before `expiration_value`. Readers keep the first
# settled row per ticker, so a row written without the value would hide it for
# good: hold the row until the value appears or this long after close.
SETTLE_VALUE_WAIT = 30 * 60
# If the value still comes later, a `settled_value` row records it (readers
# merge it into the settled row); asked for until this long after close.
SETTLE_VALUE_RETRY = 24 * 3600


def settled_record(ticker, market, now):
    if market.get("result") not in ("yes", "no"):
        return None
    record = {"type": "settled", "ticker": ticker, "recorded_at": iso(now),
              "result": market["result"]}
    # Kalshi sends "" for a field it has not populated yet; that is awaited, not a value.
    if optional_float(market.get("expiration_value")) is not None:
        record["expiration_value"] = market["expiration_value"]
    return record


def proxy_summary(errors):
    """Median and 90th-percentile absolute error and mean signed error, in dollars."""
    if not errors:
        return {"n": 0, "median_abs": None, "p90_abs": None, "bias": None, "too_few": True}
    absolute = sorted(abs(e) for e in errors)
    p90 = absolute[min(len(absolute) - 1, math.ceil(0.9 * len(absolute)) - 1)]
    return {"n": len(errors), "median_abs": statistics.median(absolute), "p90_abs": p90,
            "bias": statistics.fmean(errors), "too_few": len(errors) < PROXY_RECORD_MIN}


def wilson95(hits, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


class TallyLogger:
    """Append opening/settlement research records off the serving thread."""
    def __init__(self, state_dir, feed):
        self.path = state_dir / "tally_log.jsonl"
        self.pending_path = state_dir / "tally_pending.json"
        self.feed = feed
        self.queue = queue.Queue(maxsize=32)
        self.append_lock = threading.Lock()
        self.opened = {}
        self.settled = set()
        self.results = {}
        self.pending_open = {}
        self.pending_snapshots = {}
        self.pending_closes = {}
        self.next_settle = self.next_hydrate = 0
        self.minute_captured = None
        self.skipped_lines = 0
        self.closes = {}
        self.expirations = {}
        self.value_pending = {}  # settled without expiration_value: ticker -> close epoch
        self._archived_record = {"hits": 0, "scored": 0}
        self._archived_coverage = {"total_closes": 0, "scored": 0, "incomplete_samples": 0,
                                   "missing_settlement": 0, "invalid_proxy": 0}
        # Replaced, never mutated, so request threads can read them lock-free.
        self.record = None
        self.proxy = None

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
        skipped, late_values = [], {}
        self.opened.clear()
        self.settled.clear()
        self.results.clear()
        self.closes.clear()
        self.expirations.clear()
        self.value_pending.clear()
        self.skipped_lines = 0
        self._archived_record = {"hits": 0, "scored": 0}
        self._archived_coverage = {"total_closes": 0, "scored": 0, "incomplete_samples": 0,
                                   "missing_settlement": 0, "invalid_proxy": 0}
        try:
            with self.path.open("rb") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        skipped.append(number)
                        continue
                    problem = record_problem(row)
                    if problem == "unknown type":
                        continue
                    if problem:
                        skipped.append(number)
                        continue
                    if row["type"] == "open":
                        self.opened.setdefault(row["ticker"], row)
                    elif row["type"] == "settled":
                        if row["ticker"] not in self.settled:
                            self.settled.add(row["ticker"])
                            self.results[row["ticker"]] = row.get("result")
                            self.expirations[row["ticker"]] = row.get("expiration_value")
                    elif row["type"] == "close":
                        self.closes.setdefault(row["ticker"], row)
                    elif row["type"] == "settled_value":
                        late_values.setdefault(row["ticker"], row["expiration_value"])
        except FileNotFoundError:
            pass
        for ticker, value in late_values.items():
            if ticker in self.settled and self.expirations.get(ticker) is None:
                self.expirations[ticker] = value
        now = time.time()
        for ticker in self.settled:
            row = self.opened.get(ticker) or self.closes.get(ticker)
            if self.expirations.get(ticker) is None and row and row.get("close_time"):
                close = parse_ts(row["close_time"])
                if now - close <= SETTLE_VALUE_RETRY:
                    self.value_pending[ticker] = close
        self.skipped_lines = len(skipped)
        if skipped:
            print(f"Tally log: skipped {len(skipped)} unreadable lines "
                  f"(first line numbers: {', '.join(map(str, skipped[:5]))}).", file=sys.stderr)
        self.refresh_record()
        self.refresh_proxy()
        self.prune_memory()
        if self.path.exists():
            os.chmod(self.path, 0o600)
        try:
            rows = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = None  # corrupt or unreadable: quarantined below, never fatal
        if isinstance(rows, dict):
            self.pending_open = {ticker: row for ticker, row in rows.items()
                                 if ticker not in self.opened and isinstance(row, dict)}
            os.chmod(self.pending_path, 0o600)
        elif self.pending_path.exists():
            # A corrupt queue would fail every prepare, so it is set aside, not kept.
            self.pending_open = {}
            try:
                os.replace(self.pending_path, self.pending_path.with_suffix(f".corrupt-{int(time.time())}"))
            except OSError:
                pass
            print("[BTC TA] tally pending unreadable; set aside and reset", file=sys.stderr)

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
        failed = False  # one clear at the end of a clean pass, so a stale alarm can't stick
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
            except Exception as exc:
                failed = True
                with self.feed.lock:
                    self.feed._note("tally_open", exc)
        # A logger built without __init__ (tests) has no alarm store to clear.
        if not failed and getattr(self, "feed", None):
            with self.feed.lock:
                self.feed.errors.pop("tally_open", None)

    def append(self, row):
        payload = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
        with self.append_lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":
                    payload = b"\n" + payload
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("short tally log write")
                    remaining = remaining[written:]
            finally:
                os.close(fd)

    def settle_pending(self, now):
        failed = False  # one clear at the end of a clean pass, so a stale alarm can't stick
        # Windows with only a close row (the server started mid-window) still
        # need their settlement to score the closing-minute proxy.
        for ticker, row in list({**self.closes, **self.opened}.items()):
            if ticker in self.settled or not row.get("close_time"):
                continue
            try:
                if parse_ts(row["close_time"]) > now:
                    continue
                market = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")["market"]
                record = settled_record(ticker, market, now)
                if record is None:
                    continue
                if "expiration_value" not in record and now - parse_ts(row["close_time"]) < SETTLE_VALUE_WAIT:
                    continue  # result is in, value not yet: ask again next pass
                self.append(record)
                self.settled.add(ticker)
                self.results[ticker] = record["result"]
                self.expirations[ticker] = record.get("expiration_value")
                if record.get("expiration_value") is None:
                    self.value_pending[ticker] = parse_ts(row["close_time"])
                self.refresh_record()
                self.refresh_proxy()
            except Exception as exc:
                failed = True
                with self.feed.lock:
                    self.feed._note("tally_settle", exc)
        for ticker, close in list(self.value_pending.items()):
            if now - close > SETTLE_VALUE_RETRY:
                del self.value_pending[ticker]  # give up; the settled row stands without it
                self.refresh_proxy()  # no longer awaited: its value is now unavailable
                continue
            try:
                market = get_json(f"{KALSHI}/markets/{quote(ticker, safe='')}")["market"]
                value = market.get("expiration_value")
                if optional_float(value) is None:
                    continue  # "" means not populated yet
                self.append({"type": "settled_value", "ticker": ticker, "recorded_at": iso(now),
                             "expiration_value": value})
                self.expirations[ticker] = value
                del self.value_pending[ticker]
                self.refresh_proxy()
            except Exception as exc:
                failed = True
                with self.feed.lock:
                    self.feed._note("tally_settle", exc)
        self.prune_memory()
        # A logger built without __init__ (tests) has no alarm store to clear.
        if not failed and getattr(self, "feed", None):
            with self.feed.lock:
                self.feed.errors.pop("tally_settle", None)

    def prune_memory(self):
        """Archive settled windows beyond MAX_MEMORY_WINDOWS so in-memory dicts stay O(1)."""
        arch_rec = getattr(self, "_archived_record", None)
        arch_cov = getattr(self, "_archived_coverage", None)
        if arch_rec is None:
            arch_rec = self._archived_record = {"hits": 0, "scored": 0}
        if arch_cov is None:
            arch_cov = self._archived_coverage = {"total_closes": 0, "scored": 0, "incomplete_samples": 0,
                                                  "missing_settlement": 0, "invalid_proxy": 0}
        limit = max(MAX_MEMORY_WINDOWS, PROXY_RECORD_WINDOWS)
        active_tickers = set(self.opened) | set(self.closes)
        if len(self.settled) > limit:
            keep_settled = active_tickers | set(getattr(self, "pending_open", {})) | set(self.value_pending)
            for orphan in list(self.settled):
                if orphan not in keep_settled:
                    self.settled.discard(orphan)
                    getattr(self, "results", {}).pop(orphan, None)
                    self.expirations.pop(orphan, None)
        if len(self.opened) <= limit and len(self.closes) <= limit:
            return
        def win_ts(ticker):
            row = self.closes.get(ticker) or self.opened.get(ticker) or {}
            for key in ("close_time", "open_time", "captured_at"):
                if row.get(key):
                    try:
                        return (0, parse_ts(row[key]), "")
                    except Exception:
                        pass
            return (1, 0.0, str(row.get("close_time") or ""))
        all_tickers = sorted(active_tickers, key=lambda t: (*win_ts(t), t))
        if len(all_tickers) <= limit:
            return
        scored_closes = [
            t for t, row in sorted(self.closes.items(), key=lambda item: (*win_ts(item[0]), item[0]))
            if (row.get("samples") == 60
                and optional_float(self.expirations.get(t)) is not None
                and optional_float(row.get("proxy_avg_60s")) is not None)
        ]
        protected = set(scored_closes[-PROXY_RECORD_WINDOWS:]) | set(getattr(self, "pending_open", {})) | set(self.value_pending)
        for ticker in all_tickers[:-limit]:
            if ticker not in self.settled or ticker in protected:
                continue
            open_row = self.opened.pop(ticker, None)
            outcome = getattr(self, "results", {}).pop(ticker, None)
            if open_row is not None:
                lean = (open_row.get("timeframes") or {}).get("1m", {}).get("lean")
                if lean in ("bullish", "bearish") and outcome in ("yes", "no"):
                    arch_rec["scored"] += 1
                    arch_rec["hits"] += (lean == "bullish") == (outcome == "yes")
            close_row = self.closes.pop(ticker, None)
            settled_val = optional_float(self.expirations.pop(ticker, None))
            if close_row is not None:
                arch_cov["total_closes"] += 1
                proxy = optional_float(close_row.get("proxy_avg_60s"))
                if close_row.get("samples") != 60:
                    arch_cov["incomplete_samples"] += 1
                elif settled_val is None:
                    arch_cov["missing_settlement"] += 1
                elif proxy is None:
                    arch_cov["invalid_proxy"] += 1
                else:
                    arch_cov["scored"] += 1
            self.settled.discard(ticker)

    def refresh_record(self):
        """Track record of the legacy 1m lean at open, shown under the lean."""
        arch = getattr(self, "_archived_record", {"hits": 0, "scored": 0})
        hits, scored = arch["hits"], arch["scored"]
        for ticker, row in self.opened.items():
            lean = (row.get("timeframes") or {}).get("1m", {}).get("lean")
            outcome = self.results.get(ticker)
            if lean not in ("bullish", "bearish") or outcome not in ("yes", "no"):
                continue
            scored += 1
            hits += (lean == "bullish") == (outcome == "yes")
        interval = wilson95(hits, scored)
        self.record = {"timeframe": "1m", "measure": "legacy lean at window open vs settlement",
                       "hits": hits, "scored": scored,
                       "skipped_lines": self.skipped_lines,
                       "rate": hits / scored if scored else None,
                       "wilson95": list(interval) if interval else None,
                       "includes_50": interval is None or interval[0] <= 0.5 <= interval[1],
                       "too_few": scored < 100}

    def accept_close(self, row):
        if row["ticker"] in self.closes:
            return
        self.append(row)
        self.closes[row["ticker"]] = row
        self.refresh_proxy()

    def refresh_proxy(self):
        """Our closing-minute composite mean vs Kalshi's settlement value."""
        errors = []
        arch = getattr(self, "_archived_coverage", {"total_closes": 0, "scored": 0, "incomplete_samples": 0,
                                                    "missing_settlement": 0, "invalid_proxy": 0})
        coverage = {"total_closes": arch["total_closes"] + len(self.closes), "scored": arch["scored"],
                    "incomplete_samples": arch["incomplete_samples"], "awaiting_settlement": 0,
                    "missing_settlement": arch["missing_settlement"], "invalid_proxy": arch["invalid_proxy"]}
        def close_order(item):
            ticker, row = item
            for key in ("close_time", "captured_at"):
                if row.get(key):
                    try:
                        return (0, parse_ts(row[key]), "", ticker)
                    except Exception:
                        pass
            return (1, 0.0, str(row.get("close_time") or ""), ticker)
        for ticker, row in sorted(self.closes.items(), key=close_order):
            settled = optional_float(self.expirations.get(ticker))
            proxy = optional_float(row.get("proxy_avg_60s"))
            # Exclusive reasons, in this order; coverage spans all recorded closes.
            # A value still being asked for is awaited, not missing.
            if row.get("samples") != 60:
                coverage["incomplete_samples"] += 1
            elif settled is None and (ticker not in self.settled or ticker in self.value_pending):
                coverage["awaiting_settlement"] += 1
            elif settled is None:
                coverage["missing_settlement"] += 1
            elif proxy is None:
                coverage["invalid_proxy"] += 1
            else:
                coverage["scored"] += 1
                errors.append(proxy - settled)
        summary = proxy_summary(errors[-PROXY_RECORD_WINDOWS:])
        summary["coverage"] = coverage
        self.proxy = summary

    def capture_minute(self, now):
        row = minute_record(cached_state("1m"), now)
        if row is None or (row["ticker"], row["minute"]) == self.minute_captured:
            return
        self.append(row)
        self.minute_captured = (row["ticker"], row["minute"])

    def run_minutes(self):
        """Write one row per whole minute inside a window; missed minutes stay missing."""
        while True:
            time.sleep(60.5 - time.time() % 60)
            try:
                self.capture_minute(time.time())
                with self.feed.lock:
                    self.feed.errors.pop("tally_minute", None)
            except Exception as exc:
                with self.feed.lock:
                    self.feed._note("tally_minute", exc)

    def run(self):
        try:
            self.prepare()
        except Exception as exc:
            with self.feed.lock:
                self.feed._note("tally_log", exc)
        while True:
            try:
                self.step()
                if "tally_worker" in self.feed.errors:
                    with self.feed.lock:
                        self.feed.errors.pop("tally_worker", None)
            except Exception as exc:
                # Nothing may end this thread: opening capture, hydration and
                # settlement all live here.
                with self.feed.lock:
                    self.feed._note("tally_worker", exc)
                time.sleep(1)

    def step(self):
        try:
            snapshot = self.queue.get(timeout=1)
            if snapshot.get("type") == "close":
                if snapshot.get("ticker") not in self.closes:
                    self.pending_closes[snapshot["ticker"]] = snapshot
            else:
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
        # A failed close append (disk full, say) stays queued and is retried
        # every pass; accept_close skips tickers already written.
        for ticker, row in list(self.pending_closes.items()):
            try:
                self.accept_close(row)
                del self.pending_closes[ticker]
                if not self.pending_closes:
                    with self.feed.lock:
                        self.feed.errors.pop("tally_close", None)
            except Exception as exc:
                with self.feed.lock:
                    self.feed._note("tally_close", exc)
        if time.time() >= self.next_hydrate:
            self.next_hydrate = time.time() + 5
            self.hydrate_pending(time.time())
        if time.time() >= self.next_settle:
            self.next_settle = time.time() + 60
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
# The Acer watcher stores its state after every completed run, so the file's
# age is its heartbeat; /api/state reports it for the off-Acer alarm.
WATCH_STATE = HERE / "state" / "ta_watch_state.json"


def watcher_age(now):
    try:
        return round(now - WATCH_STATE.stat().st_mtime, 1)
    except OSError:
        return None
SOCKET_TIMEOUT = 30


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
                        state["exchange_closed"] = False
                        state["frames"] = dict(state.get("frames", {}))
                        for name, frame in state["frames"].items():
                            if frame.get("levels") is not None:
                                state["frames"][name] = {**frame, "levels": {**frame["levels"], "target": None}}
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
        STATE_CACHE[tf] = (bucket, state)
        return state


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
    # Socket timeout: a client that stops reading can't hold an SSE slot
    # forever, and idle keep-alive connections close.
    timeout = SOCKET_TIMEOUT

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
            # The slot is released by the finally below from here on, including
            # when the client drops while the headers are being written.
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
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
                        # Tell the page now; it would otherwise only see silence.
                        self.wfile.write(b'event: problem\ndata: {"error":"state serialization"}\n\n')
                        self.wfile.flush()
                        time.sleep(STREAM_INTERVAL)
                        continue
                    clear_state_error()
                    self.wfile.write(("event: state\ndata: " + body + "\n\n").encode())
                    self.wfile.flush()
                    if full:
                        last_key, last_full = key, now
                    time.sleep(STREAM_INTERVAL)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
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
    FEED.tally_logger = TallyLogger(HERE / "state", FEED)
    threading.Thread(target=FEED.tally_logger.run, daemon=True).start()
    threading.Thread(target=FEED.tally_logger.run_minutes, daemon=True).start()
    for name in VENUE_URLS:
        threading.Thread(target=FEED.run_venue, args=(name,), daemon=True).start()
    threading.Thread(target=FEED.run_upcoming, daemon=True).start()
    threading.Thread(target=FEED.run_market, daemon=True).start()
    threading.Thread(target=FEED.run_market_context, daemon=True).start()
    threading.Thread(target=FEED.run_composite, daemon=True).start()
    threading.Thread(target=FEED.run, daemon=True).start()
    threading.Thread(target=FEED.run_spot, daemon=True).start()
    for name in ("Coinbase", "Kraken"):
        threading.Thread(target=FEED.run_stream, args=(name,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BTC TA copilot (read-only, public data) at http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
