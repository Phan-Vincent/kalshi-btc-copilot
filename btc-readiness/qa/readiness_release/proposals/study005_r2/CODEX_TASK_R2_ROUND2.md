# Codex task, round 2: Study 005 r2 review fixes (offline, tests only)

The ground rules from `CODEX_TASK_R2.md` still apply: write only inside `study005r2_release/`, work offline, GET-only, use synthetic fixtures, and add a regression test for each change. Read the updated `PROTOCOL_REVISION_R2.md` (items 4 and 10 changed, and the D1 hardening section was revised).

## Changes

0. **macOS test failures (found by Claude's independent `run_checks.py`, which exited 1).** `test_study005r2.PersistenceTests.test_failure_detail_is_short_scrubbed_and_durable` and `test_observation_trims_raw_trades_but_checkpoint_keeps_them` both raise `RuntimeBlocked('Linked runtime path forbidden')`. They use `root=Path(td)`, and on macOS `/var` is a symlink to `/private/var`. Use `Path(td).resolve()` in every new temporary-directory test, as the existing suites do (`test_coherent_runtime.py:11`, `test_evidence_runner.py:16,43`). Don't relax `no_links`. Your sandbox's temp directory isn't a symlink, which is why they passed for you; never report a pass that only holds in your sandbox.
1. **Protocol text changed.** `make_revision.py` was updated and the draft regenerated: `trade_window_policy` now says only checkpoint records keep the raw window. The new draft's SHA-256 is `fe7dcea3b35c975cbcf53984c7db484ab166743772f05da00d0c633c9dbfdacd`. Copy it byte for byte to `candidate/readiness_protocol.json` and update `readiness_policy.py`'s digests. Run `make_revision.py --check`.
2. **Storage (item 4).** Signal/decision payloads must **not** carry the raw trade window. They keep the aggregates, the count, the first/last `trade_id` and the canonical SHA-256, the same as observations. Only checkpoint records keep raw trades. Prove that the signal flow veto and every report path work from the aggregates alone.
3. **Re-measure.** Update `tests/measure_storage.py`'s projection to match: drop the "two blobs per eight arms" raw-window term; signal rows are now small. Keep the ×2 uncertainty multiplier. Report the projection against **both** the 128 GiB quota and the runtime's main-database cap, `(quota − 1 MiB) / 3`. Save the result to `evidence/storage_9000.json`.
4. **Seal storage gate (item 10).** Replace "projected storage < 50% of quota" with: the shakedown's **measured** database growth rate × 72 days × 2 is at most the main-database cap. The summary records the measured bytes/day, the projection and the cap. Update `GateTests`.
5. **Physical gates.** Drop the UPS and backup-uplink requirement from the open items (owner decision, 2026-10-12: no backup internet). Don't add any physical-check schema.
6. **Trade-window edge.** The owner's answer is your current behavior: flow aggregates use the analysis-window subset, and the SHA-256 covers the full collection-window set. Remove it from the open items.

## Deliverables

- an updated `CHANGES.md`, with a "Round 2" section;
- passing `run_checks.py` output under `evidence/`;
- the new storage projection numbers stated in the summary.
