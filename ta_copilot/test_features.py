import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ta_copilot as ta
import ta_watch as watch
import tally_report as report


class CompositeTests(unittest.TestCase):
    def test_ticker_shapes_and_timestamps(self):
        now = ta.parse_ts("2026-09-30T20:00:00Z")
        self.assertEqual(ta.venue_quote("Coinbase", {"price": "100", "time": "2026-09-30T19:59:59Z"}, now)["at"], now - 1)
        self.assertEqual(ta.venue_quote("Kraken", {"error": [], "result": {"XXBTZUSD": {"c": ["101"]}}}, now)["price"], 101)
        self.assertEqual(ta.venue_quote("Bitstamp", {"last": "102", "timestamp": str(now - 3)}, now)["at"], now - 3)
        self.assertEqual(ta.venue_quote("Gemini", {"last": "103"}, now)["age_basis"], "fetch")
        with self.assertRaises(ValueError):
            ta.venue_quote("Kraken", {"error": ["failure"]}, now)

    def test_stale_and_outlier_excluded_from_median(self):
        now = 1000
        quotes = {"Coinbase": {"price": 100, "at": 999, "age_basis": "ticker"},
                  "Kraken": {"price": 100.2, "at": 998, "age_basis": "fetch"},
                  "Bitstamp": {"price": 104, "at": 999, "age_basis": "ticker"},
                  "Gemini": {"price": 99, "at": 989, "age_basis": "fetch"}}
        value, details = ta.composite_quote(quotes, now)
        self.assertAlmostEqual(value, 100.1)
        self.assertEqual(details["Bitstamp"]["excluded_reason"], "outlier")
        self.assertEqual(details["Gemini"]["excluded_reason"], "stale")
        self.assertIsNone(ta.composite_quote({}, now)[0])

    def test_full_contiguous_60_seconds(self):
        samples = [(i, 100 + i) for i in range(60)]
        self.assertIsNone(ta.rolling_average(samples[:59], 58))
        self.assertAlmostEqual(ta.rolling_average(samples, 59), 129.5)
        self.assertIsNone(ta.rolling_average(samples[:10] + samples[11:], 59))


