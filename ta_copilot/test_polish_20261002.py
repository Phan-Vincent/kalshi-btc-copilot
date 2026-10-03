"""Offline checks for rollover freshness and dashboard response policy."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import ta_copilot as ta
from test_fix_20261002 import ready_feed


def rollover_feed():
    feed = ready_feed(12000)
    feed.market = {"ticker": "OLD", "open_time": ta.iso(11100),
                   "close_time": ta.iso(12000)}
    feed.last_market_ticker = "OLD"
    feed.upcoming_markets = {"NEW": {"ticker": "NEW", "open_time": ta.iso(12000),
                                     "close_time": ta.iso(12900), "floor_strike": None,
                                     "status": "initialized"}}
    feed.upcoming_at = 11980
    feed.sample_composite(12000.05)
    return feed


class RolloverTests(unittest.TestCase):
    def test_promotion_refreshes_immediately_and_stays_fresh(self):
        feed = rollover_feed()
        promoted_at = feed.market_promoted_at
        self.assertTrue(feed.market_wake.is_set())
        self.assertEqual(feed.market_at, 11980)
        with patch.object(ta.time, "time", return_value=12000.1):
            self.assertFalse(feed.state("1m")["kalshi_stale"])
        calls = []

        def get(url, params=None, timeout=8):
            calls.append(url)
            return ({"market": dict(feed.upcoming_markets["NEW"])}
                    if url.endswith("/markets/NEW") else {"markets": []})

        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=12000.1):
            feed.poll_market()
            self.assertFalse(feed.state("1m")["kalshi_stale"])
            self.assertAlmostEqual(feed.market_at, 12000.1)
        self.assertEqual(calls, [f"{ta.KALSHI}/markets/NEW", f"{ta.KALSHI}/markets"])
        self.assertLessEqual(feed.direct_at - promoted_at, 1)

    def test_market_loop_wakes_before_two_second_timer(self):
        feed = ta.Feed()
        first = threading.Event()
        second = threading.Event()
        calls = []

        def poll():
            calls.append(1)
            if len(calls) == 1:
                first.set()
            else:
                second.set()
                raise SystemExit

        def run():
            try:
                feed.run_market()
            except SystemExit:
                pass

        with patch.object(feed, "poll_market", side_effect=poll):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            self.assertTrue(first.wait(1))
            feed.market_wake.set()
            self.assertTrue(second.wait(1))
            thread.join(1)

    def test_all_gets_fail_and_promotion_grace_expires(self):
        feed = rollover_feed()
        with patch.object(ta, "get_json", side_effect=OSError("Kalshi unavailable")) as get:
            for at in (12000.1, 12002.2, 12004.5, 12005.1, 12009.9):
                with patch.object(ta.time, "time", return_value=at):
                    feed.poll_market()
                    state = feed.state("1m")
                self.assertEqual(state["window"]["ticker"], "NEW")
                self.assertEqual(state["kalshi_stale"], at >= 12005.05)
                self.assertIn("kalshi", state["errors"])
        self.assertGreaterEqual(get.call_count, 5)
        self.assertEqual(feed.market_at, 11980)

    def test_newer_prefetch_counts_as_current_window_fetch(self):
        feed = ready_feed(12040)
        feed.market = {"ticker": "NEW", "open_time": ta.iso(12000),
                       "close_time": ta.iso(12900), "floor_strike": 100}
        feed.market_at = 12000
        feed.last_market_ticker = "NEW"
        feed.upcoming_markets = {"NEW": dict(feed.market)}
        feed.upcoming_at = 12035

        def get(url, params=None, timeout=8):
            if url.endswith("/markets"):
                return {"markets": []}
            raise OSError("detail unavailable")

        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=12040):
            feed.poll_market()
            state = feed.state("1m")
        self.assertEqual(feed.market_at, 12035)
        self.assertEqual(state["window"]["age"], 5)
        self.assertFalse(state["kalshi_stale"])
        self.assertIn("kalshi", state["errors"])

    def test_listing_fallback_promotion_gets_grace(self):
        feed = ready_feed(12000)
        feed.market_at = 11950
        feed.last_market_ticker = "OLD"
        feed.upcoming_markets = {"NEW": {"ticker": "NEW", "open_time": ta.iso(12000),
                                         "close_time": ta.iso(12900), "floor_strike": None}}
        feed.upcoming_at = 11980

        def get(url, params=None, timeout=8):
            if url.endswith("/markets"):
                return {"markets": []}
            raise OSError("detail unavailable")

        with patch.object(ta, "get_json", side_effect=get):
            with patch.object(ta.time, "time", return_value=12000.1):
                feed.poll_market()
                self.assertFalse(feed.state("1m")["kalshi_stale"])
            with patch.object(ta.time, "time", return_value=12005.2):
                self.assertTrue(feed.state("1m")["kalshi_stale"])


class SecurityHeadersTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ta.Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def assert_policy(self, headers):
        self.assertEqual(headers["Content-Security-Policy"], ta.CSP)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_policy_on_page_script_state_stream_and_error(self):
        with patch.object(ta, "cached_state", return_value={"ready": False, "stale": True}):
            for path in ("/", "/app.js", "/api/state", "/api/stream"):
                with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                    self.assert_policy(response.headers)
                    if path == "/app.js":
                        self.assertEqual(response.headers["Content-Type"], "application/javascript; charset=utf-8")
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                        self.assertIn(b"connectStream()", response.read())
                    elif path == "/api/stream":
                        self.assertEqual(response.readline(), b"event: state\n")
                    else:
                        response.read()
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/missing", timeout=3)
        self.assert_policy(caught.exception.headers)
        caught.exception.close()

    def test_external_script_and_deploy_list(self):
        page = Path(ta.HERE / "index.html").read_text()
        self.assertIn('<script src="/app.js"></script>', page)
        self.assertNotIn("<script>", page)
        self.assertIn("integrity=\"sha384-", page)
        deploy = Path(ta.HERE / "acer/deploy.sh")
        if deploy.exists():
            self.assertIn('"$SRC/app.js"', deploy.read_text())


if __name__ == "__main__":
    unittest.main()
