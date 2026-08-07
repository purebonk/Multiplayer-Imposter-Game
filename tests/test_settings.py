"""Host authorization, settings validation, and the between-games lobby fix."""

import game
from conftest import CHARACTER_RESULT, make_room, start_game, ws
from rooms import RoomState, valid_imposter_counts

CHARACTER = CHARACTER_RESULT["character"]
ANIME = CHARACTER_RESULT["anime_title"]
DECOY = CHARACTER_RESULT["decoy"]

DEFAULTS = dict(
    timer_seconds=30,
    difficulty="easy",
    give_imposter_hint=True,
    num_imposters=1,
    imposter_mode="blind",
    last_chance_guess=True,
    # None means "leave the anime selection alone", matching the wire format:
    # a client that never touches the picker simply omits the field.
    selected_anime=None,
)


async def update(room, player_id, **overrides):
    values = dict(DEFAULTS, **overrides)
    await game.update_settings(
        room,
        player_id,
        values["timer_seconds"],
        values["difficulty"],
        values["give_imposter_hint"],
        values["num_imposters"],
        values["imposter_mode"],
        values["last_chance_guess"],
        values["selected_anime"],
    )


# --------------------------------------------------------------------------
# Host authorization
# --------------------------------------------------------------------------


async def test_only_the_host_can_change_settings():
    room = make_room(4)
    before = len(ws(room, "p1").sent)

    await update(room, "p1", difficulty="hard")

    assert "Only the host can change settings." in ws(room, "p1").errors(before)
    assert room.difficulty == "easy"


async def test_only_the_host_can_start_the_game():
    room = make_room(4)
    before = len(ws(room, "p1").sent)

    await game.start_game(room, "p1")

    assert "Only the host can start the game." in ws(room, "p1").errors(before)
    assert room.state == RoomState.LOBBY


async def test_a_game_needs_the_minimum_player_count():
    room = make_room(2)
    before = len(ws(room).sent)

    await game.start_game(room, "p0")

    assert any("at least" in e for e in ws(room).errors(before))
    assert room.state == RoomState.LOBBY


# --------------------------------------------------------------------------
# Settings validation
# --------------------------------------------------------------------------


async def test_invalid_settings_are_rejected_wholesale():
    room = make_room(4, timer_seconds=30)
    for bad in (
        {"timer_seconds": 9999},
        {"difficulty": "impossible"},
        {"imposter_mode": "telepathy"},
        {"give_imposter_hint": "yes"},
        {"last_chance_guess": 1},
        {"num_imposters": 99},
    ):
        before = len(ws(room).sent)
        await update(room, "p0", **bad)
        assert "Invalid settings." in ws(room).errors(before), bad

    # Nothing partially applied.
    assert room.timer_seconds == 30
    assert room.difficulty == "easy"
    assert room.imposter_mode == "blind"


async def test_all_three_difficulty_tiers_are_accepted():
    room = make_room(4)
    for tier in ("easy", "medium", "hard"):
        await update(room, "p0", difficulty=tier)
        assert room.difficulty == tier


async def test_a_lone_host_can_still_configure_the_room():
    """Validated against the smallest startable game, not the current
    headcount, so a host waiting alone in a fresh room isn't locked out."""
    room = make_room(1)
    assert valid_imposter_counts(1) == []

    await update(room, "p0", num_imposters=1, difficulty="hard")

    assert room.difficulty == "hard"
    assert room.num_imposters == 1


async def test_imposter_count_is_reclamped_at_game_start():
    """Settings may have been chosen when the room was a different size, and
    starting 3 players with 2 imposters is an instant parity win."""
    room = make_room(3, num_imposters=3)
    await start_game(room)

    assert room.num_imposters in valid_imposter_counts(3)
    assert len(room.imposter_ids) == room.num_imposters


async def test_imposter_count_clamps_down_when_the_lobby_shrinks():
    room = make_room(6, num_imposters=2)
    assert 2 in valid_imposter_counts(6)

    for pid in ("p5", "p4"):
        room.players.pop(pid)
    await game.clamp_imposter_count(room)

    assert room.num_imposters in valid_imposter_counts(4)


# --------------------------------------------------------------------------
# Regression: settings were frozen for the room's whole lifetime
# --------------------------------------------------------------------------


async def test_settings_cannot_be_changed_mid_game():
    room = make_room(4)
    await start_game(room)
    before = len(ws(room).sent)

    await update(room, "p0", difficulty="hard", timer_seconds=15)

    assert "Can't change settings mid-game." in ws(room).errors(before)
    assert room.difficulty == "easy"


