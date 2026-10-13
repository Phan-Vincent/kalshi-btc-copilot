"""Build the DRAFT Study 005 revision-2 protocol from the sealed revision-1 protocol.

Offline only: reads ../../../study005r1_release/sealed/readiness_protocol.json and writes
a draft next to this file. Nothing reads the draft; it does not enable activation,
advice or execution (the freeze step sets the release status, never this script).

    python3 -B make_revision.py --start 2026-10-26
    python3 -B make_revision.py --start 2026-10-26 --check   # verify only, write nothing

Every field not listed in CHANGED is copied unchanged, and the script proves that
by comparing a digest of the untouched fields before and after.
"""
import argparse, copy, hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2] / 'study005r1_release' / 'sealed' / 'readiness_protocol.json'
SOURCE_SHA256 = '52ee6ab001e6a00f4e8720cfb2c164f5412ea58c8118dbbc09315f7c3fc375f6'
OUT = HERE / 'readiness_protocol.r2.draft.json'
CANDIDATE_RELEASE = '5.5.0-study005r2'
HOLDOUT_COVERAGE = 0.98

FORECAST_CHECKPOINT = (
    'First healthy observation at 570-600 seconds remaining; common checkpoint; one per scheduled '
    'slot, subject to missing_policy. No missing-period zero returns.')
MISSING_POLICY = (
    'Fixed 900-second UTC slots; one unique active KXBTC15M contract per slot. No calendar deletions, '
    'backfill or zero-return assumption. A holdout slot without an enrolled checkpoint is missing. At most '
    '2% of holdout slots may be missing, and only when each missing slot has a logged reason (a recorded '
    'poll failure with its reason, a recorded veto reason, a recorded collector stop, or a REBOOT_RESUME '
    'event spanning the slot); a missing slot '
    'without a logged reason, more than 2% missing, a fully missing calendar day, an invalid source or a '
    'canceled/nonbinary outcome prevents favorable review. Missing slots are imputed under '
    'missing_slot_imputation, which can never improve any acceptance metric.')
MISSING_SLOT_IMPUTATION = {
    'principle': 'Each missing holdout slot enters every acceptance metric in the least favorable way; '
                 'the report shows each metric with and without imputation and lists every missing slot '
                 'with its logged reason.',
    'forecast_count': 'Imputed slots count toward matched_forecasts and holdout_coverage only as imputed; '
                      'coverage = enrolled / scheduled must still be at least holdout_coverage.',
    'brier': 'Each imputed slot adds Brier 1.0 for every model forecast and 0.0 for the market, so the '
             'paired zero-minus-market difference gains +1.0 per imputed slot.',
    'calibration_ece': 'ECE is computed on enrolled forecasts, then the missing fraction (missing / '
                       'scheduled) is added, an upper bound for any assignment of the missing slots.',
    'strategy': 'For each missing slot, that day\'s stressed P&L is reduced by fill_rate * max_loss, where '
                'fill_rate is holdout fills / enrolled holdout checkpoints and max_loss is the largest stressed '
                'one-unit loss among holdout fills (1.00 if there are none). Imputed slots never add fills to '
                'filled_contracts.',
}
TRADE_WINDOW_POLICY = (
    'Recent taker flow uses every public trade in [collection epoch - 180 s, collection epoch], fetched by '
    'following cursors up to 30 pages of 1000. A window still incomplete after the cap is a data fault and an '
    'operational veto. Trades outside the window are excluded, never fatal. Flow aggregates use the trades inside '
    'the analysis window; the SHA-256 covers the canonical trade set of the full collection window. Observation '
    'and signal/decision records keep the aggregates, the trade count, the first/last trade_id and that SHA-256; '
    'only checkpoint records keep the raw window.')

CLOCK_RESTART = (
    'Persist observation UTC and continuity-clock epochs (CLOCK_BOOTTIME on Linux, CLOCK_MONOTONIC on macOS; '
    'both count through sleep). Within one boot, reject wall-clock/continuity-clock disagreement beyond tolerance '
    'across polls and restarts; no silent reset, and an unclean stop within one boot still requires operator review. '
    'A reboot (boot identity change) is resumed in the same database, never silently: only after a REBOOT_RESUME '
    'event records the previous and new boot identities and the last persisted observation time, a fee/clock '
    'attestation written after the new boot passes (clock offset within tolerance), and the wall clock is not '
    'earlier than the last persisted observation. The continuity-clock baseline restarts at the new boot. Slots '
    'missed across a reboot are missing slots under missing_policy, with the logged reason "reboot". '
    'A suspend that keeps both clocks in agreement is a gap handled by the missing-data rule, not a discontinuity.')

CHANGED = ('study_id', 'start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc',
           'candidate_release', 'approval_policy.deadline_utc', 'forecast_checkpoint', 'missing_policy',
           'primary_acceptance.holdout_coverage', 'missing_slot_imputation', 'trade_window_policy',
           'clock_policy.restart')


def utc(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()


def untouched_digest(protocol):
    p = copy.deepcopy(protocol)
    for dotted in CHANGED:
        *parents, leaf = dotted.split('.')
        node = p
        for key in parents:
            node = node[key]
        node.pop(leaf, None)  # new fields are absent from the source
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build(current, start):
    days = [start + timedelta(days=n) for n in (0, 7, 14, 70, 72)]  # 7 dev + 7 validation + 56 holdout + 2 wait
    r = copy.deepcopy(current)
    r['study_id'] = 'btc-prospective-005r2-' + start.strftime('%Y%m%d')
    for key, day in zip(('start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc'), days):
        r[key] = utc(day)
    r['candidate_release'] = CANDIDATE_RELEASE
    r['approval_policy']['deadline_utc'] = utc(start - timedelta(days=1))
    r['forecast_checkpoint'] = FORECAST_CHECKPOINT
    r['missing_policy'] = MISSING_POLICY
    r['primary_acceptance']['holdout_coverage'] = HOLDOUT_COVERAGE
    r['missing_slot_imputation'] = MISSING_SLOT_IMPUTATION
    r['trade_window_policy'] = TRADE_WINDOW_POLICY
    r['clock_policy']['restart'] = CLOCK_RESTART
    # The draft is never activatable; the freeze sets the sealed status and activation_allowed.
    r['readiness_status'] = 'PROPOSED_NOT_APPROVED'
    r['activation_allowed'] = False
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
    # readiness_status/activation_allowed are reset to draft values, so compare without them too.
    strip = lambda p: {k: v for k, v in p.items() if k not in ('readiness_status', 'activation_allowed')}
    assert untouched_digest(strip(revised)) == untouched_digest(strip(current)), 'an unlisted field changed'
    assert revised['activation_allowed'] is False and revised['advice_allowed'] is False and revised['execution_allowed'] is False
    summary = {k: revised[k] for k in ('study_id', 'start_utc', 'validation_start_utc', 'holdout_start_utc', 'end_utc', 'release_utc')}
    summary['approval_deadline_utc'] = revised['approval_policy']['deadline_utc']
    summary['untouched_fields_sha256'] = untouched_digest(strip(revised))
    if not args.check:
        OUT.write_text(json.dumps(revised, indent=4, ensure_ascii=False) + '\n')
        summary['written'] = str(OUT.name)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
