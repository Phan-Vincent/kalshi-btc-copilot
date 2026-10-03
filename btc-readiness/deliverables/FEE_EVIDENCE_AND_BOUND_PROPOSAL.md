# Fee evidence and proposed fee bound — PROPOSAL, NOT ADOPTED

**Bottom line.** This account's fee history fits $0.0001 balance precision and rules out $0.01 precision. Under $0.0001 precision, the $1-per-contract worst case that blocks every profitability study disappears. The proposed replacement bound is `0.07·p·(1−p) + $0.0101` per contract.

Nothing frozen has changed. Study 002, the frozen packages and `readiness_protocol.json` keep the original bound. Adopting this bound takes Kalshi's confirmation (draft request in `KALSHI_FEE_REQUEST_DRAFT_NOT_SENT.md`), independent review, and a newly sealed protocol.

## Evidence (GET-only, retrieved 2026-09-28 06:33 UTC)

- **Source:** `fee_evidence_collect.py` with the production read-only client, reading `/portfolio/{fills,orders}` and `/historical/{fills,orders}`.
- **Scope:** every KXBTC15M record before 2026-09-27 00:00 UTC. That's the same pre-study cutoff as the earlier Codex review, so no study-period records were read.
- **Completeness:** all four paginated reads reported complete.
- **Storage:** raw records stay in `private/` with owner-only permissions. Only the aggregate counts below leave that folder. Raw-file SHA-256 hashes are in `fee_evidence_summary.json`.

| Result | Orders |
|---|---:|
| BTC 15-minute fills / orders with fills (all matched to an order record) | [redacted] |
| Single-fill taker orders matching **$0.0001 exactly and not $0.01** (the two models differ on each) | [redacted] |
| Single-fill orders where both models agree (uninformative) | [redacted] |
| Single-fill orders matching **only $0.01** | [redacted] |
| Single-fill orders matching neither: all sells at sub-cent prices; off the $0.0001 model by $0.00002–0.00008, off the $0.01 model by $0.0005–0.0084 | [redacted] |
| Multi-fill orders with tied timestamps (sequence unknown) that match $0.0001 under some fill ordering | [redacted] |
| …of which also match $0.01 | 1, where the models agree |
| Multi-fill orders matching neither model under the orderings tried (a 7-fill order was tried in only a few of its 5,040 possible fill orderings) | [redacted] |
| Multi-fill orders where a fill's fee is **below** its trade fee (a rebate was applied on that fill) | [redacted] |
| Maker or mixed orders (excluded) | [redacted] |
| Fills below one contract / fractional fills | [redacted] |

Codex's earlier review reconciled a few orders. This covers the account's full pre-study history.

**What it supports:** reported fees are produced by the $0.0001 formulas, including the rounding accumulator and per-fill rebates. The $0.01 formulas are contradicted by every decisive order and supported by none.

**What it doesn't prove:**
- Kalshi's formal classification of the account.
- That the account will keep this precision through February 2027.
- That `fee_cost` includes every later adjustment.
- The exact sell-side rule behind the few unmatched sells. Reasonix suggested that sell fees are rounded up to the $0.0001 grid; tested locally, that doesn't explain them.

Fractional and sub-one-contract fills do happen on this account (some fills were below one contract), so fragmentation is a real scenario, not just a theoretical one.

## Proposed bound (`candidate/fee_bound_proposal.py`)

For a taker buy of Q contracts split into fills that are each a multiple of 0.01, there are at most 100·Q fills. Each fill's net fee is below its trade fee plus $0.000001 of six-decimal ceiling plus $0.0001 of rounding, and rebates are never negative. So:

`fee < Q × (0.07·p·(1−p) + 0.0101)`

`tests/test_fee_bound_proposal.py` checks the bound against the existing `fee_sequence` for:
- all 99 cent prices plus sub-cent edge prices (.0001, .0550, .9999);
- quantities .01, 1 and 2.37;
- 22 groupings each: all-.01 fills, a single fill, and 20 random partitions.

No counterexample was found.

| Entry price | Actual fee, 1 fill (.0001) | Proposed bound | Current worst case (.01, 100 tiny fills) | Win net of bound + 1.5¢ stress | Break-even win rate |
|---|---|---|---|---|---|
| 0.20 | 0.0112 | 0.0213 | 0.8000 | +0.7637 | 23.6% |
| 0.40 | 0.0168 | 0.0269 | 0.6000 | +0.5581 | 44.2% |
| 0.50 | 0.0175 | 0.0276 | 0.5000 | +0.4574 | 54.3% |
| 0.60 | 0.0168 | 0.0269 | 0.4000 | +0.3581 | 64.2% |
| 0.79 | 0.0117 | 0.0217 | 0.2100 | +0.1733 | 82.7% |
| 0.90 | 0.0063 | 0.0164 | 0.1000 | +0.0686 | 93.1% |

At $0.01 precision the fragmented path always debits $1.00 per contract, so every win nets −1.5¢ after the stress reserve. That is why profitability acceptance is currently impossible. At $0.0001 precision it becomes possible if the strategy's hit rate beats the break-even column. **That is an economic possibility, not evidence of edge.**

## Proposed protocol change (for the next sealed study only)

In `fee_policy`, change `balance_precisions` from `["0.0001", "0.01"]` to `["0.0001"]`, and replace the per-unit cent-ceiling acceptance text with the bound above. Every other field stays the same: coefficient, multiplier, 1.5¢ stress, one-unit hypothetical, acceptance thresholds, calendar lengths and custody.

Preconditions:
1. Kalshi confirms $0.0001 precision in writing.
2. An independent review of this derivation and evidence (the Reasonix review is in `REASONIX_FINDINGS.md`).
3. KXBTC15M rules are freshly verified at activation: quadratic, multiplier 1, no scheduled changes.

If Kalshi says $0.01, or declines to confirm, the conservative bound stays and the strategy remains economically blocked. In that case, stop the profitability study rather than weaken the bound.
