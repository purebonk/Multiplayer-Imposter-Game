"""Turn order enforcement and mid-round disconnect handling.

The turn-order rule is server-authoritative: the client disabling its input is
a convenience, and every test here talks straight to game.py to prove the
server refuses out-of-turn play on its own.
"""

import config
import game
from conftest import make_room, settle, start_game, ws
from rooms import RoomState


async def test_only_the_current_player_can_submit_a_hint():
    """The original turn-order bug was actually client-side (the input sat
    enabled for everyone between round_started and the first turn_started),
    but the server rule is what makes that a cosmetic bug rather than a real
    one -- so it is worth pinning down."""
    room = make_room(4)
    await start_game(room)

    current = room.current_turn_player_id()
    other = next(pid for pid in room.players if pid != current)

    before = len(ws(room, other).sent)
    await game.submit_hint(room, other, "sneaky")

    assert "It's not your turn." in ws(room, other).errors(before)
    assert other not in room.hints
    assert room.current_turn_player_id() == current, "turn advanced on a rejected hint"


async def test_turn_order_follows_the_broadcast_order():
    room = make_room(4)
    await start_game(room)

    seen = []
    for _ in range(4):
        current = room.current_turn_player_id()
        seen.append(current)
        await game.submit_hint(room, current, "clue")

    assert seen == room.turn_order
    assert len(set(seen)) == 4, "a player got two turns"


async def test_all_hints_submitted_moves_to_voting():
    room = make_room(4)
    await start_game(room)

    for _ in range(4):
        await game.submit_hint(room, room.current_turn_player_id(), "clue")

    assert room.state == RoomState.VOTING
    assert len(room.hints) == 4
    assert ws(room).last("hints_revealed") is not None


async def test_a_hint_cannot_be_submitted_twice():
    room = make_room(4)
    await start_game(room)

    first = room.current_turn_player_id()
    await game.submit_hint(room, first, "one")
    before = len(ws(room, first).sent)
    await game.submit_hint(room, first, "two")

    assert room.hints[first]["hint"] == "one"
    assert "It's not your turn." in ws(room, first).errors(before)


async def test_an_empty_hint_becomes_the_placeholder_and_still_advances():
    """Whitespace-only input must not stall the round waiting for a real one."""
    room = make_room(4)
    await start_game(room)

    first = room.current_turn_player_id()
    await game.submit_hint(room, first, "   ")

    assert room.hints[first]["hint"] == game.NO_HINT_PLACEHOLDER
    assert room.current_turn_player_id() != first


async def test_an_oversized_hint_is_truncated_server_side():
    """The input's maxlength is a UI nicety; a raw socket can send anything,
    and a hint is rebroadcast to every player, so an oversized one is an
    amplification vector."""
    room = make_room(4)
    await start_game(room)

    first = room.current_turn_player_id()
    await game.submit_hint(room, first, "A" * 50_000)

    assert len(room.hints[first]["hint"]) <= config.MAX_HINT_LENGTH
    assert len(ws(room).last("hint_given")["hint"]) <= config.MAX_HINT_LENGTH


async def test_disconnect_on_someone_elses_turn_keeps_the_current_turn():
    room = make_room(5)
    await start_game(room)

    current = room.current_turn_player_id()
    # Someone later in the order leaves.
    leaver = room.turn_order[-1]
    assert leaver != current
    room.players.pop(leaver)
    await game.handle_disconnect(room, leaver)

    assert room.current_turn_player_id() == current
    assert leaver not in room.turn_order


async def test_disconnect_on_your_own_turn_auto_advances():
    """Otherwise the round sits forever waiting on someone who has gone."""
    room = make_room(5)
    await start_game(room)

    current = room.current_turn_player_id()
    room.players.pop(current)
    await game.handle_disconnect(room, current)

    assert room.current_turn_player_id() != current
    assert room.current_turn_player_id() is not None


async def test_disconnect_earlier_in_the_order_does_not_skip_a_player():
    """Removing an already-played player shifts the list under turn_index, so
    the index has to move with it or somebody silently loses their turn."""
    room = make_room(5)
    await start_game(room)

    first = room.turn_order[0]
    await game.submit_hint(room, first, "clue")
    expected = room.current_turn_player_id()

    room.players.pop(first)
    await game.handle_disconnect(room, first)

    assert room.current_turn_player_id() == expected


async def test_no_timer_stall_is_skipped_when_a_player_drops():
    """With the timer at "No limit" nothing would ever advance past a dropped
    player's turn, so the round would hang until their grace window expired."""
    room = make_room(4, timer_seconds=None)
    await start_game(room)

    current = room.current_turn_player_id()
    room.players[current].connected = False
    await game.skip_turn_if_stalled(room, current)

    assert room.hints[current]["hint"] == game.NO_HINT_PLACEHOLDER
    assert room.current_turn_player_id() != current


async def test_skip_turn_if_stalled_is_a_no_op_when_a_timer_exists():
    """The timed path already handles this; double-handling would advance the
    turn twice."""
    room = make_room(4, timer_seconds=30)
    await start_game(room)

    current = room.current_turn_player_id()
    await game.skip_turn_if_stalled(room, current)

    assert current not in room.hints
    assert room.current_turn_player_id() == current


async def test_losing_the_last_crewmate_mid_round_ends_the_game(fast_transitions):
    room = make_room(3, last_chance_guess=False)
    await start_game(room)

    imposter = next(iter(room.imposter_ids))
    crew = [pid for pid in room.players if pid != imposter]

    room.players.pop(crew[0])
    await game.handle_disconnect(room, crew[0])
    await settle(0.1)

    reveal = ws(room, imposter).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "imposters"
    assert reveal["reason"] == "disconnect"
