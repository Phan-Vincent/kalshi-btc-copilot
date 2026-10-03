"""Study 005 revision 1: .0001-only fee acceptance and the sealed-release activation path.

Synthetic only: no network, credentials, account access or real study databases.
"""
import copy, importlib.util, io, json, os, random, shutil, subprocess, sys, tempfile, unittest, zlib
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import runtime_fixture as f
import btc_copilot_evidence as e
import evidence_runner as run
import readiness_policy as rp
import release_gate as g
import study_policy as policy
from coherent_runtime import RuntimeBlocked
from fee_bound_proposal import taker_buy_fee_bound_0001

HERE = Path(__file__).resolve().parents[1]
ORIGINAL = HERE.parent / 'readiness_release' / 'candidate' / 'study_policy.py'
R1 = json.loads((f.ROOT / 'readiness_protocol.json').read_text())
SETTINGS = {'slippage_reserve_cents': '.5'}
sys.path.insert(0, str(HERE))
import release_tool  # noqa: E402
from datetime import datetime  # noqa: E402
def release_tool_refresh():
    spec = importlib.util.spec_from_file_location('_test_fee_refresh', HERE / 'fee_evidence_refresh.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


EVIDENCE_TIME = datetime.fromisoformat(json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())['retrieved_at'])


def at(days, hours=0, seconds=0):
    return (rp.START + timedelta(days=days, hours=hours, seconds=seconds)).isoformat()


