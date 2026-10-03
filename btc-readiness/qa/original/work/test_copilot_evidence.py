import copy,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
import test_btc_copilot as base
import btc_copilot as b
import btc_copilot_evidence as e
import btc_copilot_research as r

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        base.CollectorTests.setUpClass();self.h=base.CollectorTests();self.raw=copy.deepcopy(self.h.raw)
        self.s=self.h.collect(self.raw);self.settings=r.load_settings(b.ROOT/'btc_copilot_settings.json')
        self.p=json.loads((b.ROOT/'btc_copilot_protocol.json').read_text())
        self.start=e.timestamp(self.p['start_utc']);self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pp=Path(self.temp.name)/'protocol.json';self.pp.write_text(json.dumps(self.p));self.path=Path(self.temp.name)/'study.db'
        self.study=e.Study(self.path,self.pp,self.s['model_version'],r.SOURCE_TEXT)
        self.s.update(epoch=self.start+300,open_time=self.p['start_utc'],close_time=b.stamp(self.start+900)['utc'],time_remaining_seconds=600,
                      valid_until_epoch=self.start+320,operational_vetoes=[])
        self.s['book'].update(yes_bid_dollars=D('.40'),yes_ask_dollars=D('.41'))
        self.s['shadow_strategies']={n:{'eligible':False,'side':'UP'} for n in e.STRATEGIES}
        self.s['shadow_strategies']['drift_free']['eligible']=True
        self.raw['book']['data']={'orderbook_fp':{'yes_dollars':[['.40','100']],'no_dollars':[['.59','100']]}}
    def record(self):self.study.record(self.s,self.raw,None,self.settings)
    def later(self,seconds):
        self.s['epoch']+=seconds;self.s['time_remaining_seconds']-=seconds;self.s['valid_until_epoch']=self.s['epoch']+20
        self.s['retrieval_health']['requests']['book']['started_at_epoch']=self.s['epoch']
    def test_checkpoint_matched_once(self):
        self.record();self.later(15);self.record()
        with self.study.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM signals').fetchone()[0],1)
    def test_first_delayed_quote_frozen_and_not_reselected(self):
        self.record();self.later(15);self.record()
        with self.study.db() as c:cost=c.execute('SELECT cost FROM signals').fetchone()[0]
        self.assertAlmostEqual(cost,.435)
        self.raw['book']['data']['orderbook_fp']['no_dollars']=[['.95','100']];self.later(15);self.record()
        with self.study.db() as c:self.assertEqual(c.execute('SELECT cost FROM signals').fetchone()[0],cost)
    def test_partial_depth_and_outage_are_not_fills(self):
        self.record();self.later(15);self.raw['book']['data']['orderbook_fp']['no_dollars']=[['.59','.20']];self.record()
        with self.study.db() as c:self.assertEqual(c.execute('SELECT status,cost FROM signals').fetchone(),('insufficient_depth',None))
    def test_late_observation_kept_as_missed(self):
        self.record();self.later(50);self.record()
        with self.study.db() as c:self.assertEqual(c.execute('SELECT status,cost FROM signals').fetchone(),('missed_observation',None))
    def test_version_change_does_not_reset_study(self):
        with self.assertRaises(ValueError):e.Study(self.path,self.pp,'changed',{})
    def test_fee_verification_refresh_does_not_change_behavior_version(self):
        settings={**self.settings,'fee_verified_at':'2030-01-01T00:00:00+00:00'}
        self.assertEqual(r.version(settings),r.version(self.settings))
        settings['minimum_conservative_net_edge_pp']=6;self.assertNotEqual(r.version(settings),r.version(self.settings))
    def test_holdout_not_in_report_before_release(self):
        self.record()
        with self.study.db() as c:c.execute("UPDATE markets SET phase='holdout',result=1")
        out=e.report(self.path,self.start+86400*20)
        self.assertTrue(out['holdout_locked']);self.assertNotIn('holdout',out['phases'])
        self.assertNotIn('eligible_for_independent_review',out)
    def test_no_promotion_even_after_release_with_tiny_sample(self):
        self.record()
        with self.study.db() as c:c.execute("UPDATE markets SET phase='holdout',result=1")
        out=e.report(self.path,e.timestamp(self.p['release_utc'])+1)
        self.assertFalse(out['eligible_for_independent_review']);self.assertFalse(out['validated'])
    def test_changed_evaluator_cannot_relabel_old_results(self):
        import zlib
        with self.study.db() as c:c.execute("UPDATE meta SET value=? WHERE key='sources'",(zlib.compress(json.dumps({'btc_copilot_evidence.py':'changed'}).encode()),))
        with self.assertRaises(ValueError):e.report(self.path,self.start)
    def test_day_blocks_need_days_not_snapshots(self):
        self.assertIsNone(e.bootstrap_day_mean({'one_day':1000}))
        self.assertEqual(e.bootstrap_day_mean({str(i):1 for i in range(20)}),[1,1])
    def test_duplicate_ticks_and_invalid_fill_rejected(self):
        raw=copy.deepcopy(self.raw['benchmark']);raw['data']['data']['payload'].append(raw['data']['data']['payload'][-1])
        with self.assertRaises(b.ReadError):b.ticks_from_response(raw)
        with self.assertRaises(b.ReadError):b.fill_ledger([{'ticker':'T','outcome_side':'yes','count_fp':'1','yes_price_dollars':'.5'}],'T')
    def test_shadow_snapshots_never_enter_legacy_scorecard(self):
        audit=r.Audit(Path(self.temp.name)/'audit.db');audit.record(self.s,self.raw,None,self.settings,Path(self.temp.name)/'journal')
        self.assertEqual(audit.scorecard()['cohorts'],[])
    def test_operational_fault_blocks_every_shadow_arm(self):
        self.s['operational_vetoes']=['Fee policy expired']
        self.assertTrue(all(not row['eligible'] for row in e.candidates(self.s,None,self.settings).values()))
    def test_wrong_market_and_invalid_book_fail_closed(self):
        raw=copy.deepcopy(self.raw)
        raw['book']['data']['orderbook_fp']['yes_dollars']=[['1.01','1']]
        with self.assertRaises(b.ReadError):self.h.collect(raw)
        raw=copy.deepcopy(self.raw);raw['event']['data']['event']['event_ticker']='WRONG'
        with self.assertRaises(b.ReadError):self.h.collect(raw)
    def test_fresh_shadow_signal_never_enables_advice(self):
        s=self.h.candidate(.9,.8,'.40','.59')
        self.assertEqual(s['shadow_decision'],'UP')
        self.assertEqual(s['decision'],'NO TRADE')
        self.assertFalse(s['validation']['demonstrated_edge'])
        self.assertFalse(s['position']['guidance_valid'])
        self.assertTrue(all(not x['entry_eligible'] for x in s['quantity_previews']))
    def test_public_settlement_does_not_require_private_fills(self):
        self.record();ticker=self.s['ticker']
        class Client:
            def market(self,t):return {'data':{'market':{'ticker':t,'status':'finalized','result':'yes'}}}
        self.study.settle(Client(),self.start+1000)
        with self.study.db() as c:self.assertEqual(c.execute('SELECT result FROM markets').fetchone()[0],1)

if __name__=='__main__':unittest.main()
