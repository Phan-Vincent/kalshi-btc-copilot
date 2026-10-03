# BTC copilot isolated remediation — September 27, 2026

## Executive summary

**QA BLOCKED for production trading readiness.** The authorized remediation is complete in `outputs/copilot_remediation_candidate`, an offline candidate based on study 002 release 4.2. Thirteen shared implementation defects were addressed there; six original-only defects are avoided by retaining the safer study 002 design. Candidate documentation addresses the remaining documentation finding. Original production and the sealed release were not repaired, deployed, restarted, or modified.

The candidate passes its tested correctness boundaries: 109 Python methods passed, one original-journal method was skipped with replacement coverage, 24 actual-HTML checks passed, and all 14 golden scenarios passed. All four candidate suite processes exited 0 after the final source edits. A separate baseline rerun still reproduces 29 failing Python methods and four failing dashboard checks; passing existing tests do not imply trustworthy production behavior.

A new P1 finding was discovered during remediation: unread trade pagination can support a setup. Both baselines reproduce shadow UP with incomplete trade evidence; the candidate blocks every strategy. The earlier audit now contains 20 findings. Read [QA_REPORT.md](QA_REPORT.md) for the complete production audit, architecture map, severity definitions, reproduction details and uncertainties.

This remains deterministic Kalshi BTC 15-minute binary-contract research. Public recommendations are NO TRADE, all position advice is unavailable, and no leveraged BTC risk engine was added. No new study, calibrated edge or trading approval is implied.

## Scope and safeguards

- Candidate model identity: `5.0.0-offline-QA-candidate` plus source/settings fingerprint. Its manifest explicitly says OFFLINE_QA_ONLY_NOT_APPROVED. The inherited study 002 protocol is retained for synthetic compatibility tests; its study ID/dates are reference inputs, not authorization for this candidate.
- `btc_copilot.py` refuses collector launch unconditionally before client initialization. `launch_guard` also rejects QA_ONLY before approval checks. No activation approval, credentials, state, runtime directory or database was copied into the candidate.
- Study 002 version/protocol binding continues to reject reuse of a database with a different implementation. No migration was attempted. Original journal writes are absent from the study 002 collection design.
- Tests use copied public fixtures, synthetic market inputs and temporary databases/files. HTTP clients are mocked; the adversarial and added regression harnesses also block socket connection creation. The CLI refusal test reaches no network initialization. No production collector was invoked.
- Final verification compared the public-source hash inventory, sealed manifest digest and sealed file hashes. All matched. The study 002 runtime remained absent. This comparison does not claim continuously changing original runtime state stayed byte-identical; that state was not changed by QA.

## Test results

Run from the Investing workspace:

```sh
python3 -B qa_btc_2026-09-27/run_remediation.py
```

The candidate runner refuses source/manifest mismatches and returns nonzero for any candidate suite failure, golden mismatch, UI failure or production-source/seal mismatch. It does not re-seal source automatically.

| Candidate suite | Run | Passed | Failed | Skipped | Exit |
|---|---:|---:|---:|---:|---:|
| Existing study 002 plus lifecycle tests | 51 | 51 | 0 | 0 | 0 |
| Adapted independent adversarial methods | 40 | 39 | 0 | 1 | 0 |
| Newly added remediation methods | 19 | 19 | 0 | 0 | 0 |
| Actual candidate HTML checks | 24 | 24 | 0 | 0 | 0 |
| Golden scenarios | 14 | 14 | 0 | 0 | included in adversarial process |

The honest replay test also alters every one of its 31 compared fields individually. Malformed versions/clocks reject before comparison; other alterations produce mismatch results. Fee/depth boundary tests independently recompute costs. The UI harness executes JavaScript extracted from the candidate HTML with a synthetic DOM and controlled wall/monotonic clocks; this is not a visual browser inspection.

Test artifacts: [final results](remediation_test_results.json), [19 added tests](test_remediation.py), [adversarial harness](remediation_audit.py), [UI harness](remediation_ui.cjs), [golden results](candidate_golden.json), [baseline partial-window reproduction](partial_window_baseline.json), [baseline suite results](test_results.json), [reviewable code patch](remediation.patch).

