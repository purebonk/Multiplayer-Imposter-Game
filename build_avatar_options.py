"""One-time script to fetch avatar portraits and save them into the repo.

Not part of the running app -- run manually, offline, only when you want to
change the avatar roster:

    cd /Users/aaronzhang/Projects/Multiplayer-Imposter-Game
    source .venv/bin/activate
    python3 build_avatar_options.py

Images are DOWNLOADED into static/avatars/ and committed, not hotlinked.
The running game therefore has zero dependency on any anime API or CDN --
avatars keep working even if AniList, Jikan, and MyAnimeList are all down
forever. That is deliberate: this project already lost a lot of time to
Jikan outages, and player avatars are core UI, not a decorative extra.

Source is AniList's GraphQL API rather than Jikan, chosen because Jikan was
returning 504s for days during development while AniList stayed healthy.
Only this script talks to it; nothing at runtime does.

Every character also carries an `emoji` fallback used if its image is
missing, so a partial run still produces a working picker.
"""

import asyncio
import io
import os
import pprint

import httpx
from PIL import Image

ANILIST_URL = "https://graphql.anilist.co"
AVATAR_DIR = "static/avatars"
OUTPUT_FILE = "avatar_options.py"
REQUEST_DELAY = 0.7  # AniList allows ~90 req/min; 10 requests is trivial, but be polite
AVATAR_SIZE = 256  # square, since the UI shows these as square pfps
# Source portraits are taller than wide (~230x345) with the face in the upper
# portion, so a square crop is anchored near the top rather than centered --
# a true center crop cuts foreheads off.
CROP_TOP_BIAS = 0.12

CHARACTER_QUERY = """
query ($search: String) {
  Character(search: $search) {
    id
    name { full }
    image { large }
  }
}
"""

# 10 characters chosen for cross-generation recognizability, one per series
# so the picker doesn't read as "three guys from Jujutsu Kaisen". The emoji
# is both a fallback and a bit of personality if the portrait is missing.
ROSTER = [
    {"id": "goku", "name": "Goku", "search": "Son Goku", "series": "Dragon Ball Z", "emoji": "🔥"},
    {"id": "luffy", "name": "Luffy", "search": "Monkey D. Luffy", "series": "One Piece", "emoji": "🏴‍☠️"},
    {"id": "naruto", "name": "Naruto", "search": "Naruto Uzumaki", "series": "Naruto", "emoji": "🍥"},
    {"id": "gojo", "name": "Gojo", "search": "Satoru Gojou", "series": "Jujutsu Kaisen", "emoji": "👁️"},
    {"id": "reze", "name": "Reze", "search": "Reze", "series": "Chainsaw Man", "emoji": "💣"},
    {"id": "tanjiro", "name": "Tanjiro", "search": "Tanjirou Kamado", "series": "Demon Slayer", "emoji": "🌊"},
    # AniList 404s on "Levi Ackerman"/"Rivaille"; the bare given name is the
    # only search term that resolves him.
    {"id": "levi", "name": "Levi", "search": "Levi", "series": "Attack on Titan", "emoji": "⚔️"},
    {"id": "saitama", "name": "Saitama", "search": "Saitama", "series": "One Punch Man", "emoji": "👊"},
    {"id": "light", "name": "Light", "search": "Light Yagami", "series": "Death Note", "emoji": "📓"},
    {"id": "frieren", "name": "Frieren", "search": "Frieren", "series": "Frieren", "emoji": "🪄"},
]


async def resolve_image_url(client: httpx.AsyncClient, entry: dict) -> "tuple[str | None, str | None, str | None]":
    """Returns (image_url, matched_name, failure_reason)."""
    try:
        resp = await client.post(
            ANILIST_URL, json={"query": CHARACTER_QUERY, "variables": {"search": entry["search"]}}
        )
        if resp.status_code != 200:
            return None, None, f"HTTP {resp.status_code}"
        character = (resp.json().get("data") or {}).get("Character")
        if not character:
            return None, None, "no search match"
        image_url = (character.get("image") or {}).get("large")
        matched = (character.get("name") or {}).get("full")
        if not image_url:
            return None, matched, "matched character has no image"
        return image_url, matched, None
    except Exception as e:  # noqa: BLE001 - offline script; any failure just means "skip this one"
        return None, None, str(e)


def to_square(raw: bytes) -> Image.Image:
    """Crops to a square anchored near the top of the portrait, then resizes
    to a consistent AVATAR_SIZE so every card renders identically."""
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    width, height = image.size
    side = min(width, height)

    left = (width - side) // 2
    # Only bias upward when there's vertical slack to give (a portrait);
    # for an already-square or wide source this collapses to a plain crop.
    top = int((height - side) * CROP_TOP_BIAS)

    cropped = image.crop((left, top, left + side, top + side))
    return cropped.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)


async def download(client: httpx.AsyncClient, url: str, avatar_id: str) -> "tuple[str | None, str | None]":
    """Returns (local_path, failure_reason)."""
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None, f"image download HTTP {resp.status_code}"
        # Normalized to square jpg regardless of source format, so the UI
        # never has to deal with mixed aspect ratios or transparency.
        filename = f"{avatar_id}.jpg"
        to_square(resp.content).save(os.path.join(AVATAR_DIR, filename), "JPEG", quality=88)
        return f"/static/avatars/{filename}", None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


async def main() -> None:
    os.makedirs(AVATAR_DIR, exist_ok=True)

    results = []
    failures = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for i, entry in enumerate(ROSTER):
            if i > 0:
                await asyncio.sleep(REQUEST_DELAY)

            image_url, matched, reason = await resolve_image_url(client, entry)
            local_path = None
            if image_url:
                local_path, reason = await download(client, image_url, entry["id"])

            results.append(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "series": entry["series"],
                    "emoji": entry["emoji"],
                    "image": local_path,
                }
            )
            if local_path:
                print(f"OK   {entry['name']:10s} (AniList: {matched}) -> {local_path}")
            else:
                failures.append((entry["name"], reason))
                print(f"FAIL {entry['name']:10s}: {reason} (falls back to {entry['emoji']})")

    with open(OUTPUT_FILE, "w") as f:
        f.write('"""Auto-generated by build_avatar_options.py -- do not edit by hand.\n\n')
        f.write("Regenerate with: python3 build_avatar_options.py\n\n")
        f.write("`image` is a path to a file committed in this repo (static/avatars/), not\n")
        f.write("a remote URL -- the running game never calls an anime API. A None image\n")
        f.write('means that portrait could not be fetched; the app uses `emoji` instead.\n"""\n\n')
        f.write("AVATAR_OPTIONS = ")
        f.write(pprint.pformat(results, width=100, sort_dicts=False))
        f.write("\n")

    print()
    print("=" * 60)
    print(f"Portraits saved: {len(results) - len(failures)}/{len(ROSTER)} -> {AVATAR_DIR}/")
    if failures:
        print("Using emoji fallback for:")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
