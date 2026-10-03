import copy,unittest,importlib.util
from pathlib import Path
from decimal import Decimal as D
from fee_reconciliation import fee_sequence,reconcile

def group():
    return {'fills_truncated':False,'order':{'order_id':'SYNTHETIC','ticker':'KXBTC15M-SYNTHETIC','status':'executed','fill_count_fp':'1','taker_fill_cost_dollars':'.055','taker_fees_dollars':'.0037'},'fills':[{'fill_id':'F','order_id':'SYNTHETIC','ticker':'KXBTC15M-SYNTHETIC','created_time':'2026-09-01T12:00:00Z','is_taker':True,'side':'yes','outcome_side':'yes','action':'buy','yes_price_dollars':'.055','no_price_dollars':'.945','count_fp':'1','fee_cost':'.0037'}]}

class FeeRegression(unittest.TestCase):
    def test_official_buy_example_and_signed_sell(self):
        buy=fee_sequence([('.055','1','buy')],'.01');sell=fee_sequence([('.055','1','sell')],'.01')
        self.assertEqual(D(buy['fee']),D('.005'));self.assertEqual(D(buy['net_cash_change']),D('-.06'))
        self.assertEqual(D(sell['fee']),D('.005'));self.assertEqual(D(sell['net_cash_change']),D('.05'));self.assertIsNone(sell['all_in'])
    def test_side_asymmetry_sell_revenue(self):
        buy=fee_sequence([('.051','1','buy')],'.01');sell=fee_sequence([('.051','1','sell')],'.01')
        self.assertEqual(D(buy['fee']),D('.009'));self.assertEqual(D(sell['fee']),D('.011'))
    def test_arithmetic_agrees_with_preserved_buy_implementation(self):
        source=Path(__file__).resolve().parents[1]/'copilot_study002_paced/study_policy.py'
        spec=importlib.util.spec_from_file_location('old_fee_policy',source);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        for precision in ('.01','.0001'):
            for price in ('.01','.055','.27','.50','.99'):
                fills=[(price,'.5'),(price,'.5')]
                expected=old.fee_fills(fills,precision);actual=fee_sequence([(p,q,'buy') for p,q in fills],precision)
                for key in expected:self.assertEqual(expected[key],actual[key])
    def test_distinguishing_precision_is_not_membership(self):
        r=reconcile(group());self.assertTrue(r['eligible_for_comparison']);self.assertTrue(r['precision_comparisons']['.0001']['total_matches']);self.assertFalse(r['precision_comparisons']['.01']['total_matches']);self.assertEqual(r['account_classification'],'not inferred')
    def test_incomplete_or_maker_or_duplicate_excluded(self):
        for mutation in ('truncated','quantity','maker','duplicate','nonterminal','fees','notional'):
            g=group()
            if mutation=='truncated':g['fills_truncated']=True
            elif mutation=='quantity':g['order']['fill_count_fp']='2'
            elif mutation=='maker':g['fills'][0]['is_taker']=False
            elif mutation=='duplicate':g['fills']*=2
            elif mutation=='nonterminal':g['order']['status']='resting'
            elif mutation=='fees':g['order']['taker_fees_dollars']='.0038'
            elif mutation=='notional':g['order']['taker_fill_cost_dollars']='.06'
            self.assertFalse(reconcile(g)['eligible_for_comparison'])
    def test_ambiguous_order_and_conflicting_prices_excluded(self):
        g=group();g['fills'].append(dict(g['fills'][0],fill_id='OTHER'))
        self.assertEqual(reconcile(g)['exclusion'],'fill_order_ambiguous')
        g=group();g['fills'][0]['no_price_dollars']='.9'
        self.assertFalse(reconcile(g)['eligible_for_comparison'])
    def test_missing_or_non_utc_timestamp_rejected(self):
        for stamp in (None,'2026-09-01T12:00:00','not a timestamp'):
            g=group();g['fills'][0]['created_time']=stamp
            self.assertFalse(reconcile(g)['eligible_for_comparison'])
    def test_other_series_excluded(self):
        g=group();g['order']['ticker']='INDEX-TEST'
        self.assertEqual(reconcile(g)['exclusion'],'unsupported_series_or_ticker_mismatch')
    def test_invalid_values(self):
        for values in [('nan','1','buy'),('.5','.001','buy'),('.5','-1','buy'),('.5','1','unknown'),('.00001','1','buy')]:
            with self.assertRaises((ValueError,ArithmeticError)):fee_sequence([values],'.01')
    def test_summary_contains_no_trade_identifiers_or_values(self):
        import json
        value=json.dumps(reconcile(group()))
        self.assertNotIn('SYNTHETIC',value);self.assertNotIn('.055',value)

if __name__=='__main__':unittest.main()
