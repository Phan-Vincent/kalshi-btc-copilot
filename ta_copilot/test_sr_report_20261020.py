"""Descriptive levels schema, horizons and report compatibility."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

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
            self.assertIn(f"{label} ATR: 3 obs, 3 windows, crossed {hits} (33.3%)", text)
        self.assertIn("support, 0.5–1 ATR: 0 obs, 0 windows, crossed 0 (no data yet)", text)
        self.assertIn("0 zones: n 1, reached 1 (100.0%)", text)
        self.assertIn("≥1 zones: n 2, reached 1 (50.0%)", text)
        self.assertIn("target in zone: n 2, reached 1 (50.0%)", text)
        lo, hi = report.wilson95(1, 2)
        self.assertIn(f"Wilson 95% [{lo:.1%}, {hi:.1%}]", text)
        self.assertIn("Windows: 3 total · 3 used", text)

    def test_suppression_gaps_and_missing(self):
        minutes, settled = fixture()
        minutes["A"][4]["stale"] = True
        minutes["B"][1]["history_stale"] = ["1m"]
        del minutes["C"][1]
        minutes["C"][2]["target"] = 99
        del minutes["B"][5]
        text = "\n".join(report.levels_section({}, settled, minutes))
        self.assertIn("support, <0.5 ATR: 0 obs, 0 windows, crossed 0 (no data yet)", text)
        self.assertIn("support, 1–2 ATR: 2 obs, 2 windows, crossed 1", text)
        self.assertIn("0 zones: n 1, reached 1", text)
        self.assertIn("≥1 zones: no data yet", text)
        self.assertIn("Windows: 3 total · 1 used · 1 no minute-1 row · 1 suppressed", text)
        minutes["C"][2]["timeframes"]["1m"]["levels"] = None
        self.assertIn("Windows: 3 total · 1 used · 1 no minute-1 row · 1 suppressed",
                      "\n".join(report.levels_section({}, settled, minutes)))

    def test_s01_crossings_start_on_own_side(self):
        for side, zone, wrong, beyond, own in (("support", 99, 98, 98, 100),
                                               ("resistance", 101, 102, 102, 100)):
            with self.subTest(side=side):
                minutes = {}
                for ticker, start, end in (("wrong", wrong, beyond), ("equal", zone, beyond),
                                           ("touch", own, zone), ("own", own, beyond)):
                    rows = {m: minute_row(ticker, m, 1, price) for m, price in ((1, start), (4, end))}
                    rows[1]["timeframes"]["1m"]["levels"] = levels()
                    minutes[ticker] = rows
                text = "\n".join(report.levels_section({}, {}, minutes))
                label = "<0.5" if side == "support" else "0.5–1"
                self.assertIn(f"{side}, {label} ATR: 3 obs, 3 windows, crossed 2 (66.7%)", text)
                self.assertIn("crossed means beyond its midpoint after 3 minutes", text)

    def test_s05_crossing_bootstrap_resamples_windows(self):
        minutes = {}
        expected = []
        for i in range(10):
            ticker = str(i)
            # Two overlapping observations in each window; varying window sizes.
            rows = {m: minute_row(ticker, m, 1, price)
                    for m, price in ((1, 100), (2, 100), (4, 98), (5, 100))}
            for row in rows.values():
                row["timeframes"]["1m"]["levels"] = levels(resistance=None)
            if i == 9:
                del rows[2]
            minutes[ticker] = rows
            expected.append([True] if i == 9 else [True, False])
        original = report.block_bootstrap
        with patch.object(report, "block_bootstrap", wraps=original) as bootstrap, \
                patch.object(report, "wilson95", wraps=report.wilson95) as wilson:
            text = "\n".join(report.levels_section({}, {}, minutes))
        groups, statistic = bootstrap.call_args_list[0].args
        self.assertEqual(groups, expected)
        self.assertIsNone(statistic([]))
        self.assertAlmostEqual(statistic([True, False, True]), 2 / 3)
        interval = original(expected, statistic)
        self.assertIn("support, <0.5 ATR: 19 obs, 10 windows, crossed 10 (52.6%)", text)
        self.assertIn(f"block bootstrap 95% [{interval[0]:.1%}, {interval[1]:.1%}]", text)
        self.assertEqual(wilson.call_count, 3)  # Target groups only.
        del minutes["9"]
        text = "\n".join(report.levels_section({}, {}, minutes))
        self.assertIn("18 obs, 9 windows, crossed 9 (50.0%); too few windows for an interval", text)

    def test_s05_minute_one_only(self):
        minutes, settled = fixture()
        del minutes["A"][1]
        minutes["A"][0] = copy.deepcopy(minutes["A"][2])
        text = "\n".join(report.levels_section({}, settled, minutes))
        self.assertIn("Zones between price and target at minute 1.", text)
        self.assertNotIn("earliest", text)
        self.assertIn("0 zones: no data yet", text)
        self.assertIn("Windows: 3 total · 2 used · 1 no minute-1 row", text)

    def test_s05_window_universe_and_exclusive_reasons(self):
        minutes, settled = fixture()
        template = minutes["A"][1]
        minutes = {name: {1: copy.deepcopy(template)} for name in
                   ("used", "suppressed", "no_levels", "null_levels", "missing_price",
                    "missing_target", "missing_zones", "unsettled")}
        minutes["later"] = {2: copy.deepcopy(template)}
        minutes["suppressed"][1]["stale"] = True
        for name in ("suppressed", "no_levels"):
            del minutes[name][1]["timeframes"]["1m"]["levels"]
        minutes["null_levels"][1]["timeframes"]["1m"]["levels"] = None
        minutes["missing_price"][1]["composite"] = None
        del minutes["missing_target"][1]["target"]
        minutes["missing_zones"][1]["timeframes"]["1m"]["levels"]["zones_to_target"] = None
        settled = {"used": {"expiration_value": 101}, "unsettled": {"expiration_value": None},
                   "settled_only": {"expiration_value": 101}}
        text = "\n".join(report.levels_section({"opened_only": {}, "used": {}}, settled, minutes))
        self.assertIn("Windows: 10 total · 1 used · 2 no minute-1 row · 1 suppressed · "
                      "2 no levels · 3 missing fields · 1 awaiting settlement", text)
        lo, hi = report.wilson95(1, 1)
        self.assertIn(f"0 zones: n 1, reached 1 (100.0%); Wilson 95% [{lo:.1%}, {hi:.1%}]", text)

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
