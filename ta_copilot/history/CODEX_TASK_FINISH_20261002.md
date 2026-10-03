# Codex task: finish the 2026-10-02 fix run

The previous run (`CODEX_TASK_FIX_20261002.md`, log `codex_run_fix_20261002.log`) implemented and deployed the fixes, then died ("model at capacity") before writing its report. Same hard rules as that task. State at handoff (verified by Claude 09:05 UTC):

- Local and Acer files are identical; service restarted 08:59:27 UTC (T−33 s, inside the window). 122 tests pass on the Mac.
- The 09:00 rollover capture (`/private/tmp/ta_fix_rollover.csv`) shows the new ticker at 09:00:00, target pending 09:00:00–09:00:03, target at 09:00:04, `kalshi_stale` false throughout. The 08:45 capture is `/private/tmp/ta_fix_rollover_0845.csv` (3-sample gap before the sampler-promotion change).
- Pre-fix CPU (from the log): 10% idle, 15% / 45% / 59% with 1 / 4 / 8 SSE clients.

## Do

1. **Do not redeploy** unless step 3 requires a code change; if it does, follow the timed-deploy rule (restart at T−60…T−15 s) and rerun tests.
2. Measure post-fix Acer CPU with 1, 4, 8 SSE clients (≤ 60 s each), same method as before; confirm the 13th concurrent SSE client gets 503 and the page falls back to polling.
3. UI polish only: the window card's quote labels wrap badly ("YES bid (book 1.5s) / ask (book 1.5s)") and the σ row label wraps to two lines at desktop width. Make them one line each (e.g. put source/age once in a small muted line under the quotes, shorten the σ label to "Distance in σ (settle-avg model)"). If you change `index.html`, deploy per the timed rule.
4. Headless browser pass on the live page after any deploy: all 5 timeframes, no console errors, favicon OK, SRI script loads, 375 px no horizontal scroll, dark mode.
5. Append the "Fix run 2026-10-02" section to `QA_REPORT_20261002.md` as specified in the original task: per-finding status (F01–F12), test counts, CPU before/after, both rollover captures (gap length and target time), restart times (08:44:29, 08:59:27, and any new one), Reasonix usage, deviations. Confirm `README.md` reflects the σ model, rollover handling, SSE cap and Kalshi staleness.

Last message: one-paragraph summary.
