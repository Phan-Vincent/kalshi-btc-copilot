# Task: proxy-vs-settlement measurement, off-Acer alarm, log tools (2026-10-05)

Implemented by Claude directly; the user asked for that instead of Codex. Branch `ta-proxy-alarm-merge` off `master` (`6cff224`).

The dashboard is a display-only decision aid. Nothing here adds a signal, a probability, a trade action or a new Kalshi request. Hands off Study 005. **Deploy freeze:** don't deploy between 2026-10-11 00:00 and 2026-10-13 23:59 UTC.

## Goalpost rule

The pre-declared PRIMARY (evaluated on or after 2026-10-14, first 800 windows) measures the 3-minute change of `composite`. Until it's evaluated, the displayed `composite`, `average_60s` and every existing log field keep their current definitions. This task only **adds** fields, rows and readouts.

## A. Close proxy vs settlement

Kalshi settles KXBTC15M on the BRTI average over the 60 s before close, and the `settled` row already records that value (`expiration_value`). We don't record our own proxy over that same minute, so we can't say how far off the composite is.

1. **Capture.** In `Feed.sample_composite`, on the first sample of a new 15-minute boundary `B`, and *before* appending that sample, compute the mean of the 1 s composite samples with `B−60 ≤ t < B`. Record how many of the 60 seconds were present and valid (`samples`). The ticker is the market whose `close_time == B`: the current market if it matches, else a prefetched one. If there's no matching market, nothing is written.
2. **Row.** A new `close` row: `{"type":"close","ticker","close_time","proxy_avg_60s","samples","captured_at"}`. It goes through the logger's queue, so file I/O stays off the sampler lock and every logger state change stays on the logger thread. One row per ticker; the first wins on reload (same rule as `open`/`settled`).
3. **Score.** `proxy_record` in `/api/state` (cached like `tally_record`; SSE full state carries it), over the most recent 200 windows that have both a complete close row (`samples == 60`) and a settled `expiration_value`:
   - `n`
   - median absolute error
   - 90th-percentile absolute error
   - mean signed error (proxy − settlement)
4. **Page.** A muted line under the "proxies, not BRTI" note in the spot card: `Proxy vs settlement (last n windows): median $X, 90% within $Y, bias ±$Z`. Before any scored window: `Proxy vs settlement: no scored windows yet`. Below 20 windows, append ` · too few to judge`. Use `textContent` only.
5. **Report.** `tally_report.py` adds an exploratory "Proxy vs settlement" section with the same statistics over all complete windows, and counts the incomplete ones.

## B. Off-Acer alarm (runs on the Mac)

The Acer's own watcher can't report the Acer being down, and this week's outage was silent.

1. `acer_watch.py` (stdlib only) checks:
   - the Acer host over the tailnet (TCP connect to its MagicDNS name on port 22)
   - the dashboard over Tailscale Serve (`https://homelab.example-tailnet.ts.net:11443/api/state?tf=1m` is 200, `ready`, not `stale`)
2. It alerts only after **2 consecutive failing checks**. That avoids one-off blips and Mac wake-ups. It doesn't alert when the Mac itself is offline: if it can't reach `api.telegram.org`, it can't send anyway and keeps its state unchanged. Messages are prefixed `[ACER]`: problem, recovered, and every 6 h while unhealthy. It reads the existing Telegram config from `~/.config/btc-study/telegram.json` (0600; copied from the Acer when it's back) and never prints the token.
3. `mac/acer-watch.plist` is a LaunchAgent with a 300 s interval. `mac/install_acer_watch.sh` installs, removes or shows its status; launchd jobs can't read `~/Documents`, so it copies the script to `~/.local/share/btc-acer-watch/` and runs it from there. **Don't install until the Telegram config exists on the Mac.** The Mac fallback service is unaffected.
4. Limit: it only runs while the Mac is awake.

## C. Log tools

`log_tools.py` (stdlib only):

- **`check PATH`**: counts rows by type, unreadable lines, duplicate keys (`open`/`settled`/`close` by ticker, `minute` by ticker+minute) and windows with open but no settled; it also lists missing 15-minute windows (gaps) between the first and last open. It exits 1 if there are unreadable lines.
- **`merge PRIMARY SECONDARY -o OUT`**: rows from PRIMARY win; SECONDARY fills only keys PRIMARY lacks. Bad lines are skipped and counted. The output is sorted by timestamp, mode 0600, and never overwrites an existing file or either input. It prints how many rows each source contributed.

## Tests (no network)

- The close capture uses exactly seconds `B−60…B−1`; it counts a gap; it matches the ticker; it's skipped with no matching market.
- The close row survives a reload with first wins.
- `proxy_record` arithmetic, the last-200 cap, incomplete rows excluded, and "too few".
- `/api/state` carries `proxy_record`, and the page wiring is in place.
- Watcher: the 2-failure debounce, recovery, the 6 h repeat, no state advance when a send fails, and no token in output.
- Log tools: `check` counts and gaps; `merge` precedence, its refusal to overwrite, and 0600 permissions.

The full suite must pass. Update README.

## Outcome (2026-10-05)

- Built as specified on `ta-proxy-alarm-merge`; 174 tests pass.
- **Deviations:** the alarm checks the Acer's MagicDNS name, not its IP. The LaunchAgent files live in `mac/`. `deploy.sh` now copies `log_tools.py` and `acer_watch.py`, because the Acer runs the test suite. The public export scrubs the tailnet IP.
- **Bug found live and fixed:** settlement polling only visited windows with an `open` row, so a `close` row from a window the server joined mid-way was never scored. `settle_pending` now also covers close-only windows. That's one public GET per such window, the same as the existing settlement fetch; it only happens after a mid-window start.
- **Bug found in the browser and fixed:** `const px` redeclared an identifier already used in `render()`, so the page script failed to parse. A new test runs `node --check app.js` when Node is present.
- **First live data point:** the 19:00 UTC window (`KXBTC15M-26OCT051500-00`): proxy $85,653.43 vs settlement $85,652.99, an error of +$0.44.
- `log_tools.py check` on the Mac fallback log found 35 missing windows on 2026-10-05, caused by Mac sleep.
- **Not done:** installing the alarm (it needs the Telegram config from the Acer); deploying (the Acer is offline).
