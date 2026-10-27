#!/usr/bin/env python3
"""Descriptive, offline tally outcomes; never consumed by the dashboard.

Sections: the legacy opening-lean hit rates (unchanged), the size of the move
at settlement, the in-window minute horizon (how the tally is used for
scalping), the tally against the market's own price, and one pre-declared
primary measure. Everything except the primary measure is exploratory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics

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


# --- record schema ---------------------------------------------------------------
# One check shared by every reader (this report, the server's startup load and
# log_tools), so a row that parses as JSON but can't be used is skipped and
# counted the same way everywhere instead of crashing a reader or becoming
# evidence. It checks every field a reader consumes:
# * numbers the server writes itself must be finite JSON numbers (a string
#   "101" would break later arithmetic); only Kalshi's `expiration_value` and
#   YES quote prices, which readers convert with float(), may also be numeric
#   strings ("NaN"/"inf" never pass);
# * timestamps must parse as ISO 8601;
# * per-timeframe counts (bull/bear/neutral, family_net) must be integers and
#   lean a string, with bull and bear present together.
# * optional levels contain finite numbers, nonnegative integer counts and boolean flags.
RECORD_TYPES = ("open", "settled", "close", "minute", "settled_value")
NUMERIC_FIELDS = ("composite", "composite_spot", "average_60s", "target", "sigma_1m_usd", "seconds_left",
                  "proxy_avg_60s", "brti", "brti_age", "brti_avg_60s",
                  "flow_imbalance_1m", "capture_delay_seconds", "flow_coverage_seconds")
STRING_NUMERIC_FIELDS = ("expiration_value",)
QUOTE_FIELDS = ("yes_bid", "yes_ask")
TIME_FIELDS = ("captured_at", "recorded_at", "open_time", "close_time", "target_observed_at")
COUNT_FIELDS = ("bull", "bear", "neutral", "family_net")


def finite_or_none(value, strings=False):
    """True for None or a finite number (or numeric string if ``strings``); booleans are not numbers."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    if isinstance(value, str) and not strings:
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def normalize_iso(value):
    """Normalize an ISO-8601 string so Python 3.9+ fromisoformat accepts any fractional length."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    t_pos = max(text.find("T"), text.find("t"), text.find(" "))
    dot = text.find(".", t_pos if t_pos != -1 else 0)
    if dot != -1:
        end = dot + 1
        while end < len(text) and text[end].isdigit():
            end += 1
        frac = text[dot + 1:end]
        if frac and len(frac) not in (3, 6):
            text = text[:dot + 1] + frac.ljust(6, "0")[:6] + text[end:]
    return text


def parse_iso_ts(value):
    parsed = datetime.fromisoformat(normalize_iso(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def is_timestamp(value):
    try:
        if not isinstance(value, str):
            return False
        parse_iso_ts(value)
        return True
    except (AttributeError, ValueError, TypeError):
        return False


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def record_problem(row):
    """Why a decoded log row is unusable, or None if it is a valid research record.

    Rows of other types are not research records and return "unknown type"."""
    if not isinstance(row, dict):
        return "not an object"
    kind = row.get("type")
    if kind not in RECORD_TYPES:
        return "unknown type"
    if not isinstance(row.get("ticker"), str) or not row["ticker"]:
        return "bad ticker"
    if kind == "minute":
        minute = row.get("minute")
        if not is_count(minute) or not 0 <= minute <= 15:
            return "bad minute"
        if row.get("captured_at") is None:
            return "bad captured_at"
    if kind == "settled" and row.get("result") not in ("yes", "no"):
        return "bad result"
    if kind == "settled_value" and row.get("expiration_value") is None:
        return "bad expiration_value"
    for key in TIME_FIELDS:
        if row.get(key) is not None and not is_timestamp(row[key]):
            return f"bad {key}"
    for key in NUMERIC_FIELDS:
        if not finite_or_none(row.get(key)):
            return f"bad {key}"
    for key in STRING_NUMERIC_FIELDS:
        if not finite_or_none(row.get(key), strings=True):
            return f"bad {key}"
    for key in QUOTE_FIELDS:
        quote = row.get(key)
        # Quote prices are read with float(), and early minute rows stored
        # book prices as decimal strings, so numeric strings are valid here.
        if not (finite_or_none(quote, strings=True)
                or isinstance(quote, dict) and finite_or_none(quote.get("price"), strings=True)):
            return f"bad {key}"
    for key in ("stale", "kalshi_stale"):
        if row.get(key) is not None and not isinstance(row[key], bool):
            return f"bad {key}"
    history_stale = row.get("history_stale")
    if history_stale is not None:
        if not isinstance(history_stale, list) or not all(isinstance(x, str) for x in history_stale):
            return "bad history_stale"
    samples = row.get("samples")
    if samples is not None and (not is_count(samples) or samples < 0):
        return "bad samples"
    brti_samples = row.get("brti_samples")
    if brti_samples is not None and (not is_count(brti_samples) or not 0 <= brti_samples <= 60):
        return "bad brti_samples"
    frames = row.get("timeframes")
    if frames is not None:
        if not isinstance(frames, dict) or not all(isinstance(f, dict) for f in frames.values()):
            return "bad timeframes"
        for frame in frames.values():
            levels = frame.get("levels")
            if levels is not None:
                if not isinstance(levels, dict):
                    return "bad levels"
                if any(not finite_or_none(levels.get(k)) for k in
                       ("support", "resistance", "support_dist_atr", "resistance_dist_atr")):
                    return "bad levels"
                if any(levels.get(k) is not None and
                       (not is_count(levels[k]) or levels[k] < 0) for k in
                       ("support_touches", "resistance_touches", "zones_to_target")):
                    return "bad levels"
                if any(levels.get(k) is not None and not isinstance(levels[k], bool)
                       for k in ("at_price", "target_in_zone")):
                    return "bad levels"
            for key in COUNT_FIELDS:
                if frame.get(key) is not None and not is_count(frame[key]):
                    return f"bad {key}"
            for key in ("rsi", "macd_hist"):
                if not finite_or_none(frame.get(key)):
                    return f"bad {key}"
            if ("bull" in frame) != ("bear" in frame):
                return "bad bull/bear"
            if frame.get("lean") is not None and not isinstance(frame["lean"], str):
                return "bad lean"
    return None


def wilson95(hits, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


def load_rows(path, skipped_lines=None, closes=None):
    """All research rows: opening, settled and minute records.

    Pass a dict as ``closes`` to also collect closing-minute proxy rows by ticker.
    """
    opened, settled, minutes, late_values = {}, {}, {}, {}
    skipped = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    skipped += 1
                    continue
                problem = record_problem(row)
                if problem == "unknown type":
                    continue  # not a research row; not counted as damage
                if problem:
                    skipped += 1
                    continue
                kind = row["type"]
                if kind == "open":
                    opened.setdefault(row["ticker"], row)
                elif kind == "settled":
                    settled.setdefault(row["ticker"], row)
                elif kind == "settled_value":
                    late_values.setdefault(row["ticker"], row["expiration_value"])
                elif kind == "close":
                    if closes is not None:
                        closes.setdefault(row["ticker"], row)
                else:
                    minutes.setdefault(row["ticker"], {}).setdefault(row["minute"], row)
    except FileNotFoundError:
        pass
    # A settled row written before Kalshi published its value is completed by
    # a later `settled_value` row (see ta_copilot.SETTLE_VALUE_WAIT).
    for ticker, value in late_values.items():
        if ticker in settled and settled[ticker].get("expiration_value") is None:
            settled[ticker] = {**settled[ticker], "expiration_value": value}
    if skipped_lines is not None:
        skipped_lines.append(skipped)
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
    return frame["bull"] - frame["bear"] if "bull" in frame else None


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


def suppressed(row):
    """A reading the dashboard hides: a stale spot, or a stale 1m history."""
    return row.get("stale") is True or "1m" in (row.get("history_stale") or [])


def minute_section(minutes):
    # forward_changes is shared with the primary measure, so it keeps every
    # row; only this exploratory section drops the readings the UI suppresses.
    # One observation per (horizon, window, base row): a stale base row is
    # counted once for each horizon it could have contributed to.
    groups_by_horizon = {horizon: forward_changes(minutes, horizon) for horizon in HORIZONS}
    excluded = sum(1 for groups in groups_by_horizon.values() for rows in groups.values()
                   for row, _, _ in rows if suppressed(row))
    lines = ["", "== Minute horizon: 1m tally vs what happened next " + EXPLORATORY + " ==",
             f"Windows with minute rows: {len(minutes)}; minute rows: {sum(len(r) for r in minutes.values())}; "
             f"stale observations excluded: {excluded}. Block bootstrap resamples whole windows."]
    for horizon in HORIZONS:
        groups = groups_by_horizon[horizon]
        for name, index, unit in (("composite", 1, "$"), ("YES mid", 2, "c")):
            def obs(row_change):
                row, value = row_change[0], row_change[index]
                if suppressed(row):
                    return None
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


def levels_section(opened, settled, minutes):
    lines = ["", "== Support/resistance zones (1m, descriptive) " + EXPLORATORY + " ==",
             "Next 3 minutes vs the nearest zone; crossed means beyond its midpoint after 3 minutes."]
    labels = ("<0.5", "0.5–1", "1–2", "≥2")
    buckets = {(side, label): [] for side in ("support", "resistance") for label in labels}
    targets = {label: [0, 0] for label in ("0 zones", "≥1 zones", "target in zone")}
    reasons = dict.fromkeys(("used", "no minute-1 row", "suppressed", "no levels",
                             "missing fields", "awaiting settlement"), 0)

    def proportion(sample):
        return sum(sample) / len(sample) if sample else None

    def summary(label, counts):
        hits, n = counts
        interval = wilson95(hits, n)
        return label + (f": n {n}, reached {hits} ({hits / n:.1%}); "
                        f"Wilson 95% [{interval[0]:.1%}, {interval[1]:.1%}]"
                        if interval else ": no data yet")

    for ticker, rows in sorted(minutes.items()):
        window = {key: [] for key in buckets}
        for minute, row in sorted(rows.items()):
            later = rows.get(minute + 3)
            if later is None or suppressed(row) or suppressed(later):
                continue
            levels = ((row.get("timeframes") or {}).get("1m") or {}).get("levels")
            if levels is None or row.get("composite") is None or later.get("composite") is None:
                continue
            for side in ("support", "resistance"):
                zone, dist = levels.get(side), levels.get(side + "_dist_atr")
                if zone is None or dist is None:
                    continue
                if row["composite"] < zone if side == "support" else row["composite"] > zone:
                    continue
                distance = abs(dist)
                label = labels[sum(distance >= edge for edge in (0.5, 1, 2))]
                crossed = later["composite"] < zone if side == "support" else later["composite"] > zone
                window[side, label].append(crossed)
        for key, observations in window.items():
            if observations:
                buckets[key].append(observations)
    universe = set(opened) | set(minutes)
    for ticker in sorted(universe):
        row = minutes.get(ticker, {}).get(1)
        levels = ((row.get("timeframes") or {}).get("1m") or {}).get("levels") if row is not None else None
        value = settled.get(ticker, {}).get("expiration_value")
        if row is None:
            reason = "no minute-1 row"
        elif suppressed(row):
            reason = "suppressed"
        elif levels is None:
            reason = "no levels"
        elif row.get("composite") is None or row.get("target") is None or levels.get("zones_to_target") is None:
            reason = "missing fields"
        elif value is None:
            reason = "awaiting settlement"
        else:
            reason = "used"
        reasons[reason] += 1
        if reason != "used":
            continue
        price, target = row["composite"], row["target"]
        value = float(value)
        reached = value >= target if price < target else value <= target if price > target else value == target
        groups = ["0 zones" if levels["zones_to_target"] == 0 else "≥1 zones"]
        if levels.get("target_in_zone") is True:
            groups.append("target in zone")
        for label in groups:
            targets[label][0] += reached
            targets[label][1] += 1
    for (side, label), groups in buckets.items():
        sample = [obs for group in groups for obs in group]
        n, hits = len(sample), sum(sample)
        interval = block_bootstrap(groups, proportion)
        uncertainty = (f"block bootstrap 95% [{interval[0]:.1%}, {interval[1]:.1%}]"
                       if interval is not None else "too few windows for an interval")
        rate = f"{hits / n:.1%}" if n else "no data yet"
        lines.append(f"{side}, {label} ATR: {n} obs, {len(groups)} windows, crossed {hits} ({rate}); {uncertainty}")
    lines.append("Zones between price and target at minute 1.")
    lines.append(f"Windows: {len(universe)} total · " + " · ".join(f"{n} {reason}" for reason, n in reasons.items()))
    for label, counts in targets.items():
        lines.append(summary(label, counts))
    return lines


def primary_result(opened, minutes, now):
    """The pre-declared primary measure, or a not-yet-evaluated status."""
    # Windows are ordered by parsed UTC time, not by the raw ISO string: as
    # text, '...T00:00:00.500000Z' sorts before '...T00:00:00Z', which would put
    # a later window first. The ticker breaks ties as before.
    def opened_at(ticker, rows):
        open_time = opened.get(ticker, {}).get("open_time")
        if not open_time:
            open_time = min((r["captured_at"] for r in rows.values()),
                            key=lambda text: datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        return datetime.fromisoformat(open_time.replace("Z", "+00:00")).timestamp()

    eligible = sorted((opened_at(t, rows), t) for t, rows in minutes.items() if rows)
    status = {"windows": len(eligible), "needed": PRIMARY_MIN_WINDOWS,
              "evaluate_at": PRIMARY_EVALUATE_AT.isoformat()}
    if now < PRIMARY_EVALUATE_AT or len(eligible) < PRIMARY_MIN_WINDOWS:
        return {**status, "evaluated": False}
    # A list, not a set: the window order feeds the bootstrap, and set order
    # follows PYTHONHASHSEED, which made the interval differ between runs.
    chosen = [t for _, t in eligible[:PRIMARY_MIN_WINDOWS]]
    groups = forward_changes({t: minutes[t] for t in chosen}, PRIMARY_HORIZON)
    by_window = [[(family_net(row, PRIMARY_TIMEFRAME), change) for row, change, _ in rows
                  if family_net(row, PRIMARY_TIMEFRAME) is not None and change is not None]
                 for rows in groups.values()]
    pairs = [o for g in by_window for o in g]
    interval = block_bootstrap(by_window, spearman)
    return {**status, "evaluated": True, "rows": len(pairs),
            "contributing_windows": sum(1 for g in by_window if g),
            "rho": spearman(pairs), "ci": interval,
            "verdict": None if interval is None else
            ("CI excludes 0" if interval[0] > 0 or interval[1] < 0 else "CI includes 0"),
            "tickers": chosen, "selection_sha256": hashlib.sha256("\n".join(chosen).encode()).hexdigest(),
            "seed": BOOTSTRAP_SEED, "rounds": BOOTSTRAP_ROUNDS}


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
        lines.append(f"rows {result['rows']} from {result['contributing_windows']} of "
                     f"{len(result['tickers'])} windows, rho {'n/a' if rho is None else f'{rho:+.3f}'}, "
                     f"{ci_text(result['ci'])} -> {result['verdict'] or 'undetermined'}")
        lines.append(f"selection sha256 {result['selection_sha256'][:16]}, first {result['tickers'][0]}, "
                     f"last {result['tickers'][-1]}, seed {result['seed']}, {result['rounds']} rounds")
    return lines


def snapshot_log(source, manifest_path):
    """Copy the log once, beside the manifest, and return the copy's path.

    The report and the manifest are both computed from this copy, so rows
    appended to the live log meanwhile can't make them disagree, and the exact
    input is kept for re-running.
    """
    data = Path(source).read_bytes()
    copy = Path(manifest_path).with_suffix(".input.jsonl")
    temp = copy.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(temp, copy)
    return copy


def write_manifest(path, log_path, opened, minutes, now, source=None):
    """Record exactly what the primary was computed from, for re-running it later.

    ``log_path`` must be the immutable copy the rows were loaded from."""
    result = primary_result(opened, minutes, now)
    manifest = {"evaluated_at": now.isoformat(), "log": str(log_path),
                "source_log": str(source or log_path),
                "log_sha256": hashlib.sha256(Path(log_path).read_bytes()).hexdigest(),
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "timeframe": PRIMARY_TIMEFRAME, "horizon": PRIMARY_HORIZON,
                "min_windows": PRIMARY_MIN_WINDOWS, **result}
    Path(path).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def proxy_section(closes, settled):
    """Our closing-minute composite mean against Kalshi's settlement value."""
    errors, incomplete = [], 0
    def close_order(item):
        ticker, row = item
        for key in ("close_time", "captured_at"):
            if row.get(key) and is_timestamp(row[key]):
                return (0, parse_iso_ts(row[key]), "", ticker)
        return (1, 0.0, str(row.get("close_time") or ""), ticker)
    for ticker, row in sorted(closes.items(), key=close_order):
        settlement = (settled.get(ticker) or {}).get("expiration_value")
        if settlement is None or row.get("proxy_avg_60s") is None:
            continue
        if row.get("samples") != 60:
            incomplete += 1
            continue
        errors.append(float(row["proxy_avg_60s"]) - float(settlement))
    lines = ["", "== Proxy vs settlement " + EXPLORATORY + " ==",
             f"Closing-minute composite mean minus Kalshi expiration_value; {len(errors)} complete windows, "
             f"{incomplete} with fewer than 60 samples left out."]
    if errors:
        absolute = sorted(abs(e) for e in errors)
        p90 = absolute[min(len(absolute) - 1, math.ceil(0.9 * len(absolute)) - 1)]
        lines.append(f"median |error| ${statistics.median(absolute):.2f}, 90% within ${p90:.2f}, "
                     f"bias {statistics.fmean(errors):+.2f}, max |error| ${absolute[-1]:.2f}")
    return lines


