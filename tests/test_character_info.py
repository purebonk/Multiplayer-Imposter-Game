"""The in-round character info panel.

Layer 1 is a blurb built entirely from local pool data. Layer 2 is the
optional live Jikan lookup, reusing the endpoint the post-game reveal already
uses.

Most of this file is about who may see layer 1 at all. The server decides:
`character_info` is present in a payload or it is not, and the client has no
way to conjure one. That is the same guarantee as the original "the imposter
never receives the character field", and it is tested with the same rigour --
at the protocol level, not by checking what the UI happens to render.
"""

import characters
import game
import main
from conftest import CHARACTER_RESULT, FakeWebSocket, make_room, start_game, ws
from character_pool_data import ANIME_POOL

CHARACTER = CHARACTER_RESULT["character"]
DECOY = CHARACTER_RESULT["decoy"]
ANIME = CHARACTER_RESULT["anime_title"]


# --------------------------------------------------------------------------
# Layer 1 is local-only
# --------------------------------------------------------------------------


def test_layer_one_needs_no_network_and_cannot_fail():
    """characters.py has no HTTP client at all -- the blurb is a pure
    function of data already in memory."""
    import inspect

    # Import lines only -- prose in a comment mentioning "socket" is not a
    # dependency, and matching on it would make this test noise.
    imports = [
        line for line in inspect.getsource(characters).splitlines()
        if line.startswith(("import ", "from "))
    ]
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket", "asyncio"):
        assert not any(forbidden in line for line in imports), (
            f"characters.py must stay offline ({forbidden})"
        )


def test_every_character_in_the_pool_produces_a_blurb():
    """No character can leave a player with an empty panel."""
    for anime in ANIME_POOL:
        for character in anime["characters"]:
            info = characters.describe_character(anime, character)
            assert info["name"] == character["name"]
            assert info["synopsis"].strip(), f"{anime['title']} has no synopsis"
            assert info["role"].strip()
            assert info["genres"]
            assert info["prominence"] in ("core", "notable", "deep")
            assert info["reach"] in ("mega", "popular", "cult")


def test_the_blurb_never_names_the_series():
    """A decoy always comes from the same show as the real character, so
    naming the series in the blurb would hand a decoy imposter the one fact
    the blind/decoy split exists to withhold."""
    for anime in ANIME_POOL:
        for character in anime["characters"]:
            info = characters.describe_character(anime, character)
            assert anime["title"] not in info["synopsis"]
            assert anime["title"] not in info["role"]
            assert anime["title"] not in str(info["genres"])


def test_the_blurb_reflects_prominence_and_reach():
    naruto = next(a for a in ANIME_POOL if a["title"] == "Naruto")
    core = next(c for c in naruto["characters"] if c["prominence"] == "core")
    deep = next(c for c in naruto["characters"] if c["prominence"] == "deep")

    assert "main characters" in characters.describe_character(naruto, core)["role"]
    assert "minor character" in characters.describe_character(naruto, deep)["role"]
    # The premise is the headline and is identical for everyone in the show.
    assert "ninja" in characters.describe_character(naruto, core)["synopsis"]
    assert (
        characters.describe_character(naruto, core)["synopsis"]
        == characters.describe_character(naruto, deep)["synopsis"]
    )


async def test_get_character_ships_blurbs_for_both_the_character_and_the_decoy():
    for _ in range(100):
        result = await characters.get_character("easy")
        assert result["info"]["name"] == result["character"]
        if result["decoy"]:
            assert result["decoy_info"]["name"] == result["decoy"]
            assert result["decoy_info"]["name"] != result["info"]["name"]


# --------------------------------------------------------------------------
# Blind mode: the imposter gets nothing, at the protocol level
# --------------------------------------------------------------------------


async def test_blind_imposter_payload_has_no_character_info_key_at_all():
    """Not null -- absent. There is no field for a later bug to populate."""
    room = make_room(6, imposter_mode="blind")
    await start_game(room)

    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["your_role"] == "imposter"
        assert "character_info" not in started


async def test_blind_imposter_never_receives_the_blurb_in_any_message():
    """The whole message history, not just game_started."""
    room = make_room(6, imposter_mode="blind")
    await start_game(room)
    await game.submit_hint(room, room.current_turn_player_id(), "clue")

    for pid in room.imposter_ids:
        history = str(ws(room, pid).sent)
        assert "character_info" not in history
        assert CHARACTER not in history
        assert room.character_info["synopsis"] not in history


