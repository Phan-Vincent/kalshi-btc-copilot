#!/usr/bin/env python3
"""Adversarial QA regression tests (2026-10-25) for BTC TA Copilot."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import acer_watch
import log_tools
import ta_copilot
import ta_watch
import tally_report


class AdversarialQA20261025Tests(unittest.TestCase):
    def test_ta01_record_problem_rejects_malformed_flags_samples_and_nan_indicators(self):
        """record_problem must reject non-list history_stale, non-bool stale, bad samples, and NaN rsi/macd_hist."""
        self.assertEqual(
            tally_report.record_problem(
                {"type": "minute", "ticker": "W1", "minute": 1, "captured_at": "2026-10-07T00:01:00Z", "history_stale": 123}
            ),
            "bad history_stale",
        )
        self.assertEqual(
            tally_report.record_problem(
                {"type": "minute", "ticker": "W1", "minute": 1, "captured_at": "2026-10-07T00:01:00Z", "history_stale": [1]}
            ),
            "bad history_stale",
        )
        self.assertEqual(
            tally_report.record_problem(
                {"type": "minute", "ticker": "W1", "minute": 1, "captured_at": "2026-10-07T00:01:00Z", "stale": "yes"}
            ),
            "bad stale",
        )
        self.assertEqual(
            tally_report.record_problem(
                {"type": "close", "ticker": "W1", "close_time": "2026-10-07T00:15:00Z", "samples": True}
            ),
            "bad samples",
        )
        self.assertEqual(
            tally_report.record_problem(
                {"type": "close", "ticker": "W1", "close_time": "2026-10-07T00:15:00Z", "samples": -1}
            ),
            "bad samples",
        )
        self.assertEqual(
            tally_report.record_problem(
                {
                    "type": "open",
                    "ticker": "W1",
                    "open_time": "2026-10-07T00:00:00Z",
                    "timeframes": {"1m": {"bull": 2, "bear": 1, "neutral": 5, "lean": "bullish", "rsi": float("nan")}},
                }
            ),
            "bad rsi",
        )
        self.assertEqual(
            tally_report.record_problem(
                {
                    "type": "open",
                    "ticker": "W1",
                    "open_time": "2026-10-07T00:00:00Z",
                    "timeframes": {"1m": {"bull": 2, "bear": 1, "neutral": 5, "lean": "bullish", "macd_hist": "bad"}},
                }
            ),
            "bad macd_hist",
        )
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.jsonl"
            p2 = Path(tmp) / "b.jsonl"
            out = Path(tmp) / "out.jsonl"
            p1.write_text(
                '{"type":"open","ticker":"W1","open_time":"2026-10-07T00:00:00Z","timeframes":{"1m":{"bull":2,"bear":1,"neutral":5,"lean":"bullish","rsi":NaN}}}\n',
                encoding="utf-8",
            )
            p2.write_text(
                '{"type":"open","ticker":"W2","open_time":"2026-10-07T00:15:00Z","timeframes":{"1m":{"bull":2,"bear":1,"neutral":5,"lean":"bullish","rsi":55.0}}}\n',
                encoding="utf-8",
            )
            rc = log_tools.merge(p1, p2, out)
            self.assertEqual(rc, 0)
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["ticker"], "W2")

    def test_ta01_settle_pending_skips_rows_missing_close_time(self):
        """settle_pending must not raise KeyError or latch tally_settle when an open row has no close_time."""
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "tally_log.jsonl").write_text(
                '{"type":"open","ticker":"W1","captured_at":"2026-10-07T00:00:01Z"}\n', encoding="utf-8"
            )
            feed = ta_copilot.Feed()
            logger = ta_copilot.TallyLogger(state_dir, feed)
            logger.prepare()
            logger.settle_pending(now=2_000_000_000.0)
            self.assertNotIn("tally_settle", feed.errors)

    def test_ta02_closes_sorted_by_parsed_timestamp_and_prepare_resets_state(self):
        """Fractional-second ISO close_time must sort after whole-second ISO in refresh_proxy, prune_memory, and proxy_section."""
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            path = state_dir / "tally_log.jsonl"
            rows = [
                {"type": "close", "ticker": "W_EARLY", "close_time": "2026-10-07T00:15:00Z", "proxy_avg_60s": 64000.0, "samples": 60},
                {"type": "settled", "ticker": "W_EARLY", "result": "yes", "expiration_value": 64100.0},
                {"type": "close", "ticker": "W_LATE", "close_time": "2026-10-07T00:15:00.500000Z", "proxy_avg_60s": 64000.0, "samples": 60},
                {"type": "settled", "ticker": "W_LATE", "result": "yes", "expiration_value": 64001.0},
            ]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            feed = ta_copilot.Feed()
            logger = ta_copilot.TallyLogger(state_dir, feed)
            with patch.object(ta_copilot, "PROXY_RECORD_WINDOWS", 1), patch.object(ta_copilot, "MAX_MEMORY_WINDOWS", 1):
                logger.prepare()
                self.assertEqual(logger.proxy["median_abs"], 1.0)
                self.assertIn("W_LATE", logger.closes)
                self.assertNotIn("W_EARLY", logger.closes)
            # Calling prepare() again on a smaller file clears stale in-memory state.
            path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
            logger.prepare()
            self.assertEqual(set(logger.closes), {"W_EARLY"})
            self.assertEqual(set(logger.settled), set())
            # Orphan settled tickers without open/close rows are bounded by prune_memory().
            for i in range(10):
                logger.settled.add(f"ORPHAN_{i}")
                logger.results[f"ORPHAN_{i}"] = "yes"
            with patch.object(ta_copilot, "PROXY_RECORD_WINDOWS", 2), patch.object(ta_copilot, "MAX_MEMORY_WINDOWS", 2):
                logger.prune_memory()
            self.assertEqual(len(logger.settled), 0)

    def test_ta03_update_spot_accepts_missing_bid_ask_and_rejects_nonpositive_bid_ask(self):
        """Coinbase ticker without bid/ask must not crash update_spot, while nonpositive bid/ask still raises ValueError."""
        feed = ta_copilot.Feed()
        now = 1760000010.0
        with patch("ta_copilot.time.time", return_value=now):
            feed.handle_coinbase_message(
                {"type": "ticker", "product_id": "BTC-USD", "price": "65000.00", "time": ta_copilot.iso(now)}
            )
            self.assertEqual(feed.spot["price"], 65000.0)
            self.assertIsNone(feed.spot["bid"])
            self.assertIsNone(feed.spot["ask"])
            with self.assertRaises(ValueError):
                feed.update_spot(65000.0, 0.0, 65001.0, ta_copilot.iso(now), {"price": 65000.0, "at": now, "age_basis": "ticker"}, now)

    def test_ta04_poll_market_preserves_cached_exchange_closed_across_transient_listing_error(self):
        """A transient /markets error during a confirmed exchange closure must not drop exchange_closed inside the cache TTL."""
        feed = ta_copilot.Feed()
        feed.exchange_closed = True
        feed.exchange_closed_probe = True
        feed.exchange_status_at = 20000.0
        with patch("ta_copilot.time.time", return_value=20005.0), patch(
            "ta_copilot.get_json", side_effect=OSError("HTTP 503")
        ):
            feed.poll_market()
        self.assertTrue(feed.exchange_closed)
        self.assertIn("kalshi", feed.errors)

    def test_ta05_watchers_reject_boolean_and_nonfinite_state_values(self):
        """ta_watch and acer_watch must reject bools and NaN/inf in persisted state and watcher_age."""
        self.assertFalse(ta_watch.valid_state({"active": False, "last_sent_at": True}))
        self.assertFalse(ta_watch.valid_state({"active": False, "last_sent_at": float("nan")}))
        self.assertFalse(ta_watch.valid_state({"active": False, "last_sent_at": 0, "feed_since": {"spot": False}}))
        self.assertFalse(ta_watch.valid_state({"active": False, "last_sent_at": 0, "kalshi_stale_since": True}))
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text('{"fails": true, "active": false, "last_sent_at": 0}\n', encoding="utf-8")
            with patch.object(acer_watch, "STATE_FILE", state_file):
                self.assertEqual(acer_watch.load_previous(1000.0), {})


if __name__ == "__main__":
    unittest.main()
