#!/usr/bin/env python3
"""Experimental, paper-only scalp engine for KXBTC15M (momentum).

This is the one bounded exception to the dashboard's "no buy/sell output" rule
(see CODEX_TASK_MOMENTUM_20261003.md): an unvalidated, paper-scored enter/exit
trigger. It is **descriptive arithmetic over public data**. It never talks to
Kalshi, never holds credentials, and never places, sizes, or prepares an order.
There is no probability-of-winning output anywhere in this module: the win rate
it reports is the realized frequency of past paper trades, not a forecast.

Rules (every threshold is a named constant above)
-------------------------------------------------
Eligibility, all required: ``stale`` and ``kalshi_stale`` false; order-book age
<= ``MAX_BOOK_AGE``; ``seconds_left >= MIN_SECONDS_LEFT``; the ask of the side
being bought within ``[MIN_ASK, MAX_ASK]``; spread (ask - own bid) <=
``MAX_SPREAD``; that ask's depth >= ``PAPER_SIZE``; flat; ``COOLDOWN_SECONDS``
since the last completed exit.

ENTER YES (momentum up): 1m tally bull - bear >= 3 and 60 s imbalance >= +0.20
and composite above the rolling 60 s average. ENTER NO mirrors it: bear - bull
>= 3, imbalance <= -0.20, composite below the 60 s average. A missing 60 s
average fails the price leg.

EXIT, first that fires: ``take_profit`` -- the held side's bid >= entry ask +
4c; ``stop`` -- bid <= entry ask - 4c; ``flip`` -- the 1m tally leans the other
way (opposite side leads by >= 2) or the imbalance crosses to the opposite
threshold; ``max_hold`` -- ``MAX_HOLD_SECONDS`` held; ``time_stop`` --
``seconds_left <= TIME_STOP_SECONDS_LEFT``. An exit that fires without an
executable bid is retried every second at the first executable bid; only the
time stop, a closed window or a rollover without one records ``no_bid`` and
settles the position at the market's public ``result`` (no fee).

Every journal row carries ``strategy``; statistics count only ``STRATEGY``.
Rows without the field predate it (``LEGACY_STRATEGY``).

Snapshot contract (one argument to :meth:`ScalpEngine.step`)
------------------------------------------------------------
A dashboard ``/api/state`` document (or anything shaped like one). Only these
keys are read, all optional; a missing key is treated as unknown, which blocks
entry rather than guessing::

    snapshot["stale"]                 bool
    snapshot["kalshi_stale"]          bool
    snapshot["composite"]             float, the four-venue composite spot proxy
    snapshot["average_60s"]           float, rolling 60 s composite mean
    snapshot["frames"]["1m"]["tally"]["bull"|"bear"]  1m indicator tally counts
    snapshot["frames"]["1m"]["values"]["rsi"]       1m RSI 14
    snapshot["frames"]["1m"]["values"]["bb_pctb"]   1m Bollinger %B
    snapshot["frames"]["1m"]["values"]["bb_mid"]    1m Bollinger middle band
    snapshot["order_flow"]["one_minute"]["imbalance"]           60 s flow imbalance, [-1, 1]
    snapshot["order_flow"]["one_minute"]["previous_imbalance"]  optional prior 60 s imbalance
    snapshot["window"]["ticker"]            str
    snapshot["window"]["seconds_left"]      float
    snapshot["window"]["orderbook"]         {"yes_bid": {"price": "0.47", "count": "120"},
                                             "no_bid": {...}, ...}  public book, dollar strings
    snapshot["window"]["orderbook_age"]     float seconds since that book was fetched

Exact 1m close keys (**logged only; no rule in this version reads them**):
``snapshot["frames"]["1m"]["chart"]["candles"][-1]["close"]`` is the current
forming 1m bar's close and ``[-2]["close"]`` the previous bar's close. The
dashboard only builds ``chart`` for the frame equal to the requested timeframe,
so the reader also accepts ``frames["1m"]["values"]["close"]`` /
``["close_prev"]`` and then the flat ``snapshot["close_1m"]`` /
``snapshot["prev_close_1m"]``. See ``report_integration_needs()`` for the
follow-up that makes the primary key unconditional.

Prices and fees are ``Decimal`` internally; the log stores dollar strings and
``view()`` returns JSON-safe primitives (no NaN, no Decimal) for ``/api/state``.

Journal (append-only JSONL, private)
------------------------------------
``<state_dir>/scalp_log.jsonl``: one ``enter`` row per paper entry and one
``exit`` row per close, written with ``os.O_APPEND`` and mode 0600 inside a 0700
directory. Row ``outcome`` is ``filled`` (exit at the book bid), ``settled``
(settled at the public result, no fee), ``no_bid`` (a required exit had no bid;
the position stays open until it settles) or ``aborted`` (engine restarted with
an open position; excluded from expectancy, counted and shown). Trades in the
stats are exactly the ``filled`` and ``settled`` rows, so each position
contributes one trade.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

# ---------------------------------------------------------------- constants
# Every threshold named, per the task. Nothing below this block is a magic
# number for a rule.

PAPER_SIZE = 10                         # contracts per paper entry (10)

STRATEGY = "momentum_v1"                # tag on every journal row; stats count only this
LEGACY_STRATEGY = "mean_reversion_v1"   # rows written before the tag existed

# Momentum entry legs.
ENTRY_TALLY_LEAD = 3                    # 1m tally: entry side leads by at least
ENTRY_FLOW_IMBALANCE = 0.20             # 60 s imbalance at or beyond +/- this

# Eligibility.
MAX_BOOK_AGE = 5.0                      # seconds since the public book was fetched
MIN_SECONDS_LEFT = 180.0                # window time left
MIN_ASK = Decimal("0.10")               # ask range of the side being bought
MAX_ASK = Decimal("0.90")
MAX_SPREAD = Decimal("0.02")            # ask - own bid, at entry
COOLDOWN_SECONDS = 60.0                 # since the last completed exit

# Exits, in precedence order.
EXIT_RULE_ORDER = ("take_profit", "stop", "flip", "max_hold", "time_stop")
TAKE_PROFIT = Decimal("0.04")           # held side's bid >= entry ask + 4c
STOP_LOSS = Decimal("0.04")             # held side's bid <= entry ask - 4c
FLIP_TALLY_LEAD = 2                     # opposite side leads the 1m tally by at least
FLIP_FLOW_IMBALANCE = 0.20              # imbalance at or beyond the opposite threshold
MAX_HOLD_SECONDS = 300.0                # held seconds
TIME_STOP_SECONDS_LEFT = 90.0           # window time left

# Fees: the published general taker formula for KXBTC15M,
# fee = 0.07 * C * P * (1 - P) with the series multiplier 1 (the public series
# schedule gives a default series multiplier of 1). The fee plus position cost
# is rounded upward to a centicent ($0.0001), as the public schedule states.
# The $0.01 calculation is retained as a sensitivity comparison.
FEE_MULTIPLIER = Decimal("0.07")
FEE_ROUNDING_INCREMENT = Decimal("0.0001")               # default: account balance precision
FEE_ROUNDING_INCREMENT_SENSITIVITY = Decimal("0.01")     # also reported: cent-rounded schedule

# Reporting.
MIN_TRADES_TO_JUDGE = 100               # below this, "too few trades to judge"
EXIT_DISPLAY_SECONDS = 5.0
CONFIDENCE = 0.95
LOG_NAME = "scalp_log.jsonl"
DEFAULT_STATE_DIR = Path.home() / "btc-ta" / "state"

SIDES = ("yes", "no")
COUNTS_AS_TRADE = ("filled", "settled")


# ---------------------------------------------------------------- scalars

def decimal_value(value):
    """Decimal or None; rejects NaN/Inf and unparseable input."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def number(value):
    """Finite float or None. Booleans are not numbers here."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def money(value, places=4):
    """Dollar string for the log, or None."""
    value = decimal_value(value)
    if value is None:
        return None
    return format(value.quantize(Decimal(1).scaleb(-places)), "f")


def cents(value, places=2):
    """Rounded cents as a JSON-safe float, or None."""
    value = decimal_value(value)
    if value is None:
        return None
    return float((value * 100).quantize(Decimal(1).scaleb(-places)))


def finite(value, places=6):
    """Rounded finite float for JSON output, or None."""
    value = number(value)
    return None if value is None else round(value, places)


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ---------------------------------------------------------------- fees

def taker_fee(contracts, price, increment=FEE_ROUNDING_INCREMENT):
    """Published KXBTC15M taker fee and its raw value.

    ``raw = 0.07 * C * P * (1 - P)``; round fee plus position cost up to the
    requested increment. Returns ``(fee, raw)`` in dollars.
    """
    contracts, price = decimal_value(contracts), decimal_value(price)
    increment = decimal_value(increment)
    if contracts is None or price is None or increment is None or increment <= 0:
        raise ValueError("fee needs a finite contract count, price and increment")
    if not 0 <= price <= 1 or contracts <= 0:
        raise ValueError("fee needs 0 <= price <= 1 and contracts > 0")
    raw = FEE_MULTIPLIER * contracts * price * (1 - price)
    position_cost = contracts * price
    aligned = ((position_cost + raw) / increment).to_integral_value(
        rounding=ROUND_CEILING) * increment
    return aligned - position_cost, raw


# ---------------------------------------------------------------- snapshot

def book_quotes(orderbook):
    """Best bid per side plus the implied ask, from a public Kalshi book.

    Kalshi publishes YES and NO bids. The ask on one side is the opposite
    side's best bid at ``1 - price``, and its depth is that bid's count, so
    ``yes_ask = 1 - best no_bid`` and ``no_ask = 1 - best yes_bid``. Returns
    ``{"yes": {"bid": level|None, "ask": level|None}, "no": {...}}`` or None.
    """
    if not isinstance(orderbook, dict):
        return None

    def level(key):
        raw = orderbook.get(key)
        if not isinstance(raw, dict):
            return None
        price, count = decimal_value(raw.get("price")), decimal_value(raw.get("count"))
        if price is None or count is None or not 0 < price < 1 or count <= 0:
            return None
        return {"price": price, "count": count}

    yes_bid, no_bid = level("yes_bid"), level("no_bid")

    def implied(opposite):
        if opposite is None:
            return None
        return {"price": Decimal(1) - opposite["price"], "count": opposite["count"]}

    return {"yes": {"bid": yes_bid, "ask": implied(no_bid)},
            "no": {"bid": no_bid, "ask": implied(yes_bid)}}


def frame_values(snapshot, name="1m"):
    frames = snapshot.get("frames")
    if not isinstance(frames, dict):
        return {}
    frame = frames.get(name)
    if not isinstance(frame, dict):
        return {}
    values = frame.get("values")
    return values if isinstance(values, dict) else {}


def close_pair(snapshot):
    """(previous 1m close, current 1m close), logged only.

    Primary key: ``frames["1m"]["chart"]["candles"][-1]["close"]`` (current
    forming bar) and ``[-2]["close"]`` (previous bar). Fallbacks:
    ``frames["1m"]["values"]["close"]`` / ``["close_prev"]``, then the flat
    ``snapshot["close_1m"]`` / ``snapshot["prev_close_1m"]``.
    """
    frames = snapshot.get("frames")
    frame = frames.get("1m") if isinstance(frames, dict) else None
    current = previous = None
    if isinstance(frame, dict):
        chart = frame.get("chart")
        candles = chart.get("candles") if isinstance(chart, dict) else None
        if isinstance(candles, list) and candles:
            current = number(candles[-1].get("close")) if isinstance(candles[-1], dict) else None
            if len(candles) > 1 and isinstance(candles[-2], dict):
                previous = number(candles[-2].get("close"))
        values = frame.get("values")
        if isinstance(values, dict):
            current = current if current is not None else number(values.get("close"))
            previous = previous if previous is not None else number(values.get("close_prev"))
    if current is None:
        current = number(snapshot.get("close_1m"))
    if previous is None:
        previous = number(snapshot.get("prev_close_1m"))
    return previous, current


def read_state(snapshot):
    """Flatten a dashboard state document into the values the rules read."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    values = frame_values(snapshot)
    flow = snapshot.get("order_flow")
    one_minute = flow.get("one_minute") if isinstance(flow, dict) else None
    one_minute = one_minute if isinstance(one_minute, dict) else {}
    window = snapshot.get("window")
    window = window if isinstance(window, dict) else None
    previous_close, current_close = close_pair(snapshot)
    frames = snapshot.get("frames")
    frame = frames.get("1m") if isinstance(frames, dict) else None
    tally = frame.get("tally") if isinstance(frame, dict) else None
    tally = tally if isinstance(tally, dict) else {}
    bull, bear = number(tally.get("bull")), number(tally.get("bear"))
    return {
        # A missing freshness flag counts as stale: unknown input blocks entry.
        "stale": snapshot.get("stale") is not False,
        "kalshi_stale": snapshot.get("kalshi_stale") is not False,
        "composite": number(snapshot.get("composite")),
        "average_60s": number(snapshot.get("average_60s")),
        "rsi": number(values.get("rsi")),
        "bb_pctb": number(values.get("bb_pctb")),
        "bb_mid": number(values.get("bb_mid")),
        "tally_bull": bull, "tally_bear": bear,
        "tally_lead": None if bull is None or bear is None else int(bull - bear),
        "imbalance": number(one_minute.get("imbalance")),
        "previous_imbalance": number(one_minute.get("previous_imbalance")),
        "close_1m_prev": previous_close,
        "close_1m": current_close,
        "ticker": window.get("ticker") if window else None,
        "seconds_left": number(window.get("seconds_left")) if window else None,
        "orderbook_age": number(window.get("orderbook_age")) if window else None,
        "book": book_quotes(window.get("orderbook")) if window else None,
        "window_present": window is not None,
    }


