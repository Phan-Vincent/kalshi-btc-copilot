"""QA-01/03/05 regressions for the shakedown seal gate and monitor; synthetic SQLite only."""
import importlib.util, json, sqlite3, sys, tempfile, unittest, zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]                      # study005r2_release, the home of release_tool
sys.path.insert(0,str(HERE))                                  # a Path entry is ignored by the importer
import release_tool

MONITOR=str(Path(__file__).resolve().parents[3]/'acer/shakedown_check/shakedown_check.py')
spec=importlib.util.spec_from_file_location('shakedown_check_under_test',MONITOR)
shakedown_check=importlib.util.module_from_spec(spec);spec.loader.exec_module(shakedown_check)

START=datetime(2026,10,20,tzinfo=timezone.utc).timestamp()    # midnight: aligned to both 900 s and 3600 s
SCHEMA=('CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT); '
        'CREATE TABLE markets(opened REAL,checkpoint REAL); '
        'CREATE TABLE slot_events(opened REAL,reason TEXT); '
        'CREATE TABLE generations(epoch REAL);')


def candidate(td):
    """A throwaway candidate directory: only the limits file and the synthetic runtime database."""
    root=Path(td).resolve()/'candidate';root.mkdir()
    (root/'operating_limits.json').write_text(json.dumps({'quota_bytes':128*1024**3}))
    runtime=root/'runtime_shakedown';runtime.mkdir()
    return root,runtime

def build(runtime,events,markets,generations,vetoes=()):
    with closing(sqlite3.connect(runtime/'evidence.sqlite3')) as db, db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO runtime_events VALUES (?,?,?)',events)
        db.executemany('INSERT INTO markets VALUES (?,?)',markets)
        db.executemany('INSERT INTO generations VALUES (?)',[(epoch,) for epoch in generations])
        db.executemany('INSERT INTO slot_events VALUES (?,?)',vetoes)

def summarize(runtime,now,wan_log=None):
    with patch.object(release_tool,'CANDIDATE',runtime.parent):
        return release_tool.shakedown_summary(runtime,datetime.fromtimestamp(now,timezone.utc),wan_log)

def sealed(runtime,now,wan_log=None):
    with patch.object(release_tool,'CANDIDATE',runtime.parent):
        return release_tool.check_shakedown(runtime.parent,datetime.fromtimestamp(now,timezone.utc),wan_log)

def wan_log(path,*outages):
    """A dashboard home_internet.json holding only the given outage records."""
    path.write_text(json.dumps({'outages':list(outages)}));return path

def stamps(epoch):
    return datetime.fromtimestamp(epoch,timezone.utc).isoformat()

