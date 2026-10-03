# BTC copilot prospective-validation preparation — September 27, 2026

## Executive summary

**QA BLOCKED.** This is a concrete, reviewable **blocked proposal**, not an approved study or an activation-ready release. The requested Reasonix workflow completed two bounded read-only independent reviews. Candidate-only expiry and diagnostic-identity defects were reproduced and minimally fixed. All final guarded candidate suites pass: **148 Python methods passed, one skipped; 24 HTML checks and 14 golden scenarios passed**. Twenty-two methods were added in this stage: 14 operating-boundary methods and eight proposal-contract methods.

The largest new research blocker is mathematical: the preserved conservative one-contract cost bound is $1.005, or $1.015 under stress, against at most $1 settlement payout for positive-price fills. Strict positive stress-return acceptance cannot pass under that bound. This is a property of the conservative ceiling model; it does **not** establish actual venue fees or strategy profitability. Do not weaken the bound or thresholds merely to make a study pass.

Recovery and capacity gates also remain open. Crashes can leave mismatched JSON/text publications or diagnostic duplicates; reboot/suspend continuity halts rather than silently resetting; capacity enforcement is absent. A measured synthetic sample projects about **7.84 GB of unrotated diagnostic logs** for the proposed 70 days, excluding the full frozen study archive. No live collector, new study, schedule or order was activated.

## Scope and frozen identity

Production original and sealed study 002 release 4.2 are unchanged and remain QA BLOCKED under the [comprehensive audit](../QA_REPORT.md). This package supersedes the preceding [acceptance follow-up](../ACCEPTANCE_REPORT.md) only for the new candidate version, additional tests and completed bounded Reasonix review. Earlier audit findings and excluded production/holdout scope remain applicable.

Candidate: `../outputs/copilot_remediation_candidate`, version **5.1.1-offline-validation-candidate-af40a3e74c4adfda**.

Candidate manifest SHA256: `f7ced1521d56d0537f0617306d95b8cfb50f8d634f957973f1e4515be674c069`.

Settings SHA256: `47dedad12c34481024d90cc5fafa45293bafa990bc555bf5eaa6f4a8ab0c4eab`.

The candidate's old study 002 protocol and manifest study ID are **reference/test compatibility only**, not a new approval or cohort. The new proposal has a distinct ID. Candidate core source, configuration, preserved-policy proof and proposal are identified by [candidate_identity.json](candidate_identity.json), [candidate_source_hashes.json](candidate_source_hashes.json), [preserved_policies.json](preserved_policies.json) and [protocol_proposal.json](protocol_proposal.json). [package_manifest.json](package_manifest.json) freezes this package and relevant audit sources/results; its own hash is in `package_manifest.sha256`. Neither file grants authorization. The verifier checks hashes and never reseals a mismatch.

Changes are confined to the isolated candidate and audit artifacts. The CLI still unconditionally rejects launch; QA_ONLY remains present. Public output is NO TRADE, entry unavailable and management disabled. No activation approval/runtime was copied. Source/settings/protocol integrity is checked before suites. Production public-source fingerprints and the approved study 002 seal are checked afterward; all match. Protected outcome databases, collector journals/log contents and account credentials were not opened.

## Test results

Final command from the Investing workspace:

```sh
python3 -B qa_btc_2026-09-27/run_remediation.py
```

| Final suite | Run | Passed | Failed | Skipped | Process exit |
|---|---:|---:|---:|---:|---:|
| Existing candidate study/lifecycle tests | 51 | 51 | 0 | 0 | 0 |
| Independent candidate adversarial tests | 40 | 39 | 0 | 1 | 0 |
| Earlier remediation regressions | 19 | 19 | 0 | 0 | 0 |
| Earlier acceptance regressions | 17 | 17 | 0 | 0 | 0 |
| New operating-envelope tests | 14 | 14 | 0 | 0 | 0 |
| New proposal-contract tests | 8 | 8 | 0 | 0 | 0 |
| **Python total** | **149** | **148** | **0** | **1** | **Runner 0** |
| Actual embedded HTML checks | 24 | 24 | 0 | 0 | 0 |
| Golden scenarios, within adversarial process | 14 | 14 | 0 | 0 | 0 |

