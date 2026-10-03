import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal as D
import test_btc_copilot as base
import btc_copilot as b
import btc_copilot_research as r

class UpgradeTests(unittest.TestCase):
    def test_depth_complement_partial_and_fees(self):
        result=r.walk([['.6','2'],['.5','4']],'3',True,1)
        self.assertEqual(D(result['notional']),D('1.3'))
        self.assertEqual(D(result['conservative_fee']),D('.06'))
        self.assertTrue(result['complete'])
        self.assertFalse(r.walk([['.6','2']],'3',True)['complete'])
        self.assertIsNone(r.walk([],'3',True)['average_price'])
        self.assertEqual(D(r.walk([['.6','2'],['.5','4']],'3')['notional']),D('1.7'))

    def test_settings_reject_weaker_or_nonfinite_safety(self):
        settings=r.load_settings(b.ROOT/'btc_copilot_settings.json')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'s.json'
            for key,value in [('model_uncertainty_reserve_pp',6),('final_entry_cutoff_seconds',89),('maximum_snapshot_skew_seconds',float('nan'))]:
                changed={**settings,key:value};path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):r.load_settings(path)

    def test_unavailable_data_overrides_exit_and_target_fault(self):
        base.CollectorTests.setUpClass();h=base.CollectorTests();raw=copy.deepcopy(h.raw)
        raw['market']['data']['market']['floor_strike']='1'
        c=h.make(raw);c.positions=True;old_get=c.client.get;old_pages=c.client.pages
        c.client.get=lambda path,params=None: {'data':{'market_positions':[{'ticker':raw['market']['data']['market']['ticker'],'position_fp':'1'}]}} if path=='/portfolio/positions' else old_get(path,params)
        c.client.pages=lambda path,params=None,max_pages=3: {'complete':True,'pages':[{'data':{'fills':[]}}]} if path=='/portfolio/fills' else old_pages(path,params,max_pages)
        st=b.structure(b.ticks_from_response(raw['benchmark']));st['support']=D('1000000000')
        with patch.object(b.time,'time',return_value=h.now),patch.object(b,'spot_get',return_value=raw['spot']),patch.object(b,'structure',return_value=st):s=c.collect()
        self.assertTrue(s['position']['action'].startswith('GUIDANCE UNAVAILABLE'))
        self.assertFalse(s['position']['guidance_valid'])

    def test_replay_roundtrip_and_one_forecast_per_contract(self):
        base.CollectorTests.setUpClass();h=base.CollectorTests();raw=copy.deepcopy(h.raw)
        s=h.collect(raw);s.pop('validation',None);s['time_remaining_seconds']=300
        settings=r.load_settings(b.ROOT/'btc_copilot_settings.json')
        with tempfile.TemporaryDirectory() as temp:
            a=r.Audit(Path(temp)/'audit.sqlite3');journal=Path(temp)/'journal'
            a.record(s,raw,None,settings,journal);a.record(s,raw,None,settings,journal)
            self.assertTrue(r.replay(a.path)['matches'])
            if not s['data_quality_faults']:self.assertEqual(a.scorecard()['cohorts'][0]['contracts'],1)

    def test_delayed_signal_unavailable_is_not_a_fill(self):
        base.CollectorTests.setUpClass();h=base.CollectorTests();raw=copy.deepcopy(h.raw);s=h.collect(raw)
        settings=r.load_settings(b.ROOT/'btc_copilot_settings.json')
        s.pop('validation',None)
        s.update(time_remaining_seconds=300,data_quality_faults=[],decision='UP')
        with tempfile.TemporaryDirectory() as temp:
            a=r.Audit(Path(temp)/'a.sqlite3');journal=Path(temp)/'journal'
            a.record(s,raw,None,settings,journal)
            s=copy.deepcopy(s);s['epoch']+=40;s['decision']='NO TRADE';a.record(s,raw,None,settings,journal)
            g=a.scorecard()['cohorts'][0]
            self.assertEqual(g['delay_checked'],1);self.assertEqual(g['actionable_after_delay'],0)
            journal.write_text(json.dumps({'ticker':s['ticker'],'official_result':'UP'})+'\n')
            a.record(s,raw,None,settings,journal)
            g=a.scorecard()['cohorts'][0];self.assertEqual(g['settled'],1);self.assertEqual(g['settled_hypothetical_entries'],0)

if __name__=='__main__':unittest.main()
