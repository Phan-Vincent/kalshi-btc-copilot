Completed all six items locally on `ta-improvements-20261026`.

| Item | Change |
|---|---|
| 1 | Added New York close-time ticker derivation, an exchange-open-gated direct fallback with exact slot checks, and a quiet discovery-source label. |
| 2 | Added the final-minute required drift in $/s as a settlement-average proxy. |
| 3 | Added order-book imbalance, YES microprice and skew, and observed Kalshi tape summaries using the existing fetched data. |
| 4 | Added the ATR volatility regime and MTF zone tag without changing votes or the logged levels summary. |
| 5 | Added exploratory offline calibration behind `--calibration`; default report output remains unchanged. |
| 6 | Added local `deploy.sh check`, synthetic and DOM tests, and README documentation. |

Files changed: [ta_copilot.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py), [sr_levels.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/sr_levels.py), [tally_report.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/tally_report.py), [app.js](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/app.js), [index.html](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/index.html), [acer/deploy.sh](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/acer/deploy.sh), [test_improvements_20261026.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/test_improvements_20261026.py), and [README.md](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/README.md). The pre-existing untracked brief was left untouched.

**Verification:** `python3 -m unittest discover -q -p 'test_*.py'` reported `Ran 330 tests in 7.766s` and `OK` — 0 failures, 0 skips. `bash acer/deploy.sh check` also passed. The pinned PRIMARY SHA-256 remains `0ee39f0ea8c97779607720e75872c17c7ee70e3f10c5639e29a2d56aea1485f3`. A socket test printed a connection-reset traceback while closing its client; the suite still exited successfully.

No item was skipped. Live feed and Acer behavior were not verified because this was local-only work; nothing was committed, pushed, or deployed.

