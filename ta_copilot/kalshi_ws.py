"""Read-only Kalshi authentication and market-data WebSocket parsers.

No key is loaded at import time. Callers keep credentials outside served state.
"""
import base64
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import stat
import time
from datetime import datetime, timezone


WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
API_URL = "https://external-api.kalshi.com"
ALLOWED_PATHS = frozenset(("/trade-api/ws/v2", "/trade-api/v2/api_keys",
                           "/trade-api/v2/cfbenchmarks/values"))


class KeyErrorSafe(ValueError):
    """Credential or scope failure with a message safe for the dashboard."""


class Credentials:
    def __init__(self, key_id, private_key):
        self.key_id = key_id
        self.private_key = private_key

    def __repr__(self):
        return "Credentials(<redacted>)"

    def headers(self, method, path, now_ms=None):
        if method != "GET" or path.split("?", 1)[0] not in ALLOWED_PATHS:
            raise ValueError("Kalshi signer permits only allowlisted GET paths")
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, padding
        from cryptography.hazmat.primitives import hashes
        stamp = str(int(time.time() * 1000) if now_ms is None else int(now_ms))
        message = (stamp + method + path.split("?", 1)[0]).encode("ascii")
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            signed = self.private_key.sign(message)
        elif isinstance(self.private_key, rsa.RSAPrivateKey):
            signed = self.private_key.sign(message, padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
                hashes.SHA256())
        else:
            raise KeyErrorSafe("unsupported Kalshi private key type")
        return {"KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-TIMESTAMP": stamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signed).decode("ascii")}


def load_credentials(directory=None):
    """Return (Credentials, None) or (None, safe reason). Never read the default in tests."""
    directory = Path(directory or os.environ.get("TA_KALSHI_KEY_DIR",
                      "~/.config/btc-ta-copilot/kalshi")).expanduser()
    if not directory.exists():
        return None, "not configured"
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise KeyErrorSafe("key directory ownership or type invalid")
        if info.st_mode & 0o077:
            raise KeyErrorSafe("key directory accessible by group or others")
        pem_path = directory / "private_key.pem"
        key_path = directory / "key_id"
        for path in (pem_path, key_path):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise KeyErrorSafe("key file ownership or type invalid")
            if path == pem_path and info.st_mode & 0o077:
                raise KeyErrorSafe("private_key.pem accessible by group or others")
        key_id = key_path.read_text(encoding="utf-8").strip()
        if not key_id or "\n" in key_id or "\r" in key_id:
            raise KeyErrorSafe("invalid key_id file")
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        private_key = load_pem_private_key(pem_path.read_bytes(), password=None)
        return Credentials(key_id, private_key), None
    except FileNotFoundError:
        return None, "key_id or private_key.pem missing"
    except (OSError, UnicodeError):
        return None, "key directory or file unreadable"
    except ImportError:
        return None, "cryptography unavailable"
    except KeyErrorSafe as exc:
        return None, str(exc)
    except (ValueError, TypeError):
        return None, "private_key.pem unparsable"


