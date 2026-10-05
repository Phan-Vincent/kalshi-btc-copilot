# Codex task: fix QA round 2 findings G08 and G07/G11 (2026-10-04)

Work in this folder (`ta_copilot/`). First read `README.md`, `history/QA_REPORT_20261004.md` (the verified verdicts at the top, then evidence E07, E08, E04 and E11), and `ta_copilot.py`: `family_tally`, `frame_family`, `Feed.state` (around the `frame_family` call, ~line 1060), `Feed._capture_open`, `opening_record`, `minute_record` and `TallyLogger`. Then read `stream_flow.py` (`OrderFlow`, `rolling`, `advance`), `tally_report.py` (`load_rows`, `main`), `app.js` (the family line, ~line 218) and the tests, especially `test_tally_20261003.py`.

The dashboard is a **display-only** decision aid. Add no signals, trade logic, alerts or automation.

## Fix 1 (G08 + G04): a damaged log line must not break recovery or the report

Today, one torn line in `state/tally_log.jsonl` makes `TallyLogger.prepare` and `tally_report.load_rows` raise `JSONDecodeError`. `prepare` stops at that line, so every later row is ignored at startup, and `refresh_record` and the pending-open recovery are skipped. Because `append` doesn't check for a trailing newline, the next row written is glued onto the torn line and lost too. The two readers also disagree on duplicates: `prepare` keeps the last `open`/`settled` row per ticker, while the report keeps the first.

1. **`TallyLogger.append`**: serialize appends with a lock. Before writing, if the file is non-empty and its last byte isn't `\n`, prefix the payload with `\n`, so a torn tail can't swallow the new row. Write the whole payload (loop on short writes). Keep `O_APPEND`, mode 0600 and the 0700 directory.
2. **`TallyLogger.prepare`**: skip blank lines. Count and skip any line that isn't a JSON object, then **keep reading**. **First occurrence wins** for `open` and `settled` per ticker, matching `tally_report`. Always run `refresh_record`, the pending-open load and the chmods, even when lines were skipped. If anything was skipped, print one warning to stderr (it goes to the journal) with the count and the first few line numbers. Expose the count as `skipped_lines` in `tally_record`.
3. **Page**: when `tally_record.skipped_lines > 0`, append ` · log: N unreadable lines skipped` to the track-record line (via `textContent`, as now).
4. **`tally_report.load_rows`**: skip blank lines, count and skip unreadable lines, and continue; first occurrence still wins. The report prints `Skipped N unreadable log lines.` near the top when N > 0. Keep the current callers and tests working; if you change the return shape, update every caller.
5. **Don't** report this through `errors`. The watcher contract (`ready`, `stale`, `errors`) stays unchanged.

## Fix 2 (G07/G11): flow votes only on a full 60 s of the current window

The flow tracker resets at each 15-minute window open, so in the first minute the "60 s" imbalance covers only the seconds elapsed since open. A handful of trades can then cast the flow family vote. This was seen live: 10 s after open the line read `Flow bull → net +2 (bullish)`, and a minute later `mixed`. The opening record can be affected the same way.

1. Add the constant `FLOW_MIN_COVERAGE_SECONDS = 60`. On 1m, flow coverage = `min(60, now − start of the current flow window)`, where the start is the 15-minute boundary the tracker reset at. Use the tracker's own reset boundary or the same `int(t // 900) * 900` rule, whichever is exact. Below 60 s of coverage, the flow vote is `neutral` whatever the imbalance. At 60 s or more it votes exactly as today (±0.20 inclusive).
2. The 1m `family` object in `/api/state` gains `flow_coverage_seconds` (a number) and `flow_ready` (a boolean). Other timeframes have no flow and no new fields.
3. **Page**: while `flow_ready` is false, the family line shows the flow part as `Flow warming up (23/60 s)` in the neutral style. Otherwise it's unchanged.
4. **Opening record**: it's captured within 3 s of open, so its flow vote is now always `neutral`. Keep recording the measured `flow_imbalance_1m` unchanged, as context. Add `flow_coverage_seconds` to the opening record.
5. **Minute rows must not change.** A minute-1 row is captured at about open+60.5 s from a state computed in the bucket starting at open+60.0, so coverage is ≥ 60 and the flow vote must be exactly what the current code produces. Prove it with a test at exactly open+60.0 and at open+60.5. Don't add fields to minute rows, and don't change their timing.
6. Leave the separate partial-window case (`partial_window`, "flow observed since connection") unchanged; it's out of scope.

## Hard rules

- Edit only `ta_copilot.py`, `stream_flow.py` (only if needed), `tally_report.py`, `app.js`, `index.html` (only if needed), `README.md` and test files. Put new tests in `test_fix_20261004.py` and update existing tests only where the old behavior was the bug (e.g. `test_opening_record_has_families_and_sigma` expects a bull opening flow).
- Don't touch `history/`, `state/` or `acer/`, or anything outside this folder. **Don't commit.** Don't deploy: the Acer is offline and deployment isn't part of this task. No network is needed.
- Don't change: the legacy tally (`bull`/`bear`/`neutral`/`lean`), the trend or momentum rules, `FLOW_FAMILY_THRESHOLD`, the PRIMARY constants and the report's statistics, the fields or timing of minute rows, Kalshi request behavior, the CSP, or the no-inline-script rule. Stdlib only; match the surrounding style and comment density.

## Tests (no network)

- A torn tail followed by `append`: both the earlier and the new row load afterward, and the torn line is counted as skipped.
- A bad line in the middle: `prepare` loads the rows after it, `tally_record` is computed, and the pending opens are restored.
- Duplicate conflicting `open`/`settled` rows: the dashboard (`tally_record`) and the report agree, with first wins.
- The report runs with a torn line and prints the skip count.
- Two threads appending 200 rows each produce 400 valid lines. Permissions are still 0600 and 0700.
- The flow vote is neutral below 60 s of coverage even at imbalance ±1, and votes at exactly 60 s.
- Minute rows at open+60.0 and open+60.5 have the same family votes as the current code for the same inputs.
- The opening record at open+0.2 with imbalance +0.25 has a neutral flow vote and keeps `flow_imbalance_1m` = 0.25.
- `/api/state` 1m carries `flow_coverage_seconds` and `flow_ready`.
- Wiring tests for the page: the warming-up text and the skipped-lines note.

Run the full suite: `python3 -m unittest discover -q -p 'test_*.py'`. Everything must pass.

## README

Update the family-vote paragraph: flow votes only once 60 s of the current window have passed, shows "warming up" before that, and so is always neutral in the opening record. Update the research-log section: unreadable lines are skipped and counted, an append repairs a missing trailing newline, and the first row per ticker wins.

## Last message

List each change with `file:line`, the test count before and after, and anything you were unsure about or left undone.
