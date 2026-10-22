# Excusing confirmed home-internet outages in the shakedown gate — NOT APPROVED

Draft on branch `wan-outage-excuse`, 2026-10-21. It has not been deployed: acer's `release_tool.py` and `shakedown_check.py` still match `master`.

## Why

At about 200 polls an hour, the seal gate fails an hour that has 3 collection failures (above 1%). It also fails any missed slot that has no reason logged in its 300–330 s band. The home connection drops for 1–3 minutes most nights (modem/Spectrum side; dashboard `where: modem`). One drop is enough to fail a 72 h run:

- 2026-10-21 10:48:45–10:50:20 and 10:51:32–10:51:48 UTC: two network/TLS failures and one knock-on failure ("Decision completion outside valid observation window"), so the 10:00 hour had 3 failures out of 201 polls.
- The 10:45 slot's band (10:50:00–10:50:30) fell inside the drop. The failures landed at +270 s and +336 s, outside the band, so the miss has no reason.

## Rule

An outage excuses failures and misses only when all of these hold:

1. **It is confirmed.** It appears in the command-center dashboard log (`data/home_internet.json`) with `confirmed: true`. That means the home hosts themselves reported no internet: TCP to 1.1.1.1, 8.8.8.8 and OpenDNS all failed. The MacBook's own view is not enough.
2. **It is short.** At most `WAN_MAX` = 900 s. A longer outage still fails the gate and needs a human decision.
3. **The collector corroborates it.** The collector logged at least one `COLLECTION_FAILED` containing "Network or TLS failure" inside the padded window. A window with no network failure from acer excuses nothing. Kalshi-side errors such as HTTP 503 never open a window.

The padded window runs from 30 s before the outage's start (`WAN_LEAD`) to 60 s after its end (`WAN_TAIL`). The dashboard samples about every 15 s, so the lead covers the unseen start and the tail covers the poll that completes just after reconnect.

Inside a padded window:

- **Failures are taken out of the hourly rate.** The rate becomes `(failed − wan) / (failed − wan + successful)`. Each hour row now also records `failed_during_wan_outage`.
- **Missed slots get a reason.** A missed slot whose band overlaps the window gets the reason `home WAN outage <start>..<end> (<where>)`. It still counts against the 95% coverage gate, so long or frequent outages still fail the run.

## Evidence trail and seal checks (`release_tool.py`)

- `shakedown-summary --wan-outages FILE` takes a copy of the dashboard log, because the summary runs on acer. The summary records `wan_outages`: the log's SHA-256, the three parameters, and every window it used with its reason.
- `check_shakedown` (seal, on the Mac) refuses the summary in any of these cases:
  - a window is malformed, longer than 900 s, or uses different parameters;
  - a missed slot names a WAN outage the summary doesn't list;
  - an hour's rate doesn't match its counts;
  - a WAN-excused count appears with no windows;
  - a listed window is not a confirmed outage in this Mac's dashboard log. `seal --wan-outages` defaults to `~/command-center/data/home_internet.json`.

  Summaries without `wan_outages` are checked exactly as before.

## Monitor (`shakedown_check.py`)

- The monitor applies the same rules from an optional `~/btc-study-qa/wan_outages.json` on acer.
- An alert now lists the excused outages.
- It also reports missed slots with no reason as a problem. Before, the seal gate failed them while the monitor only listed them.
- **Open item:** nothing keeps that file fresh yet. Acer can't reach the Mac, so the Mac has to push it, either from the dashboard when an outage closes or from a launchd `scp`. Without the file, the monitor behaves as before.

## Verification

- `tests/test_shakedown_qa.py` gains `WanOutageTests` (4 tests). They cover:
  - the 2026-10-21 case;
  - outages that are unconfirmed, too long, uncorroborated (HTTP 503) or at a different time;
  - forged or inconsistent summaries;
  - the monitor.

  Setting `WAN_TAIL = 0` makes 2 of them fail. `run_checks.py` passed: 416 methods, 1 skip, 14/14 golden, 24/24 UI. The evidence files it rewrote were reverted.
- A read-only dry run of the draft against acer's live database (65.5 h, 262 slots, 261 enrolled) ran in a temporary directory, which was then deleted:

  | | Hours over 1% | 10:45 miss |
  |---|---|---|
  | Without the log | 10-20 19:00 (9/208), 10-21 10:00 (3/201), 10-21 19:00 (3/208) | no reason |
  | With the log | 10-20 19:00, 10-21 19:00 | `home WAN outage … (modem)` |

  Four windows were corroborated: 10-19 10:26, 10-20 10:42, and 10-21 10:48 and 10:51.

## What this does not fix

The current run still fails. On 10-20 at 19:00 UTC there were 9 × "Cannot verify exactly one active BTC 15-minute contract", and on 10-21 at 19:00 there were 3 × Kalshi HTTP 503. Both are on the exchange side, both came at 19:00–19:03 UTC, and neither is excused by this change.

## Decisions for the owner

1. Approve or change the parameters: lead 30 s, tail 60 s, maximum 900 s.
2. Decide whether the knock-on failure in the tail should be excused at all. The alternative is to excuse only network failures, which would leave 10-21 10:00 at 1/199 and still pass.
3. Choose how the Mac pushes `wan_outages.json` to acer for the monitor.
4. Deploy: copy `release_tool.py` to `~/btc-study-qa/study005r2_release/` and `shakedown_check.py` to `~/btc-study-qa/shakedown_check/` on acer. Neither file is pinned in `source_hashes.json` or the candidate digest.
