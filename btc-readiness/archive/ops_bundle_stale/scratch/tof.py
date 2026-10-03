"""Regressions for defects found on 2026-09-27 (Claude session): sleep-induced halts and
collector death on transient non-network failures. Synthetic data only; no network."""
import copy, re, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import runtime_fixture as f
import test_evidence_runner
import evidence_runner as run
from coherent_runtime import EvidenceStore, RuntimeBlocked


def boot_seconds():
    out = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, check=True).stdout
    return time.time() - int(re.search(r'sec = (\d+)', out)[1])


class SleepContinuityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS clock semantics')
    def test_continuity_clock_counts_system_sleep(self):
        # Defining property: elapsed wall time since boot, sleep included.
        # time.monotonic() (mach_absolute_time) omits sleep on this platform.
        self.assertLess(abs(f.b.continuity_clock() - boot_seconds()), 2)

    def test_observation_and_request_timing_use_continuity_clock(self):
        c, _ = f.fixture()
        c.state = {'contracts': {}, 'previous': None}
        # Evidence-collector path (as in test_evidence_runner): a synthetic gate, not the QA launch guard.
        c.collection_guard = lambda: {'fee_verified_at_utc': f.b.stamp(f.NOW)['utc']}
        settings = f.r.load_settings(f.ROOT / 'btc_copilot_settings.json'); settings['fee_verified_at'] = f.b.stamp(f.NOW)['utc']
        ticks = iter(range(5000, 6000))
        with patch.object(f.b.time, 'time', return_value=f.NOW), patch.object(f.b.time, 'monotonic', return_value=1.0), \
             patch.object(f.b, 'continuity_clock', side_effect=lambda: float(next(ticks))), \
             patch.object(f.b, 'spot_get', return_value=f.RAW['spot']), patch.object(f.b, 'load_settings', return_value=settings):
            s = c.collect(persist=False)
        self.assertGreaterEqual(s['observation_monotonic'], 5000)
        for timing in s['retrieval_health']['requests'].values():
            self.assertGreaterEqual(timing['latency_seconds'], 0)

    def publish_pair(self, delta_wall, delta_mono):
        c, s = f.fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); protocol = f.protocol_file(root)
            clock = {'now': f.NOW}
            store = EvidenceStore(root / 'runtime', protocol, s['model_version'], {}, boot_id='synthetic', wall=lambda: clock['now'])
            try:
                c.state = store.state(); store.publish(c, s)
                s2 = f.later(s, delta_wall); s2['observation_monotonic'] = s['observation_monotonic'] + delta_mono
                clock['now'] = s2['epoch']; c.prior = s; c.state = store.state()
                try:
                    return store.publish(c, s2), store.halted
                except Exception:
                    return None, store.halted
            finally:
                store.close(clean=not store.halted)

    def test_sleep_gap_with_sleep_inclusive_clock_does_not_halt(self):
        generation, halted = self.publish_pair(600, 600)
        self.assertEqual(generation, 2); self.assertFalse(halted)

    def test_uptime_clock_during_sleep_would_halt(self):
        # What the old clock produced during a 10-minute sleep: wall +600, monotonic +0.
        self.assertEqual(self.publish_pair(600, 0), (None, True))

    def test_wall_step_without_elapsed_time_still_halts(self):
        # An NTP/manual wall-clock step is still a discontinuity.
        self.assertEqual(self.publish_pair(600, 15), (None, True))


class CollectorResilienceTests(unittest.TestCase):
    def factory(self):
        # Reuse the existing lifecycle fixture (it does not depend on instance state).
        return test_evidence_runner.EvidenceRunnerTests.factory_fixture(None)

    def test_expired_publication_degrades_and_next_poll_recovers(self):
        gate, store, engine, server, kwargs = self.factory()
        store.publish.side_effect = [RuntimeBlocked('Expired or future publication'), 2]
        result = run.run_authorized(f.ROOT, gate, max_cycles=2, **kwargs)
        self.assertEqual(result['cycles'], 2); self.assertEqual(store.publish.call_count, 2)
        store.failure.assert_called_with('COLLECTION_FAILED'); store.close.assert_called_once_with(clean=True)

    def test_malformed_response_value_error_degrades(self):
        gate, store, engine, server, kwargs = self.factory()
        engine.collect.side_effect = [ValueError('synthetic malformed payload'), {}]
        result = run.run_authorized(f.ROOT, gate, max_cycles=2, **kwargs)
        self.assertEqual(result['cycles'], 2); self.assertEqual(store.publish.call_count, 1)
        store.close.assert_called_once_with(clean=True)

    def test_halted_store_is_fatal(self):
        gate, store, engine, server, kwargs = self.factory()
        def halt(*a):
            store.halted = True; raise RuntimeBlocked('Storage limit or free-space reserve reached')
        store.publish.side_effect = halt
        with self.assertRaises(RuntimeBlocked):
            run.run_authorized(f.ROOT, gate, max_cycles=3, **kwargs)
        store.close.assert_called_once_with(clean=False)

    def test_gate_revocation_after_failure_is_fatal(self):
        gate, store, engine, server, kwargs = self.factory()
        context = gate.return_value
        engine.collect.side_effect = ValueError('synthetic')
        calls = {'n': 0}
        def gating(*a):
            calls['n'] += 1
            if calls['n'] > 3: raise RuntimeBlocked('revoked')
            return context
        gate.side_effect = gating
        with self.assertRaises(RuntimeBlocked):
            run.run_authorized(f.ROOT, gate, max_cycles=5, **kwargs)
        store.close.assert_called_once_with(clean=False)

    def test_persistent_failures_stop_for_review(self):
        gate, store, engine, server, kwargs = self.factory()
        engine.collect.side_effect = ValueError('synthetic persistent fault')
        with self.assertRaises(RuntimeBlocked):
            run.run_authorized(f.ROOT, gate, max_cycles=run.MAX_CONSECUTIVE_FAILURES + 5, **kwargs)
        self.assertEqual(engine.collect.call_count, run.MAX_CONSECUTIVE_FAILURES)
        store.close.assert_called_once_with(clean=False)


if __name__ == '__main__':
    with patch.object(socket, 'create_connection', side_effect=AssertionError('QA NETWORK DISABLED')):
        unittest.main(verbosity=2)
