"""acer_ops: deterministic attestation, read-only status, Telegram alert decisions. No network."""
import copy, json, os, socket, sqlite3, tempfile, unittest
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import runtime_fixture as f
import acer_ops as ops
import release_gate as g
from coherent_runtime import EvidenceStore

T = 1797768000.0  # 2026-12-20T12:00:00Z


class FakeClient:
    def __init__(self, **change):
        self.data = {'series': {'series': {'fee_type': 'quadratic', 'fee_multiplier': 1, 'last_updated_ts': '2026-09-18T15:20:18Z'}},
                     'changes': {'series_fee_change_arr': []},
                     'limits': {'read': {'refill_rate': 200, 'bucket_capacity': 600}},
                     'costs': {'default_cost': 10, 'endpoint_costs': [
                         {'method': 'GET', 'path': '/trade-api/v2/cfbenchmarks', 'cost': 50},
                         {'method': 'GET', 'path': '/trade-api/v2/cfbenchmarks/*endpoint', 'cost': 50}]},
                     'date': formatdate(T, usegmt=True)}
        for key, value in change.items():
            self.data[key] = value
        self.paths = []

    def get(self, path, params=None):
        self.paths.append(path)
        key = {'/series/KXBTC15M': 'series', '/series/fee_changes': 'changes', '/account/limits': 'limits',
               '/account/endpoint_costs': 'costs'}.get(path)
        if path == '/exchange/status':
            return {'data': {}, 'server_date': self.data['date']}
        return {'data': copy.deepcopy(self.data[key])}


