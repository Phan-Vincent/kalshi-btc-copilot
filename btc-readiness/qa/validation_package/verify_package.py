"""Check frozen evidence integrity. Never reseal, approve, launch or authenticate."""
import hashlib,json,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    manifest_path=HERE/'package_manifest.json'
    expected=(HERE/'package_manifest.sha256').read_text().split()[0]
    if sha(manifest_path)!=expected:raise ValueError('Package manifest mismatch; no automatic resealing')
    manifest=json.loads(manifest_path.read_text())
    if manifest['activation_allowed'] is not False:raise ValueError('Package must not authorize activation')
    for relative,digest in manifest['files'].items():
        path=AUDIT/relative
        if path.is_symlink() or not path.resolve().is_relative_to(AUDIT.resolve()):raise ValueError('Untrusted artifact path')
        if sha(path)!=digest:raise ValueError('Frozen artifact changed: '+relative)
    proposal=json.loads((HERE/'protocol_proposal.json').read_text())
    for key in ['activation_allowed','advice_allowed','execution_allowed','activation_review_ready']:
        if proposal[key] is not False:raise ValueError('Disabled proposal gate changed: '+key)
    candidate=AUDIT/'outputs/copilot_remediation_candidate'
    if not (candidate/'QA_ONLY').is_file() or (candidate/'activation_approval.json').exists() or (candidate/'runtime').exists():raise ValueError('Unexpected candidate activation state')
    print(json.dumps({'integrity':'PASS','frozen_files':len(manifest['files']),'package_sha256':expected,'activation_authorized':False,'qa_status':'QA BLOCKED'},indent=2))

if __name__=='__main__':
    verify()
    if sys.argv[1:]==['--test']:
        result=subprocess.run([sys.executable,'-B',str(AUDIT/'run_remediation.py')],timeout=240)
        if result.returncode:raise SystemExit(result.returncode)
        verify()
    elif sys.argv[1:]:raise SystemExit('Only --test is supported')
