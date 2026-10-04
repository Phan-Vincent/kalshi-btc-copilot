Completed the two fixes in `ta_copilot/`.

- [ta_copilot.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:478): flow stays neutral until 60 seconds of the current window have passed. The 1m family exposes coverage and readiness; opening records keep the measured imbalance and always record a neutral flow vote. Minute-row fields and timing are unchanged.
- [ta_copilot.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1246) and [tally_report.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/tally_report.py:49): unreadable log lines are counted and skipped, later rows still load, and the first `open` and `settled` row per ticker wins. Appends are locked, complete short writes, and separate a torn tail from the next row.
- [app.js](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/app.js:219) and [README.md](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/README.md:40): added the warming-up display, skipped-line note, and updated behavior documentation.
- [test_fix_20261004.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/test_fix_20261004.py:1) adds the requested regressions; [test_tally_20261003.py](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/test_tally_20261003.py:94) updates the former bullish-opening expectation.

**Tests:** 146 discovered before, 156 after. The final full suite passed: `Ran 156 tests — OK`. `git diff --check` and `node --check app.js` passed. The initial sandbox run had five existing local-socket permission errors; the full suite passed when loopback binding was allowed.

Reasonix timed out after a partial allowed-file edit; I finished and verified the changes locally. No implementation item is left undone. The Acer was not tested, nothing was deployed, and no commit was made. Existing untracked `history/` files were left untouched.

