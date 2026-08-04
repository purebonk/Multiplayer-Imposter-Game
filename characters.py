import random

import httpx

JIKAN_BASE_URL = "https://api.jikan.moe/v4"

ANIME_POOL = [
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

# role is MAL's own "how central is this character" tag ("Main" | "Supporting"),
# which is a far more reliable difficulty signal than trying to threshold a
# favorites count that varies wildly in scale between franchises.
EASY_ROLE = "Main"
HARD_ROLE = "Supporting"

_character_cache: dict[int, list[dict]] = {}


async def _get_character_entries(client: httpx.AsyncClient, mal_id: int) -> list[dict]:
    if mal_id in _character_cache:
        return _character_cache[mal_id]

    response = await client.get(f"{JIKAN_BASE_URL}/anime/{mal_id}/characters")
    response.raise_for_status()
    raw_entries = response.json()["data"]

    entries = [
        {"name": entry["character"]["name"], "role": entry.get("role", HARD_ROLE)}
        for entry in raw_entries
    ]
    _character_cache[mal_id] = entries
    return entries


def _filter_by_difficulty(entries: list[dict], difficulty: str) -> list[dict]:
    tier = EASY_ROLE if difficulty == "easy" else HARD_ROLE
    candidates = [e for e in entries if e["role"] == tier]
    return candidates or entries


async def fetch_random_character(difficulty: str = "easy") -> dict:
    pool = ANIME_POOL.copy()
    random.shuffle(pool)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for anime in pool:
            try:
                entries = await _get_character_entries(client, anime["mal_id"])
            except (httpx.HTTPError, KeyError):
                continue

            candidates = _filter_by_difficulty(entries, difficulty)
            if candidates:
                chosen = random.choice(candidates)
                return {
                    "character": chosen["name"],
                    "character_role": chosen["role"],
                    "anime_title": anime["title"],
                    "genres": anime["genres"],
                }

    raise RuntimeError("Could not fetch a character from any anime in the pool")
