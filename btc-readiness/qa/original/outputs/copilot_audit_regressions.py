import importlib.util,json,sqlite3,tempfile,unittest,zlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'outputs'))
import btc_copilot_evidence as frozen
P=json.loads((ROOT/'outputs/btc_copilot_protocol.json').read_text())

def module(path):
    spec=importlib.util.spec_from_file_location('candidate_evaluator',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def setup(m,temp):
    pp=Path(temp)/'protocol.json';pp.write_text(json.dumps(P));db=Path(temp)/'synthetic.sqlite3'
    study=m.Study(db,pp,'synthetic-v1',{'btc_copilot_evidence.py':m.EVALUATOR_SOURCE})
    return study,db

def market(study,ticker,opened,checkpoint=None,result=None):
    features={'p_drift':1.,'p_zero':1.,'p_market':.5,'regime':'quiet','volatility_regime':'low','utc_session':'00-08'}
    with study.db() as c:c.execute('INSERT INTO markets(ticker,phase,day,opened,closed,first_seen,checkpoint,features,result) VALUES (?,?,?,?,?,?,?,?,?)',(ticker,'holdout',frozen.utc_day(opened),opened,opened+900,opened,checkpoint,json.dumps(features),result))

def starvation(m):
    with tempfile.TemporaryDirectory() as temp:
        study,db=setup(m,temp);start=m.timestamp(P['start_utc'])
        for i in range(4):market(study,'T'+str(i),start+i*900)
        calls=[]
        class Client:
            def market(self,t):
                calls.append(t);return {'data':{'market':{'ticker':t,'status':'active' if t!='T3' else 'finalized','result':'yes'}}}
        for cycle in range(4):study.settle(Client(),start+10000+cycle*60)
        return 'T3' in calls

def incomplete_review(m):
    with tempfile.TemporaryDirectory() as temp:
        study,db=setup(m,temp);start=m.timestamp(P['holdout_start_utc'])
        for day in range(28):
            for slot in range(96):
                t=f'T{day}-{slot}';opened=start+day*86400+slot*900;pending=slot<3
                market(study,t,opened,opened+300,None if pending else 1)
                # 112 resolved wins and 84 unresolved fills, all synthetic.
                if slot<7:
                    with study.db() as c:c.execute('INSERT INTO signals(ticker,strategy,side,epoch,checked,status,cost,stress_cost) VALUES (?,?,?,?,?,?,?,?)',(t,'drift_free','UP',opened+300,1,'hypothetical_fill',.8,.81))
        result=m.report(db,m.timestamp(P['release_utc'])+1)
        return result['eligible_for_independent_review'],result['phases']['holdout']['strategies']['drift_free']['attempts']

class FrozenReproductions(unittest.TestCase):
    def test_frozen_starves_fourth_market(self):self.assertFalse(starvation(frozen))
    def test_frozen_can_pass_with_unresolved_fills_excluded(self):
        eligible,attempts=incomplete_review(frozen);self.assertTrue(eligible);self.assertEqual(attempts,112)


candidate=module(ROOT/'outputs/copilot_candidate_v4/btc_copilot_evidence.py')

def execution(m):
    from decimal import Decimal as D
    with tempfile.TemporaryDirectory() as temp:
        study,db=setup(m,temp);start=m.timestamp(P['start_utc']);t=start+300
        s={'ticker':'SYNTHETIC','model_version':'synthetic-v1','epoch':t,'open_time':P['start_utc'],'close_time':frozen.datetime.fromtimestamp(start+900,frozen.timezone.utc).isoformat(),
           'fee_multiplier':'1','shadow_strategies':{'drift_free':{'eligible':True,'side':'UP','probability':.9}},
           'time_remaining_seconds':600,'operational_vetoes':[],'book':{'yes_bid_dollars':D('.45'),'yes_ask_dollars':D('.46')},
           'model':{'p_up':.9},'drift_free_model':{'p_up':.9},'structure':{'sigma_dollars_sqrt_second':1,'hour_trend_z':1},
           'valid_until_epoch':t+20,'retrieval_health':{'requests':{'book':{'started_at_epoch':t-.2,'finished_at_epoch':t}}}}
        raw={'book':{'data':{'orderbook_fp':{'yes_dollars':[['.45','100']],'no_dollars':[['.54','100']]}}}}
        settings={'slippage_reserve_cents':'.5'}
        study.record(s,raw,None,settings)
        s['epoch']=t+15;s['time_remaining_seconds']=585;s['valid_until_epoch']=t+35
        s['retrieval_health']['requests']['book']={'started_at_epoch':t+14,'finished_at_epoch':t+15}
        s['shadow_strategies']['drift_free']['probability']=.8
        study.record(s,raw,None,settings)
        with study.db() as c:early=c.execute('SELECT checked,status FROM signals').fetchone()
        s['epoch']=t+30;s['time_remaining_seconds']=570;s['valid_until_epoch']=t+50
        s['retrieval_health']['requests']['book']={'started_at_epoch':t+29,'finished_at_epoch':t+30}
        study.record(s,raw,None,settings)
        with study.db() as c:
            original=json.loads(zlib.decompress(c.execute('SELECT evidence FROM signals').fetchone()[0]))
            final=c.execute('SELECT checked,status FROM signals').fetchone()
        return early,final,original

class CandidateRegressions(unittest.TestCase):
    def test_fair_settlement_visits_fourth_market(self):self.assertTrue(starvation(candidate))
    def test_incomplete_results_cannot_pass_and_attempts_retained(self):
        eligible,attempts=incomplete_review(candidate);self.assertFalse(eligible);self.assertEqual(attempts,196)
    def test_early_request_not_filled_later_eligible_request_used(self):
        early,final,origin=execution(candidate)
        self.assertEqual(early,(0,'pending'));self.assertEqual(final,(1,'hypothetical_fill'))
        self.assertEqual(origin['snapshot']['shadow_strategies']['drift_free']['probability'],.9)
        self.assertIn('settings',origin);self.assertIn('raw',origin)
    def test_frozen_early_fill_and_origin_overwrite_reproduced(self):
        early,final,origin=execution(frozen)
        self.assertEqual(early,(1,'hypothetical_fill'));self.assertEqual(origin['candidate']['probability'],.8)
        self.assertNotIn('snapshot',origin)
    def test_staged_monitor_settlement_is_in_finally(self):
        import ast
        tree=ast.parse((ROOT/'outputs/copilot_candidate_v4/btc_copilot.py').read_text())
        def calls(nodes):return any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='settle' for node in nodes for n in ast.walk(node))
        self.assertTrue(any(isinstance(n,ast.Try) and calls(n.finalbody) for n in ast.walk(tree)))

