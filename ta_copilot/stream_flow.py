"""Public-venue trade-stream parsing and current-window order flow.

Scope
-----
Parses public, unauthenticated trade messages from two venues and turns them
into descriptive order-flow statistics for the *current* Kalshi 15-minute
window:

* Coinbase Exchange (``wss://ws-feed.exchange.coinbase.com``): ``matches``
  (``type: "match"``) and ``ticker`` (``type: "ticker"``).
* Kraken (``wss://ws.kraken.com/v2``): the ``trade`` channel, v2 envelope.

This module is data-only. It prints and returns observed volume, never a
directional view, an entry/exit call or a probability of winning.

Taker-side semantics (the important part)
-----------------------------------------
The two Coinbase channels use *opposite* conventions, which is the classic
way to invert an order-flow series:

* ``match``: ``side`` is the **maker** (resting) side. A maker ``sell`` means
  the maker's resting offer was lifted, so the **taker bought** (an up-tick).
* ``ticker``: ``side`` is the **taker** (aggressor) side, reported directly.
* Kraken v2 ``trade``: ``side`` is the **taker** side, and ``ord_type`` is the
  taker order's type.

Every normalized :class:`Trade` therefore carries a ``taker_side`` that has
already been converted to the aggressor's perspective, so downstream code
never has to know which channel a trade came from.

Window anchoring
----------------
Flow is anchored to a window grid of ``window_seconds`` (default 900 s,
UTC epoch aligned -- the same grid ``ta_copilot.py`` uses for its 15-minute
anchor). On a window change the cumulative series restarts at the new open;
trades carrying a timestamp from an *earlier* window are dropped rather than
folded into the running totals.

Deduplication
-------------
Trades are keyed by ``(venue, trade_id)``. That collapses three real
duplicate sources: a reconnect replay, a Kraken v2 subscription snapshot of
the last 50 trades, and a Coinbase client subscribed to *both* ``matches``
and ``ticker`` (which report the same ``trade_id`` for the same fill).

Spot quotes
-----------
:func:`parse_coinbase_spot` validates a Coinbase Exchange spot quote from
either the WebSocket ``ticker`` channel or the public REST ticker route. Every
numeric field is checked for finiteness -- a ``NaN``/``Infinity`` bid, ask,
price or time is rejected as malformed rather than coerced. The returned
:class:`SpotQuote` is timed by the *exchange trade time*, so a delayed frame
is not reported as fresh spot.

Bounded memory
--------------
Every buffer is capped: retained trades, CVD chart points, large-trade
markers and dedup keys. Nothing grows without limit on a long-running feed.

Standard library only.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from typing import Any, Iterable, Optional

__all__ = [
    "COINBASE",
    "COINBASE_IGNORED_TYPES",
    "COINBASE_TRADE_TYPES",
    "DEFAULT_LARGE_TRADE_SIZE",
    "DEFAULT_LARGE_TRADE_USD",
    "DEFAULT_MAX_LARGE_TRADES",
    "DEFAULT_MAX_POINTS",
    "DEFAULT_MAX_SEEN_IDS",
    "DEFAULT_MAX_TRADES",
    "DEFAULT_SPOT_MAX_AGE",
    "DEFAULT_WINDOW_SECONDS",
    "FUTURE_TIMESTAMP_TOLERANCE",
    "KRAKEN",
    "ROLLING_SECONDS",
    "TAKER_BUY",
    "TAKER_SELL",
    "MalformedMessage",
    "OrderFlow",
    "SpotQuote",
    "Trade",
    "UnknownVenue",
    "detect_venue",
    "parse_coinbase",
    "parse_coinbase_spot",
    "parse_kraken",
    "parse_message",
]

# ---------------------------------------------------------------- constants

COINBASE = "coinbase"
KRAKEN = "kraken"

TAKER_BUY = "buy"
TAKER_SELL = "sell"

#: Coinbase Exchange message types that carry a trade.
COINBASE_TRADE_TYPES = ("match", "last_match", "ticker")

#: Coinbase Exchange message types that are known NOT to carry a trade.
#: Anything else (including a missing ``type``) is also ignored.
COINBASE_IGNORED_TYPES = (
    "subscriptions", "heartbeat", "error", "activate", "received", "open",
    "done", "change", "margin_profile_update", "status",
)

DEFAULT_WINDOW_SECONDS = 900
ROLLING_SECONDS = 60

#: A trade at or above this notional (price x size, USD) is "large".
DEFAULT_LARGE_TRADE_USD = 25_000.0
#: Optional second, size-based threshold; ``None`` disables it.
DEFAULT_LARGE_TRADE_SIZE: Optional[float] = None

DEFAULT_MAX_TRADES = 20_000
DEFAULT_MAX_POINTS = 1_500
DEFAULT_MAX_LARGE_TRADES = 250
DEFAULT_MAX_SEEN_IDS = 40_000

_COINBASE_ALIASES = ("coinbase", "coinbase-exchange", "coinbase_pro", "coinbasepro", "exchange")


# ---------------------------------------------------------------- errors

class StreamFlowError(ValueError):
    """Base class for every error this module raises."""


class MalformedMessage(StreamFlowError):
    """A frame that is not JSON, not an object, or not a valid trade.

    Non-trade frames (subscriptions, heartbeats, status, ...) are **not**
    errors: the ``parse_*`` functions return ``[]`` for those. A live reader
    should catch ``StreamFlowError`` per frame and keep the socket open.
    """


class UnknownVenue(StreamFlowError):
    """The venue name is neither Coinbase Exchange nor Kraken."""


# ---------------------------------------------------------------- helpers

def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_product(value: Any) -> str:
    """Normalize a product/pair so ``BTC-USD`` and ``BTC/USD`` compare equal."""
    return str(value).strip().upper().replace("/", "").replace("-", "").replace("_", "")


def _as_object(payload: Any) -> dict:
    """Decode a raw socket frame (str/bytes/dict) into a JSON object."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MalformedMessage(f"frame is not valid UTF-8: {exc}") from exc
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            raise MalformedMessage("empty frame")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MalformedMessage(f"frame is not JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise MalformedMessage(
                f"expected a JSON object, got {type(decoded).__name__} "
                "(array frames are Kraken v1; this module targets v2)")
        return decoded
    raise MalformedMessage(f"unsupported frame type: {type(payload).__name__}")


