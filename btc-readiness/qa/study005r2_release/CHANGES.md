# Study 005 r2 implementation record — 2026-10-12

This is an isolated QA working copy made from `study005r1_release/` without either `sealed/` tree or bytecode caches. Only files under `study005r2_release/` were written. The r2 protocol was copied byte for byte from the generated draft (SHA-256 `5ba21ddd…16049d358`); `make_revision.py --check` confirmed its pinned r1 source and untouched-field digest. `candidate/QA_ONLY` and `PROPOSED_NOT_APPROVED` remain in force. No network, real study evidence, runtime database, Acer operation, seal, stage or activation was used.

| Work item | Change | Offline regression / deviation |
| --- | --- | --- |
| 1. Trade pagination | `trade_window` follows at most 30 cursor pages of 1,000 via the GET client and keeps a cursor only at the cap. `min_ts` is floored and `max_ts` ceiled around the exact collection epoch; exact filtering happens locally. | `TradesTests` covers 1,500 trades across pages and a persistent cursor at page 30. Server support for `max_ts` needs live shakedown verification. |
| 2. Window edge | Trade schema, identity, duplicate, ticker, quantity and price checks stay fatal; out-of-window timestamps are excluded. Flow uses the analysis-window subset; the canonical hash covers the full collection-window set. | `test_window_sliver_is_valid_but_outside_analysis_is_filtered`. The protocol's phrase “every public trade” and work item 2's analysis filter leave a small edge interpretation pending owner clarification. |
| 3. Pacing/deadline | Each page uses `KalshiReadOnly.get`, hence the existing 10-token pacer. The 30-page synthetic cost plus normal jobs, including the 50-token benchmark, finishes below 20 seconds. Cap stays 30. | `test_30_page_cap_and_budget_under_20_seconds`; real network latency remains for shakedown. |
| 4. Storage | Audit observations retain aggregates, trade count, first/last ID and canonical SHA-256; checkpoint and signal/decision payloads retain the raw window. Offline replay uses the stored summary and can no longer independently reconstruct the raw trade set of an ordinary observation. | `test_observation_trims_raw_trades_but_checkpoint_keeps_them`; `tests/measure_storage.py` now uses 9,000-trade windows. The conservative 2× planning projection from 128 samples was **1.877 GB/day and 135.143 GB/72 days**, or 98.3% of the 128 GiB quota. It exceeds the 50% seal gate and the runtime’s effective SQLite main-database limit. Live measured growth must pass the seal gate; no quota or retention setting was loosened. |
| 5. Failure reasons | `runtime_events.detail` stores exception class and at most 200 scrubbed characters for collection failures and recorded collector stops. | `test_failure_detail_is_short_scrubbed_and_durable`. An abrupt power loss cannot write a stop event; an unexplained missed slot then blocks favorable review. |
| 6. Real health counts | The read-only status queries checkpoint enrollment, slot vetoes and runtime events without selecting outcomes. Watch output shows top veto reasons, warns above 5% and is critical above 20% poll failures in the last hour. | `test_status_counts_enrolled_checkpoint_not_band_observation` and updated read-only SQL test. |
| 7. Second chance | The 15-second loop also targets 595 and 580 seconds remaining when those times are ahead; no overlapping poll is started. | `test_second_band_target_is_scheduled`. Actual attempt timing and coverage need the live shakedown. |
| 8. Missing budget/imputation | The report lists every missing holdout slot and reason, limits misses to 107/5,376, retains the no-full-day gate, and reports observed and imputed Brier, ECE, paired Brier interval and stressed daily P&L. Imputed slots enter forecast count but never fills. The Study 002 report path retains its prior check. | `test_missing_slot_reasons_and_imputation_monotonicity` checks randomized missing sets and a losing fill. The comparator market Brier falls when imputed as 0.0 by the owner formula; candidate acceptance metrics can only worsen or stay equal. |
| 9. Identity/validators | START Oct 26, r2 study ID and `5.5.0-study005r2` label; exact new-field digest and inherited-policy digest; Study 002's 1.0 coverage validator runs on an isolated normalized copy. | `test_readiness_protocol.py`, copied draft hash and `make_revision.py --check`. No draft edits. |
| 10. Shakedown/seal | Explicit GET-only `--shakedown` uses `runtime_shakedown/`, a shifted QA protocol and a fresh read-only attestation; it has no approval-file dependency and cannot enter normal `runtime/`. `shakedown-summary` requires a clean collector stop, reads timing/checkpoint/reason tables and writes a database-hashed, source-bound, not-study-evidence summary. Seal requires summary age <72 h, >=72 h covered, >=95% enrolled, reasons for all misses, <=1% failures in each hour and projected storage <50% of quota; its manifest binds the summary. | `GateTests` covers summary generation, freshness, coverage, failure and storage refusals; inherited seal tests use explicit synthetic summaries in temporary copies. **No live shakedown or real seal was run.** |
| 11. Checks | `run_checks.py` keeps all seven offline suites. Its copied legacy one-page harness is adapted only in temporary test files for r2 cursor and timestamp semantics; the original audit files remain untouched. | Final run and storage output are under `evidence/`. |

Final Mac `run_checks.py`: **passed**; 367 Python methods, 2 skips, 14/14 golden scenarios and 24/24 UI checks. See `evidence/test_results.json` and `evidence/run_checks_console.txt`. The default 128-sample 9,000-trade measurement is `evidence/storage_9000.json`.

## Open specification and operational gates

