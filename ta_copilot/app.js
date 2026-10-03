const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const usd = (x, d = 2) => x == null ? '—' : '$' + Number(x).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const sgn = (x, d = 2) => x == null ? '—' : (x >= 0 ? '+' : '−') + usd(Math.abs(x), d);
const cents = x => x == null ? '—' : (Number(x) * 100).toFixed(2) + '¢';
const TZ = -new Date().getTimezoneOffset() * 60;  // chart shows local time
const shift = arr => arr.map(p => ({...p, time: p.time + TZ}));
let tf = localStorageGet('ta_tf') || '1m', fitNext = true, last = null, strikeLine = null;
let serverOffsetMs = 0;

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }

const base = () => ({
  layout: {background: {color: 'transparent'}, textColor: css('--muted'), fontSize: 11},
  grid: {vertLines: {color: css('--line')}, horzLines: {color: css('--line')}},
  rightPriceScale: {borderColor: css('--line'), minimumWidth: 78},
  timeScale: {borderColor: css('--line'), timeVisible: true, secondsVisible: false},
  crosshair: {mode: 0}, autoSize: true,
});
// The wheel and vertical swipes scroll the page, not the chart: the chart is
// most of a phone screen and would otherwise trap scrolling. Drag pans it;
// pinch, or dragging an axis, zooms it.
const main = LightweightCharts.createChart($('chart'), {...base(),
  handleScroll: {mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
  handleScale: {mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true}});
const rsiC = LightweightCharts.createChart($('rsi'), base());
const macdC = LightweightCharts.createChart($('macd'), base());
const cvdC = LightweightCharts.createChart($('cvd'), base());
const candles = main.addCandlestickSeries({upColor: css('--bull'), downColor: css('--bear'), wickUpColor: css('--bull'),
  wickDownColor: css('--bear'), borderVisible: false});
const vol = main.addHistogramSeries({priceScaleId: 'vol', priceFormat: {type: 'volume'}, lastValueVisible: false, priceLineVisible: false});
main.priceScale('vol').applyOptions({scaleMargins: {top: .82, bottom: 0}});
const line = (chart, color, w = 1, extra = {}) => chart.addLineSeries({color, lineWidth: w, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, ...extra});
const L = {ema9: line(main, css('--e9')), ema21: line(main, css('--e21')), ema50: line(main, css('--e50')),
  bb_upper: line(main, css('--bb'), 1, {lineStyle: 2}), bb_lower: line(main, css('--bb'), 1, {lineStyle: 2}),
  vwap: line(main, css('--vwap'), 2)};
const rsiS = line(rsiC, css('--accent'), 2, {lastValueVisible: true});
[70, 30].forEach(v => rsiS.createPriceLine({price: v, color: css('--neutral'), lineStyle: 2, lineWidth: 1, axisLabelVisible: false}));
const macdH = macdC.addHistogramSeries({lastValueVisible: false, priceLineVisible: false});
const macdL = line(macdC, css('--e21'), 2), macdSig = line(macdC, css('--e9'), 1);
const cvdS = line(cvdC, css('--accent'), 2, {lastValueVisible: true});

// These series have different point counts, so synchronize by time, not index.
// Only the main chart leads: CVD covers just the current window, and if it could
// lead it would shrink the main chart to its few minutes of data.
const followers = [rsiC, macdC, cvdC];
followers.forEach(c => c.applyOptions({handleScroll: false, handleScale: false}));
function syncFollowers() {
  const r = main.timeScale().getVisibleRange();
  if (r) followers.forEach(o => { try { o.timeScale().setVisibleRange(r); } catch {} });
}
main.timeScale().subscribeVisibleTimeRangeChange(syncFollowers);

document.querySelectorAll('#tabs button').forEach(b => {
  b.classList.toggle('on', b.dataset.tf === tf);
  b.onclick = () => { tf = b.dataset.tf; localStorageSet('ta_tf', tf); fitNext = true;
    document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b)); connectStream(); };
});

function badge(state) { return `<span class="badge ${state}">${state === 'bull' ? 'bull' : state === 'bear' ? 'bear' : state}</span>`; }

