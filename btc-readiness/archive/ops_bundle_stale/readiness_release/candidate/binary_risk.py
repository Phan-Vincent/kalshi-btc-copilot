"""Offline binary-contract risk checks. Conditional eligibility is never authority.

All monetary amounts are dollars. Schema 2 records the account owner's limits
(September 27, 2026): risk 5% of verified total account value per trade, never
more than a fixed $25 ceiling; open-plus-pending exposure measured as maximum
loss (premium + fee bound + stress reserve), capped at the same per-trade
amount because only one position is allowed; $30 daily gross-loss limit on the
America/Los_Angeles calendar day. Payout notional is reported, not limited.
Ledger truth and conservative fee bounds are supplied, not discovered by this
module; verification flags are attestations, not proofs.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext

D = Decimal
ZERO = D('0')
STEP = D('.01')
DAY_ZONE = 'America/Los_Angeles'
# Hard ceilings from the account owner. Configuration may tighten, never loosen.
MAX_FRACTION = D('.05')
MAX_TRADE_CEILING = D('25')
MAX_DAILY = D('30')
EXPOSURE_POLICY = 'max_loss_premium_fees_stress_including_pending'
# Cash-purchased YES/NO contracts only. Reject extensions rather than silently
# interpreting leverage, short sales, stops or targets using premium-loss math.
PROPOSAL_FIELDS = frozenset({
    'ticker', 'side', 'price', 'price_tick', 'requested_quantity',
    'available_quantity', 'fee_bound_per_contract', 'stress_reserve_per_contract',
    'fee_bound_verified', 'fee_bound_reference', 'fee_bound_as_of',
    'fee_bound_valid_until', 'fee_bound_scope', 'market_verified',
    'market_as_of', 'market_valid_until',
})


class InvalidInput(ValueError):
    pass


def number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise InvalidInput(name + ': explicit decimal string, integer or Decimal required')
    try:
        result = D(value)
    except (InvalidOperation, ValueError):
        raise InvalidInput(name + ': invalid decimal')
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise InvalidInput(name + ': invalid nonnegative finite amount')
    # Bound untrusted precision/exponents before arithmetic or serialization.
    if len(result.as_tuple().digits) > 24 or abs(result.as_tuple().exponent) > 12 or result > D('1000000000000'):
        raise InvalidInput(name + ': amount exceeds supported precision or range')
    return result


def utc(value, name):
    if not isinstance(value, str):
        raise InvalidInput(name + ': UTC ISO timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise InvalidInput(name + ': invalid UTC timestamp')
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise InvalidInput(name + ': explicit UTC required')
    return parsed.astimezone(timezone.utc)


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise InvalidInput(name + ': nonempty bounded string required')
    return value


def quantity(value, name):
    value = number(value, name, positive=True)
    if value % STEP:
        raise InvalidInput(name + ': quantity must be a multiple of .01')
    return value


def price(value, name):
    value = number(value, name, positive=True)
    if value >= 1:
        raise InvalidInput(name + ': price must be strictly between 0 and 1')
    return value


def as_string(value):
    return format(value, 'f')


def evaluate_risk(proposal, ledger, limits, now):
    """Return safe, JSON-serializable offline sizing; never emit trade advice.

    requested_quantity is an upper bound; actual eligible_quantity is rounded
    down to .01 and constrained by visible available_quantity. Market depth and
    fee evidence must remain fresh. No API, filesystem, clock or account access.
    """
    base = {'status': 'DATA_UNAVAILABLE', 'advice_enabled': False,
            'trading_authorized': False, 'reasons': [], 'eligible_quantity': '0'}
    try:
        with localcontext() as context:
            context.prec = 80
            return _evaluate(proposal, ledger, limits, now, base)
    except (InvalidInput, KeyError, TypeError, InvalidOperation, OverflowError) as exc:
        base['reasons'] = ['INVALID_OR_UNVERIFIED_INPUT: ' + str(exc)]
        return base


def _evaluate(proposal, ledger, limits, now, base):
    if not all(isinstance(value, dict) for value in (proposal, ledger, limits)):
        raise InvalidInput('proposal, ledger and limits must be objects')
    if any(key not in PROPOSAL_FIELDS for key in proposal):
        raise InvalidInput('unsupported proposal field; only cash-purchased binary-contract sizing is supported')
    if type(limits['schema_version']) is not int or limits['schema_version'] != 2:
        raise InvalidInput('unsupported limits schema')
    if limits['advice_enabled'] is not False or limits['trading_authorized'] is not False:
        raise InvalidInput('this offline engine cannot enable advice or trading')
    if limits['adding_enabled'] is not False:
        raise InvalidInput('adding must remain disabled')
    if limits['exposure_policy'] != EXPOSURE_POLICY:
        raise InvalidInput('ambiguous exposure policy')
    if limits['daily_boundary_timezone'] != DAY_ZONE:
        raise InvalidInput('daily boundary must be America/Los_Angeles')
    fraction = number(limits['risk_fraction_of_account'], 'risk_fraction_of_account', positive=True)
    ceiling = number(limits['max_loss_per_trade_ceiling'], 'max_loss_per_trade_ceiling', positive=True)
    daily_cap = number(limits['daily_loss_limit'], 'daily_loss_limit', positive=True)
    slots = limits['max_simultaneous_positions']
    if type(slots) is not int or slots != 1:
        raise InvalidInput('only one simultaneous position is authorized')
    freshness = number(limits['max_evidence_age_seconds'], 'max_evidence_age_seconds', positive=True)
    if freshness > 60:
        raise InvalidInput('evidence age must not exceed 60 seconds')
    if fraction > MAX_FRACTION or ceiling > MAX_TRADE_CEILING or daily_cap > MAX_DAILY:
        raise InvalidInput('limits exceed user-authorized caps')
    current = utc(now, 'now')
    if ledger['verified'] is not True or proposal['fee_bound_verified'] is not True or proposal['market_verified'] is not True:
        raise InvalidInput('verified ledger, fee and market evidence required')
    text(ledger['verification_reference'], 'ledger verification_reference')
    text(proposal['fee_bound_reference'], 'fee_bound_reference')
    if proposal['fee_bound_scope'] != 'all_supported_quantities_including_fragmentation_and_rounding':
        raise InvalidInput('fee bound must cover every supported quantity, fragmentation and debit rounding')
    for label, stamp in [('ledger', ledger['captured_at']), ('fee', proposal['fee_bound_as_of']), ('market', proposal['market_as_of'])]:
        age = D(str((current - utc(stamp, label)).total_seconds()))
        if age < 0 or age > freshness:
            raise InvalidInput(label + ': stale or future evidence')
    if utc(proposal['fee_bound_valid_until'], 'fee expiry') <= current or utc(proposal['market_valid_until'], 'market expiry') <= current:
        raise InvalidInput('expired evidence')
    try:
        local_day = current.astimezone(ZoneInfo(DAY_ZONE)).date().isoformat()
    except Exception:
        raise InvalidInput('Pacific time zone data unavailable')
    if ledger['day_pacific'] != local_day:
        raise InvalidInput('ledger Pacific day does not match decision day')
    # Total account value (cash plus marked positions) from a fresh verified read.
    account_value = number(ledger['account_value'], 'account_value', positive=True)
    trade_cap = min(ceiling, account_value * fraction)
    exposure_cap = trade_cap
    gross_loss = number(ledger['realized_gross_loss'], 'realized_gross_loss')
    # Wins are intentionally irrelevant. Net P&L cannot substitute for gross loss.
    ticker = text(proposal['ticker'], 'ticker')
    if proposal['side'] not in ('YES', 'NO'):
        raise InvalidInput('side must be YES or NO')
    entry = price(proposal['price'], 'price')
    tick = number(proposal['price_tick'], 'price_tick', positive=True)
    if tick >= 1 or entry % tick:
        raise InvalidInput('entry not aligned to supplied verified price tick')
    requested = quantity(proposal['requested_quantity'], 'requested_quantity')
    depth = number(proposal['available_quantity'], 'available_quantity')
    if depth % STEP:
        raise InvalidInput('available_quantity must be a multiple of .01')
    fee = number(proposal['fee_bound_per_contract'], 'fee_bound_per_contract')
    reserve = number(proposal['stress_reserve_per_contract'], 'stress_reserve_per_contract')
    unit_loss = entry + fee + reserve
    entries = ledger['positions_and_pending']
    if not isinstance(entries, list) or len(entries) > 1000:
        raise InvalidInput('bounded positions_and_pending list required')
    total_premium = total_payout = committed = ZERO
    seen = set()
    occupied_tickers = set()
    risk_by_ticker = {}
    for item in entries:
        if not isinstance(item, dict):
            raise InvalidInput('ledger entry must be object')
        identity = text(item['id'], 'entry id')
        if identity in seen:
            raise InvalidInput('duplicate ledger entry id')
        seen.add(identity)
        occupied_tickers.add(text(item['ticker'], 'entry ticker'))
        if item['state'] not in ('OPEN', 'PENDING') or item['side'] not in ('YES', 'NO'):
            raise InvalidInput('unsupported ledger state or side')
        amount = quantity(item['quantity'], 'ledger quantity')
        cost = price(item['entry_price'], 'ledger entry_price') * amount
        fees = number(item['fee_amount'], 'ledger fee_amount')
        stress = number(item['stress_reserve_amount'], 'ledger stress_reserve_amount')
        total_premium += cost
        total_payout += amount
        committed += cost + fees + stress
        key = item['ticker']
        risk_by_ticker[key] = risk_by_ticker.get(key, ZERO) + cost + fees + stress
    remaining_daily = max(ZERO, daily_cap - gross_loss - committed)
    exposure_remaining = max(ZERO, exposure_cap - committed)
    result = dict(base)
    result.update({'status': 'INELIGIBLE', 'reasons': [], 'exposure_policy': EXPOSURE_POLICY,
                   'account_value': as_string(account_value), 'max_loss_per_trade': as_string(trade_cap),
                   'max_open_pending_exposure': as_string(exposure_cap),
                   'existing_premium_exposure': as_string(total_premium), 'existing_payout_notional': as_string(total_payout),
                   'existing_committed_max_loss': as_string(committed), 'daily_realized_gross_loss': as_string(gross_loss),
                   'daily_remaining_before_proposal': as_string(remaining_daily),
                   'exposure_remaining_before_proposal': as_string(exposure_remaining),
                   'requested_quantity': as_string(requested), 'loss_bound_per_contract': as_string(unit_loss)})
    if ticker in occupied_tickers:
        result['reasons'].append('ADDING_OR_AVERAGING_DISABLED')
    if len(occupied_tickers) >= slots:
        result['reasons'].append('SIMULTANEOUS_POSITION_LIMIT')
    if committed > exposure_cap or committed + gross_loss > daily_cap or len(occupied_tickers) > slots or any(loss > trade_cap for loss in risk_by_ticker.values()):
        result['reasons'].append('EXISTING_LEDGER_LIMIT_BREACH')
    if result['reasons']:
        return result
    cap = min(requested, depth, trade_cap / unit_loss, remaining_daily / unit_loss,
              exposure_remaining / unit_loss)
    sized = (cap / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
    if sized <= 0:
        result['reasons'] = ['NO_CAPACITY_WITHIN_ALL_LIMITS']
        return result
    premium = entry * sized
    fees = fee * sized
    stress = reserve * sized
    loss = premium + fees + stress
    result.update({'status': 'CONDITIONALLY_ELIGIBLE', 'eligible_quantity': as_string(sized),
                   'entry_premium': as_string(premium), 'fee_bound': as_string(fees),
                   'stress_reserve': as_string(stress), 'max_loss': as_string(loss),
                   'total_premium_exposure': as_string(total_premium + premium),
                   'total_committed_exposure': as_string(committed + loss),
                   'total_payout_notional': as_string(total_payout + sized),
                   'daily_committed_max_loss': as_string(gross_loss + committed + loss),
                   'daily_remaining_after_proposal': as_string(remaining_daily - loss),
                   'trade_loss_remaining': as_string(trade_cap - loss),
                   'exposure_remaining': as_string(exposure_remaining - loss),
                   'reasons': ['OFFLINE_RISK_CHECK_ONLY'] + (['QUANTITY_REDUCED_TO_LIMITS'] if sized < requested else [])})
    return result
