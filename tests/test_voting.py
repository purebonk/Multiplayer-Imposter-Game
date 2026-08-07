"""Voting resolution, round transitions, and the win conditions that hang off them.

The first two tests here cover the worst bug this project has had: a live game
stranded forever on the reveal screen with no way out short of everyone
leaving. Both are regression tests in the strict sense -- they failed before
the fix and pass after it.
"""

import asyncio

import pytest

import game
from conftest import make_room, play_hints, settle, start_game, ws
from rooms import SKIP_VOTE, RoomState


async def reach_voting(room):
    await start_game(room)
    await play_hints(room)
    assert room.state == RoomState.VOTING


# --------------------------------------------------------------------------
# Regression: the tie-vote freeze
# --------------------------------------------------------------------------


async def test_voting_resolved_by_timeout_advances_to_next_round(fast_transitions):
    """A vote that resolves on the CLOCK, not by everyone voting, must still
    start the next round.

    This is the tie-freeze regression. `_voting_timeout` runs as a task; it
    called `_resolve_voting`, which cleared the room's timers -- cancelling the
    very task it was running inside. `Task.cancel()` on the running task is not
    a no-op: it arms the task and raises CancelledError at the next suspension
    point, which was the sleep between rounds. The next round never started,
    and the reveal broadcast itself was torn up mid-loop, so some players saw
    the tie and others saw nothing.

    Only the timeout path triggers it (resolving via the last vote cancels a
    task that is NOT the running one), which is why it looked intermittent.
    """
    room = make_room(4, timer_seconds=0.1)
    await reach_voting(room)

    ids = room.remaining_ids()
    # Deadlocked 1-1 with two players never voting, so the clock resolves it.
    await game.submit_vote(room, ids[0], ids[1])
    await game.submit_vote(room, ids[1], ids[0])

    await settle(0.6)

    reveal = ws(room).last("round_reveal")
    assert reveal is not None, "the tie reveal never reached the player"
    assert reveal["tie"] is True
    assert room.round_number == 2, "game froze on the reveal instead of advancing"
    # Not asserting the exact phase: the short timer used to force this
    # timeout keeps running into round 2, so the room may already have
    # auto-advanced through the hint turns. Round 2 having *started* is the
    # thing the freeze prevented.
    assert ws(room).last("round_started")["round_number"] == 2


async def test_tie_reveal_reaches_every_player(fast_transitions):
    """The cancellation used to tear up the broadcast loop partway through, so
    players later in iteration order silently never got the reveal at all."""
    room = make_room(5, timer_seconds=0.1)
    await reach_voting(room)

    ids = room.remaining_ids()
    await game.submit_vote(room, ids[0], ids[1])
    await game.submit_vote(room, ids[1], ids[0])
    await settle(0.6)

    for pid in room.players:
        assert ws(room, pid).last("round_reveal") is not None, f"{pid} never got the reveal"


async def test_turn_timer_expiry_still_starts_the_next_turn():
    """Same self-cancel shape, hint phase: `_turn_timeout` reaches `_start_turn`,
    which clears `turn_task` -- the task it is running inside."""
    room = make_room(4, timer_seconds=0.1)
    await start_game(room)
    first = room.current_turn_player_id()

    await settle(0.4)

    assert room.current_turn_player_id() != first, "turn never advanced past the timeout"
    assert ws(room).last("hint_given")["hint"] == game.NO_HINT_PLACEHOLDER


# --------------------------------------------------------------------------
# Regression: the round-advance watchdog
# --------------------------------------------------------------------------


async def test_watchdog_forces_the_round_forward_if_the_transition_dies(fast_transitions):
    """Defence in depth for the bug above: even if the normal transition task
    dies for some unrelated future reason, the room must not be stranded.

    Killing `transition_task` directly is the point -- it simulates any cause,
    not just the one that was fixed.
    """
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    for voter, target in ((0, 1), (1, 0), (2, 3), (3, 2)):
        await game.submit_vote(room, ids[voter], ids[target])

    await asyncio.sleep(0)  # let the transition task get created
    assert room.transition_task is not None
    room.transition_task.cancel()

    await settle(0.5)

    assert room.round_number == 2, "watchdog did not rescue the stranded round"
    assert room.state == RoomState.HINTS