def price_gap(state):
    """Composite minus the rolling 60 s average, or None if either is missing."""
    composite, average = state["composite"], state["average_60s"]
    return None if composite is None or average is None else composite - average


def entry_legs(state, direction):
    """The three momentum legs for ``direction``, evaluated (met, value, detail)."""
    lead, imbalance, gap = state["tally_lead"], state["imbalance"], price_gap(state)
    if direction == "yes":
        return [("tally", lead is not None and lead >= ENTRY_TALLY_LEAD, lead,
                 f"bull minus bear at or above {ENTRY_TALLY_LEAD}"),
                ("flow", imbalance is not None and imbalance >= ENTRY_FLOW_IMBALANCE, imbalance,
                 f"60 s imbalance at or above +{ENTRY_FLOW_IMBALANCE:g}"),
                ("vs 60s avg", gap is not None and gap > 0, gap,
                 "composite above the rolling 60 s average")]
    return [("tally", lead is not None and lead <= -ENTRY_TALLY_LEAD, lead,
             f"bear minus bull at or above {ENTRY_TALLY_LEAD}"),
            ("flow", imbalance is not None and imbalance <= -ENTRY_FLOW_IMBALANCE, imbalance,
             f"60 s imbalance at or below -{ENTRY_FLOW_IMBALANCE:g}"),
            ("vs 60s avg", gap is not None and gap < 0, gap,
             "composite below the rolling 60 s average")]