def _num(value: Any, field: str, *, positive: bool = True) -> float:
    """Validate a numeric field: reject bool/None/blank/non-finite as needed."""
    if isinstance(value, bool) or value is None:
        raise MalformedMessage(f"{field}: expected a number, got {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise MalformedMessage(f"{field}: empty value")
        try:
            number = float(text)
        except ValueError as exc:
            raise MalformedMessage(f"{field}: not a number: {value!r}") from exc
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise MalformedMessage(f"{field}: expected a number, got {type(value).__name__}")
    if not math.isfinite(number):
        raise MalformedMessage(f"{field}: not finite: {value!r}")
    if positive and number <= 0:
        raise MalformedMessage(f"{field}: expected a positive number, got {value!r}")
    return number


def _epoch(value: Any, field: str = "time") -> float:
    """Validate a time field: ISO-8601 string (RFC 3339) or epoch seconds.

    Naive ISO strings are read as UTC. Fractional seconds of any length are
    accepted and truncated to microseconds, which covers Kraken v2's
    nanosecond timestamps.
    """
    if isinstance(value, bool) or value is None:
        raise MalformedMessage(f"{field}: expected a timestamp, got {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise MalformedMessage(f"{field}: empty timestamp")
        try:
            seconds = float(text)
        except ValueError:
            iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
            try:
                parsed = datetime.fromisoformat(iso_text)
            except ValueError as exc:
                raise MalformedMessage(f"{field}: not a timestamp: {value!r}") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = parsed.timestamp()
    else:
        raise MalformedMessage(f"{field}: expected a timestamp, got {type(value).__name__}")
    if not math.isfinite(seconds) or seconds <= 0:
        raise MalformedMessage(f"{field}: implausible timestamp: {value!r}")
    return seconds


def _taker_side(value: Any, field: str = "side") -> str:
    if not isinstance(value, str):
        raise MalformedMessage(f"{field}: expected 'buy' or 'sell', got {value!r}")
    side = value.strip().lower()
    if side not in (TAKER_BUY, TAKER_SELL):
        raise MalformedMessage(f"{field}: expected 'buy' or 'sell', got {value!r}")
    return side


def _identifier(value: Any, field: str = "trade_id") -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MalformedMessage(f"{field}: expected an id, got {value!r}")
    if isinstance(value, (int, str)):
        text = str(value).strip()
        if text:
            return text
        raise MalformedMessage(f"{field}: empty id")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MalformedMessage(f"{field}: not finite: {value!r}")
        return str(int(value)) if value.is_integer() else repr(value)
    raise MalformedMessage(f"{field}: expected an id, got {type(value).__name__}")


def _synthetic_id(prefix: str, timestamp: float, price: float, size: float) -> str:
    """Fallback key when a venue omits its trade id (still dedups exact replays)."""
    return f"{prefix}:{timestamp!r}:{price!r}:{size!r}"


# ---------------------------------------------------------------- trade

@dataclass(frozen=True)
class Trade:
    """One normalized trade, expressed from the taker's point of view."""

    venue: str
    product: str
    price: float
    size: float
    timestamp: float
    taker_side: str
    trade_id: str
    reported_time: Any = None
    channel: str = ""

    @property
    def notional(self) -> float:
        """Traded notional in quote currency (USD)."""
        return self.price * self.size

    @property
    def key(self) -> tuple:
        """Deduplication key. ``trade_id`` is unique per venue book."""
        return (self.venue, self.trade_id)

    def as_dict(self) -> dict:
        return {
            "venue": self.venue,
            "product": self.product,
            "price": self.price,
            "size": self.size,
            "notional": self.notional,
            "timestamp": self.timestamp,
            "time": int(self.timestamp),
            "taker_side": self.taker_side,
            "trade_id": self.trade_id,
            "channel": self.channel,
        }


# ---------------------------------------------------------------- parsers

def parse_message(venue: Optional[str], payload: Any = None) -> list:
    """Parse one socket frame into normalized trades (possibly an empty list).

    ``venue`` may be ``None`` to sniff it with :func:`detect_venue`. Pass the
    payload positionally to sniff: ``parse_message(payload)``.
    """
    if payload is None:
        payload = venue
        venue = None
    message = _as_object(payload)
    if venue is None:
        venue = detect_venue(message)
        if venue is None:
            # An unrecognizable frame is not a trade; sniffing must not tear
            # down a reader loop. An explicitly named venue still raises.
            return []
    name = str(venue).strip().lower()
    if name in _COINBASE_ALIASES:
        return _parse_coinbase(message)
    if name == KRAKEN:
        return _parse_kraken(message)
    raise UnknownVenue(f"unknown venue: {venue!r}")


def detect_venue(payload: Any) -> Optional[str]:
    """Best-effort venue sniff.

    Kraken v2 frames carry ``channel`` (data) or ``method`` (the subscribe
    acknowledgement); Coinbase Exchange frames carry ``type`` or
    ``product_id``. Returns ``None`` when the frame matches neither.
    """
    message = _as_object(payload)
    if "channel" in message or "method" in message:
        return KRAKEN
    if "type" in message or "product_id" in message:
        return COINBASE
    return None


def parse_coinbase(payload: Any, product: Optional[str] = None) -> list:
    """Parse a Coinbase Exchange ``matches`` / ``ticker`` frame.

    Subscription acknowledgements, heartbeats and other non-trade frames
    return ``[]``. Trade frames with an invalid price, size, time or side
    raise :class:`MalformedMessage`.
    """
    return _parse_coinbase(_as_object(payload), product)


def parse_kraken(payload: Any, product: Optional[str] = None) -> list:
    """Parse a Kraken v2 ``trade`` frame (snapshot or update).

    Every other channel -- heartbeats, the ``status`` channel and the
    ``{"method": "subscribe", ...}`` acknowledgement -- returns ``[]``.
    """
    return _parse_kraken(_as_object(payload), product)


def _parse_coinbase(message: dict, product: Optional[str] = None) -> list:
    message_type = message.get("type")
    if not isinstance(message_type, str):
        return []
    kind = message_type.strip().lower()
    if kind == "match" or kind == "last_match":
        trade = _coinbase_match(message)
        return _filter_product([trade], product)
    if kind == "ticker":
        trade = _coinbase_ticker(message)
        return _filter_product([trade] if trade else [], product)
    # subscriptions / heartbeat / error / anything else: not a trade.
    return []


def _coinbase_match(message: dict) -> Trade:
    """``matches`` channel: ``side`` is the MAKER side, so invert it."""
    maker_side = _taker_side(message.get("side"), "side")
    taker_side = TAKER_BUY if maker_side == TAKER_SELL else TAKER_SELL
    price = _num(message.get("price"), "price")
    size = _num(message.get("size"), "size")
    stamp = _epoch(message.get("time"), "time")
    trade_id = _identifier(message.get("trade_id"))
    return Trade(venue=COINBASE, product=str(message.get("product_id") or ""),
                 price=price, size=size, timestamp=stamp, taker_side=taker_side,
                 trade_id=trade_id or _synthetic_id("match", stamp, price, size),
                 reported_time=message.get("time"), channel="match")


def _coinbase_ticker(message: dict) -> Optional[Trade]:
    """``ticker`` channel: ``side`` is already the TAKER side.

    The ticker stream also carries book and 24 h statistics. A ticker frame
    published before the first fill has no ``last_size`` and no ``side``;
    that is a non-trade update and returns ``None``.
    """
    if message.get("last_size") is None and message.get("side") is None:
        return None
    taker_side = _taker_side(message.get("side"), "side")
    size = _num(message.get("last_size"), "last_size")
    price = _num(message.get("price"), "price")
    stamp = _epoch(message.get("time"), "time")
    trade_id = _identifier(message.get("trade_id"))
    # Deliberately the same id space as `match`: the two channels report the
    # same trade_id for the same fill, so a dual-subscribed client dedups.
    return Trade(venue=COINBASE, product=str(message.get("product_id") or ""),
                 price=price, size=size, timestamp=stamp, taker_side=taker_side,
                 trade_id=trade_id or _synthetic_id("ticker", stamp, price, size),
                 reported_time=message.get("time"), channel="ticker")


def _parse_kraken(message: dict, product: Optional[str] = None) -> list:
    channel = message.get("channel")
    if not isinstance(channel, str) or channel.strip().lower() != "trade":
        # heartbeat / status / subscribe ack / book / anything else
        return []
    data = message.get("data")
    if data is None:
        raise MalformedMessage("trade: missing data array")
    if not isinstance(data, list):
        raise MalformedMessage(f"trade: expected a data array, got {type(data).__name__}")
    trades = [_kraken_trade(item) for item in data]
    return _filter_product(trades, product)


def _kraken_trade(item: Any) -> Trade:
    if not isinstance(item, dict):
        raise MalformedMessage(f"trade entry: expected an object, got {type(item).__name__}")
    taker_side = _taker_side(item.get("side"), "side")
    price = _num(item.get("price"), "price")
    size = _num(item.get("qty"), "qty")
    stamp = _epoch(item.get("timestamp"), "timestamp")
    trade_id = _identifier(item.get("trade_id"))
    order_type = item.get("ord_type")
    channel = "trade"
    if isinstance(order_type, str) and order_type.strip():
        # Keep the taker order type visible without making it required.
        channel = f"trade/{order_type.strip().lower()}"
    return Trade(venue=KRAKEN, product=str(item.get("symbol") or ""),
                 price=price, size=size, timestamp=stamp, taker_side=taker_side,
                 trade_id=trade_id or _synthetic_id("trade", stamp, price, size),
                 reported_time=item.get("timestamp"), channel=channel)


def _filter_product(trades: Iterable[Trade], product: Optional[str]) -> list:
    if product is None:
        return list(trades)
    wanted = canonical_product(product)
    return [t for t in trades if canonical_product(t.product) == wanted]


# ---------------------------------------------------------------- spot

#: Default age budget for a spot quote, matching ``ta_copilot.VENUE_MAX_AGE``.
DEFAULT_SPOT_MAX_AGE = 10.0

#: A stamp this far in the future is a clock skew, not a fresh quote.
FUTURE_TIMESTAMP_TOLERANCE = 2.0

#: Coinbase Exchange publishes the same book fields under two spellings: the
#: WebSocket ``ticker`` channel uses ``best_bid``/``best_ask``, and the public
#: REST ``/products/{id}/ticker`` route uses ``bid``/``ask``.
_COINBASE_BID_KEYS = ("best_bid", "bid")
_COINBASE_ASK_KEYS = ("best_ask", "ask")


def _optional_num(message: dict, keys: tuple) -> Optional[float]:
    """Read the first present spelling of an optional numeric field.

    A field that is absent, ``null`` or blank is *unavailable* (``None``) --
    not zero. A field that is present and non-numeric or non-finite raises,
    so a poisoned upstream value can never reach state as a usable number.
    """
    key = next((name for name in keys if name in message), None)
    if key is None:
        return None
    value = message[key]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _num(value, key)


@dataclass(frozen=True)
class SpotQuote:
    """One validated public spot quote, timed by the venue's own stamp.

    ``timestamp`` is the exchange's trade time (the Coinbase ticker ``time``
    field), never the local receipt time, so a delayed frame cannot be
    presented as fresh spot. ``age``/:meth:`is_fresh` therefore measure the
    real age of the quoted trade; ``received_at`` is kept only for diagnostics.
    """

    venue: str
    product: str
    price: float
    timestamp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    reported_time: Any = None
    received_at: Optional[float] = None

    @property
    def age_basis(self) -> str:
        """What ``timestamp`` refers to: the venue's own ticker stamp."""
        return "ticker"

    def age(self, now: Optional[float] = None) -> float:
        """Seconds since the quoted trade; negative means a future stamp."""
        return (time.time() if now is None else float(now)) - self.timestamp

    def is_fresh(self, now: Optional[float] = None, *,
                 max_age: float = DEFAULT_SPOT_MAX_AGE,
                 future_tolerance: float = FUTURE_TIMESTAMP_TOLERANCE) -> bool:
        """True while the exchange trade time is inside the age budget.

        A stamp further in the future than ``future_tolerance`` is treated as
        stale too, so a bad venue clock cannot pin spot permanently fresh.
        """
        age = self.age(now)
        return -future_tolerance <= age <= max_age

    def as_dict(self) -> dict:
        return {
            "venue": self.venue,
            "product": self.product,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "time": int(self.timestamp),
            "age_basis": self.age_basis,
        }


def parse_coinbase_spot(payload: Any, product: Optional[str] = None,
                        received_at: Optional[float] = None) -> SpotQuote:
    """Parse a Coinbase Exchange spot quote (WebSocket ``ticker`` or REST).

    Accepts both documented shapes and validates every numeric field:
    ``price``, the bid and ask (``best_bid``/``best_ask`` or ``bid``/``ask``)
    and ``time`` must be present where required and finite; ``NaN`` or
    ``Infinity`` -- as a float or a string -- raises :class:`MalformedMessage`
    rather than being coerced to a usable number. Bid and ask are optional:
    an absent or blank side is reported as ``None`` (unavailable), never as 0.

    A frame carrying a ``type`` other than ``ticker`` is not a spot quote and
    raises; the REST payload has no ``type`` and is accepted. When ``product``
    is given, a payload naming a different product raises.
    """
    message = _as_object(payload)
    kind = message.get("type")
    if kind is not None:
        if not isinstance(kind, str) or kind.strip().lower() != "ticker":
            raise MalformedMessage(f"not a ticker frame: type={kind!r}")
    if product is not None:
        seen = message.get("product_id")
        if seen is not None and canonical_product(seen) != canonical_product(product):
            raise MalformedMessage(
                f"product: expected {product!r}, got {seen!r}")
    price = _num(message.get("price"), "price")
    bid = _optional_num(message, _COINBASE_BID_KEYS)
    ask = _optional_num(message, _COINBASE_ASK_KEYS)
    stamp = _epoch(message.get("time"), "time")
    return SpotQuote(venue=COINBASE, product=str(message.get("product_id") or ""),
                     price=price, timestamp=stamp, bid=bid, ask=ask,
                     reported_time=message.get("time"), received_at=received_at)


# ---------------------------------------------------------------- flow

class OrderFlow:
    """Current-window order flow built from normalized trades.

    The tracker is anchored to a UTC window grid. Feeding a trade from a later
    window rolls the tracker forward and restarts the cumulative series at the
    new open; feeding a trade from an earlier window is dropped. Calling
    :meth:`snapshot` (or :meth:`advance`) with a wall-clock time rolls an idle
    window forward too, so a quiet feed still resets on time.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS, *,
                 product: Optional[str] = None,
                 rolling_seconds: float = ROLLING_SECONDS,
                 large_trade_usd: Optional[float] = DEFAULT_LARGE_TRADE_USD,
                 large_trade_size: Optional[float] = DEFAULT_LARGE_TRADE_SIZE,
                 max_trades: int = DEFAULT_MAX_TRADES,
                 max_points: int = DEFAULT_MAX_POINTS,
                 max_large_trades: int = DEFAULT_MAX_LARGE_TRADES,
                 max_seen_ids: int = DEFAULT_MAX_SEEN_IDS,
                 seed_points: bool = True):
        if not window_seconds or window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if rolling_seconds <= 0:
            raise ValueError("rolling_seconds must be positive")
        if rolling_seconds > window_seconds:
            raise ValueError("rolling_seconds must not exceed window_seconds")
        for name, value in (("max_trades", max_trades), ("max_points", max_points),
                            ("max_large_trades", max_large_trades),
                            ("max_seen_ids", max_seen_ids)):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if large_trade_usd is None and large_trade_size is None:
            raise ValueError("at least one large-trade threshold is required")
        if large_trade_usd is not None and large_trade_usd <= 0:
            raise ValueError("large_trade_usd must be positive")
        if large_trade_size is not None and large_trade_size <= 0:
            raise ValueError("large_trade_size must be positive")

        self.window_seconds = float(window_seconds)
        self.rolling_seconds = float(rolling_seconds)
        self.large_trade_usd = large_trade_usd
        self.large_trade_size = large_trade_size
        self.max_trades = max_trades
        self.max_points = max_points
        self.max_large_trades = max_large_trades
        self.max_seen_ids = max_seen_ids
        self.seed_points = bool(seed_points)
        self.product = canonical_product(product) if product else None

        self.window_open: Optional[int] = None
        self.window_close: Optional[int] = None

        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.buy_trades = 0
        self.sell_trades = 0

        self.trades = deque(maxlen=max_trades)
        self.cvd_points = deque(maxlen=max_points)
        self.large_trades = deque(maxlen=max_large_trades)
        self._seen: "OrderedDict[tuple, bool]" = OrderedDict()
        self.minute_deltas = {}
        self.coverage_start = None

        self.counters = {"accepted": 0, "duplicate": 0, "outside_window": 0,
                         "filtered_product": 0}

    # ------------------------------------------------------------ window

    def window_for(self, timestamp: float) -> int:
        """Align a timestamp to the window grid (UTC epoch seconds)."""
        return int(timestamp // self.window_seconds) * int(self.window_seconds)

    def _open_window(self, opened: int) -> None:
        """Reset every running total and anchor the series at the new open."""
        self.window_open = int(opened)
        self.window_close = int(opened) + int(self.window_seconds)
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.buy_trades = 0
        self.sell_trades = 0
        self.trades.clear()
        self.large_trades.clear()
        self.cvd_points.clear()
        self.minute_deltas.clear()
        self.coverage_start = None
        if self.seed_points:
            self.cvd_points.append({"time": int(opened), "value": 0.0})

    def advance(self, now: float) -> bool:
        """Roll to the window containing ``now``. Returns True if it changed.

        Never rewinds: a clock that steps backwards leaves the current window
        in place.
        """
        opened = self.window_for(now)
        if self.window_open is None or opened > self.window_open:
            self._open_window(opened)
            return True
        return False

    # ------------------------------------------------------------ ingest

    @property
    def cvd(self) -> float:
        """Cumulative volume delta since the window open: taker buy - sell."""
        return self.buy_volume - self.sell_volume

    def _remember(self, key: tuple) -> None:
        self._seen[key] = True
        while len(self._seen) > self.max_seen_ids:
            self._seen.popitem(last=False)

    def is_large(self, trade: Trade) -> bool:
        if self.large_trade_usd is not None and trade.notional >= self.large_trade_usd:
            return True
        if self.large_trade_size is not None and trade.size >= self.large_trade_size:
            return True
        return False

    def add_trade(self, trade: Trade) -> bool:
        """Record one trade. Returns True if it counted toward the flow.

        Rejected (return False, and counted in ``counters``):

        * duplicate  -- the ``(venue, trade_id)`` key was already seen;
        * outside_window -- the timestamp belongs to an earlier window;
        * filtered_product -- the trade is for a different product.
        """
        if not isinstance(trade, Trade):
            raise TypeError(f"expected Trade, got {type(trade).__name__}")
        if self.product and canonical_product(trade.product) != self.product:
            self.counters["filtered_product"] += 1
            return False

        opened = self.window_for(trade.timestamp)
        if self.window_open is None or opened > self.window_open:
            # A later window means the market rolled on; restart the series.
            self._open_window(opened)
        elif opened < self.window_open:
            self.counters["outside_window"] += 1
            return False

        key = trade.key
        if key in self._seen:
            self.counters["duplicate"] += 1
            return False
        self._remember(key)

        if not (self.window_open <= trade.timestamp < self.window_close):
            self.counters["outside_window"] += 1
            return False

        self.trades.append(trade)
        self.coverage_start = min(self.coverage_start, trade.timestamp) if self.coverage_start else trade.timestamp
        minute = int(trade.timestamp // 60) * 60
        self.minute_deltas[minute] = self.minute_deltas.get(minute, 0.0) + (
            trade.size if trade.taker_side == TAKER_BUY else -trade.size)
        if trade.taker_side == TAKER_BUY:
            self.buy_volume += trade.size
            self.buy_trades += 1
        else:
            self.sell_volume += trade.size
            self.sell_trades += 1
        self.cvd_points.append({"time": int(trade.timestamp), "value": self.cvd})
        if self.is_large(trade):
            self.large_trades.append({
                "time": int(trade.timestamp),
                "timestamp": trade.timestamp,
                "price": trade.price,
                "size": trade.size,
                "notional": trade.notional,
                "taker_side": trade.taker_side,
                "venue": trade.venue,
                "trade_id": trade.trade_id,
            })
        self.counters["accepted"] += 1
        return True

    # Convenience alias.
    add = add_trade

    def add_message(self, venue: Optional[str], payload: Any = None) -> list:
        """Parse one frame and record its trades. Returns the accepted trades.

        Parsing is atomic: if any trade in the frame is malformed the frame
        raises :class:`MalformedMessage` and nothing is recorded.
        """
        trades = parse_message(venue, payload)
        return [trade for trade in trades if self.add_trade(trade)]

    # ------------------------------------------------------------ reads

    def rolling(self, now: float) -> dict:
        """Taker buy/sell volume and imbalance over the trailing window.

        Scans the retained trades (bounded by ``max_trades``), so it stays
        correct even when trades arrive out of timestamp order.

        ``imbalance`` is ``(buy - sell) / (buy + sell)`` in ``[-1, 1]``, and
        ``None`` while the trailing window holds no volume at all.
        """
        cutoff = now - self.rolling_seconds
        previous_cutoff = cutoff - self.rolling_seconds
        buy = sell = 0.0
        previous_buy = previous_sell = 0.0
        buy_count = sell_count = 0
        for trade in self.trades:
            if previous_cutoff <= trade.timestamp < cutoff:
                if trade.taker_side == TAKER_BUY:
                    previous_buy += trade.size
                else:
                    previous_sell += trade.size
            if not (cutoff <= trade.timestamp <= now):
                continue
            if trade.taker_side == TAKER_BUY:
                buy += trade.size
                buy_count += 1
            else:
                sell += trade.size
                sell_count += 1
        total = buy + sell
        previous_total = previous_buy + previous_sell
        return {
            "seconds": self.rolling_seconds,
            "buy_volume": buy,
            "sell_volume": sell,
            "volume": total,
            "buy_trades": buy_count,
            "sell_trades": sell_count,
            "imbalance": (buy - sell) / total if total > 0 else None,
            "previous_imbalance": ((previous_buy - previous_sell) / previous_total
                                   if previous_total > 0 else None),
        }

    def snapshot(self, now: Optional[float] = None, *, advance: bool = True) -> dict:
        """Chart- and dashboard-ready view of the current window.

        Rolls the window forward first (unless ``advance=False``), so a read
        with a fresh wall clock is enough to reset an idle window.
        """
        now = time.time() if now is None else float(now)
        if advance:
            self.advance(now)
        one_minute = self.rolling(now)
        window_volume = self.buy_volume + self.sell_volume
        running = 0.0
        chart_points = ([{"time": self.window_open, "value": 0.0}]
                        if self.window_open is not None else [])
        for minute, delta in sorted(self.minute_deltas.items()):
            running += delta
            chart_points.append({"time": minute + 59, "value": running})
        return {
            "as_of": _iso(now),
            "as_of_epoch": now,
            "window": {
                "seconds": self.window_seconds,
                "open_epoch": self.window_open,
                "close_epoch": self.window_close,
                "open": _iso(self.window_open),
                "close": _iso(self.window_close),
                "seconds_elapsed": (None if self.window_open is None
                                    else max(0.0, now - self.window_open)),
                "seconds_left": (None if self.window_close is None
                                 else max(0.0, self.window_close - now)),
            },
            "cvd": self.cvd,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "volume": window_volume,
            "buy_trades": self.buy_trades,
            "sell_trades": self.sell_trades,
            "trades": self.buy_trades + self.sell_trades,
            "imbalance": ((self.buy_volume - self.sell_volume) / window_volume
                          if window_volume > 0 else None),
            "one_minute": one_minute,
            "cvd_points": chart_points,
            "coverage_start": _iso(self.coverage_start),
            "partial_window": self.coverage_start is None or self.coverage_start > self.window_open + 5,
            "large_trades": [dict(marker) for marker in self.large_trades],
            "last_trade": self.trades[-1].as_dict() if self.trades else None,
            "config": {
                "window_seconds": self.window_seconds,
                "rolling_seconds": self.rolling_seconds,
                "large_trade_usd": self.large_trade_usd,
                "large_trade_size": self.large_trade_size,
                "product": self.product,
                "max_trades": self.max_trades,
                "max_points": self.max_points,
                "max_large_trades": self.max_large_trades,
                "max_seen_ids": self.max_seen_ids,
            },
            "counters": {**self.counters, "buffered_trades": len(self.trades),
                         "seen_ids": len(self._seen), "cvd_points": len(self.cvd_points),
                         "large_trades": len(self.large_trades)},
        }
