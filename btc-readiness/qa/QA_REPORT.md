# BTC copilot production-readiness QA — September 27, 2026

## Executive summary

**QA BLOCKED.** Neither the original copilot nor study 002 release 4.2 can be certified as a trustworthy trading adviser. Their public abstention gate worked in the tested scope: public decisions stayed **NO TRADE**, position guidance stayed unavailable, and quantity previews never became eligible orders. This is a deterministic **Kalshi KXBTC15M binary-contract research monitor**, not a BTC perpetuals risk engine. UP/DOWN are contract outcomes; they are not leveraged LONG/SHORT positions.

Study 002 fixes important original-study evidence defects, including early delayed-entry quotes, overwritten originating evidence, unfair outcome polling, and exclusion of unresolved exposure. Shared defects remain in history coverage, depth normalization, trade validation, confirmation state, replay verification, numerical helpers, and dashboard timestamp validation. An invalid price grid can hang analysis. The original accepts future-dated market inputs and duplicates journal finalization after a crash. Public abstention contains recommendation risk but does not establish research-data correctness or statistical validity.

No production fixes were made. The audit created isolated source copies, synthetic fixtures, tests and this report in the current Investing workspace. Production sources were hash-checked after all tests and remained unchanged. The approved study 002 manifest seal remained intact; its runtime directory remained absent. No orders, account mutations, service changes, collector starts/stops, credential changes, approval changes, database changes, or scheduled-job changes were performed by this audit.

## Scope, evidence and boundaries

Source workspace: `/Users/you/Documents/Codex/2026-09-26/new-chat`.

- Original: `outputs/btc_copilot.py`, research/evidence modules, REST helper, settings, protocol, dashboard, health helper, and existing tests.
- Study 002: `outputs/copilot_study002_fee_review`, including policy, pacing, fee reconciliation, sealed manifest, and synthetic tests.
- Lifecycle: `outputs/study002_supervision/supervise.py`, test suite, runbook, and automation metadata.
- Older candidate v4 and paced policy were copied only where existing regression tests require comparisons. They are not deployment targets.
- Protected databases, holdout outcomes, study reports, original journals and collector log contents were not inspected. Production state inspection was limited to snapshot health, runtime metadata, file permissions/sizes, lock status, approval scope, seal, and scheduler metadata. No real account positions or fills were fetched for this audit.

At the observed health check, the original snapshot was fresh, NO TRADE, shadow-only, with position guidance disabled and no source-quality faults. Its singleton lock was held; runtime metadata specified a 15-second interval and position reads enabled. The original supervision automation was PAUSED. Study 002 supervision was ACTIVE, every 15 minutes, with evidence-only instructions and an October 1 UTC collection start. Study 002 had an approval record despite its README claiming none existed; no runtime existed. These are point-in-time observations, not an uptime certification. OS process enumeration was denied by the sandbox, so exact live PID command identity was not independently verified.

Reasonix Orchestrator was probed and given one bounded, source-only read contract. It timed out without findings. No worker claims contributed to the conclusions. Student ChatGPT has no configured repository-access integration; no account messages were sent. All final verification was local.

