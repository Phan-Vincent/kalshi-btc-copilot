"""Read-only monitor diagnostics. Never opens study outcomes, credentials or account data."""
import argparse
import fcntl
import json
import math
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parent

def assess(latest,runtime,lock_held,now):
    issues=[]
    latest=latest if isinstance(latest,dict) else {}
    runtime=runtime if isinstance(runtime,dict) else {}
    epoch=latest.get('epoch');expiry=latest.get('valid_until_epoch')
    numeric=all(type(v) in (int,float) and math.isfinite(v) for v in (epoch,expiry))
    age=now-epoch if numeric else None
    if not numeric:issues.append('invalid_observation_timestamps')
    elif age < -2:issues.append('clock_or_future_timestamp_fault')
    elif age>20 or now>=expiry:issues.append('expired_observation')
    if numeric and expiry<=epoch:issues.append('invalid_validity_window')
    if latest.get('error'):issues.append('collection_failure')
    if not lock_held:issues.append('monitor_lock_not_held')
    if not isinstance(runtime.get('pid'),int):issues.append('missing_runtime_pid')
    if latest.get('decision')!='NO TRADE':issues.append('unexpected_enabled_advice')
    validation=latest.get('validation') if isinstance(latest.get('validation'),dict) else {}
    position=latest.get('position') if isinstance(latest.get('position'),dict) else {}
    if not latest.get('error') and validation.get('status')!='UNVALIDATED_SHADOW_ONLY':issues.append('missing_shadow_gate')
    if not latest.get('error') and position.get('guidance_valid') is not False:issues.append('unexpected_position_guidance')
    return {'checked_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now)),
            'healthy':not issues,'issues':issues,'snapshot_age_seconds':round(age,2) if age is not None else None,
            'monitor_lock_held':lock_held,'scope':'File/lock health only; no account reads, outcomes, credential reads, restarts or source changes'}

def check(root=ROOT):
    work=root.parent/'work';held=False
    try:
        with (work/'btc_copilot.lock').open('r') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:held=True
    except FileNotFoundError:pass
    try:latest=json.loads((root/'btc_copilot_latest.json').read_text())
    except (OSError,ValueError):latest={}
    try:runtime=json.loads((work/'btc_copilot_runtime.json').read_text())
    except (OSError,ValueError):runtime={}
    return assess(latest,runtime,held,time.time())

if __name__=='__main__':
    result=check();print(json.dumps(result,indent=2));raise SystemExit(0 if result['healthy'] else 1)
