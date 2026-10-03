#!/usr/bin/env python3
"""Descriptive, offline tally outcomes; never consumed by the dashboard.

Sections: the legacy opening-lean hit rates (unchanged), the size of the move
at settlement, the in-window minute horizon (how the tally is used for
scalping), the tally against the market's own price, and one pre-declared
primary measure. Everything except the primary measure is exploratory.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random

TIMEFRAMES = ("1m", "5m", "10m", "15m", "1h")

# Pre-declared primary measure (fixed 2026-10-03, before any minute rows
# existed). Spearman correlation of the 1m family_net with the composite change
# over the next PRIMARY_HORIZON minutes, across minute rows, with a 95% block
# bootstrap that resamples whole windows. It is evaluated once, on the first
# PRIMARY_MIN_WINDOWS windows (by open time) that have minute rows, and only
# when the report runs on or after PRIMARY_EVALUATE_AT with that many windows.
PRIMARY_TIMEFRAME = "1m"
PRIMARY_HORIZON = 3
PRIMARY_MIN_WINDOWS = 800
PRIMARY_EVALUATE_AT = datetime(2026, 10, 14, tzinfo=timezone.utc)

HORIZONS = (1, 3)
BOOTSTRAP_SEED = 20261003
BOOTSTRAP_ROUNDS = 1000
MARKET_BAND = (0.45, 0.55)
EXPLORATORY = "[exploratory]"
EXPLORATORY_NOTE = ("Sections marked [exploratory] make many comparisons; expect some to look good by chance. "
                    "Only the PRIMARY section is a pre-declared test.")


def wilson95(hits, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


def load_rows(path):
    """All research rows: opening, settled and minute records."""
    opened, settled, minutes = {}, {}, {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                kind = row.get("type")
                if kind == "open":
                    opened.setdefault(row["ticker"], row)
                elif kind == "settled":
                    settled.setdefault(row["ticker"], row)
                elif kind == "minute":
                    minutes.setdefault(row["ticker"], {}).setdefault(row["minute"], row)
    except FileNotFoundError:
        pass
    return opened, settled, minutes


def load_records(path):
    opened, settled, _ = load_rows(path)
    return opened, settled


def summarize(opened, settled):
    summary = {}
    for tf in TIMEFRAMES:
        counts = {"bullish": 0, "bearish": 0, "mixed": 0, "scored": 0, "hits": 0}
        for ticker, row in opened.items():
            frame = row.get("timeframes", {}).get(tf)
            if not frame:
                continue
            lean = frame.get("lean")
            if lean not in ("bullish", "bearish", "mixed"):
                continue
            counts[lean] += 1
            outcome = settled.get(ticker, {}).get("result")
            if lean == "mixed" or outcome not in ("yes", "no"):
                continue
            counts["scored"] += 1
            counts["hits"] += (lean == "bullish" and outcome == "yes") or (lean == "bearish" and outcome == "no")
        summary[tf] = counts
    return summary


# --- statistics (stdlib only) ---------------------------------------------

def ranks(values):
    """Average ranks (1-based), ties sharing the mean of their positions."""
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(pairs):
    """Spearman rank correlation of (x, y) pairs; None when undefined."""
    if len(pairs) < 3:
        return None
    rx, ry = ranks([x for x, _ in pairs]), ranks([y for _, y in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def block_bootstrap(groups, statistic, rounds=BOOTSTRAP_ROUNDS, seed=BOOTSTRAP_SEED):
    """95% percentile interval, resampling whole groups (windows) with replacement.

    ``groups`` is a list of lists of observations; each window's observations
    stay together because minute rows inside one window overlap in time.
    """
    groups = [g for g in groups if g]
    if len(groups) < 10:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(rounds):
        sample = [obs for _ in groups for obs in groups[rng.randrange(len(groups))]]
        value = statistic(sample)
        if value is not None:
            stats.append(value)
    if len(stats) < rounds * 0.9:
        return None
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]


def ci_text(interval, digits=3):
    if interval is None:
        return "CI unavailable"
    verdict = "excludes 0" if interval[0] > 0 or interval[1] < 0 else "includes 0"
    return f"95% [{interval[0]:+.{digits}f}, {interval[1]:+.{digits}f}] ({verdict})"


def legacy_net(frame):
    return frame["bull"] - frame["bear"]


# --- outcomes ----------------------------------------------------------------

def mid(row):
    """YES mid in dollars from a minute row's fresh quotes, else None."""
    bid, ask = row.get("yes_bid"), row.get("yes_ask")
    try:
        bid, ask = float(bid["price"]), float(ask["price"])  # early rows stored decimal strings
    except (TypeError, KeyError, ValueError):
        return None
    return (bid + ask) / 2 if 0 < bid <= ask < 1 else None


def open_mid(row):
    try:
        bid, ask = float(row["yes_bid"]), float(row["yes_ask"])
    except (KeyError, TypeError, ValueError):
        return None
    return (bid + ask) / 2 if 0 < bid <= ask < 1 else None