def gate_summary(root,now):
    """The all-good 288-slot summary of test_study005r2, rebuilt for this root's source digest."""
    good={'status':'QA_SHAKEDOWN_NOT_STUDY_EVIDENCE','generated_at_utc':now.isoformat(),
          'hours_covered':72,'slots':288,'enrolled_checkpoints':278,
          'missed_slots':[{'reasons':['recorded veto']}]*10,
          'poll_failure_rate_by_hour':[{'failure_rate':.01}]*72,
          'unexplained_collection_failures':0,
          'storage_growth':{'measured_bytes_per_day':40*1024**3/144,'uncertainty_multiplier':2,
                            'projected_72_day_bytes':40*1024**3,'quota_bytes':128*1024**3,
                            'sqlite_page_size':4096,
                            'main_database_cap_bytes':((128*1024**3-1024**2)//(3*4096))*4096}}
    good['candidate_source_sha256']=release_tool.shakedown_source_digest(root)
    return good


class HourBlockTests(unittest.TestCase):
    def test_partial_hour_after_stop_does_not_block_the_seal(self):
        # QA-01: the stop lands 15 s into the next clock hour, which has no polls at all.
        ended=START+72*3600+15
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(START,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START+n*900,START+n*900+315) for n in range(288)],
                  [START+h*3600+300 for h in range(72)])
            out=summarize(runtime,ended)
            self.assertEqual((out['slots'],out['enrolled_checkpoints'],out['missed_slots']),(288,288,[]))
            self.assertAlmostEqual(out['hours_covered'],72+15/3600,places=6)
            self.assertEqual(len(out['poll_failure_rate_by_hour']),73)
            last=out['poll_failure_rate_by_hour'][-1]
            self.assertEqual((last['failure_rate'],last['partial_hour'],last['inactivity_explained']),(None,True,False))
            self.assertEqual(sealed(runtime,ended)['slots'],288)

    def test_start_before_a_clock_hour_leaves_only_partial_edges(self):
        # QA-01: an unaligned start makes the first and last clock hours partial, not failures.
        start=START-80;ended=start+72*3600
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(start,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START+n*900,START+n*900+315) for n in range(288)],
                  [START+h*3600+300 for h in range(72)])
            out=summarize(runtime,ended)
            self.assertEqual((out['slots'],out['enrolled_checkpoints'],out['missed_slots']),(288,288,[]))
            self.assertEqual([(row['failure_rate'] is None,row['partial_hour'],row['inactivity_explained'])
                              for row in out['poll_failure_rate_by_hour']],
                             [(True,True,False)]+[(False,False,False)]*71+[(False,True,False)])
            self.assertEqual(sealed(runtime,ended)['slots'],288)

    def test_empty_hour_explained_by_reboot_reasons_still_passes(self):
        # QA-01: a full hour with no polls is acceptable when each of its slots is a named miss.
        ended=START+72*3600;gap=START+10*3600;gone=[gap+n*900 for n in range(4)]
        events=[(START,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')]
        events+=[(opened+305,'REBOOT_RESUME',json.dumps({'last_observation_epoch':opened+299})) for opened in gone]
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,events,[(START+n*900,START+n*900+315) for n in range(288) if START+n*900 not in gone],
                  [START+h*3600+300 for h in range(72) if h!=10])
            out=summarize(runtime,ended)
            self.assertEqual((out['slots'],out['enrolled_checkpoints']),(288,284))
            self.assertEqual(sorted(x['slot_opened_utc'] for x in out['missed_slots']),sorted(stamps(s) for s in gone))
            self.assertEqual({tuple(x['reasons']) for x in out['missed_slots']},{('reboot',)})
            row=next(row for row in out['poll_failure_rate_by_hour'] if row['hour_utc']==stamps(gap))
            self.assertEqual((row['failure_rate'],row['partial_hour'],row['inactivity_explained']),(None,False,True))
            self.assertEqual(sealed(runtime,ended)['enrolled_checkpoints'],284)

    def test_empty_hour_without_reasons_fails_the_seal(self):
        # QA-01: an unexplained empty full hour must still fail; it is not a partial hour.
        ended=START+72*3600;gap=START+10*3600;gone=[gap+n*900 for n in range(4)]
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(START,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START+n*900,START+n*900+315) for n in range(288) if START+n*900 not in gone],
                  [START+h*3600+300 for h in range(72) if h!=10])
            out=summarize(runtime,ended)
            self.assertEqual(sorted(x['slot_opened_utc'] for x in out['missed_slots']),sorted(stamps(s) for s in gone))
            self.assertEqual({tuple(x['reasons']) for x in out['missed_slots']},{()})
            row=next(row for row in out['poll_failure_rate_by_hour'] if row['hour_utc']==stamps(gap))
            self.assertEqual((row['failure_rate'],row['partial_hour'],row['inactivity_explained']),(None,False,False))
            with self.assertRaises(SystemExit):sealed(runtime,ended)


class SlotCoverageTests(unittest.TestCase):
    def test_final_slot_counts_once_its_band_has_elapsed(self):
        # QA-05: ended 840 s into the last slot, so its band completed; floor(ended/900) used to drop it.
        final=START+287*900;ended=final+840
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(START,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START+n*900,START+n*900+315) for n in range(287)],
                  [START+h*3600+300 for h in range(72)])
            out=summarize(runtime,ended)
            self.assertEqual((out['slots'],out['enrolled_checkpoints']),(288,287))
            self.assertEqual([x['slot_opened_utc'] for x in out['missed_slots']],[stamps(final)])

    def test_restart_after_the_band_keeps_the_stop_reason(self):
        # QA-03: a restart at opened+329 is after the whole band, so the stop still explains the miss.
        missed=START+900;ended=missed+400
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(START,'COLLECTOR_START','start'),(missed+200,'COLLECTOR_STOP','orderly stop'),
                           (missed+329,'COLLECTOR_START','restart'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START,START+315)],[START+300,missed+300])
            out=summarize(runtime,ended)
            self.assertEqual(out['slots'],2)
            self.assertEqual([(x['slot_opened_utc'],x['reasons']) for x in out['missed_slots']],
                             [(stamps(missed),['orderly stop'])])


