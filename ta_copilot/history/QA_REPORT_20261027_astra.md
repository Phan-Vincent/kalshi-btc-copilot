# Astra QA: live BRTI + Kalshi WebSocket (2026-10-27)

Worker: school ChatGPT, Work mode, GPT-6 Astra, High effort (confirmed on screen before submission).
Chat: https://chatgpt.com/c/6ae04004-6158-83e8-93fd-2d86eb02aba1 (worked 4 m 8 s). Input: QA brief, diff 75c5b10..06f9592, kalshi_ws.py, ta_copilot.py, test_live_brti_20261027.py.
Advisory worker output, verbatim below; supervisor verification is in the commit that follows.

---

Read all five attachments. **Five high-severity defects and one medium-severity regression reproduced in targeted offline checks.** The full supplied suite requires fixtures and supporting modules that were not attached.

**1. Unbridged WebSocket trades become trusted REST coverage after a gap**

- **Severity:** high
- **Where:** `ta_copilot.py`, `handle_kalshi_frame` (1001–1006), `poll_market_context` (1665–1689), `fetch_trades`, `merge_tape`.
- **Failure scenario:** REST covers seconds 1000–1050. A subscription starts at 1090 and receives a trade at 1095, leaving 1050–1090 unfilled. A sequence gap clears `ws_pending_ids` but retains that trade. The next REST page overlaps the 1095 trade and stops before fetching the missing interval. `merge_tape` preserves coverage from 1000. Reproduction omitted a trade at 1075 while reporting **`fresh`, `complete_window`, and `complete_60s` all true**.
- **Evidence:**
  ```python
  self.ws_pending_ids.clear()
  ```
  Subsequently, the REST baseline includes every retained row:
  ```python
  if trade_id not in self.ws_pending_ids
  ```
  Paging accepts any held-ID overlap:
  ```python
  any(trade.get("trade_id") in held_ids for trade in page ...)
  ```
- **Confidence:** certain.
- **Minimal fix:** Preserve the distinction between REST-verified coverage and unbridged WebSocket rows across gaps and reconnects. Unbridged rows must never establish REST paging overlap or advance its trusted reach.

**2. A REST response finishing after subscription start falsely proves a bridge**

- **Severity:** high
- **Where:** `ta_copilot.py`, `poll_market_context` (1644–1649, 1675–1689).
- **Failure scenario:** REST takes its snapshot at 1100.0. A trade occurs at 1100.2, before the WebSocket subscription starts at 1100.5. The older REST response arrives at 1101.0. Its completion timestamp satisfies the bridge condition, so the next context poll stops REST and marks the tape complete despite missing the 1100.2 trade.
- **Evidence:**
  ```python
  raw, full = fetch_trades(ticker, opened, reach, held_ids)
  fetched_at = time.time()
  ...
  self.rest_tape_at = fetched_at
  ```
  The takeover condition uses:
  ```python
  self.kalshi_tape["from"] <= start and self.rest_tape_at >= start
  ```
- **Confidence:** certain.
- **Minimal fix:** Establish the bridge using a REST request initiated after the acknowledged subscription start, with the same subscription still continuous when the response is committed. Response arrival time cannot establish snapshot coverage.

**3. Sequence gaps carried by control acknowledgements leave invalid data marked fresh**

- **Severity:** high
- **Where:** `ta_copilot.py`, `handle_kalshi_frame` (964–976, 992–997).
- **Failure scenario:** A book or trade subscription has processed sequence 10; sequence 11 is lost; an `ok` acknowledgement arrives at sequence 12. The acknowledgement is accepted without invalidating either stream. Continuing BRTI frames refresh socket activity and tape coverage. Until another book/trade frame exposes the gap, the incomplete book remains usable and the tape remains complete. Reproduction showed **book age zero and all tape completeness flags true** ten seconds later.
- **Evidence:**
  ```python
  if self.ws_books.seq.get(sid) == seq - 1:
      self.ws_books.seq[sid] = seq
  if self.ws_trade_seq.get(sid) == seq - 1:
      self.ws_trade_seq[sid] = seq
  ```
  Neither failed comparison triggers recovery; execution proceeds to:
  ```python
  self.kalshi_last_at = received_at
  ```
