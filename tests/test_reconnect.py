"""Reconnect tokens, the grace window, and departure cleanup.

The security-relevant rule: a client cannot name a player_id and be believed.
The token is the only thing that authorises reclaiming a seat.
"""

import secrets

import pytest

import game
import main
from conftest import FakeWebSocket, make_room, start_game, ws
from rooms import Player, RoomState


def find_by_token(room, token):
    """Mirrors the lookup in main.websocket_endpoint."""
    return next(
        (p for p in room.players.values() if secrets.compare_digest(p.reconnect_token, token)),
        None,
    )


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------


def test_every_player_gets_a_distinct_unguessable_token():
    room = make_room(6)
    tokens = [p.reconnect_token for p in room.players.values()]

    assert len(set(tokens)) == len(tokens)
    for token in tokens:
        assert len(token) >= 24


def test_a_valid_token_finds_exactly_its_own_seat():
    room = make_room(4)
    for pid, player in room.players.items():
        assert find_by_token(room, player.reconnect_token).id == pid


def test_a_forged_token_matches_nobody():
    """The seat must not be claimable by guessing or by asserting an id."""
    room = make_room(4)
    for forged in (
        "totally-made-up-token",
        secrets.token_urlsafe(24),
        "p1",
        "",
        room.players["p1"].reconnect_token[:-1],
        room.players["p1"].reconnect_token + "x",
    ):
        assert find_by_token(room, forged) is None, forged


def test_a_token_is_never_broadcast_to_other_players():
    """It is the whole authorisation, so it may only ever go to its owner."""
    room = make_room(4)
    summaries = room.player_summaries()

    for summary in summaries:
        assert "reconnect_token" not in summary
    for player in room.players.values():
        assert player.reconnect_token not in str(summaries)


# --------------------------------------------------------------------------
# The grace window
# --------------------------------------------------------------------------


async def test_a_dropped_player_keeps_their_seat_during_grace(monkeypatch):
    monkeypatch.setattr(main.config, "RECONNECT_GRACE_SECONDS", 30)
    room = make_room(4)
    await start_game(room)

    await main._begin_grace_period(room, "TEST", "p1")

    assert "p1" in room.players, "seat was released immediately"
    assert room.players["p1"].connected is False
    assert ws(room).last("player_status")["connected"] is False
    room.cancel_all_grace()


async def test_grace_expiry_runs_the_normal_departure_cleanup(monkeypatch):
    """The same cleanup as before reconnect existed -- only delayed."""
    monkeypatch.setattr(main.config, "RECONNECT_GRACE_SECONDS", 0.05)
    room = make_room(4)
    await start_game(room)

    await main._begin_grace_period(room, "TEST", "p1")
    import asyncio

    await asyncio.sleep(0.25)

    assert "p1" not in room.players
    assert ws(room).last("player_left")["player"] == "P1"


async def test_reconnecting_cancels_the_pending_removal(monkeypatch):
    monkeypatch.setattr(main.config, "RECONNECT_GRACE_SECONDS", 0.05)
    room = make_room(4)
    await start_game(room)

    await main._begin_grace_period(room, "TEST", "p1")
    room.cancel_grace("p1")
    room.players["p1"].connected = True

    import asyncio

    await asyncio.sleep(0.25)
    assert "p1" in room.players, "a reconnected player was still removed"


async def test_a_disconnected_player_is_skipped_by_broadcast():
    """Their seat persists but their socket is dead."""
    room = make_room(4)
    room.players["p1"].connected = False
    before = len(ws(room, "p1").sent)

    await room.broadcast({"type": "test"})

    assert len(ws(room, "p1").sent) == before
    assert ws(room, "p0").last("test") is not None


async def test_one_dead_socket_does_not_stop_the_broadcast_loop():
    """A send failing for one recipient must not silently deprive everyone
    after them in iteration order."""

    class ExplodingWebSocket(FakeWebSocket):
        async def send_json(self, message):
            raise RuntimeError("socket is gone")

    room = make_room(4)
    room.players["p1"].websocket = ExplodingWebSocket()

    await room.broadcast({"type": "test"})

    assert ws(room, "p3").last("test") is not None


# --------------------------------------------------------------------------
# Explicit leave
# --------------------------------------------------------------------------


async def test_leaving_removes_the_seat_immediately():
    room = make_room(4)
    await start_game(room)

    await main._finalize_departure(room, "TEST", "p1")

    assert "p1" not in room.players
    assert ws(room).last("player_left")["player"] == "P1"
    assert room.grace_tasks.get("p1") is None


