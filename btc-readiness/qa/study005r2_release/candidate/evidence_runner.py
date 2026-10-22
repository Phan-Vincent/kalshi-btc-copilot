"""Evidence-only integration and offline preflight. No orders or account writes.

The current preserved fee protocol is blocked. No approval file may override
that condition. A future evidence-supported fee revision requires a new reviewed
protocol/source manifest. CLI preflight never authenticates or creates state.
"""
import argparse, copy, hashlib, json, math, os, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from decimal import Decimal
from btc_copilot import Copilot,ROOT,continuity_clock,FinalCutoff,ExchangeClosed,scheduled_closure_window
from btc_copilot_research import SOURCE_TEXT,load_settings,version
from coherent_runtime import EvidenceStore,RuntimeBlocked,no_links
from kalshi_readonly import KalshiReadOnly,ReadError
from readiness_policy import validate_readiness_protocol,DATES,APPROVAL_DEADLINE
from study_policy import epoch,execution_cost

PRIVATE_READ='/cfbenchmarks/values'
PUBLIC_READS=(r'/markets',r'/markets/trades',r'/markets/[A-Za-z0-9_.-]+',
              r'/markets/[A-Za-z0-9_.-]+/orderbook',r'/events/[A-Za-z0-9_.-]+',
              r'/series/KXBTC15M',r'/series/KXBTC15M/markets/[A-Za-z0-9_.-]+/candlesticks',r'/exchange/status')
EXPECTED_SCOPE=['public_market_data','cfbenchmarks_values']
# Ten minutes of consecutive failed polls stops the collector for operator review.
MAX_CONSECUTIVE_FAILURES=40
# The weekly exchange closure is 2 h; closed polls past 4 h need operator review.
MAX_EXCHANGE_CLOSED_SECONDS=4*3600

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_json(path,limit=1024*1024):
    no_links(path)
    if path.stat().st_size>limit:raise RuntimeBlocked('Oversized policy or approval file')
    return json.loads(path.read_text(),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))

WORKING_STATUS='WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE'
SEALED_STATUS='SEALED_EVIDENCE_ONLY_RELEASE'
# Files a sealed release directory may hold beyond its manifest's file list. The manifest
# itself is bound by the approval's manifest_sha256. No __pycache__: Python can load
# unlisted bytecode even under -B, so any cache in a sealed release is refused.
UNLISTED_ALLOWED={'release_manifest.json','activation_approval.json','runtime'}
# The exact reviewed file set of a sealed release; anything else is refused.
UNIT_FILES=frozenset(['btc-study-attest.service','btc-study-attest.timer','btc-study-collector.service',
    'btc-study-collector.timer','btc-study-watch.service','btc-study-watch.timer'])
SEALED_FILES=UNIT_FILES|frozenset(['README.md','acer_ops.py','binary_risk.py','btc_copilot.html','btc_copilot.py','btc_copilot_evidence.py',
    'btc_copilot_protocol.json','btc_copilot_research.py','btc_copilot_settings.json','coherent_runtime.py','evidence_runner.py',
    'fee_bound_proposal.py','fee_evidence_refresh_summary.json','fee_reconciliation.py','kalshi_readonly.py','launch.py',
    'operating_limits.json','readiness_policy.py','readiness_protocol.json','release_gate.py','request_pacing.py','risk_limits.json',
    'study_policy.py','test_fee_reconciliation.py','test_lifecycle_evidence.py','test_pacing.py','test_study002.py',
    'shakedown_summary.json'])