def check_scopes(credentials, fetch):
    """fetch receives signed headers and returns a decoded api_keys response."""
    data = fetch(credentials.headers("GET", "/trade-api/v2/api_keys"))
    keys = data.get("api_keys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        raise KeyErrorSafe("malformed Kalshi API key scope response")
    matches = [entry for entry in keys if isinstance(entry, dict) and
               entry.get("api_key_id") == credentials.key_id]
    if len(matches) != 1:
        raise KeyErrorSafe("Kalshi API key not listed")
    scopes = matches[0].get("scopes")
    if not isinstance(scopes, list) or not scopes or not all(
            isinstance(scope, str) and (scope == "read" or scope.startswith("read::"))
            for scope in scopes):
        raise KeyErrorSafe("Kalshi API key has non-read scope or malformed scopes")
    return True


def _decimal(value, positive=False):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("invalid Kalshi decimal") from None
    if not n.is_finite() or (positive and n <= 0):
        raise ValueError("invalid Kalshi decimal")
    return n


def brti_tick(frame):
    """Return (source_ms, value, is_whole_second) or None."""
    kind = frame.get("type")
    if kind not in ("cfbenchmarks_value", "cfbenchmarks_value_5hz"):
        return None
    msg = frame.get("msg") or {}
    if msg.get("index_id") != "BRTI":
        return None
    if kind == "cfbenchmarks_value_5hz":
        source = msg.get("source_ts_ms")
        value = msg.get("value_usd")
    else:
        raw = json.loads(msg["data"])
        if raw.get("type") != "value" or raw.get("id") != "BRTI":
            return None
        source, value = raw.get("time"), raw.get("value")
    if type(source) is not int or source <= 0:
        raise ValueError("invalid BRTI source time")
    return source, float(_decimal(value, positive=True)), source % 1000 == 0


def merge_brti_backfill(held, response, now):
    payload = (response.get("data") or {}).get("payload") if isinstance(response, dict) else None
    if not isinstance(payload, list):
        raise ValueError("malformed BRTI backfill")
    for row in payload:
        if not isinstance(row, dict) or type(row.get("time")) is not int:
            continue
        second = row["time"] // 1000
        if now - 1200 <= second <= now and second not in held:
            try:
                held[second] = float(_decimal(row.get("value"), positive=True))
            except ValueError:
                continue
    return held


def brti_average(held, first, count=60):
    values = [held.get(int(first) + i) for i in range(count)]
    return sum(values) / count if all(v is not None for v in values) else None


class BookStream:
    """Per-sid sequence and per-market books. Delta updates are O(1)."""
    def __init__(self):
        self.seq = {}
        self.books = {}
        self.synced = set()

    def apply(self, frame):
        kind = frame.get("type")
        if kind not in ("orderbook_snapshot", "orderbook_delta"):
            return None
        sid, seq = frame.get("sid"), frame.get("seq")
        msg = frame.get("msg") or {}
        ticker = msg.get("market_ticker")
        if type(sid) is not int or type(seq) is not int or not isinstance(ticker, str):
            raise ValueError("invalid orderbook frame")
        previous = self.seq.get(sid)
        if previous is not None and seq != previous + 1:
            self.invalidate(sid)
            return "gap"
        self.seq[sid] = seq
        if kind == "orderbook_snapshot":
            self.books[(sid, ticker)] = {
                side: {str(p): _decimal(c) for p, c in msg.get(side + "_dollars_fp", [])
                       if _decimal(c) > 0}
                for side in ("yes", "no")}
            self.synced.add((sid, ticker))
            return "snapshot"
        book = self.books.get((sid, ticker))
        if book is None or (sid, ticker) not in self.synced:
            return None
        side = msg.get("side")
        if side not in ("yes", "no"):
            raise ValueError("invalid orderbook side")
        price = str(msg.get("price_dollars"))
        count = book[side].get(price, Decimal(0)) + _decimal(msg.get("delta_fp"))
        if count > 0:
            book[side][price] = count
        else:
            book[side].pop(price, None)
        return "delta"

    def invalidate(self, sid):
        self.seq.pop(sid, None)
        self.synced = {key for key in self.synced if key[0] != sid}
        for key in list(self.books):
            if key[0] == sid:
                del self.books[key]

    def view(self, sid, ticker):
        if (sid, ticker) not in self.synced:
            return None
        book = self.books[(sid, ticker)]
        return {"orderbook_fp": {side + "_dollars": [
            [price, str(count)] for price, count in sorted(book[side].items(),
            key=lambda row: Decimal(row[0]))] for side in ("yes", "no")}}


def trade_row(frame):
    if frame.get("type") != "trade":
        return None
    msg = frame.get("msg") or {}
    at = msg.get("ts_ms")
    if type(at) is not int or not isinstance(msg.get("market_ticker"), str):
        raise ValueError("invalid Kalshi trade")
    price, count = _decimal(msg.get("yes_price_dollars"), True), _decimal(msg.get("count_fp"), True)
    return msg["market_ticker"], {"time": datetime.fromtimestamp(at / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
           "yes_price": str(price), "count": str(count), "taker_side": msg.get("taker_side"),
           "is_block_trade": bool(msg.get("is_block_trade")),
           "trade_id": msg.get("trade_id") if isinstance(msg.get("trade_id"), str) else None}


class Subscriptions:
    def __init__(self):
        self.next_id = 1
        self.pending = {}
        self.sids = {}
        self.tickers = {}

    def command(self, cmd, params, now=None):
        number = self.next_id
        self.next_id += 1
        self.pending[number] = (cmd, params, time.time() if now is None else now)
        return {"id": number, "cmd": cmd, "params": params}

    def ack(self, frame):
        kind, number = frame.get("type"), frame.get("id")
        if kind == "error" and number in self.pending:
            self.pending.pop(number)
            return "error"
        if kind not in ("subscribed", "ok", "unsubscribed") or number not in self.pending:
            return None
        cmd, params, _ = self.pending.pop(number)
        if kind == "subscribed" and cmd == "subscribe":
            channel, sid = frame["msg"]["channel"], frame["msg"]["sid"]
            self.sids[channel] = sid
            self.tickers[sid] = set(params.get("market_tickers", []))
        elif kind == "ok" and cmd == "update_subscription":
            sid = params["sid"]
            action = params["action"]
            if action == "add_markets":
                self.tickers.setdefault(sid, set()).update(params["market_tickers"])
            else:
                self.tickers.setdefault(sid, set()).difference_update(params["market_tickers"])
        elif cmd == "unsubscribe":
            for sid in params["sids"]:
                self.tickers.pop(sid, None)
                for channel, old in list(self.sids.items()):
                    if old == sid:
                        del self.sids[channel]
        return cmd

    def expired(self, now):
        return any(now - sent > 5 for _, _, sent in self.pending.values())

    def update(self, channel, desired, now=None):
        sid = self.sids.get(channel)
        if sid is None or any(params.get("sid") == sid for _, params, _ in self.pending.values()):
            return []
        current = self.tickers.get(sid, set())
        commands = []
        for action, tickers in (("add_markets", desired - current),
                                ("delete_markets", current - desired)):
            if tickers:
                commands.append(self.command("update_subscription", {"sid": sid,
                    "action": action, "market_tickers": sorted(tickers)}, now))
        return commands
