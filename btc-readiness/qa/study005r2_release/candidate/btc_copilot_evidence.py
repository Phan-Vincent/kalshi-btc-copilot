"""Frozen prospective shadow study. No networking or account mutations."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import time
import zlib

from study_policy import validate_protocol, check_clock, execution_cost, intervals
from fee_reconciliation import fee_evidence_problems, decisive_between
EVALUATOR_SOURCE=Path(__file__).read_text()
POLICY=validate_protocol(json.loads(Path(__file__).with_name('btc_copilot_protocol.json').read_text()))

STRATEGIES=('current_full','drift_free','market_implied','without_structure','without_confirmation','without_flow','without_imbalance','without_strike_chop')

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=str)
def utc_day(epoch):return datetime.fromtimestamp(epoch,timezone.utc).strftime('%Y-%m-%d')
def timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def phase(protocol,epoch):
    for label,lo,hi in [('development','start_utc','validation_start_utc'),('validation','validation_start_utc','holdout_start_utc'),('holdout','holdout_start_utc','end_utc')]:
        if timestamp(protocol[lo])<=epoch<timestamp(protocol[hi]):return label
    return 'outside_study'

def candidates(s,previous,settings):
    """Remove only strategy filters; every arm retains operational/cost gates."""
    outputs={}
    for strategy in STRATEGIES:
        model=s['drift_free_model'] if strategy=='drift_free' else s['model']
        if strategy=='market_implied':
            midpoint=s['book']['yes_midpoint_estimate_dollars']
            model={'p_up':float(midpoint) if midpoint is not None else .5,'p_down':1-float(midpoint) if midpoint is not None else .5,
                   'up_sensitivity_low':float(s['book']['yes_bid_dollars']) if s['book']['yes_bid_dollars'] is not None else 0,
                   'up_sensitivity_high':float(s['book']['yes_ask_dollars']) if s['book']['yes_ask_dollars'] is not None else 1}
        scores={}
        for side,prefix in [('UP','yes'),('DOWN','no')]:
            ask=s['book'].get(prefix+'_ask_dollars');bid=s['book'].get(prefix+'_bid_dollars')
            fee=s['side_evaluations'][side]['fee_per_contract']
            floor=model['up_sensitivity_low'] if side=='UP' else 1-model['up_sensitivity_high']
            common=list(s['operational_vetoes'])
            if ask is None or bid is None or fee is None:common.append('Missing executable quotes')
            else:
                if not 0<=ask-bid<=D('.04'):common.append('Spread/crossed book')
                if D(str(s['book'].get(prefix+'_ask_size_fp') or 0))<1:common.append('Insufficient entry size')
                if D(str(s['book'].get(prefix+'_bid_size_fp') or 0))<1:common.append('Insufficient liquidation size')
            edge=None if ask is None or fee is None else (floor-float(ask+fee+D(str(settings['slippage_reserve_cents']))/100))*100
            if edge is None or edge<float(settings['minimum_conservative_net_edge_pp']):common.append('Insufficient conservative net edge')
            scores[side]=(edge,common)
        chosen=max(scores,key=lambda side:scores[side][0] if scores[side][0] is not None else -math.inf)
        if strategy not in ('drift_free','market_implied','without_structure') and s['structure']['setup_side']:
            chosen=s['structure']['setup_side']
        reasons=list(scores[chosen][1]);st=s['structure']
        same=(previous and previous.get('ticker')==s['ticker'] and 5<=s['analysis_epoch']-previous.get('epoch',0)<60 and
              previous.get('confirmation_valid') is True and not previous.get('data_quality_faults') and not previous.get('operational_vetoes') and
              previous.get('source_epoch',math.inf)<timestamp(s['btc_observed_at']['utc']))
        filters={
            'structure':st['setup_side']==chosen,
            'confirmation':bool(same and previous.get('setup_side')==chosen),
            'flow':s['recent_trades']['direction'] not in ('UP','DOWN') or s['recent_trades']['direction']==chosen,
            'imbalance':s['book_imbalance_5c'] is None or not ((chosen=='UP' and s['book_imbalance_5c']<-.35) or (chosen=='DOWN' and s['book_imbalance_5c']>.35)),
            'strike_chop':abs(float(s['distance_dollars']))>=st['sigma_dollars_sqrt_second']*math.sqrt(60)*.25}
        # Drift-free is an independently specified simple value baseline; no technical filters.
        for name,passed in filters.items():
            if strategy not in ('drift_free','market_implied') and strategy!='without_'+name and not passed:reasons.append(name)
        outputs[strategy]={'side':chosen,'eligible':not reasons,'reasons':reasons,'filters':filters,
                           'probability':model['p_up'] if chosen=='UP' else model['p_down'],
                           'conservative_edge_pp':scores[chosen][0]}
    return outputs

class Study:
    def __init__(self,path,protocol_path,version,sources,validator=validate_protocol):
        self.transaction=None
        self.path=Path(path);self.protocol=validator(json.loads(Path(protocol_path).read_text()))
        if self.path.is_symlink():raise ValueError('Study database symlink forbidden')
        self.version=version;self.protocol_hash=hashlib.sha256(canonical(self.protocol).encode()).hexdigest()
        if self.path.exists() and self.path.stat().st_size:
            with closing(sqlite3.connect('file:'+str(self.path.resolve())+'?mode=ro',uri=True)) as old:
                saved=json.loads(old.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()[0])
                if saved['protocol_hash']!=self.protocol_hash or saved['version']!=version:raise ValueError('Existing study mismatch; no writes or migration')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(self.path,0o600)
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value BLOB);
            CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY,phase TEXT,day TEXT,opened REAL,closed REAL,first_seen REAL,checkpoint REAL,features TEXT,result INTEGER,result_at REAL,settlement_attempt REAL DEFAULT 0,quarantined INTEGER DEFAULT 0,confirmed_at REAL);
            CREATE TABLE IF NOT EXISTS signals(ticker TEXT,strategy TEXT,side TEXT,epoch REAL,checked INTEGER DEFAULT 0,status TEXT DEFAULT 'pending',cost TEXT,stress_cost TEXT,actual_delay REAL,evidence BLOB,execution_evidence BLOB,PRIMARY KEY(ticker,strategy));
            CREATE TABLE IF NOT EXISTS checkpoints(ticker TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE IF NOT EXISTS health(epoch REAL,kind TEXT);
            CREATE TABLE IF NOT EXISTS slot_events(ticker TEXT,opened REAL,epoch REAL,reason TEXT);
            CREATE TABLE IF NOT EXISTS runtime_events(id INTEGER PRIMARY KEY,epoch REAL,kind TEXT,detail TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS unique_slot ON markets(opened);
            CREATE TABLE IF NOT EXISTS outcome_events(ticker TEXT,epoch REAL,status TEXT,result TEXT);
            ''')
            existing=c.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
            if existing:
                saved=json.loads(existing[0])
                if saved['protocol_hash']!=self.protocol_hash or saved['version']!=version:raise ValueError('Frozen study version/protocol mismatch; no automatic cohort reset')
            else:
                c.execute('INSERT INTO meta VALUES (?,?)',('manifest',canonical({'protocol_hash':self.protocol_hash,'version':version,'protocol':self.protocol})))
                c.execute('INSERT INTO meta VALUES (?,?)',('sources',zlib.compress(canonical(sources).encode())))
    @contextmanager
    def db(self):
        if self.transaction is not None:
            yield self.transaction
            return
        with closing(sqlite3.connect(self.path)) as c:
            with c:yield c
    def record(self,s,raw,previous,settings):
        from btc_copilot_research import walk
        if s['model_version']!=self.version:raise ValueError('Frozen study settings changed; study blocked')
        opened=timestamp(s['open_time']);split=phase(self.protocol,opened);now=s['epoch']
        if opened%900 or timestamp(s['close_time'])-opened!=900:raise ValueError('Invalid scheduled slot')
        if abs(timestamp(s['close_time'])-now-s['time_remaining_seconds'])>.001:raise ValueError('Inconsistent remaining time')
        check_clock(s)
        if split=='outside_study':return
        if D(str(s['fee_multiplier']))!=D(str(self.protocol['fee_multiplier'])):raise ValueError('Fee multiplier differs from frozen study')
        signals=s['shadow_strategies'];book=raw['book']['data']['orderbook_fp']
        timing=s['retrieval_health']['requests']['book']
        if not all(math.isfinite(float(x)) for x in (now,timing['started_at_epoch'],timing['finished_at_epoch'],s['valid_until_epoch'])):raise ValueError('Nonfinite observation clock')
        if not timing['started_at_epoch']<=timing['finished_at_epoch']<=now:raise ValueError('Inconsistent book request clock')
        if s['valid_until_epoch']<=now:raise ValueError('Expired observation cannot enter the study')
        public={k:{a:v[a] for a in ('data','retrieved_at','request_started_at') if a in v} for k,v in raw.items() if k not in ('position','fills')}
        snapshot={k:v for k,v in s.items() if k not in ('position','scorecard')}
        signal_public={k:v for k,v in public.items()}
        if 'trades' in signal_public:
            signal_public['trades']={**signal_public['trades'],'data':{'summary':s['recent_trades']}}
        decision_evidence=zlib.compress(canonical({'snapshot':snapshot,'raw':signal_public,'previous':previous,'settings':settings}).encode())
        with self.db() as c:
            if self.transaction is None:c.execute('BEGIN IMMEDIATE')
            prior=c.execute("SELECT value FROM meta WHERE key='last_epoch'").fetchone()
            prior_mono=c.execute("SELECT value FROM meta WHERE key='last_monotonic'").fetchone()
            check_clock(s,float(prior[0]) if prior else None,float(prior_mono[0]) if prior_mono else None)
            collision=c.execute('SELECT ticker FROM markets WHERE opened=?',(opened,)).fetchone()
            if collision and collision[0]!=s['ticker']:raise ValueError('Ambiguous ticker for scheduled slot')
            c.execute("INSERT OR REPLACE INTO meta VALUES ('last_epoch',?)",(str(now),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('last_monotonic',?)",(str(s['observation_monotonic']),))
            c.execute('INSERT OR IGNORE INTO markets(ticker,phase,day,opened,closed,first_seen) VALUES (?,?,?,?,?,?)',(s['ticker'],split,utc_day(opened),opened,timestamp(s['close_time']),now))
            row=c.execute('SELECT checkpoint FROM markets WHERE ticker=?',(s['ticker'],)).fetchone()
            if row[0] is None and 570<=s['time_remaining_seconds']<=600 and not s['operational_vetoes']:
                bid=s['book']['yes_bid_dollars'];ask=s['book']['yes_ask_dollars']
                if bid is not None and ask is not None and 0<=ask-bid<=D('.04'):
                    features={'p_drift':s['model']['p_up'],'p_zero':s['drift_free_model']['p_up'],'p_market':float((bid+ask)/2),
                              'market_bid':str(bid),'market_ask':str(ask),'sigma':s['structure']['sigma_dollars_sqrt_second'],
                              'regime':'trending' if abs(s['structure']['hour_trend_z'])>=.65 else 'quiet',
                              'volatility_regime':'high' if s['structure']['sigma_dollars_sqrt_second']>=2 else 'low',
                              'utc_session':'00-08' if datetime.fromtimestamp(opened,timezone.utc).hour<8 else '08-16' if datetime.fromtimestamp(opened,timezone.utc).hour<16 else '16-24'}
                    c.execute('UPDATE markets SET checkpoint=?,features=? WHERE ticker=?',(now,canonical(features),s['ticker']))
                    # Durable same-time public inputs for all model comparisons; independent of rolling retention.
                    public={k:{a:v[a] for a in ('data','retrieved_at','request_started_at') if a in v} for k,v in raw.items() if k not in ('position','fills')}
                    c.execute('INSERT INTO checkpoints VALUES (?,?)',(s['ticker'],zlib.compress(canonical({'raw':public,'previous':previous,'settings':settings,'epoch':now,'features':features}).encode())))
            enrolled=c.execute('SELECT checkpoint FROM markets WHERE ticker=?',(s['ticker'],)).fetchone()[0]
            if enrolled is None and 570<=s['time_remaining_seconds']<=600:
                reasons=s['operational_vetoes'] or ['Missing executable quote or spread above 4c']
                for reason in reasons:c.execute('INSERT INTO slot_events VALUES (?,?,?,?)',(s['ticker'],opened,now,str(reason)[:200]))
            if enrolled is None:return
            for name,signal in signals.items():
                old=c.execute('SELECT side,epoch,checked FROM signals WHERE ticker=? AND strategy=?',(s['ticker'],name)).fetchone()
                if old and not old[2] and now-old[1]>=self.protocol['reaction_delay_seconds']:
                    delay=now-old[1];status='missed_observation';cost=stress=None
                    latency=s['retrieval_health']['requests']['book']
                    deadline=old[1]+self.protocol['reaction_delay_seconds']
                    window_end=deadline+self.protocol['delay_window_seconds']
                    # An in-flight request started before eligibility is not the first eligible request.
                    if now<=window_end and latency['started_at_epoch']<deadline:continue
                    if now<=window_end and latency['started_at_epoch']>=deadline:
                        status='no_longer_eligible'
                        if signal['eligible'] and signal['side']==old[0] and s['valid_until_epoch']>now:
                            opposite='no' if old[0]=='UP' else 'yes'
                            entry=execution_cost(book.get(opposite+'_dollars',[]),settings,self.protocol['fee_policy']['balance_precisions'])
                            status='insufficient_depth'
                            if entry is not None:
                                cost=entry['cost'];stress=entry['stress_cost'];status='hypothetical_fill'
                                decision_evidence=zlib.compress(canonical({'snapshot':snapshot,'raw':signal_public,'previous':previous,'settings':settings,'execution':entry}).encode())
                    c.execute('UPDATE signals SET checked=1,status=?,cost=?,stress_cost=?,actual_delay=?,execution_evidence=? WHERE ticker=? AND strategy=?',
                              (status,cost,stress,delay,decision_evidence,s['ticker'],name))
                if not old and signal['eligible']:
                    c.execute('INSERT INTO signals(ticker,strategy,side,epoch,evidence) VALUES (?,?,?,?,?)',(s['ticker'],name,signal['side'],now,decision_evidence))
    def settle(self,client,now):
        policy=self.protocol['outcome_policy']
        if now>=timestamp(self.protocol['release_utc']):return
        with self.db() as c:
            pending=c.execute('SELECT ticker,result FROM markets WHERE closed<? AND (result IS NULL OR settlement_attempt<?) ORDER BY settlement_attempt,closed LIMIT ?', (now-60,now-policy['recheck_seconds'],policy['retry_batch'])).fetchall()
        for ticker,original in pending:
            with self.db() as c:c.execute('UPDATE markets SET settlement_attempt=? WHERE ticker=?',(now,ticker))
            try:m=client.market(ticker)['data']['market']
            except Exception:continue
            if m.get('ticker')!=ticker:continue
            status=m.get('status');result=m.get('result')
            final=status=='finalized' and result in ('yes','no')
            with self.db() as c:
                c.execute('INSERT INTO outcome_events VALUES (?,?,?,?)',(ticker,now,status,str(result)))
                if original is not None and (not final or int(result=='yes')!=original):
                    c.execute('UPDATE markets SET quarantined=1 WHERE ticker=?',(ticker,));continue
                if not final:continue
                c.execute('UPDATE markets SET result=COALESCE(result,?),result_at=COALESCE(result_at,?),confirmed_at=? WHERE ticker=?',(int(result=='yes'),now,now,ticker))
                c.execute("UPDATE signals SET checked=1,status='missed_observation' WHERE ticker=? AND checked=0",(ticker,))
    def status(self,now):
        with self.db() as c:
            seen=c.execute('SELECT COUNT(*),SUM(checkpoint IS NOT NULL) FROM markets').fetchone()
        return {'mode':'SHADOW ONLY','phase':('pre-study' if now<timestamp(self.protocol['start_utc']) else 'collection complete' if now>=timestamp(self.protocol['end_utc']) else phase(self.protocol,now)),'markets_seen':seen[0],'matched_forecasts':seen[1] or 0,
                'holdout_results_locked_until':self.protocol['release_utc'],'primary_strategy':self.protocol['primary_strategy'],
                'protocol_hash':self.protocol_hash,'version':self.version,'promotion':'No automatic promotion; no validated advice available'}

def bootstrap_day_mean(values,seed=719,n=2000):
    sensitivity=intervals(values,POLICY['uncertainty'])
    if any(v is None for v in sensitivity.values()):return None
    # Envelope implements all-length acceptance, not a simultaneous confidence interval.
    return [min(v[0] for v in sensitivity.values()),max(v[1] for v in sensitivity.values())]

def summarize(markets,signals,protocol,split):
    data=[m for m in markets if m['phase']==split];settled=[m for m in data if m['checkpoint'] is not None and m['result'] is not None]
    enrolled={m['ticker']:m for m in data if m['checkpoint'] is not None}
    by_ticker={m['ticker']:m for m in settled};output={'markets_seen':len(data),'matched_settled_forecasts':len(settled),'pending_outcomes':sum(m['result'] is None for m in data),'forecast_models':{},'strategies':{},'filter_ablations':{}}
    lo,hi={'development':('start_utc','validation_start_utc'),'validation':('validation_start_utc','holdout_start_utc'),'holdout':('holdout_start_utc','end_utc')}[split]
    scheduled_days={utc_day(t) for t in range(int(timestamp(protocol[lo])),int(timestamp(protocol[hi])),86400)}
    observed_days={m['day'] for m in settled}
    output['scheduled_contracts']=int((timestamp(protocol[hi])-timestamp(protocol[lo]))/900)
    output['missing_checkpoint_contracts']=output['scheduled_contracts']-len(enrolled)
    output['missing_calendar_days']=sorted(scheduled_days-observed_days)
    output['unresolved_enrolled_contracts']=sum(m['result'] is None for m in enrolled.values())
    output['coverage_bias_warning']='Observed-only statistics are conditional on captured checkpoints. Missing periods cannot be assumed representative or assigned zero returns.'
    for model in ('p_drift','p_zero','p_market'):
        losses=[];daily={};bins={};regimes={}
        for m in settled:
            f=json.loads(m['features']);p=f[model];y=m['result'];loss=(p-y)**2;losses.append(loss);daily.setdefault(m['day'],[]).append(loss)
            bucket=min(9,int(p*10));bins.setdefault(bucket,[]).append((p,y))
            for dimension in ('regime','volatility_regime','utc_session'):regimes.setdefault(dimension+':'+f[dimension],[]).append(loss)
        output['forecast_models'][model]={'brier':statistics.mean(losses) if losses else None,'day_block_interval':bootstrap_day_mean({d:statistics.mean(v) for d,v in daily.items()}),
            'calibration_bins':{str(k):{'n':len(v),'forecast':statistics.mean(p for p,y in v),'observed':statistics.mean(y for p,y in v)} for k,v in bins.items()},
            'regimes':{k:{'n':len(v),'brier':statistics.mean(v)} for k,v in regimes.items()}}
    paired={}
    for m in settled:
        f=json.loads(m['features']);y=m['result'];paired.setdefault(m['day'],[]).append((f['p_zero']-y)**2-(f['p_market']-y)**2)
    output['zero_minus_market_brier_interval']=bootstrap_day_mean({d:statistics.mean(v) for d,v in paired.items()})
    for strategy in STRATEGIES+('always_abstain',):
        rows=[s for s in signals if s['strategy']==strategy and s['ticker'] in enrolled];pnl={m['ticker']:0. for m in settled};stress=pnl.copy();counts={}
        for s in rows:
            counts[s['status']]=counts.get(s['status'],0)+1
            if s['cost'] is not None and s['ticker'] in by_ticker:
                y=by_ticker[s['ticker']]['result'];payoff=float((s['side']=='UP')==bool(y));pnl[s['ticker']]=payoff-float(s['cost']);stress[s['ticker']]=payoff-float(s['stress_cost'])
        daily={};daily_stress={};regimes={};equity=peak=drawdown=0.
        for m in sorted(settled,key=lambda m:m['closed']):
            value=pnl[m['ticker']];equity+=value;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
            daily[m['day']]=daily.get(m['day'],0)+value;daily_stress[m['day']]=daily_stress.get(m['day'],0)+stress[m['ticker']]
            f=json.loads(m['features'])
            for dimension in ('regime','volatility_regime','utc_session'):regimes.setdefault(dimension+':'+f[dimension],[]).append(value)
        output['strategies'][strategy]={'attempts':len(rows),'status_counts':counts,'filled_contracts':sum(s['cost'] is not None and s['ticker'] in by_ticker for s in rows),'pending_filled_contracts':sum(s['cost'] is not None and s['ticker'] not in by_ticker for s in rows),'resolved_attempts':sum(s['ticker'] in by_ticker for s in rows),'worst_case_pending_return_dollars':-sum(float(s['cost']) for s in rows if s['cost'] is not None and s['ticker'] not in by_ticker),'entry_days':len({by_ticker[s['ticker']]['day'] for s in rows if s['cost'] is not None and s['ticker'] in by_ticker}),'net_hypothetical_dollars':sum(pnl.values()),
            'stress_net_dollars':sum(stress.values()),'max_drawdown_dollars':drawdown,'mean_daily_return_interval':bootstrap_day_mean(daily),
            'stress_daily_return_interval':bootstrap_day_mean(daily_stress),'days':len(daily),'all_enrolled_contracts_denominator':len(enrolled),
            'regimes':{k:{'contracts':len(v),'net':sum(v)} for k,v in regimes.items()},'daily':daily,'stress_daily':daily_stress,'stress_block_sensitivity':intervals(daily_stress,protocol['uncertainty'])}
    for strategy in STRATEGIES:
        scenario_totals={'.0001':D(0),'.01':D(0)};count=0
        for row in signals:
            if row['strategy']!=strategy or row['ticker'] not in by_ticker or not row.get('execution_evidence') or row['cost'] is None:continue
            payload=json.loads(zlib.decompress(row['execution_evidence']))
            if 'execution' not in payload:continue
            count+=1;y=by_ticker[row['ticker']]['result'];payoff=D(int((row['side']=='UP')==bool(y)))
            for precision,fee in payload['execution']['scenario_fees'].items():
                scenario_totals[precision]+=payoff-D(fee['all_in'])-D(str(payload['settings']['slippage_reserve_cents']))/100
        # Revision-1 diagnostics: which fee-bound term applied to each costed entry in this phase.
        # Uses execution evidence only (no outcomes). Absent for protocols without the field.
        terms={};levels={};costed=0
        for row in signals:
            if row['strategy']!=strategy or row['ticker'] not in enrolled or not row.get('execution_evidence') or row['cost'] is None:continue
            execution=json.loads(zlib.decompress(row['execution_evidence'])).get('execution',{})
            if 'binding_fee_terms' not in execution:continue
            costed+=1;levels[str(execution['consumed_levels'])]=levels.get(str(execution['consumed_levels']),0)+1
            for term in execution['binding_fee_terms']:terms[term]=terms.get(term,0)+1
        if costed:output['strategies'][strategy]['fee_bound_binding']={'costed_entries':costed,'binding_terms':terms,'consumed_levels':levels,
            'scope':'Counts of the acceptance-fee term that applied; ties count every tied term. Diagnostic only.'}
        output['strategies'][strategy]['assumed_level_fill_scenarios']={'resolved_entries':count,'net_dollars':{k:str(v) for k,v in scenario_totals.items()},'scope':'One hypothetical fill per consumed displayed level; not actual fills, not acceptance economics.'}
    if output['missing_calendar_days'] or output['unresolved_enrolled_contracts']:

        for model in output['forecast_models'].values():model['day_block_interval']=None
        output['zero_minus_market_brier_interval']=None
        for strategy in output['strategies'].values():
            strategy['mean_daily_return_interval']=None;strategy['stress_daily_return_interval']=None
    output['paired_block_sensitivity']=intervals({d:statistics.mean(v) for d,v in paired.items()},protocol['uncertainty'])
    output['quarantined_contracts']=sum(m['quarantined'] for m in data)
    output['unconfirmed_at_release']=sum(m['confirmed_at'] is None or m['confirmed_at']<timestamp(protocol['release_utc'])-protocol['outcome_policy']['release_recheck_window_seconds'] for m in enrolled.values())
    full=output['strategies']['current_full']
    for name in STRATEGIES:
        if name.startswith('without_'):
            row=output['strategies'][name];output['filter_ablations'][name]={'delta_net_vs_full':row['net_hypothetical_dollars']-full['net_hypothetical_dollars'],'delta_attempts':row['attempts']-full['attempts'],'interpretation':'Exploratory paired comparison; not an alternative eligible for promotion'}
    return output

FEE_LEDGER_MODE='EVIDENCE_ONLY_0001_PRECISION_FROM_ACCOUNT_LEDGER'
EXECUTION_REVIEW=('Execution is not machine-verified. Fills are hypothetical: one taker fill per consumed displayed level at the '
                  'first quote 15-35 s after a signal, with no orders placed; that depth may not have been available. The independent '
                  'reviewer must assess execution realism (see fee_bound_binding and consumed_levels) before any advice is considered.')

# Owner decision (2026-09-30, option B): a post-study refresh with zero decisive orders inside the study
# window still verifies fees, because study eligibility must not depend on the owner trading. The report
# flags the gap so the independent reviewer weighs it; any .01-only match still fails the tripwire.
ALLOW_NO_IN_STUDY_EVIDENCE=True
NO_IN_STUDY_EVIDENCE=('No decisive .0001 orders fell inside the study window. Precision for the study period is carried '
                      'from earlier account records (zero .01-only matches through the post-study refresh); the '
                      'independent reviewer must weigh this.')

def fee_evidence_review(p,fee_evidence,now):
    """Post-study check of the account-ledger fee evidence (aggregate summary from fee_evidence_refresh.py).

    Fees count as verified only for a protocol that adopted .0001 from the account ledger AND a supplied
    refresh, retrieved after the study ended and not after the report time, that passes the recomputed
    tripwire. By owner decision B, zero decisive orders inside the study window still verifies, but sets
    reviewer_must_weigh. The summary must first pass fee_evidence_refresh.py --verify against the raw
    records (release_tool.py post-study-report enforces that order). Replaces the combined fee/execution
    placeholder, which was always False. Execution assumptions are never machine-verified."""
    adopted=p.get('profitability_validation_mode')==FEE_LEDGER_MODE and p.get('fee_policy',{}).get('balance_precisions')==['0.0001']
    review={'protocol_adopts_0001_from_ledger':adopted,'post_study_refresh_supplied':fee_evidence is not None,'verified':False}
    if not adopted or fee_evidence is None:return review
    problems=fee_evidence_problems(fee_evidence)
    if problems:
        review.update(problems=problems);return review
    retrieved=timestamp(fee_evidence['retrieved_at'])
    if retrieved<timestamp(p['end_utc']):problems.append('refresh retrieved before the study ended')
    if retrieved>now:problems.append('refresh dated after the report time')
    during=decisive_between(fee_evidence,p['start_utc'][:10],p['end_utc'][:10])
    if not during and not ALLOW_NO_IN_STUDY_EVIDENCE:problems.append('no decisive .0001 orders inside the study window: precision unresolved for the study period')
    review.update(problems=problems,retrieved_at=fee_evidence['retrieved_at'],decisive_0001_orders_during_study=during,
                  raw_sha256=fee_evidence.get('raw_sha256'),verified=not problems,
                  reviewer_must_weigh=None if during else NO_IN_STUDY_EVIDENCE,
                  summary_sha256=fee_evidence.get('_input_sha256'))
    return review

def fee_assumptions_verified(p,fee_evidence=None,now=None):
    return fee_evidence_review(p,fee_evidence,time.time() if now is None else now)['verified']

def missing_slots(protocol,markets,slot_events,runtime_events):
    """Enumerate every scheduled holdout slot using only enrollment and operational evidence."""
    enrolled={m['opened'] for m in markets if m['phase']=='holdout' and m['checkpoint'] is not None}
    start,end=(int(timestamp(protocol[k])) for k in ('holdout_start_utc','end_utc'))
    result=[]
    for opened in range(start,end,900):
        if opened in enrolled:continue
        reasons=[x['reason'] for x in slot_events if x['opened']==opened]
        band_start,band_end=opened+300,opened+330
        reasons += [x['detail'] for x in runtime_events if x['kind'] in ('COLLECTION_FAILED','EXCHANGE_CLOSED') and band_start<=x['epoch']<=band_end and x['detail']]
        for event in runtime_events:
            if event['kind']!='REBOOT_RESUME':continue
            try:
                previous=json.loads(event['detail'])['last_observation_epoch']
                if (type(previous) in (int,float) and math.isfinite(previous) and
                    previous<event['epoch'] and band_start<event['epoch'] and band_end>previous):
                    reasons.append('reboot')
            except (TypeError,ValueError,KeyError):pass
        stops=[x for x in runtime_events if x['kind']=='COLLECTOR_STOP' and x['epoch']<=band_end]
        if stops:
            stopped=stops[-1]
            if not any(x['kind']=='COLLECTOR_START' and stopped['epoch']<x['epoch']<=band_end for x in runtime_events):
                reasons.append(stopped['detail'] or 'Collector stopped')
        result.append({'slot_opened_utc':datetime.fromtimestamp(opened,timezone.utc).isoformat(),
                       'day':utc_day(opened),'reasons':sorted(set(reasons))})
    return result

def ece_from_bins(bins):
    values=list(bins.values());total=sum(x['n'] for x in values)
    return sum(x['n']*abs(x['forecast']-x['observed']) for x in values)/total if total else None

def impute_holdout(summary,markets,signals,protocol,missing):
    """Literal r2 least-favorable acceptance metrics; observed figures remain alongside them."""
    enrolled={m['ticker']:m for m in markets if m['phase']=='holdout' and m['checkpoint'] is not None}
    settled={ticker:m for ticker,m in enrolled.items() if m['result'] is not None}
    scheduled=int((timestamp(protocol['end_utc'])-timestamp(protocol['holdout_start_utc']))/900)
    count=len(missing);denominator=len(settled)+count
    models={}
    for name,penalty in (('p_drift',1.),('p_zero',1.),('p_market',0.)):
        before=summary['forecast_models'][name]['brier']
        models[name]={'without_imputation':before,'with_imputation':(before*len(settled)+penalty*count)/denominator if before is not None and denominator else None}
    ece=ece_from_bins(summary['forecast_models']['p_zero']['calibration_bins'])
    paired={}
    for m in settled.values():
        f=json.loads(m['features']);y=m['result']
        paired.setdefault(m['day'],[]).append((f['p_zero']-y)**2-(f['p_market']-y)**2)
    for slot in missing:paired.setdefault(slot['day'],[]).append(1.)
    paired_after=bootstrap_day_mean({day:statistics.mean(values) for day,values in paired.items()})
    strategies={}
    for name,item in summary['strategies'].items():
        fills=[s for s in signals if s['strategy']==name and s['ticker'] in settled and s['cost'] is not None]
        fill_rate=len(fills)/len(enrolled) if enrolled else 0.
        max_loss=max((max(0.,float(s['stress_cost'])-float((s['side']=='UP')==bool(settled[s['ticker']]['result']))) for s in fills),default=1.)
        penalty=fill_rate*max_loss
        daily=dict(item['stress_daily'])
        for slot in missing:daily[slot['day']]=daily.get(slot['day'],0.)-penalty
        strategies[name]={'without_imputation':{'stress_daily':item['stress_daily'],'stress_daily_return_interval':item['stress_daily_return_interval'],'stress_net_dollars':item['stress_net_dollars']},
                          'with_imputation':{'stress_daily':daily,'stress_daily_return_interval':bootstrap_day_mean(daily),'stress_net_dollars':item['stress_net_dollars']-count*penalty},
                          'fill_rate':fill_rate,'max_stressed_one_unit_loss':max_loss,'penalty_per_missing_slot':penalty,
                          'filled_contracts_unchanged':item['filled_contracts']}
    return {'missing_slots':missing,'missing_count':count,'scheduled_slots':scheduled,'enrolled_slots':len(enrolled),
            'coverage':len(enrolled)/scheduled,'all_missing_slots_have_reasons':all(x['reasons'] for x in missing),
            'within_two_percent_budget':count<=int(scheduled*.02),
            'forecast_models':models,'calibration_ece':{'without_imputation':ece,'with_imputation':None if ece is None else ece+count/scheduled},
            'paired_zero_minus_market_brier_interval':{'without_imputation':summary['zero_minus_market_brier_interval'],'with_imputation':paired_after},
            'strategies':strategies,'matched_forecasts_with_imputation':len(settled)+count}

def report(path,now,validator=validate_protocol,fee_evidence=None):
    # fee_evidence: the aggregate summary of a fee_evidence_refresh.py run made after the study ended.
    if not math.isfinite(now) or now>time.time():raise ValueError('Report time is in the future; the release lock follows the real clock')
    # Study 005 passes readiness_policy.validate_readiness_protocol; the default stays Study 002's.
    with closing(sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;manifest=json.loads(c.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()[0]);p=manifest['protocol']
        archived=json.loads(zlib.decompress(c.execute("SELECT value FROM meta WHERE key='sources'").fetchone()[0]))
        if archived.get('btc_copilot_evidence.py')!=EVALUATOR_SOURCE:raise ValueError('Frozen evaluator source differs; restore the archived version for this report')
        validator(p)
        if manifest['protocol_hash']!=hashlib.sha256(canonical(p).encode()).hexdigest():raise ValueError('Protocol manifest mismatch')
        if archived.get('study_policy.py')!=Path(__file__).with_name('study_policy.py').read_text():raise ValueError('Policy source mismatch')
        if archived.get('request_pacing.py')!=Path(__file__).with_name('request_pacing.py').read_text():raise ValueError('Pacing source mismatch')
        if archived.get('fee_reconciliation.py')!=Path(__file__).with_name('fee_reconciliation.py').read_text():raise ValueError('Fee source mismatch')
        released=now>=timestamp(p['release_utc'])
        # Never query holdout outcomes before the predetermined release time.
        condition='' if released else " WHERE phase!='holdout'"
        markets=[dict(x) for x in c.execute('SELECT * FROM markets'+condition)]
        signals=[dict(x) for x in c.execute("SELECT s.* FROM signals s JOIN markets m ON m.ticker=s.ticker"+('' if released else " WHERE m.phase!='holdout'"))]
        slot_events=[dict(x) for x in c.execute('SELECT opened,reason FROM slot_events')] if released else []
        runtime_events=[dict(x) for x in c.execute('SELECT epoch,kind,detail FROM runtime_events')] if released else []
    out={'protocol':p,'version':manifest['version'],'holdout_locked':not released,'validated':False,'phases':{}}
    for split in ('development','validation')+(('holdout',) if released else ()):
        out['phases'][split]=summarize(markets,signals,p,split)
    if released:
        h=out['phases']['holdout'];primary=h['strategies'][p['primary_strategy']]
        revised='missing_slot_imputation' in p
        if revised:
            imputed=impute_holdout(h,markets,signals,p,missing_slots(p,markets,slot_events,runtime_events))
            h['imputation']=imputed
            ci=imputed['strategies'][p['primary_strategy']]['with_imputation']['stress_daily_return_interval']
            paired=imputed['paired_zero_minus_market_brier_interval']['with_imputation']
        else:
            ci=primary['stress_daily_return_interval'];paired=h['zero_minus_market_brier_interval']
        expected=int((timestamp(p['end_utc'])-timestamp(p['holdout_start_utc']))/900)
        a=p['primary_acceptance']
        fee_review=fee_evidence_review(p,fee_evidence,now);out['fee_evidence']=fee_review
        checks={'fee_assumptions_verified':fee_review['verified'],'no_unknown_execution':all(primary['status_counts'].get(k,0)==a['missing_execution_observations'] for k in ('pending','missed_observation')),'no_amended_results':h['quarantined_contracts']==a['amended_results'],'release_reconfirmation':h['unconfirmed_at_release']==0,'all_enrolled_outcomes_reconciled':h['unresolved_enrolled_contracts']==a['unresolved_enrolled'],'all_calendar_days_represented':len(h['missing_calendar_days'])==a['missing_calendar_days'],
                'minimum_entries':primary['filled_contracts']>=a['filled_contracts'],'minimum_days':primary['entry_days']>=a['distinct_days'],
                'positive_stress_lower_bound':ci is not None and ci[0]>a['stress_daily_lower_bound'],'better_than_market_brier':paired is not None and paired[1]<a['paired_zero_minus_market_brier_upper_bound']}
        if revised:checks.update(missing_slot_budget=imputed['within_two_percent_budget'] and imputed['all_missing_slots_have_reasons'],
                                 forecast_coverage=imputed['coverage']>=a['holdout_coverage'],
                                 minimum_forecasts=imputed['matched_forecasts_with_imputation']>=a['matched_forecasts'])
        else:checks.update(complete_scheduled_checkpoints=h['missing_checkpoint_contracts']==0,
                           forecast_coverage=h['matched_settled_forecasts']>=expected*a['holdout_coverage'],
                           minimum_forecasts=h['matched_settled_forecasts']>=a['matched_forecasts'])
        ece=imputed['calibration_ece']['with_imputation'] if revised else ece_from_bins(h['forecast_models']['p_zero']['calibration_bins'])
        checks['calibration_ece_at_most_005']=ece is not None and ece<=a['calibration_ece']
        out['acceptance_checks']=checks;out['eligible_for_independent_review']=all(checks.values())
        out['execution_assumptions']={'machine_verified':False,'reviewer_must_assess':EXECUTION_REVIEW}
    out['limitations']='Strict coverage and worst-case unknown fragmentation can make favorable review impossible; no resets or relaxed gates. Block envelope is sensitivity, not simultaneous coverage. All results are shadow estimates, not observed fills. Day-block intervals need sufficient days and are not guarantees. One predeclared primary; variants exploratory. No automatic promotion. Missing execution attempts remain visible and block favorable review; observed-only partial returns are not complete portfolio returns. Always abstaining has zero trading return.'
    return out

if __name__=='__main__':
    import time
    # Study 002 legacy entry point. Study 005 reports use evidence_runner.py --report (its own database
    # and validator), normally through release_tool.py post-study-report.
    # Optional argument: a post-study fee_evidence_refresh_summary.json (aggregate only).
    import sys
    if (Path(__file__).parent/'readiness_protocol.json').exists():
        raise SystemExit('Study 005 release: use release_tool.py post-study-report (this entry point reports Study 002 only)')
    evidence=json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv)>1 else None
    print(json.dumps(report(Path(__file__).parent/'runtime/btc_copilot_study.sqlite3',time.time(),fee_evidence=evidence),indent=2))
