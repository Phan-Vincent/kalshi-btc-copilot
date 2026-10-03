"""Copied into a temporary source-only fixture by run_checks.py."""
import unittest
import test_study002 as fixture
e=fixture.e
ROOT=fixture.ROOT
class LifecycleEvidence(unittest.TestCase):
    setUp=fixture.StudyTests.setUp
    tearDown=fixture.StudyTests.tearDown
    snap=fixture.StudyTests.snap
    record=fixture.StudyTests.record
    def test_missed_checkpoint_not_backfilled_after_restart(self):
        s,raw=self.snap(331);self.record(s,raw)
        self.s=e.Study(self.path,ROOT/'btc_copilot_protocol.json','synthetic-v2',{})
        s,raw=self.snap(400);self.record(s,raw)
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT checkpoint FROM markets').fetchone()[0])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM signals').fetchone()[0],0)
    def test_failed_clock_does_not_reset_baseline_or_evidence(self):
        s,raw=self.snap();self.record(s,raw);before=self.path.read_bytes()
        self.s=e.Study(self.path,ROOT/'btc_copilot_protocol.json','synthetic-v2',{})
        for seconds in [315,330,360]:
            s,raw=self.snap(seconds);s['observation_monotonic']=seconds
            with self.assertRaisesRegex(ValueError,'discontinuity'):self.record(s,raw)
        self.assertEqual(before,self.path.read_bytes())
    def test_unrelated_study001_sentinel_untouched(self):
        other=self.path.with_name('study001.sqlite3');other.write_bytes(b'SYNTHETIC STUDY001')
        s,raw=self.snap();self.record(s,raw)
        self.assertEqual(other.read_bytes(),b'SYNTHETIC STUDY001')
