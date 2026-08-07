import functools
import random

from character_pool_data import ANIME_POOL

# Difficulty is derived from two stored signals rather than one, because
# recognisability genuinely is the product of both -- see the long note at the
# top of character_pool_data.py. `reach` is how far the SHOW travelled outside
# anime fandom; `prominence` is how central the CHARACTER is within it.
#
#              mega       popular    cult
#   core       easy       easy       medium
#   notable    medium     medium     hard
#   deep       hard       hard       hard
#
# Reading the two edges of that grid is the whole argument for the redesign:
#   - a core character of a mega show is easy even when MAL calls them
#     "Supporting" (Vegeta, Sasuke, Mikasa, Itachi, Gojo);
#   - the literal protagonist of a cult show is only medium, because leading
#     Steins;Gate does not make you recognisable to a table of casual players.
DIFFICULTY_MATRIX = {
    ("core", "mega"): "easy",
    ("core", "popular"): "easy",
    ("core", "cult"): "medium",
    ("notable", "mega"): "medium",
    ("notable", "popular"): "medium",
    ("notable", "cult"): "hard",
    ("deep", "mega"): "hard",
    ("deep", "popular"): "hard",
    ("deep", "cult"): "hard",
}

# Ordered easy -> hard. Exposed so rooms.py validates against one list rather
# than keeping a second copy that could drift.
DIFFICULTY_TIERS = ("easy", "medium", "hard")

DEFAULT_REACH = "popular"
DEFAULT_PROMINENCE = "notable"


def difficulty_of(anime: dict, character: dict) -> str:
    """The tier a given character falls into. Unknown/missing values fall back
    to the middle of each axis rather than raising -- a data typo should cost
    one character's placement, not crash a game start."""
    reach = anime.get("reach", DEFAULT_REACH)
    prominence = character.get("prominence", DEFAULT_PROMINENCE)
    return DIFFICULTY_MATRIX.get((prominence, reach), "medium")


def _filter_by_difficulty(anime: dict, difficulty: str) -> list[dict]:
    """Characters in this anime at this tier. May legitimately be empty --
    not every show has a genuine deep cut or a mega-famous face, and callers
    are expected to pick an anime that does rather than paper over it."""
    return [c for c in anime["characters"] if difficulty_of(anime, c) == difficulty]


def _anime_with_tier(difficulty: str, titles=None) -> list[dict]:
    """Anime that actually contain a character at this tier.

    Selection is still anime-first then character (unchanged mechanism), but
    the candidate anime are now filtered up front. Previously an anime with
    nobody in the requested tier silently fell back to its ENTIRE cast, which
    quietly leaked easy characters into hard mode. With three tiers that
    fallback would have fired constantly and blurred the tiers into noise.

    `titles` narrows this to the host's chosen anime; None means the full pool.
    """
    return [a for a in _selected(titles) if _filter_by_difficulty(a, difficulty)]


def _pick_decoy(anime: dict, chosen: dict, difficulty: str) -> "str | None":
    """A different character from the SAME anime, for the Undercover-style
    imposter mode. Prefers the same difficulty tier so the decoy is a
    comparable name, but will take any other character from the show rather
    than give up -- a same-series decoy is what makes the imposter's hints
    plausible instead of pure guesswork. Returns None only when the anime
    genuinely has nobody else."""
    same_tier = [c for c in _filter_by_difficulty(anime, difficulty)
                 if c["name"] != chosen["name"]]
    if same_tier:
        return random.choice(same_tier)["name"]

    anyone_else = [c for c in anime["characters"] if c["name"] != chosen["name"]]
    return random.choice(anyone_else)["name"] if anyone_else else None


ALL_TITLES = tuple(sorted(a["title"] for a in ANIME_POOL))


def _selected(titles) -> list[dict]:
    """The anime a room is drawing from. None/empty means the whole pool."""
    if not titles:
        return ANIME_POOL
    wanted = set(titles)
    return [a for a in ANIME_POOL if a["title"] in wanted] or ANIME_POOL


def anime_catalog() -> list[dict]:
    """What the host's picker is built from. Character counts per tier are
    included so the UI can warn before a thin selection becomes a problem,
    rather than after the host presses Start."""
    catalog = []
    for anime in sorted(ANIME_POOL, key=lambda a: a["title"]):
        catalog.append({
            "title": anime["title"],
            "genres": anime["genres"],
            "total": len(anime["characters"]),
            "tiers": {t: len(_filter_by_difficulty(anime, t)) for t in DIFFICULTY_TIERS},
        })
    return catalog