async def test_watchdog_is_a_separate_task_from_the_one_it_guards(fast_transitions):
    """A watchdog killable by the thing it watches is not a watchdog."""
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    for voter, target in ((0, 1), (1, 0), (2, 3), (3, 2)):
        await game.submit_vote(room, ids[voter], ids[target])
    await asyncio.sleep(0)

    assert room.watchdog_task is not None
    assert room.watchdog_task is not room.transition_task


async def test_watchdog_does_not_fire_during_a_normal_round(fast_transitions):
    """It must be a no-op in every healthy game -- no double-advancing."""
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    for voter, target in ((0, 1), (1, 0), (2, 3), (3, 2)):
        await game.submit_vote(room, ids[voter], ids[target])

    await settle(0.6)

    assert room.round_number == 2, "advanced more than one round"
    assert len(ws(room).all_of("round_started")) == 2


# --------------------------------------------------------------------------
# Regression: the round cap / timed_out win condition (Phase 3.2)
# --------------------------------------------------------------------------


async def test_repeated_ties_end_the_game_at_the_round_cap(fast_transitions):
    """Crew running out of rounds is itself a loss, or a stalling imposter
    could survive on ties forever."""
    room = make_room(4)
    await start_game(room)
    assert room.max_rounds == 2  # ceil(4 / 2)

    for _ in range(room.max_rounds + 1):
        if room.state != RoomState.HINTS:
            break
        await play_hints(room)
        ids = room.remaining_ids()
        for voter, target in ((0, 1), (1, 0), (2, 3), (3, 2)):
            await game.submit_vote(room, ids[voter], ids[target])
        await settle()

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "imposters"
    assert reveal["timed_out"] is True


async def test_skipping_every_round_cannot_stall_past_the_cap(fast_transitions):
    """Skip votes are a no-ejection outcome, so they must still burn a round."""
    room = make_room(4)
    await start_game(room)

    for _ in range(room.max_rounds + 1):
        if room.state != RoomState.HINTS:
            break
        await play_hints(room)
        for pid in room.remaining_ids():
            await game.submit_vote(room, pid, SKIP_VOTE)
        await settle()

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["timed_out"] is True


# --------------------------------------------------------------------------
# Skip-vote semantics
# --------------------------------------------------------------------------


async def test_skip_beating_the_top_vote_ejects_nobody(fast_transitions):
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    await game.submit_vote(room, ids[0], ids[1])  # one real vote
    for pid in ids[1:]:
        await game.submit_vote(room, pid, SKIP_VOTE)  # three skips
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["tie"] is True
    assert reveal["no_ejection_reason"] == "skip"
    assert reveal["skips"] == 3
    assert "ejected_name" not in reveal


async def test_skip_tying_the_top_vote_ejects_nobody(fast_transitions):
    """Ties go to "nobody" -- skipping is a real choice, not a lost vote."""
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    await game.submit_vote(room, ids[0], ids[1])
    await game.submit_vote(room, ids[2], ids[1])  # 2 votes
    await game.submit_vote(room, ids[1], SKIP_VOTE)
    await game.submit_vote(room, ids[3], SKIP_VOTE)  # 2 skips
    await settle(0.1)

    assert ws(room).last("round_reveal")["no_ejection_reason"] == "skip"


async def test_real_votes_outnumbering_skips_still_eject(fast_transitions):
    room = make_room(5)
    await reach_voting(room)

    # Target a crewmate on purpose. Ejecting the imposter would hand off to
    # the last-chance guess phase instead of resolving straight to a reveal,
    # and who the imposter is, is random.
    target = next(pid for pid in room.remaining_ids() if pid not in room.imposter_ids)
    voters = [pid for pid in room.remaining_ids() if pid != target]

    for pid in voters[:3]:
        await game.submit_vote(room, pid, target)
    for pid in voters[3:]:
        await game.submit_vote(room, pid, SKIP_VOTE)
    await game.submit_vote(room, target, SKIP_VOTE)
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["ejected_id"] == target
    assert reveal["tie"] is False


