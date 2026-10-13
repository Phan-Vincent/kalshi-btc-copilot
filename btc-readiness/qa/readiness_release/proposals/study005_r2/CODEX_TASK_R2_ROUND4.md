# Codex task, round 4: resume after a reboot (owner decision D2 = b, 2026-10-12)

The ground rules from `CODEX_TASK_R2.md` still apply: write only inside `study005r2_release/`, work offline, GET-only, use synthetic fixtures, and add a regression test for each change. Use `Path(td).resolve()` in every temporary-directory test (`/var` is a symlink on macOS). Read `study005r2_release/CHANGES.md` rounds 2–3c first: the code has moved on since round 1 (sliced trade fetch, skew scope, `FinalCutoff`, orderly shakedown stop).

## The decision

Today a reboot permanently ends a runtime. `EvidenceStore` sees a new boot identity, calls `_halt('RUNTIME_OR_BOOT_CHANGED')`, and refuses; the RUNNING marker left by the killed process also refuses ("Unclean shutdown requires operator review"). One reboot in 10 weeks would end the study. **The owner chose to resume after a reboot in the same database**, with the gap counted as missing slots (reason "reboot") against the 2% budget.

The protocol text is already regenerated. `make_revision.py` now changes `clock_policy.restart` and extends `missing_policy`; the new draft is `readiness_protocol.r2.draft.json`, SHA-256 `d6921132fe90f82cdfc259ea70d9d2458903ebbe470ca09c6359b096f9a69715`. Copy it byte for byte to `candidate/readiness_protocol.json`, update `readiness_policy.py`'s digests and expected fields (`clock_policy` is now partly modified), and run `make_revision.py --check`. Read the new `clock_policy.restart` text: it is the specification.

## Work items

1. **`coherent_runtime.EvidenceStore`.** When the stored boot differs from the current boot, resume instead of halting. On that path:
   - **The RUNNING marker** left by the pre-reboot process does not refuse. A RUNNING marker from the *same* boot still refuses exactly as today.
   - **Persisted HALTED.** A HALTED marker with any other reason still refuses. `RUNTIME_OR_BOOT_CHANGED` halts written by older code may be treated as resumable only if the stored boot differs and nothing else changed; state your choice in `CHANGES.md`.
   - **Same checks as before:** the integrity check, the exact config match (version, quota, reserve) and the private-directory and lock checks all still apply. Only the boot field may differ.
   - **Clock order:** the wall clock must be ≥ the persisted `last_epoch`, or refuse.
   - **In one transaction:** write a `REBOOT_RESUME` runtime event whose detail holds the previous boot prefix (8 chars), the new boot prefix and the last persisted observation epoch; set `runtime_control.boot` to the new boot; and clear the persisted continuity-clock baseline (`meta.last_monotonic`) so `check_clock` starts a new baseline. Keep `last_epoch`: wall time must still advance.
   - **No silent paths:** any failure on this path refuses (halts) as today.
2. **Attestation after the reboot** (`evidence_runner.run_authorized`, or the gates). Resuming after a reboot requires a fee/clock attestation written *after the current boot*. The gate already checks freshness and clock offset; add the after-boot condition (the boot time comes from `/proc/uptime` on Linux; make it injectable for tests). It applies to both the study (`ReleaseGate`) and the shakedown (`ShakedownGate`) paths.
3. **Missing-slot reasons.** Everywhere a missed slot's reason is collected, a slot whose checkpoint band lies between a `REBOOT_RESUME` event's recorded last-observation epoch and the event time gets the reason `reboot`. That means: the release report's missing-slot list and 2% budget (`btc_copilot_evidence.py`), `release_tool.py` `shakedown-summary`, and `acer_ops.py` status. Such slots count as explained.
4. **Allowed events.** `EvidenceStore.event` accepts `REBOOT_RESUME`. It is not a failure and not degradation.
5. **Tests.** Use synthetic boot ids (the store takes `boot_id=`):
   - open with boot A, publish, kill uncleanly (leave RUNNING), reopen with boot B → resumes, writes `REBOOT_RESUME` with the right detail, sets `runtime_control.boot` to B, clears `last_monotonic`, and the next publish with a smaller continuity-clock value succeeds;
   - the same with a wall clock earlier than `last_epoch` → refuses;
   - an unclean stop on the same boot → still refuses;
   - a changed quota or version across the reboot → still refuses;
   - an attestation older than the boot → refuses; a newer one → allowed;
   - a slot spanning the reboot gap → reason `reboot` in the report, the shakedown summary and the status, and counted as explained.
6. Update `tests/test_readiness_protocol.py` for the changed fields. Regenerate the manifest (`release_tool.py inventory`) and run `run_checks.py`; it must pass on macOS.

## Not in scope

The Acer boot launcher `btc-readiness/acer/shakedown_check/shakedown_start.py` is outside the release. Claude will change it to resume instead of archiving. Don't edit it.

## Deliverables

- the code and tests;
- a `CHANGES.md` "Round 4" section (what changed, its tests, any deviation);
- passing `run_checks.py` output under `evidence/`;
- the manifest SHA-256.

Write down any ambiguity in `CHANGES.md` instead of guessing.