- The owner was asked whether the storage seal should also enforce the runtime's stricter SQLite main-database cap of roughly one third of the 128 GiB quota. The stated shakedown gate uses 50%; the runtime's existing cap remains unchanged. This mismatch needs resolution before a real seal.
- The owner was asked which side of the collection-versus-analysis trade-window edge governs flow aggregates. The code currently follows work item 2's explicit analysis filter while retaining and hashing the full collection window.
- The protocol requires a UPS and an automatically routed backup uplink tested by pulling the primary link. No evidence source or schema was specified for these physical checks, so they are an explicit Acer/operator gate and are not represented as passed by synthetic tests.
- On the Acer, run the offline suites under Linux, the real GET-only shakedown for at least 72 h, the link-failover test, storage and failure-rate review, and the real seal verification after a fresh fee refresh. The Mac sandbox skipped the `kern.boottime` comparison because `sysctl` is unavailable there. The inherited temporary seal tests do not substitute for a real shakedown summary or actual host verification.

*(The three open items above were resolved in round 2, below.)*

## Round 2 (2026-10-12; Codex started it and hit its usage limit, Claude finished it)

Spec changes since round 1:
- only checkpoint records keep the raw trade window (protocol `trade_window_policy` regenerated, draft SHA-256 `fe7dcea3…`);
- the seal storage gate is now measured growth × 72 days × 2 ≤ the runtime's main-database cap;
- the UPS and backup-uplink gate is dropped (owner decision: no backup internet; no link-failover test).

| Item | Change | Tests and evidence |
| --- | --- | --- |
| 0. macOS temp paths | New temporary-directory tests use `Path(td).resolve()`, as the existing suites do (`/var` is a symlink to `/private/var`, and `no_links` correctly refuses it). Round 1's "pass" held only in Codex's sandbox. | `run_checks.py` passes on the Mac. |
| 1. Protocol | `candidate/readiness_protocol.json` matches the regenerated draft byte for byte; `readiness_policy.py` digests updated. | `test_readiness_protocol.py`, `make_revision.py --check`. |
| 2. Signal records | Signal/decision payloads replace `raw.trades.data` with `{'summary': recent_trades}` (aggregates, count, first/last trade_id, SHA-256). Replay restores the stored summary, so the flow veto and the report work without raw trades. Only checkpoints keep the raw window. | `test_signal_and_execution_evidence_use_trade_summary` (Codex). Claude corrected its last assertion: the fixture uses the two-precision protocol, so `fee_bound_binding` is absent by design (as `test_study005r1_release` asserts); fee terms never depend on trades. |
| 3. Storage | The projection drops the per-arm raw-window term. **Quota raised 128 → 256 GiB** in `operating_limits.json` (the Acer has about 1.5 TB free; reserve unchanged). `measure_storage.py` reads the quota from `operating_limits.json` instead of hard-coding 128. Result: subtotal 25.05 GB, ×2 planning **50.1 GB = 55% of the 85.3 GiB main-database cap** (`fits_main_database_cap: true`). At 128 GiB it would have been 109% of the 42.7 GiB cap. | `evidence/storage_9000.json`. The quota assertions in `test_release_gate.py:60`, `test_study005r1_release.py:341` and `test_evidence_runner.py:80` are now 256 GiB. |
| 4. Seal gate | `release_tool` `shakedown-summary` records measured bytes/day, the 72-day × 2 projection and the cap; `seal` refuses if the projection exceeds the cap. | `GateTests` (cap boundary: equal passes, +1 fails). |
| 5–6. Open items | UPS/uplink gate removed. Trade-window edge settled: flow aggregates use the analysis-window subset; the SHA-256 covers the full collection window. | n/a |

Working manifest regenerated (`release_tool.py inventory`, manifest `6e64491d…`). Final Mac `run_checks.py`: **passed**, 368 Python methods (1 skip), 14/14 golden, 24/24 UI (`evidence/test_results.json`).

## Round 3 (2026-10-12, Claude): live shakedown fix

**Acer Linux suites (before the fix):** all passed. The only failure was `run_checks`'s Mac-only "production sources unchanged" check, which reads fixed `/Users/...` paths and can't run on Linux.

**The shakedown found a blocker the offline tests could not see.** It started 18:53 UTC; every observation was vetoed with "Source snapshot skew exceeds freshness budget" (skew 7.6–8.2 s against `maximum_snapshot_skew_seconds = 5`).

**Cause.** Sequential trade paging took about 8 s for 6 pages, about 1.3 s per page. The client's fixed origin, `external-api.kalshi.com`, serves a 1,000-trade page in about 1.2 s; `api.elections.kalshi.com` serves the same uncached query in about 0.25–0.45 s. The pacer (0.00 s), the gate (5 ms) and parsing (9 ms) were ruled out by measurement. **Kalshi honors `max_ts`:** a 30 s slice came back bounded exactly.

**Fix (no host change; the single-origin allowlist is untouched).**
- `trade_window` fetches the 180 s window as `TRADE_SLICES = 6` contiguous slices in parallel, each following its own cursor for at most 5 pages, so the 30-page total cap still holds.
- Adjacent slices share their boundary second; rows are merged by `trade_id`, and a duplicate id with different content is fatal.
- Rows are returned newest first, like the API. Any slice still holding a cursor after its cap means truncated → veto.
- The protocol text ("following cursors up to 30 pages of 1000") is unchanged and still accurate.
- Tests: `SlicedClient` fake (`min_ts ≤ created ≤ max_ts`, offset cursors); `test_slices_cover_window_and_merge_shared_boundary_trades` (boundary trades are neither lost nor doubled, order kept, conflicting duplicate refused); the 30-page cap test is now thread-safe.

