import asyncio
import random

from characters import get_character
from rooms import DIFFICULTY_OPTIONS, TIMER_OPTIONS, Room, RoomState, valid_imposter_counts

MIN_PLAYERS = 3
NO_HINT_PLACEHOLDER = "(no hint given)"
ROUND_TRANSITION_DELAY = 4  # seconds to let players read the elimination reveal before the next round starts


async def update_settings(
    room: Room, player_id: str, timer_seconds, difficulty: str, give_imposter_hint, num_imposters
) -> None:
    if player_id != room.host_id:
        await room.send_to(player_id, {"type": "error", "message": "Only the host can change settings."})
        return
    if room.state != RoomState.LOBBY:
        await room.send_to(player_id, {"type": "error", "message": "Can't change settings after starting."})
        return
    if (
        timer_seconds not in TIMER_OPTIONS
        or difficulty not in DIFFICULTY_OPTIONS
        or not isinstance(give_imposter_hint, bool)
        or num_imposters not in valid_imposter_counts(len(room.players))
    ):
        await room.send_to(player_id, {"type": "error", "message": "Invalid settings."})
        return

    room.timer_seconds = timer_seconds
    room.difficulty = difficulty
    room.give_imposter_hint = give_imposter_hint
    room.num_imposters = num_imposters
    await room.broadcast(_settings_payload(room))


def _settings_payload(room: Room) -> dict:
    return {
        "type": "settings_updated",
        "timer_seconds": room.timer_seconds,
        "difficulty": room.difficulty,
        "give_imposter_hint": room.give_imposter_hint,
        "num_imposters": room.num_imposters,
    }


async def clamp_imposter_count(room: Room) -> None:
    """Called whenever the lobby's player count changes. The host may have
    picked a valid count for the room as it was, but a player leaving can
    make that count invalid — this keeps room.num_imposters inside
    valid_imposter_counts() and tells everyone if it moved.

    valid_imposter_counts() always returns a contiguous [1..k] (or []), so
    if the current count isn't in it, it can only be because the count is
    now too high — clamping to the new max is always the right move."""
    if room.state != RoomState.LOBBY:
        return
    valid = valid_imposter_counts(len(room.players))
    if valid and room.num_imposters not in valid:
        room.num_imposters = max(valid)
        await room.broadcast(_settings_payload(room))


async def start_game(room: Room, player_id: str) -> None:
    if player_id != room.host_id:
        await room.send_to(player_id, {"type": "error", "message": "Only the host can start the game."})
        return
    if room.state != RoomState.LOBBY:
        await room.send_to(player_id, {"type": "error", "message": "Game already in progress."})
        return
    if len(room.players) < MIN_PLAYERS:
        await room.send_to(
            player_id, {"type": "error", "message": f"Need at least {MIN_PLAYERS} players to start."}
        )
        return

    # Flip state before the first await so a join or a second start_game
    # arriving while we're mid-fetch sees state != LOBBY and bails out.
    room.state = RoomState.STARTING

    try:
        result = await get_character(room.difficulty)
    except RuntimeError:
        room.state = RoomState.LOBBY
        await room.send_to(player_id, {"type": "error", "message": "Couldn't fetch a character, try again."})
        return

    await _begin_game(room, result)


async def new_round(room: Room, player_id: str) -> None:
    if player_id != room.host_id:
        await room.send_to(player_id, {"type": "error", "message": "Only the host can start a new round."})
        return
    if room.state != RoomState.REVEAL:
        await room.send_to(player_id, {"type": "error", "message": "Can't start a new round right now."})
        return
    if len(room.players) < MIN_PLAYERS:
        await room.send_to(
            player_id, {"type": "error", "message": f"Need at least {MIN_PLAYERS} players to continue."}
        )
        return

    room.state = RoomState.STARTING

    try:
        result = await get_character(room.difficulty)
    except RuntimeError:
        room.state = RoomState.REVEAL
        await room.send_to(player_id, {"type": "error", "message": "Couldn't fetch a character, try again."})
        return

    await _begin_game(room, result)


