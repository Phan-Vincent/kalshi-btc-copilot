"""Tally families, minute rows, track record and the research report (2026-10-03)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

import ta_copilot as ta
import tally_report as report
from test_fix_20261002 import ready_feed


def reading(name, state, detail="x"):
    return {"name": name, "state": state, "detail": detail}


def trend(states):
    names = ("EMA stack", "EMA 9/21", "EMA 200", "MACD", "VWAP (window)")
    return [reading(n, s) for n, s in zip(names, states)]


class FamilyTests(unittest.TestCase):
    def test_reading_families(self):
        self.assertEqual(ta.reading_family(reading("VWAP (loaded range)", "bull")), "trend")
        self.assertEqual(ta.reading_family(reading("MACD", "bear")), "trend")
        self.assertEqual(ta.reading_family(reading("RSI 14", "bull", "58.0")), "momentum")
        self.assertEqual(ta.reading_family(reading("RSI 14", "neutral", "74.0 overbought (stretched)")), "context")
        self.assertEqual(ta.reading_family(reading("Stoch 14,3", "bull")), "context")
        self.assertEqual(ta.reading_family(reading("Bollinger %B", "neutral")), "context")

    def test_trend_needs_three_of_five(self):
        votes = lambda states: ta.family_tally(trend(states))["votes"]["trend"]
        self.assertEqual(votes(["bull", "bull", "bull", "bear", "bear"]), "bull")
        self.assertEqual(votes(["bear", "bear", "bear", "bull", "neutral"]), "bear")
        self.assertEqual(votes(["bull", "bull", "bear", "bear", "neutral"]), "neutral")
        # Five agreeing trend readings are still one vote.
        self.assertEqual(ta.family_tally(trend(["bull"] * 5))["net"], 1)

    def test_context_rows_never_count(self):
        rows = trend(["bull"] * 3 + ["neutral"] * 2) + [
            reading("Stoch 14,3", "bear"), reading("Bollinger %B", "neutral"),
            reading("RSI 14", "neutral", "25.0 oversold (stretched)")]
        family = ta.family_tally(rows)
        self.assertEqual(family["votes"], {"trend": "bull", "momentum": "neutral"})
        self.assertEqual((family["net"], family["lean"]), (1, "mixed"))

    def test_flow_thresholds_and_absence_off_1m(self):
        rows = trend(["bull"] * 5) + [reading("RSI 14", "neutral", "50.0")]
        flow = lambda x: ta.family_tally(rows, x, with_flow=True)["votes"]["flow"]
        self.assertEqual(flow(0.20), "bull")
        self.assertEqual(flow(0.1999), "neutral")
        self.assertEqual(flow(-0.20), "bear")
        self.assertEqual(flow(None), "neutral")
        self.assertNotIn("flow", ta.family_tally(rows, 0.9)["votes"])
        both = ta.family_tally(rows + [reading("RSI 14", "bull", "60")], 0.5, with_flow=True)
        self.assertEqual((both["net"], both["lean"]), (2, "bullish"))
        against = ta.family_tally(trend(["bear"] * 5) + [reading("RSI 14", "bear", "40")], -0.3, with_flow=True)
        self.assertEqual((against["net"], against["lean"]), (-3, "bearish"))

    def test_legacy_tally_is_unchanged_by_families(self):
        bars = [{"time": 60 * i, "open": 100 + (i % 7), "high": 102 + (i % 7), "low": 98 + (i % 5),
                 "close": 100 + (i % 7) + (i % 3) * 0.5, "volume": 1} for i in range(250)]
        before = ta.analyze(bars, 103.0, 0)
        legacy = dict(before["tally"])
        after = ta.frame_family(ta.analyze(bars, 103.0, 0), "1m", 0.3)
        self.assertEqual(after["tally"], legacy)
        bulls = sum(r["state"] == "bull" for r in after["readings"])
        bears = sum(r["state"] == "bear" for r in after["readings"])
        self.assertEqual((legacy["bull"], legacy["bear"]), (bulls, bears))
        self.assertTrue(all("family" in r for r in after["readings"]))

    def test_state_carries_families_and_record(self):
        now = time.time()
        feed = ready_feed(now)
        state = feed.state("1m")
        self.assertIn("family", state["frames"]["1m"])
        self.assertIn("flow", state["frames"]["1m"]["family"]["votes"])
        self.assertIn("tally_record", state)
        self.assertIsNone(state["tally_record"])

    def test_opening_record_has_families_and_sigma(self):
        bars = [{"time": 60 * i, "open": 100 + i, "high": 101 + i, "low": 99 + i,
                 "close": 100 + i + (i % 2), "volume": 1} for i in range(250)]
        market = {"ticker": "KXBTC15M-T", "open_time": ta.iso(15000), "close_time": ta.iso(15900),
                  "floor_strike": 349, "yes_bid_dollars": None, "yes_ask_dollars": None}
        row = ta.opening_record({"market": market, "captured_at": 15000.2, "composite": 350,
                                 "coinbase_spot": 349, "flow_imbalance": 0.25, "bars": {"1m": bars}})
        frame = row["timeframes"]["1m"]
        self.assertEqual(frame["family"]["flow"], "bull")
        self.assertIn(frame["family_lean"], ("bullish", "bearish", "mixed"))
        self.assertGreater(row["sigma_1m_usd"], 0)
        self.assertEqual(row["flow_imbalance_1m"], 0.25)


def window_state(open_ts, now, kalshi_stale=False):
    quote = lambda price, age=1.0: {"price": price, "source": "book", "age": age}
    tally = {"bull": 4, "bear": 2, "neutral": 2, "lean": "bullish"}
    return {"ready": True, "stale": False, "kalshi_stale": kalshi_stale, "composite": 100.5,
            "average_60s": 100.2, "order_flow": {"one_minute": {"imbalance": 0.31}},
            "window": {"ticker": "KXBTC15M-W", "open_time": ta.iso(open_ts), "close_time": ta.iso(open_ts + 900),
                       "seconds_left": open_ts + 900 - now, "strike": 100.0, "sigma_1m_usd": 12.0,
                       "display_quotes": {"yes_bid": quote(0.52), "yes_ask": quote(0.54, 12.0)}},
            "frames": {"1m": {"tally": tally, "values": {"rsi": 58.0, "macd_hist": 0.4},
                              "family": {"votes": {"trend": "bull", "momentum": "bull", "flow": "bull"},
                                         "net": 3, "lean": "bullish"}}}}


class MinuteRowTests(unittest.TestCase):
    def test_capture_timing_and_fields(self):
        open_ts = 90000
        self.assertIsNone(ta.minute_record(window_state(open_ts, open_ts + 0.5), open_ts + 0.5))
        self.assertIsNone(ta.minute_record(window_state(open_ts, open_ts + 62.5), open_ts + 62.5))
        self.assertIsNone(ta.minute_record({"ready": False}, open_ts + 60.5))
        row = ta.minute_record(window_state(open_ts, open_ts + 180.5), open_ts + 180.5)
        self.assertEqual((row["type"], row["minute"], row["ticker"]), ("minute", 3, "KXBTC15M-W"))
        self.assertEqual(row["yes_bid"]["price"], 0.52)
        as_text = window_state(open_ts, open_ts + 180.5)
        as_text["window"]["display_quotes"]["yes_bid"]["price"] = "0.6700"
        self.assertEqual(ta.minute_record(as_text, open_ts + 180.5)["yes_bid"]["price"], 0.67)
        self.assertAlmostEqual(report.mid({"yes_bid": {"price": "0.6700"}, "yes_ask": {"price": "0.6800"}}), 0.675)
        self.assertIsNone(row["yes_ask"])  # 12 s old quote is stale
        self.assertEqual(row["timeframes"]["1m"]["family_net"], 3)
        self.assertEqual(row["flow_imbalance_1m"], 0.31)
        self.assertEqual(row["sigma_1m_usd"], 12.0)
        late = ta.minute_record(window_state(open_ts, open_ts + 14 * 60 + 1), open_ts + 14 * 60 + 1)
        self.assertEqual(late["minute"], 14)
        stale = ta.minute_record(window_state(open_ts, open_ts + 120.5, True), open_ts + 120.5)
        self.assertIsNone(stale["yes_bid"])

    def test_capture_writes_once_privately_and_never_backfills(self):
        open_ts = 90000
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.prepare()
            for now in (open_ts + 60.5, open_ts + 61.0, open_ts + 240.5):
                with patch.object(ta, "cached_state", return_value=window_state(open_ts, now)):
                    logger.capture_minute(now)
            rows = [json.loads(line) for line in logger.path.read_text().splitlines()]
            self.assertEqual([r["minute"] for r in rows], [1, 4])
            self.assertEqual(stat.S_IMODE(os.stat(logger.path).st_mode), 0o600)
            # A restart skips minute rows when rebuilding state.
            again = ta.TallyLogger(Path(directory), ta.Feed())
            again.prepare()
            self.assertEqual(again.opened, {})


class RecordTests(unittest.TestCase):
    def test_track_record_from_opened_and_settled(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            for i, (lean, result) in enumerate([("bullish", "yes"), ("bearish", "yes"),
                                                 ("mixed", "no"), ("bearish", "no")]):
                logger.append({"type": "open", "ticker": f"T{i}", "timeframes": {"1m": {"lean": lean}}})
                logger.append({"type": "settled", "ticker": f"T{i}", "result": result})
            logger.prepare()
            record = logger.record
            self.assertEqual((record["hits"], record["scored"]), (2, 3))
            self.assertTrue(record["includes_50"])
            self.assertTrue(record["too_few"])


def minute_row(ticker, minute, net, composite, bid=None, ask=None):
    quote = lambda p: None if p is None else {"price": p, "age": 1.0, "source": "book"}
    return {"type": "minute", "ticker": ticker, "minute": minute, "captured_at": ta.iso(1000 + minute),
            "composite": composite, "yes_bid": quote(bid), "yes_ask": quote(ask),
            "timeframes": {"1m": {"family_net": net}}}


class ReportTests(unittest.TestCase):
    def test_ranks_and_spearman(self):
        self.assertEqual(report.ranks([10, 20, 20, 30]), [1, 2.5, 2.5, 4])
        self.assertAlmostEqual(report.spearman([(1, 2), (2, 4), (3, 9), (4, 10)]), 1.0)
        self.assertAlmostEqual(report.spearman([(1, 4), (2, 3), (3, 2), (4, 1)]), -1.0)
        self.assertIsNone(report.spearman([(1, 1), (1, 2), (1, 3)]))
        self.assertIsNone(report.spearman([(1, 1)]))

    def test_block_bootstrap_is_seeded_and_needs_ten_windows(self):
        groups = [[(i % 3, i % 3 + (i % 2)), (i % 4, i % 5)] for i in range(40)]
        first = report.block_bootstrap(groups, report.spearman, rounds=200)
        self.assertEqual(first, report.block_bootstrap(groups, report.spearman, rounds=200))
        self.assertLessEqual(first[0], first[1])
        self.assertIsNone(report.block_bootstrap(groups[:9], report.spearman))

    def test_forward_changes_skip_gaps_and_stale_quotes(self):
        minutes = {"W": {1: minute_row("W", 1, 2, 100.0, 0.50, 0.52),
                         2: minute_row("W", 2, 1, 101.0, 0.55, 0.57),
                         4: minute_row("W", 4, 0, 103.0),
                         5: minute_row("W", 5, 0, 102.0, 0.60, 0.62)}}
        one = report.forward_changes(minutes, 1)["W"]
        self.assertEqual([(r["minute"], c) for r, c, _ in one], [(1, 1.0), (4, -1.0)])
        self.assertAlmostEqual(one[0][2], 5.0)
        self.assertIsNone(one[1][2])  # minute 4 had no quote
        three = report.forward_changes(minutes, 3)["W"]
        self.assertEqual([(r["minute"], c) for r, c, _ in three], [(1, 3.0), (2, 1.0)])

    def test_market_section_uses_minute_one_price(self):
        opened = {"A": {"timeframes": {"1m": {"lean": "bullish"}}, "yes_bid": None, "yes_ask": None},
                  "B": {"timeframes": {"1m": {"lean": "bearish"}}, "yes_bid": "0.30", "yes_ask": "0.32"}}
        settled = {"A": {"result": "yes"}, "B": {"result": "yes"}}
        minutes = {"A": {1: minute_row("A", 1, 1, 100.0, 0.48, 0.50)}}
        text = "\n".join(report.market_section(opened, settled, minutes))
        self.assertIn("Leaning windows with a market price: 2; lean on the market's side: 1", text)
        self.assertIn("hit rate, lean agrees with the market: 0/1", text)
        self.assertIn("hit rate, lean disagrees with the market: 1/1", text)
        self.assertIn("hit rate, market mid 45-55c: 1/1", text)

    def test_settlement_moves_and_sigma(self):
        opened = {"A": {"target": 100.0, "sigma_1m_usd": 2.0}, "B": {"target": 100.0}}
        settled = {"A": {"expiration_value": "103.0"}, "B": {"result": "no"}}
        (ticker, _, move, scaled), = report.settlement_moves(opened, settled)
        self.assertEqual((ticker, move), ("A", 3.0))
        self.assertAlmostEqual(scaled, 3.0 / (2.0 * 15 ** 0.5))

    def test_primary_gate_and_fixed_sample(self):
        def windows(n, offset=0):
            out = {}
            for w in range(offset, offset + n):
                out[f"W{w:04d}"] = {m: minute_row(f"W{w:04d}", m, (w + m) % 3 - 1, 100.0 + ((w * m) % 5))
                                    for m in range(1, 8)}
            return out
        opened = {}
        early = datetime(2026, 10, 13, tzinfo=timezone.utc)
        late = datetime(2026, 10, 15, tzinfo=timezone.utc)
        enough = windows(report.PRIMARY_MIN_WINDOWS)
        self.assertFalse(report.primary_result(opened, enough, early)["evaluated"])
        self.assertFalse(report.primary_result(opened, windows(50), late)["evaluated"])
        with patch.object(report, "BOOTSTRAP_ROUNDS", 50), \
             patch.object(report.block_bootstrap, "__defaults__", (50, report.BOOTSTRAP_SEED)):
            first = report.primary_result(opened, enough, late)
            more = {**enough, **windows(30, offset=report.PRIMARY_MIN_WINDOWS)}
            second = report.primary_result(opened, more, late)
        self.assertTrue(first["evaluated"])
        self.assertIn(first["verdict"], ("CI excludes 0", "CI includes 0"))
        self.assertEqual((first["rho"], first["ci"]), (second["rho"], second["ci"]))
        text = "\n".join(report.primary_section(opened, windows(5), early))
        self.assertIn("primary not yet evaluated (5 windows / 800", text)

    def test_full_report_runs_on_mixed_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "log.jsonl")
            rows = [{"type": "open", "ticker": "A", "target": 100.0, "open_time": ta.iso(0),
                     "timeframes": {"1m": {"bull": 4, "bear": 1, "lean": "bullish", "family_net": 2}},
                     "yes_bid": None, "yes_ask": None},
                    {"type": "settled", "ticker": "A", "result": "yes", "expiration_value": "101"},
                    minute_row("A", 1, 2, 100.0, 0.5, 0.52), minute_row("A", 2, 1, 100.5, 0.55, 0.57)]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            text = report.report(*report.load_rows(path), now=datetime(2026, 10, 3, tzinfo=timezone.utc))
            for section in ("Legacy: opening lean", "Size of the move", "Minute horizon",
                            "market's side near open", "PRIMARY"):
                self.assertIn(section, text)
            self.assertEqual(report.load_records(path)[0]["A"]["ticker"], "A")


if __name__ == "__main__":
    unittest.main()
