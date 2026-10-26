"""Offline regressions for tape freshness, tied timestamps and proxy slope."""
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import unittest
from unittest.mock import patch

import ta_copilot as ta
from test_fix_20261002 import ready_feed
from test_fix_20261007b import APP_HARNESS

HERE = Path(__file__).resolve().parent


def trade(trade_id, at):
    return {"ticker": "T", "created_time": ta.iso(at), "yes_price_dollars": "0.5",
            "count_fp": "1", "taker_side": "yes", "trade_id": trade_id}


def rows(raw):
    return ta.kalshi_trade_rows({"trades": raw}, "T", 1000, 1900)


def market_feed(now):
    feed = ready_feed(now)
    feed.market = {"ticker": "T", "open_time": ta.iso(1000),
                   "close_time": ta.iso(1900), "floor_strike": 100}
    feed.market_at = now
    return feed


def held_feed(now=1120):
    feed = market_feed(now)
    feed.context_ticker = "T"
    feed.kalshi_tape = ta.merge_tape(None, rows([trade("held", 1090)]), False, 1000, 1100)
    return feed


def summary(feed, now):
    with patch.object(ta.time, "time", return_value=now):
        return feed.state("1m")["window"]["tape_summary"]


class TapeTests(unittest.TestCase):
    def test_failed_fetches_expire_and_success_recovers(self):
        feed = market_feed(1100)
        raw = [trade("held", 1090)]
        failed = False
        def get(url, params=None, timeout=8):
            if url.endswith("/markets"):
                return {"markets": [feed.market]}
            if url.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
            if failed:
                raise OSError("trades unavailable")
            return {"trades": raw}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=1100):
            feed.poll_market_context()
        self.assertTrue(summary(feed, 1100)["complete_window"])
        failed = True
        snapshots = []
        # Listing and book stay fresh while only trades fail; 10 s is inclusive.
        for now in (1110, 1111, 1171):
            with patch.object(ta, "get_json", side_effect=get), \
                    patch.object(ta.time, "time", return_value=now):
                feed.poll_market()
                with self.assertRaisesRegex(OSError, "trades unavailable"):
                    feed.poll_market_context()
                state = feed.state("1m")
                self.assertFalse(state["kalshi_stale"])
                tape = state["window"]["tape_summary"]
                self.assertEqual(tape["complete_60s"], now == 1110)
                self.assertEqual(tape["complete_window"], now == 1110)
                snapshots.append((now, tape))
                if now == 1171:
                    self.assertEqual(tape["last_60s"]["trade_count"], 0)
        for now, tape in snapshots:
            self.assertEqual(tape["observed_to"], ta.iso(1100))
            self.assertEqual(tape["age_seconds"], now - 1100)
            self.assertEqual(tape["fresh"], now == 1110)
        failed = False
        raw.append(trade("new", 1172))
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=1173):
            feed.poll_market()
            feed.poll_market_context()
        tape = summary(feed, 1173)
        self.assertTrue(tape["complete_window"])
        self.assertTrue(tape["complete_60s"])
        self.assertTrue(tape["fresh"])
        self.assertEqual((tape["observed_to"], tape["age_seconds"]), (ta.iso(1173), 0))
        self.assertEqual(tape["last_60s"]["trade_count"], 1)
        self.assertEqual(tape["window"]["trade_count"], 2)

    def test_merge_requires_overlap_or_strictly_older(self):
        held = held_feed().kalshi_tape
        for name, raw, covered in (("tie", [trade("new", 1090)], 1090),
                                   ("overlap", [trade("held", 1090), trade("new", 1100)], 1000),
                                   ("older", [trade("new", 1089)], 1000)):
            with self.subTest(name=name):
                tape = ta.merge_tape(held, rows(raw), True, 1000, 1120)
                self.assertEqual(tape["from"], covered)
                self.assertEqual("held" in tape["rows"], name != "tie")

    def poll_pages(self, feed, pages):
        calls = []
        def get(url, params=None, timeout=8):
            if url.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
            calls.append(dict(params))
            return {"trades": pages[len(calls) - 1], "cursor": str(len(calls))}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=1120):
            feed.poll_market_context()
        for i, params in enumerate(calls):
            expected = {"ticker": "T", "min_ts": 1000, "limit": 100}
            if i:
                expected["cursor"] = str(i)
            self.assertEqual(params, expected)
        return calls

    def test_multi_page_tie_at_cap_is_partial(self):
        feed = held_feed()
        pages = [[trade(f"new-{p}-{i}", 1090) for i in range(100)] for p in range(4)]
        calls = self.poll_pages(feed, pages)
        self.assertEqual(len(calls), 4)
        self.assertEqual(ta.KALSHI_TRADE_PAGES, 4)
        self.assertEqual(feed.kalshi_tape["from"], 1090)
        self.assertEqual(len(feed.kalshi_tape["rows"]), 400)
        self.assertNotIn("held", feed.kalshi_tape["rows"])
        tape = summary(feed, 1120)
        self.assertFalse(tape["complete_window"])
        self.assertFalse(tape["complete_60s"])

    def test_tie_pages_stop_at_id_overlap(self):
        feed = held_feed()
        pages = [[trade(f"new-{p}-{i}", 1090) for i in range(100)] for p in range(2)]
        pages[1][-1] = trade("held", 1090)
        calls = self.poll_pages(feed, pages)
        self.assertEqual(len(calls), 2)
        self.assertEqual(feed.kalshi_tape["from"], 1000)
        self.assertEqual(len(feed.kalshi_tape["rows"]), 200)
        self.assertTrue(summary(feed, 1120)["complete_window"])
        self.assertTrue(summary(feed, 1120)["complete_60s"])

    def test_tie_pages_continue_to_strictly_older(self):
        feed = held_feed()
        pages = [[trade(f"new-{p}-{i}", 1090 - p) for i in range(100)] for p in range(2)]
        calls = self.poll_pages(feed, pages)
        self.assertEqual(len(calls), 2)
        self.assertEqual(feed.kalshi_tape["from"], 1000)
        self.assertIn("held", feed.kalshi_tape["rows"])
        self.assertTrue(summary(feed, 1120)["complete_window"])
        self.assertTrue(summary(feed, 1120)["complete_60s"])


