"""Adversarial QA 2026-10-07, batch B: Q01, Q02, Q07, Q08, Q09, Q10, Q13, Q16."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import log_tools
import ta_copilot as ta
import ta_watch
import tally_report as report
from test_fix_20261002 import ready_feed

HERE = Path(__file__).resolve().parent

# Runs the real app.js with a fake DOM, chart library, clock and transport,
# and prints what the page showed at each step of the QA scenarios.
APP_HARNESS = r"""
// Runs the real app.js with a fake DOM, chart library, clock and transport.
const vm = require('vm'), fs = require('fs');
const code = fs.readFileSync(process.env.APP_JS, 'utf8');
let now = 0;
const timers = new Map(); let timerId = 0;
const addTimer = (fn, ms, every) => { const id = ++timerId; timers.set(id, {fn, at: now + ms, every}); return id; };
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    const [id, t] = due; now = t.at;
    if (t.every) t.at += t.every; else timers.delete(id);
    t.fn(); await flush();
  }
  now = end;
}
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };
const elements = {};
const el = id => elements[id] || (elements[id] = {id, textContent: '', innerHTML: '', className: '', style: {}, dataset: {},
  setAttribute() {}, classList: {toggle() {}}});
const buttons = ['1m', '5m', '10m', '15m', '1h'].map(tf => ({...el('tab' + tf), dataset: {tf}, classList: {toggle() {}}}));
const stub = new Proxy(function () {}, {get: (t, k) => k === 'getVisibleRange' ? () => null : stub, apply: () => stub});
const streams = [], fetches = [];
class EventSource {
  constructor(url) { this.url = url; this.handlers = {}; this.closed = false; streams.push(this); }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  close() { this.closed = true; }
  emit(type, data) { this.handlers[type]({data: JSON.stringify(data)}); }
}
function fetch(url, opts) {
  return new Promise((resolve, reject) => {
    const call = {url, respond: s => resolve({json: async () => s}), aborted: false};
    opts?.signal?.addEventListener('abort', () => { call.aborted = true; reject(new Error('AbortError')); });
    fetches.push(call);
  });
}
const context = {
  document: {getElementById: el, querySelectorAll: () => buttons, documentElement: {}},
  getComputedStyle: () => ({getPropertyValue: () => '#123456'}),
  LightweightCharts: {createChart: () => stub}, localStorage: {getItem: () => null, setItem() {}},
  EventSource, fetch, AbortController, performance: {now: () => now}, Date, JSON, Math, Number, String, Object, Array,
  Map, Promise, console,
  setInterval: (fn, ms) => addTimer(fn, ms, ms), setTimeout: (fn, ms) => addTimer(fn, ms, 0),
  clearInterval: id => timers.delete(id), clearTimeout: id => timers.delete(id),
};
context.window = context;
const T0 = Date.parse('2026-10-07T20:00:00Z');
function frame() {
  const chart = Object.fromEntries(['candles', 'volume', 'ema9', 'ema21', 'ema50', 'bb_upper', 'bb_lower', 'vwap', 'rsi',
    'macd', 'macd_signal', 'macd_hist'].map(k => [k, []]));
  return {chart, values: {}, readings: [{name: 'EMA stack', detail: 'up', state: 'bull', family: 'trend'}],
    tally: {bull: 1, bear: 0, neutral: 0, lean: 'bullish'}, family: {votes: {trend: 'bull'}, net: 1, lean: 'bullish'},
    history: {stale: false, gap: false, lag_seconds: 0}};
}
function state(tf, price, ms, frames = [tf]) {
  return {ready: true, server_time: new Date(T0 + ms).toISOString(), timeframe: tf,
    spot: {price, bid: price, ask: price, time: new Date(T0 + ms).toISOString()}, spot_age: 0.5,
    stale: false, kalshi_stale: false, history_stale: [], composite: price, average_60s: price, venues: {},
    feed_modes: {}, order_flow: {}, window: null, errors: {}, frames: Object.fromEntries(frames.map(k => [k, frame()]))};
}
const out = {};
const live = () => [el('live').className, el('liveText').textContent];
(async () => {
  vm.createContext(context);
  vm.runInContext(code, context);
  await flush();
  // Q01: one event, then silence with the stream left open.
  streams[0].emit('state', state('1m', 100, 0));
  out.q01_start = live();
  await advance(12000);
  out.q01_silent = live();
  out.q01_recovery = {stream_closed: streams[0].closed, polls: fetches.length};
  fetches[0].respond(state('1m', 101, 12000));
  await flush();
  out.q01_after_poll = [live(), el('price').textContent];
  await advance(5000);                            // reconnect timer fires
  streams.at(-1).emit('state', state('1m', 102, 17000));
  out.q01_stream_back = [live(), el('price').textContent, streams.length];
  // Q07a: the stream errors; fallback polls run; the reconnected stream
  // delivers newer data while a poll started earlier is still in flight.
  streams.at(-1).onerror();
  fetches.at(-1).respond(state('1m', 103, 17500)); await flush();
  await advance(2000); fetches.at(-1).respond(state('1m', 104, 19500)); await flush();
  await advance(2000); const slow = fetches.at(-1);          // in flight from about 21.5 s
  await advance(1000);                                        // reconnect timer
  streams.at(-1).emit('state', state('1m', 200, 22600));
  slow.respond(state('1m', 150, 21600)); await flush();
  out.q07_late_poll = el('price').textContent;
  // Q07b: a 1m poll in flight while the user switches to 5m.
  streams.at(-1).onerror();
  const pending = fetches.at(-1);
  buttons[1].onclick();
  out.q07_aborted = pending.aborted;
  pending.respond(state('1m', 300, 30000)); await flush();
  out.q07_after_switch = el('price').textContent;
  await advance(2000);
  out.q07_new_poll_tf = fetches.at(-1).url;
  // Q08: 5m selected, server has only 1m history; shared fields must update.
  streams.at(-1).emit('state', state('5m', 999, 52000, ['1m'])); await flush();
  out.q08 = [el('price').textContent, el('frameNote').textContent, live()];
  streams.at(-1).emit('state', state('5m', 1000, 53000, ['1m', '5m']));
  out.q08_back = [el('price').textContent, el('frameNote').textContent];
  // Server serialization problem event.
  streams.at(-1).emit('problem', {error: 'state serialization'});
  out.problem = live();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run(["node", "-"], input=APP_HARNESS, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")})
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout)

    def test_q01_silent_stream_leaves_live_polls_and_recovers(self):
        self.assertEqual(self.out["q01_start"][0], "live ok")
        self.assertEqual(self.out["q01_silent"], ["live bad", "No update for 12 s"])
        self.assertEqual(self.out["q01_recovery"], {"stream_closed": True, "polls": 1})
        self.assertEqual(self.out["q01_after_poll"], [["live ok", "Live · spot 0.5s"], "$101.00"])
        self.assertEqual(self.out["q01_stream_back"][1:], ["$102.00", 2])

    def test_q07_late_or_superseded_polls_never_render(self):
        self.assertEqual(self.out["q07_late_poll"], "$200.00")
        self.assertTrue(self.out["q07_aborted"])
        self.assertEqual(self.out["q07_after_switch"], "$200.00")
        self.assertEqual(self.out["q07_new_poll_tf"], "/api/state?tf=5m")

    def test_q08_missing_frame_still_updates_shared_fields(self):
        price, note, live = self.out["q08"]
        self.assertEqual(price, "$999.00")
        self.assertIn("5m history unavailable", note)
        self.assertEqual(self.out["q08_back"], ["$1,000.00", ""])

    def test_server_problem_event_shows(self):
        self.assertEqual(self.out["problem"], ["live bad", "Server state error"])


def candle_rows(start, count, sec, close=100.0):
    return {"candles": [{"start": str(start + i * sec), "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1} for i in range(count)][::-1]}


class CandleQualityTests(unittest.TestCase):
    def poll(self, feed, tf, payload):
        with patch.object(ta, "get_json", return_value=payload):
            feed.poll_candles(tf)

    def test_q10_invalid_candles_are_rejected_atomically(self):
        now = int(time.time() // 60) * 60
        feed = ta.Feed()
        self.poll(feed, "1m", candle_rows(now - 69 * 60, 70, 60))
        good = [dict(b) for b in feed.bars["1m"]]
        for name, change in (("zero close", {"close": 0}), ("inverted", {"high": 90, "low": 110}),
                             ("negative volume", {"volume": -1}), ("nonfinite", {"open": "nan"})):
            with self.subTest(name):
                bad = candle_rows(now - 69 * 60, 70, 60)
                bad["candles"][5].update(change)
                with self.assertRaises(ValueError):
                    self.poll(feed, "1m", bad)
                self.assertEqual(feed.bars["1m"], good)
        state = ready_state(feed)
        self.assertTrue(state["ready"])

    def test_q02_old_history_is_stale_and_ticker_cannot_bridge_the_gap(self):
        now = time.time()
        start = int(now // 60) * 60 - 3660 - 69 * 60
        feed = ta.Feed()
        self.poll(feed, "1m", candle_rows(start, 70, 60))
        before = len(feed.bars["1m"])
        feed.update_spot(100.0, 99.0, 101.0, ta.iso(now), {"price": 100.0, "at": now, "age_basis": "ticker"}, now)
        self.assertEqual(len(feed.bars["1m"]), before)  # no bar opened across the gap
        state = ready_state(feed)
        self.assertIn("1m", state["history_stale"])
        self.assertTrue(state["history"]["1m"]["stale"])
        self.assertGreater(state["history"]["1m"]["lag_seconds"], 3600)
        self.assertTrue(state["frames"]["1m"]["history"]["stale"])
        self.assertTrue(any(p.startswith("candle history stale: 1m") for p in ta_watch.problems(True, state, now)))

    def test_q02_obsolete_response_is_rejected(self):
        now = int(time.time() // 60) * 60
        feed = ta.Feed()
        self.poll(feed, "1m", candle_rows(now - 69 * 60, 70, 60))
        with self.assertRaisesRegex(ValueError, "older than held history"):
            self.poll(feed, "1m", candle_rows(now - 69 * 60 - 3600, 70, 60))
        self.assertEqual(feed.fetched_last["1m"], now)

    def test_fresh_history_and_gap_flag(self):
        now = time.time()
        bars = [{"time": int(now // 60) * 60 - (59 - i) * 60} for i in range(60)]
        self.assertEqual(ta.history_quality(bars, 60, bars[-1]["time"], now),
                         {"lag_seconds": 0.0, "stale": False, "gap": False})
        del bars[30]
        self.assertTrue(ta.history_quality(bars, 60, bars[-1]["time"], now)["gap"])
        self.assertTrue(ta.history_quality(bars, 60, None, now)["stale"])

    def test_q08_state_lists_unavailable_frames(self):
        feed = ready_feed(time.time())
        state = feed.state("5m")
        self.assertTrue(state["ready"])
        self.assertIn("5m", state["frames_unavailable"])
        self.assertNotIn("1m", state["frames_unavailable"])


def ready_state(feed):
    """State with the spot/composite parts of a ready feed, keeping feed's bars."""
    now = time.time()
    feed.spot = {"price": 100.0, "bid": 99.0, "ask": 101.0, "time": ta.iso(now)}
    feed.spot_at = now
    feed.venues["Coinbase"] = {"price": 100.0, "at": now, "age_basis": "ticker"}
    feed.composite, feed.composite_at = 100.0, now
    return feed.state("1m")


class SpotFallbackTests(unittest.TestCase):
    def test_q09_spot_fallback_has_its_own_cadence(self):
        import inspect
        self.assertNotIn("poll_spot", inspect.getsource(ta.Feed.run))

        class Stop(BaseException):
            pass

        feed, clock, polls = ta.Feed(), [1000.0], []

        def poll():
            polls.append(clock[0])
            clock[0] += 0.3  # a REST round trip
            if len(polls) == 4:
                raise Stop

        with patch.object(feed, "poll_spot", side_effect=poll), \
             patch.object(ta.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(ta.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)):
            with self.assertRaises(Stop):
                feed.run_spot()
        self.assertEqual([round(t - polls[0], 6) for t in polls], [0, 2, 4, 6])


class KrakenQuoteTests(unittest.TestCase):
    @staticmethod
    def frame(trade_id, price, at):
        return json.dumps({"channel": "trade", "type": "update", "data": [
            {"symbol": "BTC/USD", "trade_id": trade_id, "price": price, "qty": .1, "side": "buy",
             "timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")}]})

    def test_q13_replayed_trade_does_not_regress_the_quote(self):
        now = time.time()
        feed = ta.Feed()
        feed.socket_connected["Kraken"] = True
        feed.handle_kraken_message(self.frame(1, 63000, now - 15))
        feed.handle_kraken_message(self.frame(2, 64000, now - 0.5))
        feed.handle_kraken_message(self.frame(1, 63000, now - 15))
        self.assertEqual(feed.venues["Kraken"]["price"], 64000)
        self.assertAlmostEqual(feed.venues["Kraken"]["at"], now - 0.5, places=2)
        self.assertTrue(feed.stream_active("Kraken"))

    def test_receipt_alone_does_not_keep_kraken_active(self):
        now = time.time()
        feed = ta.Feed()
        feed.socket_connected["Kraken"] = True
        feed.handle_kraken_message(self.frame(1, 63000, now - 15))  # only an old trade arrives
        self.assertFalse(feed.stream_active("Kraken"))

    def test_rest_quote_does_not_lock_out_the_stream(self):
        now = time.time()
        feed = ta.Feed()
        feed.socket_connected["Kraken"] = True
        feed.venues["Kraken"] = {"price": 63500, "at": now, "age_basis": "fetch"}  # REST, fetch time
        feed.handle_kraken_message(self.frame(3, 63400, now - 0.2))
        self.assertEqual(feed.venues["Kraken"]["price"], 63500)
        self.assertTrue(feed.stream_active("Kraken"))


class RecordSchemaTests(unittest.TestCase):
    def write(self, directory, rows):
        path = Path(directory) / "tally_log.jsonl"
        path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
        return path

    def test_q16_bad_schema_rows_are_skipped_in_every_reader(self):
        good = {"type": "open", "ticker": "T2", "target": 100, "captured_at": "2026-10-07T00:00:00Z",
                "open_time": "2026-10-07T00:00:00Z", "timeframes": {"1m": {"lean": "bullish"}}}
        rows = [{"type": "open", "ticker": []}, {"type": "minute", "ticker": "T1", "minute": "3"},
                {"type": "settled", "ticker": "T1", "expiration_value": "NaN"},
                {"type": "minute", "ticker": "T1", "minute": 2, "timeframes": []}, good,
                {"type": "settled", "ticker": "T2", "result": "yes", "expiration_value": "101.5"}]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, rows)
            skipped = []
            opened, settled, minutes = report.load_rows(path, skipped)
            self.assertEqual((list(opened), list(settled), minutes, skipped), (["T2"], ["T2"], {}, [4]))
            self.assertEqual(log_tools.read_rows(path)[1], 4)
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.prepare()
            self.assertEqual((logger.skipped_lines, list(logger.opened), logger.settled), (4, ["T2"], {"T2"}))

    def test_q16_nan_strings_never_become_a_correlation(self):
        rows = []
        for i in range(7):
            rows.append({"type": "open", "ticker": f"T{i}", "target": 100.0, "sigma_1m_usd": 2.0,
                         "timeframes": {"1m": {"bull": i, "bear": 0, "neutral": 0, "lean": "bullish", "net": i}}})
            rows.append({"type": "settled", "ticker": f"T{i}", "result": "yes", "expiration_value": "NaN"})
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, rows)
            skipped = []
            opened, settled, _ = report.load_rows(path, skipped)
            self.assertEqual((len(settled), skipped), (0, [7]))
            self.assertEqual(report.settlement_moves(opened, settled), [])

    def test_validator_accepts_real_row_shapes(self):
        minute = {"type": "minute", "ticker": "T", "minute": 11, "captured_at": "2026-10-07T00:11:00.502458Z", "seconds_left": 239.5,
                  "composite": 84833.08, "average_60s": None, "target": 84862.28, "sigma_1m_usd": 9.8,
                  "yes_bid": {"price": 0.41, "age": 1.2, "source": "book"}, "yes_ask": None,
                  "flow_imbalance_1m": None, "stale": False, "kalshi_stale": False, "history_stale": [],
                  "timeframes": {"1m": {"bull": 3, "bear": 1, "neutral": 4, "lean": "bullish", "family_net": -1},
                                 "5m": {"family_net": None}}}
        opened = {"type": "open", "ticker": "T", "yes_bid": "0.41", "target": None, "composite_spot": 1.5}
        self.assertIsNone(report.record_problem(minute))
        self.assertIsNone(report.record_problem(opened))
        self.assertEqual(report.record_problem({**minute, "minute": True}), "bad minute")
        self.assertEqual(report.record_problem({**minute, "composite": "inf"}), "bad composite")
        self.assertEqual(report.record_problem({**minute, "timeframes": {"1m": {"family_net": 1.5}}}),
                         "bad family_net")
        self.assertEqual(report.record_problem({"type": "scalp"}), "unknown type")


if __name__ == "__main__":
    unittest.main()
