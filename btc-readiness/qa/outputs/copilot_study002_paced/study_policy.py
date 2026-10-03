"""Pure study-002 rules. No network, credentials, production data, or account actions."""
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from datetime import datetime, timezone
from pathlib import Path
import hashlib,json,math,random

def epoch(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.utcoffset() is None or dt.utcoffset().total_seconds()!=0:raise ValueError('UTC required')
    return dt.timestamp()

def validate_protocol(p):
    if p['schema_version']!=2 or p['study_id']!='btc-prospective-002':raise ValueError('Unsupported study identity')
    times=[epoch(p[k]) for k in ('start_utc','validation_start_utc','holdout_start_utc','end_utc','release_utc')]
    if times!=sorted(set(times)) or any(t%86400 for t in times):raise ValueError('Strict UTC midnight boundaries required')
    if (times[3]-times[2])/86400!=56:raise ValueError('56-day holdout required')
    if p['reaction_delay_seconds']!=15 or p['delay_window_seconds']!=20:raise ValueError('Unsupported execution policy')
    if p['primary_strategy']!='drift_free':raise ValueError('Primary reselection forbidden')
    a=p['primary_acceptance']
    required={'matched_forecasts':5376,'filled_contracts':100,'distinct_days':40,'holdout_coverage':1.0,'stress_daily_lower_bound':0,'paired_zero_minus_market_brier_upper_bound':0,'calibration_ece':.05,'unresolved_enrolled':0,'missing_calendar_days':0,'amended_results':0,'missing_execution_observations':0}
    if a!=required:raise ValueError('Unsupported acceptance policy; separately reviewed version required')
    if p['uncertainty']['block_days']!=[1,2,7] or p['uncertainty']['replicates']!=2000 or p['uncertainty']['seed']!=719 or p['uncertainty']['minimum_days']!=56:raise ValueError('Unsupported uncertainty policy')
    f=p['fee_policy']
    if (f['coefficient'],f['type'],f['multiplier'],f['hypothetical_quantity'],f['minimum_fill_quantity'])!=('0.07','quadratic','1','1.00','0.01'):raise ValueError('Unsupported fee policy')
    if p['fee_multiplier']!='1' or f['trade_fee_precision']!='0.000001' or f['balance_precisions']!=['0.0001','0.01']:raise ValueError('Unsupported fee precision')
    if p['outcome_policy']['retry_batch']!=64 or p['outcome_policy']['recheck_seconds']!=21600 or p['outcome_policy']['release_recheck_window_seconds']!=21600:raise ValueError('Unsupported reconciliation policy')
    if p['approval_policy']['deadline_utc']!=p['start_utc']:raise ValueError('Approval deadline mismatch')
    if p['clock_policy']['wall_monotonic_tolerance_seconds']!=1 or p['clock_policy']['max_request_seconds']!=15 or p['clock_policy']['max_snapshot_age_seconds']!=20:raise ValueError('Unsupported clock policy')
    if p['pacing_policy']!={'interval_seconds':15,'tokens_per_second':60,'bucket_capacity':60,'background_tokens_per_second':20,'background_bucket_capacity':10,'maximum_wait_seconds':5,'default_cost':10,'benchmark_cost':50,'initial_tokens':0,'cooldown_seconds':[2,4,8,16,30]}:raise ValueError('Unsupported pacing policy')
    return p

def check_clock(snapshot,last_epoch=None,last_monotonic=None):
    now=snapshot['epoch'];expiry=snapshot['valid_until_epoch']
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (now,expiry)):raise ValueError('Invalid clock')
    mono=snapshot['observation_monotonic']
    if type(mono) not in (int,float) or not math.isfinite(mono):raise ValueError('Invalid monotonic clock')
    if last_monotonic is not None and (mono<=last_monotonic or abs((now-last_epoch)-(mono-last_monotonic))>1):raise ValueError('Between-observation clock discontinuity')
    if not snapshot['retrieval_health']['requests'].get('book'):raise ValueError('Missing book clock')
    if expiry<=now or expiry>now+20.001:raise ValueError('Invalid expiry')
    if last_epoch is not None and now<=last_epoch:raise ValueError('Duplicate or reversed observation')
    for t in snapshot['retrieval_health']['requests'].values():
        start,finish,elapsed=(t[k] for k in ('started_at_epoch','finished_at_epoch','latency_seconds'))
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,finish,elapsed)):raise ValueError('Invalid request clock')
        if not start<=finish<=now or not 0<=elapsed<=15 or now-start>20 or abs(finish-start-elapsed)>1:raise ValueError('Request clock discontinuity or staleness')