- **Confidence:** certain.
- **Minimal fix:** Apply sequence-gap validation to numbered control frames as well as data frames. A mismatched acknowledgement must invalidate the affected stream and trigger its existing fallback/resubscription path.

**4. The first trade after rollover relabels the previous market’s cached book**

- **Severity:** high
- **Where:** `ta_copilot.py`, `handle_kalshi_frame` (1023–1028), `state` (1846–1855).
- **Failure scenario:** The new market is promoted while the cached book and mid-price path still belong to the old market. A new-market trade arrives before the next context poll, and its book snapshot is not yet available. The trade handler changes `context_ticker` without clearing the book or path. `state()` consequently displays the old market’s quotes under the new ticker. Reproduction displayed the old 40¢/50¢ YES quotes with an age of 0.8 seconds.
- **Evidence:**
  ```python
  self.context_ticker = ticker
  self.kalshi_tape = None
  self.tape_source = "rest"
  ```
  The retained book is attributed using:
  ```python
  book_ticker = market["ticker"] if ws_view else context_ticker
  ```
- **Confidence:** certain.
- **Minimal fix:** Clear the cached book, its timestamp/source, and `yes_mid_path` whenever the trade handler changes the context ticker, consistently with the context poll’s rollover reset.

**5. BRTI changes the meaning of the existing logged `sigma_1m_usd` field**

- **Severity:** high
- **Where:** `ta_copilot.py`, `window_distance` (237–280), `minute_record` (2028).
- **Failure scenario:** With composite 100 and realized sigma 0.01, the existing minute field is 1.0. Supplying fresh BRTI of 110 changes that same logged field to 1.1, although the composite and candles are unchanged. Historical rows therefore mix composite-based and BRTI-based values under the existing field name, violating the required additive-only research change.
- **Evidence:**
  ```python
  reference_price = brti if use_brti else composite
  ...
  sigma_usd = sigma_1m * reference if sigma_1m is not None else None
  ```
  The logger still writes:
  ```python
  "sigma_1m_usd": window.get("sigma_1m_usd")
  ```
- **Confidence:** certain.
- **Minimal fix:** Calculate the existing logged sigma using the original composite-based projection independently of the displayed BRTI calculation.

**6. One-second sampling truncates the mid-price history in keyless mode**

- **Severity:** medium
- **Where:** `ta_copilot.py`, `Feed.__init__` (849), `sample_composite` (1340–1347).
- **Failure scenario:** The new sampler appends a mid-price point every second, including when using REST without credentials. The unchanged 460-point deque now retains approximately 7 minutes 40 seconds instead of the full 15-minute window supported by the previous two-second cadence. Reproduction over seconds 1000–1899 retained only 1440–1899.
- **Evidence:**
  ```python
  self.yes_mid_path = deque(maxlen=460)
  ```
  The new sampler appends without restricting this to WebSocket operation:
  ```python
  if mid is not None:
      self.yes_mid_path.append({"time": int(at), "value": mid})
  ```
- **Confidence:** certain.
- **Minimal fix:** Preserve the original keyless sampling cadence and retain enough points for the full window when sampling WebSocket mids every second.

**Checked and found correct**

- Contiguous numbered `ok` acknowledgements advance sequence tracking without causing false gaps.
- Gaps on order-book **data** frames invalidate the book; an empty snapshot clears its levels.
- Settlement averages use exactly `close−60 … close−1`; missing seconds prevent a complete average.
- Backfill preserves already-held values, and close-row composite and BRTI means are calculated independently.
- Scope validation rejects empty scopes and explicit write scopes.
- The supplied diff does not change PRIMARY’s implementation. Its pinned digest could not be independently recomputed without the full `tally_report.py`.