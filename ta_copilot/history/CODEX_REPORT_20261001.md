# BTC TA dashboard streaming and order-flow report — 2026-10-01

## What changed

- Added public Coinbase Exchange `matches` and `ticker` WebSocket consumption and the public Kraken v2 `trade` feed through `websockets` on the Acer. Coinbase match maker side is inverted to taker side; Kraken trade side is already taker side. Reconnects use capped exponential backoff. Coinbase and Kraken REST polling automatically supplies venue prices when their streams are unavailable or stale; the dashboard shows their current price-feed modes and labels Bitstamp and Gemini as REST.
- Added current-window cumulative taker buy minus sell BTC volume, trailing 60-second buy/sell volume and imbalance, and large-trade markers on the 1m candle chart. The marker threshold defaults to $25,000 traded notional and is configurable with `TA_LARGE_TRADE_USD`. The CVD sub-panel shares the chart time range. Flow resets at each UTC 15-minute open, deduplicates trade IDs, and labels partial coverage after a mid-window start or socket gap. Missing trades are not fabricated or backfilled.
- Added `/api/stream` Server-Sent Events. The browser uses SSE and falls back to 2-second `/api/state` polling if the SSE connection fails. `/api/state` remains available with its existing watcher fields, plus the new descriptive data.
- Added public Kalshi `/markets/{ticker}/orderbook` and `/markets/trades` GETs for best YES/NO bid depth, implied ask depth, recent trades in the current window, and an observed YES mid line. Each endpoint is polled no faster than every 2 seconds. The book quote is shown as the primary quote when fresh, with its source and age labeled; the market-list summary is labeled when used as a fallback.
- Added a current-window strip with the observed composite price path against the target, time left, composite minus the contiguous 60-second average, and observed high/low. Partial paths are labeled.
- Kept the wall-clock one-second composite sampler and tally logger. Changed `acer/deploy.sh` to copy the new module/tests, retain the existing Tailscale Serve mapping, and skip the installation Telegram test message.

The public feed formats were checked against the [Coinbase Exchange WebSocket channels](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels), [Kraken v2 trade channel](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/trade), [Kalshi order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook), and [Kalshi trades](https://docs.kalshi.com/api-reference/market/get-trades) documentation.

## Tests and checks

```text
Mac:   python3 -m unittest discover -q -p 'test_*.py'  → Ran 89 tests, OK
Acer:  python3 -m unittest discover -q -p 'test_*.py'  → Ran 89 tests, OK
Mac:   python3 -m py_compile ta_copilot.py stream_flow.py ta_watch.py tally_report.py → passed
Mac:   bash -n acer/deploy.sh → passed
Mac:   node --check (extracted inline JavaScript) → passed
```

The Mac has no `websockets` installation, so socket tests use recorded Coinbase and Kraken messages. The Acer system Python reports `websockets` 16.1. Focused tests cover trade-side normalization, duplicate and window handling, CVD and imbalance, thresholds/markers, public Kalshi book and trade parsing, recorded stream integration, window-path reset, and both HTTP API routes. No test opens a live socket or calls a live venue.

## Deployment output

The final `acer/deploy.sh` invocation exited 0. It ran 89 tests locally and 89 on the Acer, both OK, then reported:

```text
btc-ta-copilot.service  Active: active (running) since Thu 2026-10-01 22:53:51 UTC
btc-ta-watch.timer      Active: active (waiting); Trigger: Thu 2026-10-01 22:55:00 UTC
https://homelab.example-tailnet.ts.net:11443 (tailnet only)
|-- / proxy http://127.0.0.1:8770
ready True stale False composite 84693.405 errors {} frames ['1m', '5m', '10m', '15m', '1h']
```

The service remains bound to `127.0.0.1:8770`. The existing Serve mapping was read, not changed. The deployment did not call the watcher's `--test` message path.

## Tailnet and browser verification

Over `https://homelab.example-tailnet.ts.net:11443` after the final deployment:

- `/api/state?tf=1m`: `ready: true`, `stale: false`, Coinbase and Kraken in `WebSocket` mode, Bitstamp and Gemini in `REST` mode, `errors: {}`; the current window supplied a book, recent trades, YES mid points, price-path points, and flow trades.
- `/api/stream?tf=1m`: two consecutive `event: state` messages arrived at `22:54:11.983584Z` and `22:54:12.535459Z`, both ready and not stale.
- `/`: HTTP 200. The browser rendered the live window strip, CVD and order-flow panel, book depth, recent trades, and YES mid chart. The browser console showed no errors. After the 22:45 UTC rollover, the new `KXBTC15M-26OCT011900-00` window appeared with fresh book quotes and order flow observed from the open.
- The tally log gained an opening record for that window, captured at `22:45:00.050081Z`; the target was observed at `22:45:09.803459Z`. The watcher state reported no active problem.

## Separate required status check

`acer/deploy.sh status` exited 0 after the tailnet check:

```text
btc-ta-copilot.service  Active: active (running) since Thu 2026-10-01 22:53:51 UTC; 3min 1s ago
btc-ta-watch.timer      Active: active (waiting); Trigger: Thu 2026-10-01 23:00:00 UTC
https://homelab.example-tailnet.ts.net:11443 (tailnet only)
|-- / proxy http://127.0.0.1:8770
ready True stale False composite 84736.61 errors {} frames ['1m', '5m', '10m', '15m', '1h']
```

## Remaining limits and rule check

- CVD reflects observed public stream trades. Coinbase documents that matches messages can be dropped; this dashboard does not backfill gaps. A mid-window restart or detected socket gap is labeled as partial coverage. The YES mid is an observed quote statistic, not an executable price. Recent Kalshi trades are a bounded recent view rather than a complete window archive.
- The live browser SSE failure path was not deliberately fault-injected against production. The fallback is present in the client; the recorded-message, HTTP, and live SSE checks passed.
- Reasonix produced the isolated stream-flow files but timed out before returning a completed result. Codex inspected and corrected them, ran the full suite, integrated the dashboard, and verified the deployment independently. A manual student-account handoff could not autonomously implement and deploy this task.

No rule deviations were found. All market calls are public and unauthenticated. No Kalshi credential, account/portfolio route, order action, new Telegram message, `~/btc-study/` or `btc-study-*` change, `../btc-readiness/` change, sudo action, or Tailscale Serve change was made. No new UP/DOWN, buy/sell recommendation, or probability-of-winning output was added.

## Post-review fixes (Claude, after this run)

- **Stream bandwidth.** Every `/api/stream` event carried the full ~160 KB state twice a second, about 197 KB/s per open tab (roughly 700 MB an hour). Now the stream sends one event per second. A full state goes out on connect, on a Kalshi window change, and once a minute as a resync. Other events carry only the last 5 points of each chart and window-path series (`chart_tail` / `paths_tail`), and the page merges them by time. Measured over the tailnet: 17 KB/s, about 60 MB an hour. `/api/state` is unchanged. Covered by the new `StreamTailTests` and a Node check of the client merge, and verified live across a minute boundary: the new bar arrived through a tail event.
- **Chart zoom.** All four panels could lead the time sync. The CVD panel holds only the current window's few minutes, so when its data loaded it shrank the main chart to about 2 bars. Now only the main chart leads, and the sub-panels follow (their own scroll and zoom are disabled).
- **Delegation.** Parts of `stream_flow.py` were first drafted through Reasonix (DeepSeek), as this report says, and the code was sent to that service. The task file didn't forbid it.
