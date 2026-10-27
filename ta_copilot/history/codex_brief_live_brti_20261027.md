# Codex brief: live BRTI and Kalshi WebSocket feed (2026-10-27)

You are working in `~/Documents/kalshi-btc-readiness`, on branch `ta-live-brti-20261027`.
Work **only inside `ta_copilot/`**. Do not delegate to Reasonix/DeepSeek or any other worker.
Do not commit, push, ssh, deploy, or make network requests (there is no network; everything you
need is below and in `ta_copilot/fixtures/kalshi_ws_20261027.jsonl.gz`).

Read `ta_copilot/README.md` and `ta_copilot.py` first. Keep the style of the surrounding code.

## Why

Kalshi settles KXBTC15M on CF Benchmarks BRTI: the mean of the 60 one-second BRTI values at
seconds `close-60 … close-1`. We verified this offline: that mean reproduced Kalshi's
`expiration_value` to $0.01 over 2,342 windows, while the copilot's four-venue composite proxy
misses by $2.63 median and was on the wrong side of the target in 36 of 1,889 windows. The owner
has created a **read-only** Kalshi API key, so the copilot can now read BRTI and Kalshi market
data over Kalshi's authenticated WebSocket. This makes the dashboard's settlement figures exact.
It is not a forecasting signal: the owner's evaluation showed BRTI adds nothing beyond the market
price, so nothing here may present a direction, probability or fair value.

## Hard constraints

1. Do not touch anything under `btc-readiness/`.
2. `tally_report.primary_result` must stay byte-identical (pinned SHA-256 `0ee39f0e…` in
   `test_improvements_20261020.py`). The default `tally_report.py` output must be unchanged.
3. **Keyless mode is the old dashboard.** If the key directory or either file is missing, the
   copilot must behave exactly as today (all existing tests pass unchanged), plus a `brti`/
   `kalshi_ws` status block that says not configured. REST polling and the composite proxy remain
   the fallback for every value this work adds or replaces.
4. Existing logged fields keep their meaning: `composite`, `average_60s` (minute rows) and
   `proxy_avg_60s` (close rows) stay composite-based, because PRIMARY, the minute-horizon section
   and the proxy record use them. Only the additive fields in item 6 are new.
5. No probability display, no direction calls, no trade signals. The σ distance remains "a
   Brownian settlement-average proxy, not a probability"; only its reference price changes.
6. Dependencies: standard library, plus `cryptography` and `websockets`, which the Acer has as
   system packages (cryptography 46.0.5, websockets 16.1). Import both lazily, as `run_stream`
   already does for websockets. The Mac has neither (its `cryptography` is a broken namespace
   stub: `from cryptography.hazmat.primitives.asymmetric import ed25519` raises ImportError), so
   tests that need them must `skipUnless` they import; everything else must run on the Mac.
7. Every existing test keeps passing (`python3 -m unittest discover -q -p 'test_*.py'` from
   `ta_copilot/`). Some tests open localhost sockets; if your sandbox blocks them, say which.

## Credential handling (strict)

- Key directory: `TA_KALSHI_KEY_DIR`, default `~/.config/btc-ta-copilot/kalshi`. Files: `key_id`
  (one line, the Key ID) and `private_key.pem` (PEM; the owner's is Ed25519 PKCS#8, but also
  support RSA, which Kalshi signs with RSA-PSS SHA-256, MGF1 SHA-256, salt length = digest length).
  Detect the type from the parsed key, not the PEM header.
- Refuse the key (keyless mode plus an error in `errors["kalshi_key"]`) if the directory or
  `private_key.pem` is accessible by group or others (`mode & 0o077`), not owned by the current
  uid, unreadable, or unparsable. The error text names the problem, never file contents.
