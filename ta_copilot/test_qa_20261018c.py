"""QA 2026-10-18 batch C: H04 (badge after a window closes) and H05 (window
path tails collapsing to the tail)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from test_fix_20261007b import APP_HARNESS

HERE = Path(__file__).resolve().parent

# The batch B harness (fake DOM, chart library, clock, transport) with a
# controllable calendar as well: app.js reads Date.now() for the countdown and
# performance.now() for freshness, so both have to move with advance().
PAGE_SCENARIOS = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
const RealDate = Date;
class FakeDate extends RealDate {
  constructor(...args) { if (args.length) super(...args); else super(T0 + now); }
  static now() { return T0 + now; }
}
context.Date = FakeDate;
// app.js parses each stream event out of its own JSON copy, so keep the objects
// it really receives: expand() merges into those, and the page renders them.
const seen = [];
const realJSON = JSON;
context.JSON = {parse: text => { const s = realJSON.parse(text); seen.push(s); return s; },
  stringify: realJSON.stringify};
const live = () => [el('live').className, el('liveText').textContent];
const T = Math.floor(T0 / 1000);
const iso = ms => new RealDate(T0 + ms).toISOString();
const path = (n, from) => Array.from({length: n}, (_, i) => ({time: from + i, value: 100 + i / 10}));
function win(closeMs, extra = {}) {
  return {ticker: 'KXBTC-26OCT0720-B100', open_time: iso(-900000), close_time: iso(closeMs), age: 1, strike: 100,
    distance: 0.5, distance_source: 'composite', distance_sigmas: 0.2, distance_sd_usd: 2.5, observed_seconds: 30,
    missing_seconds: 0, orderbook_age: 1, orderbook: {}, display_quotes: {}, high_since_open: 101, low_since_open: 99,
    path_partial: false, recent_trades: [], price_path: [], yes_mid_path: [], ...extra};
}
const withWindow = (tf, ms, w, frames = [tf], price = 100) => ({...state(tf, price, ms, frames), window: w});
// L commands in the window spark: one path point each, after the leading M.
const drawn = () => el('windowPath').innerHTML.split('L').length - 1;
(async () => {
  vm.createContext(context);
  vm.runInContext(code, context);
  await flush();
  // H04: the open window's close_time passes under a live page.
  streams[0].emit('state', withWindow('1m', 0, win(1000)));
  out.h04_before = live();
  await advance(1000);                                  // the countdown reaches zero
  out.h04_expired = {live: live(), countdown: el('countdown').textContent};
  await advance(250);                                   // a tick with no new snapshot
  out.h04_next_tick = live();
  await advance(250);
  out.h04_later = live();
  await advance(11000);                                 // past FRESH_LIMIT_MS: still silent
  out.h04_no_update = live();
  // H05a: the selected timeframe has no frame chart at all, so only the window
  // paths carry data. The tail must extend the full path, not replace it.
  buttons[1].onclick();
  await flush();
  const full = path(40, T), tail = full.slice(-5);
  streams.at(-1).emit('state', withWindow('5m', 2000, win(900000, {price_path: full, yes_mid_path: full}), ['1m']));
  out.h05a_full = [seen.at(-1).window.price_path.length, drawn()];
  streams.at(-1).emit('state',
    withWindow('5m', 2250, win(900000, {price_path: tail, yes_mid_path: tail, paths_tail: true}), ['1m']));
  const merged = seen.at(-1).window;
  out.h05a_tail = [merged.price_path.length, merged.yes_mid_path.length,
    merged.price_path[0].time === full[0].time, merged.price_path.at(-1).time === tail.at(-1).time, drawn()];
  // H05b: a paths_tail with no cached full paths asks for a resync.
  buttons[2].onclick();
  await flush();
  const orphan = withWindow('10m', 3000,
    win(900000, {price_path: tail, yes_mid_path: tail, paths_tail: true}), [], 777);
  out.h05b_expand = context.expand(orphan);
  out.h05b_streams = streams.length;
  streams.at(-1).emit('state', orphan);
  out.h05b_resync = streams.length;
  out.h05b_path = seen.at(-1).window.price_path.length;   // untouched: never accepted
  out.h05b_price = el('price').textContent;
  // H05c: with a frame chart present, full then tail merge as before.
  buttons[0].onclick();
  await flush();
  const candle = (i, close) => ({time: T + i * 60, open: 100, high: 101, low: 99, close: close ?? 100 + i, volume: 1});
  const base = state('1m', 100, 5000);
  base.frames['1m'].chart.candles = [candle(0), candle(1), candle(2)];
  streams.at(-1).emit('state', base);
  const more = state('1m', 101, 5250);
  more.frames['1m'].chart.candles = [candle(1, 999), candle(3)];
  more.frames['1m'].chart_tail = true;
  streams.at(-1).emit('state', more);
  const candles = seen.at(-1).frames['1m'].chart.candles;
  out.h05c = [candles.length, candles.map(c => c.close)];
  out.h05c_price = el('price').textContent;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run(["node", "-"], input=PAGE_SCENARIOS, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")})
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout)

    def test_h04_badge_stays_kalshi_stale_after_the_window_closes(self):
        self.assertEqual(self.out["h04_before"], ["live ok", "Live · spot 0.5s"])
        self.assertEqual(self.out["h04_expired"], {"live": ["live warn", "Live · Kalshi stale"],
                                                   "countdown": "No open window"})
        # The next tick re-reads the snapshot for its freshness; it must not
        # resurrect the pre-close "Live · spot" badge.
        for name in ("h04_next_tick", "h04_later"):
            with self.subTest(tick=name):
                css, text = self.out[name]
                self.assertEqual(css, "live warn")
                self.assertEqual(text, "Live · Kalshi stale")
                self.assertNotIn("spot", text)

    def test_h04_carrying_the_close_keeps_the_no_update_path(self):
        css, text = self.out["h04_no_update"]
        self.assertEqual(css, "live bad")
        self.assertRegex(text, r"^No update for \d+ s$")

    def test_h05a_window_path_without_a_frame_keeps_the_whole_path(self):
        self.assertEqual(self.out["h05a_full"], [40, 39])
        points, mids, first, last, lines = self.out["h05a_tail"]
        self.assertEqual((points, mids), (40, 40))     # not the 5-point tail
        self.assertTrue(first)                         # the path still starts at the open
        self.assertTrue(last)
        self.assertGreaterEqual(lines, 39)             # and the spark draws all of it

    def test_h05b_tail_without_cached_paths_resyncs(self):
        self.assertFalse(self.out["h05b_expand"])
        self.assertEqual(self.out["h05b_resync"], self.out["h05b_streams"] + 1)
        self.assertEqual(self.out["h05b_path"], 5)             # never merged: a resync instead
        self.assertEqual(self.out["h05b_price"], "$100.00")   # the orphan never rendered

    def test_h05c_frame_chart_full_then_tail_still_merges(self):
        self.assertEqual(self.out["h05c"], [4, [100, 999, 102, 103]])
        self.assertEqual(self.out["h05c_price"], "$101.00")


if __name__ == "__main__":
    unittest.main()
