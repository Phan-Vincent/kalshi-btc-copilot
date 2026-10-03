# Readiness progress and QA results

## Executive summary

QA BLOCKED. The supplied risk limits are implemented in an isolated, deterministic binary-contract engine. Coherent evidence storage and an exact validation-protocol proposal now have adversarial tests. These components are not yet connected to a completed, approved collector or advice release. Existing production and all frozen packages remain unchanged.

The main external blocker is still fee economics: the preserved cent-precision fragmented-fill bound makes positive stressed profitability acceptance infeasible. A venue clarification draft is ready but unsent. No account-precision or terminal-refund assumption has been relaxed. Engineering preparation continues while that evidence is unresolved.

## Test results

Current full relevant run: `python3 -B qa_btc_2026-09-27/readiness_release/run_checks.py`, exit0.

- Python:224 methods run,223 passed,0 failed,1 intentional legacy-journal skip.
- New readiness tests:83 passed:33 risk,27 coherent runtime,3 response bounds,20 protocol.
- Golden scenarios:14/14 passed, replayed automatically from synthetic data with declared expectations.
- Existing interface checks:24/24 passed. These validate the retained interface; the new database-backed reader is not yet integrated.
- Manual review: independent arithmetic, source/transaction inspection and Reasonix findings adjudication. No manual live-market trading scenario or authenticated account test was run.
- Both frozen package integrity verifiers passed:74 validation files and23 fee-review files plus64 preserved references. Production source inventory remained unchanged.

Test output and exact suite counts are in evidence/test_results.json. Baseline reproduction failures are evidence of the defects below and are not current-suite failures.

## Findings and fixes in this candidate

| ID / severity | Component | Failure, reproduction and root cause | Expected versus actual | Fix / regression status |
|---|---|---|---|---|
| R1 — P1 | btc_copilot.persist_observation; Audit/Study | Force study.record to fail after Audit.record. Original audit count remains1 because separate connections commit independently. | Entire generation should commit or roll back; original pipeline retains partial evidence. | New EvidenceStore binds real Audit/Study to one transaction with publication and state. Rollback tests and actual child-process exits pass. Collector integration remains incomplete. |
| R2 — P1 | Coherent runtime halt/restart | A disk failure can also prevent creating HALTED; a memory-only halt cannot survive process restart. | Uncertain durable safety state must block resumption. | RUNNING is fsynced before work and retained after abrupt/unhealthy exit. Injected HALTED write failure and unclean-restart tests pass. Operator recovery authorization is still a release integration task. |
| R3 — P2 | Kalshi/Coinbase response reads | A fake response records read(size=-1), proving an unbounded read before parsing. | Reject oversized input before loading an unbounded body. | Both paths cap reads at4MiB+1, reject overflow/invalid UTF-8. Three tests pass. |
| R4 — P1 | Binary risk proposal parsing | Add leverage5, actionSELL, stop/target/collateral/order fields to otherwise valid synthetic input. Eight cases were accepted because fields were ignored. | Unsupported risk structures must fail closed. | Strict proposal allowlist now returns DATA_UNAVAILABLE/zero quantity. Regression failed before fix;33-test risk suite passes afterward. |
| R5 — P2 | Settlement reconciliation | Legacy Study.settle records retry time before GET; interruption can postpone a recheck without evidence. | Response and retry/outcome metadata should commit together, preserving original outcomes. | New coordinator settle_one writes only after validated response and rolls back the entire outcome transaction on interruption. Retry, wrong-ticker, timeout and amended-result quarantine tests pass. Legacy method remains a reference path; new collector must use coordinator. |

The new runtime also enforces a process owner lock, an in-process mutation lock, same-generation state checks, duplicate identity checks, snapshot expiry at commit and read, OS boot identity, clock continuity, SQLite page limits and free-space checks. It retains evidence when storage is exhausted. The default8GiB directory budget reserves approximately two thirds for rollback-journal/metadata overhead; main database capacity is consequently smaller. Long-duration storage sizing still needs measurement.