async def _begin_game(room: Room, character_result: dict) -> None:
    """Sets up a brand new game: character + imposters fixed for its
    entire duration (every elimination round reuses these — only start_game
    or a post-game new_round picks a fresh character/imposters)."""
    room.reset_game_state()
    room.character_name = character_result["character"]
    room.anime_title = character_result["anime_title"]

    # Recompute from room.players (not a snapshot taken before the fetch)
    # so anyone who disconnected during the API call can't become imposter.
    connected_ids = list(room.players.keys())
    num_imposters = min(room.num_imposters, len(connected_ids) - 1)  # defensive floor, shouldn't trigger given validation
    imposter_ids = set(random.sample(connected_ids, k=num_imposters))
    room.imposter_ids = imposter_ids
    room.imposter_names = {pid: room.players[pid].name for pid in imposter_ids}

    for pid in connected_ids:
        is_imposter = pid in imposter_ids
        payload = {
            "type": "game_started",
            "your_role": "imposter" if is_imposter else "crewmate",
            "timer_seconds": room.timer_seconds,
        }
        if is_imposter:
            # Deliberately withhold anime_title here: with only ~20 anime in
            # the pool, naming the show narrows the character down almost as
            # much as naming the character would. Genre + how central the
            # role is gives enough to bluff without being a giveaway.
            payload["character"] = None
            # Imposters know each other -- otherwise multiple imposters can't
            # coordinate their bluffing at all, which defeats the point of
            # having more than one.
            payload["teammates"] = [room.imposter_names[other] for other in imposter_ids if other != pid]
            if room.give_imposter_hint:
                payload["hint"] = {
                    "genres": character_result["genres"],
                    "role_hint": (
                        "a main character"
                        if character_result["character_role"] == "Main"
                        else "a supporting character"
                    ),
                }
        else:
            payload["character"] = room.character_name
            payload["anime_title"] = room.anime_title
        await room.send_to(pid, payload)

    await _begin_next_round(room)


async def _begin_next_round(room: Room) -> None:
    room.hints.clear()
    room.votes.clear()
    room.round_number += 1
    room.turn_order = room.remaining_ids()
    room.turn_index = 0
    room.state = RoomState.HINTS

    remaining = room.remaining_ids()
    await room.broadcast(
        {
            "type": "round_started",
            "round_number": room.round_number,
            "remaining_players": [{"id": pid, "name": room.players[pid].name} for pid in remaining],
            "remaining_count": len(remaining),
        }
    )
    await _start_turn(room)


async def _start_turn(room: Room) -> None:
    if room.turn_task is not None:
        room.turn_task.cancel()
        room.turn_task = None

    current_id = room.current_turn_player_id()
    if current_id is None:
        await _enter_voting(room)
        return

    await room.broadcast(
        {
            "type": "turn_started",
            "player_id": current_id,
            "player_name": room.players[current_id].name,
            "timer_seconds": room.timer_seconds,
            "turn_number": room.turn_index + 1,
            "total_turns": len(room.turn_order),
        }
    )

    if room.timer_seconds is not None:
        room.turn_task = asyncio.create_task(_turn_timeout(room, current_id, room.timer_seconds))


async def _turn_timeout(room: Room, player_id: str, seconds: int) -> None:
    await asyncio.sleep(seconds)
    # Stale-timer guard: if the turn already moved on (submitted early, or
    # the player disconnected and _start_turn ran again), this task's job
    # is already done and it should not double-advance the turn.
    if room.state != RoomState.HINTS or room.current_turn_player_id() != player_id:
        return
    await _record_hint(room, player_id, NO_HINT_PLACEHOLDER)
    room.turn_index += 1
    await _start_turn(room)


async def submit_hint(room: Room, player_id: str, hint: str) -> None:
    if room.state != RoomState.HINTS or player_id not in room.players or player_id in room.eliminated_ids:
        return
    if room.current_turn_player_id() != player_id:
        await room.send_to(player_id, {"type": "error", "message": "It's not your turn."})
        return

    if room.turn_task is not None:
        room.turn_task.cancel()
        room.turn_task = None

    hint = hint.strip() or NO_HINT_PLACEHOLDER
    await _record_hint(room, player_id, hint)
    room.turn_index += 1
    await _start_turn(room)


async def _record_hint(room: Room, player_id: str, hint: str) -> None:
    name = room.players[player_id].name
    room.hints[player_id] = {"name": name, "hint": hint}
    await room.broadcast({"type": "hint_given", "player_id": player_id, "name": name, "hint": hint})


async def _enter_voting(room: Room) -> None:
    room.state = RoomState.VOTING
    # room.hints is a plain dict populated in submission order, so iterating
    # it already yields hints in the order they were given.
    hints_payload = [{"player_id": pid, **data} for pid, data in room.hints.items()]
    await room.broadcast(
        {"type": "hints_revealed", "hints": hints_payload, "timer_seconds": room.timer_seconds}
    )

    # Voting is the one phase governed by a single timer for the whole
    # phase (rather than per-turn) -- whatever's been voted by the deadline
    # gets tallied, same as the hint-turn timeout auto-advancing.
    if room.timer_seconds is not None:
        room.voting_task = asyncio.create_task(_voting_timeout(room, room.timer_seconds))


async def _voting_timeout(room: Room, seconds: int) -> None:
    await asyncio.sleep(seconds)
    if room.state != RoomState.VOTING:
        return
    await _resolve_voting(room)


async def submit_vote(room: Room, player_id: str, target_id: str) -> None:
    remaining = room.remaining_ids()
    if room.state != RoomState.VOTING or player_id not in remaining or target_id not in remaining:
        return
    if player_id == target_id:
        await room.send_to(player_id, {"type": "error", "message": "You can't vote for yourself."})
        return

    room.votes[player_id] = target_id
    await room.broadcast(
        {
            "type": "vote_progress",
            "tally": _tally_votes(room),
            "voted_count": len(room.votes),
            "total": len(remaining),
        }
    )
    if len(room.votes) >= len(remaining):
        await _resolve_voting(room)