async def test_all_skip_round_does_not_render_a_phantom_ejection(fast_transitions):
    """An all-skip round leaves the ejection tally empty. That used to make
    `tie` false with no ejected player either, so the client rendered an
    ejection reveal for somebody who was never ejected."""
    room = make_room(4)
    await reach_voting(room)

    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, SKIP_VOTE)
    await settle(0.1)

    reveal = ws(room).last("round_reveal")
    assert reveal["tally"] == {}
    assert reveal["tie"] is True
    assert reveal["no_ejection_reason"] == "skip"
    assert reveal.get("ejected_id") is None


async def test_skip_is_never_counted_as_a_player_in_the_tally():
    """The sentinel must not leak into the ejection tally as a pseudo-player."""
    room = make_room(4)
    await reach_voting(room)

    ids = room.remaining_ids()
    await game.submit_vote(room, ids[0], SKIP_VOTE)
    await game.submit_vote(room, ids[1], ids[2])

    progress = ws(room).last("vote_progress")
    assert SKIP_VOTE not in progress["tally"]
    assert progress["skips"] == 1
    assert progress["voted_count"] == 2


async def test_skipping_counts_toward_everyone_having_voted(fast_transitions):
    """A skip fills the voter's slot, so the phase closes early instead of
    waiting out the clock on people who already decided."""
    room = make_room(4)
    await reach_voting(room)

    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, SKIP_VOTE)

    # Resolved immediately, without any timer having to fire.
    assert room.state != RoomState.VOTING


# --------------------------------------------------------------------------
# Vote validation
# --------------------------------------------------------------------------


async def test_self_voting_is_rejected():
    room = make_room(4)
    await reach_voting(room)

    voter = room.remaining_ids()[0]
    before = len(ws(room, voter).sent)
    await game.submit_vote(room, voter, voter)

    assert "You can't vote for yourself." in ws(room, voter).errors(before)
    assert voter not in room.votes


async def test_voting_for_a_non_player_is_ignored():
    room = make_room(4)
    await reach_voting(room)

    voter = room.remaining_ids()[0]
    await game.submit_vote(room, voter, "not-a-real-player-id")

    assert voter not in room.votes


async def test_an_ejected_player_cannot_vote(fast_transitions):
    room = make_room(5)
    await reach_voting(room)

    ids = room.remaining_ids()
    ejected = ids[1]
    room.eliminated_ids.add(ejected)

    await game.submit_vote(room, ejected, ids[0])
    assert ejected not in room.votes


async def test_votes_outside_the_voting_phase_are_ignored():
    room = make_room(4)
    await start_game(room)
    assert room.state == RoomState.HINTS

    ids = room.remaining_ids()
    await game.submit_vote(room, ids[0], ids[1])
    assert room.votes == {}


# --------------------------------------------------------------------------
# Win conditions
# --------------------------------------------------------------------------


async def test_ejecting_the_only_imposter_wins_for_crew(fast_transitions):
    room = make_room(5, last_chance_guess=False)
    await start_game(room)
    await play_hints(room)

    imposter = next(iter(room.imposter_ids))
    for pid in room.remaining_ids():
        if pid != imposter:
            await game.submit_vote(room, pid, imposter)
        else:
            await game.submit_vote(room, pid, SKIP_VOTE)
    await settle()

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "crew"
    assert reveal["was_imposter"] is True


async def test_imposters_win_on_reaching_parity(fast_transitions):
    """3 players, 1 imposter: ejecting a crewmate leaves 1v1, which is a loss."""
    room = make_room(3, last_chance_guess=False)
    await start_game(room)
    await play_hints(room)

    imposter = next(iter(room.imposter_ids))
    crew = [pid for pid in room.remaining_ids() if pid != imposter]
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, crew[0] if pid != crew[0] else crew[1])
    await settle()

    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "imposters"
    assert reveal["timed_out"] is False