The baseline runner's exit 0 means only that it completed; its child exits preserve the expected failing audit results. Original existing tests remain 34/34, earlier audit regressions 10/10, study/lifecycle 51/51 and supervision 34/34. Independent baseline adversarial totals remain 50 passed, 29 failed and one skipped, plus eight passed/four failed actual-HTML checks.

### Test adaptations and limitations

The original failure harness remains unchanged. A separate candidate harness uses the same safety assertions with these explicit adaptations:

1. Healthy prior-state fixtures now provide validity, faults and source timestamp. Missing health is an invalid prior, not fabricated healthy evidence.
2. Duplicate book/trade tests accept explicit rejection, as their original expectation permits; legacy harnesses expected only fault marking or deduplication.
3. The fee-target assertion calls the new production helper rather than re-executing the known broken expression.
4. The original-journal crash test is inapplicable to the candidate. A new database trigger forces failure halfway through settlement finalization; the outcome insertion and result update roll back together. Retry commits one result/event, and a duplicate retry does not repeat it. An eventual new official recheck is a legitimate audit event.
5. Golden synthetic scenarios now declare an empty, complete trade window instead of inheriting an unrelated real fixture's 1,000-trade pagination cursor. Stale/missing inputs must disable every arm; chop and almost-qualifying inputs must disable the full strategy. Research ablation arms deliberately remove filters and are not public advice. The former overly strict golden expectation that every ablation must reject chop is preserved in baseline evidence, not reclassified as a fixed bug.
6. Golden second-observation expectations are declared before the run and asserted, so a permanently abstaining detector cannot pass the directional cases.

## Findings and remediation status

The detailed exact failures, original components, reproduction methods, expected/actual results and root causes are in [QA_REPORT.md](QA_REPORT.md). This table maps every finding to the candidate change and regression status. No row means a production fix was applied.

