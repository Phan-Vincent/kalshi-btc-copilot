# Study 005 revision 2: DRAFT, not adopted (2026-10-12)

**Proposed START: 2026-11-02 00:00 UTC** (a Monday; moved from 2026-10-26 on 2026-10-22, after the r2 shakedown failed its gate and halted in the Thursday closure). The schedule is 7 + 7 + 56 + 2 days: validation Nov 9, holdout Nov 16 → **2027-01-11**, release **2027-01-13**. The approval deadline is START − 1 day, **2026-11-01 00:00 UTC** (Sat Oct 31, 5 pm PDT). The original Oct 26 plan follows. Oct 19 is possible only if a 48 h shakedown (below) passes by Oct 16 and the owner signs by Oct 17. Oct 26 leaves room for a 72 h+ shakedown that includes a weekend.

## Why r1 is void

r1 (5.4.1-study005r1, START 2026-10-12) can't enroll checkpoints at normal market volume.

- `btc_copilot.py:433` requests `/markets/trades` with `min_ts = int(now) − 180` and `limit = 1000`, as **one page**. Any returned cursor adds the data fault "Recent trade window incomplete; cursor remains". That fault is an operational veto, and `btc_copilot_evidence.py` skips the 570–600 s checkpoint whenever a veto is present.
- Measured KXBTC15M volume is about 3,000 trades per 180 s, and 17–43k per 15-minute window (Sep 28, Oct 5, 9, 11 and 12). Nearly every observation is vetoed: 0 checkpoints out of 63 observations after the 08:39 UTC restart on Oct 12.
- No earlier release on the Acer ever recorded a checkpoint. The QA and the five Codex rounds never ran against live volume.
- Unrelated incident on the same day: the Acer had no internet from about 22:40 Oct 11 to 04:55 Oct 12 UTC. The collector stopped at 00:10 UTC, as designed, and the owner resumed it at 08:39:49 UTC; the record is in `~/btc-study/state/operator_review/`.

r1 keeps running, GET-only, until r2 is staged; `stage` stops it fail-closed. Its data is not study evidence.

## Code changes (implementation: Codex, in a new copy `qa/study005r2_release/` made from `study005r1_release/`)

