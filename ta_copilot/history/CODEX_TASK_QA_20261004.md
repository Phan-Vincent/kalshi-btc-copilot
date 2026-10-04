# Codex task: adversarial QA of the BTC TA copilot, round 2 (2026-10-04)

Working folder: this directory. `ta_copilot/` is a **scratch copy** of the dashboard at private-repo commit `3989ed2`. `baseline_7976db5/` holds the files as they were at the last QA (`ta_copilot.py`, `app.js`, `index.html`, `tally_report.py` at `7976db5`), plus `app.js.pre_window_change` from `9a08fe6`.

Read these first: `ta_copilot/README.md`, `ta_copilot/history/QA_REPORT_20261002.md` (round 1, 12 findings, all marked fixed), `ta_copilot/history/CODEX_TASK_TALLY_20261003.md` (the spec for the newest code), then `ta_copilot/ta_copilot.py`, `ta_copilot/tally_report.py`, `ta_copilot/app.js`, `ta_copilot/index.html`, `ta_copilot/test_tally_20261003.py`.

**Context:** the production host (the Acer) is offline today, so there is no live deployment to test. Run your own local copy instead (see the rules). The dashboard is a **display-only** decision aid: the owner reads it and decides on their own. It must never give a direction call, a probability of winning, or any automation.

**Goal:** find real defects in the code added since round 1, and confirm the round 1 fixes still hold. That means wrong numbers, misleading displays, statistics that would mislead the owner, research-log corruption, failure modes and exposure. Prove every finding with a command and its output, or with a minimal reproducing script. Severity: **P0** = a wrong or misleading number or verdict the owner could act on, a corrupted research record, or exposure. **P1** = a failure mode or degraded behavior. **P2** = polish.

## Hard rules

- Write only inside this working directory. Never touch `~/Documents/`, `~/btc-ta/`, `~/btc-study/` or anything under `btc-readiness/`. **Don't commit and don't push.**
- Don't SSH to `acer`, `homelab` or any other host. No deploys, no Tailscale, no Telegram (never run `ta_watch.py --test` or anything that sends).
- **Public unauthenticated GETs only**: Coinbase, Kraken, Bitstamp, Gemini, `https://external-api.kalshi.com/trade-api/v2/...` markets and orderbook. No credentials, no account or portfolio routes, no orders. Your own direct requests: at most 1 per second per endpoint. The local server's own polling is fine.
- Local server: `cd ta_copilot && python3 ta_copilot.py --port 8781`. The Mac has no `websockets`, so it runs REST-only; note this as a limit, don't install anything. It writes `ta_copilot/state/` inside this copy, which is fine. **Kill every process you start before you finish.** At most 8 SSE clients at a time, each for 60 s or less.
- To force failures, monkeypatch inside throwaway scripts under `./scripts/`. Don't edit `ta_copilot/*.py`, `*.js` or `*.html`. This QA **doesn't fix code**.
- Suggested fixes must stay display-only: no signals, no trade logic, no paper trading, no alerts.
- Time budget: about 90 minutes. Do the sections in priority order: B, C, D, A, E, F.

## A. Regression of round 1 (quick)

Re-verify that F01–F12 in `QA_REPORT_20261002.md` are still fixed in the current code: expired-window suppression and `kalshi_stale` (F01); the settlement-average σ proxy (F02); countdown anchored to server time (F03); flat-RSI 50 (F04); NaN/inf rejection with strict JSON and SSE survival (F06); the shared state cache and SSE cap of 12 (F07); CSP, nosniff and no-referrer on every response (F10). Prefer short targeted scripts or existing tests over re-reading everything. Report anything that regressed.

## B. Family vote and legacy tally (`ta_copilot.py` around `read(...)`, `reading_family`, `family_tally`, `frame_family`)

1. **Golden comparison:** for the same synthetic or recorded inputs, the legacy `bull`/`bear`/`neutral`/`lean` fields must match `baseline_7976db5/ta_copilot.py` exactly on every timeframe. Build inputs that stress the edges: flat candles, missing EMA200 (too few bars), RSI exactly at 55/45/70/30, VWAP equal to price, and MACD histogram 0.
2. **Family rules:** trend votes only when 3 or more of the 5 trend readings agree; momentum comes from RSI inside 30–70 (stretched RSI is context); the flow family exists only on 1m, at ±0.20 inclusive; context rows (Bollinger %B, Stochastic, stretched RSI) never count. Look for readings that are missing or `None`. Do they become a silent neutral that changes the 3-of-5 arithmetic? Is that shown honestly?
3. `family_net` and `family_lean` (|net| ≥ 2): note that with 2 families off 1m, a "lean" needs both to agree, and with 3 on 1m, 2 of 3. Check that the page text, `/api/state` and the log all agree.
4. **Flow at window open:** the README says flow resets at each window open and is neutral in the opening record. Confirm this, and check whether during minute 1 the 60 s imbalance is computed over fewer than 60 s without being labeled. That would make minute-1 rows non-comparable with later rows.

## C. Research log: `minute` rows, `open`/`settled` rows, `tally_record` (`opening_record`, `minute_record`, `settled_record`, `TallyLogger`)

