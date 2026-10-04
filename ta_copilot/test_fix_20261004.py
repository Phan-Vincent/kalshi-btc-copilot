"""Damaged tally logs and full-minute flow votes (2026-10-04)."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import ta_copilot as ta
import tally_report as report
from stream_flow import Trade
from test_fix_20261002 import ready_feed


def opened(ticker, lean="bullish"):
    return {"type": "open", "ticker": ticker,
            "timeframes": {"1m": {"lean": lean}}}


def settled(ticker, result="yes"):
    return {"type": "settled", "ticker": ticker, "result": result}


def lines(*rows):
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


class LogRecoveryTests(unittest.TestCase):
    def test_torn_tail_append_separates_next_row(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.path.write_bytes(lines(opened("A")) + b'{"type":"open"')
            logger.append(settled("A"))
            report_skipped = []
            report_open, report_settled, _ = report.load_rows(logger.path, report_skipped)
            self.assertEqual((report_open["A"]["ticker"], report_settled["A"]["result"]), ("A", "yes"))
            self.assertEqual(report_skipped, [1])
            warning = StringIO()
            with redirect_stderr(warning):
                logger.prepare()
            self.assertEqual((logger.record["hits"], logger.record["scored"],
                              logger.record["skipped_lines"]), (1, 1, 1))
            self.assertIn("line numbers: 2", warning.getvalue())
            self.assertEqual(stat.S_IMODE(logger.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(logger.path.parent.stat().st_mode), 0o700)

    def test_bad_middle_restores_later_rows_and_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "tally_log.jsonl"
            path.write_bytes(lines(opened("A")) + b"\nnot json\n[]\n" +
                             lines(opened("B", "bearish"), settled("B", "no")))
            pending = directory / "tally_pending.json"
            pending.write_text(json.dumps({"A": opened("A"), "C": opened("C")}))
            logger = ta.TallyLogger(directory, ta.Feed())
            warning = StringIO()
            with redirect_stderr(warning):
                logger.prepare()
            self.assertEqual(set(logger.opened), {"A", "B"})
            self.assertEqual(set(logger.pending_open), {"C"})
            self.assertEqual((logger.record["hits"], logger.record["scored"],
                              logger.record["skipped_lines"]), (1, 1, 2))
            self.assertIn("2 unreadable lines", warning.getvalue())
            self.assertIn("3, 4", warning.getvalue())
            self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)

    def test_conflicting_duplicates_first_wins_in_both_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.path.write_bytes(lines(opened("A", "bullish"), opened("A", "bearish"),
                                          settled("A", "yes"), settled("A", "no")))
            logger.prepare()
            first_open, first_settled, _ = report.load_rows(logger.path)
            self.assertEqual((logger.opened["A"], logger.results["A"]),
                             (first_open["A"], first_settled["A"]["result"]))
            self.assertEqual((logger.record["hits"], logger.record["scored"]), (1, 1))
            self.assertEqual(report.summarize(first_open, first_settled)["1m"]["hits"], 1)

    def test_report_main_survives_torn_line_and_prints_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tally_log.jsonl")
            path.write_bytes(lines(opened("A"), settled("A")) + b'{"type":"minute"')
            output = StringIO()
            with patch("sys.argv", ["tally_report.py", str(path)]), redirect_stdout(output):
                report.main()
            self.assertIn("Skipped 1 unreadable log lines.", output.getvalue())
            self.assertIn("Legacy: opening lean", output.getvalue())

    def test_threaded_appends_are_valid_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            def write(prefix):
                for i in range(200):
                    logger.append(opened(f"{prefix}{i}"))
            threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("A", "B")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            raw = logger.path.read_bytes().splitlines()
            self.assertEqual(len(raw), 400)
            self.assertEqual(len({json.loads(line)["ticker"] for line in raw}), 400)
            self.assertEqual(stat.S_IMODE(os.stat(logger.path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(logger.path.parent).st_mode), 0o700)

    def test_append_retries_short_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            original_write = os.write
            def short_write(fd, data):
                return original_write(fd, data[:7])
            with patch.object(ta.os, "write", side_effect=short_write) as write:
                logger.append(opened("A"))
            self.assertGreater(write.call_count, 1)
            self.assertEqual(json.loads(logger.path.read_text()), opened("A"))


class FlowCoverageTests(unittest.TestCase):
    def test_votes_wait_until_exactly_sixty_seconds(self):
        readings = [{"name": "EMA stack", "state": "bull", "detail": "x"}]
        for imbalance, expected in ((1, "bull"), (-1, "bear"), (.20, "bull"), (-.20, "bear")):
            with self.subTest(imbalance=imbalance):
                early = ta.family_tally(readings, imbalance, True, 59.999)
                ready = ta.family_tally(readings, imbalance, True, 60)
                self.assertEqual(early["votes"]["flow"], "neutral")
                self.assertFalse(early["flow_ready"])
                self.assertEqual(ready["votes"]["flow"], expected)
                self.assertTrue(ready["flow_ready"])

    def test_state_and_minute_rows_at_sixty_and_sixty_point_five(self):
        start = 90000.0
        feed = ready_feed(start)
        feed.market = {"ticker": "W", "open_time": ta.iso(start),
                       "close_time": ta.iso(start + 900), "status": "active",
                       "floor_strike": 100}
        feed.bars["5m"] = [{**bar, "time": bar["time"] * 5} for bar in feed.bars["1m"]]
        feed.flow.add_trade(Trade("coinbase", "BTC-USD", 100, 1, start + .1, "buy", "one"))
        feed.flow.add_trade(Trade("coinbase", "BTC-USD", 100, 1, start + 1, "buy", "two"))
        for delay, ready in ((.2, False), (60.0, True), (60.5, True)):
            now = start + delay
            feed.spot["time"] = ta.iso(now)
            feed.spot_at = now
            feed.venues["Coinbase"]["at"] = now
            feed.market_at = now
            with patch.object(ta.time, "time", return_value=now):
                state = feed.state("1m")
            family = state["frames"]["1m"]["family"]
            self.assertAlmostEqual(family["flow_coverage_seconds"], min(delay, 60))
            self.assertEqual(family["flow_ready"], ready)
            self.assertEqual(family["votes"]["flow"], "bull" if ready else "neutral")
            self.assertIn("5m", state["frames"])
            self.assertNotIn("flow_coverage_seconds", state["frames"]["5m"]["family"])
            self.assertNotIn("flow_ready", state["frames"]["5m"]["family"])
            if ready:
                old = ta.family_tally(state["frames"]["1m"]["readings"], 1, True, 60)
                self.assertEqual(family["votes"], old["votes"])
                row = ta.minute_record(state, now)
                self.assertEqual(row["timeframes"]["1m"]["family"], old["votes"])
                self.assertNotIn("flow_coverage_seconds", row)
                self.assertNotIn("flow_ready", row["timeframes"]["1m"])

    def test_opening_keeps_partial_measurement_and_neutral_vote(self):
        start = 90000.0
        bars = [{"time": i * 60, "open": 100 + i, "high": 101 + i, "low": 99 + i,
                 "close": 100 + i, "volume": 1} for i in range(250)]
        market = {"ticker": "W", "open_time": ta.iso(start), "close_time": ta.iso(start + 900)}
        row = ta.opening_record({"market": market, "captured_at": start + .2,
                                 "coinbase_spot": 350, "composite": 350,
                                 "flow_imbalance": .25, "flow_coverage": .2,
                                 "bars": {"1m": bars}})
        self.assertEqual(row["timeframes"]["1m"]["family"]["flow"], "neutral")
        self.assertEqual(row["flow_imbalance_1m"], .25)
        self.assertAlmostEqual(row["flow_coverage_seconds"], .2)

    def test_page_wiring(self):
        source = Path(ta.HERE, "app.js").read_text()
        self.assertIn("Flow <span class=\"neutral\">warming up", source)
        self.assertIn("fam.flow_ready === false", source)
        self.assertIn("rec?.skipped_lines > 0", source)
        self.assertIn("$('tallyRecord').textContent +=", source)


if __name__ == "__main__":
    unittest.main()