function spark(id, points, target = null) {
  const svg = $(id), data = (points || []).filter(p => Number.isFinite(p.value));
  svg.setAttribute('viewBox', '0 0 300 80');
  if (!data.length) { svg.innerHTML = '<text x="8" y="42" fill="currentColor">No observations yet</text>'; return; }
  const values = data.map(p => p.value);
  if (target != null) values.push(Number(target));
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 0.0001);
  const left = data[0].time, right = Math.max(data[data.length - 1].time, left + 1);
  const x = p => 8 + (p.time - left) / (right - left) * 284;
  const y = v => 72 - (v - lo) / span * 64;
  const d = data.map((p, i) => (i ? 'L' : 'M') + x(p).toFixed(1) + ' ' + y(p.value).toFixed(1)).join(' ');
  const guide = target == null ? '' : `<line x1="8" y1="${y(target)}" x2="292" y2="${y(target)}" stroke="${css('--strike')}" stroke-dasharray="4 3"/>`;
  svg.innerHTML = guide + `<path d="${d}" fill="none" stroke="${css('--accent')}" stroke-width="2"/>`;
}

// Strip context per timeframe: [seconds per bar, bars of lookback, label].
const CONTEXT = {'1m': [60, 60, 'last 1 h'], '5m': [300, 48, 'last 4 h'], '10m': [600, 48, 'last 8 h'],
                 '15m': [900, 48, 'last 12 h'], '1h': [3600, 48, 'last 2 days']};

// Candle closes from the selected timeframe before the window, the 1 s composite
// path inside it (shaded), the target across the whole view, and the time still
// left in the window as empty space on the right.
function contextSpark(id, candles, path, w, timeframe) {
  const svg = $(id), [sec, bars, label] = CONTEXT[timeframe] || CONTEXT['1m'];
  $('ctxLabel').textContent = `${timeframe} · ${label}`;
  // Stretch to the column; non-scaling strokes keep lines crisp when stretched.
  svg.setAttribute('viewBox', '0 0 300 80');
  svg.setAttribute('preserveAspectRatio', 'none');
  const open = Date.parse(w.open_time) / 1000, close = Date.parse(w.close_time) / 1000;
  const before = (candles || []).filter(b => b.time + sec <= open).slice(-bars)
    .map(b => ({time: b.time + sec, value: b.close}));
  const inside = (path || []).filter(p => Number.isFinite(p.value) && p.time >= open);
  const data = before.concat(inside);
  if (!data.length) { svg.innerHTML = '<text x="8" y="42" fill="currentColor">No observations yet</text>'; return; }
  const values = data.map(p => p.value);
  if (w.strike != null) values.push(Number(w.strike));
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 0.0001);
  const left = Math.min(data[0].time, open - bars * sec), right = Math.max(close, data[data.length - 1].time + 1);
  const x = t => 8 + (t - left) / (right - left) * 284;
  const y = v => 72 - (v - lo) / span * 64;
  const line = pts => pts.map((p, i) => (i ? 'L' : 'M') + x(p.time).toFixed(1) + ' ' + y(p.value).toFixed(1)).join(' ');
  const now = Math.min((Date.now() + serverOffsetMs) / 1000, close);
  let out = `<rect x="${x(open).toFixed(1)}" y="4" width="${Math.max(4, x(close) - x(open)).toFixed(1)}" height="72" fill="${css('--accent')}" opacity="0.10"/>`;
  out += `<line x1="${x(now).toFixed(1)}" y1="4" x2="${x(now).toFixed(1)}" y2="76" stroke="${css('--muted')}" stroke-width="1" opacity="0.6" vector-effect="non-scaling-stroke"/>`;
  if (w.strike != null) out += `<line x1="8" y1="${y(w.strike).toFixed(1)}" x2="292" y2="${y(w.strike).toFixed(1)}" stroke="${css('--strike')}" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
  if (before.length) out += `<path d="${line(before.concat(inside.slice(0, 1)))}" fill="none" stroke="${css('--muted')}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  if (inside.length) out += `<path d="${line(inside)}" fill="none" stroke="${css('--accent')}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  svg.innerHTML = out;
}

function depth(level) { return level ? `${cents(level.price)} · ${Number(level.count).toLocaleString()} contracts` : '—'; }
function quoteLabel(q) { return q ? `${q.source} ${q.age.toFixed(1)}s` : 'unavailable'; }
function quoteSources(bid, ask) {
  if (bid && ask && bid.source === ask.source && bid.age === ask.age) return quoteLabel(bid);
  return `bid ${quoteLabel(bid)} · ask ${quoteLabel(ask)}`;
}

