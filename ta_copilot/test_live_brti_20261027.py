"""Offline fixture and fake-socket checks for the optional Kalshi feed."""
import base64
from decimal import Decimal
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

import kalshi_ws as ws
import ta_copilot as ta
import tally_report as report
from test_fix_20261002 import ready_feed

FIXTURE = Path(__file__).parent / "fixtures/kalshi_ws_20261027.jsonl.gz"


def frames():
    with gzip.open(FIXTURE, "rt") as source:
        return [json.loads(line) for line in source]


try:
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, padding
    from cryptography.hazmat.primitives import hashes, serialization
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))


class BookTests(unittest.TestCase):
    def test_fixture_book_matches_ticker_and_gap_is_detected(self):
        book = ws.BookStream()
        checked = 0
        gap = False
        for item in frames():
            frame = item["frame"]
            if frame["type"].startswith("orderbook_"):
                event = book.apply(frame)
                if event == "gap":
                    gap = True
                    self.assertIsNone(book.view(frame["sid"], frame["msg"]["market_ticker"]))
                    break
            elif frame["type"] == "ticker":
                ticker = frame["msg"]["market_ticker"]
                view = book.view(4, ticker)
                self.assertIsNotNone(view)
                context = ta.book_context(view)
                self.assertEqual(Decimal(context["yes_bid"]["price"]),
                                 Decimal(frame["msg"]["yes_bid_dollars"]))
                self.assertEqual(Decimal(context["yes_ask"]["price"]) if context["yes_ask"] else Decimal(1),
                                 Decimal(frame["msg"]["yes_ask_dollars"]))
                checked += 1
        self.assertTrue(gap)
        self.assertGreater(checked, 0)
        for item in frames():
            frame = item["frame"]
            if frame["type"].startswith("orderbook_"):
                book.apply(frame)
        ticker = "KXBTC15M-26OCT270100-00"
        self.assertEqual(book.view(4, ticker),
                         {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})

    def test_gap_sends_resubscribe_and_empty_snapshot_clears_book(self):
        feed, socket = ta.Feed(), FakeSocket()
        feed.ws_subs.sids["orderbook_delta"] = 4
        first = frames()
        active_ticker = next(item["frame"]["msg"]["market_ticker"] for item in first
                             if item["frame"]["type"] == "orderbook_snapshot")
        feed.market = {"ticker": active_ticker, "open_time": ta.iso(1793076300),
                       "close_time": ta.iso(1793077200)}
        before = None
        for item in first:
            frame = item["frame"]
            if frame["type"].startswith("orderbook_"):
                event = feed.handle_kalshi_frame(frame, item["recv_ms"] / 1000, socket)
                if event == "gap":
                    before = frame
                    break
        self.assertIsNotNone(before)
        self.assertEqual([x["cmd"] for x in socket.sent[-2:]], ["unsubscribe", "subscribe"])
        empty = {"type": "orderbook_snapshot", "sid": 9, "seq": 1,
                 "msg": {"market_ticker": before["msg"]["market_ticker"]}}
        feed.ws_books.apply(empty)
        self.assertEqual(feed.ws_books.view(9, empty["msg"]["market_ticker"]),
                         {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})


