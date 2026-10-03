"""Synthetic fixtures only. No collector, network, real evidence or account calls."""
import copy,fcntl,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import supervise as s
spec=importlib.util.spec_from_file_location('frozen_policy',s.TARGET/'study_policy.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()/'bundle';self.root.mkdir()
        self.base=Path(self.tmp.name).resolve()/'control';self.base.mkdir()
        self.now=s.START+3600
        self.a={'approved':True,'study_id':'btc-prospective-002','bundle_path':str(self.root),'manifest_sha256':s.MANIFEST,'approved_at_utc':s.stamp(s.START-86400),'scope':'EVIDENCE_ONLY; no profitability validation, advice, orders or account changes','fee_verified_at_utc':s.stamp(self.now-100),'rate_verified_at_utc':s.stamp(self.now-100)}
        self.r={'checked_at_utc':s.stamp(self.now),'rules_checked_at_utc':s.stamp(self.now-100),'clock_query_ok':True,'clock_offset_seconds':.1,'clock_uncertainty_seconds':.1,'official_fee_docs_verified':True,'rounding_rules_verified':True,'series_metadata_verified':True,'account_limits_verified':True,'endpoint_costs_verified':True,'fee_type':'quadratic','coefficient':'0.07','multiplier':'1','minimum_quantity':'0.01','default_cost':10,'benchmark_cost':50,'read_refill':200,'read_capacity':600}
        self.h={'lock_known':True,'lock_held':False,'pid_known':True,'pid_live':False}
    def decision(self,**kw):
        args=dict(a=self.a,state={},health=self.h,receipt=self.r,now=self.now,target=self.root);args.update(kw);return s.assess(**args)
    def put(self,path,data):path.write_text(json.dumps(data))
    def running(self):return dict(self.h,lock_held=True,pid_live=True,pid_matches=True,epoch=self.now-1,expiry=self.now+10,shadow=True,error=False)

class Decisions(Fixtures):
    def test_before_start(self):self.assertEqual(self.decision(now=s.START-.001)['action'],'WAIT')
    def test_approved_start(self):
        self.now=s.START;self.a.update(fee_verified_at_utc=s.stamp(self.now),rate_verified_at_utc=s.stamp(self.now));self.r.update(checked_at_utc=s.stamp(self.now),rules_checked_at_utc=s.stamp(self.now))
        self.assertEqual(self.decision()['action'],'LAUNCH')
    def test_end_no_restart(self):self.assertEqual(self.decision(now=s.END)['action'],'COMPLETE')
    def test_late_start_disclosed(self):self.assertTrue(self.decision()['late_start_or_gap'])
    def test_paused_even_with_bad_approval(self):self.assertEqual(self.decision(a={},state={'paused':True})['action'],'PAUSED')
    def test_approval_binding(self):
        for key,value in [('scope','trade'),('approved',False),('manifest_sha256','x'),('bundle_path','/other'),('approved_at_utc',s.stamp(s.START))]:
            with self.subTest(key=key):self.assertEqual(self.decision(a=dict(self.a,**{key:value}))['action'],'BLOCK')
    def test_attestation_expiry_and_renewal(self):
        for field in ('fee_verified_at_utc','rate_verified_at_utc'):
            for age,action in [(71999,'LAUNCH'),(72000,'VERIFY'),(86400,'VERIFY'),(86401,'VERIFY'),(-1,'VERIFY')]:
                with self.subTest(field=field,age=age):self.assertEqual(self.decision(a=dict(self.a,**{field:s.stamp(self.now-age)}))['action'],action)
    def test_clock_and_rules_receipts(self):
        for change in [{'checked_at_utc':s.stamp(self.now-1201)},{'checked_at_utc':s.stamp(self.now+1)},{'clock_query_ok':False},{'clock_offset_seconds':1},{'clock_uncertainty_seconds':-.1},{'clock_offset_seconds':float('nan')},{'rules_checked_at_utc':s.stamp(self.now-72000)},{'coefficient':'.08'},{'multiplier':'2'},{'read_refill':199},{'read_capacity':599},{'default_cost':11},{'benchmark_cost':51}]:
            with self.subTest(change=change):self.assertEqual(self.decision(receipt=dict(self.r,**change))['action'],'VERIFY')
    def test_invalid_wall_clock(self):
        for now in [float('nan'),float('inf'),None,True]:self.assertEqual(self.decision(now=now)['action'],'BLOCK')
    def test_healthy(self):self.assertEqual(self.decision(health=self.running())['action'],'HEALTHY')
    def test_live_stale_never_duplicate(self):
        for change in [{'epoch':None},{'epoch':self.now-21},{'epoch':self.now+1},{'expiry':self.now},{'expiry':None},{'shadow':False},{'error':True}]:
            with self.subTest(change=change):self.assertEqual(self.decision(health=dict(self.running(),**change))['action'],'ATTENTION')
    def test_identity_conflicts(self):
        for change in [{'lock_known':False},{'lock_held':False},{'pid_matches':False},{'pid_live':False}]:
            with self.subTest(change=change):self.assertEqual(self.decision(health=dict(self.running(),**change))['action'],'BLOCK')
    def test_crash_restart(self):self.assertEqual(self.decision(health=dict(self.h,last_pid_seen=True,pid_known=True))['action'],'LAUNCH')
    def test_unknown_previous_pid(self):self.assertEqual(self.decision(health=dict(self.h,last_pid_seen=True,pid_known=False))['action'],'BLOCK')
    def test_restart_backoff_budget_and_clock_reversal(self):
        for attempts,action in [([self.now-10],'WAIT'),([self.now-900],'LAUNCH'),([self.now-3000,self.now-2000,self.now-1000],'BLOCK'),([self.now+1],'BLOCK'),(['bad'],'BLOCK')]:
            with self.subTest(attempts=attempts):self.assertEqual(self.decision(state={'launch_attempts':attempts})['action'],action)
    def test_offline_25_hours_requires_renewal(self):self.assertEqual(self.decision(now=self.now+90000)['reason'],'expired_attestation')

class LocalControls(Fixtures):
    def test_mutex_excludes_second_supervisor(self):
        with s.mutex(self.base):
            with self.assertRaises(BlockingIOError):
                with s.mutex(self.base):pass
        with s.mutex(self.base):pass
    def test_renew_only_attestations_and_does_not_refresh_rules_from_clock(self):
        self.put(self.root/'activation_approval.json',self.a);self.put(self.base/'preflight_receipt.json',self.r)
        sentinel=self.root/'evidence.sqlite3';sentinel.write_bytes(b'SYNTHETIC DO NOT READ')
        with patch.object(s,'seal'),patch.object(s.time,'time',return_value=self.now):s.renew(self.root,self.base)
        new=json.loads((self.root/'activation_approval.json').read_text())
        for field in ['scope','approved_at_utc','manifest_sha256','bundle_path','approved','study_id']:self.assertEqual(new[field],self.a[field])
        self.assertEqual(new['fee_verified_at_utc'],self.r['rules_checked_at_utc'])
        self.assertEqual(sentinel.read_bytes(),b'SYNTHETIC DO NOT READ')
    def test_failed_renewal_preserves_approval(self):
        self.put(self.root/'activation_approval.json',self.a);before=(self.root/'activation_approval.json').read_bytes()
        self.put(self.base/'preflight_receipt.json',dict(self.r,clock_query_ok=False))
        with patch.object(s,'seal'),patch.object(s.time,'time',return_value=self.now),self.assertRaises(ValueError):s.renew(self.root,self.base)
        self.assertEqual((self.root/'activation_approval.json').read_bytes(),before)
    def test_pause_survives_bad_seal_and_blocks_launch(self):
        with patch.object(s,'CONTROL',self.base),patch.object(s,'TARGET',self.root),patch.object(s,'health',return_value=self.h),patch.object(s,'inspect',side_effect=AssertionError('must not inspect seal')),patch.object(sys,'argv',['supervise','pause']),patch('builtins.print'):s.main()
        state=json.loads((self.base/'control.json').read_text());self.assertTrue(state['paused']);self.assertEqual(self.decision(state=state)['action'],'PAUSED')
    def test_pause_unknown_process_not_killed(self):
        with patch.object(s,'CONTROL',self.base),patch.object(s,'health',return_value=dict(self.h,lock_held=True)),patch.object(s.os,'kill') as kill,patch.object(sys,'argv',['supervise','pause']),self.assertRaises(ValueError):s.main()
        kill.assert_not_called();self.assertTrue(json.loads((self.base/'control.json').read_text())['paused'])
    def test_cli_launch_refuses_prestart_no_spawn(self):
        with patch.object(s,'CONTROL',self.base),patch.object(s,'inspect',return_value=({'action':'WAIT','reason':'before_start'},self.a,{},self.h,self.r)),patch.object(s.subprocess,'Popen') as spawn,patch.object(sys,'argv',['supervise','launch']),self.assertRaises(ValueError):s.main()
        spawn.assert_not_called()
    def test_launch_fixed_command_and_attempt_persisted(self):
        with patch.object(s,'CONTROL',self.base),patch.object(s,'TARGET',self.root),patch.object(s,'inspect',return_value=({'action':'LAUNCH','late_start_or_gap':True},self.a,{},self.h,self.r)),patch.object(s,'event'),patch.object(s.subprocess,'Popen',return_value=Mock(pid=12345)) as spawn,patch.object(sys,'argv',['supervise','launch']),patch('builtins.print'):s.main()
        self.assertEqual(spawn.call_args.args[0],[sys.executable,str(self.root/'btc_copilot.py'),'--watch','--interval','15','--port','8767'])
        self.assertEqual(len(json.loads((self.base/'control.json').read_text())['launch_attempts']),1)
    def test_health_read_allowlist_and_pid_identity(self):
        rt=self.root/'runtime';rt.mkdir();self.put(rt/'btc_copilot_runtime.json',{'pid':12345})
        self.put(self.root/'btc_copilot_latest.json',{'epoch':self.now,'valid_until_epoch':self.now+10,'decision':'NO TRADE','validation':{'status':'UNVALIDATED_SHADOW_ONLY'},'position':{'guidance_valid':False}})
        (rt/'btc_copilot_study.sqlite3').write_bytes(b'UNREAD HOLDOUT SENTINEL')
        allowed={rt/'btc_copilot_runtime.json',self.root/'btc_copilot_latest.json'};realopen=Path.open;opened=[]
        def guard(path,*a,**kw):
            self.assertIn(path,allowed);opened.append(path);return realopen(path,*a,**kw)
        response=Mock(returncode=0,stdout=f'/python {self.root}/btc_copilot.py --watch --interval 15 --port 8767\n')
        with patch.object(Path,'open',guard),patch.object(s.os,'kill'),patch.object(s.subprocess,'run',return_value=response):h=s.health(self.root)
        self.assertTrue(h['pid_matches']);self.assertTrue(h['shadow']);self.assertEqual(set(opened),allowed)
    def test_actual_collector_lock_prevents_duplicate(self):
        rt=self.root/'runtime';rt.mkdir();lock=rt/'btc_copilot.lock'
        with lock.open('w') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            h=s.health(self.root)
            self.assertTrue(h['lock_held']);self.assertEqual(self.decision(health=h)['action'],'BLOCK')
        self.assertFalse(s.health(self.root)['lock_held'])
    def test_verified_pause_signals_only_candidate_after_latch(self):
        h=dict(self.h,pid_live=True,pid_matches=True,lock_held=True,pid=12345)
        def signal_check(pid,sig):
            self.assertEqual(pid,12345);self.assertEqual(sig,s.signal.SIGTERM)
            self.assertTrue(json.loads((self.base/'control.json').read_text())['paused'])
        with patch.object(s,'CONTROL',self.base),patch.object(s,'health',return_value=h),patch.object(s.os,'kill',side_effect=signal_check) as kill,patch.object(sys,'argv',['supervise','pause']),patch('builtins.print'):s.main()
        kill.assert_called_once()
    def test_invalid_pid_and_snapshot_types(self):
        rt=self.root/'runtime';rt.mkdir();self.put(rt/'btc_copilot_runtime.json',{'pid':'invalid'});self.put(self.root/'btc_copilot_latest.json',{'validation':[],'position':[]})
        h=s.health(self.root);self.assertFalse(h['pid_known']);self.assertFalse(h['shadow']);self.assertEqual(self.decision(health=h)['action'],'BLOCK')
    def test_resume_only_clears_latch_does_not_launch(self):
        with patch.object(s,'CONTROL',self.base),patch.object(s,'inspect',return_value=({'action':'PAUSED'},self.a,{'paused':True},self.h,self.r)),patch.object(s.subprocess,'Popen') as spawn,patch.object(sys,'argv',['supervise','resume']),patch('builtins.print'):s.main()
        spawn.assert_not_called();self.assertFalse(json.loads((self.base/'control.json').read_text())['paused'])
    def test_linked_inputs_rejected(self):
        (self.base/'alias').symlink_to(self.root)
        with self.assertRaises(ValueError):
            with s.mutex(self.base/'alias'):pass

class FrozenRules(Fixtures):
    def snapshot(self,now,mono):return {'epoch':now,'observation_monotonic':mono,'valid_until_epoch':now+15,'retrieval_health':{'requests':{'book':{'started_at_epoch':now-1,'finished_at_epoch':now,'latency_seconds':1}}}}
    def test_same_boot_restart_continuity(self):p.check_clock(self.snapshot(self.now+60,1060),self.now,1000)
    def test_reboot_clock_rejected(self):
        with self.assertRaisesRegex(ValueError,'discontinuity'):p.check_clock(self.snapshot(self.now+60,10),self.now,1000)
    def test_sleep_and_ntp_jump_rejected(self):
        for delta,mono in [(3600,1015),(15,1030),(-15,1015)]:
            with self.subTest(delta=delta,mono=mono),self.assertRaises(ValueError):p.check_clock(self.snapshot(self.now+delta,mono),self.now,1000)
    def test_equal_clock_gap_does_not_prove_coverage(self):p.check_clock(self.snapshot(self.now+3600,4600),self.now,1000)
    def test_frozen_launch_gate_real_sources_synthetic_approval(self):
        for name in s.FILES|{'release_manifest.json'}:shutil.copyfile(s.TARGET/name,self.root/name)
        approval=dict(self.a,fee_rules_verified=True,read_refill_tokens_per_second=200,read_bucket_capacity=600,default_read_cost=10,benchmark_read_cost=50)
        self.put(self.root/'activation_approval.json',approval)
        p.launch_guard(self.root,self.now)
        for at in [s.START-1,s.END,self.now+86401]:
            with self.subTest(at=at),self.assertRaises(ValueError):p.launch_guard(self.root,at)
        for field in ['fee_verified_at_utc','rate_verified_at_utc']:
            self.put(self.root/'activation_approval.json',dict(approval,**{field:s.stamp(self.now-86401)}))
            with self.subTest(field=field),self.assertRaises(ValueError):p.launch_guard(self.root,self.now)
        self.assertFalse((self.root/'runtime').exists())

if __name__=='__main__':unittest.main(verbosity=2)
