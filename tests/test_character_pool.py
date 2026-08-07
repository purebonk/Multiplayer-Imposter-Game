"""Character pool data integrity.

This data is hand-curated, so nothing but a test enforces its shape. The most
important assertion in this file is the last one: that every character
`get_character()` can return is selectable in the guess picker for its tier.
If those two ever disagree, an ejected imposter can be shown a list that does
not contain the right answer, and the guess becomes unwinnable through no
fault of their own.
"""

import collections

import characters
from character_pool_data import ANIME_POOL

REACH_VALUES = {"mega", "popular", "cult"}
PROMINENCE_VALUES = {"core", "notable", "deep"}


def test_the_pool_is_not_empty():
    assert len(ANIME_POOL) >= 30
    assert sum(len(a["characters"]) for a in ANIME_POOL) >= 200


def test_every_anime_has_the_required_fields():
    for anime in ANIME_POOL:
        for field in ("title", "genres", "reach", "characters"):
            assert field in anime, f"{anime.get('title', '?')} is missing {field}"
        assert anime["title"].strip(), "blank anime title"
        assert anime["characters"], f"{anime['title']} has no characters"


def test_every_anime_has_at_least_one_genre():
    """Genres feed the imposter's hint, so an untagged anime would hand them
    an empty clue."""
    for anime in ANIME_POOL:
        assert anime["genres"], f"{anime['title']} has no genres"
        for genre in anime["genres"]:
            assert isinstance(genre, str) and genre.strip()


def test_reach_and_prominence_use_known_values():
    """A typo here would silently retier a character via the matrix fallback."""
    for anime in ANIME_POOL:
        assert anime["reach"] in REACH_VALUES, f"{anime['title']}: {anime['reach']}"
        for character in anime["characters"]:
            assert character["prominence"] in PROMINENCE_VALUES, (
                f"{anime['title']} / {character.get('name')}: {character.get('prominence')}"
            )


def test_character_names_are_present_and_unique_everywhere():
    """Two characters sharing a name is genuinely bad in a game whose win
    condition is naming one -- the picker would collapse them into one entry."""
    seen = collections.Counter()
    for anime in ANIME_POOL:
        for character in anime["characters"]:
            name = character.get("name", "")
            assert name.strip(), f"{anime['title']} has a blank character name"
            seen[name] += 1

    duplicates = [name for name, count in seen.items() if count > 1]
    assert duplicates == [], f"duplicate character names: {duplicates}"


def test_anime_titles_are_unique():
    titles = [a["title"] for a in ANIME_POOL]
    assert len(titles) == len(set(titles))


def test_every_difficulty_tier_is_populated():
    for tier in characters.DIFFICULTY_TIERS:
        entries = characters.pool_entries(tier)
        assert len(entries) >= 50, f"{tier} tier is too thin: {len(entries)}"


def test_the_tiers_do_not_overlap():
    """A name in two tiers would make the difficulty setting meaningless for
    that character."""
    by_tier = {t: {n for _, n in characters.pool_entries(t)} for t in characters.DIFFICULTY_TIERS}
    tiers = list(by_tier)
    for i, first in enumerate(tiers):
        for second in tiers[i + 1:]:
            overlap = by_tier[first] & by_tier[second]
            assert overlap == set(), f"{first}/{second} share: {overlap}"


def test_the_difficulty_matrix_covers_every_combination():
    for prominence in PROMINENCE_VALUES:
        for reach in REACH_VALUES:
            assert (prominence, reach) in characters.DIFFICULTY_MATRIX


def test_recognizable_characters_land_in_the_easy_tier():
    """The whole point of the Phase 3.12 retiering. MyAnimeList calls every
    one of these "Supporting", which is exactly why its role field was the
    wrong difficulty signal."""
    easy = {name for _, name in characters.pool_entries("easy")}
    for name in (
        "Vegeta",
        "Sasuke Uchiha",
        "Sakura Haruno",
        "Mikasa Ackerman",
        "Itachi Uchiha",
        "Satoru Gojo",
        "Levi Ackerman",
    ):
        assert name in easy, f"{name} should be recognizable enough for easy mode"


def test_deep_cuts_land_in_the_hard_tier():
    hard = {name for _, name in characters.pool_entries("hard")}
    for name in ("Yotsuha Miyamizu", "Erica Brown", "Panda"):
        assert name in hard, f"{name} should be a deep cut"


def test_a_cult_shows_lead_is_not_easy():
    """Leading a niche show does not make you recognizable to a casual table."""
    easy = {name for _, name in characters.pool_entries("easy")}
    for name in ("Rintaro Okabe", "Megumin", "Thorfinn"):
        assert name not in easy, f"{name} leads a cult show and should not be easy"


def test_pool_entry_ids_are_stable_and_resolvable():
    for tier in characters.DIFFICULTY_TIERS:
        entries = characters.pool_entries(tier)
        assert [i for i, _ in entries] == list(range(len(entries)))
        for entry_id, name in entries:
            assert characters.resolve_pool_entry(tier, entry_id) == name


def test_bogus_pool_ids_resolve_to_nothing():
    """The id comes straight off the wire, so it has to survive garbage."""
    for bad in (-1, 10**9, "3", None, True, 1.5, [], {}):
        assert characters.resolve_pool_entry("easy", bad) is None, bad


async def test_get_character_only_ever_returns_its_requested_tier():
    for tier in characters.DIFFICULTY_TIERS:
        for _ in range(300):
            result = await characters.get_character(tier)
            assert result["difficulty"] == tier
            assert result["genres"]
            assert result["character_role"] in ("Main", "Supporting")


async def test_every_character_get_character_can_return_is_in_the_guess_picker():
    """The assertion that actually protects gameplay.

    Both sides are built from the same two helpers precisely so they cannot
    drift, but that is an invariant worth enforcing rather than trusting: if
    it breaks, the correct answer is simply missing from the ejected
    imposter's dropdown and the guess is unwinnable.
    """
    for tier in characters.DIFFICULTY_TIERS:
        selectable = {name for _, name in characters.pool_entries(tier)}
        for anime in characters._anime_with_tier(tier):
            for character in characters._filter_by_difficulty(anime, tier):
                assert character["name"] in selectable, (
                    f"{character['name']} can be chosen at {tier} but is not selectable"
                )


async def test_the_decoy_is_always_a_different_character_from_the_same_show():
    """"similar" mode depends on this: a decoy from another series would make
    the imposter obvious, and the real character would be a giveaway."""
    titles = {a["title"]: {c["name"] for c in a["characters"]} for a in ANIME_POOL}
    for _ in range(400):
        result = await characters.get_character("easy")
        decoy = result["decoy"]
        if decoy is None:
            continue
        assert decoy != result["character"]
        assert decoy in titles[result["anime_title"]]


def test_the_generator_script_cannot_overwrite_the_curated_pool():
    """One run of the old build script would have destroyed the hand-tiering,
    which cannot be re-derived from anything the API returns."""
    import build_character_pool

    assert build_character_pool.OUTPUT_FILE != "character_pool_data.py"