def fee_fills(fills,precision):
    """Exact hypothetical taker-buy sequence under documented fee accumulator rules."""
    precision=D(precision)
    if precision not in (D('.0001'),D('.01')):raise ValueError('Unsupported balance precision')
    accumulator=D(0);net=D(0);cost=D(0);details=[]
    for price,quantity in fills:
        price,quantity=D(str(price)),D(str(quantity))
        if not price.is_finite() or not quantity.is_finite() or not 0<=price<=1 or quantity<=0 or quantity%D('.01'):raise ValueError('Invalid hypothetical fill')
        revenue=-price*quantity;model=D('.07')*quantity*price*(1-price)
        trade=model.quantize(D('.000001'),rounding=ROUND_CEILING)
        aligned=((revenue-trade)/precision).to_integral_value(rounding=ROUND_FLOOR)*precision
        rounding=revenue-trade-aligned;accumulator+=rounding
        rebate=min((accumulator/precision).to_integral_value(rounding=ROUND_FLOOR),((trade+rounding)/precision).to_integral_value(rounding=ROUND_FLOOR))*precision
        accumulator-=rebate;fee=trade+rounding-rebate;net+=fee;cost-=revenue
        details.append({'trade':str(trade),'rounding':str(rounding),'rebate':str(rebate),'net':str(fee)})
    return {'fee':str(net),'notional':str(cost),'all_in':str(cost+net),'fills':details,'rounding_carry':str(accumulator)}

def execution_cost(rows,settings):
    # One contract, minimum .01 units; no partial fill counts as an entry.
    levels=[]
    for p,q in rows:
        p,q=D(str(p)),D(str(q))
        if not p.is_finite() or not q.is_finite() or not 0<=p<=1 or q<0 or q%D('.01'):raise ValueError('Unsupported price/depth')
        if q:levels.append((1-p,q))
    remaining=D(1);fills=[]
    for price,q in sorted(levels):
        take=min(q,remaining)
        if take:fills.append((price,take));remaining-=take
        if not remaining:break
    if remaining:return None
    scenarios={x:fee_fills(fills,x) for x in ('.0001','.01')}
    notional=sum((p*q for p,q in fills),D(0))
    model=sum((D('.07')*q*p*(1-p) for p,q in fills),D(0))
    # Ceiling is subadditive: splitting every .01 quantity quantum and ignoring
    # rebates bounds any allowed grouping into fills at each consumed price.
    bounds={}
    for precision in (D('.0001'),D('.01')):
        debit=D(0)
        for price,q in fills:
            unit_trade=(D('.07')*D('.01')*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)
            unit_debit=((D('.01')*price+unit_trade)/precision).to_integral_value(rounding=ROUND_CEILING)*precision
            debit+=(q/D('.01'))*unit_debit
        bounds[str(precision)]=debit-notional
    bound=max(bounds.values())
    legacy=sum((q.to_integral_value(rounding=ROUND_CEILING)*(D('.07')*p*(1-p)).quantize(D('.01'),rounding=ROUND_CEILING) for p,q in fills),D(0))
    reserve_cents=D(str(settings['slippage_reserve_cents']))
    if not reserve_cents.is_finite() or not D('.5')<=reserve_cents<=10:raise ValueError('Slippage reserve below safety floor or invalid')
    fee=max(bound,legacy);reserve=reserve_cents/100
    return {'scenario_fees':scenarios,'notional':str(notional),'acceptance_fee_bound':str(fee),'fragmentation_fee_bounds':{k:str(v) for k,v in bounds.items()},'cost':str(notional+fee+reserve),'stress_cost':str(notional+fee+reserve+D('.01')),'max_fills_assumed':100,'actual_fills':False}

