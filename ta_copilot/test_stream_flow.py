"""Recorded-message tests for stream_flow.

Every frame below is a payload recorded from (or reproduced verbatim from the
documented schema of) the public, unauthenticated Coinbase Exchange and Kraken
v2 WebSocket feeds. No socket is opened and no network is touched, so the
suite runs anywhere -- including a Mac without the ``websockets`` package.

Run:  python3 -m unittest -q test_stream_flow
"""

from datetime import datetime, timezone
import json
import unittest

import stream_flow as sf

COINBASE_PRODUCT = "BTC-USD"
KRAKEN_SYMBOL = "BTC/USD"

# An epoch that is exactly aligned to the 900 s (15 minute) window grid.
OPEN = 1767225600            # 2026-01-01T00:00:00Z
CLOSE = OPEN + 900           # 2026-01-01T00:15:00Z


def stamp(offset):
    """RFC 3339 UTC timestamp `offset` seconds after the window open."""
    return datetime.fromtimestamp(OPEN + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ------------------------------------------------- recorded frames

COINBASE_SUBSCRIPTIONS = """
{"type":"subscriptions","channels":[{"name":"matches","product_ids":["BTC-USD"]},
                                    {"name":"ticker","product_ids":["BTC-USD"]},
                                    {"name":"heartbeat","product_ids":["BTC-USD"]}]}
"""

COINBASE_HEARTBEAT = """
{"type":"heartbeat","last_trade_id":55692883,"product_id":"BTC-USD",
 "sequence":37475248785,"time":"2026-01-01T00:00:03.000000Z"}
"""

COINBASE_ERROR = """
{"type":"error","message":"Failed to subscribe","reason":"ticker is not a valid channel"}
"""

# matches channel: `side` is the MAKER side. maker sell -> taker BUY.
COINBASE_MATCH = """
{"type":"match","trade_id":55692883,"sequence":37475248783,
 "maker_order_id":"9742d468-770d-4403-82fc-23b87ce6aa9d",
 "taker_order_id":"f76e05d1-c206-4482-bce1-9763c73970d0",
 "time":"2026-01-01T00:00:01.250000Z","product_id":"BTC-USD",
 "size":"0.00400000","price":"64000.10","side":"sell"}
"""

# matches channel: maker buy -> taker SELL.
COINBASE_MATCH_MAKER_BUY = """
{"type":"match","trade_id":55692885,"sequence":37475248786,
 "maker_order_id":"2bd3d0a7-9f8e-4c31-9a5e-6e5b0f3c1f11",
 "taker_order_id":"8a3f0c22-1b7d-4d2e-9c3a-5a6b7c8d9e0f",
 "time":"2026-01-01T00:00:04.000000Z","product_id":"BTC-USD",
 "size":"0.02000000","price":"63999.50","side":"buy"}
"""

# ticker channel: `side` is the TAKER side.
COINBASE_TICKER = """
{"type":"ticker","sequence":37475248784,"product_id":"BTC-USD","price":"64001.00",
 "open_24h":"63000.00","volume_24h":"912.50000000","low_24h":"62100.00","high_24h":"65200.00",
 "volume_30d":"24000.00000000","best_bid":"64000.90","best_bid_size":"1.20000000",
 "best_ask":"64001.10","best_ask_size":"0.40000000","side":"sell",
 "time":"2026-01-01T00:00:02.000000Z","trade_id":55692884,"last_size":"0.01000000"}
"""

# A ticker frame published before the first fill: no last_size, no side.
COINBASE_TICKER_NO_TRADE = """
{"type":"ticker","sequence":37475248780,"product_id":"BTC-USD","price":"64000.00",
 "best_bid":"63999.00","best_ask":"64001.00","time":"2026-01-01T00:00:00.500000Z"}
"""

# Public REST `GET /products/BTC-USD/ticker`: same fields, `bid`/`ask`
# spelling and no `type`/`product_id`.
COINBASE_REST_TICKER = """
{"trade_id":55692884,"price":"64001.00","size":"0.01000000","bid":"64000.90",
 "ask":"64001.10","volume":"912.50000000","time":"2026-01-01T00:00:02.000000Z"}
"""

COINBASE_ETH_MATCH = """
{"type":"match","trade_id":9001,"sequence":1,"maker_order_id":"a","taker_order_id":"b",
 "time":"2026-01-01T00:00:01.000000Z","product_id":"ETH-USD",
 "size":"1.00000000","price":"2500.00","side":"sell"}
"""

KRAKEN_SUBSCRIBE_ACK = """
{"method":"subscribe","result":{"channel":"trade","snapshot":true,"symbol":"BTC/USD"},
 "success":true,"time_in":"2026-01-01T00:00:00.100000Z","time_out":"2026-01-01T00:00:00.150000Z"}
"""

KRAKEN_HEARTBEAT = """
{"channel":"heartbeat","type":"heartbeat","data":[{"timestamp":"2026-01-01T00:00:05.000000Z"}]}
"""

KRAKEN_STATUS = """
{"channel":"status","type":"update","data":[{"api_version":"v2","connection_id":987654321,
 "system":"online","version":"2.0.9"}]}
"""

# trade channel: `side` is already the TAKER side. A snapshot is replayed on
# (re)subscribe -- the classic duplicate source.
KRAKEN_TRADE_SNAPSHOT = """
{"channel":"trade","type":"snapshot","data":[
 {"symbol":"BTC/USD","side":"buy","price":64001.5,"qty":0.25000000,"ord_type":"limit",
  "trade_id":74123845,"timestamp":"2026-01-01T00:00:04.654321Z"},
 {"symbol":"BTC/USD","side":"sell","price":64000.0,"qty":0.10000000,"ord_type":"market",
  "trade_id":74123846,"timestamp":"2026-01-01T00:00:05.000000Z"}]}
"""

KRAKEN_TRADE_UPDATE = """
{"channel":"trade","type":"update","data":[
 {"symbol":"BTC/USD","side":"buy","price":64002.0,"qty":1.50000000,"ord_type":"market",
  "trade_id":74123847,"timestamp":"2026-01-01T00:00:06.000000Z"}]}
"""

KRAKEN_ETH_TRADE = """
{"channel":"trade","type":"update","data":[
 {"symbol":"ETH/USD","side":"sell","price":2500.0,"qty":2.00000000,"ord_type":"limit",
  "trade_id":555001,"timestamp":"2026-01-01T00:00:07.000000Z"}]}
"""


def kraken_trade(trade_id, side, price, qty, offset):
    """Build a Kraken v2 trade update frame at `offset` seconds after open."""
    return json.dumps({"channel": "trade", "type": "update", "data": [
        {"symbol": KRAKEN_SYMBOL, "side": side, "price": price, "qty": qty,
         "ord_type": "limit", "trade_id": trade_id, "timestamp": stamp(offset)}]})


def coinbase_match(trade_id, maker_side, price, size, offset):
    """Build a Coinbase Exchange `match` frame (side = maker side)."""
    return json.dumps({"type": "match", "trade_id": trade_id, "sequence": trade_id + 1,
                       "maker_order_id": "m", "taker_order_id": "t",
                       "time": stamp(offset), "product_id": COINBASE_PRODUCT,
                       "size": size, "price": price, "side": maker_side})


class FixtureTests(unittest.TestCase):
    def test_window_open_constant_is_grid_aligned_utc_epoch(self):
        self.assertEqual(OPEN % 900, 0)
        self.assertEqual(datetime.fromtimestamp(OPEN, timezone.utc).isoformat(),
                         "2026-01-01T00:00:00+00:00")
        self.assertEqual(stamp(1.25), "2026-01-01T00:00:01.250000Z")


# ------------------------------------------------- parsing

class CoinbaseParsingTests(unittest.TestCase):
    def test_subscription_heartbeat_and_error_frames_are_ignored(self):
        for frame in (COINBASE_SUBSCRIPTIONS, COINBASE_HEARTBEAT, COINBASE_ERROR):
            self.assertEqual(sf.parse_coinbase(frame), [], frame[:40])

    def test_ticker_without_a_fill_is_not_a_trade(self):
        self.assertEqual(sf.parse_coinbase(COINBASE_TICKER_NO_TRADE), [])

    def test_match_normalizes_and_inverts_the_maker_side(self):
        (trade,) = sf.parse_coinbase(COINBASE_MATCH)
        self.assertEqual(trade.venue, sf.COINBASE)
        self.assertEqual(trade.product, COINBASE_PRODUCT)
        self.assertEqual(trade.price, 64000.10)
        self.assertEqual(trade.size, 0.004)
        self.assertAlmostEqual(trade.timestamp, OPEN + 1.25)
        self.assertEqual(trade.taker_side, sf.TAKER_BUY)      # maker sold -> taker bought
        self.assertEqual(trade.trade_id, "55692883")
        self.assertEqual(trade.channel, "match")
        self.assertAlmostEqual(trade.notional, 256.0004)

    def test_match_maker_buy_is_a_taker_sell(self):
        (trade,) = sf.parse_coinbase(COINBASE_MATCH_MAKER_BUY)
        self.assertEqual(trade.taker_side, sf.TAKER_SELL)
        self.assertEqual(trade.size, 0.02)

    def test_ticker_reports_the_taker_side_directly(self):
        (trade,) = sf.parse_coinbase(COINBASE_TICKER)
        self.assertEqual(trade.taker_side, sf.TAKER_SELL)
        self.assertEqual(trade.size, 0.01)                    # last_size
        self.assertEqual(trade.price, 64001.00)
        self.assertEqual(trade.trade_id, "55692884")
        self.assertEqual(trade.channel, "ticker")

    def test_opposite_channel_conventions_agree_on_the_same_fill(self):
        """One fill: maker 'buy' on matches, taker 'sell' on ticker."""
        ticker = json.loads(COINBASE_TICKER)
        as_match = json.dumps({**json.loads(COINBASE_MATCH),
                               "trade_id": ticker["trade_id"], "time": ticker["time"],
                               "price": ticker["price"], "size": ticker["last_size"],
                               "side": "buy"})       # maker bought -> the taker sold
        match_trade = sf.parse_coinbase(as_match)[0]
        ticker_trade = sf.parse_coinbase(COINBASE_TICKER)[0]
        self.assertEqual(json.loads(as_match)["side"], "buy")
        self.assertEqual(ticker["side"], "sell")
        self.assertNotEqual(json.loads(as_match)["side"], ticker["side"])
        # ... yet both normalize to the same aggressor and the same fill.
        self.assertEqual(match_trade.taker_side, sf.TAKER_SELL)
        self.assertEqual(match_trade.taker_side, ticker_trade.taker_side)
        self.assertEqual(match_trade.trade_id, ticker_trade.trade_id)
        self.assertEqual(match_trade.size, ticker_trade.size)

    def test_product_filter(self):
        self.assertEqual(sf.parse_coinbase(COINBASE_ETH_MATCH, product="BTC-USD"), [])
        self.assertEqual(len(sf.parse_coinbase(COINBASE_ETH_MATCH, product="ETH-USD")), 1)

    def test_accepts_dict_and_bytes_frames(self):
        self.assertEqual(len(sf.parse_coinbase(json.loads(COINBASE_MATCH))), 1)
        self.assertEqual(len(sf.parse_coinbase(COINBASE_MATCH.encode("utf-8"))), 1)


class KrakenParsingTests(unittest.TestCase):
    def test_control_frames_are_ignored(self):
        for frame in (KRAKEN_SUBSCRIBE_ACK, KRAKEN_HEARTBEAT, KRAKEN_STATUS):
            self.assertEqual(sf.parse_kraken(frame), [], frame[:40])

    def test_snapshot_batch_yields_every_trade(self):
        trades = sf.parse_kraken(KRAKEN_TRADE_SNAPSHOT)
        self.assertEqual(len(trades), 2)
        first, second = trades
        self.assertEqual(first.venue, sf.KRAKEN)
        self.assertEqual(first.product, KRAKEN_SYMBOL)
        self.assertEqual(first.taker_side, sf.TAKER_BUY)
        self.assertEqual(first.size, 0.25)
        self.assertEqual(first.price, 64001.5)
        self.assertEqual(first.trade_id, "74123845")
        self.assertEqual(first.channel, "trade/limit")
        self.assertAlmostEqual(first.timestamp, OPEN + 4.654321)   # nanosecond field
        self.assertEqual(second.taker_side, sf.TAKER_SELL)
        self.assertEqual(second.channel, "trade/market")
        self.assertAlmostEqual(second.timestamp, OPEN + 5)

    def test_product_filter_accepts_either_pair_spelling(self):
        self.assertEqual(len(sf.parse_kraken(KRAKEN_ETH_TRADE, product="BTC-USD")), 0)
        self.assertEqual(len(sf.parse_kraken(KRAKEN_TRADE_UPDATE, product="BTC-USD")), 1)
        self.assertEqual(len(sf.parse_kraken(KRAKEN_TRADE_UPDATE, product="BTC/USD")), 1)
        self.assertEqual(len(sf.parse_kraken(KRAKEN_ETH_TRADE, product="ETHUSD")), 1)


class DispatchTests(unittest.TestCase):
    def test_explicit_venue_dispatch(self):
        self.assertEqual(len(sf.parse_message("coinbase", COINBASE_MATCH)), 1)
        self.assertEqual(len(sf.parse_message("kraken", KRAKEN_TRADE_UPDATE)), 1)
        self.assertEqual(len(sf.parse_message("Coinbase", COINBASE_MATCH)), 1)

    def test_venue_sniffing(self):
        self.assertEqual(sf.detect_venue(COINBASE_MATCH), sf.COINBASE)
        self.assertEqual(sf.detect_venue(KRAKEN_TRADE_UPDATE), sf.KRAKEN)
        self.assertEqual(sf.detect_venue(KRAKEN_SUBSCRIBE_ACK), sf.KRAKEN)
        self.assertEqual(sf.detect_venue('{"hello":"world"}'), None)
        self.assertEqual(len(sf.parse_message(COINBASE_MATCH)), 1)
        self.assertEqual(len(sf.parse_message(KRAKEN_TRADE_UPDATE)), 1)
        self.assertEqual(sf.parse_message('{"hello":"world"}'), [])

    def test_unknown_explicit_venue_raises(self):
        with self.assertRaises(sf.UnknownVenue):
            sf.parse_message("binance", COINBASE_MATCH)


class ValidationTests(unittest.TestCase):
    def test_bad_numeric_fields_raise(self):
        base = json.loads(COINBASE_MATCH)
        for field, value in (("price", "not-a-number"), ("price", ""), ("price", None),
                             ("price", True), ("price", -1), ("size", "0"),
                             ("size", float("inf"))):
            with self.subTest(field=field, value=value):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(json.dumps({**base, field: value}))

    def test_bad_time_fields_raise(self):
        base = json.loads(COINBASE_MATCH)
        for value in ("yesterday", "", None, 0, -5, True):
            with self.subTest(value=value):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(json.dumps({**base, "time": value}))

    def test_bad_side_raises(self):
        base = json.loads(COINBASE_MATCH)
        for value in ("hold", "", 2, None):
            with self.subTest(value=value):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(json.dumps({**base, "side": value}))

    def test_ticker_with_a_size_but_no_side_raises(self):
        frame = json.loads(COINBASE_TICKER)
        del frame["side"]
        with self.assertRaises(sf.MalformedMessage):
            sf.parse_coinbase(json.dumps(frame))

    def test_bad_kraken_fields_raise(self):
        base = json.loads(KRAKEN_TRADE_UPDATE)
        for field, value in (("price", "abc"), ("qty", "-1"), ("timestamp", "soon"),
                             ("side", "up")):
            with self.subTest(field=field):
                frame = json.loads(KRAKEN_TRADE_UPDATE)
                frame["data"][0][field] = value
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_kraken(json.dumps(frame))
        self.assertEqual(len(base["data"]), 1)      # the fixture is untouched

    def test_kraken_trade_without_a_data_array_raises(self):
        with self.assertRaises(sf.MalformedMessage):
            sf.parse_kraken('{"channel":"trade","type":"update"}')
        with self.assertRaises(sf.MalformedMessage):
            sf.parse_kraken('{"channel":"trade","type":"update","data":{}}')

    def test_empty_kraken_data_array_is_not_an_error(self):
        self.assertEqual(sf.parse_kraken('{"channel":"trade","type":"update","data":[]}'), [])

    def test_non_json_and_non_object_frames_raise(self):
        for frame in ("not json at all", "<<<binary noise>>>", b"\xff\xfe",
                      "[1,2,3]", "[]", "null"):
            with self.subTest(frame=frame):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(frame)

    def test_missing_trade_id_falls_back_to_a_deterministic_key(self):
        frame = json.loads(COINBASE_MATCH)
        del frame["trade_id"]
        first = sf.parse_coinbase(json.dumps(frame))[0]
        second = sf.parse_coinbase(json.dumps(frame))[0]
        self.assertEqual(first.trade_id, second.trade_id)
        self.assertEqual(first.key, second.key)

    def test_parsing_is_atomic_across_a_batch(self):
        frame = json.loads(KRAKEN_TRADE_SNAPSHOT)
        frame["data"][1]["qty"] = "nonsense"          # second entry is broken
        flow = sf.OrderFlow()
        with self.assertRaises(sf.MalformedMessage):
            flow.add_message("kraken", json.dumps(frame))
        self.assertEqual(flow.counters["accepted"], 0)
        self.assertEqual(flow.cvd, 0.0)

    def test_nonfinite_trade_fields_are_rejected(self):
        """F06: a NaN/Inf price, size or time never becomes a usable trade."""
        for value in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-inf"):
            with self.subTest(field="price", value=value):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(coinbase_match(1, "sell", value, 0.01, 1))
            with self.subTest(field="size", value=value):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(coinbase_match(1, "sell", 64_000.0, value, 1))
            with self.subTest(field="time", value=value):
                frame = json.loads(coinbase_match(1, "sell", 64_000.0, 0.01, 1))
                frame["time"] = value
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase(json.dumps(frame))


# ------------------------------------------------- spot quotes (F06 / F12)

class SpotQuoteTests(unittest.TestCase):
    def test_websocket_ticker_shape_parses_with_best_bid_and_ask(self):
        quote = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD")
        self.assertEqual(quote.venue, sf.COINBASE)
        self.assertEqual(quote.product, COINBASE_PRODUCT)
        self.assertEqual(quote.price, 64001.00)
        self.assertEqual(quote.bid, 64000.90)
        self.assertEqual(quote.ask, 64001.10)
        self.assertEqual(quote.timestamp, OPEN + 2)          # exchange trade time
        self.assertEqual(quote.age_basis, "ticker")

    def test_rest_ticker_shape_parses_with_bid_and_ask(self):
        quote = sf.parse_coinbase_spot(COINBASE_REST_TICKER)
        self.assertEqual(quote.price, 64001.00)
        self.assertEqual(quote.bid, 64000.90)
        self.assertEqual(quote.ask, 64001.10)
        self.assertEqual(quote.timestamp, OPEN + 2)

    def test_nonfinite_bid_ask_price_and_time_are_rejected(self):
        """F06: `float()` used to accept `best_bid='NaN'` into spot state."""
        base = json.loads(COINBASE_TICKER)
        bad = (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-inf")
        for field in ("price", "best_bid", "best_ask", "time"):
            for value in bad:
                with self.subTest(field=field, value=value):
                    frame = json.dumps({**base, field: value})
                    with self.assertRaises(sf.MalformedMessage):
                        sf.parse_coinbase_spot(frame)

    def test_nonfinite_rest_bid_or_ask_is_rejected_too(self):
        base = json.loads(COINBASE_REST_TICKER)
        for field in ("bid", "ask"):
            with self.subTest(field=field):
                frame = json.dumps({**base, field: "NaN"})
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase_spot(frame)

    def test_a_rejected_quote_is_never_normalized_to_zero(self):
        frame = json.dumps({**json.loads(COINBASE_TICKER), "best_bid": float("nan")})
        try:
            quote = sf.parse_coinbase_spot(frame)
        except sf.MalformedMessage:
            quote = None
        self.assertIsNone(quote)                              # not a 0.0 bid

    def test_non_numeric_price_or_time_is_rejected(self):
        for field, value in (("price", "not-a-number"), ("price", ""),
                             ("price", None), ("price", True),
                             ("price", -1), ("time", "yesterday"),
                             ("time", ""), ("time", None), ("time", 0)):
            with self.subTest(field=field, value=value):
                frame = json.dumps({**json.loads(COINBASE_TICKER), field: value})
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase_spot(frame)

    def test_absent_or_blank_book_side_is_unavailable_not_zero(self):
        base = json.loads(COINBASE_TICKER)
        for value in (None, "", "   "):
            with self.subTest(value=value):
                quote = sf.parse_coinbase_spot(json.dumps({**base, "best_bid": value}))
                self.assertIsNone(quote.bid)
                self.assertEqual(quote.ask, 64001.10)
        quote = sf.parse_coinbase_spot(json.dumps({k: v for k, v in base.items()
                                                   if k != "best_ask"}))
        self.assertIsNone(quote.ask)
        self.assertEqual(quote.bid, 64000.90)

    def test_a_non_ticker_frame_is_not_a_spot_quote(self):
        for frame in (COINBASE_MATCH, COINBASE_HEARTBEAT):
            with self.subTest(type=json.loads(frame)["type"]):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase_spot(frame)

    def test_a_different_product_raises_when_filtered(self):
        with self.assertRaises(sf.MalformedMessage):
            sf.parse_coinbase_spot(COINBASE_TICKER, "ETH-USD")

    def test_non_object_frames_raise(self):
        for frame in ("not json at all", "[1,2,3]", "[]", "null", b"\xff\xfe"):
            with self.subTest(frame=frame):
                with self.assertRaises(sf.MalformedMessage):
                    sf.parse_coinbase_spot(frame)

    def test_age_comes_from_the_exchange_trade_time_not_receipt(self):
        """F12: a frame received now may quote a 60 s old trade."""
        quote = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD",
                                       received_at=OPEN + 62)
        self.assertEqual(quote.received_at, OPEN + 62)
        self.assertAlmostEqual(quote.age(OPEN + 62), 60.0)
        self.assertFalse(quote.is_fresh(OPEN + 62))
        self.assertTrue(quote.is_fresh(OPEN + 2))
        self.assertFalse(quote.is_fresh(OPEN + 12.5))         # past the 10 s budget

    def test_freshness_ignores_receipt_time_entirely(self):
        soon = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD", received_at=OPEN + 2)
        late = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD", received_at=OPEN + 900)
        self.assertEqual(soon.is_fresh(OPEN + 900), late.is_fresh(OPEN + 900))
        self.assertFalse(late.is_fresh(OPEN + 900))

    def test_age_budget_is_configurable(self):
        quote = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD")
        self.assertTrue(quote.is_fresh(OPEN + 32, max_age=30.0))
        self.assertFalse(quote.is_fresh(OPEN + 32.001, max_age=30.0))
        self.assertEqual(sf.DEFAULT_SPOT_MAX_AGE, 10.0)

    def test_a_future_stamp_beyond_tolerance_is_not_fresh(self):
        quote = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD")   # stamp OPEN + 2
        self.assertTrue(quote.is_fresh(OPEN + 2))
        quote_future = sf.SpotQuote(venue=sf.COINBASE, product=COINBASE_PRODUCT,
                                    price=64_000.0, timestamp=OPEN + 100)
        self.assertFalse(quote_future.is_fresh(OPEN + 95))          # 5 s in the future
        self.assertTrue(quote_future.is_fresh(OPEN + 99))           # inside tolerance

    def test_spot_quote_as_dict_is_json_serializable(self):
        quote = sf.parse_coinbase_spot(COINBASE_TICKER, "BTC-USD")
        row = quote.as_dict()
        self.assertEqual(row["time"], OPEN + 2)
        self.assertEqual(row["age_basis"], "ticker")
        self.assertEqual(json.loads(json.dumps(row))["bid"], 64000.90)


# ------------------------------------------------- order flow

class WindowTests(unittest.TestCase):
    def test_first_trade_anchors_the_window(self):
        flow = sf.OrderFlow()
        self.assertTrue(flow.add_message("coinbase", COINBASE_MATCH))
        self.assertEqual(flow.window_open, OPEN)
        self.assertEqual(flow.window_close, CLOSE)
        self.assertEqual(flow.cvd, 0.004)              # taker buy
        self.assertEqual(flow.cvd_points[0], {"time": OPEN, "value": 0.0})

    def test_cvd_is_taker_buy_minus_taker_sell_volume(self):
        flow = sf.OrderFlow()
        for venue, frame in (("coinbase", COINBASE_MATCH),            # buy  0.004
                             ("coinbase", COINBASE_MATCH_MAKER_BUY),  # sell 0.020
                             ("coinbase", COINBASE_TICKER),           # sell 0.010
                             ("kraken", KRAKEN_TRADE_SNAPSHOT),       # buy 0.25, sell 0.10
                             ("kraken", KRAKEN_TRADE_UPDATE)):        # buy 1.50
            flow.add_message(venue, frame)
        self.assertAlmostEqual(flow.buy_volume, 1.754)
        self.assertAlmostEqual(flow.sell_volume, 0.130)
        self.assertAlmostEqual(flow.cvd, 1.624)
        self.assertEqual((flow.buy_trades, flow.sell_trades), (3, 3))

    def test_trades_before_the_window_open_are_ignored(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)                    # anchors the window
        later = coinbase_match(55692890, "sell", "64000.00", "5.0", -30)
        self.assertFalse(flow.add_message("coinbase", later))
        self.assertEqual(flow.counters["outside_window"], 1)
        self.assertAlmostEqual(flow.cvd, 0.004)                         # unchanged
        state = flow.snapshot(now=OPEN + 10)
        self.assertEqual(state["last_trade"]["trade_id"], "55692883")
        self.assertEqual([p["time"] for p in state["cvd_points"]], [OPEN, OPEN + 59])

    def test_flow_resets_when_a_trade_opens_the_next_window(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)                    # buy 0.004
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)               # buy 0.25, sell 0.10
        self.assertAlmostEqual(flow.cvd, 0.154)
        roll = coinbase_match(55692900, "buy", "64100.00", "0.5", 900)  # maker buy -> taker sell
        self.assertTrue(flow.add_message("coinbase", roll))
        self.assertEqual(flow.window_open, CLOSE)
        self.assertEqual(flow.window_close, CLOSE + 900)
        self.assertAlmostEqual(flow.sell_volume, 0.5)
        self.assertAlmostEqual(flow.buy_volume, 0.0)
        self.assertAlmostEqual(flow.cvd, -0.5)
        self.assertEqual(flow.cvd_points[0], {"time": CLOSE, "value": 0.0})
        # the previous window's trades are gone from the buffers
        self.assertEqual(len(flow.trades), 1)

    def test_advance_rolls_an_idle_window_and_never_rewinds(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)
        self.assertTrue(flow.advance(OPEN + 901))
        self.assertEqual(flow.window_open, CLOSE)
        self.assertEqual(flow.cvd, 0.0)
        self.assertEqual(list(flow.cvd_points), [{"time": CLOSE, "value": 0.0}])
        self.assertFalse(flow.advance(OPEN + 905))       # same window: no change
        self.assertFalse(flow.advance(OPEN - 10))        # a backwards clock: no change
        self.assertEqual(flow.window_open, CLOSE)

    def test_snapshot_rolls_a_quiet_window_forward(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        self.assertAlmostEqual(flow.cvd, 1.5)
        state = flow.snapshot(now=OPEN + 2000)           # no trades, clock moved on
        self.assertEqual(state["window"]["open_epoch"], OPEN + 1800)
        self.assertEqual(state["cvd"], 0.0)
        self.assertIsNone(state["last_trade"])

    def test_window_metadata(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)
        window = flow.snapshot(now=OPEN + 120)["window"]
        self.assertEqual(window["seconds"], 900)
        self.assertEqual(window["open"], "2026-01-01T00:00:00Z")
        self.assertEqual(window["close"], "2026-01-01T00:15:00Z")
        self.assertEqual(window["seconds_elapsed"], 120)
        self.assertEqual(window["seconds_left"], 780)

    def test_window_length_is_configurable(self):
        flow = sf.OrderFlow(window_seconds=60)
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)                  # OPEN + 6
        self.assertEqual(flow.window_close, OPEN + 60)
        flow.add_message("kraken", kraken_trade(74123848, "buy", 64010.0, 0.5, 65))
        self.assertEqual(flow.window_open, OPEN + 60)

    def test_snapshot_of_a_fresh_flow_is_empty_but_well_formed(self):
        state = sf.OrderFlow().snapshot(now=OPEN + 5)
        self.assertEqual(state["window"]["open_epoch"], OPEN)   # a read anchors the grid
        self.assertEqual(state["cvd"], 0.0)
        self.assertEqual(state["trades"], 0)
        self.assertEqual(state["cvd_points"], [{"time": OPEN, "value": 0.0}])
        self.assertIsNone(state["imbalance"])
        self.assertIsNone(state["one_minute"]["imbalance"])
        # Without the roll-forward the tracker is still completely empty.
        raw = sf.OrderFlow().snapshot(now=OPEN + 5, advance=False)
        self.assertIsNone(raw["window"]["open_epoch"])
        self.assertIsNone(raw["window"]["seconds_elapsed"])

    def test_invalid_configuration_is_rejected(self):
        for kwargs in ({"window_seconds": 0}, {"window_seconds": -1},
                       {"rolling_seconds": 0}, {"rolling_seconds": 901},
                       {"large_trade_usd": None}, {"large_trade_usd": 0},
                       {"max_trades": 0}, {"max_points": 0},
                       {"max_seen_ids": -3}, {"large_trade_size": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    sf.OrderFlow(**kwargs)


class RollingTests(unittest.TestCase):
    def flow(self):
        flow = sf.OrderFlow()
        # buy 1.0 at +10, sell 0.5 at +70
        flow.add_message("kraken", kraken_trade(1, "buy", 64000.0, 1.0, 10))
        flow.add_message("kraken", kraken_trade(2, "sell", 64000.0, 0.5, 70))
        return flow

    def test_one_minute_volumes_and_imbalance(self):
        flow = self.flow()
        one_minute = flow.snapshot(now=OPEN + 75)["one_minute"]
        self.assertEqual(one_minute["seconds"], 60)
        self.assertEqual(one_minute["buy_volume"], 0.0)     # +10 is outside the minute
        self.assertEqual(one_minute["sell_volume"], 0.5)
        self.assertEqual(one_minute["volume"], 0.5)
        self.assertEqual(one_minute["imbalance"], -1.0)

    def test_both_trades_inside_the_minute(self):
        flow = self.flow()
        one_minute = flow.snapshot(now=OPEN + 70)["one_minute"]
        self.assertEqual(one_minute["buy_volume"], 1.0)
        self.assertEqual(one_minute["sell_volume"], 0.5)
        self.assertAlmostEqual(one_minute["imbalance"], 1.0 / 3.0)
        self.assertEqual((one_minute["buy_trades"], one_minute["sell_trades"]), (1, 1))

    def test_rolling_volume_drains_to_nothing(self):
        flow = self.flow()
        self.assertAlmostEqual(flow.snapshot(now=OPEN + 129)["one_minute"]["sell_volume"], 0.5)
        one_minute = flow.snapshot(now=OPEN + 131)["one_minute"]
        self.assertEqual(one_minute["volume"], 0.0)
        self.assertIsNone(one_minute["imbalance"])
        self.assertAlmostEqual(flow.cvd, 0.5)               # the window total is unaffected

    def test_imbalance_stays_within_bounds(self):
        flow = self.flow()
        state = flow.snapshot(now=OPEN + 70)
        self.assertLessEqual(abs(state["one_minute"]["imbalance"]), 1.0)
        self.assertLessEqual(abs(state["imbalance"]), 1.0)
        self.assertAlmostEqual(state["imbalance"], 0.5 / 1.5)
        self.assertEqual(state["volume"], 1.5)
        self.assertEqual(state["cvd"], 0.5)

    def test_rolling_ignores_a_future_dated_trade(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", kraken_trade(1, "buy", 64000.0, 1.0, 30))
        self.assertEqual(flow.snapshot(now=OPEN + 10)["one_minute"]["volume"], 0.0)
        self.assertEqual(flow.snapshot(now=OPEN + 40)["one_minute"]["volume"], 1.0)


class LargeTradeTests(unittest.TestCase):
    def test_default_threshold_marks_only_sizeable_notional(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)                 # 256 USD
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)            # 16 004 and 6 400 USD
        self.assertEqual(list(flow.large_trades), [])
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)              # 96 003 USD
        (marker,) = flow.large_trades
        self.assertEqual(marker["taker_side"], sf.TAKER_BUY)
        self.assertEqual(marker["venue"], sf.KRAKEN)
        self.assertEqual(marker["price"], 64002.0)
        self.assertEqual(marker["size"], 1.5)
        self.assertAlmostEqual(marker["notional"], 96003.0)
        self.assertEqual(marker["time"], OPEN + 6)
        self.assertEqual(marker["trade_id"], "74123847")

    def test_notional_threshold_is_configurable(self):
        flow = sf.OrderFlow(large_trade_usd=10_000.0)
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)
        self.assertEqual([m["trade_id"] for m in flow.large_trades], ["74123845"])
        self.assertEqual(flow.snapshot()["config"]["large_trade_usd"], 10_000.0)

    def test_size_threshold_is_configurable(self):
        flow = sf.OrderFlow(large_trade_usd=None, large_trade_size=0.2)
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)
        self.assertEqual([m["trade_id"] for m in flow.large_trades], ["74123845"])

    def test_threshold_marks_the_exact_boundary(self):
        flow = sf.OrderFlow(large_trade_usd=1_000.0)
        flow.add_message("kraken", kraken_trade(1, "buy", 100.0, 10.0, 5))    # exactly 1000
        self.assertEqual(len(flow.large_trades), 1)

    def test_markers_are_isolated_from_the_window_reset(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        self.assertEqual(len(flow.large_trades), 1)
        flow.advance(OPEN + 1000)
        self.assertEqual(list(flow.large_trades), [])


class DedupTests(unittest.TestCase):
    def test_replayed_kraken_snapshot_is_counted_once(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)            # reconnect replay
        self.assertEqual(flow.counters["accepted"], 2)
        self.assertEqual(flow.counters["duplicate"], 2)
        self.assertAlmostEqual(flow.buy_volume, 0.25)
        self.assertAlmostEqual(flow.sell_volume, 0.10)

    def test_coinbase_match_and_ticker_for_one_fill_count_once(self):
        flow = sf.OrderFlow()
        fill = json.loads(COINBASE_TICKER)
        match = json.dumps({**json.loads(COINBASE_MATCH),
                            "trade_id": fill["trade_id"], "time": fill["time"]})
        flow.add_message("coinbase", match)      # taker buy, 0.004
        flow.add_message("coinbase", COINBASE_TICKER)   # same trade_id, ticker copy
        self.assertEqual(flow.counters["accepted"], 1)
        self.assertEqual(flow.counters["duplicate"], 1)
        self.assertAlmostEqual(flow.buy_volume, 0.004)      # the ticker size is not added
        self.assertEqual(len(flow.cvd_points), 2)           # seed + one trade

    def test_dedup_survives_a_window_roll_within_the_retained_keys(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        flow.advance(OPEN + 1000)
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)     # late replay of an old trade
        self.assertEqual(flow.counters["accepted"], 1)
        self.assertEqual(flow.counters["outside_window"], 1)

    def test_distinct_ids_still_count_separately(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", kraken_trade(1, "buy", 64000.0, 1.0, 5))
        flow.add_message("kraken", kraken_trade(2, "buy", 64000.0, 1.0, 6))
        self.assertEqual(flow.counters["accepted"], 2)
        self.assertAlmostEqual(flow.buy_volume, 2.0)


class ProductFilterTests(unittest.TestCase):
    def test_flow_ignores_other_products(self):
        flow = sf.OrderFlow(product="BTC-USD")
        flow.add_message("coinbase", COINBASE_ETH_MATCH)
        flow.add_message("kraken", KRAKEN_ETH_TRADE)
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        self.assertEqual(flow.counters["filtered_product"], 2)
        self.assertEqual(flow.counters["accepted"], 1)
        self.assertEqual(flow.product, "BTCUSD")


class BoundedMemoryTests(unittest.TestCase):
    def test_every_buffer_respects_its_cap(self):
        flow = sf.OrderFlow(max_trades=5, max_points=4, max_large_trades=2, max_seen_ids=6)
        for i in range(40):
            flow.add_message("kraken", kraken_trade(1000 + i, "buy", 64_000.0, 5.0, i))
        state = flow.snapshot(now=OPEN + 45)
        self.assertEqual(len(flow.trades), 5)
        self.assertEqual(len(flow.cvd_points), 4)
        self.assertEqual(len(flow.large_trades), 2)
        self.assertEqual(state["counters"]["seen_ids"], 6)
        self.assertEqual(state["counters"]["buffered_trades"], 5)
        self.assertEqual(state["counters"]["cvd_points"], 4)
        self.assertEqual(state["counters"]["large_trades"], 2)
        self.assertEqual(state["counters"]["accepted"], 40)
        self.assertAlmostEqual(flow.buy_volume, 200.0)      # totals are exact, buffers are capped

    def test_a_dropped_dedup_key_can_only_admit_a_genuinely_old_replay(self):
        flow = sf.OrderFlow(max_seen_ids=2)
        flow.add_message("kraken", kraken_trade(1, "buy", 64_000.0, 1.0, 1))
        flow.add_message("kraken", kraken_trade(2, "buy", 64_000.0, 1.0, 2))
        flow.add_message("kraken", kraken_trade(3, "buy", 64_000.0, 1.0, 3))   # evicts id 1
        flow.add_message("kraken", kraken_trade(1, "buy", 64_000.0, 1.0, 1))
        self.assertEqual(flow.counters["accepted"], 4)   # documented, bounded trade-off


class ChartOutputTests(unittest.TestCase):
    def test_cvd_points_are_chart_ready(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)                 # +0.004 at +1.25
        flow.add_message("coinbase", COINBASE_MATCH_MAKER_BUY)       # -0.020 at +4
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)            # +0.25, -0.10
        points = flow.snapshot(now=OPEN + 10)["cvd_points"]
        self.assertEqual([p["time"] for p in points], [OPEN, OPEN + 59])
        self.assertEqual([round(p["value"], 6) for p in points],
                         [0.0, 0.134])
        self.assertTrue(all(isinstance(p["time"], int) for p in points))
        self.assertAlmostEqual(points[-1]["value"], flow.cvd)
        self.assertEqual([p["time"] for p in points], sorted(p["time"] for p in points))

    def test_snapshot_is_json_serializable(self):
        flow = sf.OrderFlow()
        flow.add_message("coinbase", COINBASE_MATCH)
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        state = flow.snapshot(now=OPEN + 10)
        self.assertEqual(json.loads(json.dumps(state))["cvd"], state["cvd"])

    def test_snapshot_reports_no_directional_or_advisory_field(self):
        flow = sf.OrderFlow()
        flow.add_message("kraken", KRAKEN_TRADE_SNAPSHOT)
        flow.add_message("kraken", KRAKEN_TRADE_UPDATE)
        forbidden = ("signal", "recommend", "direction", "probab", "advice",
                     "action", "entry", "exit", "position", "order_side", "predict")

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield str(key).lower()
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        for key in keys(flow.snapshot(now=OPEN + 10)):
            for word in forbidden:
                with self.subTest(key=key, word=word):
                    self.assertNotIn(word, key)

    def test_trade_as_dict_is_chart_ready(self):
        trade = sf.parse_kraken(KRAKEN_TRADE_UPDATE)[0]
        row = trade.as_dict()
        self.assertEqual(row["time"], OPEN + 6)
        self.assertEqual(row["taker_side"], sf.TAKER_BUY)
        self.assertEqual(row["notional"], trade.notional)
        self.assertEqual(json.loads(json.dumps(row))["trade_id"], "74123847")


if __name__ == "__main__":
    unittest.main()
