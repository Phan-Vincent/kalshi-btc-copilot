# Deployment proposal — FOR APPROVAL, NOTHING DEPLOYED

**Updated 2026-09-28:** Studies 001 and 002 are retired, and the host is the Acer (`homelab`). **Nothing in this proposal enables trading advice.** Advice stays disabled until Study 005 passes.

## Done — Studies 001 and 002 retired (your decision, 2026-09-28)

- **Study 002:** durable stop latch set (`supervise.py inspect` → `PAUSED / explicit_stop_latch`). The Codex heartbeat file `study-002-evidence-only-supervision` was set to PAUSED. **Please confirm it shows as paused in the Codex app**, since the app may cache automation state.
- **Study 001:** the copilot has been stopped since Sept 27 and its heartbeat was already paused. It stays stopped.
- **Records:** `~/Documents/Codex/2026-09-26/new-chat/outputs/STUDY_RETIREMENT_2026-09-28.md`, plus a new section in that workspace's `AGENTS.md` telling future Codex sessions not to restart either study.
- **Nothing lost or opened:** no database, journal or outcome was opened or deleted. Pre-change copies of `AGENTS.md` and the automation file are saved in this folder.
- **Effect on the calendar:** both were retired before their holdouts began, so no protected cohort blocks an earlier Study 005. The Dec 14 date existed only because of Study 002. Study 005 can start as soon as it is approved.

## Decision B — send the Kalshi fee request (the critical path)

Use `KALSHI_FEE_REQUEST_DRAFT_NOT_SENT.md`. Only you can send it, from your Kalshi support channel. A written confirmation of $0.0001 precision is the one outcome that makes the profitability study testable. If Kalshi says $0.01, stop the study rather than weaken the bound.

## Decision C — Study 005 evidence-only release on the Acer

| Item | Proposed value |
|---|---|
| **Release** | `5.3.0-readiness-work-in-progress`. It becomes a sealed release after the Kalshi answer, the protocol revision and a final re-review. |
| **Working manifest** | `candidate/release_manifest.json` (26 files); hashes below |
| **Host** | Acer `homelab` (Ubuntu 26.04, Python 3.14). Checked 2026-09-28: up 40 days, **0 suspends since boot**, on AC power, NTP-synced, reaches Kalshi in ~0.5 s, 570 GB free, systemd linger enabled. The full candidate suite passes there. |
| **Destination** | `~/btc-study/releases/<version>/` on the Acer, with `~/btc-study/current` pointing at it; runtime in `current/runtime/`; state in `~/btc-study/state/`. All owner-only. |
| **Supervision** | systemd user timers, **no AI in the loop**: attestation every 4 h, health check every 5 min, Telegram alerts plus a daily 9 am PT "alive" message (`acer/README.md`) |
| **Storage** | 128 GiB quota plus a 16 GiB free-space reserve (570 GB available) |
| **Network access** | GET only. The collector reads public KXBTC15M market data plus `/cfbenchmarks/values`. Attestation reads `/series`, `/series/fee_changes`, `/account/limits`, `/account/endpoint_costs` and `/exchange/status`. No portfolio or order routes. |
| **Credentials on the Acer** | The read-only Kalshi key and config, plus a Telegram bot token, under `~/.config/btc-study/` (chmod 600). **You install them** (`acer/copy_readonly_key.sh`, plus the Telegram step in the README). |
| **Activation gate** | `release_gate.py` re-checks every 15-second cycle: no `QA_ONLY`; a protocol that permits activation; approval bound to this bundle path, host, manifest hash and study; approval dated before start; now inside the window; attestation under 24 h old. |
| **Collection window** | To be set at the freeze. The protocol still says Dec 14 → Feb 22, but with Studies 001/002 retired, it can be brought forward to the first 00:00 UTC after approval. The 7 + 7 + 56 days and every threshold stay unchanged. |
| **Stop authority** | You: `acer/install.sh rollback`. Nothing restarts automatically after a halt or unclean exit. |

**Activation prerequisites, all of which must hold:**
1. Kalshi fee answer received, and the reviewed protocol revision adopted (or the study is stopped).
2. Release frozen: new dates, `activation_allowed: true` in a newly sealed protocol, `QA_ONLY` removed, hashes re-bound, and a final Reasonix re-review of the changes made since the last one.
3. Credentials installed on the Acer, `./install.sh stage` run, Telegram test received, and the first attestation passing.
4. Your written approval, before the start date, stored as `activation_approval.json` on the Acer. Then `./install.sh activate`.

**Rollback:** `./install.sh rollback` stops and disables every study unit and keeps all evidence. Production, the Mac and the retired studies are unaffected.

## Risk engine (not deployed)

`binary_risk.py` schema 2 enforces your limits:
- 5% of verified total account value per trade, hard ceiling $25;
- open-plus-pending exposure measured as maximum loss and capped at that same amount;
- $30 daily on the Pacific calendar day;
- one position at a time, with adding and averaging disabled.

It runs only as an offline check. Wiring it to live advice comes after validation.

## Hashes (working candidate, 2026-09-28, not a release seal)

```text
cc33084da352b6fff977cfcbbcc5dae471980de5fd3a8d535d5dd2c89fd3c54e  release_manifest.json (26 files)
3694ccbd1ee8bb806bc0b83cd3c705b4b1dcff57f950c7f79165b42006a56749  release_gate.py
f21330175f747e15ed209818cd9f61b64c13217e92625d3ae55cc42ed06a2fdc  acer_ops.py
e12472d877206c2d23bf8c137222b014f033cda98c1ee6686258745c3a567060  evidence_runner.py
201f6ea02bb056a05875419e890e2f14b3098a5adc4469cf869a2aa7617ab8eb  binary_risk.py
631a21cd290d82bf717289a841574f818337e8996843899f353a418572bf48df  risk_limits.json
532442b9fa0c360937b6dc364034ef17d3d093895be441703cc1dec3243fea36  fee_bound_proposal.py
70cec8f38f227f7cf53397f1ca3ec801d60075345df3bb47efc9b8061327173b  coherent_runtime.py
315573b32cd690e7b5a1e920eb45bf246306459cd697fa5ae8130e6f1bde0ac1  btc_copilot.py
bd2556922c4b086cdefff700d8abd2d60e6acc88e5f6778f594443e25d789850  study_policy.py
b34ce1a40395e3110e6140fb213c450c02f66433ba55b42141a3a4ac4832f6d6  readiness_protocol.json (unchanged from Codex)
ebcc9ae9f2ad8b069fd918acdf25f9561e12841287d5a82de8c3a0d72a8b4c94  operating_limits.json (unchanged)
```

Verify with: `python3 -B qa/readiness_release/run_checks.py` (expected: `passed: true`, 299 methods, 1 skip).
