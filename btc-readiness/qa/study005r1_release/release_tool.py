"""Working-inventory and sealed-release manifests for the Study 005 revision-1 candidate.

    python3 -B release_tool.py inventory          # rewrite candidate/release_manifest.json (still QA_ONLY)
    python3 -B release_tool.py seal OUT_DIR       # build a sealed copy in OUT_DIR; the candidate is untouched

`seal` is the mechanical part of the freeze. It does not decide anything: the start
date comes from readiness_policy.START, which the freeze sets after review, and the
result still needs the final review, the user's approval file and `install.sh activate`.
Offline only; no network, credentials or account access.
"""
import argparse, hashlib, importlib.util, json, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE / 'candidate'
WORKING_STATUS = 'WORKING_INVENTORY_NOT_AN_ACTIVATABLE_RELEASE'
SEALED_STATUS = 'SEALED_EVIDENCE_ONLY_RELEASE'
MAX_EVIDENCE_AGE = timedelta(hours=72)
SKIP = {'release_manifest.json', 'activation_approval.json', 'runtime', '__pycache__', '.DS_Store'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
                 ('Working Study 005 revision-1 inventory for QA. Not a release seal; activation, advice and '
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
    s = sub.add_parser('seal'); s.add_argument('out_dir')
    r = sub.add_parser('post-study-report', help='after 2026-12-23: verify the fee summary, then run the release report on the host')
    r.add_argument('summary', nargs='?', default=str(HERE.parents[1] / 'fee_evidence_refresh_summary.json')); r.add_argument('--host', default='acer')
    args = ap.parse_args(argv)
    if args.command == 'post-study-report':
        print('Saved', post_study_report(args.summary, args.host)); return
    if args.command == 'inventory':
        m = write_manifest(CANDIDATE, sealed=False)
    else:
        m = seal(args.out_dir)
    print(json.dumps({k: v for k, v in m.items() if k != 'files'} | {'files': len(m['files']),
                      'manifest_sha256': sha((CANDIDATE if args.command == 'inventory' else Path(args.out_dir)) / 'release_manifest.json')}, indent=2))


if __name__ == '__main__':
    main()