Current official documentation confirms the implemented Kalshi production REST origin and authenticated BRTI passthrough, including benchmark request cost 50 versus default read cost 10. The binary order-book complement interpretation is documented: YES ask is $1 minus NO bid, retaining displayed quantity. Sources: [Kalshi BRTI passthrough](https://docs.kalshi.com/cfbenchmarks/rest-passthrough), [order-book schema](https://docs.kalshi.com/getting_started/orderbook_responses), [fee rounding](https://docs.kalshi.com/getting_started/fee_rounding), [CF Benchmarks values schema](https://docs.cfbenchmarks.com/api/rest/values/). This documentation check did not renew production fee/rate attestations or verify current account entitlements.

## Test results

Final reproducible command, from this audit directory:

```sh
python3 -B run_all.py
```

`run_all.py` records individual exit codes. Its own successful exit means the runner completed; **it does not mean QA passed**. The two adversarial suites and actual-dashboard suite deliberately retain nonzero exit codes for unfixed defects.

| Suite | Test methods/checks | Passed | Failed | Skipped | Exit |
|---|---:|---:|---:|---:|---:|
| Original existing Python tests | 34 | 34 | 0 | 0 | 0 |
| Existing original/candidate audit regressions | 10 | 10 | 0 | 0 | 0 |
| Study 002 existing tests plus existing lifecycle fixtures | 51 | 51 | 0 | 0 | 0 |
| Supervision synthetic tests | 34 | 34 | 0 | 0 | 0 |
| New adversarial tests, original | 40 | 24 | 16 | 0 | 1 |
| New adversarial tests, study 002 | 40 | 26 | 13 | 1 | 1 |
| **Python total** | **209** | **179** | **29** | **1** | — |
| New checks against actual embedded HTML script, both versions | 12 | 8 | 4 | 0 | 1 |

The existing standalone UI script also passed. It is reported separately because its multiple assertions are not Python test methods. There were no unresolved test-import/runtime errors in the final runs. Initial isolated-fixture layout errors and a test that incorrectly expected a stale-data return instead of a safe rejection were corrected in the audit harness; they were not production defects. Subtest failures are counted once per test method in the totals.

**Added:** 40 reusable adversarial Python test methods, run against both targets; a six-case actual-HTML JavaScript harness, run against both targets; 14 reproducible synthetic golden scenarios, run against both targets. No tests were installed in sealed or production source trees. Existing regression tests deliberately reproduce bugs; a green reproduction test is evidence that the bug remains, not evidence that it was fixed.

Artifacts: [test results](test_results.json), [new Python harness](adversarial_audit.py), [actual dashboard harness](audit_ui.cjs), [original failures](original_adversarial.json), [study 002 failures](study002_adversarial.json), [source fingerprints](source_hashes.json).

## Architecture and dependency audit

The actual data path is:

`Kalshi discovery/details/rules + BRTI + binary book/trades/candles + Coinbase ticker + exchange status → Decimal parsing/tick sorting/identity and freshness checks → completed BRTI minute bars, pivots, returns, volatility, velocity → hour-trend and countertrend-failure setup → settlement-average diffusion probabilities → quote/fee/edge/confirmation/depth/flow gates → shadow candidates → unconditional public NO TRADE gate → previews, state, audit SQLite, study SQLite, snapshots and dashboard`.

| Component | Actual implementation and dependency |
|---|---|
| Authoritative BTC settlement input | Authenticated GET `/cfbenchmarks/values?id=BRTI`; no fallback substitution when unavailable. Historical BRTI routes exist in helper but live collector uses latest one-hour payload. |
| Spot cross-check | Coinbase Exchange GET BTC-USD ticker; price/time used, bid/ask fields not used as Kalshi execution quotes. Coinbase failure is a required-data failure. |
| Kalshi contract inputs | Discovery `/markets`; exact market, event and series; rules, floor strike, binary $1 notional, active 900-second contract, opening-average reconciliation. |
| Liquidity and activity | Full displayed binary bid ladders; `/markets/trades` bounded to 1000 rows over requested 180 seconds; contract one-minute candles. Truncated flow is disclosed but not wholly vetoed. |
| Exchange availability | `/exchange/status`; global and optional matching shard checks. |
| Account reads | Original optional ticker-filtered positions and bounded fills; inventory ledger. Study 002 CLI prohibits positions. Shared helper permits enumerated portfolio GET reads. |
| Features | One-minute BRTI OHLC, not volume; point returns over 1/3/5/15/60 minutes; two-bar-confirmed pivots; sigma, hour z-score, wicks, velocity/acceleration; sampled taker flow and book imbalance. No RSI/MACD/EMA implementation. |
| Interpretation | Deterministic heuristics and Gaussian diffusion, including drift-free and filter-ablation shadow arms. No external LLM/model API or prompt-based decision engine in either audited source tree. |
| Fees/risk | Decimal quote complements, conservative per-contract/per-level fee ceilings, depth walk, price ceiling and profit-zone math; separate study 002 precision/fragmentation scenarios. No collateral/leverage/stop-loss/exposure-budget engine. |
| Configuration | JSON settings loaded each observation; safety floors; original fee attestation expires after 24 hours. Study 002 sealed source/protocol plus approval attestations and external preflight receipts. |
| State/cache | JSON prior/contract state; latest JSON/text; raw public snapshot; append-only observations/journal; SQLite audit with seven-day replay retention; durable frozen study databases. HTTP caching explicitly disabled. Saved outputs are snapshots, not market-data fallbacks. |
| Scheduling | Collector loop every 15 seconds; browser GET every three seconds and freshness every second; separate nominal 15-minute lifecycle heartbeat. Shared study 002 process-local pacer, weighted token budgets, 429 cooldown; original bounded HTTP retries. |
| Publication | Loopback HTTP server with exactly three paths, Host and Origin checks; HTML uses textContent rather than untrusted innerHTML. |
| Secrets | Separate config/signing-key path, RSA/Ed25519 signing, fixed HTTPS origin, no redirects carrying auth. Config and sampled private output files were mode 0600. Key contents and permissions at its referenced path were not inspected. |

Duplicated or divergent truth surfaces matter: public decisions and shadow decisions differ intentionally; replay currently verifies the public decision but not the actionable research decision. Compact prior state lacks quality metadata. Original journal finalization and state commit are separate. Legacy scorecard and frozen study must not be merged. Study 002 preview fees and fragmentation acceptance fees are different conservative scenarios and must not be presented as a single exact account fee quote. The sealed study 002 behavior identity includes policy, pacing and fee modules; the original includes its four principal modules.

## Findings

Paths below are relative to the source workspace. For shared findings, `outputs/btc_copilot.py` means both the original and its study 002 counterpart. **Fix made: none** for every production finding. Recommendations require a future reviewed version; sealed studies must not be patched or relabeled in place.

### F01 — P0: early hypothetical execution quote leaks across decision-time boundary — original

**Component:** `outputs/btc_copilot_evidence.py:123`. **Failure/root cause:** the delayed-entry gate accepts a request started up to one second before the 15-second reaction deadline, then consumes it as the first eligible quote. Later eligibility and data are also substituted into the updated evidence. **Reproduction:** existing `test_frozen_early_fill_and_origin_overwrite_reproduced`: signal at t; next snapshot t+15 with book request started t+14. **Expected:** pending until a request starts at/after t+15; preserve original decision information. **Actual:** `hypothetical_fill` at the premature observation. This is decision-time leakage in the historical simulation, not a real fill. **Recommended:** exact request-start deadline, first eligible observation only, separate origin/execution evidence. **Regression:** reproduced; study 002 exact-delay and first-eligible-request tests pass and its implementation fixes this boundary.

### F02 — P1: future BTC ticks and trades are admitted — original

**Component:** `outputs/btc_copilot.py:342,390`. **Failure/root cause:** source-age tolerance permits timestamps up to two seconds ahead; trade timestamps have no future bound. **Reproduction:** new continuous ticks through NOW+0.5 seconds, avoiding a gap veto; separately, a trade dated NOW+60 seconds. **Expected:** no future input in a decision or historical replay; abstain/quarantine unless an independently verified clock policy explicitly resolves the discrepancy. **Actual:** normal model/flow output with no data-quality fault. **Recommended:** validate every input against the decision cutoff and reject future observations. **Regression:** both original safety tests fail; both pass in study 002. No future leakage was found in the closed-pivot window itself.

### F03 — P1: incomplete outcomes can produce flattering evaluation eligibility — original

**Component:** `outputs/btc_copilot_evidence.py:165,217`. **Failure/root cause:** strategy rows are filtered through settled market tickers, dropping unresolved filled exposure; forecast coverage allows 5% missing and acceptance does not require all enrolled outcomes resolved. **Reproduction:** existing synthetic frozen regression uses 112 resolved wins and 84 unresolved fills. **Expected:** retain all 196 attempts and unresolved worst-case exposure; block review eligibility. **Actual:** reports 112 attempts and `eligible_for_independent_review=True` under the constructed sample. It still does not automatically enable advice. **Recommended:** complete calendar/checkpoint/outcome requirements and explicit pending exposure. **Regression:** original behavior reproduced; candidate and study 002 incomplete/empty-study checks reject promotion. No real holdout outcomes were opened.

### F04 — P1: unresolved early markets starve later settlement reconciliation — original

**Component:** `outputs/btc_copilot_evidence.py:139`. **Failure/root cause:** every poll selects the oldest three unresolved markets, without fair retry scheduling. **Reproduction:** existing `test_frozen_starves_fourth_market` with three persistently pending markets plus a finalizable fourth. **Expected:** later markets are eventually polled despite older delays. **Actual:** fourth market is never visited. Collection faults also skip settlement because original `main` calls it only on the success path. **Recommended:** fair persistent attempt scheduling and independent/finally reconciliation. **Regression:** frozen failure reproduced; study 002 65-market backlog test and independent background reconciliation tests pass.

### F05 — P2: origin evidence overwritten — original

**Component:** `outputs/btc_copilot_evidence.py:133`. **Failure/root cause:** delayed-fill update replaces `evidence` containing the initiating candidate with later candidate/quote data. **Reproduction:** initial probability .9, later .8 in existing frozen regression. **Expected:** retain .9 plus original snapshot/settings/raw context and separately store execution evidence. **Actual:** origin becomes .8 and lacks a full initiating snapshot. **Recommended:** immutable origin plus execution evidence column. **Regression:** reproduced; study 002 retains .9 origin and .8 execution evidence in its passing test.

### F06 — P1: incomplete history masquerades as a one-hour trend — both

**Component:** original `outputs/btc_copilot.py:85,99,113`; study 002 counterpart `:86,100,114`. **Failure/root cause:** 3000 ticks and 50 minute bars are treated as sufficient, and nearest_price substitutes the oldest available sample without enforcing requested lookback coverage. **Reproduction:** truncate otherwise healthy input to its last 3100 seconds. **Expected:** one-hour feature unavailable and all dependent arms vetoed until coverage is proven. **Actual:** approximately 51.65-minute return is labeled `1h`; no data-quality fault. **Recommended:** explicit coverage, cadence and missing-observation requirements per feature; never silently shorten the timeframe. **Regression:** `test_hour_coverage_must_be_proven` fails in both.

### F07 — P1: unhealthy prior snapshot confirms a later shadow setup — both

**Component:** original `outputs/btc_copilot.py:402,444,574`; study 002 counterpart `:407`; `btc_copilot_evidence.py:candidates`. **Failure/root cause:** prior state carries setup/direction/time but not freshness, operational vetoes or quality validity; confirmation tests only same ticker, time interval and side. **Reproduction:** otherwise eligible controlled setup with prior `data_quality_faults=['stale']` and `guidance_valid=False`. **Expected:** invalid prior observation cannot confirm; require two healthy causal observations and documented completed confirmation. **Actual:** shadow decision UP. Public decision remains NO TRADE. **Recommended:** persist validity/source times and confirmation provenance, clear confirmation on faults. **Regression:** `test_bad_previous_snapshot_cannot_confirm` fails in both. This gate test deliberately controls structure/model to isolate confirmation; real-detector golden replays are separate.

### F08 — P1: duplicate book levels inflate executable depth — both

**Component:** `outputs/kalshi_readonly.py:summarize_book`, `btc_copilot_research.py:35`, collector book validation. **Failure/root cause:** repeated price/quantity rows are summed without schema uniqueness validation. **Reproduction:** repeat the same `.6` quantity at the same price on each side. **Expected:** reject duplicate aggregated levels or canonicalize without creating new depth. **Actual:** reports 1.2 contracts, crossing the one-contract admission boundary. **Recommended:** reject duplicate prices in aggregated REST books and preserve source identity. **Regression:** `test_duplicate_book_levels_rejected_or_deduplicated` fails in both.

### F09 — P1: malformed/duplicate trades distort directional flow — both

**Component:** original `outputs/btc_copilot.py:391–397`; study 002 `:396–402`. **Failure/root cause:** counts are parsed but not checked for positivity/finiteness, and trade IDs are not deduplicated. **Reproduction:** YES taker count `-100`; separately duplicate a single trade ID/count 10. **Expected:** invalid counts fail closed; duplicate volume remains 10 or input is rejected. **Actual:** no quality fault for negative quantity; duplicate volume becomes 20. Negative quantities can reverse flow comparison. **Recommended:** typed schema, finite positive quantities, ticker/time-window checks and ID deduplication before aggregation. **Regression:** negative-count and duplicate-trade tests fail in both. Future trades are separately fixed in study 002 (F02).

### F10 — P2: malformed/future auxiliary candles are published unvalidated — both

**Component:** original `outputs/btc_copilot.py:521`; study 002 counterpart snapshot field `kalshi_recent_candlesticks`. **Failure/root cause:** candle response is a required source but the last five rows are copied without OHLC, quantity, closure, timeframe or cutoff validation. **Reproduction:** future end timestamp, high below low, NaN close and negative volume. **Expected:** reject/quarantine and disclose unavailable auxiliary candle data. **Actual:** stored snapshot contains these rows with no quality fault. **Recommended:** validate OHLCV/timeframe/ordering/closure; explicitly mark forming candles. **Regression:** future-malformed-candle test fails in both. These contract candles do not feed the BRTI structure/model in the current implementation; this test does not prove trading-model look-ahead from them.

### F11 — P2: upstream error field ignored when payload exists — both

**Component:** `btc_copilot.py:ticks_from_response`. **Failure/root cause:** reads only nested payload, not upstream error status. **Reproduction:** add `error='upstream degraded'` alongside otherwise valid ticks. **Expected:** unavailable/degraded data, not a normal healthy observation. **Actual:** accepted without quality fault. **Recommended:** validate envelope/error/schema before ticks. **Regression:** upstream-error test fails in both. Current Kalshi documentation maps many upstream errors to non-200 responses; this is a defense gap against an inconsistent/partial successful response, not a claim that the live endpoint presently emits this case.

### F12 — P2: numerical preview helper accepts invalid quantities and fees — both

**Component:** `outputs/btc_copilot_research.py:35–47`. **Failure/root cause:** `walk` validates levels but not its requested quantity or multiplier. **Reproduction:** quantity 0, -1, null; multiplier -1. **Expected:** reject all invalid inputs. **Actual:** zero is complete; negative quantity produces negative notional/fee; negative multiplier gives a negative fee. Null is rejected. **Recommended:** finite positive quantity and finite nonnegative verified multiplier at function boundary. **Regression:** quantity and negative-multiplier safety tests fail in both. Settings and collector separately validate normal live quantities/multiplier, so this is a latent helper/API defect, not a reproduced live negative-risk recommendation.

### F13 — P1: invalid price grid hangs analysis — both

**Component:** original `outputs/btc_copilot.py:212–222`; study 002 `:213–223`. **Failure/root cause:** iteration is capped by number of accepted prices, not loop iterations; start/end/step are not all validated finite. **Reproduction:** start `-Infinity`, end 1, step .01 in a subprocess killed after .7 seconds. **Expected:** immediate sanitized input rejection. **Actual:** nonterminating grid loop. **Recommended:** finite bounded bands, positive finite step, iteration cap independent of accepted prices, reject overflow/truncation. **Regression:** bounded-grid test fails in both. Dashboard expiry contains advice risk but does not recover collector liveness.

### F14 — P2: replay reports success despite forged shadow decision — both

**Component:** `outputs/btc_copilot_research.py:182`. **Failure/root cause:** compares only public decision, central P(UP), and blockers. Public decision is unconditionally NO TRADE; shadow decisions, candidate eligibility, side economics, fills and features are outside the equality check. **Reproduction:** archive a real deterministic observation but change `shadow_decision` to `FORGED`; offline replay returns `matches=True`. **Expected:** detect changed decision-relevant research outputs, or clearly label a narrow probability/blocker check without asserting complete decision reproducibility. **Actual:** match despite forged research decision. **Recommended:** versioned complete replay comparator with canonical fields and explicit scope. **Regression:** forged-shadow replay test fails in both. Raw inputs and sources are archived, so expanded replay is feasible.

### F15 — P2: dashboard freshness does not validate decision timestamp — both

**Component:** `outputs/btc_copilot.html:59` and study 002 same line. **Failure/root cause:** expiry is checked finite, epoch is not; comparisons against undefined/NaN evaluate false. **Reproduction:** actual embedded `freshness()` with future-valid expiry, decision UP and missing epoch; repeat with NaN epoch. **Expected:** NO TRADE and unavailable guidance. **Actual:** UP displayed in all four target/case combinations. **Recommended:** validate finite numeric epoch/expiry, their ordering/max lifetime and decision schema, with monotonic reception timeout. **Regression:** four actual-HTML checks fail. Normal producer outputs remained shadow-only; this is the browser's independent malformed-snapshot boundary, not an observed live UP.

### F16 — P2: journal finalization duplicates after crash — original

**Component:** `outputs/btc_copilot.py:605,608`. **Failure/root cause:** append journal then set in-memory journaled flag and commit separate JSON state. **Reproduction:** inject failure in atomic state write after successful finalization append; reload saved state and retry twice in synthetic files. **Expected:** one idempotent finalization record keyed by contract/result revision. **Actual:** two journal rows. **Recommended:** transactional journal/outbox or durable idempotency key; recover committed append before replaying. **Regression:** new crash-retry test fails original; skipped study 002 because it removes this journal path and uses outcome events instead. No production process was crashed.

### F17 — P2: fixed temporary path can follow a symlink and truncate another file — both

**Component:** original `outputs/btc_copilot.py:45–52`; study 002 `:46–53`. **Failure/root cause:** `os.open` on predictable `.tmp` path follows existing links with O_TRUNC. **Reproduction:** within an isolated temporary directory, link state.json.tmp to an unrelated sentinel, then call atomic. **Expected:** reject link; sentinel unchanged. **Actual:** sentinel overwritten. **Recommended:** unique owner-only temporary file, no-follow/exclusive open, fsync and atomic replace; inspect destination link semantics. **Regression:** symlink safety test fails in both. Study 002 launch guard rejects preexisting linked runtime files; this narrows live reachability, but the helper itself remains unsafe and guard-to-open races remain. No production sentinel or file was altered.

### F18 — P3: release documentation contradicts current approval state — study 002

**Component:** `outputs/copilot_study002_fee_review/README.md:1–5`. **Failure/root cause:** says NOT ACTIVATED and no approval exists, despite a current evidence-only approval file and active supervision. **Reproduction:** read README and sanitized approval existence/scope. **Expected:** distinguish approved-for-future-evidence collection from not-yet-running and not-authorized advice. **Actual:** stale release-state prose. **Recommended:** update external status documentation without changing sealed behavior or suggesting early launch. **Regression:** source/state comparison; proposed documentation-status test, not yet added to sealed bundle.

### F19 — P2: profit target uses entry-price fee as the exit fee — both

**Component:** `outputs/btc_copilot.py`, `exit_fee=taker_fee(entry['ask'],mult)` followed by the profit-zone grid expression; study 002 retains it. **Failure/root cause:** exit fee is evaluated at the entry ask rather than the proposed target bid. **Reproduction:** independently execute the exact target expression with entry .15, multiplier 1, cent grid and .03 profit reserve. Entry fee is .01; target becomes .20; actual conservative exit fee at .20 is .02. **Expected:** target nets at least .03 after both fees, here at least .21. **Actual:** .20 target nets .02. **Recommended:** test each candidate grid target using its own exit fee and any explicitly required exit slippage. **Regression:** `test_profit_target_recomputes_exit_fee_at_exit_price` fails in both. Public profit-zone output is currently cleared by the shadow gate; the held-position branch separately uses a conservative maximum exit fee and is not the reproduced faulty branch. This is a disabled-advice correctness defect, not an observed live take-profit instruction.

### F20 — P1: incomplete paginated trade window can support a setup — both (remediation follow-up)

**Component:** `btc_copilot.py`, `trade_flow['truncated']` and flow/setup gating. **Failure/root cause:** an unread cursor is recorded only as a display warning; the operational gates do not require a complete trade window. **Reproduction:** use the controlled eligible UP fixture and set `raw['trades']['data']['cursor']='UNREAD'`. **Expected:** incomplete evidence must veto every shadow strategy and prevent confirmation. **Actual:** both original and study 002 return shadow UP with no data-quality faults; seven shadow arms remain eligible. Public output is still NO TRADE. **Fix made:** isolated candidate adds the cursor warning to data-quality faults and common vetoes before setup selection. Production and sealed sources remain unchanged. **Regression:** `test_partial_trade_window_every_arm_abstains` passes in the candidate; [baseline reproduction](partial_window_baseline.json) retains original and study 002 failures. The public 1,000-trade fixture is itself paginated, so it now correctly represents degraded data rather than a healthy confirmation sample.

## Trading-logic validation

| Required behavior | Verdict and evidence |
|---|---|
| Data freshness | **Partial; blocked.** Stale BTC, missing data and spot divergence abstain/reject; future checks fixed in study 002. Incomplete lookback and inconsistent auxiliary inputs remain accepted. Book freshness is retrieval time, not independently proven venue timestamp. |
| Candle closure | **Verified for BRTI minute bars:** 91 ticks generate only the first completed minute. Latest forming minute excluded. Bars tolerate at least 55 observations, so closure is not equivalent to complete data. Auxiliary Kalshi candle closure is unverified/unvalidated. |
| Timeframe alignment | **Partial.** 1/3/5/15/60-minute point lookbacks share timestamp-sorted BRTI input; coverage can shorten a nominal hour. They are not separately aggregated candle timeframes. 4h is absent. DST UTC/Pacific round-trip tests pass. |
| Look-ahead bias | **Blocked original.** F01/F02 reproduced. Pivot highs/lows require two later completed bars and are evaluated only at current observation, a confirmation lag rather than demonstrated pivot leakage. Study 002 cutoff/delay/clock tests pass; no real holdout was examined. |
| LONG / SHORT | **UP/DOWN shadow logic exercised, not certified advice.** Both controlled gate tests and real-detector second-observation LONG/SHORT analogues qualify with favorable binary books. Public decisions stay NO TRADE. No leveraged LONG/SHORT engine exists. |
| NO TRADE | **Verified as first-class public result.** Unconditional shadow gate, first observation, missing/stale data, conflict, chop and adverse edge tests. Experimental baseline arms intentionally omit technical filters. |
| Stop math | **Not implemented as financial risk sizing.** Structural BRTI invalidation descriptions and completed-close checks are not executable BTC stop orders, guaranteed exit prices, or maximum-loss caps. |
| Target math | **Blocked for the disabled entry target branch.** Independent fee/grid recomputation found a 20c target nets 2c instead of its planned 3c reserve (F19); public zones remain disabled. F13 blocks malformed grids. Held-position target uses a separate conservative maximum-fee branch. |
| Leverage/collateral/liquidation | **Not implemented.** $100 × 5 = $500 is the independent arithmetic, but no audited function accepts or validates that scenario. Binary $1 contracts have a different payoff/cost model. Do not use this copilot for that calculation. |
| Position sizing / exposure limits | **Not implemented as personalized risk management.** Depth previews for configured quantities calculate binary purchase costs; no risk budget, maximum account loss, total exposure, stop-distance sizing, liquidation or remaining collateral computation. |
| Averaging / scale-in | **Partial.** YES/NO inventory, weighted average entry, reduction and reversal math pass. ADD requires existing observed holdings, newer completed bar, structural condition and improving edge, then is suppressed publicly. No budget-based post-add leverage/max-loss/collateral calculation exists; safe ADD advice is not certified. |
| State isolation | **Partial.** Identical market input produces identical model/structure regardless of prior position side; five repeated controlled inputs are deterministic. Prior setup intentionally changes confirmation, and bad prior validity is not respected (F07). Corrupt JSON raises before main error-publication loop; stale UI then expires, but startup availability reporting is incomplete. |

No leverage, exposure, liquidation, partial take-profit, or leveraged stop calculation was silently substituted with a binary contract quantity preview. These are scope limitations that independently block certification for the full leveraged-BTC use case requested, rather than requests to add features during this audit.

## Golden-scenario replay and regime reasoning

The corpus is generated in `adversarial_audit.py:golden`; expectations are recorded before each run. Both targets ran all 14 scenarios. Twelve healthy scenarios also received a later observation with real structure/model computation and previous state; prices/books were synthetic and no live simulation endpoint was used. Source data came from deterministic trajectories, not model-invented market facts.

| Scenario | Bias / full-strategy result after later observation, both versions |
|---|---|
| Obvious LONG | UP; shadow UP qualifies |
| Obvious SHORT | DOWN; shadow DOWN qualifies |
| Clean breakout | UP; full strategy abstains, lacks its required countertrend setup/confirmation |
| Failed breakout | Neutral; full strategy abstains |
| Successful retest | UP; shadow UP qualifies |
| Failed reclaim | DOWN; shadow DOWN qualifies |
| Strong downtrend | DOWN; full strategy abstains without bounce-failure confirmation |
| Strong uptrend | UP; shadow UP qualifies on generated pullback structure |
| Sideways chop | Neutral; full strategy abstains |
| Conflicting timeframes | Hour DOWN / short-term rebound; full strategy abstains |
| Stale data | Original returns faulted NO TRADE with all arms vetoed; study 002 safely rejects expired observation |
| Missing data | Both safely reject |
| Extreme volatility | Neutral; full strategy abstains |
| Almost qualifies | Neutral; full strategy abstains |

**Public safety:** all returned public recommendations were NO TRADE, and missing/stale rejected cases failed closed. **Strict corpus expectations:** 12/14 per target, 24/28 total. The two misses per target were the initial expectation that *every* experimental shadow arm should reject chop and the almost-qualifying fixture. Drift-free/filter-ablation arms can remain eligible by explicit design even when the full structure strategy abstains. These discrepancies are retained in the JSON rather than rewritten after execution; they are not classified as enabled-advice failures. Manually replayed live trading scenarios: **0**; automated synthetic target/scenario runs: **28**. See [original corpus](original_golden.json) and [study 002 corpus](study002_golden.json).

Unsupported categories must not be represented as validated detectors: generic clean-breakout/momentum/reversal/range labels are not a complete setup classifier. The main supported setup is hour trend plus countertrend failure; higher/lower pivots are features. No explicit 4h conflict model exists. Sigma floor .10, trend z .65, minimum-move .3, chop .25, divergence 5 bps, spread .04, imbalance .35, and regime sigma threshold 2 are hard-coded. Their profitability/robustness across liquidation wicks, news gaps and volatility regimes is unproven. The generated high-volatility case abstained, but there is no dedicated news/wick anomaly gate. More regime perturbations are proposed, not claimed completed.

## Failure safety, state, concurrency and auditability

Tests cover empty/missing data, duplicates, malformed levels, stale timestamps, future inputs, cross-source divergence, timeout, malformed JSON, 429/401 collection errors, missing credential configuration, invalid settings, corrupted state, safe error sanitization, singleton locking, simultaneous pacing, clock reversal/sleep/reboot continuity, duplicate study observations, slot collision, restart continuity, and crash-after-journal-append. All injected account/network failures used fakes; no invalid credential was sent to a real exchange.

Collection faults occur before record and main publishes NO TRADE/error snapshots when the failure path can write them. Missing executable book sides remain unavailable, not invented zero quotes. Study 002 shared pacing is locked, starts with zero tokens, rejects invalid costs/deadlines, imposes cooldown on 429, and does not automatically retry into a burst. Singleton collector locks and supervisor mutex were tested; database transactions, unique signal keys and unique scheduled slots provide further containment. The audit did not intentionally overlap production monitors or kill them. Atomic JSON replace prevents readers seeing a normally half-written JSON file, but does not provide a transaction spanning observations/state/audit/study/public snapshots. F16/F17 remain important limits.

Recommendation archives capture timestamps, source data, features, setup, blockers, per-side quote economics, models/settings versions, prior state, and public/shadow results. Original source fingerprints and study 002 expanded fingerprints support version matching. Seven-day audit replay retention is deliberate; frozen checkpoints persist separately. Stop dollars/leverage/risk-budget fields are absent because those engines are absent. Account guidance replay is explicitly excluded. JSONL observations/journals and database growth are not covered by a complete disk-capacity/retention alarm; disk-full/power-loss recovery and fsync durability need additional isolated tests. Current replay equality is insufficient (F14). Degraded-state explanation exists, but all-source recovery, crash between each persistence layer and alert deduplication across restart are not comprehensively certified.

Browser alert deduplication uses sessionStorage for ticker/direction, clears expired banners and requires an open dashboard. No external alert webhook exists. Server Host/Origin and traversal/private-file denial tests pass. Existing UI disconnect/future/expiry checks pass; timestamp schema failures remain (F15). This was headless script testing, not a current visual/browser-session inspection.

## LLM reasoning and security review

No LLM reasoning is used at runtime in the audited implementation. Structure, probabilities, candidate sides, blockers and state transitions are deterministic; repeated-input checks passed. There are no model prompts, model-generated prices, causal news explanations, or LLM-to-order conversion paths to test. The UI distinguishes model estimates, market-implied prices and experimental structural levels; constant low confidence and unvalidated status are appropriate. This does not prove forecast calibration or absence of heuristic bias.

The client constructs only enumerated GET requests to the fixed Kalshi HTTPS origin; Coinbase ticker is separately fixed HTTPS. Mutation/traversal/arbitrary URL routes are rejected before network dispatch. Redirects cannot forward signed headers. Signing material is kept local and raw credentials/auth headers are omitted from archives. Mode 0600 was confirmed for config, state, sampled snapshots and databases. External strings are rendered with textContent. No unsafe pickle/deserialization, external-text shell execution, or arbitrary model tool execution was found in these runtime sources. SQLite statements use bound values.

The shared helper has an explicit local `configure` command and arbitrary user-chosen output path; the supervisor has guarded collector launch/pause/resume commands and SIGTERM to a verified PID. These are maintenance capabilities, not exchange order submission. Their mutations were not invoked on production. The supervisor uses fixed subprocess argv rather than shell=True. F17 remains a local file-write weakness. The workspace is not a Git checkout, so credential-commit history cannot be verified here. Server-side API-key permission scopes and signing-key file permissions were not verified; GET-only application behavior is not proof of read-only venue credentials.

## Safety validation

**No live order path was triggered or modified.** No create/cancel/amend/transfer/leverage-change API function or execution webhook was found in the audited runtime modules. Kalshi authentication for GET reads exists; broker/exchange write paths do not. No credentials were loaded into the test copies. Collector process tests ran only unapproved isolated copies and refused before runtime/network, while source and pacing tests mocked dispatch. Synthetic hypothetical fills exist only in temporary research databases and were never simulated as live orders.

Study 002's approved seal was verified, future window and refusal safeguards exercised with synthetic approvals, and no runtime was created at the production bundle. Public NO TRADE and GUIDANCE UNAVAILABLE were not weakened. Source hashes before/after matched. Existing live collection may continue its independently authorized writes; that is not an audit modification and was not stopped.

## Remaining uncertainties and prioritized next validation

1. No profitability, calibrated probability, final holdout performance, or prospective edge conclusion is possible before the authorized release/evaluation. Raw holdout evidence stayed unread.
2. Real exchange outages, permission scope, key revocation, clock synchronization, venue book staleness, end-to-end latency under account limits, and OS uptime were not newly measured through authenticated live tests.
3. Current log contents and complete persistent market/account state were intentionally excluded to preserve frozen evidence; source review and synthetic tests do not certify the integrity of every historical production row.
4. No broad Git history/dependency vulnerability scan was possible for this non-Git workspace. Python/stdlib/cryptography and browser runtime dependencies were identified, not comprehensively supply-chain audited.
5. Proposed tests remain for finite-price upper bounds, full grid permutations, near-zero/huge inputs across all public functions, untrusted source envelope identity, all timeframe conflicts, dedicated wick/news anomalies, stop/target accounting if such an engine is separately approved, and position/account cross-source reconciliation.
6. Additional persistence fault tests should cover each commit boundary, simultaneous direct Study writers outside singleton orchestration, disk exhaustion, power loss, log retention, browser/session restarts and duplicate alerts. No claim of full concurrency or crash-recovery proof is made.
7. Helper symlink exploitability is restricted by filesystem permissions and study 002 guards; the isolated helper defect is proven, a production exploit is not.
8. The frozen sources must remain sealed. A fix requires a separately reviewed future version/study and must not silently alter ongoing evidence identity, dates or acceptance rules. The audit is not authorization to launch it or enable advice.

## Final QA status

**QA BLOCKED**

One or more material correctness, evidence-integrity and failure-boundary defects prevent trust for the intended trading-advice use. Existing passing tests and successful public abstention do not remove those blockers. Study 002 is safer than the original in several tested boundaries, but remains unvalidated evidence-only research and still has shared defects. Preserve abstention while reviewing a future corrected version.
