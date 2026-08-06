"""Offline helper to pull raw cast lists from Jikan (MyAnimeList).

    cd /Users/aaronzhang/Projects/Multiplayer-Imposter-Game
    source .venv/bin/activate
    python3 build_character_pool.py

IMPORTANT -- THIS NO LONGER WRITES character_pool_data.py.
It writes character_pool_draft.py, which nothing imports.

Phase 3.12 replaced Jikan's `role` field ("Main"/"Supporting") as the
difficulty signal, because it measures narrative centrality rather than
audience recognisability -- it calls Vegeta, Sasuke, Mikasa and Gojo
"Supporting". character_pool_data.py is now hand-tiered on `reach` +
`prominence` instead, and that judgement cannot be re-derived from anything
Jikan returns. If this script still wrote the real file, one run would
silently destroy all of that curation and quietly regress easy mode.

So the flow is now: run this to get a raw draft of a show's cast, then port
names across by hand and assign `prominence` yourself. Treat the output as
research, not as data.
"""

import asyncio
import pprint
import random
import sys

import httpx

JIKAN_BASE_URL = "https://api.jikan.moe/v4"
REQUEST_DELAY = 1.2  # seconds between requests -- safely under Jikan's ~3/sec limit
CHARACTERS_PER_ANIME = 18
# Deliberately NOT character_pool_data.py -- see the module docstring.
OUTPUT_FILE = "character_pool_draft.py"
MAX_ATTEMPTS = 5
RETRY_BACKOFF = [3, 8, 15, 30]  # seconds, one per retry after the first attempt

HARD_ROLE = "Supporting"


async def get_with_retry(client: httpx.AsyncClient, url: str, params: "dict | None" = None) -> httpx.Response:
    """Jikan has been unreliable throughout this project's development --
    mixing genuine outages (504) with rate-limit responses (429). Since this
    script only runs occasionally and isn't time-sensitive, it's worth
    retrying hard here rather than accepting the first failure, unlike the
    runtime code which must never block gameplay on this."""
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp
            last_error = Exception(f"HTTP {resp.status_code}")
        except Exception as e:
            last_error = e
        if attempt < MAX_ATTEMPTS - 1:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            print(f"    retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {wait}s ({last_error})")
            await asyncio.sleep(wait)
    raise last_error

# Anime we already have a verified mal_id + curated genres for (confirmed
# working across many earlier live tests this project). No search needed.
KNOWN_ANIME = [
    {"mal_id": 20, "title": "Naruto", "genres": ["Action", "Adventure"]},
    {"mal_id": 21, "title": "One Piece", "genres": ["Action", "Adventure", "Fantasy"]},
    {"mal_id": 1535, "title": "Death Note", "genres": ["Mystery", "Psychological", "Supernatural"]},
    {"mal_id": 16498, "title": "Attack on Titan", "genres": ["Action", "Drama", "Fantasy", "Mystery"]},
    {"mal_id": 31964, "title": "My Hero Academia", "genres": ["Action", "Adventure"]},
    {"mal_id": 38000, "title": "Demon Slayer: Kimetsu no Yaiba", "genres": ["Action", "Adventure", "Fantasy"]},
    {"mal_id": 5114, "title": "Fullmetal Alchemist: Brotherhood", "genres": ["Action", "Adventure", "Drama", "Fantasy"]},
    {"mal_id": 40748, "title": "Jujutsu Kaisen", "genres": ["Action", "Fantasy", "Horror"]},
    {"mal_id": 22319, "title": "Tokyo Ghoul", "genres": ["Action", "Drama", "Horror", "Mystery"]},
    {"mal_id": 20583, "title": "Haikyuu!!", "genres": ["Sports", "Comedy", "Drama"]},
    {"mal_id": 269, "title": "Bleach", "genres": ["Action", "Adventure", "Fantasy"]},
    {"mal_id": 6702, "title": "Fairy Tail", "genres": ["Action", "Adventure", "Fantasy"]},
    {"mal_id": 11757, "title": "Sword Art Online", "genres": ["Action", "Adventure", "Fantasy", "Romance"]},
    {"mal_id": 1575, "title": "Code Geass: Lelouch of the Rebellion", "genres": ["Action", "Drama", "Mecha", "Sci-Fi"]},
    {"mal_id": 50265, "title": "Spy x Family", "genres": ["Action", "Comedy", "Childcare"]},
    {"mal_id": 44511, "title": "Chainsaw Man", "genres": ["Action", "Fantasy", "Horror"]},
    {"mal_id": 30276, "title": "One Punch Man", "genres": ["Action", "Comedy", "Sci-Fi"]},
    {"mal_id": 11061, "title": "Hunter x Hunter (2011)", "genres": ["Action", "Adventure", "Fantasy"]},
    {"mal_id": 9253, "title": "Steins;Gate", "genres": ["Sci-Fi", "Thriller", "Drama"]},
    {"mal_id": 33352, "title": "Violet Evergarden", "genres": ["Drama", "Fantasy", "Slice of Life"]},
]

