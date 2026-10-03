# Evidence-only operating and recovery proposal — NOT APPROVED

This proposal concerns the isolated readiness candidate. It grants no authority to start collection, change production or schedules, authenticate, access protected outcomes, remove halt markers, resume a cohort or submit orders. Source/configuration/protocol/package review and explicit destination-bound approval are separate gates.

## Measured capacity and proposed budget

`tests/measure_storage.py` ran the real coherent coordinator, Audit and Study writers for **256 synthetic generations**, retaining every observation. It discarded the first 64 generations for growth estimation and used the maximum of the next three 64-generation windows. The temporary database was removed after measurement; sockets were blocked. Source hashes and exact measurements are recorded in `evidence/storage_capacity.json`.

The copied public fixture is 1,334,876 bytes. Its stored compressed observation payload averaged 36,795.625 bytes; measured database growth reached **38,272 bytes per generation**. The final database was 10,100,736 bytes and contained 256 observations/generations, five synthetic markets, four checkpoints and no signals or outcomes. This measures persistence size, not valid market replay or trading performance: the already-computed fixture was retimed solely to exercise storage.

| Planning component | Bytes |
|---|---:|
| 70 days × 5,760 observations/day = 403,200 generations | 15,431,421,952 |
| Extra 6,720 markets × (one checkpoint + two blobs × eight arms) × measured maximum payload | 4,204,374,720 |
| Extra outcome events, at most one per 15-second poll over 72 days, budgeted 4,096 bytes each | 1,698,693,120 |
| Additional retained-source allowance | 1,048,576 |
| Main database subtotal | 21,335,538,368 |
| Two-times planning headroom | 42,671,076,736 |

The arm/checkpoint allowance is intentionally additive even though measured growth already contains some checkpoints. The measured fixture produces zero eligible signals, so real signal payload overhead must not be assumed absent. The outcome allowance assumes **at most one `settle_one` event per poll**; a different scheduling policy requires a new estimate. Variable response size, volatility, compression ratios, source expansion and SQLite fragmentation can exceed these assumptions. The two-times factor is a planning choice, not a guarantee or measured worst case.

The runtime assigns approximately one-third of total quota to the main SQLite database, reserving the rest for rollback-journal and metadata headroom. Its default **8 GiB total quota permits only about 2.667 GiB main storage**. At the measured fixture rate alone, that reaches the effective database budget in about **12.99 days**, well before the 70-day collection completes. The default is unsuitable for this proposal.

Recommend a separately reviewed **128 GiB total runtime quota and at least 16 GiB free-space reserve**. This permits about 42.667 GiB main storage, exceeding the approximately 39.74 GiB main planning estimate. A filesystem-free-space snapshot showed 311,296,000,000 bytes available during measurement, which exceeded quota plus reserve at that moment. Space was not reserved; competing applications can consume it. No quota or configuration was changed by this task.

Before approval, bind explicit storage values into the new release configuration and manifest, repeat the measurement against the final code, test the selected actual limits, and confirm available capacity on the approved destination. Prefer dedicated capacity where possible. During operation, review growth against this budget; reaching quota/free-space limits must halt and preserve evidence. Increasing a quota mid-cohort is not an automatic recovery action and requires reviewed change control. Do not delete observations or authoritative evidence to make a favorable result possible.

## Publication, locking and restart boundary

- One owner and one writer lock guard a private runtime directory. A concurrent process or concurrent mutation is rejected. No legacy JSON state or output file is an authority.
- SQLite is the sole publication authority. A generation's observation, study writes, confirmation state, JSON and rendered text commit together. The single publication envelope identifies the generation; consumers must not combine it with older exported files.
- A failure before commit rolls the transaction back. A successful commit followed by process-memory failure leaves one durable generation. Such an abrupt exit still requires operator review; durable commit alone is not resume authorization.
- The persisted `RUNNING` marker is synchronized before work. Only an orderly, non-halted close removes its own marker. An unclean marker blocks a new writer. A persisted `HALTED` marker, changed runtime identity, different boot identity, corrupted database or inconsistent generation chain blocks restart.
- An orderly same-boot restart is mechanically supported only with identical bound runtime configuration and continuity checks. Deployment approval must explicitly cover its restart scope; an external supervisor must not bypass markers or reset clocks.

