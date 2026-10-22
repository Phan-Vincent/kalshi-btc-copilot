"""Scheduled exchange-closure regressions; synthetic data, temp dirs, mocked client, no network."""
import copy, importlib.util, json, sqlite3, sys, tempfile, unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parents[1]                      # study005r2_release, the home of release_tool
sys.path.insert(0,str(HERE))
import release_tool
import runtime_fixture as f                                    # inserts candidate/ on sys.path
import btc_copilot as b
import btc_copilot_evidence as ev
import acer_ops
import evidence_runner as run
from coherent_runtime import EvidenceStore

MONITOR=str(Path(__file__).resolve().parents[3]/'acer/shakedown_check/shakedown_check.py')
spec=importlib.util.spec_from_file_location('shakedown_check_under_test',MONITOR)
shakedown_check=importlib.util.module_from_spec(spec);spec.loader.exec_module(shakedown_check)

START=int(datetime(2026,10,20,tzinfo=timezone.utc).timestamp())   # midnight: aligned to both 900 s and 3600 s
CLOSED_DETAIL='Exchange closed: exchange_active=true trading_active=false'
THURSDAY=datetime(2026,10,22,7,30,tzinfo=timezone.utc).timestamp()


def iso(epoch):return datetime.fromtimestamp(epoch,timezone.utc).isoformat()


class ClosedClient:
    def __init__(self,markets,status=None,error=None):
        self.markets=markets;self.status=status;self.error=error;self.status_calls=0
    def pages(self,*a,**k):return {'pages':[{'data':{'markets':self.markets}}],'complete':True}
    def get(self,path,params=None):
        self.status_calls+=1
        if self.error:raise self.error
        return self.status


class CollectTests(unittest.TestCase):
    def collect(self,markets,status=None,error=None):
        c=object.__new__(b.Copilot);c.client=ClosedClient(markets,status,error)
        c.positions=False;c.raw={};c.state={'contracts':{},'previous':None}
        settings={'slippage_reserve_cents':'1','minimum_conservative_net_edge_pp':'0'}
        with patch.object(b,'load_settings',return_value=settings):
            return c.collect(replay_health={'collection_epoch':f.NOW},persist=False)

    def test_zero_eligible_and_trading_closed_is_exchange_closed(self):
        with self.assertRaises(b.ExchangeClosed) as ctx:
            self.collect([],{'data':{'exchange_active':True,'trading_active':False}})
        self.assertIn('trading_active=false',str(ctx.exception))

    def test_zero_eligible_and_both_active_stays_a_read_error(self):
        with self.assertRaises(b.ReadError) as ctx:
            self.collect([],{'data':{'exchange_active':True,'trading_active':True}})
        self.assertNotIsInstance(ctx.exception,b.ExchangeClosed)
        self.assertEqual(str(ctx.exception),'Cannot verify exactly one active BTC 15-minute contract: 0 eligible of 0 listed (exchange active)')

    def test_zero_eligible_and_status_failure_stays_a_read_error(self):
        with self.assertRaises(b.ReadError) as ctx:
            self.collect([],error=b.ReadError('synthetic status outage'))
        self.assertNotIsInstance(ctx.exception,b.ExchangeClosed)
        self.assertEqual(str(ctx.exception),'Cannot verify exactly one active BTC 15-minute contract: 0 eligible of 0 listed (status unreadable)')

    def test_missing_or_non_bool_status_flags_are_not_proven_closed(self):
        for status in ({'data':{'exchange_active':False}},{'data':{'exchange_active':'false','trading_active':False}}):
            with self.assertRaises(b.ReadError) as ctx:self.collect([],status)
            self.assertNotIsInstance(ctx.exception,b.ExchangeClosed)

    def test_two_eligible_contracts_stay_a_read_error_regardless_of_status(self):
        m=copy.deepcopy(f.RAW['market']['data']['market']);m2=copy.deepcopy(m);m2['ticker']='KXBTC15M-SYNTH-2'
        client=ClosedClient([m,m2],{'data':{'exchange_active':False,'trading_active':False}})
        c=object.__new__(b.Copilot);c.client=client;c.positions=False;c.raw={};c.state={'contracts':{},'previous':None}
        settings={'slippage_reserve_cents':'1','minimum_conservative_net_edge_pp':'0'}
        with patch.object(b,'load_settings',return_value=settings):
            with self.assertRaises(b.ReadError) as ctx:
                c.collect(replay_health={'collection_epoch':f.NOW},persist=False)
        self.assertNotIsInstance(ctx.exception,b.ExchangeClosed)
        self.assertEqual(client.status_calls,0)                 # no status probe for a multi-match read error

    def test_discovery_messages(self):
        m=copy.deepcopy(f.RAW['market']['data']['market']);other=copy.deepcopy(m);other['ticker']='KXBTC15M-SYNTH-2'
        prefix='Cannot verify exactly one active BTC 15-minute contract: '
        times=' '+b.dt(m['open_time']).strftime('%H:%M:%S')+'..'+b.dt(m['close_time']).strftime('%H:%M:%S')
        for markets,detail in (([m,m],'duplicate ticker '+m['ticker']),
                               ([m,other],'2 eligible: '+m['ticker']+times+', '+other['ticker']+times),
                               ([dict(m,status='closed')],'0 eligible of 1 listed (exchange active)')):
            with self.subTest(detail=detail),self.assertRaises(b.ReadError) as ctx:
                self.collect(markets,{'data':{'exchange_active':True,'trading_active':True}})
            self.assertNotIsInstance(ctx.exception,b.ExchangeClosed)
            self.assertEqual(str(ctx.exception),prefix+detail)


