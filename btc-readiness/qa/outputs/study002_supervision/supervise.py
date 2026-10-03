"""Study002 lifecycle only. Never imports a study evaluator or opens evidence databases/journals."""
from pathlib import Path
import argparse,datetime,fcntl,hashlib,json,math,os,shlex,signal,subprocess,sys,tempfile,time
from contextlib import contextmanager

WORKSPACE=Path(__file__).resolve().parents[2]
TARGET=WORKSPACE/'outputs/copilot_study002_fee_review'
CONTROL=WORKSPACE/'work/study002_supervision'
MANIFEST='848151a08e737c1604c04d2ecdee58f38e6978b4314fca03b8f4e77893dede88'
FILES={'btc_copilot.py','btc_copilot_evidence.py','btc_copilot_research.py','kalshi_readonly.py','study_policy.py','btc_copilot_protocol.json','btc_copilot_settings.json','btc_copilot.html','request_pacing.py','fee_reconciliation.py'}
START=datetime.datetime.fromisoformat('2026-10-01T00:00:00+00:00').timestamp()
END=datetime.datetime.fromisoformat('2026-12-12T00:00:00+00:00').timestamp()

def utc(v):
    d=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
    if d.utcoffset() is None or d.utcoffset().total_seconds()!=0:raise ValueError('UTC required')
    return d.timestamp()
def stamp(t):return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
def finite(v):return type(v) in (int,float) and math.isfinite(v)
def read_json(path,optional=False):
    if path.is_symlink():raise ValueError('Linked supervision input rejected')
    if optional and not path.exists():return {}
    with path.open() as f:r=json.load(f)
    if not isinstance(r,dict):raise ValueError('Expected JSON object')
    return r

def seal(target=TARGET):
    if target.is_symlink():raise ValueError('Linked bundle rejected')
    path=target/'release_manifest.json'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=MANIFEST:raise ValueError('Approved release seal differs')
    m=read_json(path)
    if set(m['files'])!=FILES:raise ValueError('Incomplete manifest')
    for name,h in m['files'].items():
        path=target/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=h:raise ValueError('Source seal differs')

def approval_valid(a,target,now):
    return (a.get('approved') is True and a.get('study_id')=='btc-prospective-002' and
            a.get('bundle_path')==str(target.resolve()) and a.get('manifest_sha256')==MANIFEST and
            a.get('scope')=='EVIDENCE_ONLY; no profitability validation, advice, orders or account changes' and
            utc(a['approved_at_utc'])<START and utc(a['approved_at_utc'])<=now)

def receipt_valid(r,now):
    try:
        if not finite(now) or not 0<=now-utc(r['checked_at_utc'])<=1200:return False
        if not 0<=now-utc(r['rules_checked_at_utc'])<72000:return False
        if r.get('clock_query_ok') is not True:return False
        if not finite(r['clock_offset_seconds']) or not finite(r['clock_uncertainty_seconds']) or r['clock_uncertainty_seconds']<0:return False
        if abs(r['clock_offset_seconds'])+r['clock_uncertainty_seconds']>1:return False
        expected={'official_fee_docs_verified':True,'rounding_rules_verified':True,'series_metadata_verified':True,'account_limits_verified':True,'endpoint_costs_verified':True,'fee_type':'quadratic','coefficient':'0.07','multiplier':'1','minimum_quantity':'0.01','default_cost':10,'benchmark_cost':50}
        if any(r.get(k)!=v for k,v in expected.items()):return False
        return finite(r['read_refill']) and finite(r['read_capacity']) and r['read_refill']>=200 and r['read_capacity']>=600
    except (KeyError,TypeError,ValueError):return False

