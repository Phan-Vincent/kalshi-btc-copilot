"""Closing-minute proxy vs settlement, the off-Acer alarm and log tools (2026-10-05)."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
import shutil
import subprocess
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import acer_watch
import log_tools
import ta_copilot as ta
import tally_report as report
from test_fix_20261002 import ready_feed

B = 90000  # a 15-minute boundary (90000 % 900 == 0)


def market(close):
    return {"ticker": f"KXBTC15M-{close}", "open_time": ta.iso(close - 900),
            "close_time": ta.iso(close), "status": "active", "floor_strike": 100}


class Collector:
    def __init__(self):
        self.rows = []

    def submit(self, row):
        self.rows.append(row)
        return True


class CloseCaptureTests(unittest.TestCase):
    def feed_with_samples(self, seconds):
        feed = ready_feed(B - 30)
        feed.samples.clear()
        for t in seconds:
            feed.samples.append((t, 100.0 + (t - (B - 60))))
        return feed

    def test_mean_uses_exactly_the_closing_minute(self):
        feed = self.feed_with_samples(range(B - 60, B))
        feed.market = market(B)
        row = feed._close_proxy(B, B + 0.05)
        self.assertEqual(row["samples"], 60)
        self.assertAlmostEqual(row["proxy_avg_60s"], 100 + 29.5)
        self.assertEqual((row["type"], row["ticker"]), ("close", f"KXBTC15M-{B}"))

    def test_gap_counts_and_mismatched_market_is_skipped(self):
        feed = self.feed_with_samples([t for t in range(B - 60, B) if t != B - 10])
        feed.market = market(B)
        self.assertEqual(feed._close_proxy(B, B + 0.05)["samples"], 59)
        feed.market = market(B + 900)
        feed.upcoming_markets = {}
        self.assertIsNone(feed._close_proxy(B, B + 0.05))
        feed.upcoming_markets = {"m": market(B)}
        self.assertEqual(feed._close_proxy(B, B + 0.05)["ticker"], f"KXBTC15M-{B}")

    def test_sampler_submits_one_row_when_crossing_the_boundary(self):
        feed = self.feed_with_samples(range(B - 60, B - 1))
        feed.market = market(B)
        feed.tally_logger = Collector()
        with patch.object(ta, "composite_quote", return_value=(100.0, {})):
            feed.sample_composite(B - 0.95)   # second B-1 completes the minute
            feed.sample_composite(B + 0.05)
            feed.sample_composite(B + 1.05)
        closes = [r for r in feed.tally_logger.rows if r.get("type") == "close"]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["samples"], 60)

    def test_no_row_on_the_first_sample_after_startup(self):
        feed = self.feed_with_samples(range(B - 60, B))
        feed.market = market(B)
        feed.tally_logger = Collector()
        with patch.object(ta, "composite_quote", return_value=(100.0, {})):
            feed.sample_composite(B + 0.05)
        self.assertFalse([r for r in feed.tally_logger.rows if r.get("type") == "close"])


class ProxyRecordTests(unittest.TestCase):
    def logger(self, rows):
        directory = Path(tempfile.mkdtemp())
        (directory / "tally_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        logger = ta.TallyLogger(directory, ta.Feed())
        logger.prepare()
        return logger, directory

    def close(self, ticker, proxy, samples=60, at=0):
        return {"type": "close", "ticker": ticker, "close_time": ta.iso(B + at),
                "proxy_avg_60s": proxy, "samples": samples, "captured_at": ta.iso(B + at)}

    def settled(self, ticker, value):
        return {"type": "settled", "ticker": ticker, "result": "yes", "expiration_value": value}

    def test_summary_arithmetic_and_incomplete_excluded(self):
        rows = []
        for i, error in enumerate([1, -2, 3, -4, 5, 0, 10, -1, 2, 2]):
            rows += [self.close(f"T{i}", 100 + error, at=900 * i), self.settled(f"T{i}", "100.00")]
        rows += [self.close("P", 500, samples=59, at=99999), self.settled("P", 100)]
        rows += [self.close("T0", 999)]  # duplicate close: the first wins
        record, _ = self.logger(rows)
        px = record.proxy
        self.assertEqual(px["n"], 10)
        self.assertEqual(px["median_abs"], 2)
        self.assertEqual(px["p90_abs"], 5)
        self.assertAlmostEqual(px["bias"], 1.6)
        self.assertTrue(px["too_few"])

    def test_cap_keeps_the_latest_windows(self):
        rows = []
        for i in range(ta.PROXY_RECORD_WINDOWS + 50):
            error = 100 if i < 50 else 1
            rows += [self.close(f"T{i}", 100 + error, at=900 * i), self.settled(f"T{i}", 100)]
        record, _ = self.logger(rows)
        self.assertEqual(record.proxy["n"], ta.PROXY_RECORD_WINDOWS)
        self.assertEqual(record.proxy["p90_abs"], 1)
        self.assertFalse(record.proxy["too_few"])

    def test_close_rows_are_appended_once_and_reloaded(self):
        logger, directory = self.logger([])
        row = self.close("X", 101)
        logger.accept_close(row)
        logger.accept_close({**row, "proxy_avg_60s": 555})
        lines = (directory / "tally_log.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        reloaded = ta.TallyLogger(directory, ta.Feed())
        reloaded.prepare()
        self.assertEqual(reloaded.closes["X"]["proxy_avg_60s"], 101)
        self.assertEqual(reloaded.proxy["n"], 0)

    def test_close_only_window_is_settled_and_scored(self):
        logger, directory = self.logger([self.close("C", 103)])
        market = {"result": "yes", "expiration_value": "100.00"}
        with patch.object(ta, "get_json", return_value={"market": market}) as fetch:
            logger.settle_pending(B + 120)
            logger.settle_pending(B + 180)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((logger.proxy["n"], logger.proxy["median_abs"]), (1, 3))

    def test_report_section(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "log.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            self.close("A", 103), self.settled("A", 100), self.close("B", 90, samples=12), self.settled("B", 100)]))
        closes = {}
        opened, settled, minutes = report.load_rows(path, None, closes)
        text = "\n".join(report.proxy_section(closes, settled))
        self.assertIn("1 complete windows, 1 with fewer than 60 samples", text)
        self.assertIn("median |error| $3.00", text)
        self.assertNotIn("Proxy vs settlement", report.report(opened, settled, minutes))

    def test_state_and_page_wiring(self):
        feed = ready_feed(B - 30)
        feed.tally_logger, _ = self.logger([])
        with patch.object(ta.time, "time", return_value=B - 30):
            state = feed.state("1m")
        self.assertEqual(state["proxy_record"]["n"], 0)
        here = Path(ta.__file__).parent
        self.assertIn('id="proxyRecord"', (here / "index.html").read_text())
        script = (here / "app.js").read_text()
        self.assertIn("s.proxy_record", script)
        self.assertIn("$('proxyRecord').textContent", script)


class PageScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_app_js_parses(self):
        # String-based wiring tests can't see a redeclared identifier; a parser can.
        script = Path(ta.__file__).parent / "app.js"
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class AcerWatchTests(unittest.TestCase):
    def test_alerts_after_two_failures_then_repeats_and_recovers(self):
        bad = ["Acer unreachable over Tailscale"]
        message, state = acer_watch.step({}, bad, 100)
        self.assertIsNone(message)
        message, state = acer_watch.step(state, bad, 400)
        self.assertTrue(message.startswith("[ACER] problem"))
        message, state = acer_watch.step(state, bad, 700)
        self.assertIsNone(message)
        message, state = acer_watch.step(state, bad, 400 + acer_watch.REPEAT_SECONDS)
        self.assertTrue(message.startswith("[ACER] still unhealthy"))
        message, state = acer_watch.step(state, [], 99999)
        self.assertTrue(message.startswith("[ACER] recovered"))
        self.assertEqual(state["fails"], 0)

    def test_single_blip_never_alerts(self):
        message, state = acer_watch.step({}, ["dashboard stale"], 100)
        message, state = acer_watch.step(state, [], 400)
        self.assertIsNone(message)
        self.assertEqual(state, {"fails": 0, "active": False, "last_sent_at": 0})

    def run_main(self, previous, found, send):
        directory = Path(tempfile.mkdtemp())
        state_file = directory / "state.json"
        if previous is not None:
            state_file.write_text(json.dumps(previous))
        err = StringIO()
        with patch.object(acer_watch, "STATE_DIR", directory), patch.object(acer_watch, "STATE_FILE", state_file), \
                patch.object(acer_watch, "reachable", return_value=True), \
                patch.object(acer_watch, "problems", return_value=found), \
                patch.object(acer_watch, "send_message", side_effect=send), redirect_stderr(err):
            code = acer_watch.main()
        return code, json.loads(state_file.read_text()) if state_file.exists() else None, err.getvalue(), state_file

    def test_failed_send_keeps_state_and_hides_the_token(self):
        def boom(_):
            raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage")
        previous = {"fails": 1, "active": False, "last_sent_at": 0}
        code, state, err, _ = self.run_main(previous, ["Acer unreachable over Tailscale"], boom)
        self.assertEqual((code, state), (1, previous))
        self.assertNotIn("SECRET123", err)

    def test_state_file_is_private(self):
        code, state, _, path = self.run_main(None, ["dashboard stale"], lambda _: None)
        self.assertEqual((code, state["fails"]), (0, 1))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_offline_mac_changes_nothing(self):
        directory = Path(tempfile.mkdtemp())
        with patch.object(acer_watch, "STATE_FILE", directory / "s.json"), \
                patch.object(acer_watch, "reachable", return_value=False), \
                patch.object(acer_watch, "problems", side_effect=AssertionError("must not check")):
            self.assertEqual(acer_watch.main(), 0)
        self.assertFalse((directory / "s.json").exists())


class LogToolsTests(unittest.TestCase):
    def write(self, rows, extra=b""):
        path = Path(tempfile.mkdtemp()) / "log.jsonl"
        path.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows) + extra)
        return path

    def opened(self, start, ticker=None, at=None):
        return {"type": "open", "ticker": ticker or f"W{start}", "open_time": ta.iso(start),
                "captured_at": ta.iso(at if at is not None else start)}

    def test_check_counts_duplicates_bad_lines_and_gaps(self):
        path = self.write([self.opened(B), self.opened(B + 900), self.opened(B + 900),
                           self.opened(B + 2700), {"type": "settled", "ticker": f"W{B}", "result": "yes"}],
                          extra=b'{"type":"minute","tick\n')
        out = StringIO()
        with redirect_stdout(out):
            code = log_tools.main(["check", str(path)])
        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("unreadable lines 1, duplicate keys 1, opened but not settled 2", text)
        self.assertIn("missing windows between first and last open: 1", text)

    def test_merge_precedence_order_permissions_and_refusal(self):
        primary = self.write([self.opened(B, "A", at=B + 5), {"type": "minute", "ticker": "A", "minute": 1,
                                                              "captured_at": ta.iso(B + 60), "who": "p"}])
        secondary = self.write([{"type": "minute", "ticker": "A", "minute": 1, "captured_at": ta.iso(B + 60),
                                 "who": "s"}, self.opened(B - 900, "Z", at=B - 895)])
        out = primary.parent / "merged.jsonl"
        with redirect_stdout(StringIO()):
            self.assertEqual(log_tools.main(["merge", str(primary), str(secondary), "-o", str(out)]), 0)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([r["ticker"] for r in rows], ["Z", "A", "A"])
        self.assertEqual(rows[2]["who"], "p")
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(log_tools.main(["merge", str(primary), str(secondary), "-o", str(out)]), 2)
            self.assertEqual(log_tools.main(["merge", str(primary), str(secondary), "-o", str(primary)]), 2)


if __name__ == "__main__":
    unittest.main()
