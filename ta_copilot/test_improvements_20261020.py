"""Verification suite for TA Copilot improvements (2026-10-20)."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import acer_watch
import log_tools
import sr_levels as sr
import stream_flow as sf
import ta_copilot as ta
import ta_watch
import tally_report as report
from test_fix_20261002 import ready_feed
from test_fix_20261007b import APP_HARNESS

HERE = Path(__file__).resolve().parent


class FinalMinuteLockInTests(unittest.TestCase):
    def test_required_remaining_average_and_observed_mean(self):
        # Outside the final minute: required_remaining_avg is None.
        early = ta.window_distance(100, 105, 120, 0.01, 1000, [])
        self.assertIsNone(early["observed_mean"])
        self.assertIsNone(early["required_remaining_avg"])
        self.assertIsNone(early["required_remaining_move"])

        # Inside the final minute: 30 observed seconds at 110, strike 100, composite 104.
        samples = [{"time": t, "value": 110.0} for t in range(940, 970)]
        row = ta.window_distance(100, 104, 30, 0.01, 1000, samples)
        self.assertEqual(row["observed_seconds"], 30)
        self.assertAlmostEqual(row["observed_mean"], 110.0)
        # (30 * 110 + 30 * req) / 60 = 100 -> req = 90.0; move from composite 104 is -14.0.
        self.assertAlmostEqual(row["required_remaining_avg"], 90.0)
        self.assertAlmostEqual(row["required_remaining_move"], -14.0)


class MultiLevelOrderbookTests(unittest.TestCase):
    def test_five_level_ladder_and_spread(self):
        raw = {"orderbook_fp": {
            "yes_dollars": [[f"0.{30 + i:02d}", str(10 + i)] for i in range(7)],
            "no_dollars": [[f"0.{55 + i:02d}", str(20 + i)] for i in range(6)],
        }}
        book = ta.book_context(raw)
        # Top of book is highest bid (0.36 YES, 0.60 NO -> 0.40 YES ask).
        self.assertEqual(book["yes_bid"], {"price": "0.36", "count": "16"})
        self.assertEqual(book["yes_ask"], {"price": "0.40", "count": "25"})
        self.assertEqual(len(book["yes_bids"]), 5)
        self.assertEqual(len(book["yes_asks"]), 5)
        self.assertEqual([x["price"] for x in book["yes_bids"]],
                         ["0.36", "0.35", "0.34", "0.33", "0.32"])
        self.assertEqual([x["price"] for x in book["yes_asks"]],
                         ["0.40", "0.41", "0.42", "0.43", "0.44"])
        self.assertAlmostEqual(book["yes_spread"], 0.04)
        self.assertAlmostEqual(book["yes_mid"], 0.38)


class ExchangeClosureTests(unittest.TestCase):
    def test_is_exchange_closed_requires_boolean_flags(self):
        self.assertTrue(ta.is_exchange_closed({"exchange_active": True, "trading_active": False}))
        self.assertTrue(ta.is_exchange_closed({"data": {"exchange_active": False, "trading_active": True}}))
        self.assertFalse(ta.is_exchange_closed({"exchange_active": True, "trading_active": True}))
        self.assertFalse(ta.is_exchange_closed({"exchange_active": "false", "trading_active": False}))
        self.assertFalse(ta.is_exchange_closed({"exchange_active": False}))
        self.assertFalse(ta.is_exchange_closed(None))

    def test_poll_market_marks_exchange_closed_and_watchers_stay_silent(self):
        now = 20000.0
        feed = ready_feed(now)
        calls = []

        def fake_get(url, params=None, timeout=8):
            calls.append(url)
            if url.endswith("/markets"):
                return {"markets": []}
            if url.endswith("/exchange/status"):
                return {"exchange_active": True, "trading_active": False}
            raise AssertionError(url)

        with patch.object(ta, "get_json", side_effect=fake_get), patch.object(ta.time, "time", return_value=now):
            feed.poll_market()
            state = feed.state("1m")
        self.assertTrue(state["exchange_closed"])
        self.assertFalse(state["kalshi_stale"])
        self.assertIsNone(state["window"])
        self.assertEqual(calls, [f"{ta.KALSHI}/markets", f"{ta.KALSHI}/exchange/status"])

        # Within EXCHANGE_STATUS_REFRESH, /exchange/status is not re-queried.
        with patch.object(ta, "get_json", side_effect=fake_get), patch.object(ta.time, "time", return_value=now + 10):
            feed.poll_market()
        self.assertEqual(len(calls), 3)

        # Both watchers treat a scheduled exchange closure as healthy rather than stale Kalshi.
        self.assertIsNone(ta_watch.kalshi_stale_since(state, now - 900, now))
        self.assertEqual(ta_watch.problems(True, {**state, "history_stale": []}, now, {}, now - 900), [])

        class Response(io.BytesIO):
            status = 200

        with patch.object(acer_watch, "urlopen", return_value=Response(json.dumps(state).encode())):
            self.assertIsNone(acer_watch.dashboard_problem())


class ZoneEnhancementTests(unittest.TestCase):
    def test_at_price_exit_distances_prior_role_and_crossings(self):
        # Build 35 closed 1m bars plus 1 forming bar; swing high at bar 5 (price 200),
        # subsequent bars cross 200 twice, then approach 200 from below before current price=200.
        bars = []
        for i in range(36):
            base = 180.0 + i * 0.1
            high, low, close = base + 0.5, base - 0.5, base
            if i == 5:
                high, close = 200.0, 198.0
            elif i == 15:
                high, close = 203.0, 202.5  # crosses above zone mid (201)
            elif i == 16:
                low, close = 194.0, 195.0   # crosses back below zone mid (201)
            bars.append({"time": (i + 1) * 60, "open": close, "high": high, "low": low,
                         "close": close, "volume": 1.0})
        res = sr.levels("1m", {"1m": bars}, {}, price=200.0, atr=20.0, window_open=202.0, now=36 * 60)
        at = res["at_price"]
        self.assertIsNotNone(at)
        self.assertEqual(at["role"], "at price")
        self.assertEqual(at["prior_role"], "resistance")
        self.assertEqual(at["to_lower_usd"], 0.0)
        self.assertEqual(at["to_upper_usd"], 3.0)
        self.assertGreaterEqual(at["crossings"], 2)


class VenueBasisAndThreeMinuteFlowTests(unittest.TestCase):
    def test_venue_basis_against_composite(self):
        now = 1000.0
        venues = {
            "Coinbase": {"price": 64000.0, "at": 999.0, "age_basis": "ticker"},
            "Kraken": {"price": 64010.0, "at": 999.0, "age_basis": "trade"},
            "Bitstamp": {"price": 64020.0, "at": 999.0, "age_basis": "ticker"},
            "Gemini": None,
        }
        comp, details = ta.composite_quote(venues, now)
        self.assertEqual(comp, 64010.0)
        self.assertEqual(details["Coinbase"]["basis_usd"], -10.0)
        self.assertEqual(details["Kraken"]["basis_usd"], 0.0)
        self.assertEqual(details["Bitstamp"]["basis_usd"], 10.0)
        self.assertIsNone(details["Gemini"]["basis_usd"])

    def test_three_minute_rolling_flow_in_snapshot(self):
        flow = sf.OrderFlow()
        open_ts = 90000.0
        # Trade at +30s (buy 2 BTC) and +150s (sell 1 BTC), snapshot at +170s:
        # 1m window ([110, 170]) sees only the sell (1 BTC, imbalance -1.0);
        # 3m window ([0, 170]) sees both (buy 2, sell 1, imbalance +1/3).
        flow.add_trade(sf.Trade("coinbase", "BTC-USD", 64000.0, 2.0, open_ts + 30, "buy", "t1"))
        flow.add_trade(sf.Trade("kraken", "BTC/USD", 64000.0, 1.0, open_ts + 150, "sell", "t2"))
        snap = flow.snapshot(open_ts + 170)
        self.assertAlmostEqual(snap["one_minute"]["imbalance"], -1.0)
        self.assertEqual(snap["three_minute"]["seconds"], 180.0)
        self.assertAlmostEqual(snap["three_minute"]["buy_volume"], 2.0)
        self.assertAlmostEqual(snap["three_minute"]["sell_volume"], 1.0)
        self.assertAlmostEqual(snap["three_minute"]["imbalance"], 1.0 / 3.0)


class MemoryPruningAndTimestampTests(unittest.TestCase):
    def test_fractional_iso_timestamps_parse_across_all_modules(self):
        for stamp in ("2026-10-07T00:00:00.5Z", "2026-10-07T00:00:00.05Z",
                      "2026-10-07T00:00:00.1234Z", "2026-10-07T00:00:00.123456789Z"):
            with self.subTest(stamp=stamp):
                self.assertTrue(report.is_timestamp(stamp))
                expected = report.parse_iso_ts(stamp)
                self.assertAlmostEqual(ta.parse_ts(stamp), expected, places=5)
                self.assertAlmostEqual(log_tools.parse_time(stamp), expected, places=5)
                self.assertAlmostEqual(sf._epoch(stamp), expected, places=5)

    def test_tally_logger_prunes_settled_memory_without_changing_record_or_proxy(self):
        rows = []
        base = 90000
        for i in range(260):
            ticker = f"W{i:04d}"
            close_ts = base + (i + 1) * 900
            lean = "bullish" if i % 2 == 0 else "bearish"
            res = "yes" if i % 3 != 0 else "no"
            err = 50.0 if i < 40 else 2.0
            rows.append({"type": "open", "ticker": ticker, "open_time": ta.iso(close_ts - 900),
                         "close_time": ta.iso(close_ts), "timeframes": {"1m": {"lean": lean}}})
            rows.append({"type": "close", "ticker": ticker, "close_time": ta.iso(close_ts),
                         "proxy_avg_60s": 100.0 + err, "samples": 60})
            rows.append({"type": "settled", "ticker": ticker, "result": res, "expiration_value": "100.0"})
        # Add one unsettled window at the end.
        rows.append({"type": "open", "ticker": "W_OPEN", "open_time": ta.iso(base + 261 * 900),
                     "close_time": ta.iso(base + 262 * 900), "timeframes": {"1m": {"lean": "bullish"}}})
        rows.append({"type": "close", "ticker": "W_OPEN", "close_time": ta.iso(base + 262 * 900),
                     "proxy_avg_60s": 101.0, "samples": 60})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tally_log.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))

            unpruned = ta.TallyLogger(Path(directory), ta.Feed())
            with patch.object(ta, "MAX_MEMORY_WINDOWS", 10000):
                unpruned.prepare()

            pruned = ta.TallyLogger(Path(directory), ta.Feed())
            with patch.object(ta, "MAX_MEMORY_WINDOWS", 200), patch.object(ta, "PROXY_RECORD_WINDOWS", 200):
                pruned.prepare()
                pruned.refresh_record()
                pruned.refresh_proxy()

            self.assertLessEqual(len(pruned.opened), 201)
            self.assertLessEqual(len(pruned.closes), 201)
            self.assertIn("W_OPEN", pruned.opened)
            self.assertEqual(pruned.record, unpruned.record)
            self.assertEqual(pruned.proxy, unpruned.proxy)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FrontendImprovementRenderingTests(unittest.TestCase):
    def test_ui_renders_lockin_depth_closure_basis_three_minute_and_zones(self):
        script = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
(async () => {
  vm.createContext(context);
  vm.runInContext(code, context);
  await flush();
  const s = state('1m', 64000, 0, ['1m']);
  s.venues = {
    Coinbase: {price: 64005, age: 0.4, age_basis: 'ticker', included: true, basis_usd: 5},
    Kraken: {price: 63995, age: 0.6, age_basis: 'trade', included: true, basis_usd: -5},
  };
  s.order_flow = {
    cvd: 1.25,
    one_minute: {buy_volume: 0.8, sell_volume: 0.4, imbalance: 0.333},
    three_minute: {seconds: 180, coverage_seconds: 42.9, buy_volume: 2.5, sell_volume: 1.5, imbalance: 0.25},
    config: {large_trade_usd: 25000},
  };
  s.frames['1m'].levels = {
    price_basis: 'composite',
    support: [],
    resistance: [],
    at_price: {
      role: 'at price', prior_role: 'support', low: 63990, high: 64010, mid: 64000,
      to_lower_usd: 10, to_upper_usd: 10, distance_usd: 0, distance_atr: 0,
      touches: 3, crossings: 2, timeframes: ['1m', '5m'], sources: ['swing'], tags: [], strength: 4,
    },
  };
  s.window = {
    ticker: 'KXBTC15M-TEST', open_time: new Date(T0 - 870000).toISOString(),
    close_time: new Date(T0 + 30000).toISOString(), age: 1, strike: 64000,
    distance_source: 'projected 60s average', distance: 10, distance_sigmas: 1.5, distance_sd_usd: 6,
    observed_seconds: 30, missing_seconds: 0, required_remaining_seconds: 30, required_remaining_avg: 63980, required_remaining_move: -20,
    orderbook_age: 1.0,
    orderbook: {
      yes_bid: {price: '0.55', count: '10'}, yes_ask: {price: '0.57', count: '15'},
      no_bid: {price: '0.43', count: '15'}, no_ask: {price: '0.45', count: '10'},
      yes_bids: [{price: '0.55', count: '10'}, {price: '0.54', count: '25'}],
      yes_asks: [{price: '0.57', count: '15'}, {price: '0.58', count: '30'}],
      no_bids: [{price: '0.43', count: '15'}, {price: '0.42', count: '30'}],
      no_asks: [{price: '0.45', count: '10'}, {price: '0.46', count: '25'}],
    },
    display_quotes: {}, price_path: [], yes_mid_path: [], recent_trades: [],
  };
  streams[0].emit('state', s);
  out.venues = el('venues').innerHTML;
  out.coverage = el('settlementCoverage').textContent;
  out.yesDepth = el('yesDepth').textContent;
  out.flowThree = [el('flowThree').textContent, el('flowThreeImbalance').textContent];
  out.zones = el('zones').innerHTML;

  // Exchange closed state
  const closed = state('1m', 64000, 1000, ['1m']);
  closed.window = null;
  closed.exchange_closed = true;
  closed.kalshi_stale = false;
  streams[0].emit('state', closed);
  out.closedLive = [el('live').className, el('liveText').textContent];
  out.closedCountdown = el('countdown').textContent;
  out.closedAge = el('kalshiAge').textContent;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
        done = subprocess.run(["node", "-"], input=script, text=True, capture_output=True,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")}, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertIn("(+$5.00)", out["venues"])
        self.assertIn("(−$5.00)", out["venues"])
        self.assertIn("Proxy: the remaining 30 s need to average $63,980.00 (−$20.00 vs composite) to land on the target; missed seconds are filled with the composite.", out["coverage"])
        self.assertIn("55.00¢ · 10 contracts (2 lvl: 35)", out["yesDepth"])
        self.assertEqual(out["flowThree"], ["2.5000 / 1.5000 BTC", "25.0% (first 42 s of window)"])
        self.assertIn("at price (support)", out["zones"])
        self.assertIn("−$10.00 / +$10.00", out["zones"])
        self.assertIn("3 (2× crossed)", out["zones"])
        self.assertEqual(out["closedLive"], ["live warn", "Live · Kalshi exchange closed"])
        self.assertEqual(out["closedCountdown"], "Exchange closed")
        self.assertIn("scheduled maintenance", out["closedAge"])


class ReviewRegressionTests(unittest.TestCase):
    def test_r1_missing_and_in_progress_seconds(self):
        samples = [{"time": t, "value": 110.0} for t in range(940, 960)]
        samples.append({"time": 970, "value": 999.0})
        row = ta.window_distance(100, 104, 29.5, 0.01, 1000, samples)
        self.assertEqual(row["required_remaining_seconds"], 30)
        self.assertAlmostEqual(row["required_remaining_avg"], (6000 - 20 * 110 - 10 * 104) / 30)
        self.assertAlmostEqual(row["required_remaining_move"], row["required_remaining_avg"] - 104)
        self.assertEqual(row["observed_seconds"], 21)
        self.assertEqual(row["missing_seconds"], 10)
        reference = (20 * 110 + 999 + 39 * 104) / 60
        self.assertAlmostEqual(row["distance"], reference - 100)
        self.assertAlmostEqual(row["sigma_1m_usd"], reference * 0.01)
        self.assertAlmostEqual(row["distance_sd_usd"], reference * 0.01 * ((29.5 / 60) ** 3 / 3) ** 0.5)

    def test_r1_cutoff_and_missing_inputs(self):
        for left in (60, 5, 4.5):
            row = ta.window_distance(100, 104, left, None, 1000, [])
            remaining = 60 - int(60 - left)
            self.assertEqual(row["required_remaining_seconds"], remaining)
            self.assertAlmostEqual(row["required_remaining_avg"], (6000 - (60 - remaining) * 104) / remaining)
        for strike, composite, left in ((100, 104, 4), (100, 104, 0), (100, 104, 61),
                                        (None, 104, 30), (100, None, 30)):
            row = ta.window_distance(strike, composite, left, None, 1000, [])
            for key in ("required_remaining_avg", "required_remaining_move", "required_remaining_seconds"):
                self.assertIsNone(row[key])

    def test_r2_coverage_boundaries(self):
        self.assertEqual(sf.OrderFlow().snapshot(90000, advance=False)["three_minute"]["coverage_seconds"], 0)
        flow = sf.OrderFlow()
        flow.advance(90000)
        for coverage, now, expected in ((None, 90042, 0), (90030, 90042, 12),
                                         (89900, 90042, 42), (90050, 90042, 0),
                                         (None, 90200, 0)):
            flow.coverage_start = coverage
            self.assertEqual(flow.snapshot(now, advance=False)["three_minute"]["coverage_seconds"], expected)
        flow.advance(90900)
        self.assertEqual(flow.snapshot(90900)["three_minute"]["coverage_seconds"], 0)

    def test_r2_no_trades_means_zero_coverage(self):
        flow = sf.OrderFlow()
        self.assertEqual(flow.snapshot(90042, advance=False)["three_minute"]["coverage_seconds"], 0.0)
        flow.advance(90000)
        for now in (90042, 90200):
            self.assertIsNone(flow.coverage_start)
            self.assertEqual(flow.snapshot(now, advance=False)["three_minute"]["coverage_seconds"], 0.0)

    def test_r3_failed_probe_is_cached_and_clears_closed(self):
        feed = ready_feed(20000)
        probes = []
        def get(url, params=None, timeout=8):
            if url.endswith("/markets"):
                return {"markets": []}
            self.assertTrue(url.endswith("/exchange/status"))
            probes.append(url)
            if len(probes) == 1:
                return {"exchange_active": False, "trading_active": False}
            raise OSError("status unavailable")
        with patch.object(ta, "get_json", side_effect=get):
            for now, closed, count in ((20000, True, 1), (20029, True, 1),
                                       (20030, False, 2), (20031, False, 2),
                                       (20059, False, 2), (20060, False, 3)):
                with patch.object(ta.time, "time", return_value=now):
                    feed.poll_market()
                self.assertEqual(feed.exchange_closed, closed)
                self.assertEqual(feed.exchange_closed_probe, closed)
                self.assertEqual(len(probes), count)
        self.assertEqual(feed.exchange_status_at, 20060)

    def test_r4_primary_exact_head_and_unused_zone_field_removed(self):
        head = subprocess.check_output(["git", "show", "HEAD:ta_copilot/tally_report.py"], text=True)
        current = (HERE / "tally_report.py").read_text()
        def primary(text):
            return text[text.index("def primary_result("):text.index("def primary_section(")]
        self.assertEqual(primary(current), primary(head))
        self.assertNotIn('"age_minutes"', (HERE / "sr_levels.py").read_text())


if __name__ == "__main__":
    unittest.main()
