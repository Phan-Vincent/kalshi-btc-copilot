#!/usr/bin/env python3
"""Descriptive, offline tally outcomes; never consumed by the dashboard."""
import argparse
import json
import math
from pathlib import Path

TIMEFRAMES = ("1m", "5m", "10m", "15m", "1h")


def wilson95(hits, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


def load_records(path):
    opened, settled = {}, {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("type") == "open":
                    opened.setdefault(row["ticker"], row)
                elif row.get("type") == "settled":
                    settled.setdefault(row["ticker"], row)
    except FileNotFoundError:
        pass
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


def report(opened, settled):
    lines = [f"Opening records: {len(opened)}; settled records: {len(settled)}",
             "Descriptive research only. Mixed leans and unresolved outcomes are excluded from hit rates."]
    for tf, count in summarize(opened, settled).items():
        n, hits = count["scored"], count["hits"]
        interval = wilson95(hits, n)
        rate = (f"{hits}/{n} ({hits / n:.1%}); Wilson 95% "
                f"[{interval[0]:.1%}, {interval[1]:.1%}]" if interval else "0/0; Wilson 95% unavailable")
        lines.append(f"{tf}: bullish {count['bullish']}, bearish {count['bearish']}, mixed {count['mixed']}; hit rate {rate}")
        if n < 100:
            lines.append("  n under 100: too small to mean anything.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path.home() / "btc-ta/state/tally_log.jsonl")
    args = parser.parse_args()
    opened, settled = load_records(args.path)
    print(report(opened, settled))


if __name__ == "__main__":
    main()
