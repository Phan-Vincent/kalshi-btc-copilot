# Readiness work — ACTIVE, QA BLOCKED

Previous goal turn classification: PROGRESS. This turn reproduced partial audit commits and unbounded response reads; implemented and tested candidate risk, coherent storage and protocol-proposal checks. No genuine impasse: collector integration and release preparation remain available work. Do not mark the active goal complete or blocked yet.

## User limits recorded

Maximum loss/trade $12; total open-plus-pending notional $500; daily loss $30; one simultaneous position. Adding/averaging disabled. Optional clarification about binary notional is pending: currently BOTH entry-premium and $1-per-contract payout-notional ceilings apply. Daily risk currently uses UTC gross realized losses plus committed open/pending maximum loss; wins do not replenish capacity. Advice remains disabled.

## Completed candidate work

- binary_risk.py, risk_limits.json: pure Decimal cash-bought binary sizing; verified/fresh ledger and fee-bound inputs mandatory; no account authentication or order authority. Strict proposal fields reject unsupported leverage, stops, targets and sell structures.33 focused tests.
- coherent_runtime.py: one SQLite generation transaction spanning real Audit.record, scorecard, real Study.record/status, confirmation state, text and JSON publication; shared transaction adapters; no retention deletion in this coordinator. Real process-exit, rollback, quota, duplicate/concurrent mutation, expiry, corruption, settlement and clock tests.27 focused tests.
- Startup fsyncs RUNNING before work. Clean healthy close removes it. Unclean process exits or failure to write HALTED require operator review; no automatic resume or cohort reset. Database atomic recovery tested on temporary files. Hardware power loss is not certified.
- Response reads capped at4MiB before decode/parse for both transports.3 focused tests.
- readiness_protocol.json + readiness_policy.py: exact UNAPPROVED proposal with7+7+56days plus2days reconciliation, proposed Dec14 2026 start/Feb24 2027 release.20 focused tests. activation_allowed=false cannot be overridden by a generic approval file.
- Fee clarification draft prepared, not sent. Public series metadata rechecked: quadratic x1. General .01 quantity granularity documented; account precision, exact series fill constraints, scheduled fee changes and terminal refunds remain unverified. Conservative economics still prevent favorable profitability acceptance.
- Reasonix design review useful; risk review timeout; runtime review partial. Adjudication in evidence/REASONIX_REVIEW_ADJUDICATION.md. No final independent acceptance sign-off.
- run_checks.py tests current candidate in an isolated temporary mirror of legacy QA. Authoritative current results: evidence/test_results.json. Frozen validation and fee packages reverified unchanged.

## Outstanding next actions

1. Complete executable evidence-only collector and one-generation UI/reader integration using EvidenceStore; current btc_copilot.main stays unconditionally disabled and legacy file-persistence path remains only a reference/regression path. No current launchable collector exists.
2. Bind all source/protocol/risk/config/dependency hashes, private runtime destination, real OS clocks/boot identity, exact read-only credential scope, source/fee/rate freshness and explicit future approval to a separately reviewed release. Do not make the unapproved proposal activatable by bypassing its false flags. Do not touch original/sealed/frozen packages.
3. Finish recovery operating procedure (unclean exit requires operator review; preserve missing windows/cohort history), storage sizing benchmark and final independent review. Full physical power-loss/hardware durability remains unverified.
4. Integrate risk evidence provenance/fresh ledger requirements only into a future authorized advice layer; do not convert conditional sizing into approval or a signal. NO TRADE stays universal public output until validation.
5. Complete final release freeze/verifier, repeat relevant suites after integration, concrete deployment proposal with exact reviewed hashes/destination/access/rollback. Working manifest is explicitly not an activatable release.
6. External dependencies: venue clarification and account-owner nonsecret evidence; no sends/auth or collection currently authorized. Preserve unfavorable fee bounds. Do not start economically infeasible strategy validation merely to meet a date.

No orders, production changes, credential access, service/schedule changes, collection, external messages or protected outcomes accessed. sources/ untouched. Candidate writes are limited to readiness_release/. Review-only public-source hash checks performed against the original repository.
