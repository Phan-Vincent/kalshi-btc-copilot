"""Offline regressions for the four verified audit defects (2026-10-27)."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import ta_copilot as ta
import tally_report
from test_fix_20261002 import ready_feed
from test_live_brti_20261027 import FakeSocket
from test_proxy_alarm_20261005 import Collector, market

B = 90000


class AuditFixTests(unittest.TestCase):
    def feed(self, now=1100):
        feed = ready_feed(now)
        feed.market = {"ticker": "T", "open_time": ta.iso(1000),
                       "close_time": ta.iso(1900), "floor_strike": 100}
        feed.market_at = now
        feed.context_ticker = "T"
        return feed

    def test_registered_gap_ack_cannot_restore_coverage_before_resubscribe(self):
        feed, socket = self.feed(), FakeSocket()
        feed.kalshi_connected = True
        feed.ws_subs.sids["trade"] = 5
        feed.ws_subs.tickers[5] = {"T"}
        feed.ws_trade_seq[5] = 10
        feed.ws_trade_start["T"] = 990
        feed.ws_trade_contiguous.add("T")
        feed.kalshi_tape = {"rows": {}, "from": 1000, "to": 1100}
        feed.tape_source = "websocket"
        command = feed.ws_subs.command("update_subscription",
            {"sid": 5, "action": "add_markets", "market_tickers": ["U"]}, 1100)
        resubscribe = feed._ws_resubscribe
        def interleave(*args):
            self.assertFalse(feed.ws_trade_contiguous)
            self.assertFalse(feed.ws_trade_start)
            with self.assertRaises(OSError):
                feed.poll_market_context()
            resubscribe(*args)
        with patch.object(feed, "_ws_resubscribe", side_effect=interleave), \
                patch.object(ta, "get_json", return_value={"orderbook_fp": {}}), \
                patch.object(ta, "fetch_trades", side_effect=OSError("offline")), \
                patch.object(ta.time, "time", return_value=1101):
            feed.handle_kalshi_frame({"type": "ok", "sid": 5, "seq": 12,
                                     "id": command["id"]}, 1101, socket)
            state = feed.state("1m")
        self.assertNotEqual(feed.tape_source, "websocket")
        self.assertFalse(state["window"]["tape_summary"]["complete_window"])
        self.assertTrue(socket.sent)

    def test_cached_brti_threshold_rebuilds_math_and_log_without_mutation(self):
        feed = self.feed(1060.1)
        feed.kalshi_key, feed.kalshi_scoped = object(), True
        feed.brti_value, feed.brti_source_at = 110.0, 1058.2
        ta.STATE_CACHE.clear()
        self.addCleanup(ta.STATE_CACHE.clear)
        with patch.object(ta, "FEED", feed):
            with patch.object(ta.time, "time", return_value=1060.1):
                original = ta.cached_state("1m")
                saved = copy.deepcopy(original)
            with patch.object(ta.time, "time", return_value=1060.15):
                aged = ta.cached_state("1m")
            self.assertIsNot(aged["brti"], original["brti"])
            self.assertAlmostEqual(aged["brti"]["age"], 1.95)
            with patch.object(ta.time, "time", return_value=1060.9):
                state = ta.cached_state("1m")
                rebuilt = feed.state("1m")
        self.assertFalse(state["brti"]["fresh"])
        self.assertAlmostEqual(state["brti"]["age"], 2.7)
        self.assertEqual(state["window"]["settlement_basis"], "proxy")
        self.assertEqual(state["window"]["distance"], rebuilt["window"]["distance"])
        self.assertIsNone(ta.minute_record(state, 1060.9)["brti"])
        self.assertEqual(original, saved)

    def test_window_and_previous_average_at_close_and_promotion(self):
        for keyed in (False, True):
            for promoted in (False, True):
                for now in (1899.9, 1900, 1905, 1930):
                    with self.subTest(keyed=keyed, promoted=promoted, now=now):
                        feed = self.feed(now)
                        feed.brti_seconds = dict.fromkeys(range(1840, 1900), 101.0)
                        if keyed:
                            feed.kalshi_key, feed.kalshi_scoped = object(), True
                        if promoted and now >= 1900:
                            feed.previous_market = feed.market
                            feed.market = {"ticker": "U", "open_time": ta.iso(1900),
                                           "close_time": ta.iso(2800), "floor_strike": 100}
                        with patch.object(ta.time, "time", return_value=now):
                            state = feed.state("1m")
                        window = state["window"]
                        if now < 1900:
                            self.assertEqual(window["ticker"], "T")
                            if keyed:
                                self.assertEqual(window["settlement_average"],
                                    ta.brti_settlement_average(feed.brti_seconds, 1900, now))
                        elif promoted:
                            self.assertEqual(window["ticker"], "U")
                        else:
                            self.assertIsNone(window)
                        previous = state["previous_settlement_average"]
                        if keyed and 1900 <= now < 1930:
                            self.assertEqual(previous["ticker"], "T")
                            self.assertEqual(previous["value"], 101.0)
                            self.assertTrue(previous["complete"])
                        else:
                            self.assertIsNone(previous)

    def test_cached_window_crossing_fractional_close_and_previous_expiry(self):
        feed = self.feed(1900.1)
        feed.kalshi_key, feed.kalshi_scoped = object(), True
        feed.market["close_time"] = ta.iso(1900.5)
        ta.STATE_CACHE.clear()
        self.addCleanup(ta.STATE_CACHE.clear)
        with patch.object(ta, "FEED", feed):
            for now, open_window, previous in ((1900.1, True, False), (1900.6, False, True),
                                                (1930.1, False, True), (1930.6, False, False)):
                with patch.object(ta.time, "time", return_value=now):
                    state = ta.cached_state("1m")
                self.assertEqual(state["window"] is not None, open_window)
                self.assertEqual(state["previous_settlement_average"] is not None, previous)

    def close_feed(self, missing=(), keyed=True):
        feed = ready_feed(B - 1)
        feed.market = market(B)
        feed.close_boundary = B - 900
        feed.samples.extend((t, 100 + t - B + 60) for t in range(B - 60, B))
        feed.brti_seconds = {t: 200.0 for t in range(B - 60, B) if t not in missing}
        feed.kalshi_key = object() if keyed else None
        feed.kalshi_scoped = keyed
        feed.tally_logger = Collector()
        return feed

    def capture_close(self, feed):
        jobs = []
        def thread(*args, **kwargs):
            self.assertTrue(kwargs["daemon"])
            job = unittest.mock.Mock()
            job.start.side_effect = lambda: jobs.append(kwargs)
            return job
        expected = feed._close_proxy(B, B + .05)
        with patch.object(ta.threading, "Thread", side_effect=thread):
            feed.sample_composite(B + .05)
            feed.sample_composite(B + .1)
        self.assertEqual(feed.tally_logger.rows, [], "keyed close submitted before finalization")
        self.assertEqual(len(jobs), 1)
        self.assertFalse(feed.lock.locked())
        return jobs[0], expected

    def finalize(self, feed, job, tick=None, backfill=None):
        clock = [B + .05]
        workers = []
        def sleep(_):
            clock[0] = round(clock[0] + .1, 2)
            if tick and clock[0] >= B + .5:
                feed.handle_kalshi_frame({"type": "cfbenchmarks_value_5hz", "msg": {
                    "index_id": "BRTI", "source_ts_ms": (B - 1) * 1000,
                    "value_usd": "200"}}, clock[0])
        def thread(*args, **kwargs):
            worker = unittest.mock.Mock()
            def start():
                workers.append(kwargs)
                kwargs["target"]()
            worker.start.side_effect = start
            return worker
        def fill(**kwargs):
            self.assertTrue(feed.lock.acquire(blocking=False), "backfill ran under feed lock")
            feed.lock.release()
            if backfill:
                backfill()
        with patch.object(ta.time, "time", side_effect=lambda: clock[0]), \
                patch.object(ta.time, "sleep", side_effect=sleep), \
                patch.object(ta.threading, "Thread", side_effect=thread), \
                patch.object(feed, "backfill_brti", side_effect=fill):
            job["target"](*job["args"])
        self.assertEqual(len(feed.tally_logger.rows), 1)
        return feed.tally_logger.rows[0], workers

    def assert_frozen_composite(self, row, expected):
        for key in ("proxy_avg_60s", "samples", "captured_at", "ticker", "close_time"):
            self.assertEqual(row[key], expected[key])
        self.assertIsNone(tally_report.record_problem(row))

    def test_deferred_close_accepts_late_last_second(self):
        feed = self.close_feed(missing=(B - 1,))
        job, expected = self.capture_close(feed)
        row, workers = self.finalize(feed, job, tick=True)
        self.assertEqual((row["brti_samples"], row["brti_avg_60s"]), (60, 200.0))
        self.assertAlmostEqual(ta.parse_ts(row["brti_finalized_at"]), B + .55)
        self.assertFalse(workers)
        self.assert_frozen_composite(row, expected)
        complete = self.close_feed()
        job, expected = self.capture_close(complete)
        row, workers = self.finalize(complete, job)
        self.assertEqual(row["brti_samples"], 60)
        self.assertEqual(row["brti_finalized_at"], expected["captured_at"])
        self.assertFalse(workers)

    def test_deferred_close_backfills_one_missing_second(self):
        feed = self.close_feed(missing=(B - 10,))
        job, expected = self.capture_close(feed)
        row, workers = self.finalize(feed, job,
            backfill=lambda: feed.brti_seconds.update({B - 10: 200.0}))
        self.assertEqual((row["brti_samples"], row["brti_avg_60s"]), (60, 200.0))
        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0]["daemon"])
        self.assert_frozen_composite(row, expected)

    def test_deferred_close_deadline_preserves_true_count(self):
        self.assert_keyless_close_is_immediate_and_unchanged()
        feed = self.close_feed(missing=(B - 1, B - 10))
        job, expected = self.capture_close(feed)
        row, workers = self.finalize(feed, job)
        self.assertEqual((row["brti_samples"], row["brti_avg_60s"]), (58, None))
        self.assertLessEqual(abs(ta.parse_ts(row["brti_finalized_at"]) - B - 10), .1)
        self.assertEqual(len(workers), 1)
        self.assert_frozen_composite(row, expected)
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), feed)
            with patch.object(logger, "append") as append:
                logger.accept_close(row)
                logger.accept_close(row)
            append.assert_called_once_with(row)
        failed = self.close_feed(missing=(B - 1,))
        job, expected = self.capture_close(failed)
        def unavailable():
            raise OSError("offline")
        row, workers = self.finalize(failed, job, backfill=unavailable)
        self.assertEqual((row["brti_samples"], row["brti_avg_60s"]), (59, None))
        self.assertEqual(len(workers), 1)
        self.assertNotIn("brti", failed.errors)
        failed.kalshi_scoped = False
        with patch.object(failed, "backfill_brti") as backfill:
            failed._close_backfill()
        backfill.assert_not_called()

    def test_slow_backfill_cannot_block_close_deadline(self):
        feed = self.close_feed(missing=(B - 1,))
        job, expected = self.capture_close(feed)
        clock = [B + .05]
        started, release = threading.Event(), threading.Event()
        callers, errors = [], []
        def backfill(**kwargs):
            callers.append(threading.current_thread())
            self.assertTrue(kwargs["force"])
            with feed.lock:
                started.set()
            release.wait(2)
        def sleep(_):
            clock[0] = round(clock[0] + .1, 2)
            if clock[0] >= B + 3.15:
                if not started.wait(1):
                    raise AssertionError("backfill never started")
        def run():
            try:
                job["target"](*job["args"])
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        try:
            with patch.object(ta.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(ta.time, "sleep", side_effect=sleep), \
                    patch.object(feed, "backfill_brti", side_effect=backfill):
                worker.start()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertFalse(errors)
                self.assertEqual(len(feed.tally_logger.rows), 1)
                row = feed.tally_logger.rows[0]
                self.assertEqual((row["brti_samples"], row["brti_avg_60s"]), (59, None))
                self.assertLessEqual(abs(ta.parse_ts(row["brti_finalized_at"]) - B - 10), .1)
                self.assertEqual(len(callers), 1)
                self.assertIsNot(callers[0], worker)
                self.assertIsNot(callers[0], threading.current_thread())
                self.assert_frozen_composite(row, expected)
        finally:
            release.set()
            worker.join(2)
            for caller in callers:
                caller.join(2)

    def assert_keyless_close_is_immediate_and_unchanged(self):
        feed = self.close_feed(missing=(B - 1,), keyed=False)
        expected = feed._close_proxy(B, B + .05)
        with patch.object(ta.threading, "Thread") as worker:
            feed.sample_composite(B + .05)
            feed.sample_composite(B + .5)
        self.assertEqual(feed.tally_logger.rows, [expected])
        self.assertNotIn("brti_finalized_at", expected)
        worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