1. **Timing:** minute rows only within 2 s of `:00`, minutes 1–14, exactly one per minute per window. Try to produce duplicates (a loop iterating twice inside the 2 s slack), skips (a slow `state()` call, a GIL stall, the clock jumping), a minute 0 or 15 row, or a row stamped with the wrong window around rollover. Watch the window the Kalshi listing still shows versus the window the wall clock says it is (the listing lags about 15 s; the prefetched ticker is promoted). Is the minute index derived from the market's `open_time` or from the wall clock, and can those disagree?
2. **No backfill after a restart:** prove it with a script that simulates a restart in the middle of a window.
3. **Concurrent writers:** open, settled and minute rows are appended from different threads to the same `tally_log.jsonl`. Can two lines interleave or tear (partial writes, missing newline, a crash in the middle of a write)? How does the report loader handle a torn line? Is the file still created with private permissions (0600, directory 0700) when it doesn't exist yet?
4. **Quote freshness in minute rows:** the YES bid/ask must be null when stale or missing, and the recorded age must be truthful. Check the rollover seconds, when the old window's quotes could be recorded against the new ticker.
5. **`tally_record`** (Wilson line under the 1m lean): which rows count as scored? What happens with duplicate `open` or `settled` lines for one ticker, `open` lines without `settled` lines, a neutral lean, missing `result`, or a `settled` line whose result conflicts with `expiration_value` vs `target`? Recompute the Wilson interval independently. Check the "too few to judge" threshold (< 100) and the 5-minute cache. Does `refresh_record` read the whole log? On which thread? At the stated growth of about 2 MB/day, estimate its cost after 30, 90 and 180 days. Could it block `/api/state` or SSE?
6. **Settled rows:** `settle_pending` revisits opened-but-unsettled tickers. Confirm it can't loop forever on a ticker Kalshi never settles, or request a ticker more often than every 2 s.

## D. `tally_report.py` statistics: will it tell the owner the truth?

1. Check `spearman` (ties, n < 3, constant input) against `scipy.stats.spearmanr` on random data with ties.
2. **`block_bootstrap`:** windows must be resampled with replacement and the statistic recomputed on the concatenated rows; the seed must be fixed. Run a **calibration check**: simulate at least 200 null logs (no relationship between `family_net` and the forward change, with realistic within-window autocorrelation and about 800 windows × 14 minutes), and measure how often "CI excludes 0" fires. It should be about 5%. Then plant a modest true correlation (ρ about 0.05–0.10) and check that it's detected. Keep runtime sane: shrink rounds if you must, and report what you used.
3. **`forward_changes`:** never bridge gaps (missing minutes) or cross a window boundary; YES mid is null when either quote is missing or stale. Build a synthetic log with gaps, duplicates and out-of-order rows.
4. **Pre-declared PRIMARY:** the date AND the 800-window gate. Is it **the first 800 windows**, chosen deterministically (chronologically), so a later run with more data gives the **same** answer? Or does it silently re-evaluate on all data, or on the latest 800, letting the goalpost drift? What happens before the date with n ≥ 800, and after the date with n < 800? What happens when `now` is naive or in local time?
5. **Settlement-move and market sections:** the join by ticker; signed move = `expiration_value` − `target`; σ-normalization by `sigma_1m_usd`·√15; the minute-1 mid for the market comparison; the 45–55¢ band edges. Do duplicate or conflicting rows inflate n?
6. Are all non-primary sections labeled exploratory? Does any printed line read like a probability of winning or a trade instruction?

## E. Live numbers on the local copy (bounded; skip anything the REST-only build can't do)

With the local server on :8781, sample `/api/state?tf=1m` and `?tf=10m` at least 3 times, spaced at least a minute apart. Check: spot vs the Coinbase public ticker; the composite median recomputed from your own 4-venue fetches; the Kalshi ticker, `floor_strike`, times and quotes vs the public `/markets/{ticker}` and `/orderbook`; the indicators and family votes recomputed independently from candles you fetch yourself; and the 10m aggregation boundaries. If timing allows, watch one real `:00/:15/:30/:45` rollover from T−20 s to T+60 s at 1 s polling. Check that the local `state/tally_log.jsonl` gets correct `open` and `minute` rows.

## F. Front end: the new tally card, the track-record line, the context-window strip

1. Does any `innerHTML` sink in the new tally/family/context/track-record code take upstream or log-derived strings unescaped?
2. **Window in context** (`app.js`, the commit `3989ed2` diff vs `app.js.pre_window_change`): the lookback per timeframe is 1m→15 min, 5m→1 h, 10m→2 h, 15m→3 h and 1h→12 h. Is the right number of bars actually available for each (10m is aggregated)? Is the current window's shading still correct at window open and in the final minute? Does the target line span the view? What does the strip show when the window is null or `kalshi_stale`?
3. If a headless browser is available (Playwright/Chrome), load the local page: all 5 timeframes, no console errors, no horizontal scroll at 375 px, and dark mode readable. If none is available, skip and say so.

## Deliverable

Write `QA_REPORT_20261004.md` in this working directory with:

- a findings table: ID (G01…), severity, title, evidence pointer, and a suggested display-only fix in one or two lines
- an evidence section per finding
- the round 1 regression table
- the checks that PASSED, each with evidence
- what you could not test, and why

Keep reproducing scripts under `./scripts/`. Your last message: one paragraph with the P0/P1/P2 counts and the single most important finding.
