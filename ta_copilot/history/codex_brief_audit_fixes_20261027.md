# Fix four verified defects in the TA copilot (with regression tests)

Repo: `~/Documents/kalshi-btc-readiness/ta_copilot`, branch `ta-audit-fixes-20261027` (from
master 13bad9c). Stdlib Python only, no new dependencies. Match the surrounding style and comment
density. Do not commit; the supervisor reviews and commits. Do the work yourself: do not delegate
to Reasonix or any other worker or MCP tool. Never touch `../btc-readiness/`.

Read first: `history/codex_audit_eval_20261027.md` (your own earlier read-only evaluation, with
reproductions) — the four items below are its ranked list. The system is a descriptive-only
dashboard (no orders, no probabilities); Kalshi settles on the mean of the 60 one-second BRTI
values at close-60 … close-1. Existing log fields must keep their exact meaning, the pinned
PRIMARY digest must not change, and keyless mode (no Kalshi key) must behave exactly as before.

## 1. Trade-gap race (handle_kalshi_frame)

A gapped `update_subscription` `ok` on the trade sid runs `_trade_gap(sid)` and then the `ok`
branch re-adds the sid's tickers to `ws_trade_contiguous` (with `setdefault`, keeping a pre-gap
`ws_trade_start`). The lock is released before `_ws_resubscribe`, so `poll_market_context` can
interleave and adopt the gapped tape as complete (`tape_source="websocket"`,
`complete_window=True`), and resubscription does not undo that.

Fix: once a gap is detected in a frame, nothing in the same frame may restore continuity; `_trade_gap`
also drops `ws_trade_start` so coverage needs a fresh `subscribed` ack (or a REST bridge). Keep
ordinary contiguous `ok` seq consumption and the rollover behaviour (`test_update_ack_seq_is_not_a_gap`).
Tests: registered update ack with a seq gap, with a FakeSocket, plus a `poll_market_context` run
injected between the locked section and `_ws_resubscribe` (e.g. by patching `_ws_resubscribe`
to call it first) — the tape must not be marked complete/websocket afterwards.

## 2. Cached BRTI freshness (cached_state)

Within a 1 s cache bucket `cached_state()` ages `spot_age`, `window["age"]`, `orderbook_age` but not
`state["brti"]`; a cached response can report BRTI 2.7 s old as `fresh` and keep a BRTI-based
`settlement_basis`/distance, and `minute_record` logs it as fresh (line ~2044).

Fix: never serve a cached state whose BRTI freshness or derived window math would differ from a
rebuild. Simplest acceptable approach: copy `state["brti"]` and age it; if it was fresh and the aged
age exceeds the 2 s threshold, rebuild with `FEED.state(tf)` instead of serving the cache. Don't
mutate the cached original. Tests: two same-bucket calls straddling age 2 s give `fresh=False`,
proxy basis and `minute_record(...)["brti"] is None`; the cached original is unchanged.

## 3. Post-close window (state, cached_state, app.js)

With a key configured, `state()` keeps `window` for 30 s after `close_time` (lines ~1836–1839) so its
`settlement_average` stays visible; the client `tick()` blanks a window with 0 s left, so the card
flickers with every SSE push until the next market is promoted (normally ~1 s, up to 30 s).

Fix: `window` is only ever an open market (`open <= now < close`), keyed or keyless. The just-closed
market's BRTI average is exposed through `previous_settlement_average` for 30 s after its close,
whether that market is still `self.market` (not yet promoted past) or `self.previous_market`. Remove
the now-dead 30 s grace from `cached_state()`. Check `app.js` still renders
`previous_settlement_average` in the no-window branch, and that the final-minute
`window["settlement_average"]` is unchanged before close. Tests: keyed and keyless at close-0.1,
close, close+5 before promotion, after promotion, close+30.

## 4. Deferred close row (sample_composite / _close_proxy / TallyLogger)

The close row is built once at the first sample after the boundary (~50 ms after close) and
`accept_close` drops later rows for that ticker. If the close-1 BRTI second arrives late, or one
second was dropped on both BRTI channels, the row permanently records `brti_samples < 60`,
`brti_avg_60s = None`, although the value arrives moments later or `/trade-api/v2/cfbenchmarks/values`
(`Feed.backfill_brti`) could fill it.

Fix: keep computing the composite part (`proxy_avg_60s`, `samples`) and `captured_at` exactly as now.
With a Kalshi key configured, hold the BRTI part: finalize when all 60 seconds are present, or at a
deadline (about boundary+10 s), first trying one `backfill_brti()` if seconds are still missing a few
seconds after close. The backfill does network I/O: never under `self.lock`, and not on the sampler
or WebSocket receive thread (a short daemon thread or the tally worker is fine). Then submit one row.
Additive field allowed: `brti_finalized_at` (ISO). `brti_avg_60s` stays non-null only with all 60
exact seconds. Keyless: submit immediately as today. Update `tally_report.py` validation if it
rejects the new field. Tests: late close-1 tick arriving at close+0.5 → 60/60; a missing second
filled by a (fake) backfill → 60/60; nothing arrives → row at deadline with the true count and
None; keyless row unchanged and immediate; one row per ticker.

## Done means

- `python3 -m unittest discover -p 'test_*.py'` passes (currently 367), new tests in
  `test_audit_fixes_20261027.py`, and each new test fails on master (say which ones you checked).
- `node --check app.js` if node exists.
- Final message: per item, what changed (files/functions) and the tests; anything you were unsure
  of or deliberately did not change.