def calibration_section(opened, settled, minutes, closes=None):
    """Observed settlement frequencies; exploratory and not a live estimate."""
    distance, market = {}, {}
    lock_hits = lock_count = 0
    for ticker, rows in minutes.items():
        result = (settled.get(ticker) or {}).get("result")
        if result not in ("yes", "no"):
            continue
        for minute, row in rows.items():
            if record_problem(row) or row.get("stale") or row.get("kalshi_stale"):
                continue
            composite, target, sigma = (row.get(k) for k in ("composite", "target", "sigma_1m_usd"))
            if (composite is None or target is None or sigma is None or sigma <= 0
                    or "1m" in (row.get("history_stale") or [])):
                continue
            if minute == 14:
                lock_count += 1
                lock_hits += (composite >= target) == (result == "yes")
            left = row.get("seconds_left")
            if left is not None and left > 60:
                scale = sigma * math.sqrt((left - 60) / 60 + 1 / 3)
                value = (composite - target) / scale
                bucket = ("<−2σ" if value < -2 else "−2 to −1σ" if value < -1 else
                          "−1 to 0σ" if value < 0 else "0 to 1σ" if value < 1 else
                          "1 to 2σ" if value < 2 else "≥2σ")
                hits, count = distance.get(bucket, (0, 0))
                distance[bucket] = (hits + (result == "yes"), count + 1)
            bid, ask = row.get("yes_bid"), row.get("yes_ask")
            if (isinstance(bid, dict) and isinstance(ask, dict) and
                    isinstance(bid.get("age"), (int, float)) and isinstance(ask.get("age"), (int, float))
                    and 0 <= bid["age"] <= 10 and 0 <= ask["age"] <= 10):
                price = mid(row)
                if price is not None:
                    bucket = min(9, int(price * 10))
                    hits, count = market.get(bucket, (0, 0))
                    market[bucket] = (hits + (result == "yes"), count + 1)
    def line(label, hits, count, unit="YES"):
        ci = wilson95(hits, count)
        return f"{label}: {hits}/{count} {unit} ({hits / count:.1%}), Wilson 95% [{ci[0]:.1%}, {ci[1]:.1%}]"
    lines = ["", "== Calibration " + EXPLORATORY + " ==",
             "Observed YES settlement rates by minute-row proxy distance and fresh YES mid; descriptive, not a probability estimate or trade signal.",
             "Distance in σ (minutes with more than 60 s left):"]
    lines += [line(bucket, *distance[bucket]) for bucket in
              ("<−2σ", "−2 to −1σ", "−1 to 0σ", "0 to 1σ", "1 to 2σ", "≥2σ") if bucket in distance]
    lines.append("Fresh YES mid (10¢ buckets):")
    lines += [line(f"{bucket * 10:02d}–{bucket * 10 + 9:02d}¢", *market[bucket]) for bucket in sorted(market)]
    errors, side_count, mismatches = [], 0, 0
    for ticker, row in (closes or {}).items():
        if row.get("brti_samples") != 60 or record_problem(row):
            continue
        value = row.get("brti_avg_60s")
        expiration = (settled.get(ticker) or {}).get("expiration_value")
        if value is None or not finite_or_none(expiration, strings=True) or expiration is None:
            continue
        official = float(expiration)
        errors.append(abs(value - official))
        target = (opened.get(ticker) or {}).get("target")
        if finite_or_none(target) and target is not None:
            side_count += 1
            mismatches += (value >= target) != (official >= target)
    lines.append(f"Live BRTI close average vs expiration_value: {len(errors)} complete windows; "
                 f"median |error| ${statistics.median(errors):.2f}, max |error| ${max(errors):.2f}; "
                 f"side-vs-target mismatches {mismatches}/{side_count}." if errors else
                 "Live BRTI close average vs expiration_value: 0 complete windows; side-vs-target mismatches 0/0.")
    lines.append(line("Minute-14 composite side lock-in accuracy", lock_hits, lock_count, "matched")
                 if lock_count else "Minute-14 composite side lock-in accuracy: 0 rows")
    return lines