def candidate_direction(state):
    """The side the 1m tally leads by the entry margin, or None."""
    lead = state["tally_lead"]
    if lead is None:
        return None
    if lead >= ENTRY_TALLY_LEAD:
        return "yes"
    if lead <= -ENTRY_TALLY_LEAD:
        return "no"
    return None


def flipped(state, side):
    """True when the 1m tally or the 60 s flow has turned against ``side``."""
    lead, imbalance = state["tally_lead"], state["imbalance"]
    if side == "yes":
        return ((lead is not None and lead <= -FLIP_TALLY_LEAD)
                or (imbalance is not None and imbalance <= -FLIP_FLOW_IMBALANCE))
    return ((lead is not None and lead >= FLIP_TALLY_LEAD)
            or (imbalance is not None and imbalance >= FLIP_FLOW_IMBALANCE))


def display_direction(state):
    """Direction to show condition ticks against while flat (display only)."""
    direction = candidate_direction(state)
    if direction:
        return direction
    lead, imbalance = state["tally_lead"], state["imbalance"]
    if lead:
        return "yes" if lead > 0 else "no"
    if imbalance:
        return "yes" if imbalance > 0 else "no"
    return None


def eligibility(state, last_exit_at, now, held=False):
    """Every eligibility gate, as ``[(name, met, detail), ...]`` plus side skip."""
    checks = []

    def add(name, met, detail):
        checks.append((name, bool(met), detail))

    add("feed fresh", not state["stale"], "stale is false")
    add("kalshi fresh", not state["kalshi_stale"], "kalshi_stale is false")
    age = state["orderbook_age"]
    add("book age", age is not None and 0 <= age <= MAX_BOOK_AGE,
        f"order-book age at or under {MAX_BOOK_AGE:g} s")
    left = state["seconds_left"]
    add("time left", left is not None and left >= MIN_SECONDS_LEFT,
        f"seconds_left at or above {MIN_SECONDS_LEFT:g}")
    add("flat", not held, "one paper position at a time")
    waited = None if last_exit_at is None else now - last_exit_at
    add("cooldown", waited is None or waited >= COOLDOWN_SECONDS,
        f"{COOLDOWN_SECONDS:g} s since the last exit")
    return checks, waited


