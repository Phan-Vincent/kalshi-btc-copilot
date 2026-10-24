# Excusing Kalshi-side HTTP 5xx in the shakedown gate — NOT APPROVED

**Owner decision 2026-10-24: approved as drafted** (1% run-level cap; HTTP 429 still counts). The file name is kept for history. Drafted on branch `shakedown-upstream-5xx`, 2026-10-24. Local implementation only; not deployed. No candidate, protocol, evidence, manifest or sealed-tree changes.

## Why

The supplied run-A incident record reports 43 failures out of 208 polls (20.7%) in the 2026-10-23 15:00 UTC hour. Kalshi's `/cfbenchmarks/values?id=BRTI` returned HTTP 503 from 15:20:58 to 15:43:33 UTC. One slot was missed, with a logged reason. The per-hour 1% gate fails the run even though these errors do not establish a collector defect.

Exchange-side incidents also affected October 20 at 19:00 UTC and October 21 at 19:02 UTC. This is a narrow HTTP 5xx proposal: discovery failures such as “Cannot verify exactly one active…” still count, including any from those periods. These incident details are supplied context, not a new live audit.

## Rule and cap

- Excuse only `COLLECTION_FAILED` details matching the client's fixed text `Kalshi GET failed with HTTP 5\d\d` (a three-digit status).
- WAN wins: a failure inside a corroborated WAN window counts only as `failed_during_wan_outage`. Otherwise a matching 5xx counts as `failed_upstream_5xx`.
- The hourly rate becomes `(failed - wan - upstream) / (failed - wan - upstream + successful)`. Zero counted polls retain the existing `None`, partial-hour and explained-inactivity rules.
- `UPSTREAM_5XX_MAX_FRACTION = 0.01`: excused 5xx polls divided by **all raw polls of the run**, including WAN failures, must be at most 1%. The summary records `upstream_5xx: {max_fraction, excused, polls}`.
- The seal checks nonnegative integer counts, nonoverlapping exclusions, exact rates, the fixed cap and both totals. Exceeding the cap fails even when every hourly rate passes. Old summaries without the new fields retain their previous checks.

## What stays a failure

HTTP 429, network/TLS errors outside qualifying WAN windows, discovery errors and all other failures retain their existing treatment. A 5xx does not create a WAN window. Missed-slot reasons still use the logged failure detail.

Coverage below 95%, any unnamed miss, an hourly counted rate above 1%, or an upstream excused fraction above 1% still fails the run. The existing duration, unexplained-failure and storage gates also remain.

## Monitor

The monitor applies the same exclusions and checks the 1% cap over all polls in its lookback, including the current partial hour. A problem alert includes `Excused as Kalshi-side 5xx: N failure(s)` when applicable.

The lookback now starts at `max(start, floor((now - LOOKBACK) / 3600) * 3600)`. Completed hours are iterated from `floor(since / 3600) * 3600`, so a timer firing at 03:00:02 includes yesterday's 15:00 hour. The run's partial first hour is judged on its available polls. Slot checkpoint-band rules remain in place.

## Risk

Chronic upstream flakiness is excused up to 1% of all run polls. Concentrated failures can therefore have a high raw hourly rate and still pass. Coverage and named-miss requirements constrain the resulting gaps; they do not eliminate this risk.

## Verification

Synthetic SQLite regressions cover a 72 h run with 43 benchmark 503s, 429s and other counted errors, WAN precedence, the run cap, tampered counts/rates/cap parameters, old summaries, timer boundaries, the partial first hour and alert text. No live database or network access is needed.

## Decisions for the owner

1. Approve or reject this exclusion.
2. Keep the 1% run-level cap or choose another value.
3. Keep HTTP 429 as a counted failure or propose a separate rule for it.

Approval and deployment are separate actions. Neither has occurred here.
