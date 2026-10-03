"""Local-only decision audit, bounded replay storage and hypothetical depth previews."""
from contextlib import contextmanager, closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import zlib
from decimal import Decimal as D, ROUND_CEILING

VERSION = '4.2.0-study002-fee-review'
SOURCE_TEXT={name:(Path(__file__).parent/name).read_text() for name in ('btc_copilot.py','btc_copilot_research.py','btc_copilot_evidence.py','kalshi_readonly.py','study_policy.py','request_pacing.py','fee_reconciliation.py')}

def load_settings(path):
    s=json.loads(Path(path).read_text())
    bounds={'model_uncertainty_reserve_pp':(7,30),'minimum_conservative_net_edge_pp':(5,30),
            'slippage_reserve_cents':(.5,10),'final_entry_cutoff_seconds':(90,600),
            'maximum_snapshot_skew_seconds':(1,15),'replay_retention_days':(1,30)}
    for key,(lo,hi) in bounds.items():
        value=float(s[key])
        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError('Invalid setting: '+key)
    quantities=s['preview_quantities']
    if not quantities or len(quantities)>8 or any(not D(str(q)).is_finite() or not 0<D(str(q))<=1000 for q in quantities):raise ValueError('Invalid preview quantities')
    return s

def version(settings):
    root=Path(__file__).parent
    digest=hashlib.sha256()
    for name in ('btc_copilot.py','btc_copilot_research.py','btc_copilot_evidence.py','kalshi_readonly.py','study_policy.py','request_pacing.py','fee_reconciliation.py'):
        digest.update(SOURCE_TEXT[name].encode())
    digest.update(json.dumps({k:v for k,v in settings.items() if k not in ('fee_verified_at','notes','fee_source','fee_schedule_effective_date')},sort_keys=True).encode())
    return VERSION+'-'+digest.hexdigest()[:16]

def walk(rows,quantity,complement=False,multiplier=1):
    levels=[]
    for p,q in rows:
        p,q=D(str(p)),D(str(q))
        if not p.is_finite() or not q.is_finite() or not 0<=p<=1 or q<0:raise ValueError('Invalid book level')
        if q:levels.append((1-p if complement else p,q))
    levels.sort(reverse=not complement)
    remaining=D(str(quantity));cost=D(0);fee=D(0)
    for price,available in levels:
        take=min(remaining,available)
        cost+=take*price
        # Round each contract conservatively, including fractional-contract ceilings.
        fee+=take.to_integral_value(rounding=ROUND_CEILING)*(D('.07')*D(str(multiplier))*price*(1-price)).quantize(D('.01'),rounding=ROUND_CEILING)
        remaining-=take
        if remaining<=0:break
    filled=D(str(quantity))-remaining
    return {'quantity':str(quantity),'displayed_quantity':str(sum((q for _,q in levels),D(0))),
            'filled_quantity':str(filled),'complete':remaining==0,'average_price':str(cost/filled) if filled else None,
            'notional':str(cost),'conservative_fee':str(fee),'partial':remaining>0}

def previews(raw,s,settings):
    book=raw['book']['data']['orderbook_fp'];out=[]
    for side,own,opposite in [('UP','yes','no'),('DOWN','no','yes')]:
        floor=s['model']['up_sensitivity_low'] if side=='UP' else 1-s['model']['up_sensitivity_high']
        for q in settings['preview_quantities']:
            buy=walk(book.get(opposite+'_dollars',[]),q,True,s['fee_multiplier'])
            sell=walk(book.get(own+'_dollars',[]),q,False,s['fee_multiplier'])
            edge=None
            if buy['complete']:
                edge=(D(str(floor))-(D(buy['notional'])+D(buy['conservative_fee']))/D(str(q))-D(str(settings['slippage_reserve_cents']))/100)*100
            out.append({'side':side,'quantity':str(q),'entry':buy,'liquidation':sell,
                        'conservative_edge_pp':str(edge) if edge is not None else None,
                        'entry_eligible':bool(s['decision']==side and edge is not None and edge>=D(str(settings['minimum_conservative_net_edge_pp']))),
                        'hypothetical':True})
    return out

def blockers(veto):
    def next_step(reason):
        if 'edge' in reason:return 'Wait for a lower ask or a stronger conservative estimate; all other gates still apply.'
        if 'setup' in reason.lower() or 'confirmed' in reason:return 'Wait for a completed structural setup and another fresh confirming observation.'
        if 'remaining' in reason:return 'Wait for the next contract and verify its opening target.'
        if 'spread' in reason or 'contract at' in reason or 'bid/ask' in reason:return 'Wait for a complete book with sufficient size and an acceptable spread.'
        if 'Fee' in reason:return 'Reverify the official fee policy before entry analysis resumes.'
        return 'Wait for fresh, consistent observations that pass this check.'
    return [{'reason':v,'next_condition':next_step(v)} for v in veto]

