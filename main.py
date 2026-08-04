import json
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import game
from rooms import Player, RoomState, rooms

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/rooms")
async def create_room():
    room = rooms.create_room()
    return {"room_code": room.code}


@app.websocket("/ws/{room_code}")
async def websocket_endpoint(
    websocket: WebSocket, room_code: str, name: str = "Player", session_id: str | None = None
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

    player_id = str(uuid.uuid4())
    if not room.players:
        room.host_id = player_id
    room.players[player_id] = Player(id=player_id, name=name, websocket=websocket, session_id=session_id)
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
                )
            elif msg_type == "submit_hint":
                await game.submit_hint(room, player_id, message.get("hint", ""))
            elif msg_type == "submit_vote":
                await game.submit_vote(room, player_id, message.get("target_id", ""))
            elif msg_type == "new_round":
                await game.new_round(room, player_id)
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