def _verify_inventory(root,sealed):
    root=Path(root).absolute();no_links(root)
    manifest=read_json(root/'release_manifest.json')
    if manifest.get('advice_allowed') is not False or manifest.get('execution_allowed') is not False:
        raise RuntimeBlocked('Inventory must keep advice and execution disabled')
    if manifest.get('activation_allowed') is not sealed or manifest.get('status')!=(SEALED_STATUS if sealed else WORKING_STATUS):
        raise RuntimeBlocked('Unsupported inventory status')
    required=set(SOURCE_TEXT)|{'btc_copilot_settings.json','btc_copilot_protocol.json','btc_copilot.html','evidence_runner.py',
                               'readiness_policy.py','readiness_protocol.json','release_gate.py','operating_limits.json',
                               'fee_evidence_refresh_summary.json'}
    files=manifest.get('files')
    if not isinstance(files,dict) or not required<=set(files):raise RuntimeBlocked('Incomplete source inventory')
    if sealed:
        # A sealed release has no QA marker, and nothing unlisted that Python could import.
        if 'QA_ONLY' in files or (root/'QA_ONLY').exists():raise RuntimeBlocked('Sealed release must not carry QA_ONLY')
        if set(files)!=SEALED_FILES:raise RuntimeBlocked('Sealed file set differs from the reviewed release')
        if any(marker in (root/name).read_text() for name in UNIT_FILES for marker in ('__START_UTC__','__LAUNCH_SHA256__','__ACER_OPS_SHA256__')):
            raise RuntimeBlocked('Sealed systemd unit has an unfilled placeholder')
        for entry in root.iterdir():no_links(entry)
        extra={entry.name for entry in root.iterdir()}-set(files)-UNLISTED_ALLOWED
        if extra:raise RuntimeBlocked('Unlisted files in sealed release: '+','.join(sorted(extra)))
    elif 'QA_ONLY' not in files:raise RuntimeBlocked('Working inventory must carry QA_ONLY')
    for name,expected in files.items():
        if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):raise RuntimeBlocked('Invalid inventory path')
        path=root/name;no_links(path)
        if sha(path)!=expected:raise RuntimeBlocked('Source inventory mismatch')
    try:protocol=validate_readiness_protocol(read_json(root/'readiness_protocol.json'))
    except ValueError as error:raise RuntimeBlocked('Protocol invalid: '+str(error)) from None
    if protocol['activation_allowed'] is not sealed:raise RuntimeBlocked('Protocol status does not match inventory status')
    if manifest.get('study_id')!=protocol['study_id'] or manifest.get('candidate_release')!=protocol['candidate_release']:
        raise RuntimeBlocked('Inventory/protocol identity mismatch')
    return manifest,protocol

def verify_candidate(root):
    """Working (QA) inventory: every listed file matches; activation flags stay false."""
    return _verify_inventory(root,sealed=False)

def verify_release(root):
    """Sealed release: rechecked by ReleaseGate before every collection cycle."""
    return _verify_inventory(root,sealed=True)

def preflight(root):
    """Offline diagnostics, deliberately separate from any activation operation."""
    try:
        manifest,protocol=verify_candidate(root)
        precisions=protocol['fee_policy']['balance_precisions']
        sample=execution_cost([['.50','1.00']],load_settings(Path(root)/'btc_copilot_settings.json'),precisions)
        reasons=['UNAPPROVED_PROTOCOL','NO_REVIEWED_EXECUTABLE_RELEASE','NO_COLLECTION_AUTHORIZATION']
        if '0.01' in precisions:reasons[2:2]=['FEE_EXECUTION_EVIDENCE_UNRESOLVED','POSITIVE_PRICE_CENT_FRAGMENTATION_BOUND_INFEASIBLE']
        return {'status':'BLOCKED','inventory_valid':True,'advice_enabled':False,'execution_enabled':False,
                'collection_enabled':False,'study_id':protocol['study_id'],'blocking_reasons':reasons,
                'balance_precisions':precisions,'conditional_half_dollar_stress_cost':sample['stress_cost'],
                'maximum_unit_settlement_payout':'1.00','planned_start_utc':protocol['start_utc'],
                'planned_release_utc':protocol['release_utc'],'manifest_sha256':sha(Path(root)/'release_manifest.json')}
    except (OSError,ValueError,KeyError,TypeError,RuntimeBlocked):
        return {'status':'BLOCKED','inventory_valid':False,'advice_enabled':False,'execution_enabled':False,
                'collection_enabled':False,'blocking_reasons':['SOURCE_OR_PROTOCOL_INTEGRITY_UNVERIFIED']}

class CandidateGate:
    """Current proposal has no activation path; rejects before I/O to a venue."""
    def __init__(self,root):self.root=Path(root)
    def __call__(self,path=None):
        report=preflight(self.root)
        raise RuntimeBlocked('Collection disabled: '+','.join(report['blocking_reasons']))

