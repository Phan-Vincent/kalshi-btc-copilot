# Study 005 revision 1: DRAFT, not adopted

**Status (2026-09-30):** Kalshi support can't state the account's classification: the first line gave a stock answer, and a person said the docs give no way to determine it. **Decision (user, 2026-09-30):** adopt $0.0001 from the account's own Kalshi fee records instead of a Kalshi confirmation. The evidence standard is under "Fee evidence standard" below. The code lives in `qa/study005r1_release/`; this candidate and the Acer staging are untouched. If the tripwire ever finds an order that matches only $0.01, discard the revision and stop the profitability study.

## Files

- `make_revision.py --start YYYY-MM-DD` builds `readiness_protocol.r1.draft.json` from the current protocol. It checks the source hash first, then proves that every field outside the change list below is byte-identical.
- `readiness_protocol.r1.draft.json` is built with the chosen start, **2026-10-12** (user decision 2026-09-30).

## What changes in the protocol (and nothing else)

| Field | Now | Revision 1 |
|---|---|---|
| `study_id` | `btc-prospective-005-20261214-readiness` | `btc-prospective-005r1-<start>`. A new identity, because the fee policy changes; no data exists under the old one. |
| Dates | Dec 14 → Feb 24 | Start at the first 00:00 UTC after approval; same 7 + 7 + 56 + 2 days. Example: Oct 26 → Jan 6. |
| `approval_policy.deadline_utc` | Dec 13 | Start − 1 day. A late approval still means a new revision, never an automatic shift. |
| `fee_policy.balance_precisions` | `["0.0001", "0.01"]` | `["0.0001"]` |
| `fee_policy.acceptance` | Maximum of legacy and both precisions' unit-split bounds | Maximum of legacy, the $0.0001 unit-split bound, and the closed-form `q·(0.07·p(1−p) + 0.0101)`. See "Fee bound choice" below. |
| `fee_policy.sources` | 3 Kalshi docs | + `fee_evidence_refresh_summary.json` (aggregate; raw records private) and the KalshiEX Rulebook (member definitions) |
| `fee_evidence_policy` | "not permission to narrow acceptance costs" | Adopts $0.0001 for this study only, from the account's own fee records, labeled as *not* a Kalshi classification, with a tripwire |
| `clock_policy.restart` | "reject … reboot or suspend discontinuities" | "reject wall-clock/continuity-clock disagreement and any reboot". A suspend is a gap, not a discontinuity (closes B6, matching the O1 sleep fix). |
| `profitability_validation_mode` | `BLOCKED_PENDING_FEE_AND_EXECUTION_EVIDENCE` | `EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER` |
| `candidate_release` | `5.2.0-…` | `5.4.0-study005r1-draft`; the label is finalized at the freeze |

**Unchanged:** all acceptance thresholds (5,376 forecasts, 100 fills, 40 days, full holdout coverage, stress lower bound > 0, Brier, ECE 0.05); the uncertainty method; pacing; outcomes; the drift-free primary strategy; the 15 s reaction delay; the 1.5¢ stress; one-unit hypotheticals; and `advice_allowed` / `execution_allowed = false`. `activation_allowed` stays `false` in the draft. Only the freeze flips it.

## Fee bound choice: DECIDED 2026-09-29, maximum of both

The user chose the maximum of the closed form, the $0.0001 unit-split bound and the legacy term; this is what the code in `qa/study005r1_release/` implements. Checked on 1,528 single-level prices (every cent plus a sub-cent sweep): the closed form is the binding term every time, so the unit-split bound is a redundant guard in practice. The legacy term binds only when an entry consumes several partial levels. A final independent review still covers the choice.

Original analysis:


The code already computes a $0.0001 unit-split bound that is **tighter** than the proposal's closed-form bound. Per contract, one displayed level:

| Entry price | Actual fee (1 fill) | Unit-split $0.0001 (in code today) | Closed-form (proposal) | Today's acceptance (includes $0.01) |
|---|---|---|---|---|
| 0.20 | 0.0112 | 0.0200 | 0.0213 | 0.8000 |
| 0.50 | 0.0175 | 0.0200 | 0.0276 | 0.5000 |

Both are valid upper bounds. **Recommendation: take the maximum of both**, plus the legacy term. That keeps the independently reviewed closed-form bound as a floor and never goes below it. The cost is about 0.8¢ per contract at 50¢, versus the 48¢ removed by dropping $0.01. The legacy term, which rounds each level's quantity up to a whole contract, stays too. It is conservative, and it can dominate when an entry consumes several partial levels.

## Code the freeze must change

**Implemented 2026-09-29 in the separate copy `qa/study005r1_release/`** (this candidate and the Acer staging are untouched). See that folder's `STATE.md` for the change list and test results. One further gap turned up while implementing: `btc_copilot_evidence.report()` validated with Study 002's validator, so Study 005's release report would have refused to run. It now takes the readiness validator. Item 2 below was done without changing Study 002's validator: `readiness_policy` checks the revision-1 fee values itself, then hands the legacy validator an isolated copy.

The validators hard-code the current protocol, so the revision cannot just be a JSON swap:

