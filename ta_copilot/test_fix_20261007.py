"""Adversarial QA 2026-10-07, batch A: Q05, Q19, Q03, Q04, Q18."""
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import acer_watch
import ta_copilot as ta
import ta_watch
import tally_report as report

HERE = Path(__file__).resolve().parent

# Builds an 820-window fixture, evaluates the primary with 200 rounds and
# prints what must not depend on the interpreter's string-hash seed.
PRIMARY_SCRIPT = r"""
import json, random, sys
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import tally_report as report
rng = random.Random(7)
minutes = {}
for w in range(820):
    ticker = f"KXBTC15M-26OCT{w:05d}"
    price, rows = 100.0, {}
    for m in range(1, 12):
        net = rng.choice((-2, -1, 0, 1, 2))
        price += 0.3 * net * rng.random() - 0.1 + rng.gauss(0, 1)
        rows[m] = {"type": "minute", "ticker": ticker, "minute": m, "composite": price,
                   "captured_at": f"2026-10-04T{w // 60 % 24:02d}:{w % 60:02d}:{m:02d}Z",
                   "timeframes": {"1m": {"family_net": net}}}
    minutes[ticker] = rows
shuffled = dict(sorted(minutes.items(), key=lambda kv: rng.random()))
with patch.object(report.block_bootstrap, "__defaults__", (200, report.BOOTSTRAP_SEED)):
    out = report.primary_result({}, shuffled, datetime(2026, 10, 15, tzinfo=timezone.utc))
print(json.dumps([out["rho"], out["ci"], out["verdict"], out["selection_sha256"], out["tickers"][:3]]))
"""


class PrimaryReproducibilityTests(unittest.TestCase):
    def test_same_result_under_different_hash_seeds(self):
        outputs = set()
        for seed in ("1", "2", "3"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            done = subprocess.run([sys.executable, "-I", "-c", PRIMARY_SCRIPT, str(HERE)], env=env,
                                  capture_output=True, text=True, timeout=120, check=True)
            outputs.add(done.stdout.strip())
        self.assertEqual(len(outputs), 1, outputs)
        rho, ci, verdict, digest, first = json.loads(outputs.pop())
        self.assertIsNotNone(ci)
        self.assertEqual(len(digest), 64)
        # Selection is the first 800 by capture time, in that order.
        self.assertEqual(first, ["KXBTC15M-26OCT00000", "KXBTC15M-26OCT00001", "KXBTC15M-26OCT00002"])

    def test_manifest_records_selection_and_digests(self):
        rows = {f"W{w:04d}": {m: {"ticker": f"W{w:04d}", "minute": m, "composite": 100.0 + (w * m) % 7,
                                  "captured_at": f"2026-10-04T00:{w // 60 % 60:02d}:{w % 60:02d}Z",
                                  "timeframes": {"1m": {"family_net": (w + m) % 3 - 1}}}
                              for m in range(1, 6)} for w in range(805)}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(report.block_bootstrap, "__defaults__", (50, report.BOOTSTRAP_SEED)):
            log = Path(directory) / "log.jsonl"
            log.write_text("{}\n")
            out = Path(directory) / "manifest.json"
            from datetime import datetime, timezone
            report.write_manifest(out, log, {}, rows, datetime(2026, 10, 15, tzinfo=timezone.utc))
            manifest = json.loads(out.read_text())
        self.assertTrue(manifest["evaluated"])
        self.assertEqual(len(manifest["tickers"]), report.PRIMARY_MIN_WINDOWS)
        self.assertEqual(manifest["tickers"][0], "W0000")
        for key in ("log_sha256", "code_sha256", "selection_sha256", "seed", "rounds", "rho", "ci", "verdict"):
            self.assertIn(key, manifest)


class SseSlotTests(unittest.TestCase):
    def test_header_write_failure_releases_the_slot(self):
        handler = ta.Handler.__new__(ta.Handler)
        handler.path = "/api/stream?tf=1m"
        with patch.object(ta.Handler, "send_response"), patch.object(ta.Handler, "send_header"), \
             patch.object(ta.Handler, "end_headers", side_effect=BrokenPipeError):
            for _ in range(ta.SSE_LIMIT + 3):
                handler.do_GET()
        taken = 0
        try:
            while ta.SSE_SLOTS.acquire(blocking=False):
                taken += 1
        finally:
            for _ in range(taken):
                ta.SSE_SLOTS.release()
        self.assertEqual(taken, ta.SSE_LIMIT)

    def test_socket_timeout_is_bounded(self):
        self.assertEqual(ta.Handler.timeout, ta.SOCKET_TIMEOUT)
        self.assertGreater(ta.SOCKET_TIMEOUT, ta.STREAM_INTERVAL * 5)


def close_row(ticker="KXBTC15M-26OCT070000-00"):
    return {"type": "close", "ticker": ticker, "close_time": "2026-10-07T00:00:00Z",
            "proxy_avg_60s": 100.0, "samples": 60}


class TallyWorkerTests(unittest.TestCase):
    def test_failed_close_append_is_retried_once_disk_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            real_append = logger.append
            calls = []

            def flaky(row):
                calls.append(row["type"])
                if len(calls) == 1:
                    # A torn half line reaches the disk before ENOSPC.
                    with open(logger.path, "ab") as handle:
                        handle.write(json.dumps(row).encode()[:20])
                    raise OSError(errno.ENOSPC, "No space left on device")
                real_append(row)

            with patch.object(logger, "append", side_effect=flaky), \
                 patch.object(logger, "hydrate_pending"), patch.object(logger, "settle_pending"):
                logger.queue.put(close_row())
                logger.step()
                self.assertIn("tally_close", logger.feed.errors)
                self.assertIn(close_row()["ticker"], logger.pending_closes)
                self.assertNotIn(close_row()["ticker"], logger.closes)
                logger.queue.put(close_row())  # a duplicate while pending is harmless
                logger.step()
                logger.step()
            self.assertNotIn("tally_close", logger.feed.errors)
            self.assertEqual(logger.pending_closes, {})
            skipped, closes = [], {}
            report.load_rows(logger.path, skipped, closes)
            self.assertEqual(list(closes), [close_row()["ticker"]])
            self.assertEqual(skipped, [1])
            self.assertEqual(calls, ["close", "close"])

    def test_worker_survives_an_unexpected_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            with patch.object(logger, "prepare"), patch.object(ta.time, "sleep"), \
                 patch.object(logger, "step", side_effect=[RuntimeError("boom"), None, KeyboardInterrupt]):
                with self.assertRaises(KeyboardInterrupt):
                    logger.run()
                self.assertEqual(logger.step.call_count, 3)
            self.assertNotIn("tally_worker", logger.feed.errors)  # cleared by the good pass


HEALTHY = {"ready": True, "stale": False, "kalshi_stale": False, "errors": {}}


def run_ta_watch(directory, state=HEALTHY, service=True, now=1000.0):
    sent = []
    with patch.object(ta_watch, "STATE_DIR", directory), \
         patch.object(ta_watch, "STATE_FILE", directory / "ta_watch_state.json"), \
         patch.object(ta_watch, "check", return_value=(service, state)), \
         patch.object(ta_watch, "send_message", side_effect=sent.append), \
         patch.object(ta_watch.time, "time", return_value=now), \
         patch.object(sys, "argv", ["ta_watch.py"]), \
         patch("sys.stderr", io.StringIO()):
        code = ta_watch.main()
    return code, sent


class WatcherStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dir = Path(self.directory.name)

    def test_ta_watch_recovers_from_damaged_state(self):
        path = self.dir / "ta_watch_state.json"
        for damaged in ("{", '{"active": "yes", "feed_since": []}', "[1, 2]"):
            with self.subTest(damaged=damaged):
                path.write_text(damaged)
                self.assertEqual(run_ta_watch(self.dir, state=None), (0, ["[BTC TA] problem: /api/state unavailable"]))
                self.assertEqual(run_ta_watch(self.dir, state=None)[0], 0)
                self.assertTrue(json.loads(path.read_text())["active"])
                path.unlink()
        self.assertTrue(list(self.dir.glob("ta_watch_state.corrupt-*")))

    def test_systemctl_timeout_still_checks_the_api(self):
        with patch.object(ta_watch.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 5)), \
             patch.object(ta_watch, "urlopen", side_effect=OSError("down")):
            service, state = ta_watch.check()
        self.assertIsNone(service)
        self.assertIsNone(state)
        self.assertIn("service status unknown (systemctl failed)", ta_watch.problems(None, HEALTHY, 0))
        self.assertEqual(run_ta_watch(self.dir, service=None)[1],
                         ["[BTC TA] problem: service status unknown (systemctl failed)"])

    def test_acer_watch_recovers_from_damaged_state(self):
        path = self.dir / "state.json"
        with patch.object(acer_watch, "STATE_DIR", self.dir), patch.object(acer_watch, "STATE_FILE", path), \
             patch.object(acer_watch, "reachable", return_value=True), \
             patch.object(acer_watch, "problems", return_value=["dashboard stale"]), \
             patch.object(acer_watch, "send_message") as send, patch("sys.stderr", io.StringIO()):
            for damaged in ("{", '{"fails": "x"}', "null"):
                with self.subTest(damaged=damaged):
                    path.write_text(damaged)
                    self.assertEqual(acer_watch.main(), 0)
                    self.assertEqual(json.loads(path.read_text())["fails"], 1)
                    self.assertEqual(acer_watch.main(), 0)
                    self.assertTrue(json.loads(path.read_text())["active"])
                    path.unlink()
        self.assertEqual(send.call_count, 3)


