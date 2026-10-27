# Adversarial QA: live BRTI + Kalshi WebSocket feed for a BTC dashboard (2026-10-27)

You are reviewing a change that is already deployed. Find real defects. Do not rewrite the code,
do not comment on style, and do not suggest new features. A short list of verified, concrete
defects beats a long list of maybes.

## What the system is

`ta_copilot.py` is a single-process, stdlib Python dashboard (threads + `ThreadingHTTPServer`,
one `Feed` object guarded by `Feed.lock`) for Kalshi's 15-minute BTC markets (series KXBTC15M).
It is descriptive only: no orders, no probabilities, no direction calls. Kalshi settles each market
on the mean of the 60 one-second CF Benchmarks BRTI values at seconds `close-60 … close-1`.

The change (attached `live_brti_20261027.diff`, against the previous deployed master; full
post-change `ta_copilot.py`, new `kalshi_ws.py`, new tests `test_live_brti_20261027.py`):
- An optional **read-only** Kalshi API key (Ed25519 or RSA-PSS) is loaded from an owner-only
  directory. A scope check (`GET /trade-api/v2/api_keys`) must show only `read`/`read::*` scopes
  before use, re-checked every 6 h. The signer is allowlisted to three GET paths.
- One authenticated WebSocket (`run_kalshi_ws`) subscribes to BRTI (`cfbenchmarks_value` 1 Hz
  and `cfbenchmarks_value_5hz`), plus `orderbook_delta` and `trade` for the current market and,
  from 60 s before its open, the next one. Markets are added/removed with `update_subscription`.
- Order book: local book from snapshot + deltas with per-subscription (`sid`) sequence checks; an
  in-sync book replaces the REST orderbook GET and is read live by `state()`. A gap, stale socket
  or disconnect falls back to REST polling.
- Trades: WebSocket trades merge into the window's tape (deduped by `trade_id`). REST cursor
  paging stops only when its coverage bridges the WebSocket subscription start (or the
  subscription predates the open) and the trade sequence is intact; otherwise REST resumes.
- BRTI per-second values (20 min) with a REST backfill after reconnect. With fresh BRTI (source
  age <= 2 s) the settlement-distance math uses BRTI instead of a 4-venue composite proxy; a BRTI
  settlement average is shown in the final minute and 30 s after close.
- Research log rows gain additive fields (`brti`, `brti_age`, `brti_avg_60s`, `brti_samples`).
  Existing composite-based fields must keep their exact meaning; a pinned PRIMARY digest must
  not change.
- Keyless mode must behave exactly like before.

## Verified live facts (2026-10-27, real key, do not dispute these)

- Kalshi's `ok` acknowledgement of `update_subscription` add/delete_markets carries the next
  `seq` of that subscription (book seq 13495, ok 13496, new market snapshot 13497). The code
  counts it (`handle_kalshi_frame`, before `ws_books.apply`).
- Deleting a market that has just closed returned no error. After close Kalshi sent snapshots
  for the old market on the same sid with contiguous seqs.
- The first trade of a new market arrived 0.88 s after its open, before the dashboard promoted
  that market (it promotes within ~1 s); such trades are held in `ws_early_trades`.
- BRTI frames arrive ~35-55 ms after source time. The 1 Hz channel's own `avg_60s_data` /
  `last_60s_windowed_average_15min` only count ticks the connection saw, so the code computes
  its own average.
- Two live windows: the copilot's BRTI close average equalled Kalshi's `expiration_value` to the
  cent. Rollovers at 05:45 and 06:00 UTC showed no gap, no fallback, and a tape complete from open.

## What to look for (priority order)

1. **Wrong numbers shown as right**: anything that can make the BRTI settlement average,
   `settlement_basis`, the final-minute observed/missing seconds, the close-row `brti_avg_60s`,
   or the tape's `complete_window` / `complete_60s` / `fresh` claim completeness or freshness
   that is not true (e.g. lost updates not detected, stale book shown as fresh, trades missed but
   tape marked complete, BRTI from a previous connection treated as continuous).
2. **Concurrency**: races between `run_kalshi_ws`/`handle_kalshi_frame`, `poll_market_context`,
   `sample_composite`, `poll_market` and `state()`; state mutated outside `self.lock`; objects
   replaced on reconnect (`ws_books`, `ws_subs`) while another thread holds a reference; lock held
   across network I/O.
3. **Fallback and recovery**: cases where the WebSocket path is down or out of sync but REST does
   not resume, or where recovery never returns to the WebSocket (stuck states, resubscribe loops,
   ack timeouts, error frames, a sid replaced by resubscribe while the old sid's frames still
   arrive, rollover with no next market listed).
4. **Credential safety**: any path that signs a non-allowlisted request, uses a key with a write
   scope, or leaks the Key ID, signature, headers or key bytes into `/api/state`, errors, logs or
   exceptions.
5. **Keyless parity and log schema**: any change in keyless behaviour or in existing logged fields.

## Output format

For each finding:
- **Severity**: high (wrong value shown/logged as correct, credential issue, stuck feed),
  medium (degraded but visible), low.
- **Where**: file and function (line numbers if you can).
- **Failure scenario**: concrete sequence of frames/events/timing and the resulting wrong state.
- **Evidence**: quote the few lines of code involved.
- **Confidence**: certain / likely / possible.
- **Minimal fix** in one or two sentences.

Then a short list of things you checked and found correct. If you find nothing at high severity,
say so plainly.
