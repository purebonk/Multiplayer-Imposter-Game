"""Mid-game joiners.

Before this, joining a room whose game had already started was refused
outright ("That room already started a game") -- defined behaviour, not a
crash, but it meant a friend who was thirty seconds late sat out the whole
game. They now take a seat as a spectator.

The load-bearing rule is that a spectator is invisible to every count the game
makes. Room.remaining_ids() excludes them, and turn order, vote tallies, the
"has everyone voted" check and both win conditions all derive from that one
list -- so these tests mostly prove that exclusion holds under each phase.
"""

import uuid

import game
import main
from conftest import CHARACTER_RESULT, FakeWebSocket, make_room, play_hints, settle, start_game, ws
from rooms import SKIP_VOTE, Player, RoomState

CHARACTER = CHARACTER_RESULT["character"]


def add_spectator(room, name="Late", player_id="late"):
    """A mid-game joiner, exactly as main.websocket_endpoint creates one."""
    room.players[player_id] = Player(
        id=player_id, name=name, websocket=FakeWebSocket(),
        session_id=str(uuid.uuid4()), avatar_id="goku",
        spectator=room.state != RoomState.LOBBY,
    )
    return player_id


# --------------------------------------------------------------------------
# The exclusion itself
# --------------------------------------------------------------------------


async def test_a_spectator_is_absent_from_the_players_in_play():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)

    assert "late" in room.players
    assert "late" not in room.remaining_ids()
    assert room.remaining_ids() == ["p0", "p1", "p2", "p3"]


async def test_a_spectator_never_becomes_an_imposter():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)

    assert "late" not in room.imposter_ids
    assert "late" not in room.imposter_profiles


async def test_a_spectator_is_flagged_in_the_roster_for_everyone():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)

    summaries = {s["id"]: s for s in room.player_summaries()}
    assert summaries["late"]["spectator"] is True
    assert summaries["p0"]["spectator"] is False


# --------------------------------------------------------------------------
# Joining during each phase
# --------------------------------------------------------------------------


async def test_joining_mid_hints_does_not_disturb_turn_order():
    room = make_room(4)
    await start_game(room)
    order_before = list(room.turn_order)
    current_before = room.current_turn_player_id()

    add_spectator(room)

    assert room.turn_order == order_before
    assert "late" not in room.turn_order
    assert room.current_turn_player_id() == current_before

    # And the round still completes normally, without waiting on them.
    await play_hints(room)
    assert room.state == RoomState.VOTING
    assert set(room.hints) == set(order_before)
    assert "late" not in room.hints


async def test_a_spectator_cannot_submit_a_hint():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)

    await game.submit_hint(room, "late", "sneaky")

    assert "late" not in room.hints
    assert room.current_turn_player_id() != "late"


async def test_joining_mid_voting_does_not_change_the_vote_threshold():
    """The "everyone has voted" check counts remaining_ids, so a spectator
    must not make the room wait for a vote that can never come."""
    room = make_room(4)
    await start_game(room)
    await play_hints(room)
    assert room.state == RoomState.VOTING

    add_spectator(room)

    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, SKIP_VOTE)

    # Resolved on the four real votes alone, without any timer firing.
    assert room.state != RoomState.VOTING


async def test_a_spectator_can_neither_cast_nor_receive_a_vote():
    room = make_room(4)
    await start_game(room)
    await play_hints(room)
    add_spectator(room)
    voter = room.remaining_ids()[0]

    await game.submit_vote(room, "late", voter)      # voting AS a spectator
    await game.submit_vote(room, voter, "late")      # voting FOR a spectator

    assert "late" not in room.votes
    assert room.votes.get(voter) is None


async def test_joining_mid_guess_phase_does_not_break_the_guess(fast_transitions):
    room = make_room(5, last_chance_guess=True)
    await start_game(room)
    await play_hints(room)
    imposter = next(iter(room.imposter_ids))
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, imposter if pid != imposter else SKIP_VOTE)
    assert room.state == RoomState.GUESSING

    add_spectator(room)
    assert room.guesser_id == imposter

    # The spectator cannot answer on the guesser's behalf...
    await game.submit_guess(room, "late", "", 0)
    assert room.state == RoomState.GUESSING

    # ...and the real guesser still can.
    import characters
    entry = next(i for i, n in characters.pool_entries(room.difficulty) if n == CHARACTER)
    await game.submit_guess(room, imposter, "", entry)
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["guess"]["correct"] is True
    assert reveal["winner"] == "imposters"


# --------------------------------------------------------------------------
# Win conditions and the round cap are blind to spectators
# --------------------------------------------------------------------------


