# Copilot first improvement batch — 2026-10-27

Implementation handoff snapshot, before commit and deployment: built on top of `80e595d`; uncommitted, not pushed, not deployed at the time of this report.

- The headline uses the 1m grouped trend/momentum/flow lean. Legacy counts remain secondary; missing or stale family data cannot fall back to the legacy lean. Research definitions and `tally_report.py` are unchanged.
- The exact lightweight-charts 4.2.3 bytes matching the existing SHA-384 integrity pin are bundled under `vendor/` with the upstream Apache license. The server allows only the exact asset route; script CSP is same-origin only. Deployment packaging includes the asset and license.
- Captured minute rows are copied into a bounded 60-row in-memory backlog. Temporary append failures retry every five seconds while new minute captures continue. Original timestamps and values are retained; successful rows leave the queue. Overflow is reported. Pending rows do not survive a process restart.

Verification: `bash ta_copilot/acer/deploy.sh check` completed 382 tests, 381 passing and one cryptography-dependent test skipped. Seven new regression tests cover original snapshot retention, retry scheduling, later captures, partial flush/error lifetime, deduplication, overflow, headline conflict/missing/stale states, and local chart HTTP/SRI/CSP behavior. Python compilation, JavaScript syntax, shell syntax, and `git diff --check` passed. An offline synthetic browser preview showed Mixed alongside the secondary five-bull count, with four chart instances and no browser console errors. The suite printed a connection-reset traceback while a test client disconnected, but no test failed.

Reasonix Flash task `ta-copilot-first-batch-20261027` produced a partial draft and stopped at its step limit before tests. Supervisor reviewed every diff, supplied verified vendor assets, preserved an existing variable-name compatibility check, froze queued snapshots, added regressions, and completed verification. Original worker result is in the existing Reasonix objective ledger.

Changed implementation: `app.js`, `index.html`, `ta_copilot.py`, `acer/deploy.sh`, `README.md`; added `test_improvements_20261027.py`, `vendor/lightweight-charts-4.2.3.js`, and `vendor/LICENSE`. No changes in `btc-readiness/` or `tally_report.py`. Production version parity was not checked in this implementation pass.

Next action, if authorized: deploy this verified local batch to Acer and verify served assets, CSP, family headline, service health, and rollover. Do not imply the local implementation has already been deployed.
