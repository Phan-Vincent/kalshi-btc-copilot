# BTC copilot independent acceptance follow-up — September 27, 2026

## Executive summary

**QA BLOCKED.** The requested Reasonix workflow was run through Reasonix Mac MCP with the Reasonix Orchestrator skill. Two Flash read-only partitions were dispatched; the data partition returned useful but partial source review with exit 1, and the persistence partition timed out. One narrowed Pro retry also timed out. There is **no completed independent Reasonix sign-off**. Codex adjudicated the usable suggestions, independently reproduced additional failures, made candidate-only fixes and completed local verification.

The offline candidate is now `5.1.0-offline-acceptance-candidate` plus source/settings fingerprint. It remains launch-disabled, public NO TRADE and management unavailable. The final five candidate suite processes all exit 0: **126 Python methods passed, one skipped with replacement coverage, 24 actual-HTML checks passed, and 14 golden scenarios passed**. Seventeen acceptance methods were added this turn. Eight failures were captured before the persistence fixes.

Engineering correctness passes the tested boundaries, with remaining recovery/operating-scope limits below. Research validity is unproven. Trading readiness remains blocked. Neither original production nor the sealed study 002 release was changed or adopted.

## Reasonix execution and adjudication

Skill: [Reasonix Orchestrator](/Users/you/.codex/plugins/cache/personal/reasonix-orchestrator/0.1.1+codex.20260925015204/skills/reasonix-orchestrator/SKILL.md).

| Partition | Mode/tier | Result | Accepted as final proof? |
|---|---|---|---|
| Persistence/evidence/authority review | Read-only Flash, bounded 14 steps/180 seconds | Timed out; exit 124 | No |
| Data/numerics/replay/test design | Read-only Flash, bounded 14 steps/180 seconds | Partial review; exit 1 | No; suggestions checked locally |
| Narrow pending-state/transaction retry | Read-only Pro, failure-verification escalation, 6 steps/150 seconds | Timed out; exit 124 | No |

The read-only probe succeeded with Reasonix v1.39.1. No worker received write authorization or production/runtime/credential/holdout scope. No further model cascade was attempted. All delegation calls returned; timeout handling uses the bounded wrapper. OS process enumeration was unavailable (`pgrep` could not obtain the process list), so descendant-process absence is not independently certified. No background delegation is being continued by this task.

The partial data review suggested stronger second-observation chop assertions and explicit replay exception expectations. Local adjudication found that the earlier golden suite already asserted second-observation shadow NO TRADE; therefore the review's claim that these cases were wholly vacuous was overstated. Nevertheless, it did not separately assert full-strategy ineligibility with a structural rejection, which is now required. The earlier replay test already treated exceptions as rejection; it was not a successful-replay loophole. Its blanket exception catch could mask unrelated failures, so it was replaced with explicit rejection expectations for four malformed replay prerequisites and mismatch assertions for every other compared field.

Speculative cross-side complementary-book duplication and minute-grouping issues were not accepted as defects: complementary YES/NO ladders are not duplicate liquidity, and typed per-second inputs protect minute grouping. A request to reject any 3-second gap was not adopted as a new threshold; the implementation explicitly permits bounded gaps and the review did not prove a bypass of its combined coverage gates. No policy was silently tightened based on these unsupported claims.

Known reported worker cost is $0.023477028 for the partial data review. Timeout costs are unavailable, so this is **not total workflow cost**. Sanitized execution status is in [reasonix_acceptance_status.json](reasonix_acceptance_status.json); transcripts and provider configuration are not published.

## Test results and reproduction evidence

Final command from the Investing workspace:

```sh
python3 -B qa_btc_2026-09-27/run_remediation.py
```

| Candidate suite | Methods/checks | Passed | Failed | Skipped | Process exit |
|---|---:|---:|---:|---:|---:|
| Existing study/lifecycle suite | 51 | 51 | 0 | 0 | 0 |
| Independent adversarial suite | 40 | 39 | 0 | 1 | 0 |
| Earlier remediation regression suite | 19 | 19 | 0 | 0 | 0 |
| New acceptance regression suite | 17 | 17 | 0 | 0 | 0 |
| Actual HTML suite | 24 | 24 | 0 | 0 | 0 |
| Golden corpus | 14 scenarios | 14 | 0 | 0 | Within adversarial process |

