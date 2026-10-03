#!/usr/bin/env python3
"""Kalshi prediction-market reads only. No order creation or account mutations.

Python 3.9+; cryptography is needed only for authenticated reads.
Raw response text preserves the server's original numeric precision.
"""
import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

BASE = "https://external-api.kalshi.com/trade-api/v2"
CONFIG = Path(__file__).with_name("kalshi_readonly_config.json")
PUBLIC = (
    r"/live_data/events/[A-Za-z0-9_.-]+",
    r"/exchange/(status|schedule)", r"/series", r"/series/[A-Za-z0-9_.-]+",
    r"/events", r"/events/[A-Za-z0-9_.-]+",
    r"/markets", r"/markets/(trades|candlesticks|orderbooks)",
    r"/markets/[A-Za-z0-9_.-]+(?:/orderbook)?",
    r"/series/[A-Za-z0-9_.-]+/markets/[A-Za-z0-9_.-]+/candlesticks",
    r"/historical/cutoff", r"/historical/(markets|trades)",
    r"/historical/markets/[A-Za-z0-9_.-]+(?:/candlesticks)?",
)
PRIVATE = (r"/portfolio/(balance|positions|fills|settlements|orders)",
           r"/cfbenchmarks/(values|history/values)",
           r"/historical/(fills|orders|positions)",
           r"/account/(limits|endpoint_costs)")


class ReadError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward authentication headers to a redirect target.


def dump(value):
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def ticker(value):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value):
        raise ReadError("Invalid ticker")
    return quote(value.upper(), safe="")


class KalshiReadOnly:
    def __init__(self, config=CONFIG):
        self.config = Path(config)
        self.opener = build_opener(NoRedirect())

    def _headers(self, full_path):
        if not self.config.exists():
            raise ReadError("Credential configuration is missing")
        cfg = json.loads(self.config.read_text())
        key_id = cfg.get("api_key_id") or os.environ.get(cfg.get("api_key_id_env", "KALSHI_READONLY_API_KEY_ID"))
        if not key_id:
            raise ReadError("Matching read-only API key ID is missing; run configure locally")
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
        try:
            key = load_pem_private_key(Path(cfg["private_key_path"]).expanduser().read_bytes(), password=None)
        except Exception:
            raise ReadError("Cannot load configured signing key") from None
        ts = str(time.time_ns() // 1000000)
        message = (ts + "GET" + full_path).encode()
        if isinstance(key, rsa.RSAPrivateKey):
            sig = key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                           salt_length=hashes.SHA256().digest_size), hashes.SHA256())
        elif isinstance(key, ed25519.Ed25519PrivateKey):
            sig = key.sign(message)
        else:
            raise ReadError("Unsupported signing key type")
        return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    def get(self, path, params=None, authenticated=False):
        """Only enumerated GET routes on one fixed HTTPS origin are reachable."""
        if any(c in path for c in ("?", "#", "%")) or ".." in path:
            raise ReadError("Invalid endpoint path")
        private = any(re.fullmatch(p, path) for p in PRIVATE)
        if not private and not any(re.fullmatch(p, path) for p in PUBLIC):
            raise ReadError("Endpoint is outside the read-only allowlist")
        params = dict(params or {})
        full_path = "/trade-api/v2" + path
        url = BASE + path + ("?" + urlencode(params) if params else "")
        for attempt in range(3):
            headers = {"Accept": "application/json", "Cache-Control": "no-cache"}
            if private or authenticated:
                headers.update(self._headers(full_path))
            req = Request(url, headers=headers, method="GET")
            started = datetime.now(timezone.utc).isoformat()
            try:
                with self.opener.open(req, timeout=20) as response:
                    raw = response.read().decode("utf-8")
                    return {"request_started_at": started,
                            "retrieved_at": datetime.now(timezone.utc).isoformat(),
                            "endpoint": path, "http_status": response.status,
                            "server_date": response.headers.get("Date"),
                            "raw_json": raw,
                            "data": json.loads(raw, parse_float=Decimal)}
            except HTTPError as error:
                status = error.code
                error.close()  # Do not print error bodies, requests or headers.
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise ReadError("Kalshi GET failed with HTTP " + str(status), status) from None
            except (URLError, TimeoutError, OSError):
                raise ReadError("Network or TLS failure during Kalshi GET") from None
        raise ReadError("Read attempts exhausted")

    def pages(self, path, params=None, max_pages=10):
        """Return raw page envelopes and flag truncation; never claim all results silently."""
        if max_pages < 1:
            raise ReadError("max_pages must be positive")
        params = dict(params or {})
        results, seen = [], set()
        for _ in range(max_pages):
            page = self.get(path, params)
            results.append(page)
            cursor = page["data"].get("cursor")
            if not cursor:
                return {"pages": results, "complete": True, "next_cursor": None}
            if cursor in seen:
                raise ReadError("Pagination cursor repeated")
            seen.add(cursor)
            params["cursor"] = cursor
        return {"pages": results, "complete": False, "next_cursor": cursor}

    def market(self, value):
        return self.get("/markets/" + ticker(value))

    def event(self, value):
        return self.get("/events/" + ticker(value))

    def orderbook(self, value):
        path = "/markets/" + ticker(value) + "/orderbook"
        try:
            return self.get(path)  # Currently works without auth; docs disagree.
        except ReadError as error:
            if error.status != 401:
                raise
            return self.get(path, authenticated=True)

    def search(self, text="", series=None, event=None, max_pages=10):
        params = {"status": "open", "limit": 100}
        if series:
            params["series_ticker"] = ticker(series)
        if event:
            params["event_ticker"] = ticker(event)
        result = self.pages("/markets", params, max_pages)
        result["matches"] = [m for p in result["pages"] for m in p["data"].get("markets", [])
                             if text.casefold() in " ".join(str(m.get(k, "")) for k in
                             ("ticker", "title", "subtitle", "yes_sub_title", "no_sub_title")).casefold()]
        return result

    def resolve(self, value):
        """Verify URL candidates against API; return event/series choices, never guess a contract."""
        if "://" not in value:
            candidates = [value]
        else:
            u = urlsplit(value)
            if u.scheme != "https" or u.hostname not in ("kalshi.com", "www.kalshi.com"):
                raise ReadError("Expected a Kalshi HTTPS market URL")
            parts = [unquote(p) for p in u.path.split("/") if p]
            if len(parts) < 2 or parts[0] != "markets":
                raise ReadError("Unrecognized URL: inspect the page for its exact event/market ticker")
            candidates = list(dict.fromkeys([parts[-1], parts[1]]))
        for candidate in candidates:
            t = ticker(candidate)
            for kind, path in (("market", "/markets/"+t), ("event", "/events/"+t), ("series", "/series/"+t)):
                try:
                    found = self.get(path)
                    return {"kind": kind, "ticker": t, "response": found}
                except ReadError as error:
                    if error.status != 404:
                        raise
        raise ReadError("No verified ticker found; inspect the page and select the contract explicitly")

    def portfolio(self, name, params=None, max_pages=10):
        if name not in ("balance", "positions", "fills", "settlements", "orders"):
            raise ReadError("Unknown portfolio read")
        return self.get("/portfolio/balance", params) if name == "balance" else self.pages("/portfolio/"+name, params, max_pages)

    def snapshot(self, value):
        market = self.market(value)
        m = market["data"]["market"]
        if m.get("market_type") != "binary" or Decimal(m.get("notional_value_dollars", "1")) != 1:
            raise ReadError("Complement interpretation requires a binary $1 contract")
        event = self.event(m["event_ticker"])
        series = self.get("/series/" + ticker(event["data"]["event"]["series_ticker"]))
        book = self.orderbook(value)
        return {"base_url": BASE, "market": market, "event": event, "series": series,
                "orderbook": book, "book_summary": summarize_book(book["data"]),
                "warning": "Sequential snapshots; quotes can change. Fees excluded. Last trade and midpoint are not executable quotes."}


