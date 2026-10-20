"""Descriptive levels schema, horizons and report compatibility."""
import copy
from datetime import datetime, timezone
import unittest

import tally_report as report
from test_qa_20261018d import fresh_minutes, PRE_CHANGE_MINUTE_LINES
from test_tally_20261003 import minute_row


def levels(**extra):
    return {"support": 99.0, "support_touches": 2, "support_dist_atr": -0.25,
            "resistance": 101.0, "resistance_touches": 3, "resistance_dist_atr": 0.5,
            "at_price": False, "zones_to_target": 0, "target_in_zone": False, **extra}


def fixture():
    minutes = {}
    for ticker, prices, count, target, inside in (
            ("A", [100, 100, 100, 98, 102], 0, 101, False),
            ("B", [100, 100, 100, 99, 101], 1, 99, True),
            ("C", [100, 100, 100, 102, 98], 2, 101, True)):
        minutes[ticker] = {}
        for m, price in enumerate(prices, 1):
            row = minute_row(ticker, m, 1, price)
            row["target"] = target
            row["timeframes"]["1m"]["levels"] = levels(
                zones_to_target=count, target_in_zone=inside,
                support_dist_atr=-0.25 if m == 1 else -1,
                resistance_dist_atr=0.5 if m == 1 else 2)
            minutes[ticker][m] = row
    settled = {"A": {"expiration_value": "101"}, "B": {"expiration_value": 98},
               "C": {"expiration_value": 100}}
    return minutes, settled


class LevelsReportTests(unittest.TestCase):
    def test_schema(self):
        row = minute_row("A", 1, 1, 100)
        frame = row["timeframes"]["1m"]
        for value in (levels(), None):
            frame["levels"] = value
            self.assertIsNone(report.record_problem(row))
        del frame["levels"]
        self.assertIsNone(report.record_problem(row))
        for key, values in (("support", [float("nan"), "99", True]),
                            ("resistance_dist_atr", [float("inf"), "1"]),
                            ("support_touches", [-1, 1.5, True]),
                            ("resistance_touches", [-2, 2.0]),
                            ("zones_to_target", [-1, 1.0]),
                            ("at_price", [0, "true"]), ("target_in_zone", [1])):
            for value in values:
                with self.subTest(key=key, value=value):
                    frame["levels"] = levels(**{key: value})
                    self.assertEqual(report.record_problem(row), "bad levels")
        for value in ([], "levels", 0):
            frame["levels"] = value
            self.assertEqual(report.record_problem(row), "bad levels")
        frame["levels"] = dict.fromkeys(levels())
        self.assertIsNone(report.record_problem(row))

    def test_counts_and_intervals(self):
        minutes, settled = fixture()
        text = "\n".join(report.levels_section({}, settled, minutes))
        for label, hits in (("support, <0.5", 1), ("support, 1–2", 1),
                            ("resistance, 0.5–1", 1), ("resistance, ≥2", 1)):
            self.assertIn(f"{label} ATR: n 3, crossed {hits} (33.3%)", text)
        self.assertIn("support, 0.5–1 ATR: no data yet", text)
        self.assertIn("0 zones: n 1, reached 1 (100.0%)", text)
        self.assertIn("≥1 zones: n 2, reached 1 (50.0%)", text)
        self.assertIn("target in zone: n 2, reached 1 (50.0%)", text)
        lo, hi = report.wilson95(1, 3)
        self.assertIn(f"Wilson 95% [{lo:.1%}, {hi:.1%}]", text)
        self.assertIn("windows skipped: 0", text)

    def test_suppression_gaps_fallback_and_missing(self):
        minutes, settled = fixture()
        minutes["A"][4]["stale"] = True
        minutes["B"][1]["history_stale"] = ["1m"]
        del minutes["C"][1]
        minutes["C"][2]["target"] = 99
        del minutes["B"][5]
        text = "\n".join(report.levels_section({}, settled, minutes))
        self.assertIn("support, <0.5 ATR: no data yet", text)
        self.assertIn("support, 1–2 ATR: n 2, crossed 1", text)
        self.assertIn("0 zones: n 1, reached 1", text)
        self.assertIn("≥1 zones: n 1, reached 0", text)
        self.assertIn("windows skipped: 1", text)
        minutes["C"][2]["timeframes"]["1m"]["levels"] = None
        self.assertIn("windows skipped: 2", "\n".join(report.levels_section({}, settled, minutes)))

    def test_report_existing_sections_unchanged(self):
        minutes = fresh_minutes()
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.assertEqual(report.minute_section(minutes)[3:], PRE_CHANGE_MINUTE_LINES)
        baseline = "\n".join(report.legacy_section({}, {}) + report.settlement_section({}, {})
                             + report.proxy_section({}, {}) + report.minute_section(minutes)
                             + report.market_section({}, {}, minutes) + report.primary_section({}, minutes, now))
        actual = report.report({}, {}, minutes, now, closes={})
        addition = "\n".join(report.levels_section({}, {}, minutes))
        self.assertEqual(actual.replace(addition + "\n", "", 1), baseline)
        enriched = copy.deepcopy(minutes)
        for rows in enriched.values():
            for row in rows.values():
                row["timeframes"]["1m"]["levels"] = levels()
        self.assertEqual(report.primary_section({}, minutes, now), report.primary_section({}, enriched, now))
        self.assertEqual(report.minute_section(minutes), report.minute_section(enriched))


if __name__ == "__main__":
    unittest.main()