The first ten new acceptance methods were run against the pre-change candidate: two passed and eight failed. [acceptance_before.txt](acceptance_before.txt) preserves the exact assertions and failure traces. [acceptance_baseline](acceptance_baseline) retains the preceding public candidate files; [acceptance_start_hashes.json](acceptance_start_hashes.json) records their hashes. These are copied source artifacts, not an existing study database or cohort.

The original-journal method remains skipped because the study 002 design removes that journal finalization path; transactional settlement rollback/retry and amendment tests provide replacement coverage. The 17 new methods include subtests for eight downstream interruption points and six target/fee combinations. Assertions test observable persistence, count, confirmation and integrity behavior, not just the implementation's helper calls.

After the final core changes, all 51 existing candidate methods, 39 applicable adversarial methods, 19 earlier regression methods and 17 new methods pass. The baseline suites were also rerun; their 29 known failing independent Python methods and four dashboard failures remain, while their existing suites remain green. Production source/seal hashes still match. Baseline failure evidence was not rewritten to pretend production was fixed.

Artifacts: [final suite results](remediation_test_results.json), [17 new acceptance methods](test_acceptance.py), [acceptance patch](acceptance.patch), [strengthened golden results](candidate_golden.json), [prior comprehensive audit](QA_REPORT.md), [prior remediation report](REMEDIATION_REPORT.md). The prior report describes the preceding candidate; this report supersedes its version, test counts and persistence/replay descriptions.

## Findings and smallest candidate fixes

### A01 — P1: failed observation persistence leaves false confirmation state

**Component:** `btc_copilot.py`, `Copilot.record`, `_record_pending`, `commit_observation`, `persist_observation`.

**Failure/root cause:** record mutated `self.state` before durability checks, persisted healthy confirmation before log success, and completed before audit/study/publication. An ENOSPC failure could leave in-memory health or a persisted healthy prior for a run that failed later.

**Reproduction:** inject ENOSPC at state write; separately fail append; then fail audit record, audit scorecard, study record, study status, latest JSON, latest text, raw JSON and final state commit after an observation is recorded. Reload the state file as a restart would.

**Expected:** failed or unfinished new observations cannot confirm the next setup; prior committed state cannot be replaced in memory by failed writes. **Before:** in-memory state changed on disk-full, and append failure left `confirmation_valid=true` on disk. The original downstream ordering also enabled healthy state before all persistence completed.

**Fix:** stage a private state copy; persist the new observation as pending/nonconfirming; durably mark diagnostic log success; update in-memory state only after successful state write. Complete audit/study/publication through a small testable persistence helper, then write the matching digest's confirmation commit marker. Commit rejects unmatched/unlogged records and enabled public advice. This is a logical completion protocol, not a cross-store ACID transaction.

**Regression:** disk-full, log-failure, eight downstream failures/restarts, repeated pending restart, successful completion and old-commit rejection pass. A failed pending run remains nonconfirming. The runner remains unconditionally disabled.

### A02 — P2: duplicate, conflicting or reversed observations overwrite/count state incorrectly

**Component:** `btc_copilot.py`, observation digest, record state lock and reload.

**Failure/root cause:** record accepted the same epoch repeatedly, incremented observations twice, accepted a different payload at the same time and could overwrite newer state with older state. Two independently initialized writers carried stale in-memory copies.

**Reproduction:** record the same snapshot twice; change BTC while keeping its epoch; subtract 15 seconds; launch two temporary writer instances against the same state path.

**Expected:** exact repeated observation is idempotent, conflicting duplicate/reversed epoch is rejected, and simultaneous writers do not lose or double-count state. **Before:** count became two; conflicts/reversals were accepted.

**Fix:** lock the output, reload the authoritative on-disk state, retain epoch/digest/log/commit metadata, reject conflict/reversal, and retry pending log repair without incrementing count. Runtime scorecard/study enrichments are deliberately excluded from the observation digest because they are appended after the deterministic snapshot.

**Regression:** exact duplicate, conflicting duplicate, reversal, concurrent writers, pending log retry and obsolete commit all pass. Diagnostic JSONL may still duplicate if the process crashes after log append but before its state marker; this is not an authoritative outcome stream and is documented below.

### A03 — P1: concurrent study records bypass duplicate-clock guard

**Component:** `btc_copilot_evidence.py`, `Study.record`.

**Failure/root cause:** prior UTC and monotonic clocks were read and checked in separate transactions before the later write transaction. Two same-epoch callers could both read no prior and both report success.

