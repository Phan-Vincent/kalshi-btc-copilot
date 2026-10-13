"""Revision-2 regressions; synthetic public data and local SQLite only."""
import copy, json, math, random, sqlite3, tempfile, unittest, zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import runtime_fixture as f
import btc_copilot as b
import btc_copilot_evidence as ev
import acer_ops
import evidence_runner as runner
import readiness_policy as policy
import release_tool
from coherent_runtime import EvidenceStore, scrub_detail
from request_pacing import Pacer

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=json.loads((ROOT/'candidate/readiness_protocol.json').read_text())

def trade(index,epoch,ticker='KXBTC15M-SYNTHETIC'):
    return {'trade_id':'trade-%06d'%index,'ticker':ticker,
            'created_time':datetime.fromtimestamp(epoch,timezone.utc).isoformat(),
            'count_fp':'1.00','yes_price_dollars':'0.50','no_price_dollars':'0.50'}

class TradesTests(unittest.TestCase):
    class SlicedClient:
        """Kalshi-like fake: min_ts <= created <= max_ts, newest first, integer-offset cursors."""
        def __init__(self,rows,end,cursor_always=False):
            import threading
            self.rows=rows;self.end=end;self.cursor_always=cursor_always;self.params=[];self.lock=threading.Lock()
        def get(self,path,params):
            with self.lock:self.params.append(dict(params))
            inside=sorted((r for r in self.rows if params['min_ts']<=b.dt(r['created_time']).timestamp()<=params['max_ts']),
                          key=lambda r:b.dt(r['created_time']).timestamp(),reverse=True)
            start=int(params.get('cursor') or 0);page=inside[start:start+1000]
            more=self.cursor_always or start+1000<len(inside)
            stamp=datetime.fromtimestamp(self.end,timezone.utc).isoformat()
            return {'data':{'trades':page,'cursor':str(start+1000) if more else ''},
                    'request_started_at':stamp,'retrieved_at':stamp}

    def test_1500_trades_follow_cursor_and_filter_request_edges(self):
        end=2000.5;ticker='KXBTC15M-SYNTHETIC'
        rows=[trade(i,end-100+i/10000,ticker) for i in range(1500)]  # all inside one 30 s slice: needs its cursor
        rows.extend([trade(1500,end-180.01,ticker),trade(1501,end+.01,ticker)])
        client=self.SlicedClient(rows,end);window=b.trade_window(client,ticker,end)
        valid=b.validated_trades(window['data']['trades'],ticker,end-180,end)
        self.assertEqual(len(valid),1500)
        firsts=[p for p in client.params if 'cursor' not in p]
        self.assertEqual(len(firsts),b.TRADE_SLICES)
        self.assertEqual(min(p['min_ts'] for p in firsts),math.floor(end-180))
        self.assertEqual(max(p['max_ts'] for p in firsts),math.ceil(end))
        self.assertEqual([p['cursor'] for p in client.params if 'cursor' in p],['1000'])
        self.assertFalse(window['data']['cursor'])
        self.assertEqual(window['pages'],b.TRADE_SLICES+1)

    def test_slices_cover_window_and_merge_shared_boundary_trades(self):
        end=2000.5;ticker='KXBTC15M-SYNTHETIC';lo=math.floor(end-180);hi=math.ceil(end)
        edges=[lo+(hi-lo)*i//b.TRADE_SLICES for i in range(b.TRADE_SLICES+1)]
        rows=[trade(i,edge,ticker) for i,edge in enumerate(edges)]           # exactly on every slice boundary
        rows+=[trade(100+i,end-179+i*29.7,ticker) for i in range(7)]          # spread through the window
        window=b.trade_window(self.SlicedClient(rows,end),ticker,end)
        ids=[r['trade_id'] for r in window['data']['trades']]
        self.assertEqual(sorted(ids),sorted(r['trade_id'] for r in rows))     # nothing lost, nothing doubled
        stamps=[b.dt(r['created_time']).timestamp() for r in window['data']['trades']]
        self.assertEqual(stamps,sorted(stamps,reverse=True))                  # newest first, like the API
        bad=rows+[dict(rows[1],count_fp='999.00')]                            # same id, different content
        with self.assertRaisesRegex(b.ReadError,'identity'):b.trade_window(self.SlicedClient(bad,end),ticker,end)

    def collect_with(self,late=None,record=None):
        """The runtime fixture's collect, with one source finishing 8 s late and an optional call log."""
        raw=copy.deepcopy(f.RAW);raw['trades']['data']={'trades':[]}
        class Fake:
            def pages(self,*a,**k):return {'pages':[{'data':{'markets':[raw['market']['data']['market']]}}],'complete':True}
            def market(self,*a):record is not None and record.append('market');return raw['market']
            def event(self,*a):record is not None and record.append('event');return raw['event']
            def orderbook(self,*a):record is not None and record.append('book');return raw['book']
            def get(self,path,params=None):
                if record is not None:record.append(path)
                if path.endswith('/candlesticks'):return raw['candles']
                if path.startswith('/series/'):return raw['series']
                return raw[{'/cfbenchmarks/values':'benchmark','/markets/trades':'trades','/exchange/status':'exchange'}[path]]
        c=object.__new__(b.Copilot);c.client=Fake();c.positions=False;c.raw={};c.state={'contracts':{},'previous':None}
        settings=f.r.load_settings(f.ROOT/'btc_copilot_settings.json');settings['fee_verified_at']=b.stamp(f.NOW)['utc']
        health={'requests':{k:{'started_at_epoch':f.NOW-.2,'finished_at_epoch':f.NOW+(8 if k==late else 0),'latency_seconds':.2}
                            for k in ('market','event','series','book','benchmark','trades','candles','spot','exchange')}}
        with patch.object(b.time,'time',return_value=f.NOW),patch.object(b,'spot_get',return_value=raw['spot']),patch.object(b,'load_settings',return_value=settings):
            return c.collect(replay_health=health,persist=False)

    def brti(self,points,last=1800003599):
        return {'data':{'data':{'payload':[{'time':(last-i)*1000,'value':'60000.00'} for i in range(points)][::-1]}}}

    def test_brti_window_one_or_two_seconds_short_is_accepted_but_not_three(self):
        for points,ok in ((3600,True),(3599,True),(3598,True),(3597,False),(2999,False)):
            if ok:
                ticks=b.ticks_from_response(self.brti(points))
                old,coverage=b.nearest_price(ticks,3600)   # the 1 h lookback still resolves
                self.assertGreaterEqual(coverage,3597)
            else:
                with self.assertRaisesRegex(b.ReadError,'Insufficient one-hour BRTI coverage'):b.ticks_from_response(self.brti(points))

    def test_missing_source_reports_its_underlying_cause(self):
        note=b.failure_note(b.ReadError('Kalshi GET failed with HTTP 429'))
        self.assertEqual(note,'ReadError: Kalshi GET failed with HTTP 429')
        self.assertLessEqual(len(b.failure_note(RuntimeError('x'*500))),120)
        source=(f.ROOT/'btc_copilot.py').read_text()
        self.assertIn("'Required data unavailable: '+', '.join(missing)+' ('+errors.get(missing[0],'no detail')+')'",source)

    def test_skew_covers_point_in_time_sources_not_the_anchored_trade_window(self):
        veto='Source snapshot skew exceeds freshness budget'
        self.assertNotIn(veto,self.collect_with(late='trades')['operational_vetoes'])
        self.assertIn(veto,self.collect_with(late='benchmark')['operational_vetoes'])
        self.assertIn(veto,self.collect_with(late='book')['operational_vetoes'])

    def test_trade_pages_are_requested_after_every_snapshot_request(self):
        calls=[];self.collect_with(record=calls)
        trades=[i for i,p in enumerate(calls) if p=='/markets/trades']
        self.assertTrue(trades)
        self.assertGreater(min(trades),max(i for i,p in enumerate(calls) if p!='/markets/trades'))

    def test_window_sliver_is_valid_but_outside_analysis_is_filtered(self):
        end=2000.5;rows=[trade(1,end-179.9),trade(2,end-178),trade(3,end-180),trade(4,end+.01)]
        valid=b.validated_trades(rows,'KXBTC15M-SYNTHETIC',end-180,end)
        self.assertEqual(len(valid),3)
        analysis=end+1.5
        self.assertEqual([x['trade_id'] for x in valid if analysis-180<=b.dt(x['created_time']).timestamp()<=analysis],['trade-000002'])
        bad=copy.deepcopy(rows);bad[0]['trade_id']=bad[1]['trade_id']
        with self.assertRaisesRegex(b.ReadError,'identity'):b.validated_trades(bad,'KXBTC15M-SYNTHETIC',end-180,end)

    def test_30_page_cap_and_budget_under_20_seconds(self):
        now=[0.]
        def sleep(seconds):now[0]+=max(seconds,1e-6)
        pacer=Pacer(clock=lambda:now[0],sleep=sleep)
        # Discovery, normal jobs including the 50-token benchmark, and the 30 trade pages.
        for cost in [10,10,10,10,10,50,10,10,10,10]:pacer.acquire(cost)
        import threading
        class Client:
            calls=0;lock=threading.Lock()
            def get(self,path,params):
                pacer.acquire(10)
                with self.lock:self.calls+=1;n=self.calls
                return {'data':{'trades':[],'cursor':'cursor-%d'%n},
                        'request_started_at':'2026-10-01T00:00:00+00:00',
                        'retrieved_at':'2026-10-01T00:00:00+00:00'}
        client=Client();window=b.trade_window(client,'KXBTC15M-SYNTHETIC',2000.5)
        self.assertEqual(client.calls,30)                      # 6 slices x 5 pages: the total cap holds
        self.assertTrue(window['data']['cursor'])              # still truncated: a veto, never hidden
        self.assertLess(now[0],20)

class PersistenceTests(unittest.TestCase):
    def test_observation_trims_raw_trades_but_checkpoint_keeps_them(self):
        c,s=f.fixture();s=f.later(s,s['time_remaining_seconds']-585)
        rows=[trade(i,s['epoch']-100+i/100) for i in range(1500)]
        c.raw['trades']['data']['trades']=rows
        s['recent_trades'].update(window_trade_count=len(rows),sample_count=len(rows),
                                   first_trade_id=rows[0]['trade_id'],last_trade_id=rows[-1]['trade_id'],
                                   trade_set_sha256=b.hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();now=[s['epoch']]
            with EvidenceStore(root/'runtime',f.protocol_file(root),s['model_version'],{},boot_id='synthetic',wall=lambda:now[0]) as store:
                c.state=store.state();store.publish(c,s)
                with sqlite3.connect(store.path) as db:
                    audit=json.loads(zlib.decompress(db.execute('SELECT payload FROM observations').fetchone()[0]))
                    checkpoint=db.execute('SELECT payload FROM checkpoints').fetchone()
                self.assertNotIn('trades',audit['raw']['trades']['data'])
                self.assertEqual(audit['raw']['trades']['data']['summary']['window_trade_count'],1500)
                self.assertIsNotNone(checkpoint)
                self.assertEqual(len(json.loads(zlib.decompress(checkpoint[0]))['raw']['trades']['data']['trades']),1500)

    def test_signal_and_execution_evidence_use_trade_summary(self):
        c,example=f.fixture();trades=[trade(i,example['epoch']-90+i/10) for i in range(900)]
        summary=dict(example['recent_trades'],window_trade_count=len(trades),sample_count=len(trades),
                     first_trade_id=trades[0]['trade_id'],last_trade_id=trades[-1]['trade_id'],
                     trade_set_sha256=b.hashlib.sha256(ev.canonical(trades).encode()).hexdigest())
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();protocol=f.protocol_file(root)
            p=json.loads(protocol.read_text());opened=ev.timestamp(p['start_utc'])
            study=ev.Study(root/'synthetic.sqlite3',protocol,'synthetic-v2',{})
            raw={'book':{'data':{'orderbook_fp':{'yes_dollars':[['.45','100']],
                                               'no_dollars':[['.54','100']]}}},
                 'trades':{'data':{'trades':trades},'retrieved_at':'synthetic'}}
            for seconds in (300,315,330):
                epoch=opened+seconds
                s={'ticker':'SYNTHETIC','model_version':'synthetic-v2','epoch':epoch,
                   'observation_monotonic':epoch,'open_time':p['start_utc'],
                   'close_time':datetime.fromtimestamp(opened+900,timezone.utc).isoformat(),
                   'fee_multiplier':'1','shadow_strategies':{'drift_free':{'eligible':True,'side':'UP'}},
                   'time_remaining_seconds':900-seconds,'operational_vetoes':[],
                   'book':{'yes_bid_dollars':ev.D('.45'),'yes_ask_dollars':ev.D('.46')},
                   'model':{'p_up':.9},'drift_free_model':{'p_up':.9},
                   'structure':{'sigma_dollars_sqrt_second':1,'hour_trend_z':1},
                   'valid_until_epoch':epoch+20,'recent_trades':summary,
                   'retrieval_health':{'requests':{'book':{'started_at_epoch':epoch-.2,
                                                            'finished_at_epoch':epoch,'latency_seconds':.2}}}}
                study.record(s,raw,None,{'slippage_reserve_cents':'.5'})
            with study.db() as db:
                initial,execution=db.execute('SELECT evidence,execution_evidence FROM signals').fetchone()
                checkpoint=db.execute('SELECT payload FROM checkpoints').fetchone()[0]
                fields=('ticker','phase','day','opened','closed','checkpoint','features','result',
                        'quarantined','confirmed_at')
                market=dict(zip(fields,db.execute('SELECT ticker,phase,day,opened,closed,checkpoint,features,1,'
                                                  'quarantined,confirmed_at FROM markets').fetchone()))
            for blob in (initial,execution):
                payload=json.loads(zlib.decompress(blob))
                self.assertEqual(payload['raw']['trades']['data'],{'summary':json.loads(ev.canonical(summary))},
                                 msg='Signal raw window persisted')
                self.assertNotIn('trades',payload['raw']['trades']['data'])
            self.assertEqual(len(json.loads(zlib.decompress(checkpoint))['raw']['trades']['data']['trades']),len(trades))
            row={'ticker':'SYNTHETIC','strategy':'drift_free','side':'UP','status':'hypothetical_fill',
                 'cost':'.5','stress_cost':'.6','execution_evidence':execution}
            report=ev.summarize([market],[row],p,'development')
            self.assertEqual(report['strategies']['drift_free']['assumed_level_fill_scenarios']['resolved_entries'],1)
            # The fixture protocol has both precisions, so the $0.0001-only fee diagnostics are absent by
            # design (see test_study005r1_release); fee terms never depend on the trade window anyway.
            self.assertNotIn('fee_bound_binding',report['strategies']['drift_free'])
        c.settings['minimum_conservative_net_edge_pp']=-1000
        s=example;s['recent_trades']['direction']='UP'
        up=ev.candidates(s,None,c.settings)['drift_free']['filters']['flow']
        s['recent_trades']['direction']='DOWN'
        down=ev.candidates(s,None,c.settings)['drift_free']['filters']['flow']
        self.assertNotEqual(up,down)

    def test_failure_detail_is_short_scrubbed_and_durable(self):
        detail=scrub_detail(ValueError('token=ABCDEF /private/secret/key https://example.com/path'))
        self.assertNotIn('ABCDEF',detail);self.assertNotIn('/private/',detail);self.assertNotIn('example.com',detail)
        self.assertLessEqual(len(detail),200)
        c,s=f.fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            with EvidenceStore(root/'runtime',f.protocol_file(root),s['model_version'],{},boot_id='synthetic',wall=lambda:s['epoch']) as store:
                store.failure('COLLECTION_FAILED',ValueError('bad synthetic page'))
                store.event('COLLECTOR_STOP',RuntimeError('operator stop'))
                with sqlite3.connect(store.path) as db:
                    rows=db.execute('SELECT kind,detail FROM runtime_events').fetchall()
                self.assertEqual(rows,[('COLLECTION_FAILED','ValueError: bad synthetic page'),
                                       ('COLLECTOR_STOP','RuntimeError: operator stop')])

class CoverageTests(unittest.TestCase):
    def test_reboot_gap_explained_in_report_summary_and_status(self):
        start=int(ev.timestamp(PROTOCOL['holdout_start_utc']))
        opened=start+900;resume=opened+400;last=start+315
        detail=json.dumps({'previous_boot_prefix':'boot-aaa','new_boot_prefix':'boot-bbb',
                           'last_observation_epoch':last})
        events=[{'epoch':resume,'kind':'REBOOT_RESUME','detail':detail}]
        missing=ev.missing_slots(PROTOCOL,[{'phase':'holdout','opened':start,'checkpoint':start+315}],[],events)
        target=next(x for x in missing if x['slot_opened_utc']==datetime.fromtimestamp(opened,timezone.utc).isoformat())
        self.assertEqual(target['reasons'],['reboot'])
        enrolled=[{'phase':'holdout','opened':slot,'checkpoint':slot+315,
                   'ticker':str(slot),'result':None}
                  for slot in range(start,int(ev.timestamp(PROTOCOL['end_utc'])),900) if slot!=opened]
        one_missing=ev.missing_slots(PROTOCOL,enrolled,[],events)
        summary={'forecast_models':{name:{'brier':None,'calibration_bins':{}}
                                    for name in ('p_drift','p_zero','p_market')},
                 'strategies':{},'zero_minus_market_brier_interval':None}
        budget=ev.impute_holdout(summary,enrolled,[],PROTOCOL,one_missing)
        self.assertEqual(budget['missing_count'],1)
        self.assertTrue(budget['all_missing_slots_have_reasons'])
        self.assertTrue(budget['within_two_percent_budget'])
        with tempfile.TemporaryDirectory() as td:
            candidate=Path(td).resolve()/'candidate';candidate.mkdir()
            runtime=candidate/'runtime_shakedown';runtime.mkdir()
            db=runtime/'evidence.sqlite3'
            (candidate/'operating_limits.json').write_text(json.dumps({'quota_bytes':128*1024**3}))
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT); '
                                'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                'CREATE TABLE slot_events(opened REAL,reason TEXT); '
                                'CREATE TABLE generations(epoch REAL); '
                                'CREATE TABLE runtime_control(degraded INTEGER,reason TEXT);')
                c.execute('INSERT INTO runtime_control VALUES (0,NULL)')
                c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',[(start-60,'COLLECTOR_START','start'),
                    (resume,'REBOOT_RESUME',detail),(start+3*900+10,'COLLECTOR_STOP','orderly stop')])
                c.executemany('INSERT INTO markets VALUES (?,?)',[(start,start+315),(start+2*900,start+2*900+315)])
                c.executemany('INSERT INTO generations VALUES (?)',[(start+315,),(start+2*900+315,)])
            now=start+3*900+20
            with patch.object(release_tool,'CANDIDATE',candidate):
                summary=release_tool.shakedown_summary(runtime,datetime.fromtimestamp(now,timezone.utc))
            item=next(x for x in summary['missed_slots'] if x['slot_opened_utc']==datetime.fromtimestamp(opened,timezone.utc).isoformat())
            self.assertEqual(item['reasons'],['reboot'])
            self.assertTrue(summary['all_misses_have_reasons'])
            status=acer_ops.read_runtime(db,now)
            item=next(x for x in status['missed_slots_24h'] if x['slot_opened_utc']==datetime.fromtimestamp(opened,timezone.utc).isoformat())
            self.assertEqual(item['reasons'],['reboot'])
    def test_status_counts_enrolled_checkpoint_not_band_observation(self):
        now=1800000000.;opened=int(now-1800)//900*900
        with tempfile.TemporaryDirectory() as td:
            db=Path(td).resolve()/'evidence.sqlite3'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE generations(epoch REAL); CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                'CREATE TABLE slot_events(opened REAL,reason TEXT); CREATE TABLE runtime_control(degraded INTEGER,reason TEXT); '
                                'CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT);')
                c.execute('INSERT INTO generations VALUES (?)',(opened+315,))
                c.execute('INSERT INTO markets VALUES (?,NULL)',(opened,))
                c.execute('INSERT INTO slot_events VALUES (?,?)',(opened,'Recent trade window incomplete'))
                c.execute('INSERT INTO runtime_control VALUES (0,NULL)')
            out=acer_ops.read_runtime(db,now)
            self.assertEqual(out['checkpoint_slots_covered_24h'],0)
            self.assertIn(('Recent trade window incomplete',1),out['top_veto_reasons_24h'])

    def test_missing_slot_reasons_and_imputation_monotonicity(self):
        p=copy.deepcopy(PROTOCOL);start=ev.timestamp(p['holdout_start_utc'])
        markets=[]
        for day in range(56):
            opened=start+day*86400
            markets.append({'ticker':'synthetic-%d'%day,'phase':'holdout','opened':opened,
                            'checkpoint':opened+315,'result':day%2,'day':ev.utc_day(opened),
                            'features':json.dumps({'p_drift':.6,'p_zero':.55,'p_market':.5,
                                                    'regime':'quiet','volatility_regime':'low','utc_session':'00-08'}),
                            'closed':opened+900,'quarantined':0,'confirmed_at':ev.timestamp(p['release_utc'])})
        signals=[{'ticker':markets[0]['ticker'],'strategy':'drift_free','side':'UP',
                  'status':'hypothetical_fill','cost':'.59','stress_cost':'.60','execution_evidence':None}]
        h=ev.summarize(markets,signals,p,'holdout')
        rng=random.Random(17)
        for n in (0,1,4,20,100):
            missing=[{'slot_opened_utc':'synthetic','day':markets[rng.randrange(56)]['day'],'reasons':['synthetic failure']} for _ in range(n)]
            result=ev.impute_holdout(h,markets,signals,p,missing)
            self.assertGreaterEqual(result['forecast_models']['p_zero']['with_imputation'],result['forecast_models']['p_zero']['without_imputation'])
            self.assertGreaterEqual(result['calibration_ece']['with_imputation'],result['calibration_ece']['without_imputation'])
            self.assertGreaterEqual(result['paired_zero_minus_market_brier_interval']['with_imputation'][1],
                                    result['paired_zero_minus_market_brier_interval']['without_imputation'][1])
            self.assertLessEqual(result['strategies']['drift_free']['with_imputation']['stress_net_dollars'],
                                 result['strategies']['drift_free']['without_imputation']['stress_net_dollars'])
            self.assertAlmostEqual(result['strategies']['drift_free']['penalty_per_missing_slot'],.6/56)
        missing=ev.missing_slots(p,markets,[],[{'epoch':start+900+315,'kind':'COLLECTION_FAILED','detail':'ValueError: synthetic'}])
        self.assertEqual(len(missing),5376-56)
        self.assertEqual(next(x for x in missing if x['slot_opened_utc']==datetime.fromtimestamp(start+900,timezone.utc).isoformat())['reasons'],['ValueError: synthetic'])