class DriftTests(unittest.TestCase):
    def test_straight_line_samples_average_to_required(self):
        samples = [{"time": t, "value": 100} for t in range(940, 995)]
        result = ta.window_distance(101, 100, 5, .01, 1000, samples)
        self.assertEqual(result["required_remaining_avg"], 112)
        slope = result["required_drift_per_sec"]
        path = [100 + slope * k for k in range(1, 6)]
        self.assertAlmostEqual(statistics.mean(path), result["required_remaining_avg"])
        self.assertEqual(slope, 4)
        self.assertAlmostEqual((55 * 100 + sum(path)) / 60, 101)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
(async () => {
  vm.createContext(context); vm.runInContext(code, context); await flush();
  const s = state('1m', 100, 0);
  s.window = {ticker: 'T', open_time: new Date(T0 - 120000).toISOString(),
    close_time: new Date(T0 + 780000).toISOString(), age: 0, strike: 100,
    required_drift_per_sec: 4, price_path: [], yes_mid_path: [], recent_trades: [],
    tape_summary: {last_60s: {contracts: 0, trade_count: 0}, window: {contracts: 1, trade_count: 1},
      covered_from: new Date(T0 - 120000).toISOString(), observed_to: new Date(T0 - 71000).toISOString(),
      age_seconds: 71, fresh: false, complete_window: false, complete_60s: false}};
  streams[0].emit('state', s);
  out.stale = el('tapeSummary').textContent;
  out.drift = el('requiredDrift').textContent;
  Object.assign(s.window.tape_summary, {fresh: true, age_seconds: 0,
    observed_to: new Date(T0).toISOString(), complete_window: true, complete_60s: true});
  streams[0].emit('state', s);
  out.recovered = el('tapeSummary').textContent;
  Object.assign(s.window.tape_summary, {complete_window: false, complete_60s: false});
  streams[0].emit('state', s);
  out.partial = el('tapeSummary').textContent;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
        proc = subprocess.run(["node", "-"], input=script, capture_output=True, text=True,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")}, timeout=30, check=True)
        cls.out = json.loads(proc.stdout)

    def test_stale_tape_label_and_recovery(self):
        self.assertEqual(self.out["stale"].count("(stale)"), 2)
        self.assertNotIn("(stale)", self.out["recovered"])
        self.assertNotIn("(partial)", self.out["recovered"])
        self.assertIn("(partial)", self.out["partial"])
        self.assertNotIn("(stale)", self.out["partial"])

    def test_drift_label_states_slope_assumption(self):
        self.assertIn("constant slope", self.out["drift"])
        self.assertIn("from the current composite", self.out["drift"])
        self.assertIn("one sample per remaining second", self.out["drift"])