**Results.**
- Mac: 369 methods (1 skip), 14/14 golden, 24/24 UI. Acer Linux: 228 readiness (1 skip) plus every other suite passes. Manifest `80670dde…`.
- Shakedown restarted 19:00:01 UTC (pre-fix data archived to `~/btc-study-qa/shakedown_archive/runtime_shakedown_pre_slices_20261012T1853Z`).
- Live: no vetoes, skew 2.6–3.3 s, trade fetch 2–3 s at 900–4,700 trades.
- **First checkpoint ever enrolled: 19:05:05 UTC (594 s remaining).**
- One `COLLECTION_FAILED` at 19:00:06, with its reason logged: "Contract rolled or became inactive during retrieval" (a window changeover).

**Watch:** skew at peak volume (about 9k trades per 180 s means 2 pages per slice). If it nears 5 s, consider more slices, not a looser budget. *(It did; see round 3b.)*

## Round 3b (2026-10-12, Claude): live shakedown fixes 2 and 3

The new twice-daily check (`btc-readiness/acer/shakedown_check/`, read-only) found two more problems on its first run against about 40 min of live data.

**1. Skew crept back at busy times** (p95 4.61 s, max 5.64 s at 9–12k trades). The *benchmark* finished last in 106 of 138 observations: its 50-token request was starved of pacing tokens by the 10-token trade pages; even without trades, skew reached 4.19 s. More slices would worsen it, because the pacer (60 tokens/s, protocol-fixed) is the bottleneck.

Fix:
- `Copilot.collect` fetches the point-in-time sources in parallel first, then the trade window.
- The skew veto covers point-in-time sources only (`essential` minus `trades`). The trade window is anchored at `collection_epoch`, so a later fetch only adds late-reported trades from that fixed past interval. It stays bounded by the unchanged 15 s per-source latency veto and by `valid_until_epoch`.
- **The independent reviewer must weigh this:** it changes which sources the skew budget applies to.
- Tests: `test_skew_covers_point_in_time_sources_not_the_anchored_trade_window` (a late trade fetch is not vetoed; a late benchmark or book still is) and `test_trade_pages_are_requested_after_every_snapshot_request`.

**2. About 12% of polls "failed" by design.** The new failure logging explained r1's mysterious ~15%: six "Decision completion outside valid observation window" errors in the last ~90 s of every window (the engine refuses observations inside `final_entry_cutoff_seconds = 90`), plus a changeover failure while Kalshi hadn't yet listed the next contract.

Fix:
- `next_poll_delay` skips only polls certain to fail: those that would start with less than 90 s left, or less than 15 s after a window opens (`FINAL_CUTOFF_SECONDS` is asserted equal to the setting). A poll that starts 90–95 s out is still made.
- A refusal that still lands inside the cutoff raises `FinalCutoff` (a `ReadError` subclass, so every existing handler still applies). The collector logs it as a `FINAL_CUTOFF` event: not a failure, no degradation, no change to the consecutive-failure count.
- Observation semantics are unchanged: checkpoints are 300–330 s after open, and signals 90–600 s before close.
- Tests: `test_polls_certain_to_fail_are_skipped_and_nothing_else` (with a sweep over three windows) and `test_final_cutoff_is_an_expected_event_not_a_failure` (mocked collector plus the real store; degradation unchanged).

**Results.**
- Mac: 373 methods (1 skip), 14/14, 24/24. Acer Linux: 232 readiness (1 skip) plus every other suite passes. Manifest `10620b9a…`.
- Shakedown restarted **19:41:45 UTC**; earlier data archived as `…/runtime_shakedown_slices_only_20261012T1900Z`.
- First window end and changeover: **zero failures**; last poll at 98 s left, next 18 s into the new window; skew p95 2.17 s, max 2.40 s at up to 9.2k trades; checkpoint enrolled 19:50:05.
- The 72 h point is about **2026-10-15 19:42 UTC**. *(Superseded: see round 3c.)*

## Round 3c (2026-10-12, Claude): orderly stop and reboot recovery

**There was no clean way to stop a shakedown.** `clean=True` was set only when the loop ended naturally (at the release date, 72 days out). SIGTERM killed Python without cleanup, so RUNNING stayed behind and `release_tool.py shakedown-summary` would always have refused.

Fix:
- `run_authorized(..., stop=None)` checks `stop()` between cycles and breaks into the clean path.
- `main()` wires it for `--shakedown` only: a SIGTERM handler sets a `threading.Event`, and the poll sleep becomes `Event.wait`, so even a 110 s dead-zone sleep wakes at once.
- The study's `--collect` path never passes `stop`, so an unplanned study stop stays unclean and needs operator review.
- Tests: `test_orderly_stop_between_cycles_closes_cleanly` and `test_study_collect_path_never_wires_an_orderly_stop`.
- **Proven live:** `systemctl --user stop` took 2 s and exited with status 0; COLLECTOR_STOP was logged and RUNNING removed; `shakedown-summary` then produced a summary. The throwaway run and summary are archived as `*_THROWAWAY_*`.

**Reboot recovery.**
- By design, a runtime never resumes across a reboot (`RUNTIME_OR_BOOT_CHANGED` → HALTED).
- The shakedown now runs as a permanent, boot-enabled user unit `btc-r2-shakedown.service` (linger is on) through `btc-readiness/acer/shakedown_check/shakedown_start.py`:
  - if the runtime on disk belongs to an earlier boot, it archives it, resets the check's 72 h milestone, sends one Telegram and starts fresh, so the 72 h clock restarts;
  - a runtime from the *same* boot that stopped uncleanly is left for review;
  - it waits up to 20 min for a fee attestation written after boot (`btc-study-attest.timer` runs 2 min after boot).
