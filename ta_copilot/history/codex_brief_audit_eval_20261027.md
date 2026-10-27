# Evaluate a third-party audit of the TA copilot (read-only)

You are in `~/Documents/kalshi-btc-readiness/ta_copilot` (git, branch master, clean tree at
13bad9c). Do NOT edit any file in the repo. You may run read-only commands and throwaway
Python reproductions (`PYTHONDONTWRITEBYTECODE=1 python3 - <<'EOF' ... EOF`) that import the
modules and drive `Feed` / `handle_kalshi_frame` / `state()` / `cached_state()` with synthetic
frames. The existing tests are `python3 -m unittest discover -p 'test_*.py'` (367 pass).

## System

`ta_copilot.py` is a stdlib, threaded, descriptive-only dashboard for Kalshi's 15-minute BTC
markets (KXBTC15M): no orders, no probabilities, no direction calls. `kalshi_ws.py` holds the
read-only Kalshi WebSocket helpers (BRTI, `orderbook_delta`, `trade`, `Subscriptions`,
`BookStream`). `app.js` is the browser client (SSE from `/api/stream`, `tick()` every 250 ms).
Kalshi settles on the mean of the 60 one-second BRTI values at close-60 … close-1.

Verified live facts (do not dispute): Kalshi's `ok` ack of `update_subscription` consumes the
next `seq` on that sid; after a market closes Kalshi keeps sending snapshots for it on the same
sid with contiguous seqs; the first trade of a new market can arrive before the dashboard
promotes it (held in `ws_early_trades`); four live windows matched `expiration_value` to the
cent; a full multi-hour run showed no gaps or fallbacks.

## The audit to evaluate (written by another agent; treat as claims, not facts)

| Pri | Claim | Files |
|---|---|---|
| P0 | Post-close 30 s window flicker between server and client | ta_copilot.py, app.js |
| P0 | WebSocket trade-gap state corruption on control `ok` frames | ta_copilot.py, kalshi_ws.py |
| P1 | `brti` and `previous_settlement_average` not aged inside `cached_state()` | ta_copilot.py |
| P1 | Opportunistic BRTI backfill when settlement-minute seconds are missing | ta_copilot.py, kalshi_ws.py |
| P1 | Prune `BookStream` deleted markets and `captured_tickers` | kalshi_ws.py, ta_copilot.py |
| P2 | Final-60 s settlement progress bar and 5-level book ladder | index.html, app.js |
| P2 | Family-vote opening hit rate and BRTI-distance calibration in the report | tally_report.py, ta_copilot.py |

Details the audit gave:
1. **Flicker** (ta_copilot.py ~1836–1842 vs app.js ~342–349): with a key configured, after
   close and before the next market is promoted, `Feed.state()` keeps `state["window"]` for 30 s
   past `close_time` (`seconds_left = 0`) so `window["settlement_average"]` stays attached. The
   client's `tick()` sees `left === 0`, sets `window: null` and `kalshi_stale: true`, and each SSE
   push brings the window back, so the Kalshi card and badge flicker every second. Proposed fix:
   route post-close averages only through `previous_settlement_average`.
2. **Control-frame gap** (ta_copilot.py ~971–1002): if an `update_subscription` `ok` arrives
   with `seq != prev + 1`, line ~975 calls `_trade_gap(sid)` (clearing `ws_trade_contiguous`), but
   lines ~997–1002 (`if ack == "update_subscription" and kind == "ok"`) re-add the tickers to
   `ws_trade_contiguous`. Also `_trade_gap()` and `_ws_resubscribe()` never clear
   `ws_trade_start`, leaving pre-gap start timestamps (`start <= opened`) after a mid-window gap.
3. **Frozen BRTI age** (ta_copilot.py ~2590–2636): within each 1 s cache bucket,
   `cached_state()` advances `spot_age`, `window["age"]`, `orderbook_age` by elapsed but leaves
   `state["brti"]["age"]` / `["fresh"]` untouched and mutates `state["brti"]` by reference.
4. **One-shot backfill** (ta_copilot.py ~901–915, ~1138): `backfill_brti()` only runs when the
   socket connects. A single dropped 1 s tick in the closing minute while the socket stays up
   makes `_close_proxy()` log `brti_samples < 60`, `brti_avg_60s = None`, although
   `/trade-api/v2/cfbenchmarks/values` could fill it.
5. **Unbounded growth** (kalshi_ws.py ~165, ~224; ta_copilot.py ~818): `Subscriptions.ack()`
   drops old tickers on `delete_markets`, but `BookStream.books` / `.synced` keep
   `(sid, old_ticker)` forever unless a gap invalidates the sid; `captured_tickers` grows too.

## What to do

For each of the five correctness/hygiene claims:
- **Verdict**: confirmed / partly true / false / already handled. Quote the actual lines (with
  current line numbers) that prove it.
- **Reproduction**: where feasible, a throwaway script that drives the real code and shows the
  wrong state (print before/after). Say plainly if you could not reproduce it and why. For the
  flicker claim, reason through `app.js` concretely (what `tick()` and the SSE handler do to the
  DOM each 250 ms / 1 s in that 30 s span); you cannot run a browser.
- **Real-world impact** given the live facts: how often it would actually happen, and whether a
  wrong number would be shown or logged as correct (that is the severity bar that matters).
- **Priority**: is the audit's P0/P1 right? Give your own.
- **Minimal fix** in one or two sentences, and the regression test you would add.
- Flag anything the audit's proposed fix would break (keyless parity, the pinned PRIMARY digest,
  existing log fields, the close-row `brti_avg_60s` semantics).

For the two P2 ideas: one short paragraph each on value vs cost and whether they fit the
descriptive-only rule. Do not design them.

Finish with: (a) a ranked list of what is worth fixing now, (b) anything the audit missed that you
noticed while verifying (only concrete, evidenced defects), (c) things you checked and found
correct. Be terse; a short evidenced answer beats a long one.