| Finding | Candidate component | Failure and root cause | Candidate behavior / fix | Regression status |
|---|---|---|---|---|
| F01 P0 | evidence delayed execution | Pre-deadline request used as delayed fill | Inherited study 002 request-start deadline guard; first eligible request only | Exact deadline, pre-deadline and late-window tests pass |
| F02 P1 | collection and clock gates | Future ticks/trades admitted by original tolerance | Inherited zero future tolerance; typed trade timestamps/time windows further validated | Future tick/trade and clock cases pass |
| F03 P1 | evidence summary | Pending fills excluded from exposure/eligibility | Inherited completeness and pending worst-case exposure accounting | Pending exposure and empty/missing-day acceptance tests pass |
| F04 P1 | evidence reconciliation | Oldest three unresolved contracts starve backlog; reconciliation tied to success | Inherited fair 64-item reconciliation and background failure-safe path | Backlog/amendment/background tests pass |
| F05 P2 | evidence signals | Execution evidence replaces originating evidence | Inherited distinct immutable origin/execution payloads | Retained probabilities .9 versus .8 independently verified |
| F06 P1 | `btc_copilot.py` ticks/structure | Count-only coverage and unrestricted nearest sample label incomplete input 1h | Require at least 3,000 samples spanning 3,599 seconds, per-second integer timestamps and lookback sample within one second; missing gaps retain operational veto | Short hour/subsecond/missing/stale and timeframe tests pass |
| F07 P1 | collection, record, evidence candidates | Unhealthy/unknown prior setup acts as confirmation | Persist validity/faults/source epoch; require healthy same-market prior, at least five seconds elapsed and a newer BTC source sample | Bad/unknown/future/same-source prior and persisted-health tests pass |
| F08 P1 | `kalshi_readonly.py` book normalization | Duplicate aggregated prices manufacture liquidity | Reject repeated levels and invalid price/quantity schema | Duplicate .6+.6 and depth tests pass |
| F09 P1 | `btc_copilot.py` trades | Negative, nonfinite and duplicate counts distort flow | Validate unique nonempty string ID, market identity, positive quantity, aware time window and complementary bounded prices before aggregation | Duplicate, negative, NaN, future/old/ticker/price boundary tests pass |
| F10 P2 | `btc_copilot.py` candles | Unvalidated auxiliary values imply observed closed candles | Check minute ordering/duplicates/window/volume/OHLC; quarantine forming candle; preserve explicit all-null no-trade prices | Forming, future, unordered, duplicate, bad/null OHLC tests pass |
| F11 P2 | required-response gate | Error marker ignored if payload exists | Reject error at response/body/nested data level for every required source | Upstream payload-error test and all nine required-source tests pass |
| F12 P2 | research numerical helpers | Zero/negative/nonfinite quantity or multiplier accepted | Reject invalid requested quantity, fee multiplier, rows and duplicate prices | Zero/negative/extreme/NaN/duplicate helper tests pass |
| F13 P1 | `btc_copilot.py` grid | Infinite start/step loops never reach accepted-point cap | Validate finite 0..1 bounds/positive step/order; bound iterations and resolution; reject excessive grid | Subprocess hang regression plus finite/cap/bounds tests pass |
| F14 P2 | research replay | Constant public abstention hides forged shadow result | Reproduce separate collection/analysis/completion times and compare 31 deterministic market/model/quote/candidate/gate fields; list mismatches | Honest replay, delayed completion and every compared-field tamper pass |
| F15 P2 | dashboard | Missing/NaN epoch treated as fresh | Strict clocks, <=20-second TTL, mandatory valid close, public shadow schema, monotonic receipt timeout/skew; malformed/disconnected output suppresses view | 24 HTML checks pass, including valid render and outage |
| F16 P2 | evidence settlement | Original journal append precedes state commit; crash duplicates finalization | Inherited transactional database outcome/result updates instead of original journal path | Original-only test skipped; new injected-crash rollback/retry test and amendment tests pass |
| F17 P2 | `btc_copilot.py` atomic | Predictable .tmp follows links, no durability sync | Unique exclusive same-directory temp, 0600, file/directory fsync, atomic replace, linked destination rejection and exception cleanup | Temp sentinel, destination symlink/hardlink, failed replace and fsync tests pass |
| F18 P3 | candidate README/manifest | Stale sealed README conflates future approval and activation | Candidate clearly states upstream future evidence approval, own offline status and no inherited authority | Document/source inspection; sealed README deliberately untouched |
| F19 P2 | `btc_copilot.py` target | Exit fee evaluated at entry price | Check each target's actual conservative exit fee against net reserve | .15 entry/.03 reserve independent cost assertion passes |
| F20 P1 | `btc_copilot.py` completeness gates | Unread trade cursor only warns, allowing seven arms to qualify | Mark partial trade window a data-quality fault/common veto before selection; missing trade/candle keys reject | Baseline failure recorded; all-arm partial-window and missing-key regressions pass |

These target conservative estimated entry/exit economics, not guaranteed execution. Position-dependent account guidance remains unavailable and is outside deterministic replay comparison. The inherited fee reconciliation and pacing suites remain green; no current account fee/rate attestation was renewed.

## Golden replay results

Every public result must be NO TRADE. First full-strategy observations abstain pending confirmation. These are synthetic cases, not labels fitted to real holdout outcomes.

| Scenario | Expected second shadow result | Actual |
|---|---|---|
| Obvious LONG | UP | UP |
| Obvious SHORT | DOWN | DOWN |
| Clean breakout, no qualifying retest | NO TRADE | NO TRADE |
| Failed breakout | NO TRADE | NO TRADE |
| Successful retest | UP | UP |
| Failed reclaim | DOWN | DOWN |
| Strong downtrend, no qualifying bounce/reclaim | NO TRADE | NO TRADE |
| Strong uptrend with qualifying structure | UP | UP |
| Sideways chop | NO TRADE | NO TRADE |
| Conflicting timeframes | NO TRADE | NO TRADE |
| Stale data | Rejected/data unavailable | ReadError |
| Missing data | Rejected/data unavailable | ReadError |
| Extreme volatility | NO TRADE | NO TRADE |
| Almost qualifies | NO TRADE | NO TRADE |

## Trading-logic validation

