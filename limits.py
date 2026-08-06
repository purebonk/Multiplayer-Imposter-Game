"""Rate limiting, client-IP resolution, and input sanitisation.

Kept separate from game logic: nothing here knows about rounds or roles, and
game.py never imports it. A limit firing produces a clean, explicit refusal
that the client can display -- never a silent drop and never an exception
escaping into a connection handler.
"""

import logging
import time
import unicodedata
from collections import defaultdict, deque

import config

logger = logging.getLogger("imposter.limits")


# --------------------------------------------------------------------------
# Client IP
# --------------------------------------------------------------------------

def client_ip(scope_or_request) -> str:
    """Resolve the real client address.

    Behind Render the socket peer is the proxy, so every player would
    otherwise collapse into a single IP -- breaking per-IP limits in both
    directions (one player's abuse would lock out everyone, and a real
    group would trip limits instantly). X-Forwarded-For is only consulted
    when TRUST_PROXY is set, because a client can trivially send that
    header themselves; trusting it by default would make every per-IP
    limit bypassable.
    """
    headers = {}
    try:
        headers = {k.decode().lower(): v.decode() for k, v in scope_or_request.headers.raw}
    except AttributeError:
        try:
            headers = {k.lower(): v for k, v in scope_or_request.headers.items()}
        except Exception:  # noqa: BLE001
            headers = {}

    if config.TRUST_PROXY:
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()

    peer = getattr(scope_or_request, "client", None)
    return getattr(peer, "host", None) or "unknown"


# --------------------------------------------------------------------------
# Sliding-window limiter (per key, e.g. per IP)
# --------------------------------------------------------------------------

class SlidingWindow:
    """Counts events per key within a window. Memory is bounded by pruning
    on access plus an explicit sweep, so an attacker cycling keys can't grow
    it without bound."""

    def __init__(self, limit: int, window_seconds: float, name: str):
        self.limit = limit
        self.window = window_seconds
        self.name = name
        self._events: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque:
        events = self._events[key]
        cutoff = now - self.window
        while events and events[0] < cutoff:
            events.popleft()
        return events

    def check(self, key: str) -> bool:
        """True if allowed (and records the event); False if over limit."""
        now = time.monotonic()
        events = self._prune(key, now)
        if len(events) >= self.limit:
            logger.warning(
                "rate_limit_hit limit=%s key_hash=%s count=%d window=%ss",
                self.name, _key_hash(key), len(events), self.window,
            )
            return False
        events.append(now)
        return True

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        events = self._prune(key, now)
        if not events:
            return 0
        return max(1, int(self.window - (now - events[0])) + 1)

    def sweep(self) -> None:
        now = time.monotonic()
        for key in list(self._events):
            if not self._prune(key, now):
                del self._events[key]


def _key_hash(key: str) -> str:
    """IPs are personal data; log a short stable digest instead so abuse is
    still correlatable across log lines without storing addresses."""
    return f"{hash(key) & 0xFFFFFF:06x}"


# --------------------------------------------------------------------------
# Token bucket (per connection)
# --------------------------------------------------------------------------

class TokenBucket:
    """Allows a burst then throttles to a sustained rate. Per-connection, so
    a shared IP full of real players is never penalised for their combined
    traffic -- only a single socket behaving abnormally is."""

    def __init__(self, capacity: int, refill_per_second: float):
        self.capacity = capacity
        self.refill = refill_per_second
        self.tokens = float(capacity)
        self.updated = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


# --------------------------------------------------------------------------
# Shared limiter instances
# --------------------------------------------------------------------------

room_create_minute = SlidingWindow(config.ROOM_CREATE_PER_MINUTE, 60, "room_create_1m")
room_create_long = SlidingWindow(config.ROOM_CREATE_PER_15MIN, 900, "room_create_15m")
failed_join = SlidingWindow(config.FAILED_JOIN_PER_MINUTE, 60, "failed_join")
api_calls = SlidingWindow(config.API_PER_MINUTE, 60, "api")
details_calls = SlidingWindow(config.DETAILS_PER_MINUTE, 60, "character_details")

ALL_WINDOWS = (room_create_minute, room_create_long, failed_join, api_calls, details_calls)

# Live WebSocket connections per resolved IP.
_connections: dict[str, int] = defaultdict(int)


def connection_slot_available(ip: str) -> bool:
    return _connections[ip] < config.MAX_CONNECTIONS_PER_IP


def add_connection(ip: str) -> None:
    _connections[ip] += 1


def remove_connection(ip: str) -> None:
    if _connections.get(ip):
        _connections[ip] -= 1
        if _connections[ip] <= 0:
            del _connections[ip]


def connection_count() -> int:
    return sum(_connections.values())


def sweep_all() -> None:
    for window in ALL_WINDOWS:
        window.sweep()


# --------------------------------------------------------------------------
# Input sanitisation
# --------------------------------------------------------------------------

# Zero-width and bidi-override characters: invisible in most renderers, so
# they let someone register a name that *looks* identical to another
# player's, which matters in a game about identifying people.
_INVISIBLE = {
    "​", "‌", "‍", "⁠", "﻿",
    "‎", "‏", "‪", "‫", "‬", "‭", "‮",
}


def clean_text(raw, max_length: int) -> str:
    """Server-side normalisation for any player-supplied string.

    Returns "" for anything unusable; callers decide whether that's a
    rejection or a fallback. Never raises, whatever it's handed.
    """
    if not isinstance(raw, str):
        return ""
    # NFKC folds look-alike/compatibility forms so visually identical names
    # compare equal rather than sneaking through as "different".
    text = unicodedata.normalize("NFKC", raw)
    cleaned = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        if ch.isspace():
            # Newlines/tabs become a space rather than vanishing: deleting
            # them would silently weld words together ("Aaron\nEvil" ->
            # "AaronEvil"), changing what the player actually typed.
            cleaned.append(" ")
        elif unicodedata.category(ch)[0] == "C":
            continue  # other control/format characters
        else:
            cleaned.append(ch)
    text = "".join(cleaned)
    # Collapse runs of whitespace so padding can't be used to fake width.
    text = " ".join(text.split())
    return text[:max_length].strip()
