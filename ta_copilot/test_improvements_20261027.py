"""Behavior checks for family headlines, local charts, and minute retry capture."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import ta_copilot as ta
from test_fix_20261002 import QuietServer
from test_fix_20261007b import APP_HARNESS
from test_tally_20261003 import window_state


class MinuteRetryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.logger = ta.TallyLogger(Path(directory.name), ta.Feed())
        self.opened = 90000

    def capture(self, minute):
        now = self.opened + minute * 60 + .5
        state = window_state(self.opened, now)
        with patch.object(ta, "cached_state", return_value=state):
            self.logger.capture_minute(now)
        return state

    def rows(self):
        return [json.loads(line) for line in self.logger.path.read_text().splitlines()]

    def test_retry_keeps_original_snapshot_and_does_not_duplicate(self):
        with patch.object(self.logger, "append", side_effect=OSError("disk busy")):
            state = self.capture(1)
        self.assertIn("tally_minute", self.logger.feed.errors)
        state["frames"]["1m"]["family"]["votes"]["trend"] = "bear"
        with patch.object(ta, "cached_state", side_effect=AssertionError("retry recaptured")):
            self.assertTrue(self.logger.flush_minutes())
            self.assertTrue(self.logger.flush_minutes())
        self.capture(1)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["captured_at"], ta.iso(self.opened + 60.5))
        self.assertEqual(rows[0]["timeframes"]["1m"]["family"]["trend"], "bull")
        self.assertNotIn("tally_minute", self.logger.feed.errors)

    def test_partial_flush_keeps_error_and_latest_capture_identity(self):
        with patch.object(self.logger, "append", side_effect=OSError("disk busy")):
            self.capture(1)
            self.capture(2)
        original = self.logger.append
        def fail_second(row):
            if row["minute"] == 2:
                raise OSError("still busy")
            original(row)
        with patch.object(self.logger, "append", side_effect=fail_second):
            self.assertFalse(self.logger.flush_minutes())
        self.assertEqual([r["minute"] for r in self.rows()], [1])
        self.assertIn("tally_minute", self.logger.feed.errors)
        self.assertEqual(self.logger.minute_captured, ("KXBTC15M-W", 2))
        self.assertTrue(self.logger.flush_minutes())
        self.capture(2)
        self.assertEqual([r["minute"] for r in self.rows()], [1, 2])
        self.assertNotIn("tally_minute", self.logger.feed.errors)

    def test_overflow_is_bounded_and_does_not_evict_older_rows(self):
        with patch.object(ta, "MINUTE_BACKLOG_LIMIT", 2), \
                patch.object(self.logger, "append", side_effect=OSError("disk busy")):
            for minute in (1, 2, 3, 3):
                self.capture(minute)
        self.assertEqual(len(self.logger.minute_pending), 2)
        self.assertEqual(self.logger.minute_overflowed, 1)
        self.assertIn("tally_minute_overflow", self.logger.feed.errors)
        self.logger.flush_minutes()
        self.assertEqual([r["minute"] for r in self.rows()], [1, 2])

    def test_scheduler_retries_without_recapture_and_keeps_new_minutes(self):
        class StopClock(BaseException):
            pass
        now = [self.opened + 59.0]
        captures, attempts = [], []
        original = self.logger.append
        def snapshot(_):
            captures.append(now[0])
            return window_state(self.opened, now[0])
        def append(row):
            attempts.append(now[0])
            if now[0] < self.opened + 120:
                raise OSError("disk busy")
            original(row)
        def sleep(delay):
            self.assertGreater(delay, 0)
            now[0] += delay
            if now[0] > self.opened + 121:
                raise StopClock()
        with patch.object(ta.time, "time", side_effect=lambda: now[0]), \
                patch.object(ta.time, "sleep", side_effect=sleep), \
                patch.object(ta, "cached_state", side_effect=snapshot), \
                patch.object(self.logger, "append", side_effect=append):
            with self.assertRaises(StopClock):
                self.logger.run_minutes()
        self.assertEqual(captures, [self.opened + 60.5, self.opened + 120.5])
        self.assertEqual(attempts[1] - attempts[0], ta.MINUTE_RETRY_SECONDS)
        self.assertEqual([r["minute"] for r in self.rows()], [1, 2])
        self.assertNotIn("tally_minute", self.logger.feed.errors)


class LocalChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = QuietServer(("127.0.0.1", 0), ta.Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_page_asset_integrity_mime_and_same_origin_policy(self):
        with urlopen(self.base + "/", timeout=3) as response:
            page = response.read().decode()
            policy = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self';", policy)
        self.assertNotIn("unpkg", policy + page)
        self.assertIn(f'src="{ta.VENDOR_CHART_PATH}"', page)
        with urlopen(self.base + ta.VENDOR_CHART_PATH, timeout=3) as response:
            body = response.read()
            self.assertEqual(response.headers.get_content_type(), "application/javascript")
            self.assertEqual(response.headers["Content-Security-Policy"], policy)
        expected = re.search(r'integrity="sha384-([^"]+)"', page).group(1)
        self.assertEqual(base64.b64encode(hashlib.sha384(body).digest()).decode(), expected)
        self.assertIn(b"LightweightCharts", body)

    def test_vendor_route_is_allowlisted_and_missing_asset_is_404(self):
        for path in ("/vendor/LICENSE", "/vendor/../ta_copilot.py", "/vendor/other.js"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.base + path, timeout=3)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        with tempfile.TemporaryDirectory() as directory, patch.object(ta, "HERE", Path(directory)):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + ta.VENDOR_CHART_PATH, timeout=3)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FamilyHeadlineTests(unittest.TestCase):
    def test_conflict_missing_warmup_and_stale_states(self):
        script = APP_HARNESS[:APP_HARNESS.index("const out = {};")] + r"""
