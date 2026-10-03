"""Offline operating-envelope tests. Child processes touch temporary files only."""
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
sys.path.insert(0,str(AUDIT));sys.argv.insert(1,'candidate')
import remediation_audit as a
import copy,errno,json,os,sqlite3,socket,subprocess,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import closing
b=a.b
import study_policy as policy

class OperatingBounds(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.raw=copy.deepcopy(a.RAW);self.raw['trades']['data']={'trades':[]}
        self.s=a.collect(self.raw);self.c=a.make(self.raw);self.c.state_path=self.root/'state.json';self.c.raw=self.raw;self.c.prior=None;self.c.settings=a.SETTINGS
        self.work=patch.object(b,'WORK',self.root);self.work.start();self.published=patch.object(b,'ROOT',self.root);self.published.start()
    def tearDown(self):self.published.stop();self.work.stop();self.temp.cleanup()
    def stores(self):
        return SimpleNamespace(record=lambda *args:None,scorecard=lambda:{}),SimpleNamespace(record=lambda *args:None,status=lambda *args:{},last_report=a.NOW)
    def test_expiry_after_study_storage_rejects_before_publication(self):
        audit,study=self.stores();clock=[a.NOW]
        study.record=lambda *args:clock.__setitem__(0,self.s['valid_until_epoch'])
        with patch.object(b.time,'time',side_effect=lambda:clock[0]):
            b.Copilot.record(self.c,self.s)
            with self.assertRaises(a.ReadError):b.persist_observation(self.c,audit,study,copy.deepcopy(self.s))
        state=json.loads(self.c.state_path.read_text())
        self.assertFalse(state['previous']['confirmation_valid'])
        self.assertFalse(state['last_observation']['committed'])
        self.assertFalse((self.root/'btc_copilot_latest.json').exists())
    def test_expiry_during_publication_prevents_confirmation_commit(self):
        audit,study=self.stores();clock=[a.NOW];real=b.atomic
        def slow(path,value):
            result=real(path,value)
            if Path(path).name=='btc_copilot_latest.json':clock[0]=self.s['valid_until_epoch']
            return result
        with patch.object(b.time,'time',side_effect=lambda:clock[0]):
            b.Copilot.record(self.c,self.s)
            with patch.object(b,'atomic',side_effect=slow),self.assertRaises(a.ReadError):b.persist_observation(self.c,audit,study,copy.deepcopy(self.s))
        self.assertFalse(json.loads(self.c.state_path.read_text())['previous']['confirmation_valid'])
    def child(self,boundary):
        (self.root/'fixture.json').write_text(b.dump(self.s))
        code=r'''
import sys,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,sys.argv[1]);sys.argv=['child','candidate'];import remediation_audit as a
b=a.b;root=Path(os.environ['QA_TEMP']);s=json.loads((root/'fixture.json').read_text());raw=a.copy.deepcopy(a.RAW);raw['trades']['data']={'trades':[]}
c=a.make(raw);c.state_path=root/'state.json';c.raw=raw;c.prior=None;c.settings=a.SETTINGS
boundary=os.environ['QA_BOUNDARY'];atomic=b.atomic;append=b.append;calls=[0]
def crash_atomic(path,value):
    calls[0]+=1
    if boundary=='after_log' and calls[0]==2:os._exit(91)
    result=atomic(path,value)
    if boundary=='after_json' and Path(path).name=='btc_copilot_latest.json':os._exit(91)
    return result
def crash_append(path,value):
    if boundary=='after_pending':os._exit(91)
    return append(path,value)
with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')),patch.object(b,'ROOT',root),patch.object(b,'WORK',root),patch.object(b.time,'time',return_value=a.NOW),patch.object(b,'atomic',side_effect=crash_atomic),patch.object(b,'append',side_effect=crash_append):
    b.Copilot.record(c,s)
    if boundary=='after_audit':
        audit=a.r.Audit(root/'audit.sqlite3');audit.record(s,raw,None,a.SETTINGS,root/'absent');os._exit(91)
    audit=SimpleNamespace(record=lambda *args:None,scorecard=lambda:{})
    study=SimpleNamespace(record=lambda *args:None,status=lambda *args:{},last_report=a.NOW)
    b.persist_observation(c,audit,study,s)
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(AUDIT)],env=dict(os.environ,QA_TEMP=str(self.root),QA_BOUNDARY=boundary),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,91,result.stderr)
        state=json.loads(self.c.state_path.read_text());self.assertFalse(state['previous']['confirmation_valid']);self.assertFalse(state['last_observation']['committed'])
        return state
    def test_abrupt_exit_after_pending_recovers_without_duplicate_count(self):
        self.child('after_pending');snapshot=json.loads((self.root/'fixture.json').read_text());b.Copilot.record(self.c,snapshot)
        self.assertEqual(self.c.state['contracts'][snapshot['ticker']]['observations'],1);self.assertFalse(self.c.state['previous']['confirmation_valid'])
    def test_abrupt_exit_after_append_repeats_diagnostic_with_stable_identity(self):
        self.child('after_log');snapshot=json.loads((self.root/'fixture.json').read_text());b.Copilot.record(self.c,snapshot)
        rows=[json.loads(x) for x in (self.root/'btc_copilot_observations.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows),2);self.assertEqual(self.c.state['contracts'][snapshot['ticker']]['observations'],1)
        self.assertTrue(all(row['_observation_digest']==b.observation_digest(snapshot) for row in rows))
    def test_abrupt_exit_after_json_leaves_partial_but_nonconfirming_publication(self):
        (self.root/'btc_copilot_latest.txt').write_text('OLDER NO TRADE')
        self.child('after_json');snapshot=json.loads((self.root/'btc_copilot_latest.json').read_text())
        self.assertEqual(snapshot['decision'],'NO TRADE');self.assertEqual((self.root/'btc_copilot_latest.txt').read_text(),'OLDER NO TRADE')
    def test_abrupt_exit_after_audit_commit_keeps_pending_state(self):
        self.child('after_audit')
        with closing(sqlite3.connect(self.root/'audit.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
    def test_abrupt_exit_mid_sqlite_transaction_rolls_back(self):
        path=self.root/'transaction.sqlite3'
        with closing(sqlite3.connect(path)) as db:db.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,value TEXT)')
        code="import sqlite3,os,sys;c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE');c.execute(\"INSERT INTO evidence VALUES (1,'partial')\");os._exit(92)"
        result=subprocess.run([sys.executable,'-B','-c',code,str(path)],timeout=5);self.assertEqual(result.returncode,92)
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],0)
    def clock_snapshot(self,epoch,mono):
        return {'epoch':epoch,'observation_monotonic':mono,'valid_until_epoch':epoch+20,'retrieval_health':{'requests':{'book':{'started_at_epoch':epoch-.2,'finished_at_epoch':epoch,'latency_seconds':.2}}}}
    def test_reboot_monotonic_reset_fails_closed(self):
        with self.assertRaises(ValueError):policy.check_clock(self.clock_snapshot(1015,10),1000,500)
    def test_suspend_wall_only_jump_fails_closed(self):
        with self.assertRaises(ValueError):policy.check_clock(self.clock_snapshot(1300,501),1000,500)
    def test_consistent_long_pause_records_gap_not_clock_reset(self):
        policy.check_clock(self.clock_snapshot(1300,800),1000,500)
        audit=a.r.Audit(self.root/'retention.sqlite3');s=copy.deepcopy(self.s);audit.record(s,self.raw,None,a.SETTINGS,self.root/'none')
        s['epoch']+=300;audit.record(s,self.raw,None,a.SETTINGS,self.root/'none')
        with audit.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM gaps').fetchone()[0],1)
    def test_repeated_disk_full_never_advances_confirmation(self):
        before=copy.deepcopy(self.c.state)
        for _ in range(3):
            with patch.object(b,'atomic',side_effect=OSError(errno.ENOSPC,'full')),self.assertRaises(OSError):b.Copilot.record(self.c,self.s)
        self.assertEqual(self.c.state,before);self.assertFalse(self.c.state_path.exists())
    def test_replay_retention_prunes_only_declared_observation_window(self):
        audit=a.r.Audit(self.root/'retention.sqlite3');old=copy.deepcopy(self.s);old['epoch']-=float(a.SETTINGS['replay_retention_days'])*86400+1
        audit.record(old,self.raw,None,a.SETTINGS,self.root/'none');audit.record(self.s,self.raw,None,a.SETTINGS,self.root/'none')
        with audit.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM implementations').fetchone()[0],1)

    def test_expiry_while_waiting_for_commit_lock_is_rejected(self):
        from contextlib import contextmanager
        real_lock=b.output_lock;clock=[a.NOW]
        with patch.object(b.time,'time',side_effect=lambda:clock[0]):
            b.Copilot.record(self.c,self.s)
            @contextmanager
            def delayed_lock(path):
                with real_lock(path):
                    clock[0]=self.s['valid_until_epoch']
                    yield
            with patch.object(b,'output_lock',side_effect=delayed_lock),self.assertRaises(a.ReadError):b.Copilot.commit_observation(self.c,self.s)
        state=json.loads(self.c.state_path.read_text())
        self.assertFalse(state['previous']['confirmation_valid'])
        self.assertFalse(state['last_observation']['committed'])

    def test_preserved_positive_price_cost_bound_cannot_make_positive_return(self):
        from decimal import Decimal as D
        for cents in range(1,100):
            price=D(cents)/100
            cost=policy.execution_cost([[str(1-price),'1']],{'slippage_reserve_cents':'.5'})
            self.assertGreaterEqual(D(cost['cost']),D('1.005'))
            self.assertGreaterEqual(D(cost['stress_cost']),D('1.015'))
            self.assertLess(D(1)-D(cost['stress_cost']),0)

if __name__=='__main__':
    with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):unittest.main(verbosity=2)