SQLite `DELETE` journaling and `FULL` synchronization provide tested process-crash rollback semantics. They do not prove protection against every hardware, filesystem, controller or physical power-loss failure. Preserve journal files with the database when evidence is captured after an incident.

## Failure actions

| Condition | Required behavior |
|---|---|
| Stale, incomplete or failed collection | Publish/return NO TRADE / DATA UNAVAILABLE, record degraded state and invalidate prior confirmation. Later fresh collection may recover within the existing approved cohort; missing checkpoints or execution windows remain missing. |
| Publication/storage uncertainty or inability to record the failure | Halt or retain an unclean session marker; preserve the database and last committed generation. No fabricated latest observation. |
| Wall/monotonic discontinuity, reboot or incompatible runtime identity | Halt. Do not reset monotonic history or claim continuous observations across the break. |
| Disk quota, low free space, full database or IO failure | Stop writes; retain evidence. If a new HALTED file cannot be written, the already-persisted RUNNING marker remains a restart barrier. |
| Concurrent invocation | Reject the additional writer; do not run parallel collectors or merge competing observations. |
| Conflicting or amended public outcome | Preserve observed events and quarantine under the unchanged study policy. Do not overwrite original evidence. |
| Missed approval/start boundary or required calendar coverage | Do not shift dates, restart the sample, backfill quotes, infer a fill or replace missing returns with zero. |

A transient source failure and a storage/clock halt have different recovery paths. Recording a source outage does not erase its impact on frozen acceptance. The current protocol requires complete holdout coverage; an irrecoverable gap can prevent favorable review even if later collection resumes normally.

## Operator review after a halt

1. Stop the proposed collector/supervisor only within the separately approved operating scope. Do not start a second writer. Record incident time, visible reason, last known generation and version/configuration identifiers without exposing secrets or protected outcomes.
2. Preserve the entire new runtime, including database, rollback journal, lock/session/halt markers and any diagnostics, and record hashes. Forensic analysis should use a separately approved copy; opening a hot SQLite database for recovery can itself write journal changes.
3. Establish whether the last transaction committed, whether the generation chain and publication agree, and whether clock continuity and required windows remain provable. Classify any uncertainty as missing evidence; never infer a successful observation or execution.
4. Determine whether the existing cohort can continue under its unchanged rules. If not, retain it as incomplete/failed and propose a new independently reviewed future cohort. A new cohort cannot inherit favorable observations selectively.
5. Obtain explicit review and authorization for any repair, marker removal, quota change, clock reset, migration or new-cohort activation. This proposal supplies none of those authorizations and contains no automatic repair command.

## Rollback and retention

Rollback means stopping use of the new candidate and retaining its complete evidence for review. It does not mean deleting its runtime, overwriting a prior database, copying new observations into study 002, removing QA/activation guards or silently resuming a previous collector. Original and sealed sources remain untouched. Any separately approved replacement uses a fresh version-bound directory and new approval scope.

All authoritative generations, checkpoints, decision/execution evidence, sources and outcome events remain retained through the approved review/custody horizon. No runtime pruning, downsampling, vacuuming or source deletion is introduced by this proposal. Archived copies and their own storage costs need a separately specified retention and access policy; they are not included as guaranteed free capacity in the live-runtime projection.

## Reproduction

```text
python3 -B qa_btc_2026-09-27/readiness_release/tests/measure_storage.py
```

The command writes a JSON report to stdout and uses only an owned temporary synthetic database. It does not activate the collector or contact a venue. Measurement success is capacity evidence, not operational sign-off or approval to collect.