def pool_counts(titles=None) -> dict:
    """How many characters a given anime selection offers in each tier.

    This is what makes the thin-selection edge case visible: picking three
    shows and setting difficulty to hard can leave literally nothing to draw,
    and the host should see that as a number before starting, not as an error.
    """
    chosen = _selected(titles)
    return {
        tier: sum(len(_filter_by_difficulty(a, tier)) for a in chosen)
        for tier in DIFFICULTY_TIERS
    }


def valid_titles(titles) -> list[str]:
    """Filter a client-supplied list down to titles that actually exist."""
    if not isinstance(titles, list):
        return []
    known = {a["title"] for a in ANIME_POOL}
    seen, out = set(), []
    for title in titles:
        if isinstance(title, str) and title in known and title not in seen:
            seen.add(title)
            out.append(title)
    return out


@functools.lru_cache(maxsize=4)
def pool_entries(difficulty: str) -> tuple:
    """Every character a game at this difficulty could possibly have chosen,
    as ((id, name), ...) sorted by name.

    This is what the ejected imposter picks from for their last-chance guess,
    instead of typing a name from memory. Deliberately the WHOLE tier, never
    just the current anime's cast -- narrowing it to one show would hand them
    the answer. Seeing ~80-200 names tells them nothing they didn't already
    know, since the difficulty setting is shown in the lobby anyway.

    Cached because it's rebuilt on every guess phase and the underlying pool
    is a static import that never changes at runtime.

    Built from the same two helpers get_character() uses, so it is exactly the
    set a game at this difficulty can produce -- never a re-derivation that
    could drift. If this and get_character disagreed, the correct answer would
    simply be missing from the picker and the guess would be unwinnable.
    """
    names = {
        c["name"]
        for anime in _anime_with_tier(difficulty)
        for c in _filter_by_difficulty(anime, difficulty)
    }
    # Index-as-id is stable for the process because the sort is total and the
    # source data is immutable; the server re-derives the name from the id at
    # submit time, so a client never gets to define what a given id means.
    return tuple((index, name) for index, name in enumerate(sorted(names)))


def resolve_pool_entry(difficulty: str, entry_id) -> "str | None":
    """Turn a client-supplied pool id back into a character name, or None."""
    if not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None
    entries = pool_entries(difficulty)
    if 0 <= entry_id < len(entries):
        return entries[entry_id][1]
    return None


async def get_character(difficulty: str = "easy", titles=None) -> dict:
    """Picks a random character from the static, hand-curated pool
    (character_pool_data.py). This is the ONLY character source used during
    actual gameplay -- no network call, so starting a round is instant and
    never depends on Jikan being up. The live Jikan lookup in main.py's
    /api/character-details endpoint is a completely separate, decorative,
    post-game-only feature and does not feed back into this.

    Mechanism is unchanged from before the retiering: pick an anime, then pick
    a character from it at the requested tier. Only the candidate anime are
    now pre-filtered, so the tier is honoured rather than silently widened.

    `titles` restricts the draw to the host's chosen anime; None is the whole
    pool. `decoy` is the alternate same-series character handed to imposters in
    "similar" mode; it is always computed but only ever sent to a client
    when that mode is on."""
    pool = _anime_with_tier(difficulty, titles)
    if not pool:
        # Reachable two ways: broken data, or a host whose chosen anime have
        # nobody in the chosen tier. game.start_game refuses that combination
        # up front with a message naming the real problem, so this stays a
        # clean failure rather than an IndexError killing the socket.
        raise RuntimeError(f"no characters available at difficulty {difficulty!r}")
    anime = random.choice(pool)
    chosen = random.choice(_filter_by_difficulty(anime, difficulty))
    return {
        "character": chosen["name"],
        "anime_title": anime["title"],
        "genres": anime["genres"],
        # Collapsed to MAL's old vocabulary on purpose: this only feeds the
        # imposter's vague "a main character" / "a supporting character" hint,
        # and leaking the full three-way prominence there would narrow the
        # answer more than the hint is meant to.
        "character_role": "Main" if chosen.get("prominence") == "core" else "Supporting",
        "difficulty": difficulty_of(anime, chosen),
        "decoy": _pick_decoy(anime, chosen, difficulty),
    }