class Audit:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(exist_ok=True)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(self.path,0o600)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,epoch REAL,ticker TEXT,version TEXT,decision TEXT,p REAL,remaining REAL,payload BLOB);
            CREATE TABLE IF NOT EXISTS contracts(ticker TEXT PRIMARY KEY,day TEXT,version TEXT,p REAL,result REAL,signal TEXT,signal_at REAL,delayed_cost REAL,delay_seconds REAL,delay_checked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS implementations(version TEXT PRIMARY KEY,source BLOB);
            CREATE TABLE IF NOT EXISTS gaps(epoch REAL PRIMARY KEY,reason TEXT);
            CREATE INDEX IF NOT EXISTS obs_epoch ON observations(epoch);''')
    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:yield connection
    def gap(self,epoch,reason):
        with self.connect() as c:c.execute('INSERT OR IGNORE INTO gaps VALUES (?,?)',(epoch,reason))
    def record(self,s,raw,previous,settings,journal):
        # No authentication envelope, raw_json, private positions or fills retained here.
        public={k:{x:v[x] for x in ('data','retrieved_at','request_started_at') if x in v} for k,v in raw.items() if k not in ('position','fills')}
        public_snapshot={k:v for k,v in s.items() if k not in ('position','scorecard')}
        payload=zlib.compress(json.dumps({'snapshot':public_snapshot,'raw':public,'previous':previous,'settings':settings},default=str).encode())
        with self.connect() as c:
            latest=c.execute('SELECT MAX(epoch) FROM observations').fetchone()[0]
            if latest is not None and s['epoch']-latest>45:
                c.execute('INSERT OR IGNORE INTO gaps VALUES (?,?)',(s['epoch'],'Unobserved interval: '+str(round(s['epoch']-latest,2))+' seconds'))
            sources=SOURCE_TEXT
            c.execute('INSERT OR IGNORE INTO implementations VALUES (?,?)',(s['model_version'],zlib.compress(json.dumps(sources).encode())))
            c.execute('INSERT INTO observations(epoch,ticker,version,decision,p,remaining,payload) VALUES (?,?,?,?,?,?,?)',
                      (s['epoch'],s['ticker'],s['model_version'],s['decision'],s['model']['p_up'],s['time_remaining_seconds'],payload))
            if 'validation' not in s:  # Legacy exploratory scorecard must not disclose new holdout performance.
                # Fixed prospective sample: first healthy forecast at <=10m and >=90s.
                if 90<=s['time_remaining_seconds']<=600 and not s['data_quality_faults']:
                    c.execute('INSERT OR IGNORE INTO contracts(ticker,day,version,p) VALUES (?,?,?,?)',(s['ticker'],s['timestamp']['utc'][:10],s['model_version'],s['model']['p_up']))
                row=c.execute('SELECT signal,signal_at,delay_checked FROM contracts WHERE ticker=? AND version=?',(s['ticker'],s['model_version'])).fetchone()
                if row and row[0] and not row[2] and s['epoch']-row[1]>=15:
                    delay=s['epoch']-row[1];cost=None
                    evaluation=s['side_evaluations'][row[0]]
                    if delay<=35 and s['decision']==row[0] and evaluation['ask'] is not None:
                        cost=float(evaluation['ask']+evaluation['fee_per_contract']+D(str(settings['slippage_reserve_cents']))/100)
                    c.execute('UPDATE contracts SET delayed_cost=?,delay_seconds=?,delay_checked=1 WHERE ticker=?',(cost,delay,s['ticker']))
                if row and not row[0] and s['decision'] in ('UP','DOWN'):
                    c.execute('UPDATE contracts SET signal=?,signal_at=? WHERE ticker=?',(s['decision'],s['epoch'],s['ticker']))
                if Path(journal).exists():
                    for line in Path(journal).read_text().splitlines():
                        try:r=json.loads(line)
                        except ValueError:continue
                        outcome=r.get('official_result')
                        if outcome in ('UP','DOWN'):c.execute('UPDATE contracts SET result=? WHERE ticker=?',(float(outcome=='UP'),r['ticker']))
            cutoff=s['epoch']-float(settings['replay_retention_days'])*86400
            c.execute('DELETE FROM observations WHERE epoch<?',(cutoff,))
            c.execute('DELETE FROM gaps WHERE epoch<?',(cutoff,))
    def scorecard(self):
        with self.connect() as c:
            rows=c.execute('SELECT day,version,p,result,signal,delayed_cost,delay_checked FROM contracts').fetchall()
            gaps=c.execute('SELECT COUNT(*) FROM gaps').fetchone()[0]
        groups={}
        for day,version,p,y,signal,cost,checked in rows:
            g=groups.setdefault(version,{'version':version,'contracts':0,'settled':0,'brier_sum':0,'signals':0,'delay_checked':0,'actionable_after_delay':0,'settled_hypothetical_entries':0,'hypothetical_net_sum':0,'days':set(),'daily':{},'probability_bins':{}})
            g['contracts']+=1;g['signals']+=bool(signal);g['delay_checked']+=bool(checked);g['actionable_after_delay']+=cost is not None
            if y is not None:
                g['settled']+=1;g['brier_sum']+=(p-y)**2;g['days'].add(day)
                daily=g['daily'].setdefault(day,{'contracts':0,'brier_sum':0})
                daily['contracts']+=1;daily['brier_sum']+=(p-y)**2
                bucket=str(min(4,int(p*5)))
                bucket_data=g['probability_bins'].setdefault(bucket,{'contracts':0,'forecast_sum':0,'up_outcomes':0})
                bucket_data['contracts']+=1;bucket_data['forecast_sum']+=p;bucket_data['up_outcomes']+=y
                if cost is not None:g['settled_hypothetical_entries']+=1;g['hypothetical_net_sum']+=float((signal=='UP')==bool(y))-cost
        for g in groups.values():
            g['brier_score']=g.pop('brier_sum')/g['settled'] if g['settled'] else None
            g['settled_days']=len(g.pop('days'))
            for daily in g['daily'].values():daily['brier_score']=daily.pop('brier_sum')/daily['contracts']
            for bucket in g['probability_bins'].values():
                bucket['mean_forecast']=bucket.pop('forecast_sum')/bucket['contracts']
                bucket['observed_up_rate']=bucket.pop('up_outcomes')/bucket['contracts']
        return {'cohorts':list(groups.values()),'outage_observations':gaps,'calibrated':False,
                'method':'One first healthy forecast per contract at 90–600 seconds remaining. First signal after enrollment; next observation 15–35 seconds later must still qualify. Missing observations are not fills. Hypothetical one-contract settlement returns; no actual P&L. Cohorts separated by version.'}

def replay(path,observation_id=None):
    """Recompute entry decision offline, refusing a different implementation version."""
    from unittest.mock import patch
    import btc_copilot as b
    with closing(sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)) as c:
        row=c.execute('SELECT id,payload FROM observations WHERE id=?',(observation_id,)).fetchone() if observation_id else c.execute('SELECT id,payload FROM observations ORDER BY id DESC LIMIT 1').fetchone()
    if not row:raise ValueError('Observation not found or outside retention window')
    saved=json.loads(zlib.decompress(row[1]));raw=saved['raw'];expected=saved['snapshot'];settings=saved['settings']
    if version(settings)!=expected['model_version']:raise ValueError('Implementation version differs; use the retained version source before replaying')
    class Client:
        def pages(self,path,params=None,max_pages=3):
            if path!='/markets':raise ValueError('Replay route unavailable')
            return {'pages':[{'data':{'markets':[raw['market']['data']['market']]}}],'complete':True}
        def market(self,t):return raw['market']
        def event(self,t):return raw['event']
        def orderbook(self,t):return raw['book']
        def get(self,path,params=None):
            if path.startswith('/series/') and path.endswith('/candlesticks'):return raw['candles']
            if path.startswith('/series/'):return raw['series']
            return raw[{'/cfbenchmarks/values':'benchmark','/markets/trades':'trades','/exchange/status':'exchange'}[path]]
    copilot=object.__new__(b.Copilot);copilot.client=Client();copilot.positions=False
    copilot.state={'previous':saved['previous'],'contracts':{}};copilot.record=lambda s:None
    with patch.object(b,'load_settings',return_value=settings),patch.object(b.time,'time',return_value=expected['epoch']),patch.object(b,'spot_get',return_value=raw['spot']):
        actual=copilot.collect(replay_health=expected['retrieval_health'])
    matches=actual['decision']==expected['decision'] and abs(actual['model']['p_up']-expected['model']['p_up'])<1e-12 and actual['entry_blockers']==expected['entry_blockers']
    return {'observation_id':row[0],'matches':matches,'expected_decision':expected['decision'],'replayed_decision':actual['decision'],'scope':'Entry decision, probability and blockers only; account guidance excluded; no network'}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['scorecard','replay']);parser.add_argument('--id',type=int)
    args=parser.parse_args();path=Path(__file__).parent/'runtime/btc_copilot_audit.sqlite3'
    print(json.dumps(replay(path,args.id) if args.command=='replay' else Audit(path).scorecard(),indent=2))
