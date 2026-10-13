# Codex task: implement Study 005 revision 2 (offline, tests only)

Read `PROTOCOL_REVISION_R2.md` (next to this file) first. It is the spec; this file is the work order. The draft protocol `readiness_protocol.r2.draft.json` is already built by `make_revision.py --start 2026-10-26`. Don't edit the draft by hand; if it must change, change `make_revision.py` and regenerate it.

## Ground rules

- Work only in a **new** directory, `btc-readiness/qa/study005r2_release/`. Create it by copying `btc-readiness/qa/study005r1_release/` without `sealed/`, `sealed_5.4.0/` and any `__pycache__`. **Don't modify** `study005r1_release/`, `readiness_release/` or anything on the Acer.
- There's no network in the sandbox. All tests are offline, with synthetic fixtures. Never read real study evidence or any `runtime/` database.
- All Kalshi access stays GET-only. `advice_enabled`, `execution_enabled`, `advice_allowed` and `execution_allowed` stay false. Don't add any order, position or account write path.
- Match the existing code style (dense, terse). Add a regression test for each item.
- Leave the candidate as a QA working copy: `QA_ONLY` present, protocol status `PROPOSED_NOT_APPROVED`. Sealing is a later, separate step.

## Work items

1. **Trade window pagination** (`candidate/btc_copilot.py`, the `trades` job near line 433, and the `trade_flow` block near 528). Follow cursors on `/markets/trades` up to 30 pages of 1,000, with every page through the existing pacer. The window is `[collection_epoch − 180, collection_epoch]`; pass `max_ts` too if the client supports it. `truncated` is true only when a cursor remains after the cap.
2. **Window-edge bug** (`validated_trades`). Today the request uses `int(collection_now) − 180` but validation uses `analysis_now − 180`, so a trade in that sliver raises `ReadError` and the whole poll fails. Validate against the request's own window, and **filter** trades outside the analysis window instead of raising. Keep the identity, duplicate, ticker and quantity checks fatal.
3. **Pacing and deadline.** Prove with a test that a 30-page window plus the normal jobs finishes inside `valid_until_epoch` (first request start + 20 s) under the `request_pacing` budget (60 tokens/s, 10 per page, 50 for the benchmark). If it can't, lower the cap and say so in `CHANGES.md`.
4. **Storage** (`candidate/btc_copilot_evidence.py`). Observations keep the trade aggregates, the count, the first/last `trade_id` and the SHA-256 of the canonical trade set, not the raw trades. Checkpoint records and signal/decision records keep the raw window. Update `tests/measure_storage.py` for 9,000-trade windows and report the projected GB/day and the 72-day total against the 128 GiB quota.
5. **Failure reasons.** `runtime_events` gains a `detail` column: the exception class plus a message of at most 200 characters, scrubbed of credentials and paths. Record it for every `COLLECTION_FAILED` and on every collector stop.
6. **Health counts real checkpoints** (`candidate/acer_ops.py`). Coverage counts slots with an enrolled checkpoint (`markets.checkpoint IS NOT NULL`), not "any observation in the band". Status and the watcher report the top veto reasons for missed slots. Add a WARN when poll failures exceed 5% in the last hour, and a CRIT above 20%. The status reader stays read-only and must not read outcomes.
7. **Second chance in the band.** Schedule polls so that at least two healthy attempts fall inside 570–600 s remaining (for example, an extra poll aligned to 585 s remaining), without exceeding the pacing budget. The checkpoint rule is unchanged: the first healthy observation in the band.
8. **Missing-slot budget and imputation in the report** (`btc_copilot_evidence.py` near line 352, `study_policy.py`). Replace `complete_scheduled_checkpoints` with the protocol's `missing_policy` and `missing_slot_imputation`:
   - at most 2% missing, each with a logged reason (from item 5, a veto reason or a recorded stop);
   - no fully missing calendar day;
   - Brier, ECE and stressed daily P&L imputed exactly as the protocol text says;
   - each metric reported with and without imputation, plus the list of missing slots.

   Add a property test that imputation never improves any metric, whatever the missing slots would have been.
9. **Identity and validators.** In `candidate/readiness_policy.py`: `START = 2026-10-26`, `STUDY_ID` and `CANDIDATE_RELEASE = '5.5.0-study005r2'`. Update `EXPECTED_KEYS` with the two new fields, `MODIFIED_FIELDS` and `PRESERVED_POLICY_SHA256`. Also handle `study_policy.py`'s hard-coded `holdout_coverage: 1.0` without breaking Study 002's validator. Copy the draft protocol in as `candidate/readiness_protocol.json` with the QA status.
10. **Shakedown mode and seal gate** (`release_tool.py`, `evidence_runner.py`).
    - Add a `shakedown` mode. It runs the real collector against live Kalshi into a separate runtime directory (`runtime_shakedown/`, never `runtime/`), is labeled not study evidence, and needs no approval file.
    - `release_tool.py shakedown-summary` writes `shakedown_summary.json` with: hours covered, slots, enrolled checkpoints, missed slots with reasons, poll failure rate by hour, and storage growth.
    - `seal` refuses unless the summary is under 72 h old and shows at least 72 h, at least 95% enrolled, every miss with a reason, a failure rate of 1% or less per hour, and projected storage under 50% of the quota. The manifest binds the summary.
11. **Tests and checks.** `run_checks.py` passes on the Mac. List anything that needs the Acer (Linux-only suites, the real-seal tests) in `CHANGES.md` for Claude to run there.

## Deliverables

- the `study005r2_release/` tree;
- `study005r2_release/CHANGES.md`: per item, what changed, its tests, and any deviation from the spec;
- an updated `STATE.md` header for r2;
- the `run_checks.py` output saved under `evidence/`.

Stop and write down any spec ambiguity in `CHANGES.md` instead of guessing. The imputation formulas and the 2% budget are owner decisions, so don't change them.
