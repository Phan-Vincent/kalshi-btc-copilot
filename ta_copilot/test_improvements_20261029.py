"""Offline regressions for the 2026-10-29 hygiene and display batch."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import ta_copilot as ta
import tally_report as report
from test_fix_20261007b import APP_HARNESS
from test_live_brti_20261027 import FakeSocket
from test_proxy_alarm_20261005 import Collector, market

HERE = Path(__file__).resolve().parent


class DeletedBookTests(unittest.TestCase):
    def feed(self):
        feed = ta.Feed()
        feed.ws_subs.sids["orderbook_delta"] = 4
        feed.ws_subs.tickers[4] = {"OLD", "NEW"}
        for seq, ticker in enumerate(("OLD", "NEW"), 1):
            feed.handle_kalshi_frame(self.snapshot(ticker, seq), 1900)
        return feed

    def snapshot(self, ticker, seq):
        return {"type": "orderbook_snapshot", "sid": 4, "seq": seq,
                "msg": {"market_ticker": ticker, "yes_dollars_fp": [["0.4", "1"]]}}

    def delete(self, feed):
        command = feed.ws_subs.command("update_subscription",
            {"sid": 4, "action": "delete_markets", "market_tickers": ["OLD"]}, 1900)
        feed.handle_kalshi_frame({"type": "ok", "sid": 4, "seq": 3, "id": command["id"]}, 1901)

    def test_delete_ack_prunes_old_book_and_preserves_sequence(self):
        feed = self.feed()
        self.delete(feed)
        self.assertEqual(feed.ws_subs.tickers[4], {"NEW"})
        self.assertNotIn((4, "OLD"), feed.ws_books.books)
        self.assertNotIn((4, "OLD"), feed.ws_books.synced)
        self.assertEqual(feed.ws_books.seq[4], 3)

    def test_late_snapshot_and_delta_consume_sequence_without_resubscribe(self):
        feed, socket = self.feed(), FakeSocket()
        self.delete(feed)
        self.assertIsNone(feed.handle_kalshi_frame(self.snapshot("OLD", 4), 1902, socket))
        delta = {"type": "orderbook_delta", "sid": 4, "seq": 5,
                 "msg": {"market_ticker": "OLD", "side": "yes", "price_dollars": "0.4", "delta_fp": "1"}}
        self.assertIsNone(feed.handle_kalshi_frame(delta, 1903, socket))
        self.assertEqual(feed.ws_books.seq[4], 5)
        self.assertIsNone(feed.ws_books.view(4, "OLD"))
        self.assertNotIn((4, "OLD"), feed.ws_books.books)
        delta["seq"], delta["msg"]["market_ticker"] = 6, "NEW"
        self.assertEqual(feed.handle_kalshi_frame(delta, 1904, socket), "delta")
        self.assertEqual(feed.ws_books.view(4, "NEW")["orderbook_fp"]["yes_dollars"], [["0.4", "2"]])
        self.assertEqual(socket.sent, [])

    def test_added_market_snapshot_before_ok_is_kept(self):
        feed = self.feed()
        command = feed.ws_subs.command("update_subscription",
            {"sid": 4, "action": "add_markets", "market_tickers": ["NEXT"]}, 1900)
        self.assertEqual(feed.handle_kalshi_frame(self.snapshot("NEXT", 3), 1901), "snapshot")
        feed.handle_kalshi_frame({"type": "ok", "sid": 4, "seq": 4, "id": command["id"]}, 1901)
        delta = {"type": "orderbook_delta", "sid": 4, "seq": 5,
                 "msg": {"market_ticker": "NEXT", "side": "yes", "price_dollars": "0.4", "delta_fp": "1"}}
        self.assertEqual(feed.handle_kalshi_frame(delta, 1902), "delta")
        self.assertEqual(feed.ws_books.view(4, "NEXT")["orderbook_fp"]["yes_dollars"], [["0.4", "2"]])

    def test_remaining_book_is_untouched(self):
        feed = self.feed()
        before = copy.deepcopy(feed.ws_books.view(4, "NEW"))
        book = feed.ws_books.books[(4, "NEW")]
        self.delete(feed)
        feed.handle_kalshi_frame(self.snapshot("OLD", 4), 1902)
        self.assertEqual(feed.ws_books.view(4, "NEW"), before)
        self.assertIs(feed.ws_books.books[(4, "NEW")], book)

    def test_unacknowledged_delete_does_not_prune(self):
        feed = self.feed()
        command = feed.ws_subs.command("update_subscription",
            {"sid": 4, "action": "delete_markets", "market_tickers": ["OLD"]}, 1900)
        feed.handle_kalshi_frame({"type": "ok", "sid": 4, "seq": 3, "id": command["id"] + 1}, 1901)
        self.assertIsNotNone(feed.ws_books.view(4, "OLD"))


class CaptureRetentionTests(unittest.TestCase):
    def test_capture_retention_is_bounded_and_latest_tickers_still_dedupe(self):
        feed = ta.Feed()
        feed.tally_logger = Collector()
        count = ta.CAPTURED_TICKERS_LIMIT + 25
        markets = [market(90000 + i * 900) for i in range(count)]
        for row in markets:
            feed._capture_open(row, ta.parse_ts(row["open_time"]))
            self.assertLessEqual(len(feed.captured_tickers), ta.CAPTURED_TICKERS_LIMIT)
        for row in markets[-ta.CAPTURED_TICKERS_LIMIT:]:
            feed._capture_open(row, ta.parse_ts(row["open_time"]))
        self.assertEqual(len(feed.tally_logger.rows), count)
        self.assertEqual(list(feed.captured_tickers), [row["ticker"] for row in markets[-ta.CAPTURED_TICKERS_LIMIT:]])


class ReportTests(unittest.TestCase):
    def test_close_finalized_at_is_validated(self):
        row = {"type": "close", "ticker": "T", "close_time": ta.iso(1900), "brti_finalized_at": "bad"}
        self.assertEqual(report.record_problem(row), "bad brti_finalized_at")
        row["brti_finalized_at"] = ta.iso(1901)
        self.assertIsNone(report.record_problem(row))
        row.pop("brti_finalized_at")
        self.assertIsNone(report.record_problem(row))

    def test_family_opening_hits_zero_missing_and_unresolved(self):
        values = {"up_hit": 2, "down_hit": -1, "miss": 1, "zero": 0, "missing": None, "unresolved": -2}
        opened = {ticker: {"timeframes": {"1m": {"family_net": net}}} for ticker, net in values.items()}
        settled = {ticker: {"result": result} for ticker, result in
                   (("up_hit", "yes"), ("down_hit", "no"), ("miss", "no"), ("zero", "yes"), ("missing", "yes"))}
        text = "\n".join(report.family_opening_section(opened, settled))
        low, high = report.wilson95(2, 3)
        self.assertIn("[exploratory] (not pre-declared)", text)
        self.assertIn("1m: up 2, down 2, no lean 1; hit rate 2/3 (66.7%)", text)
        self.assertIn(f"Wilson 95% [{low:.1%}, {high:.1%}]", text)

    def test_family_opening_without_scored_rows(self):
        text = "\n".join(report.family_opening_section({"T": {"timeframes": {"1m": {"family_net": 0}}}}, {}))
        self.assertIn("no lean 1; hit rate 0/0; Wilson 95% unavailable", text)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RecentWindowsTests(unittest.TestCase):
    def test_recent_table_renders_brti_errors_nulls_and_eight_columns(self):
        script = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r'''
vm.createContext(context);
vm.runInContext(code, context);
vm.runInContext(`renderRecentWindows({recent_windows: [
  {settlement: 100, result: 'yes', proxy_error: -2, brti_error: 1.25},
  {settlement: null, status: 'awaiting_settlement', brti_error: null},
  {settlement: 100, result: 'no', brti_error: -0.75},
  {settlement: 100, result: 'yes', brti_error: 0}]})`, context);
const rows = el('recentWindows').innerHTML;
vm.runInContext('renderRecentWindows({recent_windows: []})', context);
console.log(JSON.stringify({rows, empty: el('recentWindows').innerHTML}));
'''
        done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        rows = out["rows"].split("</tr>")[:-1]
        self.assertEqual([row.count("<td") for row in rows], [8, 7, 8, 8])
        self.assertIn('colspan="2"', rows[1])
        self.assertIn("awaiting settlement", rows[1])
        self.assertIn("+$1.25", rows[0].split("<td>")[-1])
        self.assertIn("—", rows[1].split("<td>")[-1])
        self.assertIn("−$0.75", rows[2].split("<td>")[-1])
        self.assertIn("+$0.00", rows[3].split("<td>")[-1])
        self.assertIn('colspan="8"', out["empty"])
        page = (HERE / "index.html").read_text()
        table = page.split('<table class="recent-windows">')[1].split("</table>")[0]
        self.assertEqual(table.count("<th>"), 8)
        self.assertIn("<th>Proxy error</th><th>BRTI error</th>", table)


if __name__ == "__main__":
    unittest.main()