class SealSchemaTests(unittest.TestCase):
    def test_seal_gate_rejects_blank_reasons_bad_hours_and_bad_rates(self):
        # QA-05: the schema gate used to let a blank reason, a missing count, NaN hours and a NaN rate through.
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();(root/'source.py').write_text('v1')
            good=gate_summary(root,now);path=root/'shakedown_summary.json'
            path.write_text(json.dumps(good));self.assertEqual(release_tool.check_shakedown(root,now)['slots'],288)
            no_count=dict(good);del no_count['unexplained_collection_failures']
            for bad in (dict(good,missed_slots=[{'reasons':['recorded veto']}]*9+[{'reasons':['  ']}]),
                        dict(good,missed_slots=[{'reasons':['recorded veto']}]*9+[{'reasons':['ok',7]}]),
                        no_count,
                        dict(good,hours_covered=float('nan')),
                        dict(good,poll_failure_rate_by_hour=[{'failure_rate':float('nan')}]*72)):
                path.write_text(json.dumps(bad))
                with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)


class MonitorTests(unittest.TestCase):
    def test_monitor_names_slot_event_and_reboot_misses(self):
        # QA-05: the monitor used to report these explained misses as "NO OBSERVATION OR REASON".
        now=START+2200;logged=START+900;rebooted=START+1800
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();db=root/'evidence.sqlite3';limits=root/'operating_limits.json'
            limits.write_text(json.dumps({'quota_bytes':128*1024**3}))
            payload=zlib.compress(json.dumps({'snapshot':{'retrieval_health':{'snapshot_skew_seconds':1.5}}}).encode())
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript('CREATE TABLE observations(id INTEGER PRIMARY KEY,epoch REAL,payload BLOB); '
                                'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                'CREATE TABLE slot_events(opened REAL,reason TEXT); '
                                'CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT);')
                c.execute('INSERT INTO observations VALUES (1,?,?)',(logged+300,payload))
                c.execute('INSERT INTO runtime_events VALUES (?,?,?)',(START,'COLLECTOR_START','start'))
                c.execute('INSERT INTO runtime_events VALUES (?,?,?)',
                          (rebooted+305,'REBOOT_RESUME',json.dumps({'last_observation_epoch':rebooted+299})))
                c.execute('INSERT INTO markets VALUES (?,?)',(START,START+315))
                c.execute('INSERT INTO slot_events VALUES (?,?)',(logged,'Recent trade window incomplete'))
            active=SimpleNamespace(stdout='active\n',stderr='',returncode=0)
            with patch.object(shakedown_check,'DB',db),patch.object(shakedown_check,'LIMITS',limits), \
                 patch.object(shakedown_check.subprocess,'run',return_value=active):
                out=shakedown_check.report(now)
        labels=[label for label,_ in out['missed_reasons']]
        self.assertEqual((out['slots'],out['enrolled']),(3,1))
        self.assertIn('Recent trade window incomplete',labels)
        self.assertIn('reboot',labels)
        self.assertNotIn('NO OBSERVATION OR REASON IN THE BAND',labels)


# The 2026-10-21 shape: a modem drop at 10:48:45 for 94 s, two network failures and one knock-on failure,
# and the 10:45 slot's 10:50:00-10:50:30 band unobserved.
MISS=START+10*3600+45*60;DROP=MISS+225;BACK=DROP+94
NET='ReadError: Required data unavailable: event (ReadError: Network or TLS failure during Kalshi GET)'
FAILS=[(DROP+45,'COLLECTION_FAILED',NET),(BACK+16,'COLLECTION_FAILED','ReadError: Decision completion outside valid observation window'),
       (BACK+50,'COLLECTION_FAILED',NET)]