class ShakedownGate:
    """Explicit QA collection into a separate directory; no approval or study activation."""
    def __init__(self,root,wall=time.time,boot_time=None):
        from release_gate import host_boot_epoch
        self.root=Path(root).absolute();self.wall=wall;self.boot_time=boot_time or host_boot_epoch
    def __call__(self,path=None):
        root=self.root
        if not (root/'QA_ONLY').is_file():raise RuntimeBlocked('Shakedown requires QA_ONLY')
        verify_candidate(root)
        protocol=validate_readiness_protocol(read_json(root/'readiness_protocol.json'))
        if protocol['readiness_status']!='PROPOSED_NOT_APPROVED' or protocol['activation_allowed'] is not False:
            raise RuntimeBlocked('Shakedown protocol must remain unapproved')
        from release_gate import read_current_attestation
        now=self.wall()
        attestation=read_current_attestation(Path.home()/'btc-study/state/attestation.json',
                                             now,self.boot_time(now))
        operating=read_json(root/'operating_limits.json')
        return {'credential_config':str(Path.home()/'.config/btc-study/kalshi_readonly_config.json'),
                'runtime_dir':str(root/'runtime_shakedown'),'quota_bytes':operating['quota_bytes'],
                'free_reserve_bytes':operating['free_reserve_bytes'],'port':operating['port'],
                'fee_verified_at_utc':attestation['fee_verified_at_utc']}

def shakedown_protocol(root,now):
    destination=Path(root)/'runtime_shakedown/shakedown_protocol.json'
    if destination.exists():validate_shakedown_protocol(read_json(destination));return destination
    destination.parent.mkdir(mode=0o700,exist_ok=True)
    p=validate_readiness_protocol(read_json(Path(root)/'readiness_protocol.json'))
    start=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    for key,days in (('start_utc',0),('validation_start_utc',7),('holdout_start_utc',14),('end_utc',70),('release_utc',72)):
        p[key]=(start+timedelta(days=days)).isoformat()
    p['approval_policy']['deadline_utc']=(start-timedelta(days=1)).isoformat()
    validate_shakedown_protocol(p)
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:os.write(fd,(json.dumps(p,sort_keys=True)+'\n').encode());os.fsync(fd)
    finally:os.close(fd)
    return destination

def validate_shakedown_protocol(protocol):
    p=copy.deepcopy(protocol)
    start=datetime.fromisoformat(p['start_utc'])
    if start.tzinfo is None or start.utcoffset()!=timedelta(0) or any((start.hour,start.minute,start.second,start.microsecond)):
        raise ValueError('Shakedown start must be UTC midnight')
    for key,days in (('start_utc',0),('validation_start_utc',7),('holdout_start_utc',14),('end_utc',70),('release_utc',72)):
        if p[key]!=(start+timedelta(days=days)).isoformat():raise ValueError('Shakedown dates differ from r2 durations')
        p[key]=DATES[key]
    if protocol['approval_policy']['deadline_utc']!=(start-timedelta(days=1)).isoformat():raise ValueError('Shakedown approval deadline differs')
    p['approval_policy']['deadline_utc']=APPROVAL_DEADLINE
    validate_readiness_protocol(p)
    return copy.deepcopy(protocol)

class EvidenceClient(KalshiReadOnly):
    """Narrower than the legacy client: no portfolio, order, or setup routes."""
    def get(self,path,params=None,authenticated=False):
        if authenticated:raise RuntimeBlocked('Caller cannot request extra authentication scope')
        if path!=PRIVATE_READ and not any(re.fullmatch(pattern,path) for pattern in PUBLIC_READS):
            raise RuntimeBlocked('Endpoint outside evidence-collector scope')
        return super().get(path,params,authenticated=False)

class EvidenceCopilot(Copilot):
    def __init__(self,client,gate):
        # Do not load legacy JSON state, parent credential config or runtime files.
        self.client=client;self.collection_guard=lambda:gate()
        self.transport_guard=gate;self.positions=False;self.raw={}
        self.state={'contracts':{},'previous':None}
    def record(self,*args):raise RuntimeBlocked('Legacy file persistence forbidden in evidence collector')

def http_503(error):
    pending=[error];seen=set()
    while pending:
        current=pending.pop()
        if current is None or id(current) in seen:continue
        seen.add(id(current))
        if getattr(current,'status',None)==503:return True
        pending.extend((current.__cause__,current.__context__))
        pending.extend(getattr(current,'job_errors',()))
    return False

