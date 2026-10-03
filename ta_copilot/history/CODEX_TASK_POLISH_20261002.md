# Codex task: CSP header and rollover stale flicker (2026-10-02)

Work in `ta_copilot/`. Read `README.md`, the "Fix run 2026-10-02" section of `QA_REPORT_20261002.md`, `ta_copilot.py`, `index.html`, `acer/deploy.sh` and the tests first. Same hard rules as `CODEX_TASK_FIX_20261002.md`: public unauthenticated GETs only, no signals, hands off the study, no Telegram, no Tailscale/sudo changes, watcher contract unchanged (`ready`, `stale`, `errors`), stdlib-only server, match the surrounding style. Reasonix may draft; you review and own it.

## 1. Rollover `kalshi_stale` flicker (P2)

At each rollover `kalshi_stale` is true for ~2 s (14:15:00–01, 14:30:00–01) because the promoted prefetched market's data came from the 30 s `poll_upcoming` and is 16–21 s old until the first direct `GET /markets/{ticker}` lands. Fix it honestly:

- At promotion, trigger the direct per-ticker refresh immediately (don't wait for the next 2 s cycle), and
- don't flag Kalshi stale merely because a just-promoted market's metadata came from the prefetch; staleness should measure time since the last successful Kalshi fetch for the current window (listing or direct), with a short grace (≤ 5 s) after promotion.
- A real Kalshi outage at rollover must still set `kalshi_stale` within ~10 s. Add tests for both: no flag on a normal promotion; flag on a promotion where every Kalshi GET fails.

## 2. Content-Security-Policy (F10 remainder)

- Move the inline `<script>` from `index.html` into `app.js`, served by the handler at `/app.js` (`application/javascript; charset=utf-8`, `Cache-Control: no-store`). Add `app.js` to the `scp` list in `acer/deploy.sh`.
- Send on every response: `Content-Security-Policy: default-src 'none'; script-src 'self' https://unpkg.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. Adjust only if the page genuinely needs more (justify it in the report); the SRI pin on the unpkg script stays.
- Tests: headers present on `/`, `/app.js`, `/api/state`, `/api/stream`; `/app.js` served; `node --check app.js`.

## Deploy and verify

- Full suite on the Mac; `deploy.sh` runs it on the Acer. Deploy so the restart lands **T−60…T−15 s** before a `:00/:15/:30/:45` boundary; record `ActiveEnterTimestamp`.
- Capture the next rollover with 1 s polling T−20…T+60: report `window` presence, target time and every `kalshi_stale` value (expect false throughout).
- Headless browser pass on the live page: no console errors and **no CSP violation reports**, all 5 timeframes render, SSE connects (and polling fallback still works if `/api/stream` is blocked), favicon OK, 375 px no horizontal scroll, dark mode.

## Deliverable

Append a "Polish run 2026-10-02" section to `QA_REPORT_20261002.md` (what changed, test counts, restart time, rollover capture, browser results, any deviations) and update `README.md` (CSP, `app.js`, staleness definition). Last message: one paragraph summary.
