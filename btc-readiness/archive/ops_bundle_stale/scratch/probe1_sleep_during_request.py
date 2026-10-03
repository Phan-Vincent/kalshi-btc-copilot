import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path('readiness_release/candidate').resolve()))
sys.path.insert(0, str(Path('readiness_release/tests').resolve()))
import runtime_fixture as f
from coherent_runtime import EvidenceStore, RuntimeBlocked
from study_policy import check_clock

c, s = f.fixture()
# Simulate a 16-second system sleep that spans the 'book' request, with the
# new sleep-inclusive continuity_clock: wall and elapsed both advance by 16s.
start = s['epoch'] - 16.2
finish = s['epoch'] - 0.2
s['retrieval_health']['requests']['book'] = {
    'started_at_epoch': start,
    'finished_at_epoch': finish,
    'latency_seconds': 16.0,
}
# valid_until stays epoch+20 (well-formed). check_clock alone should trip.
try:
    check_clock(s)
    print('check_clock: NO ERROR (unexpected)')
except ValueError as e:
    print('check_clock raised ValueError:', e)

with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    protocol = f.protocol_file(root)
    store = EvidenceStore(root / 'runtime', protocol, s['model_version'], {}, boot_id='synthetic', wall=lambda: s['epoch'])
    try:
        try:
            store.publish(c, s)
            print('publish: NO ERROR (unexpected)')
        except Exception as e:
            print('publish raised:', type(e).__name__, e)
        print('store.halted =', store.halted, '| HALTED file =', (root / 'runtime' / 'HALTED').exists())
    finally:
        store.close(clean=not store.halted)