class AttestTests(unittest.TestCase):
    def run_attest(self, client=None, clock=True, now=T + 0.4):
        return ops.attest(client or FakeClient(), clock_status=clock, now=lambda: now)

    def test_passing_attestation_satisfies_release_gate(self):
        ok, a, reasons = self.run_attest()
        self.assertTrue(ok, reasons)
        self.assertEqual(g.check_attestation(a, T + 60), a)

    def test_only_get_allowlisted_routes(self):
        client = FakeClient(); self.run_attest(client)
        self.assertEqual(client.paths, ['/series/KXBTC15M', '/series/fee_changes', '/account/limits', '/account/endpoint_costs', '/exchange/status'])

    def test_any_change_blocks_renewal(self):
        cases = {
            'fee type': FakeClient(series={'series': {'fee_type': 'flat', 'fee_multiplier': 1}}),
            'multiplier': FakeClient(series={'series': {'fee_type': 'quadratic', 'fee_multiplier': 2}}),
            'scheduled change': FakeClient(changes={'series_fee_change_arr': [{'series_ticker': 'KXBTC15M'}]}),
            'malformed changes': FakeClient(changes={}),
            'refill': FakeClient(limits={'read': {'refill_rate': 100, 'bucket_capacity': 600}}),
            'default cost': FakeClient(costs={'default_cost': 12, 'endpoint_costs': [{'method': 'GET', 'path': '/trade-api/v2/cfbenchmarks', 'cost': 50}]}),
            'benchmark cost': FakeClient(costs={'default_cost': 10, 'endpoint_costs': [{'method': 'GET', 'path': '/trade-api/v2/cfbenchmarks', 'cost': 60}]}),
            'no benchmark cost': FakeClient(costs={'default_cost': 10, 'endpoint_costs': []}),
            'clock offset': FakeClient(date=formatdate(T - 5, usegmt=True)),
            'no date': FakeClient(date=None),
        }
        for name, client in cases.items():
            with self.subTest(name):
                ok, a, reasons = self.run_attest(client)
                self.assertFalse(ok); self.assertIsNone(a); self.assertTrue(reasons)
        ok, a, reasons = self.run_attest(clock=False)
        self.assertFalse(ok)

    def test_slow_earlier_requests_do_not_skew_clock_check(self):
        # Live regression (2026-09-28): four reads took ~2.1 s before /exchange/status,
        # and the old check compared the first request's start with the last Date header.
        ticks = iter([T, T + 2.1, T + 2.3])
        client = FakeClient(date=formatdate(T + 2, usegmt=True))
        ok, a, reasons = ops.attest(client, clock_status=True, now=lambda: next(ticks))
        self.assertTrue(ok, reasons)
        self.assertLessEqual(abs(a['clock_offset_seconds']) + a['clock_uncertainty_seconds'], 2)

    def test_clock_offset_bounds(self):
        date = formatdate(T, usegmt=True)
        offset, half = ops.clock_offset(T + .2, T + .4, date)
        self.assertAlmostEqual(offset, -.2, places=5); self.assertAlmostEqual(half, .6, places=5)
        self.assertEqual(ops.clock_offset(T + 1, T, date), (None, None))
        self.assertEqual(ops.clock_offset(T, T + 1, 'not a date'), (None, None))
        # A 4 s request is too uncertain to certify a 2 s bound even with a perfect clock.
        ticks = iter([T, T, T + 4])
        ok, _, _ = ops.attest(FakeClient(date=formatdate(T + 2, usegmt=True)), clock_status=True, now=lambda: next(ticks))
        self.assertFalse(ok)

    def test_failed_or_erroring_attest_never_renews(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            with patch.multiple(ops, STATE=state, ATTESTATION=state / 'attestation.json', ATTEST_LOG=state / 'log.jsonl'):
                ops.write_private(ops.ATTESTATION, {'fee_verified_at_utc': 'old'})
                before = ops.ATTESTATION.read_bytes()
                broken = Mock(get=Mock(side_effect=OSError('network down')))
                with patch('kalshi_readonly.KalshiReadOnly', return_value=broken), patch.object(ops, 'ntp_synchronized', return_value=True):
                    code = ops.cmd_attest(SimpleNamespace(credential_config='/nonexistent'))
                self.assertEqual(code, 1)
                self.assertEqual(ops.ATTESTATION.read_bytes(), before)
                self.assertFalse(json.loads(ops.ATTEST_LOG.read_text().splitlines()[-1])['ok'])
                self.assertEqual(oct(os.stat(ops.ATTESTATION).st_mode & 0o777), '0o600')


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); base = Path(self.td.name).resolve()
        self.root = base / 'release'; self.root.mkdir()
        (self.root / 'operating_limits.json').write_text((f.ROOT / 'operating_limits.json').read_text())
        self.state = base / 'state'
        self.patch = patch.multiple(ops, STATE=self.state, ATTESTATION=self.state / 'attestation.json')
        self.patch.start()
        # Report ample free space regardless of the test machine's temp volume.
        self.disk = patch.object(ops, 'free_bytes', return_value=10 ** 12)
        self.disk.start()

    def tearDown(self):
        self.disk.stop(); self.patch.stop(); self.td.cleanup()

    def test_low_free_space_warns(self):
        self.now = T
        with patch.object(ops, 'free_bytes', return_value=3 * 1024 ** 3):
            r = ops.status(self.root, T, service=lambda: 'inactive')
        self.assertIn('near reserve', ' '.join(r['warn']))

    def activate(self, attested_hours_ago=1):
        (self.root / 'activation_approval.json').write_text('{}')
        ops.write_private(ops.ATTESTATION, {'fee_verified_at_utc': ops.iso(self.now - attested_hours_ago * 3600)})

    def publish(self, count=1):
        c, s = f.fixture()
        protocol = f.protocol_file(self.root.parent)
        store = EvidenceStore(self.root / 'runtime', protocol, s['model_version'], {}, boot_id='synthetic', wall=lambda: s['epoch'])
        try:
            c.state = store.state(); store.publish(c, s)
        finally:
            store.close()
        return s['epoch']

    def test_not_activated_is_ok_with_info(self):
        self.now = T
        r = ops.status(self.root, T, service=lambda: 'inactive')
        self.assertEqual(r['level'], 'OK'); self.assertFalse(r['activated'])

    def test_activated_but_service_down_is_crit(self):
        self.now = T; self.activate()
        r = ops.status(self.root, T, service=lambda: 'failed')
        self.assertEqual(r['level'], 'CRIT'); self.assertTrue(any('service' in x for x in r['crit']))

    def test_attestation_age_levels(self):
        self.now = T
        for hours, level in ((1, None), (21, 'WARN'), (25, 'CRIT')):
            with self.subTest(hours=hours):
                self.activate(hours)
                r = ops.status(self.root, T, service=lambda: 'active')
                text = ' '.join(r['crit'] + r['warn'])
                if level is None:
                    self.assertNotIn('attestation', text)
                elif level == 'WARN':
                    self.assertIn('renewal is failing', ' '.join(r['warn']))
                else:
                    self.assertIn('expired', ' '.join(r['crit']))

    def test_halted_marker_is_crit(self):
        self.now = T
        (self.root / 'runtime').mkdir(); (self.root / 'runtime' / 'HALTED').write_text('CLOCK_DISCONTINUITY')
        r = ops.status(self.root, T, service=lambda: 'inactive')
        self.assertEqual(r['level'], 'CRIT'); self.assertIn('CLOCK_DISCONTINUITY', r['crit'][0])

    def test_reads_generation_timing_and_stale_is_crit(self):
        epoch = self.publish(); self.now = epoch + 10; self.activate()
        r = ops.status(self.root, epoch + 10, service=lambda: 'active')
        self.assertEqual(r['observations_24h'], 1); self.assertEqual(r['last_generation_age_seconds'], 10.0)
        self.assertNotIn('No new observation', ' '.join(r['crit']))
        r = ops.status(self.root, epoch + 400, service=lambda: 'active')
        self.assertIn('No new observation', ' '.join(r['crit']))

    def test_status_never_queries_outcome_tables_and_is_read_only(self):
        epoch = self.publish(); self.now = epoch
        statements = []
        real = sqlite3.connect
        def traced(*a, **k):
            conn = real(*a, **k); conn.set_trace_callback(statements.append); return conn
        before = (self.root / 'runtime' / 'evidence.sqlite3').read_bytes()
        with patch.object(ops.sqlite3, 'connect', side_effect=traced):
            ops.status(self.root, epoch, service=lambda: 'active')
        sql = ' '.join(statements).lower()
        self.assertTrue(statements)
        for table in ('markets', 'outcome', 'signals', 'checkpoints', 'observations', 'publication'):
            self.assertNotRegex(sql, r'\b%s\b' % table)
        self.assertEqual((self.root / 'runtime' / 'evidence.sqlite3').read_bytes(), before)