OUTAGE={'start':DROP,'end':BACK,'seconds':94,'confirmed':True,'where':'modem','where_samples':{'modem':4}}


def wan_run(runtime,fails=FAILS):
    ended=START+72*3600
    build(runtime,[(START,'COLLECTOR_START','start'),*fails,(ended,'COLLECTOR_STOP','orderly stop')],
          [(START+n*900,START+n*900+315) for n in range(288) if START+n*900!=MISS],
          [START+h*3600+300 for h in range(72)])
    return ended


class WanOutageTests(unittest.TestCase):
    def test_confirmed_outage_excuses_its_failures_and_miss(self):
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td);ended=wan_run(runtime);log=wan_log(Path(td)/'home_internet.json',OUTAGE)
            out=summarize(runtime,ended)
            self.assertEqual([x['reasons'] for x in out['missed_slots']],[[]])
            with self.assertRaises(SystemExit):sealed(runtime,ended)
            out=summarize(runtime,ended,log)
            reason=release_tool.WAN_REASON+stamps(DROP)+'..'+stamps(BACK)+' (modem)'
            self.assertEqual([x['reasons'] for x in out['missed_slots']],[[reason]])
            row=next(row for row in out['poll_failure_rate_by_hour'] if row['hour_utc']==stamps(START+10*3600))
            self.assertEqual((row['failed'],row['failed_during_wan_outage'],row['failure_rate']),(3,3,0.0))
            self.assertEqual([o['reason'] for o in out['wan_outages']['windows']],[reason])
            self.assertEqual(sealed(runtime,ended,log)['enrolled_checkpoints'],287)
            # Seal confirms each excused window against this Mac's dashboard log.
            with self.assertRaises(SystemExit):sealed(runtime,ended)
            with self.assertRaises(SystemExit):sealed(runtime,ended,wan_log(Path(td)/'other.json',dict(OUTAGE,end=BACK+1)))

    def test_outage_excuses_nothing_unless_confirmed_short_and_corroborated(self):
        http=[(e,k,'ReadError: Required data unavailable: benchmark (ReadError: Kalshi GET failed with HTTP 503)') for e,k,_ in FAILS]
        for fails,outage in ((http,OUTAGE),(FAILS,dict(OUTAGE,confirmed=False)),
                             (FAILS,dict(OUTAGE,start=BACK-901)),(FAILS,dict(OUTAGE,start=DROP+3600,end=BACK+3600))):
            with tempfile.TemporaryDirectory() as td:
                root,runtime=candidate(td);ended=wan_run(runtime,fails)
                out=summarize(runtime,ended,wan_log(Path(td)/'home_internet.json',outage))
                self.assertEqual((out['wan_outages']['windows'],[x['reasons'] for x in out['missed_slots']]),([],[[]]))
                with self.assertRaises(SystemExit):sealed(runtime,ended,Path(td)/'home_internet.json')

    def test_seal_gate_rejects_forged_or_inconsistent_wan_excuses(self):
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();(root/'source.py').write_text('v1')
            good=gate_summary(root,now);path=root/'shakedown_summary.json'
            hour={'failed':3,'successful':200,'failed_during_wan_outage':3,'failure_rate':0.0}
            for bad in (dict(good,missed_slots=[{'reasons':['recorded veto']}]*9+[{'reasons':[release_tool.WAN_REASON+'made up']}]),
                        dict(good,poll_failure_rate_by_hour=[{'failure_rate':.01}]*71+[hour]),
                        dict(good,poll_failure_rate_by_hour=[{'failure_rate':.01}]*71+[dict(hour,failed_during_wan_outage=0)]),
                        dict(good,wan_outages={'lead_seconds':0,'tail_seconds':3600,'max_seconds':86400,'windows':[]})):
                path.write_text(json.dumps(bad))
                with self.assertRaises(SystemExit):release_tool.check_shakedown(root,now)

    def test_monitor_applies_the_same_excuse(self):
        now=START+12*3600
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();db=root/'evidence.sqlite3';limits=root/'operating_limits.json'
            limits.write_text(json.dumps({'quota_bytes':128*1024**3}))
            payload=zlib.compress(json.dumps({'snapshot':{'retrieval_health':{'snapshot_skew_seconds':1.5}}}).encode())
            with closing(sqlite3.connect(db)) as c, c:
                c.executescript('CREATE TABLE observations(id INTEGER PRIMARY KEY,epoch REAL,payload BLOB); '
                                'CREATE TABLE markets(opened REAL,checkpoint REAL); '
                                'CREATE TABLE slot_events(opened REAL,reason TEXT); '
                                'CREATE TABLE runtime_events(epoch REAL,kind TEXT,detail TEXT);')
                c.executemany('INSERT INTO observations(epoch,payload) VALUES (?,?)',[(START+h*3600+300,payload) for h in range(12)])
                c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',[(START,'COLLECTOR_START','start')]+FAILS)
                c.executemany('INSERT INTO markets VALUES (?,?)',[(START+n*900,START+n*900+315) for n in range(48) if START+n*900!=MISS])
            active=SimpleNamespace(stdout='active\n',stderr='',returncode=0)
            runs={}
            for name,log in (('absent',root/'missing.json'),('logged',wan_log(root/'wan_outages.json',OUTAGE))):
                with patch.object(shakedown_check,'DB',db),patch.object(shakedown_check,'LIMITS',limits), \
                     patch.object(shakedown_check,'WAN_LOG',log),patch.object(shakedown_check.subprocess,'run',return_value=active):
                    runs[name]=shakedown_check.report(now)
        self.assertTrue(any(p.startswith('Poll failures above 1%') for p in runs['absent']['problems']))
        self.assertTrue(any('missed slot(s) with no reason' in p for p in runs['absent']['problems']))
        self.assertEqual(runs['logged']['problems'],[])
        self.assertEqual(runs['logged']['failures_during_wan_outage'],3)
        self.assertEqual([label for label,_ in runs['logged']['missed_reasons']],['home WAN outage 10-20 10:48:45 (modem)'])


