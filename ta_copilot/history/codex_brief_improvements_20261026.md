# Codex brief: BTC TA Copilot improvements (2026-10-26)

You are working in `~/Documents/kalshi-btc-readiness`, on branch `ta-improvements-20261026`.
Work **only inside `ta_copilot/`**. Do not delegate to Reasonix/DeepSeek or any other worker.
Do not commit, push, ssh, or deploy. Python standard library only (no new dependencies).

Read `ta_copilot/README.md` first. The dashboard is read-only and descriptive: no credentials,
no orders, no direction calls, and nothing presented as a trade signal.

## Hard constraints

1. Do not touch anything under `btc-readiness/` (the Study 005 r2 candidate is under a running
   shakedown; any change there invalidates it).
2. `tally_report.primary_result` must stay byte-identical (the test in
   `test_improvements_20261020.py` pins its SHA-256 `0ee39f0e…`). The default `tally_report.py`
   output, without the new flag, must be unchanged.
3. Do not change the research log schema: no new or changed fields in `open`, `minute`, `close`,
   `settled` or `settled_value` rows, and `record_problem()` keeps its current rules. New values go
   only into `/api/state` and the page. If a logged summary (e.g. the `levels` summary in minute
   rows) is built from a function you extend, make sure the logged dict is exactly as before.
4. No new Kalshi or exchange requests, except the direct `GET /markets/{ticker}` in item 1, which
   must go through the existing `direct_market()` throttle (once per 2 s per ticker). Microstructure
   must reuse the order book and trades that `poll_market_context()` already fetches.
5. **No probability display.** The README states the σ distance "is a Brownian settlement-average
   proxy, not a probability". Do not add `above_target_prob` or any settlement probability or
   fair value. Item 2 below is the required-drift part only.
6. Every existing test must keep passing (`python3 -m unittest discover -q -p 'test_*.py'` from
   `ta_copilot/`). Some tests open localhost sockets; if your sandbox blocks them, say which ones
   and confirm the rest pass. Keep the style of the surrounding code.

## Work items

