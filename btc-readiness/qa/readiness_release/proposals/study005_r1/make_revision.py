"""Build the DRAFT Study 005 revision-1 protocol from the current readiness protocol.

Offline only: reads ../../candidate/readiness_protocol.json and writes a draft next
to this file. Nothing reads the draft; it does not enable activation, advice or
execution (the freeze step flips activation_allowed, never this script).

    python3 -B make_revision.py --start 2026-10-26
    python3 -B make_revision.py --start 2026-10-26 --check   # verify only, write nothing

Every field not listed in CHANGED is copied unchanged, and the script proves that
by comparing a digest of the untouched fields before and after.
"""
import argparse, copy, hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / 'candidate' / 'readiness_protocol.json'
SOURCE_SHA256 = 'b34ce1a40395e3110e6140fb213c450c02f66433ba55b42141a3a4ac4832f6d6'
OUT = HERE / 'readiness_protocol.r1.draft.json'
CANDIDATE_RELEASE = '5.4.1-study005r1'

FEE_ACCEPTANCE = (
    'Balance precision .0001 only, as established from this account\'s own Kalshi fee records (see fee_evidence_policy). '
    'Per consumed displayed level at price p and quantity q, the acceptance fee is the maximum of: (a) legacy conservative '
    'level fees; (b) the unit-split bound: split q into .01 units, ceil each unit model fee to six decimals, ceil each unit '
    'all-in debit to .0001, assume no rebates; (c) the closed-form bound q*(0.07*p*(1-p)+0.0101). Summed over levels. A '
    'change in account precision or classification, or any KXBTC15M fee change, blocks the study.')
FEE_EVIDENCE = (
    'Adopted for this study only, from the account\'s own Kalshi fee records, not a Kalshi classification: GET-only '
    'reconciliation of all KXBTC15M taker orders, re-run within 72 h of sealing and again after the study ends; each run '
    'must show zero orders matching only .01, complete pagination and at least one decisive .0001 match. The sealing-time '
    'summary is archived with the release as fee_evidence_refresh_summary.json. The account was opened '
    'directly on kalshi.com under Kalshi\'s Member Agreement, not through an FCM or IB (account-holder statement). Kalshi '
    'support declined to state the classification (2026-09-30). Tripwire: any order matching only .01, at the freeze, '
    'after the study or before any advice is considered, invalidates this fee assumption; without a passing post-study '
    'refresh the release report leaves fee assumptions unverified. Reconciliation stays offline, taker-only and '
    'excluded from strategy evaluation.')
CLOCK_RESTART = (
    'Persist observation UTC and continuity-clock epochs (CLOCK_BOOTTIME on Linux, CLOCK_MONOTONIC on macOS; '
    'both count through sleep). Reject wall-clock/continuity-clock disagreement beyond tolerance across polls '
    'and restarts, and reject any reboot (boot identity change); no silent reset. A suspend that keeps both '
    'clocks in agreement is a gap handled by the missing-data rule, not a discontinuity.')

CHANGED = ('study_id', 'start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc',
           'candidate_release', 'approval_policy.deadline_utc', 'fee_policy.balance_precisions',
           'fee_policy.acceptance', 'fee_policy.sources', 'fee_evidence_policy', 'clock_policy.restart',
           'profitability_validation_mode')


def utc(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()


def untouched_digest(protocol):
    p = copy.deepcopy(protocol)
    for dotted in CHANGED:
        *parents, leaf = dotted.split('.')
        node = p
        for key in parents:
            node = node[key]
        node.pop(leaf)
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build(current, start):
    days = [start + timedelta(days=n) for n in (0, 7, 14, 70, 72)]  # 7 dev + 7 validation + 56 holdout + 2 wait
    r = copy.deepcopy(current)
    r['study_id'] = 'btc-prospective-005r1-' + start.strftime('%Y%m%d')
    for key, day in zip(('start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc'), days):
        r[key] = utc(day)
    r['candidate_release'] = CANDIDATE_RELEASE
    r['approval_policy']['deadline_utc'] = utc(start - timedelta(days=1))
    f = r['fee_policy']
    f['balance_precisions'] = ['0.0001']
    f['acceptance'] = FEE_ACCEPTANCE
    f['sources'] = f['sources'] + ['fee_evidence_refresh_summary.json (aggregate account fee reconciliation; raw records private)', 'https://www.cftc.gov/sites/default/files/filings/orgrules/25/07/rules07012525155.pdf (KalshiEX Rulebook: Member, Self-Clearing Member, FCM/IB Customer)']
    r['fee_evidence_policy'] = FEE_EVIDENCE
    r['clock_policy']['restart'] = CLOCK_RESTART
    r['profitability_validation_mode'] = 'EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER'
    # Deliberately unchanged here: readiness_status, activation_allowed, advice_allowed, execution_allowed.
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--start', required=True, help='collection start date (UTC midnight), YYYY-MM-DD')
    ap.add_argument('--check', action='store_true', help='verify and print the summary without writing')
    args = ap.parse_args()
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise SystemExit('Source protocol changed since this draft was written; re-review before regenerating')
    current = json.loads(raw)
    start = datetime.strptime(args.start, '%Y-%m-%d').date()
    if start <= datetime.now(timezone.utc).date() + timedelta(days=1):
        raise SystemExit('Start must leave at least one full day for approval before the deadline')
    revised = build(current, start)
    assert untouched_digest(revised) == untouched_digest(current), 'an unlisted field changed'
    assert revised['activation_allowed'] is False and revised['advice_allowed'] is False and revised['execution_allowed'] is False
    summary = {k: revised[k] for k in ('study_id', 'start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc')}
    summary['approval_deadline_utc'] = revised['approval_policy']['deadline_utc']
    summary['untouched_fields_sha256'] = untouched_digest(revised)
    if not args.check:
        OUT.write_text(json.dumps(revised, indent=4, ensure_ascii=False) + '\n')
        summary['written'] = str(OUT.name)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
