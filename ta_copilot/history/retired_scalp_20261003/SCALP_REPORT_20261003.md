# Experimental paper scalp report — 2026-10-03

## Built

Added `scalp_signal.py`, `scalp_report.py`, and offline tests. The engine evaluates one state snapshot per second even when no browser is open. Its only external settlement lookup is an unauthenticated public `GET /markets/{ticker}` after a held market closes. No account, portfolio, or order route was added. `/api/state.scalp` and both full and tail SSE events carry the paper state and all-time statistics. The indicator tally remains descriptive; the new panel beside its 1m lean is the sole directional exception. It persistently says **EXPERIMENTAL · paper · unvalidated**. `README.md` records this exception and its limitations.

The journal is `~/btc-ta/state/scalp_log.jsonl`, created with mode 0600 in a mode 0700 state directory. It appends enter, exit, no-bid, settlement, and restart-abort records with times, ticker, side, observed quotes and depth, fees, input values, exit rule, and realized P&L where applicable. An open paper position at restart gets an `aborted` exit, is counted, and is excluded from completed-trade statistics. `scalp_report.py` reports all-time completed trades, realized win rate and Wilson 95% interval, mean after-fee P&L per contract and an approximate Student-t 95% interval, cumulative P&L, max drawdown, aborted count, and the $0.01 fee-rounding sensitivity.

## Exact rules and constants

| Rule | Constant and behavior |
|---|---|
| Paper size | `PAPER_SIZE = 10` contracts, one position at a time. |
| Entry freshness | `stale = false`, `kalshi_stale = false`, book age `≤ 5 s`. A missing value blocks entry. |
| Entry time and quotes | `seconds_left ≥ 180`; bought-side implied ask in `[10¢, 90¢]`; ask minus own bid between 0 and `2¢`; best ask depth at least 10. Ask is `1 −` the opposite side's best bid, with that bid's count. |
| Cooldown | At least `60 s` after the last exit. |
| ENTER NO | 1m RSI 14 `≥ 70`; Bollinger `%B > 1`; 60 s taker-flow imbalance `≤ 0` **or** at least `0.20` lower than the previous 60 s. |
| ENTER YES | 1m RSI 14 `≤ 30`; `%B < 0`; imbalance `≥ 0` **or** at least `0.20` higher than the previous 60 s. |
| EXIT precedence | (1) Composite crosses back through the 1m Bollinger mid (NO from above, YES from below); (2) held-side best bid `≤ entry ask − 5¢`; (3) held for `≥ 300 s`; (4) `seconds_left ≤ 90`. |
| Missing executable exit bid | A missing, older-than-5-s, or shallower-than-10 bid cannot fill the full paper size. At a required exit, append `no_bid`, then use the market's public yes/no result after close; no settlement fee. If the window disappears at close, record the missed time-stop exit. |

The extra full-size/fresh-bid check at exit is conservative: the task forbids partial fills, and a stale or shallow top bid cannot substantiate a 10-contract paper sale. No inside-spread fill is claimed. The last two 1m closes, composite and its 60 s average, RSI, `%B`, Bollinger mid, current and previous flow imbalance, window time, and book age are captured in input records; the closes are logged, not used by the rules. The book observation timestamp is taken immediately after its public GET completes.

## Paper fees and scoring

