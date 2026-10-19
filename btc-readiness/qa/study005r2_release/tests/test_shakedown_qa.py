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

def summarize(runtime,now):
    with patch.object(release_tool,'CANDIDATE',runtime.parent):
        return release_tool.shakedown_summary(runtime,datetime.fromtimestamp(now,timezone.utc))

def sealed(runtime,now):
    with patch.object(release_tool,'CANDIDATE',runtime.parent):
        return release_tool.check_shakedown(runtime.parent,datetime.fromtimestamp(now,timezone.utc))

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


if __name__=='__main__':
    unittest.main()