def assess(a,state,health,receipt,now,target=TARGET):
    """Pure decision; no outcomes, mutations, account access or side effects."""
    if not finite(now):return {'action':'BLOCK','reason':'invalid_clock'}
    if state.get('paused') is True:return {'action':'PAUSED','reason':'explicit_stop_latch'}
    try:
        if not approval_valid(a,target,now):return {'action':'BLOCK','reason':'approval_mismatch'}
    except (KeyError,TypeError,ValueError):return {'action':'BLOCK','reason':'invalid_approval'}
    if now<START:return {'action':'WAIT','reason':'before_start'}
    if now>=END:return {'action':'COMPLETE','reason':'window_closed'}
    if not health.get('lock_known',False):return {'action':'BLOCK','reason':'lock_state_unavailable'}
    running=health.get('lock_held') or health.get('pid_live')
    try:
        fee_age=now-utc(a['fee_verified_at_utc']);rate_age=now-utc(a['rate_verified_at_utc'])
        stale=not 0<=fee_age<=86400 or not 0<=rate_age<=86400
        due=stale or max(fee_age,rate_age)>=72000
    except (KeyError,ValueError,TypeError):return {'action':'VERIFY','reason':'invalid_attestations'}
    if due:return {'action':'VERIFY','reason':'expired_attestation' if stale else 'renew_at_20_hours','running':bool(running)}
    if not receipt_valid(receipt,now):return {'action':'VERIFY','reason':'fresh_clock_and_rules_required','running':bool(running)}
    if running:
        if not health.get('lock_held') or not health.get('pid_live') or not health.get('pid_matches'):return {'action':'BLOCK','reason':'process_identity_or_lock_conflict'}
        epoch=health.get('epoch');age=now-epoch if finite(epoch) else float('inf');expiry=health.get('expiry',0)
        if not finite(age) or not finite(expiry) or age<0 or age>20 or now>=expiry:return {'action':'ATTENTION','reason':'stale_or_future_snapshot_no_duplicate_restart'}
        if health.get('error') or not health.get('shadow'):return {'action':'ATTENTION','reason':'collection_failure_or_advice_gate'}
        return {'action':'HEALTHY','reason':'fresh_shadow_snapshot'}
    if not isinstance(state.get('launch_attempts',[]),list) or any(not finite(t) or t>now for t in state.get('launch_attempts',[])):return {'action':'BLOCK','reason':'invalid_restart_history'}
    attempts=[t for t in state.get('launch_attempts',[]) if finite(t) and 0<=now-t<86400]
    if len(attempts)>=3:return {'action':'BLOCK','reason':'restart_budget_exhausted'}
    if attempts and now-max(attempts)<900:return {'action':'WAIT','reason':'restart_backoff'}
    if health.get('last_pid_seen') and not health.get('pid_known'):return {'action':'BLOCK','reason':'cannot_verify_previous_pid'}
    return {'action':'LAUNCH','reason':'approved_checked_window','late_start_or_gap':now>START+330}

@contextmanager
def mutex(base=None):
    base=CONTROL if base is None else base
    if base.is_symlink():raise ValueError('Linked control directory')
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    p=base/'supervisor.lock'
    if p.is_symlink():raise ValueError('Linked mutex')
    fd=os.open(p,os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(fd,'r+') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);yield

