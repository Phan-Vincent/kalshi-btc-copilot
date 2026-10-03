# Codex task: adversarial QA of the live TA dashboard (2026-10-02)

Target: the BTC TA copilot in this folder (`ta_copilot/`), live at https://homelab.example-tailnet.ts.net:11443 (Acer user service `btc-ta-copilot.service`, loopback :8770, Tailscale Serve). Read `README.md`, `CODEX_REPORT_20261001.md`, `ta_copilot.py`, `stream_flow.py`, `index.html` first. Deployed files on the Acer (`ssh acer`, `~/btc-ta/`) are byte-identical to this folder as of 01:10 UTC.

**Goal:** find real defects — wrong numbers, misleading displays, failure modes, security exposure — by testing the LIVE service against independent sources, not by reading code alone. Every finding needs evidence (command + output, timestamps). Rate each P0 (wrong/misleading number a user could act on, or exposure), P1 (failure mode / degraded behavior), P2 (polish).

## Hard rules

- **Read-only QA.** Do not edit any file in this folder except your report. Do not run `acer/deploy.sh` (deploy or remove), do not restart/stop services, do not change Tailscale Serve/Funnel, do not touch `~/btc-ta/state/` except reading.
- **No Telegram messages.** Never run `ta_watch.py --test` or anything that sends.
- **Public data only.** No Kalshi credentials, account/portfolio routes or orders. Unauthenticated GETs only.
- **Hands off the study:** nothing under `~/btc-study/`, `btc-study-*` units, or `../btc-readiness/`.
- **Bounded load.** Concurrency tests: at most 8 simultaneous SSE clients, at most 60 s each, measured with `ps`/`top` on the Acer. No floods against Kalshi/exchanges (≤1 req/s per endpoint from you).
- To reproduce failure modes, run a **local copy** on the Mac (`python3 ta_copilot.py --port 8781`, Mac has no `websockets` so it runs REST-only) or write throwaway scripts in `$TMPDIR`; you may monkeypatch in those scripts. Kill anything you start.
- Reasonix/DeepSeek may be used for drafting throwaway scripts; don't send it credentials or `~/btc-ta/state` contents.

## Checks (do all; add your own)

### A. Number correctness vs independent sources (sample ≥3 times, spaced)
1. Spot, bid/ask: `/api/state` vs Coinbase public ticker fetched at the same moment.
2. Composite: recompute the median from your own concurrent fetches of the 4 venue tickers; check exclusion logic (stale/outlier) against what's displayed.
3. Kalshi window: ticker, `floor_strike`, open/close times, YES/NO quotes vs `GET https://external-api.kalshi.com/trade-api/v2/markets/{ticker}` and `/orderbook`. Confirm the implied-ask arithmetic and the YES/NO label orientation.
4. Distance and σ: recompute `distance`, `sigma_1m_usd`, `distance_sigmas` from the served 1m candles. Assess whether σ·√minutes is the right scale in the final 2 min, given settlement is on a 60 s *average* (an average has lower variance than the endpoint; at 60–120 s left the current 60 s average isn't the settlement window at all). Quantify how much it overstates/understates; report as a finding if material.
5. Indicators: independently recompute EMA9/21/50/200, RSI14 (Wilder), MACD, BB, ATR, Stoch, swing levels for 1m and 10m from Coinbase candles you fetch yourself (pandas/numpy fine if available, else plain Python). Check 10m aggregation boundaries against 5m data.
6. Change 15m/1h, high/low since open, CVD sign convention (Coinbase `match` side is maker side) — spot-check against raw trades you pull.

### B. Rollover (observe at least two real :00/:15/:30/:45 boundaries live)
Poll `/api/state` every 1 s from T−20 s to T+60 s and record: `window` (null?), ticker, strike, `seconds_left`, `path_partial`, order-flow reset, `stale`. Code reading suggests `poll_market` filters `status=open` and Kalshi lists the new market ~15 s late, so the page may show "No open window" for ~15 s and the old market's countdown sits at 0:00. Confirm or refute, with timings. Also check the tally log gained correct `open`/`settled` lines for those windows (read-only).

### C. Failure modes (local copy / scripts)
1. Kalshi API failing (monkeypatch `get_json` to raise for kalshi URLs): does the page keep showing the old market and quotes with no visible stale marker? `stale` only considers spot/composite; `window.age` isn't displayed. What happens when the stale window passes its close?
2. One venue or all venues failing / returning garbage / future timestamps.
3. NaN path: `/api/state` uses `json.dumps` default (allows NaN → invalid JSON for the browser); `/api/stream` uses `allow_nan=False` and only catches pipe errors, so a ValueError would kill the stream. Can any computed field become NaN/inf from realistic inputs (flat candles, zero volume, identical closes, sigma 0)? Prove or rule out.
4. SSE: each client recomputes `FEED.state()` (all 5 timeframes' indicators) every second with no caching, on unbounded `ThreadingHTTPServer` threads. Measure Acer CPU with 1, 4, 8 clients (bounded per rules). Check disconnected-client thread cleanup (open then kill clients; count threads in `/proc/<pid>/task` before/after).
5. Client: tail-merge correctness across a full resync and a timeframe switch; countdown uses the browser clock, not `server_time` — quantify the error with a skewed clock (e.g., compare to server_time); SSE→poll fallback actually works (block `/api/stream` in a local copy).
6. Tally logger: `settle_pending` revisits every opened-but-unsettled ticker each minute forever — count such tickers in `tally_log.jsonl` (read-only) and check whether any are stuck.

### D. Security / exposure
- Confirm 8770 is loopback-only (`ss -ltnp`), Serve is tailnet-only and Funnel is off (`tailscale serve status`, `tailscale funnel status` — read-only).
- HTTP surface: methods other than GET, odd paths, huge query strings, header injection via `tf`.
- Front-end: `innerHTML` sinks fed by upstream data (venue fields, Kalshi trade `time`, readings detail). Is any upstream string reachable unescaped? The unpkg `<script>` has no SRI/CSP — note the risk level for a tailnet-only, credential-free page.
- systemd unit hardening (read the unit file).

### E. UI pass
Load the page in a headless browser if available (Playwright/Chrome), else skip with a note. Check all 5 timeframes render, no console errors, mobile width (375 px) has no horizontal scroll, dark mode readable.

## Deliverable

Write `QA_REPORT_20261002.md` in this folder: a findings table (ID, severity, title, evidence pointer, suggested fix in one or two lines), then an evidence section per finding, then a list of checks that PASSED with their evidence, then anything you could not test and why. Do not fix code. Last message: one paragraph summary with P0/P1/P2 counts.