class WindowTests(unittest.TestCase):
    def test_dst_and_boundaries(self):
        for value,inside in (('2026-10-22T07:30:00',True),('2026-10-22T06:59:00',False),
                             ('2026-10-22T09:00:00',False),('2026-11-05T08:30:00',True),
                             ('2026-11-05T07:30:00',False),('2026-10-21T07:30:00',False)):
            now=datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()
            window=b.scheduled_closure_window(now)
            with self.subTest(value=value):
                self.assertEqual(window is not None,inside)
                if inside:
                    self.assertEqual(window,(now-1800,now+5400))



class CollectorOnceTests(unittest.TestCase):
    def test_exchange_closed_is_an_expected_event_not_a_failure(self):
        c,s=f.fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            with EvidenceStore(root/'runtime',f.protocol_file(root),s['model_version'],{},boot_id='synthetic',wall=lambda:s['epoch']) as real:
                def control():
                    db=sqlite3.connect(real.path)
                    try:return db.execute('SELECT degraded,reason FROM runtime_control').fetchone()
                    finally:db.close()
                before=control()
                engine=SimpleNamespace(state=None,collect=Mock(side_effect=b.ExchangeClosed(CLOSED_DETAIL)))
                result=run.Collector(engine,real,lambda *a:None).once()
                self.assertEqual(result,{'generation':None,'decision':'NO TRADE','advice_enabled':False,'exchange_closed':True})
                self.assertEqual(control(),before)              # never sets or changes degradation
                db=sqlite3.connect(real.path)
                try:rows=db.execute('SELECT kind,detail FROM runtime_events').fetchall()
                finally:db.close()
                self.assertEqual(rows,[('EXCHANGE_CLOSED','ExchangeClosed: '+CLOSED_DETAIL)])

    def collector(self):
        store=SimpleNamespace(state=Mock(return_value={}),event=Mock(),failure=Mock(),halted=False)
        engine=SimpleNamespace(collect=Mock(side_effect=b.ExchangeClosed(CLOSED_DETAIL)))
        return run.Collector(engine,store,lambda:None),store

    def test_observed_then_fifty_discovery_503s(self):
        collector,store=self.collector();collector.once(THURSDAY)
        collector.engine.collect.side_effect=b.ReadError('Kalshi GET failed with HTTP 503',503)
        for i in range(50):
            result=collector.once(THURSDAY+15*(i+1))
            self.assertEqual(result,{'generation':None,'decision':'NO TRADE','advice_enabled':False,
                                     'exchange_closed':True,'closure_inferred':True})
        store.failure.assert_not_called()
        self.assertEqual(store.event.call_args.args[0],'CLOSURE_STATUS_UNAVAILABLE')
        detail=str(store.event.call_args.args[1])
        self.assertIn('503',detail);self.assertIn('window=',detail);self.assertIn('observed=',detail)
        self.assertLessEqual(len('ReadError: '+detail),200)

    def test_unproven_outside_window_and_non_503_fail(self):
        for observed,now,error in ((None,THURSDAY,b.ReadError('503',503)),
                                  (THURSDAY,THURSDAY+5400,b.ReadError('503',503)),
                                  (THURSDAY-7*86400,THURSDAY,b.ReadError('503',503)),
                                  (THURSDAY,THURSDAY+15,b.ReadError('429',429)),
                                  (THURSDAY,THURSDAY+15,b.ReadError('network/TLS')),
                                  (THURSDAY,THURSDAY+15,b.ReadError('malformed')),
                                  (THURSDAY,THURSDAY+15,ValueError('malformed')),
                                  (THURSDAY,THURSDAY+15,b.ReadError('HTTP 503 text without status'))):
            with self.subTest(observed=observed,now=now,error=error):
                collector,store=self.collector()
                if observed is not None:collector.once(observed)
                collector.engine.collect.side_effect=error
                with self.assertRaises(type(error)):collector.once(now)
                self.assertEqual(store.failure.call_args.args[0],'COLLECTION_FAILED')

    def test_publication_503_stays_a_failure(self):
        collector,store=self.collector();collector.once(THURSDAY)
        collector.engine.collect.side_effect=None;collector.engine.collect.return_value={}
        store.publish=Mock(side_effect=b.ReadError('publication HTTP 503',503))
        with self.assertRaises(b.ReadError):collector.once(THURSDAY+15)
        self.assertEqual(store.failure.call_args.args[0],'COLLECTION_FAILED')

    def test_fallback_status_503_is_preserved_and_inferred(self):
        c=object.__new__(b.Copilot);c.client=ClosedClient([],error=b.ReadError('status HTTP 503',503))
        c.positions=False;c.raw={};c.state={'contracts':{},'previous':None}
        settings={'slippage_reserve_cents':'1','minimum_conservative_net_edge_pp':'0'}
        with patch.object(b,'load_settings',return_value=settings):
            with self.assertRaises(b.ReadError) as ctx:c.collect(replay_health={'collection_epoch':THURSDAY},persist=False)
        self.assertEqual(ctx.exception.__cause__.status,503)
        collector,store=self.collector();collector.once(THURSDAY)
        collector.engine.collect.side_effect=ctx.exception
        self.assertTrue(collector.once(THURSDAY+15)['closure_inferred']);store.failure.assert_not_called()

    def test_snapshot_job_503_is_preserved_and_inferred(self):
        c,_=f.fixture()
        original=c.client.get
        def get(path,params=None):
            if path=='/cfbenchmarks/values':raise b.ReadError('benchmark HTTP 503',503)
            if path=='/series/'+b.SERIES:raise b.ReadError('series HTTP 429',429)
            return original(path,params)
        with patch.object(c.client,'get',side_effect=get),patch.object(b,'spot_get',return_value=f.RAW['spot']):
            with self.assertRaises(b.ReadError) as ctx:c.collect(replay_health={'collection_epoch':f.NOW},persist=False)
        self.assertIn(503,[e.status for e in ctx.exception.job_errors])
        collector,store=self.collector();collector.once(THURSDAY)
        collector.engine.collect.side_effect=ctx.exception
        self.assertTrue(collector.once(THURSDAY+15)['closure_inferred']);store.failure.assert_not_called()

    def test_exception_chains_and_cycles(self):
        for attribute in ('__cause__','__context__'):
            inner=b.ReadError('503',503);outer=b.ReadError('wrapped')
            setattr(outer,attribute,inner);inner.__context__=outer
            self.assertTrue(run.http_503(outer))
        outer=b.ReadError('network');outer.__context__=outer
        self.assertFalse(run.http_503(outer))

    def test_inferred_event_does_not_degrade_real_store(self):
        _,snapshot=f.fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            with EvidenceStore(root/'runtime',f.protocol_file(root),snapshot['model_version'],{},boot_id='synthetic',wall=lambda:THURSDAY) as store:
                engine=SimpleNamespace(collect=Mock(side_effect=b.ExchangeClosed(CLOSED_DETAIL)))
                collector=run.Collector(engine,store,lambda:None);collector.once(THURSDAY)
                with closing(sqlite3.connect(store.path)) as db:before=db.execute('SELECT degraded,reason FROM runtime_control').fetchone()
                engine.collect.side_effect=b.ReadError('503',503);collector.once(THURSDAY+15)
                with closing(sqlite3.connect(store.path)) as db:
                    self.assertEqual(db.execute('SELECT degraded,reason FROM runtime_control').fetchone(),before)
                    self.assertEqual(db.execute('SELECT kind FROM runtime_events ORDER BY id DESC LIMIT 1').fetchone()[0],'CLOSURE_STATUS_UNAVAILABLE')