class Collector:
    """Testable serial pipeline; caller must supply a checked activation gate.

    No risk eligibility is presented as advice. Real CLI construction always uses
    CandidateGate, which currently blocks. Synthetic tests inject fake clients,
    clocks and gates; these seams are not exposed as command-line overrides.
    """
    def __init__(self,engine,store,gate):self.engine=engine;self.store=store;self.gate=gate;self.observed_closure=None
    def once(self,now=None):
        now=time.time() if now is None else now
        self.gate()
        self.engine.state=self.store.state()
        collected=False
        try:
            snapshot=self.engine.collect(persist=False)
            collected=True
            self.gate()
            generation=self.store.publish(self.engine,snapshot)
            return {'generation':generation,'decision':'NO TRADE','advice_enabled':False}
        except FinalCutoff as error:
            # Expected refusal (final entry cutoff): logged as its own event, never degradation or a failure.
            if not self.store.halted:self.store.event('FINAL_CUTOFF',error)
            return {'generation':None,'decision':'NO TRADE','advice_enabled':False,'final_cutoff':True}
        except ExchangeClosed as error:
            self.observed_closure=now
            # Scheduled closure (Kalshi standard hours, Thu 03:00-05:00 ET): logged as its own event, never degradation or a failure.
            if not self.store.halted:self.store.event('EXCHANGE_CLOSED',error)
            return {'generation':None,'decision':'NO TRADE','advice_enabled':False,'exchange_closed':True}
        except Exception as error:
            window=scheduled_closure_window(now)
            if (not collected and isinstance(error,ReadError) and http_503(error) and window and self.observed_closure is not None and
                window[0]<=self.observed_closure<=now<window[1]):
                detail='%s; window=%d..%d observed=%d'%(str(error)[:90],*window,self.observed_closure)
                if not self.store.halted:self.store.event('CLOSURE_STATUS_UNAVAILABLE',ReadError(detail))
                return {'generation':None,'decision':'NO TRADE','advice_enabled':False,'exchange_closed':True,'closure_inferred':True}
            if not self.store.halted:self.store.failure('COLLECTION_FAILED',error)
            raise
    def reconcile_one(self):
        self.gate()
        from request_pacing import background_reads
        with background_reads():return self.store.settle_one(self.engine.client)

class EvidenceView(BaseHTTPRequestHandler):
    def do_GET(self):
        hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts or (self.headers.get('Origin') and self.headers['Origin'] not in {'http://'+h for h in hosts}):
            self.send_error(403);return
        if self.path not in ('/latest.json','/'):self.send_error(404);return
        # One read yields the text, snapshot and generation. No independent files.
        envelope=self.server.store.latest()
        if self.path=='/latest.json':body=json.dumps(envelope,default=str,allow_nan=False).encode();kind='application/json'
        else:
            import html
            text=envelope.get('text','NO TRADE / DATA UNAVAILABLE')
            body=('<!doctype html><meta charset="utf-8"><title>BTC research — advice disabled</title><h1>Evidence only — NO TRADE</h1><p>Snapshot only. Refresh to recheck freshness.</p><pre>'+html.escape(text)+'</pre>').encode();kind='text/html; charset=utf-8'
        self.send_response(200);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'")
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

