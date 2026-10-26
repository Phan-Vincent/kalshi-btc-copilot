"""Offline checks for the 2026-10-26 descriptive TA additions."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sr_levels
import ta_copilot as ta
import tally_report as report
from test_fix_20261002 import ready_feed
from test_fix_20261007b import APP_HARNESS

HERE = Path(__file__).resolve().parent


class DerivedTickerTests(unittest.TestCase):
    def test_new_york_close_edt_est_and_midnight(self):
        cases = (("2026-07-01T14:03:00+00:00", "KXBTC15M-26JUL011015-15"),
                 ("2026-01-01T14:03:00+00:00", "KXBTC15M-26JAN010915-15"),
                 ("2026-07-02T03:53:00+00:00", "KXBTC15M-26JUL020000-00"))
        for instant, expected in cases:
            with self.subTest(instant=instant):
                self.assertEqual(ta.derived_window_ticker(datetime.fromisoformat(instant).timestamp()), expected)

    def test_derived_requires_explicit_open_and_exact_slot(self):
        now = 90010.0
        ticker = ta.derived_window_ticker(now)
        slot = 90000
        feed = ready_feed(now)
        market = {"ticker": ticker, "open_time": ta.iso(slot), "close_time": ta.iso(slot + 900),
                  "floor_strike": 100}
        calls = []
        status = {"exchange_active": True, "trading_active": True}
        def get(url, params=None, timeout=8):
            calls.append(url)
            if url.endswith("/markets"):
                return {"markets": []}
            if url.endswith("/exchange/status"):
                return status
            return {"market": market}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
            self.assertEqual(feed.state("1m")["window"]["discovery_source"], "derived")
        self.assertEqual(calls[-1], f"{ta.KALSHI}/markets/{ticker}")
        self.assertEqual(feed.market_promoted_at, 0)
        self.assertEqual(feed.market_at, now)

        cached = ready_feed(now)
        cached.exchange_status_at = now - 10
        cached.exchange_open_probe = True
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            calls.clear()
            cached.poll_market()
        self.assertEqual(cached.market_discovery_source, "derived")
        self.assertNotIn(f"{ta.KALSHI}/exchange/status", calls)

        closed = ready_feed(now)
        closed.exchange_status_at = now - 10
        closed.exchange_closed_probe = True
        closed.exchange_open_probe = False
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            calls.clear()
            closed.poll_market()
        self.assertIsNone(closed.market)
        self.assertNotIn(f"{ta.KALSHI}/markets/{ticker}", calls)

        for status in ({"exchange_active": False, "trading_active": True}, None):
            with self.subTest(status=status):
                feed = ready_feed(now)
                calls.clear()
                def gated(url, params=None, timeout=8):
                    calls.append(url)
                    return {"markets": []} if url.endswith("/markets") else status
                with patch.object(ta, "get_json", side_effect=gated), patch.object(ta.time, "time", return_value=now):
                    feed.poll_market()
                self.assertIsNone(feed.market)
                self.assertNotIn(f"{ta.KALSHI}/markets/{ticker}", calls)
        for bad in ({**market, "open_time": ta.iso(slot + 1)},
                    {**market, "close_time": ta.iso(slot + 901)}):
            with self.subTest(bad=bad):
                feed = ready_feed(now)
                with patch.object(ta, "get_json", side_effect=lambda url, params=None, timeout=8:
                                  {"markets": []} if url.endswith("/markets") else
                                  {"exchange_active": True, "trading_active": True} if url.endswith("/exchange/status") else
                                  {"market": bad}), patch.object(ta.time, "time", return_value=now):
                    feed.poll_market()
                self.assertIsNone(feed.market)

    def test_sources_listing_prefetch_direct_cached(self):
        now = 90010.0
        base = {"ticker": "TEST", "open_time": ta.iso(90000), "close_time": ta.iso(90900)}
        feed = ready_feed(now)
        with patch.object(ta, "get_json", return_value={"markets": [base]}), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
        self.assertEqual(feed.market_discovery_source, "listing")
        feed = ready_feed(now)
        feed.market, feed.market_at = base, now - 1
        with patch.object(ta, "get_json", side_effect=lambda url, params=None, timeout=8:
                          {"markets": []} if url.endswith("/markets") else {"market": base}), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
        self.assertEqual(feed.market_discovery_source, "direct")
        feed.direct_ticker, feed.direct_at = "TEST", now
        with patch.object(ta, "get_json", return_value={"markets": []}), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
        self.assertEqual(feed.market_discovery_source, "cached")
        feed = ready_feed(now)
        feed.upcoming_markets = {"TEST": base}
        feed.upcoming_at = now - 2
        feed.direct_ticker, feed.direct_at = "TEST", now
        with patch.object(ta, "get_json", return_value={"markets": []}), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
        self.assertEqual(feed.market_discovery_source, "prefetch")
        feed = ready_feed(now)
        feed.upcoming_markets = {"TEST": base}
        feed.upcoming_at = now - 2
        feed.direct_ticker, feed.direct_at = "TEST", now
        calls = []
        def listing_fails(url, params=None, timeout=8):
            calls.append(url)
            raise OSError("listing unavailable")
        with patch.object(ta, "get_json", side_effect=listing_fails), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
        self.assertEqual(feed.market_discovery_source, "prefetch")
        self.assertEqual(calls, [f"{ta.KALSHI}/markets"])


class MetricsTests(unittest.TestCase):
    def test_required_drift_cutoff(self):
        samples = [{"time": t, "value": 110.0} for t in range(940, 970)]
        row = ta.window_distance(100, 104, 30, .01, 1000, samples)
        self.assertAlmostEqual(row["required_drift_per_sec"], -14 / 30)
        self.assertIsNone(ta.window_distance(100, 104, 4, .01, 1000, samples)["required_drift_per_sec"])
        self.assertIsNone(ta.window_distance(100, 104, 61, .01, 1000, samples)["required_drift_per_sec"])

    def test_book_and_tape_edges(self):
        book = ta.book_context({"orderbook_fp": {"yes_dollars": [["0.40", "10"]],
                                                "no_dollars": [["0.50", "30"]]}})
        self.assertAlmostEqual(book["top_imbalance"], -0.5)
        self.assertAlmostEqual(book["depth_imbalance"], -0.5)
        self.assertAlmostEqual(book["microprice"], .425)
        self.assertAlmostEqual(book["micro_skew_cents"], -2.5)
        for raw in ({"yes_dollars": [], "no_dollars": [["0.5", "1"]]},
                    {"yes_dollars": [["0.4", "0"]], "no_dollars": [["0.5", "0"]]}):
            edge = ta.book_context({"orderbook_fp": raw})
            self.assertIsNone(edge["microprice"])
            if raw["yes_dollars"]:
                self.assertIsNone(edge["depth_imbalance"])
        now = 1000
        trades = [{"time": ta.iso(990), "count": "2", "yes_price": "0.40", "taker_side": "yes"},
                  {"time": ta.iso(900), "count": "3", "yes_price": "0.60", "taker_side": "no"},
                  {"time": "bad", "count": "4", "yes_price": "0.5"},
                  {"time": ta.iso(999), "count": "NaN", "yes_price": "0.5"}, None]
        result = ta.kalshi_tape_summary(trades, now)
        self.assertEqual(result["last_60s"]["contracts"], 2)
        self.assertEqual(result["window"]["trade_count"], 2)
        self.assertAlmostEqual(result["window"]["taker_imbalance"], -.2)
        self.assertAlmostEqual(result["window"]["yes_vwap"], .52)

    def test_volatility_regimes_and_no_vote_change(self):
        bars = [{"time": i * 60, "open": 100, "high": 101, "low": 99,
                 "close": 100, "volume": 1} for i in range(100)]
        with patch.object(ta, "atr", return_value=[None] * 40 + [1] * 59 + [1.3]):
            high = ta.analyze(bars, 100, 0)
        with patch.object(ta, "atr", return_value=[None] * 40 + [1] * 59 + [.7]):
            low = ta.analyze(bars, 100, 0)
        with patch.object(ta, "atr", return_value=[None] * 41 + [1] * 59):
            short = ta.analyze(bars, 100, 0)
        self.assertEqual(high["values"]["vol_regime"], "expanding")
        self.assertEqual(low["values"]["vol_regime"], "compressed")
        self.assertIsNone(short["values"]["atr_ratio"])
        self.assertEqual(high["tally"], low["tally"])

    def test_mtf_and_compact_logged_levels(self):
        bars = {tf: [{"time": i * 60, "open": 100, "high": 101, "low": 99,
                      "close": 100, "volume": 1} for i in range(35)]
                for tf in ("1m", "5m", "15m", "1h")}
        with patch.object(sr_levels, "pivot_points", return_value=[(60, 100, "high")]):
            zones = sr_levels.levels("1m", bars, {}, 100, 20, now=2100)
        zone = zones["at_price"]
        self.assertEqual(zone["higher_tf_count"], 3)
        self.assertTrue(zone["mtf_confluence"])
        state = {"ready": True, "window": {"ticker": "TEST", "open_time": ta.iso(90000),
                 "seconds_left": 840, "strike": 100}, "frames": {"1m": {"tally": {"bull": 1, "bear": 0, "neutral": 0},
                 "values": {}, "levels": zones}}, "order_flow": {}}
        row = ta.minute_record(state, 90060)
        self.assertNotIn("mtf_confluence", row["timeframes"]["1m"]["levels"])
        self.assertEqual(set(row["timeframes"]["1m"]["levels"]),
                         {"support", "support_touches", "support_dist_atr", "resistance",
                          "resistance_touches", "resistance_dist_atr", "at_price",
                          "zones_to_target", "target_in_zone"})


class ReportAndDeployTests(unittest.TestCase):
    def test_calibration_and_default_report(self):
        rows = {}
        settled = {}
        for i, (comp, yes, price) in enumerate(((102, True, .45), (98, False, .55))):
            ticker = f"T{i}"
            settled[ticker] = {"type": "settled", "ticker": ticker, "result": "yes" if yes else "no"}
            rows[ticker] = {}
            for minute, left in ((1, 840), (14, 60)):
                rows[ticker][minute] = {"type": "minute", "ticker": ticker, "minute": minute,
                    "captured_at": ta.iso(90000 + minute * 60), "composite": comp, "target": 100,
                    "sigma_1m_usd": 1, "seconds_left": left,
                    "yes_bid": {"price": price - .01, "age": 1},
                    "yes_ask": {"price": price + .01, "age": 1}}
        lines = report.calibration_section({}, settled, rows)
        self.assertTrue(any("2/2" in line and "lock-in" in line for line in lines))
        self.assertTrue(any("Wilson 95%" in line for line in lines))
        self.assertTrue(any("40–49¢" in line for line in lines))
        self.assertEqual(lines[-1], "Minute-14 composite side lock-in accuracy: 2/2 matched (100.0%), Wilson 95% [34.2%, 100.0%]")
        self.assertEqual(report.report({}, settled, rows), report.report({}, settled, rows, calibration=False))
        self.assertNotIn("== Calibration", report.report({}, settled, rows))
        self.assertIn("== Calibration", report.report({}, settled, rows, calibration=True))

    def test_primary_hash_and_deploy_check(self):
        source = (HERE / "tally_report.py").read_text()
        primary = source[source.index("def primary_result("):source.index("def primary_section(")]
        self.assertEqual(hashlib.sha256(primary.encode()).hexdigest(),
                         "0ee39f0ea8c97779607720e75872c17c7ee70e3f10c5639e29a2d56aea1485f3")
        # The Acer copy (~/btc-ta) has no acer/ directory.
        if not (HERE / "acer/deploy.sh").exists():
            return
        deploy = (HERE / "acer/deploy.sh").read_text()
        self.assertIn("check) check ;;", deploy)
        self.assertIn("  deploy)\n    check", deploy)
        self.assertIn("python3 -m py_compile", deploy)
        self.assertIn("node --check app.js", deploy)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DOMTests(unittest.TestCase):
    def test_new_context_and_proxy_render(self):
        script = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
(async () => {
  vm.createContext(context); vm.runInContext(code, context); await flush();
  const s = state('1m', 100, 0);
  s.frames['1m'].values = {atr: 10, atr_ratio: 1.3, vol_regime: 'expanding'};
  s.frames['1m'].levels = {support: [{role: 'support', low: 90, high: 91, mid: 90.5,
    distance_usd: -9.5, distance_atr: -.95, touches: 2, timeframes: ['1m', '5m', '15m'],
    sources: ['swing'], tags: [], strength: 4, mtf_confluence: true}], resistance: []};
  s.window = {ticker: 'TEST', discovery_source: 'derived', open_time: new Date(T0 - 870000).toISOString(),
    close_time: new Date(T0 + 30000).toISOString(), age: 1, strike: 100,
    distance_source: 'projected 60s average', distance: 1, observed_seconds: 30,
    required_drift_per_sec: -2, orderbook_age: 1,
    orderbook: {top_imbalance: .5, depth_imbalance: .2, microprice: .55, micro_skew_cents: 1},
    tape_summary: {last_60s: {contracts: 2, trade_count: 1, taker_imbalance: 1, yes_vwap: .5},
      window: {contracts: 3, trade_count: 2, taker_imbalance: 0, yes_vwap: .6}},
    price_path: [], yes_mid_path: [], recent_trades: []};
  streams[0].emit('state', s);
  for (const id of ['discoverySource','requiredDrift','bookImbalance','microprice','tapeSummary','zones','levels'])
    out[id] = el(id).textContent || el(id).innerHTML;
  console.log(JSON.stringify(out));
})();
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.js"
            path.write_text(script)
            proc = subprocess.run(["node", str(path)], env={**os.environ, "APP_JS": str(HERE / "app.js")},
                                  capture_output=True, text=True, check=True)
        rendered = json.loads(proc.stdout)
        self.assertIn("derived", rendered["discoverySource"])
        self.assertIn("/s needed", rendered["requiredDrift"])
        self.assertIn("+0.50 / +0.20", rendered["bookImbalance"])
        self.assertIn("MTF", rendered["zones"])
        self.assertIn("expanding", rendered["levels"])
