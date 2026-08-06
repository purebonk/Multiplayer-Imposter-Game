"""Quick-chat reactions -- cosmetic social feature, fully isolated from gameplay.

This module never imports game.py and never touches turn order, timers,
votes, hints, or room state. The only thing it mutates is the per-player
rate-limit counters on Player. A bug in here can spam or drop bubbles; it
cannot affect who wins.

Reactions are self-expression: a bubble always renders on the sender's own
card. There is deliberately no targeting in the protocol -- even the
"I think it's X" accusation is just text the sender says about themselves
being suspicious of X, so there is no target field to spoof or mis-route.

Rate limiting is server-enforced, matching every other rule in this app:
the client's disabled button is politeness, this is the actual limit.
"""

import time

from limits import clean_text
from rooms import Room, player_summary

REACTION_EMOJIS = ("👀", "🤔", "😂", "💀", "🔥")

# Casual/meme tone to match how the game actually gets played. The first
# four are fixed by product direction; the rest are in the same register.
PRESET_PHRASES = (
    "sus",
    "sybau",
    "peep this lame ahh",
    "sonion",
    "nah that's crazy",
    "im cooked",
    "trust",
    "yall are lost",
)

ACCUSATION_TEMPLATE = "I think it's {name}"

# Short enough that this stays a quick reaction rather than turning into a
# full chat client -- long messages would also overflow the bubble.
MAX_FREE_TEXT = 40

# Tuned for a casual party game, not adversarial abuse. 1.2s is slower than
# a person can meaningfully "react" to distinct moments but fast enough that
# normal enthusiastic use never hits it.
COOLDOWN_SECONDS = 1.2
# Three blocked attempts means they're holding/hammering the button, not
# just double-tapping once -- a single accidental double-click costs nothing.
SPAM_STRIKES = 3
# Long enough to break the spamming rhythm, short enough that a kid who got
# overexcited isn't sidelined for a whole round.
LOCKOUT_SECONDS = 8.0


def _resolve(room: Room, kind: str, value: str) -> "tuple[str, bool] | None":
    """Turns a (kind, value) pair into (bubble_text, is_emoji), or None if it
    isn't something we're willing to broadcast. Every branch validates against
    server-side data -- the client can't invent an emoji, a phrase, or a
    player name."""
    if kind == "emoji":
        return (value, True) if value in REACTION_EMOJIS else None

    if kind == "phrase":
        return (value, False) if value in PRESET_PHRASES else None

    if kind == "accusation":
        # value is a player_id, not a name: the server looks up the current
        # name itself so a client can't put arbitrary words after "I think
        # it's" or accuse someone who isn't in the room.
        accused = room.players.get(value)
        if accused is None:
            return None
        return ACCUSATION_TEMPLATE.format(name=accused.name), False

    if kind == "free":
        # clean_text also strips zero-width/control characters, so a bubble
        # can't be used to render invisible payloads or fake another name.
        text = clean_text(value, MAX_FREE_TEXT)
        if not text:
            return None
        return text, False

    return None


async def send_reaction(room: Room, player_id: str, kind: str, value: str) -> None:
    sender = room.players.get(player_id)
    if sender is None:
        return

    resolved = _resolve(room, kind, value)
    if resolved is None:
        return
    text, is_emoji = resolved

    now = time.monotonic()

    # One cooldown covers every reaction kind -- switching between emoji,
    # phrases, accusations and free text must not be a way around the limit.
    if now < sender.reaction_locked_until:
        await room.send_to(
            player_id,
            {
                "type": "reaction_blocked",
                "retry_in": round(sender.reaction_locked_until - now, 1),
                "reason": "timeout",
            },
        )
        return

    if now - sender.last_reaction_at < COOLDOWN_SECONDS:
        sender.blocked_reaction_attempts += 1
        if sender.blocked_reaction_attempts >= SPAM_STRIKES:
            sender.reaction_locked_until = now + LOCKOUT_SECONDS
            sender.blocked_reaction_attempts = 0
            await room.send_to(
                player_id,
                {"type": "reaction_blocked", "retry_in": LOCKOUT_SECONDS, "reason": "timeout"},
            )
        else:
            await room.send_to(
                player_id,
                {
                    "type": "reaction_blocked",
                    "retry_in": round(COOLDOWN_SECONDS - (now - sender.last_reaction_at), 1),
                    "reason": "cooldown",
                },
            )
        return

    # Clean send: reset strikes so past near-misses don't accumulate into a
    # lockout for someone who's actually pacing themselves fine.
    sender.last_reaction_at = now
    sender.blocked_reaction_attempts = 0

    await room.broadcast(
        {
            "type": "reaction",
            "from_id": player_id,
            "from_name": sender.name,
            "from_avatar": player_summary(sender)["avatar_emoji"],
            "text": text,
            "is_emoji": is_emoji,
        }
    )
