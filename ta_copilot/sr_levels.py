"""Descriptive swing clusters and session levels; no votes or I/O."""
import math

ZONE_MAX_WIDTH_ATR = 0.5
ZONE_TOL_ATR = 0.25
ZONE_TOL_FLOOR_USD = 5.0
FRAMES = ("1m", "5m", "10m", "15m", "1h")
WEIGHTS = {"1m": 1, "5m": 2, "10m": 2, "15m": 3, "1h": 4}


def pivot_points(bars, k=2):
    closed, points = bars[:-1], []
    for i in range(k, len(closed) - k):
        window = closed[i - k:i + k + 1]
        for side, fn in (("high", max), ("low", min)):
            if closed[i][side] == fn(b[side] for b in window):
                points.append((int(closed[i]["time"]), closed[i][side], side))
    return points


def levels(view_tf, bars_by_tf, history, price, atr, vwap=None, target=None,
           target_price=None, window_open=None, now=None, price_basis="spot"):
    tol = max(ZONE_TOL_ATR * atr, ZONE_TOL_FLOOR_USD) if atr is not None else ZONE_TOL_FLOOR_USD
    max_width = max(ZONE_MAX_WIDTH_ATR * atr, 2 * ZONE_TOL_FLOOR_USD) if atr is not None else 2 * ZONE_TOL_FLOOR_USD
    wanted = [tf for tf in FRAMES[FRAMES.index(view_tf):] if tf != "10m" or tf == view_tf]
    # Session frames are quality checked even when below the viewed frame.
    needed = set(wanted) | {"1m", "1h"}
    excluded = [tf for tf in FRAMES if tf in needed and
                (len(bars_by_tf.get(tf) or []) < 30 or
                 (history.get(tf) or {}).get("stale") or (history.get(tf) or {}).get("gap"))]
    candidates = []
    for tf in wanted:
        if tf not in excluded:
            candidates.extend((p, tf, "swing", t) for t, p, _ in pivot_points(bars_by_tf[tf]))
    if now is None:
        now = max((b["time"] for bars in bars_by_tf.values() for b in (bars or [])), default=0)
    hour, day = int(now // 3600) * 3600, int(now // 86400) * 86400

    skipped = []

    def session(tf, start, end, prefix):
        bars = [b for b in bars_by_tf.get(tf, []) if start <= b["time"] < end and b["time"] <= now]
        step = 60 if tf == "1m" else 3600
        if tf in excluded or not set(range(start, end, step)) <= {b["time"] for b in bars}:
            skipped.append(prefix)
            return
        if bars:
            for side, fn in (("high", max), ("low", min)):
                value = fn(b[side] for b in bars)
                stamp = max(int(b["time"]) for b in bars if b[side] == value)
                candidates.append((value, tf, prefix + "_" + side, stamp))

    session("1m", hour - 3600, hour, "prior_hour")
    session("1h", day, hour + 3600, "day")
    session("1h", day - 86400, day, "prior_day")
    if window_open is not None:
        candidates.append((window_open, None, "window_open", None))
    groups = []
    for candidate in sorted(candidates, key=lambda c: c[0]):
        if (not groups or candidate[0] - groups[-1][-1][0] > tol or
                candidate[0] - groups[-1][0][0] > max_width):
            groups.append([])
        groups[-1].append(candidate)
    zones = []
    for group in groups:
        low, high = group[0][0], group[-1][0]
        mid = sum(p * WEIGHTS.get(tf, 1) for p, tf, _, _ in group) / sum(WEIGHTS.get(tf, 1) for _, tf, _, _ in group)
        tags = [f"round ${n:,}" for n in range(math.ceil((low - tol) / 500) * 500,
                                             math.floor((high + tol) / 500) * 500 + 1, 500)]
        if vwap is not None and low - tol <= vwap <= high + tol:
            tags.append("VWAP · Coinbase volume")
        sources = sorted({src for _, _, src, _ in group})
        parts = {"touches": sum(WEIGHTS[tf] for _, tf, src, _ in group if src == "swing"),
                 "session": len(set(sources) - {"swing"}), "confluence": len(tags)}
        lo, hi, center, ref = round(low, 2), round(high, 2), round(mid, 2), round(price, 2)
        distance = round(center - ref, 2)
        zone = {"low": round(low, 2), "high": round(high, 2), "mid": round(mid, 2),
                "touches": sum(src == "swing" for _, _, src, _ in group),
                "timeframes": [tf for tf in FRAMES if any(c[1] == tf for c in group)],
                "sources": sources, "last_touch": max((t for _, _, _, t in group if t is not None), default=None),
                "tags": tags, "strength": sum(parts.values()), "strength_parts": parts,
                "role": "at price" if lo <= ref <= hi else "support" if center < ref else "resistance",
                "distance_usd": distance, "distance_atr": round(distance / atr, 3) if atr else None}
        zones.append((zone, low, high, mid))

    def containing(value):
        return max((z for z, lo, hi, _ in zones if round(lo, 2) <= round(value, 2) <= round(hi, 2)),
                   key=lambda z: z["strength"], default=None)

    base = price if target_price is None else target_price
    between = [z for z, lo, hi, _ in sorted(zones, key=lambda item: abs(item[3] - base))
               if target is not None and min(base, target) < lo and hi < max(base, target)]
    return {"price_basis": price_basis, "skipped_sessions": skipped, "tolerance": tol, "max_width": max_width, "excluded_timeframes": excluded, "at_price": containing(price),
            "support": [z for z, _, _, mid in sorted(zones, key=lambda item: -item[3]) if z["role"] == "support"][:3],
            "resistance": [z for z, _, _, mid in zones if z["role"] == "resistance"][:3],
            "target": None if target is None else {"strike": target, "in_zone": containing(target),
                                                    "between": between, "count": len(between)}}