def settlement_moves(opened, settled):
    """Per window: (ticker, open row, signed $ move, move in sigma or None)."""
    out = []
    for ticker, row in opened.items():
        result = settled.get(ticker, {})
        try:
            move = float(result["expiration_value"]) - float(row["target"])
        except (KeyError, TypeError, ValueError):
            continue
        sigma = row.get("sigma_1m_usd")
        scaled = move / (sigma * math.sqrt(15)) if sigma else None
        out.append((ticker, row, move, scaled))
    return out


def forward_changes(minutes, horizon):
    """Per window: [(minute row, composite change, YES mid change in cents)]."""
    groups = {}
    for ticker, rows in minutes.items():
        for minute, row in sorted(rows.items()):
            later = rows.get(minute + horizon)
            if later is None:
                continue
            composite = (later["composite"] - row["composite"]
                         if later.get("composite") is not None and row.get("composite") is not None
                         else None)
            a, b = mid(row), mid(later)
            yes_cents = (b - a) * 100 if a is not None and b is not None else None
            groups.setdefault(ticker, []).append((row, composite, yes_cents))
    return groups


def family_net(row, tf):
    frame = (row.get("timeframes") or {}).get(tf) or {}
    return frame.get("family_net")


# --- sections ------------------------------------------------------------------

def legacy_section(opened, settled):
    lines = [f"Opening records: {len(opened)}; settled records: {len(settled)}",
             "Descriptive research only. Mixed leans and unresolved outcomes are excluded from hit rates.",
             EXPLORATORY_NOTE,
             "", "== Legacy: opening lean vs settlement " + EXPLORATORY + " =="]
    for tf, count in summarize(opened, settled).items():
        n, hits = count["scored"], count["hits"]
        interval = wilson95(hits, n)
        rate = (f"{hits}/{n} ({hits / n:.1%}); Wilson 95% "
                f"[{interval[0]:.1%}, {interval[1]:.1%}]" if interval else "0/0; Wilson 95% unavailable")
        lines.append(f"{tf}: bullish {count['bullish']}, bearish {count['bearish']}, mixed {count['mixed']}; hit rate {rate}")
        if n < 100:
            lines.append("  n under 100: too small to mean anything.")
    return lines


def settlement_section(opened, settled):
    moves = settlement_moves(opened, settled)
    lines = ["", "== Size of the move at settlement " + EXPLORATORY + " ==",
             f"Windows with a target and settlement value: {len(moves)}. "
             "Spearman of the opening net score with (settlement value - target); bootstrap over windows."]
    for tf in TIMEFRAMES:
        for label, net in (("legacy bull-bear", legacy_net), ("family_net", lambda f: f.get("family_net"))):
            groups = []
            for _, row, move, _ in moves:
                frame = (row.get("timeframes") or {}).get(tf)
                value = net(frame) if frame else None
                if value is not None:
                    groups.append([(value, move)])
            if not groups:
                continue
            pairs = [g[0] for g in groups]
            rho = spearman(pairs)
            lines.append(f"{tf} {label}: n {len(pairs)}, rho "
                         f"{'n/a' if rho is None else f'{rho:+.3f}'}, {ci_text(block_bootstrap(groups, spearman))}")
    scaled = [m[3] for m in moves if m[3] is not None]
    if scaled:
        lines.append(f"Move in sigma units (1m realized sigma x sqrt 15): n {len(scaled)}, "
                     f"mean {sum(scaled) / len(scaled):+.3f}")
    return lines


def minute_section(minutes):
    lines = ["", "== Minute horizon: 1m tally vs what happened next " + EXPLORATORY + " ==",
             f"Windows with minute rows: {len(minutes)}; minute rows: {sum(len(r) for r in minutes.values())}. "
             "Block bootstrap resamples whole windows."]
    for horizon in HORIZONS:
        groups = forward_changes(minutes, horizon)
        for name, index, unit in (("composite", 1, "$"), ("YES mid", 2, "c")):
            def obs(row_change):
                row, value = row_change[0], row_change[index]
                net = family_net(row, "1m")
                return None if net is None or value is None else (net, value)
            by_window = [[o for o in map(obs, rows) if o is not None] for rows in groups.values()]
            pairs = [o for g in by_window for o in g]
            if not pairs:
                lines.append(f"next {horizon} min {name}: no data yet")
                continue
            rho = spearman(pairs)
            lines.append(f"next {horizon} min {name}: n {len(pairs)} rows, rho "
                         f"{'n/a' if rho is None else f'{rho:+.3f}'}, {ci_text(block_bootstrap(by_window, spearman))}")
            buckets = {}
            for net, value in pairs:
                buckets.setdefault(net, []).append(value)
            lines.append("  mean change by family_net: " + ", ".join(
                f"{net:+d}: {sum(v) / len(v):+.2f}{unit} (n {len(v)})" for net, v in sorted(buckets.items())))
    return lines


