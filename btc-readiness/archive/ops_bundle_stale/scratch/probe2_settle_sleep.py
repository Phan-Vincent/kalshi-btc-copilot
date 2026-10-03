import sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path('readiness_release/candidate').resolve()))
sys.path.insert(0, str(Path('readiness_release/tests').resolve()))
import runtime_fixture as f
import coherent_runtime as cr
from coherent_runtime import EvidenceStore, RuntimeBlocked
import btc_copilot as b

c, s = f.fixture()
with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    protocol = f.protocol_file(root)
    clock = {'now': f.NOW}
    store = EvidenceStore(root / 'runtime', protocol, s['model_version'], {}, boot_id='synthetic', wall=lambda: clock['now'])
    try:
        store.publish(c, s)
        close = b.dt(s['close_time']).timestamp()
        clock['now'] = close + 61
        client = SimpleNamespace(market=lambda ticker: {'data': {'market': {'ticker': ticker, 'status': 'finalized', 'result': 'yes'}}})
        # Simulate a 16s system sleep during the settlement GET:
        # wall advances 16s, time.monotonic() (mach_absolute_time) advances 0.1s.
        with patch.object(cr.time, 'monotonic', side_effect=[1000.0, 1000.1]):
            real_wall = clock['now']
            def sleepy_market(ticker):
                clock['now'] = real_wall + 16  # wake 16 wall-seconds later
                return client.market(ticker)
            sleepy = SimpleNamespace(market=sleepy_market)
            try:
                store.settle_one(sleepy)
                print('settle_one: NO ERROR (unexpected)')
            except Exception as e:
                print('settle_one raised:', type(e).__name__, e)
        print('store.halted =', store.halted, '| HALTED file =', (root / 'runtime' / 'HALTED').exists())
    finally:
        store.close(clean=not store.halted)
