"""QA 2026-10-18, batch D: H02 (the exploratory minute section drops the readings
the dashboard hides), H11 (the PRIMARY sample-size line names the windows that
contributed) and H07 (merge completes a settled row's late expiration_value).

The expected minute-section and PRIMARY numbers below were computed on these same
fixtures with HEAD's module (`git show HEAD:ta_copilot/tally_report.py`, loaded
from a temporary directory): they are what the pre-declared measure produced
before the change, so comparing against them proves it did not move.
"""
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


def minute_row(ticker, number, composite, net, bid=None, ask=None, **extra):
    row = {"type": "minute", "ticker": ticker, "minute": number,
           "captured_at": f"2026-10-07T00:{number:02d}:00Z",
           "composite": composite, "timeframes": {"1m": {"family_net": net}}}
    if bid is not None:
        row["yes_bid"], row["yes_ask"] = {"price": bid}, {"price": ask}
    row.update(extra)
    return row


def fresh_minutes():
    """Four windows of consecutive minutes, every reading fresh."""
    return {
        "A": {1: minute_row("A", 1, 100.0, 2, 0.50, 0.52),
              2: minute_row("A", 2, 101.0, 1, 0.53, 0.55),
              3: minute_row("A", 3, 102.0, 0, 0.54, 0.56),
              4: minute_row("A", 4, 103.0, 1, 0.56, 0.58)},
        "B": {1: minute_row("B", 1, 100.5, -1, 0.48, 0.50),
              2: minute_row("B", 2, 100.0, 0, 0.47, 0.49),
              3: minute_row("B", 3, 99.5, -2, 0.46, 0.48),
              4: minute_row("B", 4, 99.0, 2, 0.45, 0.47)},
        "C": {1: minute_row("C", 1, 99.0, 0, 0.52, 0.54),
              2: minute_row("C", 2, 98.5, -2, 0.51, 0.53),
              3: minute_row("C", 3, 98.0, -1, 0.50, 0.52),
              4: minute_row("C", 4, 98.0, -1, 0.49, 0.51)},
        "D": {1: minute_row("D", 1, 101.0, 1, 0.60, 0.62),
              2: minute_row("D", 2, 101.5, 2, 0.61, 0.63),
              3: minute_row("D", 3, 102.0, 0, 0.63, 0.65),
              4: minute_row("D", 4, 102.5, 0, 0.64, 0.66)},
    }


# HEAD's minute_section() data lines for fresh_minutes(), in order.
PRE_CHANGE_MINUTE_LINES = [
    "next 1 min composite: n 12 rows, rho +0.717, CI unavailable",
    "  mean change by family_net: -2: -0.50$ (n 2), -1: -0.25$ (n 2), +0: +0.12$ (n 4), "
    "+1: +0.75$ (n 2), +2: +0.75$ (n 2)",
    "next 1 min YES mid: n 12 rows, rho +0.808, CI unavailable",
    "  mean change by family_net: -2: -1.00c (n 2), -1: -1.00c (n 2), +0: +0.25c (n 4), "
    "+1: +1.00c (n 2), +2: +2.50c (n 2)",
    "next 3 min composite: n 4 rows, rho +1.000, CI unavailable",
    "  mean change by family_net: -1: -1.50$ (n 1), +0: -1.00$ (n 1), +1: +1.50$ (n 1), +2: +3.00$ (n 1)",
    "next 3 min YES mid: n 4 rows, rho +0.949, CI unavailable",
    "  mean change by family_net: -1: -3.00c (n 1), +0: -3.00c (n 1), +1: +4.00c (n 1), +2: +6.00c (n 1)",
]


def sparse_minutes():
    """Twelve windows for the primary; the last has no 3-minute pair."""
    minutes = {}
    for w in range(12):
        ticker = f"W{w:02d}"
        rows = {}
        for m in range(1, 7 if w < 11 else 3):  # W11 stops at minute 2
            rows[m] = minute_row(ticker, m, 100.0 + (w * 7 + m * 3) % 11, (w + m) % 5 - 2)
        minutes[ticker] = rows
    return minutes


SPARSE_OPENED = {f"W{w:02d}": {"open_time": f"2026-10-07T00:{w:02d}:00Z"} for w in range(12)}


