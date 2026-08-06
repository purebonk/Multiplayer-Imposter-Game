import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import character_details
import config
import game
import limits
import reactions
from avatar_options import AVATAR_OPTIONS
from rooms import AVATAR_IDS, Player, RoomCapacityError, RoomState, random_avatar_id, rooms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("imposter")

DETAILS_ERROR_MESSAGE = (
    "Couldn't load extra details right now — Jikan (the anime database) might be temporarily unavailable."
)
BUSY_MESSAGE = "The server is at capacity right now. Try again in a few minutes."
RATE_MESSAGE = "You're doing that too quickly. Give it a moment and try again."


async def _sweep_loop() -> None:
    """Frees abandoned rooms and prunes limiter bookkeeping. Without this a
    room created via the API but never joined would live for the lifetime of
    the process."""
    while True:
        try:
            await asyncio.sleep(config.ROOM_SWEEP_INTERVAL_SECONDS)
            removed = rooms.sweep_stale()
            limits.sweep_all()
            if removed:
                logger.info(
                    "rooms_swept count=%d active_rooms=%d connections=%d",
                    len(removed), rooms.room_count(), limits.connection_count(),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            logger.exception("room_sweep_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_sweep_loop())
    logger.info(
        "startup trust_proxy=%s max_rooms=%d allowed_origins=%s",
        config.TRUST_PROXY, config.MAX_ROOMS, config.ALLOWED_ORIGINS,
    )
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan)

# The frontend is served from this same origin, so this only needs to cover
# the origins a browser legitimately loads the page from. Note CORS does NOT
# apply to WebSockets -- that's enforced separately on connect.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _require(window: limits.SlidingWindow, request: Request) -> None:
    ip = limits.client_ip(request)
    if not window.check(ip):
        raise HTTPException(
            status_code=429,
            detail=RATE_MESSAGE,
            headers={"Retry-After": str(window.retry_after(ip))},
        )


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/character-details")
async def get_character_details(character: str, request: Request):
    # Proxies to a third party, so it gets its own tighter budget on top of
    # the general API limit -- this is the one route where a client can make
    # us do outbound network work.
    _require(limits.api_calls, request)
    _require(limits.details_calls, request)

    character = limits.clean_text(character, config.MAX_GUESS_LENGTH)
    if not character:
        raise HTTPException(status_code=400, detail="Missing character name.")

    # Deliberately no game/room lookup here at all -- this is a stateless,
    # decorative endpoint. Any failure mode (timeout, rate limit, no match,
    # Jikan itself down) collapses to the same honest message; the frontend
    # doesn't need to distinguish why it failed, just that it did.
    try:
        return await character_details.fetch_character_details(character)
    except Exception:
        raise HTTPException(status_code=502, detail=DETAILS_ERROR_MESSAGE)


@app.get("/api/avatars")
async def list_avatars(request: Request):
    _require(limits.api_calls, request)
    # The roster lives in avatar_options.py only; the client asks for it
    # rather than keeping a second copy that could drift out of sync.
    return {"avatars": AVATAR_OPTIONS}


@app.get("/api/reaction-options")
async def list_reaction_options(request: Request):
    _require(limits.api_calls, request)
    # Same reasoning as /api/avatars: reactions.py is the single source of
    # truth for what's allowed, so the picker is built from it instead of a
    # duplicated client list that could accept things the server rejects.
    return {
        "emojis": reactions.REACTION_EMOJIS,
        "phrases": reactions.PRESET_PHRASES,
        "max_free_text": reactions.MAX_FREE_TEXT,
    }


@app.post("/api/rooms")
async def create_room(request: Request):
    # Two windows: a short one so a burst can't spike the instance, and a
    # longer one so sustained creation can't slowly fill it. Both sized for
    # a whole household sharing one IP, not a single person.
    _require(limits.room_create_minute, request)
    _require(limits.room_create_long, request)

    try:
        room = rooms.create_room()
    except RoomCapacityError:
        logger.warning("room_capacity_reached active_rooms=%d", rooms.room_count())
        raise HTTPException(status_code=503, detail=BUSY_MESSAGE)

    logger.info("room_created active_rooms=%d", rooms.room_count())
    return {"room_code": room.code}


@app.websocket("/ws/{room_code}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_code: str,
    name: str = "Player",
    session_id: str | None = None,
    avatar_id: str | None = None,
):
    ip = limits.client_ip(websocket)

    # Origin check stands in for CORS, which does not apply to WebSockets --
    # without it any website could open a socket to this server on behalf of
    # a visitor. Off by default so local tooling and raw-socket tests work.
    if config.ENFORCE_WS_ORIGIN:
        origin = websocket.headers.get("origin")
        if origin and origin not in config.ALLOWED_ORIGINS:
            logger.warning("ws_origin_rejected")
            await websocket.close(code=1008)
            return

    if not limits.connection_slot_available(ip):
        logger.warning("connection_cap_hit ip_hash=%s", limits._key_hash(ip))
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "message": "Too many connections from your network."}
        )
        await websocket.close()
        return

    room = rooms.get_room(room_code)

    # Accept first: a WebSocket close code set before accept() never reaches
    # the browser (uvicorn rejects the handshake with a plain HTTP 403), so
    # rejections have to happen as a real message over an established socket.
    await websocket.accept()

    if room is None:
        # The only brute-force surface in the app: guessing 4-character room
        # codes. Only FAILED joins are counted, so real players mistyping a
        # code a few times are fine, but someone walking the keyspace is not.
        if not limits.failed_join.check(ip):
            await websocket.send_json(
                {"type": "error", "message": "Too many failed join attempts. Wait a minute and try again."}
            )
            await websocket.close()
            return
        await websocket.send_json({"type": "error", "message": "Room not found."})
        await websocket.close()
        return
    if room.state != RoomState.LOBBY:
        await websocket.send_json({"type": "error", "message": "That room already started a game."})
        await websocket.close()
        return
    if len(room.players) >= config.MAX_PLAYERS_PER_ROOM:
        await websocket.send_json({"type": "error", "message": "That room is full."})
        await websocket.close()
        return

    # A client that doesn't send session_id (e.g. a raw script, not the
    # bundled frontend) falls back to a fresh uuid per connection, which is
    # equivalent to "not deduplicated" rather than wrongly treating every
    # such connection as the same tab.
    session_id = limits.clean_text(session_id, 64) or str(uuid.uuid4())
    if rooms.session_room_code(session_id) is not None:
        await websocket.send_json(
            {"type": "error", "message": "This tab is already connected to a room."}
        )
        await websocket.close()
        return

    # Server-side name handling: the browser's maxlength is not a control.
    # An empty/garbage name falls back rather than being rejected, so a
    # malformed client still gets a usable (if anonymous) seat.
    name = limits.clean_text(name, config.MAX_NAME_LENGTH) or "Player"

    # Unknown/absent avatar ids get a random character rather than an error
    # or a blank slot -- picking one is optional, and the client never gets
    # to supply an image URL, only choose from the server's own roster.
    if avatar_id not in AVATAR_IDS:
        avatar_id = random_avatar_id()

    player_id = str(uuid.uuid4())
    if not room.players:
        room.host_id = player_id
    room.players[player_id] = Player(
        id=player_id, name=name, websocket=websocket, session_id=session_id, avatar_id=avatar_id
    )
    rooms.register_session(session_id, room_code)

    await room.send_to(
        player_id,
        {
            "type": "welcome",
            "player_id": player_id,
            "host_id": room.host_id,
            "players": room.player_summaries(),
            "timer_seconds": room.timer_seconds,
            "difficulty": room.difficulty,
            "give_imposter_hint": room.give_imposter_hint,
            "num_imposters": room.num_imposters,
            "imposter_mode": room.imposter_mode,
            "last_chance_guess": room.last_chance_guess,
        },
    )
    await room.broadcast(
        {
            "type": "player_joined",
            "player": name,
            "players": room.player_summaries(),
            "host_id": room.host_id,
        }
    )
    # Arrivals can make a previously-invalid imposter count valid (or an
    # over-ambitious one still too high), so keep the lobby setting honest.
    await game.clamp_imposter_count(room)

    limits.add_connection(ip)
    bucket = limits.TokenBucket(config.WS_BURST, config.WS_REFILL_PER_SECOND)
    flood_strikes = 0

    try:
        while True:
            raw = await websocket.receive_text()

            # Size-check before parsing: parsing a multi-megabyte frame is
            # itself the attack, so it must never reach json.loads.
            if len(raw) > config.MAX_WS_MESSAGE_BYTES:
                await websocket.send_json(
                    {"type": "error", "message": "That message was too large."}
                )
                continue

            # Per-connection, not per-IP: a shared household IP is expected,
            # a single socket firing hundreds of messages a second is not.
            if not bucket.take():
                flood_strikes += 1
                if flood_strikes > config.WS_FLOOD_STRIKES:
                    logger.warning("ws_flood_disconnect ip_hash=%s", limits._key_hash(ip))
                    await websocket.close(code=1008)
                    break
                await websocket.send_json(
                    {"type": "error", "message": "You're sending messages too quickly."}
                )
                continue

            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                await websocket.send_json({"type": "error", "message": "Malformed message."})
                continue

            # Valid JSON is not necessarily a JSON *object*: `[1,2,3]` parses
            # fine and used to reach .get(), raising AttributeError and
            # killing the connection handler.
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "Malformed message."})
                continue

            room.touch()
            msg_type = message.get("type")
            if msg_type == "start_game":
                await game.start_game(room, player_id)
            elif msg_type == "update_settings":
                await game.update_settings(
                    room,
                    player_id,
                    message.get("timer_seconds"),
                    message.get("difficulty"),
                    message.get("give_imposter_hint"),
                    message.get("num_imposters"),
                    message.get("imposter_mode"),
                    message.get("last_chance_guess"),
                )
            elif msg_type == "submit_guess":
                await game.submit_guess(room, player_id, message.get("guess", ""))
            elif msg_type == "submit_hint":
                await game.submit_hint(room, player_id, message.get("hint", ""))
            elif msg_type == "submit_vote":
                await game.submit_vote(room, player_id, message.get("target_id", ""))
            elif msg_type == "new_round":
                await game.new_round(room, player_id)
            elif msg_type == "send_reaction":
                # Cosmetic only -- reactions.py never touches game state.
                await reactions.send_reaction(
                    room, player_id, message.get("kind", ""), message.get("value", "")
                )
            else:
                # Unknown or missing type: refuse explicitly rather than
                # silently ignoring, so a broken client can tell.
                await websocket.send_json(
                    {"type": "error", "message": "Unknown request."}
                )
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        # A bug in one connection must not take down the room or leave the
        # player half-removed -- log it and fall through to the same cleanup
        # a normal disconnect uses.
        logger.exception("ws_handler_error")
    finally:
        limits.remove_connection(ip)
        # `finally`, not just `except WebSocketDisconnect`, on purpose: a
        # real-world disconnect (refresh, dropped wifi, backgrounded mobile
        # tab) doesn't always surface as that exact exception type — this
        # session alone has seen ClientDisconnected and bare RuntimeError
        # from the same underlying event. Catching only WebSocketDisconnect
        # meant any of those left the player permanently stuck in
        # room.players with nothing ever cleaning it up: a ghost that never
        # resolves, not just a slow one. `finally` runs no matter which
        # exception (if any) ends this coroutine.
        if player_id in room.players:
            del room.players[player_id]
            rooms.release_session(session_id)
            if room.players:
                if room.host_id == player_id:
                    room.host_id = next(iter(room.players))
                await room.broadcast(
                    {
                        "type": "player_left",
                        "player": name,
                        "players": room.player_summaries(),
                        "host_id": room.host_id,
                    }
                )
                await game.handle_disconnect(room, player_id)
            else:
                rooms.remove_room(room_code)
