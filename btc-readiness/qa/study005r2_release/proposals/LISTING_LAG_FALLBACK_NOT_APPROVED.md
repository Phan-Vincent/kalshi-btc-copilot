# Direct-contract fallback for listing lag — NOT APPROVED

Drafted on `listing-lag-fallback`, 2026-10-25. Local candidate implementation for owner review; not approved, sealed or deployed. No protocol change.

## Why

The supplied run-A incident record reports a halt on 2026-10-24 at 16:10:20 UTC. From 16:00:35, 40 consecutive polls found zero eligible contracts in an empty open-market listing. `MAX_CONSECUTIVE_FAILURES` stopped collection. The separate TA copilot had prefetched `KXBTC15M-26OCT241215-15`, saw its target at 16:00:06 and read it directly through 16:14. The contract was open 16:00–16:15 UTC. Shorter listing lags beginning at `:00:35` also occurred on October 19 and 20. These are supplied incident findings, not a new live audit.

## Rule

Only a completed discovery with zero eligible contracts and successfully read boolean `exchange_active=true` and `trading_active=true` can use the fallback.

1. Remember listed KXBTC15M tickers in process memory, by exact opening and closing times. Retain only the current and next 900-second slots. Completed listings can supply next-slot tickers before they become eligible. Restart clears memory.
2. Prefer the remembered ticker matching the current slot. Multiple remembered tickers fail closed. A failed remembered read does not try a derived ticker.
3. With no remembered ticker, derive `KXBTC15M-` plus the slot close in America/New_York (`%y%b%d%H%M`, uppercase English month), plus `-` and close minute `%M`. ZoneInfo applies DST: 2026-10-24 16:15Z gives `KXBTC15M-26OCT241215-15`; 2026-11-07 16:15Z gives `KXBTC15M-26NOV071115-15`.
4. Read that market once through the existing allowlisted `client.market(t)`. Require exact ticker identity, exact slot times and the unchanged `verified_market`. Continue the usual snapshot, including its fresh market read, event identity and series checks.
5. If the event supplies markets, require exactly one eligible entry, matching the selected ticker and event. Check every supplied list; reject empty, malformed, duplicate, different or second-eligible lists. An absent list supplies no membership evidence.

The protocol's missing policy requires “one unique active KXBTC15M contract per slot”; its duplicate policy says “ambiguous discovery fails closed”. A directly verified contract with an empty listing is not ambiguous discovery in the sense of competing observed eligible contracts. It does weaken the evidence for uniqueness: the empty listing cannot exclude an unlisted second contract. An event market list narrows that risk but cannot prove completeness or exclude a second event. Owner approval is required for this interpretation of the frozen candidate.

## Payload evidence and provenance

`qa/original/work/btc_copilot_test_fixture.json` has `event.data.event` with `event_ticker` and `series_ticker`, and a sibling `event.data.markets` list containing one full market. Its `raw_json` agrees. The list is not nested inside the event object. The implementation also checks `data.event.markets` if supplied; that optional shape is covered synthetically, not established by the fixture. Live check (Claude, 2026-10-25, read-only on acer): run A's stored raw event payloads have the same shape, `data.event` plus a sibling `data.markets` list with one full market object (status, rules_primary, strike_type, floor_strike, times). On 10 sampled live observations, the membership rule found exactly one eligible market equal to the snapshot's ticker, so the rule does not break the normal listing path. Payloads from the incident minutes themselves don't exist: those polls failed before the snapshot.

Each snapshot records `retrieval_health.discovery` as `listing`, `remembered_ticker` or `derived_ticker`. No runtime event kinds are added.

## What still fails

Incomplete discovery, two eligible listing entries (including duplicate ticker entries), unreadable or nonboolean status, ambiguous remembered tickers, HTTP 404/503, invalid direct market identity/series/status/times/rules, and contradictory event membership fail closed. Boolean closed flags retain `ExchangeClosed`. Adapters without a callable direct market reader retain the original discovery error. The real KalshiReadOnly client supplies that reader.

Direct selection/verification and subsequent market/event verification errors retain the original zero-eligible message with a short `; direct fallback: ...` reason, capped at 200 characters. Other snapshot source failures retain their existing errors. No second fallback attempt, eligibility relaxation, request-allowlist change or status-based failure excuse is introduced. Existing freshness, pacing, cutoff and scoring checks remain.

## Request budget and study effect

The normal listing path adds no requests. A successful fallback adds one market GET to the existing snapshot work, plus the status probe already made on the zero-eligible path. Relative to a normal successful listing poll, that is two additional GETs: status probe and direct verification. The usual snapshot market and exchange-status reads still run; neither early read replaces a timed snapshot source. Failed direct verification stops after at most one extra market GET. There are no retries. Existing pacing applies, and extra latency can still cause a freshness veto.

The candidate and working-manifest digests change. Run A cannot qualify this candidate. A fresh shakedown, run B, and subsequent owner release review are required. Inventory regeneration is not approval or an activatable seal.

## Verification

The 22 fake-client fallback tests pass. `python3 -B -m unittest discover -s tests -p 'test_*.py'` runs 330 tests: 329 pass, one known Mac sandbox clock test skips. The 34 manifest hashes match the candidate. `run_checks.py` was not run. No network, SSH or Git mutations were used; no external worker was dispatched because model delegation would require network access.

## Decisions for the owner

1. Approve or reject the fallback and the stated uniqueness interpretation.
2. Permit the derived ticker, or permit only a ticker remembered from a completed listing. This draft implements both; remembered-only approval requires removing the derived path before run B.

Approval, a fresh shakedown and deployment are separate actions. None is completed by this proposal.