The skipped legacy journal finalization path does not exist in study 002; transaction rollback/retry/amendment tests provide replacement coverage. Golden expectations were recorded before execution; the obvious LONG/SHORT and qualifying setups assert second-observation **shadow** UP/DOWN eligibility, not merely public abstention. Public output must remain NO TRADE in every scenario. These are synthetic deterministic replays, not human/live-market replays or profitability evidence.

Five additional [integrity-boundary checks](integrity_boundary_results.json) pass in tiny temporary fixtures: disabled valid package accepted; changed manifest, changed artifact, unexpected approval and escaping artifact path rejected. These are separate from the Python-method count. The plaintext hash inventory detects changes against its recorded digest; it is not a signature, an immutable external trust anchor or authorization.

Before this stage's fixes, the first 12 operating methods produced nine passes, two failures and one error. The error was the missing diagnostic digest required for crash deduplication. The subsequently added lock-wait expiry regression separately failed before its fix. [operating_before.txt](operating_before.txt) and [commit_lock_before.txt](commit_lock_before.txt) preserve reproductions. Focused tests passed after the minimal fixes, then the entire guarded suite was rerun. A proposal test initially failed because it matched narrative wording; it now asserts explicit disabled capacity-monitor/quota flags. No implementation behavior was relaxed to pass it.

Results: [final guarded results](final_test_results.json), [operating methods](test_operating_bounds.py), [package-contract methods](test_package_contract.py), [final operating output](operating_final.txt), [source diff](candidate_fix.patch). The prior original/sealed baseline results retain **29 failing independent Python methods and four HTML failures**. Passing candidate suites does not fix those untouched targets.

## Findings, reproductions and disposition

### V01 — P1: observation expires during persistence or confirmation-lock acquisition

**Component:** candidate `btc_copilot.py`, `persist_observation` and `commit_observation`.

**Reproduction:** advance the mocked clock to `valid_until_epoch` after study storage, during latest JSON publication, or after acquiring the real state lock immediately before its body executes. Before the fix, stale observations could publish/commit normal completion. The lock-wait variant was identified by the independent reviewer and reproduced separately.

**Expected:** reject expired/future observations; preserve pending/nonconfirming state. **Actual after fix:** `ReadError`, `previous.confirmation_valid=false`, `last_observation.committed=false` at tested boundaries. **Root cause:** freshness was checked only when collecting, not throughout downstream storage/publication; a pre-lock check cannot cover lock-acquisition delay.

**Smallest fix:** repeat freshness checks at persistence boundaries and inside the confirmation lock before reading/modifying state. Regression tests pass, including focused before/after and the full suite. Residual expiry between the in-lock clock sample and the eventual disk write is not temporally atomic; future consumers must independently check source TTL before using data. This fix is not a power-loss or full temporal-atomicity certification.

### V02 — P2: append-before-marker crash leaves indistinguishable diagnostic duplicates

**Component:** candidate `Copilot.record` and `_record_pending`; diagnostic JSONL.

**Reproduction:** owned child process appends the observation diagnostic, then `os._exit(91)` before the durable logged marker; restart the same synthetic observation. **Expected:** one logical observation with identifiable retries; diagnostic rows cannot be authoritative trade outcomes. **Before:** duplicate physical rows lacked the stable observation identity required by this test. **After:** two physical diagnostic rows carry the same `_observation_digest`; state observation count is one and confirmation stays pending. **Root cause:** filesystem append and state marker are separate durable operations.

**Fix:** attach the already-used observation digest to both diagnostic append paths. No claim of exactly-once physical logs. Regression passes. SQLite study tables/unique keys remain outcome authority; readers must deduplicate diagnostics by digest, not count lines.

### V03 — P2: interrupted publication is not a coherent multi-file transaction

**Component:** `persist_observation`, latest JSON/text/raw JSON and evidence report.

