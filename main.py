import json
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import character_details
import game
import reactions
from avatar_options import AVATAR_OPTIONS
from rooms import AVATAR_IDS, Player, RoomState, random_avatar_id, rooms

DETAILS_ERROR_MESSAGE = (
    "Couldn't load extra details right now — Jikan (the anime database) might be temporarily unavailable."
)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/character-details")
async def get_character_details(character: str):
    # Deliberately no game/room lookup here at all -- this is a stateless,
    # decorative endpoint. Any failure mode (timeout, rate limit, no match,
    # Jikan itself down) collapses to the same honest message; the frontend
    # doesn't need to distinguish why it failed, just that it did.
    try:
        return await character_details.fetch_character_details(character)
    except Exception:
        raise HTTPException(status_code=502, detail=DETAILS_ERROR_MESSAGE)


@app.get("/api/avatars")
async def list_avatars():
    # The roster lives in avatar_options.py only; the client asks for it
    # rather than keeping a second copy that could drift out of sync.
    return {"avatars": AVATAR_OPTIONS}


@app.get("/api/reaction-options")
async def list_reaction_options():
    # Same reasoning as /api/avatars: reactions.py is the single source of
    # truth for what's allowed, so the picker is built from it instead of a
    # duplicated client list that could accept things the server rejects.
    return {
        "emojis": reactions.REACTION_EMOJIS,
        "phrases": reactions.PRESET_PHRASES,
        "max_free_text": reactions.MAX_FREE_TEXT,
    }


@app.post("/api/rooms")
async def create_room():
    room = rooms.create_room()
    return {"room_code": room.code}


@app.websocket("/ws/{room_code}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_code: str,
    name: str = "Player",
    session_id: str | None = None,
    avatar_id: str | None = None,
):
    room = rooms.get_room(room_code)

    # Accept first: a WebSocket close code set before accept() never reaches
    # the browser (uvicorn rejects the handshake with a plain HTTP 403), so
    # rejections have to happen as a real message over an established socket.
    await websocket.accept()

    if room is None:
        await websocket.send_json({"type": "error", "message": "Room not found."})
        await websocket.close()
        return
    if room.state != RoomState.LOBBY:
        await websocket.send_json({"type": "error", "message": "That room already started a game."})
        await websocket.close()
        return

    # A client that doesn't send session_id (e.g. a raw script, not the
    # bundled frontend) falls back to a fresh uuid per connection, which is
    # equivalent to "not deduplicated" rather than wrongly treating every
    # such connection as the same tab.
    session_id = session_id or str(uuid.uuid4())
    if rooms.session_room_code(session_id) is not None:
        await websocket.send_json(
            {"type": "error", "message": "This tab is already connected to a room."}
        )
        await websocket.close()
        return

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

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except json.JSONDecodeError:
                continue

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
                )
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
    except WebSocketDisconnect:
        pass
    finally:
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