class RunLoopTests(unittest.TestCase):
    def loop(self,side_effects,cycles,advance=15,start_at=None):
        operating=json.loads((f.ROOT/'operating_limits.json').read_text())
        context={'credential_config':'/nonexistent/synthetic','runtime_dir':str(f.ROOT/'runtime'),
                 **{k:operating[k] for k in ('quota_bytes','free_reserve_bytes','port')}}
        gate=Mock(return_value=context)
        store=SimpleNamespace(state=Mock(return_value={'contracts':{},'previous':None}),publish=Mock(return_value=1),
                              failure=Mock(),event=Mock(),halted=False,settle_one=Mock(return_value=False),close=Mock())
        engine=SimpleNamespace(client=SimpleNamespace(),collect=Mock(side_effect=side_effects))
        server=SimpleNamespace(serve_forever=Mock(),shutdown=Mock(),server_close=Mock())
        start=run.epoch(run.validate_readiness_protocol(json.loads((f.ROOT/'readiness_protocol.json').read_text()))['start_utc'])
        start=start+300 if start_at is None else start_at
        wall_state=[start]
        def wall():value=wall_state[0];wall_state[0]+=advance;return value
        def monotonic():return 1000+(wall_state[0]-start)   # extra calls (next_poll_delay) must not consume time
        kwargs={'client_factory':Mock(return_value=SimpleNamespace()),'engine_factory':Mock(return_value=engine),
                'store_factory':Mock(return_value=store),'server_factory':Mock(return_value=server),
                'wall':wall,'monotonic':monotonic,'sleep':Mock()}
        return gate,store,run.run_authorized(f.ROOT,gate,max_cycles=cycles,**kwargs)

    def test_two_hours_of_closed_polls_do_not_stop_or_fail(self):
        closed=[run.ExchangeClosed(CLOSED_DETAIL)]*480            # 2 h at 15 s
        gate,store,result=self.loop(closed,480)
        self.assertEqual(result['status'],'STOPPED');self.assertEqual(result['cycles'],480)
        store.failure.assert_not_called()                         # closed polls never touch the failure count
        closed_events=sum(1 for call in store.event.call_args_list if call.args and call.args[0]=='EXCHANGE_CLOSED')
        self.assertEqual(closed_events,480)

    def test_closed_polls_beyond_four_hours_need_operator_review(self):
        with self.assertRaisesRegex(run.RuntimeBlocked,'Exchange closed longer than expected; operator review'):
            self.loop([run.ExchangeClosed(CLOSED_DETAIL)]*1000,1000)

    def test_closed_polls_do_not_reset_prior_failures(self):
        side=[run.ReadError('synthetic outage')]+[run.ExchangeClosed(CLOSED_DETAIL)]*480+[run.ReadError('synthetic outage')]*39
        with self.assertRaisesRegex(run.RuntimeBlocked,'Persistent failure requires operator review'):
            self.loop(side,600)

    def test_forty_genuine_failures_still_stop_the_collector(self):
        with self.assertRaisesRegex(run.RuntimeBlocked,'Persistent failure requires operator review'):
            self.loop([run.ReadError('synthetic outage')]*40,100)

    def closure(self):
        # 30 min into the first scheduled closure after the protocol start, so the loop is inside the study window.
        start=run.epoch(run.validate_readiness_protocol(json.loads((f.ROOT/'readiness_protocol.json').read_text()))['start_utc'])
        at=next(t for t in range(int(start),int(start)+8*86400,900) if b.scheduled_closure_window(t))+1800
        self.assertTrue(b.scheduled_closure_window(at));return at

    def test_fifty_inferred_polls_do_not_stop_the_loop(self):
        side=[run.ExchangeClosed(CLOSED_DETAIL)]+[run.ReadError('503',503)]*50
        _,store,result=self.loop(side,51,start_at=self.closure())
        self.assertEqual(result['cycles'],51);store.failure.assert_not_called()
        self.assertEqual(sum(call.args[0]=='CLOSURE_STATUS_UNAVAILABLE' for call in store.event.call_args_list),50)

    def test_inferred_polls_do_not_reset_prior_failures(self):
        side=[run.ReadError('network'),run.ExchangeClosed(CLOSED_DETAIL)]+[run.ReadError('503',503)]*50+[run.ReadError('network')]*39
        with self.assertRaisesRegex(run.RuntimeBlocked,'Persistent failure requires operator review'):
            self.loop(side,91,start_at=self.closure())

    def test_mixed_pauses_preserve_four_hour_cap(self):
        side=[run.ExchangeClosed(CLOSED_DETAIL)]+[run.ReadError('503',503)]*50+[run.ExchangeClosed(CLOSED_DETAIL)]*1000
        with self.assertRaisesRegex(run.RuntimeBlocked,'Exchange closed longer than expected; operator review'):
            self.loop(side,962,start_at=self.closure())



