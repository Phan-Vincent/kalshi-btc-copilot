# BTC TA Copilot (read-only, descriptive)

A live technical-analysis view for the Kalshi KXBTC15M series. It is separate from the Study 005 candidate and the Acer study stack, and it does not change the advice gate: **no credentials, no account access, and no orders.** One bounded exception to the no-direction rule is the experimental, unvalidated, paper-scored YES/NO scalp trigger beside the 1m tally. It is not a probability forecast or validated advice.

## Where it runs

On the Acer, as the user service `btc-ta-copilot.service` (restarts on failure; linger keeps it up without a login). It listens on `127.0.0.1:8770` and is published tailnet-only by Tailscale Serve:

**https://homelab.example-tailnet.ts.net:11443/**

```sh
acer/deploy.sh          # tests, copy to ~/btc-ta, restart services; requires the existing Serve mapping
acer/deploy.sh scalp    # dashboard-only paper scalp deploy; leaves watcher timer untouched
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
- **Experimental paper scalp** (`momentum_v1`): the single bounded directional exception. A 10-contract paper position enters YES when the 1m tally has bull − bear ≥ 3, the 60 s order-flow imbalance is ≥ +0.20, and the composite is above its rolling 60 s average; NO mirrors it (bear − bull ≥ 3, imbalance ≤ −0.20, composite below the average). A missing 60 s average blocks entry. Entry also requires fresh spot and Kalshi data, a book ≤ 5 s old, at least 180 s left, an implied ask from 10¢ to 90¢, a spread ≤ 2¢, depth ≥ 10, flat state, and a 60 s cooldown. Exit precedence: held-side bid ≥ entry ask + 4¢ (take profit), bid ≤ entry ask − 4¢ (stop), a flip (the 1m tally leaning the other way by ≥ 2, or the imbalance crossing ±0.20 against the position; ignored while spot is stale), 300 s held, then ≤ 90 s left. A fired exit without an executable full-size fresh bid is retried each second; only the time stop, a closed window or a rollover sends the position to the public result. Every journal row is tagged with its strategy and the stats count only `momentum_v1`; untagged rows are the earlier mean-reversion version. The trigger runs once per second without an open browser and never submits an order.

- **Live stream** (`/api/stream`, SSE, 1 event/s): a full state on connect, on a Kalshi window change and once a minute; other events carry only the newest 5 points of each series, merged by time in the page (about 17 KB/s per open tab). The page falls back to polling `/api/state` if SSE fails.

## Caveats

- Kalshi settles on the 60-second CF Benchmarks BRTI average. This four-venue composite and its average are proxies, **not BRTI**, and can differ from settlement. The displayed distance in σ is a Brownian settlement-average proxy, not a probability. Before the final minute it compares the current composite with the target and scales the expected future 60 s average using 1m realized σ. During the final minute it combines observed one-second composite samples with the current composite for unobserved or missing seconds; the displayed scale shrinks with the unobserved fraction. The separate rolling 60 s average stays unavailable until it has a full contiguous minute of valid samples.
- The page shows STALE when Coinbase's exchange trade time is more than 10 s old, its last update is more than 20 s old, or the composite is unavailable. A lagging Coinbase stream triggers REST fallback. Kalshi freshness is separate: `window.age` measures time since the last successful public Kalshi fetch carrying the displayed window, from the listing or direct ticker endpoint. `kalshi_stale` becomes true after 10 s without one, except for a five-second grace when a prefetched window is first promoted. A failed GET does not refresh this timestamp; a continued outage at rollover becomes stale when the grace ends. The window card shows the metadata age when it exceeds 10 s, the header badge turns amber when `kalshi_stale` is true, and an expired market is hidden. Socket reconnects use bounded backoff while REST keeps retrying. Order flow is descriptive market data, not a trading recommendation.
- The scalp journal is append-only at `~/btc-ta/state/scalp_log.jsonl` with mode 0600 inside a 0700 directory. It assumes a full 10-contract paper buy at the best implied ask and sell at the best held-side bid; it skips shallow entry books. The [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) gives the general taker formula `0.07 × contracts × price × (1 − price)`, with fee plus position cost rounded up to a centicent ($0.0001). The report also shows a $0.01 rounding sensitivity. Both entry and exit pay taker fees; settlement has no fee. These are paper assumptions, not account fills. A restart aborts an open paper position, counts it, and excludes it from completed-trade statistics. The panel shows realized paper win rate, Wilson 95% interval, mean P&L per contract after fees with a 95% interval, and a warning below 100 trades. The tally's opening lean previously scored 44–51% over 220 windows, with all Wilson intervals spanning 50%; it supplies no evidence that this new scalp rule works.

## Health alerts and research log

`btc-ta-watch.timer` runs every five minutes as a separate Acer user timer. It checks the dashboard service, `/api/state`, `stale`, and feed errors older than 10 minutes. It sends a `[BTC TA]` Telegram message on a problem, recovery, and every six hours while unhealthy. This deployment does not send an installation test message. The watcher reads the existing Acer Telegram configuration locally and keeps its own state in `~/btc-ta/state/` with private permissions. It does not send a daily alive message.

The dashboard prefetches the next public market and captures composite spot and indicators at its scheduled opening. Kalshi may still show the market as `initialized` then, with no target or usable YES quotes. In that case, the snapshot is held privately in `~/btc-ta/state/tally_pending.json`; after the public target appears, one `open` line is appended to `tally_log.jsonl`. The line distinguishes `captured_at` from `target_observed_at`, and leaves opening YES bid/ask null if no valid quote existed at capture. It includes the per-timeframe tally, RSI and MACD histogram. After settlement, a separate `settled` line includes the public `result` and `expiration_value` when present. A restart in the middle of a window does not label a late snapshot as an opening observation. Logging runs in its own thread and has no effect on the dashboard response.

```sh
ssh acer 'cd ~/btc-ta && python3 tally_report.py'
ssh acer 'cd ~/btc-ta && python3 scalp_report.py'
```

The report is descriptive only, with per-timeframe hit rates and Wilson 95% intervals. It flags scored sample sizes under 100 as too small to mean anything. The dashboard never reads it.

## Tests

```sh
python3 -m unittest discover -q -p 'test_*.py'
```