async def test_settings_cannot_be_changed_between_elimination_rounds(fast_transitions):
    """REVEAL is used both mid-game and at game over, so state alone can't
    tell them apart -- changing the timer between rounds would be unfair."""
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    room.game_over = False
    before = len(ws(room).sent)

    await update(room, "p0", timer_seconds=15)

    assert "Can't change settings mid-game." in ws(room).errors(before)
    assert room.timer_seconds != 15


async def test_settings_can_be_changed_after_a_game_ends():
    """The actual Phase 3.11 bug: REVEAL had no exit except new_round, so once
    a game ended the room could never reach LOBBY again and settings were
    frozen until everybody left and rebuilt the room."""
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    game._finalize_game_over(room, {"type": "round_reveal"}, "crew", timed_out=False)

    before = len(ws(room).sent)
    await update(room, "p0", difficulty="hard", timer_seconds=15, imposter_mode="similar")

    assert ws(room).errors(before) == []
    assert room.difficulty == "hard"
    assert room.timer_seconds == 15
    assert room.imposter_mode == "similar"


async def test_non_host_still_cannot_change_settings_after_a_game():
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    game._finalize_game_over(room, {"type": "round_reveal"}, "crew", timed_out=False)

    await update(room, "p1", difficulty="hard")
    assert room.difficulty == "easy"


# --------------------------------------------------------------------------
# return_to_lobby
# --------------------------------------------------------------------------


async def test_return_to_lobby_resets_the_game_but_keeps_settings():
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    game._finalize_game_over(room, {"type": "round_reveal"}, "crew", timed_out=False)
    await update(room, "p0", difficulty="hard")

    await game.return_to_lobby(room, "p0")

    assert room.state == RoomState.LOBBY
    assert room.game_over is False
    assert room.round_number == 0
    assert room.imposter_ids == set()
    assert room.character_name is None
    assert room.difficulty == "hard", "settings must survive the trip back"
    assert ws(room).last("returned_to_lobby")["difficulty"] == "hard"


async def test_return_to_lobby_is_refused_mid_game():
    room = make_room(4)
    await start_game(room)
    before = len(ws(room).sent)

    await game.return_to_lobby(room, "p0")

    assert "Can't return to the lobby right now." in ws(room).errors(before)
    assert room.state == RoomState.HINTS


async def test_return_to_lobby_is_host_only():
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    room.game_over = True
    before = len(ws(room, "p1").sent)

    await game.return_to_lobby(room, "p1")

    assert "Only the host can do that." in ws(room, "p1").errors(before)
    assert room.state == RoomState.REVEAL


async def test_new_round_is_refused_between_elimination_rounds():
    """A host shouldn't be able to bail out of a losing game by starting a
    fresh one from the mid-game reveal."""
    room = make_room(4)
    await start_game(room)
    room.state = RoomState.REVEAL
    room.game_over = False
    before = len(ws(room).sent)

    await game.new_round(room, "p0")

    assert "Can't start a new round right now." in ws(room).errors(before)


# --------------------------------------------------------------------------
# Imposter secrecy -- the project's central design rule
# --------------------------------------------------------------------------


async def test_the_imposter_is_never_sent_the_real_character():
    room = make_room(5)
    await start_game(room)

    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["your_role"] == "imposter"
        assert started["character"] is None
        assert "anime_title" not in started
        # Nothing anywhere in their message history may contain it.
        assert CHARACTER not in str(ws(room, pid).sent)


async def test_crewmates_do_get_the_character():
    room = make_room(5)
    await start_game(room)

    for pid in set(room.players) - room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["character"] == CHARACTER
        assert started["anime_title"] == ANIME


async def test_similar_mode_sends_the_decoy_never_the_real_character():
    room = make_room(5, imposter_mode="similar")
    await start_game(room)

    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["character"] == DECOY
        assert started["decoy_mode"] is True
        assert CHARACTER not in str(ws(room, pid).sent)


async def test_imposters_are_told_who_their_teammates_are():
    """Otherwise multiple imposters cannot coordinate at all."""
    room = make_room(7, num_imposters=2)
    await start_game(room)

    assert len(room.imposter_ids) == 2
    for pid in room.imposter_ids:
        teammates = ws(room, pid).last("game_started")["teammates"]
        assert len(teammates) == 1
        assert room.players[pid].name not in teammates


async def test_the_genre_hint_is_withheld_when_the_host_turns_it_off():
    room = make_room(5, give_imposter_hint=False)
    await start_game(room)

    for pid in room.imposter_ids:
        assert "hint" not in ws(room, pid).last("game_started")
