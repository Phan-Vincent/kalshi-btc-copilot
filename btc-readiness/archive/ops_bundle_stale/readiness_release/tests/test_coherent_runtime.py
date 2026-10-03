import copy,json,os,socket,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from types import SimpleNamespace
import runtime_fixture as f
from coherent_runtime import EvidenceStore,RuntimeBlocked

class CoherentRuntime(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.protocol=f.protocol_file(self.root)
  self.c,self.s=f.fixture();self.now=f.NOW
  self.store=self.open()
 def open(self,**kw):return EvidenceStore(self.root/'runtime',self.protocol,self.s['model_version'],{},boot_id='synthetic-boot',wall=lambda:self.now,quota_bytes=16*1024**2,free_reserve_bytes=1024**2,**kw)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def counts(self):
  with closing(sqlite3.connect(self.store.path)) as c:return tuple(c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ('observations','markets','generations','publication'))
 def test_one_generation_uses_real_audit_study_and_same_commit(self):
  traced=[]
  self.store.publish(self.c,self.s,interrupt=lambda stage:traced.append(stage))
  self.assertEqual(self.counts(),(1,1,1,1));self.assertEqual(traced,['after_audit','after_study','before_commit','after_commit'])
  self.assertTrue(self.store.state()['last_observation']['committed']);self.assertEqual(self.store.latest()['generation'],1)
  self.assertEqual(self.store.latest()['snapshot']['study']['markets_seen'],1)
  self.assertFalse((self.root/'runtime/btc_copilot_state.json').exists())
 def test_every_precommit_failure_rolls_back_real_audit_and_study(self):
  for stage in ('after_audit','after_study','before_commit'):
   with self.subTest(stage=stage):
    def interrupt(at):
     if at==stage:raise OSError('synthetic write failure')
    with self.assertRaises(OSError):self.store.publish(self.c,self.s,interrupt=interrupt)
    self.assertEqual(self.counts(),(0,0,0,0));self.assertIsNone(self.store.state()['previous'])
    self.assertEqual(self.store.latest()['status'],'DATA UNAVAILABLE')
  self.store.publish(self.c,self.s);self.assertEqual(self.counts(),(1,1,1,1))
 def test_commit_before_process_memory_failure_recovers_exactly_once(self):
  def fail(stage):
   if stage=='after_commit':raise SystemExit('synthetic abrupt termination')
  with self.assertRaises(SystemExit):self.store.publish(self.c,self.s,interrupt=fail)
  self.store.close(clean=False)
  with self.assertRaisesRegex(RuntimeBlocked,'Unclean'):self.open()
  self.assertEqual(self.counts(),(1,1,1,1))
 def test_duplicate_identical_inputs_idempotent(self):
  self.store.publish(self.c,self.s);self.assertEqual(self.store.publish(self.c,self.s),1);self.assertEqual(self.counts(),(1,1,1,1))
 def test_same_epoch_changed_raw_rejected_and_publication_degraded(self):
  self.store.publish(self.c,self.s);self.c.raw['book']['data']['extra']='changed'
  with self.assertRaisesRegex(RuntimeBlocked,'Conflicting'):self.store.publish(self.c,self.s)
  self.assertEqual(self.counts(),(1,1,1,1));self.assertEqual(self.store.latest()['status'],'DATA UNAVAILABLE')
 def test_failed_collection_invalidates_confirmation_then_recovers(self):
  self.store.publish(self.c,self.s);self.store.failure('COLLECTION_FAILED')
  self.assertFalse(self.store.state()['previous']['confirmation_valid'])
  self.c.state=self.store.state();self.c.prior=copy.deepcopy(self.c.state['previous']);self.now+=15
  self.store.publish(self.c,f.later(self.s));self.assertEqual(self.store.latest()['generation'],2)
 def test_stale_future_nan_and_malformed_publication_fail_closed(self):
  for delta in (-1,20):
   self.now=f.NOW+delta
   with self.assertRaises(RuntimeBlocked):self.store.publish(self.c,self.s)
   self.assertEqual(self.counts(),(0,0,0,0))
  self.now=float('nan')
  with self.assertRaises(RuntimeBlocked):self.store.publish(self.c,self.s)
 def test_expiry_at_commit_rolls_everything_back(self):
  def expire(stage):
   if stage=='before_commit':self.now+=21
  with self.assertRaises(RuntimeBlocked):self.store.publish(self.c,self.s,interrupt=expire)
  self.assertEqual(self.counts(),(0,0,0,0))
 def test_reader_never_presents_expired_data(self):
  self.store.publish(self.c,self.s);self.now=self.s['valid_until_epoch'];self.assertEqual(self.store.latest()['status'],'DATA UNAVAILABLE')
 def test_clock_discontinuity_is_persistent_halt(self):
  self.store.publish(self.c,self.s);self.now+=15;s=f.later(self.s);s['observation_monotonic']+=10
  with self.assertRaises(ValueError):self.store.publish(self.c,s)
  self.assertTrue((self.root/'runtime/HALTED').exists());self.store.close()
  with self.assertRaises(RuntimeBlocked):self.open()
 def test_reboot_cannot_silently_reset_monotonic_history(self):
  self.store.publish(self.c,self.s);self.store.close()
  with self.assertRaises(RuntimeBlocked):EvidenceStore(self.root/'runtime',self.protocol,self.s['model_version'],{},boot_id='different',wall=lambda:self.now,quota_bytes=16*1024**2,free_reserve_bytes=1024**2)
  self.assertTrue((self.root/'runtime/HALTED').exists())
 def test_same_boot_restart_preserves_evidence(self):
  self.store.publish(self.c,self.s);before=self.store.state();self.store.close();self.store=self.open();self.assertEqual(self.store.state(),before)
 def test_duplicate_writer_process_cannot_open(self):
  with self.assertRaisesRegex(RuntimeBlocked,'writer'):self.open()
  self.assertFalse((self.root/'runtime/HALTED').exists());self.store.publish(self.c,self.s)
 def test_quota_limit_keeps_existing_evidence_and_halts(self):
  self.store.publish(self.c,self.s);before=self.store.path.read_bytes()
  with patch('coherent_runtime.shutil.disk_usage',return_value=SimpleNamespace(free=0)),self.assertRaises(RuntimeBlocked):self.store.publish(self.c,self.s)
  self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(self.store.latest()['status'],'DATA UNAVAILABLE')
 def test_sqlite_page_limit_enforced_without_deleting_rows(self):
  self.store.publish(self.c,self.s)
  with self.store._connect() as db:
   before=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
   with self.assertRaises(sqlite3.DatabaseError):
    db.execute('BEGIN IMMEDIATE');db.execute('INSERT INTO implementations VALUES (?,?)',('overflow',b'x'*(8*1024**2)))
   if db.in_transaction:db.rollback()
   self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],before)
 def test_corrupted_publication_detected_on_read_and_restart(self):
  self.store.publish(self.c,self.s)
  with closing(sqlite3.connect(self.store.path)) as db, db:db.execute("UPDATE publication SET state='{}'")
  self.assertEqual(self.store.latest()['status'],'DATA UNAVAILABLE');self.store.close()
  with self.assertRaises(RuntimeBlocked):self.open()
 def test_symlink_and_hardlink_rejected(self):
  self.store.close();target=self.root/'sentinel';target.write_bytes(b'KEEP')
  self.store.path.unlink();self.store.path.symlink_to(target)
  with self.assertRaises(RuntimeBlocked):self.open()
  self.store.path.unlink();os.link(target,self.store.path)
  with self.assertRaises(RuntimeBlocked):self.open()
  self.assertEqual(target.read_bytes(),b'KEEP')
 def test_no_legacy_retention_delete_in_coherent_store(self):
  self.assertTrue(self.store.audit.retain_all)
  self.store.publish(self.c,self.s)
  with self.store._connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
 def test_abrupt_process_exit_at_each_transaction_boundary(self):
  for stage in ('after_audit','after_study','before_commit','after_commit'):
   with self.subTest(stage=stage):
    root=self.root/('crash-'+stage)
    store=EvidenceStore(root,self.protocol,self.s['model_version'],{},boot_id='synthetic',wall=lambda:self.now)
    child=os.fork()
    if child==0:
     def stop(at):
      if at==stage:os._exit(77)
     try:store.publish(self.c,self.s,interrupt=stop)
     except BaseException:os._exit(99)
     os._exit(98)
    _,status=os.waitpid(child,0);store.close(clean=False)
    self.assertEqual(os.waitstatus_to_exitcode(status),77)
    with self.assertRaisesRegex(RuntimeBlocked,'Unclean'):
     EvidenceStore(root,self.protocol,self.s['model_version'],{},boot_id='synthetic',wall=lambda:self.now)
    # QA-only crash recovery on the temporary database. Production requires
    # operator review before any writer resumes or clears its session marker.
    with closing(sqlite3.connect(root/'evidence.sqlite3')) as db:
     count=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
     self.assertEqual(count,1 if stage=='after_commit' else 0)
     self.assertEqual(db.execute('SELECT COUNT(*) FROM publication').fetchone()[0],count)
 def test_settlement_crash_after_get_never_postpones_retry(self):
  self.store.publish(self.c,self.s);self.now=f.b.dt(self.s['close_time']).timestamp()+61
  client=SimpleNamespace(market=lambda ticker:{'data':{'market':{'ticker':ticker,'status':'finalized','result':'yes'}}})
  def fail(stage):
   if stage=='after_get':raise SystemExit('crash')
  with self.assertRaises(SystemExit):self.store.settle_one(client,interrupt=fail)
  with self.store._connect() as db:
   self.assertEqual(db.execute('SELECT settlement_attempt,result FROM markets').fetchone(),(0,None))
  self.assertTrue(self.store.settle_one(client))
  with self.store._connect() as db:self.assertEqual(db.execute('SELECT result FROM markets').fetchone(),(1,))
  self.assertFalse(self.store.settle_one(client))
 def test_settlement_transaction_interruption_rolls_back_attempt_and_outcome(self):
  self.store.publish(self.c,self.s);self.now=f.b.dt(self.s['close_time']).timestamp()+61
  client=SimpleNamespace(market=lambda ticker:{'data':{'market':{'ticker':ticker,'status':'finalized','result':'no'}}})
  def fail(stage):
   if stage=='before_settlement_commit':raise OSError('disk full')
  with self.assertRaises(OSError):self.store.settle_one(client,interrupt=fail)
  with self.store._connect() as db:
   self.assertEqual(db.execute('SELECT settlement_attempt,result FROM markets').fetchone(),(0,None))
   self.assertEqual(db.execute('SELECT COUNT(*) FROM outcome_events').fetchone()[0],0)
 def test_settlement_amendment_quarantined_original_preserved(self):
  self.store.publish(self.c,self.s);self.now=f.b.dt(self.s['close_time']).timestamp()+61
  result=['yes'];client=SimpleNamespace(market=lambda ticker:{'data':{'market':{'ticker':ticker,'status':'finalized','result':result[0]}}})
  self.store.settle_one(client);self.now+=21601;result[0]='no';self.store.settle_one(client)
  with self.store._connect() as db:
   self.assertEqual(db.execute('SELECT result,quarantined FROM markets').fetchone(),(1,1))
   self.assertEqual(db.execute('SELECT COUNT(*) FROM outcome_events').fetchone()[0],2)
 def test_network_failure_and_wrong_ticker_never_fabricate_result(self):
  self.store.publish(self.c,self.s);self.now=f.b.dt(self.s['close_time']).timestamp()+61
  def fail(ticker):raise TimeoutError('synthetic timeout')
  for client in (SimpleNamespace(market=fail),SimpleNamespace(market=lambda ticker:{'data':{'market':{'ticker':'OTHER','status':'finalized','result':'yes'}}})):
   with self.assertRaises((TimeoutError,RuntimeBlocked)):self.store.settle_one(client)
   with self.store._connect() as db:self.assertEqual(db.execute('SELECT settlement_attempt,result FROM markets').fetchone(),(0,None))
 def test_current_generation_survives_conflicting_concurrent_writer(self):
  import threading
  self.store.publish(self.c,self.s);self.now+=15;started=threading.Event();finish=threading.Event();errors=[]
  def pause(stage):
   if stage=='after_audit':started.set();finish.wait(3)
  def worker():
   try:self.store.publish(self.c,f.later(self.s),interrupt=pause)
   except Exception as error:errors.append(error)
  thread=threading.Thread(target=worker);thread.start();self.assertTrue(started.wait(2))
  try:
   with self.assertRaisesRegex(RuntimeBlocked,'Concurrent'):self.store.publish(self.c,f.later(self.s))
  finally:finish.set();thread.join(4)
  self.assertEqual(errors,[]);self.assertEqual(self.counts(),(2,1,2,1))
 def test_failed_halt_marker_write_still_blocks_restart(self):
  self.store.publish(self.c,self.s)
  original=os.open
  def unavailable(path,*args,**kw):
   if str(path).endswith('/HALTED'):raise OSError('synthetic full volume')
   return original(path,*args,**kw)
  with patch('coherent_runtime.os.open',side_effect=unavailable):self.store._halt('STORAGE_LIMIT')
  self.assertFalse((self.root/'runtime/HALTED').exists());self.store.close()
  with self.assertRaisesRegex(RuntimeBlocked,'Unclean'):self.open()
 def test_private_or_directional_output_rejected(self):
  for key,value in [('decision','UP'),('entry',{'ask':'.5'})]:
   s=copy.deepcopy(self.s);s[key]=value
   with self.assertRaises(RuntimeBlocked):self.store.publish(self.c,s)
  self.c.positions=True
  with self.assertRaises(RuntimeBlocked):self.store.publish(self.c,self.s)
 def test_stale_analysis_state_rejected(self):
  self.store.publish(self.c,self.s);self.now+=15;self.c.state={'contracts':{},'previous':None}
  with self.assertRaisesRegex(RuntimeBlocked,'durable state'):self.store.publish(self.c,f.later(self.s))

if __name__=='__main__':
 with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):unittest.main(verbosity=2)
