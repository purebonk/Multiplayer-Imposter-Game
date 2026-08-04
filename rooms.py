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

ROOM_CODE_LENGTH = 4
ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits


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


@dataclass
class Room:
    code: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    players: dict[str, Player] = field(default_factory=dict)
    host_id: Optional[str] = None
    state: RoomState = RoomState.LOBBY

    timer_seconds: Optional[int] = 30
    difficulty: str = "easy"

    imposter_id: Optional[str] = None
    imposter_name: Optional[str] = None
    character_name: Optional[str] = None
    anime_title: Optional[str] = None
    # keyed by player_id -> {"name": str, "hint": str}; the name is captured
    # at submit time so a hint given just before someone disconnects still
    # renders correctly in the reveal, without a room.players lookup.
    hints: dict[str, dict] = field(default_factory=dict)
    votes: dict[str, str] = field(default_factory=dict)

    turn_order: list[str] = field(default_factory=list)
    turn_index: int = 0
    turn_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    def player_summaries(self) -> list[dict]:
        return [{"id": p.id, "name": p.name} for p in self.players.values()]

    def current_turn_player_id(self) -> Optional[str]:
        if 0 <= self.turn_index < len(self.turn_order):
            return self.turn_order[self.turn_index]
        return None

    async def broadcast(self, message: dict) -> None:
        for player in list(self.players.values()):
            await player.websocket.send_json(message)

    async def send_to(self, player_id: str, message: dict) -> None:
        player = self.players.get(player_id)
        if player is not None:
            await player.websocket.send_json(message)

    def reset_round_state(self) -> None:
        if self.turn_task is not None:
            self.turn_task.cancel()
            self.turn_task = None
        self.hints.clear()
        self.votes.clear()
        self.imposter_id = None
        self.imposter_name = None
        self.character_name = None
        self.anime_title = None
        self.turn_order = []
        self.turn_index = 0


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create_room(self) -> Room:
        code = self._generate_unique_code()
        room = Room(code=code)
        self._rooms[code] = room
        return room

    def get_room(self, code: str) -> Optional[Room]:
        return self._rooms.get(code.upper())

    def remove_room(self, code: str) -> None:
        self._rooms.pop(code, None)

    def _generate_unique_code(self) -> str:
        while True:
            code = "".join(random.choices(ROOM_CODE_ALPHABET, k=ROOM_CODE_LENGTH))
            if code not in self._rooms:
                return code


rooms = RoomManager()