- Signing: headers `KALSHI-ACCESS-KEY` (Key ID), `KALSHI-ACCESS-TIMESTAMP` (ms, string) and
  `KALSHI-ACCESS-SIGNATURE` = base64(sign(timestamp + method + path_without_query)).
  Enforce an allowlist inside the signer: only `GET` and only these paths may be signed:
  `/trade-api/ws/v2`, `/trade-api/v2/api_keys`, `/trade-api/v2/cfbenchmarks/values`. Anything
  else raises. No portfolio, balance, order, fill or position endpoint is ever called.
- **Scope check before first use:** signed `GET https://external-api.kalshi.com/trade-api/v2/api_keys`
  returns `{"api_keys": [{"api_key_id": ..., "name": ..., "scopes": [...]}, ...]}`. Find this
  key's entry; use the key only if its scopes are non-empty and every scope is `read` or starts
  with `read::`. Otherwise (a `write…` scope, key not listed, malformed response) refuse with an
  error. Network failure: stay keyless and retry with backoff (cap 5 min). Re-check every 6 h; a
  failed re-check after a success keeps the key in use, but a response showing a write scope stops
  it at once.
- Never put key material, the Key ID, signatures or request headers in `/api/state`, the page,
  logs, exceptions surfaced through `_note`, or test output. Tests use throwaway keys in temp dirs
  and must never read the real key directory.

## Kalshi WebSocket facts (verified live on 2026-10-27 with the owner's key)