def market_section(opened, settled, minutes):
    """Kalshi usually lists the market as initialized at :00 with no quotes, so
    the market's price comes from the minute-1 row when the opening one is missing."""
    lines = ["", "== Legacy 1m lean vs the market's side near open " + EXPLORATORY + " ==",
             "Market price: YES mid at open, or at minute 1 when no opening quote was observed."]
    agree, disagree, band = [0, 0], [0, 0], [0, 0]  # [hits, scored]
    agreements = with_quote = 0
    for ticker, row in opened.items():
        lean = (row.get("timeframes") or {}).get("1m", {}).get("lean")
        outcome = settled.get(ticker, {}).get("result")
        price = open_mid(row)
        if price is None and 1 in minutes.get(ticker, {}):
            price = mid(minutes[ticker][1])
        if price is None or lean not in ("bullish", "bearish"):
            continue
        with_quote += 1
        market_yes = price > 0.5
        same = (lean == "bullish") == market_yes
        agreements += same
        if outcome not in ("yes", "no"):
            continue
        hit = (lean == "bullish") == (outcome == "yes")
        bucket = agree if same else disagree
        bucket[0] += hit
        bucket[1] += 1
        if MARKET_BAND[0] <= price <= MARKET_BAND[1]:
            band[0] += hit
            band[1] += 1
    lines.append(f"Leaning windows with a market price: {with_quote}; lean on the market's side: {agreements}")
    for label, (hits, n) in (("lean agrees with the market", agree), ("lean disagrees with the market", disagree),
                             (f"market mid {MARKET_BAND[0] * 100:.0f}-{MARKET_BAND[1] * 100:.0f}c", band)):
        interval = wilson95(hits, n)
        lines.append(f"hit rate, {label}: " + (
            f"{hits}/{n} ({hits / n:.1%}); Wilson 95% [{interval[0]:.1%}, {interval[1]:.1%}]"
            if interval else "0/0"))
    return lines


def primary_result(opened, minutes, now):
    """The pre-declared primary measure, or a not-yet-evaluated status."""
    eligible = sorted((opened.get(t, {}).get("open_time") or min(r["captured_at"] for r in rows.values()), t)
                      for t, rows in minutes.items() if rows)
    status = {"windows": len(eligible), "needed": PRIMARY_MIN_WINDOWS,
              "evaluate_at": PRIMARY_EVALUATE_AT.isoformat()}
    if now < PRIMARY_EVALUATE_AT or len(eligible) < PRIMARY_MIN_WINDOWS:
        return {**status, "evaluated": False}
    chosen = {t for _, t in eligible[:PRIMARY_MIN_WINDOWS]}
    groups = forward_changes({t: minutes[t] for t in chosen}, PRIMARY_HORIZON)
    by_window = [[(family_net(row, PRIMARY_TIMEFRAME), change) for row, change, _ in rows
                  if family_net(row, PRIMARY_TIMEFRAME) is not None and change is not None]
                 for rows in groups.values()]
    pairs = [o for g in by_window for o in g]
    interval = block_bootstrap(by_window, spearman)
    return {**status, "evaluated": True, "rows": len(pairs), "rho": spearman(pairs), "ci": interval,
            "verdict": None if interval is None else
            ("CI excludes 0" if interval[0] > 0 or interval[1] < 0 else "CI includes 0")}


def primary_section(opened, minutes, now):
    result = primary_result(opened, minutes, now)
    lines = ["", "== PRIMARY (pre-declared 2026-10-03) ==",
             f"Spearman of {PRIMARY_TIMEFRAME} family_net with the next {PRIMARY_HORIZON} min composite change, "
             f"first {PRIMARY_MIN_WINDOWS} windows, block bootstrap over windows."]
    if not result["evaluated"]:
        lines.append(f"primary not yet evaluated ({result['windows']} windows / {PRIMARY_MIN_WINDOWS}; "
                     f"not before {PRIMARY_EVALUATE_AT:%Y-%m-%d %H:%M} UTC)")
    else:
        rho = result["rho"]
        lines.append(f"rows {result['rows']}, rho {'n/a' if rho is None else f'{rho:+.3f}'}, "
                     f"{ci_text(result['ci'])} -> {result['verdict'] or 'undetermined'}")
    return lines


def report(opened, settled, minutes=None, now=None):
    minutes = minutes or {}
    now = now or datetime.now(timezone.utc)
    lines = legacy_section(opened, settled)
    lines += settlement_section(opened, settled)
    lines += minute_section(minutes)
    lines += market_section(opened, settled, minutes)
    lines += primary_section(opened, minutes, now)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path, default=Path.home() / "btc-ta/state/tally_log.jsonl")
    args = parser.parse_args()
    print(report(*load_rows(args.path)))


if __name__ == "__main__":
    main()
