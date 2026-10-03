import copy
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs'))
import btc_copilot as b
from kalshi_readonly import KalshiReadOnly, ReadError, summarize_book


class ModelTests(unittest.TestCase):
    def test_direction_and_uncertainty(self):
        st={'sigma_dollars_sqrt_second':1.,'changes':{'5m':{'dollars':D('0')}}}
        ticks=[(1000+i,D('105')) for i in range(3600)]
        up=b.probability(ticks,D('100'),ticks[-1][0]+300,st)
        down=b.probability(ticks,D('110'),ticks[-1][0]+300,st)
        self.assertGreater(up['p_up'],.5)
        self.assertLess(down['p_up'],.5)
        self.assertLess(up['up_sensitivity_low'],up['p_up'])
        self.assertGreater(up['up_sensitivity_high'],up['p_up'])
        self.assertAlmostEqual(up['p_up']+up['p_down'],1)
        self.assertFalse(up['calibrated'])

    def test_known_settlement_average_dominates_last_tick(self):
        ticks=[(1000+i,D('110')) for i in range(3600)]
        ticks[-1]=(ticks[-1][0],D('90'))
        st={'sigma_dollars_sqrt_second':1.,'changes':{'5m':{'dollars':D('0')}}}
        p=b.probability(ticks,D('100'),ticks[-1][0]+1,st)
        self.assertGreater(p['p_up'],.99)

    def test_missing_settlement_samples_fail_closed(self):
        ticks=[(1000+i,D('110')) for i in range(3600)]
        del ticks[-5]
        st={'sigma_dollars_sqrt_second':1.,'changes':{'5m':{'dollars':D('0')}}}
        with self.assertRaises(ReadError):b.probability(ticks,D('100'),ticks[-1][0]+1,st)

    def test_fees_and_subcent_grid(self):
        market={'price_ranges':[{'start':'0','end':'.1','step':'.001'},
                                {'start':'.1','end':'.9','step':'.01'},
                                {'start':'.9','end':'1','step':'.001'}]}
        self.assertEqual(b.taker_fee(D('.5'),1),D('.02'))
        self.assertIn(D('.035'),b.grid_prices(market))
        self.assertNotIn(D('.345'),b.grid_prices(market))
        ceiling=b.price_ceiling(market,.6,1,D('.05'))
        self.assertLessEqual(ceiling+b.taker_fee(ceiling,1)+D('.005')+D('.05'),D('.6'))
        self.assertLess(b.price_ceiling(market,.6,1,D('.05')),D('.6'))

    def test_empty_book_not_fabricated(self):
        book=summarize_book({'orderbook_fp':{'yes_dollars':[],'no_dollars':[]}})
        self.assertIsNone(book['yes_ask_dollars'])
        self.assertIsNone(book['yes_midpoint_estimate_dollars'])

    def test_mutating_endpoints_rejected(self):
        c=KalshiReadOnly()
        for route in ('/portfolio/orders/batched','/portfolio/transfers','/portfolio/positions/../orders','https://evil.test','/cfbenchmarks/anything'):
            with self.assertRaises(ReadError):c.get(route)

    def test_fill_inventory_reductions_and_reversal(self):
        def fill(i,side,qty,yes):
            return {'fill_id':str(i),'ticker':'T','created_time':f'2026-09-26T00:00:0{i}Z',
                    'outcome_side':side,'count_fp':str(qty),'yes_price_dollars':str(yes),
                    'no_price_dollars':str(1-D(str(yes)))}
        first=fill(1,'no',10,.3)
        add=fill(2,'no',10,.4)
        reduction=fill(3,'yes',5,.5)
        r=b.fill_ledger([first,add,reduction,reduction],'T')
        self.assertEqual(r['quantity'],D('-15'))
        self.assertEqual(r['average_entry_price'],D('.65'))
        self.assertEqual([f['observed_action'] for f in r['actions']],['ENTRY','ADD','REDUCE'])
        r=b.fill_ledger([first,fill(4,'yes',20,.5)],'T')
        self.assertEqual(r['quantity'],D('10'))
        self.assertEqual(r['average_entry_price'],D('.5'))
        self.assertEqual(r['actions'][-1]['observed_action'],'EXIT_AND_REVERSE')


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=json.loads((Path(__file__).parent/'btc_copilot_test_fixture.json').read_text())
        cls.now=cls.raw['benchmark']['data']['data']['payload'][-1]['time']/1000+1.5

    def make(self,raw):
        class Fake:
            def pages(self,path,params=None,max_pages=3):
                return {'pages':[{'data':{'markets':[raw['market']['data']['market']]}}],'complete':True}
            def market(self,t):return raw['market']
            def event(self,t):return raw['event']
            def orderbook(self,t):return raw['book']
            def get(self,path,params=None):
                if path.startswith('/series/') and path.endswith('/candlesticks'):return raw['candles']
                if path.startswith('/series/'):return raw['series']
                if path=='/cfbenchmarks/values':return raw['benchmark']
                if path=='/markets/trades':return raw['trades']
                if path=='/exchange/status':return raw['exchange']
                raise AssertionError(path)
        c=object.__new__(b.Copilot);c.client=Fake();c.positions=False;c.state={'previous':None,'contracts':{}};c.raw={};c.record=lambda s:None
        return c

    def collect(self,raw,now=None):
        with patch.object(b.time,'time',return_value=now or self.now),patch.object(b,'spot_get',return_value=raw['spot']):
            return self.make(raw).collect()

    def test_first_snapshot_abstains(self):
        s=self.collect(copy.deepcopy(self.raw))
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertLessEqual(s['valid_until_epoch'],self.now+20)

    def test_stale_benchmark_abstains(self):
        raw=copy.deepcopy(self.raw)
        raw['benchmark']['data']['data']['payload']=raw['benchmark']['data']['data']['payload'][:-30]
        s=self.collect(raw)
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertTrue(any('stale' in f for f in s['risk_flags']))

    def test_rolled_contract_rejected(self):
        close=b.dt(self.raw['market']['data']['market']['close_time']).timestamp()
        with self.assertRaises(ReadError):self.collect(copy.deepcopy(self.raw),close+1)

    def test_wrong_target_abstains(self):
        raw=copy.deepcopy(self.raw)
        raw['market']['data']['market']['floor_strike']='1'
        s=self.collect(raw)
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertTrue(any('Target could not be reconciled' in f for f in s['risk_flags']))

    def candidate(self,p,low,yes_bid,no_bid):
        raw=copy.deepcopy(self.raw)
        raw['book']['data']={'orderbook_fp':{'yes_dollars':[[yes_bid,'100']], 'no_dollars':[[no_bid,'100']]}}
        raw['trades']['data']={'trades':[],'cursor':''}
        c=self.make(raw)
        ticks=b.ticks_from_response(raw['benchmark']);st=b.structure(ticks)
        st.update({'bias':'UP','setup_side':'UP','setup':'bullish confirmed synthetic fixture','sigma_dollars_sqrt_second':.1})
        c.state['previous']={'ticker':raw['market']['data']['market']['ticker'],'epoch':self.now-15,
            'btc':float(ticks[-1][1]),'up_ask':1-float(no_bid),'p_up':p-.01,
            'setup':st['setup'],'setup_side':'UP','imbalance':0}
        model={'p_up':p,'p_down':1-p,'up_sensitivity_low':low,'up_sensitivity_high':min(1,p+.1),'calibrated':False}
        with patch.object(b.time,'time',return_value=self.now),patch.object(b,'spot_get',return_value=raw['spot']),patch.object(b,'structure',return_value=st),patch.object(b,'probability',return_value=model):
            return c.collect()

    def test_confirmed_setup_with_large_conservative_edge_can_qualify(self):
        s=self.candidate(.9,.8,'.40','.59')
        self.assertEqual(s['shadow_decision'],'UP')
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertGreaterEqual(s['shadow_entry']['conservative_net_edge_pp'],5)
        self.assertIsNone(s['entry'])
        self.assertIsNone(s['profit_taking_bid_zone'])

    def test_sixty_percent_probability_at_sixty_one_cent_ask_abstains(self):
        s=self.candidate(.6,.55,'.60','.39')
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertTrue(any('Insufficient edge' in f for f in s['risk_flags']))


if __name__=='__main__':unittest.main()