- The boot path has not been exercised by a real reboot yet.

**Results.** Mac: 375 methods (1 skip), 14/14, 24/24. Acer Linux: 234 readiness (1 skip) plus every other suite passes. Manifest `a9ea78b1…`.

**Real 72 h run started 2026-10-12 20:25:57 UTC → 72 h at about 2026-10-15 20:26 UTC.**

**Open item for the r2 protocol (owner decision, not yet raised).** In the *study*, a reboot permanently halts the runtime: the study cannot continue in the same database, so one reboot in 10 weeks ends the study. This conflicts with the 2% missing-slot budget's intent. Options:
- (a) keep it, and avoid reboots;
- (b) allow resume after a reboot, with the gap recorded as missing slots with reason "reboot", counted against the 2% budget. This changes `clock_policy.restart`, so the Codex review must cover it.

**Remaining before sealing:**
1. ~~Run the Linux suites on the Acer.~~ Done.
2. Run the GET-only shakedown for at least 72 h. It must also confirm that Kalshi honors `max_ts`, and show real per-poll timing and storage growth.
3. Run a fresh fee refresh.
4. Codex reviews r1 → r2.
5. Seal and stage.
6. Owner approval by 2026-10-25 00:00 UTC, then activate.

## Round 4 (2026-10-12, Codex): reboot resume in the same database

- Copied the regenerated r2 draft byte for byte into `candidate/readiness_protocol.json` (SHA-256 `d6921132fe90f82cdfc259ea70d9d2458903ebbe470ca09c6359b096f9a69715`). Updated the preserved and revised policy digests so only `clock_policy.restart` is exempted from the inherited-field digest and is pinned in the revised-field digest. `make_revision.py --start 2026-10-26 --check` passes.
- `EvidenceStore` now checks the stored config, database integrity, private path, writer lock and persisted halt before permitting a changed boot. It leaves an unclean same-boot `RUNNING` marker blocked. For a changed boot, one SQLite transaction records `REBOOT_RESUME` with the old/new eight-character boot prefixes and last persisted observation epoch, updates `runtime_control.boot`, and deletes `meta.last_monotonic`. It keeps `meta.last_epoch` and refuses an earlier wall clock. The next publication compares UTC order against the previous generation while starting a new continuity-clock baseline. A resume failure halts.
- **Persisted-halt choice:** every existing `HALTED` marker still requires review, including an old `RUNTIME_OR_BOOT_CHANGED` marker. Only a database with no persisted halt can use automatic reboot resume; this avoids clearing a halt whose full cause cannot be verified from the marker alone.
- Both `ReleaseGate` and `ShakedownGate` require the attestation file modification time and the fee, rate and clock verification timestamps to be at or after the current boot. Linux reads `/proc/uptime`; macOS reads `kern.boottime`. The boot epoch is injectable for synthetic tests. Unavailable host boot time blocks collection.
- A missing checkpoint band that intersects the interval from the last persisted observation to `REBOOT_RESUME` is labeled `reboot` in the release report, shakedown summary and read-only Acer status. It counts as explained in the report's 2% missing-slot gate and the shakedown summary's reason gate. `REBOOT_RESUME` is an allowed nonfailure runtime event and does not degrade the store.
- **Gap interpretation:** “checkpoint band lies between” is implemented as interval overlap, so a band partly interrupted by the reboot is explained. If no prior observation epoch exists, the event stores `null` and supplies no automatic missing-slot explanation; any such miss remains subject to the ordinary reason gate.
- Synthetic regressions cover unclean cross-boot resume, same-boot refusal, an earlier wall clock, changed version/quota, persisted halt refusal, new monotonic baseline, after-boot attestation in both gates, and a reboot gap across all three missing-slot views and the 2% explanation gate. The older synthetic attestation tests now inject a boot epoch because macOS does not expose `kern.boottime` inside this sandbox. No Acer launcher, live runtime, account or network operation was changed.
- Final Mac `run_checks.py`: **passed**, 382 Python methods (2 skips), 14/14 golden scenarios and 24/24 UI checks. See `evidence/test_results.json` and `evidence/run_checks_console.txt`. Working manifest SHA-256: `2e22792e141b3a6af86a96acb574b68881c70753909749276a89e434035b5351`. The candidate remains `QA_ONLY` and `PROPOSED_NOT_APPROVED`.

**Claude's review of round 4.** Read the resume path in `coherent_runtime.py`: the event, boot swap and baseline reset are one `BEGIN IMMEDIATE` transaction; the clock order is checked inside it; any failure halts; a same-boot unclean stop and any config change still refuse. Independent Mac `run_checks.py`: 382 methods, **1** skip (Codex's second skip, `kern.boottime`, occurs only inside its sandbox). The manifest matched Codex's. Acer Linux: 241 readiness tests plus every other suite pass. The shakedown (round 3c code) was stopped cleanly at 00:43:25 UTC Oct 13 after a **4.3 h soak**, and restarted on round 4 code at 00:44:21.

## Round 4b (2026-10-13, Claude): soak findings

The 4.3 h soak on round 3c code captured **17/17 checkpoints** (skew p95 2.15 s), but **5.7% of polls failed**, against a seal gate of ≤1% per hour. Diagnosed and fixed:

1. **"Insufficient one-hour BRTI coverage" (32 of 52).** 70 live samples taken through the collector's own client: 68 had 3600 points spanning exactly 3599.0 s; **2 had 3599 points spanning 3598.0 s, cut one second short at the old edge with no gaps**. The rule failed anything under 3599 s, and the 1 h lookback (`nearest_price`) also needed a tick within 1 s.
   - Fix: `BRTI_MIN_SPAN_SECONDS = 3597` and `LOOKBACK_TOLERANCE_SECONDS = 3`. The 1 h z-score already scales by the actual coverage seconds.
   - **This is a frozen-model data rule. The reviewer must weigh it.**
   - Test: `test_brti_window_one_or_two_seconds_short_is_accepted_but_not_three`.
2. **"Cannot verify exactly one active contract" (6),** always exactly 15 or 30 s after a window opened: Kalshi lists the next contract 15–30+ s late. `LISTING_DELAY_SECONDS` 15 → 35. Checkpoints (300+ s) and signals (≤600 s before close) are unaffected.
3. **"Required data unavailable: trades" (8) plus "Read pacing deadline exceeded" (6)**, in one burst from 21:41 to 21:58 UTC. Each pacing error came about 9 s after a trades failure, which suggests HTTP 429 → pacer cooldown → the next poll's discovery starved. Trade windows were only 8–12k, while 15.8k elsewhere caused no failure. The cause was **unconfirmed**, because the message dropped it.
   - Fix (diagnostic): `failure_note()` keeps the first underlying cause, e.g. "Required data unavailable: trades (ReadError: Kalshi GET failed with HTTP 429)". Client messages are fixed strings, and the store scrubs details anyway.
   - Test: `test_missing_source_reports_its_underlying_cause`.
   - If 429s are confirmed, decide on mitigation with data. (The TA dashboard on the same host also polls Kalshi publicly from the same IP.)

**Results.** Mac: 384 methods (1 skip), 14/14, 24/24. Acer Linux: 243 readiness tests plus every other suite pass. Manifest `d84119aa…`. Round 4 soak data archived as `…/runtime_shakedown_round4_20261013T0044Z`. **Shakedown restarted 2026-10-13 01:04:07 UTC → 72 h at about 2026-10-16 01:04 UTC.** *(Superseded: see round 4c.)*

**Live reboot test (2026-10-13 01:29 UTC, owner-initiated `sudo reboot`): passed.**
- SIGTERM → clean COLLECTOR_STOP at 01:29:02; boot at 01:29:19; post-boot attestation at 01:31:57; `REBOOT_RESUME` plus COLLECTOR_START at 01:32:00 in the same database.
- 3/3 checkpoints across the reboot.
- Found and fixed a bug in `shakedown_start.py` (outside the release): it compared the raw boot id with the stored SHA-256 and would have reported every start as a reboot.

## Round 4c (2026-10-13, Claude): writer lock wait, after the shakedown halted

**What happened.** The shakedown halted at 03:00:41 UTC with `sqlite3.OperationalError: database is locked` → HALTED `FAILURE_RECORD_UNAVAILABLE`. 03:00 is when the twice-daily check runs. **That check iterated a live 12-hour payload scan** (about 115 MB, decompressing row by row), holding SQLite's shared lock for seconds. The runtime connects with **`timeout=0`**, so both the publication and the failure record failed instantly, which forces the halt. That check had passed at its own start, so the death went unflagged until the 15:00 UTC check: **about 15.5 h of shakedown lost**. A persisted halt requires review by design, so the run was archived (`…/runtime_shakedown_HALTED_lock_20261013T0300Z`).

**Fixes.**
- **Runtime:** `WRITER_LOCK_WAIT_SECONDS = 10` on the store's connection. A brief read-only reader (status, watch, checks) now delays a commit instead of failing it; a reader holding a lock longer than that still fails the write, as before.
  - With `timeout=0`, any watch read overlapping a commit would have cost a poll, and two overlapping writes would have halted the 10-week study.
  - **The reviewer must weigh this.**
  - Test: `test_writer_waits_for_a_brief_reader_instead_of_halting` (a reader holds a shared lock for 1 s; `failure()` succeeds; verified to FAIL with `timeout=0`).
- **The check (outside the release):** autocommit, `timeout=1`, every query fully fetched before processing; skew sampled from the last 240 observations in batches of 20, and veto reasons from at most 12 recent misses. Live: 6 back-to-back checks took about 0.2 s each with the collector unaffected.
- **Immediate alert (outside the release):** `OnFailure=btc-r2-shakedown-alert.service` sends a Telegram within seconds, with the HALTED reason and the last log line, instead of waiting for the next twice-daily check.

**Results.** Mac: 385 methods (1 skip), 14/14, 24/24. Acer Linux: 244 readiness tests plus every other suite pass. Manifest `63d975eb…`. **Shakedown restarted 2026-10-13 18:37:48 UTC → 72 h at about 2026-10-16 18:38 UTC.** *(Superseded: see round 4d.)*

## Round 4d (2026-10-13, Claude): FIFO pacing and a trade-fetch budget

**Finding.** At US-afternoon load (about 19:00–19:30 UTC), skew reached p95 4.71 s and max 7.07 s, and 8 polls failed with "Decision completion outside valid observation window": about 3.6% of polls, which would fail the ≤1%/h gate. All 3 checkpoints were still enrolled.

**Cause.** Measured directly with an idle pacer, the BRTI benchmark took 1.14 s median (p90 1.49 s, max 2.27 s). Inside the collector it took 2.22 s (p90 4.96 s, max 8.69 s).
- The pacer served whoever grabbed tokens first, so the seven 10-token snapshot requests took tokens as they trickled in and **starved the 50-token benchmark**.
- That delayed the trade fetch too, which at busy times (13 s) then overran the 20 s validity window.
- The owner's apt upgrades (2026-10-12) were ruled out: no reboot pending, kernel current, load 0.3.