class DecideTests(unittest.TestCase):
    def report(self, level='OK', crit=(), warn=()):
        return {'level': level, 'crit': list(crit), 'warn': list(warn), 'service': 'active'}

    def at(self, pacific_hour, day=20):
        # December: Pacific = UTC-8.
        return g.epoch('2026-12-%02dT%02d:00:00+00:00' % (day + (pacific_hour + 8) // 24, (pacific_hour + 8) % 24), 't')

    def test_first_ok_before_daily_hour_is_silent(self):
        message, state = ops.decide(self.report(), {}, self.at(7))
        self.assertIsNone(message); self.assertEqual(state['level'], 'OK')

    def test_problem_alerts_once_then_repeats_after_six_hours(self):
        crit = self.report('CRIT', ['Collector service is failed'])
        m1, s1 = ops.decide(crit, {'signature': ['OK'], 'level': 'OK', 'daily': '2026-12-20'}, self.at(10))
        self.assertIn('BTC study: CRIT', m1)
        m2, s2 = ops.decide(crit, s1, self.at(11))
        self.assertIsNone(m2)
        m3, s3 = ops.decide(crit, s2, self.at(16, 20) + 1)
        self.assertIn('still CRIT', m3)

    def test_recovery_is_announced(self):
        _, s = ops.decide(self.report('WARN', warn=['Degraded: X']), {'daily': '2026-12-20'}, self.at(10))
        m, _ = ops.decide(self.report(), s, self.at(10) + 300)
        self.assertIn('RECOVERED', m)

    def test_daily_alive_summary_once_per_pacific_day(self):
        m1, s1 = ops.decide(self.report(), {'signature': ['OK'], 'level': 'OK'}, self.at(9))
        self.assertIn('daily check', m1)
        m2, s2 = ops.decide(self.report(), s1, self.at(15))
        self.assertIsNone(m2)
        m3, _ = ops.decide(self.report(), s2, self.at(9, 21))
        self.assertIn('daily check', m3)

    def test_failed_delivery_is_retried(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            with patch.multiple(ops, STATE=state, WATCH_STATE=state / 'watch.json', ALERT_LOG=state / 'alerts.jsonl'), \
                    patch.object(ops, 'status', return_value=self.report('CRIT', ['Runtime HALTED: X'])):
                send = Mock(side_effect=OSError('offline'))
                ops.cmd_watch(SimpleNamespace(release_root='/unused'), send=send)
                ops.cmd_watch(SimpleNamespace(release_root='/unused'), send=send)
                self.assertEqual(send.call_count, 2)
                self.assertTrue(all(not json.loads(x)['delivered'] for x in (state / 'alerts.jsonl').read_text().splitlines()))


class TelegramTests(unittest.TestCase):
    def config(self, td, mode=0o600):
        path = Path(td) / 'telegram.json'
        path.write_text(json.dumps({'bot_token': 'SYNTHETIC', 'chat_id': '42'})); os.chmod(path, mode)
        return path

    def test_refuses_readable_config(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PermissionError):
                ops.telegram_send('x', self.config(td, 0o644), opener=Mock())

    def test_posts_message(self):
        with tempfile.TemporaryDirectory() as td:
            response = Mock(status=200); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
            opener = Mock(return_value=response)
            ops.telegram_send('hello', self.config(td), opener=opener)
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.telegram.org/botSYNTHETIC/sendMessage')
            self.assertIn(b'chat_id=42', request.data); self.assertIn(b'text=hello', request.data)


if __name__ == '__main__':
    with patch.object(socket, 'create_connection', side_effect=AssertionError('QA NETWORK DISABLED')):
        unittest.main(verbosity=2)
