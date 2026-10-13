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

**Results.** Mac: 384 methods (1 skip), 14/14, 24/24. Acer Linux: 243 readiness tests plus every other suite pass. Manifest `d84119aa…`. Round 4 soak data archived as `…/runtime_shakedown_round4_20261013T0044Z`. **Shakedown restarted 2026-10-13 01:04:07 UTC → 72 h at about 2026-10-16 01:04 UTC.**
