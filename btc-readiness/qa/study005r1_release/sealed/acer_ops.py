"""Unattended operations for the evidence-only collector on the Acer host.

    python3 acer_ops.py attest      # deterministic fee/rate/clock check (systemd timer, every 4 h)
    python3 acer_ops.py status      # read-only health report (JSON)
    python3 acer_ops.py watch       # status + Telegram alert on change, daily "alive" summary
    python3 acer_ops.py send-test   # one Telegram test message

No AI agent is involved. All Kalshi access is GET-only. The status reader opens
the evidence database read-only and queries only generation timing, runtime
control and runtime events: never markets, results or outcomes.
"""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse, json, os, sqlite3, subprocess, sys, tempfile, time
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOME = Path.home()
STATE = HOME / 'btc-study' / 'state'
ATTESTATION = STATE / 'attestation.json'
ATTEST_LOG = STATE / 'attest_log.jsonl'
WATCH_STATE = STATE / 'watch_state.json'
ALERT_LOG = STATE / 'alerts.jsonl'
TELEGRAM = HOME / '.config' / 'btc-study' / 'telegram.json'
SERVICE = 'btc-study-collector.service'
TIMER = 'btc-study-collector.timer'
PACIFIC = ZoneInfo('America/Los_Angeles')
SLOT = 900
CHECKPOINT = (300, 330)       # 570-600 s remaining in a 900 s contract
RENEW_WARN_AGE = 20 * 3600
MAX_ATTESTATION_AGE = 86400
CRIT_REPEAT = 6 * 3600
DAILY_HOUR = 9


def utc_now():
    return datetime.now(timezone.utc)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise


def append_private(path, record):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as f:
        f.write(json.dumps(record, sort_keys=True) + '\n')


# ---------------------------------------------------------------- attest

def clock_offset(before, after, server_date):
    """Local-minus-server offset estimate and its half-width, from one request.

    The server stamped its Date header at some local instant in [before, after],
    and the header truncates to whole seconds, so the true server time lies in
    [D, D + 1). Returns (None, None) if the header is missing or unparseable.
    """
    try:
        server = parsedate_to_datetime(server_date).timestamp()
    except (TypeError, ValueError, IndexError):
        return None, None
    if not before <= after:
        return None, None
    return (before + after) / 2 - (server + .5), (after - before) / 2 + .5


def attest(client, *, clock_status, now=time.time):
    """Return (ok, attestation_or_None, reasons). Only a fully passing check renews."""
    reasons = []
    started = now()
    series = client.get('/series/KXBTC15M')['data'].get('series') or {}
    fee_type, multiplier = series.get('fee_type'), str(series.get('fee_multiplier'))
    if fee_type != 'quadratic' or multiplier != '1':
        reasons.append('KXBTC15M fee changed: %s x%s' % (fee_type, multiplier))
    changes = client.get('/series/fee_changes', {'series_ticker': 'KXBTC15M'})['data'].get('series_fee_change_arr')
    if not isinstance(changes, list):
        reasons.append('Fee-change feed malformed')
    elif changes:
        reasons.append('%d scheduled KXBTC15M fee change(s) listed' % len(changes))
    limits = client.get('/account/limits')['data'].get('read') or {}
    costs = client.get('/account/endpoint_costs')['data']
    benchmark = [x.get('cost') for x in costs.get('endpoint_costs', [])
                 if x.get('method') == 'GET' and x.get('path', '').startswith('/trade-api/v2/cfbenchmarks')]
    budget = {'read_refill_tokens_per_second': limits.get('refill_rate'), 'read_bucket_capacity': limits.get('bucket_capacity'),
              'default_read_cost': costs.get('default_cost'), 'benchmark_read_cost': max(benchmark) if benchmark else None}
    if not all(type(v) is int for v in budget.values()) or budget['read_refill_tokens_per_second'] < 200 \
            or budget['read_bucket_capacity'] < 600 or budget['default_read_cost'] != 10 or budget['benchmark_read_cost'] != 50:
        reasons.append('Read budget/costs changed: %s' % budget)
    before = now()
    exchange = client.get('/exchange/status')
    after = now()
    offset, uncertainty = clock_offset(before, after, exchange.get('server_date'))
    # Conservative: the whole possible offset range must lie within 2 s.
    if offset is None or abs(offset) + uncertainty > 2:
        reasons.append('Clock offset vs Kalshi unverified or > 2 s: %s +/- %s' % (offset, uncertainty))
    if clock_status is not True:
        reasons.append('System clock not NTP-synchronized')
    if reasons:
        return False, None, reasons
    stamp = iso(started)
    return True, {'fee_verified_at_utc': stamp, 'rate_verified_at_utc': stamp, 'clock_verified_at_utc': stamp,
                  'fee_rules_verified': True, 'fee_type': 'quadratic', 'fee_multiplier': '1', 'scheduled_fee_changes': 0,
                  'clock_synchronized': True, 'clock_offset_seconds': round(offset, 3),
                  'clock_uncertainty_seconds': round(uncertainty, 3), **budget,
                  'series_last_updated_ts': series.get('last_updated_ts'),
                  'source': 'GET /series/KXBTC15M, /series/fee_changes, /account/limits, /account/endpoint_costs, /exchange/status'}, []


