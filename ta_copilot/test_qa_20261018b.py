"""QA shakedown 2026-10-18b: derived history freshness (H01), empty expiration
values (H03), sticky feed alarms (H06), a corrupt pending queue (H08), an
orderbook that outlives a failed trades GET (H09) and reference bars (H10)."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ta_copilot as ta
import tally_report as report

# Aligned to every timeframe's bucket, so "the last bar" is unambiguous.
NOW = 99000.0


def ready(feed, now):
    """The spot/composite parts state() needs before it returns a ready snapshot."""
    feed.spot = {"price": 100.0, "bid": 99.0, "ask": 101.0, "time": ta.iso(now)}
    feed.spot_at = now
    feed.venues["Coinbase"] = {"price": 100.0, "at": now, "age_basis": "ticker"}
    feed.composite, feed.composite_at = 100.0, now
    return feed


def frame(last, sec, n=70, skip=None):
    """n contiguous bars ending at `last`, optionally with one bar missing."""
    return [{"time": t, "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
             "close": 100.0 + i, "volume": 1.0}
            for i, t in enumerate(sorted(last - sec * i for i in range(n))) if t != skip]


class DerivedHistoryTests(unittest.TestCase):
    def state(self, last, hole=None):
        feed = ready(ta.Feed(), NOW)
        for name in ("1m", "5m", "10m"):
            feed.bars[name] = frame(last[name], ta.TIMEFRAMES[name],
                                    skip=last["5m"] - 600 if hole and name == "5m" else None)
            feed.fetched_last[name] = feed.bars[name][-1]["time"]
        with patch.object(ta.time, "time", return_value=NOW):
            return feed.state("1m")

    def test_stale_10m_source_leaves_the_derived_frame_stale(self):
        # The 5m bar ending 98700 is 600 s old, so 5m is stale; the derived 10m
        # bucket starts at 97800 and its own 600 s budget alone would say fresh.
        state = self.state({"1m": 98940, "5m": 98100, "10m": 97800})
        self.assertTrue(state["history"]["5m"]["stale"])
        self.assertTrue(state["history"]["10m"]["stale"])
        self.assertTrue(state["frames"]["10m"]["history"]["stale"])
        self.assertIn("10m", state["history_stale"])
        self.assertEqual(state["history"]["10m"]["lag_seconds"], state["history"]["5m"]["lag_seconds"])
        # What history_quality alone still reports for the derived frame.
        self.assertFalse(ta.history_quality(frame(97800, 600), 600, 97800, NOW)["stale"])

    def test_gap_in_5m_shows_on_the_derived_frame(self):
        state = self.state({"1m": 98940, "5m": 98700, "10m": 98400}, hole=True)
        self.assertTrue(state["history"]["5m"]["gap"])
        self.assertTrue(state["history"]["10m"]["gap"])
        self.assertFalse(state["history"]["10m"]["stale"])
        self.assertNotIn("10m", state["history_stale"])

    def test_fresh_sources_leave_the_derived_frame_clean(self):
        state = self.state({"1m": 98940, "5m": 98700, "10m": 98400})
        self.assertFalse(state["history"]["5m"]["stale"])
        self.assertFalse(state["history"]["10m"]["stale"])
        self.assertFalse(state["history"]["10m"]["gap"])
        self.assertNotIn("10m", state["history_stale"])


class SettledValueTests(unittest.TestCase):
    def logger(self, directory, close):
        logger = ta.TallyLogger(Path(directory), ta.Feed())
        logger.prepare()
        logger.opened = {"T": {"ticker": "T", "close_time": ta.iso(close), "timeframes": {}}}
        return logger

    def test_empty_expiration_value_is_awaited_not_recorded(self):
        close = 99000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            empty = {"market": {"result": "yes", "expiration_value": ""}}
            with patch.object(ta, "get_json", return_value=empty):
                logger.settle_pending(close + 60)
            self.assertNotIn("T", logger.settled)
            self.assertFalse(logger.path.exists())  # the row is held, not written without a value
            real = {"market": {"result": "yes", "expiration_value": "84000.12"}}
            with patch.object(ta, "get_json", return_value=real):
                logger.settle_pending(close + 120)
            rows = [json.loads(line) for line in logger.path.read_text().splitlines()]
            self.assertEqual([row["expiration_value"] for row in rows], ["84000.12"])
            self.assertEqual([report.record_problem(row) for row in rows], [None])

    def test_value_retry_ignores_empty_then_writes_the_real_value(self):
        close = 99000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            logger.opened, logger.settled = {}, {"T"}
            logger.expirations, logger.value_pending = {"T": None}, {"T": close}
            empty = {"market": {"result": "yes", "expiration_value": ""}}
            with patch.object(ta, "get_json", return_value=empty):
                logger.settle_pending(close + 60)
            self.assertFalse(logger.path.exists())
            self.assertEqual(logger.value_pending, {"T": close})  # still awaited
            real = {"market": {"result": "yes", "expiration_value": "84000.12"}}
            with patch.object(ta, "get_json", return_value=real):
                logger.settle_pending(close + 120)
            rows = [json.loads(line) for line in logger.path.read_text().splitlines()]
            self.assertEqual(rows, [{"type": "settled_value", "ticker": "T",
                                     "recorded_at": ta.iso(close + 120), "expiration_value": "84000.12"}])
            self.assertEqual((logger.value_pending, logger.expirations["T"]), ({}, "84000.12"))
            # An unpopulated value would have been written as an unreadable row.
            self.assertEqual(report.record_problem({**rows[0], "expiration_value": ""}),
                             "bad expiration_value")


class StickyAlarmTests(unittest.TestCase):
    def logger(self, directory, close):
        logger = ta.TallyLogger(Path(directory), ta.Feed())
        logger.prepare()
        logger.opened = {"T": {"ticker": "T", "close_time": ta.iso(close), "timeframes": {}}}
        return logger

    def test_one_failed_settle_pass_does_not_latch_the_alarm(self):
        close = 99000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            with patch.object(ta, "get_json", side_effect=OSError("Kalshi unavailable")):
                logger.settle_pending(close + 60)
            self.assertIn("tally_settle", logger.feed.errors)
            # Result in, value still awaited: a clean pass still clears the alarm.
            with patch.object(ta, "get_json", return_value={"market": {"result": "yes"}}):
                logger.settle_pending(close + 120)
            self.assertNotIn("tally_settle", logger.feed.errors)
            self.assertNotIn("T", logger.settled)

    def test_failed_value_pass_does_not_latch_the_alarm(self):
        close = 99000.0
        with tempfile.TemporaryDirectory() as directory:
            logger = self.logger(directory, close)
            logger.opened, logger.settled = {}, {"T"}
            logger.expirations, logger.value_pending = {"T": None}, {"T": close}
            with patch.object(ta, "get_json", side_effect=OSError("Kalshi unavailable")):
                logger.settle_pending(close + 60)
            self.assertIn("tally_settle", logger.feed.errors)
            real = {"market": {"result": "yes", "expiration_value": "84000.12"}}
            with patch.object(ta, "get_json", return_value=real):
                logger.settle_pending(close + 120)
            self.assertNotIn("tally_settle", logger.feed.errors)
            self.assertEqual(logger.value_pending, {})

    def test_one_failed_hydrate_pass_does_not_latch_the_alarm(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = ta.TallyLogger(Path(directory), ta.Feed())
            logger.prepare()
            logger.pending_open = {"T": {"type": "open", "ticker": "T", "target": None,
                                         "close_time": ta.iso(99000), "timeframes": {}}}
            with patch.object(ta, "get_json", side_effect=OSError("Kalshi unavailable")):
                logger.hydrate_pending(99000)
            self.assertIn("tally_open", logger.feed.errors)
            # Still listed without a target: a clean pass clears the alarm anyway.
            with patch.object(ta, "get_json", return_value={"market": {"floor_strike": None}}):
                logger.hydrate_pending(99000)
            self.assertNotIn("tally_open", logger.feed.errors)
            self.assertEqual(list(logger.pending_open), ["T"])
            with patch.object(ta, "get_json", return_value={"market": {"floor_strike": 101.25,
                                                                      "status": "active"}}):
                logger.hydrate_pending(99000)
            self.assertEqual(logger.opened["T"]["target"], 101.25)
            self.assertNotIn("tally_open", logger.feed.errors)


class CorruptPendingTests(unittest.TestCase):
    def test_corrupt_pending_queue_is_set_aside(self):
        for payload in ('{bad', '[]'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                state.joinpath("tally_pending.json").write_text(payload)
                logger = ta.TallyLogger(state, ta.Feed())
                with patch("sys.stderr", new_callable=io.StringIO) as warning:
                    logger.prepare()  # never raises
                self.assertEqual(logger.pending_open, {})
                self.assertFalse(state.joinpath("tally_pending.json").exists())
                moved = list(state.glob("tally_pending.corrupt-*"))
                self.assertEqual([path.read_text() for path in moved], [payload])
                self.assertIn("tally pending unreadable", warning.getvalue())

    def test_valid_pending_queue_keeps_dict_entries_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.joinpath("tally_pending.json").write_text(json.dumps({"T": {"ticker": "T"}, "B": 3}))
            logger = ta.TallyLogger(state, ta.Feed())
            logger.prepare()
            self.assertEqual(logger.pending_open, {"T": {"ticker": "T"}})
            self.assertEqual(state.joinpath("tally_pending.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(state.glob("tally_pending.corrupt-*")), [])


class BookSurvivesTradesFailureTests(unittest.TestCase):
    def test_orderbook_is_stored_when_the_trades_get_fails(self):
        feed = ta.Feed()
        feed.market = {"ticker": "KXBTC15M-CTX", "open_time": ta.iso(NOW - 300),
                       "close_time": ta.iso(NOW + 600)}

        def get(url, params=None, timeout=8):
            if url.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [["0.35", "12"]], "no_dollars": [["0.60", "7"]]}}
            raise OSError("Kalshi trades unavailable")

        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=NOW):
            with self.assertRaises(OSError):
                feed.poll_market_context()
        self.assertEqual(feed.book["yes_bid"], {"price": "0.35", "count": "12"})
        self.assertEqual(feed.book_at, NOW)
        self.assertEqual(feed.context_ticker, "KXBTC15M-CTX")
        self.assertEqual(list(feed.yes_mid_path), [{"time": int(NOW), "value": .375}])
        self.assertEqual(feed.kalshi_recent_trades, [])


class ReferenceBarTests(unittest.TestCase):
    def state(self, opens):
        feed = ready(ta.Feed(), NOW)
        feed.bars["1m"] = [{"time": t, "open": o, "high": o + 1, "low": o - 1,
                            "close": o, "volume": 1.0} for t, o in sorted(opens.items())]
        feed.fetched_last["1m"] = max(opens)
        with patch.object(ta.time, "time", return_value=NOW):
            return feed.state("1m")

    def bars(self, minutes=30):
        return {int(NOW) - 60 * i: 100.0 + i for i in range(1, minutes + 1)}

    def test_missing_reference_bar_is_none_not_a_younger_bar(self):
        state = self.state(self.bars())
        self.assertIsNone(state["ref_1h"])  # 60 minutes of bars are not there to quote
        self.assertEqual(state["ref_15m"], 115.0)  # the bar exactly 15 minutes back

    def test_bar_exactly_at_the_target_is_the_reference(self):
        opens = self.bars()
        opens[int(NOW) - 3600] = 87.5
        state = self.state(opens)
        self.assertEqual(state["ref_1h"], 87.5)
        self.assertEqual(state["ref_15m"], 115.0)


if __name__ == "__main__":
    unittest.main()
