const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const usd = (x, d = 2) => x == null ? '—' : '$' + Number(x).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const sgn = (x, d = 2) => x == null ? '—' : (x >= 0 ? '+' : '−') + usd(Math.abs(x), d);
const cents = x => x == null ? '—' : (Number(x) * 100).toFixed(2) + '¢';
const TZ = -new Date().getTimezoneOffset() * 60;  // chart shows local time
const shift = arr => arr.map(p => ({...p, time: p.time + TZ}));
let tf = localStorageGet('ta_tf') || '1m', fitNext = true, last = null, strikeLine = null;
let serverOffsetMs = 0;
// Freshness is judged on this page's own monotonic clock, from when a snapshot
// was last accepted: an open stream that stops sending must not stay "Live".
const FRESH_LIMIT_MS = 10000;
// `failure` is the newest transport/readiness problem; only an accepted
// healthy snapshot clears it, so a clock tick can age the badge but never
// declare recovery on its own.
let shown = null, shownAt = 0, shownServerMs = 0, streamHeardAt = 0, failure = null;

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
  b.onclick = () => { tf = b.dataset.tf; localStorageSet('ta_tf', tf); fitNext = true; shownServerMs = 0; abortPoll();
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
const CONTEXT = {'1m': [60, 15, 'last 15 min'], '5m': [300, 12, 'last 1 h'], '10m': [600, 12, 'last 2 h'],
                 '15m': [900, 12, 'last 3 h'], '1h': [3600, 12, 'last 12 h']};

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
  const f = s.frames[s.timeframe];
  if (f) renderFrame(s, f); else clearFrame();
  $('frameNote').textContent = frameNote(s, f);
  renderShared(s, f);
}

function frameNote(s, f) {
  if (!f) return `${s.timeframe} history unavailable (warming up or fetch failing); chart and readings hidden.`;
  const h = f.history || {};
  if (h.stale) return `${s.timeframe} candle history is ${Math.round(h.lag_seconds || 0)} s behind; directional readings unavailable.`;
  if (h.gap) return `${s.timeframe} history has missing bars; directional readings unavailable.`;
  return '';
}

function readingProblem(s, tf) {
  if (s.no_update) return 'no update for over 10 s';
  const f = s.frames[tf];
  if (!f) return `${tf} history unavailable (warming up or fetch failing)`;
  if (s.history_stale?.includes(tf) || f.history?.stale) return `${tf} history stale`;
  if (f.history?.gap) return `${tf} history has missing bars`;
  if (s.stale) return 'spot or composite unavailable';
  return '';
}

function compositeQuality(s) {
  const included = Object.values(s.venues || {}).filter(q => q.included && Number.isFinite(q.price));
  const n = included.length;
  if (!n || s.composite == null) return `Composite unavailable · ${n}/4 venues included`;
  const prices = included.map(q => q.price);
  return `${n}/4 venues included · ` + (n === 1 ? 'degraded: single venue' :
    `venue range ${usd(Math.max(...prices) - Math.min(...prices))}`);
}

function clearFrame() {
  [candles, vol, rsiS, macdL, macdSig, macdH, ...Object.values(L)].forEach(x => x.setData([]));
  candles.setMarkers([]);
  if (strikeLine) { candles.removePriceLine(strikeLine); strikeLine = null; }
  $('readings').innerHTML = ''; $('levels').innerHTML = '';
  $('rsiNow').textContent = ''; $('macdNow').textContent = '';
}

function renderFrame(s, f) {
  const c = f.chart, v = f.values;
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
  const problem = readingProblem(s, s.timeframe);
  $('readings').innerHTML = f.readings.map(r => problem
    ? `<tr class="context"><td>${r.name}</td><td>${esc(problem)}</td><td class="muted">Unavailable</td></tr>`
    : r.family === 'context'
    ? `<tr class="context"><td>${r.name}</td><td>${r.detail} · context, not counted in families</td><td>${badge(r.state)}</td></tr>`
    : `<tr><td>${r.name}</td><td class="muted">${r.detail}</td><td>${badge(r.state)}</td></tr>`).join('');
  const lv = [['Resistance (swing)', usd(v.resistance)], ['Support (swing)', usd(v.support)],
    ['ATR 14', usd(v.atr)], ['BB upper / lower', `${usd(v.bb_upper, 0)} / ${usd(v.bb_lower, 0)}`],
    ['BB width', v.bb_width_pct == null ? '—' : v.bb_width_pct.toFixed(2) + '%'], ['VWAP', usd(v.vwap)],
    ['EMA 200', usd(v.ema200)]];
  $('levels').innerHTML = lv.map(([a, b]) => `<tr><td class="muted">${a}</td><td>${b}</td></tr>`).join('');
  $('rsiNow').textContent = v.rsi == null ? '' : '· ' + v.rsi.toFixed(1);
  $('macdNow').textContent = v.macd_hist == null ? '' : `· hist ${v.macd_hist >= 0 ? '+' : ''}${v.macd_hist.toFixed(2)}`;
}