| Requirement | Verified scope |
|---|---|
| Data freshness | Synthetic stale/future/incomplete responses reject or veto; retrieval skew/budget and source timestamps retained. No new live ingestion or uptime certification. |
| Candle closure | Partial BRTI minute excluded; current forming auxiliary minute omitted from published closed candles. |
| Timeframe alignment | Deterministic 1m/3m/5m/15m/1h tick features tested; sufficient hour coverage required. No separate 4h/15m OHLC feature engine was added. |
| Look-ahead | Inherited study 002 request-start delayed-entry guard verified; future source data rejected; historical evidence remains synthetic and frozen holdouts unopened. |
| LONG / SHORT | UP/DOWN shadow full-strategy fixtures and second observations verified. These are binary outcomes, not leveraged positions. |
| NO TRADE | Public gate unchanged; stale/missing/partial inputs cannot qualify any arm; near-qualifying/chop/conflicting full strategy abstains. |
| Stop math | No dollar stop-loss/stop-order engine exists; structural invalidation is experimental and explicitly not exit advice. Not verified as a leveraged risk engine. |
| Target math | Disabled entry branch now meets conservative net-fee reserve; held-position branch retains conservative maximum-fee estimate. No executable take-profit order or fill guarantee. |
| Leverage math | Unsupported; $100 collateral/5x/$500 notional case cannot be assessed by this binary monitor. |
| Position sizing | Hypothetical binary depth/cost previews and input bounds verified; no portfolio risk budget/liquidation sizing exists. |
| Averaging / scale-in | Synthetic fill inventory/basis math verified in both directions; all ADD/management advice still disabled. No new scale-in plan or martingale feature. |
| State isolation | Prior position does not change deterministic market model; invalid prior cannot confirm; healthy setup history intentionally controls confirmation. No multi-store transaction or complete restart certification. |

## Safety validation

**No live execution path was triggered or modified.** No order placement/cancellation/modification, leverage change, transfer or execution webhook was called. No production configuration, credentials, schedules, approval files, databases, services or collectors were changed. Protected study/holdout outcomes remain unopened. Offline analytical helper code was modified only in the candidate; it remains GET-only and its collector launch is disabled.

The Reasonix Orchestrator skill was used for one bounded read-only review of persistence source. Its tool returned an execution error with a partial advisory review, not a successful independent audit. Local inspection and transactional crash tests verified the relevant conclusions; no worker result was accepted as final test evidence. Student ChatGPT has no repository-access integration in this context, so no account messages were sent. Skill: `/Users/you/.codex/plugins/cache/personal/reasonix-orchestrator/0.1.1+codex.20260925015204/skills/reasonix-orchestrator/SKILL.md`.

## Remaining uncertainties and adoption blockers

1. Original runtime and sealed release retain material findings. This candidate has not been adopted. Applying it to the sealed study would change its implementation identity and cannot be treated as a patch to an unchanged evaluation cohort.
2. No strategy has demonstrated calibrated prospective edge here. Existing study approval does not transfer to candidate source. Independent review and a separately approved version/protocol are required before any candidate evidence collection; advice/execution requires its own validation and authorization.
3. Real holdout outcomes, journals and protected database/log contents were intentionally not accessed. Statistical performance, current exchange fee entitlements/credential permissions, crash history and secret commit history are not certified.
4. Thresholds such as divergence, spread, uncertainty and setup sensitivity remain hard-coded policy assumptions, not empirically established universal regime boundaries. Synthetic successes do not prove profitability, slippage behavior or news-regime robustness.
5. Atomic single-file writes and transactional study finalization do not form a transaction spanning state, JSONL observations, research database, study database and dashboard publication. `record` observation counts are not globally idempotent after arbitrary inter-layer crashes. Disk-full/power-loss recovery, every interruption boundary and indefinite storage growth remain incomplete. The candidate is blocked from running, so these limits are not being exercised against production.
6. UI testing was headless actual-script execution, not a visual/browser-session certification. Monotonic freshness protects an open page but is not a distributed trusted clock.
7. Account guidance/fills replay is excluded; no leverage, liquidation, collateral/exposure budget or quantitative stop-risk engine exists. The requested leveraged-trading trust claim remains unsupported.
8. OS process enumeration was sandbox-denied during audit; exact live PID identity remains unverified. Existing production automation was observed read-only and not interrupted.

## Final QA status

**QA BLOCKED**

The isolated candidate clears the reproduced implementation checks. Production remains unmodified and not certified for trading advice. This is a reviewable remediation artifact, not deployment or trading approval.
