The audit identifies real issues, but neither P0 rating is justified. The most consequential findings are stale BRTI being treated as fresh within a cache bucket and a narrow trade-gap recovery race.

Reviewed `master` at `13bad9c`, without delegation or repository edits. The suite ran **367 tests: OK, one skipped**. The tree already contained untracked `history/codex_brief_audit_eval_20261027.md`; I left it untouched. No browser or live-service testing was performed; I accept the supplied live facts.

The reproductions below used this common setup:

```python
from unittest.mock import patch
import ta_copilot as ta
from test_fix_20261002 import ready_feed
from test_live_brti_20261027 import FakeSocket

def feed(now=1100):
    f = ready_feed(now)
    f.market = {"ticker": "T", "open_time": ta.iso(1000),
                "close_time": ta.iso(1900), "floor_strike": 100}
    f.market_at = now
    f.context_ticker = "T"
    return f
```

1. **Post-close flicker — confirmed conditionally; P2, not P0.**

   [ta_copilot.py:1837–1839](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1837) explicitly retains the window when:

   > `kalshi_configured and now < parse_ts(market["close_time"]) + 30`

   Reproduction:

   ```python
   f = feed()
   f.kalshi_key, f.kalshi_scoped = object(), True
   for now in (1900.1, 1929.9, 1930.0):
       with patch.object(ta.time, "time", return_value=now):
           s = f.state("1m")
           print(now, bool(s["window"]), s["previous_settlement_average"])
   ```

   Output:

   ```text
   1900.1 True None
   1929.9 True None
   1930.0 False None
   ```

   The DOM behavior is concrete: each accepted SSE snapshot calls `render(s)` ([app.js:484–489](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/app.js:484)), restoring the expired ticker, target, quotes and other window content. Within 250 ms, `tick()` sees zero remaining time and renders `window: null, kalshi_stale: true` ([app.js:437–450](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/app.js:437)), clearing those fields. **`render()` also assigns `last = s`** at line 134, so subsequent ticks leave it cleared until the next SSE push. With one-second pushes, that repeats once per second—not four times per second.

   Badge alternation depends on the incoming snapshot’s other freshness flags; an already-stale snapshot may keep the same warning. The condition ends when the next market is promoted or the old window reaches close+30. With healthy prefetch/promotion, it can be brief; 30 seconds is the maximum, not the normal duration. It displays an expired market temporarily, but does not corrupt settlement logs.

   **Minimal fix/test:** expose only open markets as `window`; expose the just-closed market’s average through `previous_settlement_average`, including when it is still `self.market`. Test close−ε, close, promotion and close+30 in configured and keyless modes. Simply removing the grace would lose the average before promotion because the existing previous-average branch reads only `previous_market`.

2. **Control-frame trade-gap corruption — partly true; P1 for the reproduced race, not P0.**

   The audit correctly identifies opposing transitions:

   - [ta_copilot.py:985](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:985): `self._trade_gap(sid)`
   - [ta_copilot.py:1013–1014](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1013): `setdefault(ticker, received_at)` followed by `ws_trade_contiguous.add(ticker)`.

   Reproduction:

   ```python
   for socket in (None, FakeSocket()):
       f = feed()
       f.kalshi_connected = True
       f.ws_subs.sids["trade"] = 5
       f.ws_subs.tickers[5] = {"T"}
       f.ws_trade_seq[5] = 10
       f.ws_trade_start["T"] = 990
       f.ws_trade_contiguous.add("T")
       c = f.ws_subs.command("update_subscription",
           {"sid": 5, "action": "add_markets", "market_tickers": ["U"]}, 1100)
       print("before", f.ws_trade_contiguous, f.ws_trade_start)
       f.handle_kalshi_frame(
           {"type": "ok", "sid": 5, "seq": 12, "id": c["id"]}, 1101, socket)
       print("after", f.ws_trade_contiguous, f.ws_trade_start)
   ```

   Observed:

   ```text
   no socket: {'T'} -> {'T', 'U'}; T start remains 990
   socket:    {'T'} -> set();     T start remains 990
   ```

   **The live path matters:** line 1072 immediately resubscribes when a socket is supplied; `_ws_resubscribe()` clears continuity at line 945. The new trade subscription acknowledgment **overwrites** subscribed tickers’ start timestamps at lines 1007–1008. In my reproduction, `T` became `start=1102`, preventing the claimed persistent pre-gap coverage bypass.

   There is nevertheless a real race: the handler releases `self.lock` before invoking resubscription. I injected `poll_market_context()` at that boundary, with an existing websocket tape and pre-open start. It bypassed REST using [lines 1657–1661](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1657), producing:

   ```text
   interleaved poll:       tape_source=websocket, complete_window=True
   after resubscription:  contiguous=set(), complete_window=True
   ```

   Resubscription clears continuity but does not undo that restored tape source. Thus a genuinely gapped tape can temporarily be presented as complete. This requires an actual sequence gap plus unlucky thread scheduling; ordinary contiguous `ok` frames do not trigger it. Your gap-free live run is consistent with this finding.

   **Minimal fix/test:** invalidate continuity/start coverage atomically and prevent update acknowledgments from restoring continuity after a detected gap; require a new subscription/REST bridge. Test a **registered** update acknowledgment with a gap, a supplied socket, and an interleaved context poll. Preserve ordinary `ok` sequence consumption.

