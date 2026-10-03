# Offline QA remediation candidate

This is an isolated candidate based on study 002 release 4.2. It is not the original runtime, the sealed study 002 bundle, an approved study, or an adopted release. Model identity is 5.1.0-offline-acceptance-candidate plus source/settings hash. The unchanged study 002 protocol is retained only for synthetic compatibility tests; its dates and approvals do not authorize this candidate.

The sealed source bundle had a future evidence-only approval at audit time. This candidate has no copied approval, credentials, account state, database, runtime, scheduler, or collector activation. `btc_copilot.py` refuses launch unconditionally; `study_policy.launch_guard` also refuses when QA_ONLY is present. Public outputs remain NO TRADE and all position advice remains disabled. UP/DOWN shadow outputs are hypothetical binary-contract research, not leveraged BTC orders.

Do not replace production files with this directory. No collector, trading, deployment, new study or migration is authorized. Adoption requires independent review and a separately approved prospective protocol/version after decisions about the preserved sealed study.

From the Investing workspace root, run `python3 -B qa_btc_2026-09-27/run_remediation.py`. Tests use public fixtures and synthetic data only. See ../../REMEDIATION_REPORT.md for findings and evidence (path without space: ../../REMEDIATION_REPORT.md).

Acceptance follow-up: persisted prior observations remain unconfirmed until audit, study and public-output stages complete. Study record clocks are checked in a serialized transaction. See ../../ACCEPTANCE_REPORT.md for fault-injection evidence, Reasonix limits and remaining adoption blockers.
