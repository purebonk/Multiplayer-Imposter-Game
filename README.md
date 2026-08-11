# Anime Imposter

A real-time multiplayer social-deduction party game. Everyone is given the same
anime character — except the imposters, who are given nothing. Each round every
player says one word about their character, then the group votes on who was
faking it.

Built with FastAPI, native WebSockets, and vanilla JavaScript. No frontend
framework, no database, no build step.

```
┌─ Round 2 of 3 · 4 players remain ──────────────────────┐
│  🎴 Character: Gojo Satoru (Jujutsu Kaisen)            │
│                                                        │
│  Aaron  "blindfold"    Bea  "strongest"                │
│  Cal    "teacher"      Dee  "infinity"                 │
└────────────────────────────────────────────────────────┘
```

---

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://localhost:8000>. Open a few more tabs to play against yourself —
each tab is an independent player.

Needs 3 players to start.

### With Docker

```bash
docker build -t anime-imposter .
docker run -p 8000:8000 anime-imposter
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

194 tests, about 11 seconds, no network and no server required. See
[`tests/README.md`](tests/README.md) for how the harness works.

---

## How it works

```
Browser                          Server
   │                                │
   │  POST /api/rooms               │   in-memory RoomManager
   │ ─────────────────────────────► │   { "A7K2": Room(...) }
   │        { "room_code": "A7K2" } │
   │ ◄───────────────────────────── │
   │                                │
   │  ws://…/ws/A7K2?name=…         │   one asyncio task per connection
   │ ═════════════════════════════► │
   │                                │
   │  { "type": "submit_hint" … }   │   game.py mutates Room, then
   │ ─────────────────────────────► │   broadcasts to every socket
   │  { "type": "turn_started" … }  │
   │ ◄───────────────────────────── │
```

| File | Responsibility |
|---|---|
| `main.py` | HTTP + WebSocket endpoints, the per-connection message loop, reconnect and departure orchestration |
| `game.py` | The state machine: roles, turn order, voting, win conditions |
| `rooms.py` | `Room` / `Player` data and the room registry |
| `characters.py` | Character selection and difficulty tiering (pure, in-memory) |
| `limits.py` | Rate limiting and input sanitisation |
| `reactions.py` | Emote/quick-chat, deliberately isolated from game state |
| `static/` | The entire frontend — three files, no build |

State machine: `LOBBY → HINTS → VOTING → (GUESSING) → REVEAL`, looping back to
`HINTS` for the next elimination round until a side wins or the round cap hits.

---

## Design decisions

These are the calls that shaped the codebase, and the reasoning behind them.

**The server is the only source of truth.** Every rule — whose turn it is, who
may vote, who may change settings, which anime the pool draws from — is
enforced server-side. The client disabling a button is a convenience, never a
control. Tests assert this by calling `game.py` directly and confirming an
out-of-turn hint or a non-host settings change is refused.

**The imposter's client never receives the character.** Not hidden in the UI —
*absent from the payload*. A blind imposter's `game_started` message has no
`character_info` key at all, so there is no field for a later bug to populate.
The reconnect snapshot is a second place it could leak and is tested
separately. This is the constraint the whole protocol is designed around.

**Gameplay makes no third-party API calls.** An earlier version fetched
characters live from Jikan (MyAnimeList) and lost entire evenings to their
outages. Character data is now a committed, hand-curated file — 67 anime, 617
characters — and starting a round is instant and cannot fail. The one live
lookup left is an optional "learn more about this character" panel, isolated in
its own module that imports no game state. During development Jikan was down
often enough that this split kept proving itself.

**Difficulty is recognisability, not narrative importance.** MyAnimeList labels
Vegeta, Sasuke and Mikasa "Supporting", which makes its role field useless as a
difficulty signal. Each anime instead carries a `reach` (how far the show
travelled outside anime fandom) and each character a `prominence` (how central
they are within it); a matrix combines them into three tiers. Keeping the axes
separate makes every placement auditable and lets a whole show be re-tiered
with one edit.

**A dropped player keeps their seat, but the game keeps moving.** Refreshing
mid-game restores your exact seat, role and round state via a
`secrets.compare_digest`-checked reconnect token. The grace window is purely
about identity — it never pauses a timer, because pausing the game would let
anyone stall it by closing a laptop.

**Joining mid-game makes you a spectator, not a rejection.** Late arrivals hold
a real seat, watch the round, and are dealt in automatically at the next game.
They are excluded from `remaining_ids()`, and since turn order, vote tallies
and both win conditions all derive from that one list, they cannot perturb any
of it.

---

## Things that went wrong, and what fixed them

The bugs worth writing down, because each one taught a general lesson.

**A timer task that cancelled itself.** Resolving a vote cleared the room's
timers — including the very task that was running the resolution.
`Task.cancel()` on the running task is not a no-op: it raises `CancelledError`
at the next suspension point, so the round reveal broadcast was torn up
partway through and the next round never started. A live game would strand on
the reveal screen forever. Fixed with a current-task-safe cancel helper, plus a
watchdog that force-advances the round if the transition never completes.
Regression-tested by reintroducing the bug and confirming the test fails.

**`display` beating the `hidden` attribute.** `[hidden] { display: none }` is
specificity (0,1,0), so a bare `.card { display: flex }` ties it — and author
CSS wins ties. An unscoped `display` therefore silently cancels the hidden
attribute and renders an element the JS believes it hid. This shipped five
separate times before `tests/test_css_hidden.py` started parsing the stylesheet
and failing on any unscoped rule.

**Valid JSON that wasn't an object.** `[1,2,3]` parses fine, reached `.get()`,
raised `AttributeError`, and killed the connection handler — leaving a ghost
player nothing ever cleaned up. Cleanup moved into a `finally` block, because a
real disconnect does not reliably surface as `WebSocketDisconnect`.

**A vacuous identity check.** `if (myId !== hostId) return;` reads as "am I the
host", but both are `null` at boot, so it passes. A feature fired before the
socket existed and silently did nothing.

**Silent failure on the very first click.** "Create a Room" disabled its button
and assumed the request succeeded. Every failure — rate limit, capacity,
offline — looked identical: a greyed-out button and nothing else. Found by
playtesting, not by tests.

---

## Configuration

Everything is environment-driven with working defaults; see
[`.env.example`](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `ALLOWED_ORIGINS` | localhost | CORS + WebSocket origin check |
| `TRUST_PROXY` | `false` | Only trust `X-Forwarded-For` behind a known proxy |
| `MAX_ROOMS` | `300` | Global ceiling; excess returns 503 |
| `RECONNECT_GRACE_SECONDS` | `25` | How long a dropped seat is held |

Rate limits are sized for **a household sharing one IP**, not one person —
anything that only needs to stop a single abusive client is enforced
per-connection instead.

---

## Known gaps

- Rooms live in process memory, so a restart drops them and it will not scale
  past one instance. Correct for the use case; Redis would be the next step.
- Browser-level tests (animations, the CSS specificity sweep) exist but are not
  in the committed suite — they need a real browser and would make `pytest`
  slow and environment-dependent.
- Character data is hand-curated from my own knowledge. Entries I was unsure
  about are flagged in comments rather than presented with false confidence.
