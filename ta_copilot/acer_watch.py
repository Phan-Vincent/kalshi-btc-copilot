#!/usr/bin/env python3
"""Off-Acer alarm: from the Mac, check that the Acer and its dashboard answer.

The Acer's own watcher can't report the Acer itself being down. This runs on
the Mac from a LaunchAgent every five minutes, alerts after two consecutive
failing checks, and stays silent when the Mac itself is offline. Only while
the Mac is awake.
"""
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATE_DIR = Path.home() / ".local/state/btc-acer-watch"
STATE_FILE = STATE_DIR / "state.json"
TELEGRAM = Path.home() / ".config/btc-study/telegram.json"
ACER = ("homelab.example-tailnet.ts.net", 22)  # MagicDNS name of the Acer
DASHBOARD = "https://homelab.example-tailnet.ts.net:11443/api/state?tf=1m"
INTERNET = ("api.telegram.org", 443)
FAILS_BEFORE_ALERT = 2
REPEAT_SECONDS = 6 * 3600


def reachable(address, timeout=5):
    try:
        with socket.create_connection(address, timeout=timeout):
            return True
    except OSError:
        return False


def dashboard_problem():
    try:
        with urlopen(DASHBOARD, timeout=8) as response:
            if response.status != 200:
                return f"dashboard HTTP {response.status}"
            state = json.load(response)
    except Exception:
        return "dashboard unreachable"
    if not state.get("ready"):
        return "dashboard not ready"
    if state.get("stale"):
        return "dashboard stale"
    return None


def problems():
    if not reachable(ACER):
        # The dashboard can't answer either; one clear line is enough.
        return ["Acer unreachable over Tailscale"]
    found = dashboard_problem()
    return [found] if found else []


def step(previous, found, now):
    """Return (message or None, next state). Persist only after a send succeeds."""
    fails = previous.get("fails", 0) + 1 if found else 0
    active, last = previous.get("active", False), previous.get("last_sent_at", 0)
    state = {"fails": fails, "active": active, "last_sent_at": last}
    if found and fails >= FAILS_BEFORE_ALERT and not active:
        return "[ACER] problem: " + "; ".join(found), {**state, "active": True, "last_sent_at": now}
    if found and active and now - last >= REPEAT_SECONDS:
        return "[ACER] still unhealthy: " + "; ".join(found), {**state, "last_sent_at": now}
    if not found and active:
        return "[ACER] recovered: Acer and dashboard answering", {**state, "active": False, "last_sent_at": now}
    return None, state


def store(value):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    temp = STATE_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value) + "\n")
    os.replace(temp, STATE_FILE)


def send_message(text):
    # The token stays in memory; never print it or an exception that may hold the URL.
    config = json.loads(TELEGRAM.read_text(encoding="utf-8"))
    url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
    data = urlencode({"chat_id": config["chat_id"], "text": text}).encode()
    with urlopen(Request(url, data=data, method="POST"), timeout=10) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram did not accept message")


def main():
    try:
        if not reachable(INTERNET):
            return 0  # the Mac is offline: no verdict, no state change
        try:
            previous = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            previous = {}
        message, state = step(previous, problems(), time.time())
        if message:
            send_message(message)
        store(state)
        return 0
    except Exception:
        print("[ACER] watch failed; credentials withheld", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