async def test_the_host_role_transfers_when_the_host_leaves():
    room = make_room(4)
    await main._finalize_departure(room, "TEST", "p0")

    assert room.host_id != "p0"
    assert room.host_id in room.players
    assert ws(room, "p1").last("player_left")["host_id"] == room.host_id


async def test_an_old_token_cannot_resurrect_a_seat_after_leaving():
    room = make_room(4)
    token = room.players["p1"].reconnect_token

    await main._finalize_departure(room, "TEST", "p1")

    assert find_by_token(room, token) is None


async def test_the_room_is_torn_down_when_the_last_player_leaves():
    from rooms import rooms as room_manager

    room = make_room(1)
    room_manager._rooms["TEST"] = room

    await main._finalize_departure(room, "TEST", "p0")

    assert room_manager.get_room("TEST") is None


# --------------------------------------------------------------------------
# The reconnect snapshot
# --------------------------------------------------------------------------


async def resume(room, player_id):
    """Run the real main._resume_session against a fresh socket.

    The socket's inbox is empty, so the message loop it hands off to exits at
    once via WebSocketDisconnect -- exactly as a client hanging up looks. This
    tests the shipped snapshot code rather than a copy of it.
    """
    fresh = FakeWebSocket()
    await main._resume_session(fresh, room, "TEST", room.players[player_id], "127.0.0.1")
    room.cancel_all_grace()  # the loop's finally block opens a grace window
    return fresh


async def test_reconnecting_restores_the_same_seat_and_role():
    room = make_room(5)
    await start_game(room)
    crewmate = next(pid for pid in room.players if pid not in room.imposter_ids)

    socket = await resume(room, crewmate)

    snapshot = socket.last("reconnected")
    assert snapshot["player_id"] == crewmate, "identity changed on reconnect"
    assert snapshot["your_role"] == "crewmate"
    assert snapshot["room_state"] == RoomState.HINTS.value
    assert len(room.players) == 5, "reconnect created a duplicate seat"


async def test_a_reconnecting_crewmate_gets_the_character_back():
    room = make_room(5)
    await start_game(room)
    crewmate = next(pid for pid in room.players if pid not in room.imposter_ids)

    snapshot = (await resume(room, crewmate)).last("reconnected")

    assert snapshot["character"] == "Edward Elric"
    assert snapshot["anime_title"] == "Fullmetal Alchemist: Brotherhood"


async def test_a_reconnecting_imposter_is_never_told_the_character():
    """Same secrecy rule as game_started. The snapshot is a second place the
    character could leak, and an easy one to forget."""
    room = make_room(5)
    await start_game(room)
    imposter = next(iter(room.imposter_ids))

    socket = await resume(room, imposter)

    snapshot = socket.last("reconnected")
    assert snapshot["your_role"] == "imposter"
    assert snapshot["character"] is None
    assert "anime_title" not in snapshot
    assert "Edward Elric" not in str(socket.sent)


async def test_a_reconnecting_imposter_gets_the_same_decoy_back():
    """A freshly randomised decoy would be an obvious tell to the rest of the
    table, and to the imposter themselves."""
    room = make_room(5, imposter_mode="similar")
    await start_game(room)
    imposter = next(iter(room.imposter_ids))
    assert room.decoy_name == "Roy Mustang"

    snapshot = (await resume(room, imposter)).last("reconnected")

    assert snapshot["character"] == "Roy Mustang"
    assert snapshot["decoy_mode"] is True
    assert "Edward Elric" not in str(snapshot)


async def test_reconnecting_restores_hints_already_given():
    room = make_room(4)
    await start_game(room)
    first = room.current_turn_player_id()
    await game.submit_hint(room, first, "spiky")

    snapshot = (await resume(room, first)).last("reconnected")

    hints = {h["player_id"]: h["hint"] for h in snapshot["hints"]}
    assert hints[first] == "spiky"


async def test_reconnecting_broadcasts_that_the_player_is_back():
    """Asserted on the broadcast rather than the final flag: `resume` uses an
    empty inbox, so the session ends the instant it begins and the finally
    block immediately reopens a grace window. The connected=True status going
    out is the observable that clears the "Reconnecting…" card for everyone."""
    room = make_room(4)
    await start_game(room)
    room.players["p1"].connected = False
    before = len(ws(room, "p0").sent)

    await resume(room, "p1")

    statuses = [
        m for m in ws(room, "p0").sent[before:]
        if m["type"] == "player_status" and m["player_id"] == "p1"
    ]
    assert statuses[0]["connected"] is True