def side_quote_checks(state, direction):
    """Ask range, spread and depth for the side being bought."""
    book = state["book"]
    side = book.get(direction) if book else None
    ask = side.get("ask") if side else None
    bid = side.get("bid") if side else None
    spread = (ask["price"] - bid["price"]) if ask and bid else None
    return {
        "ask": ask, "bid": bid, "spread": spread,
        "checks": [
            ("ask range", ask is not None and MIN_ASK <= ask["price"] <= MAX_ASK,
             f"ask between {MIN_ASK * 100:.0f}c and {MAX_ASK * 100:.0f}c"),
            ("spread", spread is not None and 0 <= spread <= MAX_SPREAD,
             f"ask minus own bid at or under {MAX_SPREAD * 100:.0f}c"),
            ("depth", ask is not None and ask["count"] >= PAPER_SIZE,
             f"top-of-book count at or above the {PAPER_SIZE}-contract paper size"),
        ],
    }


# ---------------------------------------------------------------- statistics

def wilson95(hits, n):
    """Wilson 95% interval for a realized hit rate, or None."""
    if not n:
        return None
    z = 1.959963984540054
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


# Two-sided 95% Student-t critical values; the table stops where the normal
# approximation is already good enough for this report.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
       8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
       15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
       22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
       29: 2.045, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984,
       120: 1.980}


def t95(degrees):
    if degrees in T95:
        return T95[degrees]
    below = [d for d in T95 if d < degrees]
    if not below:
        return T95[1]
    return T95[max(below)] if degrees <= 120 else 1.959963984540054


def mean_ci95(values):
    """Student-t 95% interval for the mean, or None. Deterministic, no draws."""
    values = [v for v in values if v is not None]
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    half = t95(n - 1) * math.sqrt(variance / n)
    return mean - half, mean + half


def max_drawdown(pnls):
    """Largest peak-to-trough decline of the cumulative P&L curve (dollars)."""
    peak = running = 0.0
    worst = 0.0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return -worst


def trade_summaries(rows):
    """Completed trades from journal rows: one per position, ``filled``/``settled``."""
    trades = [{"ticker": row.get("ticker"), "side": row.get("side"),
               "rule": row.get("rule"), "outcome": row.get("outcome"),
               "at_epoch": number(row.get("at_epoch")),
               "contracts": number(row.get("contracts")) or PAPER_SIZE,
               "net_dollars": number(row.get("net_dollars")),
               "net_dollars_01": number(row.get("net_dollars_01"))}
              for row in rows
              if row.get("type") == "exit" and row.get("outcome") in COUNTS_AS_TRADE]
    for trade in trades:
        net = trade["net_dollars"]
        trade["per_contract"] = None if net is None else net / trade["contracts"]
        trade["won"] = bool(net is not None and net > 0)
    return trades


def row_strategy(row):
    return row.get("strategy") or LEGACY_STRATEGY


def replay(rows, strategy=STRATEGY):
    """Replay one strategy's rows: completed trades, open entries, aborted count.

    ``strategy=None`` replays every row regardless of its tag.
    """
    open_entries, trades, aborted = {}, [], 0
    last_exit_at = None
    for row in rows:
        if strategy is not None and row_strategy(row) != strategy:
            continue
        ticker = row.get("ticker")
        if row.get("type") == "enter":
            open_entries[ticker] = row
            continue
        if row.get("type") != "exit":
            continue
        outcome = row.get("outcome")
        if outcome == "no_bid":
            continue                     # the position is still open until it settles
        open_entries.pop(ticker, None)
        at = number(row.get("at_epoch"))
        if at is not None:
            last_exit_at = at if last_exit_at is None else max(last_exit_at, at)
        if outcome in COUNTS_AS_TRADE:
            trades.extend(trade_summaries([row]))
        elif outcome == "aborted":
            aborted += 1
    return trades, open_entries, last_exit_at, aborted


def summarize(trades, aborted=0):
    """All-time descriptive statistics over completed paper trades.

    The win rate is the realized frequency of these trades. It is not, and must
    not be presented as, a probability of winning the next one.
    """
    nets = [t["net_dollars"] for t in trades if t["net_dollars"] is not None]
    per_contract = [t["per_contract"] for t in trades if t["per_contract"] is not None]
    wins = sum(1 for t in trades if t["won"])
    n = len(trades)
    rate_ci = wilson95(wins, n)
    mean_ci = mean_ci95(per_contract)
    nets_01 = [t["net_dollars_01"] for t in trades if t["net_dollars_01"] is not None]
    per_contract_01 = [t["net_dollars_01"] / t["contracts"] for t in trades
                       if t["net_dollars_01"] is not None and t["contracts"]]
    mean_ci_01 = mean_ci95(per_contract_01)
    stats = {
        "trades": n,
        "wins": wins,
        "losses": sum(1 for t in trades if t["net_dollars"] is not None and not t["won"]),
        # Realized frequency of these paper trades, never a probability forecast.
        "win_rate": None if not n else wins / n,
        "win_rate_ci": None if rate_ci is None else [finite(rate_ci[0]), finite(rate_ci[1])],
        "cumulative_pnl_dollars": finite(sum(nets)) if nets else 0.0,
        "max_drawdown_dollars": finite(max_drawdown(nets)) if nets else 0.0,
        "mean_per_contract_dollars": finite(sum(per_contract) / len(per_contract)) if per_contract else None,
        "mean_per_contract_ci_dollars": None if mean_ci is None else [finite(mean_ci[0]), finite(mean_ci[1])],
        "mean_per_contract_cents": None if not per_contract else finite(100 * sum(per_contract) / len(per_contract), 4),
        "mean_per_contract_ci_cents": None if mean_ci is None else [finite(100 * mean_ci[0], 4), finite(100 * mean_ci[1], 4)],
        # Same figures on the cent-rounded fee schedule, for sensitivity.
        "cumulative_pnl_dollars_01": finite(sum(nets_01)) if nets_01 else 0.0,
        "max_drawdown_dollars_01": finite(max_drawdown(nets_01)) if nets_01 else 0.0,
        "mean_per_contract_dollars_01": (finite(sum(per_contract_01) / len(per_contract_01))
                                         if per_contract_01 else None),
        "mean_per_contract_ci_dollars_01": None if mean_ci_01 is None else [finite(mean_ci_01[0]), finite(mean_ci_01[1])],
        "aborted": aborted,
        "too_few": n < MIN_TRADES_TO_JUDGE,
        "floor_for_judgement": MIN_TRADES_TO_JUDGE,
    }
    return stats


