"""Evidence-only integration and offline preflight. No orders or account writes.

The current preserved fee protocol is blocked. No approval file may override
that condition. A future evidence-supported fee revision requires a new reviewed
protocol/source manifest. CLI preflight never authenticates or creates state.
"""
import argparse, copy, hashlib, json, math, re, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from decimal import Decimal
from btc_copilot import Copilot,ROOT,continuity_clock
from btc_copilot_research import SOURCE_TEXT,load_settings,version
from coherent_runtime import EvidenceStore,RuntimeBlocked,no_links
from kalshi_readonly import KalshiReadOnly,ReadError
from readiness_policy import validate_readiness_protocol
from study_policy import epoch,execution_cost

PRIVATE_READ='/cfbenchmarks/values'
PUBLIC_READS=(r'/markets',r'/markets/trades',r'/markets/[A-Za-z0-9_.-]+',
              r'/markets/[A-Za-z0-9_.-]+/orderbook',r'/events/[A-Za-z0-9_.-]+',
              r'/series/KXBTC15M',r'/series/KXBTC15M/markets/[A-Za-z0-9_.-]+/candlesticks',r'/exchange/status')
EXPECTED_SCOPE=['public_market_data','cfbenchmarks_values']
# Ten minutes of consecutive failed polls stops the collector for operator review.
MAX_CONSECUTIVE_FAILURES=40

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
    'study_policy.py','test_fee_reconciliation.py','test_lifecycle_evidence.py','test_pacing.py','test_study002.py'])

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

class Collector:
    """Testable serial pipeline; caller must supply a checked activation gate.

    No risk eligibility is presented as advice. Real CLI construction always uses
    CandidateGate, which currently blocks. Synthetic tests inject fake clients,
    clocks and gates; these seams are not exposed as command-line overrides.
    """
    def __init__(self,engine,store,gate):self.engine=engine;self.store=store;self.gate=gate
    def once(self):
        self.gate()
        self.engine.state=self.store.state()
        try:
            snapshot=self.engine.collect(persist=False)
            self.gate()
            generation=self.store.publish(self.engine,snapshot)
            return {'generation':generation,'decision':'NO TRADE','advice_enabled':False}
        except Exception:
            if not self.store.halted:self.store.failure('COLLECTION_FAILED')
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
                   max_cycles=None,wall=time.time,monotonic=continuity_clock,sleep=time.sleep):
    """Executable collection lifecycle behind a release-owned gate.

    Current CandidateGate always rejects; no CLI switch bypasses it. Injection
    arguments exist for isolated lifecycle tests and are never CLI parameters.
    A future reviewed gate must bind the returned paths, source hashes, scope,
    clocks and operational limits before this factory is authorized for use.
    """
    context=gate() # Must precede *all* constructors and file/runtime operations.
    root=Path(root).absolute()
    protocol=validate_readiness_protocol(read_json(root/'readiness_protocol.json'))
    settings=load_settings(root/'btc_copilot_settings.json')
    operating=read_json(root/'operating_limits.json')
    if (operating['advice_enabled'] is not False or operating['execution_enabled'] is not False or
        operating['interval_seconds']!=15 or operating['port']!=8769 or operating['bind_host']!='127.0.0.1' or
        operating['outcomes_per_poll']!=1 or operating['runtime_subdirectory']!='runtime'):
        raise RuntimeBlocked('Unsupported operating policy')
    if Path(context['runtime_dir']).absolute()!=root/'runtime':raise RuntimeBlocked('Runtime destination must be bound to this bundle')
    if any(context[key]!=operating[key] for key in ('quota_bytes','free_reserve_bytes','port')):
        raise RuntimeBlocked('Operating context does not match frozen limits')
    client=client_factory(config=context['credential_config'],guard=gate)
    engine=engine_factory(client,gate)
    store=store_factory(context['runtime_dir'],root/'readiness_protocol.json',version(settings),SOURCE_TEXT,
                        quota_bytes=context['quota_bytes'],free_reserve_bytes=context['free_reserve_bytes'],
                        validator=validate_readiness_protocol)
    server=None;thread=None;clean=False;cycles=0;failures=0;settle_failures=0;previous=None
    def halted():
        root_dir=getattr(store,'root',None)
        return store.halted or (root_dir is not None and (Path(root_dir)/'HALTED').exists())
    def tolerate(count):
        # Halted stores and revoked gates are fatal; other failures were recorded
        # as degradation. Persistent failure stops for operator review.
        if halted():raise
        gate()
        if count>=MAX_CONSECUTIVE_FAILURES:raise RuntimeBlocked('Persistent failure requires operator review')
    try:
        if context['port']!=8769:raise RuntimeBlocked('Only the separately planned evidence port is supported')
        server=server_factory(('127.0.0.1',8769),EvidenceView);server.store=store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        collector=Collector(engine,store,gate)
        while max_cycles is None or cycles<max_cycles:
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
                    collector.once();failures=0
                except Exception:
                    # A later fresh poll may recover; missed windows are never backfilled.
                    failures+=1;tolerate(failures)
            try:
                collector.reconcile_one();settle_failures=0
            except Exception:
                settle_failures+=1;tolerate(settle_failures)
            cycles+=1
            if max_cycles is not None and cycles>=max_cycles:break
            # No overlapping scheduled observations; delayed cycles remain gaps.
            sleep(max(0,15-(monotonic()-started)))
        clean=True
        return {'status':'STOPPED','cycles':cycles,'advice_enabled':False,'execution_enabled':False}
    finally:
        if server is not None:server.shutdown();server.server_close()
        if thread is not None:thread.join(timeout=5)
        store.close(clean=clean)

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
