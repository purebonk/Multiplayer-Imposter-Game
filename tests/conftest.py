"""Shared test harness.

The whole suite drives the REAL game logic (game.py / rooms.py) with a fake
WebSocket standing in for a live socket. Nothing here starts a server, opens a
port, or touches the network -- `characters.py` is already a pure in-memory
lookup, and the one live-API feature (post-game "View Character Details") is
deliberately isolated in its own module and is not exercised by gameplay.

That keeps the suite fast and, more importantly, deterministic: every timing
assertion here is about asyncio task scheduling, which real sockets would only
add noise to.
"""

import asyncio
import random
import sys
import uuid
from pathlib import Path

import pytest

# Tests live in tests/; the app is a flat package at the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import WebSocketDisconnect  # noqa: E402

import game  # noqa: E402
from rooms import Player, Room, RoomState  # noqa: E402


class FakeWebSocket:
    """Records what the server sent to one player, and optionally scripts what
    that player sends back.

    `send_json` awaits `asyncio.sleep(0)` on purpose. A real socket send yields
    to the event loop, and several bugs this suite guards against (a timer task
    cancelling itself) only surface at a genuine suspension point. A fake that
    never yields would make those tests pass for the wrong reason.

    `inbox` is a list of raw frames the client "sends". Once it is drained,
    `receive_text` raises WebSocketDisconnect, which is how a real client
    hanging up looks -- so main._run_session can be driven to completion
    against its actual code rather than a reimplementation of it.
    """

    def __init__(self, inbox=None):
        self.sent = []
        self.closed = False
        self.close_code = None
        self._inbox = list(inbox or [])

    async def send_json(self, message):
        await asyncio.sleep(0)
        self.sent.append(message)

    async def receive_text(self):
        await asyncio.sleep(0)
        if self._inbox:
            return self._inbox.pop(0)
        raise WebSocketDisconnect()

    async def close(self, code=1000):
        self.closed = True
        self.close_code = code

    def types(self):
        return [m["type"] for m in self.sent]

    def last(self, message_type):
        """Most recent message of a type, or None."""
        for message in reversed(self.sent):
            if message["type"] == message_type:
                return message
        return None

    def all_of(self, message_type):
        return [m for m in self.sent if m["type"] == message_type]

    def errors(self, since=0):
        return [m["message"] for m in self.sent[since:] if m["type"] == "error"]


def make_room(player_count=4, timer_seconds=None, **overrides):
    """A room with `player_count` connected players, host = p0.

    Defaults to timer_seconds=None ("No limit") so tests advance turns by
    submitting rather than by waiting on real clocks. Tests that specifically
    exercise timeout behaviour pass a short timer explicitly.
    """
    room = Room(code="TEST")
    for i in range(player_count):
        pid = f"p{i}"
        room.players[pid] = Player(
            id=pid,
            name=f"P{i}",
            websocket=FakeWebSocket(),
            session_id=str(uuid.uuid4()),
            avatar_id="goku",
        )
    room.host_id = "p0"
    room.timer_seconds = timer_seconds
    for key, value in overrides.items():
        setattr(room, key, value)
    return room


# A fixed character result, so tests never depend on which character the pool
# happened to roll. Shaped exactly like characters.get_character() output.
#
# Deliberately NOT "Goku": every test player is given the `goku` avatar, whose
# display name appears in player summaries. The imposter-secrecy tests assert
# the character's name appears nowhere in an imposter's message history, and a
# collision there would make them fail for a reason that isn't a leak.
CHARACTER_RESULT = {
    "character": "Edward Elric",
    "anime_title": "Fullmetal Alchemist: Brotherhood",
    "genres": ["Action", "Adventure", "Drama", "Fantasy"],
    "character_role": "Main",
    "difficulty": "easy",
    "decoy": "Roy Mustang",
    # Layer-1 blurbs, shaped exactly as characters.get_character() returns
    # them. Both describe characters from the same show, so the genres match.
    "info": {
        "name": "Edward Elric",
        "summary": "A core cast member of a widely-known series",
        "genres": ["Action", "Adventure", "Drama", "Fantasy"],
        "prominence": "core",
        "reach": "popular",
    },
    "decoy_info": {
        "name": "Roy Mustang",
        "summary": "A core cast member of a widely-known series",
        "genres": ["Action", "Adventure", "Drama", "Fantasy"],
        "prominence": "core",
        "reach": "popular",
    },
}


async def start_game(room, **character_overrides):
    """Begin a game with a known character, bypassing the random pool pick."""
    result = dict(CHARACTER_RESULT, **character_overrides)
    await game._begin_game(room, result)
    return room


async def play_hints(room, hint="clue"):
    """Submit a hint for every player in turn until the hint phase ends."""
    for _ in range(len(room.players) + 2):
        current = room.current_turn_player_id()
        if current is None or room.state != RoomState.HINTS:
            break
        await game.submit_hint(room, current, hint)


def ws(room, player_id="p0"):
    return room.players[player_id].websocket


@pytest.fixture(autouse=True)
def deterministic_random():
    """Seed the RNG per test so a failure is reproducible.

    Imposter assignment and character selection are both random. Tests are
    written not to care *who* the imposter is -- they resolve roles from
    `room.imposter_ids` rather than assuming an index -- but a fixed seed
    means that when something does break, re-running shows the same thing.
    """
    random.seed(20260806)


@pytest.fixture
def fast_transitions(monkeypatch):
    """Shrink the between-round pause and the watchdog deadline.

    These are 4s and 3s in production so players can read the reveal. Left
    alone, the voting tests would spend most of their runtime asleep. Patching
    the module constants exercises exactly the same code paths -- nothing is
    stubbed out, only the durations change.
    """
    monkeypatch.setattr(game, "ROUND_TRANSITION_DELAY", 0.05)
    monkeypatch.setattr(game, "ROUND_ADVANCE_GRACE", 0.05)
    return game


async def settle(seconds=0.3):
    """Let background transition/watchdog tasks run to completion."""
    await asyncio.sleep(seconds)