def load_rows(path):
    """Journal rows, skipping blank or unreadable lines. Missing file -> []."""
    rows = []
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    return rows


def stats_from_log(path):
    trades, _open, _last_exit, aborted = replay(load_rows(path))
    return summarize(trades, aborted)


def report_integration_needs():
    """Open items for the dashboard integration (kept in code so it ships)."""
    return (
        "Build the engine once at startup with the dashboard state dir, call "
        "engine.step(state, now) once per second and publish engine.view() as "
        "state['scalp'] (JSON-safe, no NaN).",
        "Call engine.settle(ticker, market['result'], now) whenever a public "
        "result is observed, so a position with no bid at its exit settles.",
        "frames['1m'] carries 'chart' only when the requested timeframe is 1m; "
        "stash the last two 1m closes (for example frame['values']['close'] and "
        "['close_prev']) if the journal should always hold them.",
        "order_flow.one_minute.previous_imbalance is optional today; the swing "
        "leg of flow fading only uses it when the feed provides it.",
    )


# ---------------------------------------------------------------- engine

class ScalpEngine:
    """Single-position, paper-only scalp state machine.

    Deterministic: every clock reading arrives as the ``now`` argument, there
    is no randomness, no sleep, and no network. ``prepare()`` runs on
    construction, replays the journal, and records an ``aborted`` exit if the
    previous process died holding a position.
    """

    def __init__(self, state_dir=None, *, now=None, log_name=LOG_NAME):
        self.state_dir = Path(state_dir) if state_dir is not None else DEFAULT_STATE_DIR
        self.path = self.state_dir / log_name
        self.lock = threading.Lock()
        self.position = None
        self.pending = None              # missed-bid close awaiting the public result
        self.last_exit_at = None
        self.last_event = None
        self.trades = []
        self.aborted = 0
        self.errors = []
        self.state = read_state({})
        self.prepare(now)

    # ---------------------------------------------------------- journal

    def _open_log(self):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

    def append(self, row):
        """Append one private, append-only journal row."""
        payload = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "ab", closefd=False) as handle:
                handle.write(payload)
        finally:
            os.close(fd)

    def _write(self, row):
        """Append one row; a failed paper action leaves the position unchanged."""
        row.setdefault("strategy", STRATEGY)
        try:
            self._open_log()
            self.append(row)
        except OSError as exc:
            self.note(f"journal: {exc}")
            return False
        return True

    def note(self, message):
        if message not in self.errors:
            self.errors.append(message)
            del self.errors[:-5]

    def prepare(self, now=None):
        """Replay the journal; abort a position left open by a dead process."""
        now = time.time() if now is None else float(now)
        try:
            self._open_log()
            rows = load_rows(self.path)
        except OSError as exc:
            self.note(f"journal: {exc}")
            rows = []
        trades, open_entries, last_exit_at, aborted = replay(rows)
        self.trades, self.last_exit_at, self.aborted = trades, last_exit_at, aborted
        for entry in open_entries.values():
            row = {"type": "exit", "outcome": "aborted", "rule": "restart",
                   "strategy": row_strategy(entry),
                   "ticker": entry.get("ticker"), "side": entry.get("side"),
                   "at": iso(now), "at_epoch": now, "contracts": entry.get("contracts", PAPER_SIZE),
                   "entry_ask": entry.get("ask"), "entry_at_epoch": entry.get("at_epoch"),
                   "entry_depth": entry.get("ask_depth"), "entry_fee": entry.get("fee"),
                   "bid": None, "bid_depth": None, "exit_fee": None,
                   "inputs": entry.get("inputs"),
                   "net_dollars": None, "net_dollars_01": None,
                   "note": "engine restarted with an open paper position; excluded from expectancy"}
            if self._write(row):
                self.aborted += 1
                self.last_exit_at = now
                self.last_event = row
        return self

    # ---------------------------------------------------------- state reads

    def _entry_blockers(self, state, now, direction, quote_checks):
        checks, _waited = eligibility(state, self.last_exit_at, now, held=self.position is not None)
        blockers = [name for name, met, _detail in checks if not met]
        blockers += [name for name, met, _detail in quote_checks["checks"] if not met]
        if direction is not None:
            blockers += [name for name, met, _value, _detail in entry_legs(state, direction) if not met]
        if state["ticker"] is None:
            blockers.append("window")
        if direction is None:
            blockers.append("tally lead")
        return blockers

    # ---------------------------------------------------------- step

    def step(self, snapshot, now):
        """Advance one state snapshot. Returns the JSON-safe view."""
        with self.lock:
            now = float(now)
            state = read_state(snapshot)
            self.state = state
            if self.position is not None:
                self._manage_open_position(state, now)
            elif self.pending is None:
                self._consider_entry(state, now)
            return self.view(now=now)

    def _manage_open_position(self, state, now):
        position = self.position
        if state["ticker"] is None:
            position["note"] = "no window in this state snapshot"
            if now >= position["close_at"] - TIME_STOP_SECONDS_LEFT:
                self._record_missed_exit(state, now, position, "time_stop",
                                         "time stop reached without a current window or executable bid; awaiting public result")
            return
        if state["ticker"] != position["ticker"]:
            self._record_missed_exit(state, now, position, "time_stop",
                                     "the held market rolled over; waiting for its public result")
            return
        position["note"] = None
        side = state["book"].get(position["side"]) if state["book"] else None
        bid = side.get("bid") if side else None
        executable = (not state["kalshi_stale"] and state["orderbook_age"] is not None
                      and 0 <= state["orderbook_age"] <= MAX_BOOK_AGE
                      and bid is not None and bid["count"] >= PAPER_SIZE)
        if not executable:
            bid = None
        if bid is not None:
            position["last_bid"] = bid["price"]
            position["last_bid_at"] = now
            position["last_bid_depth"] = bid["count"]
        fired = self._fired_exit_rule(state, now, position, bid)
        waiting = position.get("exit_pending")
        rule = waiting["rule"] if waiting else fired
        if rule is None:
            return
        if bid is None:
            if fired == "time_stop":
                self._record_missed_exit(state, now, position, rule,
                                         "no executable full-size fresh bid by the time stop; settling at the public result")
                return
            if waiting is None:
                position["exit_pending"] = {"rule": rule, "since": now}
            position["note"] = f"{rule} exit fired; retrying each second for an executable bid"
            return
        self._close_at_bid(state, now, position, rule, bid, side)

    def _fired_exit_rule(self, state, now, position, bid):
        """First exit rule that fires, in the fixed precedence order."""
        left = state["seconds_left"]
        entry = position["entry_ask"]
        rules = {
            "take_profit": lambda: bid is not None and bid["price"] >= entry + TAKE_PROFIT,
            "stop": lambda: bid is not None and bid["price"] <= entry - STOP_LOSS,
            # A stale feed's tally and flow are not evidence of a flip.
            "flip": lambda: not state["stale"] and flipped(state, position["side"]),
            "max_hold": lambda: now - position["entry_at"] >= MAX_HOLD_SECONDS,
            "time_stop": lambda: left is not None and left <= TIME_STOP_SECONDS_LEFT,
        }
        for rule in EXIT_RULE_ORDER:
            if rules[rule]():
                return rule
        return None

    def _inputs_row(self, state):
        return {
            "tally_bull": finite(state["tally_bull"], 0), "tally_bear": finite(state["tally_bear"], 0),
            "rsi": finite(state["rsi"], 4), "bb_pctb": finite(state["bb_pctb"], 6),
            "bb_mid": finite(state["bb_mid"], 4), "composite": finite(state["composite"], 4),
            "average_60s": finite(state["average_60s"], 4),
            "imbalance": finite(state["imbalance"], 6),
            "previous_imbalance": finite(state["previous_imbalance"], 6),
            "close_1m_prev": finite(state["close_1m_prev"], 4),
            "close_1m": finite(state["close_1m"], 4),
            "seconds_left": finite(state["seconds_left"], 2),
            "orderbook_age": finite(state["orderbook_age"], 2),
        }

    # ---------------------------------------------------------- entry

    def _consider_entry(self, state, now):
        direction = candidate_direction(state)
        quote_checks = side_quote_checks(state, direction) if direction else {"ask": None, "bid": None,
                                                                             "spread": None, "checks": []}
        if direction is not None and not self._entry_blockers(state, now, direction, quote_checks):
            self._enter(state, now, direction, quote_checks)

    def _enter(self, state, now, direction, quote_checks):
        ask, bid = quote_checks["ask"], quote_checks["bid"]
        contracts = Decimal(PAPER_SIZE)
        cost = contracts * ask["price"]
        fee, raw = taker_fee(contracts, ask["price"])
        fee_01, _raw_01 = taker_fee(contracts, ask["price"], FEE_ROUNDING_INCREMENT_SENSITIVITY)
        row = {"type": "enter", "at": iso(now), "at_epoch": now,
               "ticker": state["ticker"], "side": direction,
               "rule": f"enter_{direction}", "contracts": PAPER_SIZE,
               "ask": money(ask["price"]), "ask_depth": money(ask["count"], 2),
               "bid": money(bid["price"]) if bid else None,
               "bid_depth": money(bid["count"], 2) if bid else None,
               "spread": money(quote_checks["spread"]),
               "book_age": finite(state["orderbook_age"], 2),
               "position_cost": money(cost),
               "fee_raw": money(raw, 6), "fee": money(fee), "fee_01": money(fee_01),
               "all_in_cost": money(cost + fee), "all_in_cost_01": money(cost + fee_01),
               "net_dollars": None, "net_dollars_01": None,
               "inputs": self._inputs_row(state)}
        if not self._write(row):
            return
        self.position = {"ticker": state["ticker"], "side": direction,
                         "entry_ask": ask["price"], "entry_depth": ask["count"],
                         "entry_fee": fee, "entry_cost": cost, "entry_at": now,
                         "close_at": now + state["seconds_left"],
                         "contracts": PAPER_SIZE, "rule": row["rule"],
                         "inputs": row["inputs"], "last_bid": bid["price"] if bid else None,
                         "last_bid_at": now, "last_bid_depth": bid["count"] if bid else None,
                         "note": None}
        self.last_event = row

    # ---------------------------------------------------------- exits

    def _exit_row(self, state, now, position, rule, outcome, note):
        side = (state["book"].get(position["side"])
                if state["book"] and state["ticker"] == position["ticker"] else None)
        bid = side.get("bid") if side and outcome == "filled" else None
        row = {"type": "exit", "outcome": outcome, "rule": rule,
               "at": iso(now), "at_epoch": now,
               "ticker": position["ticker"], "side": position["side"],
               "contracts": position["contracts"],
               "entry_ask": money(position["entry_ask"]),
               "entry_depth": money(position["entry_depth"], 2),
               "entry_at": iso(position["entry_at"]), "entry_at_epoch": position["entry_at"],
               "held_seconds": finite(now - position["entry_at"], 2),
               "bid": money(bid["price"]) if bid else None,
               "bid_depth": money(bid["count"], 2) if bid else None,
               "spread": (money(side["ask"]["price"] - bid["price"])
                          if bid and side and side.get("ask") else None),
               "book_age": finite(state["orderbook_age"], 2),
               "entry_fee": money(position["entry_fee"]), "exit_fee": None,
               "net_dollars": None, "net_dollars_01": None,
               "inputs": self._inputs_row(state), "note": note}
        return row

    def _close_at_bid(self, state, now, position, rule, bid, side):
        contracts = Decimal(position["contracts"])
        exit_fee, raw = taker_fee(contracts, bid["price"])
        exit_fee_01, _raw_01 = taker_fee(contracts, bid["price"], FEE_ROUNDING_INCREMENT_SENSITIVITY)
        gross = (bid["price"] - position["entry_ask"]) * contracts
        net = gross - position["entry_fee"] - exit_fee
        net_01 = gross - taker_fee(contracts, position["entry_ask"],
                                   FEE_ROUNDING_INCREMENT_SENSITIVITY)[0] - exit_fee_01
        row = self._exit_row(state, now, position, rule, "filled",
                             f"{rule} exit at the book bid")
        row.update({"gross_dollars": money(gross), "exit_fee_raw": money(raw, 6),
                    "exit_fee": money(exit_fee), "exit_fee_01": money(exit_fee_01),
                    "net_dollars": money(net), "net_dollars_01": money(net_01),
                    "net_per_contract": money(net / contracts)})
        waiting = position.get("exit_pending")
        if waiting:
            row.update({"exit_fired_at_epoch": waiting["since"],
                        "exit_retry_seconds": finite(now - waiting["since"], 2)})
        if not self._write(row):
            return
        self._record_trade(row)
        self.position = None

    def _record_missed_exit(self, state, now, position, rule, note):
        row = self._exit_row(state, now, position, rule, "no_bid", note)
        row.update({"settles_at_result": True, "net_dollars": None, "net_dollars_01": None})
        if not self._write(row):
            return
        self.pending = {"ticker": position["ticker"], "rule": rule, "entry": position}
        self.position = None
        self.last_event = row

    def _record_trade(self, row):
        self.trades.append({"ticker": row["ticker"], "side": row["side"], "rule": row["rule"],
                            "outcome": row["outcome"], "at_epoch": row["at_epoch"],
                            "contracts": row["contracts"],
                            "net_dollars": number(row.get("net_dollars")),
                            "net_dollars_01": number(row.get("net_dollars_01"))})
        trade = self.trades[-1]
        net = trade["net_dollars"]
        trade["per_contract"] = None if net is None else net / trade["contracts"]
        trade["won"] = bool(net is not None and net > 0)
        self.last_exit_at = row["at_epoch"]
        self.last_event = row

    # ---------------------------------------------------------- settlement

    def settle(self, ticker, result, now):
        """Settle a held paper position at the market's public result.

        Called by the feed when it observes ``result`` ("yes"/"no"). No fee is
        charged on settlement. Returns the JSON-safe view.
        """
        with self.lock:
            now = float(now)
            result = str(result or "").lower()
            if result not in SIDES:
                self.note("settle: result is not yes/no")
                return self.view(now=now)
            holder = self.position or (self.pending or {}).get("entry")
            if holder is None or holder["ticker"] != ticker:
                return self.view(now=now)
            if self.position is not None:
                self._record_missed_exit(self.state, now, holder, "time_stop",
                                         "public result arrived before the next paper tick; no executable exit bid")
                if self.position is not None:
                    return self.view(now=now)  # journal write failed; retry later
                holder = self.pending["entry"]
            attempt = self.pending["rule"] if self.pending and self.pending["ticker"] == ticker else None
            contracts = Decimal(holder["contracts"])
            payoff = contracts if holder["side"] == result else Decimal(0)
            net = payoff - holder["entry_cost"] - holder["entry_fee"]
            net_01 = (payoff - holder["entry_cost"]
                      - taker_fee(contracts, holder["entry_ask"], FEE_ROUNDING_INCREMENT_SENSITIVITY)[0])
            row = self._exit_row(self.state, now, holder, "settlement", "settled",
                                 "settled at the public result; no fee on settlement")
            row.update({"result": result, "payoff_dollars": money(payoff),
                        "exit_attempt_rule": attempt,
                        "gross_dollars": money(payoff - holder["entry_cost"]),
                        "exit_fee": money(Decimal(0)), "exit_fee_01": money(Decimal(0)),
                        "net_dollars": money(net), "net_dollars_01": money(net_01),
                        "net_per_contract": money(net / contracts)})
            if not self._write(row):
                return self.view(now=now)
            self._record_trade(row)
            self.position = None
            self.pending = None
            return self.view(now=now)

    def settlement_due(self, now):
        """Return a held market ticker after its close for a public GET poll."""
        with self.lock:
            holder = self.position or (self.pending or {}).get("entry")
            return (holder["ticker"] if holder is not None and now >= holder["close_at"]
                    else None)

    def snapshot_view(self, now):
        with self.lock:
            return self.view(now=now)

    # ---------------------------------------------------------- view

    def _hold_view(self, state, now):
        position = self.position
        side = state["book"].get(position["side"]) if state["book"] else None
        bid = side.get("bid") if side else None
        fresh_bid = (bid is not None and not state["kalshi_stale"]
                     and state["orderbook_age"] is not None
                     and 0 <= state["orderbook_age"] <= MAX_BOOK_AGE
                     and bid["count"] >= PAPER_SIZE)
        bid_price = bid["price"] if fresh_bid else None
        waiting = position.get("exit_pending")
        if bid_price is None:
            label = f"HOLD {position['side'].upper()} · entry {cents(position['entry_ask'], 0):.0f}¢ · no bid"
            if waiting:
                label = f"EXIT PENDING ({waiting['rule']}) · {label}"
            change = None
        else:
            change = bid_price - position["entry_ask"]
            held = int(max(0.0, now - position["entry_at"]))
            label = (f"HOLD {position['side'].upper()} · entry {cents(position['entry_ask'], 0):.0f}¢ · "
                     f"bid {cents(bid_price, 0):.0f}¢ · {cents(change, 0):+.0f}¢ · "
                     f"{held // 60}:{held % 60:02d} held")
        return {"ticker": position["ticker"], "side": position["side"],
                "entry_ask": money(position["entry_ask"]),
                "entry_cents": cents(position["entry_ask"], 2),
                "entry_at": iso(position["entry_at"]), "entry_at_epoch": position["entry_at"],
                "entry_rule": position["rule"], "contracts": position["contracts"],
                "bid": money(bid_price), "bid_cents": cents(bid_price, 2),
                "bid_depth": money(bid["count"], 2) if fresh_bid else None,
                "change_cents": cents(change, 2),
                "change_dollars": money(change * position["contracts"]) if change is not None else None,
                "held_seconds": finite(now - position["entry_at"], 2),
                "flip_met": flipped(state, position["side"]),
                "exit_pending": waiting["rule"] if waiting else None,
                "note": position.get("note"),
                "label": label}

    def _last_label(self, event):
        if not event:
            return None
        outcome = event.get("outcome")
        if outcome == "aborted":
            return "ABORTED (restart) · excluded from expectancy"
        if outcome == "no_bid":
            return f"EXIT ({event.get('rule')}) · no bid — settling at the public result"
        if outcome == "settled":
            per_contract = decimal_value(event.get("net_per_contract"))
            return (f"SETTLED {event.get('result', '').upper()} "
                    f"{cents(per_contract, 2):+.2f}¢ after fees" if per_contract is not None else "SETTLED")
        if outcome == "filled":
            per_contract = decimal_value(event.get("net_per_contract"))
            return (f"EXIT ({event.get('rule')}) {cents(per_contract, 2):+.2f}¢ after fees"
                    if per_contract is not None else f"EXIT ({event.get('rule')})")
        return None

    def conditions_view(self, state, now):
        """Condition ticks for the panel while flat (momentum legs then gates)."""
        direction = display_direction(state)
        rows = []
        if direction is not None:
            for name, met, value, detail in entry_legs(state, direction):
                rows.append({"name": name, "met": bool(met), "value": finite(value, 6), "detail": detail})
        checks, _waited = eligibility(state, self.last_exit_at, now, held=False)
        quote_checks = side_quote_checks(state, direction) if direction else {"checks": []}
        for name, met, detail in list(checks) + list(quote_checks["checks"]):
            rows.append({"name": name, "met": bool(met), "detail": detail})
        return direction, rows

    def view(self, now=None):
        """JSON-safe summary for ``/api/state.scalp``."""
        now = float(now) if now is not None else time.time()
        state = self.state
        blockers = []
        direction = None
        conditions = []
        if self.position is None and self.pending is None:
            direction, conditions = self.conditions_view(state, now)
            quote_checks = side_quote_checks(state, direction) if direction else {"checks": [], "ask": None,
                                                                                 "bid": None, "spread": None}
            blockers = self._entry_blockers(state, now, direction, quote_checks)
        if self.position is not None:
            position = self._hold_view(state, now)
            label = position["label"]
        elif self.pending is not None:
            entry = self.pending["entry"]
            label = f"SETTLING {entry['side'].upper()} · awaiting the public result"
            position = {"ticker": entry["ticker"], "side": entry["side"],
                        "entry_ask": money(entry["entry_ask"]),
                        "entry_cents": cents(entry["entry_ask"], 2),
                        "entry_at": iso(entry["entry_at"]), "entry_at_epoch": entry["entry_at"],
                        "entry_rule": entry["rule"], "contracts": entry["contracts"],
                        "bid": None, "bid_cents": None, "bid_depth": None,
                        "change_cents": None, "change_dollars": None,
                        "held_seconds": finite(now - entry["entry_at"], 2),
                        "flip_met": None, "exit_pending": None, "note": f"exit rule {self.pending['rule']} had no bid",
                        "label": label}
        else:
            position = None
            ask = side_quote_checks(state, direction)["ask"] if direction else None
            recent_exit = (self.last_event and self.last_event.get("type") == "exit"
                           and number(self.last_event.get("at_epoch")) is not None
                           and 0 <= now - self.last_event["at_epoch"] < EXIT_DISPLAY_SECONDS)
            if recent_exit:
                label = self._last_label(self.last_event) or "FLAT · watching"
            elif direction is not None and ask is not None and blocker_free(blockers):
                label = (f"ENTER {direction.upper()} @ {cents(ask['price'], 0):.0f}¢ — "
                         f"tally {state['tally_lead']:+d}, flow {state['imbalance']:+.2f}, "
                         f"{'above' if direction == 'yes' else 'below'} 60s avg")
            else:
                label = "FLAT · watching"
        stats = summarize(self.trades, self.aborted)
        view = {
            "experimental": True, "paper": True, "validated": False,
            "strategy": STRATEGY,
            "as_of": iso(now), "as_of_epoch": now,
            "state": "holding" if self.position else "settling" if self.pending else "flat",
            "label": label, "direction": direction,
            "conditions": conditions, "blockers": blockers,
            "position": position,
            "last": self.last_event, "last_label": self._last_label(self.last_event),
            "stats": stats,
            "size": PAPER_SIZE,
            "thresholds": {
                "entry_tally_lead": ENTRY_TALLY_LEAD, "entry_flow_imbalance": ENTRY_FLOW_IMBALANCE,
                "flip_tally_lead": FLIP_TALLY_LEAD, "flip_flow_imbalance": FLIP_FLOW_IMBALANCE,
                "take_profit": float(TAKE_PROFIT), "min_seconds_left": MIN_SECONDS_LEFT,
                "max_book_age": MAX_BOOK_AGE, "min_ask": float(MIN_ASK), "max_ask": float(MAX_ASK),
                "max_spread": float(MAX_SPREAD), "cooldown_seconds": COOLDOWN_SECONDS,
                "stop_loss": float(STOP_LOSS), "max_hold_seconds": MAX_HOLD_SECONDS,
                "time_stop_seconds_left": TIME_STOP_SECONDS_LEFT,
                "fee_multiplier": float(FEE_MULTIPLIER),
                "fee_rounding_increment": float(FEE_ROUNDING_INCREMENT),
                "fee_rounding_increment_sensitivity": float(FEE_ROUNDING_INCREMENT_SENSITIVITY),
            },
            "errors": list(self.errors),
        }
        return view


def blocker_free(blockers):
    """True when no eligibility gate is unmet."""
    return not blockers
