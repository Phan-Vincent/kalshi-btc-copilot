"""Adversarial QA 2026-10-07, additional resilience items and watcher heartbeat."""
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import acer_watch
import stream_flow as sf
from stream_flow import Trade
import ta_copilot as ta
import tally_report as report
from test_fix_20261002 import ready_feed


class SettlementValueTests(unittest.TestCase):
    def logger(self, directory, close):
        logger = ta.TallyLogger(Path(directory), ta.Feed())
        logger.opened = {"T": {"ticker": "T", "close_time": ta.iso(close), "timeframes": {}}}
        return logger

    def settle(self, logger, market, now):
        with patch.object(ta, "get_json", return_value={"market": market}):
            logger.settle_pending(now)

    def test_waits_for_the_expiration_value(self):
        close = 100000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            self.settle(logger, {"result": "yes"}, close + 60)
            self.assertNotIn("T", logger.settled)
            self.assertFalse(logger.path.exists())
            self.settle(logger, {"result": "yes", "expiration_value": "84000.12"}, close + 120)
            self.assertIn("T", logger.settled)
            _, settled, _ = report.load_rows(logger.path)
            self.assertEqual(settled["T"]["expiration_value"], "84000.12")
            self.assertEqual(logger.expirations["T"], "84000.12")

    def test_writes_without_the_value_after_the_wait(self):
        close = 100000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            self.settle(logger, {"result": "no"}, close + ta.SETTLE_VALUE_WAIT + 1)
            _, settled, _ = report.load_rows(logger.path)
            self.assertEqual(settled["T"]["result"], "no")
            self.assertNotIn("expiration_value", settled["T"])


class FlowOverflowTests(unittest.TestCase):
    def test_absurd_sizes_cannot_make_totals_infinite(self):
        flow = sf.OrderFlow()
        now = 90000.0
        flow.advance(now)
        self.assertTrue(flow.add_trade(Trade("kraken", "BTC/USD", 1.0, 1e308, now + 1, "buy", "a")))
        self.assertFalse(flow.add_trade(Trade("kraken", "BTC/USD", 1.0, 1e308, now + 2, "buy", "b")))
        self.assertFalse(flow.add_trade(Trade("kraken", "BTC/USD", 1e10, 1e300, now + 3, "sell", "c")))
        self.assertEqual(flow.counters["invalid"], 2)
        json.dumps(flow.snapshot(now + 5), allow_nan=False)  # stays servable


def five_minute(times):
    return [{"time": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0} for t in times]


class DerivedBarTests(unittest.TestCase):
    def test_interior_bucket_missing_a_source_bar_is_partial(self):
        times = [6000 + 300 * i for i in range(10) if i != 5]  # 10m bucket at 7200 lacks its 7500 bar
        bars = ta.aggregate(five_minute(times), 600, 300)
        partial = [b["time"] for b in bars if b.get("partial")]
        self.assertEqual(partial, [7500 // 600 * 600])
        self.assertTrue(ta.history_quality(bars, 600, bars[-1]["time"], bars[-1]["time"] + 60)["gap"])

    def test_forming_bucket_and_complete_history_are_not_partial(self):
        times = [6000 + 300 * i for i in range(9)]  # last 10m bucket has only its first (forming) bar
        bars = ta.aggregate(five_minute(times), 600, 300)
        self.assertFalse(any(b.get("partial") for b in bars))
        self.assertFalse(ta.history_quality(bars, 600, bars[-1]["time"], bars[-1]["time"] + 60)["gap"])
        self.assertNotIn("partial", ta.aggregate(five_minute(times), 600)[0])  # no source width: unchanged


class CandlePagingTests(unittest.TestCase):
    def test_two_pages_leave_no_bar_between_them(self):
        def candles(url, params):
            sec = 300
            first = -(-params["start"] // sec) * sec  # bars whose start lies in [start, end]
            return {"candles": [{"start": str(t), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
                                for t in range(first, params["end"] + 1, sec)][::-1]}

        feed = ta.Feed()
        with patch.object(ta, "get_json", side_effect=candles), patch.object(ta.time, "time", return_value=1791400123.0):
            feed.poll_candles("5m")
        times = [b["time"] for b in feed.bars["5m"]]
        self.assertEqual(len(times), ta.MAX_BARS)
        self.assertTrue(all(b - a == 300 for a, b in zip(times, times[1:])))
        self.assertFalse(any(b.get("partial") for b in feed.bars["10m"]))


class WatcherHeartbeatTests(unittest.TestCase):
    def test_state_reports_the_watcher_age(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ta_watch_state.json"
            with patch.object(ta, "WATCH_STATE", path):
                now = time.time()
                self.assertIsNone(ta.watcher_age(now))
                path.write_text("{}")
                os.utime(path, (now - 400, now - 400))
                self.assertAlmostEqual(ta.watcher_age(now), 400, delta=1)
                self.assertAlmostEqual(ready_feed(now).state("1m")["watcher_age"], 400, delta=2)

    def test_mac_alarm_flags_a_stopped_watcher(self):
        class Response(io.BytesIO):
            status = 200

        healthy = {"ready": True, "stale": False, "kalshi_stale": False}
        for age, expected in ((1000, "Acer watcher has not completed a run for 17 min"), (290, None), (None, None)):
            with self.subTest(age=age), \
                 patch.object(acer_watch, "urlopen", return_value=Response(json.dumps({**healthy, "watcher_age": age}).encode())):
                self.assertEqual(acer_watch.dashboard_problem(), expected)


if __name__ == "__main__":
    unittest.main()