def run_authorized(root,gate,*,client_factory=EvidenceClient,engine_factory=EvidenceCopilot,
                   store_factory=EvidenceStore,server_factory=ThreadingHTTPServer,
                   max_cycles=None,wall=time.time,monotonic=continuity_clock,sleep=time.sleep,
                   shakedown=False,protocol_path=None,stop=None):
    """Executable collection lifecycle behind a release-owned gate.

    Current CandidateGate always rejects; no CLI switch bypasses it. Injection
    arguments exist for isolated lifecycle tests and are never CLI parameters.
    A future reviewed gate must bind the returned paths, source hashes, scope,
    clocks and operational limits before this factory is authorized for use.
    """
    context=gate() # Must precede *all* constructors and file/runtime operations.
    root=Path(root).absolute()
    protocol_file=Path(protocol_path) if shakedown else root/'readiness_protocol.json'
    validator=validate_shakedown_protocol if shakedown else validate_readiness_protocol
    protocol=validator(read_json(protocol_file))
    settings=load_settings(root/'btc_copilot_settings.json')
    operating=read_json(root/'operating_limits.json')
    if (operating['advice_enabled'] is not False or operating['execution_enabled'] is not False or
        operating['interval_seconds']!=15 or operating['port']!=8769 or operating['bind_host']!='127.0.0.1' or
        operating['outcomes_per_poll']!=1 or operating['runtime_subdirectory']!='runtime'):
        raise RuntimeBlocked('Unsupported operating policy')
    expected_runtime=root/('runtime_shakedown' if shakedown else 'runtime')
    if Path(context['runtime_dir']).absolute()!=expected_runtime:raise RuntimeBlocked('Runtime destination must be bound to this bundle')
    if any(context[key]!=operating[key] for key in ('quota_bytes','free_reserve_bytes','port')):
        raise RuntimeBlocked('Operating context does not match frozen limits')
    client=client_factory(config=context['credential_config'],guard=gate)
    engine=engine_factory(client,gate)
    store=store_factory(context['runtime_dir'],protocol_file,version(settings),SOURCE_TEXT,
                        quota_bytes=context['quota_bytes'],free_reserve_bytes=context['free_reserve_bytes'],
                        validator=validator)
    server=None;thread=None;clean=False;cycles=0;failures=0;settle_failures=0;previous=None;closed_since=None
    def halted():
        root_dir=getattr(store,'root',None)
        return store.halted or (root_dir is not None and (Path(root_dir)/'HALTED').exists())
    def tolerate(count):
        # Halted stores and revoked gates are fatal; other failures were recorded
        # as degradation. Persistent failure stops for operator review.
        if halted():raise
        gate()
        if count>=MAX_CONSECUTIVE_FAILURES:raise RuntimeBlocked('Persistent failure requires operator review')
    stop_reason=None
    try:
        if hasattr(store,'event'):store.event('COLLECTOR_START')
        if not shakedown:
            if context['port']!=8769:raise RuntimeBlocked('Only the separately planned evidence port is supported')
            server=server_factory(('127.0.0.1',8769),EvidenceView);server.store=store
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        collector=Collector(engine,store,gate)
        while max_cycles is None or cycles<max_cycles:
            # Orderly stop, checked only between cycles. main() wires it for --shakedown alone; the study
            # keeps "any unplanned stop is unclean and needs operator review".
            if stop is not None and stop():break
            gate();started=monotonic();now=wall()
            if not math.isfinite(now):raise RuntimeBlocked('Invalid runtime clock')
            # Wall-clock steps between polls (not sleep: the continuity clock counts sleep)
            # stop uncleanly for review, including a step across the release boundary.
            if previous and abs((now-previous[0])-(started-previous[1]))>1:raise RuntimeBlocked('Wall-clock step between polls')
            previous=(now,started)
            if now>=epoch(protocol['release_utc']):break
            if now<epoch(protocol['start_utc']):raise RuntimeBlocked('Collection window has not opened')
            if now<epoch(protocol['end_utc']):
                try:
                    result=collector.once(now)
                except Exception:
                    # A later fresh poll may recover; missed windows are never backfilled.
                    closed_since=None
                    failures+=1;tolerate(failures)
                else:
                    if result.get('exchange_closed'):
                        # A closed poll is an explained pause: it neither resets nor advances failures.
                        if closed_since is None:closed_since=now
                        elif now-closed_since>MAX_EXCHANGE_CLOSED_SECONDS:
                            raise RuntimeBlocked('Exchange closed longer than expected; operator review')
                    else:
                        closed_since=None
                        if not result.get('final_cutoff'):failures=0
            if not shakedown:
                try:collector.reconcile_one();settle_failures=0
                except Exception:
                    settle_failures+=1;tolerate(settle_failures)
            cycles+=1
            if max_cycles is not None and cycles>=max_cycles:break
            # No overlapping scheduled observations; delayed cycles remain gaps.
            sleep(next_poll_delay(now,monotonic()-started))
        clean=True
        return {'status':'STOPPED','cycles':cycles,'advice_enabled':False,'execution_enabled':False}
    except BaseException as error:
        stop_reason=error
        raise
    finally:
        if server is not None:server.shutdown();server.server_close()
        if thread is not None:thread.join(timeout=5)
        if hasattr(store,'event') and not store.halted:
            try:store.event('COLLECTOR_STOP',stop_reason or RuntimeBlocked('orderly stop'))
            except Exception:clean=False
        store.close(clean=clean)

