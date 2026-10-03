# Kalshi BTC 15-minute copilot: research tools and a sealed evidence study

A personal research project around Kalshi's **KXBTC15M** markets: binary contracts on whether Bitcoin's 60-second average price (CF Benchmarks BRTI) at the end of a 15-minute window finishes above a target set when the window opens.

The repository has two parts:

1. **`ta_copilot/`**: a live, read-only technical-analysis dashboard for the current KXBTC15M window, with a research log that measures whether its indicators predict anything.
2. **`btc-readiness/`**: an evidence-only study framework that asks whether a model-driven "copilot" for these markets survives fees and holds up out of sample. It is built around a sealed, tamper-evident release that runs unattended on a small Linux server.

> **Not financial advice. Not affiliated with Kalshi or CF Benchmarks.** Nothing here places, prepares or simulates orders against an exchange. All exchange access is unauthenticated public market data, or GET-only account reads in the study's fee check. The dashboard's indicators have **not** been shown to predict outcomes (see [Findings so far](#findings-so-far)).

This is a **redacted public copy** of a private working repository; see [What was redacted](#what-was-redacted).

---

## Contents

| Path | What it is |
|---|---|
| `ta_copilot/` | The live TA dashboard: server, page, research log and report, tests, deploy script |
| `ta_copilot/history/` | Agent task briefs, reports, an adversarial QA report, and a retired paper-trading experiment |
| `btc-readiness/qa/study005r1_release/` | **The current study release**: `candidate/` (working copy), `sealed/` (the frozen 5.4.1 release), `release_tool.py` (seal, stage, post-study report), `tests/`, Linux deploy scripts in `acer/` |
| `btc-readiness/qa/readiness_release/` | The previous candidate, plus the protocol-revision spec under `proposals/study005_r1/` |
| `btc-readiness/deliverables/` | Human-readable reports: the readiness report, deployment proposal, fee-evidence and fee-bound proposal, review findings |
| `btc-readiness/fee_evidence_*.py` | Read-only scripts that classify an account's fee rounding from its own fill history |
| `btc-readiness/review_bundle/`, `reasonix_out/`, `archive/` | Sanitized bundles given to independent reviewers, and their outputs |
| `btc-readiness/qa/` (other folders) | Earlier candidates, baselines and QA evidence kept for the record (audit trail, not current code) |

---

## Part 1: the TA copilot (`ta_copilot/`)

A single-file, standard-library Python server (`ta_copilot.py`) and a static page (`index.html` + `app.js`). It shows the current Kalshi window alongside a Bitcoin price composite and common indicators. It is **descriptive**: it shows no buy/sell calls and no probability of winning.

### What it shows

- **Spot composite**: the median of fresh Coinbase, Kraken, Bitstamp and Gemini last trades, with stale venues and outliers excluded, and a rolling 60-second average. This is a *proxy* for BRTI, not BRTI. Coinbase and Kraken stream over WebSocket when the optional `websockets` package is present, with REST fallback.
- **The Kalshi window**: the target (floor strike), time left, public order-book bids and implied asks, recent public trades, and the YES mid path. It also shows the distance to target in σ under a Brownian settlement-average model, which is again a proxy, not a probability.
- **Charts per timeframe** (1m, 5m, 10m, 15m, 1h): candles with EMA 9/21/50, Bollinger 20/2 and anchored VWAP; RSI 14; MACD 12/26/9; and the current window's cumulative volume delta (CVD) from taker order flow.
- **Indicator tally**: each reading votes bull, bear or neutral.
  - **Legacy count:** eight readings, with a lean shown when one side leads by 2 or more.
  - **Family vote:** five of those readings measure the same thing (trend), so a second view gives one vote each to **trend** (EMA stack, EMA 9/21, EMA 200, MACD, VWAP; 3 of 5 must agree), **momentum** (RSI 14 inside 30–70) and, on 1m, **order flow** (60 s taker imbalance at ±0.20 or beyond). Bollinger %B, Stochastic and stretched RSI are shown as context and never counted.
  - **Track record:** under the lean, a line shows how the 1m lean at window open has scored against settlement, with its Wilson 95% interval.

### Research log and report

The server appends private JSON lines to `state/tally_log.jsonl`:

- **`open`**: the tally for every timeframe, captured at each window's open
- **`minute`**: one row per whole minute inside the window
- **`settled`**: the public result after close

`tally_report.py` reads that log offline; the dashboard never reads it. Its sections are labelled **exploratory**, apart from **one test that was declared before any minute data existed**: the Spearman correlation between the 1m family net score and the composite's move over the next 3 minutes, over the first 800 windows, with a block bootstrap that resamples whole windows. It is evaluated once, on or after a fixed date. The rest of the report covers:

- the legacy hit rates
- the size of the move at settlement against the opening score
- 1- and 3-minute forward changes in price and contract mid
- the tally against the market's own price near open

### Security posture

- No credentials; only unauthenticated GETs to public endpoints.
- The server binds to loopback and is meant to be published privately, for example with Tailscale Serve.
- Every response carries a strict Content-Security-Policy (one pinned, SRI-checked chart script from unpkg; no inline script), `nosniff` and `no-referrer`.
- Server-Sent Events are capped at 12 clients; past that, the page polls `/api/state`.
- One state computation per timeframe per second is shared by all clients.

### Run it

Developed and tested on Python 3.14. Standard library only; `websockets` is optional.

```sh
cd ta_copilot
python3 ta_copilot.py                 # http://127.0.0.1:8770/
python3 -m unittest discover -p 'test_*.py'   # 146 tests, no network
python3 tally_report.py state/tally_log.jsonl
```

`acer/deploy.sh` deploys to a Linux host as a systemd **user** service, with a health-watch timer that sends Telegram alerts. Set `HOST` and the Tailscale Serve port for your machine. Telegram credentials are read on the server from a private config file and are not in this repo.

---

## Part 2: the evidence-only study (`btc-readiness/`)

### The question

Can a model-driven copilot for KXBTC15M be shown to beat **fees and the spread** out of sample, before it is ever allowed to give advice? Until a study says yes, advice stays disabled, and the code has no order-placement path at all.

### Design

- **Pre-registered protocol** (`readiness_protocol.json`):
  - a development week, a validation week, and a 56-day **holdout** that nobody looks at until a fixed release date
  - acceptance thresholds fixed in advance
  - the protocol is frozen when the release is sealed
- **Evidence only.** The collector records public market data, the model's would-be decisions and outcomes. It never acts on them, and the holdout stays locked by the real clock until release.
- **Fee model.** Kalshi's taker fee for this series is quadratic: `0.07 × contracts × price × (1 − price)`, with rounding that depends on the account's balance precision ($0.0001 or $0.01).
  - **Precision from the account itself:** `fee_evidence_refresh.py` classifies the precision from the account's own fill history, GET-only, and a tripwire refuses to seal on stale or contradictory evidence.
  - **A conservative fee bound:** the maximum of the closed form, a per-unit split and a legacy term.
  - **Execution is not machine-verified:** fill realism is left to an independent reviewer and flagged as such in the report.

### Tamper-evident release

- **Sealing:** `release_tool.py seal` writes a manifest that hashes every file. It refuses symlinks and non-regular files, requires an exact file set, and verifies in fresh, isolated interpreters.
- **Launch checks:** `launch.py` uses the standard library only. It verifies every file, and that the manifest is the approved one, **before importing any study code**. Systemd runs it with `python3 -I -B`, after a `sha256sum` pre-check of the launcher itself.
- **Approval:** a signed `activation_approval.json` is required before a protocol deadline. The systemd units are sealed into the release and byte-compared on activation.
- **Fail-closed operation:** staging a new release stops and disables the collector first. A watch timer raises CRIT alerts on verification failure. The read-only Kalshi client blocks portfolio, order and account routes.
- **Post-study report:** `release_tool.py post-study-report` verifies a fresh fee-evidence snapshot byte-for-byte, sends exactly those bytes to the server, and records their SHA-256 in the report.

### Independent review

The release went through five rounds of adversarial review by a separate AI agent (OpenAI Codex), each in a disposable sandboxed copy. Every finding was reproduced and fixed with a regression test before the final "seal" verdict. The history of findings and fixes is in `btc-readiness/qa/study005r1_release/STATE.md`. A second agent (Reasonix / DeepSeek) reviewed fees, risk and operations; see `deliverables/REASONIX_FINDINGS.md`.

### Run the tests

```sh
cd btc-readiness/qa/study005r1_release
python3 -B -m unittest discover -s tests -p 'test_*.py'
cd candidate && python3 -B -m unittest discover -p 'test_*.py'
```

In this public copy, `tests/test_study005r1_release.py` cannot load, because it reads the account's real fee-evidence summary, which is redacted. The other 166 readiness tests and the candidate suites (51 + 19 + 17 + 14) pass.

`run_checks.py` also ends by hash-checking original source files in a local path that only exists on the author's machine, so it errors after the suites run.

---

## Findings so far

These are honest, interim numbers as of 2026-10-03.

- **The indicator tally is a coin flip so far.** Over about 230 windows, the lean at window open matched the settlement side 43–49% of the time across timeframes. Every 95% interval includes 50%, and the opening net score's correlation with the size of the move is indistinguishable from zero. The minute-level, pre-declared test has not been evaluated yet.
- **Costs are the hurdle.** At a 50¢ contract, the taker fee is about 1.75¢ per contract on each side. With a 1–2¢ spread, a round trip costs roughly **5–5.5¢** before any profit. A short-term rule has to move the contract by more than that just to break even.
- **A paper-trading experiment was retired.** An automated paper-scored scalping trigger was tried for about an hour, first as mean reversion, then as momentum. It made 8 paper trades and was net negative after fees (about −5¢ per contract over the first seven), mostly on stops. That's far too few trades to judge the rule, but it's consistent with the cost hurdle above. It was removed; the code and report are in `ta_copilot/history/retired_scalp_20261003/`.
- **The sealed study** is approved and armed. It starts collecting on 2026-10-12, its holdout runs to 2026-12-21, and the report is due 2026-12-23. No result is available yet.

---

## What was redacted

This copy is generated from the private repository by an export script that refuses to finish if any denylisted string survives. Compared with the private repo:

- **Account-derived fee evidence** (`fee_evidence_summary.json`, `fee_evidence_refresh_summary.json`, every copy): replaced with a stub marked `"redacted": true`. These held order and fill counts from the author's account. Raw fills and orders were never committed.
- **Account figures in the prose:** the account balance and the order and fill counts quoted in reports are removed or replaced with `[redacted]` or a qualitative summary ("every decisive order matched $0.0001"). Two reviewer files built entirely on that evidence (`reasonix_out/fee_review.txt`, `prompt_fee.txt`) are stubbed. No order IDs, fills, balances, positions or P&L from the account remain.
- **Kept on purpose:** the study's risk limits (5% of verified account value per trade, capped at $25; $30 a day; one position). They are configuration in `risk_limits.json` and the risk engine, not account data, and the tests depend on them.
- **Personal identifiers:** the server's and laptop's hostnames, the Tailscale tailnet name, local usernames, home-directory paths and the ChatGPT project and Claude scratch-workspace IDs captured in agent logs are replaced with `laptop`, `homelab`, `homelab.example-tailnet.ts.net`, `/Users/you`, `g-p-PROJECT` and `scratch-workspaces/WORKSPACE`.
- **Removed files:** two retired agent-instruction files (they described a local credential setup and contained scheduler session IDs), and a personal backup helper script.
- **Not included:** git history (this copy starts fresh), runtime state and logs, and agent run transcripts.

Because files inside `sealed/` were edited by the redaction, their hashes no longer match `release_manifest.json`. **The sealed releases here will not verify; they are for reading.** Seal your own release from `candidate/` if you want to run the study.

---

## Built with

Planned, written and reviewed with AI coding agents: Claude Code, OpenAI Codex and Reasonix (DeepSeek), under the author's direction. The task briefs and reports in `ta_copilot/history/` show how the work was split and checked.

## License

[MIT](LICENSE). The software is provided as is, without warranty. In particular, nothing here is fit for making trading decisions.