The [Kalshi fee schedule, effective July 7, 2026](https://kalshi.com/docs/kalshi-fee-schedule.pdf) states the general taker formula `M × 0.07 × C × P × (1 − P)`, with default `M = 1`, and says to round **fee plus position cost** upward to a centicent (`$0.0001`). KXBTC15M does not appear in the schedule's nonstandard-series list, so applying the general formula is an inference from that public document. The implemented rate constant is `0.07`; the default rounding increment is `$0.0001`, and the sensitivity increment is `$0.01`. Entry and book-bid exits each pay a taker fee; a public-result settlement pays no exit fee. For 10 contracts, the modeled fee at 47¢ is `$0.1744` versus `$0.18` under cent rounding; at 55¢ it is `$0.1733` versus `$0.18`; at 50¢ it is `$0.1750` versus `$0.18`. These are paper fee models, not a representation of live account fills.

The task's account-fee precision note was treated as the requested model default. No credential or private account route was used in this run. No winning-probability forecast is produced. The panel's win rate is a realized frequency of prior paper trades and warns **too few trades to judge** while `n < 100`.

## Verification and deployment

- The final Mac and Acer suites each passed **145/145** tests. They cover both entry directions and flow-swing clauses, every entry eligibility filter and live cooldown, target/stop/maximum-hold/time-stop exits, target crossing and rule precedence, book ask/bid derivation and depth, missing-bid settlement, the result-between-ticks race, restart abort, both fee increments at several prices, statistics and intervals, the CLI report, private file modes, `/api/state.scalp`, and SSE tail retention. Python compilation, `node --check app.js`, and `bash -n acer/deploy.sh` passed. An initial sandboxed Mac run could not bind the existing HTTP test sockets; the complete suite passed with loopback access.
- `acer/deploy.sh scalp` copied only dashboard files, ran both suites, and restarted only `btc-ta-copilot.service`. The first restart was **2026-10-03 06:59:21 UTC**, T−39 s before 07:00. An independent settlement review found a race that could omit the `no_bid` row if the public result arrived between ticks; the code and regression test were corrected, then the final restart landed at **2026-10-03 07:14:20 UTC**, **T−40 s** before 07:15. Both fell within the required T−60…T−15 interval. The watcher timer's activation timestamp remained 2026-09-30 20:34:27 UTC; its code, service, and notification path were not changed. The existing Serve mapping remained tailnet only. Local and Acer SHA-256 hashes matched for `ta_copilot.py`, `stream_flow.py`, `scalp_signal.py`, `scalp_report.py`, `app.js`, `index.html`, `README.md`, and `test_scalp_signal.py` after the final deploy.
- `state/scalp_log.jsonl` existed on Acer with mode **0600**, under a **0700** directory. After the final restart, `scalp_report.py` reported 0 entries, 0 completed trades, and 0 aborted exits. No live scalp trigger was observed in either verification interval.
- Five `/api/state?tf=1m` samples at 07:00:07–11 UTC were ready, `stale=false`, `kalshi_stale=false`, had no scalp errors, and advanced `scalp.as_of` once per second. After the final restart, three more samples at **07:15:26–28** showed the new market, the same ready/fresh/no-error state, and successive scalp timestamps. Full and tail SSE events at **07:15:29–30** each carried the `scalp` object and successive timestamps.
- The deployed page rendered the new badge, `FLAT · watching`, condition checks, and the zero-trade stats line. At a 375 px viewport in dark mode, the settled layout had a 360 px document width and a 298 px scalp panel; computed foreground/background colors were `rgb(231,234,240)`/`rgb(14,17,22)`. A final desktop reload showed live data, 28 canvases, and an empty browser error log. Public GETs for `/`, `/app.js`, and `/api/state` returned the existing CSP, `nosniff`, and `no-referrer` headers; no CSP policy change was made. The UI assets had identical hashes across both deployments.

The preceding tally, checked on Acer before deployment, contained 223 opens and 222 settlements. Its 1m/5m/10m/15m/1h opening-lean scored rates were 51.2%/48.5%/47.8%/48.4%/44.1%; every Wilson interval included 50%. This is context, not validation of the new scalp rule.

## Deviations and limits

- A bounded Reasonix Flash writer hit its tool-call limit after drafting the engine and unfinished CLI; its result was a failure, with estimated provider cost `$0.035812038`. Codex fixed the entry-flow gate, exit crossing and execution checks, journal behavior, CLI, integration, and tests, then independently verified them. The Reasonix worker read a fee-evidence file under `../btc-readiness` despite its explicit out-of-scope instruction. The worker reported no study edits, and Codex did not modify the study; no private values from that read were used as verification here. No student-account handoff was useful for the timed autonomous deployment.
- Subsequent source review after the first deploy found the settlement logging race; it was corrected, and the final deploy/test pass is the result to use.
- The browser screenshot call timed out. Mobile and dark-mode findings above came from the live DOM, layout measurements, computed styles, and visible accessibility text. Console capture showed no errors; no induced CSP violation or market-data outage was tested.
- No paper entry or exit occurred during the live check, so live scoring and settlement remain unobserved. Offline tests exercised those paths. The strategy remains unvalidated, and the Study 005 advice gate remains disabled.