3. **Frozen BRTI / previous settlement cache — partly true; P1 for BRTI.**

   [ta_copilot.py:2571](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:2571) makes only a shallow copy: `state = dict(prior[1])`. Lines 2576–2603 advance selected ages, but never BRTI freshness or its dependent distance calculation.

   ```python
   f = feed(1060.1)
   f.kalshi_key, f.kalshi_scoped = object(), True
   f.brti_value, f.brti_source_at = 110., 1058.2
   ta.STATE_CACHE.clear()
   with patch.object(ta, "FEED", f):
       with patch.object(ta.time, "time", return_value=1060.1):
           a = ta.cached_state("1m")
       with patch.object(ta.time, "time", return_value=1060.9):
           b, actual = ta.cached_state("1m"), f.state("1m")
       for s in (b, actual):
           print(s["brti"]["age"], s["brti"]["fresh"],
                 s["window"]["settlement_basis"], s["window"]["distance"],
                 ta.minute_record(s, 1060.9)["brti"])
       print(a["brti"] is b["brti"])
   ```

   Output:

   ```text
   cached: 1.9 True  BRTI 10.0 110.0
   actual: 2.7 False proxy 0.0 None
   shared object: True
   ```

   This can show a stale BRTI-based distance as current and log stale BRTI as fresh: [line 2044](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:2044) gates the logged value on `fresh`. Exposure is under one second, principally when updates stop or are delayed across the two-second threshold and another caller populated the bucket earlier.

   **The claimed mutation is false:** the BRTI dictionary is shared, but `cached_state()` does not mutate it. A fix that mutates it without copying would introduce that problem.

   `previous_settlement_average` has no age field. Its historical value needs no aging; its visibility expires after 30 seconds. I reproduced delayed expiry with a synthetic fractional close, but normal integer-second closes align close+30 with a new cache bucket, which rebuilds state. That part is not an established real-world defect.

   **Minimal fix/test:** copy and age BRTI, and rebuild or invalidate its dependent window calculation when freshness expires. Test repeated same-bucket calls across age=2, unchanged cached originals, logged BRTI suppression, and previous-average expiry at an aligned close+30.

4. **One-shot BRTI backfill — confirmed availability limitation; P2, not P1.**

   The sole production call is [ta_copilot.py:1128](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1128), before the receive loop. `_close_proxy()` correctly uses:

   > `if len(brti_held) == 60 else None`

   at [lines 1401–1402](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1401).

   ```python
   f = feed()
   f.brti_seconds = {s: 100. for s in range(1840, 1900) if s != 1850}
   before = f._close_proxy(1900, 1900.1)
   f.kalshi_signed_get = lambda *a, **k: {
       "data": {"payload": [{"time": 1850000, "value": "100"}]}}
   f.backfill_brti(1901)
   after = f._close_proxy(1900, 1901)
   print([(r["brti_samples"], r["brti_avg_60s"]) for r in (before, after)])
   ```

   Output: `[(59, None), (60, 100.0)]`. The original row remains `(59, None)`.

   A missing second must remain absent from **both eligible sources**: the parser accepts whole-second timestamps from the one-second and 5 Hz channels ([kalshi_ws.py:119–137](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/kalshi_ws.py:119)). Losing one channel’s frame need not lose the second. The synthetic response proves repair capability, not actual endpoint availability or publication latency.

   Impact is lost coverage, **not an incorrect average logged as complete**. The supplied complete live windows and gap-free run offer no evidence this is frequent.

   **Minimal fix/test:** perform bounded, throttled backfill off the receive thread when relevant seconds are missing, with explicit close-finalization behavior. Test successful/unavailable backfill and retain `brti_avg_60s=None` unless all exact 60 seconds exist. Merely adding periodic backfill does not repair an already-written close row.