def summarize_book(data):
    if "orderbook_fp" not in data:
        raise ReadError("Unsupported orderbook schema")
    book = data["orderbook_fp"]
    result = {}
    levels = {}
    for side in ("yes", "no"):
        rows = book.get(side + "_dollars") or []
        levels[side] = sorted([(Decimal(p), Decimal(q)) for p, q in rows if Decimal(q) > 0], reverse=True)
        best = levels[side][0][0] if levels[side] else None
        result[side + "_bid_dollars"] = best
        result[side + "_bid_size_fp"] = sum((q for p, q in levels[side] if p == best), Decimal(0)) if best is not None else None
        result[side + "_bid_level_count"] = len(levels[side])
        result[side + "_bid_depth_fp"] = sum((q for p, q in levels[side]), Decimal(0))
        result[side + "_bid_depth_within_5c_fp"] = sum((q for p, q in levels[side] if best-p <= Decimal("0.05")), Decimal(0)) if best is not None else None
        result[side + "_top_5_bid_levels"] = levels[side][:5]
    for side, opposite in (("yes", "no"), ("no", "yes")):
        other = result[opposite + "_bid_dollars"]
        ask = Decimal(1) - other if other is not None else None
        bid = result[side + "_bid_dollars"]
        result[side + "_ask_dollars"] = ask
        result[side + "_ask_size_fp"] = result[opposite + "_bid_size_fp"]
        result[side + "_spread_dollars"] = ask - bid if ask is not None and bid is not None else None
        result[side + "_midpoint_estimate_dollars"] = (ask+bid)/2 if ask is not None and bid is not None else None
    result["ask_source"] = "$1 minus opposite-side bid; same displayed quantity"
    result["quote_source"] = "orderbook; not last trade or market midpoint"
    return result


def configure(path):
    """Store only the matching ID locally; do not move or duplicate the key."""
    cfg = json.loads(path.read_text())
    key_id = getpass.getpass("Matching read-only Kalshi API key ID (hidden): ").strip()
    if not key_id:
        raise ReadError("Empty API key ID")
    cfg["api_key_id"] = key_id
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(dump(cfg) + "\n")
    print("API key ID saved locally. Private key unchanged.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("snapshot", "resolve", "search", "balance", "positions", "fills", "settlements", "orders", "status", "configure"))
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--config", type=Path, default=CONFIG)
    p.add_argument("--series")
    p.add_argument("--event")
    p.add_argument("--max-pages", type=int, default=10)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        if args.command == "configure":
            configure(args.config)
            return
        c = KalshiReadOnly(args.config)
        if args.command == "snapshot":
            result = c.snapshot(args.value)
        elif args.command == "resolve":
            result = c.resolve(args.value)
        elif args.command == "search":
            result = c.search(args.value, args.series, args.event, args.max_pages)
        elif args.command == "status":
            result = c.get("/exchange/status")
        else:
            result = c.portfolio(args.command, max_pages=args.max_pages)
        content = dump(result) + "\n"
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            print("Read-only response saved.")
        else:
            print(content)
    except (ReadError, ValueError, KeyError, OSError) as error:
        # Avoid tracebacks or arbitrary config/server content in diagnostic output.
        print(str(error) if isinstance(error, ReadError) else "Local configuration or response parsing failed", file=__import__("sys").stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
