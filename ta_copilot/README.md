# BTC TA Copilot (read-only, descriptive)

A live technical-analysis view for the Kalshi KXBTC15M series. It is separate from the Study 005 candidate and the Acer study stack, and it does not change the advice gate: **no credentials, no account access, and no orders.** The experimental paper scalp trigger (mean reversion, then `momentum_v1`) ran 2026-10-03 07:14–08:14 UTC and was **retired by the owner on 2026-10-03**; its code, report and pre-removal snapshot are in `history/retired_scalp_20261003/` (on the Acer: `~/btc-ta/retired_scalp_20261003/`), and its journal stays on the Acer at `~/btc-ta/state/scalp_log.jsonl` as a record. The no-direction rule applies again without exception.

## Where it runs

On the Acer, as the user service `btc-ta-copilot.service` (restarts on failure; linger keeps it up without a login). It listens on `127.0.0.1:8770` and is published tailnet-only by Tailscale Serve:

**https://homelab.example-tailnet.ts.net:11443/**

```sh
acer/deploy.sh          # tests, copy to ~/btc-ta, restart services; requires the existing Serve mapping
acer/deploy.sh status   # dashboard, watcher timer, Serve mapping and a health probe
acer/deploy.sh remove   # stop/disable services and remove Serve mapping (files stay in ~/btc-ta)
ssh acer 'journalctl --user -u btc-ta-copilot -n 50'
ssh acer "systemctl --user list-timers 'btc-ta-*'"
```

## Run locally

```sh
python3 ta_copilot.py            # http://127.0.0.1:8770/
python3 ta_copilot.py --port 8771
```

The Acer uses its system Python `websockets` package (16.1) for public Coinbase and Kraken feeds. If `websockets` is unavailable or a socket drops, REST polling takes over. Local tests use recorded messages and need only the Python standard library. The page loads lightweight-charts 4.2.3 from unpkg with an SRI pin; the dashboard code is served separately as `/app.js` with `Cache-Control: no-store`. Every response has a Content-Security-Policy that allows the pinned chart script, the page's inline stylesheet, the data-URL favicon, and same-origin state requests, plus `nosniff` and `no-referrer` headers.

## What it shows

