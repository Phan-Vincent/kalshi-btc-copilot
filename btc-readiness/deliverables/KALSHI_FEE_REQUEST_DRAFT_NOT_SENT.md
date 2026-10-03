# Kalshi support request — DRAFT, NOT SENT

**How to send:** email **support@kalshi.com** from the email address on your Kalshi account. Kalshi's contact page ([help.kalshi.com, "Contact Kalshi Support"](https://help.kalshi.com/en/articles/13823855-contact-kalshi-support)) lists that as the official channel after in-app chat, and asks for your account email and full name. Fill in the two bracketed placeholders below. Nothing here has been sent. Do not attach API keys, signatures or raw fill exports. If support asks for an example order, send the order ID yourself in a reply.

Copy only the text between the two `---` lines.

---

**Subject:** KXBTC15M fee rounding — balance precision for my account (API fee model question)

Hello,

My name is [Your full name], and I am writing from the email on my Kalshi account. I raised this in support chat earlier but didn't get an answer, so I am following up by email. These are detailed questions about exchange fee rounding. If they need to go to a person on the exchange operations or API team, please forward them.

I am validating an offline model of Kalshi fees for the KXBTC15M series (taker, quadratic, coefficient 0.07, multiplier 1). I am not asking you to place, change or cancel any order. **If you can only answer one question, please answer question 1.**

1. **Balance precision for my account.** Your fee-rounding documentation (https://docs.kalshi.com/getting_started/fee_rounding) says direct members have $0.0001 balance precision and non-direct members have $0.01. Am I a direct member? If so, since when, and is there an account field or statement line that shows it? Could my classification change (for example, if I ever traded through a broker), and would I be notified? For context: nearly all of my single-fill taker orders match the $0.0001 formulas exactly, none match only the $0.01 formulas, and the rest are the sells in question 6. I would still like an authoritative confirmation.

2. **Rebate cap on small fills.** Take a single taker buy of 1.00 contract at $0.50:
   - As one 1.00-contract fill, my reading gives a fee of $0.0175 at $0.0001 precision, and $0.02 with $0.0025 left in the rounding accumulator at $0.01 precision.
   - As 100 separate 0.01-contract fills at $0.0001 precision, is the total fee still $0.0175?
   - As 100 separate 0.01-contract fills at $0.01 precision, my literal reading of the per-fill rebate cap gives a total fee of $0.50, with $0.4825 left in the accumulator. Is that correct, or is the cap applied across the whole order?

3. **Terminal accumulator and final fees.** When an order is fully filled, canceled or expires, is any remaining rounding-accumulator balance refunded? If so, when, and in which API field or statement line does it appear? Is the `fee_cost` reported on each fill final, or can later refunds or adjustments post separately?

4. **Fill quantum.** What is the smallest fill quantity that can execute on KXBTC15M (for example 0.01 contract), and can one order be split into many fills of that size?

5. **Fee changes.** Will every future fee or multiplier change for KXBTC15M appear in `GET /series/fee_changes` before it takes effect, and with how much advance notice? That endpoint currently shows no scheduled changes for KXBTC15M. Are any planned through the end of March 2027?

6. **Sell-side rounding.** On a few sell fills at sub-cent prices, the reported fee is $0.00002–$0.00008 higher than the published formula gives when applied to sell proceeds. Is there a different rounding step for sells?

A worked ledger (each fill with its trade fee, rounding and rebate) for the 100-fill example in question 2, at both precisions, would answer questions 2–4 together. I can share specific order IDs by reply if that helps.

Thank you,
[Your full name]

---

## Why these questions (for you, not for Kalshi)

- **Question 1 decides the fee model.** Under $0.01 precision, fragmented tiny fills can bring the total cost to $1 per contract, and that one assumption blocks any positive profitability result. Of your comparable single-fill taker orders, nearly all match only the $0.0001 model and none match only the $0.01 model; a few sub-cent sells (question 6) match neither exactly. A written confirmation lets the next study drop the $0.01 worst case (see `FEE_EVIDENCE_AND_BOUND_PROPOSAL.md`). The classification question matters because the answer has to hold for the whole study.
- **Questions 2–4** matter only if support says $0.01 applies, or if the cap works differently from the docs. The Q2 numbers were rechecked on 2026-09-29 with `fee_reconciliation.fee_sequence`. The `fee_cost` question covers the "later adjustments" gap listed in the fee evidence write-up.
- **Question 5:** the Acer's 4-hourly attestation relies on `/series/fee_changes` to catch fee changes, so what matters is whether that endpoint is authoritative and gives advance notice. On 2026-09-28 06:33 UTC it showed no scheduled KXBTC15M changes. The date is March 2027, not February, to leave room if the study start slips.
- **Question 6** is a small modeling gap: at most $0.0001 per order, on sells only. It doesn't affect entry sizing.
- **Channel:** in-app chat went unanswered, so the request goes by email. Kalshi's contact page asks users not to email while a chat is open. The draft says this follows up on that chat, and it asks for escalation because first-line support is AI-assisted.
