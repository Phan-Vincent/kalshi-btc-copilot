# BTC 15-minute copilot — readiness report (2026-09-28)

**Status: trading advice stays disabled. The candidate is ready for review, not for deployment.** No orders were placed, no sealed files were changed, no credentials were changed, no messages were sent, and no services were started. All account access was GET-only.

**Update 2026-09-28:** at your instruction, Studies 001 and 002 are retired: Study 002's stop latch is set and its heartbeat paused, and nothing was deleted or opened. The study moves to the Acer, with deterministic timers and Telegram alerts; nothing there is installed or started yet.

This session built on Codex's unfinished 5.2.0 readiness work (a copy of Codex's folder; the original is untouched) and produced working candidate **5.3.0**.

## Track results

| Track | Result |
|---|---|
| **1. Fee evidence** | The account's full pre-study history fits **$0.0001** balance precision: every decisive order matched $0.0001, and none matched $0.01 only. Under $0.0001, the $1-per-contract worst case that made profitability acceptance impossible no longer applies. A proposed bound, `0.07·p(1−p) + $0.0101`, was confirmed by Reasonix across 393,210 exhaustive and 300,000 random fill groupings. The Kalshi request is drafted, not sent. The existing bounds stay in force until Kalshi confirms and a separate review is done. |
| **2. Reliability** | 9 defects fixed with regression tests. The main one: **ordinary Mac sleep permanently halted collection**, because macOS `time.monotonic()` stops during sleep. Also fixed: a single non-network glitch killed the collector; settlement transients were fatal; integrity mismatches weren't halted; the manifest was stale. |
| **3. Risk controls** | Schema-2 engine with your limits: 5% of verified account value per trade, capped at $25; exposure counted as premium + fees + stress + pending; $30 daily on the Pacific calendar day; 1 position; adding disabled. Missing limits or stale evidence disable advice. The engine is offline only. |
| **4. Validation** | Codex's Study 005 protocol is preserved byte-for-byte: 7 + 7 + 56 days, all acceptance thresholds. Dec 14 was the earliest start only because of Study 002's holdout. **With Studies 001/002 retired before their holdouts, Study 005 can start at the first 00:00 UTC after approval.** Collection has not started. |
| **5. Acer setup (new)** | `release_gate.py` (re-checks approval and a <24 h attestation every cycle) and `acer_ops.py` (GET-only fee/rate/clock attestation, read-only status, Telegram alerts with a daily "alive" message), plus systemd units, `install.sh` and a runbook in `../acer/`. Tested on the Mac and on the Acer itself. A live read-only dry run of the attestation passed (clock +0.34 ± 0.65 s). It also caught and fixed a clock-measurement bug. |

Tests: `run_checks.py` on the Mac → **299 Python methods passed (1 intentional legacy skip), 14/14 golden scenarios, 24/24 UI checks, production sources unchanged.** On the Acer (Linux): 158 readiness tests (1 macOS-only skip) plus Codex's 51 candidate tests pass, and `--collect` refuses with exit 2.

## Remaining blockers

| # | Blocker | Owner | Depends on | Deadline / next milestone |
|---|---|---|---|---|
| B1 | ~~Study 002 halts on Mac sleep~~ **Resolved:** retired | — | — | Please confirm the Study 002 heartbeat shows as paused in the Codex app |
| B2 | Kalshi confirmation of $0.0001 precision, fill quantum, terminal carry and scheduled fee changes | **You** send the request; Kalshi answers | Draft ready | **Now the critical path:** the study starts as soon as this and B3/B5 are done |
| B3 | Reviewed protocol revision adopting the $0.0001 bound, with new dates. If Kalshi says $0.01, **stop the profitability study** rather than weaken the bound. | Reviewer plus you | B2 | Within days of B2 |
| B4 | Always-on host. **Resolved: the Acer.** Remaining: install the read-only key and Telegram config on it (`acer/README.md`), run `./install.sh stage`, and receive the test alert | **You** | Nothing | Any time; stage early so a week of attestations and alerts is proven before activation |
| B5 | Release freeze: new dates, `activation_allowed` in a newly sealed protocol, re-bound hashes, a final Reasonix re-review (including `release_gate.py`/`acer_ops.py`), `QA_ONLY` removed via an explicit step | Engineering | B3 | Right after B3 |
| B6 | Protocol wording: "reject suspend discontinuities" should become "reject wall/continuity-clock disagreement" to match the sleep fix | Reviewer | B5 | With B3 |
| B7 | Signal frequency is unknown. The study needs ≥100 hypothetical fills over ≥40 days, and too few signals fails it regardless of edge. With Study 002 retired, the first real counts come from Study 005's 7-day development window. | Study 005 development week | B5 | End of Study 005's first week |
| B8 | Wire the risk engine to a fresh ledger (balance, positions and fills via GET) in the advice layer | Engineering | Study 005 passing | After Study 005's release |

## Honest timeline

- **Now:** you send the Kalshi request and install the credentials on the Acer. Stage it to prove attestations and alerts for a week.
- **Kalshi answer + about a week:** protocol revision, freeze, re-review, your approval.
- **Start at the next 00:00 UTC after approval**, then 70 days of collection plus 2 days to release. If Kalshi answers within about two weeks, that's roughly **mid-October → late December**. The earliest possible advice date moves from Feb 24, 2027 to about **early January 2027**, and it depends entirely on how fast Kalshi answers.
- A pass isn't likely by default: the strategy has to beat the break-even win rate after fees (for example 54.3% at a 50¢ entry). If Kalshi confirms $0.01 precision, the economics are infeasible and the right move is to stop, not wait.

## Files

- `DEPLOYMENT_PROPOSAL.md`: retirement record, decisions B/C, Acer destination, hashes, access and rollback
- `../acer/`: `README.md` runbook, `install.sh`, `copy_readonly_key.sh`, systemd units
- `KALSHI_FEE_REQUEST_DRAFT_NOT_SENT.md`: ready to paste into Kalshi support
- `FEE_EVIDENCE_AND_BOUND_PROPOSAL.md`: evidence, bound derivation, economics table
- `OPERATIONAL_FIXES.md`: O1–O9 with reproductions
- `REASONIX_FINDINGS.md`: the independent review and how each finding was resolved
- `test_results.json`: final suite results
- Candidate code: `../qa/readiness_release/candidate/`; tests: `../qa/readiness_release/tests/`
- Raw private fee data (owner-only permissions, never shared with reviewers): `../private/`