- **Spot**: Coinbase Exchange BTC-USD WebSocket ticker and matches feed. A REST ticker takes over if the socket is unavailable. Every spot update patches the forming bar on each timeframe.
- **Composite spot proxy**: median of fresh Coinbase, Kraken, Bitstamp and Gemini BTC-USD last trades. A ticker older than 10 s or more than 0.5% from the fresh-venue median is excluded. The Kalshi card shows each price, age, inclusion status and a rolling 60 s mean from contiguous 1 s composite samples. Kraken uses trade time on WebSocket and fetch time on REST; Gemini uses fetch time.
- **Candles**: Coinbase Advanced Trade public market candles (1m/5m/15m/1h, 300 bars each), refreshed every 10–120 s. **10m** has no native granularity, so it's aggregated from 600 five-minute bars (about 2 days). The Exchange `/candles` endpoint lags several minutes, so it isn't used.
- **Kalshi window**: the open KXBTC15M market from the public `/markets` endpoint, polled every 2 s. If that listing lags at rollover, the prefetched next ticker is shown immediately and triggers a direct public `GET /markets/{ticker}` without waiting for the next poll; direct requests for a ticker stay at most once every 2 s. The card says "Target pending" until `floor_strike` arrives. It shows the countdown, YES/NO bid and ask, and distance from the target in the settlement-average proxy model described below.
- **Order flow**: public Coinbase matches and Kraken trades produce cumulative taker buy minus taker sell BTC volume since the current 15-minute open, a trailing 60-second split and imbalance, and large-trade markers. The default marker threshold is `$25,000` notional; `TA_LARGE_TRADE_USD` can change it. A partial-window label appears if earlier trades are unavailable.
- **Kalshi context**: public order-book YES/NO best-bid depth and implied ask depth, a YES mid line, and recent public trades in the current window. The mid is not executable. These GETs run no faster than every 2 seconds.
- **Window in context**: follows the selected timeframe. It shows candle closes over a matching lookback (1m → 1 h, 5m → 4 h, 10m → 8 h, 15m → 12 h, 1h → 2 days), then the 1 s composite path inside the current Kalshi window (shaded, with the time still left shown as space on the right), plus the target across the whole view. Beside it: time left, composite minus the rolling 60-second average, and the high and low since the window opened.
- **Browser delivery**: `/api/stream` pushes state with Server-Sent Events. State is computed once per timeframe per roughly one-second cache lifetime and shared with `/api/state`. At most 12 SSE clients are admitted; additional clients receive HTTP 503 and the page falls back to `/api/state` polling. The watcher still uses `/api/state`.
- **Indicators per timeframe**: EMA 9/21/50/200, RSI 14 (Wilder), MACD 12/26/9, Bollinger 20/2 (%B, width), ATR 14, Stochastic 14/3, anchored VWAP (from the Kalshi window open on 1m; from the first loaded bar otherwise), and fractal swing support/resistance.
- **Tally**: each reading is bull, bear or neutral. The lean is shown only when one side leads by 2 or more. RSI/Bollinger extremes count as neutral ("stretched") rather than as reversal calls. The tally is **unvalidated** and is not a trade signal.
- **Family vote** (2026-10-03): below the legacy count, one vote each for **trend** (EMA stack, EMA 9/21, EMA 200, MACD and VWAP; it votes when 3 or more of the 5 agree), **momentum** (RSI 14 inside 30–70) and, on 1m only, **flow** (60 s taker imbalance at ±0.20 or beyond; the flow tracker resets at each window open, so flow is neutral in the opening record and covers only the window's elapsed seconds during its first minute). It shows the net (bull families minus bear families) and a family lean when |net| ≥ 2. Bollinger %B, Stochastic and stretched RSI appear as muted **context** rows and never count toward families. The legacy count and lean are unchanged, so old and new records compare directly. Under the lean, a track-record line shows how the legacy 1m lean at window open has scored against settlement, with its Wilson 95% interval (`tally_record` in `/api/state`).

- **Live stream** (`/api/stream`, SSE, 1 event/s): a full state on connect, on a Kalshi window change and once a minute; other events carry only the newest 5 points of each series, merged by time in the page (about 17 KB/s per open tab). The page falls back to polling `/api/state` if SSE fails.

## Caveats

- Kalshi settles on the 60-second CF Benchmarks BRTI average. This four-venue composite and its average are proxies, **not BRTI**, and can differ from settlement. The displayed distance in σ is a Brownian settlement-average proxy, not a probability. Before the final minute it compares the current composite with the target and scales the expected future 60 s average using 1m realized σ. During the final minute it combines observed one-second composite samples with the current composite for unobserved or missing seconds; the displayed scale shrinks with the unobserved fraction. The separate rolling 60 s average stays unavailable until it has a full contiguous minute of valid samples.
- The page shows STALE when Coinbase's exchange trade time is more than 10 s old, its last update is more than 20 s old, or the composite is unavailable. A lagging Coinbase stream triggers REST fallback. Kalshi freshness is separate: `window.age` measures time since the last successful public Kalshi fetch carrying the displayed window, from the listing or direct ticker endpoint. `kalshi_stale` becomes true after 10 s without one, except for a five-second grace when a prefetched window is first promoted. A failed GET does not refresh this timestamp; a continued outage at rollover becomes stale when the grace ends. The window card shows the metadata age when it exceeds 10 s, the header badge turns amber when `kalshi_stale` is true, and an expired market is hidden. Socket reconnects use bounded backoff while REST keeps retrying. Order flow is descriptive market data, not a trading recommendation.

## Health alerts and research log

`btc-ta-watch.timer` runs every five minutes as a separate Acer user timer. It checks the dashboard service, `/api/state`, `stale`, and feed errors older than 10 minutes. It sends a `[BTC TA]` Telegram message on a problem, recovery, and every six hours while unhealthy. This deployment does not send an installation test message. The watcher reads the existing Acer Telegram configuration locally and keeps its own state in `~/btc-ta/state/` with private permissions. It does not send a daily alive message.

The dashboard prefetches the next public market and captures composite spot and indicators at its scheduled opening. Kalshi may still show the market as `initialized` then, with no target or usable YES quotes. In that case, the snapshot is held privately in `~/btc-ta/state/tally_pending.json`; after the public target appears, one `open` line is appended to `tally_log.jsonl`. The line distinguishes `captured_at` from `target_observed_at`, and leaves opening YES bid/ask null if no valid quote existed at capture. It includes the per-timeframe tally, RSI, MACD histogram, family votes, `family_net` and `family_lean`, plus the 60 s flow imbalance and the 1m realized σ in dollars (`sigma_1m_usd`). After settlement, a separate `settled` line includes the public `result` and `expiration_value` when present. A restart in the middle of a window does not label a late snapshot as an opening observation. Logging runs in its own thread and has no effect on the dashboard response.

Inside each window, one `minute` line is appended at each whole minute 1–14 (written only within 2 s of `:00`; a restart never backfills missed minutes). It records the ticker, minute, seconds left, composite, 60 s average, target, 1m σ, fresh YES bid and ask with their age (null when stale or missing), the 1m flow imbalance, the stale flags, and every timeframe's legacy tally and family vote. It records no outcomes and makes no Kalshi request of its own. Each row is roughly 1.5 KB, so the log grows by about 2 MB a day.

```sh
ssh acer 'cd ~/btc-ta && python3 tally_report.py'
```

The report is descriptive only, and the dashboard never reads it. Its sections:

- **Legacy:** the original per-timeframe opening-lean hit rates with Wilson 95% intervals, flagging samples under 100.
- **Size of the move:** the Spearman correlation of the opening net score with settlement value − target, with a bootstrap interval.
- **Minute horizon:** the 1m `family_net` against the next 1 and 3 minutes of composite and YES mid, with mean change by net value and a block bootstrap that resamples whole windows.
- **Market:** the legacy 1m lean against the market's side near open. Kalshi usually has no quote at `:00`, so this uses the minute-1 mid.
- **PRIMARY:** one pre-declared test, the Spearman correlation of 1m `family_net` with the next 3-minute composite change, over the first 800 windows with minute rows. It is evaluated only on or after 2026-10-14 00:00 UTC and reports "CI excludes 0" or "CI includes 0".

Every other section is labeled exploratory. With about 1,000 windows the report takes roughly half a minute to run.

## Tests

```sh
python3 -m unittest discover -q -p 'test_*.py'
```

## History

Codex task briefs, reports, last messages and run logs, the 2026-10-02 adversarial QA report (`QA_REPORT_20261002.md`) and the retired scalp experiment live in `history/`. Run logs (`*.log`) are kept on disk but not in git.
