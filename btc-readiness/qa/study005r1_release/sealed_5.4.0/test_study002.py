"""Synthetic-only suite: no network, credentials or existing study databases."""
import copy,json,sqlite3,tempfile,unittest,zlib,subprocess,sys,hashlib,random
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
import study_policy as policy
import btc_copilot_evidence as e
ROOT=Path(__file__).resolve().parent
P=json.loads((ROOT/'btc_copilot_protocol.json').read_text())

class Rules(unittest.TestCase):
    def test_protocol_and_no_weak_threshold(self):
        policy.validate_protocol(P)
        p=copy.deepcopy(P);p['primary_acceptance']['holdout_coverage']=.95
        with self.assertRaises(ValueError):policy.validate_protocol(p)
    def test_future_utc_dates_and_holdout_duration(self):
        self.assertEqual(policy.epoch(P['end_utc'])-policy.epoch(P['holdout_start_utc']),56*86400)
        p=copy.deepcopy(P);p['start_utc']='2026-10-01T00:00:00'
        with self.assertRaises(ValueError):policy.validate_protocol(p)
    def test_clock_discontinuity_duplicate_future_and_nan(self):
        good={'epoch':100.,'observation_monotonic':100.,'valid_until_epoch':120.,'retrieval_health':{'requests':{'book':{'started_at_epoch':99.,'finished_at_epoch':100.,'latency_seconds':1.}}}}
        policy.check_clock(good,90)
        for field,value in [('finished_at_epoch',101),('started_at_epoch',102),('latency_seconds',12),('latency_seconds',float('nan'))]:
            s=copy.deepcopy(good);s['retrieval_health']['requests']['book'][field]=value
            with self.assertRaises(ValueError):policy.check_clock(s,90)
        with self.assertRaises(ValueError):policy.check_clock(good,100)
        with self.assertRaises(ValueError):policy.check_clock(good,90,99)
        with self.assertRaises(ValueError):policy.execution_cost([['.5','1']],{'slippage_reserve_cents':-1})
    def test_official_fee_example(self):
        official=policy.fee_fills([('.055','1')],'.01')
        self.assertEqual(D(official['fee']),D('.005'))
        self.assertEqual(D(official['fills'][0]['trade']),D('.003639'))
        self.assertEqual(D(official['fills'][0]['rounding']),D('.001361'))
        direct=policy.fee_fills([('.5','1')],'.0001');indirect=policy.fee_fills([('.5','1')],'.01')
        self.assertEqual(D(direct['fee']),D('.0175'));self.assertEqual(D(indirect['fee']),D('.02'))
    def test_fractional_rebates_and_conservation(self):
        fills=[('.27','.1')]*10
        for precision in ('.01','.0001'):
            f=policy.fee_fills(fills,precision);self.assertGreaterEqual(D(f['fee']),0)
            self.assertEqual(D(f['all_in']),D(f['notional'])+D(f['fee']))
            for row in f['fills']:
                self.assertEqual(D(row['net']),D(row['trade'])+D(row['rounding'])-D(row['rebate']))
                self.assertGreaterEqual(D(row['net']),0)
    def test_adversarial_fragmentation_bound(self):
        rng=random.Random(41)
        for _ in range(100):
            fills=[(D(rng.randrange(1,100))/100,D('.01')) for i in range(100)]
            bound=sum((D('.07')*p*q*(1-p) for p,q in fills),D(0))+D('1.0001')
            for precision in ('.01','.0001'):
                self.assertLessEqual(D(policy.fee_fills(fills,precision)['fee']),bound)
    def test_accumulator_is_order_local(self):
        together=policy.fee_fills([('.27','.5')]*2,'.01')
        separate=2*D(policy.fee_fills([('.27','.5')],'.01')['fee'])
        self.assertLess(D(together['fee']),separate)
        self.assertGreater(D(together['fills'][1]['rebate']),0)
    def test_depth_partial_and_invalid_grid(self):
        settings={'slippage_reserve_cents':'.5'}
        self.assertIsNone(policy.execution_cost([['.5','.99']],settings))
        with self.assertRaises(ValueError):policy.execution_cost([['.5','.999']],settings)
        x=policy.execution_cost([['.5','1']],settings)
        self.assertGreaterEqual(D(x['cost']),D(x['scenario_fees']['.01']['all_in']))
    def test_tight_fragmentation_bound_for_random_groupings(self):
        rng=random.Random(19)
        for price in (D('.01'),D('.055'),D('.27'),D('.5'),D('.99')):
            costs=policy.execution_cost([[str(1-price),'1']],{'slippage_reserve_cents':'.5'})
            for _ in range(50):
                left=100;fills=[]
                while left:
                    take=rng.randint(1,left);left-=take;fills.append((price,D(take)/100))
                for precision in ('.0001','.01'):
                    self.assertLessEqual(D(policy.fee_fills(fills,precision)['fee']),D(costs['fragmentation_fee_bounds'][str(D(precision))]))
    def test_bootstrap_seed_reproducibility_and_consecutive_days(self):
        start=policy.epoch(P['holdout_start_utc'])
        days={e.utc_day(start+i*86400):float(i//7) for i in range(56)}
        a=policy.intervals(days,P['uncertainty']);self.assertEqual(a,policy.intervals(days,P['uncertainty']))
        self.assertEqual(set(a),{'1','2','7'});self.assertGreater(a['7'][1]-a['7'][0],a['1'][1]-a['1'][0])
        self.assertTrue(all(v is None for v in policy.intervals(dict(list(days.items())[:55]),P['uncertainty']).values()))
    def test_missing_calendar_not_zeros(self):
        summary=e.summarize([],[],P,'holdout')
        self.assertEqual(summary['missing_checkpoint_contracts'],5376)
        self.assertEqual(len(summary['missing_calendar_days']),56)
        self.assertEqual(summary['strategies']['drift_free']['daily'],{})

class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'synthetic.sqlite3'
        self.s=e.Study(self.path,ROOT/'btc_copilot_protocol.json','synthetic-v2',{'btc_copilot_evidence.py':e.EVALUATOR_SOURCE,'study_policy.py':(ROOT/'study_policy.py').read_text(),'request_pacing.py':(ROOT/'request_pacing.py').read_text(),'fee_reconciliation.py':(ROOT/'fee_reconciliation.py').read_text()})
        self.start=policy.epoch(P['start_utc'])
    def tearDown(self):self.tmp.cleanup()
    def snap(self,t=300):
        now=self.start+t
        s={'ticker':'SYNTHETIC','model_version':'synthetic-v2','epoch':now,'observation_monotonic':now,'open_time':P['start_utc'],'close_time':e.datetime.fromtimestamp(self.start+900,e.timezone.utc).isoformat(),'fee_multiplier':'1','shadow_strategies':{'drift_free':{'eligible':True,'side':'UP','probability':.9}},'time_remaining_seconds':900-t,'operational_vetoes':[],'book':{'yes_bid_dollars':D('.45'),'yes_ask_dollars':D('.46')},'model':{'p_up':.9},'drift_free_model':{'p_up':.9},'structure':{'sigma_dollars_sqrt_second':1,'hour_trend_z':1},'valid_until_epoch':now+20,'retrieval_health':{'requests':{'book':{'started_at_epoch':now-.2,'finished_at_epoch':now,'latency_seconds':.2}}}}
        raw={'book':{'data':{'orderbook_fp':{'yes_dollars':[['.45','100']],'no_dollars':[['.54','100']]}}}}
        return s,raw
    def record(self,s,raw):self.s.record(s,raw,None,{'slippage_reserve_cents':'.5'})
    def test_request_before_deadline_waits_then_first_eligible_consumed(self):
        s,raw=self.snap();self.record(s,raw)
        s,raw=self.snap(315);self.record(s,raw)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT checked FROM signals').fetchone()[0],0)
        s,raw=self.snap(330);s['shadow_strategies']['drift_free']['probability']=.8;self.record(s,raw)
        with self.s.db() as c:
            row=c.execute('SELECT status,evidence,execution_evidence FROM signals').fetchone()
        self.assertEqual(row[0],'hypothetical_fill')
        self.assertEqual(json.loads(zlib.decompress(row[1]))['snapshot']['shadow_strategies']['drift_free']['probability'],.9)
        self.assertEqual(json.loads(zlib.decompress(row[2]))['snapshot']['shadow_strategies']['drift_free']['probability'],.8)
        reopened=e.Study(self.path,ROOT/'btc_copilot_protocol.json','synthetic-v2',{'btc_copilot_evidence.py':e.EVALUATOR_SOURCE,'study_policy.py':(ROOT/'study_policy.py').read_text(),'request_pacing.py':(ROOT/'request_pacing.py').read_text(),'fee_reconciliation.py':(ROOT/'fee_reconciliation.py').read_text()})
        with reopened.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM signals').fetchone()[0],1)
    def test_late_quote_missed_no_best_price_replacement(self):
        s,raw=self.snap();self.record(s,raw)
        s,raw=self.snap(336);self.record(s,raw)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT status FROM signals').fetchone()[0],'missed_observation')
    def test_exact_delay_boundary_and_expiry(self):
        s,raw=self.snap();self.record(s,raw)
        s,raw=self.snap(315);s['retrieval_health']['requests']['book'].update(started_at_epoch=s['epoch'],latency_seconds=0)
        self.record(s,raw)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT status FROM signals').fetchone()[0],'hypothetical_fill')
        s,raw=self.snap(330);s['valid_until_epoch']=s['epoch']
        with self.assertRaises(ValueError):self.record(s,raw)
    def test_duplicate_observation_and_slot_collision(self):
        s,raw=self.snap();self.record(s,raw)
        with self.assertRaises(ValueError):self.record(s,raw)
        s,raw=self.snap(301);s['ticker']='OTHER'
        with self.assertRaises(ValueError):self.record(s,raw)
    def test_settlement_fairness_with_backlog(self):
        with self.s.db() as c:
            for i in range(65):c.execute('INSERT INTO markets(ticker,opened,closed) VALUES (?,?,?)',(str(i),self.start+i*900,self.start+i*900+900))
        calls=[]
        class Client:
            def market(self,t):calls.append(t);return {'data':{'market':{'ticker':t,'status':'active'}}}
        for i in range(2):self.s.settle(Client(),self.start+100000+i*15)
        self.assertIn('64',calls)
    def test_amendment_quarantined_original_preserved(self):
        s,raw=self.snap();self.record(s,raw)
        class Client:
            result='yes'
            def market(self,t):return {'data':{'market':{'ticker':t,'status':'finalized','result':self.result}}}
        c=Client();self.s.settle(c,self.start+1000);c.result='no';self.s.settle(c,self.start+23000)
        with self.s.db() as db:
            self.assertEqual(db.execute('SELECT result,quarantined FROM markets').fetchone(),(1,1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outcome_events').fetchone()[0],2)
    def test_holdout_poison_never_parsed_before_unlock(self):
        with self.s.db() as c:c.execute("INSERT INTO markets(ticker,phase,opened,features,result,checkpoint) VALUES ('H','holdout',?,'POISON',1,?)",(policy.epoch(P['holdout_start_utc']),policy.epoch(P['holdout_start_utc'])+300))
        with patch.object(e.time,'time',return_value=policy.epoch(P['release_utc'])+1):report=e.report(self.path,policy.epoch(P['release_utc'])-1)
        self.assertTrue(report['holdout_locked']);self.assertNotIn('holdout',report['phases'])
    def test_pending_exposure_retained_and_old_database_unchanged(self):
        s,raw=self.snap();self.record(s,raw)
        with self.s.db() as c:
            c.execute("UPDATE signals SET cost='0.8',stress_cost='0.81',status='hypothetical_fill',checked=1")
            markets=[dict(zip([x[0] for x in c.execute('SELECT * FROM markets').description],row)) for row in c.execute('SELECT * FROM markets')]
            c.row_factory=sqlite3.Row;signals=[dict(x) for x in c.execute('SELECT * FROM signals')]
        result=e.summarize(markets,signals,P,'development')['strategies']['drift_free']
        self.assertEqual(result['attempts'],1);self.assertEqual(result['pending_filled_contracts'],1)
        self.assertEqual(result['worst_case_pending_return_dollars'],-.8)
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):e.Study(self.path,ROOT/'btc_copilot_protocol.json','other-version',{})
        self.assertEqual(before,self.path.read_bytes())
    def test_empty_study_cannot_pass_at_release(self):
        with patch.object(e.time,'time',return_value=policy.epoch(P['release_utc'])+1):report=e.report(self.path,policy.epoch(P['release_utc']))
        self.assertFalse(report['eligible_for_independent_review']);self.assertFalse(report['acceptance_checks']['complete_scheduled_checkpoints']);self.assertFalse(report['acceptance_checks']['fee_assumptions_verified']);self.assertIs(report['execution_assumptions']['machine_verified'],False)

class Isolation(unittest.TestCase):
    def test_actual_cli_denied_before_runtime_or_network(self):
        self.assertFalse((ROOT/'activation_approval.json').exists())
        self.assertFalse((ROOT/'runtime').exists())
        result=subprocess.run([sys.executable,str(ROOT/'btc_copilot.py'),'--watch'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('NOT ACTIVATED',result.stderr)
        self.assertFalse((ROOT/'runtime').exists())
    def test_incomplete_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'release_manifest.json').write_text(json.dumps({'study_id':P['study_id'],'files':{}}))
            with self.assertRaises(ValueError):policy.verify_release(root)
    def test_approval_hash_expiry_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();(root/'btc_copilot_protocol.json').write_text(json.dumps(P))
            
            for name in policy.RELEASE_FILES:(root/name).write_bytes((ROOT/name).read_bytes())
            m={'study_id':P['study_id'],'files':{name:policy.digest(root/name) for name in policy.RELEASE_FILES}}
            (root/'release_manifest.json').write_text(json.dumps(m));now=policy.epoch(P['start_utc'])
            a={'approved':True,'study_id':P['study_id'],'bundle_path':str(root),'manifest_sha256':policy.digest(root/'release_manifest.json'),'approved_at_utc':'2026-09-30T12:00:00+00:00','fee_verified_at_utc':P['start_utc'],'fee_rules_verified':True,'rate_verified_at_utc':P['start_utc'],'read_refill_tokens_per_second':200,'read_bucket_capacity':600,'default_read_cost':10,'benchmark_read_cost':50}
            path=root/'activation_approval.json';path.write_text(json.dumps(a));policy.launch_guard(root,now)
            invalid=dict(a,read_refill_tokens_per_second=199);path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):policy.launch_guard(root,now)
            invalid=dict(a,read_bucket_capacity=float('nan'));path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):policy.launch_guard(root,now)
            path.write_text(json.dumps(a))
            with self.assertRaises(ValueError):policy.launch_guard(root,now+86401)
            with self.assertRaises(ValueError):policy.launch_guard(root,now-1)
            (root/'runtime').symlink_to(root)
            with self.assertRaises(ValueError):policy.launch_guard(root,now)
            (root/'runtime').unlink();(root/'btc_copilot_protocol.json').write_text('{}')
            with self.assertRaises(ValueError):policy.launch_guard(root,now)
    def test_reconciliation_cannot_read_when_guard_rejects(self):
        import btc_copilot as b
        class Client:
            def market(self,t):raise AssertionError('External read should never occur')
        class Study:
            def settle(self,c,now):
                with self_test.assertRaises(ValueError):c.market('SYNTHETIC')
        self_test=self
        with patch.object(b,'launch_guard',side_effect=ValueError('not approved')):b.reconcile_background(Study(),Client())
    def test_get_only_transport_and_no_activate_command(self):
        text=(ROOT/'kalshi_readonly.py').read_text();self.assertIn('method="GET"',text)
        self.assertNotIn('method="POST"',text)
        self.assertNotIn('write_text',policy.launch_guard.__code__.co_names)

if __name__=='__main__':unittest.main()
