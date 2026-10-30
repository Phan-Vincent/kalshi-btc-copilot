# Codex brief — ta_copilot hygiene + display batch, 2026-10-29

Work in `/Users/you/Documents/kalshi-btc-readiness/ta_copilot` on branch
`codex-improvements-20261029` (already checked out). Do not commit; Claude reviews the diff.
No network. Don't deploy or touch the Acer.

House rules (unchanged): this is a **display-only descriptive dashboard**. No signals,
no UP/DOWN calls, no paper trading, no automation. Do not change the meaning of any
existing log field, do not weaken 60/60 BRTI completeness, and **do not change PRIMARY**
(`tally_report.py` PRIMARY selection, statistic, seed, bootstrap or output).
Match the surrounding code style and comment density. Put new tests in
`test_improvements_20261029.py`.

## 1. Prune deleted Kalshi books (audit item 5, `history/codex_audit_eval_20261027.md` §5)

`Subscriptions.ack()` (`kalshi_ws.py` ~line 258) removes tickers from `self.tickers[sid]` on an
`ok` for `update_subscription` / `delete_markets`, but `BookStream.books` and `BookStream.synced`
keep the `(sid, ticker)` entries forever.

- When that ack is processed in the feed (find where `ack()` is called in `ta_copilot.py`),
  drop `(sid, t)` for each deleted ticker from the book stream (add a small
  `BookStream.drop(sid, tickers)` method). **Keep `seq[sid]` intact.**
- A late `orderbook_snapshot`/`orderbook_delta` for a ticker no longer subscribed on that sid must
  still advance `seq[sid]` (no false sequence gap, no resync) but must not re-create the book.
  Check how `apply()` currently handles a delta for an unsynced key and make sure the late-frame
  path doesn't trigger a resubscribe/gap. If `BookStream` can't see subscription state, pass
  what it needs in explicitly — keep it simple.
- Use the reproduction in the audit (§5) as the base of a test; add a late-snapshot-after-delete
  test and a test that the remaining ticker's book on the same sid is untouched.

## 2. Bound `Feed.captured_tickers` (`ta_copilot.py` ~line 830, 1453, 1474)

It grows by ~96 tickers/day forever. Replace with a bounded structure (e.g. keep the most recent
~200 tickers, insertion-ordered dict or set + deque). The dedup semantics for any ticker still in
the current/next windows must be identical. Test: after inserting > cap tickers, the latest ones
still dedupe and the size stays ≤ cap.

## 3. Validate `brti_finalized_at` (`tally_report.py` line 60)

`_finalize_close` writes `brti_finalized_at` (ISO timestamp) on keyed close rows, but
`TIME_FIELDS` doesn't include it. Add it.

Because `tally_report.py` is the PRIMARY file: before editing, run
`python3 tally_report.py state/tally_log.jsonl` (and any PRIMARY-only flag it has) and save the
output to the scratch area outside the repo (or `/tmp` inside your sandbox); after all edits
to `tally_report.py` in this batch, run again and **diff — the PRIMARY section must be
byte-identical**. Report the diff result. (The manifest's `code_sha256` changes by design; that is
expected and fine.) Add a test that a close row with a malformed `brti_finalized_at` is reported by
`record_problem()` and a valid one is not.

## 4. Show `brti_error` in the Recent settled windows table

Backend already computes `brti_error` (`ta_copilot.py` ~2554, tested in
`test_render_20261027.py`). In `app.js` `renderRecentWindows()` and the header in `index.html`
(line ~167), add a **BRTI error** column next to Proxy error, rendered with `sgn()` or `—` when
null. Update the empty-state `colspan` (currently 7) and the settlement-status `colspan` logic if
needed. If `test_render_20261027.py` or another test checks the header/column count, update it.
Keep the table readable at phone width (check existing CSS for the table; don't add new libraries).

## 5. Exploratory: family-lean opening hit rate (`tally_report.py`)

The opening hit-rate section (`legacy_section`, ~line 403) scores the legacy lean only. Add an
**exploratory** table beside it for the 1m `family_net` lean at open (family_net > 0 → up,
< 0 → down, 0 → no lean, counted separately), vs settlement result, using the existing
`wilson95()`. Label it clearly as exploratory/not pre-declared. Do not touch PRIMARY code paths,
and the PRIMARY output diff in item 3 must still be byte-identical. Add a small test on synthetic
rows.

## 6. Measure only: per-second `state()` cost

Don't change code for this. Using a ready test feed (see the harnesses in existing tests, e.g.
`ready_feed` / `APP_HARNESS`), time `FEED.state(tf)` for each timeframe and the shared 1 s state
build with 300 bars, and report median ms per call and which part dominates (indicators vs
`sr_levels.levels()`). Put the numbers in your final message only.

## Out of scope

Live-BRTI σ calibration cohort (needs a new logged field), splitting `ta_copilot.py`, and the
shared test-helpers refactor.

## Done means

- `python3 -m unittest discover -p 'test_*.py'` passes; report the count before and after.
- `node --check app.js` passes.
- Final message: per-item summary with file:line, the PRIMARY diff result, the state() timings,
  and anything you were unsure about.
