"""Live Jikan lookup for the post-game "View Character Details" feature.

This is the ONLY place in the entire app that calls Jikan at runtime.
game.py / rooms.py / characters.py never import this module, and this
module never touches Room or WebSocket state — a failure here can't affect
an in-progress or future round, only this one optional lookup.
"""

import httpx

JIKAN_BASE_URL = "https://api.jikan.moe/v4"
REQUEST_TIMEOUT = 5.0


async def fetch_character_details(character_name: str) -> dict:
    """Raises on any failure (timeout, rate limit, no match, Jikan down,
    etc.) — the caller turns that into one clear error response. No
    retries: this is a manual, user-triggered, decorative lookup, not
    something worth stalling the page on."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        search_resp = await client.get(
            f"{JIKAN_BASE_URL}/characters", params={"q": character_name, "limit": 1}
        )
        search_resp.raise_for_status()
        results = search_resp.json()["data"]
        if not results:
            raise LookupError(f"No Jikan match for '{character_name}'")

        char_id = results[0]["mal_id"]
        detail_resp = await client.get(f"{JIKAN_BASE_URL}/characters/{char_id}/full")
        detail_resp.raise_for_status()
        data = detail_resp.json()["data"]

    return {
        "name": data.get("name", character_name),
        "image_url": (data.get("images") or {}).get("jpg", {}).get("image_url"),
        "about": data.get("about") or "No description available.",
    }