class TradeTests(unittest.TestCase):
    def test_fixture_trade_shape_and_dedupe(self):
        frame = next(item["frame"] for item in frames() if item["frame"]["type"] == "trade")
        ticker, row = ws.trade_row(frame)
        self.assertEqual(ta.parse_ts(row["time"]), frame["msg"]["ts_ms"] / 1000)
        raw = {"ticker": ticker, "created_time": row["time"],
               "yes_price_dollars": frame["msg"]["yes_price_dollars"],
               "count_fp": frame["msg"]["count_fp"], "taker_side": frame["msg"]["taker_side"],
               "is_block_trade": frame["msg"]["is_block_trade"],
               "trade_id": frame["msg"]["trade_id"]}
        rest = ta.kalshi_trade_rows({"trades": [raw]}, ticker, ta.parse_ts(row["time"]) - 1,
                                     ta.parse_ts(row["time"]) + 1)
        self.assertEqual(rest, [row])
        tape = ta.merge_tape({"rows": {row["trade_id"]: row}, "from": ta.parse_ts(row["time"]) - 1,
                              "to": ta.parse_ts(row["time"])}, rest, False,
                             ta.parse_ts(row["time"]) - 1, ta.parse_ts(row["time"]) + 1)
        self.assertEqual(len(tape["rows"]), 1)

    def test_contiguous_trade_subscription_and_gap(self):
        item = next(item for item in frames() if item["frame"]["type"] == "trade")
        at = item["recv_ms"] / 1000
        ticker = item["frame"]["msg"]["market_ticker"]
        for start, bridged in ((at - 100, False), (at - 10, True), (at - 10, False)):
            feed = ta.Feed()
            feed.market = {"ticker": ticker, "open_time": ta.iso(at - 50),
                           "close_time": ta.iso(at + 50)}
            feed.context_ticker = ticker
            feed.ws_trade_start[ticker] = start
            feed.ws_trade_contiguous.add(ticker)
            if bridged:
                feed.kalshi_tape = {"rows": {}, "from": at - 50, "to": at}
            feed.handle_kalshi_frame(item["frame"], at)
            self.assertEqual(len(feed.kalshi_tape["rows"]), 1)
            self.assertEqual(feed.kalshi_tape["from"], at - 50 if bridged or start <= at - 50 else None)
            gap = json.loads(json.dumps(item["frame"]))
            gap["seq"] += 2
            feed.handle_kalshi_frame(gap, at + 1)
            self.assertNotIn(ticker, feed.ws_trade_contiguous)


