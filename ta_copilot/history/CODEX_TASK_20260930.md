# Codex task: three TA dashboard upgrades (scheduled 2026-09-30 13:25 PT)

You are working on the BTC TA dashboard in this folder (`ta_copilot/`). Read `README.md`, `ta_copilot.py`, `index.html`, `test_indicators.py` and `acer/` first. It is deployed on the Acer (`ssh acer`, host homelab) as the user service `btc-ta-copilot.service` in `~/btc-ta`, published at https://homelab.example-tailnet.ts.net:11443 by Tailscale Serve. Deploy with `acer/deploy.sh`; check with `acer/deploy.sh status`.

## Hard rules

- Public, unauthenticated GET requests only. No Kalshi credentials, no account or portfolio routes, no orders, and no UP/DOWN or buy/sell recommendations anywhere in the UI or alerts.
- **Do not modify anything under `~/btc-study/` on the Acer, the `btc-study-*` units, or anything under `../btc-readiness/`.** Those are the sealed Study 005 release. Read-only use of `~/.config/btc-study/telegram.json` (the bot token and chat id) is allowed. Never print, log or copy the token.
- Keep the Python standard library as the only dependency. Keep the server on loopback. Keep the existing tests passing, and add tests for each new piece.
- Don't touch the Tailscale Serve config beyond what `deploy.sh` already does. Don't use sudo.

## 1. BRTI proxy

Kalshi settles KXBTC15M on the CF Benchmarks BRTI 60-second average; the dashboard currently uses Coinbase only.
- Add a composite spot from the public tickers of Coinbase, Kraken, Bitstamp and Gemini (BTC-USD). Use the median of the fresh venues; a venue older than 10 s or more than 0.5% from the median is excluded and shown as excluded.
- Keep a rolling 60-second average of the composite from 1-second samples. Show both the composite and the 60 s average in the Kalshi card, with distance to target computed from the composite (and from the 60 s average in the final 2 minutes of the window). Show the per-venue prices and ages in a small expandable panel.
- Label it clearly as a proxy: it is not BRTI, and it can still differ from settlement.
- Candles and indicators may stay on Coinbase.

## 2. Telegram health alerts

- Add a separate user timer on the Acer, `btc-ta-watch.timer` (every 5 min) running `btc-ta-watch.service` → a new `ta_watch.py` in `~/btc-ta`. **Do not** add to `acer_ops.py` or the btc-study timers.
- Alert when the service is not active, `/api/state` fails or reports `stale`, or any feed error has persisted more than 10 minutes. Send one message when a problem starts and one when it recovers, plus a repeat every 6 h while the problem continues. No daily "alive" message: the study watch already sends one.
- Keep state in `~/btc-ta/state/` (chmod 700/600). Prefix messages with `[BTC TA]`.
- Install it from `acer/deploy.sh` (and remove it in `deploy.sh remove`). After installing, send exactly **one** test message: `[BTC TA] test alert — watcher installed`.

## 3. Tally-outcome log (research only)

- At each 15-minute window open, append one JSON line to `~/btc-ta/state/tally_log.jsonl` with: window ticker, open time, target, composite spot, per-timeframe tally (bull/bear/neutral counts and lean), RSI, MACD histogram, and the YES bid/ask at that moment.
- After the window settles, fill in the outcome from the public Kalshi market (`result`, and `expiration_value` if present) as a separate appended `settled` record keyed by ticker. Don't rewrite earlier lines.
- Add `tally_report.py`: it prints the counts and hit rate of the lean against the outcome per timeframe, with a Wilson 95% interval, and says plainly when n is too small (under 100) to mean anything. It's descriptive only: nothing in the dashboard consumes it.
- Logging must not block or slow the dashboard, and a logging failure must never crash the server.

## Finish

1. Run the tests locally, deploy with `acer/deploy.sh`, then run `acer/deploy.sh status` and `systemctl --user list-timers 'btc-ta-*'` on the Acer.
2. Verify the page over https://homelab.example-tailnet.ts.net:11443 (it should return 200, and `/api/state` should show the composite).
3. Update `README.md`.
4. Write `CODEX_REPORT_20260930.md` in this folder: what changed, test results, deploy/status output, anything you could not do or left for review, and any deviation from these rules (there should be none).
