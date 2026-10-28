"""Render regression checks (headless node:vm harness) and recent-windows server tests."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ta_copilot as ta
from test_fix_20261002 import ready_feed

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "fixtures" / "render_harness.mjs"


def run_scenario(name):
    done = subprocess.run(["node", str(HARNESS), name], capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(f"scenario {name} failed ({done.returncode}):\n{done.stderr}")
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RenderScenarioTests(unittest.TestCase):
    def test_conflict_headline_mixed_and_legacy_secondary(self):
        out = run_scenario("conflict")
        self.assertIn("Mixed", out["lean"])
        self.assertNotIn("Bullish", out["lean"])
        self.assertEqual(out["legacyCount"], "5 bull")

    def test_stale_brti_shows_proxy_basis_and_never_live(self):
        out = run_scenario("stale_brti")
        self.assertTrue(out["brtiStatus"].startswith("Proxy:"))
        self.assertNotIn("BRTI live", out["brtiStatus"])
        self.assertEqual(out["distanceLabel"], "Composite vs target")

    def test_rollover_switches_ticker_trades_and_panel_gains_closed_window(self):
        out = run_scenario("rollover")
        self.assertEqual(out["ticker"], "KXBTC15M-B")
        self.assertEqual(out["strike"], "$64,100.00")
        self.assertEqual(out["distTarget"], "Target $64,100.00")
        self.assertTrue(out["countdown"].startswith("14:"), out["countdown"])
        self.assertTrue(out["aCountdown"].startswith("0:"), out["aCountdown"])
        self.assertIn("20:01:00", out["trades"])
        self.assertNotIn("19:59:30", out["trades"])
        self.assertIn("20:00", out["panel"])        # X's close now in the panel
        self.assertIn("$64,050.00", out["panel"])   # X's settlement
        self.assertIn("YES", out["panel"])

    def test_reconnect_recovers_after_fresh_state(self):
        out = run_scenario("reconnect")
        self.assertEqual(out["before"], ["live ok", "Live · spot 0.5s"])
        self.assertEqual(out["silent"], ["live bad", "No update for 12 s"])
        self.assertTrue(out["closed"])
        self.assertTrue(out["reconnected"])
        self.assertEqual(out["after"], ["live ok", "Live · spot 0.5s"])
        self.assertEqual(out["price"], "$64,100.00")

    def test_recent_windows_cells_statuses_and_empty_text(self):
        out = run_scenario("recent_windows")
        self.assertIn("20:00", out["withRows"])
        self.assertIn("$64,100.00", out["withRows"])
        self.assertIn("YES", out["withRows"])
        self.assertIn("$64,080.00", out["withRows"])
        self.assertIn("(60/60)", out["withRows"])
        self.assertIn("awaiting settlement", out["withRows"])
        self.assertIn("incomplete samples", out["withRows"])
        self.assertIn("20.00", out["withRows"])     # proxy error −$20.00
        # A settled window with incomplete proxy samples still shows its outcome.
        s4 = out["withRows"].split("19:15")[1].split("</tr>")[0]
        self.assertIn("$63,950.00", s4)
        self.assertIn(">NO<", s4)
        self.assertIn("(45/60)", s4)
        self.assertIn("(60/60)", s4)
        self.assertNotIn("incomplete samples", s4)
        for key in ("empty", "missing"):
            self.assertIn("No settled windows yet", out[key])


class RecentWindowsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.logger = ta.TallyLogger(Path(directory.name), ta.Feed())
        self.base = 90000.0

    def write_rows(self, rows):
        self.logger.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.logger.prepare()

    def open_row(self, ticker, close, target=None):
        return {"type": "open", "ticker": ticker, "open_time": ta.iso(close - 900),
                "close_time": ta.iso(close), "target": target,
                "timeframes": {"1m": {"lean": "bullish"}}}

    def close_row(self, ticker, close, samples=60, proxy=101.0, brti=None, brti_samples=None):
        row = {"type": "close", "ticker": ticker, "close_time": ta.iso(close),
               "proxy_avg_60s": proxy, "samples": samples}
        if brti is not None:
            row["brti_avg_60s"] = brti
            row["brti_samples"] = brti_samples
        return row

    def settled_row(self, ticker, value=None):
        row = {"type": "settled", "ticker": ticker, "result": "yes"}
        if value is not None:
            row["expiration_value"] = value
        return row

    def test_ordering_and_cap_of_eight(self):
        rows = []
        for i in range(12):
            close = self.base + (i + 1) * 900
            ticker = f"W{i:02d}"
            rows += [self.open_row(ticker, close, 100.0 + i),
                     self.close_row(ticker, close),
                     self.settled_row(ticker, str(100.0 + i))]
        self.write_rows(rows)
        windows = self.logger.recent_windows
        self.assertEqual(len(windows), 8)
        self.assertEqual([w["ticker"] for w in windows], [f"W{i:02d}" for i in range(11, 3, -1)])
        close_times = [ta.parse_ts(w["close_time"]) for w in windows]
        self.assertEqual(close_times, sorted(close_times, reverse=True))

    def test_status_precedence_matches_refresh_proxy(self):
        # One close per status, in the same exclusive order refresh_proxy uses.
        rows = []
        rows += [self.open_row("INC", self.base + 900),
                 self.close_row("INC", self.base + 900, samples=45),
                 self.settled_row("INC", "101.0")]                       # incomplete_samples wins
        rows += [self.open_row("AWAIT", self.base + 1800),
                 self.close_row("AWAIT", self.base + 1800)]              # no settled row yet
        rows += [self.open_row("MISS", self.base + 2700),
                 self.close_row("MISS", self.base + 2700),
                 self.settled_row("MISS")]                               # settled, no value
        rows += [self.open_row("OK", self.base + 3600, 100.0),
                 self.close_row("OK", self.base + 3600, proxy=100.5, brti=100.4, brti_samples=60),
                 self.settled_row("OK", "101.0")]                        # settled, scored
        self.write_rows(rows)
        status = {w["ticker"]: w["status"] for w in self.logger.recent_windows}
        self.assertEqual(status, {"OK": "settled", "AWAIT": "awaiting_settlement",
                                  "MISS": "missing_settlement", "INC": "incomplete_samples"})
        # The coverage counters agree with those statuses.
        coverage = self.logger.proxy["coverage"]
        self.assertEqual(coverage["scored"], 1)
        self.assertEqual(coverage["incomplete_samples"], 1)
        self.assertEqual(coverage["awaiting_settlement"], 1)
        self.assertEqual(coverage["missing_settlement"], 1)

    def test_old_close_rows_get_null_brti_fields(self):
        rows = [self.open_row("OLD", self.base + 900, 100.0),
                self.close_row("OLD", self.base + 900),
                self.settled_row("OLD", "101.0")]
        self.write_rows(rows)
        window = self.logger.recent_windows[0]
        self.assertIsNone(window["brti_avg_60s"])
        self.assertIsNone(window["brti_samples"])

    def test_error_math_only_when_both_values_and_complete_samples(self):
        rows = [self.open_row("E", self.base + 900, 100.0),
                self.close_row("E", self.base + 900, proxy=100.5, brti=100.4, brti_samples=60),
                self.settled_row("E", "101.0"),
                self.open_row("P", self.base + 1800, 100.0),
                self.close_row("P", self.base + 1800, samples=45, proxy=100.5, brti=100.4, brti_samples=30),
                self.settled_row("P", "101.0")]
        self.write_rows(rows)
        by_ticker = {w["ticker"]: w for w in self.logger.recent_windows}
        self.assertAlmostEqual(by_ticker["E"]["proxy_error"], -0.5)
        self.assertAlmostEqual(by_ticker["E"]["brti_error"], -0.6)
        self.assertNotIn("proxy_error", by_ticker["P"])
        self.assertNotIn("brti_error", by_ticker["P"])

    def test_brti_error_needs_only_complete_brti(self):
        rows = [self.open_row("B", self.base + 900, 100.0),
                self.close_row("B", self.base + 900, samples=45, proxy=100.5, brti=100.4, brti_samples=60),
                self.settled_row("B", "101.0")]
        self.write_rows(rows)
        window = self.logger.recent_windows[0]
        self.assertEqual(window["status"], "incomplete_samples")
        self.assertAlmostEqual(window["brti_error"], -0.6)
        self.assertNotIn("proxy_error", window)

    def test_recent_windows_survive_prune_memory(self):
        # The 8 newest closes stay in memory even when pruning limits both dicts.
        rows = []
        for i in range(10):
            close = self.base + (i + 1) * 900
            rows += [self.open_row(f"W{i}", close, 100.0),
                     self.close_row(f"W{i}", close),
                     self.settled_row(f"W{i}", "101.0")]
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            with patch.object(ta, "MAX_MEMORY_WINDOWS", 2), patch.object(ta, "PROXY_RECORD_WINDOWS", 2):
                logger.prepare()
            windows = logger.recent_windows
            self.assertEqual(len(windows), 8)
            tickers = {w["ticker"] for w in windows}
            self.assertEqual(tickers, {f"W{i}" for i in range(2, 10)})
            self.assertTrue(tickers.issubset(logger.closes))
            self.assertNotIn("W0", logger.closes)
            self.assertNotIn("W1", logger.closes)

    def test_state_payload_exposes_recent_windows(self):
        feed = ready_feed(time.time())
        self.logger.recent_windows = [{"ticker": "T"}]
        feed.tally_logger = self.logger
        self.assertEqual(feed.state("1m")["recent_windows"], [{"ticker": "T"}])
        feed.tally_logger = None
        self.assertIsNone(feed.state("1m")["recent_windows"])


if __name__ == "__main__":
    unittest.main()
