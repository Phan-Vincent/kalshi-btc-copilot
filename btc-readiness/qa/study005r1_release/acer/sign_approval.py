"""Interactive evidence-only approval for the staged Study 005 release. Run ON THE ACER, by the owner.

Invoked by `./install.sh approve`. Shows what is being approved, refuses after the protocol's
approval deadline or if an approval already exists, asks the owner to type APPROVE, writes
activation_approval.json (owner-only) bound to this host, release path, manifest and study,
then runs `launch.py --verify-approval`. Approval never enables advice or trading.
"""
import hashlib, json, os, socket, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
ROOT = (HOME / 'btc-study' / 'current').resolve()
APPROVAL = ROOT / 'activation_approval.json'


def main():
    manifest_path = ROOT / 'release_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    protocol = json.loads((ROOT / 'readiness_protocol.json').read_text())
    deadline = datetime.fromisoformat(protocol['approval_policy']['deadline_utc'])
    now = datetime.now(timezone.utc)
    if manifest.get('status') != 'SEALED_EVIDENCE_ONLY_RELEASE' or protocol.get('activation_allowed') is not True:
        sys.exit('Refusing: the staged release is not a sealed evidence-only release.')
    if protocol.get('advice_allowed') is not False or protocol.get('execution_allowed') is not False:
        sys.exit('Refusing: the protocol does not keep advice and execution disabled.')
    if now >= deadline:
        sys.exit('Refusing: the approval deadline (%s) has passed. A later start needs a new reviewed protocol.' % deadline.isoformat())
    if APPROVAL.exists():
        sys.exit('Refusing: %s already exists. Remove it yourself first if you mean to re-approve.' % APPROVAL)
    pre = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'launch.py'), '--verify-only'], capture_output=True, text=True)
    if pre.returncode != 0:
        sys.exit('Refusing: the staged release does not verify: ' + pre.stdout.strip())
    approval = {
        'approved': True, 'scope': 'EVIDENCE_ONLY', 'bundle_path': str(ROOT), 'host': socket.gethostname(),
        'study_id': protocol['study_id'], 'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'approved_at_utc': now.isoformat(),
        'attestation_path': str(HOME / 'btc-study' / 'state' / 'attestation.json'),
        'credential_config': str(HOME / '.config' / 'btc-study' / 'kalshi_readonly_config.json'),
    }
    print('You are approving EVIDENCE-ONLY collection. No orders, no trading advice, GET-only Kalshi reads.\n')
    print('  study          ', protocol['study_id'])
    print('  release        ', manifest['candidate_release'], '(manifest %s)' % approval['manifest_sha256'])
    print('  host / path    ', approval['host'], approval['bundle_path'])
    print('  collection     ', protocol['start_utc'], '->', protocol['end_utc'])
    print('  results        ', protocol['release_utc'])
    print('  deadline       ', deadline.isoformat(), '(approving now:', now.isoformat() + ')')
    print('\nStop authority stays with you: ./install.sh rollback stops everything and keeps all evidence.')
    if input('\nType APPROVE to sign, anything else to cancel: ').strip() != 'APPROVE':
        sys.exit('Cancelled. Nothing was written.')
    fd = os.open(APPROVAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as fh:
        json.dump(approval, fh, indent=2)
    check = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'launch.py'), '--verify-approval'], capture_output=True, text=True)
    print('\nlaunch.py --verify-approval:', check.stdout.strip())
    if check.returncode != 0:
        APPROVAL.unlink()
        sys.exit('The approval did not verify and was removed. Nothing is activated.')
    print('Signed. Next, on the Mac: ./install.sh activate  (collection starts %s)' % protocol['start_utc'])


if __name__ == '__main__':
    main()