async def _resolve_voting(room: Room) -> None:
    if room.state != RoomState.VOTING:
        return
    room.cancel_timers()
    room.state = RoomState.REVEAL  # flipped synchronously before any await, closing the same race window used elsewhere

    tally = _tally_votes(room)
    ejected_id = _determine_ejection(tally)
    tie = ejected_id is None and len(tally) > 0

    payload = {"type": "round_reveal", "reason": "vote", "tally": tally, "tie": tie, "game_over": False}

    game_over = False
    winner = None
    if ejected_id is not None:
        room.eliminated_ids.add(ejected_id)
        payload["ejected_id"] = ejected_id
        payload["ejected_name"] = room.players[ejected_id].name
        payload["was_imposter"] = ejected_id in room.imposter_ids
        game_over, winner = _check_win_condition(room)

    if game_over:
        payload["game_over"] = True
        payload["winner"] = winner
        payload["all_imposters"] = list(room.imposter_names.values())
        payload["character"] = room.character_name
        payload["anime_title"] = room.anime_title
        await room.broadcast(payload)
        return

    await room.broadcast(payload)
    await asyncio.sleep(ROUND_TRANSITION_DELAY)

    # Someone may have disconnected during the pause. handle_disconnect()
    # only win-checks during HINTS/VOTING (state is REVEAL right now), so
    # that departure was never re-evaluated against the win condition —
    # do it here before committing to another round.
    if len(room.players) < 2:
        return  # room is emptying out; main.py's own cleanup handles this
    late_game_over, late_winner = _check_win_condition(room)
    if late_game_over:
        await room.broadcast(
            {
                "type": "round_reveal",
                "reason": "disconnect",
                "tie": False,
                "game_over": True,
                "winner": late_winner,
                "all_imposters": list(room.imposter_names.values()),
                "character": room.character_name,
                "anime_title": room.anime_title,
            }
        )
        return

    await _begin_next_round(room)


def _determine_ejection(tally: dict[str, int]) -> "str | None":
    if not tally:
        return None
    max_votes = max(tally.values())
    top = [pid for pid, count in tally.items() if count == max_votes]
    return top[0] if len(top) == 1 else None


def _check_win_condition(room: Room) -> "tuple[bool, str | None]":
    remaining = set(room.remaining_ids())
    remaining_imposters = room.imposter_ids & remaining
    remaining_crew = remaining - room.imposter_ids
    if not remaining_imposters:
        return True, "crew"
    if len(remaining_imposters) >= len(remaining_crew):
        return True, "imposters"
    return False, None


async def handle_disconnect(room: Room, player_id: str) -> None:
    if room.state == RoomState.LOBBY:
        await clamp_imposter_count(room)
        return
    if not room.players:
        return

    if room.state == RoomState.HINTS:
        await _handle_hint_phase_disconnect(room, player_id)
        await _maybe_end_game_from_disconnect(room)
    elif room.state == RoomState.VOTING:
        room.votes.pop(player_id, None)
        ended = await _maybe_end_game_from_disconnect(room)
        if not ended:
            remaining = room.remaining_ids()
            if remaining and len(room.votes) >= len(remaining):
                await _resolve_voting(room)


async def _handle_hint_phase_disconnect(room: Room, player_id: str) -> None:
    if player_id not in room.turn_order:
        return

    left_index = room.turn_order.index(player_id)
    was_current_turn = left_index == room.turn_index
    room.turn_order.remove(player_id)

    if was_current_turn:
        # The list just shifted left under turn_index, so it already points
        # at whoever's next (or past the end) — auto-skip by restarting.
        if room.turn_task is not None:
            room.turn_task.cancel()
            room.turn_task = None
        await _start_turn(room)
    elif left_index < room.turn_index:
        room.turn_index -= 1
    # else: someone later in line left — nothing to adjust.


async def _maybe_end_game_from_disconnect(room: Room) -> bool:
    """A disconnect (not just a vote) can decide the game -- e.g. the last
    remaining crew member drops mid-round. Checked after every disconnect
    during an active game so the room doesn't sit waiting on input from
    people who can no longer provide it."""
    if room.state not in (RoomState.HINTS, RoomState.VOTING):
        return False
    game_over, winner = _check_win_condition(room)
    if not game_over:
        return False

    room.cancel_timers()
    room.state = RoomState.REVEAL
    await room.broadcast(
        {
            "type": "round_reveal",
            "reason": "disconnect",
            "tie": False,
            "game_over": True,
            "winner": winner,
            "all_imposters": list(room.imposter_names.values()),
            "character": room.character_name,
            "anime_title": room.anime_title,
        }
    )
    return True


def _tally_votes(room: Room) -> dict[str, int]:
    tally: dict[str, int] = {}
    for target_id in room.votes.values():
        tally[target_id] = tally.get(target_id, 0) + 1
    return tally