5. **Deleted books / captured ticker growth — confirmed; P2, not P1.**

   [kalshi_ws.py:264](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/kalshi_ws.py:264) removes subscription tickers using `difference_update(...)`, but does not prune books. Snapshots store them at lines 183–187; only sid invalidation removes them at lines 203–208.

   ```python
   f = feed()
   f.ws_subs.sids["orderbook_delta"] = 4
   f.ws_subs.tickers[4] = {"OLD", "NEW"}
   f.handle_kalshi_frame({"type": "orderbook_snapshot", "sid": 4, "seq": 1,
       "msg": {"market_ticker": "OLD", "yes_dollars_fp": [["0.4", "1"]]}}, 1900)
   c = f.ws_subs.command("update_subscription",
       {"sid": 4, "action": "delete_markets", "market_tickers": ["OLD"]}, 1900)
   f.handle_kalshi_frame({"type": "ok", "sid": 4, "seq": 2, "id": c["id"]}, 1901)
   print(f.ws_subs.tickers[4], f.ws_books.books.keys(), f.ws_books.synced)
   ```

   Output: subscription `{'NEW'}`, but books and synced still contain `(4, 'OLD')`. A subsequent contiguous OLD snapshot also retains/recreates it.

   `captured_tickers` is initialized at line 822 and only added to at [line 1428](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1428); a fake successful logger accepted 100 distinct opening captures and left 100 entries.

   Growth is roughly 96 tickers/day under continuous windows. Books reset on reconnection; captured tickers persist for the process lifetime. Neither reproduction selected an old book for a different current ticker. This is long-running memory hygiene, without demonstrated wrong numbers.

   **Minimal fix/test:** prune deleted ticker payloads and bound capture deduplication retention. Crucially, continue consuming sid sequences for late old-market frames before ignoring their payloads; otherwise the cleanup creates false gaps. Test delete acknowledgment → late OLD snapshot → valid NEW update, plus capture deduplication within its retention period.

The **settlement progress bar and five-level ladder** fit the descriptive-only rule and are optional P2 usability work. Coverage is already shown numerically ([app.js:296–308](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/app.js:296)); a ladder adds more layout and freshness-handling cost. Neither merits priority over correctness.

The **family-vote opening hit rate and BRTI-distance calibration** fit as clearly labeled exploratory retrospective statistics. Family opening correlation already exists ([tally_report.py:419–437](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/tally_report.py:419)); calibration currently uses composite distance ([lines 733–748](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/tally_report.py:733)). Keep BRTI/composite cohorts distinct, preserve existing log meanings, and leave the pinned PRIMARY function/digest unchanged. These are research additions, not correctness fixes.

**Worth fixing now, ranked:**

1. Trade-gap recovery race: prevent falsely complete tape coverage.
2. Cached BRTI freshness and dependent distance/logging.
3. Post-close window/previous-average consistency.
4. Deleted-book and capture-set retention.
5. Bounded backfill and close-finalization handling when missing coverage is observed.

**Concrete omission:** a delayed final-second frame can permanently reduce close-log coverage even without a dropped tick. I captured at close+0.01 with 59 seconds, then delivered the missing close−1 frame at close+0.1: held coverage became 60, while the captured row remained `59/None`. The sampler captures once ([ta_copilot.py:1339–1341](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:1339)); `accept_close()` rejects subsequent rows for that ticker ([lines 2415–2419](/Users/you/Documents/kalshi-btc-readiness/ta_copilot/ta_copilot.py:2415)). This is another coverage-loss case, not false settlement accuracy, and periodic backfill alone would not solve it.

**Checked and correct:** contiguous control acknowledgments advance sequence numbers; contiguous post-close snapshots do not themselves imply gaps; ordinary resubscription resets subscribed trade start times; close averages use exactly close−60 through close−1 and require all 60 values; keyless behavior and composite-based logged sigma remain covered; PRIMARY’s pinned digest passes. None of the proposed fixes requires changing existing log-field meanings, weakening 60-second completeness, or altering PRIMARY.