**Fixes.**
1. **`request_pacing.Pacer.acquire` is FIFO.** Each request takes a ticket and only the head may take tokens. There are still **no reservations**: a waiting head holds no tokens, and a head that cannot be served before its deadline raises PaceError and its ticket is skipped. Rates, buckets, costs, the 429 cooldown and the 5 s wait cap are unchanged. The benchmark is dispatched first in `collect()`, so it holds the first ticket. **The reviewer must weigh this pacing change.**
2. **`TRADE_FETCH_BUDGET_SECONDS = 15`.** `trade_window` starts no new page 15 s after the poll began; a window cut short keeps its cursor, which means truncated → the existing "Recent trade window incomplete" veto. A logged veto for that poll, never a failed observation.

**Tests.**
- `test_pacer_serves_in_arrival_order_so_the_benchmark_is_not_starved`: real threads; the benchmark is served first.
- `test_pacer_head_that_cannot_be_served_in_time_gives_up_its_turn`.
- `test_trade_fetch_budget_marks_the_window_incomplete_instead_of_failing`.
- All 13 existing `test_pacing` tests are unchanged and pass.

**Results.** Mac: 388 methods (1 skip), 14/14, 24/24. Acer Linux: 247 readiness tests plus every other suite pass. Manifest `ef08e186…`. Pre-fix run archived as `…/runtime_shakedown_round4c_20261013T1838Z`. **Shakedown restarted 2026-10-13 19:38:55 UTC → 72 h at about 2026-10-16 19:39 UTC.** A live 15-minute busy-hour comparison is running.

**Live result (added 2026-10-14).** First 15 busy-hour minutes after the restart (54 observations): skew median 0.92 s, p95 1.21 s, max 1.57 s (was p95 4.71 s, max 7.07 s). Benchmark median 1.15 s, the same as the raw latency, so the starvation is gone. Trades p95 4.26 s at up to 10k trades. 0 skew vetoes, 0 truncations, 0 collection failures, 1 FINAL_CUTOFF (expected); the checkpoint was captured.

## Round 4e (2026-10-14, Claude): independent QA of round 4d, test-only fixes

**Review.** ChatGPT GPT-6 Astra at High effort reviewed the round 4d code and tests plus the `closing()` cleanup and returned **FIX FIRST**. Claude checked every finding against the code and confirmed all of them. Astra judged the `closing()` changes correct.

1. **The trade budget is not a hard bound.** It only stops new pages *starting* after 15 s. A page that starts at +14.9 s can still wait up to 5 s in the pacer, then up to the 20 s urlopen timeout, and finish after `valid_until_epoch`. That poll then fails with "Decision completion outside valid observation window". The first page of each slice is also exempt (`if pages and …`). So the round 4d claim "never a failed observation" holds only for the common case. Live latency makes this a tail risk (P2 in practice).
2. **Submitting the benchmark first does not guarantee it the first ticket.** The ticket is taken inside `before_read`, after the thread starts, so another worker can arrive first. It is a strong preference, not a guarantee (P3).
3. **The pacer can grant tokens just after its deadline.** The deadline check and the token debit in `try_acquire` happen under separate lock sections, so the 5 s wait cap is approximate (P3).
4. **The round 4d pacer tests were weak.** They dropped worker exceptions, used unbounded `join()`, set arrival order with a fixed sleep, and the expiry test never reached the non-head `abandoned.add` path.

**Fixed now (tests only, outside the shakedown digest).** `test_pacer_serves_in_arrival_order…` now orders arrivals by observed ticket allocation, records every worker exception, uses bounded joins with a deadlock assertion, and asserts the full service order and an empty queue. The new test `test_pacer_skips_consecutive_abandoned_waiters_but_not_a_live_ticket` covers two consecutive non-head expiries behind a live head, then shows that the next caller skips both. An in-memory mutant without `abandoned.add` fails this test. Results: Mac 389 methods (1 skip), golden 14/14, UI 24/24. The arrival-order test passed 15 repeat runs. `shakedown_source_digest` is unchanged (`65f34177…`), so the running shakedown stays valid.

**Deferred to the next candidate change (one shakedown restart; user decision 2026-10-14).**
- Findings 1–3 above. For 1: carry one monotonic observation deadline through pacing, transport and response reading, with a reserve for validation and publication; check the cutoff before *every* page and again after pacing; mark an unstarted slice incomplete explicitly instead of inventing a cursor. For 2: use a handshake, or reword the comments to say it is a preference. For 3: check the deadline in the same locked section that debits tokens.
- `candidate/acer_ops.py:177` `read_runtime`: wrap in `closing()` (the last 5 ResourceWarnings).
- `candidate/acer_ops.py` alert signatures (`:311`/`:364`) include live numbers, so the same alert repeats every 5 minutes. Normalize them to stable text and levels.

## Round 4f (2026-10-18, Reasonix + Claude): QA fixes outside the candidate

**Review.** An offline adversarial QA pass at `52a07a8` reported seven findings. Codex verified them against the source with synthetic reproductions. A read-only DeepSeek Flash worker (Reasonix) then re-checked the disputed verdicts, and Claude checked those answers against the code. DeepSeek Flash wrote the fixes below in two bounded Reasonix tasks. Claude reviewed every changed line and ran all suites. Only files **outside `candidate/`** changed, so `shakedown_source_digest` is unchanged and the running shakedown stays valid.

