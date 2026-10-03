# Prospective validation proposal — NOT APPROVED

Study: `btc-prospective-005-20261214-readiness`  
Candidate label: `5.2.0-readiness-work-in-progress`  
Status: proposed specification; no activation, collection, trading advice, execution or inherited approval.

This proposal preserves every policy field of the copied study-002 protocol except the explicitly declared new identity, candidate label, future calendar and earlier approval deadline. The source and outcome files of sealed study 002 are not modified or consulted. The copied protocol is a public policy reference, not permission to reuse its observations or approval.

## Proposed UTC calendar

| Boundary | Proposed UTC time |
|---|---|
| Approval deadline | 2026-12-13 00:00:00 |
| Development starts | 2026-12-14 00:00:00 |
| Validation starts | 2026-12-21 00:00:00 |
| Holdout starts | 2026-12-28 00:00:00 |
| Collection ends | 2027-02-22 00:00:00 |
| Earliest result release | 2027-02-24 00:00:00 |

The design retains seven development days, seven validation days, 56 holdout days and two days before release. At 96 scheduled slots per UTC day, the holdout requires all 5,376 matched forecasts. Missed approval or missing required observations do not authorize automatic date shifts, backfill, imputation or removal of slots.

December 14 is a conservative, nonoverlapping calendar after the sealed study's published December 12 release boundary. It is not a promised shipment date or proof that no earlier, separately reviewed independent research could be possible. Changing any proposed boundary requires a new review before collection, without modifying sealed study dates or accessing protected cohorts.

## Preserved design

- Primary strategy: `drift_free`; no post-outcome reselection.
- One hypothetical contract, 15-second reaction delay, 20-second qualifying window, first healthy common checkpoint at 570–600 seconds remaining.
- Exit: official settlement only. Market midpoint remains a probability comparator, not an executable price. Always-abstain remains the zero-return baseline; ablations remain exploratory.
- Acceptance: 5,376 matched forecasts, at least 100 filled contracts over at least 40 distinct days, full coverage, ECE at most .05, and zero unresolved, missing-calendar, amended-result or missing-execution observations.
- Every predeclared 1-, 2- and 7-day block analysis must have stress-return lower bound strictly above zero and paired Brier upper bound strictly below zero. Retain 2,000 replicates, seed 719 and minimum 56 days; intersection of sensitivity checks is not a simultaneous-confidence claim.
- Fees, fill quantum, both balance-precision scenarios, conservative fragmentation bounds, slippage/stress treatment, pacing, clock discontinuity handling, outcome rechecks, missing-data rules and holdout custody remain unchanged.

The canonical preserved-policy SHA256 is:

`574126a8c60a38a387ad46448e9f8fdbde72ed2fdae592eb9224fa57a589b9f9`

This invariant binds inherited numeric fields **and policy text**. It is a policy-preservation check, not a release signature or completed-package freeze. Final code, settings, dependencies, protocol and package hashes require separate review once implementation is complete.

## Economic blocker remains

`profitability_validation_mode` remains `BLOCKED_PENDING_FEE_AND_EXECUTION_EVIDENCE`.

Under the preserved positive-price cent-fragmentation bound, the one-contract cost can be $1.005 with the minimum .5¢ reserve, and stressed cost $1.015, against at most $1 contractual payout. Positive stress-return acceptance is therefore infeasible under that bound. This is a statement about the preserved conservative model, not proof of actual account costs or strategy profitability.

No cost narrowing, acceptance weakening, favorable-result selection or profitable-trade claim is authorized. Applicable official fee rules, fill constraints, terminal refund treatment and nonsecret account-precision evidence must support any separately reviewed model revision before a profitability study can be approved.

## Validator and limitations

`readiness_policy.validate_readiness_protocol(protocol)` is a pure offline function. It returns an independent copy of a valid proposal and raises `ValueError` for invalid identity, dates, deadline, policy changes, unsupported fields or activation flags. It never mutates the input. After explicit readiness validation it adapts only study identity and deadline on a temporary copy to run the legacy validator; this does not grant legacy approval to the new study.

No source requests, credentials, private account records, holdout outcomes, schedules, services or launch calls occur during protocol validation. A passing check does not prove operational readiness, truthful external attestations, current venue rules, fee feasibility, release integrity or user approval. Existing launch guards remain in force.

Focused tests:

```text
python3 -B qa_btc_2026-09-27/readiness_release/tests/test_readiness_protocol.py
20 tests passed
```

Tests challenge calendar shortening and shifting, UTC midnight, deadlines, identity, primary/exit/checkpoint changes, every acceptance field, fee/precision weakening, pacing/clock/uncertainty changes, custody prose, hidden activation and input mutation. They independently compare inherited policy with the copied legacy protocol.

## Required next gates

Before any collection, complete implementation and bounded independent review of the evidence-only release; bind reviewed source/configuration/dependency/protocol/package hashes to an explicit destination and fresh runtime; verify current read-only venue access and applicable source/fee/rate rules; establish holdout custody and stop authority; and obtain explicit approval before the proposed deadline. No study-002 approval is inherited.

This document is review preparation. It does not approve a release, activate collection or establish trustworthy trading advice.
