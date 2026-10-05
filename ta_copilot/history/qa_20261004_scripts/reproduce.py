"""Offline QA reproductions; run from this directory's parent."""
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ta_copilot"))
import ta_copilot as ta
import tally_report as tr


def baseline():
    spec = importlib.util.spec_from_file_location("baseline_ta", ROOT / "baseline_7976db5/ta_copilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = baseline()
for size, mode in ((40, "flat"), (250, "flat"), (250, "ramp"), (250, "wave")):
    bars = []
    for i in range(size):
        close = 100 if mode == "flat" else 100 + i / 10 if mode == "ramp" else 100 + math.sin(i / 5)
        bars.append({"time": 60 * i, "open": close, "high": close + 1, "low": close - 1,
                     "close": close, "volume": 1})
    a, b = ta.analyze(bars, bars[-1]["close"], 0), old.analyze(bars, bars[-1]["close"], 0)
    print("legacy", size, mode, a["tally"], b["tally"], a["tally"] == b["tally"])

def row(minute, value, net=1, stale=False):
    return {"type": "minute", "ticker": "W", "minute": minute,
            "captured_at": ta.iso(90000 + minute * 60), "composite": value,
            "stale": stale, "yes_bid": None, "yes_ask": None,
            "timeframes": {"1m": {"family_net": net}}}

print("gap_1_to_4", [(r["minute"], c) for r, c, _ in tr.forward_changes({"W": {1: row(1, 100), 4: row(4, 130)}}, 3)["W"]])
stale = {"W": {1: row(1, 100, stale=True), 2: row(2, 150)}}
print("stale_forward", [(r["minute"], c) for r, c, _ in tr.forward_changes(stale, 1)["W"]])
state = {"ready": True, "stale": True, "kalshi_stale": False,
         "composite": 100, "average_60s": None, "order_flow": {}, "frames": {},
         "window": {"ticker": "W", "open_time": ta.iso(90000), "close_time": ta.iso(90900),
                    "seconds_left": 840, "strike": 100, "display_quotes": {}}}
print("stale_logged", ta.minute_record(state, 90060.5)["composite"], ta.minute_record(state, 90060.5)["stale"])
short_flow = [{"name": n, "state": "bull", "detail": "x"} for n in ("EMA stack", "EMA 9/21", "MACD")]
print("short_flow_family", ta.family_tally(short_flow, 1.0, with_flow=True))
bars = [{"time": i * 60, "open": 100 + i / 10, "high": 101 + i / 10,
         "low": 99 + i / 10, "close": 100 + i / 10, "volume": 1} for i in range(250)]
opening = ta.opening_record({"market": {"ticker": "OPEN", "open_time": ta.iso(15000),
    "close_time": ta.iso(15900), "floor_strike": 125}, "captured_at": 15000.2,
    "market_observed_at": 15000.2, "composite": 125, "coinbase_spot": 125,
    "flow_imbalance": 0.25, "bars": {"1m": bars}})
print("opening_flow", opening["capture_delay_seconds"], opening["flow_imbalance_1m"],
      opening["timeframes"]["1m"]["family"]["flow"])

with tempfile.TemporaryDirectory() as d:
    logger = ta.TallyLogger(Path(d), ta.Feed())
    logger.prepare()
    with patch.object(ta, "cached_state", return_value=state):
        logger.capture_minute(90060.5)
        restarted = ta.TallyLogger(Path(d), ta.Feed())
        restarted.prepare()
        restarted.capture_minute(90061.0)
    rows = [json.loads(x) for x in logger.path.read_text().splitlines()]
    print("restart_same_minute", [(r["ticker"], r["minute"]) for r in rows])
    with logger.path.open("ab") as f:
        f.write(b'{"type":"open"')
    try:
        restarted.prepare()
    except Exception as exc:
        print("torn_prepare", type(exc).__name__, str(exc))
    try:
        tr.load_rows(logger.path)
    except Exception as exc:
        print("torn_report", type(exc).__name__, str(exc))

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "tally_log.jsonl"
    rows = [
        {"type": "open", "ticker": "X", "timeframes": {"1m": {"lean": "bullish"}}},
        {"type": "settled", "ticker": "X", "result": "yes"},
        {"type": "open", "ticker": "X", "timeframes": {"1m": {"lean": "bearish"}}},
        {"type": "settled", "ticker": "X", "result": "yes"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    logger = ta.TallyLogger(Path(d), ta.Feed())
    logger.prepare()
    loaded = tr.load_rows(path)
    print("duplicate_conflict", "dashboard", logger.record["hits"], logger.record["scored"],
          "report", tr.summarize(*loaded[:2])["1m"]["hits"], tr.summarize(*loaded[:2])["1m"]["scored"])

print("contradictory_settlement", tr.summarize(
    {"X": {"timeframes": {"1m": {"lean": "bullish"}}}},
    {"X": {"result": "yes", "expiration_value": "99.0"}})["1m"]["hits"],
    "move", tr.settlement_moves({"X": {"target": 100.0}}, {"X": {"expiration_value": "99.0"}})[0][2])
print("market_exact_50c", tr.market_section(
    {"X": {"timeframes": {"1m": {"lean": "bullish"}}, "yes_bid": 0.49, "yes_ask": 0.51}},
    {"X": {"result": "yes"}}, {})[3])

try:
    tr.primary_result({}, {}, __import__("datetime").datetime(2026, 10, 15))
except Exception as exc:
    print("naive_primary", type(exc).__name__, str(exc))