def load_original_policy():
    spec = importlib.util.spec_from_file_location('study_policy_before_r1', ORIGINAL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def books(seed=5, count=300):
    rng = random.Random(seed)
    out = [[[str(D(c) / 100), '1.00']] for c in range(1, 100)]
    for _ in range(count):
        prices = rng.sample(range(1, 100), rng.randint(1, 4))
        out.append([[str(D(c) / 100), str(D(rng.randint(1, 150)) / 100)] for c in prices])
    return out


def legacy_fee(rows):
    """Independent restatement of the legacy term: each consumed level rounded up to whole contracts, cent fee."""
    from decimal import ROUND_CEILING
    levels = sorted((1 - D(p), D(q)) for p, q in rows if D(q)); remaining = D(1); total = D(0)
    for price, q in levels:
        take = min(q, remaining)
        if take:
            total += take.to_integral_value(rounding=ROUND_CEILING) * (D('.07') * price * (1 - price)).quantize(D('.01'), rounding=ROUND_CEILING)
            remaining -= take
        if not remaining:
            break
    return total


class FeeAcceptanceTests(unittest.TestCase):
    def test_default_precisions_reproduce_pre_revision_costs_exactly(self):
        before = load_original_policy()
        for rows in books():
            with self.subTest(rows=rows):
                self.assertEqual(policy.execution_cost(rows, SETTINGS), before.execution_cost(rows, SETTINGS))

    def test_r1_acceptance_is_max_of_unit_split_legacy_and_closed_form(self):
        for rows in books():
            r1 = policy.execution_cost(rows, SETTINGS, ['0.0001'])
            both = policy.execution_cost(rows, SETTINGS)
            if r1 is None:
                self.assertIsNone(both); continue
            with self.subTest(rows=rows):
                self.assertEqual(set(r1['fragmentation_fee_bounds']), {'0.0001'})
                fee = D(r1['acceptance_fee_bound'])
                self.assertGreaterEqual(fee, D(r1['closed_form_fee_bound']))
                self.assertGreaterEqual(fee, D(r1['fragmentation_fee_bounds']['0.0001']))
                terms = [D(r1['closed_form_fee_bound']), D(r1['fragmentation_fee_bounds']['0.0001']), legacy_fee(rows)]
                self.assertEqual(fee, max(terms))
                # Never looser than today except by dropping the .01 worst case; at extreme prices
                # (e.g. a .99 ask) the closed form makes revision 1 slightly stricter than today.
                self.assertLessEqual(fee, max(D(both['acceptance_fee_bound']), D(r1['closed_form_fee_bound'])))
                self.assertEqual(D(r1['stress_cost']) - D(r1['cost']), D('.01'))

    def test_single_level_closed_form_matches_reviewed_proposal(self):
        for cents in range(1, 100):
            ask = D(cents) / 100
            r1 = policy.execution_cost([[str(1 - ask), '1.00']], SETTINGS, ['0.0001'])
            self.assertEqual(D(r1['closed_form_fee_bound']), taker_buy_fee_bound_0001(ask))

    def test_half_dollar_example(self):
        r1 = policy.execution_cost([['.50', '1.00']], SETTINGS, ['0.0001'])
        self.assertEqual(D(r1['acceptance_fee_bound']), D('.0276'))
        self.assertEqual(D(r1['stress_cost']), D('.5426'))
        self.assertEqual(D(policy.execution_cost([['.50', '1.00']], SETTINGS)['acceptance_fee_bound']), D('.5'))

    def test_binding_term_diagnostics(self):
        single = policy.execution_cost([['.50', '1.00']], SETTINGS, ['0.0001'])
        self.assertEqual((single['binding_fee_terms'], single['consumed_levels']), (['closed_form'], 1))
        # Three partial levels: each rounded up to a whole contract under the legacy term.
        thin = policy.execution_cost([['.50', '.34'], ['.49', '.33'], ['.48', '.33']], SETTINGS, ['0.0001'])
        self.assertEqual((thin['binding_fee_terms'], thin['consumed_levels']), (['legacy_level'], 3))
        self.assertEqual(D(thin['acceptance_fee_bound']), D(thin['legacy_level_fee_bound']))
        for rows in books():
            r1 = policy.execution_cost(rows, SETTINGS, ['0.0001'])
            if r1 is None:
                continue
            values = {'closed_form': r1['closed_form_fee_bound'], 'unit_split_0001': r1['fragmentation_fee_bounds']['0.0001'],
                      'legacy_level': r1['legacy_level_fee_bound']}
            with self.subTest(rows=rows):
                self.assertTrue(r1['binding_fee_terms'])
                self.assertEqual(sorted(k for k, v in values.items() if D(v) == D(r1['acceptance_fee_bound'])), r1['binding_fee_terms'])
        # The Study 002 path carries no diagnostics (its stored evidence is unchanged).
        self.assertNotIn('binding_fee_terms', policy.execution_cost([['.50', '1.00']], SETTINGS))

    def test_unsupported_precision_policies_rejected(self):
        for value in (['0.01'], ['0.01', '0.0001'], [], ['0.001'], '0.0001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.execution_cost([['.50', '1.00']], SETTINGS, value)


class StudyUsesProtocolPrecisionTests(unittest.TestCase):
    """The evidence engine takes the fee precision from the protocol it was opened with."""
    def study(self, td):
        sources = {'btc_copilot_evidence.py': e.EVALUATOR_SOURCE}
        for name in ('study_policy.py', 'request_pacing.py', 'fee_reconciliation.py'):
            sources[name] = (f.ROOT / name).read_text()
        return e.Study(Path(td) / 'synthetic.sqlite3', f.ROOT / 'readiness_protocol.json', 'synthetic-v2', sources,
                       validator=rp.validate_readiness_protocol)

    def snap(self, start, t):
        now = start + t
        s = {'ticker': 'SYNTHETIC', 'model_version': 'synthetic-v2', 'epoch': now, 'observation_monotonic': now,
             'open_time': R1['start_utc'], 'close_time': e.datetime.fromtimestamp(start + 900, e.timezone.utc).isoformat(),
             'fee_multiplier': '1', 'shadow_strategies': {'drift_free': {'eligible': True, 'side': 'UP', 'probability': .9}},
             'time_remaining_seconds': 900 - t, 'operational_vetoes': [], 'book': {'yes_bid_dollars': D('.45'), 'yes_ask_dollars': D('.46')},
             'model': {'p_up': .9}, 'drift_free_model': {'p_up': .9}, 'structure': {'sigma_dollars_sqrt_second': 1, 'hour_trend_z': 1},
             'valid_until_epoch': now + 20,
             'retrieval_health': {'requests': {'book': {'started_at_epoch': now - .2, 'finished_at_epoch': now, 'latency_seconds': .2}}}}
        raw = {'book': {'data': {'orderbook_fp': {'yes_dollars': [['.45', '100']], 'no_dollars': [['.54', '100']]}}}}
        return s, raw

    def test_hypothetical_fill_is_costed_with_0001_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            study = self.study(td); start = policy.epoch(R1['start_utc'])
            for t in (300, 315, 330):
                s, raw = self.snap(start, t); study.record(s, raw, None, SETTINGS)
            with study.db() as c:
                status, cost, stress, evidence = c.execute('SELECT status,cost,stress_cost,execution_evidence FROM signals').fetchone()
        self.assertEqual(status, 'hypothetical_fill')
        execution = json.loads(zlib.decompress(evidence))['execution']
        self.assertEqual(execution['balance_precisions'], ['0.0001'])
        self.assertEqual((execution['binding_fee_terms'], execution['consumed_levels']), (['closed_form'], 1))
        # Ask .46: closed form .07*.46*.54+.0101 = .027488 dominates unit-split .02 and legacy .02.
        self.assertEqual(D(execution['acceptance_fee_bound']), D('.027488'))
        self.assertEqual(D(stress), D('.46') + D('.027488') + D('.005') + D('.01'))


def post_study_evidence(in_study=1, **change):
    """The current aggregate summary, re-dated as if refreshed after the study ended, with `in_study`
    extra decisive .0001 orders on a day inside the study window (all totals kept consistent)."""
    data = json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())
    data['retrieved_at'] = at(71)
    if in_study:
        data['comparison_classes']['only_0001'] += in_study; data['btc15m_orders_with_fills'] += in_study
        data['by_period'].setdefault('since_2026-09-27', {}); p = data['by_period']['since_2026-09-27']
        p['only_0001'] = p.get('only_0001', 0) + in_study
        data['decisive_by_utc_day'][at(10)[:10]] = {'only_0001': in_study}
    data.update(change); return data


class ReleaseReportChecksTests(unittest.TestCase):
    """The released report for a revision-1 study: fees need a passing post-study refresh; execution is left to review."""
    def released(self, fee_evidence=None):
        with tempfile.TemporaryDirectory() as td:
            study = StudyUsesProtocolPrecisionTests.study(None, td)
            with patch.object(e.time, 'time', return_value=policy.epoch(R1['release_utc']) + 1):
                return e.report(study.path, policy.epoch(R1['release_utc']), validator=rp.validate_readiness_protocol, fee_evidence=fee_evidence)

    def test_fee_check_needs_passing_post_study_refresh(self):
        report = self.released(post_study_evidence())
        self.assertIs(report['acceptance_checks']['fee_assumptions_verified'], True)
        self.assertEqual(report['fee_evidence']['problems'], [])
        self.assertIn('decisive_0001_orders_during_study', report['fee_evidence'])
        self.assertNotIn('fee_execution_assumptions_verified', report['acceptance_checks'])
        # An empty study still fails on coverage, entries and the rest; the fee check alone never passes it.
        self.assertFalse(report['eligible_for_independent_review'])
        self.assertIs(report['execution_assumptions']['machine_verified'], False)
        self.assertIn('independent reviewer', report['execution_assumptions']['reviewer_must_assess'])

    def test_fee_check_fails_without_or_with_bad_evidence(self):
        self.assertIs(self.released()['acceptance_checks']['fee_assumptions_verified'], False)
        # Owner decision B: no in-study orders still verifies, but is flagged for the reviewer.
        none_in_study = self.released(post_study_evidence(in_study=0))['fee_evidence']
        self.assertIs(none_in_study['verified'], True); self.assertEqual(none_in_study['decisive_0001_orders_during_study'], 0)
        self.assertIn('independent reviewer must weigh', none_in_study['reviewer_must_weigh'])
        self.assertIsNone(self.released(post_study_evidence())['fee_evidence']['reviewer_must_weigh'])
        after_report = self.released(post_study_evidence(retrieved_at=at(73)))['fee_evidence']
        self.assertIn('refresh dated after the report time', after_report['problems'])
        bad = [post_study_evidence(retrieved_at=at(69)),  # before the study ended
               post_study_evidence(series_fee_type='flat'), post_study_evidence(fee_changes_for_series=[{'change': 1}]),
               post_study_evidence(comparison_classes=dict(post_study_evidence()['comparison_classes'], only_01=1)),
               post_study_evidence(pagination_complete=dict(post_study_evidence()['pagination_complete'], **{'/historical/fills': False}))]
        for evidence in bad:
            with self.subTest(evidence=evidence['retrieved_at']):
                self.assertIs(self.released(evidence)['acceptance_checks']['fee_assumptions_verified'], False)

    def test_fee_check_helper(self):
        release = policy.epoch(R1['release_utc'])
        self.assertTrue(e.fee_assumptions_verified(R1, post_study_evidence(), release))
        self.assertFalse(e.fee_assumptions_verified(R1, None, release))
        legacy = json.loads((f.ROOT / 'btc_copilot_protocol.json').read_text())
        self.assertFalse(e.fee_assumptions_verified(legacy, post_study_evidence(), release))
        for change in ({'profitability_validation_mode': 'ENABLED'}, {'fee_policy': dict(R1['fee_policy'], balance_precisions=['0.0001', '0.01'])},
                       {'fee_policy': {}}):
            with self.subTest(change=change):
                self.assertFalse(e.fee_assumptions_verified(dict(R1, **change), post_study_evidence(), release))

    def test_future_report_time_refused(self):
        # No allowance: even one second ahead of the real clock is refused.
        with tempfile.TemporaryDirectory() as td:
            study = StudyUsesProtocolPrecisionTests.study(None, td)
            release = policy.epoch(R1['release_utc'])
            with patch.object(e.time, 'time', return_value=release - 1), self.assertRaisesRegex(ValueError, 'future'):
                e.report(study.path, release, validator=rp.validate_readiness_protocol)
            with patch.object(e.time, 'time', return_value=release - 1):
                self.assertTrue(e.report(study.path, release - 1, validator=rp.validate_readiness_protocol)['holdout_locked'])


class BindingTermReportTests(unittest.TestCase):
    """Development-week tally of which fee term applied, computed without any outcomes."""
    def rows(self, books_by_ticker):
        markets, signals = [], []
        for i, (ticker, book) in enumerate(books_by_ticker):
            markets.append({'ticker': ticker, 'phase': 'development', 'checkpoint': 1.0 + i, 'result': None, 'day': R1['start_utc'][:10],
                            'closed': 1.0 + i, 'features': '{}', 'quarantined': 0, 'confirmed_at': None})
            entry = policy.execution_cost(book, SETTINGS, ['0.0001'])
            signals.append({'ticker': ticker, 'strategy': 'drift_free', 'side': 'UP', 'status': 'hypothetical_fill', 'cost': entry['cost'],
                            'stress_cost': entry['stress_cost'], 'execution_evidence': zlib.compress(json.dumps({'execution': entry}).encode())})
        return markets, signals

    def test_tally_by_term_and_levels(self):
        markets, signals = self.rows([('A', [['.50', '1.00']]), ('B', [['.40', '1.00']]),
                                      ('C', [['.50', '.34'], ['.49', '.33'], ['.48', '.33']])])
        out = e.summarize(markets, signals, R1, 'development')['strategies']
        tally = out['drift_free']['fee_bound_binding']
        self.assertEqual(tally['costed_entries'], 3)
        self.assertEqual(tally['binding_terms'], {'closed_form': 2, 'legacy_level': 1})
        self.assertEqual(tally['consumed_levels'], {'1': 2, '3': 1})
        self.assertNotIn('fee_bound_binding', out['current_full'])

    def test_absent_for_evidence_without_the_field(self):
        markets, signals = self.rows([('A', [['.50', '1.00']])])
        signals[0]['execution_evidence'] = zlib.compress(json.dumps({'execution': policy.execution_cost([['.50', '1.00']], SETTINGS)}).encode())
        self.assertNotIn('fee_bound_binding', e.summarize(markets, signals, R1, 'development')['strategies']['drift_free'])


class SealedReleaseTests(unittest.TestCase):
    """The real activation path: sealed by release_tool, opened by the real validator and verifier."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); base = Path(self.td.name).resolve()
        self.root = base / 'release'
        before = (f.ROOT / 'release_manifest.json').read_bytes()
        release_tool.seal(self.root, now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
        self.assertEqual((f.ROOT / 'release_manifest.json').read_bytes(), before, 'seal must not touch the candidate')
        self.attest = base / 'attestation.json'
        self.private(self.attest, {'fee_verified_at_utc': at(6, 8), 'rate_verified_at_utc': at(6, 8), 'clock_verified_at_utc': at(6, 8),
                                   'fee_rules_verified': True, 'fee_type': 'quadratic', 'fee_multiplier': '1', 'scheduled_fee_changes': 0,
                                   'clock_synchronized': True, 'clock_offset_seconds': 0.4, 'read_refill_tokens_per_second': 200,
                                   'read_bucket_capacity': 600, 'default_read_cost': 10, 'benchmark_read_cost': 50})
        self.approval = {'approved': True, 'scope': 'EVIDENCE_ONLY', 'bundle_path': str(self.root), 'host': 'homelab',
                         'study_id': R1['study_id'], 'manifest_sha256': g.sha(self.root / 'release_manifest.json'),
                         'approved_at_utc': at(-3), 'attestation_path': str(self.attest), 'credential_config': '/nonexistent/synthetic'}
        self.private(self.root / 'activation_approval.json', self.approval)

    def tearDown(self):
        self.td.cleanup()

    @staticmethod
    def private(path, data):
        path.write_text(json.dumps(data)); os.chmod(path, 0o600)

    def gate(self, now=None):
        now = g.epoch(at(6, 12), 'now') if now is None else now
        return g.ReleaseGate(self.root, validator=rp.validate_readiness_protocol, verify=run.verify_release,
                             wall=lambda: now, hostname=lambda: 'homelab')

    def test_seal_output(self):
        manifest = json.loads((self.root / 'release_manifest.json').read_text())
        protocol = json.loads((self.root / 'readiness_protocol.json').read_text())
        self.assertEqual(manifest['status'], run.SEALED_STATUS); self.assertIs(manifest['activation_allowed'], True)
        self.assertEqual((protocol['readiness_status'], protocol['activation_allowed']), ('SEALED_EVIDENCE_ONLY', True))
        self.assertIs(protocol['advice_allowed'], False); self.assertIs(protocol['execution_allowed'], False)
        self.assertFalse((self.root / 'QA_ONLY').exists()); self.assertNotIn('QA_ONLY', manifest['files'])

    def test_real_validator_and_verifier_open_the_sealed_release(self):
        context = self.gate()()
        self.assertEqual(context['runtime_dir'], str(self.root / 'runtime'))
        self.assertEqual(context['fee_verified_at_utc'], at(6, 8))

    def test_run_authorized_accepts_sealed_protocol_with_real_gate(self):
        operating = json.loads((self.root / 'operating_limits.json').read_text())
        store = SimpleNamespace(state=Mock(return_value={'contracts': {}, 'previous': None}), publish=Mock(return_value=1),
                                failure=Mock(), halted=False, settle_one=Mock(return_value=False), close=Mock())
        engine = SimpleNamespace(client=SimpleNamespace(), collect=Mock(return_value={}))
        server = SimpleNamespace(serve_forever=Mock(), shutdown=Mock(), server_close=Mock())
        now = g.epoch(at(6, 12), 'now')
        result = run.run_authorized(self.root, self.gate(now), max_cycles=1, client_factory=Mock(return_value=engine.client),
                                    engine_factory=Mock(return_value=engine), store_factory=Mock(return_value=store),
                                    server_factory=Mock(return_value=server), wall=lambda: now, monotonic=lambda: 1000, sleep=Mock())
        self.assertEqual(result['cycles'], 1); self.assertFalse(result['advice_enabled'])
        self.assertEqual(operating['quota_bytes'], 128 * 1024 ** 3)
        store.close.assert_called_once_with(clean=True)

    def test_tampered_source_blocks_the_next_cycle(self):
        self.gate()()
        with open(self.root / 'btc_copilot.py', 'a') as fh:
            fh.write('\n# tampered\n')
        with self.assertRaisesRegex(RuntimeBlocked, 'inventory mismatch'):
            self.gate()()

    def test_unlisted_module_blocks(self):
        (self.root / 'kalshi_readonly_shadow.py').write_text('')
        with self.assertRaisesRegex(RuntimeBlocked, 'Unlisted'):
            self.gate()()

    def test_protocol_edit_blocks(self):
        p = json.loads((self.root / 'readiness_protocol.json').read_text()); p['advice_allowed'] = True
        (self.root / 'readiness_protocol.json').write_text(json.dumps(p))
        with self.assertRaises(RuntimeBlocked):
            self.gate()()

    def test_qa_marker_blocks(self):
        (self.root / 'QA_ONLY').write_text('')
        with self.assertRaisesRegex(RuntimeBlocked, 'QA'):
            self.gate()()

    def test_approval_after_deadline_or_outside_window_blocks(self):
        # The deadline is START - 1 day. Twelve hours late (still before start) must block;
        # one second before the deadline opens.
        for late in (at(0, 1), at(-1, 12), at(-1)):
            self.private(self.root / 'activation_approval.json', dict(self.approval, approved_at_utc=late))
            with self.subTest(late=late), self.assertRaisesRegex(RuntimeBlocked, 'late'):
                self.gate()()
        self.private(self.root / 'activation_approval.json', dict(self.approval, approved_at_utc=at(-1, 0, -1)))
        self.gate()()
        self.private(self.root / 'activation_approval.json', self.approval)
        for now in (g.epoch(at(0), 's') - 1, g.epoch(at(72), 'r')):
            with self.subTest(now=now), self.assertRaises(RuntimeBlocked):
                self.gate(now)()

    def install_units(self, units):
        units = Path(units); units.mkdir(parents=True, exist_ok=True)
        for unit in run.UNIT_FILES:
            shutil.copy2(self.root / unit, units / unit)
        return units

    def launch(self, *args, units=True):
        # A temporary HOME, so the host's own installed systemd units (if any) never affect the test.
        home = Path(self.td.name) / ('home' if units else 'home_without_units')
        if units:
            self.install_units(home / '.config' / 'systemd' / 'user')
        env = dict(os.environ, HOME=str(home))
        return subprocess.run([sys.executable, '-I', '-B', str(self.root / 'launch.py'), *args],
                              capture_output=True, text=True, timeout=120, cwd=str(self.root), env=env)

    def test_launcher_verifies_sealed_release(self):
        out = self.launch('--verify-only')
        self.assertEqual((out.returncode, out.stdout.strip()), (0, 'VERIFIED'), out.stdout + out.stderr)
        self.assertIn('sealed unit not installed', self.launch('--verify-only', units=False).stdout)

    def test_tampered_source_never_executes(self):
        with open(self.root / 'btc_copilot.py', 'a') as fh:
            fh.write("\nprint('TAMPERED_CODE_RAN')\n")
        out = self.launch('--collect')
        self.assertEqual(out.returncode, 2)
        self.assertNotIn('TAMPERED_CODE_RAN', out.stdout + out.stderr)
        self.assertIn('hash mismatch: btc_copilot.py', out.stdout)

    def test_bytecode_cache_refused_by_launcher_and_gate(self):
        cache = self.root / '__pycache__'; cache.mkdir()
        (cache / 'release_gate.cpython-314.pyc').write_bytes(b'')
        out = self.launch('--collect')
        self.assertEqual(out.returncode, 2); self.assertIn('unlisted: __pycache__', out.stdout)
        with self.assertRaises(RuntimeBlocked):
            self.gate()()

    def test_symlink_and_unapproved_manifest_refused_by_launcher(self):
        (self.root / 'extra.py').symlink_to(self.root / 'launch.py')
        self.assertIn('symlink: extra.py', self.launch('--verify-only').stdout)
        (self.root / 'extra.py').unlink()
        self.private(self.root / 'activation_approval.json', dict(self.approval, manifest_sha256='0' * 64))
        out = self.launch('--collect')
        self.assertEqual(out.returncode, 2); self.assertIn('approval manifest_sha256 does not match this release', out.stdout)

    def test_extra_listed_file_refused(self):
        # An exact reviewed file set: even a correctly hashed, listed extra module is refused.
        (self.root / 'extra.py').write_text('')
        m = json.loads((self.root / 'release_manifest.json').read_text()); m['files']['extra.py'] = g.sha(self.root / 'extra.py')
        (self.root / 'release_manifest.json').write_text(json.dumps(m))
        with self.assertRaisesRegex(RuntimeBlocked, 'reviewed release'):
            run.verify_release(self.root)

    def launcher(self):
        spec = importlib.util.spec_from_file_location('_sealed_launch_%d' % id(self), self.root / 'launch.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

    def test_sealed_units_are_filled_and_bound(self):
        launch_sha = g.sha(self.root / 'launch.py')
        manifest = json.loads((self.root / 'release_manifest.json').read_text())
        for unit in run.UNIT_FILES:
            text = (self.root / unit).read_text()
            self.assertNotIn('__', text.replace('__main__', ''), unit); self.assertIn(unit, manifest['files'])
        for unit in ('btc-study-collector.service', 'btc-study-attest.service'):
            self.assertIn('echo "%s  launch.py" | /usr/bin/sha256sum --check' % launch_sha, (self.root / unit).read_text())
        for unit in ('btc-study-watch.service', 'btc-study-attest.service'):
            self.assertIn('echo "%s  acer_ops.py" | /usr/bin/sha256sum --check' % g.sha(self.root / 'acer_ops.py'), (self.root / unit).read_text())
        self.assertIn('OnCalendar=' + R1['start_utc'].replace('T', ' ').replace('+00:00', ' UTC'), (self.root / 'btc-study-collector.timer').read_text())
        self.assertNotIn('launch.py', (self.root / 'btc-study-watch.service').read_text())  # alerts never depend on the launcher

    def test_watch_reports_release_verification_failure(self):
        import acer_ops
        self.assertIsNone(acer_ops.release_verification(f.ROOT))  # QA candidate: not applicable
        report = acer_ops.status(self.root, g.epoch(at(6, 12), 'n'), service=lambda: 'active', verify=lambda root: 'hash mismatch: x')
        self.assertIn('Release verification failed: hash mismatch: x', report['crit'])
        with open(self.root / 'btc_copilot.py', 'a') as fh:
            fh.write('# drift\n')
        self.assertIn('hash mismatch: btc_copilot.py', acer_ops.release_verification(self.root))

    def test_launcher_checks_approval_binding_and_deadline(self):
        launch = self.launcher(); now = datetime.fromisoformat(at(-2))
        with tempfile.TemporaryDirectory() as units:
            units = self.install_units(units)
            ok = launch.problems(self.root, True, Path(units), 'homelab', now)
            self.assertEqual(ok, [])
            self.assertTrue(launch.problems(self.root, True, Path(units), 'MacBook', now))
            for change in ({'approved_at_utc': at(-1)}, {'scope': 'ADVICE'}, {'bundle_path': '/elsewhere'}, {'study_id': 'other'}):
                self.private(self.root / 'activation_approval.json', dict(self.approval, **change))
                with self.subTest(change=change):
                    self.assertTrue(launch.problems(self.root, True, Path(units), 'homelab', datetime.fromisoformat(at(0))))

    def test_launcher_refuses_installed_units_that_differ(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory() as units:
            units = self.install_units(units)
            self.assertEqual(launch.problems(self.root, False, units), [])
            (units / 'btc-study-collector.service').write_text('[Service]\nExecStart=/bin/true\n')
            self.assertIn('installed unit differs from the sealed release: btc-study-collector.service', launch.problems(self.root, False, units))
            self.install_units(units); (units / 'btc-study-watch.service').unlink()
            self.assertIn('sealed unit not installed: btc-study-watch.service', launch.problems(self.root, False, units))
            self.install_units(units); (units / 'btc-study-extra.service').write_text('')
            self.assertIn('installed unit not in the sealed release: btc-study-extra.service', launch.problems(self.root, False, units))
            self.assertEqual(launch.problems(self.root, False, None), [])  # --verify-files ignores units

    def test_launcher_malformed_manifest_is_a_controlled_refusal(self):
        m = json.loads((self.root / 'release_manifest.json').read_text()); m['files'] = list(m['files'])
        (self.root / 'release_manifest.json').write_text(json.dumps(m))
        out = self.launch('--collect')
        self.assertEqual(out.returncode, 2); self.assertNotIn('Traceback', out.stderr); self.assertIn('no valid file map', out.stdout)

    def test_working_candidate_is_not_a_sealed_release(self):
        with self.assertRaises(RuntimeBlocked):
            run.verify_release(f.ROOT)
        run.verify_candidate(f.ROOT)
        with self.assertRaises(RuntimeBlocked):
            run.verify_candidate(self.root)


class SealHardeningTests(unittest.TestCase):
    def test_seal_refuses_symlinked_candidate_files(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td).resolve() / 'candidate'
            shutil.copytree(f.ROOT, candidate, ignore=shutil.ignore_patterns('__pycache__', 'runtime'))
            (Path(td) / 'outside.py').write_text('INJECTED = True\n')
            (candidate / 'extra_injected.py').symlink_to(Path(td) / 'outside.py')
            with patch.object(release_tool, 'CANDIDATE', candidate), self.assertRaisesRegex(SystemExit, 'links'):
                release_tool.seal(Path(td) / 'sealed', now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
            self.assertFalse((Path(td) / 'sealed').exists())

    def test_seal_verifies_with_the_sealed_copy_not_cached_modules(self):
        # Replacing the in-process verifier must not matter: seal verifies in a fresh interpreter.
        with tempfile.TemporaryDirectory() as td, patch.object(run, 'verify_release', side_effect=AssertionError('cached verifier used')):
            release_tool.seal(Path(td) / 'sealed', now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
            self.assertTrue((Path(td) / 'sealed' / 'release_manifest.json').exists())


class PostStudyPathTests(unittest.TestCase):
    """Release-day path: watch verification, the collector's own database, and the supported command."""
    def test_watch_verification_skips_only_a_working_candidate(self):
        import acer_ops
        self.assertIsNone(acer_ops.release_verification(f.ROOT))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve() / 'sealed'
            release_tool.seal(root, now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
            (root / 'QA_ONLY').write_text('')
            self.assertIn('unlisted: QA_ONLY', acer_ops.release_verification(root))
            (root / 'QA_ONLY').unlink(); (root / 'launch.py').unlink()
            self.assertEqual(acer_ops.release_verification(root), 'launch.py missing from a sealed release')
            (root / 'release_manifest.json').write_text('not json')
            self.assertEqual(acer_ops.release_verification(root), 'release_manifest.json missing or unreadable')

    def test_watch_checks_the_approval_when_one_exists(self):
        import acer_ops
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve() / 'sealed'
            release_tool.seal(root, now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
            home = Path(td) / 'home'; units = home / '.config' / 'systemd' / 'user'; units.mkdir(parents=True)
            for unit in run.UNIT_FILES:
                shutil.copy2(root / unit, units / unit)
            with patch.dict(os.environ, {'HOME': str(home)}):
                self.assertIsNone(acer_ops.release_verification(root))
                # A stale approval (bound to another manifest, e.g. left from a previous release) is reported.
                (root / 'activation_approval.json').write_text(json.dumps({'approved': True, 'manifest_sha256': '0' * 64, 'approved_at_utc': at(-3)}))
                self.assertIn('does not match this release', acer_ops.release_verification(root))

    def test_relabelled_manifest_does_not_silence_the_watch(self):
        import acer_ops
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve() / 'sealed'
            release_tool.seal(root, now=EVIDENCE_TIME + timedelta(hours=1), raw_check=False)
            m = json.loads((root / 'release_manifest.json').read_text()); m['status'] = 'WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE'
            (root / 'release_manifest.json').write_text(json.dumps(m))
            with open(root / 'btc_copilot.py', 'a') as fh:
                fh.write('# drift\n')
            self.assertIsNotNone(acer_ops.release_verification(root))  # no QA marker and protocol is sealed
            (root / 'QA_ONLY').write_text('')
            self.assertIsNotNone(acer_ops.release_verification(root))  # protocol still says activation_allowed: true

    def test_legacy_report_entry_refuses_a_study005_release(self):
        out = subprocess.run([sys.executable, '-B', str(f.ROOT / 'btc_copilot_evidence.py')], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0); self.assertIn('use release_tool.py post-study-report', out.stderr)

    def test_direct_report_entry_needs_the_verified_digest(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'summary.json'; path.write_text(json.dumps(post_study_evidence()))
            for extra in ([], ['--summary-sha256', '0' * 64]):
                with self.subTest(extra=extra):
                    with patch('sys.stdout', new_callable=io.StringIO) as out:
                        self.assertEqual(run.main(['--report', str(path)] + extra), 2)
                    self.assertIn('not the raw-verified snapshot', out.getvalue())

    def test_report_study_reads_the_collectors_database(self):
        from coherent_runtime import EvidenceStore
        sources = {'btc_copilot_evidence.py': e.EVALUATOR_SOURCE}
        for name in ('study_policy.py', 'request_pacing.py', 'fee_reconciliation.py'):
            sources[name] = (f.ROOT / name).read_text()
        start = policy.epoch(R1['start_utc']); release = policy.epoch(R1['release_utc'])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            store = EvidenceStore(root / 'runtime', f.ROOT / 'readiness_protocol.json', 'synthetic-v2', sources,
                                  boot_id='synthetic', wall=lambda: start + 60, validator=rp.validate_readiness_protocol)
            store.close(clean=True)
            with patch.object(e.time, 'time', return_value=release + 1):
                out = run.report_study(root, dict(post_study_evidence(), _input_sha256='a' * 64), now=release)
        self.assertEqual(out['protocol']['study_id'], R1['study_id'])
        self.assertIs(out['acceptance_checks']['fee_assumptions_verified'], True)
        self.assertEqual(len(out['fee_evidence']['summary_sha256']), 64)
        self.assertFalse(out['eligible_for_independent_review'])

    def test_post_study_report_refuses_an_unverified_summary_before_touching_the_host(self):
        run_mock = Mock()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'summary.json'; path.write_text(json.dumps(post_study_evidence()))
            with self.assertRaisesRegex(SystemExit, 'NOT verified against the raw records'):
                release_tool.post_study_report(path, run=run_mock, private=Path(td) / 'empty')
        run_mock.assert_not_called()

    def test_post_study_report_sends_exactly_the_verified_bytes(self):
        sent = {}
        def fake_run(command, **kw):
            if command[0] == 'scp':
                sent['bytes'] = Path(command[2]).read_bytes()
                Path(td, 's.json').write_text('{"changed": true}')  # the original changes after verification
            return SimpleNamespace(returncode=0, stdout='{"report": 1}', stderr='')
        with tempfile.TemporaryDirectory() as td:
            original = b'{"verified": "snapshot"}'; Path(td, 's.json').write_bytes(original)
            run_mock = Mock(side_effect=fake_run)
            with patch.object(release_tool, 'verify_summary', return_value={}) as verify:
                out = release_tool.post_study_report(Path(td) / 's.json', run=run_mock, out_path=Path(td) / 'r.json')
            self.assertEqual(verify.call_args.args[2], original)
            self.assertEqual(sent['bytes'], original)
            self.assertEqual(out.read_text(), '{"report": 1}')
        remote = run_mock.call_args_list[1].args[0][-1]
        self.assertIn('launch.py --verify-only && python3 -B evidence_runner.py --report', remote)
        self.assertIn('--summary-sha256 ' + g.hashlib.sha256(original).hexdigest(), remote)


class FeeEvidenceGateTests(unittest.TestCase):
    """seal refuses unless the account-ledger tripwire passed within 72 hours."""
    def summary_root(self, td, **change):
        data = json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())
        for key, value in change.items():
            data[key] = value
        (Path(td) / 'fee_evidence_refresh_summary.json').write_text(json.dumps(data)); return Path(td)

    def test_current_evidence_passes_the_tripwire(self):
        summary = release_tool.check_fee_evidence(f.ROOT, EVIDENCE_TIME + timedelta(hours=1))
        self.assertEqual(summary['tripwire'], {'only_01_matches': 0, 'passed': True})
        self.assertEqual(summary['comparison_classes'].get('only_01', 0), 0)

    def test_stale_future_or_failed_evidence_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.summary_root(td)
            for now in (EVIDENCE_TIME + timedelta(hours=73), EVIDENCE_TIME - timedelta(minutes=1)):
                with self.subTest(now=now), self.assertRaises(SystemExit):
                    release_tool.check_fee_evidence(root, now)
            for tripwire in ({'only_01_matches': 1, 'passed': False}, {'only_01_matches': 0, 'passed': False}, {'only_01_matches': 1, 'passed': True}):
                root = self.summary_root(td, tripwire=tripwire)
                with self.subTest(tripwire=tripwire), self.assertRaises(SystemExit):
                    release_tool.check_fee_evidence(root, EVIDENCE_TIME + timedelta(hours=1))

    def test_malformed_nested_summary_is_a_problem_not_a_crash(self):
        from fee_reconciliation import fee_evidence_problems
        base = json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())
        for key, value in (('by_period', {'a': None}), ('decisive_by_utc_day', {'d': None}), ('comparison_classes', {'x': -1}),
                           ('pagination_complete', None), ('retrieved_at', 'x'), ('series_fee_multiplier', '2')):
            with self.subTest(key=key):
                self.assertTrue(fee_evidence_problems(dict(base, **{key: value})))

    def test_raw_rederivation_detects_edited_summary_and_files(self):
        refresh = release_tool_refresh()
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            stamp = datetime.fromisoformat(at(-2)).strftime('%Y%m%dT%H%M%SZ')
            names = ('fills_%s.json' % stamp, 'orders_%s.json' % stamp)
            (private / names[0]).write_text('[]'); (private / names[1]).write_text('{}')
            hashes = {n: g.sha(private / n) for n in names}
            summary = refresh.summarize([], {}, at(-2), {'fee_type': 'quadratic', 'fee_multiplier': '1'}, {}, {}, hashes)
            self.assertEqual(refresh.raw_problems(summary, private), [])
            self.assertTrue(refresh.raw_problems(dict(summary, btc15m_fills=1), private))
            self.assertIn('retrieved_at does not match the raw file timestamps', refresh.raw_problems(dict(summary, retrieved_at=at(5)), private))
            (private / names[1]).write_text('{"x": {}}')
            self.assertIn('raw file hash mismatch: ' + names[1], refresh.raw_problems(summary, private))

    def test_daily_counts_must_sit_in_their_period(self):
        from fee_reconciliation import fee_evidence_problems
        base = json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())
        self.assertEqual(fee_evidence_problems(base), [])
        moved = copy.deepcopy(base); days = moved['decisive_by_utc_day']
        early = min(d for d in days if d < '2026-09-27')
        days[early]['only_0001'] -= 1
        if not days[early]['only_0001']:
            del days[early]
        days[at(10)[:10]] = {'only_0001': 1}  # totals preserved, but a pre-study order moved into the study
        self.assertIn('daily totals do not match their periods', fee_evidence_problems(moved))

    def test_inconsistent_summary_refused_even_if_it_claims_to_pass(self):
        base = json.loads((f.ROOT / 'fee_evidence_refresh_summary.json').read_text())
        variants = {'incomplete pagination': {'pagination_complete': dict(base['pagination_complete'], **{'/portfolio/fills': False})},
                    'hidden only_01': {'comparison_classes': dict(base['comparison_classes'], only_01=1)},
                    'missing daily counts': {'decisive_by_utc_day': None},
                    'totals mismatch': {'btc15m_orders_with_fills': base['btc15m_orders_with_fills'] + 1}}
        with tempfile.TemporaryDirectory() as td:
            for name, change in variants.items():
                root = self.summary_root(td, **change)  # the stored tripwire still says passed
                with self.subTest(name=name), self.assertRaisesRegex(SystemExit, 'tripwire failed'):
                    release_tool.check_fee_evidence(root, EVIDENCE_TIME + timedelta(hours=1))

    def test_protocol_describes_the_evidence_honestly(self):
        text = R1['fee_evidence_policy']
        self.assertIn("account's own Kalshi fee records, not a Kalshi classification", text)
        self.assertIn('Tripwire', text)
        self.assertNotIn('confirmed in writing', json.dumps(R1))


class ReportValidatorTests(unittest.TestCase):
    def test_report_accepts_injected_validator(self):
        # The release report must validate a Study 005 protocol with the readiness validator,
        # not Study 002's; the default stays Study 002's for the legacy path.
        import inspect
        self.assertIs(inspect.signature(e.report).parameters['validator'].default, policy.validate_protocol)


if __name__ == '__main__':
    unittest.main(verbosity=2)