**Reproduction:** dispatch two synthetic same-epoch study records, adding a short delay after clock validation to expose the read/check/write race. **Expected:** one commits, the duplicate rejects; clock and slot updates roll back on interruption. **Before:** both returned committed.

**Fix:** acquire `BEGIN IMMEDIATE`, read prior UTC/monotonic values, check ordering/continuity and write state in the same serialized transaction. Input-only checks remain before writes.

**Regression:** one successful/one rejected duplicate and a forced database trigger failure rolling back clock/slot state pass. Existing delay, fairness, pending-exposure and amendment tests remain green.

### A04 — P2: append logs bypass the atomic helper's link protection

**Component:** `btc_copilot.py`, `append`, `output_lock`.

**Failure/root cause:** atomic replacement had been hardened, but JSONL append still followed symlinks and wrote existing hard-linked files without checks or durability sync.

**Reproduction:** point the temporary observations log at a sentinel using a symlink or hard link. **Expected:** refuse the linked destination and preserve sentinel bytes. **Before:** the log appended into the sentinel.

**Fix:** `O_NOFOLLOW`, open-descriptor link-count checks, 0600 permissions, exclusive file lock, flush/fsync. Output lock files similarly reject links. No production log file was touched.

**Regression:** symlink/hardlink sentinel tests and concurrent writer/log-count tests pass. This protects the tested pre-existing links; it is not a claim to defend a directory against a malicious same-user actor racing every filesystem operation.

### A05 — P2: acceptance assertions hide some failure causes

**Component:** `remediation_audit.py`, `test_remediation.py`.

**Failure/root cause:** chop/almost cases did not separately assert the second full-strategy rejection reason; a general exception catch in replay tampering could pass for an unrelated coding failure.

**Reproduction:** inspect the first-observation eligibility clause and blanket exception branch; challenge second-observation fields separately. **Expected:** structurally invalid setups fail after a confirming opportunity; malformed replay prerequisites explicitly reject and other mutations report the precise field mismatch. **Before:** second shadow decisions were asserted, but full rejection reason was not; any listed exception was accepted across fields.

**Fix:** require second-observation full ineligibility and `structure` rejection for chop/almost; explicitly assert exceptions for malformed version/collection/analysis/completion prerequisites; require a mismatch containing each other altered field.

**Regression:** all 14 golden scenarios pass the stronger assertions; five directional second observations still qualify, so constant abstention cannot pass. Explicit malformed prerequisites and full-field tampering pass.

### A06 — P2: replay omits decision-relevant contract/source/display facts

**Component:** `btc_copilot_research.py`, `replay`.

**Failure/root cause:** the previous comparator's 31 fields omitted saved strike, contract/source identity, schedule, chart, rules, fee multiplier and other deterministic context. A saved strike could be changed without changing the already-saved model field, leaving replay's equality claim too narrow for the whole public market observation.

**Reproduction:** alter an omitted saved field such as strike while retaining raw inputs and compared fields. [Before/after replay evidence](replay_strike_before_after.json) records the baseline accepting a forged strike and the candidate reporting a strike mismatch. **Expected:** mismatch; **Before:** these fields were outside the comparator and could leave `matches=true`.

**Fix:** compare 55 deterministic fields, adding timestamp, identity, schedule, strike/distance, source names, chart, rules, fees, context and public target state. Keep account-dependent narrative/invalidation, private retrieval times, account guidance, study/scorecard statistics and wall/monotonic runtime metadata explicitly excluded.

**Regression:** honest replay and delayed-completion replay pass; each compared field is altered independently and either explicitly rejects as a malformed prerequisite or returns its field mismatch. Cost/target recomputation across entry .05/.15/.45 and multipliers 1/2 passes.

## Disposition of the original 20 findings

The original detailed reproduction descriptions remain in [QA_REPORT.md](QA_REPORT.md). Their candidate dispositions were checked against current source and the full relevant suite:

- F01–F05: retained study 002 deadline, source-evidence separation, pending exposure and outcome fairness rules; focused tests remain green.
- F06: explicit hour span/sample/nearest coverage and gap gates retained. Permitted sampling gaps are a declared policy tolerance, not a guarantee that every historical second exists.
- F07: source/health/time confirmation guards retained and further strengthened by A01's pending completion marker.
- F08–F13: duplicate depth, trades/candles/error validation, numerical bounds and grid termination regressions pass.
- F14: replay expanded from 31 to 55 fields with explicit exclusion scope and stronger mutation tests.
- F15: all 24 actual-HTML validity, outage, expiry and clock checks pass.
- F16: study transaction finalization remains the authoritative replacement for original journal writes; crash/retry and amendments remain verified.
- F17: single-file atomic protection retained and append/locking paths additionally hardened in A04.
- F18: candidate README accurately states its offline authority; sealed README remains untouched.
- F19: fee-aware targets and additional independent price/multiplier examples pass.
- F20: unread trade cursor remains a common quality veto that disables every arm; direct selection helpers depend on normalized snapshot vetoes by design. No separate unvalidated input path has been certified.