class BrtiTests(unittest.TestCase):
    def test_ticks_freshness_average_and_backfill(self):
        items = frames()
        one = next(item["frame"] for item in items if item["frame"]["type"] == "cfbenchmarks_value")
        five = next(item["frame"] for item in items if item["frame"]["type"] == "cfbenchmarks_value_5hz")
        whole_five = next(item["frame"] for item in items if item["frame"]["type"] == "cfbenchmarks_value_5hz"
                          and item["frame"]["msg"]["source_ts_ms"] % 1000 == 0)
        self.assertTrue(ws.brti_tick(one)[2])
        self.assertFalse(ws.brti_tick(five)[2])
        self.assertEqual(ws.brti_tick(whole_five), ws.brti_tick(one))
        feed = ta.Feed()
        source, value, _ = ws.brti_tick(one)
        feed.handle_kalshi_frame(whole_five, source / 1000 + .04)
        feed.handle_kalshi_frame(one, source / 1000 + .05)
        self.assertEqual(feed.brti_seconds[source // 1000], value)
        self.assertLess(source / 1000 + 1.9 - feed.brti_source_at, 2)
        self.assertGreater(source / 1000 + 2.1 - feed.brti_source_at, 2)
        held = {t: 100.0 for t in range(100, 160)}
        self.assertEqual(ws.brti_average(held, 100), 100)
        del held[120]
        self.assertIsNone(ws.brti_average(held, 100))
        feed.brti_seconds = {100: 101.0}
        response = {"data": {"payload": [{"time": 100000, "value": "999"},
                                          {"time": 101000, "value": "102"}]}}
        with patch.object(feed, "kalshi_signed_get", return_value=response) as get:
            self.assertTrue(feed.backfill_brti(200))
            self.assertFalse(feed.backfill_brti(220))
        get.assert_called_once()
        self.assertEqual(feed.brti_seconds[100], 101.0)
        self.assertEqual(feed.brti_seconds[101], 102.0)

    def test_settlement_math_and_proxy_fallback(self):
        close = 1000
        values = {t: float(t) for t in range(940, 1000)}
        full = ta.brti_settlement_average(values, close, close)
        self.assertTrue(full["complete"])
        self.assertEqual(full["value"], statistics.fmean(values.values()))
        del values[955]
        self.assertFalse(ta.brti_settlement_average(values, close, close)["complete"])
        row = ta.window_distance(1000, 990, 30, .01, close, [], brti=999, brti_samples=values)
        self.assertEqual(row["settlement_basis"], "BRTI")
        self.assertEqual(row["distance_source"], "projected 60s BRTI average")
        self.assertEqual(row["missing_seconds"], 1)
        proxy = ta.window_distance(1000, 990, 30, .01, close, [])
        self.assertEqual(proxy, ta.window_distance(1000, 990, 30, .01, close, [], brti=None))
        self.assertEqual(proxy["settlement_basis"], "proxy")


class CredentialTests(unittest.TestCase):
    def test_permissions_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "kalshi"
            directory.mkdir(mode=0o700)
            (directory / "key_id").write_text("throwaway\n")
            (directory / "private_key.pem").write_bytes(b"throwaway private bytes")
            os.chmod(directory / "private_key.pem", 0o644)
            self.assertIn("group or others", ws.load_credentials(directory)[1])
            os.chmod(directory / "private_key.pem", 0o600)
            os.chmod(directory, 0o755)
            self.assertIn("group or others", ws.load_credentials(directory)[1])
        class FakeKey:
            key_id = "throwaway"
            def headers(self, method, path):
                return {}
        for scopes in (["read"], ["read", "read::portfolio_balance"]):
            self.assertTrue(ws.check_scopes(FakeKey(), lambda _: {"api_keys": [
                {"api_key_id": "throwaway", "scopes": scopes}]}))
        for data in ({"api_keys": [{"api_key_id": "throwaway", "scopes": ["read", "write"]}]},
                     {"api_keys": [{"api_key_id": "throwaway", "scopes": ["write::trade"]}]},
                     {"api_keys": []}, {"api_keys": "invalid"}):
            with self.assertRaises(ws.KeyErrorSafe):
                ws.check_scopes(FakeKey(), lambda _, data=data: data)

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography asymmetric modules unavailable")
    def test_signatures_allowlist_and_no_secrets_in_state(self):
        for key in (ed25519.Ed25519PrivateKey.generate(), rsa.generate_private_key(65537, 2048)):
            credential = ws.Credentials("throwaway", key)
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "kalshi"
                directory.mkdir(mode=0o700)
                (directory / "key_id").write_text("throwaway\n")
                private = directory / "private_key.pem"
                private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                os.chmod(private, 0o600)
                loaded, error = ws.load_credentials(directory)
                self.assertIsNone(error)
                self.assertIsInstance(loaded.private_key, type(key))
            headers = credential.headers("GET", "/trade-api/v2/cfbenchmarks/values?id=BRTI", 1234)
            message = b"1234GET/trade-api/v2/cfbenchmarks/values"
            signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
            if isinstance(key, ed25519.Ed25519PrivateKey):
                key.public_key().verify(signature, message)
            else:
                key.public_key().verify(signature, message, padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
            for method, path in (("POST", "/trade-api/ws/v2"),
                                 ("GET", "/trade-api/v2/portfolio/balance")):
                with self.assertRaises(ValueError):
                    credential.headers(method, path)
            self.assertNotIn("throwaway", repr(credential))
            feed = ready_feed(1100)
            feed.kalshi_key = credential
            with feed.lock:
                feed._kalshi_error("kalshi_key", "scope check unavailable")
            with patch.object(ta.time, "time", return_value=1100):
                state = feed.state("1m")
            rendered = json.dumps(state)
            self.assertNotIn("throwaway", rendered)
            self.assertNotIn("BEGIN PRIVATE KEY", rendered)


class IntegrationTests(unittest.TestCase):
    def test_websocket_book_skips_rest_then_gap_restores_poll(self):
        now = 1100
        feed = ready_feed(now)
        feed.market = {"ticker": "T", "open_time": ta.iso(1000),
                       "close_time": ta.iso(1900), "floor_strike": 100}
        feed.ws_subs.sids["orderbook_delta"] = 4
        feed.ws_books.apply({"type": "orderbook_snapshot", "sid": 4, "seq": 1,
            "msg": {"market_ticker": "T", "yes_dollars_fp": [["0.45", "10"]],
                    "no_dollars_fp": [["0.50", "10"]]}})
        feed.kalshi_connected = True
        feed.kalshi_last_at = now
        calls = []
        def get(url, params=None, timeout=8):
            calls.append(url)
            return ({"orderbook_fp": {"yes_dollars": [["0.40", "10"]],
                                      "no_dollars": [["0.55", "10"]]}}
                    if url.endswith("/orderbook") else {"trades": []})
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            feed.poll_market_context()
            self.assertEqual(feed.book_source, "websocket")
            self.assertFalse(any(url.endswith("/orderbook") for url in calls))
            feed.ws_books.invalidate(4)
            feed.poll_market_context()
        self.assertEqual(feed.book_source, "rest")
        self.assertTrue(any(url.endswith("/orderbook") for url in calls))
        self.assertEqual(len(feed.yes_mid_path), 1)

    def test_state_reads_websocket_book_live_between_context_polls(self):
        now = 1100
        feed = ready_feed(now)
        feed.market = {"ticker": "T", "open_time": ta.iso(1000),
                       "close_time": ta.iso(1900), "floor_strike": 100}
        feed.market_at = now
        feed.ws_subs.sids["orderbook_delta"] = 4
        feed.ws_books.apply({"type": "orderbook_snapshot", "sid": 4, "seq": 1,
            "msg": {"market_ticker": "T", "yes_dollars_fp": [["0.4500", "10.00"]],
                    "no_dollars_fp": [["0.5000", "10.00"]]}})
        feed.kalshi_connected = True
        feed.kalshi_last_at = now
        with patch.object(ta, "get_json", side_effect=AssertionError("no REST book")), \
                patch.object(ta.time, "time", return_value=now):
            feed.handle_kalshi_frame({"type": "orderbook_delta", "sid": 4, "seq": 2, "msg": {
                "market_ticker": "T", "price_dollars": "0.4700", "delta_fp": "5.00",
                "side": "yes", "ts_ms": 1100000}}, now)
            window = feed.state("1m")["window"]
        self.assertEqual(window["orderbook_source"], "websocket")
        self.assertEqual(Decimal(window["orderbook"]["yes_bid"]["price"]), Decimal("0.47"))
        self.assertEqual(window["display_quotes"]["yes_bid"]["source"], "book")

    def test_update_ack_seq_is_not_a_gap(self):
        # Live 2026-10-27: book seq 13495, ok 13496, new market's snapshot 13497.
        feed, socket = ta.Feed(), FakeSocket()
        feed.ws_subs.sids.update({"orderbook_delta": 1, "trade": 2})
        feed.handle_kalshi_frame({"type": "orderbook_snapshot", "sid": 1, "seq": 1,
            "msg": {"market_ticker": "T", "yes_dollars_fp": [["0.4000", "1.00"]]}}, 1.0, socket)
        feed.handle_kalshi_frame({"type": "trade", "sid": 2, "seq": 1, "msg": {
            "market_ticker": "T", "trade_id": "a", "yes_price_dollars": "0.40",
            "count_fp": "1", "taker_side": "yes", "ts_ms": 1000}}, 1.0, socket)
        feed.handle_kalshi_frame({"type": "ok", "sid": 1, "seq": 2,
            "msg": {"market_tickers": ["T", "U"]}}, 2.0, socket)
        feed.handle_kalshi_frame({"type": "ok", "sid": 2, "seq": 2,
            "msg": {"market_tickers": ["T", "U"]}}, 2.0, socket)
        event = feed.handle_kalshi_frame({"type": "orderbook_snapshot", "sid": 1, "seq": 3,
            "msg": {"market_ticker": "U", "yes_dollars_fp": [["0.5000", "1.00"]]}}, 2.1, socket)
        feed.handle_kalshi_frame({"type": "trade", "sid": 2, "seq": 3, "msg": {
            "market_ticker": "T", "trade_id": "b", "yes_price_dollars": "0.40",
            "count_fp": "1", "taker_side": "yes", "ts_ms": 2000}}, 2.2, socket)
        self.assertEqual(event, "snapshot")
        self.assertIsNotNone(feed.ws_books.view(1, "T"))
        self.assertEqual(feed.ws_trade_seq[2], 3)
        self.assertEqual(socket.sent, [])

    def test_recent_trades_stay_newest_30_on_a_large_tape(self):
        feed = ta.Feed()
        feed.market = {"ticker": "T", "open_time": ta.iso(1000), "close_time": ta.iso(1900)}
        def trade(seq, ms):
            return {"type": "trade", "sid": 5, "seq": seq, "msg": {
                "market_ticker": "T", "trade_id": f"t{ms}", "yes_price_dollars": "0.50",
                "count_fp": "1", "taker_side": "yes", "ts_ms": ms}}
        seq = 0
        for ms in range(1000000, 1000000 + 250 * 400, 400):  # 250 trades, beyond the full-sort size
            seq += 1
            feed.handle_kalshi_frame(trade(seq, ms), ms / 1000)
        expected = sorted(feed.kalshi_tape["rows"].values(), key=lambda r: r["time"], reverse=True)[:30]
        strip = lambda rows: [{k: v for k, v in r.items() if k != "trade_id"} for r in rows]
        self.assertEqual(feed.kalshi_recent_trades, strip(expected))
        seq += 1
        feed.handle_kalshi_frame(trade(seq, 1000000 + 249 * 400), 1100)  # duplicate trade_id
        self.assertEqual(feed.kalshi_recent_trades, strip(expected))
        seq += 1
        feed.handle_kalshi_frame(trade(seq, 1000000 + 5 * 400 + 1), 1100)  # late, older trade
        self.assertEqual(feed.kalshi_recent_trades, strip(expected))
        self.assertEqual(len(feed.kalshi_tape["rows"]), 251)

    def test_trades_before_promotion_are_adopted_into_new_tape(self):
        feed = ta.Feed()
        feed.market = {"ticker": "T", "open_time": ta.iso(100), "close_time": ta.iso(1000)}
        feed.upcoming_markets = {"U": {"ticker": "U", "open_time": ta.iso(1000),
                                       "close_time": ta.iso(1900)}}
        feed.ws_trade_start["U"] = 900
        feed.ws_trade_contiguous.add("U")
        def trade(seq, ms):
            return {"type": "trade", "sid": 5, "seq": seq, "msg": {
                "market_ticker": "U", "trade_id": f"u{seq}", "yes_price_dollars": "0.50",
                "count_fp": "1", "taker_side": "yes", "ts_ms": ms}}
        feed.handle_kalshi_frame(trade(1, 1000300), 1000.4)  # before the sampler promotes U
        self.assertIsNone(feed.kalshi_tape)
        feed.market = feed.upcoming_markets["U"]
        feed.handle_kalshi_frame(trade(2, 1001200), 1001.3)
        self.assertEqual(set(feed.kalshi_tape["rows"]), {"u1", "u2"})
        self.assertEqual(feed.kalshi_tape["from"], 1000)
        self.assertEqual(feed.ws_early_trades, {})

    def test_trade_rest_bridge_and_gap_fallback(self):
        now = 1100
        feed = ready_feed(now)
        feed.market = {"ticker": "T", "open_time": ta.iso(1000),
                       "close_time": ta.iso(1900), "floor_strike": 100}
        feed.context_ticker = "T"
        feed.kalshi_connected = True
        feed.kalshi_last_at = now
        feed.ws_trade_start["T"] = 1090
        feed.ws_trade_contiguous.add("T")
        feed.kalshi_tape = {"rows": {}, "from": 1000, "to": now}
        calls = []
        def get(url, params=None, timeout=8):
            calls.append(url)
            return ({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
                    if url.endswith("/orderbook") else {"trades": []})
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=now):
            feed.poll_market_context()
            self.assertEqual(feed.tape_source, "rest")
            feed.poll_market_context()
            self.assertEqual(feed.tape_source, "websocket")
        self.assertEqual(sum(url.endswith("/markets/trades") for url in calls), 1)
        def trade(seq):
            return {"type": "trade", "sid": 5, "seq": seq, "msg": {
                "market_ticker": "T", "trade_id": str(seq), "yes_price_dollars": "0.50",
                "count_fp": "1", "taker_side": "yes", "ts_ms": 1101000}}
        feed.handle_kalshi_frame(trade(1), 1101)
        feed.handle_kalshi_frame(trade(3), 1102)
        self.assertEqual(feed.tape_source, "websocket_disconnected")
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=1102):
            feed.poll_market_context()
        self.assertEqual(sum(url.endswith("/markets/trades") for url in calls), 2)

    def test_keyless_state_and_no_websocket_thread(self):
        now = 1100
        feed = ready_feed(now)
        with patch.object(ta.time, "time", return_value=now):
            state = feed.state("1m")
        self.assertFalse(state["brti"]["configured"])
        self.assertEqual(state["feed_modes"]["Kalshi"], "keyless")
        self.assertIsNone(feed.kalshi_key)
        self.assertNotIn("throwaway", json.dumps(state))
        started = []
        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                started.append(target)
            def start(self):
                pass
        class FakeServer:
            def __init__(self, *args):
                pass
            def serve_forever(self):
                pass
        with patch.object(ta, "FEED", ta.Feed()), \
             patch.object(ta.kalshi_ws, "load_credentials", return_value=(None, "not configured")), \
             patch.object(ta.threading, "Thread", FakeThread), \
             patch.object(ta, "ThreadingHTTPServer", FakeServer), \
             patch.object(sys, "argv", ["ta_copilot.py"]), \
             patch("sys.stdout", new_callable=io.StringIO):
            ta.main()
        self.assertFalse(any(getattr(target, "__name__", "") == "run_kalshi_ws"
                             for target in started))

    def test_log_fields_and_primary_digest(self):
        minute = ta.minute_record({"ready": True, "window": {"ticker": "T",
            "open_time": ta.iso(1000), "close_time": ta.iso(1900),
            "seconds_left": 840}, "frames": {}, "brti": {"value": 100.0,
            "age": .5, "fresh": True}}, 1060)
        self.assertEqual((minute["brti"], minute["brti_age"]), (100.0, .5))
        feed = ta.Feed()
        feed.market = {"ticker": "T", "close_time": ta.iso(1000)}
        feed.samples.extend((t, 99.0) for t in range(940, 1000))
        feed.brti_seconds = {t: 100.0 for t in range(940, 1000)}
        actual_close = feed._close_proxy(1000, 1000.05)
        self.assertEqual((actual_close["brti_avg_60s"], actual_close["brti_samples"]),
                         (100.0, 60))
        self.assertEqual(actual_close["proxy_avg_60s"], 99.0)
        row = {"type": "minute", "ticker": "T", "minute": 1, "captured_at": ta.iso(1060),
               "brti": 100.0, "brti_age": .5}
        self.assertIsNone(report.record_problem(row))
        row["brti"] = math.nan
        self.assertEqual(report.record_problem(row), "bad brti")
        close = {"type": "close", "ticker": "T", "brti_avg_60s": 100.0, "brti_samples": 60}
        self.assertIsNone(report.record_problem(close))
        source = (Path(__file__).parent / "tally_report.py").read_text()
        primary = source[source.index("def primary_result("):source.index("def primary_section(")]
        self.assertEqual(hashlib.sha256(primary.encode()).hexdigest(),
                         "0ee39f0ea8c97779607720e75872c17c7ee70e3f10c5639e29a2d56aea1485f3")
        self.assertNotIn("Live BRTI", report.report({}, {}, {}, closes={}))
        calibration = report.report({"T": {"target": 100}},
            {"T": {"result": "no", "expiration_value": "99.99"}}, {}, closes={
                "T": {"type": "close", "ticker": "T", "brti_samples": 60,
                      "brti_avg_60s": 100.01}}, calibration=True)
        self.assertIn("side-vs-target mismatches 1/1", calibration)

    def test_rollover_commands(self):
        feed, socket = ta.Feed(), FakeSocket()
        feed.market = {"ticker": "OLD", "open_time": ta.iso(100), "close_time": ta.iso(1000)}
        feed.upcoming_markets = {"NEW": {"ticker": "NEW", "open_time": ta.iso(1000),
                                          "close_time": ta.iso(1900)}}
        self.assertEqual(feed.desired_kalshi_tickers(939), {"OLD"})
        self.assertEqual(feed.desired_kalshi_tickers(940), {"OLD", "NEW"})
        feed.ws_subs.sids["orderbook_delta"] = 4
        feed.ws_subs.tickers[4] = {"OLD"}
        for command in feed.ws_subs.update("orderbook_delta", feed.desired_kalshi_tickers(940), 940):
            feed._ws_send(socket, command)
        self.assertEqual(socket.sent[-1]["params"]["action"], "add_markets")
        feed.ws_subs.ack({"type": "ok", "id": socket.sent[-1]["id"], "sid": 4,
                          "msg": {"market_tickers": ["OLD", "NEW"]}})
        for command in feed.ws_subs.update("orderbook_delta", feed.desired_kalshi_tickers(1000), 1000):
            feed._ws_send(socket, command)
        self.assertEqual(socket.sent[-1]["params"], {"sid": 4, "action": "delete_markets",
                                                     "market_tickers": ["OLD"]})


if __name__ == "__main__":
    unittest.main()


def trade_frame(sid, seq, ticker, trade_id, ms):
    return {"type": "trade", "sid": sid, "seq": seq, "msg": {
        "market_ticker": ticker, "trade_id": trade_id, "yes_price_dollars": "0.50",
        "count_fp": "1", "taker_side": "yes", "ts_ms": ms}}


def rest_trade(trade_id, at):
    return {"ticker": "T", "trade_id": trade_id, "created_time": ta.iso(at),
            "yes_price_dollars": "0.50", "count_fp": "1", "taker_side": "yes"}


class AstraQATests(unittest.TestCase):
    """Regressions for the 2026-10-27 GPT-6 Astra QA (history/QA_REPORT_20261027_astra.md)."""

    def market_feed(self, now):
        feed = ready_feed(now)
        feed.market = {"ticker": "T", "open_time": ta.iso(1000), "close_time": ta.iso(1900), "floor_strike": 100}
        feed.market_at = now
        feed.context_ticker = "T"
        return feed

    def test_1_unbridged_websocket_rows_never_stop_rest_paging(self):
        feed = self.market_feed(1100)
        feed.kalshi_tape = {"rows": {"r1040": {**ta.kalshi_trade_rows({"trades": [rest_trade("r1040", 1040)]},
                                                                      "T", 1000, 1900)[0]}},
                            "from": 1000, "to": 1050}
        feed.rest_tape_at = 1050
        feed.ws_trade_start["T"] = 1090
        feed.ws_trade_contiguous.add("T")
        feed.handle_kalshi_frame(trade_frame(5, 1, "T", "w1095", 1095000), 1095)  # unbridged, pending
        feed.handle_kalshi_frame(trade_frame(5, 3, "T", "w1097", 1097000), 1097)  # seq gap
        pages = []
        def get(url, params=None, timeout=8):
            if url.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
            pages.append(params.get("cursor"))
            if not params.get("cursor"):  # newest full page, overlapping only the WebSocket row
                page = [rest_trade("w1095", 1095)] + [rest_trade(f"n{i}", 1096 + i / 100) for i in range(99)]
                return {"trades": page, "cursor": "older"}
            return {"trades": [rest_trade("r1075", 1075), rest_trade("r1040", 1040)], "cursor": ""}
        with patch.object(ta, "get_json", side_effect=get), patch.object(ta.time, "time", return_value=1100):
            feed.poll_market_context()
        self.assertEqual(len(pages), 2, "REST paging stopped on an unbridged WebSocket row")
        self.assertIn("r1075", feed.kalshi_tape["rows"])

    def test_2_bridge_needs_a_rest_request_started_after_subscription(self):
        clock = [1100.0]
        feed = self.market_feed(1100)
        feed.kalshi_tape = {"rows": {}, "from": 1000, "to": 1099}
        feed.rest_tape_at = 1099
        feed.kalshi_connected = True
        feed.ws_trade_start["T"] = 1100.5
        feed.ws_trade_contiguous.add("T")
        def fetch(ticker, opened, reach, held_ids=()):
            clock[0] = 1101.0  # the response lands after the subscription started
            return [], False
        def get(url, params=None, timeout=8):
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        with patch.object(ta, "fetch_trades", side_effect=fetch), patch.object(ta, "get_json", side_effect=get), \
                patch.object(ta.time, "time", side_effect=lambda: clock[0]):
            feed.kalshi_last_at = 1101.0
            feed.poll_market_context()
            clock[0] = 1101.5
            feed.kalshi_last_at = 1101.5
            feed.poll_market_context()
        self.assertEqual(feed.tape_source, "rest")

    def test_3_seq_gap_on_a_control_frame_invalidates(self):
        feed, socket = ta.Feed(), FakeSocket()
        feed.market = {"ticker": "T", "open_time": ta.iso(1000), "close_time": ta.iso(1900)}
        feed.ws_subs.sids.update({"orderbook_delta": 4, "trade": 5})
        feed.ws_subs.tickers.update({4: {"T"}, 5: {"T"}})
        feed.handle_kalshi_frame({"type": "orderbook_snapshot", "sid": 4, "seq": 10,
            "msg": {"market_ticker": "T", "yes_dollars_fp": [["0.4000", "1.00"]]}}, 1100, socket)
        feed.ws_trade_contiguous.add("T")
        feed.handle_kalshi_frame(trade_frame(5, 10, "T", "a", 1100000), 1100, socket)
        feed.handle_kalshi_frame({"type": "ok", "sid": 4, "seq": 12, "msg": {}}, 1101, socket)
        feed.handle_kalshi_frame({"type": "ok", "sid": 5, "seq": 12, "msg": {}}, 1101, socket)
        self.assertIsNone(feed.ws_books.view(4, "T"))
        self.assertNotIn("T", feed.ws_trade_contiguous)
        self.assertEqual({c["params"]["channels"][0] for c in socket.sent if c["cmd"] == "subscribe"},
                         {"orderbook_delta", "trade"})

    def test_4_trade_rollover_drops_previous_market_book(self):
        now = 1900.8
        feed = ready_feed(now)
        feed.market = {"ticker": "U", "open_time": ta.iso(1900), "close_time": ta.iso(2800), "floor_strike": 100}
        feed.market_at = now
        feed.context_ticker = "T"
        feed.book = ta.book_context({"orderbook_fp": {"yes_dollars": [["0.40", "1"]], "no_dollars": [["0.50", "1"]]}})
        feed.book_at = now
        feed.yes_mid_path.append({"time": 1899, "value": 0.45})
        feed.handle_kalshi_frame(trade_frame(5, 1, "U", "u1", 1900500), now)
        with patch.object(ta.time, "time", return_value=now):
            window = feed.state("1m")["window"]
        self.assertEqual(window["ticker"], "U")
        self.assertIsNone(window["orderbook"])
        self.assertEqual(window["yes_mid_path"], [])

    def test_5_logged_sigma_stays_composite_based(self):
        now = 1100
        feed = self.market_feed(now)
        for i, bar in enumerate(feed.bars["1m"]):  # nonzero realized sigma
            bar["close"] = 100.0 + (i % 2)
        feed.kalshi_key, feed.kalshi_scoped = object(), True
        feed.brti_value, feed.brti_source_at = 110.0, now
        with patch.object(ta.time, "time", return_value=now):
            with_brti = feed.state("1m")
            feed.kalshi_key = None
            keyless = feed.state("1m")
        self.assertEqual(with_brti["window"]["settlement_basis"], "BRTI")
        self.assertEqual(with_brti["window"]["sigma_1m_usd"], keyless["window"]["sigma_1m_usd"])
        self.assertEqual(ta.minute_record(with_brti, 1060)["sigma_1m_usd"],
                         ta.minute_record(keyless, 1060)["sigma_1m_usd"])

    def test_6_mid_path_keeps_a_full_window_and_keyless_cadence(self):
        feed = self.market_feed(1100)
        self.assertGreaterEqual(feed.yes_mid_path.maxlen, 900)
        feed.book = ta.book_context({"orderbook_fp": {"yes_dollars": [["0.40", "1"]], "no_dollars": [["0.50", "1"]]}})
        feed.book_at = 1100
        feed.sample_composite(1101.05)  # keyless: only the REST context poll adds mid points
        self.assertEqual(list(feed.yes_mid_path), [])