class KalshiHealthTests(unittest.TestCase):
    STALE = {**HEALTHY, "kalshi_stale": True, "window": None}

    def test_ta_watch_alerts_on_persistent_stale_kalshi_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            results = [run_ta_watch(directory, state=self.STALE, now=t)[1] for t in (1000, 1300, 1600)]
            self.assertEqual(results[:2], [[], []])
            self.assertEqual(results[2], ["[BTC TA] problem: Kalshi window missing or stale >8 min"])
            self.assertEqual(run_ta_watch(directory, state=HEALTHY, now=1900)[1],
                             ["[BTC TA] recovered: service and feeds healthy"])
            saved = json.loads((directory / "ta_watch_state.json").read_text())
            self.assertIsNone(saved["kalshi_stale_since"])

    def test_brief_stale_does_not_alert(self):
        self.assertEqual(ta_watch.problems(True, self.STALE, 1060, {}, 1000), [])
        self.assertEqual(ta_watch.kalshi_stale_since(self.STALE, None, 50), 50)
        self.assertEqual(ta_watch.kalshi_stale_since(self.STALE, 10, 50), 10)
        self.assertIsNone(ta_watch.kalshi_stale_since(HEALTHY, 10, 50))

    def test_acer_watch_reports_stale_kalshi(self):
        class Response(io.BytesIO):
            status = 200

        with patch.object(acer_watch, "urlopen", return_value=Response(json.dumps(self.STALE).encode())):
            self.assertEqual(acer_watch.dashboard_problem(), "dashboard Kalshi window missing or stale")
        with patch.object(acer_watch, "urlopen", return_value=Response(json.dumps(HEALTHY).encode())):
            self.assertIsNone(acer_watch.dashboard_problem())


if __name__ == "__main__":
    unittest.main()
