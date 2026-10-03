import unittest,threading,subprocess,sys
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
import request_pacing as pacing
import kalshi_readonly as transport

class Clock:
    def __init__(self):self.now=0.
    def __call__(self):return self.now
    def sleep(self,n):self.now+=max(n,1e-9)

class PacingTests(unittest.TestCase):
    def setUp(self):self.clock=Clock();self.p=pacing.Pacer(self.clock,self.clock.sleep)
    def test_cold_start_weighted_and_no_idle_burst(self):
        self.p.acquire(50);self.assertGreaterEqual(self.clock.now,50/60-1e-8)
        self.clock.now+=100
        self.assertEqual(self.p.try_acquire(50),0)
        self.assertEqual(self.p.try_acquire(10),0)
        self.assertGreater(self.p.try_acquire(10),0)
    def test_background_batch_and_foreground_share_global_bound(self):
        times=[]
        for _ in range(64):self.p.acquire(10,True);times.append(self.clock.now)
        self.assertGreaterEqual(times[-1]-times[0],31.5-1e-8)
        self.assertLessEqual(self.p.tokens,60)
        self.assertGreaterEqual(self.p.tokens,0)
    def test_same_instant_concurrent_requests_cannot_overspend(self):
        self.clock.now=10;barrier=threading.Barrier(21);results=[]
        def request():barrier.wait();results.append(self.p.try_acquire(10))
        threads=[threading.Thread(target=request) for _ in range(20)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join()
        self.assertEqual(results.count(0),6)
    def test_background_cannot_consume_foreground_capacity_unbounded(self):
        self.clock.now=10
        self.assertEqual(self.p.try_acquire(10,True),0)
        self.assertGreater(self.p.try_acquire(10,True),0)
        self.assertEqual(self.p.try_acquire(50),0)
    def test_429_shared_cooldown_exponential_no_retry(self):
        self.clock.now=10;self.p.throttled()
        self.assertGreaterEqual(self.p.try_acquire(10),2)
        self.p.throttled();self.assertGreaterEqual(self.p.try_acquire(10,True),4)
        for _ in range(9):self.p.throttled()
        self.assertEqual(self.p.cooldown_until-self.clock.now,30)
        with self.assertRaises(pacing.PaceError):self.p.acquire(10)
    def test_oversleep_and_clock_reverse_fail_closed(self):
        def oversleep(n):self.clock.now+=6
        self.p.sleep=oversleep
        with self.assertRaises(pacing.PaceError):self.p.acquire(50)
        self.clock.now=-1
        with self.assertRaises(pacing.PaceError):self.p.try_acquire(10)
    def test_invalid_cost_and_background_cost(self):
        for cost in (0,-1,61,float('nan'),float('inf'),True):
            with self.assertRaises(pacing.PaceError):self.p.try_acquire(cost)
        with self.assertRaises(pacing.PaceError):self.p.try_acquire(50,True)
    def test_invalid_wait_deadline(self):
        for wait in (-1,float('nan'),float('inf'),6,True):
            with self.assertRaises(pacing.PaceError):self.p.acquire(10,max_wait=wait)
    def test_lane_context_restored_after_exception(self):
        with self.assertRaises(ValueError):
            with pacing.background_reads():
                self.assertTrue(pacing.BACKGROUND.get());raise ValueError()
        self.assertFalse(pacing.BACKGROUND.get())
    def test_all_gets_paced_before_auth_and_guard_rechecked(self):
        c=transport.KalshiReadOnly();events=[]
        c._headers=lambda p:events.append('auth') or {}
        class Opener:
            def open(self,*args,**kwargs):events.append('network');raise HTTPError('https://test',429,'throttle',{},None)
        c.opener=Opener()
        with patch.object(pacing,'before_read',side_effect=lambda p:events.append('pace')),patch('study_policy.launch_guard',side_effect=lambda *a:events.append('guard')),patch.object(pacing,'on_throttled',side_effect=lambda:events.append('429')):
            with self.assertRaises(transport.ReadError):c.get('/cfbenchmarks/values')
        self.assertEqual(events,['pace','guard','auth','network','429'])
    def test_pacing_failure_never_signs_or_dispatches(self):
        c=transport.KalshiReadOnly();c._headers=lambda p:self.fail('signed')
        with patch.object(pacing,'before_read',side_effect=pacing.PaceError('expired')):
            with self.assertRaises(pacing.PaceError):c.get('/cfbenchmarks/values')
    def test_global_singleton_shared_by_multiple_clients(self):
        p=self.p;calls=[]
        class Opener:
            def open(self,*a,**k):calls.append(self_clock.now);raise HTTPError('https://test',403,'denied',{},None)
        self_clock=self.clock
        with patch.object(pacing,'GLOBAL_PACER',p),patch('study_policy.launch_guard',return_value=None):
            for _ in range(7):
                c=transport.KalshiReadOnly();c.opener=Opener()
                with self.assertRaises(transport.ReadError):c.get('/exchange/status')
        self.assertGreaterEqual(calls[-1],70/60-1e-8)
    def test_launch_interval_rejected_before_approval_or_runtime(self):
        root=Path(__file__).resolve().parent
        for interval in ('5','14','16','nan','inf'):
            run=subprocess.run([sys.executable,str(root/'btc_copilot.py'),'--interval',interval],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0);self.assertIn('requires exactly 15 seconds',run.stderr)
        self.assertFalse((root/'runtime').exists())

if __name__=='__main__':unittest.main()
