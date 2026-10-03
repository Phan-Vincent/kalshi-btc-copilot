"""Independent interruption/concurrency regressions; temporary state only."""
import sys
sys.argv.insert(1,'candidate')
import remediation_audit as a
import copy,errno,json,socket,sqlite3,tempfile,unittest,os,time,threading
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
b=a.b
import btc_copilot_evidence as e
import study_policy as p

class Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        raw=copy.deepcopy(a.RAW);raw['trades']['data']={'trades':[]};self.s=a.collect(raw);self.c=a.make(raw)
        self.c.state_path=self.root/'state.json'
        self.work=patch.object(b,'WORK',self.root);self.work.start()
    def tearDown(self):self.work.stop();self.tmp.cleanup()
    def test_disk_full_state_write_does_not_mutate_memory(self):
        before=copy.deepcopy(self.c.state)
        with patch.object(b,'atomic',side_effect=OSError(errno.ENOSPC,'synthetic disk full')),self.assertRaises(OSError):b.Copilot.record(self.c,self.s)
        self.assertEqual(self.c.state,before)
    def test_log_failure_cannot_leave_healthy_confirmation(self):
        with patch.object(b,'append',side_effect=OSError(errno.ENOSPC,'synthetic log full')),self.assertRaises(OSError):b.Copilot.record(self.c,self.s)
        persisted=json.loads(self.c.state_path.read_text()) if self.c.state_path.exists() else self.c.state
        self.assertFalse((persisted.get('previous') or {}).get('confirmation_valid',False))
        self.assertFalse((self.c.state.get('previous') or {}).get('confirmation_valid',False))
    def test_duplicate_observation_does_not_increment_count(self):
        b.Copilot.record(self.c,self.s);b.Copilot.record(self.c,self.s)
        self.assertEqual(self.c.state['contracts'][self.s['ticker']]['observations'],1)
    def test_reversed_observation_rejected_before_state_write(self):
        b.Copilot.record(self.c,self.s);before=self.c.state_path.read_bytes()
        older=copy.deepcopy(self.s);older['epoch']-=15
        with self.assertRaises((a.ReadError,ValueError)):b.Copilot.record(self.c,older)
        self.assertEqual(self.c.state_path.read_bytes(),before)
    def test_different_payload_same_observation_rejected(self):
        b.Copilot.record(self.c,self.s);changed=copy.deepcopy(self.s);changed['btc']+=1
        with self.assertRaises((a.ReadError,ValueError)):b.Copilot.record(self.c,changed)
    def test_log_symlink_cannot_modify_other_file(self):
        sentinel=self.root/'sentinel';sentinel.write_text('KEEP');path=self.root/'observations.jsonl';path.symlink_to(sentinel)
        with self.assertRaises((a.ReadError,OSError)):b.append(path,{'bad':True})
        self.assertEqual(sentinel.read_text(),'KEEP')
    def test_log_hardlink_cannot_modify_other_file(self):
        sentinel=self.root/'sentinel';sentinel.write_text('KEEP');path=self.root/'observations.jsonl';os.link(sentinel,path)
        with self.assertRaises((a.ReadError,OSError)):b.append(path,{'bad':True})
        self.assertEqual(sentinel.read_text(),'KEEP')
    def test_pending_survives_every_downstream_failure_and_restart(self):
        from types import SimpleNamespace
        boundaries=['audit.record','audit.scorecard','study.record','study.status','latest.json','latest.txt','raw.json','state_commit']
        for boundary in boundaries:
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as td:
                root=Path(td);c=a.make(self.c.raw if self.c.raw else a.RAW);c.raw=copy.deepcopy(a.RAW);c.prior=None;c.settings=a.SETTINGS;c.state_path=root/'state.json'
                audit=SimpleNamespace(record=lambda *args:None,scorecard=lambda:{})
                study=SimpleNamespace(record=lambda *args:None,status=lambda *args:{},last_report=a.NOW)
                real_atomic=b.atomic
                def atomic(path,value):
                    names={'latest.json':'btc_copilot_latest.json','latest.txt':'btc_copilot_latest.txt','raw.json':'btc_copilot_raw_latest.json','state_commit':'state.json'}
                    if Path(path).name==names.get(boundary):raise OSError('injected '+boundary)
                    return real_atomic(path,value)
                with patch.object(b,'WORK',root),patch.object(b,'ROOT',root),patch.object(b.time,'time',return_value=a.NOW):
                    b.Copilot.record(c,self.s)
                    if boundary.startswith('audit.') or boundary.startswith('study.'):
                        obj=audit if boundary.startswith('audit.') else study
                        with patch.object(obj,boundary.split('.')[1],side_effect=OSError(boundary)),self.assertRaises(OSError):b.persist_observation(c,audit,study,copy.deepcopy(self.s))
                    else:
                        with patch.object(b,'atomic',side_effect=atomic),self.assertRaises(OSError):b.persist_observation(c,audit,study,copy.deepcopy(self.s))
                restart=json.loads(c.state_path.read_text())
                self.assertFalse(restart['previous']['confirmation_valid']);self.assertFalse(restart['last_observation']['committed'])
    def test_successful_pipeline_commits_once_and_rejects_old_commit(self):
        from types import SimpleNamespace
        c=self.c;c.raw=copy.deepcopy(a.RAW);c.prior=None;c.settings=a.SETTINGS
        audit=SimpleNamespace(record=lambda *args:None,scorecard=lambda:{})
        study=SimpleNamespace(record=lambda *args:None,status=lambda *args:{},last_report=a.NOW)
        with patch.object(b,'ROOT',self.root),patch.object(b.time,'time',return_value=a.NOW):
            b.Copilot.record(c,self.s);self.assertFalse(c.state['previous']['confirmation_valid'])
            b.persist_observation(c,audit,study,copy.deepcopy(self.s));self.assertTrue(c.state['previous']['confirmation_valid'])
            b.Copilot.commit_observation(c,self.s);self.assertEqual(c.state['contracts'][self.s['ticker']]['observations'],1)
            later=copy.deepcopy(self.s);later['epoch']+=15;b.Copilot.record(c,later)
            with self.assertRaises(a.ReadError):b.Copilot.commit_observation(c,self.s)
            self.assertFalse(c.state['previous']['confirmation_valid'])
    def test_log_failure_retry_repair_without_count_increment(self):
        with patch.object(b,'append',side_effect=OSError('log failed')),self.assertRaises(OSError):b.Copilot.record(self.c,self.s)
        b.Copilot.record(self.c,self.s)
        self.assertEqual(self.c.state['contracts'][self.s['ticker']]['observations'],1)
        self.assertFalse(self.c.state['previous']['confirmation_valid'])
        self.assertTrue(self.c.state['last_observation']['logged'])
    def test_two_state_writers_do_not_lose_or_duplicate_updates(self):
        second=a.make(a.RAW);second.state_path=self.c.state_path
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda c:b.Copilot.record(c,self.s),(self.c,second)))
        state=json.loads(self.c.state_path.read_text());self.assertEqual(state['contracts'][self.s['ticker']]['observations'],1)
        self.assertEqual(len((self.root/'btc_copilot_observations.jsonl').read_text().splitlines()),1)
    def test_corrupt_state_never_reinitialized(self):
        self.c.state_path.write_text('{')
        with self.assertRaises(json.JSONDecodeError):b.Copilot.record(self.c,self.s)
        self.assertEqual(self.c.state_path.read_text(),'{')
    def test_repeated_restart_cannot_use_pending_snapshot_for_confirmation(self):
        raw,structure,model,healthy_prior=a.eligible()
        c=a.make(raw);c.state_path=self.root/'state.json'
        s=a.collect(raw,None,structure,model);b.Copilot.record(c,s)
        for _ in range(3):
            pending=json.loads(c.state_path.read_text())['previous']
            # A newer source with the same setup still cannot confirm an incomplete run.
            pending['epoch']=a.NOW-15;pending['source_epoch']=a.NOW-16
            later=a.collect(raw,pending,structure,model)
            self.assertEqual(later['shadow_decision'],'NO TRADE')
            self.assertFalse(later['shadow_strategies']['current_full']['eligible'])
    def test_fee_targets_across_prices_and_multipliers(self):
        from decimal import Decimal as D
        market=copy.deepcopy(a.RAW['market']['data']['market'])
        for multiplier in (1,2):
            for entry in (D('.05'),D('.15'),D('.45')):
                target=b.profit_target(market,entry,multiplier)
                self.assertIsNotNone(target)
                self.assertGreaterEqual(target-entry-b.taker_fee(entry,multiplier)-b.taker_fee(target,multiplier),D('.03'))
    def study_fixture(self):
        protocol=a.ROOT/'btc_copilot_protocol.json';start=p.epoch(json.loads(protocol.read_text())['start_utc']);now=start+300
        study=e.Study(self.root/'study.sqlite3',protocol,'synthetic-only',{})
        s={'ticker':'SYNTHETIC','model_version':'synthetic-only','epoch':now,'observation_monotonic':now,'open_time':b.stamp(start)['utc'],'close_time':b.stamp(start+900)['utc'],'fee_multiplier':'1','shadow_strategies':{},'time_remaining_seconds':600,'operational_vetoes':['test abstention'],'book':{},'model':{},'drift_free_model':{},'structure':{},'valid_until_epoch':now+20,'retrieval_health':{'requests':{'book':{'started_at_epoch':now-.2,'finished_at_epoch':now,'latency_seconds':.2}}}}
        return study,s,{'book':{'data':{'orderbook_fp':{}}}}
    def test_simultaneous_duplicate_study_records_only_one_succeeds(self):
        study,s,raw=self.study_fixture();clock=e.check_clock
        def paused(*args):clock(*args);time.sleep(.05)
        def attempt():
            try:study.record(copy.deepcopy(s),raw,None,{'slippage_reserve_cents':'.5'});return 'committed'
            except ValueError:return 'rejected'
        with patch.object(e,'check_clock',side_effect=paused),ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertEqual(sorted(results),['committed','rejected'])
    def test_study_mid_transaction_crash_preserves_clock_and_slot(self):
        study,s,raw=self.study_fixture()
        with study.db() as db:db.execute("CREATE TRIGGER injected_crash BEFORE INSERT ON markets BEGIN SELECT RAISE(ABORT,'crash'); END")
        with self.assertRaises(sqlite3.IntegrityError):study.record(s,raw,None,{'slippage_reserve_cents':'.5'})
        with study.db() as db:
            self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='last_epoch'").fetchone());self.assertEqual(db.execute('SELECT COUNT(*) FROM markets').fetchone()[0],0)
    def test_atomic_pre_replace_fsync_failure_preserves_file(self):
        dest=self.root/'output';dest.write_text('OLD')
        with patch.object(b.os,'fsync',side_effect=OSError(errno.ENOSPC,'sync full')),self.assertRaises(OSError):b.atomic(dest,'NEW')
        self.assertEqual(dest.read_text(),'OLD');self.assertFalse(list(self.root.glob('.output.*.tmp')))

if __name__=='__main__':
    with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):unittest.main(verbosity=2)
