# Study 005 revision 2 QA candidate

This directory is the isolated `5.5.0-study005r2` working copy. `QA_ONLY` is present. The protocol remains `PROPOSED_NOT_APPROVED`, and activation, advice and execution are disabled. The normal `--collect` path refuses this copy. No approval, credentials, study runtime or live evidence are bundled.

The explicit `evidence_runner.py --shakedown` path is a GET-only QA collector. On the Acer, it requires the existing read-only credential configuration and a fresh fee/rate/clock attestation, writes only to `runtime_shakedown/`, and uses a shifted QA protocol so pre-study checkpoints can be measured. Its records are not prospective study evidence. No shakedown has been started by this offline implementation task.

After an authorized live run of at least 72 hours, `release_tool.py shakedown-summary` reads only timing, checkpoint and reason tables and writes `shakedown_summary.json`. `release_tool.py seal` requires a recent passing summary plus fresh fee evidence. Sealing, staging and activation are later owner-controlled steps. See `../CHANGES.md` and the proposal protocol for the gates and unresolved operational checks.
