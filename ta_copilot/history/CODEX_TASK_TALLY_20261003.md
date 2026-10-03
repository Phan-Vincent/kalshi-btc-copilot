# Codex task: make the indicator tally honest and measurable (2026-10-03)

Work in `ta_copilot/`. First read `README.md`, `ta_copilot.py` (the `read(...)` tally block and `TallyLogger`), `tally_report.py`, `app.js`, `index.html`, `acer/deploy.sh`, and the tests. Also read `history/QA_REPORT_20261002.md` for context.

## What the user wants

The tally is a **display-only decision aid that the user reads and acts on themselves**. This task changes how the tally counts and how it is scored. It adds **no** automation: no paper trading, no trade journal, no entry/exit triggers, no strategy logic, no signals or alerts. If any part of this brief seems to need one of those, stop and say so in your last message instead of building it.

## Hard rules

- Public unauthenticated GETs only. No credentials, account/portfolio routes, or orders. **No new Kalshi requests:** everything below must reuse state the server already fetches. The Acer's Kalshi connection is shared with Study 005.
- Hands off the study (`~/btc-study/`, `btc-study-*`, `../btc-readiness/`). No Telegram, no watcher changes, no Tailscale/sudo changes. Watcher contract (`ready`, `stale`, `errors`) unchanged.
- No probability-of-winning output anywhere on the page. The tally stays descriptive. Stdlib only; match the surrounding style. Reasonix may draft, but you review and own it.
- The repo is under git. **Do not commit.** Leave the changes in the working tree for review.
- **Deploy freeze:** if the current UTC time is between 2026-10-11 00:00 and 2026-10-13 23:59, do not deploy. Finish everything else and say so.

## Change 1: vote by family, keep the old lean for continuity

Today eight readings vote equally, but five of them (EMA stack, EMA 9/21, EMA 200, VWAP, MACD) measure the same thing: trend. Keep every reading on the page, and add:

- **Families**, each casting one vote: `bull`, `bear` or `neutral`.
  - **Trend:** EMA stack, EMA 9/21, EMA 200, VWAP, MACD. The family vote is the majority of the five, and `neutral` if no side has 3 or more.
  - **Momentum:** RSI 14 (55/45 bands as now). Stochastic is noisy on 1m, so it moves to context (below).
  - **Flow:** the 60 s order-flow imbalance, `bull` at ≥ +0.20, `bear` at ≤ −0.20, else `neutral`. Use it only on 1m; on other timeframes this family is absent.
- **Context rows**, shown but never counted: Bollinger %B, Stochastic, and RSI when stretched (≥ 70 or ≤ 30). Label them "context".
- `family_net` = bull families − bear families. `family_lean` is `bullish`/`bearish` when |net| ≥ 2, else `mixed`.
- Keep the existing `bull`/`bear`/`neutral`/`lean` fields **unchanged** in `/api/state` and in the log, so old and new records stay comparable. Put all thresholds in named constants.

## Change 2: one snapshot per minute inside each window

Keep the opening record exactly as it is. In addition, at each whole minute of a window (minutes 1–14, within 2 s of `:00`), append one `minute` row to `~/btc-ta/state/tally_log.jsonl` with the same private permissions. The row records:

- ticker, minute index, `seconds_left`, `captured_at`
- composite, average_60s, target
- best YES bid and ask with their age (null when stale or missing)
- per timeframe: `bull`, `bear`, `neutral`, `lean`, `family` votes, `family_net`, `family_lean`, RSI, MACD histogram
- the 1m flow imbalance
- `stale` and `kalshi_stale` flags

Do not record outcomes in these rows. The report derives them from later rows. Logging must not block the request path, and a restart must not backfill missing minutes.

## Change 3: a report that can tell something

Rewrite `tally_report.py` to keep the current output as a "legacy" section and add the following:

1. **Continuous outcome at open.** For each scored window: signed move = settlement `expiration_value` − `target`, in dollars, plus the same divided by 1m realized σ × √15 if that σ is in the record (add it to the opening record going forward). Report the Spearman correlation between `family_net` (and legacy `bull − bear`) and the signed move, per timeframe, with a bootstrap 95% CI (resample windows, fixed seed, stdlib only).
2. **Minute horizon (the scalping use).** For each `minute` row, compute from later rows of the same window:
   - the composite change over the next 1 and 3 minutes
   - the YES mid change over the next 1 and 3 minutes, in ¢ (null when either quote is missing or stale)

   Report the Spearman correlation of 1m `family_net` with each, and the mean change by `family_net` value. Use a **block bootstrap by window** for the CIs, because minute rows inside a window overlap.
3. **Against the market.** For opening records with a YES quote:
   - how often the legacy lean agrees with the market's side (mid > 50¢ → yes)
   - the lean's hit rate when it agrees and when it disagrees
   - the hit rate within 45–55¢ opens

   Use counts and Wilson CIs only, no probabilities.
4. **Pre-declared primary measure.** Declare as constants at the top:
   - **PRIMARY** = the Spearman correlation of 1m `family_net` with the 3-minute composite change, over minute rows, block-bootstrapped by window
   - **EVALUATE_AT** = the first report run on or after **2026-10-14 00:00 UTC** with at least **800 windows** of minute rows

   Before then, print "primary not yet evaluated (n windows / 800)". After that, print the result once, with the plain verdict "CI excludes 0" or "CI includes 0". Label every other number "exploratory (many comparisons; expect some to look good by chance)".

## Change 4: page

- In the tally card, show the three family votes and the net, for example `Trend bull · Momentum neutral · Flow bear → net 0 (mixed)`, above the existing per-reading table. Show context rows in muted text marked "context".
- Under the 1m lean, add one honest track-record line from the existing opening log: `Lean at open: 82/160 (51.2%), 95% [43.6%, 58.9%] · interval includes 50%`. Compute it server-side, cache it for 5 minutes, and expose it as `tally_record` in `/api/state` (the SSE full-state path must carry it). Below 100 scored windows, append "too few to judge".
- It must work at 375 px and in dark mode. No inline script and no CSP changes.

## Tests, deploy, verify

- Unit tests (no live network) for:
  - the family vote and its thresholds, including the 3-of-5 trend majority and the absent flow family off 1m
  - the context rows never counting
  - the legacy fields unchanged for a fixed input (golden test)
  - minute-row capture timing, no backfill after a restart, and permissions
  - every report metric on a synthetic log: Spearman, the seeded bootstrap, the block bootstrap by window, forward-change derivation with gaps, market comparison, and the primary gate before and after its date and n
  - `tally_record` present in `/api/state`
- Run the full suite on the Mac. Deploy with `acer/deploy.sh` (subject to the freeze), with the restart landing **T−60…T−15 s** before a `:00/:15/:30/:45` boundary, and record `ActiveEnterTimestamp`.
- Live checks:
  - `/api/state` carries the family fields and `tally_record`
  - a `minute` row appears at the next whole minute with the expected fields
  - the log keeps mode 0600
  - the page renders with no console errors or CSP violations
  - **no new Kalshi request paths** appear in the server log
  - `python3 tally_report.py` runs on the Acer

## Deliverable

Write `TALLY_REPORT_20261003.md` with:
- what changed and the exact constants
- the family definitions
- the minute-row schema
- the report's metrics and the pre-declared primary measure
- test counts, the restart time, the live checks, and any deviations

Update `README.md`. Last message: one paragraph summary, plus `git status --short`.