## Risk and trading-logic validation

- User caps:$12 full loss per trade,$500 open-plus-pending notional,$30 daily loss,one simultaneous position. Adding and averaging are disabled.
- Pending positions count toward both exposure and maximum loss. Wins never replenish the daily gross-loss budget.
- Until notional semantics are clarified, both premium and $1-per-contract payout ceilings apply. Daily budgeting currently uses UTC; these are explicit conservative implementation choices.
- Loss is premium plus a verified conservative fee bound and stress reserve. Fractions use exact Decimal math and floor to0.01contracts. Boundary sweep includes297 price/day combinations.
- No leverage, collateral, BTC stop-distance sizing, liquidation model or sell/short inventory structures are supported by this binary engine; unsupported proposal fields reject. Stop and target reliability is not a substitute for full binary-settlement loss.
- Freshness, candle closure, timeframe alignment, future-data rejection, LONG/SHORT shadow behavior and NO-TRADE output were rerun in the retained adversarial/golden corpus. This does not prove economic edge or validate unsupported timeframes.
- State isolation, duplicate handling, failed-observation confirmation invalidation, abrupt process exits and normal clean restarts were tested on temporary stores. Unclean restart deliberately requires review; no automatic cohort reset exists.
- No LLM generates trade decisions in these tests. Reasonix reviewed code, and its partial/timed-out reviews are not acceptance sign-off.

## Safety validation

No live execution path was triggered or modified. No order was created, canceled, modified or submitted. No account authentication, credential read/change, production edit, service/schedule change, collection activation, external message or protected-outcome access occurred. Read-only official public documentation/series metadata and original source hashes were inspected. Public output remains NO TRADE; conditional risk calculations never authorize advice.

## Remaining blockers and owners

| Blocker | Owner / dependency | Next verifiable milestone |
|---|---|---|
| Fee/fragmentation semantics, terminal carry and scheduled changes | Venue plus account owner; official nonsecret evidence | Written rule resolution and independently reviewed conservative bound. No response or date assumed. |
| Guarded collector, coherent reader and approved recovery workflow | Codex implementation plus independent QA | Executable evidence-only candidate with strict manifest/destination/scope gates, crash tests and full suite. |
| Risk evidence provenance and final policy choices | Account owner evidence plus Codex integration | Fresh complete ledger and conservative all-quantity fee bound; missing evidence keeps advice disabled. |
| Prospective validation | Approved frozen release and future observations | Preserve7 development+7 validation+56 holdout days and2-day release wait; no cohort reuse or shortened thresholds. |
| Final acceptance/deployment package | Codex and independent reviewer; explicit user approval later | Exact immutable release hashes, destination/access requirements and rollback, ready for a final approval decision. |

The proposed nonoverlapping calendar starts2026-12-14 and releases no earlier than2027-02-24. That is conditional research timing, not a shipment promise. A favorable outcome remains impossible under the current unfavorable cost bound; calendar completion alone cannot authorize advice. No reliable deployment date can yet be promised.

## Working identity and deployment boundary

Model: `5.2.0-readiness-work-in-progress-97780b23d5fec957`

Working inventory SHA256: `17df9077f825af9f1b15147b0e611ab032aa3b43e18d128110d715f4dcd1b5df`

These hashes identify tested work in progress. They are not an activation approval, final release seal or deployment proposal. The copied manifest is explicitly WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE. Production, sealed study002, frozen validation and fee-review packages are preserved.

## Next work

Integrate the guarded evidence-only collector and single-generation reader; bind real clocks, credentials scope, future protocol and runtime destination; finish recovery/capacity evidence; obtain complete independent review; freeze and verify the final candidate. Keep trading disabled and the venue clarification unsent until explicitly authorized.

QA BLOCKED
