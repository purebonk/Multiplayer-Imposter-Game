"""The ejected imposter's last-chance guess.

The picker (guess by pool id rather than by typing a name) exists because
players were losing guesses they had conceptually right to spelling and
romanisation. The id is resolved server-side, so the client never gets to
decide what a given id means.
"""

import characters
import game
from conftest import CHARACTER_RESULT, make_room, play_hints, settle, start_game, ws
from rooms import SKIP_VOTE, RoomState

CHARACTER = CHARACTER_RESULT["character"]


async def reach_guess_phase(room):
    """Vote out the imposter, which hands off to the guess phase."""
    await start_game(room)
    await play_hints(room)
    imposter = next(iter(room.imposter_ids))
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, imposter if pid != imposter else SKIP_VOTE)
    assert room.state == RoomState.GUESSING
    return imposter


def pool_id_for(difficulty, name):
    return next(i for i, n in characters.pool_entries(difficulty) if n == name)


async def test_ejecting_the_imposter_opens_the_guess_phase():
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    started = ws(room).last("guess_started")
    assert started["guesser_id"] == imposter
    assert room.guesser_id == imposter


async def test_only_the_guesser_receives_the_pickable_roster():
    """Everyone else has no use for it, and it is a large payload."""
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    options = ws(room, imposter).last("guess_options")
    assert options is not None
    assert len(options["options"]) == len(characters.pool_entries(room.difficulty))

    for pid in room.players:
        if pid != imposter:
            assert ws(room, pid).last("guess_options") is None


async def test_the_right_answer_is_always_in_the_roster(fast_transitions):
    """Otherwise the guess is unwinnable through no fault of the player."""
    for tier in characters.DIFFICULTY_TIERS:
        room = make_room(5, last_chance_guess=True, difficulty=tier)
        result = await characters.get_character(tier)
        await start_game(room, **result)
        await play_hints(room)
        imposter = next(iter(room.imposter_ids))
        for pid in room.remaining_ids():
            await game.submit_vote(room, pid, imposter if pid != imposter else SKIP_VOTE)

        names = {o["name"] for o in ws(room, imposter).last("guess_options")["options"]}
        assert result["character"] in names, f"{result['character']} missing from {tier} roster"


async def test_a_correct_pick_steals_the_win(fast_transitions):
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    await game.submit_guess(room, imposter, "", pool_id_for(room.difficulty, CHARACTER))
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["guess"]["correct"] is True
    assert reveal["guess"]["text"] == CHARACTER
    assert reveal["reason"] == "guess"
    assert reveal["winner"] == "imposters"
    assert reveal["game_over"] is True


async def test_a_wrong_pick_does_not_win(fast_transitions):
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    wrong = next(i for i, n in characters.pool_entries(room.difficulty) if n != CHARACTER)
    await game.submit_guess(room, imposter, "", wrong)
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["guess"]["correct"] is False
    assert reveal.get("winner") != "imposters" or reveal.get("reason") != "guess"


async def test_free_text_still_works_for_non_browser_clients(fast_transitions):
    """The picker is the frontend's path; a raw socket keeps the forgiving
    text matcher rather than being locked out."""
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    await game.submit_guess(room, imposter, CHARACTER)
    await settle(0.1)

    assert ws(room).last("round_reveal")["guess"]["correct"] is True


async def test_the_text_matcher_accepts_a_partial_name():
    """A party game shouldn't hinge on typing "Monkey D. Luffy" over "Luffy"."""
    assert game._guess_matches("luffy", "Monkey D. Luffy") is True
    assert game._guess_matches("Monkey D. Luffy", "Monkey D. Luffy") is True
    assert game._guess_matches("  LUFFY  ", "Monkey D. Luffy") is True
    assert game._guess_matches("zoro", "Monkey D. Luffy") is False
    assert game._guess_matches("", "Monkey D. Luffy") is False
    assert game._guess_matches("d", "Monkey D. Luffy") is False  # too short to count


async def test_only_the_guesser_can_submit_a_guess(fast_transitions):
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)
    other = next(pid for pid in room.remaining_ids() if pid != imposter)

    await game.submit_guess(room, other, "", pool_id_for(room.difficulty, CHARACTER))

    assert room.state == RoomState.GUESSING, "someone else resolved the guess phase"
    assert room.guesser_id == imposter


async def test_a_bogus_pool_id_falls_back_to_text_and_does_not_crash(fast_transitions):
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    await game.submit_guess(room, imposter, "nonsense", 10**9)
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["guess"]["correct"] is False


async def test_the_guess_phase_is_skipped_when_the_host_disables_it(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await play_hints(room)

    imposter = next(iter(room.imposter_ids))
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, imposter if pid != imposter else SKIP_VOTE)
    await settle(0.1)

    assert room.state != RoomState.GUESSING
    assert ws(room).last("guess_started") is None
    assert ws(room).last("round_reveal")["game_over"] is True


async def test_the_guesser_leaving_resolves_it_as_a_miss(fast_transitions):
    """Otherwise everyone stares at a countdown for someone who has gone."""
    room = make_room(5, last_chance_guess=True)
    imposter = await reach_guess_phase(room)

    # Watch from a player who is definitely still here -- the imposter (who
    # just left) is randomly assigned and could have been p0.
    observer = next(pid for pid in room.players if pid != imposter)

    room.players.pop(imposter)
    await game.handle_disconnect(room, imposter)
    await settle(0.1)

    assert room.state != RoomState.GUESSING
    reveal = ws(room, observer).last("round_reveal")
    assert reveal["guess"]["correct"] is False
