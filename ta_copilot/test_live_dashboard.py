"""Recorded public-feed and HTTP checks; no live socket or venue calls."""
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

import ta_copilot as ta


class PublicContextTests(unittest.TestCase):
    def test_orderbook_uses_opposite_bid_as_ask_and_preserves_depth(self):
        book = ta.book_context({"orderbook_fp": {
            "yes_dollars": [["0.3500", "12.50"], ["0.3400", "90.00"]],
            "no_dollars": [["0.6000", "7.25"], ["0.5900", "8.00"]]}})
        self.assertEqual(book["yes_bid"], {"price": "0.3500", "count": "12.50"})
        self.assertEqual(book["yes_ask"], {"price": "0.4000", "count": "7.25"})
        self.assertEqual(book["no_ask"], {"price": "0.6500", "count": "12.50"})
        self.assertAlmostEqual(book["yes_mid"], .375)
        self.assertIsNone(ta.book_context({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})["yes_mid"])

    def test_recent_trades_filter_current_window(self):
        rows = {"trades": [
            {"ticker": "A", "created_time": ta.iso(102), "yes_price_dollars": "0.4200", "count_fp": "2.50"},
            {"ticker": "A", "created_time": ta.iso(99), "yes_price_dollars": "0.4000", "count_fp": "1"},
            {"ticker": "B", "created_time": ta.iso(103), "yes_price_dollars": "0.4500", "count_fp": "1"}]}
        self.assertEqual(ta.kalshi_trades(rows, "A", 100, 200), [
            {"time": ta.iso(102), "yes_price": "0.4200", "count": "2.50",
             "taker_side": None, "is_block_trade": False}])

    def test_context_uses_only_public_get_routes(self):
        feed = ta.Feed()
        feed.market = {"ticker": "A", "open_time": ta.iso(100), "close_time": ta.iso(1000)}
        calls = []
        def get(url, params=None, timeout=8):
            calls.append((url, params))
            if url.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [["0.35", "12"]], "no_dollars": [["0.60", "7"]]}}
            return {"trades": []}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=200):
            feed.poll_market_context()
        self.assertEqual([x[0] for x in calls], [
            f"{ta.KALSHI}/markets/A/orderbook", f"{ta.KALSHI}/markets/trades"])
        self.assertEqual(feed.yes_mid_path[-1]["value"], .375)


class StreamIntegrationTests(unittest.TestCase):
    def test_state_names_the_mode_of_all_four_venues(self):
        feed = ta.Feed()
        now = time.time()
        feed.spot = {"price": 100, "bid": 99, "ask": 101, "time": ta.iso(now)}
        feed.spot_at = feed.composite_at = now
        feed.composite = 100
        feed.bars["1m"] = [{"time": int(now // 60 - 30 + i) * 60, "open": 100,
                             "high": 101, "low": 99, "close": 100, "volume": 1}
                            for i in range(30)]
        modes = feed.state("1m")["feed_modes"]
        self.assertEqual(modes, {"Bitstamp": "REST", "Gemini": "REST",
                                 "Coinbase": "REST fallback", "Kraken": "REST fallback"})

    def test_recorded_coinbase_and_kraken_messages_update_modes_and_flow(self):
        feed = ta.Feed()
        now = time.time()
        stamp = ta.iso(now)
        ticker = {"type": "ticker", "product_id": "BTC-USD", "price": "64000",
                  "best_bid": "63999", "best_ask": "64001", "time": stamp}
        match = {"type": "match", "product_id": "BTC-USD", "trade_id": 111,
                 "price": "64000", "size": "0.5", "side": "sell", "time": stamp}
        kraken = {"channel": "trade", "type": "update", "data": [
            {"symbol": "BTC/USD", "trade_id": 222, "price": 64001,
             "qty": .25, "side": "sell", "timestamp": stamp}]}
        feed.socket_connected = {"Coinbase": True, "Kraken": True}
        feed.handle_coinbase_message(json.dumps(ticker))
        feed.handle_coinbase_message(json.dumps(match))
        feed.handle_coinbase_message(json.dumps(match))
        feed.handle_kraken_message(json.dumps(kraken))
        with feed.lock:
            flow = feed.flow.snapshot(now)
        self.assertAlmostEqual(flow["cvd"], .25)
        self.assertEqual(flow["trades"], 2)
        self.assertEqual(feed.spot["price"], 64000)
        self.assertEqual(feed.venues["Kraken"]["price"], 64001)
        self.assertTrue(feed.stream_active("Coinbase"))
        feed.socket_connected["Coinbase"] = False
        self.assertFalse(feed.stream_active("Coinbase"))

    def test_window_path_reset_and_wall_clock_sample(self):
        feed = ta.Feed()
        with patch.object(ta, "composite_quote", return_value=(100, {})):
            feed.sample_composite(899.1)
            feed.sample_composite(900.1)
        self.assertEqual(list(feed.window_path), [{"time": 900, "value": 100}])
        self.assertEqual(feed.flow.window_open, 900)


class SSETests(unittest.TestCase):
    def test_stream_event_and_state_route(self):
        ta.STATE_CACHE.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), ta.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        with patch.object(ta.FEED, "state", return_value={"ready": False, "stale": True}) as state:
            with urlopen(base + "/api/state?tf=1m", timeout=3) as response:
                self.assertEqual(json.load(response)["stale"], True)
            with urlopen(base + "/api/stream?tf=1m", timeout=3) as response:
                self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
                self.assertEqual(response.readline(), b"event: state\n")
                event = json.loads(response.readline()[6:])
                self.assertEqual((event["ready"], event["stale"]), (False, True))
        server.shutdown()
        server.server_close()
        ta.STATE_CACHE.clear()


class StreamTailTests(unittest.TestCase):
    def _state(self, ticker="T1", n=300):
        pts = [{"time": 60 * i, "value": float(i)} for i in range(n)]
        bars = [{"time": 60 * i, "open": 1, "high": 1, "low": 1, "close": 1} for i in range(n)]
        return {"ready": True, "frames": {"1m": {"chart": {"candles": bars, "ema9": pts}, "values": {}},
                                          "5m": {"values": {"rsi": 50}}},
                "window": {"ticker": ticker, "price_path": pts[:50], "yes_mid_path": pts[:20], "strike": 1}}

    def test_tail_state_trims_only_series(self):
        import ta_copilot as ta
        s = self._state()
        t = ta.tail_state(s, "1m")
        self.assertTrue(t["frames"]["1m"]["chart_tail"])
        self.assertEqual(len(t["frames"]["1m"]["chart"]["candles"]), ta.STREAM_TAIL)
        self.assertEqual(t["frames"]["1m"]["chart"]["ema9"][-1], s["frames"]["1m"]["chart"]["ema9"][-1])
        self.assertEqual(len(t["window"]["price_path"]), ta.STREAM_TAIL)
        self.assertEqual(t["frames"]["5m"], s["frames"]["5m"])
        self.assertEqual(len(s["frames"]["1m"]["chart"]["candles"]), 300)  # original untouched
        self.assertNotIn("chart_tail", s["frames"]["1m"])

    def test_stream_key_changes_on_window_only(self):
        import ta_copilot as ta
        a = self._state()
        self.assertEqual(ta.stream_key(a, "1m"), ta.stream_key(self._state(n=301), "1m"))
        self.assertNotEqual(ta.stream_key(a, "1m"), ta.stream_key(self._state(ticker="T2"), "1m"))


if __name__ == "__main__":
    unittest.main()
