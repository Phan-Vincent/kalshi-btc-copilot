"""200 synthetic 800-window logs, 14 minute rows per window; actual bootstrap."""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ta_copilot"))
import tally_report as tr

RNG = random.Random(9042026)
LOGS = 200
WINDOWS = 800
MINUTES = 14
ROUNDS = 30

def log(signal):
    groups = []
    for _ in range(WINDOWS):
        x, y = 0, 0.0
        observations = []
        for _ in range(MINUTES):
            x = max(-3, min(3, round(.65 * x + RNG.gauss(0, 1.6))))
            y = .7 * y + RNG.gauss(0, 1) + signal * x
            observations.append((x, y))
        groups.append(observations)
    return groups

start = time.monotonic()
for signal in (0, .04):
    fired = available = 0
    rhos = []
    for i in range(LOGS):
        groups = log(signal)
        rho = tr.spearman([obs for group in groups for obs in group])
        rhos.append(rho)
        ci = tr.block_bootstrap(groups, tr.spearman, rounds=ROUNDS, seed=tr.BOOTSTRAP_SEED)
        if ci is not None:
            available += 1
            fired += ci[0] > 0 or ci[1] < 0
        if (i + 1) % 50 == 0:
            print(f"signal={signal} logs={i+1} fired={fired} available={available} elapsed={time.monotonic()-start:.1f}s", flush=True)
    print(f"FINAL signal={signal} fired={fired}/{available} mean_rho={sum(rhos)/len(rhos):.4f} rounds={ROUNDS} windows={WINDOWS} rows/window={MINUTES}", flush=True)