function renderShared(s, f) {
  const px = s.spot.price, flow = s.order_flow || {};
  $('price').textContent = usd(px);
  $('composite').textContent = usd(s.composite);
  $('average60').textContent = usd(s.average_60s);
  $('compositeQuality').textContent = compositeQuality(s);
  // Our closing-minute composite mean vs Kalshi's settlement value (BRTI 60 s average).
  const proxy = s.proxy_record;
  $('proxyRecord').textContent = !proxy || !proxy.n ? 'Proxy vs settlement: no scored windows yet'
    : `Historical proxy error (last ${proxy.n} scored windows): median ${usd(proxy.median_abs)}, 90% within ${usd(proxy.p90_abs)}, ` +
      `bias ${sgn(proxy.bias)}` + (proxy.too_few ? ' · too few to judge' : '');
  const coverage = proxy?.coverage;
  // Listed in the server's check order; only closes awaiting settlement can still be scored.
  $('proxyCoverage').textContent = coverage ?
    `All recorded closes: ${coverage.scored}/${coverage.total_closes} scored · ${coverage.incomplete_samples} incomplete samples · ` +
    `${coverage.awaiting_settlement || 0} awaiting settlement · ${coverage.missing_settlement} settlement value unavailable · ` +
    `${coverage.invalid_proxy} invalid proxy (each close counted once, in this order).` : '';
  $('venues').innerHTML = Object.entries(s.venues || {}).map(([name, q]) =>
    `<tr><td>${name} ${s.feed_modes?.[name] ? '(' + s.feed_modes[name] + ')' : ''}</td><td>${usd(q.price)}</td><td>${q.age == null ? '—' : q.age.toFixed(1) + 's'} (${q.age_basis === 'fetch' ? 'fetch age' : q.age_basis === 'trade' ? 'trade age' : q.age_basis === 'ticker' ? 'exchange timestamp age' : 'age basis unavailable'}) · ${q.included ? 'included' : 'excluded: ' + q.excluded_reason}</td></tr>`).join('');
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
    const sigmaProblem = readingProblem(s, '1m');
    $('sig').textContent = sigmaProblem ? `Unavailable · ${sigmaProblem}` :
      w.distance_sigmas == null ? '—' : `${w.distance_sigmas >= 0 ? '+' : '−'}${Math.abs(w.distance_sigmas).toFixed(2)}σ · sd ${usd(w.distance_sd_usd, 0)}`;
    // Without a composite or target the server makes no projection and counts no samples.
    $('settlementCoverage').textContent = w.distance_source === 'projected 60s average' && w.distance != null ?
      `${w.observed_seconds || 0}/60 s observed · ${60 - (w.observed_seconds || 0)} s projected ` +
      `(${w.missing_seconds || 0} missing elapsed seconds; remainder still ahead). Historical proxy error is separate.` : '';
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
    contextSpark('windowPath', f ? f.chart.candles : [], w.price_path, w, s.timeframe);
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
    $('settlementCoverage').textContent = '';
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
  renderTally(s, s.frames['1m']);
  renderMtf(s);
  const errs = Object.entries(s.errors || {});
  $('errors').textContent = errs.length ? 'Feed errors: ' + errs.map(([k, e]) => `${k} — ${e.error}`).join(' · ') : '';
}

