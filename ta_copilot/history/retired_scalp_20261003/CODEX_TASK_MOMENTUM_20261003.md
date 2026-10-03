# Codex task: switch the experimental scalp trigger to momentum rules (2026-10-03)

Work in `ta_copilot/`. Read `README.md`, `SCALP_REPORT_20261003.md`, `scalp_signal.py`, `scalp_report.py`, `ta_copilot.py`, `app.js`, `index.html`, and the tests first. The deployed build (restart 2026-10-03 07:14:20 UTC) runs **mean-reversion** rules; its journal `~/btc-ta/state/scalp_log.jsonl` is empty (0 rows at 07:33 UTC). The user now wants **momentum** rules instead.

## Unchanged (keep exactly)

- The bounded exception: one **experimental, unvalidated, paper-scored** enter/exit trigger for KXBTC15M YES/NO, next to the 1m tally, with the persistent "EXPERIMENTAL · paper · unvalidated" badge. No probability-of-winning output, no other buy/sell labels, Study 005 advice gate untouched.
- **Paper only**: public unauthenticated GETs; no Kalshi credentials, account/portfolio routes, or orders. No Telegram, no watcher changes, no Tailscale/sudo changes; hands off `~/btc-study/`, `btc-study-*`, `../btc-readiness/` (read-only references allowed). Watcher contract (`ready`, `stale`, `errors`) unchanged.
- Eligibility filters (fresh feeds, book age ≤ 5 s, ≥ 180 s left, ask 10–90¢, spread ≤ 2¢, depth ≥ paper size, 60 s cooldown, one position), paper fills (buy at ask, sell at bid, no partials), fee formula and both rounding increments, journal format/permissions, restart `aborted` handling, stats/CIs, `scalp_report.py`, the panel layout. Stdlib only, match the surrounding style. Reasonix may draft; you review and own it.

## Change 1 — momentum rules (named constants; replace the mean-reversion ones)

Inputs: the 1m frame tally (`bull`, `bear`), composite, `average_60s`, 60 s order-flow imbalance, window and book as now.

- **ENTER YES**: 1m tally `bull − bear ≥ 3` **and** 60 s imbalance `≥ +0.20` **and** composite `>` rolling 60 s average.
- **ENTER NO**: `bear − bull ≥ 3` **and** imbalance `≤ −0.20` **and** composite `<` 60 s average.
- If `average_60s` is unavailable, the price leg is not met (no entry).
- **EXIT**, precedence order: `take_profit` — held side's bid `≥` entry ask `+ 4¢`; `stop` — bid `≤` entry ask `− 4¢`; `flip` — the 1m tally leans the other way (opposite side leads by ≥ 2) **or** the imbalance crosses to `≤ −0.20` (held YES) / `≥ +0.20` (held NO); `max_hold` 300 s; `time_stop` at `seconds_left ≤ 90`.
- Panel conditions line shows the three momentum legs with values (e.g. `tally +4 ✓ · flow +0.31 ✓ · above 60s avg ✓`).

## Change 2 — retry exits instead of converting at once

Today, if an exit rule fires on a second when the held side's bid isn't executable (stale, book > 5 s, depth < size), the position is converted straight to hold-to-settlement. Instead: keep the position open and retry the fired exit every second at the first executable bid (re-checking the stop/target against the then-current bid is fine; record the originally fired rule and the retry delay). Fall back to hold-to-settlement only when the time stop is reached with no executable bid, or the window has closed/rolled over. Test both paths.

## Change 3 — tag the strategy

Add `strategy: "momentum_v1"` (a constant) to every journal row, and make the stats, the panel line and `scalp_report.py` count only rows of the current strategy (report other strategies separately if present). The journal is empty now, so there is nothing to migrate; this keeps future rule changes from mixing results.

## Tests, deploy, verify

- Unit tests (no live network) for each momentum leg and its boundary values, the missing-60 s-average case, every exit and its precedence, the exit retry and both settlement fallbacks, strategy tagging and filtering. Remove or rewrite the mean-reversion rule tests; keep all fill/fee/journal/stats tests passing.
- Full suite on the Mac; deploy with the restart landing **T−60…T−15 s** before a `:00/:15/:30/:45` boundary; record `ActiveEnterTimestamp`.
- Live: `/api/state.scalp` updates each second with the momentum conditions; the panel renders with no console errors or CSP violations; the journal keeps its private permissions. Report any live paper trades seen (don't wait for one).

## Deliverable

Append a "Momentum switch 2026-10-03" section to `SCALP_REPORT_20261003.md` (exact rules and constants, the retry change, strategy tag, test counts, restart time, live checks, deviations) and update `README.md`. Last message: one paragraph summary.