This is local verification of bounded behavior, not full independent Reasonix coverage or a guarantee for every source response/regime.

## Engineering, research and trading conclusions

**Engineering:** candidate passes tested data, math, abstention, replay, duplicate and interruption boundaries. Failure injection is synthetic and primarily exception/transaction testing; it does not certify every OS-level crash or power-loss interleaving. Publication/recovery and operating policy still need the review below.

**Research:** no prospective calibrated edge was established. No protected outcomes were opened. The candidate's changed source hash cannot inherit study 002's cohort or approval. Statistical validity and venue execution/fee assumptions remain blocked pending independent evidence.

**Trading readiness:** blocked. Public NO TRADE and unavailable management remain mandatory. The program is a binary-contract research monitor, not a leverage/collateral/liquidation/quantitative-stop engine. No order path was exercised or introduced.

The [prospective validation proposal](prospective_validation_proposal.json) defines a distinct proposed candidate study identity, source/manifest binding, 7-day development, 7-day validation and 56-day future holdout, frozen primary/comparison/cost policies, strict coverage/outcome gates, no prior-outcome reuse and no automatic promotion. Dates are intentionally unset. The proposal is not executable configuration or approval; its ID/dates will not pass the inherited study 002 validator. It requires a separately reviewed release rather than removing this candidate's offline guard.

## Safety validation

No live execution path was triggered or modified. No trading API authentication, order, cancellation, modification, leverage change, fund transfer or execution webhook was called. No production configuration, credential, scheduler, service, collector, approval or database was changed. Reasonix provider communication was the delegated review service; market data requests in tests were mocked.

Final source inventory comparison shows no changed production public-source paths. Study 002 file hashes and manifest digest remain intact; its runtime remains absent. The candidate has no approval or runtime. Only public candidate source, audit artifacts/tests and proposal documents were modified. Protected study/holdout outcomes remain unread.

## Remaining uncertainties and blockers

1. Independent Reasonix acceptance is incomplete. All three bounded review calls were unsuccessful/partial. No successful external reviewer sign-off is represented by the local passing tests.
2. Production retains the reproduced material findings. Candidate adoption is neither performed nor approved.
3. There is no demonstrated prospective edge or current authenticated fee/rate/permission attestation. The proposed study must be independently approved and bound to a new frozen source version before any evidence collection.
4. Cross-store writes are not one ACID transaction. The completion marker prevents an unfinished new observation from confirming, but partial audit/study/diagnostic records can remain. Crash after a database commit may leave a rejected duplicate on retry; no silent repair/backfill is implemented. Diagnostic append-before-marker crash may duplicate JSONL rows. Interrupted publication may leave fresh NO TRADE JSON alongside older text; no directional advice is possible, but coherent recovery/publication still needs review.
5. Tests reload persisted state and inject exceptions/triggers. They do not exhaustively simulate process death at every instruction, hardware power loss, filesystem corruption, reboot/suspend monotonic continuity, prolonged disk exhaustion or indefinite retention. There is no new recovery daemon or scheduler.
6. Calls to `commit_observation` rely on the fixed persistence coordinator's ordering; this internal method is not a cryptographic receipt proving external stores. Arbitrary callers or manual state edits are outside the tested trust boundary. Candidate activation remains disabled, so this is not a route to live orders or advice.
7. State/log paths are assumed to be local files in the controlled audit/runtime directory; no claim covers malicious same-user races or unsupported special-file destinations. Git credential history and exact live PID identity remain unavailable.
8. UI testing is headless actual-script execution, not visual inspection or a trusted distributed clock. Private guidance/narrative replay remains intentionally excluded and all such advice stays disabled.

## Final QA status

**QA BLOCKED**

The candidate is a reviewable, launch-disabled engineering artifact with stronger local acceptance evidence. It is not a successfully Reasonix-certified release, an approved study or a trustworthy live trading adviser.