class ReaderTests(unittest.TestCase):
    kind='EXCHANGE_CLOSED'
    def runtime_db(self,db,events,markets=(),generations=(),vetoes=(),control=(0,None)):
        with closing(sqlite3.connect(db)) as c, c:
            c.executescript('CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT); '
                            'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                            'CREATE TABLE slot_events(opened REAL,reason TEXT); '
                            'CREATE TABLE generations(epoch REAL); '
                            'CREATE TABLE runtime_control(degraded INTEGER,reason TEXT);')
            c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',events)
            c.executemany('INSERT INTO markets VALUES (?,?)',markets)
            c.executemany('INSERT INTO generations VALUES (?)',[(e,) for e in generations])
            c.executemany('INSERT INTO slot_events VALUES (?,?)',vetoes)
            c.execute('INSERT INTO runtime_control VALUES (?,?)',control)

    def test_missing_slots_names_exchange_closed_in_its_band(self):
        p={'holdout_start_utc':iso(START),'end_utc':iso(START+3*900)}
        markets=[{'phase':'holdout','opened':START,'checkpoint':START+315}]
        events=[{'epoch':START+900+315,'kind':self.kind,'detail':CLOSED_DETAIL}]
        missing=ev.missing_slots(p,markets,[],events)
        target=next(x for x in missing if x['slot_opened_utc']==iso(START+900))
        self.assertEqual(target['reasons'],[CLOSED_DETAIL])

    def test_acer_ops_names_exchange_closed_and_does_not_count_it_as_a_failure(self):
        now=START+3600
        with tempfile.TemporaryDirectory() as td:
            db=Path(td).resolve()/'evidence.sqlite3'
            self.runtime_db(db,[(START,'COLLECTOR_START','start'),
                                (START+900+315,self.kind,CLOSED_DETAIL)],
                            markets=[(START,START+315)],generations=[START+315])
            out=acer_ops.read_runtime(db,now)
        target=next(x for x in out['missed_slots_24h'] if x['slot_opened_utc']==iso(START+900))
        self.assertEqual(target['reasons'],[CLOSED_DETAIL])
        self.assertEqual((out['failures_last_hour'],out['poll_failure_rate_last_hour']),(0,0))

    def test_release_tool_summary_names_exchange_closed_in_its_band(self):
        now=datetime.fromtimestamp(START+2*3600,timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()/'candidate';root.mkdir()
            runtime=root/'runtime_shakedown';runtime.mkdir()
            (root/'operating_limits.json').write_text(json.dumps({'quota_bytes':128*1024**3}))
            db=runtime/'evidence.sqlite3'
            enrolled=[(s,s+315) for s in range(START,START+6301,900) if s!=START+900]
            self.runtime_db(db,[(START,'COLLECTOR_START','start'),
                                (START+900+315,self.kind,CLOSED_DETAIL),
                                (START+2*3600,'COLLECTOR_STOP','orderly stop')],
                            markets=enrolled)
            with patch.object(release_tool,'CANDIDATE',root):
                out=release_tool.shakedown_summary(runtime,now)
            target=next(x for x in out['missed_slots'] if x['slot_opened_utc']==iso(START+900))
            self.assertEqual(target['reasons'],[CLOSED_DETAIL])
            self.assertTrue(out['all_misses_have_reasons'])
            self.assertTrue(all(h['failed']==0 for h in out['poll_failure_rate_by_hour']))

    def test_shakedown_check_names_exchange_closed_and_does_not_count_it_as_a_failure(self):
        now=START+2*3600
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();db=root/'evidence.sqlite3';limits=root/'operating_limits.json'
            limits.write_text(json.dumps({'quota_bytes':128*1024**3}))
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript('CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT); '
                                'CREATE TABLE observations(id INTEGER PRIMARY KEY,epoch REAL,payload BLOB); '
                                'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                'CREATE TABLE slot_events(opened REAL,reason TEXT);')
                c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',
                              [(START,'COLLECTOR_START','start'),(START+900+315,self.kind,CLOSED_DETAIL)])
                for s in range(START,START+6301,900):
                    if s!=START+900:c.execute('INSERT INTO markets VALUES (?,?)',(s,s+315))
            active=SimpleNamespace(stdout='active\n',stderr='',returncode=0)
            with patch.object(shakedown_check,'DB',db),patch.object(shakedown_check,'LIMITS',limits), \
                 patch.object(shakedown_check.subprocess,'run',return_value=active):
                out=shakedown_check.report(now)
        labels=[label for label,_ in out['missed_reasons']]
        self.assertIn(CLOSED_DETAIL,labels)
        self.assertNotIn('NO OBSERVATION OR REASON IN THE BAND',labels)
        self.assertEqual((out['failures'],out['polls']),(0,0))


class InferredReaderTests(ReaderTests):
    kind='CLOSURE_STATUS_UNAVAILABLE'


class MonitorMessageTests(unittest.TestCase):
    def test_no_72h_message_when_the_service_is_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td).resolve()/'shakedown_check_state.json'
            report={'problems':[],'hours_run':80,'service':'inactive','enrolled':10,'slots':10,'window_hours':12}
            with patch.object(shakedown_check,'report',return_value=report), \
                 patch.object(shakedown_check,'STATE',state),patch.object(shakedown_check,'telegram',Mock()) as tg:
                shakedown_check.main()
            tg.assert_not_called()
            self.assertFalse(json.loads(state.read_text()).get('target_reached_sent'))


if __name__=='__main__':
    unittest.main()
