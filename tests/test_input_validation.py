"""Input sanitisation and the WebSocket message loop's robustness.

These drive main._run_session directly with scripted client frames, so they
exercise the shipped dispatch loop -- size cap, rate limit, JSON parse, type
check -- rather than a reimplementation of it.

The bar throughout: a hostile or broken client must not be able to kill its
own connection handler in a way that leaves a ghost seat, crash the room, or
get oversized content rebroadcast to everybody else.
"""

import json

import config
import main
from conftest import FakeWebSocket, make_room, start_game, ws
from limits import clean_text


async def run_session(room, player_id, frames):
    """Drive the real message loop with a scripted client, then tidy up."""
    socket = FakeWebSocket(inbox=[json.dumps(f) if isinstance(f, dict) else f for f in frames])
    room.players[player_id].websocket = socket
    await main._run_session(socket, room, "TEST", player_id, "127.0.0.1")
    room.cancel_all_grace()
    return socket


# --------------------------------------------------------------------------
# The message loop survives garbage
# --------------------------------------------------------------------------


async def test_malformed_json_is_refused_without_killing_the_connection():
    room = make_room(4)
    socket = await run_session(room, "p0", ["{not json at all", '{"type":', "<<<>>>"])

    assert socket.errors() == ["Malformed message."] * 3
    assert "p0" in room.players, "a bad frame cost the player their seat"


async def test_valid_json_that_is_not_an_object_is_refused():
    """`[1,2,3]` parses fine and used to reach .get(), raising AttributeError
    and killing the handler -- which left a ghost player in the room."""
    room = make_room(4)
    socket = await run_session(room, "p0", ["[1,2,3]", '"a string"', "42", "null", "true"])

    assert socket.errors() == ["Malformed message."] * 5
    assert "p0" in room.players


async def test_an_unknown_message_type_is_refused_explicitly():
    """Refused rather than silently ignored, so a broken client can tell."""
    room = make_room(4)
    socket = await run_session(room, "p0", [{"type": "drop_database"}, {}, {"type": None}])

    assert socket.errors() == ["Unknown request."] * 3
    assert "p0" in room.players


async def test_an_oversized_frame_is_rejected_before_it_is_parsed():
    """Parsing a multi-megabyte frame is itself the attack, so the size check
    has to come before json.loads."""
    room = make_room(4)
    huge = json.dumps({"type": "submit_hint", "hint": "A" * config.MAX_WS_MESSAGE_BYTES})

    socket = await run_session(room, "p0", [huge])

    assert socket.errors() == ["That message was too large."]
    assert room.hints == {}


async def test_a_flood_of_messages_is_throttled_then_disconnected():
    """Per-connection, not per-IP: a household shares an IP, but one socket
    firing hundreds of messages a second does not."""
    room = make_room(4)
    flood = [{"type": "drop_database"}] * (config.WS_BURST + config.WS_FLOOD_STRIKES + 25)

    socket = await run_session(room, "p0", flood)

    assert any("too quickly" in e for e in socket.errors())
    assert socket.closed is True
    assert socket.close_code == 1008


async def test_a_handler_crash_still_cleans_the_player_up():
    """A bug in one connection must not leave a ghost seat nothing resolves."""

    class ExplodingWebSocket(FakeWebSocket):
        async def receive_text(self):
            raise ValueError("something unexpected")

    room = make_room(4)
    socket = ExplodingWebSocket()
    room.players["p1"].websocket = socket

    await main._run_session(socket, room, "TEST", "p1", "127.0.0.1")

    # Not removed outright -- a drop opens the reconnect grace window instead,
    # which is the cleanup path for an unexpected exception too.
    assert room.players["p1"].connected is False
    assert room.grace_tasks.get("p1") is not None
    room.cancel_all_grace()


async def test_leaving_bypasses_the_grace_window_entirely():
    room = make_room(4)
    socket = await run_session(room, "p1", [{"type": "leave_room"}])

    assert "p1" not in room.players
    assert room.grace_tasks.get("p1") is None
    assert socket.closed is True


# --------------------------------------------------------------------------
# clean_text: the shared sanitiser
# --------------------------------------------------------------------------


def test_clean_text_enforces_the_length_cap():
    assert len(clean_text("A" * 10_000, 40)) == 40
    assert clean_text("hello", 40) == "hello"


def test_clean_text_rejects_non_strings():
    for value in (None, 42, [], {}, True):
        assert clean_text(value, 40) == ""


def test_clean_text_flattens_newlines_rather_than_deleting_them():
    """Deleting them would silently weld words together ("ab\\ncd" -> "abcd"),
    which changes what the player actually said."""
    assert clean_text("ab\ncd", 40) == "ab cd"
    assert clean_text("a\t\tb", 40) == "a b"
    assert clean_text("  lots   of   space  ", 40) == "lots of space"


def test_clean_text_strips_invisible_and_control_characters():
    """Zero-width and control characters can be used to fake a duplicate name
    or smuggle layout tricks into a hint."""
    assert clean_text("he​llo", 40) == "hello"
    assert clean_text("bad\x00chars\x07", 40) == "badchars"
    assert clean_text("‮evil", 40) == "evil"


def test_clean_text_normalises_lookalike_unicode():
    """NFKC folds full-width lookalikes, so they cannot be used to impersonate
    another player's name."""
    assert clean_text("ｈｅｌｌｏ", 40) == "hello"


def test_clean_text_leaves_ordinary_text_alone():
    for text in ("Monkey D. Luffy", "sus", "he's behind you!", "ロロノア・ゾロ"):
        assert clean_text(text, 60) == text


# --------------------------------------------------------------------------
# Nothing user-supplied is echoed without going through the cap
# --------------------------------------------------------------------------


async def test_a_hint_is_capped_before_it_is_rebroadcast():
    """A hint goes to every player, so an uncapped one is an amplification
    vector: one oversized frame becomes N oversized frames."""
    room = make_room(4)
    await start_game(room)
    first = room.current_turn_player_id()

    import game

    await game.submit_hint(room, first, "B" * 5_000)

    broadcast = ws(room, "p0").last("hint_given")
    assert len(broadcast["hint"]) <= config.MAX_HINT_LENGTH


async def test_html_in_a_hint_is_passed_through_as_plain_text():
    """The client builds every hint with textContent, never innerHTML, so the
    server deliberately does not mangle punctuation. This pins the contract:
    the markup must survive as literal text, not be silently half-escaped into
    something that looks sanitised but is not."""
    room = make_room(4)
    await start_game(room)
    first = room.current_turn_player_id()

    import game

    await game.submit_hint(room, first, "<img src=x onerror=alert(1)>")

    hint = ws(room, "p0").last("hint_given")["hint"]
    assert hint == "<img src=x onerror=alert(1)>"
    assert len(hint) <= config.MAX_HINT_LENGTH


async def test_a_name_is_capped_and_falls_back_when_empty():
    """The browser's maxlength is not a control; a raw socket can send 1760
    characters, and an empty name should still get a usable seat."""
    assert len(clean_text("N" * 2_000, config.MAX_NAME_LENGTH)) == config.MAX_NAME_LENGTH
    assert (clean_text("   ", config.MAX_NAME_LENGTH) or "Player") == "Player"
    assert (clean_text("​​", config.MAX_NAME_LENGTH) or "Player") == "Player"
