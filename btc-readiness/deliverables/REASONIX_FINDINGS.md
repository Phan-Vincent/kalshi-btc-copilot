# Independent Reasonix review — findings and adjudication

- **Reviewer:** Reasonix v1.39.3, model `deepseek-v4-pro`. Codex was not used.
- **Setup:** three parallel reviews, each in a disposable, sanitized copy (`review_bundle/`, `ops_bundle/`, now archived at `archive/ops_bundle_stale/`). The copies contain no credentials, keys, raw fills or balances, only source, tests, a public market-data fixture and aggregate fee counts. Reviewers had write access only in their own copy.
- **Completion:** all three reviews finished (exit 0). Earlier Codex attempts had timed out or returned partial results.
- **Cost:** about $0.33 of reported Reasonix model cost across the three runs (per-run metrics in `reasonix_out/*_metrics.json`).
- **Raw transcripts:** `reasonix_out/{risk,fee,ops}_review.txt`.

One process note: my first launch used read-only mode, which blocks running Python, so reviewers couldn't test hypotheses. I restarted all three with write access limited to their scratch copies.

## Risk engine review

| Finding | Reviewer label | Adjudication |
|---|---|---|
| A ledger captured at 23:59:59 PDT but labeled with the next Pacific day was accepted, so the previous day's losses could be omitted (P2) | CONFIRMED | **Accepted and fixed.** The engine now requires the Pacific day of `captured_at` to equal both `day_pacific` and the decision day. A regression test failed before the fix and passes after. |
| The test command fails with a missing `candidate/` path (P3) | CONFIRMED | **Artifact of my review bundle.** I flattened the files when building the bundle, so the paths didn't resolve. The real layout runs fine; the reviewer fixed the path locally and got 38/38 passing. |
| Other attack classes: 200,000 random decimal probes for cap overshoot, attempts to loosen config, DST handling, double-counting pending orders | No defect | Consistent with the local suite. |

## Fee evidence and bound review

| Finding | Reviewer label | Adjudication |
|---|---|---|
| The bound `0.07·p·(1−p) + 0.0101` is correct and strict under the $0.0001 model. It checked all 393,210 compositions for Q ≤ 0.16 at 6 prices, plus 250,000 random buy groupings and 50,000 sell groupings, with no violation. | CONFIRMED | Accepted. It agrees with `test_fee_bound_proposal.py`. |
| The evidence supports $0.0001 for single-fill orders; the account-level conclusion is SUSPECTED | Mixed | **Agreed.** Account precision still needs Kalshi's confirmation, which is question 1 of the draft. |
| "Zero usable multi-fill orders" | SUSPECTED | **Partly superseded.** The reviewer only had the single-fill aggregate. My local analysis (`fee_evidence_deep.py`) found most same-timestamp multi-fill orders matching $0.0001 under some fill ordering, and many showing per-fill rebates. Fill sequence is still ambiguous, so the multi-fill accumulator is supported but not proven. |
| Sell-side hypothesis: the reported fee is the net fee rounded up to the $0.0001 grid | SUSPECTED | **Rejected by local test.** Of the eligible single-fill sells, most match the model exactly and the few mismatches aren't explained by that rounding. It remains an open question for Kalshi (question 6). The gap is ≤ $0.0001 per order, affects sells only, and doesn't affect entry sizing. |
| Before adopting the bound, confirm: account precision, future KXBTC15M fee stability, $0.0001 price grid and 0.01 fill quantum, no unreported adjustments | SUSPECTED | Accepted. All of these are in the Kalshi draft or the activation prerequisites. |

## Operations review

The reviewer ran 54 in-scope tests, then reproduced six defects with scratch scripts. All six were confirmed locally as regression tests that failed before the fixes, then fixed (O4–O9 in `OPERATIONAL_FIXES.md`):

1. **P1:** a sleep spanning a request still halted the store, because staleness was reported as a clock discontinuity.
2. **P1:** `settle_one` still used `time.monotonic()`, so a sleep during a settlement read halted the store.
3. **P2:** a single transient settlement failure killed the run uncleanly.
4. **P3:** a wall-clock step across the release boundary stopped cleanly with no review marker.
5. **P3:** a publication integrity mismatch was retried 40 times instead of halting.
6. **P3 (suspected, confirmed locally):** an external `HALTED` file wasn't immediately fatal.

The reviewer also verified two properties hold: the failure counter resets correctly, and a sleep between the freshness checks degrades rather than halting.

## Status

Every independent finding has been fixed, rejected by test, or recorded as an external dependency, and the full suite passes after all fixes. **This is not an independent sign-off of the final code:** the fixes made after the reviews have been verified by the local suite only, and weren't re-reviewed by Reasonix. A final re-review of the frozen release belongs to the release gate.
