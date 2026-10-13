"""Offline fee arithmetic/reconciliation. No network and no account classification inference."""
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from datetime import datetime

def fee_sequence(fills,precision):
    precision=D(precision)
    if precision not in (D('.0001'),D('.01')):raise ValueError('Unsupported precision')
    carry=D(0);total=D(0);notional=D(0);cash=D(0);all_buy=True;details=[]
    for price,quantity,action in fills:
        price,quantity=D(str(price)),D(str(quantity))
        if not price.is_finite() or not quantity.is_finite() or not 0<=price<=1 or quantity<=0 or quantity%D('.01') or price%D('.0001') or action not in ('buy','sell'):raise ValueError('Invalid fill')
        cost=price*quantity;revenue=-cost if action=='buy' else cost
        trade=(D('.07')*quantity*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)
        aligned=((revenue-trade)/precision).to_integral_value(rounding=ROUND_FLOOR)*precision
        rounding=revenue-trade-aligned;carry+=rounding
        rebate=min((carry/precision).to_integral_value(rounding=ROUND_FLOOR),((trade+rounding)/precision).to_integral_value(rounding=ROUND_FLOOR))*precision
        carry-=rebate;net=trade+rounding-rebate;total+=net;notional+=cost;cash+=revenue-net;all_buy=all_buy and action=='buy'
        details.append({'trade':str(trade),'rounding':str(rounding),'rebate':str(rebate),'net':str(net)})
    return {'fee':str(total),'notional':str(notional),'all_in':str(notional+total) if all_buy else None,'net_cash_change':str(cash),'fills':details,'rounding_carry':str(carry)}

def reconcile(group):
    """Return aggregate agreement only; never identifiers, prices, quantities or fees."""
    rows=group.get('fills',[]);order=group.get('order') or {}
    result={'sample_fills':len(rows),'eligible_for_comparison':False,'account_classification':'not inferred'}
    def reject(reason):result['exclusion']=reason;return result
    if not rows or group.get('fills_truncated'):return reject('missing_or_truncated_fill_group')
    ids=[r.get('fill_id') for r in rows]
    if any(not isinstance(x,str) or not x for x in ids) or len(ids)!=len(set(ids)):return reject('missing_or_duplicate_fill_identity')
    if any(r.get('order_id')!=order.get('order_id') for r in rows) or not order.get('order_id'):return reject('order_identity_mismatch')
    if not str(order.get('ticker','')).startswith('KXBTC15M-') or any((r.get('ticker') or r.get('market_ticker'))!=order['ticker'] for r in rows):return reject('unsupported_series_or_ticker_mismatch')
    if order.get('status') not in ('executed','canceled'):return reject('order_not_terminal')
    if any(r.get('is_taker') is not True for r in rows):return reject('maker_or_mixed_order_requires_other_fee_rules')
    try:
        times=[datetime.fromisoformat(r['created_time'].replace('Z','+00:00')) for r in rows]
        if any(t.utcoffset() is None or t.utcoffset().total_seconds()!=0 for t in times):return reject('timestamp_not_utc')
    except (KeyError,TypeError,ValueError,AttributeError):return reject('invalid_fill_timestamp')
    if len(set(times))!=len(rows):return reject('fill_order_ambiguous')
    rows=[r for _,r in sorted(zip(times,rows),key=lambda pair:pair[0])];sequence=[];reported=[]
    try:
        for r in rows:
            side=r.get('outcome_side') or r.get('side');action=r['action']
            if side not in ('yes','no') or (r.get('side') and side!=r['side']):return reject('ambiguous_outcome_side')
            yes,no=D(r['yes_price_dollars']),D(r['no_price_dollars'])
            if yes+no!=1:return reject('noncomplementary_prices')
            price=yes if side=='yes' else no;quantity=D(r['count_fp']);fee=D(r['fee_cost'])
            if not fee.is_finite() or fee<0:return reject('invalid_reported_fee')
            sequence.append((price,quantity,action));reported.append(fee)
        if sum((q for p,q,a in sequence),D(0))!=D(order['fill_count_fp']):return reject('incomplete_quantity')
        if sum((p*q for p,q,a in sequence),D(0))!=D(order['taker_fill_cost_dollars']):return reject('notional_does_not_reconcile')
        if sum(reported,D(0))!=D(order['taker_fees_dollars']):return reject('fees_do_not_reconcile')
        comparisons={}
        for precision in ('.0001','.01'):
            predicted=fee_sequence(sequence,precision)
            comparisons[precision]={'total_matches':D(predicted['fee'])==sum(reported,D(0)),
                                    'every_fill_matches':all(D(p['net'])==actual for p,actual in zip(predicted['fills'],reported)),
                                    'trade_fee_only_matches':all(D(p['trade'])==actual for p,actual in zip(predicted['fills'],reported)),
                                    'modeled_rounding_present':any(D(p['rounding'])!=0 for p in predicted['fills'])}
    except (KeyError,ValueError,ArithmeticError,TypeError):return reject('invalid_or_missing_comparison_fields')
    matching=[p for p,v in comparisons.items() if v['total_matches'] and v['every_fill_matches']]
    result['comparison_class']='ambiguous_both_match' if len(matching)==2 else 'neither_matches' if not matching else 'one_scenario_matches_conditionally'
    result.update(eligible_for_comparison=True,actions=sorted(set(a for p,q,a in sequence)),precision_comparisons=comparisons,
                  scope='Reported fill and order fees under current quadratic coefficient1x. Conditional on fee_cost being net fees and no unreported adjustments; not legal membership or future fee verification.')
    return result

