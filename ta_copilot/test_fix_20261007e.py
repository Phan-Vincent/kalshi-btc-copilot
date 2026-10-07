"""Fix-verification follow-ups 2026-10-07: Q01/Q02 residuals, Q16 residuals,
manifest input snapshot, late settlement values."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import log_tools
import ta_copilot as ta
import tally_report as report
from test_fix_20261007b import APP_HARNESS

HERE = Path(__file__).resolve().parent

# The batch B harness setup (fake DOM, clock, transport), with new scenarios.
PAGE_SCENARIOS = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
const live = () => [el('live').className, el('liveText').textContent];
(async () => {
  vm.createContext(context);
  vm.runInContext(code, context);
  await flush();
  // Q01: a not-ready event after a healthy one must stay non-live across ticks.
  streams[0].emit('state', state('1m', 100, 0));
  streams[0].emit('state', {ready: false, server_time: new Date(T0 + 500).toISOString()});
  await advance(1000);
  out.not_ready_after_ticks = live();
  streams[0].emit('state', state('1m', 101, 1500));
  out.recovered = live();
  // Q01: a poll that times out must stay non-live across ticks.
  streams[0].onerror();                       // fallback poll starts now
  await advance(5000);                        // its 5 s deadline passes (and a reconnect opens)
  out.timeout = live();
  await advance(750);
  out.timeout_after_ticks = live();
  streams.at(-1).emit('state', state('1m', 102, 7000));
  out.recovered_again = live();
  // Q02: fresh 5m selected, stale 1m supplies the headline.
  buttons[1].onclick();
  const s = state('5m', 103, 8000, ['1m', '5m']);
  s.history_stale = ['1m'];
  streams.at(-1).emit('state', s);
  out.q02 = {live: live(), headline: el('tallyStale').textContent, head: el('mtfHead').innerHTML,
             note: el('frameNote').textContent};
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageFailureStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run(["node", "-"], input=PAGE_SCENARIOS, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "APP_JS": str(HERE / "app.js")})
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout)

    def test_q01_failures_persist_until_a_healthy_snapshot(self):
        self.assertEqual(self.out["not_ready_after_ticks"], ["live bad", "Warming up"])
        self.assertEqual(self.out["recovered"][0], "live ok")
        self.assertEqual(self.out["timeout"], ["live bad", "Server unreachable"])
        self.assertEqual(self.out["timeout_after_ticks"], ["live bad", "Server unreachable"])
        self.assertEqual(self.out["recovered_again"][0], "live ok")

    def test_q02_stale_headline_is_marked_on_another_tab(self):
        q02 = self.out["q02"]
        self.assertEqual(q02["live"], ["live warn", "Live · 1m history stale"])
        self.assertIn("1m history stale", q02["headline"])
        self.assertIn("1m ⚠", q02["head"])
        self.assertNotIn("5m ⚠", q02["head"])
        self.assertEqual(q02["note"], "")  # the selected 5m chart itself is fresh


def write_log(directory, rows):
    path = Path(directory) / "tally_log.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


MINUTE = {"type": "minute", "ticker": "W", "captured_at": "2026-10-07T00:01:00Z",
          "timeframes": {"1m": {"bull": 1, "bear": 0, "neutral": 2, "lean": "mixed", "family_net": 1}}}
OPEN = {"type": "open", "ticker": "W", "open_time": "2026-10-07T00:00:00Z", "close_time": "2026-10-07T00:15:00Z",
        "captured_at": "2026-10-07T00:00:00.05Z", "target": 100.0,
        "timeframes": {"1m": {"bull": 2, "bear": 0, "neutral": 1, "lean": "bullish"}}}


class ValidatorResidualTests(unittest.TestCase):
    def test_rows_that_used_to_crash_consumers_are_skipped(self):
        bad = [{**MINUTE, "ticker": "S", "minute": 1, "composite": "101"},
               {**MINUTE, "ticker": "S", "minute": 4, "composite": "104"},
               {**OPEN, "ticker": "D", "open_time": "not-a-date"},
               {**OPEN, "ticker": "N", "timeframes": {"1m": {"bull": {}, "bear": 0}}},
               {**OPEN, "ticker": "H", "timeframes": {"1m": {"bull": 1}}}]
        good = [OPEN, {**MINUTE, "minute": 1, "composite": 100.0}, {**MINUTE, "minute": 4, "composite": 101.5},
                {"type": "settled", "ticker": "W", "result": "yes", "expiration_value": "101.5",
                 "recorded_at": "2026-10-07T00:15:20Z"}]
        with tempfile.TemporaryDirectory() as directory:
            path = write_log(directory, bad + good)
            skipped, closes = [], {}
            opened, settled, minutes = report.load_rows(path, skipped, closes)
            self.assertEqual((sorted(opened), sorted(minutes), skipped), (["W"], ["W"], [5]))
            report.report(opened, settled, minutes, closes=closes)  # every section runs
            self.assertEqual(log_tools.read_rows(path)[1], 5)
            with patch("sys.stdout"):
                log_tools.check(path)  # gap parsing no longer meets "not-a-date"

    def test_problem_names(self):
        self.assertEqual(report.record_problem({**MINUTE, "minute": 1, "composite": "101"}), "bad composite")
        self.assertEqual(report.record_problem({**OPEN, "open_time": "not-a-date"}), "bad open_time")
        self.assertEqual(report.record_problem({**OPEN, "timeframes": {"1m": {"bull": {}, "bear": 0}}}), "bad bull")
        self.assertEqual(report.record_problem({**MINUTE, "minute": 1, "captured_at": None}), "bad captured_at")
        self.assertIsNone(report.record_problem({**MINUTE, "minute": 1, "yes_bid": {"price": "0.67", "age": 1}}))
        self.assertEqual(report.legacy_net({}), None)


class ManifestSnapshotTests(unittest.TestCase):
    def test_report_and_manifest_use_one_frozen_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_log(directory, [OPEN, {**MINUTE, "minute": 1, "composite": 100.0}])
            manifest = Path(directory) / "primary_manifest.json"
            copy = report.snapshot_log(source, manifest)
            with source.open("a") as handle:  # the live log grows meanwhile
                handle.write(json.dumps({**MINUTE, "ticker": "X", "minute": 1, "composite": 1.0}) + "\n")
            opened, _, minutes = report.load_rows(copy)
            from datetime import datetime, timezone
            report.write_manifest(manifest, copy, opened, minutes, datetime(2026, 10, 8, tzinfo=timezone.utc),
                                  source=source)
            saved = json.loads(manifest.read_text())
            self.assertEqual(saved["windows"], 1)
            self.assertEqual(saved["log_sha256"], hashlib.sha256(copy.read_bytes()).hexdigest())
            self.assertNotEqual(saved["log_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual((saved["log"], saved["source_log"]), (str(copy), str(source)))
            self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)

    def test_cli_writes_the_input_copy_beside_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_log(directory, [OPEN, {**MINUTE, "minute": 1, "composite": 100.0}])
            manifest = Path(directory) / "primary_manifest.json"
            done = subprocess.run([sys.executable, str(HERE / "tally_report.py"), str(source), "--manifest",
                                   str(manifest)], capture_output=True, text=True, timeout=60, check=True)
            copy = Path(directory) / "primary_manifest.input.jsonl"
            self.assertIn(f"its exact input is {copy}", done.stdout)
            self.assertEqual(copy.read_bytes(), source.read_bytes())
            self.assertEqual(json.loads(manifest.read_text())["log"], str(copy))


class LateSettlementValueTests(unittest.TestCase):
    CLOSE = 100000.0

    def logger(self, directory):
        logger = ta.TallyLogger(Path(directory), ta.Feed())
        logger.opened = {"T": {"ticker": "T", "close_time": ta.iso(self.CLOSE), "timeframes": {}}}
        return logger

    def settle(self, logger, market, now):
        with patch.object(ta, "get_json", return_value={"market": market}):
            logger.settle_pending(now)

    def test_value_after_the_wait_is_recorded_and_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory)
            self.settle(logger, {"result": "yes"}, self.CLOSE + ta.SETTLE_VALUE_WAIT + 1)
            self.assertIn("T", logger.value_pending)
            self.settle(logger, {"result": "yes"}, self.CLOSE + 3600)  # still none: keep waiting
            self.settle(logger, {"result": "yes", "expiration_value": "84000.5"}, self.CLOSE + 7200)
            self.assertEqual((logger.value_pending, logger.expirations["T"]), ({}, "84000.5"))
            _, settled, _ = report.load_rows(logger.path)
            self.assertEqual((settled["T"]["result"], settled["T"]["expiration_value"]), ("yes", "84000.5"))
            restarted = ta.TallyLogger(Path(directory), ta.Feed())
            restarted.prepare()
            self.assertEqual(restarted.expirations["T"], "84000.5")
            self.assertEqual(restarted.value_pending, {})
            rows, bad = log_tools.read_rows(logger.path)
            self.assertEqual((len(rows), bad), (2, 0))

    def test_restart_resumes_waiting_and_gives_up_after_a_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_log(directory, [{**OPEN, "ticker": "T", "close_time": ta.iso(self.CLOSE)},
                                         {"type": "settled", "ticker": "T", "result": "no",
                                          "recorded_at": ta.iso(self.CLOSE + 1900)}])
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            with patch.object(ta.time, "time", return_value=self.CLOSE + 3600):
                logger.prepare()
            self.assertEqual(logger.value_pending, {"T": self.CLOSE})
            self.settle(logger, {"result": "no"}, self.CLOSE + ta.SETTLE_VALUE_RETRY + 1)
            self.assertEqual(logger.value_pending, {})
            self.assertEqual(len(path.read_text().splitlines()), 2)  # nothing appended


if __name__ == "__main__":
    unittest.main()
