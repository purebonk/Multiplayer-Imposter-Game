import asyncio
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import WebSocket

import config
from avatar_options import AVATAR_OPTIONS
from characters import DIFFICULTY_TIERS

TIMER_OPTIONS = (15, 30, 60, None)
# Imported rather than re-listed so the server can never accept a tier the
# character pool has no concept of (or reject one it does).
DIFFICULTY_OPTIONS = DIFFICULTY_TIERS
MAX_IMPOSTERS = 3

# How much the imposter is given to work with, borrowed from the Undercover /
# Mr. White genre:
#   blind   -- no character at all; pure bluffing (the original behaviour)
#   similar -- a DIFFERENT character from the same anime, so their hints sound
#              plausible and the deduction becomes much subtler
IMPOSTER_MODE_OPTIONS = ("blind", "similar")

# Sentinel stored in room.votes for an explicit abstention. Not a player id, so
# it can never collide with one (ids are uuid4) and never lands in the ejection
# tally by accident.
SKIP_VOTE = "__skip__"

# Avatars are referenced by a short id ("goku"), never by URL -- the client
# never gets to tell the server what image to show, it only picks from this
# roster. AVATAR_LOOKUP is what validation checks against.
AVATAR_LOOKUP = {entry["id"]: entry for entry in AVATAR_OPTIONS}
AVATAR_IDS = tuple(AVATAR_LOOKUP)


def random_avatar_id() -> str:
    """Players who never open the picker still get a character rather than a
    blank slot -- assigned per player, not a shared default."""
    return random.choice(AVATAR_IDS)

ROOM_CODE_LENGTH = 4
ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits


def valid_imposter_counts(player_count: int) -> list[int]:
    # Crew must always start a round with a strict majority, or a single
    # elimination round could hand imposters the win by dead heat alone.
    return [n for n in range(1, MAX_IMPOSTERS + 1) if n < player_count / 2]


def player_summary(player: "Player") -> dict:
    """The canonical shape every payload uses to describe a player. Resolving
    the avatar server-side (rather than shipping just the id and making each
    client look it up) keeps the roster in exactly one place."""
    avatar = AVATAR_LOOKUP[player.avatar_id]
    return {
        "id": player.id,
        "name": player.name,
        "avatar_id": player.avatar_id,
        "avatar_name": avatar["name"],
        "avatar_emoji": avatar["emoji"],
        "avatar_image": avatar["image"],
        # Drives the "Reconnecting…" card state. Never includes the token.
        "connected": player.connected,
        # Drives the "watching" tag; also tells a client it may not act yet.
        "spectator": player.spectator,
    }


class RoomState(str, Enum):
    LOBBY = "lobby"
    STARTING = "starting"
    HINTS = "hints"
    VOTING = "voting"
    # A just-ejected imposter naming the character to steal the win. Sits
    # between VOTING and REVEAL; everyone else is spectating a countdown.
    GUESSING = "guessing"
    REVEAL = "reveal"


@dataclass
class Player:
    id: str
    name: str
    websocket: WebSocket
    session_id: str
    avatar_id: str = field(default_factory=random_avatar_id)

    # Reconnection. A dropped player keeps their seat for a grace window, so
    # a refresh doesn't cost them the game. The token is the proof of
    # identity -- server-side only, never guessable, and a client cannot
    # claim someone else's seat without it.
    connected: bool = True
    reconnect_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))

    # Joined while a game was already running. They hold a real seat and watch
    # the round, but are excluded from turn order, voting, role assignment and
    # every win-condition count until the next game promotes them. Cleared in
    # game._begin_game, which is the only place it is safe to do -- see the
    # note there for why promotion cannot happen mid-game.
    spectator: bool = False

    # Reaction rate-limit state. Lives on Player (not a module-level dict)
    # so it's cleaned up automatically when the player disconnects and the
    # Player object is dropped -- no separate bookkeeping to leak.
    last_reaction_at: float = 0.0
    blocked_reaction_attempts: int = 0
    reaction_locked_until: float = 0.0


