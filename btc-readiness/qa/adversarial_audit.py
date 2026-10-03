"""Offline audit. Only copied sources, public fixtures and synthetic state are used.

Run: python3 -B adversarial_audit.py original|study002
Safety assertions intentionally fail when an unfixed defect is reproduced.
"""
import copy, io, json, math, os, socket, subprocess, sys, tempfile, unittest, zlib
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
TARGET=sys.argv.pop(1)
ROOT=HERE/'original/outputs' if TARGET=='original' else HERE/'outputs/copilot_study002_fee_review'
sys.path.insert(0,str(ROOT))
import btc_copilot as b
import btc_copilot_research as r
from kalshi_readonly import ReadError, KalshiReadOnly
RAW=json.loads((HERE/'original/work/btc_copilot_test_fixture.json').read_text())
NOW=RAW['benchmark']['data']['data']['payload'][-1]['time']/1000+1.5
SETTINGS=r.load_settings(ROOT/'btc_copilot_settings.json')
SETTINGS['fee_verified_at']=b.stamp(NOW)['utc']
RESULTS=[]

def make(raw,previous=None,record=None):
    class Fake:
        def pages(self,path,params=None,max_pages=3):
            return {'pages':[{'data':{'markets':[raw['market']['data']['market']]}}],'complete':True}
        def market(self,t):return raw['market']
        def event(self,t):return raw['event']
        def orderbook(self,t):return raw['book']
        def get(self,path,params=None):
            if path.endswith('/candlesticks'):return raw['candles']
            if path.startswith('/series/'):return raw['series']
            return raw[{'/cfbenchmarks/values':'benchmark','/markets/trades':'trades','/exchange/status':'exchange'}[path]]
    c=object.__new__(b.Copilot);c.client=Fake();c.positions=False;c.raw={}
    c.state={'previous':copy.deepcopy(previous),'contracts':{}};c.record=record or (lambda s:None)
    return c

def collect(raw,previous=None,st=None,model=None,now=NOW,record=None):
    c=make(raw,previous,record)
    health={'requests':{k:{'started_at_epoch':now-.2,'finished_at_epoch':now,'latency_seconds':.2}
                        for k in ('market','event','series','book','benchmark','trades','candles','spot','exchange')}}
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(b.time,'time',return_value=now))
        stack.enter_context(patch.object(b,'spot_get',return_value=raw['spot']))
        stack.enter_context(patch.object(b,'load_settings',return_value=copy.deepcopy(SETTINGS)))
        if st is not None:stack.enter_context(patch.object(b,'structure',return_value=st))
        if model is not None:stack.enter_context(patch.object(b,'probability',return_value=model))
        return c.collect(replay_health=health)

def eligible(side='UP',previous=True):
    raw=copy.deepcopy(RAW);raw['trades']['data']={'trades':[]}
    raw['book']['data']={'orderbook_fp':{'yes_dollars':[['.40','100']],'no_dollars':[['.59','100']]}} if side=='UP' else {'orderbook_fp':{'yes_dollars':[['.59','100']],'no_dollars':[['.40','100']]}}
    st=b.structure(b.ticks_from_response(raw['benchmark']))
    st.update(bias=side,setup_side=side,setup='controlled eligibility fixture',sigma_dollars_sqrt_second=.1)
    model={'p_up':.9 if side=='UP' else .1,'p_down':.1 if side=='UP' else .9,
           'up_sensitivity_low':.8 if side=='UP' else .01,'up_sensitivity_high':.99 if side=='UP' else .2,'calibrated':False}
    prev={'ticker':raw['market']['data']['market']['ticker'],'epoch':NOW-15,'btc':float(b.ticks_from_response(raw['benchmark'])[-1][1]),
          'up_ask':.41,'down_ask':.41,'p_up':model['p_up'],'setup':'controlled eligibility fixture','setup_side':side,'imbalance':0}
    return raw,st,model,prev if previous else None

