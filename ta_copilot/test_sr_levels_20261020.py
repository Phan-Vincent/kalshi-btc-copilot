"""Descriptive zones, storage and unchanged indicator votes."""
import json
import math
import unittest
from unittest.mock import patch

import sr_levels as sr
import ta_copilot as ta
from test_indicators import bars_from
from test_tally_20261003 import window_state
import test_qa_20261018b as history_tests


def swings(prices, start=0, step=60):
    bars = [{"time": start + i * step, "open": 100 + i / 100, "close": 100 + i / 100,
             "high": 101 + i / 100, "low": 50 + i / 100, "volume": 1.0} for i in range(40)]
    for i, price in enumerate(prices):
        bars[5 + i * 7]["high"] = price
    return bars


def old_pivots(bars, k=2):
    highs, lows = [], []
    closed = bars[:-1]
    for i in range(k, len(closed) - k):
        window = closed[i - k:i + k + 1]
        if closed[i]["high"] == max(b["high"] for b in window):
            highs.append(closed[i]["high"])
        if closed[i]["low"] == min(b["low"] for b in window):
            lows.append(closed[i]["low"])
    return highs, lows


class ZonesTests(unittest.TestCase):
    def levels(self, prices=(200,), price=180, **kwargs):
        return sr.levels('1m', {'1m': swings(prices)}, {}, price, kwargs.pop('atr', 20),
                         now=864000, **kwargs)

    def test_single_linkage_and_weights(self):
        result = sr.levels('1m', {'1m': swings([200, 204, 208, 220]), '5m': swings([202])},
                           {}, 180, 20, now=864000)
        a, b = result['resistance']
        self.assertEqual((a['low'], a['high'], a['touches']), (200, 208, 4))
        self.assertEqual(a['mid'], 203.2)
        self.assertEqual(a['timeframes'], ['1m', '5m'])
        self.assertEqual(a['strength_parts'], {'touches': 5, 'session': 0, 'confluence': 0})
        self.assertEqual(a['strength'], 5)
        self.assertEqual(b['mid'], 220)
        self.assertEqual(result['tolerance'], 5)
        self.assertEqual(self.levels(atr=None)['tolerance'], 5)
        self.assertIsNone(self.levels(atr=0)['resistance'][0]['distance_atr'])

    def test_candidate_chain_respects_max_width(self):
        for atr, max_width, gap in ((None, 10, 5), (0, 10, 5), (10, 10, 5), (40, 20, 10)):
            with self.subTest(atr=atr):
                prices = [200 + i * gap for i in range(9)]
                pivots = [(i, price, 'high') for i, price in enumerate(prices)]
                with patch.object(sr, 'pivot_points', return_value=pivots):
                    result = self.levels([], atr=atr, target=400)
                self.assertEqual(result['max_width'], max_width)
                self.assertLessEqual(gap, result['tolerance'])
                zones = result['target']['between']
                self.assertEqual([(z['low'], z['high']) for z in zones],
                                 [(prices[i], prices[i + 2]) for i in (0, 3, 6)])
                self.assertEqual(sum(z['touches'] for z in zones), len(prices))
                self.assertTrue(all(z['high'] - z['low'] <= max_width for z in zones))

    def test_confluence_never_standalone(self):
        empty = sr.levels('1m', {}, {}, 500, None, vwap=500, now=0)
        self.assertEqual((empty['support'], empty['resistance'], empty['at_price']), ([], [], None))
        plain = self.levels([499])['resistance'][0]
        tagged = self.levels([499], vwap=502)['resistance'][0]
        self.assertEqual(tagged['tags'], ['round $500', 'VWAP · Coinbase volume'])
        self.assertEqual(tagged['strength'], plain['strength'] + 1)
        self.assertEqual(tagged['strength_parts']['confluence'], 2)
        self.assertEqual(self.levels([1000])['resistance'][0]['tags'], ['round $1,000'])

    def test_sources_and_exclusions(self):
        bars = {tf: swings([200 + i * 20]) for i, tf in enumerate(sr.FRAMES)}
        result = sr.levels('1m', bars, {'5m': {'stale': True}, '15m': {'gap': True}}, 180, 20, now=864000)
        self.assertEqual(result['excluded_timeframes'], ['5m', '15m'])
        self.assertEqual([z['mid'] for z in result['resistance']], [200, 280])
        self.assertEqual(sr.levels('10m', bars, {}, 180, 20, now=864000)['resistance'][0]['mid'], 240)
        bars['5m'] = bars['5m'][:29]
        self.assertIn('5m', sr.levels('1m', bars, {}, 180, 20, now=864000)['excluded_timeframes'])
        for tf in sr.FRAMES:
            self.assertIsNone(sr.levels(tf, {}, {}, 180, 20)['at_price'])

    def test_view_source_matrix(self):
        bars = {tf: swings([200 + i * 20]) for i, tf in enumerate(sr.FRAMES)}
        expected = {'1m': ['1m', '5m', '15m', '1h'], '5m': ['5m', '15m', '1h'],
                    '10m': ['10m', '15m', '1h'], '15m': ['15m', '1h'], '1h': ['1h']}
        for view, frames in expected.items():
            result = sr.levels(view, bars, {}, 180, 20, now=864000, target=400)
            zones = result['target']['between']
            self.assertEqual([z['timeframes'][0] for z in zones], frames)
            self.assertEqual([z['strength'] for z in zones], [sr.WEIGHTS[tf] for tf in frames])

    def test_window_open_session_weight_and_timestamp(self):
        result = self.levels([200], window_open=204)
        zone = result['resistance'][0]
        self.assertEqual((zone['low'], zone['high'], zone['mid']), (200, 204, 202))
        self.assertEqual(zone['sources'], ['swing', 'window_open'])
        self.assertEqual(zone['strength_parts'], {'touches': 1, 'session': 1, 'confluence': 0})
        self.assertEqual(zone['last_touch'], 300)
        only = sr.levels('1m', {}, {}, 180, 20, window_open=200)['resistance'][0]
        self.assertIsNone(only['last_touch'])
        self.assertEqual(only['touches'], 0)

    def test_roles_and_distances(self):
        below = self.levels([200, 204], price=210)['support'][0]
        above = self.levels([200, 204], price=190)['resistance'][0]
        inside = self.levels([200, 204], price=202)['at_price']
        self.assertEqual((below['role'], above['role'], inside['role']), ('support', 'resistance', 'at price'))
        self.assertEqual((below['distance_usd'], below['distance_atr']), (-8, -.4))

    def test_targets_use_all_zones_and_composite(self):
        result = self.levels([200, 220, 240, 260, 280], target=290, target_price=190)
        self.assertEqual(len(result['resistance']), 3)
        self.assertEqual(result['target']['count'], 5)
        self.assertEqual([z['mid'] for z in result['target']['between']], [200, 220, 240, 260, 280])
        down = self.levels([200, 220, 240], target=190, target_price=250)['target']
        self.assertEqual([z['mid'] for z in down['between']], [240, 220, 200])
        self.assertIsNotNone(self.levels([200, 204], target=202)['target']['in_zone'])
        self.assertEqual(self.levels([200], target=200)['target']['count'], 0)
        self.assertEqual(self.levels([200], target=190)['target']['count'], 0)
        self.assertIsNone(self.levels()['target'])

    def test_utc_sessions_and_window(self):
        now = 3 * 86400 + 2 * 3600 + 30
        minute = swings([], start=now // 3600 * 3600 - 3600, step=60)
        hourly = swings([], start=86400, step=3600) + swings([], start=3 * 86400, step=3600)[:3]
        result = sr.levels('1h', {'1m': minute, '1h': hourly}, {}, 100, 20,
                           window_open=300, target=400, now=now)
        zones = result['target']['between'] + result['support']
        sources = {src for z in zones for src in z['sources']}
        self.assertTrue({'prior_hour_high', 'prior_hour_low', 'day_high', 'day_low',
                         'prior_day_high', 'prior_day_low', 'window_open'} <= sources)
        window = next(z for z in result['resistance'] if 'window_open' in z['sources'])
        self.assertIsNone(window['last_touch'])
        self.assertEqual(window['strength_parts']['session'], 1)
        blocked = sr.levels('1h', {'1m': minute, '1h': hourly},
                            {'1m': {'gap': True}, '1h': {'stale': True}}, 100, 20, now=now)
        self.assertEqual(blocked['excluded_timeframes'], ['1m', '1h'])
        self.assertEqual(blocked['resistance'], [])

    def test_pivots_match_old_and_closed(self):
        for values in ([100] * 50, [100 + math.sin(i) * 20 for i in range(100)]):
            bars = bars_from(values)
            for k in (1, 2, 3):
                self.assertEqual(ta.pivots(bars, k), old_pivots(bars, k))
            self.assertNotIn(bars[-1]['time'], [t for t, _, _ in sr.pivot_points(bars)])

    def test_analysis_and_family_invariant(self):
        bars = bars_from([200 + math.sin(i / 3) * 10 + i / 10 for i in range(250)])
        with patch.object(ta, 'pivots', old_pivots):
            baseline = ta.frame_family(ta.analyze(bars, 210, 0), '1m', .3, 60)
        actual = ta.frame_family(ta.analyze(bars, 210, 0), '1m', .3, 60)
        actual['levels'] = sr.levels('1m', {'1m': bars}, {}, 210, actual['values']['atr'], now=864000)
        for key in ('readings', 'tally', 'family', 'values'):
            self.assertEqual(actual[key], baseline[key])
        self.assertEqual(ta.frame_family(actual, '1m', .3, 60)['family'], baseline['family'])

    def test_minute_compact_and_missing(self):
        state = window_state(90000, 90060.5)
        frame = next(iter(state['frames'].values()))
        frame['levels'] = self.levels([200, 220], price=210, target=230)
        row = ta.minute_record(state, 90060.5)
        compact = next(iter(row['timeframes'].values()))['levels']
        self.assertEqual(compact, {'support': 200, 'support_touches': 1, 'support_dist_atr': -.5,
                                  'resistance': 220, 'resistance_touches': 1, 'resistance_dist_atr': .5,
                                  'at_price': False, 'zones_to_target': 1, 'target_in_zone': False})
        self.assertTrue(all(v is None or isinstance(v, (int, float)) and math.isfinite(v) for v in compact.values()))
        for target in (None, {'count': 0, 'in_zone': {}}):
            frame['levels']['target'] = target
            compact = next(iter(ta.minute_record(state, 90060.5)['timeframes'].values()))['levels']
            self.assertEqual(compact['target_in_zone'], None if target is None else False)
        frame['levels']['support'][0]['mid'] = float('inf')
        json.dumps(ta.minute_record(state, 90060.5), allow_nan=False)
        del frame['levels']
        self.assertIsNone(next(iter(ta.minute_record(state, 90060.5)['timeframes'].values()))['levels'])

    def test_feed_inherits_quality_before_levels(self):
        state = history_tests.DerivedHistoryTests().state({'1m': 98940, '5m': 98100, '10m': 97800})
        self.assertIn('10m', state['frames']['10m']['levels']['excluded_timeframes'])
        self.assertIn('5m', state['frames']['1m']['levels']['excluded_timeframes'])
        self.assertNotIn('10m', state['frames']['1m']['levels']['excluded_timeframes'])


if __name__ == '__main__':
    unittest.main()