# Anime resolved by title search rather than a hand-typed id -- a wrong
# guessed id can be *valid* but point at a completely different show, which
# would silently poison the data with no error at all. Searching by the
# title we actually mean and using whatever id Jikan resolves it to avoids
# that failure mode entirely.
SEARCH_TITLES = [
    "Dragon Ball Z",
    "Cowboy Bebop",
    "Neon Genesis Evangelion",
    "Mob Psycho 100",
    "The Promised Neverland",
    "Assassination Classroom",
    "Toradora!",
    "Your Lie in April",
    "Re:Zero − Starting Life in Another World",
    "Overlord",
    "Black Clover",
    "Dr. Stone",
    "Vinland Saga",
    "Made in Abyss",
    "Fruits Basket",
    "Kaguya-sama: Love is War",
    "Konosuba",
    "Erased",
    "Parasyte -the maxim-",
    "Fate/Zero",
]


async def resolve_by_search(client: httpx.AsyncClient, title: str) -> "dict | None":
    resp = await get_with_retry(client, f"{JIKAN_BASE_URL}/anime", params={"q": title, "limit": 1})
    data = resp.json()["data"]
    if not data:
        return None
    entry = data[0]
    genres = [g["name"] for g in entry.get("genres", [])]
    return {"mal_id": entry["mal_id"], "title": entry["title"], "genres": genres}


async def fetch_characters(client: httpx.AsyncClient, mal_id: int) -> list[dict]:
    resp = await get_with_retry(client, f"{JIKAN_BASE_URL}/anime/{mal_id}/characters")
    entries = resp.json()["data"]
    characters = [
        {"name": e["character"]["name"], "role": e.get("role", HARD_ROLE)}
        for e in entries
    ]
    main = [c for c in characters if c["role"] == "Main"]
    supporting = [c for c in characters if c["role"] == "Supporting"]
    random.shuffle(supporting)
    return main + supporting[: max(0, CHARACTERS_PER_ANIME - len(main))]


async def process_known(client: httpx.AsyncClient, anime: dict, results: list, skipped: list) -> None:
    try:
        chars = await fetch_characters(client, anime["mal_id"])
    except Exception as e:
        skipped.append((anime["title"], f"character fetch failed: {e}"))
        print(f"SKIPPED {anime['title']}: character fetch failed: {e}")
        return
    if not chars:
        skipped.append((anime["title"], "no characters returned"))
        print(f"SKIPPED {anime['title']}: no characters returned")
        return
    main_count = sum(1 for c in chars if c["role"] == "Main")
    results.append({"title": anime["title"], "mal_id": anime["mal_id"], "genres": anime["genres"], "characters": chars})
    print(f"OK   {anime['title']}: {len(chars)} characters ({main_count} Main)")


async def process_search(client: httpx.AsyncClient, title: str, results: list, skipped: list) -> None:
    try:
        resolved = await resolve_by_search(client, title)
    except Exception as e:
        skipped.append((title, f"search failed: {e}"))
        print(f"SKIPPED {title}: search failed: {e}")
        return
    if resolved is None:
        skipped.append((title, "no search match"))
        print(f"SKIPPED {title}: no search match")
        return
    if not resolved["genres"]:
        print(f"FLAG {resolved['title']}: Jikan returned no genre tags -- using ['Unknown']")
        resolved["genres"] = ["Unknown"]

    await asyncio.sleep(REQUEST_DELAY)
    try:
        chars = await fetch_characters(client, resolved["mal_id"])
    except Exception as e:
        skipped.append((title, f"character fetch failed: {e}"))
        print(f"SKIPPED {title}: character fetch failed: {e}")
        return
    if not chars:
        skipped.append((title, "no characters returned"))
        print(f"SKIPPED {title}: no characters returned")
        return
    main_count = sum(1 for c in chars if c["role"] == "Main")
    results.append(
        {"title": resolved["title"], "mal_id": resolved["mal_id"], "genres": resolved["genres"], "characters": chars}
    )
    print(f"OK   {resolved['title']} (matched from '{title}'): {len(chars)} characters ({main_count} Main)")


async def main() -> None:
    results: list = []
    skipped: list = []
    total_targets = len(KNOWN_ANIME) + len(SEARCH_TITLES)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, anime in enumerate(KNOWN_ANIME):
            if i > 0:
                await asyncio.sleep(REQUEST_DELAY)
            await process_known(client, anime, results, skipped)

        for title in SEARCH_TITLES:
            await asyncio.sleep(REQUEST_DELAY)
            await process_search(client, title, results, skipped)

    with open(OUTPUT_FILE, "w") as f:
        f.write('"""DRAFT research output from build_character_pool.py. Nothing imports this.\n\n')
        f.write("The `role` field below is Jikan's own Main/Supporting tag. It is NOT the\n")
        f.write("game's difficulty signal -- see character_pool_data.py, which stores\n")
        f.write("`reach` per anime and `prominence` per character and is maintained by hand.\n")
        f.write("Port names across manually and assign prominence yourself.\n")
        f.write('"""\n\n')
        f.write("DRAFT_POOL = ")
        f.write(pprint.pformat(results, width=100))
        f.write("\n")

    total_chars = sum(len(a["characters"]) for a in results)
    print()
    print("=" * 60)
    print(f"Anime processed successfully: {len(results)}/{total_targets}")
    print(f"Total characters collected: {total_chars}")
    print(f"Skipped: {len(skipped)}")
    for title, reason in skipped:
        print(f"  - {title}: {reason}")
    print(f"Wrote {OUTPUT_FILE} (draft only -- character_pool_data.py is untouched)")
    print("Next step is manual: port names across and assign `prominence` by hand.")

    if not results:
        print("\nWARNING: zero anime succeeded -- the draft is empty.")
        print("The game is unaffected; it reads the curated character_pool_data.py.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