1. **`readiness_policy.py`** pins `STUDY_ID`, the Dec 14 dates, the deadline and the preserved-policy digest, and it **raises on `activation_allowed: true`**. It needs revision-1 constants and a separate release-mode check that accepts `activation_allowed: true` only for a sealed status.
2. **`study_policy.validate_protocol`** rejects `balance_precisions != ["0.0001", "0.01"]` and any other `profitability_validation_mode`. Accept the revision-1 values for the revision-1 identity only.
3. **`study_policy.execution_cost`** takes the maximum over `('.0001', '.01')`. Drop `.01` from the acceptance maximum and add the closed-form bound. The `.01` column can remain an exploratory report line.
4. **`evidence_runner.py`:**
   - `verify_candidate` requires `WORKING_INVENTORY…` status and a `QA_ONLY` file, and `preflight` hard-codes the fee-infeasible blocking reasons. Add a frozen-release verifier.
   - **Pass that verifier to `ReleaseGate(verify=…)` in `main()`. Today it is `verify=None`**, so a frozen release's files would not be re-hashed against its manifest at each gate check.
5. **Tests.**
   - `test_readiness_protocol` asserts that `["0.0001"]` is rejected. Retarget it to the archived protocol and add revision-1 tests.
   - **Add an end-to-end test that `ReleaseGate` opens with the real validator on a frozen fixture.** Today's "opens" test swaps in `lambda p: p`, so the real activation path has never run.
6. **Release manifest:** sealed status, no `QA_ONLY`, re-bound hashes. Update the runbook, and `install.sh`'s version label.

Attestation can't verify balance precision, because no API field exposes it and Kalshi support couldn't state it. The account's own fee ledger is the evidence (see below). The 4-hourly check still blocks on any fee-type, multiplier or scheduled-change drift.

## Fee evidence standard: DECIDED 2026-09-30 (replaces "Kalshi confirms in writing")

Kalshi support could not state whether the account is a direct member. $0.0001 precision is adopted on this evidence instead, and the protocol labels it as such:

1. **The account's own fee ledger.** A GET-only reconciliation of every KXBTC15M taker order (`btc-readiness/fee_evidence_refresh.py`). On 2026-09-30, every decisive order matched the $0.0001 model exactly and not the $0.01 model, and **none matched only $0.01**, reproducing the original evidence. Raw records stay in `private/` (owner-only); only the aggregate summary ships, bound in the release manifest.
2. **How the account was opened.** Per the account holder, directly on kalshi.com under Kalshi's Member Agreement, not through an FCM or IB. In the KalshiEX Rulebook, FCM and IB customers are the intermediated (non-direct) participants.
3. **Kalshi's answers, recorded as given.** Support declined to state the classification (2026-09-30). The protocol says the precision was *established from the account's own fee records, not a Kalshi classification*.
4. **Tripwire.** Any order matching only $0.01 invalidates the fee assumption. `release_tool.py seal` refuses unless the summary's tripwire passed within 72 h. It must be re-run before any advice is ever considered.

**In-study evidence (owner decision B, 2026-09-30):** if none of the owner's own KXBTC15M orders fall inside the study window, the post-study refresh still verifies fees, provided it passes the tripwire. The report then sets `reviewer_must_weigh`, saying that precision for the period is carried from earlier records. Codex had recommended failing closed; the owner chose not to make eligibility depend on trading. The final review must cover this decision.

Residual risk: a future reclassification, for example if the account ever trades through a broker. The tripwire covers this whenever new fills exist. This replaces a precondition set in advance, so the final Reasonix review must cover it explicitly.

## Acceptance check split: DECIDED 2026-09-29 (for the final review)

Study 002's evaluator hard-coded `fee_execution_assumptions_verified: False`, so no study could ever be marked eligible for independent review. Revision 1 splits it:

- **`fee_assumptions_verified`** is true only when the protocol adopts $0.0001 from the account's fee records (`profitability_validation_mode: EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER` and `balance_precisions: ["0.0001"]`).
- **Execution stays unverified and is not a machine check.** The report states that fills are hypothetical (one taker fill per displayed level, from the first quote 15–35 s after a signal, with no orders placed), and the independent reviewer must judge whether that is realistic, using the binding-term and consumed-level diagnostics. A pass means "eligible for independent review" only; it never enables advice.

Every other acceptance check is unchanged. This is a change to an acceptance gate, so the final Reasonix review must cover it explicitly.

## Path to deployment

| # | Step | Who | Size |
|---|---|---|---|
| 1 | ~~Kalshi confirmation~~ replaced by the fee evidence standard below (2026-09-30); re-run `fee_evidence_refresh.py` within 72 h of sealing (`seal` enforces this) | Engineering | At the freeze |
| 2 | ~~Choose the fee bound~~ **decided: maximum of both** (2026-09-29) | — | Done |
| 3 | ~~Code changes 1–6 with tests~~ **done in `qa/study005r1_release/`** (317 methods on the Mac; Linux suites pass on the Acer); set the real `START` at the freeze | Engineering | Done, pending freeze |
| 4 | Freeze: regenerate with the real start date, flip `activation_allowed`, drop `QA_ONLY`, seal hashes | Engineering | Short |
| 5 | Final independent review (**Codex**, user's choice 2026-09-30; previously planned as Reasonix) of everything since the last review, including `release_gate.py`/`acer_ops.py` and the step 3 changes | Reviewer | 1–2 days |
| 6 | Stage the sealed release on the Acer next to the QA one; attestation keeps running | You (`install.sh stage`) | Minutes |
| 7 | Sign `activation_approval.json` on the Acer before `start − 1 day`, bound to host, path, manifest and study | You | Minutes |
| 8 | `./install.sh activate`; collection starts at `start_utc` | You | Minutes |

**Not blockers:** signal frequency (B7) is first measured in the development week. Wiring the risk engine to a live ledger (B8) comes only after the study passes. Advice stays disabled throughout.
