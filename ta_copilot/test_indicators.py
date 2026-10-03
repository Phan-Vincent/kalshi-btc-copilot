import math
import unittest

import ta_copilot as ta


def bars_from(closes, start=0, step=60):
    return [{"time": start + i * step, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0}
            for i, c in enumerate(closes)]


class Indicators(unittest.TestCase):
    def test_sma_ema_constant(self):
        xs = [5.0] * 40
        self.assertEqual(ta.sma(xs, 10)[-1], 5.0)
        self.assertAlmostEqual(ta.ema(xs, 10)[-1], 5.0)
        self.assertIsNone(ta.ema(xs, 10)[8])
        self.assertEqual(ta.ema(xs, 10)[9], 5.0)

    def test_sma_values(self):
        self.assertEqual(ta.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_ema_known(self):
        # seed SMA(1,2,3)=2, then 4*0.5+2*0.5=3, then 5*0.5+3*0.5=4
        self.assertEqual(ta.ema([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_rsi_extremes(self):
        self.assertEqual(ta.rsi([float(i) for i in range(30)])[-1], 100.0)
        self.assertAlmostEqual(ta.rsi([float(30 - i) for i in range(30)])[-1], 0.0)
        alt = [100 + (1 if i % 2 else -1) for i in range(60)]
        self.assertAlmostEqual(ta.rsi(alt)[-1], 50.0, delta=5)

    def test_macd_uptrend_positive(self):
        closes = [100 + i * 0.5 for i in range(80)]
        line, sig, hist = ta.macd(closes)
        self.assertGreater(line[-1], 0)
        self.assertIsNotNone(hist[-1])
        self.assertIsNone(sig[30])

    def test_bollinger_constant_collapses(self):
        mid, up, lo = ta.bollinger([10.0] * 30)
        self.assertEqual((mid[-1], up[-1], lo[-1]), (10.0, 10.0, 10.0))

    def test_atr_fixed_range(self):
        closes = [100.0] * 40
        self.assertAlmostEqual(ta.atr([101.0] * 40, [99.0] * 40, closes)[-1], 2.0)

    def test_stochastic(self):
        closes = [float(i) for i in range(30)]
        k, d = ta.stochastic([c + 1 for c in closes], [c - 1 for c in closes], closes)
        self.assertIsNone(k[12])
        self.assertIsNone(d[14])
        self.assertAlmostEqual(k[-1], (29 - 15) / (30 - 15) * 100)
        self.assertAlmostEqual(d[-1], sum(k[-3:]) / 3)

    def test_anchored_vwap(self):
        bars = bars_from([10.0, 20.0, 30.0])
        vw = ta.anchored_vwap(bars, anchor=60)
        self.assertIsNone(vw[0])
        self.assertAlmostEqual(vw[1], 20.0)
        self.assertAlmostEqual(vw[2], 25.0)

    def test_pivots_exclude_forming_bar(self):
        closes = [1, 2, 5, 2, 1, 0, 1, 2, 3, 9]
        highs, lows = ta.pivots(bars_from([float(c) for c in closes]))
        self.assertIn(6.0, highs)
        self.assertIn(-1.0, lows)
        self.assertNotIn(10.0, highs)

    def test_analyze_uptrend_leans_bullish(self):
        closes = [100 + i + math.sin(i / 3) * 4 for i in range(250)]
        a = ta.analyze(bars_from(closes), closes[-1], anchor=0)
        self.assertEqual(a["tally"]["lean"], "bullish", a["readings"])
        self.assertTrue(all(r["state"] in {"bull", "bear", "neutral"} for r in a["readings"]))
        self.assertEqual(len(a["chart"]["candles"]), 250)
        down = [400 - c for c in closes]
        self.assertEqual(ta.analyze(bars_from(down), down[-1], anchor=0)["tally"]["lean"], "bearish")

    def test_extremes_and_ties_are_neutral(self):
        closes = [100.0 + i for i in range(250)]
        states = {r["name"]: r["state"] for r in ta.analyze(bars_from(closes), closes[-1], 0)["readings"]}
        self.assertEqual(states["RSI 14"], "neutral")
        self.assertEqual(states["Stoch 14,3"], "neutral")

    def test_aggregate_10m_from_5m(self):
        # starts mid-bucket at 300s: that partial 600s group is dropped
        bars = bars_from([1.0, 2.0, 3.0, 4.0, 5.0], start=300, step=300)
        agg = ta.aggregate(bars, 600)
        self.assertEqual([b["time"] for b in agg], [600, 1200])
        self.assertEqual((agg[0]["open"], agg[0]["close"], agg[0]["high"], agg[0]["low"], agg[0]["volume"]),
                         (2.0, 3.0, 4.0, 1.0, 2.0))
        self.assertEqual((agg[1]["open"], agg[1]["close"]), (4.0, 5.0))  # forming bucket kept

    def test_sigma(self):
        self.assertIsNone(ta.realized_sigma([100.0] * 5))
        self.assertEqual(ta.realized_sigma([100.0] * 70), 0.0)


if __name__ == "__main__":
    unittest.main()
