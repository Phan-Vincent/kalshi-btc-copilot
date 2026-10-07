#!/usr/bin/env python3
"""User-timer health alerts for the separate BTC TA dashboard."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATE_DIR = Path.home() / "btc-ta/state"
STATE_FILE = STATE_DIR / "ta_watch_state.json"
TEST_MARKER = STATE_DIR / "ta_watch_test_sent"
TELEGRAM = Path.home() / ".config/btc-study/telegram.json"
API = "http://127.0.0.1:8770/api/state?tf=1m"
REPEAT_SECONDS = 6 * 3600
FEED_ERROR_SECONDS = 10 * 60
# Kalshi context must stay stale across checks spanning this long (the third
# consecutive five-minute check) before it counts: rollovers recover in seconds.
KALSHI_STALE_SECONDS = 8 * 60
DEFAULT_STATE = {"active": False, "last_sent_at": 0}


def secure_state_dir():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def store(path, value):
    secure_state_dir()
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def error_start_times(state, prior, now):
    starts = {}
    for name, error in (state or {}).get("errors", {}).items():
        try:
            from ta_copilot import parse_ts
            observed = parse_ts(error.get("first_at", error["at"]))
        except (KeyError, ValueError, TypeError):
            observed = now
        starts[name] = min(observed, prior.get(name, now))
    return starts


def load_previous(now):
    """Saved watcher state; an unreadable or malformed file is set aside, not fatal."""
    try:
        previous = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_STATE)
    except (OSError, ValueError):
        previous = None
    if valid_state(previous):
        return previous
    try:
        os.replace(STATE_FILE, STATE_FILE.with_suffix(f".corrupt-{int(now)}"))
    except OSError:
        pass
    print("[BTC TA] watcher state unreadable; set aside and reset", file=sys.stderr)
    return dict(DEFAULT_STATE)


def valid_state(value):
    number = (int, float)
    return (isinstance(value, dict) and isinstance(value.get("active", False), bool)
            and isinstance(value.get("last_sent_at", 0), number)
            and isinstance(value.get("feed_since", {}), dict)
            and all(isinstance(v, number) for v in value.get("feed_since", {}).values())
            and isinstance(value.get("kalshi_stale_since"), (*number, type(None))))


def kalshi_stale_since(state, prior, now):
    """When the current stretch of stale Kalshi context began, or None if fresh."""
    if not state or not state.get("kalshi_stale"):
        return None
    return prior if prior is not None else now


def problems(service_active, state, now, feed_since=None, kalshi_since=None):
    found = []
    if service_active is None:
        found.append("service status unknown (systemctl failed)")
    elif not service_active:
        found.append("service inactive")
    if state is None:
        found.append("/api/state unavailable")
        return found
    if not state.get("ready"):
        found.append("/api/state not ready")
    if state.get("stale"):
        found.append("/api/state stale")
    if state.get("kalshi_stale") and kalshi_since is not None and now - kalshi_since >= KALSHI_STALE_SECONDS:
        found.append("Kalshi window missing or stale >8 min")
    feed_since = error_start_times(state, {}, now) if feed_since is None else feed_since
    for name, error in sorted(state.get("errors", {}).items()):
        if now - feed_since.get(name, now) > FEED_ERROR_SECONDS:
            found.append(f"feed {name} error >10 min")
    return found


def transition(previous, current, now):
    """Return message and next state; caller persists only after send succeeds."""
    active = bool(current)
    if active and not previous.get("active"):
        return "[BTC TA] problem: " + "; ".join(current), {"active": True, "last_sent_at": now}
    if not active and previous.get("active"):
        return "[BTC TA] recovered: service and feeds healthy", {"active": False, "last_sent_at": now}
    if active and now - previous.get("last_sent_at", 0) >= REPEAT_SECONDS:
        return "[BTC TA] still unhealthy: " + "; ".join(current), {"active": True, "last_sent_at": now}
    return None, previous


def send_message(text):
    # Read credentials only on Acer, in memory. Never put the token in output.
    config = json.loads(TELEGRAM.read_text(encoding="utf-8"))
    url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
    data = urlencode({"chat_id": config["chat_id"], "text": text}).encode()
    with urlopen(Request(url, data=data, method="POST"), timeout=10) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram did not accept message")


def check():
    """(service active: True/False/None if unknown, state or None). Never raises."""
    try:
        service = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "btc-ta-copilot.service"],
                                 timeout=5, check=False).returncode == 0
    except Exception:
        service = None
    try:
        with urlopen(API, timeout=5) as response:
            if response.status != 200:
                raise ValueError("state status")
            state = json.load(response)
    except Exception:
        state = None
    return service, state


def main():
    try:
        secure_state_dir()
        if len(sys.argv) == 2 and sys.argv[1] == "--test":
            if not TEST_MARKER.exists():
                send_message("[BTC TA] test alert — watcher installed")
                store(TEST_MARKER, "sent\n")
            return 0
        if len(sys.argv) != 1:
            return 2
        now = time.time()
        previous = load_previous(now)
        service, state = check()
        feed_since = error_start_times(state, previous.get("feed_since", {}), now)
        kalshi_since = kalshi_stale_since(state, previous.get("kalshi_stale_since"), now)
        message, next_state = transition(previous, problems(service, state, now, feed_since, kalshi_since), now)
        if message:
            send_message(message)
        store(STATE_FILE, json.dumps({"active": next_state.get("active", False),
                                      "last_sent_at": next_state.get("last_sent_at", 0),
                                      "feed_since": feed_since, "kalshi_stale_since": kalshi_since}) + "\n")
        return 0
    except Exception:
        # No exception text: urllib exceptions can contain the bot-token URL.
        print("[BTC TA] watcher failed; credentials withheld", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