1. **Fetch every page of the trade window.** Follow cursors up to a cap of **30 pages** (30k trades; peak observed is about 8.6k per 180 s on average). The window becomes `[collection_epoch − 180, collection_epoch]`, defined once and used both for the request (`min_ts`, plus `max_ts` if supported) and for validation. Only a truncation beyond the cap remains a veto.
2. **Latent bug, fix in the same change.** `validated_trades` checks `analysis_now − 180 ≤ t ≤ analysis_now`, but the request starts at `int(collection_now) − 180`. Trades in that 1–3 s sliver raise `ReadError`, so the whole poll fails. Today that can only happen in quiet markets, when one page covers the full 180 s. With pagination it would happen on **every** poll. Validate against the request's own window, and filter rather than fail on trades outside the analysis window.
3. **Pacing.** Pages go through `request_pacing` (10 tokens each; 60 tokens/s locally, while Kalshi's budget is 200/s). Worst case per poll is about 130 + 300 tokens, against 900 available per 15 s interval. The whole poll must still finish inside `valid_until_epoch` (first request start + 20 s). Add a test at the cap.
4. **Storage** (revised 2026-10-12 after Codex round 1). Only **checkpoint** records keep the raw trade window (96/day × about 395 KB is about 38 MB/day). Observation and signal/decision records keep the aggregates, the trade count, the first/last trade_id and the SHA-256 of the canonical trade set. The flow veto only uses the yes/no taker totals, and the checkpoint's model features don't use trades at all. Round 1 kept raw windows in every signal arm, which projected to 135 GB over 72 days (45 GB of that from arm copies). Re-run `tests/measure_storage.py` with 9k-trade windows. **Round 2 result:** quota raised 128 → 256 GiB in `operating_limits.json` (the Acer has about 1.5 TB free); the ×2 planning projection is 50.1 GB, 55% of the 85.3 GiB main-database cap.
5. **Record why polls fail.** `runtime_events` stores only `COLLECTION_FAILED`. Add the exception class and a short, scrubbed message. The cause of the r1 failures (2 at startup, 7 in a row from 08:43:38 to 08:45:06, then 08:52:23 and 08:54:23) is still unknown; diagnose it during the shakedown.
5b. **Make the health check count real checkpoints.** `acer_ops.py` counts a slot as "covered" when *any* observation falls in the 570–600 s band, even if it was vetoed. On 2026-10-12 at 16:39 UTC, the Telegram status said 31/95 slots covered, while `checkpoints` held 0 rows across 33 markets. Status, the watcher and the shakedown summary must count enrolled checkpoints (`markets.checkpoint IS NOT NULL`) and report the reason for each veto. They should also alert when the poll failure rate is above 5% per hour. r1 ran at about 15%: 293 `COLLECTION_FAILED` between 08:40 and 16:39 UTC, 35–42 an hour, steady.
6. **Identity and dates.** `readiness_policy.START = 2026-11-02` (was 2026-10-26), `STUDY_ID = btc-prospective-005r2-20261102`, `CANDIDATE_RELEASE = 5.5.0-study005r2`. Build the protocol with an r2 `make_revision.py` from the sealed r1 protocol (sha256 `52ee6ab0…75f6`). The changed fields are the identity, the dates, the deadline, the release label and the new `trade_window_policy` text below; nothing else changes.
7. **Tests.** Volume fixtures above 1,000 trades, a cursor across pages, the window edge (item 2), the cap, the pacing budget and the storage size.

## Protocol change (new field `trade_window_policy`)

"Recent taker flow uses every public trade in [collection epoch − 180 s, collection epoch], fetched by following cursors up to 30 pages of 1,000. A window that is still incomplete after the cap is a data fault and an operational veto. Trades outside the window are excluded, never fatal." Everything else, including all acceptance thresholds, the missing-data policy and the fee policy, is unchanged unless the owner decides D1.

## New gate before sealing: a live shakedown

`release_tool.py seal` refuses unless a shakedown summary shows, from **at least 72 h** of the r2 collector against live Kalshi on the Acer (a QA runtime, not study evidence), all of the following:
- at least 95% of 15-minute slots got a checkpoint;
- every missed slot has a logged reason;
- no unexplained `COLLECTION_FAILED` bursts;
- measured live growth projected over 72 days, × 2 headroom, fits under the runtime's main-database cap (`(quota − 1 MiB) / 3`, about 85.3 GiB at the r2 quota of 256 GiB; `coherent_runtime.py` sets it with `max_page_count`). This replaces the earlier "under 50% of the quota", which ignored that cap.

The summary must be under 72 h old at sealing, and the manifest binds it, as with the fee evidence. This is the check r1 never had.

## D1, holdout coverage: DECIDED 2026-10-12 (owner): option (b) plus hardening

r1 required all 96 slots every day for 56 days, with "no restart budget". One outage like the one on Oct 11–12 during the holdout would have ruled out a favorable review. r2 replaces this with a pre-declared budget:

- **Budget.** Up to **2% of holdout slots (107 of 5,376)** may lack a checkpoint, and only when each missing slot has a logged reason: a runtime event with a failure reason (item 5), a veto reason, or a recorded collector stop. A missing slot with no logged reason counts as over budget. Above 2%, there is no favorable review.
- **Least-favorable imputation** (field `missing_slot_imputation` in the draft). Gaps can only hurt the result, so the protection against selection bias is kept. The report lists every missing slot, its reason, and each metric with and without imputation.
  - Brier: each imputed slot scores 1.0 for every model and 0.0 for the market.
  - ECE: computed on enrolled forecasts, plus the missing fraction. That's an upper bound for any assignment of the missing slots.
  - Strategy: each missing slot reduces that day's stressed P&L by *fill rate × the largest stressed one-unit loss among holdout fills* (1.00 if there are none). Imputed slots never add fills.
  - *Refined 2026-10-12 while drafting:* a literal maximum-loss fill for every slot (about −$1) would wipe out the daily-bound test with a handful of gaps, which would make the budget meaningless.
- **Protocol fields that change:**
  - `primary_acceptance.holdout_coverage`: 1.0 → 0.98, with an `imputation` sub-policy;
  - `missing_policy` text;
  - `forecast_checkpoint` text ("all 96 slots" → "subject to missing_policy").

  Everything else in `primary_acceptance` stays as it is. The 5,376-forecast count is computed with the imputed slots included.
- **Hardening (revised 2026-10-12, owner):**
  - No backup internet: the owner has no second uplink and won't pay for one. A UPS isn't needed either, because the Acer is a laptop with its own battery. The Oct 11–12 outage was a hung router WAN: the cable stayed up, the router's LAN side stayed alive (IGMP queries every 2 min throughout), and power-cycling it fixed it.
  - **Budget arithmetic:** 2% is 107 slots, about 27 h over the 56 days, roughly four outages the size of Oct 11–12 (25 slots each).
  - **Still required:** poll failures under 1% per hour, and a second scheduled poll inside the 570–600 s band, so one failed poll doesn't lose the slot.
  - **Optional, free:** a WAN watchdog on the Acer that reboots the Netgear router through its local admin API after about 10 minutes with no WAN. The owner would store the admin password in a root-only file; Claude never sees it. It only helps if rebooting the Netgear (not a separate modem) is what clears the hang.
  - The Wi-Fi failover in `btc-readiness/acer/net_failover/` is built but not installed. Keep it in case a backup network is ever available.
- **Review.** This loosens an acceptance gate that r1's reviews treated as fixed, so the Codex review must explicitly cover the budget, the imputation (that it can never improve a metric) and the rule for logged reasons.

## Built 2026-10-12

`make_revision.py --start 2026-11-02` (originally `--start 2026-10-26`) builds `readiness_protocol.r2.draft.json` from the sealed r1 protocol (pinned sha256). These fields differ:

- the identity, the five dates, `candidate_release` and `approval_policy.deadline_utc`;
- `forecast_checkpoint`, `missing_policy` and `primary_acceptance.holdout_coverage` (0.98);
- the new fields `missing_slot_imputation` and `trade_window_policy`;
- `readiness_status` and `activation_allowed`, reset to draft values.

All other fields are proven byte-identical (untouched digest `155af1c2…`).

The validators must follow:
- `readiness_policy.py`: `EXPECTED_KEYS`, `MODIFIED_FIELDS`, `PRESERVED_POLICY_SHA256` and `START`;
- `study_policy.py`: the `required` dict pins `holdout_coverage: 1.0`;
- the report's `complete_scheduled_checkpoints` check (`btc_copilot_evidence.py:352`), which must become the budget and imputation check.

The implementation brief is `CODEX_TASK_R2.md`.

## Path

1. Implement items 1–7 (Codex) → `run_checks.py` on the Mac and the Acer.
2. Codex review rounds on the changes.
3. Shakedown of at least 72 h; it should be running by Oct 20.
4. Fee refresh within 72 h of sealing → seal → `install.sh stage`.
5. The owner signs `activation_approval.json` before 2026-11-01 00:00 UTC (was 2026-10-25) → `install.sh activate`.