const out = {};
(async () => {
  vm.createContext(context); vm.runInContext(code, context); await flush();
  const make = ms => {
    const s = state('1m', 64000, ms);
    s.frames['1m'].tally = {bull:5, bear:1, neutral:2, lean:'bullish'};
    s.frames['1m'].family = {votes:{trend:'bull', momentum:'neutral', flow:'bear'},
      net:0, lean:'mixed', flow_ready:true};
    return s;
  };
  const s = make(0), before = JSON.stringify(s.frames);
  streams[0].emit('state', s);
  out.conflict = [el('lean').innerHTML, el('nBull').textContent, el('families').innerHTML];
  out.unchanged = before === JSON.stringify(s.frames);
  const missing = make(1000); delete missing.frames['1m'].family;
  streams[0].emit('state', missing); out.missing = el('lean').innerHTML;
  const warm = make(2000); warm.frames['1m'].family.flow_ready = false;
  warm.frames['1m'].family.flow_coverage_seconds = 20;
  streams[0].emit('state', warm); out.warm = el('families').innerHTML;
  const stale = make(3000); stale.frames['1m'].history.stale = true;
  streams[0].emit('state', stale);
  out.stale = [el('lean').innerHTML, el('families').innerHTML, el('nBull').textContent];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True,
                                env={**os.environ, "APP_JS": str(ta.HERE / "app.js")}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("Mixed", out["conflict"][0])
        self.assertNotIn("Bullish", out["conflict"][0])
        self.assertEqual(out["conflict"][1], "5 bull")
        self.assertIn("net 0", out["conflict"][2])
        self.assertTrue(out["unchanged"])
        self.assertIn("Unavailable", out["missing"])
        self.assertIn("warming up (20/60 s)", out["warm"])
        self.assertIn("Unavailable", out["stale"][0])
        self.assertIn("history stale", out["stale"][1])
        self.assertEqual(out["stale"][2], "")


if __name__ == "__main__":
    unittest.main()
