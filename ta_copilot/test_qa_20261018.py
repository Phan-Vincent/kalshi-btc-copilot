"""QA-06 (2026-10-18): unknown-type log rows are not damage in log_tools, and the
primary cohort is ordered by parsed UTC time rather than by the raw ISO string."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import log_tools
import tally_report as report

# Comfortably after PRIMARY_EVALUATE_AT, so only the window count gates evaluation.
EVALUATED = datetime(2026, 10, 15, tzinfo=timezone.utc)

OPEN_A = {"type": "open", "ticker": "A", "open_time": "2026-10-07T00:00:00Z",
          "captured_at": "2026-10-07T00:00:00.100000Z", "target": 100.0,
          "timeframes": {"1m": {"lean": "bullish"}}}
SETTLED_A = {"type": "settled", "ticker": "A", "result": "yes", "expiration_value": "101",
             "recorded_at": "2026-10-07T00:15:20Z"}
UNKNOWN = {"type": "note", "text": "hand-written marker, not a research record"}
BAD_SCHEMA = {"type": "minute", "ticker": "A", "minute": 3, "captured_at": "2026-10-07T00:03:00Z",
              "composite": "not-a-number"}


def write(directory, name, rows):
    path = Path(directory, name)
    path.write_text("".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in rows))
    return path


def minute(ticker, number, captured_at, composite=100.0):
    return {"type": "minute", "ticker": ticker, "minute": number, "captured_at": captured_at,
            "composite": composite, "timeframes": {"1m": {"family_net": 1}}}


class ReadRowsTests(unittest.TestCase):
    def test_unknown_type_row_is_not_counted_as_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, "tally_log.jsonl", [OPEN_A, UNKNOWN, "{not json", BAD_SCHEMA])
            rows, bad = log_tools.read_rows(path)
            # Only the malformed JSON line and the bad composite are damage.
            self.assertEqual(bad, 2)
            # G1: unknown-type rows are dropped, not returned and not counted
            # (see read_rows: check() reads r["type"] and merge() keys by row_key()).
            self.assertEqual([(r["type"], r["ticker"]) for r in rows], [("open", "A")])

    def test_every_other_record_problem_still_counts(self):
        broken = [{"type": "open", "ticker": []},
                  {"type": "open", "ticker": "B", "open_time": "not-a-date"},
                  {"type": "minute", "ticker": "B", "minute": "3"},
                  {"type": "settled", "ticker": "B", "expiration_value": "NaN"},
                  []]
        self.assertEqual([report.record_problem(row) for row in broken],
                         ["bad ticker", "bad open_time", "bad minute", "bad expiration_value", "not an object"])
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, "tally_log.jsonl", broken + [UNKNOWN])
            self.assertEqual(log_tools.read_rows(path), ([], len(broken)))

    def test_check_does_not_trip_over_rows_without_a_type(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, "tally_log.jsonl", [{"no": "type"}, UNKNOWN, OPEN_A])
            with patch("sys.stdout", new_callable=io.StringIO) as printed:
                self.assertEqual(log_tools.check(path), 0)
            self.assertIn("unreadable lines 0", printed.getvalue())


class MergeTests(unittest.TestCase):
    def test_merge_keeps_research_rows_and_never_calls_unknown_types_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = write(directory, "primary.jsonl", [OPEN_A, UNKNOWN])
            secondary = write(directory, "secondary.jsonl", [SETTLED_A])
            out = Path(directory, "merged.jsonl")
            with patch("sys.stdout", new_callable=io.StringIO) as printed:
                self.assertEqual(log_tools.merge(primary, secondary, out), 0)
            self.assertNotIn("unreadable", printed.getvalue())
            merged = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual([row["type"] for row in merged], ["open", "settled"])
            self.assertEqual(merged, [OPEN_A, SETTLED_A])  # valid rows survive unchanged


class PrimaryOrderTests(unittest.TestCase):
    def test_fractional_seconds_do_not_reorder_the_cohort(self):
        opened = {"W500MS": {"open_time": "2026-10-07T00:00:00.500000Z"},
                  "W0": {"open_time": "2026-10-07T00:00:00Z"}}
        minutes = {"W500MS": {1: minute("W500MS", 1, "2026-10-07T00:00:01Z")},
                   "W0": {1: minute("W0", 1, "2026-10-07T00:00:01Z")}}
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 2):
            result = report.primary_result(opened, minutes, EVALUATED)
        self.assertTrue(result["evaluated"])
        # As text, '.500000Z' sorts before 'Z', which put the later window first.
        self.assertEqual(result["tickers"], ["W0", "W500MS"])

    def test_captured_at_fallback_is_chosen_and_sorted_by_parsed_time(self):
        minutes = {"FALLBACK": {1: minute("FALLBACK", 1, "2026-10-07T00:00:05.500000Z"),
                                2: minute("FALLBACK", 2, "2026-10-07T00:00:05Z")},
                   "BETWEEN": {1: minute("BETWEEN", 1, "2026-10-07T00:00:06Z")},
                   "LATER": {1: minute("LATER", 1, "2026-10-07T00:00:10Z")}}
        # FALLBACK has no open_time and falls back to its earliest captured_at.
        opened = {"BETWEEN": {"open_time": "2026-10-07T00:00:05.200000Z"},
                  "LATER": {"open_time": "2026-10-07T00:00:10Z"}}
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 3):
            result = report.primary_result(opened, minutes, EVALUATED)
        # 05.0 (not the text-smallest 05.500000) comes first, ahead of BETWEEN at 05.2.
        self.assertEqual(result["tickers"], ["FALLBACK", "BETWEEN", "LATER"])

    def test_window_without_three_minute_pairs_stays_in_the_cohort(self):
        minutes = {"PAIRS": {1: minute("PAIRS", 1, "2026-10-07T00:01:00Z"),
                             2: minute("PAIRS", 2, "2026-10-07T00:02:00Z"),
                             4: minute("PAIRS", 4, "2026-10-07T00:04:00Z"),
                             5: minute("PAIRS", 5, "2026-10-07T00:05:00Z")},
                   "GAP": {1: minute("GAP", 1, "2026-10-07T00:01:00Z"),
                           2: minute("GAP", 2, "2026-10-07T00:02:00Z")},
                   "EMPTY": {}}
        opened = {"PAIRS": {"open_time": "2026-10-07T00:00:00Z"},
                  "GAP": {"open_time": "2026-10-07T00:00:30Z"},
                  "EMPTY": {"open_time": "2026-10-07T00:01:00Z"}}
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 2):
            result = report.primary_result(opened, minutes, EVALUATED)
        # The cohort is "windows with minute rows", not "windows with usable pairs":
        # GAP has no 3-minute pair but is still one of the 2 selected windows.
        self.assertEqual((result["windows"], result["tickers"]), (2, ["PAIRS", "GAP"]))
        self.assertEqual(result["rows"], 2)  # only PAIRS contributes observations


if __name__ == "__main__":
    unittest.main()