### 1. Derived-ticker fallback at rollover (`ta_copilot.py`, `app.js`)
Today `Feed.poll_market()` uses the `/markets` open listing, then the prefetched upcoming market,
then the current market. Add a last fallback for when none of those gives a market for the current
15-minute slot:
- `derived_window_ticker(now)`: the KXBTC15M ticker for the slot containing `now`, named after the
  slot **close** in `America/New_York` (`zoneinfo`, so EDT/EST is handled): `SERIES + '-' + %y +
  three-letter upper-case month + %d%H%M + '-' + %M`. Port the logic from
  `btc-readiness/qa/study005r2_release/candidate/btc_copilot.py` `direct_candidate()` (read it,
  don't edit it). Test both an EDT and an EST date, and a slot that closes at midnight.
- Use it only when the exchange is known open: a cached `/exchange/status` probe within
  `EXCHANGE_STATUS_REFRESH` that says open, or a fresh probe that does (reuse `is_exchange_closed`;
  a failed probe does not count as open here). Never during a confirmed closure.
- Fetch through `direct_market()`; accept the result only if its ticker matches and
  `open_time == slot` and `close_time == slot + 900` exactly; otherwise treat it as no market.
- Expose `window.discovery_source` in `/api/state`: `"listing"`, `"prefetch"`, `"direct"`,
  `"derived"` or `"cached"` (current market kept from an earlier poll). Show it quietly on the
  Kalshi card in `app.js` only when it is not `"listing"`.
- Keep the existing staleness, grace and `market_promoted_at` semantics unchanged for the existing
  paths; a derived market counts as a fresh successful fetch.

### 2. Final-minute required drift (`ta_copilot.py`, `index.html`, `app.js`)
`window_distance()` already gives the required remaining average and move in the final minute.
Add `required_drift_per_sec = (required_remaining_avg - composite) / remaining_seconds` under the
same conditions (final 60 s, hidden below 5 s remaining), and show it as "$X/s needed" on the
existing final-minute line. Keep the existing `#sig` and `#settlementCoverage` text identical so
current tests pass. Label it as part of the proxy, not BRTI.

### 3. Order-book and Kalshi tape microstructure (`ta_copilot.py`, `index.html`, `app.js`)
- Extend `book_context()` with top-of-book imbalance and 5-level depth imbalance
  (`(yes_bid_depth - no_bid_depth)/(sum)`, null when the sum is 0), and a size-weighted microprice
  of the YES book plus `micro_skew_cents` (microprice minus YES mid). Null when either side is empty.
- `kalshi_tape_summary(trades, now)` over the trades already kept for the window: trailing 60 s and
  window-total contract count, trade count, YES-taker vs NO-taker contract imbalance, and
  volume-weighted YES price. Skip malformed rows rather than raising.
- Render both compactly on the Kalshi context card. Text must say these are descriptive and not
  executable (the mid already says so).

### 4. Volatility regime and multi-timeframe confluence (`ta_copilot.py`, `sr_levels.py`, `app.js`)
- In `analyze()`, add `atr_ratio` = ATR 14 / median of the trailing 60 ATR 14 values, and
  `vol_regime`: `"expanding"` (≥ 1.3), `"compressed"` (≤ 0.7), else `"normal"`; null when fewer than
  60 ATR values exist. It does not vote, count in the tally, or change family votes.
- In `sr_levels.levels()`, add per-zone `higher_tf_count` (distinct native timeframes above the
  viewed one that contributed touches) and `mtf_confluence` (`higher_tf_count >= 2`). Do not change
  zone membership, widths, strength scores, ordering, or the logged levels summary.
- Show the regime next to ATR in the timeframe table and an "MTF" tag on qualifying zones.

### 5. Offline calibration (`tally_report.py`)
Add `calibration_section(opened, settled, minutes)` behind a new `--calibration` flag (default off,
so the default report is unchanged). Using minute rows that have a target, composite and 1m σ, and a
settled result:
- empirical YES settlement rate by distance-in-σ bucket (recompute the distance from the logged
  fields the same way the server does, or use the logged value if one exists),
- empirical YES rate by logged YES mid bucket (10¢ buckets, fresh quotes only),
- minute-14 "lock-in" accuracy: how often the side the composite is on at minute 14 matches the
  result,
each with counts and Wilson 95% intervals, reusing the existing Wilson helper. Label the section
exploratory. Skip rows that fail the existing checks. Settled rows follow the existing first-row-wins
and `settled_value` merge rules.

### 6. Deployment pre-flight and tests
- Add `acer/deploy.sh check`: runs locally only (no ssh): `python3 -m py_compile` on every `.py` that
  the deploy copies, `node --check app.js` (skip with a message if `node` is missing), and the full
  unittest discovery. Make `deploy` run `check` first instead of its current bare unittest line.
  Keep `status` and `remove` unchanged. Update the usage line.
- Add `test_improvements_20261026.py` covering items 1–6 with recorded/synthetic data only (no
  network): ticker derivation (EDT/EST/midnight), the fallback's gating and slot check, the
  discovery source values, required drift and its 5 s cutoff, book imbalance/microprice edge cases
  (empty side, zero depth), the tape summary with malformed rows, `vol_regime` thresholds and the
  < 60 case, `mtf_confluence`, the unchanged logged levels summary, calibration buckets and Wilson
  bounds, the default report being unchanged, and `deploy.sh` having a `check` case. If an
  existing Node harness (see `test_fix_20261007b.py`) can cover the new DOM output, extend it.
- Update `README.md` for each user-visible change, in the existing style (plain, specific).

## When done
Reply with: files changed, a short note per item, anything you skipped or could not verify and
why, and the exact test command output summary (counts, failures, skips).
