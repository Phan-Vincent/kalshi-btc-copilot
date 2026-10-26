"""Listing-lag proposal; synthetic public envelopes, no network."""
import copy, unittest
from datetime import datetime
from unittest.mock import patch
import runtime_fixture as f
import btc_copilot as b

PREFIX='Cannot verify exactly one active BTC 15-minute contract: '
ORIGINAL=PREFIX+'0 eligible of 0 listed (exchange active)'

class FakeClient:
    def __init__(self):
        self.raw=copy.deepcopy(f.RAW);self.raw['trades']['data']={'trades':[]}
        self.listed=[self.raw['market']['data']['market']]
        self.complete=True;self.direct=[];self.status_calls=0;self.error=None;self.status_error=None
    def pages(self,*args,**kwargs):return {'pages':[{'data':{'markets':self.listed}}],'complete':self.complete}
    def market(self,ticker):
        self.direct.append(ticker)
        if self.error:raise self.error
        return self.raw['market']
    def event(self,*args):return self.raw['event']
    def orderbook(self,*args):return self.raw['book']
    def get(self,path,params=None):
        if path=='/exchange/status':
            self.status_calls+=1
            if self.status_error:raise self.status_error
            return self.raw['exchange']
        if path.endswith('/candlesticks'):return self.raw['candles']
        if path.startswith('/series/'):return self.raw['series']
        return self.raw[{'/cfbenchmarks/values':'benchmark','/markets/trades':'trades'}[path]]