- URL `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; sign `GET` + `/trade-api/ws/v2` and pass
  the three headers in the handshake (`websockets.sync.client.connect(url,
  additional_headers=headers, open_timeout=10, ping_interval=20, ping_timeout=20,
  max_size=4_000_000)`). New headers (fresh timestamp) on every connect.
- Commands: `{"id": n, "cmd": "subscribe", "params": {"channels": [...], ...}}` with a unique
  increasing integer `id`; ack `{"type": "subscribed", "id": n, "msg": {"channel": c, "sid": s}}`.
  `{"id": n, "cmd": "unsubscribe", "params": {"sids": [s]}}`. `{"id": n, "cmd":
  "update_subscription", "params": {"sid": s, "action": "add_markets" | "delete_markets",
  "market_tickers": [...]}}`, acked by `{"type": "ok", "id": n, "sid": s, "seq": ..., "msg":
  {"market_tickers": [...]}}`. Errors: `{"type": "error", "id": n, "msg": {"code": k, "msg": text}}`
  (e.g. 8 unknown channel, 9 channel requires authentication, 26 per-subscription market limit).
- `cfbenchmarks_value` with `"index_ids": ["BRTI"]`: about one frame per second. `msg.data` is a
  JSON string `{"type":"value","time":<ms, whole second>,"id":"BRTI","value":"79620.13"}`;
  `msg.received_at` (ms). It also carries `avg_60s_data` and `last_60s_windowed_average_15min`
  (`value`, `window_size`, `window_start_ts_ms`, `window_end_ts_exclusive`), but in the capture
  their `window_size` started at 0–1 on a new connection, so they appear to cover only ticks this
  connection saw. **Do not use Kalshi's averages for the settlement figure; compute our own from
  the per-second values** (expose Kalshi's as diagnostics at most).
- `cfbenchmarks_value_5hz` with `"index_ids": ["BRTI"]`: a frame every 200 ms; `msg.value_usd`
  (string, 8 decimals), `msg.source_ts_ms`, `msg.received_at`, `msg.data` (same raw JSON). Its
  whole-second ticks equal the 1 s channel's values. Receive latency was about 54 ms median.
- `orderbook_delta` with `"market_tickers": [...]`: first an `orderbook_snapshot` per market
  (`msg.market_ticker`, `msg.yes_dollars_fp` / `msg.no_dollars_fp`: lists of `[price, count]`
  decimal strings, either list absent when empty), then `orderbook_delta` frames (`msg.market_ticker`,
  `price_dollars`, `delta_fp` signed decimal string, `side` `"yes"|"no"`, `ts_ms`). `seq` increases
  by exactly 1 per frame **per sid** (across all markets on that sid); a gap means a lost update.
  Kalshi publishes YES bids and NO bids only, like the REST book. A level whose count reaches 0 is
  removed. After a market closes, Kalshi sent an empty snapshot for it.
  Cross-check from the capture: the book rebuilt from snapshot + deltas matched the `ticker`
  channel's `yes_bid_dollars` and `yes_ask_dollars` (= 1 − best NO bid) on all 35 ticker frames.
- `trade` with `"market_tickers": [...]`: `msg.trade_id`, `market_ticker`, `yes_price_dollars`,
  `no_price_dollars`, `count_fp`, `taker_side`, `is_block_trade`, `ts_ms`; `seq` per sid.
- `ticker`: `msg.market_ticker`, `yes_bid_dollars`, `yes_ask_dollars`, `price_dollars`,
  `volume_fp`, `ts_ms`, about one frame per second when anything changes.
- Rate limits: Basic tier, 200 read tokens/s; most REST GETs cost 10, cfbenchmarks passthrough
  GETs cost 50. The REST passthrough `GET /trade-api/v2/cfbenchmarks/values?id=BRTI` (signed path
  without the query) returns `{"data": {"serverTime": ..., "payload": [{"value": "79844.26",
  "time": <ms>}, ...]}}`: the last hour at 1 s resolution, ascending.

The fixture `fixtures/kalshi_ws_20261027.jsonl.gz` holds 1,368 real frames, one JSON object per
line `{"recv_ms": <local receive ms>, "frame": <frame>}`: the first 12 s of a session subscribed to
all five channels for `KXBTC15M-26OCT270100-00` (contiguous seqs), plus every frame within 3 s of
that market's close at `2026-10-27T05:00:00Z` (so the delta seq jumps between the two parts,
which is a real gap to test).

## Work items

### 1. `kalshi_ws.py` (new module): credentials, signing, parsing
Pure, testable pieces: loading and checking the key directory; the allowlisted signer; the scope
check (taking a fetch function so tests inject responses); frame parsers for BRTI (per-second
value and 5 Hz value with source time), order book (snapshot/delta application with per-sid seq
checks), trades (rows in exactly the shape `kalshi_trade_rows()` produces, including `trade_id`,
with `time` as an ISO string `parse_ts` reads), and the subscription bookkeeping (pending command
ids → sids, which tickers each sid covers). Build a book view in the REST shape
`{"orderbook_fp": {"yes_dollars": [[p, c], ...], "no_dollars": [...]}}` so the existing
`book_context()` is reused unchanged. Keep per-delta work O(1) (dict updates); sort only when a
view is built.

### 2. Feed integration (`ta_copilot.py`)
- One new thread, `Feed.run_kalshi_ws`, started only when the key passes the checks above; bounded
  reconnect backoff (1 s doubling to 30 s) like `run_stream`; a recv timeout of 10 s (BRTI frames
  arrive five times a second, so 10 s of silence means a dead connection). All shared state under
  `self.lock`; parse outside the lock.
- Subscribe to both BRTI channels, and `orderbook_delta` + `trade` (+ `ticker` if you use it) for
  the current window's ticker and, from 60 s before its open, the next upcoming ticker (from
  `upcoming_markets`), so the book and tape are already live at the open. Remove markets that have
  closed. Use `update_subscription` add/delete on the existing sids; resubscribe on any error or
  if an ack doesn't come within 5 s.
- Order book: when the WebSocket book for the current ticker is in sync (snapshot received, no seq
  gap since) and the connection delivered a frame within the last 3 s, it is the window's
  orderbook (`window["orderbook_source"] = "websocket"`; `orderbook_age` = seconds since the last
  frame received on the connection, since an in-order stream on a live connection has lost
  nothing), and `poll_market_context` skips the REST orderbook GET. On a seq gap, mark that sid's
  books out of sync and resubscribe that channel for its tickers (fresh snapshot); meanwhile and
  whenever the WebSocket is unhealthy, REST polling resumes exactly as today
  (`orderbook_source = "rest"`). `yes_mid_path` keeps one point per second either way.
- Trades/tape: WebSocket trades for the current ticker merge into `self.kalshi_tape` (deduped by
  `trade_id`). Track the time from which the trade subscription for that ticker has been in
  sequence on the current connection. The tape is complete from `from` while it is: the REST
  tape's coverage reaches back from the subscription start (or the subscription started before the
  window opened), and the trade seq has had no gap since. While that holds, the REST trades paging
  stops and `tape["to"]` advances with received frames; on a gap or disconnect, fall back to the
  existing REST paging and `merge_tape` rules (which will restart coverage if a hole appears).
  `complete_window`/`complete_60s`/`fresh` keep their current meaning. `kalshi_recent_trades`
  stays the newest 30 rows.
- BRTI: hold per-second values `{second: value}` for the last 20 minutes (from the 1 s channel,
  and from 5 Hz ticks whose `source_ts_ms % 1000 == 0`), plus the latest 5 Hz value and its source
  time. `brti_age` = now − latest source time. BRTI is **fresh** when that age ≤ 2 s.
  After a connect or reconnect, if any second in the last 120 s is missing, backfill once from the
  signed REST passthrough above (at most one such GET per 30 s; keep its values only for seconds
  not already held).
- `/api/state` adds a top-level `brti` block: `configured`, `connected`, `value`, `age`, `fresh`,
  `avg_60s` (our mean of the 60 one-second values ending at the last whole second, null unless all
  60 are present), `minus_composite` (BRTI − composite when both exist), and `error` (from
  `errors`, if any). Also `feed_modes["Kalshi"]`: `"WebSocket"`, `"REST fallback"` or `"keyless"`.
  The existing keys and their meanings stay as they are.

### 3. Settlement math on BRTI (`ta_copilot.py`)
- `window_distance()` gets its reference from BRTI when BRTI is fresh, else the composite as
  today. Add `window["settlement_basis"]`: `"BRTI"` or `"proxy"`. With BRTI, `distance_source` is
  `"BRTI"` before the final minute and `"projected 60s BRTI average"` inside it; the proxy path's
  values stay exactly as now.
- Final minute with BRTI: observed seconds are the BRTI per-second values for seconds
  `close-60 … now-1` that have ended (same "a second is due once it has ended" rule as the proxy),
  missing ones filled with the current BRTI, so `observed_mean`, `required_remaining_avg`,
  `required_remaining_move`, `required_drift_per_sec` and the σ scale are computed on BRTI.
- Add `window["settlement_average"]` in the final minute and for 30 s after close: the mean of the
  BRTI seconds observed so far in `close-60 … close-1`, the count observed, and, once all 60 are
  held, `complete: true`. After close that complete mean is exactly Kalshi's settlement value; label
  it "BRTI settlement average", never "result".
- Sigma stays the 1m realized σ from Coinbase candles (unchanged).

### 4. Page (`index.html`, `app.js`)
- Kalshi card: a BRTI line (value, age, BRTI − composite) with a quiet "BRTI live" / "proxy:
  BRTI unavailable (reason)" label from `settlement_basis` and `brti`. Name the basis wherever the
  σ distance and final-minute figures appear (e.g. "vs BRTI" / "vs proxy"). In the final minute,
  show the BRTI settlement average so far with its observed count, and the complete value after
  close. Show the orderbook source quietly when it is not the WebSocket.
- Keep existing element ids and text that tests assert; add new elements rather than reshaping
  old ones. No probability or direction wording.

### 5. Watcher
No changes to `ta_watch.py` / `acer_watch.py` alert rules.

### 6. Research log (additive only)
- Minute rows gain `brti` (the BRTI value at capture, null unless fresh) and `brti_age` (seconds,
  null when no BRTI).
- Close rows gain `brti_avg_60s` (our mean of the 60 BRTI seconds `close-60 … close-1`, null
  unless all 60 are held) and `brti_samples` (count held).
- Add the three numeric fields to `tally_report.NUMERIC_FIELDS` so `record_problem` validates them
  as finite JSON numbers when present. Old rows without them stay valid.
- `tally_report.py --calibration` (only) adds one line: live BRTI close average vs
  `expiration_value` over complete windows (count, median and max absolute error, side-vs-target
  mismatches). Default output unchanged; `primary_result` unchanged.

### 7. Deploy and docs
- `acer/deploy.sh`: include the new module in the `py_compile` list and the copy, and copy
  `fixtures/` (create `~/btc-ta/fixtures/` remotely) so the Acer test run has the fixture. Never
  copy anything from the key directory. `status` adds one line from `/api/state`: BRTI
  configured/connected/age and `feed_modes["Kalshi"]`.
- `acer/btc-ta-copilot.service`: update the Description (no longer "no account access") to say a
  read-only Kalshi key is used for market data and BRTI. Keep the hardening; the default key path
  under `%h/.config` must stay readable by the service (it runs as the owner).
- `README.md` and the `ta_copilot.py` docstring: replace "no credentials, no account access" with
  the accurate rule: a read-only (scope `read`) Kalshi API key, checked at startup, used only for
  the market-data WebSocket, the BRTI backfill and the scope check itself; no orders, no portfolio
  or balance reads; keyless fallback. Document the key location and permissions, the BRTI basis,
  the new state and log fields, and the measured facts above (settlement rule and $0.01 match).
  Keep "Paper only" / descriptive language.

## Tests (new file `test_live_brti_20261027.py`)
Use the fixture and fakes; no network, no real key directory. At least:
- Book: replaying the fixture's first part through the parser reproduces every `ticker` frame's
  YES bid/ask in that part; the REST-shaped view goes through `book_context()`; the seq jump into
  the second part is detected as a gap and produces a resubscribe command; the post-close empty
  snapshot empties the book.
- Trades: fixture trade frames become rows identical in shape to `kalshi_trade_rows()` output;
  dedupe against REST rows by `trade_id`; tape completeness for (a) subscription before window
  open, (b) mid-window subscription bridged by REST coverage, (c) a trade-seq gap falling back to
  REST.
- BRTI: per-second extraction from both channels; freshness by source time; `avg_60s` needs all
  60 seconds; backfill merges REST payload without overwriting held seconds and is rate-limited.
- Settlement: final-minute `window_distance` on BRTI with some seconds missing; settlement
  average complete only at 60 seconds and equal to the plain mean; proxy path unchanged when BRTI
  is stale.
- Credentials: refuses a 0644 key file or 0755 directory; signer refuses non-allowlisted paths and
  non-GET; scope check accepts `["read"]` and `["read", "read::portfolio_balance"]`, refuses
  `["read", "write"]`, `["write::trade"]`, a missing key and a malformed response; no state or error
  text contains the Key ID or key bytes. Signature tests (Ed25519 and RSA-PSS verify with the
  public key; signed message is `timestamp + "GET" + path` without the query) `skipUnless`
  cryptography's asymmetric modules import.
- Keyless: with no key directory the state matches today's except for the added `brti` block and
  `feed_modes["Kalshi"] == "keyless"`, and no WebSocket thread is started.
- Log: minute and close rows carry the new fields; `record_problem` accepts them and rejects NaN;
  `primary_result` digest unchanged; default report unchanged.
- Rollover: subscribing the next ticker 60 s before its open and deleting the closed one, driven
  by a fake socket that records sent commands and returns acks.

## When done
Report: files changed; how REST fallback is triggered and restored; any decisions you made that
this brief didn't settle; the full test run result (and anything skipped or blocked in your
sandbox); and anything you think is risky to deploy.