function renderTally(s, head) {
  const problem = readingProblem(s, '1m');
  $('tallyTf').textContent = '1m';
  $('tallyStale').textContent = problem;
  $('readTf').textContent = $('lvlTf').textContent = s.timeframe;
  if (problem) {
    $('lean').innerHTML = '<span class="muted">Unavailable</span>';
    $('families').innerHTML = `<span class="muted">${esc(problem)}</span>`;
    ['bBull', 'bNeu', 'bBear'].forEach(id => $(id).style.width = '0%');
    ['nBull', 'nNeu', 'nBear'].forEach(id => $(id).textContent = '');
  } else {
    const t = head.tally, n = t.bull + t.bear + t.neutral || 1;
    $('lean').innerHTML = `<span class="${t.lean === 'bullish' ? 'bull' : t.lean === 'bearish' ? 'bear' : 'neutral'}">${t.lean[0].toUpperCase() + t.lean.slice(1)}</span>`;
    $('bBull').style.width = t.bull / n * 100 + '%'; $('bNeu').style.width = t.neutral / n * 100 + '%'; $('bBear').style.width = t.bear / n * 100 + '%';
    $('nBull').textContent = `${t.bull} bull`; $('nNeu').textContent = `${t.neutral} neutral`; $('nBear').textContent = `${t.bear} bear`;
    // One vote per family (trend, momentum, 1m flow); the legacy count above is unchanged.
    const fam = head.family;
    $('families').innerHTML = fam ? Object.entries(fam.votes).map(([k, vote]) =>
      k === 'flow' && fam.flow_ready === false
        ? `Flow <span class="neutral">warming up (${Math.floor(fam.flow_coverage_seconds || 0)}/60 s)</span>`
        : `${k[0].toUpperCase() + k.slice(1)} <span class="${vote}">${vote}</span>`).join(' · ') +
      ` → net ${fam.net > 0 ? '+' : ''}${fam.net} (<span class="${fam.lean === 'bullish' ? 'bull' : fam.lean === 'bearish' ? 'bear' : 'neutral'}">${fam.lean}</span>)` : '—';
  }
  const rec = s.tally_record;
  $('tallyRecord').textContent = !rec || !rec.scored ? 'Legacy 1m opening lean vs settlement: no scored windows yet'
    : `Legacy 1m opening lean vs settlement: ${rec.hits}/${rec.scored} (${(rec.rate * 100).toFixed(1)}%), 95% [${(rec.wilson95[0] * 100).toFixed(1)}%, ` +
      `${(rec.wilson95[1] * 100).toFixed(1)}%] · interval ${rec.includes_50 ? 'includes' : 'excludes'} 50%` +
      (rec.too_few ? ' · too few to judge' : '');
  if (rec?.skipped_lines > 0) $('tallyRecord').textContent += ` · log: ${rec.skipped_lines} unreadable lines skipped`;
}

