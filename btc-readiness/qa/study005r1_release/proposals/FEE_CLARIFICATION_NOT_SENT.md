# Venue clarification draft — NOT SENT

Subject: KXBTC15M fractional-fill rounding, rebate cap and terminal accumulator treatment

Please clarify the applicable rules for KXBTC15M. We are validating an offline research model; this request does not ask you to place or modify any order.

Consider a non-direct account with $0.01 balance precision, one taker buy order totaling 1.00 contract at $0.50, filled as 100 separate fills of 0.01 contract. Assume the quadratic coefficient is 0.07 and multiplier is 1.

Our literal implementation of the published per-fill rules gives:

| Per 0.01-contract fill | Dollars |
|---|---:|
| Premium | 0.005000 |
| Model fee before rounding | 0.000175 |
| Trade fee rounded to six decimals | 0.000175 |
| Debit aligned to cents, before rebate | 0.010000 |
| Rounding amount added to accumulator | 0.004825 |
| Trade plus rounding fees available to cap rebate | 0.005000 |

A cent-sized rebate exceeds the current fill's $0.005 fee. If the nonnegative-net-fee cap applies separately to each fill, all 100 fills have zero rebate. Total premium is $0.50, total fees $0.50, total debit $1.00, and remaining accumulator $0.4825. One equivalent unfragmented fill instead debits $0.52. These are conditional calculations, not observed transactions.

1. Is this fill sequence permitted for KXBTC15M and this account type? Please distinguish order-size minimum, execution/fill quantum, quantity increment, and any aggregation before fee calculation.
2. Does the rebate cap apply to the current fill alone, accumulated order fees, or another basis? Please provide the exact calculation for this example.
3. Is any accumulator balance refunded when an order completes, is canceled, expires or is otherwise terminal? If so, when, with what rounding, and in which API/statement field is the credit recorded?
4. How does the rule that fees converge to one equivalent fill interact with a cent rebate quantum larger than every fill's fee? Is convergence conditional on a later sufficiently large fill?
5. Does the accumulator ever cross order IDs, market IDs, maker/taker transitions, or partial cancellation/replacement boundaries?
6. Please confirm the current KXBTC15M coefficient/multiplier, any applicable maker/settlement fees, and scheduled fee changes with exact effective timestamps.
7. What nonsecret field or official account notice establishes whether this account uses $0.01 or $0.0001 balance precision? We do not need credentials, account balances or identifying trade records in the reply.
8. Is fractional settlement payout rounded separately? Please specify the rule and whether any residual carry is refunded at settlement.

Please provide an official rule reference or worked ledger for the example and identify any account-specific conditions. We will retain conservative cost bounds until the ambiguity is resolved and independently reviewed.

References: [fee rounding](https://docs.kalshi.com/getting_started/fee_rounding), [fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration), [KXBTC15M series metadata](https://external-api.kalshi.com/trade-api/v2/series/KXBTC15M).

---

Nonsecret evidence checklist for the account owner, not an authorization to authenticate:

- Dated official classification or precision field, with personal identifiers redacted.
- Applicable effective fee rules, minimum executable quantity and terminal-credit semantics.
- Confirmation that available credentials are restricted to required reads; no secret values or signing-key contents.
- Dated read-budget and endpoint-cost evidence for the eventual collector. Public series metadata alone does not prove these account conditions.

No message has been sent. No authenticated request, fill experiment or order is proposed as a substitute for official clarification.
