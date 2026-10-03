import copy
import importlib.util
import json
from pathlib import Path
import unittest
from datetime import timedelta
from decimal import Decimal as D

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('binary_risk', ROOT / 'candidate' / 'binary_risk.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
NOW = '2026-09-27T12:00:00Z'


def fixtures():
    limits = json.loads((ROOT / 'candidate' / 'risk_limits.json').read_text())
    proposal = dict(ticker='KXBTC15M-SYNTHETIC', side='YES', price='.50', price_tick='.01',
                    requested_quantity='100', available_quantity='100', fee_bound_per_contract='.02',
                    stress_reserve_per_contract='.01', fee_bound_verified=True,
                    fee_bound_reference='synthetic exact conservative bound', fee_bound_as_of=NOW,
                    fee_bound_scope='all_supported_quantities_including_fragmentation_and_rounding',
                    fee_bound_valid_until='2026-09-27T12:00:15Z', market_verified=True,
                    market_as_of=NOW, market_valid_until='2026-09-27T12:00:15Z')
    ledger = dict(verified=True, verification_reference='synthetic complete ledger', captured_at=NOW,
                  day_pacific='2026-09-27', account_value='500', realized_gross_loss='0', positions_and_pending=[])
    return proposal, ledger, limits


def position(state='OPEN', ticker='OTHER'):
    return dict(id='1', ticker=ticker, state=state, side='YES', quantity='10', entry_price='.5',
                fee_amount='.2', stress_reserve_amount='.1')


class RiskTests(unittest.TestCase):
    def result(self, change=None):
        p, l, limits = fixtures()
        if change:
            change(p, l, limits)
        return r.evaluate_risk(p, l, limits, NOW)

    def test_trade_cap_and_exact_breakdown(self):
        x = self.result()
        self.assertEqual(x['status'], 'CONDITIONALLY_ELIGIBLE')
        self.assertEqual(D(x['max_loss_per_trade']), 25)
        self.assertEqual(D(x['eligible_quantity']), D('47.16'))
        self.assertEqual(D(x['max_loss']), D('24.9948'))
        self.assertEqual(D(x['entry_premium']), D('23.58'))
        self.assertEqual(D(x['fee_bound']), D('.9432'))
        self.assertEqual(D(x['stress_reserve']), D('.4716'))
        self.assertLessEqual(D(x['max_loss']), 25)
        self.assertGreater((D(x['eligible_quantity']) + D('.01')) * D('.53'), 25)
        self.assertFalse(x['advice_enabled'])
        self.assertFalse(x['trading_authorized'])

    def test_no_double_count_daily_commit(self):
        x = self.result()
        self.assertEqual(D(x['daily_committed_max_loss']), D(x['max_loss']))
        self.assertEqual(D(x['daily_remaining_after_proposal']), 30 - D(x['max_loss']))

    def test_daily_losses_reduce_quantity(self):
        x = self.result(lambda p, l, c: l.update(realized_gross_loss='29'))
        self.assertEqual(D(x['eligible_quantity']), D('1.88'))
        self.assertLessEqual(D(x['daily_committed_max_loss']), 30)

    def test_wins_do_not_replenish_gross_loss_budget(self):
        a = self.result(lambda p, l, c: l.update(realized_gross_loss='29'))
        b = self.result(lambda p, l, c: l.update(realized_gross_loss='29', realized_net_pnl='100000'))
        self.assertEqual(a, b)

    def test_exact_daily_limit_abstains(self):
        x = self.result(lambda p, l, c: l.update(realized_gross_loss='30'))
        self.assertEqual(x['status'], 'INELIGIBLE')
        self.assertEqual(x['eligible_quantity'], '0')

    def test_daily_loss_breach_detected(self):
        x = self.result(lambda p, l, c: l.update(realized_gross_loss='31'))
        self.assertIn('EXISTING_LEDGER_LIMIT_BREACH', x['reasons'])

    def test_pending_counts_exposure_and_committed_risk(self):
        x = self.result(lambda p, l, c: l.update(positions_and_pending=[position('PENDING')]))
        self.assertEqual(x['status'], 'INELIGIBLE')
        self.assertEqual(D(x['existing_premium_exposure']), 5)
        self.assertEqual(D(x['existing_payout_notional']), 10)
        self.assertEqual(D(x['existing_committed_max_loss']), D('5.3'))
        self.assertEqual(D(x['exposure_remaining_before_proposal']), D('19.7'))
        self.assertEqual(D(x['daily_remaining_before_proposal']), D('24.7'))
        self.assertIn('SIMULTANEOUS_POSITION_LIMIT', x['reasons'])

    def test_open_position_occupies_slot(self):
        self.assertEqual(self.result(lambda p, l, c: l.update(positions_and_pending=[position()]))['status'], 'INELIGIBLE')

    def test_same_market_open_or_pending_prevents_add(self):
        for state in ('OPEN', 'PENDING'):
            x = self.result(lambda p, l, c: l.update(positions_and_pending=[position(state, p['ticker'])]))
            self.assertIn('ADDING_OR_AVERAGING_DISABLED', x['reasons'])

    def test_opposite_side_does_not_evade_position_limit(self):
        def alter(p, l, c):
            q = position('OPEN', p['ticker'])
            q['side'] = 'NO'
            l['positions_and_pending'] = [q]
        self.assertEqual(self.result(alter)['status'], 'INELIGIBLE')

    def test_payout_notional_reported_not_limited(self):
        # Owner decision: exposure is money at risk, not $1 payout per contract.
        def alter(p, l, c):
            p.update(price='.01', requested_quantity='1000', available_quantity='1000', fee_bound_per_contract='0', stress_reserve_per_contract='0')
        x = self.result(alter)
        self.assertEqual(D(x['eligible_quantity']), 1000)
        self.assertEqual(D(x['max_loss']), 10)
        self.assertEqual(D(x['total_payout_notional']), 1000)
        self.assertEqual(D(x['total_committed_exposure']), 10)

    def test_depth_and_request_are_upper_bounds(self):
        for key in ('available_quantity', 'requested_quantity'):
            x = self.result(lambda p, l, c: p.update({key: '1.23'}))
            self.assertEqual(D(x['eligible_quantity']), D('1.23'))
        self.assertEqual(self.result(lambda p, l, c: p.update(available_quantity='0'))['status'], 'INELIGIBLE')

    def test_fragmentation_bound_not_dropped(self):
        x = self.result(lambda p, l, c: p.update(fee_bound_per_contract='.505', stress_reserve_per_contract='.01'))
        self.assertEqual(D(x['loss_bound_per_contract']), D('1.015'))
        self.assertEqual(D(x['eligible_quantity']), D('24.63'))
        self.assertEqual(D(x['max_loss']), D('24.99945'))

    def test_missing_each_required_limit_fails_closed(self):
        for key in ('risk_fraction_of_account', 'max_loss_per_trade_ceiling', 'daily_loss_limit', 'max_simultaneous_positions', 'exposure_policy', 'daily_boundary_timezone', 'max_evidence_age_seconds'):
            self.assertEqual(self.result(lambda p, l, c: c.pop(key))['status'], 'DATA_UNAVAILABLE', key)

    def test_missing_ledger_never_infers_zero(self):
        p, l, c = fixtures()
        self.assertEqual(r.evaluate_risk(p, None, c, NOW)['status'], 'DATA_UNAVAILABLE')
        for key in ('realized_gross_loss', 'positions_and_pending', 'captured_at', 'day_pacific', 'account_value', 'verification_reference'):
            self.assertEqual(self.result(lambda p, l, c: l.pop(key))['status'], 'DATA_UNAVAILABLE', key)

    def test_unverified_evidence_fails(self):
        for target, key in [('ledger', 'verified'), ('proposal', 'fee_bound_verified'), ('proposal', 'market_verified')]:
            p, l, c = fixtures()
            for value in (False, 1, 'true', None):
                (l if target == 'ledger' else p)[key] = value
                self.assertEqual(r.evaluate_risk(p, l, c, NOW)['status'], 'DATA_UNAVAILABLE')

    def test_stale_future_or_expired_timestamps_fail(self):
        for key in ('fee_bound_as_of', 'market_as_of'):
            for stamp in ('2026-09-27T11:58:59Z', '2026-09-27T12:00:01Z'):
                self.assertEqual(self.result(lambda p, l, c: p.update({key: stamp}))['status'], 'DATA_UNAVAILABLE')
        for key in ('fee_bound_valid_until', 'market_valid_until'):
            self.assertEqual(self.result(lambda p, l, c: p.update({key: NOW}))['status'], 'DATA_UNAVAILABLE')
        self.assertEqual(self.result(lambda p, l, c: l.update(captured_at='2026-09-27T11:58:59Z'))['status'], 'DATA_UNAVAILABLE')

    def at(self, now, day):
        p, l, c = fixtures()
        for key in ('fee_bound_as_of', 'market_as_of'):
            p[key] = now
        valid = (r.utc(now, 'now') + timedelta(seconds=15)).isoformat().replace('+00:00', 'Z')
        for key in ('fee_bound_valid_until', 'market_valid_until'):
            p[key] = valid
        l.update(captured_at=now, day_pacific=day)
        return r.evaluate_risk(p, l, c, now)['status']

    def test_pacific_day_boundary(self):
        # UTC midnight is still the previous Pacific day; Pacific midnight is 07:00Z in PDT.
        self.assertEqual(self.at('2026-09-28T00:00:00Z', '2026-09-27'), 'CONDITIONALLY_ELIGIBLE')
        self.assertEqual(self.at('2026-09-28T00:00:00Z', '2026-09-28'), 'DATA_UNAVAILABLE')
        self.assertEqual(self.at('2026-09-28T06:59:59Z', '2026-09-27'), 'CONDITIONALLY_ELIGIBLE')
        self.assertEqual(self.at('2026-09-28T07:00:00Z', '2026-09-27'), 'DATA_UNAVAILABLE')
        self.assertEqual(self.at('2026-09-28T07:00:00Z', '2026-09-28'), 'CONDITIONALLY_ELIGIBLE')

    def test_pacific_day_across_dst_end(self):
        # November 1, 2026: PDT ends. Pacific midnight on November 2 is 08:00Z.
        self.assertEqual(self.at('2026-11-02T07:30:00Z', '2026-11-01'), 'CONDITIONALLY_ELIGIBLE')
        self.assertEqual(self.at('2026-11-02T08:00:00Z', '2026-11-01'), 'DATA_UNAVAILABLE')
        self.assertEqual(self.at('2026-11-02T08:00:00Z', '2026-11-02'), 'CONDITIONALLY_ELIGIBLE')

    def test_account_fraction_sizes_below_ceiling(self):
        x = self.result(lambda p, l, c: l.update(account_value='200'))
        self.assertEqual(D(x['max_loss_per_trade']), 10)
        self.assertEqual(D(x['eligible_quantity']), D('18.86'))
        self.assertLessEqual(D(x['max_loss']), 10)

    def test_large_account_value_cannot_exceed_ceiling(self):
        for value in ('10000', '999999999'):
            x = self.result(lambda p, l, c: l.update(account_value=value))
            self.assertEqual(D(x['max_loss_per_trade']), 25)
            self.assertLessEqual(D(x['max_loss']), 25)

    def test_bad_account_value_fails_closed(self):
        for value in ('0', '-500', 'NaN', 'Infinity', None, True, 500.0, ''):
            self.assertEqual(self.result(lambda p, l, c: l.update(account_value=value))['status'], 'DATA_UNAVAILABLE', value)

    def test_naive_non_utc_and_malformed_time_rejected(self):
        for stamp in ('2026-09-27T12:00:00', '2026-09-27T05:00:00-07:00', 'bad', None):
            self.assertEqual(self.result(lambda p, l, c: l.update(captured_at=stamp))['status'], 'DATA_UNAVAILABLE')

    def test_adversarial_numbers_fail_closed(self):
        for key in ('price', 'requested_quantity', 'available_quantity', 'fee_bound_per_contract', 'stress_reserve_per_contract'):
            for value in (True, None, '-1', 'NaN', 'Infinity', '1e999', .5, {}, []):
                self.assertEqual(self.result(lambda p, l, c: p.update({key: value}))['status'], 'DATA_UNAVAILABLE', (key, value))

    def test_invalid_price_quantity_and_tick(self):
        for key, value in [('price', '0'), ('price', '1'), ('price', '2'), ('requested_quantity', '0'), ('requested_quantity', '.001'), ('available_quantity', '.005'), ('price_tick', '.03'), ('price_tick', '0')]:
            self.assertEqual(self.result(lambda p, l, c: p.update({key: value}))['status'], 'DATA_UNAVAILABLE')

    def test_limits_cannot_be_silently_relaxed(self):
        for key, value in [('risk_fraction_of_account', '.0501'), ('risk_fraction_of_account', '1'), ('max_loss_per_trade_ceiling', '25.01'), ('daily_loss_limit', '31'), ('max_simultaneous_positions', 2), ('max_simultaneous_positions', True), ('adding_enabled', True), ('max_evidence_age_seconds', '61'), ('exposure_policy', 'payout_notional'), ('daily_boundary_timezone', 'UTC'), ('daily_boundary_timezone', 'US/Pacific')]:
            self.assertEqual(self.result(lambda p, l, c: c.update({key: value}))['status'], 'DATA_UNAVAILABLE')

    def test_duplicate_or_malformed_ledger_fails(self):
        for entries in ([position(), position()], [None], [{'id': 'incomplete'}], {'position': position()}):
            self.assertEqual(self.result(lambda p, l, c: l.update(positions_and_pending=entries))['status'], 'DATA_UNAVAILABLE')

    def test_existing_pending_breach_is_not_offset_by_wins(self):
        def alter(p, l, c):
            q = position('PENDING')
            q['quantity'] = '600'
            l.update(positions_and_pending=[q], realized_net_pnl='1000')
        self.assertIn('EXISTING_LEDGER_LIMIT_BREACH', self.result(alter)['reasons'])

    def test_fee_bound_must_cover_fragmentation_and_fractional_rounding(self):
        for scope in (None, 'one_whole_contract_only', ''):
            self.assertEqual(self.result(lambda p, l, c: p.update(fee_bound_scope=scope))['status'], 'DATA_UNAVAILABLE')

    def test_configuration_cannot_authorize_advice(self):
        for key, value in [('advice_enabled', True), ('trading_authorized', True), ('schema_version', 1), ('schema_version', 2.0)]:
            self.assertEqual(self.result(lambda p, l, c: c.update({key: value}))['status'], 'DATA_UNAVAILABLE')

    def test_exact_twenty_five_dollar_boundary(self):
        x = self.result(lambda p, l, c: p.update(requested_quantity='60', available_quantity='60', fee_bound_per_contract='0', stress_reserve_per_contract='0'))
        self.assertEqual(D(x['max_loss']), 25)
        self.assertEqual(D(x['eligible_quantity']), 50)

    def test_one_full_loss_leaves_only_five_dollars_that_day(self):
        x = self.result(lambda p, l, c: l.update(realized_gross_loss='25'))
        self.assertLessEqual(D(x['max_loss']), 5)
        self.assertEqual(D(x['eligible_quantity']), D('9.43'))

    def test_existing_per_trade_loss_breach_detected(self):
        def alter(p, l, c):
            q = position()
            q['quantity'] = '50'
            l['positions_and_pending'] = [q]
        self.assertIn('EXISTING_LEDGER_LIMIT_BREACH', self.result(alter)['reasons'])

    def test_pending_and_open_same_ticker_loss_aggregates(self):
        def alter(p, l, c):
            a, b = position(), position('PENDING')
            a.update(quantity='25')
            b.update(id='2', quantity='25')
            l['positions_and_pending'] = [a, b]
        x = self.result(alter)
        self.assertEqual(D(x['existing_committed_max_loss']), D('25.6'))
        self.assertIn('EXISTING_LEDGER_LIMIT_BREACH', x['reasons'])

    def test_unsupported_risk_structures_fail_closed(self):
        for key, value in [('leverage', 5), ('action', 'SELL'), ('action', 'BUY'),
                           ('stop_price', '.4'), ('target_price', '.6'),
                           ('collateral', '100'), ('order_type', 'MARKET'),
                           ('unknown_future_structure', {})]:
            with self.subTest(field=key, value=value):
                result = self.result(lambda p, l, c: p.update({key: value}))
                self.assertEqual(result['status'], 'DATA_UNAVAILABLE')
                self.assertEqual(result['eligible_quantity'], '0')
                self.assertFalse(result['advice_enabled'])
                self.assertFalse(result['trading_authorized'])
                self.assertIn('unsupported proposal field', result['reasons'][0])

    def test_no_mutation_of_inputs(self):
        p, l, c = fixtures()
        before = copy.deepcopy((p, l, c))
        r.evaluate_risk(p, l, c, NOW)
        self.assertEqual((p, l, c), before)

    def test_repeatability(self):
        self.assertEqual(self.result(), self.result())

    def test_boundary_grid_quantities_never_exceed_limits(self):
        for cent in range(1, 100):
            for daily in ('0', '18', '29.99'):
                x = self.result(lambda p, l, c: (p.update(price=str(D(cent) / 100)), l.update(realized_gross_loss=daily)))
                if x['status'] == 'CONDITIONALLY_ELIGIBLE':
                    self.assertLessEqual(D(x['max_loss']), 25)
                    self.assertLessEqual(D(x['daily_committed_max_loss']), 30)
                    self.assertLessEqual(D(x['total_committed_exposure']), 25)
                    self.assertEqual(D(x['eligible_quantity']) % D('.01'), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
