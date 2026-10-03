# Study 005 revision 1: working copy (2026-09-29)

> **CURRENT: 5.4.1-study005r1 SEALED AND STAGED (2026-10-01 06:47 UTC)**, manifest `21c5c58fe7770ea3…` (`sealed/`). It replaces 5.4.0 (`sealed_5.4.0/`, manifest `8cc42ed4…`), which the owner approved and activated at 06:12 UTC. Its status then raised false CRITs (no 'activated, waiting for start' state); one CRIT Telegram went out at 06:15, and RECOVERED at 06:47.
> The 5.4.1 fix:
> - status has a waiting-for-start state (the timer must be armed, `enabled/active`; approved-but-unarmed is a WARN, CRIT within 24 h of the start);
> - a 10-minute startup grace;
> - `activated` now means armed;
> - the watch checks the approval whenever one exists;
> - `stage` stops and disables the collector fail-closed before switching releases.
>
> These were reviewed by Codex in two targeted passes, and the fail-closed check was verified on the Acer. Mac 357 methods, Acer 216 + 51 pass.
>
> **APPROVED AND ACTIVATED by the owner, 2026-10-01 07:00 UTC.** On the Acer: `current` → `releases/5.4.1-study005r1`; approval verified; collector timer `enabled/active` for **2026-10-12 00:00 UTC**; status OK, 'Activated; collection starts…'. 5.4.0 (with its first approval) and 5.3.0 are kept for the record.
>
> **Next:** collection starts Oct 12; development week Oct 12–19, validation Oct 19–26, holdout Oct 26 → Dec 21; release report Dec 23 (see 'After the study').

This is a separate copy of `../readiness_release/`, which stays byte-identical to what is staged on the Acer (manifest `cc33084d…`). Everything here is still a QA candidate: `QA_ONLY` is present, and the protocol says `PROPOSED_NOT_APPROVED` / `activation_allowed: false`. **Nothing here is deployed, and nothing enables advice or execution.** The spec is `../readiness_release/proposals/study005_r1/PROTOCOL_REVISION_R1.md`.

**Fee bound:** decided 2026-09-29: the maximum of the closed form, the $0.0001 unit split and the legacy term (already implemented). The closed form binds at every single-level price tested.

**Acceptance check split (decided 2026-09-29):** the release report's hard-coded `'fee_execution_assumptions_verified': False` is replaced by `fee_assumptions_verified`. It is true only when the protocol records the Kalshi-confirmed $0.0001 precision (`profitability_validation_mode` plus `balance_precisions`), so it stays false for Study 002. Execution is **not** machine-verified. The report now carries `execution_assumptions: {machine_verified: false, reviewer_must_assess: …}`, and the independent reviewer judges whether the fill model is realistic. This must be fixed before the freeze, because the evaluator source is archived at start.

**Fee evidence (decided 2026-09-30):** Kalshi support couldn't state the account's classification, so $0.0001 is adopted from the account's own fee ledger. The summary is `candidate/fee_evidence_refresh_summary.json`, bound in the manifest: every decisive order matches $0.0001 only; none match $0.01 only. It is labeled in the protocol as not a Kalshi classification. `seal` refuses unless its tripwire passed within 72 h. Profitability mode: `EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER`.

