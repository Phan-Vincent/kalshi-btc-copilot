import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ta_copilot"))
import tally_report as tr
from scipy.stats import spearmanr

rng = random.Random(81)
max_diff = 0
for n in (3, 5, 20, 100):
    for _ in range(100):
        pairs = [(rng.randrange(5), rng.randrange(7)) for _ in range(n)]
        actual = tr.spearman(pairs)
        expected = spearmanr([x for x, _ in pairs], [y for _, y in pairs]).statistic
        if actual is None:
            assert math.isnan(expected)
        else:
            max_diff = max(max_diff, abs(actual - expected))
print("spearman_400_tied_cases_max_abs_diff", max_diff)
for pairs in ([], [(1, 2)], [(1, 2), (2, 3)], [(1, 2), (1, 3), (1, 4)]):
    print("edge", len(pairs), tr.spearman(pairs))

def minute(t, m, val, net):
    return {"ticker": t, "minute": m, "captured_at": f"2026-10-04T{m:02d}:00:00Z",
            "composite": val, "yes_bid": None, "yes_ask": None,
            "timeframes": {"1m": {"family_net": net}}}

minutes = {}
for i in range(800):
    ticker = f"T{i:04d}"
    minutes[ticker] = {m: minute(ticker, m, 100 + (i % 7) + m * (i % 3 - 1), i % 3 - 1)
                       for m in range(1, 8)}
early = tr.primary_result({}, minutes, datetime(2026, 10, 13, tzinfo=timezone.utc))
late = tr.primary_result({}, minutes, datetime(2026, 10, 15, tzinfo=timezone.utc))
minutes["Z_LATER"] = {m: minute("Z_LATER", m, m, 3) for m in range(1, 8)}
again = tr.primary_result({}, minutes, datetime(2026, 10, 15, tzinfo=timezone.utc))
print("primary_gate", early["evaluated"], late["evaluated"], late["rows"],
      (late["rho"], late["ci"]) == (again["rho"], again["ci"]))
print("market_band", tr.MARKET_BAND)
