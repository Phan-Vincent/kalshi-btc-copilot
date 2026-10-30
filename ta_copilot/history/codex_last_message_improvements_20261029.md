Implemented and independently reviewed. No commits, deployment, Acer access, or external network use.

1. Deleted books pruned; late frames advance sequence without recreating books or resubscribing: [kalshi_ws.py:168](kalshi_ws.py:168), [ta_copilot.py:997](ta_copilot.py:997).
2. Captured tickers capped at 200, preserving recent deduplication: [ta_copilot.py:1481](ta_copilot.py:1481).
3. `brti_finalized_at` validated: [tally_report.py:60](tally_report.py:60).
4. BRTI error column added, including null handling and eight-column empty state: [app.js:395](app.js:395), [index.html:168](index.html:168).
5. Exploratory family opening hit rate added with Wilson intervals and separate zero-lean count: [tally_report.py:419](tally_report.py:419).
6. Measured state builds with 300 bars per timeframe:

| Call | Median ms |
|---|---:|
| `state("1m")` | 12.991 |
| `state("5m")` | 13.719 |
| `state("10m")` | 13.460 |
| `state("15m")` | 13.533 |
| `state("1h")` | 13.605 |
| All five cold-cache builds | 68.661 |

Each call builds all frames. Indicators dominate: **8.790 ms**, versus **4.251 ms** for `sr_levels.levels()` per shared frame build.

Verification: **394 → 403 tests, OK**, one skipped in both runs. Nine new regressions added; one existing report comparison updated. Node syntax and diff checks passed.

**PRIMARY diff: byte-identical**, including a separate evaluated 800-window synthetic comparison. PRIMARY functions remain unchanged.

Worker used: one local implementation sub-agent. Phone layout was checked through CSS/DOM; no device rendering was performed.

