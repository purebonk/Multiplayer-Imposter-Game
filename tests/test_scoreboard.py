"""Session scoreboard across games in one room.

"Play Again" existed but nothing carried over, so a group that played six games
had no record of any of them. The server owns this because it is the only party
that knows who was dealt in, who was merely spectating, and which side won.
"""

import uuid

import game
from conftest import make_room, play_hints, settle, start_game, ws
from rooms import SKIP_VOTE, Player, RoomState


async def finish_game(room, crew_wins=True):
    """Play one game to a decided end."""
    await play_hints(room)
    imposter = next(iter(room.imposter_ids))
    crew = [pid for pid in room.remaining_ids() if pid not in room.imposter_ids]
    target = imposter if crew_wins else crew[0]
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, target if pid != target else SKIP_VOTE)
    await settle(0.3)


def board(room):
    return {row["name"]: (row["wins"], row["games"]) for row in room.scoreboard()}


async def test_a_fresh_room_has_no_scoreboard():
    room = make_room(4)
    assert room.scoreboard() == []


async def test_the_winning_side_is_credited(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    imposters = set(room.imposter_ids)
    await finish_game(room, crew_wins=True)

    scores = {pid: room.scores[pid] for pid in room.scores}
    for pid, entry in scores.items():
        assert entry["games"] == 1
        assert entry["wins"] == (0 if pid in imposters else 1)


async def test_imposters_are_credited_when_they_win(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    imposters = set(room.imposter_ids)
    await finish_game(room, crew_wins=False)

    for pid, entry in room.scores.items():
        assert entry["wins"] == (1 if pid in imposters else 0)


async def test_an_ejected_player_still_scores_for_their_team(fast_transitions):
    """Being voted out as crew and watching your side win is still a win --
    scoring only survivors would reward hiding rather than being right."""
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    imposter = next(iter(room.imposter_ids))
    await finish_game(room, crew_wins=True)

    ejected = room.eliminated_ids
    assert ejected, "the imposter should have been ejected"
    for pid in room.participant_ids - {imposter}:
        assert room.scores[pid]["wins"] == 1


async def test_scores_accumulate_across_games(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await finish_game(room, crew_wins=True)

    room.state = RoomState.REVEAL
    room.game_over = True
    await start_game(room)
    await finish_game(room, crew_wins=True)

    for entry in room.scores.values():
        assert entry["games"] == 2, "games must accumulate, not reset"
    assert sum(e["wins"] for e in room.scores.values()) >= 2


async def test_a_spectator_is_not_scored_for_a_game_they_watched(fast_transitions):
    room = make_room(4, last_chance_guess=False)
    await start_game(room)
    room.players["late"] = Player(
        id="late", name="Late", websocket=ws(room).__class__(),
        session_id=str(uuid.uuid4()), avatar_id="goku", spectator=True,
    )
    await finish_game(room, crew_wins=True)

    assert "late" not in room.participant_ids
    assert "late" not in room.scores


async def test_a_promoted_spectator_scores_from_their_first_real_game(fast_transitions):
    room = make_room(4, last_chance_guess=False)
    await start_game(room)
    room.players["late"] = Player(
        id="late", name="Late", websocket=ws(room).__class__(),
        session_id=str(uuid.uuid4()), avatar_id="goku", spectator=True,
    )
    await finish_game(room, crew_wins=True)
    assert "late" not in room.scores

    room.state = RoomState.REVEAL
    room.game_over = True
    await start_game(room)               # promotes them
    await finish_game(room, crew_wins=True)

    assert room.scores["late"]["games"] == 1, "only the game they actually played"


async def test_the_board_is_sorted_best_first(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await finish_game(room, crew_wins=True)

    wins = [row["wins"] for row in room.scoreboard()]
    assert wins == sorted(wins, reverse=True)


async def test_a_departed_player_leaves_the_board(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await finish_game(room, crew_wins=True)
    assert len(room.scoreboard()) == 5

    gone = room.remaining_ids()[0]
    room.players.pop(gone)

    names = {row["id"] for row in room.scoreboard()}
    assert gone not in names
    assert len(room.scoreboard()) == 4


async def test_the_board_carries_current_names_and_avatars(fast_transitions):
    """Rendered exactly like every other player list, so it must carry the
    same fields rather than a bare name string."""
    room = make_room(4, last_chance_guess=False)
    await start_game(room)
    await finish_game(room, crew_wins=True)

    for row in room.scoreboard():
        for field in ("id", "name", "avatar_image", "avatar_emoji", "wins", "games"):
            assert field in row, field


async def test_the_game_over_payload_carries_the_updated_board(fast_transitions):
    """Including the result that just happened -- not last game's standings."""
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await finish_game(room, crew_wins=True)

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert len(reveal["scoreboard"]) == 5
    assert sum(r["games"] for r in reveal["scoreboard"]) == 5
    assert sum(r["wins"] for r in reveal["scoreboard"]) == len(room.participant_ids) - len(room.imposter_ids)


async def test_a_mid_round_reveal_does_not_score(fast_transitions):
    """Only a finished GAME counts, not each elimination round inside it."""
    room = make_room(7, last_chance_guess=False, num_imposters=2)
    await start_game(room)
    await play_hints(room)

    crew = [pid for pid in room.remaining_ids() if pid not in room.imposter_ids]
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, crew[0] if pid != crew[0] else SKIP_VOTE)
    await settle(0.3)

    if not room.game_over:
        assert room.scores == {}, "a round ended, but the game did not"