class DisclosureAndHealth(unittest.TestCase):
    def test_holdout_not_parsed_before_release(self):
        for m in (frozen,candidate):
            with tempfile.TemporaryDirectory() as temp:
                study,db=setup(m,temp);start=m.timestamp(P['holdout_start_utc'])
                market(study,'SYNTHETIC-SECRET',start,start+300,1)
                with study.db() as c:c.execute("UPDATE markets SET features='INVALID: MUST NEVER PARSE'")
                result=m.report(db,m.timestamp(P['release_utc'])-1)
                self.assertTrue(result['holdout_locked']);self.assertNotIn('holdout',result['phases'])
                self.assertNotIn('SYNTHETIC-SECRET',json.dumps(result))
    def test_health_rejects_stale_future_missing_lock_and_enabled_advice(self):
        from copilot_health_check import assess
        good={'epoch':100.,'valid_until_epoch':120.,'decision':'NO TRADE','validation':{'status':'UNVALIDATED_SHADOW_ONLY'},'position':{'guidance_valid':False}}
        self.assertTrue(assess(good,{'pid':123},True,110)['healthy'])
        for now,held,change in [(121,True,{}),(97,True,{}),(110,False,{}),(110,True,{'decision':'UP'}),(110,True,{'validation':[]})]:
            self.assertFalse(assess(dict(good,**change),{'pid':123},held,now)['healthy'])
    def test_health_malformed_inputs_fail_closed(self):
        from copilot_health_check import assess
        for value in (None,[],{'epoch':float('nan'),'valid_until_epoch':120}, {'epoch':True,'valid_until_epoch':120}):
            self.assertFalse(assess(value,None,False,110)['healthy'])

if __name__=='__main__':unittest.main()
