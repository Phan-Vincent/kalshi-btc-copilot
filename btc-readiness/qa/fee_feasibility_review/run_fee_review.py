"""Repeat isolated fee tests and guarded existing suites; never reseal or activate."""
import hashlib,json,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    baseline=json.loads((HERE/'protected_local_start_hashes.json').read_text())
    for relative,expected in baseline.items():
        if digest(AUDIT/relative)!=expected:raise ValueError('Preserved artifact changed before tests: '+relative)
    commands=[('fee', [sys.executable,'-B',str(HERE/'test_fee_feasibility.py')]),('guarded_package',[sys.executable,'-B',str(AUDIT/'validation_package/verify_package.py'),'--test'])]
    processes=[]
    for name,command in commands:
        completed=subprocess.run(command,capture_output=True,text=True,timeout=240)
        (HERE/(name+'_suite_output.txt')).write_text(completed.stdout+completed.stderr)
        processes.append({'suite':name,'exit_code':completed.returncode,'command':command})
    changed=[relative for relative,expected in baseline.items() if digest(AUDIT/relative)!=expected]
    fee=json.loads((HERE/'fee_test_results.json').read_text());old=json.loads((AUDIT/'remediation_test_results.json').read_text())
    result={'processes':processes,'new_fee_tests':fee,'guarded_existing':{'python_passed':old['python_passed'],'python_skipped':old['python_skipped'],'ui_passed':old['ui_passed'],'golden_passed':old['golden_passed'],'safety':old['safety']},'preserved_candidate_and_package_unchanged':not changed,'changed_paths':changed,'combined_python_methods_run':old['python_methods_run']+fee['run'],'combined_python_passed':old['python_passed']+fee['passed'] if old['python_passed'] is not None else None,'actual_execution':False,'activation_performed':False}
    (HERE/'final_results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return all(p['exit_code']==0 for p in processes) and not changed and fee['failures']==0 and fee['errors']==0

if __name__=='__main__':sys.exit(not main())