function render(s) {
  last = s;
  serverOffsetMs = Date.parse(s.server_time) - Date.now();
  const f = s.frames[s.timeframe]; if (!f) return;
  const c = f.chart, v = f.values, px = s.spot.price;

  candles.setData(shift(c.candles));
  vol.setData(shift(c.volume.map(x => ({time: x.time, value: x.value,
    color: (x.up ? css('--bull') : css('--bear')) + '55'}))));
  for (const k in L) L[k].setData(shift(c[k]));
  rsiS.setData(shift(c.rsi));
  macdL.setData(shift(c.macd)); macdSig.setData(shift(c.macd_signal));
  macdH.setData(shift(c.macd_hist.map(p => ({...p, color: (p.value >= 0 ? css('--bull') : css('--bear')) + '99'}))));
  const flow = s.order_flow || {};
  cvdS.setData(shift(flow.cvd_points || []));
  const markers = new Map();
  if (s.timeframe === '1m') for (const trade of flow.large_trades || []) {
    const minute = Math.floor(trade.time / 60) * 60;
    if (!markers.has(minute) || markers.get(minute).notional < trade.notional) markers.set(minute, trade);
  }
  candles.setMarkers([...markers].sort((a, b) => a[0] - b[0]).map(([minute, trade]) => ({
    time: minute + TZ, position: 'aboveBar', color: css('--neutral'), shape: 'circle',
    text: 'Large ' + usd(trade.notional, 0)})));
  if (strikeLine) { candles.removePriceLine(strikeLine); strikeLine = null; }
  if (s.window && s.window.strike) strikeLine = candles.createPriceLine({price: s.window.strike, color: css('--strike'),
    lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'target'});
  if (fitNext) { const n = c.candles.length; main.timeScale().setVisibleLogicalRange({from: n - 120, to: n + 3}); fitNext = false; }
  syncFollowers();  // setData on a follower can reset its range
  $('vwapNote').textContent = s.timeframe === '1m' ? '(anchored at window open)' : '(anchored at first loaded bar)';

  $('price').textContent = usd(px);
  $('composite').textContent = usd(s.composite);
  $('average60').textContent = usd(s.average_60s);
  $('venues').innerHTML = Object.entries(s.venues || {}).map(([name, q]) =>
    `<tr><td>${name} ${s.feed_modes?.[name] ? '(' + s.feed_modes[name] + ')' : ''}</td><td>${usd(q.price)}</td><td>${q.age == null ? '—' : q.age.toFixed(1) + 's'} ${q.included ? 'included' : 'excluded: ' + q.excluded_reason}</td></tr>`).join('');
  $('bidask').textContent = `${usd(s.spot.bid)} / ${usd(s.spot.ask)}`;
  $('chg').innerHTML = `${pct(px, s.ref_15m)} / ${pct(px, s.ref_1h)}`;

  const w = s.window;
  if (w) {
    $('ticker').textContent = w.ticker;
    $('kalshiAge').textContent = w.age > 10 ? `Kalshi data ${Math.floor(w.age)} s old` : '';
    $('strike').textContent = w.strike == null ? 'Target pending' : usd(w.strike);
    $('distanceLabel').textContent = (w.distance_source === 'projected 60s average' ? 'Projected 60s average' : 'Composite') + ' vs target';
    const side = w.distance == null ? '' : w.distance >= 0 ? 'bull' : 'bear';
    $('distBlock').className = 'dist-block ' + side;
    $('dist').innerHTML = w.distance == null ? '—' : `<span class="${side}">${sgn(w.distance)} ${w.distance >= 0 ? 'above' : 'below'}</span>`;
    $('sig').textContent = w.distance_sigmas == null ? '—' : `${w.distance_sigmas >= 0 ? '+' : '−'}${Math.abs(w.distance_sigmas).toFixed(2)}σ · sd ${usd(w.distance_sd_usd, 0)}`;
    if (w.missing_seconds) $('sig').textContent += ` · ${w.missing_seconds} missing sample s projected`;
    const book = w.orderbook_age != null && w.orderbook_age <= 10 ? w.orderbook || {} : {};
    const q = w.display_quotes || {};
    $('yesLabel').textContent = 'YES bid / ask';
    $('noLabel').textContent = 'NO bid / ask';
    $('yesSource').textContent = quoteSources(q.yes_bid, q.yes_ask);
    $('noSource').textContent = quoteSources(q.no_bid, q.no_ask);
    $('yes').textContent = `${cents(q.yes_bid?.price)} / ${cents(q.yes_ask?.price)}`;
    $('no').textContent = `${cents(q.no_bid?.price)} / ${cents(q.no_ask?.price)}`;
    $('stripHigh').textContent = usd(w.high_since_open);
    $('stripLow').textContent = usd(w.low_since_open);
    $('stripAvg').textContent = s.composite == null || s.average_60s == null ? '—' : sgn(s.composite - s.average_60s);
    $('pathNote').textContent = w.path_partial ? 'Observed path is partial for this window.' : '';
    contextSpark('windowPath', c.candles, w.price_path, w, s.timeframe);
    $('bookAge').textContent = w.orderbook_age == null ? '' : `· book ${w.orderbook_age.toFixed(1)}s old`;
    $('yesDepth').textContent = `${depth(book.yes_bid)} / ${depth(book.yes_ask)}`;
    $('noDepth').textContent = `${depth(book.no_bid)} / ${depth(book.no_ask)}`;
    spark('yesMid', w.yes_mid_path);
    $('kalshiTrades').innerHTML = (w.recent_trades || []).slice(0, 8).map(t =>
      `<tr><td>${t.time.slice(11, 19)}</td><td>${cents(t.yes_price)}</td><td>${Number(t.count).toLocaleString()}</td></tr>`).join('');
  } else {
    $('kalshiAge').textContent = 'Kalshi window unavailable';
    ['ticker', 'strike', 'dist', 'sig', 'yes', 'no'].forEach(id => $(id).textContent = id === 'ticker' ? '' : '—');
    $('distanceLabel').textContent = 'Composite vs target';
    $('distBlock').className = 'dist-block';
    $('countdown').textContent = 'No open window';
    $('yesLabel').textContent = 'YES bid / ask'; $('noLabel').textContent = 'NO bid / ask';
    $('yesSource').textContent = ''; $('noSource').textContent = '';
    ['stripTime', 'stripAvg', 'stripHigh', 'stripLow', 'yesDepth', 'noDepth'].forEach(id => $(id).textContent = '—');
    $('bookAge').textContent = '';
    $('pathNote').textContent = '';
    $('kalshiTrades').innerHTML = '';
    spark('windowPath', []); spark('yesMid', []);
  }

  $('flowCvd').textContent = flow.cvd == null ? '—' : `${flow.cvd >= 0 ? '+' : ''}${flow.cvd.toFixed(4)} BTC`;
  $('cvdNow').textContent = $('flowCvd').textContent;
  const one = flow.one_minute || {};
  $('flowOne').textContent = one.buy_volume == null ? '—' : `${one.buy_volume.toFixed(4)} / ${one.sell_volume.toFixed(4)} BTC`;
  $('flowImbalance').textContent = one.imbalance == null ? '—' : `${(one.imbalance * 100).toFixed(1)}%`;
  $('flowThreshold').textContent = usd(flow.config?.large_trade_usd, 0) + ' notional';
  $('flowCoverage').textContent = flow.partial_window ? 'Flow observed since connection; earlier window trades may be missing.' : 'Flow observed from window open.';

  // The headline tally always reads the 1m frame; the tabs drive the chart and side cards.
  const t = (s.frames['1m'] || f).tally, n = t.bull + t.bear + t.neutral || 1;
  $('tallyTf').textContent = s.frames['1m'] ? '1m' : s.timeframe;
  $('readTf').textContent = $('lvlTf').textContent = s.timeframe;
  $('lean').innerHTML = `<span class="${t.lean === 'bullish' ? 'bull' : t.lean === 'bearish' ? 'bear' : 'neutral'}">${t.lean[0].toUpperCase() + t.lean.slice(1)}</span>`;
  $('bBull').style.width = t.bull / n * 100 + '%'; $('bNeu').style.width = t.neutral / n * 100 + '%'; $('bBear').style.width = t.bear / n * 100 + '%';
  $('nBull').textContent = `${t.bull} bull`; $('nNeu').textContent = `${t.neutral} neutral`; $('nBear').textContent = `${t.bear} bear`;
  // One vote per family (trend, momentum, 1m flow); the legacy count above is unchanged.
  const fam = (s.frames['1m'] || f).family;
  $('families').innerHTML = fam ? Object.entries(fam.votes).map(([k, vote]) =>
    `${k[0].toUpperCase() + k.slice(1)} <span class="${vote}">${vote}</span>`).join(' · ') +
    ` → net ${fam.net > 0 ? '+' : ''}${fam.net} (<span class="${fam.lean === 'bullish' ? 'bull' : fam.lean === 'bearish' ? 'bear' : 'neutral'}">${fam.lean}</span>)` : '—';
  const rec = s.tally_record;
  $('tallyRecord').textContent = !rec || !rec.scored ? 'Lean at open: no scored windows yet'
    : `Lean at open: ${rec.hits}/${rec.scored} (${(rec.rate * 100).toFixed(1)}%), 95% [${(rec.wilson95[0] * 100).toFixed(1)}%, ` +
      `${(rec.wilson95[1] * 100).toFixed(1)}%] · interval ${rec.includes_50 ? 'includes' : 'excludes'} 50%` +
      (rec.too_few ? ' · too few to judge' : '');

  $('readings').innerHTML = f.readings.map(r => r.family === 'context'
    ? `<tr class="context"><td>${r.name}</td><td>${r.detail} · context, not counted in families</td><td>${badge(r.state)}</td></tr>`
    : `<tr><td>${r.name}</td><td class="muted">${r.detail}</td><td>${badge(r.state)}</td></tr>`).join('');
  const lv = [['Resistance (swing)', usd(v.resistance)], ['Support (swing)', usd(v.support)],
    ['ATR 14', usd(v.atr)], ['BB upper / lower', `${usd(v.bb_upper, 0)} / ${usd(v.bb_lower, 0)}`],
    ['BB width', v.bb_width_pct == null ? '—' : v.bb_width_pct.toFixed(2) + '%'], ['VWAP', usd(v.vwap)],
    ['EMA 200', usd(v.ema200)]];
  $('levels').innerHTML = lv.map(([a, b]) => `<tr><td class="muted">${a}</td><td>${b}</td></tr>`).join('');
  $('rsiNow').textContent = v.rsi == null ? '' : '· ' + v.rsi.toFixed(1);
  $('macdNow').textContent = v.macd_hist == null ? '' : `· hist ${v.macd_hist >= 0 ? '+' : ''}${v.macd_hist.toFixed(2)}`;

  // Every tally reading (rows) for every timeframe (columns), with the detail on hover.
  const tfs = ['1m', '5m', '10m', '15m', '1h'].filter(k => s.frames[k]);
  $('mtfHead').innerHTML = `<tr><th></th>${tfs.map(k => `<th>${k}</th>`).join('')}</tr>`;
  const mark = r => !r ? '<td class="muted">—</td>' :
    `<td class="${r.state}" title="${esc(r.name + ': ' + r.detail)}">${r.state === 'bull' ? '▲' : r.state === 'bear' ? '▼' : '•'}</td>`;
  $('mtf').innerHTML = MTF_ROWS.map(([label, prefix]) => `<tr><td>${label}</td>${tfs.map(k =>
    mark(s.frames[k].readings.find(r => r.name.startsWith(prefix)))).join('')}</tr>`).join('') +
    `<tr class="total"><td>Bull/bear</td>${tfs.map(k => { const x = s.frames[k].tally;
      return `<td class="${x.lean === 'bullish' ? 'bull' : x.lean === 'bearish' ? 'bear' : 'neutral'}">${x.bull}/${x.bear}</td>`; }).join('')}</tr>`;

  const errs = Object.entries(s.errors || {});
  $('errors').textContent = errs.length ? 'Feed errors: ' + errs.map(([k, e]) => `${k} — ${e.error}`).join(' · ') : '';
}