class SafetyTests(unittest.TestCase):
    def rejects_or_marks_fault(self,raw):
        try:s=collect(raw)
        except (ReadError,ValueError,KeyError,TypeError,ArithmeticError):return
        self.assertTrue(s['data_quality_faults'],'Input accepted without a data-quality fault')

    def test_public_shadow_gate_long_and_short(self):
        for side in ('UP','DOWN'):
            raw,st,model,prev=eligible(side);s=collect(raw,prev,st,model)
            self.assertEqual(s['shadow_decision'],side)
            self.assertEqual(s['decision'],'NO TRADE');self.assertIsNone(s['entry'])
            self.assertFalse(s['position']['guidance_valid'])
            self.assertTrue(all(not x['entry_eligible'] for x in s['quantity_previews']))

    def test_stale_data_every_shadow_arm_abstains(self):
        raw=copy.deepcopy(RAW);raw['benchmark']['data']['data']['payload']=raw['benchmark']['data']['data']['payload'][:-30]
        try:s=collect(raw)
        except ReadError:return # Study002 rejects expired observations before persistence.
        self.assertTrue(s['data_quality_faults']);self.assertTrue(all(not x['eligible'] for x in s['shadow_strategies'].values()))

    def test_missing_data_rejected(self):
        raw=copy.deepcopy(RAW);raw['benchmark']['data']['data']['payload']=[]
        with self.assertRaises(ReadError):collect(raw)

    def test_duplicate_ticks_rejected(self):
        raw=copy.deepcopy(RAW);rows=raw['benchmark']['data']['data']['payload'];rows.append(rows[-1])
        with self.assertRaises(ReadError):collect(raw)

    def test_unordered_ticks_normalized(self):
        raw=copy.deepcopy(RAW);raw['benchmark']['data']['data']['payload'].reverse()
        self.assertEqual(b.ticks_from_response(raw['benchmark']),b.ticks_from_response(RAW['benchmark']))

    def test_forming_minute_not_closed(self):
        ticks=[(6000+i,D(i+100)) for i in range(91)]
        bars=b.minute_bars(ticks);self.assertEqual([x['time'] for x in bars],[6000])
        self.assertEqual(bars[0]['close'],D(159))

    def test_future_tick_1_second_rejected(self):
        raw=copy.deepcopy(RAW);rows=raw['benchmark']['data']['data']['payload']
        # Add continuous ticks through the decision boundary, avoiding a gap veto masking this check.
        tail=rows[-1];ts=tail['time']+1000
        while ts<=int((NOW+1)*1000):rows.append(dict(tail,time=ts));ts+=1000
        self.rejects_or_marks_fault(raw)

    def test_hour_coverage_must_be_proven(self):
        raw=copy.deepcopy(RAW);raw['benchmark']['data']['data']['payload']=raw['benchmark']['data']['data']['payload'][-3100:]
        self.rejects_or_marks_fault(raw)

    def test_subsecond_sampling_must_be_rejected_or_normalized(self):
        raw=copy.deepcopy(RAW);rows=raw['benchmark']['data']['data']['payload']
        raw['benchmark']['data']['data']['payload']=sorted(rows+[dict(x,time=x['time']+200) for x in rows],key=lambda x:x['time'])
        self.rejects_or_marks_fault(raw)

    def test_upstream_error_with_payload_must_be_rejected(self):
        raw=copy.deepcopy(RAW);raw['benchmark']['data']['data']['error']='upstream degraded'
        self.rejects_or_marks_fault(raw)

    def test_future_candle_rejected_or_quarantined(self):
        raw=copy.deepcopy(RAW);raw['candles']['data']['candlesticks']=[{'end_period_ts':int(NOW)+600,'price':{'open':'100','high':'90','low':'110','close':'NaN'},'volume_fp':'-1'}]
        self.rejects_or_marks_fault(raw)

    def test_future_trade_rejected(self):
        raw=copy.deepcopy(RAW);raw['trades']['data']={'trades':[{'created_time':b.stamp(NOW+60)['utc'],'count_fp':'100','taker_outcome_side':'yes'}]}
        self.rejects_or_marks_fault(raw)

    def test_negative_trade_quantity_rejected(self):
        raw=copy.deepcopy(RAW);raw['trades']['data']={'trades':[{'created_time':b.stamp(NOW-1)['utc'],'count_fp':'-100','taker_outcome_side':'yes'}]}
        self.rejects_or_marks_fault(raw)

    def test_duplicate_book_levels_rejected_or_deduplicated(self):
        raw=copy.deepcopy(RAW);raw['book']['data']={'orderbook_fp':{'yes_dollars':[['.40','.6'],['.40','.6']],'no_dollars':[['.59','.6'],['.59','.6']]}}
        s=collect(raw)
        self.assertTrue(s['data_quality_faults'] or s['book']['yes_ask_size_fp']==D('.6'),'Duplicate rows manufactured 1.2 contracts from .6')

    def test_bad_previous_snapshot_cannot_confirm(self):
        raw,st,model,prev=eligible();prev.update(data_quality_faults=['stale'],guidance_valid=False)
        s=collect(raw,prev,st,model);self.assertEqual(s['shadow_decision'],'NO TRADE')

    def test_first_observation_abstains_full_strategy(self):
        raw,st,model,_=eligible();s=collect(raw,None,st,model)
        self.assertEqual(s['shadow_decision'],'NO TRADE');self.assertFalse(s['shadow_strategies']['current_full']['eligible'])

    def test_identical_input_deterministic_5_runs(self):
        raw,st,model,prev=eligible('DOWN')
        outputs=[collect(raw,prev,st,model) for _ in range(5)]
        self.assertTrue(all((x['decision'],x['shadow_decision'],x['model'],x['entry_blockers'])==(outputs[0]['decision'],outputs[0]['shadow_decision'],outputs[0]['model'],outputs[0]['entry_blockers']) for x in outputs))

    def test_prior_position_does_not_change_market_model(self):
        a=collect(copy.deepcopy(RAW));prev={'ticker':a['ticker'],'epoch':NOW-15,'btc':float(a['btc']),'p_up':a['model']['p_up'],'setup':a['structure']['setup'],'setup_side':'DOWN','position_side':'DOWN','imbalance':0}
        c=collect(copy.deepcopy(RAW),prev);self.assertEqual(a['model'],c['model']);self.assertEqual(a['structure'],c['structure'])

    def test_price_conflict_abstains(self):
        raw=copy.deepcopy(RAW);raw['spot']['data']['price']=str(D(raw['spot']['data']['price'])*2)
        s=collect(raw);self.assertTrue(s['data_quality_faults']);self.assertTrue(all(not x['eligible'] for x in s['shadow_strategies'].values()))

    def test_invalid_book_rejected(self):
        for p,q in [('1.01','1'),('.5','-1'),('.5','NaN'),('Infinity','1')]:
            raw=copy.deepcopy(RAW);raw['book']['data']['orderbook_fp']['yes_dollars']=[[p,q]]
            with self.assertRaises((ReadError,ArithmeticError)):collect(raw)

    def test_empty_book_public_abstains(self):
        raw=copy.deepcopy(RAW);raw['book']['data']={'orderbook_fp':{'yes_dollars':[],'no_dollars':[]}}
        s=collect(raw);self.assertEqual(s['decision'],'NO TRADE');self.assertTrue(all(not x['eligible'] for x in s['shadow_strategies'].values()))

    def test_walk_zero_negative_null_quantities_fail_closed(self):
        for q in ('0','-1',None):
            with self.subTest(q=q),self.assertRaises((ValueError,ArithmeticError,TypeError)):
                r.walk([['.5','10']],q)

    def test_fee_multiplier_negative_rejected(self):
        with self.assertRaises((ValueError,ArithmeticError)):r.walk([['.5','1']],'1',True,-1)

    def test_notional_and_fee_independent(self):
        x=r.walk([['.6','2'],['.5','4']],'3',True,1)
        self.assertEqual(D(x['notional']),2*D('.4')+D('.5'))
        self.assertEqual(D(x['conservative_fee']),D('.06'))
        self.assertEqual(D(x['average_price']),D('1.3')/3)

    def test_profit_target_recomputes_exit_fee_at_exit_price(self):
        # Reproduce the exact target expression used inside collect, then audit its net return.
        market={'price_ranges':[{'start':'0','end':'1','step':'.01'}]}
        entry=D('.15');entry_fee=b.taker_fee(entry,1);reserve=D('.03')
        target=next(g for g in b.grid_prices(market) if g>=entry+entry_fee+b.taker_fee(entry,1)+reserve)
        net=target-entry-entry_fee-b.taker_fee(target,1)
        self.assertGreaterEqual(net,reserve,'20c target nets 2c rather than planned 3c after exit fee')

    def test_settings_safety_floors(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'settings.json'
            for key,value in [('preview_quantities',['0']),('preview_quantities',['-1']),('model_uncertainty_reserve_pp',float('nan')),('final_entry_cutoff_seconds',0)]:
                p.write_text(json.dumps(dict(SETTINGS,**{key:value})))
                with self.assertRaises(ValueError):r.load_settings(p)

    def test_grid_invalid_infinity_bounded(self):
        code="import sys;sys.path.insert(0,"+repr(str(ROOT))+");import btc_copilot as b;b.grid_prices({'price_ranges':[{'start':'-Infinity','end':'1','step':'.01'}]})"
        try:run=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,timeout=.7)
        except subprocess.TimeoutExpired:self.fail('grid_prices hung on nonfinite input')
        self.assertNotEqual(run.returncode,0)

    def test_missing_credentials_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            c=KalshiReadOnly(Path(td)/'missing')
            with self.assertRaisesRegex(ReadError,'configuration is missing'):c._headers('/trade-api/v2/cfbenchmarks/values')

    def test_execution_routes_denied_before_transport(self):
        c=KalshiReadOnly()
        for route in ('/portfolio/orders/batched','/portfolio/transfers','/account/leverage','https://example.com','/markets/T/../orders','/markets/%2e%2e'):
            with self.assertRaises(ReadError),patch.object(c.opener,'open',side_effect=AssertionError('network attempted')):c.get(route)

    def test_collect_network_faults_no_record(self):
        for fault in (TimeoutError('timeout'),ValueError('malformed JSON'),ReadError('HTTP 429',429),ReadError('HTTP 401',401)):
            raw=copy.deepcopy(RAW);c=make(raw,record=lambda s: self.fail('record on failed collection'))
            c.client.get=lambda *a:(_ for _ in ()).throw(fault)
            with patch.object(b,'load_settings',return_value=SETTINGS),patch.object(b.time,'time',return_value=NOW),patch.object(b,'spot_get',return_value=raw['spot']):
                if TARGET=='study002':
                    health={'requests':{}}
                    with self.assertRaises(ReadError):c.collect(replay_health=health)
                else:
                    with self.assertRaises(ReadError):c.collect()

    def test_corrupt_state_constructor_explicit_failure(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td,'btc_copilot_state.json').write_text('{')
            with patch.object(b,'WORK',Path(td)),patch.object(b,'launch_guard',create=True,return_value=None):
                with self.assertRaises(json.JSONDecodeError):b.Copilot()

    def test_replay_detects_tampered_shadow_decision(self):
        raw,st,model,prev=eligible();s=collect(raw,prev,st,model)
        # Real probability input for faithful replay; only the saved shadow outcome is altered.
        s=collect(copy.deepcopy(RAW));s['shadow_decision']='FORGED'
        settings=copy.deepcopy(SETTINGS)
        with tempfile.TemporaryDirectory() as td:
            a=r.Audit(Path(td)/'audit.db');a.record(s,RAW,None,settings,Path(td)/'journal')
            result=r.replay(a.path)
            self.assertFalse(result['matches'],'Replay accepted a forged shadow decision')

    def test_duplicate_trade_not_double_counted(self):
        raw=copy.deepcopy(RAW)
        row={'trade_id':'SYNTHETIC-DUP','created_time':b.stamp(NOW-1)['utc'],'count_fp':'10','taker_outcome_side':'yes'}
        raw['trades']['data']={'trades':[row,copy.deepcopy(row)]}
        s=collect(raw);self.assertTrue(s['data_quality_faults'] or s['recent_trades']['volume_fp']==D(10))

    def test_scale_in_inventory_math_both_directions(self):
        for side in ('yes','no'):
            fills=[{'fill_id':str(i),'ticker':'T','outcome_side':side,'count_fp':str(q),
                    'yes_price_dollars':str(p if side=='yes' else 1-p),'no_price_dollars':str(p if side=='no' else 1-p),
                    'created_time':b.stamp(1000+i)['utc']} for i,(q,p) in enumerate([(D(10),D('.4')),(D(5),D('.6'))])]
            ledger=b.fill_ledger(fills,'T');self.assertEqual(abs(ledger['quantity']),D(15))
            self.assertEqual(ledger['average_entry_price'],D(7)/15)

    def test_timezone_dst_conversion(self):
        for ts,offset in [('2026-03-08T09:59:00Z','-08:00'),('2026-03-08T10:01:00Z','-07:00'),('2026-11-01T09:01:00Z','-08:00')]:
            value=b.stamp(b.dt(ts).timestamp());self.assertTrue(value['pacific'].endswith(offset))
            self.assertEqual(b.dt(value['utc']).timestamp(),b.dt(value['pacific']).timestamp())

    def test_server_allowlist_no_files_served_to_bad_host_or_private_path(self):
        class Handler:
            server=type('Server',(),{'server_port':8766})()
            def send_error(self,status):self.status=status
        for host,origin,path,status in [('evil.test:8766',None,'/',403),('localhost:8766','https://evil.test','/',403),
                                         ('localhost:8766',None,'/../kalshi_readonly_config.json',404),('localhost:8766',None,'/runtime/btc_copilot_study.sqlite3',404)]:
            h=Handler();h.headers={'Host':host};h.path=path
            if origin:h.headers['Origin']=origin
            b.View.do_GET(h);self.assertEqual(h.status,status)

    def test_transport_network_timeout_no_fabricated_response(self):
        c=KalshiReadOnly()
        with patch.object(c.opener,'open',side_effect=TimeoutError('secret error body')),patch.object(b,'launch_guard',create=True,return_value=None):
            if TARGET=='study002':
                import request_pacing
                import study_policy
                from contextlib import ExitStack
                context=ExitStack();context.enter_context(patch.object(request_pacing,'before_read',return_value=None));context.enter_context(patch.object(study_policy,'launch_guard',return_value=None))
            else:context=patch.object(c,'_headers',return_value={})
            with context,self.assertRaises(ReadError) as ex:c.get('/exchange/status')
            self.assertNotIn('secret',str(ex.exception))

    def test_state_files_owner_only_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'state.json';b.atomic(path,{'revision':1});b.atomic(path,{'revision':2})
            self.assertEqual(path.stat().st_mode & 0o777,0o600);self.assertEqual(json.loads(path.read_text()),{'revision':2})
            self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_atomic_temporary_symlink_does_not_truncate_other_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);sentinel=root/'unrelated';sentinel.write_text('PRESERVE')
            path=root/'state.json';path.with_suffix('.json.tmp').symlink_to(sentinel)
            try:b.atomic(path,{'revision':1})
            except OSError:pass
            self.assertEqual(sentinel.read_text(),'PRESERVE')

    @unittest.skipIf(TARGET=='study002','Study002 removes original settlement journal writes')
    def test_journal_crash_retry_idempotent(self):
        s=collect(copy.deepcopy(RAW));state={'previous':None,'contracts':{'OLD':{'ticker':'OLD','initial_bias':'UP','first_observed_at':s['timestamp'],'close_time':b.stamp(NOW-100)['utc'],
                'strike':'100','entry_signal':None,'observations':1,'journaled':False}}}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'state.json';path.write_text(json.dumps(state))
            class Client:
                def market(self,t):return {'data':{'market':{'ticker':t,'status':'finalized','result':'yes'}}}
            for _ in range(2):
                c=object.__new__(b.Copilot);c.state=json.loads(path.read_text());c.state_path=path;c.positions=False;c.client=Client()
                with patch.object(b,'ROOT',root),patch.object(b,'WORK',root),patch.object(b,'atomic',side_effect=OSError('synthetic crash after append')):
                    with self.assertRaises(OSError):b.Copilot.record(c,s)
            lines=(root/'btc_copilot_journal.jsonl').read_text().splitlines()
            self.assertEqual(len(lines),1,'Crash between journal append and state commit duplicated finalization')

