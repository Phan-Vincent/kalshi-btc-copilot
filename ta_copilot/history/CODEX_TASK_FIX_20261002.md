# Codex task: fix the QA findings in the live TA dashboard (2026-10-02)

Work in this folder (`ta_copilot/`). Read `README.md`, `QA_REPORT_20261002.md` (the findings and evidence), `ta_copilot.py`, `stream_flow.py`, `index.html`, `ta_watch.py` and the tests first. Deploy target: the Acer via `acer/deploy.sh` → https://homelab.example-tailnet.ts.net:11443.

## Hard rules (unchanged from earlier tasks)

- **Public, unauthenticated GETs only.** No Kalshi credentials, account/portfolio routes or orders.
- **No signals.** No UP/DOWN, buy/sell, or probability-of-winning output anywhere. A σ distance is allowed; converting it to a probability is not.
- **Hands off the study:** nothing under `~/btc-study/`, `btc-study-*` units, or `../btc-readiness/`.
- **No Telegram messages** (never `ta_watch.py --test`). No Tailscale Serve/Funnel changes, no sudo.
- **Watcher contract:** `/api/state` keeps `ready`, `stale`, `errors` (with `at`/`first_at`) and their meanings; `ta_watch.py` must keep working unchanged. Add new fields rather than repurposing these.
- Keep the code style of the surrounding code (stdlib only on the server; small, plain functions; short comments).
- Reasonix/DeepSeek may draft code; you own correctness and must review everything it produces. Don't send it credentials or `~/btc-ta/state` contents.

## Fixes (finding IDs from the QA report)

1. **F01 — stale/expired Kalshi market.** A market whose `close_time` has passed must never be shown as the current window. Add `window.market_age` freshness (already `age`): if older than 10 s, the page shows a visible "Kalshi data N s old" marker on the window card and the header badge is not plain green "Live" (e.g. amber "Live · Kalshi stale"). Add `kalshi_stale: bool` to state; do not fold it into `stale` (watcher semantics).
2. **F05 — rollover gap.** At each rollover the page shows no window for 11–21 s because `poll_market` relies on the `status=open` listing, while `GET /markets/{ticker}` already returns the new market (target appears ~7–10 s after open). When the listing has no live market, use the prefetched `upcoming_markets` entry whose `open_time <= now < close_time` and poll it directly with `GET /markets/{ticker}` (≤ 1 req / 2 s). Show it immediately as the current window; while `floor_strike` is missing, show "Target pending" (not "No open window") and blank distance/σ. Book/trade context should follow the new ticker as soon as it's selected.
3. **F02 — σ against the settlement average.** Kalshi settles on the 60 s average over the final minute `[close−60, close)`. Replace the distance/σ logic with a settlement-average model (Brownian, per-second variance `σ_1m²/60`, σ_1m from the existing realized 1m sigma × reference price):
   - `L = seconds_left > 60`: reference = current composite; `sd = σ_1m·√((L−60)/60 + 1/3)`.
   - `L ≤ 60`: observed part = composite samples (1 s path) in `[close−60, now]`; projected average `A = (sum(observed) + composite·L_unobserved) / 60` using actual sample counts (if fewer observed samples than expected, say so and treat missing seconds as unobserved at the current composite); `sd = σ_1m·√((L/60)³/3)`.
   - `distance = reference_or_A − strike`; `distance_sigmas = distance / sd` when sd > 0.
   - Expose `distance_source` (`"composite"` / `"projected 60s average"`), `distance_sd_usd`, and `sigma_model: "brownian settlement-average proxy"`. Label in the page: "Distance in σ (settlement-avg model, proxy)". Keep the README caveat that this is a proxy, not BRTI, and not a probability.
   - Unit tests with hand-computed values at L = 600, 115, 55, 1.
4. **F07 — SSE cost.** Compute `FEED.state(tf)` at most once per second per timeframe and share it across all SSE clients and `/api/state` callers (a small cache keyed by tf with a ~1 s TTL; thread-safe). Cap concurrent SSE clients (e.g. 12); beyond the cap return 503 so the page falls back to polling. Measure on the Acer after deploy with 1/4/8 clients (≤ 60 s each) and report CPU before/after.
5. **F12 — stale Coinbase ticker shown fresh.** Spot freshness must consider the exchange trade timestamp, not just receipt time: if the Coinbase ticker's own `time` is older than `VENUE_MAX_AGE`, treat spot as stale (`stale` true is correct here — spot really is stale) and fall back to REST if the stream is lagging.
6. **F08 — sampler delay.** In `state()`, when the cached composite is > 2 s old but `composite_quote(self.venues, now)` still yields a value from fresh venues, use that recomputed value instead of blanking; only blank when no venue is fresh.
7. **F06 — NaN.** Validate bid/ask (and every float taken from an upstream payload into state) as finite; reject the message otherwise. Serialize `/api/state` with `allow_nan=False` too; in the SSE loop catch `ValueError` from serialization, record it in `errors`, and keep the stream alive.
8. **F04 — flat RSI.** When average gain and average loss are both 0, RSI = 50.
9. **F09 — empty book side.** Fall back per side: if the book lacks a side, show that side from the market summary, labeled as such.
10. **F03 — countdown clock.** Compute the countdown from the server: keep an offset `server_time − Date.now()` updated on each state and apply it in `tick()`.
11. **F11 / F10.** Add an inline data-URL favicon. Pin the unpkg script with `integrity` (sha384 of the exact 4.2.3 file you fetch) and `crossorigin="anonymous"`.

## Tests

Add focused unit tests for every fix (stdlib `unittest`, no live network; monkeypatch `get_json`). Run the whole suite on the Mac and let `deploy.sh` run it on the Acer. Extract and `node --check` the inline JS. Re-run the QA reproductions from the report for F01, F02, F05, F06, F08, F09, F12 against a local copy and show they now behave correctly.

## Deploy (timed)

A restart wipes the current window's price path and order flow, making it "partial". Deploy so that the service restart lands between **T−60 s and T−15 s** before a 15-minute boundary (`:00/:15/:30/:45` UTC), so the next window is complete. `deploy.sh` runs tests then restarts — start it accordingly and record the actual restart time (`systemctl --user show -p ActiveEnterTimestamp btc-ta-copilot`).

After deploy:
- `acer/deploy.sh status` OK; `/api/state` ready, not stale, no errors.
- Observe the next real rollover with 1 s polling from T−20 to T+60: report how long (if at all) the window is missing or shows "Target pending", and when the target appears.
- Headless browser pass (as in the QA): no console errors, all timeframes render, favicon no longer 404s, SRI script loads.
- Watcher: run the watcher's check path in a way that does NOT send Telegram (read its code; if there's no dry-run, don't run it — just confirm `/api/state` fields it reads are intact).

## Deliverable

Append a "Fix run 2026-10-02" section to `QA_REPORT_20261002.md` with a row per finding (fixed / not fixed + why), test counts, the before/after CPU numbers, the rollover timings, the restart time, and any deviations from these rules. Update `README.md` where behavior changed (σ model, rollover, SSE cap, Kalshi staleness). Last message: one paragraph summary.
