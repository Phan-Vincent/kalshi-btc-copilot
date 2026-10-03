# Operational reliability — findings and candidate fixes (5.3.0 working candidate)

This builds on Codex's 5.2.0 work in progress, which already covers coherent single-transaction publication, the RUNNING/HALTED restart barrier, the writer lock, SQLite page limits and the free-space reserve. These fixes are candidate-only. Production, sealed Study 002 and the frozen packages are unchanged.

## O1 — P1: ordinary Mac sleep permanently halts collection

- **Component:** `btc_copilot.py` (the `observation_monotonic` stamp and request latency timing), checked by `study_policy.check_clock` and `coherent_runtime.EvidenceStore.publish`.
- **Root cause:** on macOS, `time.monotonic()` is `mach_absolute_time`, which stops while the system sleeps. `check_clock` requires the wall-clock delta and the monotonic delta between observations to agree within 1 s. After any sleep they disagree by the sleep length. `publish` then writes `HALTED(CLOCK_DISCONTINUITY)`, which needs operator review.
- **Live evidence on this Mac (2026-09-27):**
  - Since boot: wall clock 861,292 s, `CLOCK_MONOTONIC` 861,291 s, `time.monotonic()` 378,540 s. So about 5.6 days of the 10-day uptime was sleep.
  - `pmset` recorded 0.5–23 h of sleep per day over Sept 21–26.
- **Reproduction:** `test_operational_fixes.SleepContinuityTests`. Before the fix, `continuity_clock` didn't exist and the observation stamp used the uptime clock. `test_uptime_clock_during_sleep_would_halt` shows the halt a 10-minute sleep caused. Pre-fix output is in `evidence/operational_fixes_before.txt`.
- **Fix:** add `continuity_clock()`, which uses `CLOCK_MONOTONIC` on macOS and `CLOCK_BOOTTIME` on Linux. Both keep counting through sleep, and neither is stepped by wall-clock changes. Use it for `observation_monotonic`, for request latency, and for the evidence runner's pacing.
- **Still enforced:** a wall-clock step without matching elapsed time still halts (`test_wall_step_without_elapsed_time_still_halts`). A reboot still halts (boot identity check).
- **Policy note for review:** the preserved protocol text says to reject "reboot or suspend discontinuities". With a sleep-inclusive clock, a suspend is no longer a clock discontinuity. It becomes a **gap**, and the unchanged missing-data rule handles it: any missed checkpoint slot still prevents favorable review. The next sealed protocol should reword the clause to "wall-clock / continuity-clock disagreement" so the text matches the behavior.
- **Sealed Study 002 is not fixed.** It still uses `time.monotonic()`. It will halt at the first sleep after its Sept 30, 5 pm PT start unless the Mac is kept awake (see the deployment proposal).

## O2 — P1: any non-network poll failure kills a 70-day collector

- **Component:** `evidence_runner.run_authorized`.
- **Root cause:** the loop caught only `ReadError`. Two ordinary events escaped the loop:
  - a slow poll whose snapshot expired before commit (`RuntimeBlocked('Expired or future publication')`);
  - a malformed payload (`ValueError`).

  Either one ended the process with `close(clean=False)`, which leaves `RUNNING` and requires operator review. Codex's own `OPERATING_RECOVERY.md` says stale or failed collection should degrade and recover on a later poll.
- **Reproduction:** `CollectorResilienceTests`. `test_expired_publication…` and `test_malformed_response…` failed before the fix.
- **Fix:**
  - Any exception from a poll is recorded as degradation (the pipeline already records it) and the loop continues.
  - Still fatal: a halted store (clock, storage, integrity), a gate that is revoked when rechecked after the failure, and 40 consecutive failed polls (`MAX_CONSECUTIVE_FAILURES`, 10 minutes).
  - The failure counter resets on a successful poll.

## O3 — P3: working manifest was stale

`release_manifest.json` omitted `evidence_runner.py`, which `verify_candidate` requires, so `preflight()` would have reported `inventory_valid: false`. It is regenerated for 5.3.0. Offline preflight now reports a valid inventory, with all five blocking reasons still in place.

## O4–O9 — found by the independent Reasonix operations review, confirmed and fixed

Reasonix (deepseek-v4-pro, workspace-write in a disposable copy) reproduced each of these with scratch scripts. I reproduced each again as a regression test in `ReasonixOpsFindingTests`. Seven of those tests failed before the fixes; the output is in `evidence/reasonix_ops_findings_before.txt`.

| ID | Severity | Defect | Fix |
|---|---|---|---|
| O4 (R1) | P1 | After O1, a sleep spanning one request made its latency exceed 15 s. `check_clock` raised a message containing "clock", so `publish` halted. | `check_clock` now separates the two cases. A disagreement between the wall clock and elapsed time is still a halting discontinuity. An over-budget read raises "Request stale beyond read budget", which is recorded as degradation. |
| O5 (R2) | P1 | `settle_one` still timed its read with `time.monotonic()`, so a ~2 s sleep during a settlement read halted the store. | Uses `continuity_clock()`. Only a wall/continuity disagreement or a backward step halts. A slow read, or one finishing after release, is discarded and recorded as degradation. |
| O6 (R3) | P2 | A single settlement failure other than `ReadError` killed the run uncleanly. | The settlement path uses the same tolerance as collection, with its own counter of consecutive failures. |
| O7 (R4) | P3 | A forward wall-clock step across `release_utc` produced a *clean* stop that left no review marker. | The loop compares the wall-clock delta with the continuity-clock delta between polls. A step raises and stops uncleanly (`RUNNING` stays). |
| O8 (R5) | P3 | A mid-run publication/generation digest mismatch counted as 40 transient failures before stopping. | `_verify` writes `HALTED(INTEGRITY_MISMATCH)` immediately. |
| O9 (R6) | P3 | An externally written `HALTED` file wasn't treated as fatal until 40 failures. | The loop's fatal check reads the on-disk `HALTED` marker as well as the in-memory flag. |

Reasonix also verified two properties that hold: the failure counter resets only on success, and a sleep between the pre-commit and commit freshness checks degrades rather than halting.

## Not changed

Storage sizing stays at the 128 GiB quota plus 16 GiB reserve in `operating_limits.json`, per Codex's measurement. Coherent publication, locking and the halt barrier were not modified. The independent Reasonix review of these areas is in `REASONIX_FINDINGS.md`.

## Tests

`python3 -B qa/readiness_release/run_checks.py` covers the full candidate:
- 269 Python methods, all passing except 1 intentional legacy skip;
- 14/14 golden scenarios;
- 24/24 UI checks;
- production sources unchanged.

New in this session: 19 operational regressions, 4 fee-bound tests, and the risk suite rewritten from 33 to 38 tests.