@dataclass
class Room:
    code: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Monotonic so the sweeper is immune to wall-clock adjustments.
    last_activity: float = field(default_factory=time.monotonic)
    players: dict[str, Player] = field(default_factory=dict)
    host_id: Optional[str] = None
    state: RoomState = RoomState.LOBBY

    timer_seconds: Optional[int] = 30
    difficulty: str = "easy"
    give_imposter_hint: bool = True
    num_imposters: int = 1
    imposter_mode: str = "blind"
    last_chance_guess: bool = True
    # Whether an imposter is told which anime the character is from. Off by
    # default (the original behaviour). Forced on in "similar" mode, where the
    # imposter already holds a character from that same show -- see
    # imposter_sees_anime().
    show_imposter_anime: bool = False
    # Titles the character pool may draw from. Empty means "all anime", which
    # is the default so customising is opt-in and never blocks starting a game.
    selected_anime: list[str] = field(default_factory=list)

    character_name: Optional[str] = None
    anime_title: Optional[str] = None
    # The same-series decoy handed to imposters in "similar" mode. Stored so
    # a reconnecting imposter is given back the identical decoy rather than a
    # different one, which would be an obvious tell.
    decoy_name: Optional[str] = None
    # Layer-1 blurbs, held separately so each client can be sent only the one
    # describing the character IT holds. character_info must never reach an
    # imposter; decoy_info must never reach anyone else.
    character_info: Optional[dict] = None
    decoy_info: Optional[dict] = None
    # Fixed once per GAME at start_game/new_round, not per round -- an
    # elimination-style game keeps the same imposters across every round
    # until someone wins. Full player summaries (name + avatar) are snapshotted
    # at assignment rather than looked up later, so the final reveal can still
    # render an imposter's card even if they disconnected mid-game.
    imposter_ids: set[str] = field(default_factory=set)
    imposter_profiles: dict[str, dict] = field(default_factory=dict)
    eliminated_ids: set[str] = field(default_factory=set)
    round_number: int = 0
    # Fixed once per game, from the player count at start_game/new_round --
    # crew running out of rounds (repeated ties) is a loss condition, so
    # this can't be left uncapped or a stalling imposter could never lose.
    max_rounds: int = 1
    # True only once a game has actually finished. REVEAL is used both between
    # elimination rounds and at game over, so the state alone can't tell those
    # apart -- and settings may only be edited in the second case.
    game_over: bool = False

    # keyed by player_id -> {"name": str, "hint": str}; the name is captured
    # at submit time so a hint given just before someone disconnects still
    # renders correctly in the reveal, without a room.players lookup.
    hints: dict[str, dict] = field(default_factory=dict)
    votes: dict[str, str] = field(default_factory=dict)

    turn_order: list[str] = field(default_factory=list)
    turn_index: int = 0
    turn_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    voting_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    # The post-reveal pause before the next round, plus a watchdog that forces
    # the round forward if that pause never completes. Two separate tasks on
    # purpose: whatever kills the first must not be able to kill the second.
    transition_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    watchdog_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    # Last-chance guess. `pending_reveal` parks the round_reveal payload that
    # was about to be broadcast so the guess outcome can be folded into it
    # rather than sending two conflicting reveals.
    guesser_id: Optional[str] = None
    pending_reveal: Optional[dict] = None
    guess_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    # player_id -> pending "remove them for good" task. Kept separate from
    # the game timers because it has a different lifecycle: cancel_timers()
    # runs between rounds, and a reconnect window must survive that.
    grace_tasks: dict = field(default_factory=dict, repr=False, compare=False)

    def imposter_sees_anime(self) -> bool:
        """Whether imposters are entitled to the anime title this game.

        "similar" mode forces it on: the imposter is handed a real character
        from the same show, so the series is already implied -- withholding the
        name only makes them guess at something they effectively have.
        """
        return self.show_imposter_anime or self.imposter_mode == "similar"

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def cancel_grace(self, player_id: str) -> None:
        task = self.grace_tasks.pop(player_id, None)
        if task is not None:
            task.cancel()

    def cancel_all_grace(self) -> None:
        for task in self.grace_tasks.values():
            task.cancel()
        self.grace_tasks.clear()

    def connected_players(self) -> list:
        return [p for p in self.players.values() if p.connected]

    def player_summaries(self) -> list[dict]:
        return [player_summary(p) for p in self.players.values()]

    def remaining_ids(self) -> list[str]:
        """Players still in contention this game.

        A disconnect is already reflected here for free: main.py deletes a
        departed player from `players` immediately, so this list shrinks
        without needing to separately track "left" vs "ejected".

        Spectators are excluded, and that single exclusion is what keeps a
        mid-game joiner from disturbing anything: turn order, vote tallies,
        the "has everyone voted" check and both win conditions are all derived
        from this list, so none of them can see a spectator at all.
        """
        return [
            pid for pid, player in self.players.items()
            if pid not in self.eliminated_ids and not player.spectator
        ]

    def spectator_ids(self) -> list[str]:
        return [pid for pid, player in self.players.items() if player.spectator]

    def playing_count(self) -> int:
        """Seats that count toward starting a game -- spectators included,
        because _begin_game promotes them before anything is assigned."""
        return len(self.players)

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
            # Players inside their reconnect grace window keep their seat but
            # have a dead socket -- skip rather than raise.
            if not player.connected:
                continue
            try:
                await player.websocket.send_json(message)
            except Exception:
                pass

    async def send_to(self, player_id: str, message: dict) -> None:
        player = self.players.get(player_id)
        if player is None or not player.connected:
            return
        try:
            await player.websocket.send_json(message)
        except Exception:
            pass

    def cancel_task(self, attr: str) -> None:
        """Clear a stored timer task, never cancelling the task we're inside.

        This guard is the whole point. Every one of these timers ends by
        resolving the phase it was timing, and that resolution path clears the
        timers -- so a timer callback would reach in and cancel *itself*.
        `Task.cancel()` on the running task doesn't no-op: it arms the task, and
        CancelledError is raised at its next real suspension point, tearing the
        coroutine down mid-way through work it had already started. That is
        exactly how a resolved vote could broadcast to some players and then
        die before starting the next round. Clearing the attribute still
        happens either way, so the bookkeeping is identical.
        """
        task = getattr(self, attr)
        setattr(self, attr, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def cancel_timers(self) -> None:
        for attr in ("turn_task", "voting_task", "guess_task", "transition_task", "watchdog_task"):
            self.cancel_task(attr)

    def reset_round_state(self) -> None:
        """Clears per-round state (hints/votes/turns). Does NOT touch
        character/imposter identity — those are fixed for the whole game."""
        self.cancel_timers()
        self.hints.clear()
        self.votes.clear()
        self.turn_order = []
        self.turn_index = 0
        self.guesser_id = None
        self.pending_reveal = None

    def reset_game_state(self) -> None:
        """Clears everything tying this room to a specific game, for a fresh
        start_game/new_round: new character, new imposters, round count."""
        self.reset_round_state()
        self.character_name = None
        self.anime_title = None
        self.decoy_name = None
        self.character_info = None
        self.decoy_info = None
        self.imposter_ids = set()
        self.imposter_profiles = {}
        self.eliminated_ids = set()
        self.round_number = 0
        self.game_over = False


class RoomCapacityError(RuntimeError):
    """Raised when the global room ceiling is reached. Surfaced to the caller
    as a clean refusal rather than letting the instance run itself out of
    memory."""


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
        if len(self._rooms) >= config.MAX_ROOMS:
            raise RoomCapacityError(f"room limit reached ({config.MAX_ROOMS})")
        code = self._generate_unique_code()
        room = Room(code=code)
        self._rooms[code] = room
        return room

    def get_room(self, code: str) -> Optional[Room]:
        return self._rooms.get(code.upper())

    def remove_room(self, code: str) -> None:
        room = self._rooms.pop(code, None)
        # Timers hold a reference to the room and would otherwise fire against
        # a room nobody can reach any more. Grace tasks too -- they'd try to
        # finalise a departure from a room that no longer exists.
        if room is not None:
            room.cancel_timers()
            room.cancel_all_grace()

    def room_count(self) -> int:
        return len(self._rooms)

    def sweep_stale(self) -> list[str]:
        """Frees rooms nothing can reach any more. Two cases matter:
        a room created via the API but never joined (otherwise it lives
        forever), and a room whose sockets are technically open but which has
        seen no activity for a long time."""
        now = time.monotonic()
        removed = []
        for code, room in list(self._rooms.items()):
            idle = now - room.last_activity
            empty_expired = not room.players and idle > config.EMPTY_ROOM_TTL_SECONDS
            idle_expired = idle > config.IDLE_ROOM_TTL_SECONDS
            if empty_expired or idle_expired:
                for player in list(room.players.values()):
                    self.release_session(player.session_id)
                self.remove_room(code)
                removed.append(code)
        return removed

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
