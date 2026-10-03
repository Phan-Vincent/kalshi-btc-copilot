import json
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


class ReasonixOpsFindingTests(unittest.TestCase):
    """Reproductions of the independent Reasonix operations review (2026-09-28)."""

    def store(self, td, clock):
        c, s = f.fixture()
        root = Path(td).resolve(); protocol = f.protocol_file(root)
        return c, s, root, EvidenceStore(root / 'runtime', protocol, s['model_version'], {}, boot_id='synthetic', wall=lambda: clock['now'])

    def test_R1_sleep_during_request_degrades_not_halts(self):
        with tempfile.TemporaryDirectory() as td:
            clock = {'now': f.NOW}
            c, s, root, store = self.store(td, clock)
            try:
                s['retrieval_health']['requests']['book'] = {'started_at_epoch': s['epoch'] - 16.2, 'finished_at_epoch': s['epoch'] - .2, 'latency_seconds': 16.0}
                clock['now'] = s['epoch']
                with self.assertRaises(ValueError):
                    store.publish(c, s)
                self.assertFalse(store.halted); self.assertFalse((root / 'runtime/HALTED').exists())
            finally:
                store.close(clean=not store.halted)

    def test_R1_request_wall_step_still_halts(self):
        with tempfile.TemporaryDirectory() as td:
            clock = {'now': f.NOW}
            c, s, root, store = self.store(td, clock)
            try:
                s['retrieval_health']['requests']['book'] = {'started_at_epoch': s['epoch'] - 10.2, 'finished_at_epoch': s['epoch'] - .2, 'latency_seconds': .5}
                clock['now'] = s['epoch']
                with self.assertRaises(ValueError):
                    store.publish(c, s)
                self.assertTrue(store.halted)
            finally:
                store.close(clean=not store.halted)

    def settle(self, mono, wall_advance):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            clock = {'now': f.NOW}
            c, s, root, store = self.store(td, clock)
            try:
                c.state = store.state(); store.publish(c, s)
                clock['now'] = f.b.dt(s['close_time']).timestamp() + 61
                def market(ticker):
                    clock['now'] += wall_advance
                    return {'data': {'market': {'ticker': ticker, 'status': 'finalized', 'result': 'yes'}}}
                with patch.object(f.b, 'continuity_clock', side_effect=mono):
                    try:
                        outcome = store.settle_one(SimpleNamespace(market=market))
                    except Exception as error:
                        outcome = type(error).__name__
                return outcome, store.halted
            finally:
                store.close(clean=not store.halted)

    def test_R2_sleep_during_settlement_read_does_not_halt(self):
        self.assertEqual(self.settle([1000.0, 1016.0], 16), (True, False))

    def test_R2_long_sleep_during_settlement_read_degrades(self):
        self.assertEqual(self.settle([1000.0, 1300.0], 300), ('RuntimeBlocked', False))

    def test_R2_settlement_wall_step_still_halts(self):
        self.assertEqual(self.settle([1000.0, 1000.1], 16), ('RuntimeBlocked', True))

    def factory(self):
        return test_evidence_runner.EvidenceRunnerTests.factory_fixture(None)

    def test_R3_transient_settlement_failure_degrades(self):
        gate, store, engine, server, kwargs = self.factory()
        store.settle_one.side_effect = [RuntimeBlocked('Settlement ticker mismatch'), False]
        result = run.run_authorized(f.ROOT, gate, max_cycles=2, **kwargs)
        self.assertEqual(result['cycles'], 2); self.assertEqual(store.settle_one.call_count, 2)
        store.close.assert_called_once_with(clean=True)

    def test_R4_wall_step_between_polls_is_unclean_stop(self):
        gate, store, engine, server, kwargs = self.factory()
        release = run.epoch(run.validate_readiness_protocol(json.loads((f.ROOT/'readiness_protocol.json').read_text()))['release_utc'])
        walls = iter([release - 3600, release + 3600])
        kwargs['wall'] = lambda: next(walls)
        with self.assertRaises(RuntimeBlocked):
            run.run_authorized(f.ROOT, gate, max_cycles=5, **kwargs)
        store.close.assert_called_once_with(clean=False)

    def test_R5_integrity_mismatch_halts_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            clock = {'now': f.NOW}
            c, s, root, store = self.store(td, clock)
            try:
                c.state = store.state(); store.publish(c, s)
                with store._connect() as db:
                    db.execute("UPDATE publication SET state='{}'")
                with self.assertRaises(RuntimeBlocked):
                    store.state()
                self.assertTrue(store.halted); self.assertTrue((root / 'runtime/HALTED').exists())
            finally:
                store.close(clean=not store.halted)

    def test_R6_external_halt_file_is_fatal_at_once(self):
        gate, store, engine, server, kwargs = self.factory()
        with tempfile.TemporaryDirectory() as td:
            store.root = Path(td); (store.root / 'HALTED').write_text('operator')
            engine.collect.side_effect = RuntimeBlocked('Runtime halted')
            with self.assertRaises(RuntimeBlocked):
                run.run_authorized(f.ROOT, gate, max_cycles=10, **kwargs)
            self.assertEqual(engine.collect.call_count, 1)