class LogTests(unittest.TestCase):
    def test_prefetched_window_captures_boundary_without_placeholder_quote(self):
        feed = ta.Feed()
        feed.tally_logger = Mock()
        market = {"ticker": "KXBTC15M-NEXT", "open_time": ta.iso(12000),
                  "close_time": ta.iso(12900), "status": "initialized", "floor_strike": None,
                  "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000"}
        with patch.object(ta, "get_json", return_value={"markets": [market]}), \
             patch.object(ta.time, "time", return_value=11980):
            feed.poll_upcoming()
        feed.sample_composite(12000.3)
        feed.sample_composite(12001.3)
        feed.tally_logger.submit.assert_called_once()
        snapshot = feed.tally_logger.submit.call_args.args[0]
        self.assertAlmostEqual(snapshot["captured_at"], 12000.3)
        self.assertIsNone(snapshot["market"]["yes_bid_dollars"])
        self.assertIsNone(snapshot["market"]["yes_ask_dollars"])

    def test_logging_exception_does_not_break_composite_sampling(self):
        feed = ta.Feed()
        feed.tally_logger = Mock()
        feed.tally_logger.submit.side_effect = RuntimeError("disk queue failed")
        feed.upcoming_markets = {"next": {"ticker": "next", "open_time": ta.iso(12000),
                                          "close_time": ta.iso(12900)}}
        feed.sample_composite(12000.5)
        self.assertIn("tally_log", feed.errors)
        self.assertEqual(feed.samples[-1][0], 12000)

    def test_open_is_captured_once_and_late_start_skipped(self):
        feed = ta.Feed()
        feed.tally_logger = Mock()
        market = {"ticker": "KXBTC15M-TEST", "open_time": ta.iso(12000),
                  "close_time": ta.iso(12900)}
        with patch.object(ta, "get_json", return_value={"markets": [market]}), \
             patch.object(ta.time, "time", return_value=12005):
            feed.poll_market()
            feed.poll_market()
        feed.tally_logger.submit.assert_called_once()
        late = ta.Feed()
        late.tally_logger = Mock()
        with patch.object(ta, "get_json", return_value={"markets": [market]}), \
             patch.object(ta.time, "time", return_value=12020):
            late.poll_market()
        late.tally_logger.submit.assert_not_called()
        rollover = ta.Feed()
        rollover.tally_logger = Mock()
        rollover.last_market_ticker = "KXBTC15M-OLD"
        with patch.object(ta, "get_json", return_value={"markets": [market]}), \
             patch.object(ta.time, "time", return_value=12015):
            rollover.poll_market()
        rollover.tally_logger.submit.assert_called_once()

    def test_open_and_settled_records_append(self):
        bars = [{"time": i * 60, "open": 100 + i, "high": 101 + i,
                 "low": 99 + i, "close": 100 + i, "volume": 1} for i in range(250)]
        market = {"ticker": "KXBTC15M-TEST", "open_time": ta.iso(12000), "close_time": ta.iso(12900),
                  "floor_strike": 349, "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.43"}
        row = ta.opening_record({"market": market, "captured_at": 12002,
                                 "composite": 350, "coinbase_spot": 349, "bars": {"1m": bars}})
        self.assertEqual((row["ticker"], row["composite_spot"], row["yes_ask"]), ("KXBTC15M-TEST", 350, "0.43"))
        self.assertEqual(row["capture_delay_seconds"], 2)
        self.assertIn("rsi", row["timeframes"]["1m"])
        settled = ta.settled_record(market["ticker"], {"result": "yes", "expiration_value": "350.1"}, 13000)
        self.assertEqual(settled["expiration_value"], "350.1")
        self.assertIsNone(ta.settled_record(market["ticker"], {"result": ""}, 13000))
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.prepare()
            logger.append(row)
            logger.append(settled)
            self.assertEqual(len(Path(directory, "tally_log.jsonl").read_text().splitlines()), 2)
            opened, outcomes = report.load_records(logger.path)
            self.assertEqual(report.summarize(opened, outcomes)["1m"]["scored"], 1)
            self.assertIn("too small to mean anything", report.report(opened, outcomes))

    def test_settlement_get_appends_once_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.prepare()
            logger.opened["KXBTC15M-TEST"] = {"close_time": ta.iso(12900)}
            with patch.object(ta, "get_json", return_value={"market": {"result": "yes", "expiration_value": "350.1"}}) as getter:
                logger.settle_pending(12899)
                getter.assert_not_called()
                logger.settle_pending(13000)
                logger.settle_pending(13060)
                getter.assert_called_once_with(f"{ta.KALSHI}/markets/KXBTC15M-TEST")
            rows = [json.loads(line) for line in logger.path.read_text().splitlines()]
            self.assertEqual(rows, [ta.settled_record("KXBTC15M-TEST", {"result": "yes", "expiration_value": "350.1"}, 13000)])

    def test_pending_open_survives_restart_and_hydrates_target(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            logger = ta.TallyLogger(state, ta.Feed())
            logger.prepare()
            market = {"ticker": "KXBTC15M-PENDING", "open_time": ta.iso(12000),
                      "close_time": ta.iso(12900), "status": "initialized",
                      "floor_strike": None, "yes_bid_dollars": None, "yes_ask_dollars": None}
            logger.accept_snapshot({"market": market, "captured_at": 12000.5,
                                    "coinbase_spot": 100, "composite": 101, "bars": {}})
            self.assertFalse(logger.path.exists())
            self.assertEqual(state.joinpath("tally_pending.json").stat().st_mode & 0o777, 0o600)
            restarted = ta.TallyLogger(state, ta.Feed())
            restarted.prepare()
            with patch.object(ta, "get_json", return_value={"market": {"status": "active", "floor_strike": 101.25}}), \
                 patch.object(ta.time, "time", return_value=12030):
                restarted.hydrate_pending(12030)
            row = json.loads(restarted.path.read_text().strip())
            self.assertEqual(row["target"], 101.25)
            self.assertEqual(row["capture_delay_seconds"], .5)
            self.assertIsNone(row["yes_bid"])
            self.assertEqual(row["target_observed_at"], ta.iso(12030))
            self.assertEqual(restarted.pending_open, {})

    def test_wilson_bounds(self):
        self.assertIsNone(report.wilson95(0, 0))
        lo, hi = report.wilson95(50, 100)
        self.assertLess(lo, .5)
        self.assertGreater(hi, .5)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(report.load_records(Path(directory, "missing.jsonl")), ({}, {}))


class WatchTests(unittest.TestCase):
    def test_delayed_error_and_transitions(self):
        now = ta.parse_ts("2026-09-30T20:20:00Z")
        state = {"ready": True, "stale": False,
                 "errors": {"kraken": {"at": ta.iso(now), "first_at": ta.iso(now - 601)}}}
        self.assertIn("feed kraken error >10 min", watch.problems(True, state, now))
        state["errors"]["kraken"]["first_at"] = ta.iso(now - 600)
        self.assertEqual(watch.problems(True, state, now), [])
        state["errors"]["kraken"]["first_at"] = ta.iso(now)
        persisted = watch.error_start_times(state, {"kraken": now - 601}, now)
        self.assertIn("feed kraken error >10 min", watch.problems(True, state, now, persisted))
        message, active = watch.transition({"active": False}, ["service inactive"], now)
        self.assertTrue(message.startswith("[BTC TA] problem"))
        self.assertIsNone(watch.transition(active, ["service inactive"], now + 300)[0])
        self.assertTrue(watch.transition(active, ["service inactive"], now + 21600)[0].startswith("[BTC TA] still"))
        self.assertTrue(watch.transition(active, [], now + 300)[0].startswith("[BTC TA] recovered"))

    def test_test_message_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(watch, "STATE_DIR", Path(directory)), \
                 patch.object(watch, "TEST_MARKER", Path(directory, "sent")), \
                 patch.object(watch, "send_message") as sender, \
                 patch.object(watch.sys, "argv", ["ta_watch.py", "--test"]):
                self.assertEqual(watch.main(), 0)
                self.assertEqual(watch.main(), 0)
                sender.assert_called_once_with("[BTC TA] test alert — watcher installed")


if __name__ == "__main__":
    unittest.main()
