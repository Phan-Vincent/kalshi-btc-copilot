"""ReleaseGate: host/bundle/manifest-bound approval plus a fresh deterministic attestation."""
import copy, json, os, shutil, socket, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import runtime_fixture as f
import release_gate as g
from coherent_runtime import RuntimeBlocked
from readiness_policy import validate_readiness_protocol

NOW = g.epoch('2026-12-20T12:00:00+00:00', 'now')


def attestation(at='2026-12-20T08:00:00+00:00', **change):
    a = {'fee_verified_at_utc': at, 'rate_verified_at_utc': at, 'clock_verified_at_utc': at,
         'fee_rules_verified': True, 'fee_type': 'quadratic', 'fee_multiplier': '1', 'scheduled_fee_changes': 0,
         'clock_synchronized': True, 'clock_offset_seconds': 0.4, 'read_refill_tokens_per_second': 200,
         'read_bucket_capacity': 600, 'default_read_cost': 10, 'benchmark_read_cost': 50}
    a.update(change); return a


def private(path, data):
    path.write_text(json.dumps(data)); os.chmod(path, 0o600)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); base = Path(self.td.name).resolve()
        self.root = base / 'release'; shutil.copytree(f.ROOT, self.root, ignore=shutil.ignore_patterns('__pycache__', 'runtime'))
        (self.root / 'QA_ONLY').unlink()
        p = json.loads((self.root / 'readiness_protocol.json').read_text())
        p['activation_allowed'] = True  # what a frozen, approved release protocol would say
        (self.root / 'readiness_protocol.json').write_text(json.dumps(p))
        self.protocol = p
        self.attest = base / 'attestation.json'; private(self.attest, attestation())
        self.approval = {'approved': True, 'scope': 'EVIDENCE_ONLY', 'bundle_path': str(self.root), 'host': 'homelab',
                         'study_id': p['study_id'], 'manifest_sha256': g.sha(self.root / 'release_manifest.json'),
                         'approved_at_utc': '2026-12-10T00:00:00+00:00', 'attestation_path': str(self.attest),
                         'credential_config': '/nonexistent/synthetic'}
        self.write_approval()

    def tearDown(self):
        self.td.cleanup()

    def write_approval(self, **change):
        a = dict(self.approval); a.update(change); private(self.root / 'activation_approval.json', a)

    def gate(self, now=NOW, validator=lambda p: copy.deepcopy(p), host='homelab'):
        return g.ReleaseGate(self.root, validator=validator, wall=lambda: now, hostname=lambda: host)()

    def test_bound_release_with_fresh_attestation_opens(self):
        context = self.gate()
        self.assertEqual(context['runtime_dir'], str(self.root / 'runtime'))
        self.assertEqual(context['quota_bytes'], 128 * 1024 ** 3)
        self.assertEqual(context['fee_verified_at_utc'], '2026-12-20T08:00:00+00:00')

    def test_qa_candidate_always_refused(self):
        with self.assertRaisesRegex(RuntimeBlocked, 'QA candidate'):
            g.ReleaseGate(f.ROOT, validator=lambda p: p, wall=lambda: NOW, hostname=lambda: 'homelab')()

    def test_current_readiness_policy_refuses_activation(self):
        # Even with an approval file, today's protocol validator forbids activation.
        with self.assertRaises((RuntimeBlocked, ValueError)):
            self.gate(validator=validate_readiness_protocol)

    def test_binding_mismatches_refused(self):
        for change in ({'host': 'MacBook'}, {'bundle_path': '/elsewhere'}, {'manifest_sha256': '0' * 64},
                       {'study_id': 'other'}, {'approved': 'true'}, {'scope': 'ADVICE'},
                       {'approved_at_utc': '2026-12-15T00:00:00+00:00'}, {'approved_at_utc': '2026-12-10T00:00:00'}):
            with self.subTest(change=change):
                self.write_approval(**change)
                with self.assertRaises(RuntimeBlocked):
                    self.gate()
        self.write_approval()
        with self.assertRaises(RuntimeBlocked):
            self.gate(host='MacBook')

    def test_outside_window_refused(self):
        for now in (g.epoch(self.protocol['start_utc'], 's') - 1, g.epoch(self.protocol['release_utc'], 'r')):
            with self.subTest(now=now), self.assertRaises(RuntimeBlocked):
                self.gate(now=now)

    def test_attestation_expiry_enforced_each_cycle_and_renewal_picked_up(self):
        self.gate(now=NOW)
        later = g.epoch('2026-12-21T08:00:01+00:00', 'later')
        with self.assertRaisesRegex(RuntimeBlocked, 'stale'):
            self.gate(now=later)
        private(self.attest, attestation('2026-12-21T08:00:00+00:00'))
        self.assertEqual(self.gate(now=later)['fee_verified_at_utc'], '2026-12-21T08:00:00+00:00')

    def test_bad_attestations_refused(self):
        for change in ({'fee_verified_at_utc': '2026-12-20T13:00:00+00:00'}, {'fee_type': 'flat'},
                       {'fee_multiplier': '2'}, {'scheduled_fee_changes': 1}, {'fee_rules_verified': 1},
                       {'clock_synchronized': False}, {'clock_offset_seconds': 3}, {'clock_offset_seconds': float('nan')},
                       {'read_refill_tokens_per_second': 100}, {'benchmark_read_cost': 60}, {'default_read_cost': 10.0},
                       {'rate_verified_at_utc': '2026-12-18T00:00:00+00:00'}):
            with self.subTest(change=change):
                private(self.attest, attestation(**change))
                with self.assertRaises((RuntimeBlocked, ValueError)):
                    self.gate()

    def test_group_readable_or_linked_files_refused(self):
        os.chmod(self.attest, 0o644)
        with self.assertRaises(RuntimeBlocked):
            self.gate()
        os.chmod(self.attest, 0o600)
        link = self.attest.with_name('link.json'); link.symlink_to(self.attest)
        self.write_approval(attestation_path=str(link))
        with self.assertRaises(RuntimeBlocked):
            self.gate()
        self.write_approval(); os.chmod(self.root / 'activation_approval.json', 0o640)
        with self.assertRaises(RuntimeBlocked):
            self.gate()

    def test_missing_approval_or_attestation_refused(self):
        self.attest.unlink()
        with self.assertRaises((RuntimeBlocked, OSError)):
            self.gate()
        (self.root / 'activation_approval.json').unlink()
        with self.assertRaises((RuntimeBlocked, OSError)):
            self.gate()


if __name__ == '__main__':
    with patch.object(socket, 'create_connection', side_effect=AssertionError('QA NETWORK DISABLED')):
        unittest.main(verbosity=2)
