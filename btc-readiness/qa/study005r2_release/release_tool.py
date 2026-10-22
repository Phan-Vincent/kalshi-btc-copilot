"""Working-inventory and sealed-release manifests for the Study 005 revision-2 candidate.

    python3 -B release_tool.py inventory          # rewrite candidate/release_manifest.json (still QA_ONLY)
    python3 -B release_tool.py seal OUT_DIR       # build a sealed copy in OUT_DIR; the candidate is untouched

`seal` is the mechanical part of the freeze. It does not decide anything: the start
date comes from readiness_policy.START, which the freeze sets after review, and the
result still needs the final review, the user's approval file and `install.sh activate`.
Offline only; no network, credentials or account access.
"""
import argparse, hashlib, importlib.util, json, math, shutil, sqlite3, subprocess, sys, tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE / 'candidate'
WORKING_STATUS = 'WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE'
SEALED_STATUS = 'SEALED_EVIDENCE_ONLY_RELEASE'
MAX_EVIDENCE_AGE = timedelta(hours=72)
SKIP = {'release_manifest.json', 'activation_approval.json', 'runtime', 'runtime_shakedown', '__pycache__', '.DS_Store'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def shakedown_source_digest(root):
    excluded=SKIP|{'shakedown_summary.json','fee_evidence_refresh_summary.json'}
    files={p.name:sha(p) for p in sorted(Path(root).iterdir()) if p.is_file() and p.name not in excluded}
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def write_manifest(root, sealed):
    root = Path(root)
    protocol = json.loads((root / 'readiness_protocol.json').read_text())
    files = {p.name: sha(p) for p in sorted(root.iterdir()) if p.name not in SKIP and p.is_file() and not p.is_symlink()}
    manifest = {
        'status': SEALED_STATUS if sealed else WORKING_STATUS,
        'study_id': protocol['study_id'],
        'candidate_release': protocol['candidate_release'],
        'activation_allowed': sealed, 'advice_allowed': False, 'execution_allowed': False,
        'scope': ('Sealed evidence-only release. Collection still requires a host-bound activation_approval.json '
                  'and a fresh attestation; advice and execution stay disabled.') if sealed else
                 ('Working Study 005 revision-2 inventory for QA. Not a release seal; activation, advice and '
                  'execution remain disabled.'),
        'files': files,
    }
    (root / 'release_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def _fee_reconciliation():
    # Load the candidate's own checker by path under a private name (no sys.modules reuse).
    spec = importlib.util.spec_from_file_location('_seal_fee_reconciliation', CANDIDATE / 'fee_reconciliation.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def check_fee_evidence(root, now=None):
    """The .0001 adoption rests on the account's own fee records: recompute the tripwire, and require it recent."""
    summary = json.loads((Path(root) / 'fee_evidence_refresh_summary.json').read_text())
    found = _fee_reconciliation().fee_evidence_problems(summary)
    if found:
        raise SystemExit('Fee evidence tripwire failed: ' + '; '.join(found))
    now = now or datetime.now(timezone.utc)
    age = now - datetime.fromisoformat(summary['retrieved_at'])
    if not timedelta(0) <= age <= MAX_EVIDENCE_AGE:
        raise SystemExit('Fee evidence is stale; re-run fee_evidence_refresh.py and copy its summary into candidate/')
    return summary


def check_raw_evidence(root):
    """Re-derive the summary from the raw private records on this Mac; an edited summary is refused."""
    spec = importlib.util.spec_from_file_location('_seal_fee_refresh', HERE / 'fee_evidence_refresh.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    found = module.raw_problems(json.loads((Path(root) / 'fee_evidence_refresh_summary.json').read_text()))
    if found:
        raise SystemExit('Fee evidence does not match the raw records: ' + '; '.join(found))


def check_candidate_tree(root):
    """Only plain regular files (and nothing linked) may be sealed."""
    bad = [p.name for p in Path(root).iterdir() if p.name not in SKIP and (p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1)]
    if bad:
        raise SystemExit('Refusing to seal links or non-regular entries: ' + ', '.join(sorted(bad)))

def shakedown_summary(runtime=None,now=None):
    """Read only timing, checkpoint and reason tables; never read outcomes or results."""
    runtime=Path(runtime or CANDIDATE/'runtime_shakedown').absolute()
    if (runtime/'RUNNING').exists():raise SystemExit('Stop the shakedown collector cleanly before summarizing')
    path=runtime/'evidence.sqlite3'
    now=now or datetime.now(timezone.utc)
    now_epoch=now.timestamp()
    with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
        starts=[x[0] for x in db.execute("SELECT epoch FROM runtime_events WHERE kind='COLLECTOR_START' ORDER BY epoch")]
        if not starts:raise SystemExit('Shakedown has no recorded collector start')
        start=starts[0]
        checkpoints={x[0] for x in db.execute('SELECT opened FROM markets WHERE checkpoint IS NOT NULL')}
        vetoes=list(db.execute('SELECT opened,reason FROM slot_events'))
        events=list(db.execute('SELECT epoch,kind,detail FROM runtime_events ORDER BY epoch'))
        generations=[x[0] for x in db.execute('SELECT epoch FROM generations ORDER BY epoch')]
    ended=min(now_epoch,max([start]+generations+[x[0] for x in events]))
    first=math.ceil(start/900)*900
    # Every slot whose 330 s checkpoint band has elapsed counts (matches shakedown_check.py); the old
    # floor(ended/900) bound silently dropped the final slot even when its band had completed.
    slots=[s for s in range(first,math.floor((ended-330)/900)*900+1,900)]
    missed={}
    for opened in slots:
        if opened in checkpoints:continue
        reasons=[reason for slot,reason in vetoes if slot==opened]
        reasons += [detail for epoch,kind,detail in events if kind in ('COLLECTION_FAILED','EXCHANGE_CLOSED','CLOSURE_STATUS_UNAVAILABLE') and opened+300<=epoch<=opened+330 and detail]
        for epoch,kind,detail in events:
            if kind!='REBOOT_RESUME':continue
            try:
                previous=json.loads(detail)['last_observation_epoch']
                if (type(previous) in (int,float) and math.isfinite(previous) and
                    previous<epoch and opened+300<epoch and opened+330>previous):
                    reasons.append('reboot')
            except (TypeError,ValueError,KeyError):pass
        stops=[(epoch,detail) for epoch,kind,detail in events if kind=='COLLECTOR_STOP' and epoch<=opened+330]
        if stops:
            stopped,detail=stops[-1]
            # Only a start that had the collector up for the whole band clears the miss; a restart at
            # opened+329 still leaves the band unobserved, so the stop reason survives.
            if not any(epoch>stopped and epoch<=opened+300 and kind=='COLLECTOR_START' for epoch,kind,_ in events):reasons.append(detail or 'Collector stopped')
        missed[opened]=sorted(set(reasons))
    hourly=[]
    for hour in range(math.floor(start/3600)*3600,math.ceil(ended/3600)*3600,3600):
        fail=sum(hour<=epoch<hour+3600 and kind=='COLLECTION_FAILED' for epoch,kind,_ in events)
        good=sum(hour<=epoch<hour+3600 for epoch in generations)
        inside=[s for s in slots if hour<=s<hour+3600]
        hourly.append({'hour_utc':datetime.fromtimestamp(hour,timezone.utc).isoformat(),'failed':fail,'successful':good,
                       'failure_rate':fail/(fail+good) if fail+good else None,
                       'partial_hour':hour<start or hour+3600>ended,
                       # An empty hour is excused only when every slot in it is a named miss: an
                       # unexplained empty full hour is still a seal failure.
                       'inactivity_explained':fail+good==0 and bool(inside) and all(missed.get(s) for s in inside)})
    size=sum(p.stat().st_size for p in runtime.iterdir() if p.is_file())
    quota=json.loads((CANDIDATE/'operating_limits.json').read_text())['quota_bytes']
    hours=max(0,(ended-start)/3600)
    with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
        page_size=db.execute('PRAGMA page_size').fetchone()[0]
    cap=((quota-1024**2)//(3*page_size))*page_size
    measured_per_day=size/max(hours,1)*24
    projected=int(measured_per_day*72*2)
    summary={'status':'QA_SHAKEDOWN_NOT_STUDY_EVIDENCE','generated_at_utc':now.isoformat(),
             'collection_ended_at_utc':datetime.fromtimestamp(ended,timezone.utc).isoformat(),'source_runtime':str(runtime),
             'source_database_sha256':sha(path),'candidate_source_sha256':shakedown_source_digest(CANDIDATE),
             'hours_covered':hours,'slots':len(slots),
             'enrolled_checkpoints':len(slots)-len(missed),
             'missed_slots':[{'slot_opened_utc':datetime.fromtimestamp(opened,timezone.utc).isoformat(),'reasons':reasons}
                             for opened,reasons in missed.items()],
             'all_misses_have_reasons':all(missed.values()),
             'poll_failure_rate_by_hour':hourly,
             'unexplained_collection_failures':sum(kind=='COLLECTION_FAILED' and not detail for _,kind,detail in events),
             'storage_growth':{'observed_bytes':size,'measured_bytes_per_day':measured_per_day,
                               'uncertainty_multiplier':2,'projected_72_day_bytes':projected,
                               'quota_bytes':quota,'sqlite_page_size':page_size,'main_database_cap_bytes':cap,
                               'projected_fraction_of_quota':projected/quota,
                               'projected_fraction_of_main_database_cap':projected/cap}}
    (CANDIDATE/'shakedown_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

def shakedown_rate_ok(row):
    """A zero-attempt hour has no rate; it passes only when the window edge cut it or every slot in it is a named miss."""
    rate=row.get('failure_rate')
    if rate is None:return row.get('partial_hour') is True or row.get('inactivity_explained') is True
    return type(rate) in (int,float) and math.isfinite(rate) and 0<=rate<=.01

def check_shakedown(root,now=None):
    path=Path(root)/'shakedown_summary.json'
    if not path.is_file():raise SystemExit('Shakedown summary missing')
    summary=json.loads(path.read_text());now=now or datetime.now(timezone.utc)
    age=now-datetime.fromisoformat(summary['generated_at_utc'])
    if not timedelta(0)<=age<MAX_EVIDENCE_AGE:raise SystemExit('Shakedown summary stale or future-dated')
    slots=summary.get('slots',0);missed=summary.get('missed_slots',[])
    rates=summary.get('poll_failure_rate_by_hour',[])
    growth=summary.get('storage_growth',{})
    quota=growth.get('quota_bytes');page_size=growth.get('sqlite_page_size')
    measured=growth.get('measured_bytes_per_day');projected=growth.get('projected_72_day_bytes')
    storage_valid=(type(quota) is int and quota>1024**2 and type(page_size) is int and
                   page_size>=512 and page_size<=65536 and page_size & (page_size-1)==0 and
                   type(measured) in (int,float) and math.isfinite(measured) and measured>=0 and
                   type(projected) is int and projected>=0 and
                   abs(projected-measured*72*2)<2 and growth.get('uncertainty_multiplier')==2)
    cap=((quota-1024**2)//(3*page_size))*page_size if storage_valid else 0
    hours=summary.get('hours_covered')
    reasons_valid=all(type(r) is str and r.strip() for x in missed for r in x.get('reasons') or [])
    if (summary.get('status')!='QA_SHAKEDOWN_NOT_STUDY_EVIDENCE' or
        summary.get('candidate_source_sha256')!=shakedown_source_digest(root) or
        type(hours) not in (int,float) or not math.isfinite(hours) or hours<72 or slots<1 or
        summary.get('enrolled_checkpoints')!=slots-len(missed) or summary.get('all_misses_have_reasons',True) is not True or
        (slots-len(missed))/slots<.95 or not all(x.get('reasons') for x in missed) or not reasons_valid or
        type(summary.get('unexplained_collection_failures')) is not int or summary['unexplained_collection_failures']!=0 or
        len(rates)<72 or not all(shakedown_rate_ok(row) for row in rates) or
        not storage_valid or growth.get('main_database_cap_bytes')!=cap or projected>cap):
        raise SystemExit('Shakedown coverage, reasons, failure rate or storage gate failed')
    return summary


VERIFY_IN_FRESH_PROCESS = (
    "import pathlib, sys\n"
    "root = pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0, str(root))\n"
    "import evidence_runner\n"
    "assert pathlib.Path(evidence_runner.__file__).resolve().parent == root, evidence_runner.__file__\n"
    "evidence_runner.verify_release(root)\n"
    "print('SEALED_COPY_VERIFIED_BY', pathlib.Path(evidence_runner.__file__).resolve())\n")


def seal(out_dir, now=None, raw_check=True):
    # raw_check=False exists only for in-process tests (the raw records never leave this Mac);
    # the command line always re-derives the summary from them.
    out = Path(out_dir).absolute()
    check_shakedown(CANDIDATE,now)
    check_fee_evidence(CANDIDATE, now)
    if raw_check:
        check_raw_evidence(CANDIDATE)
    check_candidate_tree(CANDIDATE)
    if out.exists():
        raise SystemExit('Refusing to overwrite ' + str(out))
    shutil.copytree(CANDIDATE, out, symlinks=True, ignore=shutil.ignore_patterns(*SKIP))
    (out / 'QA_ONLY').unlink()
    protocol_path = out / 'readiness_protocol.json'
    protocol = json.loads(protocol_path.read_text())
    protocol['readiness_status'] = 'SEALED_EVIDENCE_ONLY'
    protocol['activation_allowed'] = True
    protocol_path.write_text(json.dumps(protocol, indent=4, ensure_ascii=False) + '\n')
    # Bind the systemd units into the release: the collector's start time, and the launcher hash that
    # /usr/bin/sha256sum checks before any release code runs.
    start = protocol['start_utc'].replace('T', ' ').replace('+00:00', ' UTC')
    launch_sha, ops_sha = sha(out / 'launch.py'), sha(out / 'acer_ops.py')
    for unit in sorted(out.glob('btc-study-*')):
        unit.write_text(unit.read_text().replace('__START_UTC__', start).replace('__LAUNCH_SHA256__', launch_sha)
                        .replace('__ACER_OPS_SHA256__', ops_sha))
    manifest = write_manifest(out, sealed=True)
    # Verify with the sealed copy's own code in an isolated interpreter (-I: no PYTHONPATH,
    # no user site, script dir not on sys.path), never with modules cached in this process.
    for command in ([sys.executable, '-I', '-B', '-c', VERIFY_IN_FRESH_PROCESS, str(out)],
                    [sys.executable, '-I', '-B', str(out / 'launch.py'), '--verify-files']):
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, cwd=str(out))
        if result.returncode != 0:
            shutil.rmtree(out)
            raise SystemExit('Sealed copy failed verification: ' + (result.stdout + result.stderr).strip()[-500:])
    return manifest


def verify_summary(path, private=None, data=None):
    """Raw-record verification of a fee summary on this Mac (same checks as fee_evidence_refresh.py --verify)."""
    spec = importlib.util.spec_from_file_location('_post_study_refresh', HERE / 'fee_evidence_refresh.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    summary = json.loads(Path(path).read_bytes() if data is None else data)
    found = _fee_reconciliation().fee_evidence_problems(summary) + module.raw_problems(summary, private or module.PRIVATE)
    if found:
        raise SystemExit('Post-study fee summary NOT verified against the raw records: ' + '; '.join(found))
    return summary


def post_study_report(summary_path, host='acer', out_path=None, run=subprocess.run, private=None):
    """The only supported Study 005 report path. Reads the summary ONCE, verifies those exact bytes against
    the raw records, sends that snapshot, and has the host re-check its digest before reporting."""
    data = Path(summary_path).read_bytes()
    verify_summary(summary_path, private, data)
    digest = hashlib.sha256(data).hexdigest()
    remote = 'btc-study/state/post_study_fee_summary.json'
    with tempfile.TemporaryDirectory() as td:
        snapshot = Path(td) / 'post_study_fee_summary.json'
        snapshot.write_bytes(data)
        steps = [['scp', '-q', str(snapshot), '%s:%s' % (host, remote)],
                 ['ssh', '-o', 'BatchMode=yes', host, 'chmod 600 ~/%s && cd ~/btc-study/current && python3 -I -B launch.py --verify-only '
                  '&& python3 -B evidence_runner.py --report ~/%s --summary-sha256 %s' % (remote, remote, digest)]]
        for command in steps:
            result = run(command, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise SystemExit('Post-study report failed: ' + (result.stdout + result.stderr).strip()[-500:])
    out = Path(out_path or HERE.parents[1] / 'study005_release_report.json')
    out.write_text(result.stdout)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    sub.add_parser('shakedown-summary')
    s = sub.add_parser('seal'); s.add_argument('out_dir')
    r = sub.add_parser('post-study-report', help='after 2026-12-23: verify the fee summary, then run the release report on the host')
    r.add_argument('summary', nargs='?', default=str(HERE.parents[1] / 'fee_evidence_refresh_summary.json')); r.add_argument('--host', default='acer')
    args = ap.parse_args(argv)
    if args.command == 'post-study-report':
        print('Saved', post_study_report(args.summary, args.host)); return
    if args.command == 'inventory':
        m = write_manifest(CANDIDATE, sealed=False)
    elif args.command == 'shakedown-summary':
        print(json.dumps(shakedown_summary(),indent=2));return
    else:
        m = seal(args.out_dir)
    print(json.dumps({k: v for k, v in m.items() if k != 'files'} | {'files': len(m['files']),
                      'manifest_sha256': sha((CANDIDATE if args.command == 'inventory' else Path(args.out_dir)) / 'release_manifest.json')}, indent=2))


if __name__ == '__main__':
    main()
