"""Host-controlled anime selection.

An empty selection means "the whole pool" everywhere -- client, server and
character picker -- so there is no reachable state in which the pool is empty
and a game cannot start.
"""

import characters
import game
from conftest import make_room, ws
from test_settings import update


# --------------------------------------------------------------------------
# The catalog the picker is built from
# --------------------------------------------------------------------------


def test_the_catalog_covers_the_whole_pool_with_per_tier_counts():
    catalog = characters.anime_catalog()
    assert len(catalog) == len(characters.ANIME_POOL)
    for entry in catalog:
        assert entry["title"] and entry["genres"] and entry["total"] > 0
        assert set(entry["tiers"]) == set(characters.DIFFICULTY_TIERS)
        assert sum(entry["tiers"].values()) == entry["total"]


def test_the_catalog_is_sorted_for_scanning():
    titles = [e["title"] for e in characters.anime_catalog()]
    assert titles == sorted(titles)


# --------------------------------------------------------------------------
# Counts drive the UI warning and the server's refusal
# --------------------------------------------------------------------------


def test_an_empty_selection_counts_as_the_whole_pool():
    assert characters.pool_counts([]) == characters.pool_counts(None)
    assert characters.pool_counts([]) == characters.pool_counts()
    assert sum(characters.pool_counts([]).values()) > 500


def test_counts_narrow_with_the_selection():
    narrow = characters.pool_counts(["Naruto"])
    full = characters.pool_counts()
    for tier in characters.DIFFICULTY_TIERS:
        assert 0 < narrow[tier] < full[tier]


def test_a_thin_selection_can_legitimately_have_an_empty_tier():
    """The edge case the whole guard exists for: Your Name has no medium-tier
    characters at all, so a host who picks only it and sets Medium has nothing
    to draw."""
    counts = characters.pool_counts(["Your Name"])
    assert counts["medium"] == 0
    assert counts["easy"] > 0 and counts["hard"] > 0


async def test_get_character_only_draws_from_the_selection():
    for _ in range(200):
        result = await characters.get_character("easy", ["Naruto", "Dragon Ball Z"])
        assert result["anime_title"] in ("Naruto", "Dragon Ball Z")


async def test_get_character_respects_the_tier_within_a_selection():
    for _ in range(200):
        result = await characters.get_character("hard", ["Naruto"])
        assert result["anime_title"] == "Naruto"
        assert result["difficulty"] == "hard"


# --------------------------------------------------------------------------
# Server-side validation -- the picker being host-only in the UI is not it
# --------------------------------------------------------------------------


def test_unknown_titles_are_dropped_not_trusted():
    assert characters.valid_titles(["Naruto", "Totally Made Up Anime"]) == ["Naruto"]
    assert characters.valid_titles(["Naruto", "Naruto"]) == ["Naruto"]
    assert characters.valid_titles([123, None, {}, True]) == []
    assert characters.valid_titles("Naruto") == []   # not a list
    assert characters.valid_titles(None) == []


async def test_only_the_host_can_change_the_anime_pool():
    room = make_room(4)
    before = len(ws(room, "p1").sent)

    await update(room, "p1", selected_anime=["Naruto"])

    assert "Only the host can change settings." in ws(room, "p1").errors(before)
    assert room.selected_anime == []


async def test_the_host_can_set_and_clear_the_selection():
    room = make_room(4)

    await update(room, "p0", selected_anime=["Naruto", "Bleach"])
    assert set(room.selected_anime) == {"Naruto", "Bleach"}

    await update(room, "p0", selected_anime=[])
    assert room.selected_anime == []   # back to the whole pool


async def test_a_selection_of_only_bogus_titles_is_refused():
    """Accepting it would silently reinterpret the host's choice as "all"; a
    host who thinks they narrowed the pool should be told they did not."""
    room = make_room(4)
    before = len(ws(room).sent)

    await update(room, "p0", selected_anime=["Nonexistent Show", "Also Fake"])

    assert any("at least one anime" in e for e in ws(room).errors(before))
    assert room.selected_anime == []


async def test_bogus_titles_are_filtered_out_of_a_partly_valid_selection():
    room = make_room(4)
    await update(room, "p0", selected_anime=["Naruto", "Not An Anime"])
    assert room.selected_anime == ["Naruto"]


async def test_a_non_list_selection_is_rejected():
    room = make_room(4)
    before = len(ws(room).sent)

    await update(room, "p0", selected_anime="Naruto")

    assert "Invalid settings." in ws(room).errors(before)
    assert room.selected_anime == []


async def test_the_settings_broadcast_carries_the_selection_and_counts():
    room = make_room(4)
    await update(room, "p0", selected_anime=["Naruto"])

    payload = ws(room, "p1").last("settings_updated")
    assert payload["selected_anime"] == ["Naruto"]
    assert payload["pool_counts"] == characters.pool_counts(["Naruto"])


# --------------------------------------------------------------------------
# The thin-tier edge case, end to end
# --------------------------------------------------------------------------


async def test_starting_is_refused_when_the_tier_is_empty_in_the_selection():
    """Without this, get_character raises and the host sees "couldn't fetch a
    character, try again" -- sending them looking for a network fault that
    does not exist."""
    room = make_room(4)
    await update(room, "p0", difficulty="medium", selected_anime=["Your Name"])
    before = len(ws(room).sent)

    await game.start_game(room, "p0")

    errors = ws(room).errors(before)
    assert any("No medium characters in your chosen anime" in e for e in errors), errors
    assert room.state.value == "lobby", "the room must stay startable"


async def test_widening_the_selection_makes_it_startable_again():
    room = make_room(4)
    await update(room, "p0", difficulty="medium", selected_anime=["Your Name"])
    await update(room, "p0", difficulty="medium", selected_anime=["Your Name", "Naruto"])

    await game.start_game(room, "p0")

    assert room.state.value == "hints"
    assert room.anime_title in ("Your Name", "Naruto")


async def test_changing_difficulty_also_resolves_it():
    room = make_room(4)
    await update(room, "p0", difficulty="medium", selected_anime=["Your Name"])
    await update(room, "p0", difficulty="easy", selected_anime=["Your Name"])

    await game.start_game(room, "p0")

    assert room.state.value == "hints"
    assert room.anime_title == "Your Name"


async def test_a_narrow_selection_plays_a_full_game(fast_transitions):
    from conftest import play_hints
    from rooms import SKIP_VOTE

    room = make_room(4)
    # last_chance_guess through `update`, not make_room: update() sends the
    # whole settings block, so a value set on the room directly would be
    # overwritten by the helper's default and the ejection would detour
    # through the guess phase instead of resolving straight to a reveal.
    await update(room, "p0", selected_anime=["Naruto"], last_chance_guess=False)
    await game.start_game(room, "p0")
    assert room.anime_title == "Naruto"

    await play_hints(room)
    imposter = next(iter(room.imposter_ids))
    for pid in room.remaining_ids():
        await game.submit_vote(room, pid, imposter if pid != imposter else SKIP_VOTE)

    from conftest import settle
    await settle(0.3)
    reveal = ws(room).last("round_reveal")
    assert reveal["game_over"] is True
    assert reveal["winner"] == "crew"


async def test_the_decoy_still_comes_from_the_same_show_in_a_narrow_selection():
    for _ in range(100):
        result = await characters.get_character("easy", ["Naruto"])
        assert result["decoy"] is not None
        naruto = next(a for a in characters.ANIME_POOL if a["title"] == "Naruto")
        assert result["decoy"] in {c["name"] for c in naruto["characters"]}
        assert result["decoy"] != result["character"]
