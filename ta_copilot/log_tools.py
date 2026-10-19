#!/usr/bin/env python3
"""Check or merge tally research logs (tally_log.jsonl). Read-only on inputs.

  log_tools.py check PATH
  log_tools.py merge PRIMARY SECONDARY -o OUT

Rows are keyed like the dashboard and report read them: open, settled and
close by ticker, minute by ticker and minute; the first row per key wins.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from tally_report import record_problem

WINDOW = 900


def row_key(row):
    kind = row.get("type")
    if kind in ("open", "settled", "close", "settled_value") and "ticker" in row:
        return kind, row["ticker"]
    if kind == "minute" and "ticker" in row and "minute" in row:
        return kind, row["ticker"], row["minute"]
    return None


def row_time(row):
    return row.get("captured_at") or row.get("recorded_at") or ""


def read_rows(path):
    """(rows in file order, unreadable line count). Blank lines are ignored.

    A line that is not JSON, or that parses but fails record_problem(), is
    unreadable. Rows of a type that is not a research record ("unknown type")
    are not damage: like tally_report.load_rows(), they are not counted, and
    they are also not returned."""
    rows, bad = [], 0
    with Path(path).open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                bad += 1
                continue
            problem = record_problem(row)
            if problem == "unknown type":
                # Dropped instead of returned, and not counted as bad: check()
                # reads r["type"] directly, so a dict without one would raise
                # KeyError, and merge() keys rows with row_key(), which is None
                # for every unknown type, so all of them would fold into one row.
                continue
            if problem is None:
                rows.append(row)
            else:
                bad += 1
    return rows, bad


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def window_gaps(rows):
    """15-minute windows with no open row between the first and last opened window."""
    opens = sorted({int(parse_time(r["open_time"])) for r in rows
                    if r.get("type") == "open" and r.get("open_time")})
    if not opens:
        return []
    present = set(opens)
    return [t for t in range(opens[0], opens[-1] + WINDOW, WINDOW) if t not in present]


def check(path):
    rows, bad = read_rows(path)
    keys = Counter(row_key(r) for r in rows)
    kinds = Counter(r["type"] for r in rows)
    duplicates = sum(n - 1 for n in keys.values() if n > 1)
    opened = {r["ticker"] for r in rows if r["type"] == "open"}
    settled = {r["ticker"] for r in rows if r["type"] == "settled"}
    gaps = window_gaps(rows)
    print(f"{path}: " + ", ".join(f"{k} {kinds[k]}" for k in ("open", "settled", "close", "minute", "settled_value")))
    print(f"unreadable lines {bad}, duplicate keys {duplicates}, opened but not settled {len(opened - settled)}")
    print(f"missing windows between first and last open: {len(gaps)}")
    for start in gaps[:20]:
        print("  " + datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    if len(gaps) > 20:
        print(f"  ... and {len(gaps) - 20} more")
    return 1 if bad else 0


def merge(primary, secondary, out):
    out = Path(out)
    if out.exists() or out.resolve() in (Path(primary).resolve(), Path(secondary).resolve()):
        print(f"refusing to write {out}: it exists or is an input", file=sys.stderr)
        return 2
    merged, sources = {}, Counter()
    for label, path in (("primary", primary), ("secondary", secondary)):
        rows, bad = read_rows(path)
        if bad:
            print(f"{label}: skipped {bad} unreadable lines")
        for row in rows:
            key = row_key(row)
            if key not in merged:
                merged[key] = row
                sources[label] += 1
    payload = "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
                      for row in sorted(merged.values(), key=row_time)).encode()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
    print(f"wrote {len(merged)} rows to {out}: {sources['primary']} from primary, "
          f"{sources['secondary']} from secondary")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("path")
    m = sub.add_parser("merge")
    m.add_argument("primary")
    m.add_argument("secondary")
    m.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.path)
    return merge(args.primary, args.secondary, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