**Reproduction:** child exits after actual latest JSON replacement, leaving a deliberately older text file. **Expected for future release:** consumers either see one coherent generation or declare degraded state. **Actual:** new NO TRADE JSON coexists with older NO TRADE text; confirmation remains pending. Audit may already have committed while study/publication/state completion did not. **Root cause:** separate atomic replacements and separate databases do not form one transaction.

**Recommended fix before activation:** separately reviewed generation/digest publication contract and restart handling; authoritative readers must reject mixed generations. The proposal describes this requirement, but no new runtime consumer enforcement or collector repair was implemented here. Crash-preserves-nonconfirmation regression passes; coherent publication remains unverified and blocking. Raw/evidence artifacts can survive a final freshness abort and must not be interpreted as committed recommendations.

### V04 — P2: persisted monotonic clock cannot continue normally after reboot/suspend discontinuity

**Component:** unchanged `study_policy.check_clock` and evidence metadata.

**Reproduction:** previous wall/monotonic 1000/500 followed by 1015/10 (reboot), or 1300/501 (wall-only suspend). **Expected:** fail closed rather than silently rewrite cohort continuity. **Actual:** `ValueError`; matching +300 wall/+300 monotonic is allowed but audit records a gap. **Root cause:** strict persisted wall/monotonic continuity, without an approved boot-aware recovery design.

**Disposition:** safety behavior verified; no automatic reset added. A bounded cold-start/reboot recovery policy and hardware crash testing are still required before an operational release. Regression tests pass, but these mocked clock cases are not actual machine reboot/suspend experiments. Missing checkpoints/windows remain missing, never imputed or backfilled.

### V05 — P1: positive-return acceptance is infeasible under the preserved conservative cost bound

**Component:** unchanged `study_policy.execution_cost` and frozen acceptance policy.

**Reproduction:** one hypothetical contract at every cent price .01–.99, .5c slippage reserve; the actual helper returns cost at least 1.005 and stress cost at least 1.015. The test covers all 99 prices; [cost_feasibility.json](cost_feasibility.json) records representative examples.

**Expected:** know before collecting whether the frozen cost/acceptance assumptions permit success. **Actual:** maximum binary settlement payoff 1 is less than even the winning cost; strict positive stress return is unattainable for these fully positive-price fills. **Root cause:** the conservative cent-balance scenario splits quantity into 100 minimum .01 units, rounds each positive debit up to at least .01 and assumes no rebates; adding the reserve exceeds payout.

**Disposition:** documented activation blocker; no fee policy, acceptance threshold or precision scenario weakened. Obtain verified applicable fee/fill-quantum/balance-precision evidence and independently review any evidence-supported model revision before a separately frozen future proposal. This does not prove the strategy is unprofitable under actual execution. Regression passes by demonstrating the bound; it does not make the bound feasible.

### V06 — P2: retention does not bound full evidence/log storage; no enforced capacity stop

**Component:** audit retention, JSONL, study databases/checkpoints/outcomes, runtime storage plan.

**Reproduction:** old audit observation beyond the configured seven-day window is pruned while implementation source remains; inspect record/write paths and measure 100 synthetic observations in an isolated temporary database/log. **Expected:** explicit bounded operating capacity, preserved frozen evidence and halt before unsafe disk exhaustion. **Actual:** only audit observations/gaps are pruned; implementation/frozen study evidence/diagnostics are not globally bounded. Repeated injected ENOSPC rejects state progress, but there is no proactive quota/capacity monitor. SQLite deletion need not shrink its file. **Root cause:** replay retention is not whole-system retention.

**Measurement:** about 36.5 KB compressed replay payload and 19.4 KB diagnostic row per repeated fixture; approximately 1.47 GB rolling seven-day payload and 7.84 GB 70-day diagnostic log, before full study archive/schema/temporary/WAL/response growth. These are representative estimates, not worst-case bounds or soak results. The proposed 10 GiB volume, 2 GiB free-space stop and 5 GiB warning are **unenforced planning values**, not a demonstrated adequate budget; the projection can exceed them and must be reconciled before approval.