FINAL_CUTOFF_SECONDS=90  # btc_copilot_settings final_entry_cutoff_seconds; a poll starting later can never be recorded
LISTING_DELAY_SECONDS=35  # Kalshi lists the next contract 15-30+ s after its open (live: polls at +15 s and +30 s failed)

def next_poll_delay(now,elapsed):
    nominal=max(0,15-elapsed)
    opened=math.floor(now/900)*900
    specials=[opened+305,opened+320]
    finished=now+elapsed
    upcoming=[target-finished for target in specials if target>finished]
    delay=max(0,min([nominal]+upcoming))
    # Skip only polls that are certain to fail: from the final cutoff until the next contract is listed.
    target=finished+delay;slot=math.floor(target/900)*900
    if target-slot<LISTING_DELAY_SECONDS:delay=slot+LISTING_DELAY_SECONDS-finished
    elif target>slot+900-FINAL_CUTOFF_SECONDS:delay=slot+900+LISTING_DELAY_SECONDS-finished
    return max(0,delay)

def report_study(root,fee_evidence,now=None):
    """Study 005 release report: the collector's own database and the readiness validator.

    Offline and read-only. Run through release_tool.py post-study-report, which first verifies the fee
    summary against the raw records on the Mac. Holdout outcomes stay locked until the real release time."""
    from btc_copilot_evidence import report
    return report(Path(root).absolute()/'runtime'/'evidence.sqlite3',time.time() if now is None else now,
                  validator=validate_readiness_protocol,fee_evidence=fee_evidence)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--shakedown',action='store_true',help='QA-only live GET collection into runtime_shakedown')
    parser.add_argument('--report',metavar='VERIFIED_FEE_SUMMARY',help='post-study report (use release_tool.py post-study-report)')
    parser.add_argument('--summary-sha256',help='SHA-256 of the raw-verified summary bytes (supplied by release_tool.py post-study-report)')
    args=parser.parse_args(argv)
    if args.report:
        import sqlite3
        path=Path(args.report)
        if not args.summary_sha256 or sha(path)!=args.summary_sha256:
            print('REPORT NOT PRODUCED: the fee summary is not the raw-verified snapshot; use release_tool.py post-study-report');return 2
        try:
            summary=read_json(path);summary['_input_sha256']=args.summary_sha256
            print(json.dumps(report_study(ROOT,summary),indent=2,default=str));return 0
        except (OSError,ValueError,KeyError,TypeError,RuntimeBlocked,sqlite3.Error) as error:
            print('REPORT NOT PRODUCED: %s: %s'%(type(error).__name__,error));return 2
    if args.shakedown:
        try:
            gate=ShakedownGate(ROOT);gate();protocol_path=shakedown_protocol(ROOT,time.time())
            # SIGTERM (systemctl stop) asks for an orderly stop: the poll sleep wakes at once and the loop exits
            # after the current cycle, logging COLLECTOR_STOP and removing RUNNING so the summary can run.
            import signal
            stopping=threading.Event()
            signal.signal(signal.SIGTERM,lambda *args:stopping.set())
            result=run_authorized(ROOT,gate,shakedown=True,protocol_path=protocol_path,sleep=stopping.wait,stop=stopping.is_set)
        except (RuntimeBlocked,ValueError,OSError) as error:print(str(error));return 2
        print(json.dumps(result));return 0
    if args.collect:
        # Authorization/protocol gate runs before construction, credentials,
        # runtime creation, network requests or any local listener.
        # A QA candidate always uses the refusing CandidateGate. A frozen release uses
        # ReleaseGate, which still refuses until its protocol permits activation.
        if (ROOT/'QA_ONLY').exists():gate=CandidateGate(ROOT)
        else:
            from release_gate import ReleaseGate
            gate=ReleaseGate(ROOT,validator=validate_readiness_protocol,verify=verify_release)
        try:return_code=run_authorized(ROOT,gate)
        except (RuntimeBlocked,ValueError,OSError) as error:print(str(error));return 2
        print(json.dumps(return_code));return 0
    print(json.dumps(preflight(ROOT),indent=2));return 2

if __name__=='__main__':raise SystemExit(main())
