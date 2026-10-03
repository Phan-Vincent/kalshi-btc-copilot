import sys
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path('readiness_release/candidate').resolve()))
sys.path.insert(0, str(Path('readiness_release/tests').resolve()))
import evidence_runner as run
import test_evidence_runner as ter

t = ter.EvidenceRunnerTests('test_executable_lifecycle_uses_explicit_limits_and_closes_cleanly')
gate, store, engine, server, kwargs = t.factory_fixture()
# reconcile_one -> store.settle_one raises a non-ReadError transient fault once,
# recorded as SETTLEMENT_FAILED degradation, but the run must not die for it.
store.settle_one.side_effect = [RuntimeError('synthetic transient settle fault'), False]
try:
    result = run.run_authorized(ter.f.ROOT, gate, max_cycles=2, **kwargs)
    print('run_authorized returned:', result)
except Exception as e:
    print('run_authorized raised:', type(e).__name__, e)
print('store.failure calls:', store.failure.call_args_list)
print('store.close calls:', store.close.call_args_list)
print('store.settle_one calls:', store.settle_one.call_count)