def golden():
    # Expectations are declared before generation/execution; every public output must abstain.
    specs=[('obvious_LONG','UP','pullback'),('obvious_SHORT','DOWN','bounce'),('clean_breakout','UP','breakout'),
           ('failed_breakout','NEUTRAL','fake'),('successful_retest','UP','retest'),('failed_reclaim','DOWN','reclaim'),
           ('strong_downtrend','DOWN','trend'),('strong_uptrend','UP','trend'),('sideways_chop','NEUTRAL','chop'),
           ('conflicting_timeframes','DOWN','conflict'),('stale_data',None,'stale'),('missing_data',None,'missing'),
           ('extreme_volatility',None,'extreme'),('almost_qualifies',None,'almost')]
    end=int(NOW-1.5);out=[]
    for name,bias,kind in specs:
        raw=copy.deepcopy(RAW);series=[]
        for i in range(3600):
            x=i/3599
            sign=-1 if bias=='DOWN' else 1
            value=60000+sign*150*x+6*math.sin(i/70)
            if kind in ('chop','fake','almost'):value=60000+3*math.sin(i/30)
            if kind in ('pullback','retest'):value-=25*math.exp(-((i-3450)/60)**2)
            if kind in ('bounce','reclaim'):value+=25*math.exp(-((i-3450)/60)**2)
            if kind=='breakout':value+=50/(1+math.exp(-(i-3460)/10))
            if kind=='fake':value+=80*math.exp(-((i-3450)/25)**2)
            if kind=='conflict':value+=70*max(0,(i-3450)/150)
            if kind=='extreme':value+=1000*math.sin(i/2)
            series.append({'time':(end-3599+i)*1000,'value':str(value)})
        if kind=='stale':series=series[:-30]
        if kind=='missing':series=[]
        raw['benchmark']['data']['data']['payload']=series
        if series:
            raw['spot']['data']['price']=series[-1]['value'];raw['spot']['data']['time']=b.stamp(NOW-1)['utc']
            ticks=b.ticks_from_response(raw['benchmark']);opened=b.dt(raw['market']['data']['market']['open_time']).timestamp()
            ps=b.window_samples(ticks,opened,False)
            if len(ps)==60:raw['market']['data']['market']['floor_strike']=str((sum(ps)/60).quantize(D('.01')))
        expected={'public_decision':'NO TRADE','bias':bias,'all_shadow_ineligible':kind in ('stale','missing','chop','almost')}
        try:
            s=collect(raw);actual={'public_decision':s['decision'],'bias':s['initial_bias'],'setup_side':s['structure']['setup_side'],
                                  'shadow_decision':s['shadow_decision'],'current_full_eligible':s['shadow_strategies']['current_full']['eligible'],
                                  'any_shadow_eligible':any(x['eligible'] for x in s['shadow_strategies'].values()),'data_faults':s['data_quality_faults']}
            passed=s['decision']=='NO TRADE' and (bias is None or bias==s['initial_bias']) and (not expected['all_shadow_ineligible'] or not actual['any_shadow_eligible'])
            # Replay a second real detector observation; do not let first-run abstention mask signal logic.
            if kind not in ('stale','missing'):
                later=copy.deepcopy(raw);new_now=NOW+15;tail=later['benchmark']['data']['data']['payload'][-1]
                sign=-1 if bias=='DOWN' else 1
                for j in range(1,16):
                    later['benchmark']['data']['data']['payload'].append({'time':tail['time']+1000*j,'value':str(D(tail['value'])+D(str(sign*.04*j)))})
                later['spot']['data']['price']=later['benchmark']['data']['data']['payload'][-1]['value']
                later['spot']['data']['time']=b.stamp(new_now-1)['utc']
                for k,v in later.items():
                    if isinstance(v,dict) and 'request_started_at' in v:v['request_started_at']=b.stamp(new_now-.2)['utc']
                later['trades']['data']={'trades':[]}
                if bias in ('UP','DOWN'):
                    later['book']['data']={'orderbook_fp':{'yes_dollars':[['.40' if bias=='UP' else '.59','100']],'no_dollars':[['.59' if bias=='UP' else '.40','100']]}}
                prev={'ticker':s['ticker'],'epoch':NOW,'btc':float(s['btc']),'up_ask':float(s['book']['yes_ask_dollars']) if s['book']['yes_ask_dollars'] is not None else None,
                      'p_up':s['model']['p_up'],'setup':s['structure']['setup'],'setup_side':s['structure']['setup_side'],'imbalance':s['book_imbalance_5c']}
                follow=collect(later,prev,now=new_now)
                actual['second_observation']={'public_decision':follow['decision'],'shadow_decision':follow['shadow_decision'],'setup_side':follow['structure']['setup_side'],
                                              'full_eligible':follow['shadow_strategies']['current_full']['eligible'],'bias':follow['initial_bias'],'full_reasons':follow['shadow_strategies']['current_full']['reasons']}
                passed=passed and follow['decision']=='NO TRADE'
        except Exception as ex:
            actual={'rejected':type(ex).__name__};passed=kind in ('stale','missing')
        out.append({'scenario':name,'expected_before_run':expected,'actual':actual,'passes_expectation':passed,'llm_used':False})
    (HERE/(TARGET+'_golden.json')).write_text(json.dumps(out,indent=2))

if __name__=='__main__':
    # No accidental network is possible even if a test inadvertently reaches a real client.
    with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):
        golden()
        stream=io.StringIO();suite=unittest.defaultTestLoader.loadTestsFromTestCase(SafetyTests)
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    (HERE/(TARGET+'_adversarial.txt')).write_text(stream.getvalue())
    failed_ids={getattr(t,'test_case',t).id() for t,_ in result.failures+result.errors}
    summary={'target':TARGET,'run':result.testsRun,'passed':result.testsRun-len(failed_ids)-len(result.skipped),'failed_test_methods':len(failed_ids),'skipped':len(result.skipped),
             'failures':[{'test':str(t),'detail':detail} for t,detail in result.failures],
             'errors':[{'test':str(t),'detail':detail} for t,detail in result.errors]}
    (HERE/(TARGET+'_adversarial.json')).write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2));sys.exit(not result.wasSuccessful())
