# BTC TA dashboard upgrade report — 2026-09-30

## Changes

- Added a four-venue BTC-USD last-trade composite (Coinbase, Kraken, Bitstamp, Gemini), 10 s age and 0.5% outlier exclusions, and a 60 s average requiring 60 contiguous one-second samples. The Kalshi card shows both proxies, venue details, and target distance from the composite or the average during the final two minutes. The page labels both as proxies, not BRTI. Coinbase candles and indicators remain in place.
- Added `ta_watch.py` and the separate `btc-ta-watch.service`/`btc-ta-watch.timer` user units. The watcher checks service activity, `/api/state`, staleness, and feed errors older than 10 minutes. It alerts on problem start, recovery, and every six hours while unhealthy. It has no daily alive message. It reads the existing Telegram configuration on the Acer only, never logs the token, and stores private state under `~/btc-ta/state/`. `acer/deploy.sh` installs and removes the timer and sends the specified installation test message once, guarded by a marker.
- Added append-only `open` and `settled` records in `~/btc-ta/state/tally_log.jsonl`. The collector prefetches the upcoming public market, captures the composite and per-timeframe tally/RSI/MACD at the scheduled boundary, and records the actual capture delay. When the target is not yet public, it holds the compact capture in private `tally_pending.json` and appends the one `open` line after the public target appears. Missing opening YES bid/ask remain null. The separate settlement record uses the public `result` and optional `expiration_value`. Disk writes and settlement GETs run outside the dashboard serving thread. Added the offline, descriptive `tally_report.py` with Wilson 95% intervals and an explicit under-100 warning.
- Updated `README.md` and added focused tests in `test_features.py`.

## Local verification

```text
python3 -m unittest discover -q -p 'test_*.py'
Ran 26 tests in 0.012s — OK
python3 -m py_compile ta_copilot.py ta_watch.py tally_report.py — passed
bash -n acer/deploy.sh — passed
node --check (extracted inline JavaScript) — passed
```

A local `/api/state` smoke check returned `ready: true`, `stale: false`, a four-venue composite, and no errors. A separate temporary-directory integration check ran the actual public Kalshi market-detail GET and settlement append for finalized `KXBTC15M-26SEP301630-30`; it wrote one `settled` line with `result: yes` and `expiration_value: 83703.31`.

## Deployment and status

`acer/deploy.sh` completed successfully after the final change. It ran the 26 tests locally and again on the Acer. Its status output included:

```text
btc-ta-copilot.service  Active: active (running) since 2026-09-30 21:08:22 UTC
btc-ta-watch.timer      Active: active (waiting); Trigger: 2026-09-30 21:10:00 UTC
https://homelab.example-tailnet.ts.net:11443 (tailnet only)
|-- / proxy http://127.0.0.1:8770
ready True stale False composite 83681.6 errors {} frames ['1m', '5m', '10m', '15m', '1h']
```

The required separate `acer/deploy.sh status` check again showed the service active, the watcher timer waiting, the existing Serve mapping, and `ready True stale False composite 83698.015 errors {}`. The Acer `systemctl --user list-timers 'btc-ta-*'` output showed one `btc-ta-watch.timer`; its 21:15 run succeeded and the next trigger was 21:20 UTC. The state directory was mode `700`. The log, pending file, watcher state and one-time test marker were mode `600`. The deployment's successful `--test` step indicates the specified Telegram test message was accepted; the marker's timestamp was 20:34:27 UTC and later deployments did not send another test message.

The published page returned **HTTP 200**. A later HTTPS `/api/state` check showed `ready True`, `stale False`, `composite 83698.015`, `average_60s 83709.74775000001`, and `errors {}`. All four venues appeared in the API response.

The live `21:15` opening produced exactly one `open` record for `KXBTC15M-26SEP301730-30`. Its boundary capture was `21:15:00.864352Z` (0.864 s after open); Kalshi's target of `83712.16` was observed at `21:15:15.472516Z` and the completed line was appended. It contains all five timeframes. Opening YES bid/ask are null because the market was still initialized at capture. `tally_report.py` printed one opening record, zero settled records and the under-100 warning.

## Remaining observation and rule check

Earlier revisions missed the 20:45 and 21:00 UTC openings. The 21:00 `status=open` list was empty at 21:00:15 and returned the new market later. Neither missed observation was backfilled or presented as an opening snapshot. The final approach prefetches the scheduled market and passed the 21:15 live capture check. The 21:15 market has not settled yet; its live `settled` append remains to be observed. Focused tests cover one-time boundary capture, pending state across restart, public target hydration, the settlement GET and append, report scoring, and watcher transitions.

**Literal data-availability limitation:** Kalshi's future market had no public target or usable YES quotes at the scheduled open. The complete `open` line was therefore appended after the target appeared, while its composite and indicators were captured at the boundary. YES bid/ask at that boundary were unavailable and are null. This is a timing/field deviation from a literal reading of section 3; the log exposes both timestamps and does not substitute later quotes. Browser-based visual inspection was unavailable because browser control failed; the page's HTTP 200, API data, and JavaScript syntax were verified.

Reasonix write delegation was blocked by the environment's automatic approval policy before it made any edit; the implementation and verification were completed locally. No files under `~/btc-study/`, `btc-study-*` units, or `../btc-readiness/` were modified. No sudo, Kalshi credential, account/portfolio route, order action, recommendation, or Tailscale Serve configuration change beyond the existing deploy behavior was used. Market-data HTTP calls are public, unauthenticated GETs. The Telegram Bot API POST is the alert delivery explicitly required by section 2 of the task. No other rule deviation was found.
