"""Exercise verifier rejection in tiny synthetic packages; never edit the real seal."""
import hashlib,json,shutil,subprocess,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def check():
    results=[]
    for scenario in ['valid_disabled_fixture','changed_manifest','changed_artifact','unexpected_approval','escaping_artifact_path']:
        with tempfile.TemporaryDirectory() as directory:
            audit=Path(directory);package=audit/'validation_package';candidate=audit/'outputs/copilot_remediation_candidate'
            package.mkdir();candidate.mkdir(parents=True)
            shutil.copyfile(HERE/'verify_package.py',package/'verify_package.py')
            proposal={k:False for k in ['activation_allowed','advice_allowed','execution_allowed','activation_review_ready']}
            (package/'protocol_proposal.json').write_text(json.dumps(proposal));(candidate/'QA_ONLY').write_text('offline')
            manifest={'activation_allowed':False,'files':{'validation_package/protocol_proposal.json':sha(package/'protocol_proposal.json')}}
            if scenario=='escaping_artifact_path':manifest['files']={'../outside':'not-read'}
            (package/'package_manifest.json').write_text(json.dumps(manifest))
            (package/'package_manifest.sha256').write_text(sha(package/'package_manifest.json'))
            if scenario=='changed_manifest':(package/'package_manifest.json').write_text('{}')
            if scenario=='changed_artifact':(package/'protocol_proposal.json').write_text('{}')
            if scenario=='unexpected_approval':(candidate/'activation_approval.json').write_text('{}')
            process=subprocess.run([sys.executable,'-B',str(package/'verify_package.py')],capture_output=True,text=True,timeout=10)
            expected=0 if scenario=='valid_disabled_fixture' else 1
            results.append({'scenario':scenario,'exit_code':process.returncode,'expected_exit':expected,'passed':process.returncode==expected})
    (HERE/'integrity_boundary_results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2));return all(result['passed'] for result in results)
if __name__=='__main__':sys.exit(not check())
