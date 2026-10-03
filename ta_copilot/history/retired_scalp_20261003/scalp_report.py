#!/usr/bin/env python3
"""Descriptive, offline report over the paper scalp journal; never consumed by the dashboard.

Reads ``scalp_log.jsonl`` (default ``~/btc-ta/state/scalp_log.jsonl``) and prints what the
experimental paper scalp engine actually did: entries, completed trades, the exit rules that
fired, the realized win rate with a Wilson 95% interval, mean P&L per contract after fees with a
Student-t 95% interval, cumulative P&L, max drawdown, and the aborted count.

Every figure is a description of past paper trades. None of it is a probability of winning the
next trade, and sample sizes under 100 are flagged as too small to mean anything.

Both fee roundings are reported: the default $0.0001 account-precision model and the $0.01
sensitivity model.
"""
import argparse
import json
from pathlib import Path

import scalp_signal as signal

DEFAULT_PATH = Path.home() / "btc-ta" / "state" / signal.LOG_NAME


def fee_line(stats):
    return (f"  after {signal.FEE_ROUNDING_INCREMENT}-rounded fees: "
            f"cumulative {stats['cumulative_pnl_dollars']:+.4f} USD, "
            f"max drawdown {stats['max_drawdown_dollars']:.4f} USD, "
            f"mean {stats['mean_per_contract_cents'] if stats['mean_per_contract_cents'] is not None else 'n/a'}"
            f"c/contract")


def second_fee_line(stats):
    mean = stats["mean_per_contract_dollars_01"]
    return (f"  after {signal.FEE_ROUNDING_INCREMENT_SENSITIVITY}-rounded fees: "
            f"cumulative {stats['cumulative_pnl_dollars_01']:+.4f} USD, "
            f"max drawdown {stats['max_drawdown_dollars_01']:.4f} USD, "
            f"mean {'n/a' if mean is None else f'{mean * 100:+.3f}'}c/contract")


def interval_text(interval, percent=False):
    if not interval:
        return "CI unavailable"
    lo, hi = interval
    scale = 100 if percent else 1
    return f"[{lo * scale:.1f}{'%' if percent else ''}, {hi * scale:.1f}{'%' if percent else ''}]"


def report(rows):
    """Plain-text descriptive report for journal rows."""
    entries = [row for row in rows if row.get("type") == "enter"]
    exits = [row for row in rows if row.get("type") == "exit"]
    others = {}
    for row in rows:
        tag = signal.row_strategy(row)
        if tag != signal.STRATEGY:
            others[tag] = others.get(tag, 0) + 1
    rows = [row for row in rows if signal.row_strategy(row) == signal.STRATEGY]
    entries = [row for row in rows if row.get("type") == "enter"]
    exits = [row for row in rows if row.get("type") == "exit"]
    trades, open_entries, _last_exit, aborted = signal.replay(rows)
    stats = signal.summarize(trades, aborted)
    lines = [
        f"Strategy: {signal.STRATEGY}"
        + (f" (excluded rows from other strategies: "
           f"{', '.join(f'{k} {v}' for k, v in sorted(others.items()))})" if others else ""),
        f"Journal rows: {len(rows)}; entries: {len(entries)}; exit records: {len(exits)}",
        "Paper only. Descriptive research on past paper trades; not a probability of winning, "
        "not advice, and not connected to any order path.",
    ]

    if trades:
        rate = stats["win_rate"]
        lines.append(
            f"Trades: {stats['trades']} (wins {stats['wins']}, losses {stats['losses']}); "
            f"win rate {rate:.1%} Wilson 95% {interval_text(stats['win_rate_ci'], percent=True)}")
        mean_cents = stats["mean_per_contract_cents"]
        lines.append(
            f"Mean P&L per contract: {mean_cents:+.3f}c after fees, "
            f"95% {interval_text(stats['mean_per_contract_ci_cents'])} (Student-t, the reported "
            "default increment)")
        lines.append(fee_line(stats))
        lines.append(second_fee_line(stats))
    else:
        lines.append("Trades: 0; nothing scored yet.")

    if open_entries:
        lines.append(f"Open entries awaiting a public result: {len(open_entries)} "
                     f"({', '.join(sorted(str(t) for t in open_entries))})")
    lines.append(f"Aborted exits (engine restart with an open position, excluded from expectancy): "
                 f"{stats['aborted']}")

    by_rule = {}
    for trade in trades:
        key = (trade["rule"] or "unknown", trade["side"])
        bucket = by_rule.setdefault(key, {"n": 0, "pnl": 0.0})
        bucket["n"] += 1
        bucket["pnl"] += trade["net_dollars"] or 0.0
    lines.append("Entries and exit rules that produced trades:")
    if entries:
        counts = ", ".join(
            f"{side} {sum(1 for entry in entries if entry.get('side') == side)}"
            for side in signal.SIDES)
        lines.append(f"  entries: {len(entries)} "
                     f"({counts})")
    if by_rule:
        for (rule, side), bucket in sorted(by_rule.items()):
            lines.append(f"  exit rule {rule} ({side}): {bucket['n']} trades, "
                         f"{bucket['pnl']:+.4f} USD")
    else:
        lines.append("  no exits yet")

    if stats["too_few"]:
        lines.append(f"n under {stats['floor_for_judgement']}: too few trades to judge.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH,
                        help=f"paper scalp journal (default: {DEFAULT_PATH})")
    parser.add_argument("--json", action="store_true", help="print the statistics as JSON instead")
    args = parser.parse_args(argv)
    rows = signal.load_rows(args.path)
    if args.json:
        trades, _open, _last_exit, aborted = signal.replay(rows)
        print(json.dumps(signal.summarize(trades, aborted),
                         indent=2, sort_keys=True, allow_nan=False))
    else:
        print(report(rows))


if __name__ == "__main__":
    main()
