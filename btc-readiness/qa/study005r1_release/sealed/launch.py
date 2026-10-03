"""Verify-then-run launcher for a sealed evidence-only release. Standard library only.

systemd first checks this file's own SHA-256 with /usr/bin/sha256sum (the hash is written
into the sealed unit files by release_tool.py seal), then runs `python3 -I -B launch.py`.
Before ANY other release module is imported, this checks that every file in the release
directory is listed in release_manifest.json with a matching SHA-256, that nothing unlisted
is present (no __pycache__, no bytecode, no symlinks or hard links), that any installed
btc-study systemd units are byte-identical to the sealed copies, and, for --collect and
--verify-approval, that the approval is bound to this manifest, host, path and study and
was given before the protocol's deadline. Only then does --collect hand over to
evidence_runner, whose ReleaseGate re-checks everything on every collection cycle.

Scope: this stops accidental or unreviewed changes from running. It is not a security
boundary against someone who can already write to the release directory, the systemd
units or the evidence database.
"""
import hashlib, json, runpy, socket, sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
UNITS_DIR = Path.home() / '.config' / 'systemd' / 'user'
ALLOWED_UNLISTED = {'release_manifest.json', 'activation_approval.json', 'runtime'}
PLACEHOLDERS = ('__START_UTC__', '__LAUNCH_SHA256__', '__ACER_OPS_SHA256__')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(path.name + ' missing or linked')
    value = json.loads(path.read_text())
    if type(value) is not dict:
        raise ValueError(path.name + ' is not an object')
    return value


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('explicit UTC required')
    return parsed


def file_problems(root, units_dir):
    try:
        manifest = read_json(root / 'release_manifest.json')
    except ValueError as error:
        return [str(error)]
    files = manifest.get('files')
    if type(files) is not dict or not all(type(k) is str and type(v) is str for k, v in files.items()):
        return ['release_manifest.json has no valid file map']
    found = []
    if manifest.get('status') != 'SEALED_EVIDENCE_ONLY_RELEASE' or 'launch.py' not in files or 'QA_ONLY' in files:
        found.append('not a sealed release that lists this launcher')
    for entry in root.iterdir():
        if entry.is_symlink():
            found.append('symlink: ' + entry.name)
        elif entry.name not in files and entry.name not in ALLOWED_UNLISTED:
            found.append('unlisted: ' + entry.name)
    for name, expected in files.items():
        path = root / name
        if Path(name).name != name or path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            found.append('missing or linked: ' + name)
        elif sha(path) != expected:
            found.append('hash mismatch: ' + name)
        elif name.startswith('btc-study-') and any(p in path.read_text() for p in PLACEHOLDERS):
            found.append('unfilled placeholder in ' + name)
    runtime = root / 'runtime'
    if runtime.exists() and (runtime.is_symlink() or not runtime.is_dir()):
        found.append('runtime is not a plain directory')
    # Every sealed unit must be installed and byte-identical, and nothing else named btc-study-*.
    if units_dir is not None:
        sealed_units = sorted(n for n in files if n.startswith('btc-study-'))
        installed = {u.name: u for u in units_dir.glob('btc-study-*')} if units_dir.is_dir() else {}
        for name in sealed_units:
            if name not in installed:
                found.append('sealed unit not installed: ' + name)
            elif installed[name].read_bytes() != (root / name).read_bytes():
                found.append('installed unit differs from the sealed release: ' + name)
        for name in sorted(set(installed) - set(sealed_units)):
            found.append('installed unit not in the sealed release: ' + name)
    return found


def approval_problems(root, hostname, now):
    try:
        approval = read_json(root / 'activation_approval.json')
        protocol = read_json(root / 'readiness_protocol.json')
        deadline = utc(protocol['approval_policy']['deadline_utc'])
        approved = utc(approval['approved_at_utc'])
    except (ValueError, KeyError, TypeError) as error:
        return ['approval or protocol unreadable: ' + str(error)]
    found = []
    expected = {'approved': True, 'scope': 'EVIDENCE_ONLY', 'bundle_path': str(root), 'host': hostname,
                'study_id': protocol.get('study_id'), 'manifest_sha256': sha(root / 'release_manifest.json')}
    for key, value in expected.items():
        if approval.get(key) != value:
            found.append('approval ' + key + ' does not match this release')
    if not approved < deadline:
        found.append('approval given at or after the protocol deadline')
    if approved > now:
        found.append('approval is future-dated')
    return found


def problems(root=ROOT, require_approval=True, units_dir=UNITS_DIR, hostname=None, now=None):
    found = file_problems(root, units_dir)
    if require_approval and not found:
        found = approval_problems(root, hostname or socket.gethostname(), now or datetime.now(timezone.utc))
    return found


def main(argv):
    # mode: (check approval, check installed units). --verify-files is for release_tool.py seal on the
    # build machine, where the host's installed units are irrelevant.
    modes = {'--collect': (True, True), '--verify-approval': (True, True), '--verify-only': (False, True), '--verify-files': (False, False)}
    if len(argv) != 1 or argv[0] not in modes:
        print('usage: launch.py --collect | --verify-approval | --verify-only | --verify-files'); return 2
    approval, units = modes[argv[0]]
    try:
        found = problems(require_approval=approval, units_dir=UNITS_DIR if units else None)
    except (OSError, ValueError) as error:
        found = ['verification error: ' + str(error)]
    if found:
        print('NOT LAUNCHED: ' + '; '.join(found)); return 2
    if argv[0] != '--collect':
        print('VERIFIED'); return 0
    sys.path.insert(0, str(ROOT))
    sys.argv = [str(ROOT / 'evidence_runner.py'), '--collect']
    runpy.run_path(str(ROOT / 'evidence_runner.py'), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