**Nothing external is blocking.** START is set to 2026-10-12. The final independent review is running with **Codex** (user's choice, 2026-09-30) in a sandboxed disposable bundle. After it: fix findings, re-run the fee refresh (under 72 h before sealing), seal, stage, the user's approval before Oct 10 5 pm PT, then activate.

## Independent review (Codex)

**Round 1 (2026-09-30): FIX P0 FIRST.** All 8 findings reproduced locally and were fixed, each with a regression test:

| Finding | Fix |
|---|---|
| P0 gate accepted approval after the protocol deadline (also in 5.3.0) | `release_gate` requires `approved < approval_policy.deadline_utc` |
| P0 unlisted `__pycache__` bytecode could load under `-B` | Caches refused in a sealed release by the verifier, the launcher and `install.sh` |
| P0 tampered source ran before its hash check | New stdlib-only `launch.py`: verifies every file, and that the manifest is the approved one, **before any import**; systemd runs `python3 -I -B launch.py --collect` |
| P1 seal followed symlinks; minimum rather than exact file set | `seal` refuses links and non-regular files; exact `SEALED_FILES` set |
| P1 seal verified with cached modules | `seal` verifies in fresh `-I` interpreters (the verifier plus `launch.py --verify-only`) |
| P1 fee summary trusted its own verdict | `fee_reconciliation.fee_evidence_problems` recomputes the tripwire from pagination, classes, periods and daily totals |
| P1 precision could drift during the study | `report(..., fee_evidence=)` needs a post-study refresh (retrieved at or after `end_utc`) that passes the recomputed tripwire; it reports decisive orders inside the study window |
| P1 caller-supplied future time unlocked the holdout | `report()` refuses times beyond the real clock (+5 min). Not a custody boundary against reading the database directly |

Codex also sampled 10,800 fill groupings against the fee bound and found no counterexample. It tried to call Reasonix on its own through a connection configured in the user's Codex setup (it failed with HTTP 504); round 2 ran with `mcp_servers={}`.

**Round 2 (2026-09-30): FIX P0 FIRST.** Three round-1 fixes were confirmed; the rest were partial, plus new items. Fixed:
- The systemd units are sealed into the release (hash-bound). Collector and attest run a `/usr/bin/sha256sum` pre-check of `launch.py` before any Python. `activate` byte-compares the installed units and runs `launch.py --verify-approval`. The launcher refuses installed units that differ from the sealed ones.
- Fee evidence: fee-rule fields (quadratic x1, no changes) and nested types are validated. `seal` re-derives every count from the raw private files (`fee_evidence_refresh.py raw_problems`); the refresh script moved here, and the old path is a wrapper.
- Report: no clock allowance. The refresh must be dated between `end_utc` and the report time, and zero decisive in-study orders **still verifies, with a `reviewer_must_weigh` flag** (owner decision B, 2026-09-30: eligibility must not depend on the owner trading; `ALLOW_NO_IN_STUDY_EVIDENCE=True`).
- Malformed inputs are controlled refusals. `seal` uses `--verify-files`, so the build host's units are irrelevant.
- Proven on Linux with a real seal: the pre-check passes on a genuine launcher and blocks a tampered one; the launcher refuses while the 5.3.0 units are installed and verifies with matching units.

**Round 3 (2026-09-30): FIX P0 FIRST**, but narrower: most earlier fixes were confirmed. Fixed:
- `install.sh` checks the launcher's hash with `shasum` before running it.
- `retrieved_at` is bound to the raw file names, and daily counts must sit inside their periods. `fee_evidence_refresh.py --verify SUMMARY` exists, and the refresh's exit status comes from the recomputed checks.
- The launcher requires all sealed units installed and identical, and rejects extra units.
- The watch hash-checks `acer_ops.py` and raises a CRIT, 'Release verification failed', when the rest of the release fails verification. Attest checks `acer_ops.py` too.
- Accepted under the threat model: pagination metadata cannot be re-derived from the raw records.
- All proven on Linux with a real seal.

**Owner decision B** is implemented: no in-study decisive orders still verifies, with `reviewer_must_weigh`.

**Round 4 (2026-09-30): DO NOT SEAL** (no P0; three P1s and one P2). It confirmed decision B and most earlier fixes. Fixed:
- The watch's verification skips only a manifest-identified working inventory. A missing manifest or launcher, or a stray `QA_ONLY`, is a CRIT.
- `install.sh status` hash-checks `acer_ops.py` first, and the docstring describes decision B.
- **Latent release-day bug found and fixed:** the old report entry opened `runtime/btc_copilot_study.sqlite3` with Study 002's validator, but the collector writes `runtime/evidence.sqlite3` under Study 005's protocol, so the Study 005 report could not have been produced. `evidence_runner.py --report` now uses the right database and validator and refuses cleanly with exit 2. It is tested end-to-end through `EvidenceStore`.
- New supported command `release_tool.py post-study-report`: raw-verify the summary, then copy it to the Acer, then run `launch.py --verify-only` and `--report` there, then save the result on the Mac. The report records the summary hash.

**Round 5 (2026-09-30): VERDICT SEAL** (no P0 under the threat model). Its P1/P2 follow-ups were fixed anyway:
- The watch skips verification only when the `QA_ONLY` marker, the working manifest status and an inactive protocol all agree.
- The legacy Study 002 report entry refuses inside a Study 005 release.
- `post-study-report` verifies one byte snapshot, sends exactly those bytes, and passes their SHA-256. `evidence_runner.py --report` refuses without a matching digest, and the report records that digest.
- The runbook uses hash-checked `install.sh status` and `install.sh send-test`.
- (Codex's note that `STATE.md` still pointed at the legacy command came from a bundle built just before the update.)

Final state: Mac `run_checks.py` 351 methods passed (1 skip), 14/14 golden, 24/24 UI. Acer (Linux, real seal): 210 readiness (1 skip) and 51/51 candidate; all unit pre-checks pass.

## What changed from 5.3.0

| File | Change |
|---|---|
| `candidate/readiness_protocol.json` | The revision-1 protocol, **start 2026-10-12** (user decision 2026-09-30): holdout Oct 26 → Dec 21, release Dec 23, approval deadline 2026-10-11 00:00 UTC (Fri Oct 10, 5 pm PT). Built by `make_revision.py`. |
| `candidate/readiness_policy.py` | Revision-1 identity and dates derived from one `START` constant. The digest binds the new fee and clock text and requires `balance_precisions == ["0.0001"]`. **`activation_allowed: true` is accepted only with `readiness_status: SEALED_EVIDENCE_ONLY`**; advice, execution and reuse are always false. |
| `candidate/study_policy.py` | `execution_cost(rows, settings, balance_precisions)`. The default (both precisions) is unchanged, so Study 002 and the golden suites are unaffected. `["0.0001"]` drops the $0.01 bound and takes the maximum of legacy, unit-split $0.0001 and the closed form. `validate_protocol` (Study 002's) is unchanged. |
| `candidate/btc_copilot_evidence.py` | `Study.record` costs fills with the protocol's own precisions. `report(path, now, validator=…)` lets the release report validate a Study 005 protocol. The old code would have refused it as "Unsupported study identity". |
| `candidate/evidence_runner.py` | `verify_release()` for sealed releases: every listed file is re-hashed, no `QA_ONLY`, no unlisted files, and the protocol and manifest identities match. **`main()` now passes it to `ReleaseGate(verify=…)`**; before, it was `verify=None`. The preflight's blocking reasons follow the protocol's precisions. |
| `release_tool.py` (new) | `inventory` rewrites the working manifest. `seal OUT_DIR` builds a sealed copy (the mechanical half of the freeze) and verifies it with the copy's own code. |
| `tests/test_study005r1_release.py` (new, 49 tests) | Default costs identical to 5.3.0 over 398 books. Revision-1 cost equals the maximum of its three terms. The Study-level fill is costed at $0.0001. **The sealed release opens with the real validator and verifier**, and `run_authorized` accepts it. Tampering, unlisted modules, protocol edits, `QA_ONLY`, late approval and out-of-window times all block. |
| Binding-term diagnostics (added 2026-09-29) | On the revision-1 path, each costed entry's execution evidence records `binding_fee_terms` (which of `closed_form` / `unit_split_0001` / `legacy_level` set the fee; ties list all), `legacy_level_fee_bound` and `consumed_levels`. The report adds `fee_bound_binding` counts per strategy and phase, using no outcomes, so the development week shows how often thin books make the legacy term bind. Diagnostics only; costs are unchanged. The Study 002 path has no such fields. |
| Acceptance check split (added 2026-09-29) | `btc_copilot_evidence.fee_assumptions_verified(p)` replaces the always-false placeholder, and the report adds `execution_assumptions`. Tests: a released empty revision-1 study shows the fee check true but is still not eligible; the helper is false for Study 002 and for any altered fee fields. |
| Existing tests | Fixture dates are derived from `readiness_policy.START` instead of Dec 14 literals. The protocol tests are updated for revision 1, with explicit legacy-diff fields and the sealed/proposed status rules. |

**Results:** `run_checks.py` on the Mac: 351 Python methods passed (1 intentional skip), 14/14 golden, 24/24 UI, production sources unchanged. On the Acer (Linux, Python 3.14): 210 readiness tests (1 skip) and 51/51 candidate tests.

**Finding:** at extreme prices, revision 1 is slightly stricter than today. At a 99¢ entry, the closed-form bound (about 1.08¢) exceeds today's 1¢ acceptance. That is expected under "maximum of both".

## After the study (on or after Dec 23)

1. On the Mac, after 2026-12-21 00:00 UTC: `python3 -B qa/study005r1_release/fee_evidence_refresh.py`. The GET-only refresh must pass its tripwire.
2. After the release time, 2026-12-23 00:00 UTC: `python3 -B qa/study005r1_release/release_tool.py post-study-report`. This is the only supported report path. It verifies the summary against the raw records first, and saves `btc-readiness/study005_release_report.json`. Holdout outcomes stay locked by the real clock until release.

## Freeze steps

1. Set `readiness_policy.START` to the real date, and regenerate the protocol with `../readiness_release/proposals/study005_r1/make_revision.py --start …`. The `PRESERVED_POLICY_SHA256` digest excludes the dates, so it doesn't change.
2. Set the final `CANDIDATE_RELEASE` label in both the policy and the protocol. Re-run `../../fee_evidence_refresh.py` and copy its summary into `candidate/`; it must pass the tripwire, and `seal` checks it is under 72 h old. The summary text is outside the policy digest, but the manifest binds it.
3. Run `release_tool.py inventory`, then `run_checks.py` on the Mac and the Acer.
4. Final Reasonix review of the changes since 5.3.0, plus `release_gate.py` and `acer_ops.py`.
5. `release_tool.py seal <dir>`; stage it on the Acer; the user signs `activation_approval.json` before `START − 1 day`; `install.sh activate`.