// Tally rows in server order: [label, reading-name prefix].
const MTF_ROWS = [['EMA stack', 'EMA stack'], ['EMA 9/21', 'EMA 9/21'], ['EMA 200', 'EMA 200'], ['RSI 14', 'RSI'],
  ['MACD', 'MACD'], ['BB %B', 'Bollinger'], ['Stoch', 'Stoch'], ['VWAP', 'VWAP']];
const esc = x => String(x).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

function pct(px, ref) {
  if (ref == null) return '—';
  const d = px - ref;
  return `<span class="${d >= 0 ? 'bull' : 'bear'}">${d >= 0 ? '+' : '−'}${Math.abs(d / ref * 100).toFixed(2)}%</span>`;
}

function tick() {
  const now = new Date(Date.now() + serverOffsetMs);
  $('clock').textContent = now.toISOString().slice(11, 19) + ' UTC · ' +
    now.toLocaleTimeString('en-US', {timeZone: 'America/Los_Angeles', hour12: false}) + ' PT';
  if (last && last.window) {
    const left = Math.max(0, new Date(last.window.close_time) - now) / 1000;
    if (left === 0) {
      render({...last, server_time: now.toISOString(), window: null, kalshi_stale: true});
      setStatus(last);
      return;
    }
    $('countdown').textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, '0')} left`;
    $('stripTime').textContent = $('countdown').textContent.replace(' left', '');
  }
}

async function poll() {
  try {
    const r = await fetch('/api/state?tf=' + tf, {cache: 'no-store'});
    const s = await r.json();
    if (!s.ready) { setLive(false, 'Warming up…'); return; }
    render(s);
    setStatus(s);
  } catch (e) { setLive(false, 'Server unreachable'); }
}
function setLive(ok, text) { $('live').className = 'live ' + (ok ? 'ok' : 'bad'); $('liveText').textContent = text; }
function setStatus(s) {
  if (s.stale) setLive(false, 'STALE · spot or composite unavailable');
  else if (s.kalshi_stale) { $('live').className = 'live warn'; $('liveText').textContent = 'Live · Kalshi stale'; }
  else setLive(true, `Live · spot ${s.spot_age.toFixed(1)}s`);
}

let source = null, retry = null, fallback = null, cache = null;

// Stream events after the first carry only the newest points of each series
// (chart_tail / paths_tail); merge them by time into the last full snapshot.
function mergeByTime(base, tail) {
  const out = (base || []).slice();
  for (const p of tail || []) {
    let j = out.length - 1;
    while (j >= 0 && out[j].time > p.time) j--;
    if (j >= 0 && out[j].time === p.time) out[j] = p; else out.splice(j + 1, 0, p);
  }
  return out;
}
function expand(s) {
  const f = s.frames && s.frames[s.timeframe];
  if (f && f.chart) {
    if (!f.chart_tail) cache = {tf: s.timeframe, chart: f.chart, window: null};
    else if (!cache || cache.tf !== s.timeframe) return false;
    else { for (const k in f.chart) cache.chart[k] = mergeByTime(cache.chart[k], f.chart[k]); f.chart = cache.chart; }
  }
  const w = s.window;
  if (w && cache) {
    const paths = ['price_path', 'yes_mid_path'];
    if (!w.paths_tail) cache.window = {ticker: w.ticker, ...Object.fromEntries(paths.map(k => [k, w[k] || []]))};
    else if (cache.window && cache.window.ticker === w.ticker)
      for (const k of paths) { cache.window[k] = mergeByTime(cache.window[k], w[k]); w[k] = cache.window[k]; }
  }
  return true;
}
function startFallback() {
  if (fallback) return;
  poll();
  fallback = setInterval(poll, 2000);
}
function stopFallback() { if (fallback) { clearInterval(fallback); fallback = null; } }
function connectStream() {
  if (source) { source.close(); source = null; }
  if (retry) { clearTimeout(retry); retry = null; }
  cache = null;
  if (!window.EventSource) { startFallback(); return; }
  const current = new EventSource('/api/stream?tf=' + encodeURIComponent(tf));
  source = current;
  current.addEventListener('state', event => {
    if (source !== current) return;
    try {
      const s = JSON.parse(event.data);
      if (!s.ready) { setLive(false, 'Warming up…'); return; }
      if (!expand(s)) { connectStream(); return; }  // tail without a base: resync
      render(s);
      setStatus(s);
      stopFallback();
    } catch { startFallback(); }
  });
  current.onerror = () => {
    if (source !== current) return;
    current.close(); source = null;
    startFallback();
    retry = setTimeout(connectStream, 5000);
  };
}
setInterval(tick, 250); tick(); connectStream();