HTTP_503='ReadError: Required data unavailable: benchmark (ReadError: Kalshi GET failed with HTTP 503)'


def upstream_run(runtime,detail=HTTP_503,successes=200):
    ended=START+72*3600;miss=START+15*3600+900
    failures=[(miss+315+n,'COLLECTION_FAILED',detail) for n in range(43)]
    build(runtime,[(START,'COLLECTOR_START','start'),*failures,(ended,'COLLECTOR_STOP','orderly stop')],
          [(START+n*900,START+n*900+315) for n in range(288) if START+n*900!=miss],
          [START+h*3600+10+n*15 for h in range(72) for n in range(successes)])
    return ended


class Upstream5xxTests(unittest.TestCase):
    def test_72_hour_503_excuse_and_429_failure(self):
        for detail in (HTTP_503,HTTP_503.replace('503','429')):
            with self.subTest(detail=detail),tempfile.TemporaryDirectory() as td:
                root,runtime=candidate(td);ended=upstream_run(runtime,detail)
                out=summarize(runtime,ended);row=out['poll_failure_rate_by_hour'][15]
                self.assertEqual(out['missed_slots'][0]['reasons'],[detail])
                self.assertEqual(out['upstream_5xx']['polls'],14443)
                if detail==HTTP_503:
                    self.assertEqual((row['failed'],row['failed_upstream_5xx'],row['failure_rate']),(43,43,0.0))
                    self.assertEqual(out['upstream_5xx'],{'max_fraction':.01,'excused':43,'polls':14443})
                    sealed(runtime,ended)
                else:
                    self.assertEqual(row['failed_upstream_5xx'],0)
                    with self.assertRaises(SystemExit):sealed(runtime,ended)

    def test_wan_wins_over_5xx(self):
        fails=FAILS+[(DROP+10,'COLLECTION_FAILED',HTTP_503)]
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td);ended=wan_run(runtime,fails)
            log=wan_log(Path(td)/'wan.json',OUTAGE);out=summarize(runtime,ended,log)
            row=out['poll_failure_rate_by_hour'][10]
            self.assertEqual((row['failed'],row['failed_during_wan_outage'],row['failed_upstream_5xx'],row['failure_rate']),(4,4,0,0.0))
            self.assertEqual(out['upstream_5xx']['excused'],0)
            sealed(runtime,ended,log)

    def test_run_cap_rejects_even_zero_hourly_rates(self):
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td);ended=upstream_run(runtime,successes=1)
            out=summarize(runtime,ended)
            self.assertEqual(out['poll_failure_rate_by_hour'][15]['failure_rate'],0.0)
            with self.assertRaisesRegex(SystemExit,'UPSTREAM_5XX_MAX_FRACTION'):sealed(runtime,ended)

    def test_summary_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td);ended=upstream_run(runtime);good=summarize(runtime,ended)
            for field,value in (('failure_rate',.001),('failed_upstream_5xx',44),
                                ('failed_upstream_5xx',-1),('failed_upstream_5xx',True),('failed_upstream_5xx',1.5),
                                ('failed_during_wan_outage',1),('excused',42),('polls',14444),('max_fraction',.02)):
                with self.subTest(field=field,value=value):
                    bad=json.loads(json.dumps(good))
                    if field in ('excused','polls','max_fraction'):bad['upstream_5xx'][field]=value
                    else:bad['poll_failure_rate_by_hour'][15][field]=value
                    (root/'shakedown_summary.json').write_text(json.dumps(bad))
                    with self.assertRaises(SystemExit):sealed(runtime,ended)
            # A corroborated WAN window must not allow overlapping counts either.
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td);ended=wan_run(runtime)
            log=wan_log(Path(td)/'wan.json',OUTAGE);bad=summarize(runtime,ended,log)
            bad['poll_failure_rate_by_hour'][10]['failed_upstream_5xx']=1
            bad['upstream_5xx']['excused']=1
            (root/'shakedown_summary.json').write_text(json.dumps(bad))
            with self.assertRaises(SystemExit):sealed(runtime,ended,log)

    def test_old_format_summary_still_passes(self):
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);good=gate_summary(root,now)
            self.assertNotIn('upstream_5xx',good)
            (root/'shakedown_summary.json').write_text(json.dumps(good))
            self.assertEqual(release_tool.check_shakedown(root,now)['slots'],288)

    def test_other_failures_still_count(self):
        for detail in ('Kalshi GET failed with HTTP 429','Network or TLS failure',
                       'Cannot verify exactly one active BTC 15-minute contract',
                       'Kalshi GET failed with HTTP 5030','other failure'):
            with self.subTest(detail=detail),tempfile.TemporaryDirectory() as td:
                root,runtime=candidate(td);ended=upstream_run(runtime,detail,successes=1)
                out=summarize(runtime,ended)
                self.assertEqual(out['upstream_5xx']['excused'],0)
                with self.assertRaises(SystemExit):sealed(runtime,ended)


