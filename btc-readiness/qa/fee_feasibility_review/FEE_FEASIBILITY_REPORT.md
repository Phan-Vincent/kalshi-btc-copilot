# BTC copilot fee and execution feasibility audit — September 27, 2026

## Executive summary

**QA BLOCKED. No fee-model correction or narrowing is justified yet.** Reasonix completed a bounded follow-up acceptance review of the conditional arithmetic. Codex independently retrieved official documentation, recomputed the examples and ran 16 new isolated deterministic methods. All passed. The full guarded candidate/package suites were rerun: 148 existing Python methods passed, one skipped; 24 HTML checks and 14 golden scenarios passed. Combined: **164 passed, zero failed, one skipped** across 165 Python methods.

The decisive finding is that the documented per-fill rebate cap can prevent every rebate on tiny fills even within **one order**. Under the literal equations modeled by the existing helper, a 50¢ one-contract buy filled as 100 × .01 contracts costs $1 before reserves at cent-balance precision, whereas a single full-contract fill costs 52¢. The preserved $1.005/$1.015 conservative costs are therefore **attainable in this conditional modeled path**, not just arbitrary loose ceilings. Actual venue behavior, permitted microfill paths and terminal carry handling are not attested by these synthetic calculations.

Current public KXBTC15M metadata supports quadratic fees with multiplier 1. It does not identify this user's balance precision or establish rules at the proposed future start. Scheduled fee changes could not be retrieved conclusively. No account authentication, private-fill access, protected outcomes, collector launch, orders or configuration changes occurred. The candidate and all previously frozen package files remain byte-for-byte unchanged.

## Official evidence and limits

