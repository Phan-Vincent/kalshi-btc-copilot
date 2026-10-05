import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

out = Path(__file__).resolve().parents[1] / "scripts/live_samples.jsonl"
with out.open("w") as handle:
    for i in range(3):
        for tf in ("1m", "10m"):
            try:
                with urlopen(f"http://127.0.0.1:8781/api/state?tf={tf}", timeout=10) as response:
                    state = json.load(response)
                window = state.get("window") or {}
                row = {"at": datetime.now(timezone.utc).isoformat(), "tf": tf,
                       "ready": state.get("ready"), "spot": state.get("spot"),
                       "composite": state.get("composite"), "venues": state.get("venues"),
                       "ticker": window.get("ticker"), "target": window.get("strike"),
                       "yes_bid": window.get("yes_bid"), "yes_ask": window.get("yes_ask"),
                       "stale": state.get("stale"), "kalshi_stale": state.get("kalshi_stale"),
                       "family": (state.get("frames") or {}).get(tf, {}).get("family"),
                       "candles": (state.get("frames") or {}).get(tf, {}).get("chart", {}).get("candles", [])[-3:]}
            except Exception as exc:
                row = {"at": datetime.now(timezone.utc).isoformat(), "tf": tf, "error": str(exc)}
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            print(json.dumps({k: v for k, v in row.items() if k not in ("venues", "candles", "spot")}), flush=True)
        if i < 2:
            time.sleep(61)
