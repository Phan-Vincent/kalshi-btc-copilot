"""Offline reproductions of the 2026-10-02 dashboard findings."""
import json
import math
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import ta_copilot as ta


def ready_feed(now):
    feed = ta.Feed()
    feed.spot = {"price": 100.0, "bid": 99.0, "ask": 101.0, "time": ta.iso(now)}
    feed.spot_at = now
    feed.bars["1m"] = [{"time": int(now // 60 - 30 + i) * 60,
                         "open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1.0} for i in range(30)]
    feed.venues["Coinbase"] = {"price": 100.0, "at": now, "age_basis": "ticker"}
    feed.composite, feed.composite_at = 100.0, now
    return feed


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


class SettlementDistanceTests(unittest.TestCase):
    def test_hand_computed_600_and_115_seconds(self):
        for left, factor in ((600, 9 + 1 / 3), (115, 1.25)):
            with self.subTest(left=left):
                row = ta.window_distance(90, 100, left, .01, 1000, [])
                self.assertEqual(row["distance_source"], "composite")
                self.assertEqual(row["sigma_model"], "brownian settlement-average proxy")
                self.assertAlmostEqual(row["distance"], 10)
                self.assertAlmostEqual(row["distance_sd_usd"], math.sqrt(factor))
                self.assertAlmostEqual(row["distance_sigmas"], 10 / math.sqrt(factor))

    def test_hand_computed_55_and_1_second_with_missing_samples(self):
        five = [{"time": i, "value": 100 + i - 940} for i in range(940, 945)]
        row = ta.window_distance(100, 110, 55, .01, 1000, five)
        self.assertEqual(row["distance_source"], "projected 60s average")
        self.assertEqual((row["observed_seconds"], row["missing_seconds"]), (5, 0))
        self.assertAlmostEqual(row["distance"], (sum(range(100, 105)) + 55 * 110) / 60 - 100)
        self.assertAlmostEqual(row["distance_sd_usd"], .01 * (sum(range(100, 105)) + 55 * 110) / 60
                               * math.sqrt((55 / 60) ** 3 / 3))
        fifty_eight = [{"time": i, "value": 100} for i in range(940, 999) if i != 950]
        row = ta.window_distance(100, 110, 1, .01, 1000, fifty_eight)
        self.assertEqual((row["observed_seconds"], row["missing_seconds"]), (58, 1))
        self.assertAlmostEqual(row["distance"], (58 * 100 + 2 * 110) / 60 - 100)
        self.assertAlmostEqual(row["distance_sd_usd"], .01 * (58 * 100 + 2 * 110) / 60
                               * math.sqrt((1 / 60) ** 3 / 3))


class FeedFixTests(unittest.TestCase):
    def test_expired_market_hidden_and_kalshi_age_separate(self):
        now = 12000.0
        feed = ready_feed(now)
        feed.market = {"ticker": "OLD", "open_time": ta.iso(now - 900),
                       "close_time": ta.iso(now - 1), "floor_strike": 100}
        feed.market_at = now - 12
        with patch.object(ta.time, "time", return_value=now):
            state = feed.state("1m")
        self.assertIsNone(state["window"])
        self.assertTrue(state["kalshi_stale"])
        self.assertFalse(state["stale"])
        feed.market["close_time"] = ta.iso(now + 50)
        with patch.object(ta.time, "time", return_value=now):
            state = feed.state("1m")
        self.assertGreater(state["window"]["age"], 10)
        self.assertTrue(state["kalshi_stale"])
        self.assertFalse(state["stale"])

    def test_rollover_prefetch_uses_direct_market_and_pending_target(self):
        now = 12010.0
        feed = ready_feed(now)
        feed.upcoming_markets = {"NEW": {"ticker": "NEW", "open_time": ta.iso(12000),
                                         "close_time": ta.iso(12900), "floor_strike": None,
                                         "status": "initialized"}}
        feed.upcoming_at = now - 2
        calls = []
        def get(url, params=None, timeout=8):
            calls.append(url)
            return {"markets": []} if url.endswith("/markets") else {"market": dict(feed.upcoming_markets["NEW"])}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
            state = feed.state("1m")
        self.assertEqual(calls, [f"{ta.KALSHI}/markets", f"{ta.KALSHI}/markets/NEW"])
        self.assertEqual(state["window"]["ticker"], "NEW")
        self.assertIsNone(state["window"]["strike"])
        self.assertIsNone(state["window"]["distance"])
        self.assertFalse(state["kalshi_stale"])

    def test_sampler_promotes_prefetched_window_at_boundary(self):
        feed = ready_feed(12000)
        feed.market = {"ticker": "OLD", "open_time": ta.iso(11100),
                       "close_time": ta.iso(12000)}
        feed.upcoming_markets = {"NEW": {"ticker": "NEW", "open_time": ta.iso(12000),
                                         "close_time": ta.iso(12900), "floor_strike": None,
                                         "status": "initialized"}}
        feed.upcoming_at = 11990
        feed.sample_composite(12000.05)
        self.assertEqual(feed.market["ticker"], "NEW")
        self.assertEqual(feed.market_at, 11990)

    def test_sampler_delay_recomputes_from_fresh_venues(self):
        now = 12000.0
        feed = ready_feed(now)
        feed.composite_at = now - 3
        feed.composite = 70
        with patch.object(ta.time, "time", return_value=now):
            state = feed.state("1m")
        self.assertEqual(state["composite"], 100)
        self.assertFalse(state["stale"])

    def test_delayed_coinbase_trade_is_stale_and_rest_fallback_eligible(self):
        now = time.time()
        feed = ready_feed(now)
        feed.socket_connected["Coinbase"] = True
        frame = {"type": "ticker", "product_id": "BTC-USD", "price": "80",
                 "best_bid": "79", "best_ask": "81", "time": ta.iso(now - 60)}
        feed.handle_coinbase_message(frame)
        state = feed.state("1m")
        self.assertTrue(state["stale"])
        self.assertGreater(state["spot_age"], 59)
        self.assertFalse(feed.stream_active("Coinbase"))
        self.assertEqual(feed.bars["1m"][-1]["close"], 100)

    def test_nonfinite_upstream_spot_candle_and_market_rejected(self):
        now = time.time()
        feed = ready_feed(now)
        frame = {"type": "ticker", "product_id": "BTC-USD", "price": "100",
                 "best_bid": "NaN", "best_ask": "101", "time": ta.iso(now)}
        with self.assertRaises(ValueError):
            feed.handle_coinbase_message(frame)
        self.assertEqual(feed.spot["bid"], 99)
        with patch.object(ta, "get_json", return_value={"candles": [{"start": "12000", "low": "NaN",
                   "high": "101", "open": "100", "close": "100", "volume": "1"}]}):
            with self.assertRaises(ValueError):
                feed.poll_candles("1m")
        with patch.object(ta, "get_json", return_value={"markets": [{"ticker": "BAD",
             "open_time": ta.iso(now - 1), "close_time": ta.iso(now + 100),
             "floor_strike": "NaN"}]}):
            with self.assertRaises(ValueError):
                feed.poll_market()

    def test_empty_book_falls_back_per_quote(self):
        now = 12000.0
        feed = ready_feed(now)
        feed.market = {"ticker": "NEW", "open_time": ta.iso(now - 10),
                       "close_time": ta.iso(now + 100), "status": "active",
                       "floor_strike": 100, "yes_bid_dollars": "0.47",
                       "yes_ask_dollars": "0.48", "no_bid_dollars": "0.52",
                       "no_ask_dollars": "0.53"}
        feed.market_at = now
        feed.context_ticker = "NEW"
        feed.book = {"yes_bid": {"price": "0.46", "count": "2"},
                     "yes_ask": None, "no_bid": None, "no_ask": None, "yes_mid": None}
        feed.book_at = now
        with patch.object(ta.time, "time", return_value=now):
            quotes = feed.state("1m")["window"]["display_quotes"]
        self.assertEqual(quotes["yes_bid"]["source"], "book")
        self.assertEqual(quotes["yes_ask"], {"price": .48, "source": "market summary", "age": 0})
        self.assertEqual(quotes["no_bid"]["price"], .52)

    def test_flat_rsi_is_neutral(self):
        self.assertEqual(ta.rsi([100] * 30)[-1], 50)


class StateCacheTests(unittest.TestCase):
    def test_cached_window_is_hidden_at_close_without_recomputing(self):
        now = time.time()
        ta.STATE_CACHE.clear()
        ta.STATE_CACHE["1m"] = (int(now), {"ready": True, "stale": False,
            "kalshi_stale": False, "spot_age": 0, "server_time": ta.iso(now - .5),
            "window": {"ticker": "OLD", "close_time": ta.iso(now - .2),
                       "age": 0, "seconds_left": .3}})
        with patch.object(ta.FEED, "state") as build, patch.object(ta.time, "time", return_value=now):
            state = ta.cached_state("1m")
        build.assert_not_called()
        self.assertIsNone(state["window"])
        self.assertTrue(state["kalshi_stale"])
        ta.STATE_CACHE.clear()

    def test_concurrent_callers_share_one_state_per_timeframe(self):
        ta.STATE_CACHE.clear()
        calls = []
        def build(tf):
            calls.append(tf)
            time.sleep(.02)
            return {"ready": False, "timeframe": tf}
        with patch.object(ta.FEED, "state", side_effect=build), \
             patch.object(ta.time, "time", return_value=12345.25):
            out = []
            threads = [threading.Thread(target=lambda: out.append(ta.cached_state("1m"))) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(calls, ["1m"])
        self.assertEqual(len(out), 8)
        ta.STATE_CACHE.clear()

    def test_strict_state_serialization_rejects_nan(self):
        server = QuietServer(("127.0.0.1", 0), ta.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(ta, "cached_state", return_value={"ready": True, "bid": float("nan")}):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(url + "/api/state", timeout=3)
                self.assertEqual(caught.exception.code, 503)
                state = json.loads(caught.exception.read(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                self.assertFalse(state["ready"])
                self.assertIn("state_serialization", state["errors"])
                self.assertIn("first_at", state["errors"]["state_serialization"])
                caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            with ta.FEED.lock:
                ta.FEED.errors.pop("state_serialization", None)

    def test_sse_survives_serialization_error_and_cap_returns_503(self):
        server = QuietServer(("127.0.0.1", 0), ta.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/stream"
        try:
            for _ in range(ta.SSE_LIMIT):
                self.assertTrue(ta.SSE_SLOTS.acquire(blocking=False))
            try:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(url, timeout=3)
                self.assertEqual(caught.exception.code, 503)
                caught.exception.close()
            finally:
                for _ in range(ta.SSE_LIMIT):
                    ta.SSE_SLOTS.release()
            values = iter([{"ready": True, "bad": float("nan")}])
            with patch.object(ta, "cached_state", side_effect=lambda tf: next(values, {"ready": True, "stale": False})), \
                 patch.object(ta, "STREAM_INTERVAL", .01):
                with urlopen(url, timeout=3) as response:
                    self.assertEqual(response.readline(), b"event: state\n")
                    self.assertEqual(json.loads(response.readline()[6:])["ready"], True)
        finally:
            server.shutdown()
            server.server_close()
            with ta.FEED.lock:
                ta.FEED.errors.pop("state_serialization", None)


class PageFixTests(unittest.TestCase):
    def test_clock_staleness_fallback_favicon_and_sri_are_wired(self):
        page = Path(ta.HERE / "index.html").read_text()
        script = Path(ta.HERE / "app.js").read_text()
        self.assertIn('serverOffsetMs = Date.parse(s.server_time) - Date.now()', script)
        self.assertIn('new Date(Date.now() + serverOffsetMs)', script)
        self.assertIn('Kalshi data ${Math.floor(w.age)} s old', script)
        self.assertIn('Target pending', script)
        self.assertIn('quoteSources(q.yes_bid, q.yes_ask)', script)
        self.assertIn('rel="icon" href="data:image/svg+xml,', page)
        self.assertIn('integrity="sha384-stKllnUqA9AD0gsKCuUtf5XlqAW7PwIgDagoNsTWkjkBmJ/GZ/uHTgEBxdLV2VSK"', page)


if __name__ == "__main__":
    unittest.main()
