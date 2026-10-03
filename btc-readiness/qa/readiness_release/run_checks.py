"""Run current candidate against isolated copies of all relevant synthetic tests."""
from pathlib import Path
import hashlib,json,re,shutil,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parent;AUDIT=ROOT.parent
EVIDENCE=ROOT/'evidence'
results=[]
def job(name,command,cwd):
 p=subprocess.run(command,cwd=cwd,text=True,capture_output=True,timeout=90)
 output=p.stdout+p.stderr;(EVIDENCE/(name+'.txt')).write_text(output)
 match=re.search(r'Ran (\d+) tests?',output);skips=re.search(r'skipped=(\d+)',output)
 result={'name':name,'exit_code':p.returncode,'run':int(match[1]) if match else None,'skipped':int(skips[1]) if skips else 0}
 results.append(result);print(json.dumps(result),flush=True)
 return p
with tempfile.TemporaryDirectory(prefix='btc-readiness-qa-') as td:
 lab=Path(td);source=lab/'outputs/copilot_remediation_candidate'
 shutil.copytree(ROOT/'candidate',source)
 for name in ('remediation_audit.py','test_remediation.py','test_acceptance.py','remediation_ui.cjs','candidate_ui_fixture.json'):
  shutil.copy2(AUDIT/name,lab/name)
 (lab/'original/work').mkdir(parents=True)
 shutil.copy2(AUDIT/'original/work/btc_copilot_test_fixture.json',lab/'original/work/btc_copilot_test_fixture.json')
 (lab/'outputs/copilot_study002_paced').mkdir()
 shutil.copy2(AUDIT/'outputs/copilot_study002_paced/study_policy.py',lab/'outputs/copilot_study002_paced/study_policy.py')
 (lab/'validation_package').mkdir()
 shutil.copy2(AUDIT/'validation_package/test_operating_bounds.py',lab/'validation_package/test_operating_bounds.py')
 # Only local copied source and synthetic fixtures reach child processes.
 job('candidate_existing',[sys.executable,'-B','-m','unittest','discover','-s','.','-p','test_*.py','-v'],source)
 job('candidate_adversarial',[sys.executable,'-B','remediation_audit.py','candidate'],lab)
 adversarial=json.loads((lab/'candidate_adversarial.json').read_text())
 results[-1].update(run=adversarial['run'],skipped=adversarial['skipped'])
 for name in ('candidate_adversarial.json','candidate_golden.json'):shutil.copy2(lab/name,EVIDENCE/name)
 job('candidate_added_regressions',[sys.executable,'-B','test_remediation.py'],lab)
 job('candidate_acceptance',[sys.executable,'-B','test_acceptance.py'],lab)
 job('candidate_operating_bounds',[sys.executable,'-B','test_operating_bounds.py'],lab/'validation_package')
 job('candidate_ui',['node','remediation_ui.cjs'],lab)
 shutil.copy2(lab/'candidate_ui_results.json',EVIDENCE/'candidate_ui_results.json')
 job('readiness_added',[sys.executable,'-B','-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_*.py','-v'],ROOT)
 golden=json.loads((EVIDENCE/'candidate_golden.json').read_text());ui=json.loads((EVIDENCE/'candidate_ui_results.json').read_text())
 summary={'suites':results,'python_methods_run':sum(x['run'] or 0 for x in results),
          'python_skipped':sum(x['skipped'] for x in results),
          'golden_passed':sum(x['passes_expectation'] for x in golden),'golden_run':len(golden),
          'ui_passed':sum(x['passed'] for x in ui),'ui_run':len(ui),
          'production_sources_unchanged':all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in json.loads((AUDIT/'source_hashes.json').read_text()).items()),
          'candidate_runtime_absent':not (ROOT/'candidate/runtime').exists(),
          'candidate_approval_absent':not (ROOT/'candidate/activation_approval.json').exists(),
          'candidate_QA_ONLY_present':(ROOT/'candidate/QA_ONLY').exists(),
          'scope':'Current in-progress candidate only; no independent acceptance sign-off or deployment. Frozen reference package tests are separately preserved.'}
 summary['passed']=all(x['exit_code']==0 for x in results) and summary['golden_passed']==len(golden) and summary['ui_passed']==len(ui) and all(summary[k] for k in ('production_sources_unchanged','candidate_runtime_absent','candidate_approval_absent','candidate_QA_ONLY_present'))
 (EVIDENCE/'test_results.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
 sys.exit(not summary['passed'])
