"""Offline rule, accounting, journal, and API-shape checks for paper scoring."""
import json
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

import scalp_signal as scalp
import scalp_report
import ta_copilot as ta
from stream_flow import OrderFlow, Trade


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # A test client closes its keep-alive socket after the response.


NO_BID = lambda price, count="20": {"yes_bid": {"price": f"{1 - float(price) - .01:.2f}", "count": "20"},
                                   "no_bid": {"price": price, "count": count}}


def snapshot(side="no", **changes):
    # NO ask .47/bid .46; YES ask .47/bid .46, each with 10+ contracts.
    book = ({"yes_bid": {"price": "0.53", "count": "20"},
             "no_bid": {"price": "0.46", "count": "20"}} if side == "no" else
            {"yes_bid": {"price": "0.46", "count": "20"},
             "no_bid": {"price": "0.53", "count": "20"}})
    result = {"ready": True, "stale": False, "kalshi_stale": False,
              "composite": 99 if side == "no" else 101, "average_60s": 100,
              "frames": {"1m": {"values": {"rsi": 50, "bb_pctb": .5, "bb_mid": 100,
                                           "close": 100, "close_prev": 100},
                                "tally": ({"bull": 1, "bear": 5, "neutral": 2} if side == "no"
                                          else {"bull": 5, "bear": 1, "neutral": 2})}},
              "order_flow": {"one_minute": {"imbalance": -.3 if side == "no" else .3,
                                              "previous_imbalance": None}},
              "window": {"ticker": "KXBTC15M-TEST", "seconds_left": 500,
                         "orderbook_age": 1, "orderbook": book}}
    for key, value in changes.items():
        if key in ("bull", "bear"):
            result["frames"]["1m"]["tally"][key] = value
        elif key in ("imbalance", "previous_imbalance"):
            result["order_flow"]["one_minute"][key] = value
        elif key in ("seconds_left", "orderbook_age", "orderbook", "ticker"):
            result["window"][key] = value
        else:
            result[key] = value
    return result


class ScalpRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = scalp.ScalpEngine(Path(self.tmp.name), now=100)

    def test_no_and_yes_momentum_entry(self):
        for side in ("no", "yes"):
            with self.subTest(side=side):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / side, now=100)
                view = engine.step(snapshot(side), 100)
                self.assertEqual(view["state"], "holding")
                self.assertEqual(view["position"]["side"], side)
                self.assertEqual(view["position"]["entry_ask"], "0.4700")
                self.assertEqual(view["strategy"], "momentum_v1")

    def test_entry_legs_at_their_boundaries(self):
        for side, changes in (("no", {"bull": 2, "bear": 5, "imbalance": -.2}),
                              ("yes", {"bull": 5, "bear": 2, "imbalance": .2})):
            with self.subTest(side=side):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / f"edge-{side}", now=100)
                self.assertEqual(engine.step(snapshot(side, **changes), 100)["state"], "holding")

    def test_each_entry_leg_blocks_when_missing_or_wrong(self):
        for changes in ({"bull": 3}, {"imbalance": -.19}, {"composite": 100},
                        {"average_60s": None}, {"imbalance": None}, {"bull": None},
                        {"composite": 101}):
            with self.subTest(changes=changes):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / str(len(list(Path(self.tmp.name).iterdir()))), now=100)
                view = engine.step(snapshot(**changes), 100)
                self.assertEqual(view["state"], "flat")
                self.assertTrue(view["blockers"])

    def test_every_entry_eligibility_gate(self):
        base = snapshot()
        variants = [
            ("stale", snapshot(stale=True)),
            ("kalshi stale", snapshot(kalshi_stale=True)),
            ("book age", snapshot(orderbook_age=5.01)),
            ("future book", snapshot(orderbook_age=-0.1)),
            ("time", snapshot(seconds_left=179.99)),
            ("low ask", snapshot(orderbook={"yes_bid": {"price": ".91", "count": "20"},
                                                   "no_bid": {"price": ".90", "count": "20"}})),
            ("high ask", snapshot(orderbook={"yes_bid": {"price": ".09", "count": "20"},
                                                    "no_bid": {"price": ".08", "count": "20"}})),
            ("spread", snapshot(orderbook={"yes_bid": {"price": ".53", "count": "20"},
                                                  "no_bid": {"price": ".44", "count": "20"}})),
            ("depth", snapshot(orderbook={"yes_bid": {"price": ".53", "count": "9"},
                                                 "no_bid": {"price": ".46", "count": "20"}})),
            ("missing book", snapshot(orderbook=None)),
        ]
        self.assertEqual(self.engine.step(base, 100)["state"], "holding")
        for name, variant in variants:
            with self.subTest(gate=name):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / name.replace(" ", "_"), now=100)
                self.assertEqual(engine.step(variant, 100)["state"], "flat")
        # Flat and cooldown are independent gates after a completed trade.
        self.assertIn("flat", scalp.eligibility(scalp.read_state(base), None, 101, held=True)[0][-2][0])
        self.assertFalse(dict((name, met) for name, met, _ in
                              scalp.eligibility(scalp.read_state(base), 100, 159)[0])["cooldown"])
        self.assertTrue(dict((name, met) for name, met, _ in
                             scalp.eligibility(scalp.read_state(base), 100, 160)[0])["cooldown"])

    def test_bid_ask_and_depth_from_single_book(self):
        quotes = scalp.book_quotes(snapshot()["window"]["orderbook"])
        self.assertEqual(quotes["no"]["ask"], {"price": Decimal("0.47"), "count": Decimal("20")})
        self.assertEqual(quotes["yes"]["ask"], {"price": Decimal("0.54"), "count": Decimal("20")})
        self.assertEqual(quotes["no"]["bid"]["price"], Decimal("0.46"))
        self.assertIsNone(scalp.book_quotes({"yes_bid": {"price": "NaN", "count": "20"}})["no"]["ask"])

    def test_exit_precedence(self):
        flip = {"bull": 5, "bear": 1, "imbalance": .3}
        for bid, rule in (("0.51", "take_profit"), ("0.43", "stop"), ("0.46", "flip")):
            with self.subTest(rule=rule):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / f"order-{rule}", now=100)
                engine.step(snapshot(), 100)
                view = engine.step(snapshot(orderbook=NO_BID(bid), **flip), 101)
                self.assertEqual(view["last"]["rule"], rule)
                self.assertEqual(view["last"]["bid"], f"{float(bid):.4f}")
                self.assertEqual(view["stats"]["trades"], 1)

    def test_take_profit_and_stop_boundaries(self):
        self.engine.step(snapshot(), 100)
        self.assertEqual(self.engine.step(snapshot(orderbook=NO_BID("0.50")), 101)["state"], "holding")
        self.assertEqual(self.engine.step(snapshot(orderbook=NO_BID("0.44")), 102)["state"], "holding")
        self.assertEqual(self.engine.step(snapshot(orderbook=NO_BID("0.51")), 103)["last"]["rule"], "take_profit")

    def test_flip_by_tally_or_flow_and_not_when_stale(self):
        for name, changes in (("tally", {"bull": 4, "bear": 2}), ("flow", {"imbalance": .2})):
            with self.subTest(flip=name):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / f"flip-{name}", now=100)
                engine.step(snapshot(), 100)
                self.assertEqual(engine.step(snapshot(**changes), 101)["last"]["rule"], "flip")
        engine = scalp.ScalpEngine(Path(self.tmp.name) / "flip-stale", now=100)
        engine.step(snapshot(), 100)
        self.assertEqual(engine.step(snapshot(stale=True, bull=5, bear=1), 101)["state"], "holding")
        self.assertEqual(engine.step(snapshot(bull=3, bear=2, imbalance=.19), 102)["state"], "holding")

    def test_stop_max_hold_and_time_stop(self):
        for name, now, change in (
            ("stop", 101, {"orderbook": NO_BID("0.42")}),
            ("max_hold", 400, {}),
            ("time_stop", 101, {"seconds_left": 90}),
        ):
            with self.subTest(rule=name):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / name, now=100)
                engine.step(snapshot(), 100)
                self.assertEqual(engine.step(snapshot(**change), now)["last"]["rule"], name)

    def test_fired_exit_retries_until_an_executable_bid(self):
        self.engine.step(snapshot(), 100)
        waiting = self.engine.step(snapshot(bull=5, bear=1, orderbook_age=6), 101)
        self.assertEqual(waiting["state"], "holding")
        self.assertEqual(waiting["position"]["exit_pending"], "flip")
        self.assertIn("EXIT PENDING (flip)", waiting["label"])
        # The flip has passed, but the fired exit still completes at the first usable bid.
        done = self.engine.step(snapshot(orderbook=NO_BID("0.45")), 104)
        self.assertEqual(done["last"]["outcome"], "filled")
        self.assertEqual(done["last"]["rule"], "flip")
        self.assertEqual(done["last"]["exit_retry_seconds"], 3.0)

    def test_pending_exit_settles_at_the_time_stop_without_a_bid(self):
        self.engine.step(snapshot(), 100)
        self.engine.step(snapshot(bull=5, bear=1, orderbook_age=6), 101)
        view = self.engine.step(snapshot(seconds_left=90, orderbook_age=6), 102)
        self.assertEqual(view["state"], "settling")
        self.assertEqual(view["last"]["outcome"], "no_bid")
        self.assertEqual(view["last"]["rule"], "flip")

    def test_cooldown_prevents_reentry_until_sixty_seconds_after_exit(self):
        self.engine.step(snapshot(), 100)
        stop = snapshot(orderbook=NO_BID("0.42"))
        self.engine.step(stop, 101)
        self.assertEqual(self.engine.step(snapshot(), 160)["state"], "flat")
        self.assertEqual(self.engine.step(snapshot(), 161)["state"], "holding")

    def test_no_bid_settlement_and_restart_abort(self):
        self.engine.step(snapshot(), 100)
        self.assertIsNone(self.engine.settlement_due(599))
        self.assertEqual(self.engine.settlement_due(600), "KXBTC15M-TEST")
        no_bid = snapshot(seconds_left=90, orderbook={"yes_bid": {"price": ".53", "count": "20"}})
        waiting = self.engine.step(no_bid, 101)
        self.assertEqual(waiting["state"], "settling")
        self.assertEqual(waiting["last"]["outcome"], "no_bid")
        self.assertEqual(waiting["stats"]["trades"], 0)
        settled = self.engine.settle("KXBTC15M-TEST", "no", 700)
        self.assertEqual(settled["stats"]["trades"], 1)
        self.assertEqual(settled["last"]["exit_fee"], "0.0000")
        self.assertEqual(settled["last"]["result"], "no")
        self.assertEqual(scalp.stats_from_log(self.engine.path)["trades"], 1)
        other = scalp.ScalpEngine(Path(self.tmp.name) / "restart", now=100)
        other.step(snapshot(), 100)
        restarted = scalp.ScalpEngine(Path(self.tmp.name) / "restart", now=200)
        self.assertEqual(restarted.snapshot_view(200)["stats"]["aborted"], 1)
        self.assertEqual(restarted.snapshot_view(200)["stats"]["trades"], 0)
        self.assertEqual(scalp.load_rows(restarted.path)[-1]["outcome"], "aborted")

    def test_shallow_or_stale_exit_bid_waits_for_settlement(self):
        for name, exit_state in (
            ("shallow", snapshot(seconds_left=90, orderbook={
                "yes_bid": {"price": ".53", "count": "20"},
                "no_bid": {"price": ".46", "count": "9"}})),
            ("old", snapshot(seconds_left=90, orderbook_age=5.01)),
        ):
            with self.subTest(name=name):
                engine = scalp.ScalpEngine(Path(self.tmp.name) / name, now=100)
                engine.step(snapshot(), 100)
                state = engine.step(exit_state, 101)
                self.assertEqual(state["state"], "settling")
                self.assertEqual(state["last"]["outcome"], "no_bid")
                self.assertIsNone(state["last"]["bid"])

    def test_missing_window_time_stop_and_result_race_are_recorded(self):
        engine = scalp.ScalpEngine(Path(self.tmp.name) / "window-gap", now=100)
        engine.step(snapshot(), 100)
        self.assertEqual(engine.step(snapshot(window=None), 509)["state"], "holding")
        self.assertEqual(engine.step(snapshot(window=None), 510)["last"]["outcome"], "no_bid")

        racing = scalp.ScalpEngine(Path(self.tmp.name) / "result-race", now=100)
        racing.step(snapshot(), 100)
        view = racing.settle("KXBTC15M-TEST", "no", 600)
        self.assertEqual(view["last"]["outcome"], "settled")
        self.assertEqual([row["outcome"] for row in scalp.load_rows(racing.path)
                          if row["type"] == "exit"], ["no_bid", "settled"])

    def test_fees_and_statistics(self):
        for price, fee4, fee2 in (("0.47", "0.1744", "0.18"),
                                  ("0.55", "0.1733", "0.18"),
                                  ("0.50", "0.1750", "0.18")):
            with self.subTest(price=price):
                self.assertEqual(scalp.taker_fee(10, price)[0], Decimal(fee4))
                self.assertEqual(scalp.taker_fee(10, price, Decimal("0.01"))[0], Decimal(fee2))
        trades = [{"net_dollars": .2, "net_dollars_01": .1, "per_contract": .02,
                   "contracts": 10, "won": True},
                  {"net_dollars": -.1, "net_dollars_01": -.2, "per_contract": -.01,
                   "contracts": 10, "won": False}]
        stats = scalp.summarize(trades, 1)
        self.assertEqual(stats["trades"], 2)
        self.assertEqual(stats["aborted"], 1)
        self.assertAlmostEqual(stats["win_rate"], .5)
        self.assertLess(stats["win_rate_ci"][0], .5)
        self.assertGreater(stats["win_rate_ci"][1], .5)
        self.assertAlmostEqual(stats["win_rate_ci"][0], 0.094531, places=5)
        self.assertAlmostEqual(stats["win_rate_ci"][1], 0.905469, places=5)
        self.assertAlmostEqual(stats["cumulative_pnl_dollars"], .1)
        self.assertAlmostEqual(stats["max_drawdown_dollars"], .1)
        self.assertIsNotNone(stats["mean_per_contract_ci_cents"])
        self.assertAlmostEqual(scalp.mean_ci95([1, -1])[0], -12.706, places=3)

    def test_private_log_and_json_safe_view(self):
        self.assertEqual(self.engine.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.engine.state_dir.stat().st_mode & 0o777, 0o700)
        view = self.engine.step(snapshot(), 100)
        json.dumps(view, allow_nan=False)
        self.assertEqual(scalp.load_rows(self.engine.path)[0]["type"], "enter")