def ntp_synchronized():
    try:
        out = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == 'yes'


def cmd_attest(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kalshi_readonly import KalshiReadOnly
    # The attest reads are GET-only and run before and during activation, so they bypass
    # the collector's launch guard (which would refuse a QA bundle) but keep its allowlist.
    client = KalshiReadOnly(config=args.credential_config, guard=lambda path: None)
    try:
        ok, attestation, reasons = attest(client, clock_status=ntp_synchronized())
    except Exception as error:  # network or parsing: never renew on uncertainty
        ok, attestation, reasons = False, None, ['Attestation read failed: %s' % type(error).__name__]
    append_private(ATTEST_LOG, {'at': utc_now().isoformat(), 'ok': ok, 'reasons': reasons})
    if ok:
        write_private(ATTESTATION, attestation)
    print(json.dumps({'ok': ok, 'reasons': reasons}))
    return 0 if ok else 1


# ---------------------------------------------------------------- status

def service_active():
    try:
        out = subprocess.run(['systemctl', '--user', 'is-active', SERVICE], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return out or 'unknown'


def timer_state():
    """'enabled/active' when the collector timer is armed; otherwise e.g. 'disabled/inactive'."""
    states = []
    for verb in ('is-enabled', 'is-active'):
        try:
            out = subprocess.run(['systemctl', '--user', verb, TIMER], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ''
        states.append(out or 'unknown')
    return '/'.join(states)


def read_runtime(db_path, now):
    """Timing-only view of the evidence store. Never touches outcome tables."""
    uri = 'file:%s?mode=ro' % db_path
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        last = db.execute('SELECT MAX(epoch) FROM generations').fetchone()[0]
        epochs = [row[0] for row in db.execute('SELECT epoch FROM generations WHERE epoch >= ?', (now - 86400,))]
        degraded, reason = db.execute('SELECT degraded, reason FROM runtime_control').fetchone()
        failures = db.execute('SELECT COUNT(*) FROM runtime_events WHERE epoch >= ?', (now - 3600,)).fetchone()[0]
    # Checkpoint coverage over the last 24 completed hours of 15-minute slots.
    first = int(now - 86400) // SLOT * SLOT + SLOT
    slots = range(first, int(now) // SLOT * SLOT, SLOT)
    covered = sum(1 for s in slots if any(s + CHECKPOINT[0] <= e <= s + CHECKPOINT[1] for e in epochs))
    return {'last_generation_age_seconds': None if last is None else round(now - last, 1),
            'observations_24h': len(epochs), 'checkpoint_slots_24h': len(slots), 'checkpoint_slots_covered_24h': covered,
            'degraded': bool(degraded), 'degraded_reason': reason, 'failures_last_hour': failures}


def free_bytes(path):
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize


def release_verification(root):
    """None when the sealed release verifies, or when the manifest positively identifies a working QA
    candidate; otherwise a short reason. A damaged or unreadable manifest, a missing launcher or an
    unexpected QA marker in a sealed release are all reported."""
    root = Path(root)
    try:
        manifest_status = json.loads((root / 'release_manifest.json').read_text()).get('status')
    except (OSError, ValueError, AttributeError):
        return 'release_manifest.json missing or unreadable'
    try:
        protocol_activation = json.loads((root / 'readiness_protocol.json').read_text()).get('activation_allowed')
    except (OSError, ValueError, AttributeError):
        protocol_activation = None
    # Skip only a positively identified working QA candidate: marker, manifest and protocol must all agree.
    if manifest_status == 'WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE' and (root / 'QA_ONLY').is_file() and protocol_activation is False:
        return None
    if not (root / 'launch.py').is_file():
        return 'launch.py missing from a sealed release'
    mode = '--verify-approval' if (root / 'activation_approval.json').exists() else '--verify-only'
    try:
        out = subprocess.run([sys.executable, '-I', '-B', str(root / 'launch.py'), mode],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return 'verification could not run: ' + type(error).__name__
    return None if out.returncode == 0 else ((out.stdout or out.stderr).strip() or 'exit %d' % out.returncode)[:200]


STARTUP_GRACE = 600     # seconds after start_utc before an idle collector is CRIT
ARM_WARN_WINDOW = 86400  # an unarmed timer is WARN until this close to the start, then CRIT


def status(release_root, now=None, service=service_active, verify=None, timer=timer_state):
    now = time.time() if now is None else now
    root = Path(release_root)
    runtime = root / 'runtime'
    report = {'checked_at_utc': iso(now), 'crit': [], 'warn': [], 'info': []}
    approved = (root / 'activation_approval.json').exists() and not (root / 'QA_ONLY').exists()
    report['approved'] = approved
    report['activated'] = approved  # refined below: armed timer before the start, or the start has passed
    try:
        a = json.loads(ATTESTATION.read_text())
        age = now - datetime.fromisoformat(a['fee_verified_at_utc']).timestamp()
        report['attestation_age_hours'] = round(age / 3600, 2)
        if age > MAX_ATTESTATION_AGE:
            report['crit'].append('Fee attestation expired (%.1f h): collector will refuse to collect' % (age / 3600))
        elif age > RENEW_WARN_AGE:
            report['warn'].append('Fee attestation %.1f h old: renewal is failing' % (age / 3600))
    except (OSError, ValueError, KeyError, TypeError):
        (report['crit'] if approved else report['info']).append('No valid fee attestation')
    if not approved:
        report['info'].append('Collector not activated (QA candidate or no approval)')
    problem = (release_verification if verify is None else verify)(root)
    if problem:
        report['crit'].append('Release verification failed: ' + problem)
    # Activated but before the protocol's start: an idle collector and no database are expected.
    try:
        start = datetime.fromisoformat(json.loads((root / 'readiness_protocol.json').read_text())['start_utc']).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        start = None
    waiting = approved and start is not None and now < start
    starting = approved and start is not None and start <= now < start + STARTUP_GRACE
    report['waiting_for_start'] = waiting
    state = service()
    report['service'] = state
    if waiting:
        armed = timer()
        report['collector_timer'] = armed
        report['activated'] = armed == 'enabled/active'
        if armed == 'enabled/active':
            report['info'].append('Activated; collection starts %s' % iso(start))
        else:
            # Approved but not (or no longer) activated: a reminder, escalating close to the start.
            (report['crit'] if start - now <= ARM_WARN_WINDOW else report['warn']).append(
                'Collector timer is %s: run install.sh activate before %s' % (armed, iso(start)))
        if state == 'active':
            report['warn'].append('Collector service is active before the protocol start')
    elif starting:
        # Startup grace: missing data is a WARN (never OK, never CRIT) until the grace ends.
        if state != 'active' or not (runtime / 'evidence.sqlite3').exists():
            report['warn'].append('Collector starting (service %s; grace until %s)' % (state, iso(start + STARTUP_GRACE)))
    if (runtime / 'HALTED').exists():
        report['crit'].append('Runtime HALTED: %s' % (runtime / 'HALTED').read_text()[:80])
    if approved and not waiting and not starting and state != 'active':
        report['crit'].append('Collector service is %s' % state)
    db = runtime / 'evidence.sqlite3'
    if db.exists():
        try:
            r = read_runtime(db, now)
            report.update(r)
            age = r['last_generation_age_seconds']
            if approved and not waiting and not starting and (age is None or age > 300):
                report['crit'].append('No new observation for %s s' % age)
            if r['degraded']:
                report['warn'].append('Degraded: %s' % r['degraded_reason'])
            if r['checkpoint_slots_24h'] and r['checkpoint_slots_covered_24h'] < r['checkpoint_slots_24h']:
                report['warn'].append('Missed %d of %d checkpoint slots in 24 h' % (
                    r['checkpoint_slots_24h'] - r['checkpoint_slots_covered_24h'], r['checkpoint_slots_24h']))
        except sqlite3.Error as error:
            report['warn'].append('Evidence DB unreadable right now: %s' % type(error).__name__)
    elif approved and not waiting and not starting:
        report['crit'].append('No evidence database yet')
    try:
        free = free_bytes(root)
        report['free_gib'] = round(free / 1024 ** 3, 1)
        limits = json.loads((root / 'operating_limits.json').read_text())
        if free < limits['free_reserve_bytes'] * 1.25:
            report['warn'].append('Free space %.1f GiB near reserve' % (free / 1024 ** 3))
    except (OSError, ValueError, KeyError):
        pass
    report['level'] = 'CRIT' if report['crit'] else 'WARN' if report['warn'] else 'OK'
    return report


# ---------------------------------------------------------------- alerts

def telegram_send(text, config_path=TELEGRAM, opener=urlopen):
    info = config_path.stat()
    if info.st_mode & 0o077:
        raise PermissionError('telegram.json must be chmod 600')
    cfg = json.loads(config_path.read_text())
    body = urlencode({'chat_id': cfg['chat_id'], 'text': text[:3500], 'disable_web_page_preview': 'true'}).encode()
    request = Request('https://api.telegram.org/bot%s/sendMessage' % cfg['bot_token'], data=body, method='POST')
    with opener(request, timeout=15) as response:
        if response.status != 200:
            raise OSError('Telegram HTTP %s' % response.status)


def format_report(r, headline):
    lines = [headline]
    lines += ['CRIT: ' + x for x in r['crit']] + ['WARN: ' + x for x in r['warn']]
    if 'observations_24h' in r:
        lines.append('24h: %s observations, %s/%s checkpoint slots' % (
            r['observations_24h'], r['checkpoint_slots_covered_24h'], r['checkpoint_slots_24h']))
    if 'attestation_age_hours' in r:
        lines.append('Fee check age: %s h' % r['attestation_age_hours'])
    lines.append('Service: %s' % r.get('service'))
    return '\n'.join(lines)


def decide(report, previous, now):
    """Return the message to send (or None) and the new watch state."""
    signature = [report['level']] + sorted(report['crit']) + sorted(report['warn'])
    state = dict(previous)
    message = None
    local = datetime.fromtimestamp(now, PACIFIC)
    today = local.date().isoformat()
    if signature != previous.get('signature'):
        was = previous.get('level')
        headline = ('BTC study: RECOVERED' if report['level'] == 'OK' and was in ('WARN', 'CRIT')
                    else 'BTC study: %s' % report['level'])
        if report['level'] != 'OK' or was in ('WARN', 'CRIT'):
            message = format_report(report, headline)
        state.update(signature=signature, level=report['level'], last_alert=now)
    elif report['level'] == 'CRIT' and now - previous.get('last_alert', 0) >= CRIT_REPEAT:
        message = format_report(report, 'BTC study: still CRIT')
        state['last_alert'] = now
    if message is None and local.hour >= DAILY_HOUR and previous.get('daily') != today:
        message = format_report(report, 'BTC study daily check (%s)' % report['level'])
        state['daily'] = today
    elif message is not None and local.hour >= DAILY_HOUR:
        state['daily'] = today
    return message, state


def cmd_watch(args, send=telegram_send):
    now = time.time()
    report = status(args.release_root, now)
    try:
        previous = json.loads(WATCH_STATE.read_text())
    except (OSError, ValueError):
        previous = {}
    message, state = decide(report, previous, now)
    if message:
        try:
            send(message)
            delivered = True
        except Exception as error:
            delivered = False
            state = previous  # retry the same alert next run
            append_private(ALERT_LOG, {'at': iso(now), 'delivered': False, 'error': type(error).__name__, 'message': message})
        if delivered:
            append_private(ALERT_LOG, {'at': iso(now), 'delivered': True, 'message': message})
    write_private(WATCH_STATE, state)
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['attest', 'status', 'watch', 'send-test'])
    parser.add_argument('--release-root', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--credential-config', default=str(HOME / '.config' / 'btc-study' / 'kalshi_readonly_config.json'))
    args = parser.parse_args(argv)
    if args.command == 'attest':
        return cmd_attest(args)
    if args.command == 'status':
        print(json.dumps(status(args.release_root), indent=2)); return 0
    if args.command == 'watch':
        return cmd_watch(args)
    telegram_send('BTC study: Telegram alerts are working (test from %s)' % os.uname().nodename)
    print('sent'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