class SuppressedReadingsTests(unittest.TestCase):
    """H02: stale spot readings and stale 1m histories are not evidence."""

    def test_fresh_input_keeps_the_numbers_it_had_before(self):
        lines = report.minute_section(fresh_minutes())
        self.assertIn("minute rows: 16; stale observations excluded: 0", lines[2])
        self.assertEqual(lines[3:], PRE_CHANGE_MINUTE_LINES)
        self.assertIn(report.EXPLORATORY, lines[1])

    def test_stale_and_history_stale_readings_are_excluded_and_counted(self):
        minutes = fresh_minutes()
        minutes["A"][1]["stale"] = True                  # base row for 1->2 and 1->4
        minutes["B"][2]["history_stale"] = ["1m", "5m"]  # base row for 2->3 only
        minutes["C"][3]["history_stale"] = ["5m"]        # 1m history is fresh: kept
        minutes["D"][1]["stale"] = False                 # explicitly fresh
        lines = report.minute_section(minutes)
        text = "\n".join(lines)
        # A's minute 1 (both horizons) and B's minute 2 are the stale observations.
        self.assertIn("minute rows: 16; stale observations excluded: 3", lines[2])
        self.assertIn(report.EXPLORATORY, lines[1])
        # 12 - 2 pairs at the 1-minute horizon, 4 - 1 at the 3-minute horizon.
        self.assertIn("next 1 min composite: n 10 rows", text)
        self.assertIn("next 1 min YES mid: n 10 rows", text)
        self.assertIn("next 3 min composite: n 3 rows", text)
        self.assertIn("next 3 min YES mid: n 3 rows", text)
        # C's 5m-only staleness and D's explicit False exclude nothing.
        self.assertIn("+0: +0.33$ (n 3)", text)
        self.assertIn("+2: +0.50$ (n 1)", text)

    def test_a_window_of_only_suppressed_readings_reports_no_data(self):
        minutes = {"S": {1: minute_row("S", 1, 100.0, 1, stale=True),
                         2: minute_row("S", 2, 101.0, 0),
                         4: minute_row("S", 4, 104.0, -1)}}
        text = "\n".join(report.minute_section(minutes))
        self.assertIn("stale observations excluded: 2", text)  # minute 1, both horizons
        self.assertIn("next 1 min composite: no data yet", text)
        self.assertIn("next 3 min composite: no data yet", text)


class ContributingWindowsTests(unittest.TestCase):
    """H11: the sample-size line names the windows that actually contributed."""

    # HEAD's primary_result() for sparse_minutes(), PRIMARY_MIN_WINDOWS patched to 12.
    PRE_CHANGE_TICKERS = [f"W{w:02d}" for w in range(12)]
    PRE_CHANGE_SHA256 = "df0fd77d644a15ca93ce525318adbd293ecd8df58c8d172c5fa5c2d755ad7b1f"
    PRE_CHANGE_RHO = -0.11367511917043592
    PRE_CHANGE_CI = (-0.482267185014663, 0.2727101989943404)

    def test_sparse_windows_are_counted_and_the_primary_is_unchanged(self):
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 12):
            result = report.primary_result(SPARSE_OPENED, sparse_minutes(), EVALUATED)
        self.assertTrue(result["evaluated"])
        # W11 has minute rows but no 3-minute pair, so it cannot contribute.
        self.assertEqual((result["windows"], result["contributing_windows"]), (12, 11))
        self.assertEqual(result["rows"], 33)
        self.assertLess(result["contributing_windows"], result["windows"])
        # Everything the pre-declared measure reports is exactly HEAD's.
        self.assertEqual(result["tickers"], self.PRE_CHANGE_TICKERS)
        self.assertEqual(result["selection_sha256"], self.PRE_CHANGE_SHA256)
        self.assertEqual(result["verdict"], "CI includes 0")
        self.assertEqual((result["seed"], result["rounds"]), (20261003, 1000))
        self.assertAlmostEqual(result["rho"], self.PRE_CHANGE_RHO, places=12)
        self.assertAlmostEqual(result["ci"][0], self.PRE_CHANGE_CI[0], places=12)
        self.assertAlmostEqual(result["ci"][1], self.PRE_CHANGE_CI[1], places=12)

    def test_the_section_line_names_the_contributing_windows(self):
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 12):
            text = "\n".join(report.primary_section(SPARSE_OPENED, sparse_minutes(), EVALUATED))
        self.assertIn("rows 33 from 11 of 12 windows, rho -0.114, "
                      "95% [-0.482, +0.273] (includes 0) -> CI includes 0", text)
        self.assertIn("selection sha256 df0fd77d644a15ca, first W00, last W11, "
                      "seed 20261003, 1000 rounds", text)

    def test_suppressed_readings_still_count_in_the_primary(self):
        minutes = sparse_minutes()
        minutes["W00"][1]["stale"] = True            # a primary base row
        minutes["W01"][2]["history_stale"] = ["1m"]  # ditto
        minutes["W02"][4]["stale"] = True            # only ever a target row
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 12):
            result = report.primary_result(SPARSE_OPENED, minutes, EVALUATED)
        # H02 lives in minute_section's obs(); forward_changes, which the primary
        # shares, still sees every row, so the pre-declared measure cannot move.
        self.assertEqual(result["rows"], 33)
        self.assertEqual(result["tickers"], self.PRE_CHANGE_TICKERS)
        self.assertEqual(result["selection_sha256"], self.PRE_CHANGE_SHA256)
        self.assertAlmostEqual(result["rho"], self.PRE_CHANGE_RHO, places=12)
        self.assertAlmostEqual(result["ci"][0], self.PRE_CHANGE_CI[0], places=12)
        self.assertAlmostEqual(result["ci"][1], self.PRE_CHANGE_CI[1], places=12)

    def test_a_not_yet_evaluated_result_has_no_contributing_windows(self):
        early = datetime(2026, 10, 13, tzinfo=timezone.utc)
        with patch.object(report, "PRIMARY_MIN_WINDOWS", 12):
            result = report.primary_result(SPARSE_OPENED, sparse_minutes(), early)
        self.assertFalse(result["evaluated"])
        self.assertNotIn("contributing_windows", result)