class ListingFallbackTests(unittest.TestCase):
    def setUp(self):
        self.client=FakeClient();self.c=object.__new__(b.Copilot);self.c.client=self.client
        self.c.positions=False;self.c.raw={};self.c.state={'contracts':{},'previous':None}
        self.settings=f.r.load_settings(f.ROOT/'btc_copilot_settings.json');self.settings['fee_verified_at']=b.stamp(f.NOW)['utc']
        self.health={'requests':{k:{'started_at_epoch':f.NOW-.2,'finished_at_epoch':f.NOW,'latency_seconds':.2}
                                for k in ('market','event','series','book','benchmark','trades','candles','spot','exchange')}}
    def collect(self):
        with patch.object(b.time,'time',return_value=f.NOW),patch.object(b,'spot_get',return_value=self.client.raw['spot']),patch.object(b,'load_settings',return_value=self.settings):
            return self.c.collect(replay_health=self.health,persist=False)
    def derived(self):
        self.client.listed=[]
        self.client.raw['market']['data']['market']['ticker']='KXBTC15M-26SEP261400-00'
        self.client.raw['event']['data']['markets']=[copy.deepcopy(self.client.raw['market']['data']['market'])]
    def failure(self,reason):
        with self.assertRaises(b.ReadError) as ctx:self.collect()
        self.assertTrue(str(ctx.exception).startswith(ORIGINAL+'; direct fallback: '),str(ctx.exception))
        self.assertIn(reason,str(ctx.exception));self.assertLessEqual(len(str(ctx.exception)),200)

    def test_normal_listing(self):
        self.assertEqual(self.collect()['retrieval_health']['discovery'],'listing')
        self.assertEqual(len(self.client.direct),1);self.assertEqual(self.client.status_calls,1)
    def test_remembered_listing(self):
        self.collect();self.client.direct=[];self.client.status_calls=0;self.client.listed=[]
        s=self.collect();self.assertEqual(s['retrieval_health']['discovery'],'remembered_ticker')
        self.assertEqual(self.client.direct,[s['ticker'],s['ticker']]);self.assertEqual(self.client.status_calls,2)
    def test_derived_listing(self):
        self.derived();s=self.collect();self.assertEqual(s['retrieval_health']['discovery'],'derived_ticker')
        self.assertEqual(self.client.direct,[s['ticker'],s['ticker']]);self.assertEqual(self.client.status_calls,2)
    def test_ticker_edt_est_and_live_samples(self):
        self.c.listed_slots={};self.client.error=b.ReadError('stop after selection')
        for utc,t in (('2026-10-24T16:00:35+00:00','KXBTC15M-26OCT241215-15'),
                      ('2026-11-07T16:00:35+00:00','KXBTC15M-26NOV071115-15'),
                      ('2026-10-24T14:45:35+00:00','KXBTC15M-26OCT241100-00'),
                      ('2026-10-25T07:45:35+00:00','KXBTC15M-26OCT250400-00')):
            with self.subTest(utc=utc),self.assertRaises(b.ReadError):self.c.direct_candidate(datetime.fromisoformat(utc).timestamp())
            self.assertEqual(self.client.direct[-1],t)
    def test_http_404_503(self):
        self.derived()
        for code in (404,503):
            self.client.error=b.ReadError('Kalshi GET failed with HTTP '+str(code));self.failure('HTTP '+str(code))
        self.assertEqual(len(self.client.direct),2)
    def test_market_verification(self):
        for changes in ({'close_time':'2026-09-26T18:15:00Z'},{'status':'closed'},{'event_ticker':'OTHER'},
                        {'ticker':'OTHER'},{'open_time':'bad timestamp'},
                        {'open_time':'2026-09-26T17:45:01Z','close_time':'2026-09-26T18:00:01Z'}):
            with self.subTest(changes=changes):
                self.setUp();self.derived();self.client.raw['market']['data']['market'].update(changes)
                self.failure('slot mismatch' if changes.get('open_time','').endswith('01Z') else
                             'identity mismatch' if 'ticker' in changes else
                             'ValueError' if changes.get('open_time')=='bad timestamp' else 'verification failed')
                self.assertEqual(len(self.client.direct),1)
    def test_long_direct_error_detail_capped(self):
        self.derived();self.client.error=b.ReadError('HTTP 503 '+('x'*500))
        self.failure('HTTP 503')
    def test_failed_remembered_read_does_not_try_derived(self):
        self.collect();self.client.direct=[];self.client.listed=[]
        self.client.error=b.ReadError('Kalshi GET failed with HTTP 404');self.failure('HTTP 404')
        self.assertEqual(self.client.direct,[f.RAW['market']['data']['market']['ticker']])
    def test_adapter_without_direct_reader_keeps_original_error(self):
        self.client.listed=[];self.client.market=None
        with self.assertRaises(b.ReadError) as ctx:self.collect()
        self.assertEqual(str(ctx.exception),ORIGINAL)
    def test_status_unreadable(self):
        self.client.listed=[];self.client.status_error=b.ReadError('status outage')
        with self.assertRaises(b.ReadError) as ctx:self.collect()
        self.assertEqual(str(ctx.exception),PREFIX+'0 eligible of 0 listed (status unreadable)');self.assertEqual(self.client.direct,[])
    def test_false_status_flags(self):
        for flags in ((False,True),(True,False),(False,False)):
            self.client.listed=[];self.client.raw['exchange']['data']=dict(zip(('exchange_active','trading_active'),flags))
            with self.subTest(flags=flags),self.assertRaises(b.ExchangeClosed):self.collect()
            self.assertEqual(self.client.direct,[])
    def test_nonboolean_status(self):
        self.client.listed=[];self.client.raw['exchange']['data']={'exchange_active':1,'trading_active':True}
        with self.assertRaisesRegex(b.ReadError,'status unreadable'):self.collect()
        self.assertEqual(self.client.direct,[])
    def test_two_eligible_or_duplicate_listing(self):
        m=self.client.listed[0]
        for other in (dict(m,ticker=m['ticker']+'-2'),m):
            self.client.listed=[m,other]
            with self.assertRaisesRegex(b.ReadError,PREFIX):self.collect()
            self.assertEqual(self.client.direct,[]);self.assertEqual(self.client.status_calls,0)
    def test_incomplete_discovery(self):
        self.client.complete=False
        with self.assertRaisesRegex(b.ReadError,'truncated'):self.collect()
        self.assertFalse(getattr(self.c,'listed_slots',{}));self.assertEqual(self.client.direct,[])
    def test_memory_slots_pruned(self):
        m=copy.deepcopy(self.client.listed[0]);slot=int(f.NOW)//900*900
        rows=[dict(m,ticker=m['ticker']+(str(n) if n else ''),open_time=b.stamp(slot+n)['utc'],close_time=b.stamp(slot+n+900)['utc']) for n in (-900,0,900,1800)]
        self.client.listed=rows;self.collect();self.assertEqual(set(self.c.listed_slots),{slot,slot+900})
        self.c.remember_listing([],slot+901);self.assertEqual(set(self.c.listed_slots),{slot+900})
        self.client.raw['market']['data']['market']=rows[2]
        m,source=self.c.direct_candidate(slot+901);self.assertEqual(source,'remembered_ticker');self.assertEqual(m['ticker'],rows[2]['ticker'])
        self.c.remember_listing([],slot+1800);self.assertEqual(self.c.listed_slots,{})
    def test_ambiguous_memory(self):
        self.derived();self.c.listed_slots={int(f.NOW)//900*900:{'KXBTC15M-A','KXBTC15M-B'}}
        self.failure('ambiguous remembered tickers');self.assertEqual(self.client.direct,[])
    def test_event_different_or_second_eligible_market(self):
        for count in (1,2):
            with self.subTest(count=count):
                self.setUp();self.derived();m=copy.deepcopy(self.client.raw['market']['data']['market'])
                other=dict(m,ticker=m['ticker']+'-2');self.client.raw['event']['data']['markets']=[other] if count==1 else [m,other]
                self.failure('Event market membership')
    def test_event_empty_duplicate_or_malformed_markets(self):
        for value in ([],[f.RAW['market']['data']['market']]*2,None,{}):
            with self.subTest(value=value):
                self.setUp();self.derived();self.client.raw['event']['data']['markets']=value;self.failure('Event market')
    def test_event_identity_and_series(self):
        for key in ('event_ticker','series_ticker'):
            self.setUp();self.derived();self.client.raw['event']['data']['event'][key]='OTHER';self.failure('Event')
    def test_event_without_markets(self):
        self.derived();del self.client.raw['event']['data']['markets']
        self.assertEqual(self.collect()['retrieval_health']['discovery'],'derived_ticker')
    def test_nested_event_markets(self):
        self.derived();body=self.client.raw['event']['data'];body['event']['markets']=body.pop('markets')
        self.assertEqual(self.collect()['retrieval_health']['discovery'],'derived_ticker')
        body['event']['markets']=[];self.failure('Event market membership')
    def test_normal_listing_event_membership(self):
        self.client.raw['event']['data']['markets']=[]
        with self.assertRaisesRegex(b.ReadError,'Event market membership'):self.collect()

if __name__=='__main__':unittest.main()