def intervals(values,policy):
    days=sorted(values)
    if len(days)<policy['minimum_days']:return {str(b):None for b in policy['block_days']}
    if any(epoch(days[i]+'T00:00:00+00:00')-epoch(days[i-1]+'T00:00:00+00:00')!=86400 for i in range(1,len(days))):raise ValueError('Nonconsecutive daily sample')
    data=[values[d] for d in days];out={};n=len(data)
    for block in policy['block_days']:
        rng=random.Random(policy['seed']);samples=[]
        for _ in range(policy['replicates']):
            sample=[]
            while len(sample)<n:
                start=rng.randrange(n-block+1);sample.extend(data[start:start+block])
            samples.append(sum(sample[:n])/n)
        samples.sort();out[str(block)]=[samples[int(len(samples)*.025)],samples[int(len(samples)*.975)]]
    return out

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

RELEASE_FILES={'btc_copilot.py','btc_copilot_evidence.py','btc_copilot_research.py','kalshi_readonly.py','study_policy.py','btc_copilot_protocol.json','btc_copilot_settings.json','btc_copilot.html','request_pacing.py'}

def verify_release(root):
    root=Path(root).resolve();m=json.loads((root/'release_manifest.json').read_text())
    if set(m['files'])!=RELEASE_FILES:raise ValueError('Incomplete release manifest')
    if m['study_id']!='btc-prospective-002':raise ValueError('Manifest study mismatch')
    for name,expected in m['files'].items():
        path=root/name
        if Path(name).name!=name or path.is_symlink() or digest(path)!=expected:raise ValueError('Release mismatch: '+name)
    return m

def launch_guard(root,now):
    if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('Invalid launch clock')
    root=Path(root).resolve();verify_release(root)
    p=validate_protocol(json.loads((root/'btc_copilot_protocol.json').read_text()))
    approval=root/'activation_approval.json'
    if not approval.exists():raise ValueError('NOT ACTIVATED: explicit user approval record absent')
    if approval.is_symlink():raise ValueError('Approval symlink forbidden')
    a=json.loads(approval.read_text())
    if a.get('bundle_path')!=str(root):raise ValueError('Approval path mismatch')
    if a.get('approved') is not True or a.get('study_id')!=p['study_id'] or a.get('manifest_sha256')!=digest(root/'release_manifest.json'):raise ValueError('Approval not bound to this release')
    approved=epoch(a['approved_at_utc'])
    if not approved<=now or not approved<epoch(p['start_utc']):raise ValueError('Approval too late or future-dated')
    if not epoch(p['start_utc'])<=now<epoch(p['release_utc']):raise ValueError('Outside authorized collection/reconciliation window')
    attested=epoch(a['fee_verified_at_utc'])
    if not 0<=now-attested<=86400 or a.get('fee_rules_verified') is not True:raise ValueError('Fee rules require fresh verification')
    for field in ('read_refill_tokens_per_second','read_bucket_capacity','default_read_cost','benchmark_read_cost'):
        if type(a.get(field)) not in (int,float) or not math.isfinite(a[field]):raise ValueError('Invalid budget attestation')
    rate_time=epoch(a['rate_verified_at_utc'])
    if not 0<=now-rate_time<=86400 or a.get('read_refill_tokens_per_second',0)<200 or a.get('read_bucket_capacity',0)<600 or a.get('default_read_cost')!=10 or a.get('benchmark_read_cost')!=50:raise ValueError('Read budget or costs require fresh verification')
    for name in ('btc_copilot_latest.json','btc_copilot_latest.txt','btc_copilot_journal.jsonl','btc_copilot_evidence_report.json'):
        if (root/name).is_symlink():raise ValueError('Published output symlink forbidden')
    work=root/'runtime'
    if work.is_symlink():raise ValueError('Runtime symlink forbidden')
    if work.exists() and any(x.is_symlink() or (x.is_file() and x.stat().st_nlink!=1) for x in work.iterdir()):raise ValueError('Runtime contains linked file')
    return p
