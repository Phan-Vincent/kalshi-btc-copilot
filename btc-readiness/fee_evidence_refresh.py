"""Moved: the fee-evidence refresh now lives at qa/study005r1_release/fee_evidence_refresh.py (next to
release_tool.py, which re-derives its summary from the raw private files). This wrapper runs it."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).resolve().parent / 'qa/study005r1_release/fee_evidence_refresh.py'), run_name='__main__')