async def test_blind_imposter_reconnect_snapshot_has_no_character_info():
    """The reconnect path is the second place this could leak, and the one
    easiest to forget."""
    room = make_room(6, imposter_mode="blind")
    await start_game(room)
    imposter = next(iter(room.imposter_ids))

    fresh = FakeWebSocket()
    await main._resume_session(fresh, room, "TEST", room.players[imposter], "127.0.0.1")
    room.cancel_all_grace()

    snapshot = fresh.last("reconnected")
    assert snapshot["your_role"] == "imposter"
    assert "character_info" not in snapshot
    assert CHARACTER not in str(fresh.sent)


async def test_the_genre_hint_is_not_the_same_thing_as_the_panel():
    """A blind imposter may still get the existing genre hint if the host
    enabled it -- that is a deliberate, much weaker clue and predates this
    feature. It must not turn into a full blurb."""
    room = make_room(6, imposter_mode="blind", give_imposter_hint=True)
    await start_game(room)

    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert "hint" in started              # the old, intentional clue
        assert "character_info" not in started  # not the new panel
        assert "prominence" not in str(started)
        assert "reach" not in str(started)


async def test_spectators_get_no_panel_either():
    """They hold no character, so there is nothing they are entitled to read."""
    import uuid
    from rooms import Player

    room = make_room(4)
    await start_game(room)
    room.players["late"] = Player(
        id="late", name="Late", websocket=FakeWebSocket(),
        session_id=str(uuid.uuid4()), avatar_id="goku", spectator=True,
    )

    snapshot = main._spectator_snapshot(room)
    assert "character_info" not in snapshot
    assert CHARACTER not in str(snapshot)


# --------------------------------------------------------------------------
# Crew get the panel about the real character
# --------------------------------------------------------------------------


async def test_crew_receive_the_blurb_for_the_real_character():
    room = make_room(6)
    await start_game(room)

    for pid in set(room.players) - room.imposter_ids:
        info = ws(room, pid).last("game_started")["character_info"]
        assert info["name"] == CHARACTER
        assert info["synopsis"].strip()
        assert info["role"].strip()
        assert info["genres"]


async def test_crew_keep_the_blurb_across_a_reconnect():
    room = make_room(6)
    await start_game(room)
    crewmate = next(pid for pid in room.players if pid not in room.imposter_ids)

    fresh = FakeWebSocket()
    await main._resume_session(fresh, room, "TEST", room.players[crewmate], "127.0.0.1")
    room.cancel_all_grace()

    assert fresh.last("reconnected")["character_info"]["name"] == CHARACTER


# --------------------------------------------------------------------------
# Decoy mode: symmetric access, strictly to their own character
# --------------------------------------------------------------------------


async def test_decoy_imposter_gets_a_blurb_about_the_decoy_only():
    room = make_room(6, imposter_mode="similar")
    await start_game(room)

    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        info = started["character_info"]
        assert info["name"] == DECOY
        assert info["name"] != CHARACTER
        # And nothing anywhere in their history names the real character.
        assert CHARACTER not in str(ws(room, pid).sent)


async def test_the_decoy_blurb_is_shaped_exactly_like_the_crew_blurb():
    """Symmetry is the point: if the imposter's panel had a different shape,
    a missing field, or a different vocabulary, that difference would itself
    tell them they are holding a decoy."""
    room = make_room(6, imposter_mode="similar")
    await start_game(room)

    imposter = next(iter(room.imposter_ids))
    crewmate = next(pid for pid in room.players if pid not in room.imposter_ids)
    theirs = ws(room, imposter).last("game_started")["character_info"]
    crew = ws(room, crewmate).last("game_started")["character_info"]

    assert set(theirs) == set(crew), "different fields would be a tell"
    # Same show, so the premise and genres must be byte-identical -- any
    # difference there would be a tell on its own.
    assert theirs["synopsis"] == crew["synopsis"]
    assert theirs["genres"] == crew["genres"]
    assert theirs["role"] in (
        "One of the main characters", "A recurring supporting character", "A minor character"
    )


async def test_nothing_in_the_payload_flags_the_blurb_as_a_decoy():
    """No "is_decoy", no "real_character", no marker of any kind on the info
    object itself."""
    room = make_room(6, imposter_mode="similar")
    await start_game(room)

    for pid in room.imposter_ids:
        info = ws(room, pid).last("game_started")["character_info"]
        for suspicious in ("decoy", "real", "actual", "fake", "true_"):
            assert not any(suspicious in k.lower() for k in info), info
            assert suspicious not in str(info.values()).lower()


