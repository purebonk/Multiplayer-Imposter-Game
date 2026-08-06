"""Environment-driven configuration.

Everything that legitimately differs between local development and the
deployed instance lives here with a safe default, so deploying is a matter
of setting env vars rather than editing code. See .env.example.

There are no secrets in this project -- Jikan and AniList are both keyless
and are only called by the offline build scripts plus one optional,
post-game lookup -- so nothing here is credential material. The variables
are deployment knobs.
"""

import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# --- deployment -------------------------------------------------------------

# Render terminates TLS and forwards requests, so the socket peer is the
# proxy, not the player. Only honour X-Forwarded-For when we know we're
# behind that proxy; trusting it locally would let anyone spoof their IP
# and walk straight through every per-IP limit below.
TRUST_PROXY = _bool("TRUST_PROXY", False)

# Same-origin app (FastAPI serves the frontend), so this only needs to list
# the origins a browser might legitimately load the page from.
ALLOWED_ORIGINS = _list(
    "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)

# WebSockets are NOT covered by CORS. Enforced separately on connect; empty
# disables the check (useful for local tooling and raw-socket tests).
ENFORCE_WS_ORIGIN = _bool("ENFORCE_WS_ORIGIN", False)

PORT = _int("PORT", 8000)

# --- capacity ---------------------------------------------------------------
# Sized for Render's 512MB free tier. Rooms are small, but each live
# WebSocket carries real per-connection buffers, so the player ceiling
# matters more than the room ceiling.

MAX_ROOMS = _int("MAX_ROOMS", 300)
MAX_PLAYERS_PER_ROOM = _int("MAX_PLAYERS_PER_ROOM", 20)

# A room created but never joined would otherwise sit in memory forever.
EMPTY_ROOM_TTL_SECONDS = _int("EMPTY_ROOM_TTL_SECONDS", 600)  # 10 min
# A room whose sockets are still open but which has seen no activity.
IDLE_ROOM_TTL_SECONDS = _int("IDLE_ROOM_TTL_SECONDS", 7200)  # 2 h
ROOM_SWEEP_INTERVAL_SECONDS = _int("ROOM_SWEEP_INTERVAL_SECONDS", 120)

# --- rate limits ------------------------------------------------------------
# IMPORTANT: a household, dorm floor, or hotspot full of players shares one
# IP. Every per-IP number here is deliberately sized for "a group of people
# playing together", not "one person". Anything that only needs to stop a
# single abusive client is enforced per-connection instead.

ROOM_CREATE_PER_MINUTE = _int("ROOM_CREATE_PER_MINUTE", 10)
ROOM_CREATE_PER_15MIN = _int("ROOM_CREATE_PER_15MIN", 30)

# Failed joins are the only brute-force surface (guessing room codes).
FAILED_JOIN_PER_MINUTE = _int("FAILED_JOIN_PER_MINUTE", 30)

# General API calls (avatars, reaction options, character details).
API_PER_MINUTE = _int("API_PER_MINUTE", 120)
# The one endpoint that proxies to a third party.
DETAILS_PER_MINUTE = _int("DETAILS_PER_MINUTE", 20)

# Per-CONNECTION, not per-IP: a shared IP is expected, a single socket
# firing hundreds of messages a second is not. Token bucket -- burst well
# above human speed, then throttled to a sustained rate.
WS_BURST = _int("WS_BURST", 40)
WS_REFILL_PER_SECOND = _int("WS_REFILL_PER_SECOND", 12)
# Sustained flooding past the bucket eventually closes the socket.
WS_FLOOD_STRIKES = _int("WS_FLOOD_STRIKES", 60)

MAX_CONNECTIONS_PER_IP = _int("MAX_CONNECTIONS_PER_IP", 50)

# --- input limits -----------------------------------------------------------

MAX_NAME_LENGTH = _int("MAX_NAME_LENGTH", 20)
MAX_HINT_LENGTH = _int("MAX_HINT_LENGTH", 40)
MAX_GUESS_LENGTH = _int("MAX_GUESS_LENGTH", 60)
# Anything larger is rejected before we attempt to parse it as JSON.
MAX_WS_MESSAGE_BYTES = _int("MAX_WS_MESSAGE_BYTES", 4096)