**Recommended:** independently measured soak/body-size/retention design and implemented disk/IO stop mechanism with evidence custody; no automatic deletion added. ENOSPC/nonconfirmation and narrow retention regressions pass. Long-run capacity remains blocking. See [capacity_measurement.json](capacity_measurement.json) and reproducible [measure_capacity.py](measure_capacity.py).

### V07 — P2: the proposed new study is not an executable approved protocol

**Component:** proposal, legacy study validator/manifest and future release plan.

**Reproduction:** the legacy validator requires schema 2 and `btc-prospective-002`; substituting the proposed distinct study ID is rejected. Candidate main always exits NOT ACTIVATED. **Expected:** no approval inheritance or launch by swapping dates/IDs. **Actual:** this proposal cannot activate that disabled candidate. **Root cause:** intentional frozen legacy identity; a future new release/validator/approval mechanism has not been implemented or independently accepted.

**Disposition:** preserve the guard and legacy protocol. Obtain separately authorized implementation/review of a new research release, complete operational gates, then a new manifest/package-bound evidence-only approval. Proposal-contract tests verify unique identity, provisional dates, disabled flags and unchanged policy bytes. No broad refactor or activation shortcut was made.

## Independent Reasonix review

Used [Reasonix Orchestrator](/Users/you/.codex/plugins/cache/personal/reasonix-orchestrator/0.1.1+codex.20260925015204/skills/reasonix-orchestrator/SKILL.md) through Reasonix Mac MCP. Probe succeeded; two Flash read-only bounded contracts completed with exit 0. The first reviewed five proposal/recovery files and found the lock-acquisition expiry gap. The second reviewed final correction, supplied local test evidence and preservation inventory and gave **bounded ACCEPT**. Reported review cost total: **$0.014054826**, not total workflow cost. Sanitized scope/verdict/limits are in [independent_review.json](independent_review.json); raw transcripts/provider configuration are not published.

Reviewers did not run tests. They accepted the recovery/clock test claims, arithmetic under the conservative bound, policy-preservation evidence and correctly blocked status within supplied scope. They did not certify activation, actual profitability, coherent publication, hardware power-loss durability or capacity enforcement. The second noted the lock test simulates clock crossing after acquiring a real lock rather than an actually blocked acquire; that limitation is retained. It also requested stronger durable-marker assertions, which were added and followed by focused/full local reruns. Candidate source/hash remained unchanged after the final review.

Student ChatGPT was not used: no configured autonomous repository integration is available, and a manual account handoff would not perform these local verifications. No student/account message or login was attempted.

## Prospective study proposal and exact future approval scope

Proposed study ID: **btc-prospective-005-20261214-PROPOSED**. Dates are future **UTC-midnight proposals only**, after the public study 002 release boundary, without reading its outcomes or reusing its cohort.

| Boundary | Proposed UTC |
|---|---|
| Approval deadline | 2026-12-13 00:00 |
| Development start | 2026-12-14 00:00 |
| Validation start | 2026-12-21 00:00 |
| Holdout start | 2026-12-28 00:00 |
| Collection end | 2027-02-22 00:00 |
| Earliest result release | 2027-02-24 00:00 |

Design: seven development days, seven validation days, 56 holdout days; fixed primary `drift_free`; one hypothetical contract; official settlement exit; 15-second cadence/reaction delay and 20-second delayed window; first healthy common checkpoint at 570–600 seconds remaining. Require all **5,376** scheduled holdout forecasts, 100 filled contracts, 40 distinct fill days, full coverage, no unresolved/amended/missing calendar/execution observations, ECE ≤.05, stress-return lower bound strictly >0 and paired Brier upper bound strictly <0 for every predeclared 1/2/7-day block sensitivity. Bootstrap remains 2,000 replicates, seed 719. Cost/threshold policy bytes are unchanged. Exploratory arms never select or promote the primary.

