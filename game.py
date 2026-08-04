import asyncio
import random

from characters import fetch_random_character
from rooms import DIFFICULTY_OPTIONS, TIMER_OPTIONS, Room, RoomState

MIN_PLAYERS = 3
NO_HINT_PLACEHOLDER = "(no hint given)"


async def update_settings(room: Room, player_id: str, timer_seconds, difficulty: str) -> None:
    if player_id != room.host_id:
        await room.send_to(player_id, {"type": "error", "message": "Only the host can change settings."})
        return
    if room.state != RoomState.LOBBY:
        await room.send_to(player_id, {"type": "error", "message": "Can't change settings after starting."})
        return
    if timer_seconds not in TIMER_OPTIONS or difficulty not in DIFFICULTY_OPTIONS:
        await room.send_to(player_id, {"type": "error", "message": "Invalid settings."})
        return

    room.timer_seconds = timer_seconds
    room.difficulty = difficulty
    await room.broadcast(
        {"type": "settings_updated", "timer_seconds": room.timer_seconds, "difficulty": room.difficulty}
    )


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
        result = await fetch_random_character(room.difficulty)
    except RuntimeError:
        room.state = RoomState.LOBBY
        await room.send_to(player_id, {"type": "error", "message": "Couldn't fetch a character, try again."})
        return

    await _begin_round(room, result)


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
        result = await fetch_random_character(room.difficulty)
    except RuntimeError:
        room.state = RoomState.REVEAL
        await room.send_to(player_id, {"type": "error", "message": "Couldn't fetch a character, try again."})
        return

    await _begin_round(room, result)


async def _begin_round(room: Room, character_result: dict) -> None:
    room.reset_round_state()
    room.character_name = character_result["character"]
    room.anime_title = character_result["anime_title"]

    # Recompute from room.players (not a snapshot taken before the fetch)
    # so anyone who disconnected during the API call can't become imposter
    # or get stuck in the turn order.
    connected_ids = list(room.players.keys())
    imposter_id = random.choice(connected_ids)
    room.imposter_id = imposter_id
    room.imposter_name = room.players[imposter_id].name
    room.turn_order = connected_ids
    room.turn_index = 0
    room.state = RoomState.HINTS

    imposter_hint = {
        "genres": character_result["genres"],
        "role_hint": "a main character" if character_result["character_role"] == "Main" else "a supporting character",
    }

    for pid in connected_ids:
        is_imposter = pid == imposter_id
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
            payload["hint"] = imposter_hint
        else:
            payload["character"] = room.character_name
            payload["anime_title"] = room.anime_title
        await room.send_to(pid, payload)

    await _start_turn(room)


async def _start_turn(room: Room) -> None:
    if room.turn_task is not None:
        room.turn_task.cancel()
        room.turn_task = None

    current_id = room.current_turn_player_id()
    if current_id is None:
        await _reveal_hints_and_enter_voting(room)
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
    if room.state != RoomState.HINTS or player_id not in room.players:
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


async def _reveal_hints_and_enter_voting(room: Room) -> None:
    room.state = RoomState.VOTING
    # room.hints is a plain dict populated in submission order, so iterating
    # it already yields hints in the order they were given.
    hints_payload = [{"player_id": pid, **data} for pid, data in room.hints.items()]
    await room.broadcast({"type": "hints_revealed", "hints": hints_payload})


async def submit_vote(room: Room, player_id: str, target_id: str) -> None:
    if room.state != RoomState.VOTING or player_id not in room.players or target_id not in room.players:
        return

    room.votes[player_id] = target_id
    await room.broadcast(
        {
            "type": "vote_progress",
            "tally": _tally_votes(room),
            "voted_count": len(room.votes),
            "total": len(room.players),
        }
    )
    await _maybe_reveal_votes(room)


async def handle_disconnect(room: Room, player_id: str) -> None:
    if not room.players:
        return
    if room.state == RoomState.HINTS:
        await _handle_hint_phase_disconnect(room, player_id)
    elif room.state == RoomState.VOTING:
        await _maybe_reveal_votes(room)


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


async def _maybe_reveal_votes(room: Room) -> None:
    if not room.players or len(room.votes) < len(room.players):
        return

    room.state = RoomState.REVEAL
    await room.broadcast(
        {
            "type": "round_reveal",
            "imposter_id": room.imposter_id,
            "imposter_name": room.imposter_name,
            "character": room.character_name,
            "anime_title": room.anime_title,
            "tally": _tally_votes(room),
        }
    )


def _tally_votes(room: Room) -> dict[str, int]:
    tally: dict[str, int] = {}
    for target_id in room.votes.values():
        tally[target_id] = tally.get(target_id, 0) + 1
    return tally
