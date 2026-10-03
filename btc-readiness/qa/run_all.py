"""Re-run all offline suites; write results only inside this audit directory."""
from pathlib import Path
import subprocess,json,re,sys,hashlib
HERE=Path(__file__).resolve().parent
jobs=[('original_existing',HERE/'original',[sys.executable,'-B','-m','unittest','discover','-s','work','-p','test_*.py','-v']),
('original_known_regressions',HERE/'original',[sys.executable,'-B','-m','unittest','discover','-s','outputs','-p','copilot_audit_regressions.py','-v']),
('study002_existing_and_lifecycle',HERE/'outputs/copilot_study002_fee_review',[sys.executable,'-B','-m','unittest','discover','-s','.','-p','test_*.py','-v']),
('supervision',HERE/'outputs/study002_supervision',[sys.executable,'-B','-m','unittest','discover','-s','.','-p','test_supervise.py','-v']),
('original_existing_ui',HERE/'original',['node','work/test_copilot_ui.cjs']),
('original_adversarial',HERE,[sys.executable,'-B','adversarial_audit.py','original']),
('study002_adversarial',HERE,[sys.executable,'-B','adversarial_audit.py','study002']),
('actual_html_ui',HERE,['node','audit_ui.cjs'])]
results=[]
for name,cwd,cmd in jobs:
 r=subprocess.run(cmd,cwd=cwd,capture_output=True,text=True,timeout=45)
 (HERE/(name+'_suite.txt')).write_text(r.stdout+r.stderr)
 item={'suite':name,'exit_code':r.returncode,'command':cmd,'cwd':str(cwd)}
 matched=re.search(r'Ran (\d+) tests?',r.stderr)
 if matched:item['tests']=int(matched[1])
 if name.endswith('_adversarial'):
  data=json.loads((HERE/(name.replace('_adversarial','')+'_adversarial.json')).read_text())
  item.update({k:data[k] for k in ['run','passed','failed_test_methods','skipped']})
 results.append(item)
original=json.loads((HERE/'source_hashes.json').read_text())
changed=[p for p,h in original.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
seal=Path('/Users/you/Documents/Codex/2026-09-26/new-chat/outputs/copilot_study002_fee_review/release_manifest.json')
m=json.loads(seal.read_text());intact=all(hashlib.sha256((seal.parent/k).read_bytes()).hexdigest()==h for k,h in m['files'].items())
results.append({'production_sources_unchanged':not changed,'changed_paths':changed,'study002_seal_intact':intact,'study002_runtime_absent':not (seal.parent/'runtime').exists()})
(HERE/'test_results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