async def test_a_spectator_does_not_change_the_round_cap():
    """max_rounds is fixed from the headcount at game start; someone arriving
    later must not make the crew's clock longer or shorter."""
    room = make_room(4)
    await start_game(room)
    cap_before = room.max_rounds

    for i in range(3):
        add_spectator(room, player_id=f"late{i}")

    assert room.max_rounds == cap_before == 2


async def test_spectators_do_not_prop_up_the_crew_in_the_win_check():
    """3 players, 1 imposter: ejecting a crewmate is 1v1 and an imposter win.
    Two spectators in the room must not make it look like crew still lead."""
    room = make_room(3, last_chance_guess=False)
    await start_game(room)
    await play_hints(room)
    add_spectator(room, player_id="lateA")
    add_spectator(room, player_id="lateB")

    imposter = next(iter(room.imposter_ids))
    crew = [pid for pid in room.remaining_ids() if pid != imposter]
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, crew[0] if pid != crew[0] else crew[1])
    await settle(0.3)

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "imposters"


async def test_a_spectator_leaving_mid_game_changes_nothing():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)
    order_before = list(room.turn_order)

    room.players.pop("late")
    await game.handle_disconnect(room, "late")

    assert room.turn_order == order_before
    assert room.state == RoomState.HINTS
    assert ws(room).last("round_reveal") is None


# --------------------------------------------------------------------------
# Promotion
# --------------------------------------------------------------------------


async def test_the_next_game_promotes_every_spectator():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)
    assert room.players["late"].spectator is True

    # A fresh game -- the one seam where promotion is safe.
    room.state = RoomState.REVEAL
    room.game_over = True
    await start_game(room)

    assert room.players["late"].spectator is False
    assert "late" in room.remaining_ids()
    assert "late" in room.turn_order


async def test_a_promoted_spectator_can_be_dealt_the_character():
    """They were deliberately never told it while watching, so the promotion
    has to be what hands it over."""
    room = make_room(4)
    await start_game(room)
    add_spectator(room)
    assert ws(room, "late").last("game_started") is None  # nothing while watching

    room.state = RoomState.REVEAL
    room.game_over = True
    await start_game(room)

    started = ws(room, "late").last("game_started")
    assert started is not None
    if started["your_role"] == "crewmate":
        assert started["character"] == CHARACTER
    else:
        assert started["character"] is None  # imposter secrecy still holds


async def test_the_round_cap_is_recomputed_to_include_promoted_spectators():
    room = make_room(3)
    await start_game(room)
    assert room.max_rounds == 2  # ceil(3/2)
    for i in range(3):
        add_spectator(room, player_id=f"late{i}")

    room.state = RoomState.REVEAL
    room.game_over = True
    await start_game(room)

    assert len(room.remaining_ids()) == 6
    assert room.max_rounds == 3  # ceil(6/2)


async def test_returning_to_the_lobby_also_promotes_spectators():
    room = make_room(4)
    await start_game(room)
    add_spectator(room)
    room.state = RoomState.REVEAL
    room.game_over = True

    await game.return_to_lobby(room, "p0")

    assert room.players["late"].spectator is False
    assert room.state == RoomState.LOBBY


# --------------------------------------------------------------------------
# Secrecy
# --------------------------------------------------------------------------


def test_the_spectator_snapshot_withholds_the_character():
    """A watcher is on neither side. Handing them the answer would let them
    tip off -- or accidentally out -- the imposter to a table that is usually
    in the same room or voice call."""
    room = make_room(4)
    room.state = RoomState.HINTS
    room.character_name = CHARACTER
    room.anime_title = "Fullmetal Alchemist: Brotherhood"
    room.decoy_name = "Roy Mustang"
    room.imposter_ids = {"p1"}

    snapshot = main._spectator_snapshot(room)

    assert CHARACTER not in str(snapshot)
    assert "anime_title" not in snapshot
    assert "character" not in snapshot
    assert "your_role" not in snapshot
    assert "p1" not in str(snapshot.get("imposters", ""))


async def test_a_spectator_who_reconnects_stays_a_spectator():
    """The reconnect path classifies anyone not in imposter_ids as a crewmate
    and hands them the character -- a spectator has to be branched off first."""
    room = make_room(4)
    await start_game(room)
    add_spectator(room)

    fresh = FakeWebSocket()
    await main._resume_session(fresh, room, "TEST", room.players["late"], "127.0.0.1")
    room.cancel_all_grace()

    snapshot = fresh.last("reconnected")
    assert snapshot["spectator"] is True
    assert snapshot.get("your_role") is None
    assert snapshot.get("character") is None
    assert CHARACTER not in str(fresh.sent)