def write(directory, name, rows):
    path = Path(directory, name)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


SETTLED_NO_VALUE = {"type": "settled", "ticker": "T", "result": "yes",
                    "recorded_at": "2026-10-07T00:15:20Z"}
SETTLED_WITH_VALUE = {**SETTLED_NO_VALUE, "expiration_value": "84000.5"}


class MergeLateValueTests(unittest.TestCase):
    """H07: a secondary log's late expiration_value reaches the merged row."""

    def merge(self, primary_rows, secondary_rows):
        with tempfile.TemporaryDirectory() as directory:
            primary = write(directory, "primary.jsonl", primary_rows)
            secondary = write(directory, "secondary.jsonl", secondary_rows)
            out = Path(directory, "merged.jsonl")
            with patch("sys.stdout", new_callable=io.StringIO) as printed:
                self.assertEqual(log_tools.merge(primary, secondary, out), 0)
            merged = [json.loads(line) for line in out.read_text().splitlines()]
            # The inputs are never rewritten by merge.
            self.assertEqual([json.loads(line) for line in primary.read_text().splitlines()], primary_rows)
            self.assertEqual([json.loads(line) for line in secondary.read_text().splitlines()], secondary_rows)
        return merged, printed.getvalue()

    def test_a_missing_value_is_filled_from_secondary(self):
        merged, printed = self.merge([SETTLED_NO_VALUE], [SETTLED_WITH_VALUE])
        self.assertEqual(merged, [SETTLED_WITH_VALUE])
        self.assertIn("filled 1 expiration_value from secondary", printed)
        self.assertNotIn("unreadable", printed)

    def test_a_null_value_is_filled_from_secondary(self):
        merged, printed = self.merge([{**SETTLED_NO_VALUE, "expiration_value": None}], [SETTLED_WITH_VALUE])
        self.assertEqual(merged[0]["expiration_value"], "84000.5")
        self.assertIn("filled 1 expiration_value from secondary", printed)

    def test_rows_that_disagree_on_result_are_not_merged(self):
        other = {**SETTLED_WITH_VALUE, "result": "no"}
        merged, printed = self.merge([SETTLED_NO_VALUE], [other])
        self.assertEqual(merged, [SETTLED_NO_VALUE])
        self.assertNotIn("filled", printed)

    def test_an_existing_value_is_never_overwritten(self):
        merged, printed = self.merge([SETTLED_WITH_VALUE], [{**SETTLED_WITH_VALUE, "expiration_value": "1"}])
        self.assertEqual(merged, [SETTLED_WITH_VALUE])
        self.assertNotIn("filled", printed)

    def test_only_settled_rows_are_completed(self):
        opener = {"type": "open", "ticker": "T", "open_time": "2026-10-07T00:00:00Z"}
        merged, printed = self.merge([opener], [{**opener, "expiration_value": "84000.5"}])
        self.assertEqual(merged, [opener])
        self.assertNotIn("filled", printed)

    def test_the_same_row_in_both_logs_is_counted_once(self):
        merged, printed = self.merge([SETTLED_NO_VALUE], [SETTLED_NO_VALUE, SETTLED_WITH_VALUE])
        self.assertEqual(merged, [SETTLED_WITH_VALUE])
        self.assertIn("filled 1 expiration_value from secondary", printed)
        self.assertIn("1 from primary, 0 from secondary", printed)


if __name__ == "__main__":
    unittest.main()
