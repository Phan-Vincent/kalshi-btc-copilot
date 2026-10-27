"""Watcher alert for BRTI unavailable while a Kalshi key is configured (2026-10-27)."""
import json
from pathlib import Path
import tempfile
import unittest

import ta_watch
from test_fix_20261007 import HEALTHY, run_ta_watch

LIVE = {**HEALTHY, "brti": {"configured": True, "connected": True, "fresh": True, "age": 0.2}}
DOWN = {**HEALTHY, "brti": {"configured": True, "connected": False, "fresh": False, "age": 30.0}}
KEYLESS = {**HEALTHY, "brti": {"configured": False, "connected": False, "fresh": False, "age": None,
                               "error": "not configured"}}
ALERT = "[BTC TA] problem: BRTI unavailable >8 min (settlement figures on the proxy)"


class BrtiAlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dir = Path(self.directory.name)

    def test_alerts_on_third_check_and_recovers(self):
        results = [run_ta_watch(self.dir, state=DOWN, now=t)[1] for t in (1000, 1300, 1600)]
        self.assertEqual(results, [[], [], [ALERT]])
        self.assertEqual(run_ta_watch(self.dir, state=LIVE, now=1900)[1],
                         ["[BTC TA] recovered: service and feeds healthy"])
        self.assertIsNone(json.loads((self.dir / "ta_watch_state.json").read_text())["brti_stale_since"])

    def test_a_fresh_check_restarts_the_span(self):
        sent = [run_ta_watch(self.dir, state=s, now=t)[1]
                for s, t in ((DOWN, 1000), (DOWN, 1300), (LIVE, 1600), (DOWN, 1900), (DOWN, 2200))]
        self.assertEqual(sent, [[], [], [], [], []])

    def test_keyless_older_server_and_closed_exchange_never_alert(self):
        closed = {**DOWN, "exchange_closed": True}
        for state in (KEYLESS, HEALTHY, closed):
            with self.subTest(state=state):
                self.assertIsNone(ta_watch.brti_stale_since(state, 100, 1000))
                self.assertEqual(ta_watch.problems(True, state, 1000, {}, None, 100), [])
        self.assertIsNone(ta_watch.brti_stale_since(None, 100, 1000))

    def test_state_file_field_is_validated(self):
        self.assertTrue(ta_watch.valid_state({"active": False, "last_sent_at": 0, "brti_stale_since": 5}))
        self.assertTrue(ta_watch.valid_state({"active": False, "last_sent_at": 0, "brti_stale_since": None}))
        self.assertFalse(ta_watch.valid_state({"active": False, "last_sent_at": 0, "brti_stale_since": "x"}))


if __name__ == "__main__":
    unittest.main()
