"""Activation gate for an approved, host-bound evidence-only release.

Called before construction and on every collection cycle. Each call re-reads the
approval and the attestation written by `acer_ops.py attest`, so a timer-driven
renewal is picked up without a restart and an expired attestation stops
collection at the next cycle. No AI agent is in this path.

The QA candidate keeps QA_ONLY and a protocol with activation_allowed=false, so
this gate always refuses it. Removing either is the separately approved release
freeze, not something this module does.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, math, socket, time

from coherent_runtime import RuntimeBlocked, no_links

MAX_ATTESTATION_AGE = 86400
APPROVAL = 'activation_approval.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def epoch(value, name):
    if not isinstance(value, str):
        raise RuntimeBlocked(name + ': UTC timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise RuntimeBlocked(name + ': invalid timestamp') from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise RuntimeBlocked(name + ': explicit UTC required')
    return parsed.timestamp()


def read_private_json(path, limit=64 * 1024):
    path = Path(path)
    no_links(path)
    info = path.stat()
    if info.st_size > limit:
        raise RuntimeBlocked(path.name + ': oversized')
    if info.st_mode & 0o077:
        raise RuntimeBlocked(path.name + ': must not be group/world accessible')
    return json.loads(path.read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def check_attestation(a, now):
    """Deterministic fee, rate-budget and clock attestation, at most 24 h old."""
    for field in ('fee_verified_at_utc', 'rate_verified_at_utc', 'clock_verified_at_utc'):
        age = now - epoch(a.get(field), field)
        if not 0 <= age <= MAX_ATTESTATION_AGE:
            raise RuntimeBlocked(field + ': stale or future attestation')
    if a.get('fee_rules_verified') is not True or a.get('fee_type') != 'quadratic' or a.get('fee_multiplier') != '1':
        raise RuntimeBlocked('Fee rules not verified as quadratic x1')
    if a.get('scheduled_fee_changes') != 0:
        raise RuntimeBlocked('Scheduled fee change requires review')
    if a.get('clock_synchronized') is not True:
        raise RuntimeBlocked('Clock not verified as synchronized')
    offset = a.get('clock_offset_seconds')
    if type(offset) not in (int, float) or not math.isfinite(offset) or abs(offset) > 2:
        raise RuntimeBlocked('Clock offset unverified or too large')
    budget = {k: a.get(k) for k in ('read_refill_tokens_per_second', 'read_bucket_capacity', 'default_read_cost', 'benchmark_read_cost')}
    if any(type(v) is not int for v in budget.values()):
        raise RuntimeBlocked('Invalid read budget attestation')
    if budget['read_refill_tokens_per_second'] < 200 or budget['read_bucket_capacity'] < 600 \
            or budget['default_read_cost'] != 10 or budget['benchmark_read_cost'] != 50:
        raise RuntimeBlocked('Read budget or costs changed; review required')
    return a


class ReleaseGate:
    def __init__(self, root, *, validator, verify=None, wall=time.time, hostname=socket.gethostname):
        self.root = Path(root).absolute()
        self.validator = validator
        self.verify = verify
        self.wall = wall
        self.hostname = hostname

    def __call__(self, path=None):
        root, now = self.root, self.wall()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise RuntimeBlocked('Invalid gate clock')
        if (root / 'QA_ONLY').exists():
            raise RuntimeBlocked('NOT ACTIVATED: QA candidate')
        if self.verify is not None:
            self.verify(root)
        protocol = self.validator(json.loads((root / 'readiness_protocol.json').read_text()))
        if protocol.get('activation_allowed') is not True or protocol.get('advice_allowed') is not False \
                or protocol.get('execution_allowed') is not False:
            raise RuntimeBlocked('Protocol does not permit evidence-only activation')
        approval = read_private_json(root / APPROVAL)
        if approval.get('approved') is not True or approval.get('scope') != 'EVIDENCE_ONLY':
            raise RuntimeBlocked('No evidence-only approval')
        if approval.get('bundle_path') != str(root) or approval.get('host') != self.hostname():
            raise RuntimeBlocked('Approval not bound to this bundle and host')
        if approval.get('study_id') != protocol['study_id'] or approval.get('manifest_sha256') != sha(root / 'release_manifest.json'):
            raise RuntimeBlocked('Approval not bound to this release')
        approved = epoch(approval.get('approved_at_utc'), 'approved_at_utc')
        start, release = epoch(protocol['start_utc'], 'start_utc'), epoch(protocol['release_utc'], 'release_utc')
        if not approved <= now or not approved < start:
            raise RuntimeBlocked('Approval too late or future-dated')
        if not start <= now < release:
            raise RuntimeBlocked('Outside authorized collection/reconciliation window')
        attestation = check_attestation(read_private_json(approval['attestation_path']), now)
        operating = json.loads((root / 'operating_limits.json').read_text())
        return {'credential_config': approval['credential_config'], 'runtime_dir': str(root / 'runtime'),
                'quota_bytes': operating['quota_bytes'], 'free_reserve_bytes': operating['free_reserve_bytes'],
                'port': operating['port'], 'fee_verified_at_utc': attestation['fee_verified_at_utc']}