def monitor_report(td,start,now,failures,successes=200,log=None):
    root=Path(td);db=root/'evidence.sqlite3';limits=root/'limits.json'
    limits.write_text(json.dumps({'quota_bytes':128*1024**3}))
    payload=zlib.compress(json.dumps({'snapshot':{'retrieval_health':{'snapshot_skew_seconds':1.5}}}).encode())
    with closing(sqlite3.connect(db)) as c,c:
        c.executescript(SCHEMA+'CREATE TABLE observations(id INTEGER PRIMARY KEY,epoch REAL,payload BLOB);')
        epochs=[h+10+n*15 for h in range(int(start//3600)*3600,int(now),3600) for n in range(successes)
                if start<=h+10+n*15<=now]
        c.executemany('INSERT INTO observations(epoch,payload) VALUES (?,?)',[(e,payload) for e in epochs])
        c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',[(start,'COLLECTOR_START','start')]+failures)
        c.executemany('INSERT INTO markets VALUES (?,?)',[(s,s+315) for s in range(int(start//900)*900,int(now),900)])
    with patch.object(shakedown_check,'DB',db),patch.object(shakedown_check,'LIMITS',limits), \
         patch.object(shakedown_check,'WAN_LOG',log or root/'absent.json'), \
         patch.object(shakedown_check.time,'sleep'), \
         patch.object(shakedown_check.subprocess,'run',return_value=SimpleNamespace(stdout='active\n')):
        return shakedown_check.report(now)


class MonitorBoundaryTests(unittest.TestCase):
    def test_timer_boundary_includes_previous_1500_hour(self):
        now=START+27*3600+2;start=START+3*3600
        for detail in (HTTP_503,HTTP_503.replace('503','429')):
            with self.subTest(detail=detail),tempfile.TemporaryDirectory() as td:
                failures=[(START+15*3600+1200+n,'COLLECTION_FAILED',detail) for n in range(3)]
                out=monitor_report(td,start,now,failures)
                bad=[p for p in out['problems'] if p.startswith('Poll failures above 1%')]
                if detail==HTTP_503:
                    self.assertEqual(bad,[])
                    self.assertEqual(out['failures_upstream_5xx'],3)
                    self.assertEqual(out['problems'],[])
                else:
                    self.assertEqual(len(bad),1)
                    self.assertIn('10-20 15:00 3/203',bad[0])
                    self.assertEqual(out['failures_upstream_5xx'],0)

    def test_partial_first_hour_is_judged(self):
        start=START+15*3600+1800;now=START+27*3600+2
        with tempfile.TemporaryDirectory() as td:
            failures=[(start+10+n,'COLLECTION_FAILED','Kalshi GET failed with HTTP 429') for n in range(3)]
            out=monitor_report(td,start,now,failures)
            self.assertTrue(any('10-20 15:00' in p for p in out['problems']))

    def test_monitor_cap_and_wan_precedence(self):
        now=START+12*3600
        with tempfile.TemporaryDirectory() as td:
            failures=[(START+3600+1200+n,'COLLECTION_FAILED',HTTP_503) for n in range(30)]
            out=monitor_report(td,START,now,failures)
            self.assertFalse(any(p.startswith('Poll failures above 1%') for p in out['problems']))
            self.assertTrue(any('UPSTREAM_5XX_MAX_FRACTION' in p for p in out['problems']))
        with tempfile.TemporaryDirectory() as td:
            log=wan_log(Path(td)/'wan.json',OUTAGE)
            out=monitor_report(td,START,now,FAILS+[(DROP+10,'COLLECTION_FAILED',HTTP_503)],log=log)
            self.assertEqual(out['failures_during_wan_outage'],4)
            self.assertEqual(out['failures_upstream_5xx'],0)
            self.assertEqual(out['problems'],[])

    def test_alert_names_excused_5xx(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/'state.json'
            with patch.object(shakedown_check,'report',return_value={'problems':['test drift'],'failures_upstream_5xx':3}), \
                 patch.object(shakedown_check,'STATE',state),patch.object(shakedown_check.sys,'argv',['shakedown_check.py']), \
                 patch.object(shakedown_check,'telegram') as send:
                shakedown_check.main()
            self.assertIn('Excused as Kalshi-side 5xx: 3 failure(s)',send.call_args.args[0])


class AdversarialQA20261025Tests(unittest.TestCase):
    def test_r2_01_monitor_counts_wan_failures_in_current_partial_hour(self):
        now=START+5400
        outage={'start':START+3700,'end':START+3760,'seconds':60,'confirmed':True,'where':'modem'}
        with tempfile.TemporaryDirectory() as td:
            log=wan_log(Path(td)/'wan.json',outage)
            out=monitor_report(td,START,now,[(START+3710,'COLLECTION_FAILED',NET),(START+3720,'COLLECTION_FAILED',NET)],log=log)
        self.assertEqual(out['wan_outages'],['home WAN outage 10-20 01:01:40 (modem)'])
        self.assertEqual(out['failures_during_wan_outage'],2)

    def test_r2_02_monitor_uses_stops_preceding_lookback_window(self):
        now=START+24*3600+1800
        since=int(now-shakedown_check.LOOKBACK)//3600*3600
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);db=root/'evidence.sqlite3';limits=root/'limits.json'
            limits.write_text(json.dumps({'quota_bytes':128*1024**3}))
            payload=zlib.compress(json.dumps({'snapshot':{'retrieval_health':{'snapshot_skew_seconds':1.5}}}).encode())
            with closing(sqlite3.connect(db)) as c,c:
                c.executescript(SCHEMA+'CREATE TABLE observations(id INTEGER PRIMARY KEY,epoch REAL,payload BLOB);')
                c.execute('INSERT INTO observations(epoch,payload) VALUES (?,?)',(since+600,payload))
                c.executemany('INSERT INTO runtime_events VALUES (?,?,?)',
                              [(START,'COLLECTOR_START','start'),(since-10,'COLLECTOR_STOP','orderly stop'),
                               (since+320,'COLLECTOR_START','restart')])
                c.executemany('INSERT INTO markets VALUES (?,?)',[(s,s+315) for s in range(int(since)+900,int(now),900)])
            with patch.object(shakedown_check,'DB',db),patch.object(shakedown_check,'LIMITS',limits), \
                 patch.object(shakedown_check,'WAN_LOG',root/'absent.json'), \
                 patch.object(shakedown_check.time,'sleep'), \
                 patch.object(shakedown_check.subprocess,'run',return_value=SimpleNamespace(stdout='active\n')):
                out=shakedown_check.report(now)
        self.assertEqual(out['problems'],[])
        self.assertIn(('orderly stop',1),out['missed_reasons'])

    def test_r2_03_seal_gate_rejects_unbacked_wan_counts_bool_slots_and_fake_inactivity_explained(self):
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();(root/'source.py').write_text('v1')
            good=gate_summary(root,now);path=root/'shakedown_summary.json'
            log=wan_log(root/'wan.json',OUTAGE)
            wan_block={'source_sha256':release_tool.sha(log),'lead_seconds':30,'tail_seconds':60,'max_seconds':900,
                       'windows':[dict(OUTAGE,reason=release_tool.wan_reason(OUTAGE))]}
            for bad in (
                dict(good,wan_outages=wan_block,poll_failure_rate_by_hour=[{'failure_rate':0.0,'failed_during_wan_outage':999}]+[{'failure_rate':.01}]*71),
                dict(good,slots=True,enrolled_checkpoints=True,missed_slots=[]),
                dict(good,missed_slots=[],enrolled_checkpoints=288,
                     poll_failure_rate_by_hour=[{'hour_utc':stamps(START),'failure_rate':None,'partial_hour':False,'inactivity_explained':True}]+[{'failure_rate':.01}]*71),
            ):
                path.write_text(json.dumps(bad))
                with self.assertRaises(SystemExit):
                    release_tool.check_shakedown(root,now,log)

    def test_r2_04_first_slot_band_after_unaligned_start_matches_monitor(self):
        start=START+100;ended=start+72*3600
        with tempfile.TemporaryDirectory() as td:
            root,runtime=candidate(td)
            build(runtime,[(start,'COLLECTOR_START','start'),(ended,'COLLECTOR_STOP','orderly stop')],
                  [(START+n*900,START+n*900+315) for n in range(288)],
                  [START+h*3600+300 for h in range(72)])
            out=summarize(runtime,ended)
            self.assertEqual((out['slots'],out['enrolled_checkpoints']),(288,288))

    def test_r2_05_shakedown_start_handles_empty_or_uninitialized_db(self):
        start_mod_path=str(Path(__file__).resolve().parents[3]/'acer/shakedown_check/shakedown_start.py')
        spec=importlib.util.spec_from_file_location('shakedown_start_under_test',start_mod_path)
        shakedown_start=importlib.util.module_from_spec(spec);spec.loader.exec_module(shakedown_start)
        with tempfile.TemporaryDirectory() as td:
            rt=Path(td);(rt/'evidence.sqlite3').write_bytes(b'')
            with patch.object(shakedown_start,'RUNTIME',rt):
                self.assertEqual(shakedown_start.stored_boot_and_last(),(None,None))


if __name__=='__main__':
    unittest.main()