Missing data are not zero returns, backfilled quotes or calendar deletions. Holdout uses a new database and separate evaluator/access custody; no analyst access before approved release. Publication/clock/storage faults halt and preserve pending evidence. Source staleness/future/identity/pagination faults abstain and record degraded state. Approval deadline missed means **no start**, not automatically shifted dates. Release requires reconciled official settlement evidence and review, never automatic promotion.

**Approval must wait until blockers are resolved.** A future approval must explicitly identify final new study ID, absolute new release/runtime destination, final source/config/dependency/protocol/package hashes, final UTC dates and approval timestamp before deadline, read-only source entitlements/request pacing/quota, storage/recovery limits, holdout custodian/release policy and stop authority. No runtime destination has been approved in this package. See [APPROVAL_SCOPE.md](APPROVAL_SCOPE.md).

Evidence collection is separate from orders/advice. Trading/order/account/leverage/fund mutations, position hold/add/exit recommendations, sealed-study modifications, prior approval reuse, protected outcome reuse, autonomous repairs/restarts/schedules or promotion are excluded. Even a future evidence-only approval would not enable those actions. Authenticated BRTI read access exists in the helper; this stage did not authenticate, establish its entitlements or verify future credentials. It must be scoped and attested separately if required by the approved collection design.

## Trading-logic and safety validation

| Requested property | Verified scope/status |
|---|---|
| Data freshness | Synthetic stale/future/missing/provenance/TTL cases plus downstream expiry tests pass; current live availability not certified |
| Candle closure | Completed BRTI minute bars and future/unclosed input rejection tested; unfinished bars excluded within supported source path |
| Timeframe alignment | Supported 1/3/5/15/60-minute BRTI features tested; no general 4h engine or five-timeframe BTC strategy |
| Look-ahead bias | Source/replay timing and origin/delayed-quote tests pass within inspected deterministic code; prospective outcomes/performance unexamined |
| LONG/SHORT | Synthetic shadow UP/DOWN eligibility verified; these are Kalshi outcomes, not leveraged BTC positions |
| NO TRADE | Public abstention and invalid/stale/missing/conflicting/advice-forgery gates tested |
| Stop math | No quantitative BTC stop-risk engine exists; cannot certify |
| Target math | Binary quote/profit-zone helper tests pass; not stop/target trade execution |
| Leverage/collateral/liquidation | Unsupported; cannot certify |
| Position sizing/exposure | Decimal depth/cost previews and disabled advice tested; no validated personal budget/leverage sizing |
| Averaging/scale-in | Position guidance remains disabled; no validated adding/martingale/scale-in engine |
| State isolation | Version/digest, repeated/conflicting/reversed observations, concurrent writes and pending-restart tests pass within synthetic scope |
| LLM reasoning repeatability | Runtime has no LLM decision call/prompt; Reasonix reviewed code, not trading decisions |

**No live execution path was triggered or modified.** No order was placed, cancelled, modified or simulated as live. No production configuration/credentials/scheduled jobs/databases/services changed. No original or sealed collector was started/stopped. Test child processes only wrote temporary synthetic data; only those owned children were forcibly exited to exercise crash boundaries. No order-capable source path was found in the audited copilot; the read client is GET-restricted, and public abstention/launch guards remain.

## Remaining uncertainties and final readiness

Current official source/fee/fill-quantum/precision/rate/credential entitlement attestations are not renewed by this package. New-release dependency locking is incomplete; [runtime_inventory.json](runtime_inventory.json) records the tested Python/SQLite/Node/optional cryptography versions, not an immutable deployable environment. Actual machine power loss, reboot/suspend recovery, durable storage semantics, adversarial FIFOs/special files, multi-process/long-duration production load, quotas and coherent publication remain unverified. Runtime PID/descendant absence is not independently certified by OS process enumeration. Protected state/outcomes remain intentionally unread, so no holdout performance or contamination claim is made beyond inspected source/approval boundaries.

The package is ready to review **as a blocked artifact**. It is **not ready for activation approval**. Completing review artifacts and passing tests does not grant trading trust, study approval or deployment permission. Original/sealed versions retain known defects; the candidate retains the above research and operating blockers.

**QA BLOCKED**
