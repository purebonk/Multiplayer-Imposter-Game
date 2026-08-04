import asyncio
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import WebSocket

TIMER_OPTIONS = (15, 30, 60, None)
DIFFICULTY_OPTIONS = ("easy", "hard")
MAX_IMPOSTERS = 3

ROOM_CODE_LENGTH = 4
ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits


def valid_imposter_counts(player_count: int) -> list[int]:
    # Crew must always start a round with a strict majority, or a single
    # elimination round could hand imposters the win by dead heat alone.
    return [n for n in range(1, MAX_IMPOSTERS + 1) if n < player_count / 2]


class RoomState(str, Enum):
    LOBBY = "lobby"
    STARTING = "starting"
    HINTS = "hints"
    VOTING = "voting"
    REVEAL = "reveal"


@dataclass
class Player:
    id: str
    name: str
    websocket: WebSocket
    session_id: str


@dataclass
class Room:
    code: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    players: dict[str, Player] = field(default_factory=dict)
    host_id: Optional[str] = None
    state: RoomState = RoomState.LOBBY

    timer_seconds: Optional[int] = 30
    difficulty: str = "easy"
    give_imposter_hint: bool = True
    num_imposters: int = 1

    character_name: Optional[str] = None
    anime_title: Optional[str] = None
    # Fixed once per GAME at start_game/new_round, not per round -- an
    # elimination-style game keeps the same imposters across every round
    # until someone wins. Names are captured alongside the ids (not looked
    # up later) so the final reveal still shows them correctly even if an
    # imposter disconnects before the game ends.
    imposter_ids: set[str] = field(default_factory=set)
    imposter_names: dict[str, str] = field(default_factory=dict)
    eliminated_ids: set[str] = field(default_factory=set)
    round_number: int = 0

    # keyed by player_id -> {"name": str, "hint": str}; the name is captured
    # at submit time so a hint given just before someone disconnects still
    # renders correctly in the reveal, without a room.players lookup.
    hints: dict[str, dict] = field(default_factory=dict)
    votes: dict[str, str] = field(default_factory=dict)

    turn_order: list[str] = field(default_factory=list)
    turn_index: int = 0
    turn_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    voting_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    def player_summaries(self) -> list[dict]:
        return [{"id": p.id, "name": p.name} for p in self.players.values()]

    def remaining_ids(self) -> list[str]:
        """Currently-connected players who haven't been ejected this game.

        A disconnect is already reflected here for free: main.py deletes a
        departed player from `players` immediately, so this list shrinks
        without needing to separately track "left" vs "ejected"."""
        return [pid for pid in self.players if pid not in self.eliminated_ids]

    def current_turn_player_id(self) -> Optional[str]:
        if 0 <= self.turn_index < len(self.turn_order):
            return self.turn_order[self.turn_index]
        return None

    async def broadcast(self, message: dict) -> None:
        # A send failing for one recipient (their socket died but hasn't been
        # cleaned up yet) must not stop the loop early — otherwise whoever
        # comes after them in iteration order silently never gets this
        # message at all, even though they're still connected.
        for player in list(self.players.values()):
            try:
                await player.websocket.send_json(message)
            except Exception:
                pass

    async def send_to(self, player_id: str, message: dict) -> None:
        player = self.players.get(player_id)
        if player is not None:
            await player.websocket.send_json(message)

    def cancel_timers(self) -> None:
        if self.turn_task is not None:
            self.turn_task.cancel()
            self.turn_task = None
        if self.voting_task is not None:
            self.voting_task.cancel()
            self.voting_task = None

    def reset_round_state(self) -> None:
        """Clears per-round state (hints/votes/turns). Does NOT touch
        character/imposter identity — those are fixed for the whole game."""
        self.cancel_timers()
        self.hints.clear()
        self.votes.clear()
        self.turn_order = []
        self.turn_index = 0

    def reset_game_state(self) -> None:
        """Clears everything tying this room to a specific game, for a fresh
        start_game/new_round: new character, new imposters, round count."""
        self.reset_round_state()
        self.character_name = None
        self.anime_title = None
        self.imposter_ids = set()
        self.imposter_names = {}
        self.eliminated_ids = set()
        self.round_number = 0


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        # One browser tab (one session_id, generated client-side on page
        # load) can only ever occupy a slot in one room across the whole
        # server. This is the actual enforcement for "no duplicate joins" —
        # the client-side guard in app.js is just UX politeness on top of it,
        # since nothing stops a second WebSocket connection from trying.
        self._session_rooms: dict[str, str] = {}

    def create_room(self) -> Room:
        code = self._generate_unique_code()
        room = Room(code=code)
        self._rooms[code] = room
        return room

    def get_room(self, code: str) -> Optional[Room]:
        return self._rooms.get(code.upper())

    def remove_room(self, code: str) -> None:
        self._rooms.pop(code, None)

    def session_room_code(self, session_id: str) -> Optional[str]:
        return self._session_rooms.get(session_id)

    def register_session(self, session_id: str, room_code: str) -> None:
        self._session_rooms[session_id] = room_code

    def release_session(self, session_id: str) -> None:
        self._session_rooms.pop(session_id, None)

    def _generate_unique_code(self) -> str:
        while True:
            code = "".join(random.choices(ROOM_CODE_ALPHABET, k=ROOM_CODE_LENGTH))
            if code not in self._rooms:
                return code


rooms = RoomManager()
