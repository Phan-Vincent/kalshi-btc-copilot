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


def problems(service_active, state, now, feed_since=None):
    found = []
    if not service_active:
        found.append("service inactive")
    if state is None:
        found.append("/api/state unavailable")
        return found
    if not state.get("ready"):
        found.append("/api/state not ready")
    if state.get("stale"):
        found.append("/api/state stale")
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
    service = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "btc-ta-copilot.service"],
                             timeout=5, check=False).returncode == 0
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
        try:
            previous = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            previous = {"active": False, "last_sent_at": 0}
        now = time.time()
        service, state = check()
        feed_since = error_start_times(state, previous.get("feed_since", {}), now)
        message, next_state = transition(previous, problems(service, state, now, feed_since), now)
        if message:
            send_message(message)
        store(STATE_FILE, json.dumps({**next_state, "feed_since": feed_since}) + "\n")
        return 0
    except Exception:
        # No exception text: urllib exceptions can contain the bot-token URL.
        print("[BTC TA] watcher failed; credentials withheld", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
