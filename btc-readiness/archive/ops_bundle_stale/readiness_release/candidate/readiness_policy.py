"""Pure readiness-proposal checks. Validation never grants collection authority.

The reference digest binds ALL inherited policy prose and numeric rules, not
only the subset checked by the legacy validator. It excludes the explicitly
validated new identity, proposed dates, release label and disabled flags.
No files, network, credentials, outcomes, approvals or launch paths are read.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from study_policy import validate_protocol

STUDY_ID = 'btc-prospective-005-20261214-readiness'
CANDIDATE_RELEASE = '5.2.0-readiness-work-in-progress'
PRESERVED_POLICY_SHA256 = '574126a8c60a38a387ad46448e9f8fdbde72ed2fdae592eb9224fa57a589b9f9'
EXPECTED_KEYS = frozenset(['activation_allowed', 'advice_allowed', 'approval_policy', 'candidate_release', 'clock_policy', 'comparison_policy', 'delay_window_seconds', 'end_utc', 'execution_allowed', 'fee_evidence_policy', 'fee_multiplier', 'fee_policy', 'forecast_checkpoint', 'holdout_policy', 'holdout_start_utc', 'missing_policy', 'outcome_policy', 'pacing_policy', 'primary_acceptance', 'primary_exit', 'primary_strategy', 'profitability_validation_mode', 'reaction_delay_seconds', 'readiness_status', 'release_utc', 'reuse_study002_approval_or_data', 'risk_policy', 'schema_version', 'start_utc', 'study_id', 'uncertainty', 'validation_start_utc'])
MODIFIED_FIELDS = frozenset(['activation_allowed', 'advice_allowed', 'candidate_release', 'end_utc', 'execution_allowed', 'holdout_start_utc', 'readiness_status', 'release_utc', 'reuse_study002_approval_or_data', 'start_utc', 'study_id', 'validation_start_utc'])
DATES = {'start_utc': '2026-12-14T00:00:00+00:00', 'validation_start_utc': '2026-12-21T00:00:00+00:00', 'holdout_start_utc': '2026-12-28T00:00:00+00:00', 'end_utc': '2027-02-22T00:00:00+00:00', 'release_utc': '2027-02-24T00:00:00+00:00'}
APPROVAL_DEADLINE = '2026-12-13T00:00:00+00:00'


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ValueError('Protocol must contain finite JSON data only') from error


def _utc_midnight(value):
    if type(value) is not str:
        raise ValueError('UTC midnight string required')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError('Invalid UTC boundary') from error
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0 or any((result.hour, result.minute, result.second, result.microsecond)):
        raise ValueError('UTC midnight required')
    return result.astimezone(timezone.utc)


def validate_readiness_protocol(protocol):
    """Validate exact proposal; return an independent copy, never mutate input.

    This is an offline specification check, not evidence of favorable economics,
    operational sign-off, current venue rules, source integrity or user approval.
    """
    if type(protocol) is not dict or set(protocol) != EXPECTED_KEYS:
        raise ValueError('Unsupported or missing readiness protocol fields')
    _canonical(protocol)
    if protocol['study_id'] != STUDY_ID or protocol['candidate_release'] != CANDIDATE_RELEASE:
        raise ValueError('Unsupported readiness identity or candidate release')
    if protocol['readiness_status'] != 'PROPOSED_NOT_APPROVED':
        raise ValueError('Readiness protocol must remain unapproved')
    for key in ('activation_allowed', 'advice_allowed', 'execution_allowed', 'reuse_study002_approval_or_data'):
        if protocol[key] is not False:
            raise ValueError('Readiness proposal cannot authorize activation, advice, execution or prior-study reuse')
    boundaries = [_utc_midnight(protocol[key]) for key in DATES]
    spans = [(right-left).days for left, right in zip(boundaries, boundaries[1:])]
    if spans != [7, 7, 56, 2]:
        raise ValueError('Preserve 7 development, 7 validation, 56 holdout and 2 release-wait days')
    if any(protocol[key] != expected for key, expected in DATES.items()):
        raise ValueError('Different future dates require separate protocol review; no automatic shift')
    approval = protocol.get('approval_policy')
    if type(approval) is not dict or approval.get('deadline_utc') != APPROVAL_DEADLINE:
        raise ValueError('Explicit pre-start approval deadline required')
    if _utc_midnight(APPROVAL_DEADLINE) >= boundaries[0]:
        raise ValueError('Approval deadline must precede collection')
    preserved = copy.deepcopy(protocol)
    for field in MODIFIED_FIELDS:
        preserved.pop(field, None)
    preserved['approval_policy'].pop('deadline_utc')
    digest = hashlib.sha256(_canonical(preserved)).hexdigest()
    if digest != PRESERVED_POLICY_SHA256:
        raise ValueError('Inherited acceptance, cost, pacing, clock, uncertainty, strategy, exit or custody policy changed')
    # Legacy validator understands only study002 and deadline == start. Adapt an
    # isolated copy AFTER checking new identity/calendar/deadline and all rules.
    normalized = copy.deepcopy(protocol)
    normalized['study_id'] = 'btc-prospective-002'
    normalized['approval_policy']['deadline_utc'] = normalized['start_utc']
    validate_protocol(normalized)
    return copy.deepcopy(protocol)
