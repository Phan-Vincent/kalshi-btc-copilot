The three upgrades are deployed on the Acer. All 26 tests passed locally and on the Acer; the dashboard service and five-minute watcher timer are active. The [published page](https://homelab.example-tailnet.ts.net:11443/) returned HTTP 200, and `/api/state` showed a fresh composite and 60-second average.

The live 21:15 window was captured 0.864 seconds after opening. **Kalshi had not yet published its target or usable YES quotes**, so the opening line was appended when the target appeared about 15 seconds later; its opening bid/ask fields are null. Earlier 20:45 and 21:00 openings were missed by interim revisions and were not backfilled. The live settlement append is still pending; its path passed a public-market integration check.

The checks, deployment output, limitations, and rule review are in [CODEX_REPORT_20260930.md](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/CODEX_REPORT_20260930.md). Reasonix delegation was blocked before making edits, so I completed and verified the work locally.

