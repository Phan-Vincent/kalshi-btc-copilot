"""Pure Study 005 revision-1 protocol checks. Validation never grants collection authority.

The reference digest binds ALL inherited policy prose and numeric rules, including
revision 1's .0001-only fee policy and continuity-clock wording. It excludes the
explicitly validated identity, dates, release label, status and permission flags.
No files, network, credentials, outcomes, approvals or launch paths are read.

Only a SEALED_EVIDENCE_ONLY protocol may carry activation_allowed=true; advice,
execution and prior-study reuse are always false. Sealing is the separately
approved release freeze (set START, regenerate the protocol, re-bind hashes).
"""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from study_policy import validate_protocol

# DRAFT start date. The freeze sets the real one; a different date is a new review.
START = datetime(2026, 10, 12, tzinfo=timezone.utc)
STUDY_ID = 'btc-prospective-005r1-' + START.strftime('%Y%m%d')
CANDIDATE_RELEASE = '5.4.1-study005r1'
PRESERVED_POLICY_SHA256 = 'f93935d44257656a0431efbcdc1c515d1b5e10a14f249bffbf0716fafbc089b3'  # revision-1 policy text
EXPECTED_KEYS = frozenset(['activation_allowed', 'advice_allowed', 'approval_policy', 'candidate_release', 'clock_policy', 'comparison_policy', 'delay_window_seconds', 'end_utc', 'execution_allowed', 'fee_evidence_policy', 'fee_multiplier', 'fee_policy', 'forecast_checkpoint', 'holdout_policy', 'holdout_start_utc', 'missing_policy', 'outcome_policy', 'pacing_policy', 'primary_acceptance', 'primary_exit', 'primary_strategy', 'profitability_validation_mode', 'reaction_delay_seconds', 'readiness_status', 'release_utc', 'reuse_study002_approval_or_data', 'risk_policy', 'schema_version', 'start_utc', 'study_id', 'uncertainty', 'validation_start_utc'])
MODIFIED_FIELDS = frozenset(['activation_allowed', 'advice_allowed', 'candidate_release', 'end_utc', 'execution_allowed', 'holdout_start_utc', 'readiness_status', 'release_utc', 'reuse_study002_approval_or_data', 'start_utc', 'study_id', 'validation_start_utc'])
DATES = {key: (START + timedelta(days=days)).isoformat() for key, days in
         (('start_utc', 0), ('validation_start_utc', 7), ('holdout_start_utc', 14), ('end_utc', 70), ('release_utc', 72))}
APPROVAL_DEADLINE = (START - timedelta(days=1)).isoformat()
STATUSES = {'PROPOSED_NOT_APPROVED': False, 'SEALED_EVIDENCE_ONLY': True}  # status -> activation_allowed
BALANCE_PRECISIONS = ['0.0001']
PROFITABILITY_MODE = 'EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER'
# Values the Study 002 validator understands; substituted only in an isolated copy.
LEGACY_FEE_VALUES = {'balance_precisions': ['0.0001', '0.01']}
LEGACY_PROFITABILITY_MODE = 'BLOCKED_PENDING_FEE_AND_EXECUTION_EVIDENCE'


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
    status = protocol['readiness_status']
    if type(status) is not str or status not in STATUSES or protocol['activation_allowed'] is not STATUSES[status]:
        raise ValueError('Activation is allowed only by a sealed evidence-only protocol')
    for key in ('advice_allowed', 'execution_allowed', 'reuse_study002_approval_or_data'):
        if protocol[key] is not False:
            raise ValueError('Readiness protocol cannot authorize advice, execution or prior-study reuse')
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
    fee = protocol.get('fee_policy')
    if type(fee) is not dict or fee.get('balance_precisions') != BALANCE_PRECISIONS \
            or protocol['profitability_validation_mode'] != PROFITABILITY_MODE:
        raise ValueError('Revision 1 requires the account-ledger .0001-only fee policy')
    if preserved_digest(protocol) != PRESERVED_POLICY_SHA256:
        raise ValueError('Inherited acceptance, cost, pacing, clock, uncertainty, strategy, exit or custody policy changed')
    # Legacy validator understands only study002, deadline == start and both precisions.
    # Adapt an isolated copy AFTER checking the revision-1 values above.
    normalized = copy.deepcopy(protocol)
    normalized['study_id'] = 'btc-prospective-002'
    normalized['approval_policy']['deadline_utc'] = normalized['start_utc']
    normalized['fee_policy'].update(copy.deepcopy(LEGACY_FEE_VALUES))
    normalized['profitability_validation_mode'] = LEGACY_PROFITABILITY_MODE
    validate_protocol(normalized)
    return copy.deepcopy(protocol)


def preserved_digest(protocol):
    preserved = copy.deepcopy(protocol)
    for field in MODIFIED_FIELDS:
        preserved.pop(field, None)
    preserved['approval_policy'].pop('deadline_utc', None)
    return hashlib.sha256(_canonical(preserved)).hexdigest()