The fee documentation specifies two balance precisions, six-decimal upward trade-fee rounding, signed-cash alignment, an order-local rounding accumulator and precision-aligned rebates capped against negative per-fill net fees. [Official fee-rounding documentation](https://docs.kalshi.com/getting_started/fee_rounding).

The fixed-point page, last updated August 20, 2026, documents .01 contract granularity, four-decimal dollar prices and market-specific `price_ranges`. This supports the representation used in tests, not all current KXBTC15M fill/order permissions. [Fixed-point documentation](https://docs.kalshi.com/getting_started/fixed_point_migration).

The retrieved fee PDF is effective July 7, 2026. Its general taker coefficient is .07, multiplied by the series multiplier; maker pricing differs, settlement fees are absent and intermediary charges may differ. Its alignment wording and cent-rounded example table must be reconciled with detailed account-specific rounding. [Official fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).

The unauthenticated web JSON read of KXBTC15M returned `fee_type=quadratic`, `fee_multiplier=1`, `last_updated_ts=2026-09-18T15:20:18.321673Z`. This is point-in-time public evidence, not future or account attestation. [Public REST series metadata](https://external-api.kalshi.com/trade-api/v2/series/KXBTC15M), [series endpoint documentation](https://docs.kalshi.com/api-reference/market/get-series).

The documented fee-change endpoint supports a series filter. Web requests were inaccessible; direct local unauthenticated reads failed DNS in the restricted environment. The fee-schedule page exposed only an iframe to the web extractor. **No absence of scheduled changes is claimed.** [Fee-change endpoint documentation](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes), [fee-schedule page](https://kalshi.com/fee-schedule).

Evidence summary: [source_evidence.json](source_evidence.json). Direct transport failures: [public_fee_metadata.json](public_fee_metadata.json). The successful web metadata observation and failed direct local transport are explicitly distinguished; local failures do not prove a venue outage. No credentials were loaded or used, and no production fee-verification timestamp was renewed.

## Independent calculations

All figures below concern **one contract in one hypothetical order** at a displayed price of .50, conditional on taker1x. They are model outputs, not real fills.

| Balance precision | Fills | Entry notional | Net modeled fees | Pre-reserve debit | With .5¢ reserve | With total 1.5¢ stress reserve | Winning stress net |
|---|---:|---:|---:|---:|---:|---:|---:|
| .0001 | 1 × 1.00 | .50 | .0175 | .5175 | .5225 | .5325 | +.4675 |
| .0001 | 100 × .01 | .50 | .0175 | .5175 | .5225 | .5325 | +.4675 |
| .01 | 1 × 1.00 | .50 | .0200 | .5200 | .5250 | .5350 | +.4650 |
| .01 | 100 × .01 | .50 | .5000 | 1.0000 | 1.0050 | 1.0150 | −.0150 |

At cent precision, each tiny fill has .005 notional and .000175 model fee. The helper floors signed cash change −.005175 to −.01, producing .004825 rounding excess. Trade plus rounding is .005, less than one .01 rebate quantum. Its rebate cap is therefore zero on every fill, even when accumulated carry exceeds .01. After 100 fills, total fee is .50 and unreleased carry .4825. Including rebates in the calculation does **not** remove this path's cost blocker.

At .0001 precision, each fill contributes .000025 carry, and a .0001 rebate occurs every fourth fill. Total rebates .0025 offset rounding excess, leaving .0175 total fee in the half-price example. This is not a general fragmentation-invariance result: at price .055, one full fill costs .0587 while 100 tiny fills cost .0600 under .0001 precision. At .9999, even the finer-precision modeled debit reaches $1 before reserves. Finer precision alone never establishes profitability.

For every whole-cent price .01–.99, tests show the tiny-fill cent path debits $1 before reserves. The existing ignored-rebate upper bound coincides with that path; stress cost $1.015 exceeds maximum one-contract payout $1. A single-fill calculation cannot replace the fragmentation bound. At .50, the actual helper's finer-precision no-rebate bound is .0200 fees, and its preserved legacy bound is also .0200; narrowing only the balance branch would still require a reviewed conservative bound, not simply substituting the exact .0175 example.

These are accounting-path calculations. Positive winning net in another row means **pointwise feasibility**, not positive expected return, calibrated forecasts or passing prospective confidence bounds. The model does not include unknown intermediary add-ons. Maximum payout scales with quantity: two winning contracts pay up to $2; they cannot be assessed against a one-contract $1 payoff.

Full [20-scenario matrix](conditional_calculations.json), [integer-oracle tests](test_fee_feasibility.py).

## Assumption classification

| Assumption | Assessment |
|---|---|
| General fee rounding and two precision branches | Supported by retrieved official documentation |
| Current KXBTC15M quadratic/multiplier1 metadata | Supported by unauthenticated web JSON read at this review; recheck before any future study |
| .01 fractional quantity representation | Supported generally; specific permitted KXBTC15M execution paths unverified |
| Every fill receives a useful accumulated rebate | Unsupported as a universal claim; cap counterexample disproves it in the literal model |
| Every one-contract order costs $1.005 | Unsupported; one full fill differs markedly |
| 100 tiny fills can occur for the applicable one-contract order | Conditional worst-case assumption; not established from real order/fill evidence |
| No terminal carry refund or other adjustment | Conservative unresolved assumption; no applicable rule located in reviewed sources |
| Account uses .0001 precision | Unknown; price/fee agreement does not classify membership |
| Exact future fee rules, intermediary charges and fill distribution | Unknown; no authenticated account investigation or successful scheduled-change feed |
| Positive expected profitability or study acceptance | Unverified; neither synthetic favorable payout nor accounting feasibility proves it |

## Findings and recommended disposition

### FEE01 — P1: profitability acceptance remains mathematically blocked in the preserved cent-fragmentation scenario

**Component:** unchanged `study_policy.execution_cost`, `fee_reconciliation.fee_sequence`, frozen acceptance policy.

**Reproduction:** `fee_sequence([('.50','.01','buy')]*100,'.01')` produces fee .50, all-in 1.00, carry .4825, no rebates. `execution_cost([('.50','1')],{'slippage_reserve_cents':'.5'})` returns cost 1.005 and stress 1.015.

**Expected:** establish feasibility under all approved assumptions before collection; do not substitute a favorable fill grouping. **Actual:** a winning contract still loses .015 under the preserved stress scenario. **Root cause:** cent rebate quantum and nonnegative per-fill fee cap prevent accumulated rounding refunds on this path.

**Fix/recommendation:** no code change justified. Preserve both precision branches, legacy bound, slippage/stress reserves and acceptance thresholds. Obtain evidence that genuinely excludes or changes this path before proposing a new frozen model. Regression status: exact counterexample, all 99 cent prices and 200 seeded mixed grouping upper-bound checks pass.

### FEE02 — P1 unresolved rule ambiguity: convergence description versus literal rebate-cap behavior

**Component:** documentation semantics and helper's unreleased `rounding_carry`; not a conclusively established implementation defect.

**Reproduction:** literal equations leave .4825 carry after the one-contract microfill example, while the documentation overview describes convergence toward equivalent single-fill cost. **Expected:** an unambiguous applicable terminal carry/rebate rule. **Actual:** reviewed material does not establish one. **Root cause:** general description does not resolve the cap boundary or any final adjustment.

**Recommendation:** request authoritative venue clarification or separately authorized complete-order evidence; do not assume an end-of-order refund, treat fragmented fees as identical or silently amend the model. Regression status: tests verify the helper's literal behavior; they cannot resolve venue semantics. This is a blocking uncertainty, not proof the live venue charges the modeled .50 fee.

### FEE03 — P2: future/account-specific attestation remains incomplete

**Component:** source/fee evidence, account precision, KXBTC15M fill constraints and scheduled future changes.

**Reproduction:** current public series metadata is available through the web tool, but fee-change requests fail through the available transports; no private account/fill evidence is authorized. **Expected:** verified assumptions for the actual approved scope and future start. **Actual:** point-in-time coefficient metadata only, with the above unknowns. **Root cause:** source-access limits and missing scoped attestation.

**Recommendation:** obtain the missing official rules and nonsecret account-precision/fee-scope attestation before any separately reviewed revision. No credentials, private fills, schedules or config were changed here. Regression status: transport failures recorded; synthetic tests cannot substitute for attestation.

## Reasonix execution and local adjudication

Used [Reasonix Orchestrator](/Users/you/.codex/plugins/cache/personal/reasonix-orchestrator/0.1.1+codex.20260925015204/skills/reasonix-orchestrator/SKILL.md) through the local MCP server; probe succeeded.

The first bounded Flash read returned useful arithmetic but **exit 1 / ok false**, after permission-denied external reads. It is partial and not sign-off. Codex independently verified its cap hypothesis but rejected its two-contract payout/fee comparison and its proposed substitution of single-fill finer-precision scenarios for a fragmentation bound.

A narrowed source-only Flash follow-up completed **exit 0 / ok true**, accepting the conditional arithmetic, integer/Decimal consistency and no-narrowing conclusion. It did not fetch official documents, run tests or certify venue/account behavior. Reviewer shorthand confused integer price units with cent indices; local code/calculations use correct units. Unsupported account-local-carry/fee-attribution observations were not adopted as defects or policy changes. Worker prose never overrides the independently checked outputs.

Reported costs: first .012921144 USD, follow-up .012481740 USD; total **.025402884 USD**, not total workflow cost. [Sanitized execution/adjudication status](reasonix_review_status.json). No raw worker transcript or provider configuration is published. Both calls returned; no unattended delegation is being continued.

## Test results and reproducibility

Run from the Investing workspace:

```sh
python3 -B qa_btc_2026-09-27/fee_feasibility_review/run_fee_review.py
```

| Scope | Passed | Failed/errors | Skipped |
|---|---:|---:|---:|
| New offline fee methods | 16 | 0 | 0 |
| Guarded existing candidate Python methods | 148 | 0 | 1 |
| **Combined Python** | **164** | **0** | **1** |
| Existing actual-HTML checks | 24 | 0 | 0 |
| Existing golden scenarios | 14 | 0 | 0 |

Both suite processes and the runner exit 0. The new suite covers 99 whole-cent prices, 200 seeded one-contract groupings checked at both precisions, a 20-scenario matrix, signed sells, subcent edges, invalid quantities, order-versus-account carry, realized useful rebates and counterexamples to finer-precision fragmentation invariance. Its integer oracle uses microdollars and independently computes ceil/floor, fees, cash, carry and each fill's components; expected assertions were written before running. No LLM determines expected fee outputs. Network access is disabled in these tests.

Supplementary source/report/evidence/calculation hashes and frozen output copies are recorded in `fee_review_manifest.json`, with its digest in `fee_review_manifest.sha256`. Run `python3 -B qa_btc_2026-09-27/fee_feasibility_review/verify_fee_review.py` to verify them. It never reseals or approves. Mutable rerun timing logs are excluded; frozen output copies are retained. A plaintext digest is an integrity reference, not an immutable signature or permission to act.

No runtime fix was made, so no before/after fee-policy patch exists. New tests characterize supported equations and challenge assumptions rather than bless live execution. The original/sealed production defects remain unchanged. [Final results](final_results.json), [new test output](fee_test_output.txt), [reproduction runner](run_fee_review.py).

## Smallest justified next step and exact additional approval

**Current recommendation: retain the existing model and blocked status.** This task authorized isolated tests/review only. Nothing requires or justifies production adoption now.

Before proposing any model revision, obtain authoritative applicable rules for minimum fill quantity/fragmentation, terminal carry disposition, fee components and adjustments; trustworthy nonsecret attestation of balance precision and fee scope; complete future scheduled-fee information; and executable-price/slippage assumptions for the proposed research scope. Do not infer precision from membership labels, a few matching fee examples or past favorable results.

If that evidence excludes the cent branch or establishes a compensating rule, the smallest **proposal** would bind the evidenced precision and terminal adjustment to a new study/release, recompute a rigorous permitted-fragmentation upper bound, retain existing acceptance/reserve thresholds and add explicit failure-on-missing-attestation tests. It must not replace a bound with one favorable per-level sample. No such revision is implemented here.

Any authenticated evidence gathering needs separate explicit read-only approval naming exact account scope/endpoints and no account mutation. Any model change needs explicit candidate-only implementation approval for identified files and separately reviewed source/config/protocol hashes. Future collection still requires all recovery/capacity/dependency/holdout gates and a new manifest/package-bound evidence-only activation approval. Orders, trading advice, credentials/config changes, sealed-study changes and protected outcome access remain excluded.

## Safety and preservation

Candidate sources, configuration, launch guard, disabled advice and the 74-file frozen validation package pass integrity checks before and after full suites. Guarded production source inventories and study 002 seal still match; study 002 runtime remains absent. [Preserved local start hashes](protected_local_start_hashes.json) match exactly. The existing frozen package was not resealed or amended. This is a separate supplementary review.

No live execution path was triggered or modified. No real orders or simulated-as-live actions, account authentication, private-fill reads, protected outcome reads, collector start/stop, production config/database/credential/service/schedule changes occurred. Tests only computed hypothetical fee paths offline. Public unauthenticated GET attempts concerned series and fee metadata, not orders/accounts/outcomes. No actual profitability is claimed.

**QA BLOCKED**
