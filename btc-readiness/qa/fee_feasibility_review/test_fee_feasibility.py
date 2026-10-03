"""Offline documented-rule examples, integer oracle and adversarial fill paths.

This computes conditional models only. No fee policy, account or collector changes.
"""
import hashlib,io,json,random,socket,sys,unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
CANDIDATE=AUDIT/'outputs/copilot_remediation_candidate'
sys.path.insert(0,str(CANDIDATE))
from fee_reconciliation import fee_sequence
from study_policy import execution_cost

def integer_oracle(fills,precision):
    """Independent units: dollars in microdollars, price in 1e-4, qty in .01.
    Integer floor for signed cash; order-local carry and current-fill rebate cap.
    General taker coefficient1x conditional on public series attestation.
    """
    quantum={'.0001':100,'.01':10000}[precision]
    carry=fee=notional=cash=rebates=0;details=[]
    for p,q,action in fills:
        pi=int(D(p)*10000);qi=int(D(q)*100)
        if D(p)*10000!=pi or D(q)*100!=qi or not 0<=pi<=10000 or qi<=0:raise ValueError('Invalid oracle grid')
        revenue=pi*qi*(1 if action=='sell' else -1)
        numerator=7*qi*pi*(10000-pi)
        trade=(numerator+999999)//1000000
        aligned=(revenue-trade)//quantum*quantum
        rounding=revenue-trade-aligned;carry+=rounding
        rebate=min(carry//quantum,(trade+rounding)//quantum)*quantum
        carry-=rebate;net=trade+rounding-rebate
        fee+=net;notional+=pi*qi;cash+=revenue-net;rebates+=rebate
        details.append((trade,rounding,rebate,net))
    return {'fee_micro':fee,'notional_micro':notional,'cash_micro':cash,'carry_micro':carry,'rebates_micro':rebates,'details':details}

def compare(fills,precision):
    actual=fee_sequence(fills,precision);expected=integer_oracle(fills,precision)
    assert D(actual['fee'])*1000000==expected['fee_micro']
    assert D(actual['notional'])*1000000==expected['notional_micro']
    assert D(actual['net_cash_change'])*1000000==expected['cash_micro']
    assert D(actual['rounding_carry'])*1000000==expected['carry_micro']
    details=[tuple(int(D(f[k])*1000000) for k in ['trade','rounding','rebate','net']) for f in actual['fills']]
    assert details==expected['details']
    return actual

def tiny(price):return [(str(price),'.01','buy')]*100

class FeeFeasibility(unittest.TestCase):
    def test_documented_buy_example(self):
        s=compare([('.055','1','buy')],'.01')
        self.assertEqual(s['fills'][0],{'trade':'0.003639','rounding':'0.001361','rebate':'0.00','net':'0.005000'})
        self.assertEqual(D(s['net_cash_change']),D('-.06'))
    def test_signed_sell_alignment(self):
        s=compare([('.055','1','sell')],'.01')
        self.assertEqual(D(s['net_cash_change']),D('.05'));self.assertIsNone(s['all_in'])
    def test_one_contract_and_hundred_fragments_cent_precision(self):
        single=compare([('.5','1','buy')],'.01');split=compare(tiny('.5'),'.01')
        self.assertEqual(D(single['all_in']),D('.52'));self.assertEqual(D(split['all_in']),D('1'))
        self.assertEqual(D(split['rounding_carry']),D('.4825'))
    def test_direct_precision_fragmented_half_price(self):
        single=compare([('.5','1','buy')],'.0001');split=compare(tiny('.5'),'.0001')
        self.assertEqual(D(single['all_in']),D('.5175'));self.assertEqual(D(split['all_in']),D('.5175'))
        self.assertEqual(sum(D(f['rebate']) for f in split['fills']),D('.0025'));self.assertEqual(D(split['rounding_carry']),0)
    def test_cent_rebate_cap_defeats_large_accumulator(self):
        s=compare(tiny('.5'),'.01')
        self.assertGreater(D(s['rounding_carry']),D('.01'))
        self.assertTrue(all(D(f['rebate'])==0 for f in s['fills']))
        self.assertTrue(all(D(f['trade'])+D(f['rounding'])==D('.005') for f in s['fills']))
    def test_all_99_cent_prices_attain_pre_reserve_one_dollar(self):
        for cents in range(1,100):
            s=compare(tiny(D(cents)/100),'.01')
            self.assertEqual(D(s['all_in']),D(1),str(cents))
            self.assertTrue(all(D(f['rebate'])==0 for f in s['fills']))
    def test_subcent_and_edge_prices_do_not_force_rebate(self):
        for price in ['.0001','.001','.055','.1234','.5001','.9','.999','.9999']:
            for precision in ['.01','.0001']:
                s=compare(tiny(price),precision)
                self.assertTrue(all(D(f['net'])>=0 for f in s['fills']))
    def test_mixed_price_fractional_fills_agree_with_oracle(self):
        fills=[('.15','.25','buy'),('.35','.25','buy'),('.65','.5','buy')]
        for precision in ['.01','.0001']:compare(fills,precision)
    def test_order_accumulator_does_rebate_when_fill_cap_permits(self):
        s=compare([('.5','1','buy')]*4,'.01')
        self.assertEqual(sum(D(f['rebate']) for f in s['fills']),D('.01'))
        self.assertEqual(D(s['fee']),D('.07'));self.assertEqual(D(s['rounding_carry']),0)
    def test_accumulator_scope_is_order_not_account(self):
        one_order=compare([('.5','1','buy')]*4,'.01')
        four_orders=sum(D(compare([('.5','1','buy')],'.01')['fee']) for _ in range(4))
        self.assertEqual(D(one_order['fee']),D('.07'));self.assertEqual(four_orders,D('.08'))
    def test_seeded_one_contract_groupings_are_bounded_without_rebates(self):
        rng=random.Random(719)
        for case in range(200):
            units=100;fills=[]
            while units:
                quantity=rng.randint(1,units);units-=quantity
                fills.append((str(D(rng.randint(1,9999))/10000),str(D(quantity)/100),'buy'))
            # Aggregate repeated book levels; execution_cost independently consumes one full contract.
            book={}
            for price,q,action in fills:book[D(price)]=book.get(D(price),D(0))+D(q)
            bounds=execution_cost([(str(1-p),str(q)) for p,q in book.items()],{'slippage_reserve_cents':'.5'})
            for precision in ['.01','.0001']:
                s=compare(fills,precision)
                self.assertLessEqual(D(s['fee']),D(bounds['fragmentation_fee_bounds'][str(D(precision))]))
    def test_invalid_fractional_granularity_is_rejected(self):
        for quantity in ['.001','0','-1','nan']:
            with self.assertRaises((ValueError,ArithmeticError)):fee_sequence([('.5',quantity,'buy')],'.01')
    def test_endpoint_model_fees_are_zero(self):
        for price in ['0','1']:
            for precision in ['.01','.0001']:
                s=compare([(price,'1','buy')],precision);self.assertEqual(D(s['fee']),0)
    def test_current_acceptance_keeps_both_precisions_and_stress(self):
        s=execution_cost([('.5','1')],{'slippage_reserve_cents':'.5'})
        self.assertEqual(set(s['fragmentation_fee_bounds']),{'0.0001','0.01'})
        self.assertEqual(D(s['cost']),D('1.005'));self.assertEqual(D(s['stress_cost']),D('1.015'))
        self.assertFalse(s['actual_fills'])
    def test_precision_alone_does_not_guarantee_profit_at_high_price(self):
        low=compare(tiny('.5'),'.0001');high=compare(tiny('.9999'),'.0001')
        self.assertLess(D(low['all_in'])+D('.015'),D(1))
        self.assertGreater(D(high['all_in'])+D('.015'),D(1))
    def test_fragmentation_affects_direct_rounding_at_other_price(self):
        single=compare([('.055','1','buy')],'.0001');split=compare(tiny('.055'),'.0001')
        self.assertEqual(D(single['all_in']),D('.0587'));self.assertEqual(D(split['all_in']),D('.06'))

def matrix():
    rows=[]
    for price in ['.01','.055','.50','.99','.9999']:
        for precision in ['.0001','.01']:
            for parts in [1,100]:
                fills=[(price,str(D(1)/parts),'buy')]*parts
                s=compare(fills,precision);pre=D(s['all_in'])
                rows.append({'price':price,'balance_precision':precision,'order_count':1,'fill_count':parts,'quantity_contracts':'1','pre_reserve_all_in':str(pre),'fee':s['fee'],'remaining_order_carry':s['rounding_carry'],'rebates':str(sum(D(f['rebate']) for f in s['fills'])),'with_reserve':str(pre+D('.005')),'with_stress_reserve':str(pre+D('.015')),'winning_stress_net':str(1-pre-D('.015')),'actual_execution':False})
    return rows

if __name__=='__main__':
    stream=io.StringIO()
    with patch.object(socket,'create_connection',side_effect=AssertionError('OFFLINE TEST NETWORK DISABLED')):
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FeeFeasibility))
        rows=matrix()
    (HERE/'fee_test_output.txt').write_text(stream.getvalue())
    (HERE/'conditional_calculations.json').write_text(json.dumps(rows,indent=2)+'\n')
    summary={'run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'seeded_groupings':200,'cent_price_cases':99,'matrix_cases':len(rows),'network_disabled':True,'actual_execution':False}
    (HERE/'fee_test_results.json').write_text(json.dumps(summary,indent=2)+'\n');print(stream.getvalue());print(json.dumps(summary,indent=2));sys.exit(not result.wasSuccessful())
