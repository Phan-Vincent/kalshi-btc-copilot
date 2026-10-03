"""Final guarded candidate suites. All runtime artifacts stay in audit/temp paths."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE/'outputs/copilot_remediation_candidate'
manifest=json.loads((ROOT/'release_manifest.json').read_text())
# A hash mismatch must fail; this runner never silently re-seals changed source.
assert all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==h for k,h in manifest['files'].items()),'Candidate manifest mismatch'
assert (ROOT/'QA_ONLY').exists() and not (ROOT/'activation_approval.json').exists() and not (ROOT/'runtime').exists()
jobs=[('candidate_existing',ROOT,[sys.executable,'-B','-m','unittest','discover','-s','.','-p','test_*.py','-v']),
('candidate_adversarial',HERE,[sys.executable,'-B','remediation_audit.py','candidate']),
('candidate_added_regressions',HERE,[sys.executable,'-B','test_remediation.py']),
('candidate_ui',HERE,['node','remediation_ui.cjs']),
('candidate_acceptance',HERE,[sys.executable,'-B','test_acceptance.py']),
('candidate_operating_bounds',HERE/'validation_package',[sys.executable,'-B','test_operating_bounds.py']),
('candidate_package_contract',HERE/'validation_package',[sys.executable,'-B','test_package_contract.py'])]
results=[]
for name,cwd,cmd in jobs:
 completed=subprocess.run(cmd,cwd=cwd,text=True,capture_output=True,timeout=45)
 (HERE/(name+'_final_suite.txt')).write_text(completed.stdout+completed.stderr)
 result={'suite':name,'exit_code':completed.returncode,'command':cmd,'cwd':str(cwd)}
 count=re.search(r'Ran (\d+) tests?',completed.stderr)
 if count:result['run']=int(count[1])
 results.append(result)
# Preserve and compare the production public-source inventory, without opening state or outcomes.
baseline=json.loads((HERE/'source_hashes.json').read_text())
changed=[path for path,expected in baseline.items() if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected]
seal=Path('/Users/you/Documents/Codex/2026-09-26/new-chat/outputs/copilot_study002_fee_review/release_manifest.json')
sealed=json.loads(seal.read_text())
safety={'production_sources_unchanged':not changed,'changed_paths':changed,'study002_seal_intact':all(hashlib.sha256((seal.parent/k).read_bytes()).hexdigest()==h for k,h in sealed['files'].items()),'study002_manifest_unchanged':hashlib.sha256(seal.read_bytes()).hexdigest()=='848151a08e737c1604c04d2ecdee58f38e6978b4314fca03b8f4e77893dede88','study002_runtime_absent':not (seal.parent/'runtime').exists(),'candidate_runtime_absent':not (ROOT/'runtime').exists(),'candidate_approval_absent':not (ROOT/'activation_approval.json').exists()}
golden=json.loads((HERE/'candidate_golden.json').read_text());adversarial=json.loads((HERE/'candidate_adversarial.json').read_text());ui=json.loads((HERE/'candidate_ui_results.json').read_text())
report={'suites':results,'python_methods_run':sum(x.get('run',0) for x in results)+adversarial['run'],'python_passed':sum(x.get('run',0) for x in results)+adversarial['passed'] if all(x['exit_code']==0 for x in results) else None,'python_skipped':adversarial['skipped'],'ui_run':len(ui),'ui_passed':sum(x['passed'] for x in ui),'golden_run':len(golden),'golden_passed':sum(x['passes_expectation'] for x in golden),'safety':safety}
(HERE/'remediation_test_results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
sys.exit(not(all(x['exit_code']==0 for x in results) and all(x['passed'] for x in ui) and all(x['passes_expectation'] for x in golden) and not changed and safety['study002_seal_intact'] and safety['study002_manifest_unchanged'] and safety['study002_runtime_absent'] and safety['candidate_runtime_absent']))
