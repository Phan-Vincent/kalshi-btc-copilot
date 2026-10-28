// Runs the real app.js in node:vm with a minimal DOM, a no-op
// LightweightCharts stub, and a scriptable clock and transport, so render
// behaviour can be checked without a browser.
//
// As a module it exposes createHarness (DOM stub + app.js load) and the small
// state builders used by the fixtures. Run directly to drive one scenario:
//   node render_harness.mjs <name>
// which loads render_states/<name>.json and prints a JSON object of what the
// page showed, for test_render_20261027.py to assert on.
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const T0 = Date.parse('2026-10-27T20:00:00Z');

export function extractIds(html) {
  const ids = [];
  const re = /\bid="([^"]+)"/g;
  let m;
  while ((m = re.exec(html))) ids.push(m[1]);
  return ids;
}

export function deepMerge(base, patch) {
  const out = { ...base };
  for (const key in patch) {
    const value = patch[key];
    if (value && typeof value === 'object' && !Array.isArray(value) &&
        base[key] && typeof base[key] === 'object' && !Array.isArray(base[key])) {
      out[key] = deepMerge(base[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

export function frame() {
  const chart = Object.fromEntries(['candles', 'volume', 'ema9', 'ema21', 'ema50', 'bb_upper', 'bb_lower',
    'vwap', 'rsi', 'macd', 'macd_signal', 'macd_hist'].map(k => [k, []]));
  return { chart, values: {}, readings: [{ name: 'EMA stack', detail: 'up', state: 'bull', family: 'trend' }],
    tally: { bull: 1, bear: 0, neutral: 0, lean: 'bullish' },
    family: { votes: { trend: 'bull' }, net: 1, lean: 'bullish', flow_ready: true },
    history: { stale: false, gap: false, lag_seconds: 0 } };
}

export function baseState(tf, price, ms, frames = ['1m']) {
  return { ready: true, server_time: new Date(T0 + ms).toISOString(), timeframe: tf,
    spot: { price, bid: price, ask: price, time: new Date(T0 + ms).toISOString() }, spot_age: 0.5,
    stale: false, kalshi_stale: false, exchange_closed: false, history_stale: [],
    composite: price, average_60s: price, venues: {}, feed_modes: {}, order_flow: {},
    window: null, errors: {}, proxy_record: null, recent_windows: null,
    frames: Object.fromEntries(frames.map(k => [k, frame()])) };
}

export function createHarness({ app, html }) {
  let now = 0;
  const timers = new Map();
  let timerId = 0;
  const addTimer = (fn, ms, every) => { const id = ++timerId; timers.set(id, { fn, at: now + ms, every }); return id; };
  const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, t] = due;
      now = t.at;
      if (t.every) t.at += t.every; else timers.delete(id);
      t.fn();
      await flush();
    }
    now = end;
  }
  const elements = {};
  const makeEl = id => ({ id, textContent: '', innerHTML: '', className: '', style: {}, dataset: {}, open: false,
    setAttribute() {}, addEventListener() {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    querySelectorAll: () => [] });
  for (const id of extractIds(html)) elements[id] = makeEl(id);
  const el = id => elements[id] || (elements[id] = makeEl(id));
  const buttons = ['1m', '5m', '10m', '15m', '1h'].map(tf => ({ ...makeEl('tab' + tf), dataset: { tf } }));
  const stub = new Proxy(function () {}, { get: (t, k) => k === 'getVisibleRange' ? () => null : stub, apply: () => stub });
  const streams = [], fetches = [];
  class EventSource {
    constructor(url) { this.url = url; this.handlers = {}; this.closed = false; streams.push(this); }
    addEventListener(type, fn) { this.handlers[type] = fn; }
    close() { this.closed = true; }
    emit(type, data) { (this.handlers[type] || (() => {}))({ data: JSON.stringify(data) }); }
  }
  function fetch(url, opts) {
    return new Promise((resolve, reject) => {
      const call = { url, aborted: false, respond: s => resolve({ json: async () => s }), reject: e => reject(e) };
      if (opts && opts.signal) opts.signal.addEventListener('abort', () => { call.aborted = true; reject(new Error('AbortError')); });
      fetches.push(call);
    });
  }
  const context = {
    document: { getElementById: el, querySelectorAll: sel => sel === '#tabs button' ? buttons : [], documentElement: {} },
    getComputedStyle: () => ({ getPropertyValue: () => '#123456' }),
    LightweightCharts: { createChart: () => stub },
    localStorage: { getItem: () => null, setItem() {} },
    EventSource, fetch, AbortController, performance: { now: () => now },
    Date, JSON, Math, Number, String, Object, Array, Map, Promise, console, encodeURIComponent,
    setInterval: (fn, ms) => addTimer(fn, ms, ms), setTimeout: (fn, ms) => addTimer(fn, ms, 0),
    clearInterval: id => timers.delete(id), clearTimeout: id => timers.delete(id),
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(app, context);
  return { context, vm, el, elements, buttons, streams, fetches, advance, flush, now: () => now };
}

const live = h => [h.el('live').className, h.el('liveText').textContent];

async function runConflict(fixture, h) {
  const s = deepMerge(baseState('1m', 64000, 0), fixture);
  h.streams[0].emit('state', s);
  await h.flush();
  return { lean: h.el('lean').innerHTML, legacyCount: h.el('nBull').textContent, families: h.el('families').innerHTML };
}

async function runStaleBrti(fixture, h) {
  const s = deepMerge(baseState('1m', 64000, 0), fixture);
  h.streams[0].emit('state', s);
  await h.flush();
  return { brtiStatus: h.el('brtiStatus').textContent, distanceLabel: h.el('distanceLabel').textContent,
    brtiValue: h.el('brtiValue').textContent };
}

async function runRollover(fixture, h) {
  const a = deepMerge(baseState('1m', 64000, 0), fixture.a);
  const b = deepMerge(baseState('1m', 64100, 20000), fixture.b);
  h.streams[0].emit('state', a);
  await h.flush();
  await h.advance(300);
  const aCountdown = h.el('countdown').textContent, aTrades = h.el('kalshiTrades').innerHTML;
  h.streams[0].emit('state', b);
  await h.flush();
  await h.advance(300);
  return { ticker: h.el('ticker').textContent, strike: h.el('strike').textContent,
    distTarget: h.el('distTarget').textContent, countdown: h.el('countdown').textContent,
    trades: h.el('kalshiTrades').innerHTML, panel: h.el('recentWindows').innerHTML,
    aCountdown, aTrades };
}

async function runReconnect(fixture, h) {
  h.streams[0].emit('state', baseState('1m', fixture.price, 0));
  await h.flush();
  const before = live(h);
  await h.advance(12000);
  const silent = live(h), closed = h.streams[0].closed;
  await h.advance(5000);
  h.streams.at(-1).emit('state', baseState('1m', fixture.fresh_price, 17000));
  await h.flush();
  return { before, silent, closed, reconnected: h.streams.length > 1, after: live(h), price: h.el('price').textContent };
}

async function runRecentWindows(fixture, h) {
  const emit = (list, ms) => {
    const s = baseState('1m', 64000, ms);
    s.recent_windows = list;
    h.streams[0].emit('state', s);
  };
  emit(fixture.rows, 0); await h.flush();
  const withRows = h.el('recentWindows').innerHTML;
  emit(fixture.empty, 1000); await h.flush();
  const empty = h.el('recentWindows').innerHTML;
  emit(fixture.missing, 2000); await h.flush();
  const missing = h.el('recentWindows').innerHTML;
  return { withRows, empty, missing };
}

const SCENARIOS = { conflict: runConflict, stale_brti: runStaleBrti, rollover: runRollover,
  reconnect: runReconnect, recent_windows: runRecentWindows };

async function main() {
  const name = process.argv[2];
  if (!SCENARIOS[name]) { console.error('unknown scenario: ' + name); process.exit(2); }
  const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'render_states', name + '.json'), 'utf8'));
  const h = createHarness({ app, html });
  await h.flush();
  console.log(JSON.stringify(await SCENARIOS[name](fixture, h)));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e); process.exit(1); });
}