def report(opened, settled, minutes=None, now=None, closes=None, calibration=False):
    minutes = minutes or {}
    now = now or datetime.now(timezone.utc)
    lines = legacy_section(opened, settled)
    lines += settlement_section(opened, settled)
    if closes is not None:
        lines += proxy_section(closes, settled)
    lines += minute_section(minutes)
    lines += market_section(opened, settled, minutes)
    lines += levels_section(opened, settled, minutes)
    lines += primary_section(opened, minutes, now)
    if calibration:
        lines += calibration_section(opened, settled, minutes, closes)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path, default=Path.home() / "btc-ta/state/tally_log.jsonl")
    parser.add_argument("--manifest", type=Path,
                        help="also write the primary's selection, seed, input and code digests as JSON")
    parser.add_argument("--calibration", action="store_true", help="append exploratory observed-rate calibration")
    args = parser.parse_args()
    skipped = []
    closes = {}
    source = snapshot_log(args.path, args.manifest) if args.manifest else args.path
    rows = load_rows(source, skipped, closes)
    if skipped[0]:
        print(f"Skipped {skipped[0]} unreadable log lines.")
    now = datetime.now(timezone.utc)
    print(report(*rows, now=now, closes=closes, calibration=args.calibration))
    if args.manifest:
        write_manifest(args.manifest, source, rows[0], rows[2], now, source=args.path)
        print(f"Primary manifest written to {args.manifest}; its exact input is {source}")


if __name__ == "__main__":
    main()