function renderMtf(s) {
  // Every tally reading (rows) for every timeframe (columns), with the detail on hover.
  const tfs = ['1m', '5m', '10m', '15m', '1h'].filter(k => s.frames[k]);
  const stale = k => s.history_stale?.includes(k) || s.frames[k].history?.gap;
  $('mtfHead').innerHTML = `<tr><th></th>${tfs.map(k => stale(k)
    ? `<th class="stale" title="${k} history stale or incomplete">${k} ⚠</th>` : `<th>${k}</th>`).join('')}</tr>`;
  const mark = r => !r ? '<td class="muted">—</td>' :
    `<td class="${r.state}" title="${esc(r.name + ': ' + r.detail)}">${r.state === 'bull' ? '▲' : r.state === 'bear' ? '▼' : '•'}</td>`;
  $('mtf').innerHTML = MTF_ROWS.map(([label, prefix]) => `<tr><td>${label}</td>${tfs.map(k =>
    readingProblem(s, k) ? `<td class="muted" title="${esc(readingProblem(s, k))}">—</td>` :
    mark(s.frames[k].readings.find(r => r.name.startsWith(prefix)))).join('')}</tr>`).join('') +
    `<tr class="total"><td>Bull/bear</td>${tfs.map(k => { const x = s.frames[k].tally;
      // A short mark: five "Unavailable" cells would overflow the card on a phone.
      if (readingProblem(s, k)) return `<td class="muted" title="${esc(readingProblem(s, k))}">—</td>`;
      return `<td class="${x.lean === 'bullish' ? 'bull' : x.lean === 'bearish' ? 'bear' : 'neutral'}">${x.bull}/${x.bear}</td>`; }).join('')}</tr>`;
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
      if (last.no_update) checkFreshness(); else setStatus(last);  // keep "No update" while silent
      return;
    }
    $('countdown').textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, '0')} left`;
    $('stripTime').textContent = $('countdown').textContent.replace(' left', '');
  }
  checkFreshness();
}

function checkFreshness() {
  const t = performance.now();
  if (source && t - Math.max(streamHeardAt, streamOpenedAt) > FRESH_LIMIT_MS) {
    // Open but silent: drop it, poll meanwhile, and reconnect.
    source.close(); source = null;
    startFallback();
    if (!retry) retry = setTimeout(connectStream, 5000);
  }
  if (!shown) return;
  const age = t - shownAt;
  if (age > FRESH_LIMIT_MS) {
    // Re-render once without directional summaries. `no_update` is page-only,
    // distinct from the server's `stale`; it isn't new data, so a pending
    // chart fit (after a tab switch) waits for the next real snapshot.
    if (last && !last.no_update) {
      const fit = fitNext;
      render({...last, no_update: true, server_time: new Date(Date.now() + serverOffsetMs).toISOString()});
      fitNext = fit;
    }
    setLive(false, `No update for ${Math.floor(age / 1000)} s` + (failure ? ' · ' + failure : ''));
  }
  else setStatus(shown, age / 1000);
}

// Show a snapshot only if it is for the selected timeframe and not older than
// the one on screen: a late poll response must not roll the page back.
function accept(s) {
  const at = Date.parse(s.server_time);
  if (s.timeframe !== tf || !(at >= shownServerMs)) return false;
  shownServerMs = at; shown = s; shownAt = performance.now(); failure = null;
  render(s);
  setStatus(s);
  return true;
}

// One poll at a time, with a deadline; a tab switch aborts the pending one.
let pollCtl = null;
async function poll() {
  if (pollCtl) return;
  const ctl = new AbortController(), want = tf, deadline = setTimeout(() => ctl.abort(), 5000);
  pollCtl = ctl;
  try {
    const r = await fetch('/api/state?tf=' + want, {cache: 'no-store', signal: ctl.signal});
    const s = await r.json();
    if (ctl !== pollCtl || want !== tf) return;  // superseded
    if (!s.ready) { fail('warming up'); return; }
    accept(s);
  } catch (e) { if (ctl === pollCtl) fail('server unreachable'); }
  finally { clearTimeout(deadline); if (pollCtl === ctl) pollCtl = null; }
}
function abortPoll() { if (pollCtl) { pollCtl.abort(); pollCtl = null; } }
// Timeframes on screen whose candle history is stale: the selected chart's,
// and 1m, which always supplies the headline tally.
function staleShown(s) {
  return [...new Set([s.timeframe, '1m'])].filter(k => s.history_stale?.includes(k));
}
function fail(text) { failure = text; setLive(false, text[0].toUpperCase() + text.slice(1)); }
function setLive(ok, text) { $('live').className = 'live ' + (ok ? 'ok' : 'bad'); $('liveText').textContent = text; }
function setStatus(s, aged = 0) {
  if (failure) setLive(false, failure[0].toUpperCase() + failure.slice(1));
  else if (s.stale) setLive(false, 'STALE · spot or composite unavailable');
  else if (s.kalshi_stale) { $('live').className = 'live warn'; $('liveText').textContent = 'Live · Kalshi stale'; }
  else if (staleShown(s).length) { $('live').className = 'live warn'; $('liveText').textContent = `Live · ${staleShown(s).join(' & ')} history stale`; }
  else setLive(true, `Live · spot ${(s.spot_age + aged).toFixed(1)}s`);
}

let source = null, retry = null, fallback = null, cache = null, streamOpenedAt = 0;

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
  source = current; streamOpenedAt = performance.now();
  current.addEventListener('state', event => {
    if (source !== current) return;
    try {
      const s = JSON.parse(event.data);
      streamHeardAt = performance.now();
      if (!s.ready) { fail('warming up'); return; }
      if (s.timeframe !== tf) return;
      if (!expand(s)) { connectStream(); return; }  // tail without a base: resync
      accept(s);
      stopFallback();
    } catch { startFallback(); }
  });
  // The server could not serialize its state; it keeps the stream open.
  current.addEventListener('problem', () => {
    if (source !== current) return;
    streamHeardAt = performance.now();
    fail('server state error');
  });
  current.onerror = () => {
    if (source !== current) return;
    current.close(); source = null;
    startFallback();
    retry = setTimeout(connectStream, 5000);
  };
}
setInterval(tick, 250); tick(); connectStream();