def write_atomic(path,data):
    if path.is_symlink():raise ValueError('Linked write target')
    fd,name=tempfile.mkstemp(prefix='.supervision-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def event(kind,data,base=None):
    base=CONTROL if base is None else base
    p=base/'events.jsonl'
    if p.is_symlink():raise ValueError('Linked log')
    fd=os.open(p,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    with os.fdopen(fd,'a') as f:f.write(json.dumps({'at':stamp(time.time()),'kind':kind,**data})+'\n')

def health(target=TARGET):
    rt=target/'runtime';h={'lock_known':True,'lock_held':False,'pid_known':True,'pid_live':False,'pid_matches':False}
    if rt.is_symlink():raise ValueError('Linked runtime')
    lock=rt/'btc_copilot.lock'
    if lock.is_symlink():raise ValueError('Linked collector lock')
    if lock.exists():
        try:
            with lock.open('r') as f:
                try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:h['lock_held']=True
        except OSError:h['lock_known']=False
    runtime=read_json(rt/'btc_copilot_runtime.json',True)
    pid=runtime.get('pid');h['last_pid_seen']=bool(pid)
    if type(pid) is int and pid>1:
        h['pid']=pid
        try:os.kill(pid,0);h['pid_live']=True
        except ProcessLookupError:pass
        except PermissionError:h.update(pid_known=False,pid_live=True)
        if h['pid_live']:
            r=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True,timeout=3)
            if r.returncode:h['pid_known']=False
            else:
                args=shlex.split(r.stdout.strip())
                h['pid_matches']=len(args)==7 and args[1:]==[str(target/'btc_copilot.py'),'--watch','--interval','15','--port','8767']
    elif pid is not None:h['pid_known']=False;h['last_pid_seen']=True
    latest=read_json(target/'btc_copilot_latest.json',True)
    validation=latest.get('validation');position=latest.get('position')
    validation=validation if isinstance(validation,dict) else {}
    position=position if isinstance(position,dict) else {}
    h.update(epoch=latest.get('epoch'),expiry=latest.get('valid_until_epoch'),error=bool(latest.get('error')),
             shadow=latest.get('decision')=='NO TRADE' and validation.get('status')=='UNVALIDATED_SHADOW_ONLY' and position.get('guidance_valid') is False)
    return h

def inspect(target=TARGET,base=CONTROL,now=None):
    now=time.time() if now is None else now;seal(target)
    a=read_json(target/'activation_approval.json');state=read_json(base/'control.json',True)
    h=health(target);r=read_json(base/'preflight_receipt.json',True)
    return assess(a,state,h,r,now,target),a,state,h,r

def renew(target=TARGET,base=CONTROL):
    now=time.time();seal(target);a=read_json(target/'activation_approval.json');state=read_json(base/'control.json',True);receipt=read_json(base/'preflight_receipt.json')
    if state.get('paused') or not approval_valid(a,target,now) or now>=END or not receipt_valid(receipt,now):raise ValueError('Renewal requirements not satisfied')
    updates={'fee_verified_at_utc':receipt['rules_checked_at_utc'],'rate_verified_at_utc':receipt['rules_checked_at_utc'],'fee_rules_verified':True,'read_refill_tokens_per_second':receipt['read_refill'],'read_bucket_capacity':receipt['read_capacity'],'default_read_cost':10,'benchmark_read_cost':50}
    write_atomic(target/'activation_approval.json',dict(a,**updates))
    event('verified_renewal',{'receipt_sha256':hashlib.sha256((base/'preflight_receipt.json').read_bytes()).hexdigest(),'verified_at':receipt['rules_checked_at_utc']},base)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['inspect','renew','launch','pause','resume']);args=parser.parse_args()
    if args.command=='inspect':print(json.dumps(inspect()[0]));return
    with mutex():
        if args.command=='renew':renew();print('{"renewed":true}');return
        if args.command=='pause':
            state=read_json(CONTROL/'control.json',True)
            state['paused']=True;write_atomic(CONTROL/'control.json',state);event('explicit_pause',{})
            h=health(TARGET)
            if h.get('lock_held') and not h.get('pid_live'):raise ValueError('Pause latched; lock held by unknown process, not signaled')
            if h.get('pid_live'):
                if not h.get('pid_matches') or not h.get('lock_held'):raise ValueError('Pause latched; process identity unverified, not signaled')
                os.kill(h['pid'],signal.SIGTERM)
            print('{"pause_latched":true}');return
        decision,a,state,h,r=inspect()
        if args.command=='resume':
            state['paused']=False;write_atomic(CONTROL/'control.json',state);event('explicit_resume',{});print('{"resumed_supervision_only":true}');return
        if decision['action']!='LAUNCH':raise ValueError('Launch refused: '+decision['reason'])
        state.setdefault('launch_attempts',[]).append(time.time());write_atomic(CONTROL/'control.json',state)
        event('launch_attempt',{'late_start_or_gap':decision['late_start_or_gap']})
        path=CONTROL/'collector.log'
        if path.is_symlink():raise ValueError('Linked process log')
        fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        with os.fdopen(fd,'a') as log:
            child=subprocess.Popen([sys.executable,str(TARGET/'btc_copilot.py'),'--watch','--interval','15','--port','8767'],cwd=TARGET,stdout=log,stderr=log,start_new_session=True)
        event('process_spawned_not_yet_healthy',{'pid':child.pid});print(json.dumps({'spawned_pid':child.pid,'health_verified':False}))

if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'action':'BLOCK','error_kind':type(e).__name__,'reason':str(e) if isinstance(e,ValueError) else 'Supervisor operation unavailable'}));raise SystemExit(1)
