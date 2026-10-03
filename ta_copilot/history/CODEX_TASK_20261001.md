# Codex task: streaming and order flow for the live TA dashboard (2026-10-01)

You are working on the BTC TA dashboard in this folder (`ta_copilot/`). It is deployed on the Acer (`ssh acer`, host homelab) with `acer/deploy.sh` and published at https://homelab.example-tailnet.ts.net:11443. Read `README.md`, `CODEX_REPORT_20260930.md` and all the code first.

**Goal:** make the dashboard truly real-time and add order-flow context for the current 15-minute window.

## 1. Streaming instead of polling

- Use public WebSocket feeds for Coinbase (matches/ticker) and Kraken (trade), through the `websockets` library already installed on the Acer's system Python (16.1). Check it's available on the Mac for tests too. If it isn't, tests must not require it; use recorded messages.
- Keep REST polling as an automatic fallback if a socket drops, with reconnect and backoff. Show which mode each venue is in.
- Push updates to the browser with Server-Sent Events (`/api/stream`) instead of the 2-second fetch loop. Keep `/api/state` unchanged for the watcher and tests. The page must fall back to polling if SSE fails.

## 2. Order flow

- From the trade stream, compute:
  - cumulative volume delta (taker buy minus sell volume), anchored at the window open;
  - the 1-minute taker buy/sell imbalance;
  - a large-trade marker on the 1m chart (threshold configurable, with a sensible default).
- Add CVD as a sub-panel, synced with the existing charts.

## 3. Kalshi market context

Use public REST only: `/markets/{ticker}/orderbook` and `/markets/trades`.
- Show top-of-book depth for YES and NO, and recent Kalshi trades for the current window.
- Add the YES mid over the window as a small line chart.
- Respect rate limits: poll no faster than every 2 s.

## 4. Window-focused view

Add a "this window" strip showing:
- the open-to-now price path against the target;
- time left;
- composite vs. the 60 s average;
- the high and low since open.

## Hard rules

- **Data:** public, unauthenticated data only. No Kalshi credentials, account or portfolio routes, or orders.
- **No signals:** no UP/DOWN, buy/sell or probability-of-winning output anywhere. Order flow is shown as data, not a signal.
- **Hands off the study:** do not modify `~/btc-study/` on the Acer, the `btc-study-*` units, or anything under `../btc-readiness/`.
- **Server and watcher:** keep the server on loopback. Keep the Telegram watcher working (`stale` must still trip it), and send no new Telegram messages. Keep the tally logger working.
- **Existing fixes:** keep the composite sampler aligned to wall-clock seconds; don't regress that fix.
- **Dependencies:** none beyond the Python standard library and `websockets`. No sudo, and no Tailscale Serve changes.
- **Tests:** add tests for each new piece. Use recorded socket messages, not live sockets. All existing tests must still pass.

## Finish

1. Deploy with `acer/deploy.sh`.
2. Verify over the tailnet URL that `/api/state` is healthy, `/api/stream` emits events and the page renders.
3. Run `acer/deploy.sh status`.
4. Write `CODEX_REPORT_20261001.md`: what changed, test results, deploy/status output, anything left undone, and any rule deviations (there should be none).