**Fixed (release tool and Acer monitor).**
1. **QA-01: empty hours blocked the seal.** `shakedown_summary` marks each hourly row with `partial_hour` and `inactivity_explained`. `check_shakedown` accepts a zero-attempt hour only if the shakedown window cuts it, or if every slot in it is a miss with a recorded reason. An unexplained empty full hour still fails. A numeric rate must be finite and between 0 and 1%.
2. **QA-05: the final slot was dropped.** Every slot whose 300–330 s band has fully elapsed now counts, including the last one. This matches `shakedown_check.py`.
3. **QA-03: a late restart hid the stop reason.** A `COLLECTOR_STOP` is cleared only by a start at or before `opened+300`. A restart at `+329` keeps the reason.
4. **QA-05: weak schema checks.** The seal gate rejects blank or non-string reasons, a missing, non-integer or nonzero `unexplained_collection_failures`, and NaN or non-numeric `hours_covered`.
5. **QA-05: monitor labels.** `acer/shakedown_check/shakedown_check.py` explains misses from `slot_events`, `REBOOT_RESUME` (the same test the release tool uses) and collector stops, instead of "NO OBSERVATION OR REASON IN THE BAND". `slot_events` is fetched once, in full, so no lock is held.

**Fixed (TA Copilot, QA-06).** `log_tools.read_rows` no longer counts unknown-type rows as unreadable, matching `tally_report.load_rows`. They are still not returned, as before. `tally_report.primary_result` orders windows by parsed UTC time instead of ISO text. **The predeclared cohort is unchanged:** it is still the first 800 windows with minute rows.

**Tests.** `tests/test_shakedown_qa.py` (8 tests) and `ta_copilot/test_qa_20261018.py` (7 tests). All 8 shakedown tests fail on the pre-fix code, and so do 6 of the 7 TA tests. The seventh guards that the cohort is unchanged. Results: Mac 397 methods (1 skip), golden 14/14, UI 24/24, from `run_checks.py` in a temporary copy. `make_revision.py --check` passes, and TA Copilot passes 235 tests.

**Not changed (held for the next candidate change, or a decision).**
- QA-02: `impute_holdout` gives zero penalty when every fill won. This matches the predeclared formula (`readiness_protocol.json:121`), so changing it is a protocol decision.
- QA-04: `Study.record` returns early for outside-study observations. No normal collector path reaches it.
- The candidate copies of the QA-03 stop/restart rule (`btc_copilot_evidence.py`, `acer_ops.py`). Until they change, a late restart keeps its stop reason in the seal summary but not in the candidate's own `missing_slots`.
- The round 4e deferred items.
- The Acer's copies of `release_tool.py` and `shakedown_check.py` have not been updated. Sync them before the shakedown summary and seal.

## Round 4g (2026-10-19, Reasonix + Claude): scheduled exchange closure is an explained pause

**Finding.** The round 4d shakedown halted 2026-10-15 07:10:20 UTC after about 35.5 h. From 07:00:35, all 40 consecutive polls failed with "Cannot verify exactly one active BTC 15-minute contract", and `MAX_CONSECUTIVE_FAILURES` stopped the run for review.
- Kalshi's published standard hours (`GET /exchange/schedule`, times in ET) close the exchange **every Thursday 03:00–05:00 ET**: 07:00–09:00 UTC until 2026-11-01, then 08:00–10:00 UTC. No KXBTC15M contract is listed then.
- The TA Copilot log shows the same gap on Oct 1, 8 and 15. The study window has 10 Thursdays, so the collector would have halted every week.
- The collector was not at fault.

**Fix (candidate).**
1. When discovery lists **no** contract, `collect()` reads `/exchange/status` (already allowlisted). If its boolean `exchange_active` or `trading_active` is false, it raises `ExchangeClosed`. Kalshi documents `trading_active` as "false outside exchange hours".
2. `Collector.once` records it as a new `EXCHANGE_CLOSED` runtime event, the same way as `FINAL_CUTOFF`. It is neither a failure nor degradation, and the failure count neither resets nor advances.
3. More than 4 h of continuous closure stops the collector for operator review. The weekly closure is 2 h.
4. These still count as failures, as before: no contract while status says active, a failed status read, non-boolean flags, or more than one contract.
5. Missed slots get the `EXCHANGE_CLOSED` detail as their reason in `btc_copilot_evidence.missing_slots`, `acer_ops`, `release_tool.shakedown_summary` and `shakedown_check.py`. None of them counts it as a poll failure.

**Fix (outside the candidate).** `shakedown_check.py` sends the "72 h reached" message only while the service is active and no problems are reported. It had sent it for the run that died after 35 h.

**Unchanged:** the protocol, `PUBLIC_READS`, cadence, `MAX_CONSECUTIVE_FAILURES`, slot and checkpoint definitions, scoring and gate thresholds.

**Tests.** `tests/test_exchange_closed.py` (15 tests). 14 of them fail on the pre-fix code. Results: Mac `run_checks.py` 412 methods (1 skip), golden 14/14, UI 24/24 (run in a temporary copy). `verify_candidate` passes, and so does `make_revision.py --check`. Working manifest `6a196793…`, `shakedown_source_digest` `1d236024…`. The digest changed, so a fresh 72 h shakedown is required.

**Not yet observed live:** `/exchange/status` during a closure. The next one is Thursday 2026-10-22 07:00–09:00 UTC, and the new shakedown should span it.