async def test_decoy_imposter_keeps_the_same_blurb_across_a_reconnect():
    """A blurb that changed on refresh -- or flipped to the real character's
    -- would be an obvious tell."""
    room = make_room(6, imposter_mode="similar")
    await start_game(room)
    imposter = next(iter(room.imposter_ids))
    before = ws(room, imposter).last("game_started")["character_info"]

    fresh = FakeWebSocket()
    await main._resume_session(fresh, room, "TEST", room.players[imposter], "127.0.0.1")
    room.cancel_all_grace()

    after = fresh.last("reconnected")["character_info"]
    assert after == before
    assert CHARACTER not in str(fresh.sent)


async def test_the_real_character_is_never_derivable_from_the_two_blurbs():
    """Both describe characters from the same show, so the pair must not
    distinguish which is which to anyone who somehow saw both."""
    room = make_room(6, imposter_mode="similar")
    await start_game(room)

    assert room.character_info["genres"] == room.decoy_info["genres"]
    assert set(room.character_info) == set(room.decoy_info)


# --------------------------------------------------------------------------
# Layer 2 stays inside the existing isolated pattern
# --------------------------------------------------------------------------


def test_layer_two_adds_no_new_live_dependency():
    """character_details.py remains the single runtime caller of a third
    party, and no game module imports it."""
    import inspect

    for module in (characters, game):
        source = inspect.getsource(module)
        assert "character_details" not in source
        assert "httpx" not in source

    # And the only route that reaches it is the pre-existing one.
    main_source = inspect.getsource(main)
    assert main_source.count("character_details.fetch_character_details") == 1


def test_layer_two_is_isolated_from_room_state():
    """A failure in the lookup cannot touch a game, because the module has no
    access to one."""
    import inspect

    imports = [
        line for line in inspect.getsource(__import__("character_details")).splitlines()
        if line.startswith(("import ", "from "))
    ]
    for forbidden in ("rooms", "game", "fastapi", "config"):
        assert not any(forbidden in line for line in imports), imports


def test_the_lookup_endpoint_is_capped_and_rate_limited():
    import inspect

    source = inspect.getsource(main.get_character_details)
    assert "limits.details_calls" in source
    assert "limits.api_calls" in source
    assert "clean_text" in source

    import character_details
    assert character_details.REQUEST_TIMEOUT <= 10


# --------------------------------------------------------------------------
# Host-controlled anime visibility for imposters
# --------------------------------------------------------------------------


async def test_blind_imposter_does_not_get_the_anime_by_default():
    room = make_room(6, imposter_mode="blind")
    await start_game(room)

    assert room.imposter_sees_anime() is False
    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert "anime_title" not in started
        assert ANIME not in str(ws(room, pid).sent)


async def test_the_host_can_grant_the_anime_to_a_blind_imposter():
    room = make_room(6, imposter_mode="blind", show_imposter_anime=True)
    await start_game(room)

    assert room.imposter_sees_anime() is True
    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["anime_title"] == ANIME
        # Still no blurb -- knowing the show is a far weaker clue.
        assert "character_info" not in started
        assert CHARACTER not in str(ws(room, pid).sent)


async def test_similar_mode_always_grants_the_anime():
    """The imposter holds a character from that same show, so withholding the
    series name only makes them guess at something they effectively have."""
    room = make_room(6, imposter_mode="similar", show_imposter_anime=False)
    await start_game(room)

    assert room.imposter_sees_anime() is True
    for pid in room.imposter_ids:
        started = ws(room, pid).last("game_started")
        assert started["anime_title"] == ANIME
        assert started["character"] == DECOY
        assert CHARACTER not in str(ws(room, pid).sent)


async def test_the_anime_grant_survives_a_reconnect():
    for mode, granted in (("blind", False), ("blind", True), ("similar", False)):
        room = make_room(6, imposter_mode=mode, show_imposter_anime=granted)
        await start_game(room)
        imposter = next(iter(room.imposter_ids))

        fresh = FakeWebSocket()
        await main._resume_session(fresh, room, "TEST", room.players[imposter], "127.0.0.1")
        room.cancel_all_grace()

        snapshot = fresh.last("reconnected")
        expected = granted or mode == "similar"
        assert ("anime_title" in snapshot) is expected, (mode, granted)
        assert CHARACTER not in str(fresh.sent)


async def test_the_setting_is_host_only_and_type_checked():
    from test_settings import update

    room = make_room(4)
    await update(room, "p1", show_imposter_anime=True)
    assert room.show_imposter_anime is False, "non-host changed it"

    before = len(ws(room).sent)
    await update(room, "p0", show_imposter_anime="yes")
    assert "Invalid settings." in ws(room).errors(before)
    assert room.show_imposter_anime is False

    await update(room, "p0", show_imposter_anime=True)
    assert room.show_imposter_anime is True
