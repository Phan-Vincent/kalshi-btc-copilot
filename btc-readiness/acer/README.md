# BTC study on the Acer (homelab): runbook

Everything here runs without Codex or any other AI. Three systemd **user** timers run on the Acer; linger is already enabled, so they run without anyone logged in.

| Unit | What it does | When |
|---|---|---|
| `btc-study-attest.timer` | GET-only check of the KXBTC15M fee type/multiplier, the fee-change feed, your read budget/costs, and the clock (NTP sync plus offset vs Kalshi). It renews `~/btc-study/state/attestation.json` **only if every check passes**. | Every 4 h and at boot |
| `btc-study-watch.timer` | Health check; sends a Telegram message on any problem, on recovery, repeats CRIT every 6 h, and sends a daily "alive" summary at 9 am Pacific | Every 5 min |
| `btc-study-collector.timer` → `.service` | The evidence-only collector (no trading, no advice) | Starts once at the protocol's start time, **only after `activate`** |

The collector re-checks approval and attestation every 15-second cycle. If renewals stop (Kalshi down, fee change, clock drift), it refuses to collect after 24 h and you get a Telegram CRIT. **If you get no daily 9 am message, something is wrong.** Silence is itself the alarm.

## One-time setup (you do these; they involve credentials)

**1. Telegram alert bot (about 2 minutes).**
- In Telegram, message **@BotFather** → `/newbot` → name it e.g. *BTC study alerts* and copy the token.
- Send your new bot any message, then on the Acer run:
  `curl -s https://api.telegram.org/bot<TOKEN>/getUpdates`
  and note the `"chat":{"id":…}` number.
- Create the config on the Acer. The `read -s` prompts keep the token out of your shell history:

```bash
mkdir -p ~/.config/btc-study && chmod 700 ~/.config/btc-study && read -rsp "Bot token: " T && echo && read -rp "Chat id: " C && printf '{"bot_token": "%s", "chat_id": "%s"}\n' "$T" "$C" > ~/.config/btc-study/telegram.json && chmod 600 ~/.config/btc-study/telegram.json && unset T C
```

**2. Read-only Kalshi key on the Acer.** Run this on the Mac. It copies the existing read-only key and writes the Acer config without printing either:

```bash
./copy_readonly_key.sh
```

## Deploy (after setup)

From this folder, on the Mac:

```bash
./install.sh stage
```

`stage` copies the release to `~/btc-study/releases/<version>` (with `~/btc-study/current` pointing at it), installs the units, starts **only** the attest and watch timers, runs one attestation, and prints status. Then test Telegram:

```bash
ssh acer 'python3 ~/btc-study/current/acer_ops.py send-test'
```

`activate` enables the collector timer. It refuses while the release is still the QA candidate (a `QA_ONLY` marker, or a protocol with `activation_allowed=false`). A frozen, approved release must replace it first.

## Everyday

- Health, anytime: `./install.sh status`, or `ssh acer 'python3 ~/btc-study/current/acer_ops.py status'`
- Logs: `ssh acer 'journalctl --user -u "btc-study-*" --since today'`
- Stop everything, keeping all evidence: `./install.sh rollback`

## What a Telegram alert means

| Message | Meaning | What to do |
|---|---|---|
| `Collector service is failed/inactive` | The process exited. It never restarts itself, by design. | Check the logs. An unclean exit needs review before any restart. |
| `Runtime HALTED: …` | A clock step, storage limit or integrity problem | Don't delete markers. Ask for a review. |
| `Fee attestation … renewal is failing` | The 4-hourly check hasn't passed for 20 h | Look at `~/btc-study/state/attest_log.jsonl` for the reason (a fee change, a clock problem, Kalshi being down). |
| `Fee attestation expired` | Over 24 h. The collector refuses to collect. | Same as above. Collection resumes on the next passing check. |
| `Missed N of 96 checkpoint slots` | Coverage gap in the last 24 h | Gaps count against the study and can't be backfilled. |

## Safety properties (tested)

- Kalshi access is GET-only. The collector's client blocks portfolio, order and account routes; attestation reads only series, fee-change, limits, costs and exchange-status routes.
- The status reader opens the evidence database **read-only** and queries only timing and runtime tables. A test fails if it ever touches markets, outcomes, signals or checkpoints.
- Credentials and the attestation must be `chmod 600`; group- or world-readable files are refused.
- The whole candidate test suite passes on the Acer (Linux) as well as the Mac.
