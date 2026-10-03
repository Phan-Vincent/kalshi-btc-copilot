"""Verify supplementary review hashes; no resealing, network or activation."""
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    path=HERE/'fee_review_manifest.json'
    expected=(HERE/'fee_review_manifest.sha256').read_text().split()[0]
    if sha(path)!=expected:raise ValueError('Supplementary manifest mismatch')
    manifest=json.loads(path.read_text())
    if manifest['activation_allowed'] is not False or manifest['qa_status']!='QA BLOCKED':raise ValueError('Review is not approval')
    for name,digest in manifest['files'].items():
        target=AUDIT/name
        if target.is_symlink() or not target.resolve().is_relative_to(AUDIT.resolve()) or sha(target)!=digest:raise ValueError('Frozen review artifact mismatch: '+name)
    preserved=json.loads((HERE/'protected_local_start_hashes.json').read_text())
    for name,digest in preserved.items():
        if sha(AUDIT/name)!=digest:raise ValueError('Preserved candidate/package changed: '+name)
    print(json.dumps({'integrity':'PASS','frozen_files':len(manifest['files']),'preserved_files':len(preserved),'activation_allowed':False,'qa_status':'QA BLOCKED','manifest_sha256':expected},indent=2))
if __name__=='__main__':main()