**Owner decision, not yet raised.** Closure slots count as explained misses against the 2% budget (107/5,376), at 8 slots per Thursday. Whether scheduled closure slots should count as scheduled slots at all is a protocol question.


## Round 4h (2026-10-22, Codex): closure 503s and specific discovery errors

**Finding.** The r2 shakedown halted at 07:22:50 UTC on October 22. Directly observed closure from 07:00:35 was followed by discovery HTTP 503s from 07:11:05, counted as consecutive failures until operator review stopped collection.

**Fix (candidate).** After a directly observed closure in the same Thursday 03:00–05:00 America/New_York window, HTTP 503s from discovery, fallback status or required snapshot jobs record `CLOSURE_STATUS_UNAVAILABLE`. These inferred pauses preserve the failure count and continuous four-hour closure cap. The window follows DST. Memory is process-local: restart, a different window, non-503 errors or no prior observation still fail closed. All four readers explain missed slots with the new event and exclude it from poll failures.

**Diagnostics.** Discovery failures retain their existing prefix and distinguish zero eligible contracts (active or unreadable status), duplicate tickers and distinct eligible contracts with UTC opening/closing times. No deduplication or eligibility policy changes.

**Tests.** Synthetic offline regressions cover DST boundaries, wrapped 503s, restart and non-503 failures, pause counts and cap, reader reasons and failure rates, and exact discovery messages. Protocols and production trees are unchanged.

## Round 4i (2026-10-22): start moved to 2026-11-02

**Why.** A passing 72 h shakedown could not finish before the 2026-10-25 approval deadline, and the next one should span the Thursday 2026-10-29 07:00–09:00 UTC closure to exercise round 4h live.

**Change.** `readiness_policy.START` is 2026-11-02; `make_revision.py --start 2026-11-02` regenerated the draft, copied byte for byte into `candidate/readiness_protocol.json`. Study ID `btc-prospective-005r2-20261102`; validation Nov 9, holdout Nov 16 → 2027-01-11, release 2027-01-13; approval deadline 2026-11-01 00:00 UTC. The inherited- and revised-policy digests exclude dates and are unchanged. The working manifest is regenerated. Still `PROPOSED_NOT_APPROVED`; a different date remains a separate review.


## Round 4j (2026-10-24): proposed upstream 5xx excuse and monitor hour boundary

**Approved by the owner 2026-10-24 as drafted (1% cap, 429 still counts).** Kalshi HTTP 5xx failures leave the hourly shakedown rate after WAN exclusions (WAN wins), with an exact-count seal check and a 1% cap over all raw run polls. HTTP 429, discovery and other failures still count. Miss reasons and coverage requirements are unchanged; old summaries remain supported. See `proposals/UPSTREAM_5XX_EXCUSE_NOT_APPROVED.md` for the owner decisions and risk.

**Monitor.** Apply the same exclusion and lookback cap, report excused 5xx in drift alerts, and align the lookback down to the hour. Judge the run's partial first hour, closing the 03:00/15:00 timer blind spot.

**Tests.** Synthetic summary/gate and monitor regressions added to `tests/test_shakedown_qa.py`. The requested shakedown, exchange-closure and release-gate suites pass (64 tests). No candidate, protocol, evidence, manifest, sealed-tree or production changes; not deployed.


## Round 4k (2026-10-25)

QA patches in `cecff77` cover R2-01..05 and TA-01..05.
Follow-up: repair the R2-03 fixtures and hour validation, reject non-finite watcher ages, propagate SQLite faults, and expire future-dated closure probes.
R2-04 counts one more first slot when its enrollment band remains ahead, matching the monitor; this can lower coverage by one slot and change seal eligibility.
R2-06 is deferred to the next candidate revision.

## Round 4l (2026-10-25): proposed direct-contract fallback for listing lag

**NOT APPROVED.** Run A halted October 24 at 16:10:20 UTC after 40 empty-listing failures while the separately observed 16:00–16:15 contract remained live. Shorter `:00:35` lags occurred October 19/20. Incident details are supplied context.

**Candidate draft.** Completed zero-eligible discovery with both exchange flags boolean true may verify one remembered current-slot ticker, or derive it from the slot close in America/New_York. Memory retains completed-listing tickers for current/next slots only. Direct reads use the existing allowlist and unchanged eligibility predicate, exact identity and slot times. Ambiguous memory and direct failures do not retry. Snapshot event lists, when supplied, must have exactly one eligible entry matching the selected ticker and event. `retrieval_health.discovery` records `listing`, `remembered_ticker` or `derived_ticker`; no new runtime events. Normal polls add no requests; fallback adds one direct GET plus the pre-existing zero-eligible status probe.

**Owner decision.** Approve the uniqueness interpretation and choose both ticker sources or remembered-only. An empty listing cannot exclude an unlisted second contract. See `proposals/LISTING_LAG_FALLBACK_NOT_APPROVED.md`. Candidate digest changed; a fresh run B is required. Protocols, request allowlist, evidence and production trees are unchanged. No commit, push or deployment.

**Verification.** `tests/test_listing_fallback.py` uses fake clients for remembered/derived selection, EDT/EST and supplied ticker samples, HTTP/verification errors, status gates, duplicate discovery, memory retention, event membership and request counts. Results: 22/22 new tests pass; full release discovery runs 330 tests, 329 pass and one skips (`kern.boottime` unavailable in the Mac sandbox). Manifest hashes match 34/34 files. `run_checks.py` is not used.

**Round 4l approved by the owner 2026-10-25** as drafted (remembered and derived ticker). Run B is the first shakedown on this candidate.
