Implemented directly, uncommitted, in [ta_copilot.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py), with eight regressions in [test_audit_fixes_20261027.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/test_audit_fixes_20261027.py).

1. **Trade gap:** `_trade_gap` clears coverage starts; `handle_kalshi_frame` prevents same-frame continuity restoration. Tested registered gapped acknowledgments with FakeSocket and an interleaved context poll.
2. **Cached BRTI:** `cached_state` copies and ages BRTI, rebuilding across the freshness threshold. Tested proxy math, logged BRTI suppression, and unchanged originals.
3. **Post-close display:** `state` exposes only open windows and retains the previous BRTI average for 30 seconds, including before promotion. Tested keyed/keyless boundaries, promotion, cache expiry, and pre-close averages.
4. **Deferred close:** `sample_composite` preserves composite fields and capture time; daemon finalization waits for complete BRTI or close+10 seconds, attempting backfill around close+3. Tested late ticks, successful/unavailable/slow backfill, true incomplete counts, immediate unchanged keyless rows, and deduplication.

Verification: **375 tests passed, one skipped**; all eight new test methods fail against master `13bad9c`. `node --check app.js`, `git diff --check`, and the pinned PRIMARY digest check passed.

`app.js` already renders the previous average; `tally_report.py` accepts the additive field, so both stayed unchanged. No live endpoint testing, delegation, commits, or deployment. `../btc-readiness/` was untouched.