PAGINATED_READS=('/portfolio/fills','/historical/fills','/portfolio/orders','/historical/orders')

def _counts(value):
    return type(value) is dict and all(type(k) is str and type(v) is int and v>=0 for k,v in value.items())

def fee_evidence_problems(summary):
    """Recompute the account-ledger tripwire from a fee_evidence_refresh summary; never trust its own verdict.

    Returns a list of problems (empty means consistent and passing). Aggregate data only. This checks
    internal consistency; release_tool.py additionally re-derives the counts from the raw private records."""
    if type(summary) is not dict:return ['summary is not an object']
    problems=[]
    if summary.get('series_fee_type')!='quadratic' or summary.get('series_fee_multiplier')!='1':problems.append('KXBTC15M fee rules are not quadratic x1')
    if summary.get('fee_changes_for_series') not in (None,[]):problems.append('fee changes listed for KXBTC15M')
    pages=summary.get('pagination_complete')
    if type(pages) is not dict or any(pages.get(path) is not True for path in PAGINATED_READS):problems.append('incomplete or missing pagination')
    classes=summary.get('comparison_classes')
    if not _counts(classes):return problems+['invalid comparison_classes']
    if classes.get('only_01',0)!=0:problems.append('an order matched only the .01 model')
    if classes.get('only_0001',0)<1:problems.append('no decisive .0001 evidence')
    if summary.get('tripwire')!={'only_01_matches':classes.get('only_01',0),'passed':not problems}:problems.append('stored tripwire disagrees with recomputation')
    if summary.get('btc15m_orders_with_fills')!=sum(classes.values()):problems.append('class totals do not match order count')
    periods=summary.get('by_period')
    if type(periods) is not dict or not all(_counts(p) for p in periods.values()):problems.append('invalid by_period')
    elif {k:sum(p.get(k,0) for p in periods.values()) for k in set(classes)|{k for p in periods.values() for k in p}}!={k:classes.get(k,0) for k in set(classes)|{k for p in periods.values() for k in p}}:
        problems.append('period totals do not match classes')
    days=summary.get('decisive_by_utc_day')
    if type(days) is not dict or not all(_counts(d) for d in days.values()):problems.append('missing or invalid decisive_by_utc_day')
    elif sum(d.get('only_0001',0) for d in days.values())!=classes.get('only_0001',0) or any(d.get('only_01',0) for d in days.values()):
        problems.append('daily totals do not match classes')
    elif type(periods) is dict and all(_counts(p) for p in periods.values()):
        # Each day must sit in the period that contains it.
        before=sum(d.get('only_0001',0) for day,d in days.items() if day<'2026-09-27')
        if before!=periods.get('before_2026-09-27',{}).get('only_0001',0) or sum(d.get('only_0001',0) for d in days.values())-before!=periods.get('since_2026-09-27',{}).get('only_0001',0):
            problems.append('daily totals do not match their periods')
    try:datetime.fromisoformat(summary['retrieved_at'])
    except (KeyError,TypeError,ValueError):problems.append('invalid retrieved_at')
    return problems

def decisive_between(summary,start_day,end_day):
    """Decisive .0001-only orders on UTC days in [start_day, end_day) (ISO dates). Call only after fee_evidence_problems passes."""
    return sum(d.get('only_0001',0) for day,d in summary['decisive_by_utc_day'].items() if start_day<=day<end_day)