class StateIntegration(unittest.TestCase):
    def test_cli_report_summarizes_completed_paper_trade(self):
        rows = [{"type": "enter", "ticker": "T", "side": "no", "strategy": "momentum_v1"},
                {"type": "exit", "ticker": "T", "side": "no", "rule": "take_profit",
                 "outcome": "filled", "at_epoch": 200, "contracts": 10, "strategy": "momentum_v1",
                 "net_dollars": "0.1000", "net_dollars_01": "0.0900"},
                {"type": "exit", "ticker": "L", "side": "yes", "rule": "target",
                 "outcome": "filled", "at_epoch": 100, "contracts": 10,
                 "net_dollars": "-0.5000", "net_dollars_01": "-0.5000"}]
        text = scalp_report.report(rows)
        self.assertIn("Trades: 1", text)
        self.assertIn("take_profit (no): 1 trades", text)
        self.assertIn("mean_reversion_v1 1", text)
        self.assertIn("too few trades to judge", text)

    def test_journal_rows_are_tagged_and_other_strategies_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = scalp.ScalpEngine(folder, now=100)
            engine.step(snapshot(), 100)
            engine.step(snapshot(orderbook=NO_BID("0.51")), 101)
            rows = scalp.load_rows(engine.path)
            self.assertEqual({row["strategy"] for row in rows}, {"momentum_v1"})
            legacy = {"type": "exit", "ticker": "L", "side": "yes", "outcome": "filled",
                      "at_epoch": 50, "contracts": 10, "net_dollars": "-1.0000"}
            self.assertEqual(scalp.summarize(*scalp.replay([legacy] + rows)[::3])["trades"], 1)
            self.assertEqual(len(scalp.replay([legacy] + rows, strategy=None)[0]), 2)

    def test_previous_flow_minute_is_observed_separately(self):
        flow = OrderFlow()
        flow.add_trade(Trade("coinbase", "BTC-USD", 100, 1, 1000, "buy", "one"))
        flow.add_trade(Trade("coinbase", "BTC-USD", 100, 1, 1050, "sell", "two"))
        one = flow.snapshot(1100)["one_minute"]
        self.assertEqual(one["previous_imbalance"], 1)
        self.assertEqual(one["imbalance"], -1)

    def test_api_state_and_sse_tail_carry_scalp(self):
        feed = ta.Feed()
        state = feed.state("1m")
        self.assertIn("scalp", state)
        with tempfile.TemporaryDirectory() as folder:
            feed.scalp_engine = scalp.ScalpEngine(folder, now=100)
            with patch.object(ta, "FEED", feed):
                ta.STATE_CACHE.clear()
                state = ta.cached_state("1m")
                self.assertIn("scalp", state)
                self.assertTrue(state["scalp"]["paper"])
                self.assertEqual(ta.tail_state(state, "1m")["scalp"], state["scalp"])
                json.dumps(state, allow_nan=False)
                server = QuietServer(("127.0.0.1", 0), ta.Handler)
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/state?tf=1m", timeout=3) as response:
                        self.assertTrue(json.load(response)["scalp"]["paper"])
                finally:
                    server.shutdown()
                    server.server_close()
                ta.STATE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