class GateTests(unittest.TestCase):
    def test_shakedown_summary_counts_real_checkpoints_and_hours(self):
        start=datetime(2026,10,20,tzinfo=timezone.utc).timestamp();end=start+72*3600
        with tempfile.TemporaryDirectory() as td:
            candidate=Path(td).resolve()/'candidate';candidate.mkdir()
            runtime=candidate/'runtime_shakedown';runtime.mkdir()
            (candidate/'operating_limits.json').write_text(json.dumps({'quota_bytes':128*1024**3}))
            with sqlite3.connect(runtime/'evidence.sqlite3') as db:
                db.executescript('CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT); '
                                 'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                 'CREATE TABLE slot_events(opened REAL,reason TEXT); '
                                 'CREATE TABLE generations(epoch REAL);')
                db.execute('INSERT INTO runtime_events VALUES (?,?,?)',(start,'COLLECTOR_START','start'))
                db.execute('INSERT INTO runtime_events VALUES (?,?,?)',(end,'COLLECTOR_STOP','orderly stop'))
                db.executemany('INSERT INTO generations VALUES (?)',[(start+h*3600+300,) for h in range(72)])
                db.executemany('INSERT INTO markets VALUES (?,?)',[(start+n*900,start+n*900+315) for n in range(288)])
            with patch.object(release_tool,'CANDIDATE',candidate):
                (runtime/'RUNNING').write_text('synthetic')
                with self.assertRaises(SystemExit):release_tool.shakedown_summary(runtime,datetime.fromtimestamp(end,timezone.utc))
                (runtime/'RUNNING').unlink()
                out=release_tool.shakedown_summary(runtime,datetime.fromtimestamp(end,timezone.utc))
                self.assertEqual((out['slots'],out['enrolled_checkpoints'],out['hours_covered']),(288,288,72))
                self.assertEqual(out['missed_slots'],[])
                growth=out['storage_growth']
                self.assertEqual(growth['projected_72_day_bytes'],int(growth['measured_bytes_per_day']*72*2))
                self.assertEqual(growth['main_database_cap_bytes'],((128*1024**3-1024**2)//(3*4096))*4096)
                self.assertEqual(release_tool.check_shakedown(candidate,datetime.fromtimestamp(end,timezone.utc))['slots'],288)

    def test_shakedown_seal_gate_requires_fresh_complete_summary(self):
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        good={'status':'QA_SHAKEDOWN_NOT_STUDY_EVIDENCE','generated_at_utc':now.isoformat(),
              'hours_covered':72,'slots':288,'enrolled_checkpoints':278,
              'missed_slots':[{'reasons':['recorded veto']}] * 10,
              'poll_failure_rate_by_hour':[{'failure_rate':.01}]*72,
              'unexplained_collection_failures':0,
              'storage_growth':{'measured_bytes_per_day':40*1024**3/144,'uncertainty_multiplier':2,
                                'projected_72_day_bytes':40*1024**3,'quota_bytes':128*1024**3,
                                'sqlite_page_size':4096,
                                'main_database_cap_bytes':((128*1024**3-1024**2)//(3*4096))*4096}}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();path=root/'shakedown_summary.json'
            source=root/'source.py';source.write_text('v1')
            good['candidate_source_sha256']=release_tool.shakedown_source_digest(root)
            path.write_text(json.dumps(good));self.assertEqual(release_tool.check_shakedown(root,now)['slots'],288)
            source.write_text('v2')
            with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)
            source.write_text('v1')
            for key,value in [('hours_covered',71),('enrolled_checkpoints',250)]:
                bad=dict(good);bad[key]=value
                path.write_text(json.dumps(bad))
                with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)
            bad=dict(good);bad['generated_at_utc']=(now-timedelta(hours=73)).isoformat();path.write_text(json.dumps(bad))
            with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)
            cap=good['storage_growth']['main_database_cap_bytes']
            for projection in (cap,cap+1,60*1024**3):
                growth=dict(good['storage_growth'],measured_bytes_per_day=projection/144,
                            projected_72_day_bytes=projection)
                path.write_text(json.dumps(dict(good,storage_growth=growth)))
                if projection==cap:self.assertEqual(release_tool.check_shakedown(root,now)['slots'],288)
                else:
                    with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)
            bad=dict(good['storage_growth'],projected_72_day_bytes=1)
            path.write_text(json.dumps(dict(good,storage_growth=bad)))
            with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)

    def test_second_band_target_is_scheduled(self):
        opened=1800000000//900*900
        self.assertAlmostEqual(runner.next_poll_delay(opened+300,0),5)
        self.assertAlmostEqual(runner.next_poll_delay(opened+300,7),8)
        self.assertAlmostEqual(runner.next_poll_delay(opened+315,0),5)
        self.assertLessEqual(runner.next_poll_delay(opened+320,0),15)

    def test_polls_certain_to_fail_are_skipped_and_nothing_else(self):
        opened=1800000000//900*900
        settings=json.loads((ROOT/'candidate/btc_copilot_settings.json').read_text())
        self.assertEqual(runner.FINAL_CUTOFF_SECONDS,settings['final_entry_cutoff_seconds'])
        # A poll starting with 95 s left may still finish before the cutoff: it is kept.
        self.assertAlmostEqual(runner.next_poll_delay(opened+790,0),15)
        # One that would start with less than 90 s left can never be recorded: wait for the next listing.
        L=runner.LISTING_DELAY_SECONDS
        self.assertAlmostEqual(runner.next_poll_delay(opened+800,0),900+L-800)
        self.assertAlmostEqual(runner.next_poll_delay(opened+880,4),900+L-884)
        # Inside the next contract's listing delay: wait until it is listed, then resume the 15 s cadence.
        self.assertAlmostEqual(runner.next_poll_delay(opened+900,0),L)
        self.assertAlmostEqual(runner.next_poll_delay(opened+900+L,2),13)
        for start in range(0,900*3,7):  # no scheduled start ever lands in a certain-failure zone
            target=opened+start+1.5+runner.next_poll_delay(opened+start,1.5)  # delay counts from the finish
            offset=(target-opened)%900
            self.assertTrue(runner.LISTING_DELAY_SECONDS-1e-9<=offset<=900-runner.FINAL_CUTOFF_SECONDS+1e-9,(start,offset))

    def test_final_cutoff_is_an_expected_event_not_a_failure(self):
        from unittest.mock import Mock
        engine=Mock();engine.collect.side_effect=b.FinalCutoff('Decision completion inside the final entry cutoff')
        store=Mock(halted=False)
        result=runner.Collector(engine,store,lambda *a:None).once()
        self.assertTrue(result['final_cutoff']);store.failure.assert_not_called()
        self.assertEqual(store.event.call_args.args[0],'FINAL_CUTOFF')
        self.assertTrue(issubclass(b.FinalCutoff,b.ReadError))  # every existing ReadError handler still applies
        c,s=f.fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            with EvidenceStore(root/'runtime',f.protocol_file(root),s['model_version'],{},boot_id='synthetic',wall=lambda:s['epoch']) as real:
                def control():
                    db=sqlite3.connect(real.path)
                    try:return db.execute('SELECT degraded,reason FROM runtime_control').fetchone()
                    finally:db.close()
                before=control()
                real.event('FINAL_CUTOFF',b.FinalCutoff('Decision completion inside the final entry cutoff'))
                self.assertEqual(control(),before)  # never sets or changes degradation
                db=sqlite3.connect(real.path)
                try:self.assertEqual(db.execute('SELECT kind FROM runtime_events').fetchall(),[('FINAL_CUTOFF',)])
                finally:db.close()

if __name__=='__main__':unittest.main()
