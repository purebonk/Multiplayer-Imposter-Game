# Tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

That's it — `pytest` from the repo root. ~117 tests, about 7 seconds, no network
and no server required.

## Why it's structured this way

The suite drives the **real** game logic (`game.py`, `rooms.py`, `main.py`) with
a fake WebSocket standing in for a live socket. Nothing here starts a server or
binds a port.

That works because of two decisions made earlier in the project rather than for
the tests' benefit:

- `characters.py` is a pure in-memory lookup, so starting a round never touches
  the network. The one live-API feature (post-game "View Character Details")
  lives in its own module and isn't part of gameplay.
- The server is authoritative for every rule, so the interesting behaviour is
  reachable without a browser.

`FakeWebSocket` (in `conftest.py`) records what the server sent and can also
script what the client sends back. When its inbox drains it raises
`WebSocketDisconnect` — exactly how a real client hanging up looks — which lets
`main._run_session` be driven to completion against its actual code instead of
a reimplementation of it.

Two details that matter:

- `send_json` awaits `asyncio.sleep(0)`. A real socket send yields to the event
  loop, and several bugs guarded against here (a timer task cancelling itself)
  only surface at a genuine suspension point. A fake that never yielded would
  make those tests pass for the wrong reason.
- `fast_transitions` shrinks `ROUND_TRANSITION_DELAY` and `ROUND_ADVANCE_GRACE`
  from 4s/3s to 50ms. Same code paths, nothing stubbed — only the durations
  change, so the suite isn't mostly spent asleep.

## Files

| File | Covers |
|---|---|
| `test_voting.py` | Vote resolution, round transitions, skip-vote semantics, win conditions, the round cap |
| `test_turns.py` | Turn-order enforcement, mid-round disconnects, hint sanitisation |
| `test_settings.py` | Host authorization, settings validation, the between-games lobby, imposter secrecy |
| `test_guess_phase.py` | The ejected imposter's last-chance guess and its character picker |
| `test_reconnect.py` | Reconnect tokens, the grace window, departure cleanup, snapshot secrecy |
| `test_input_validation.py` | The WebSocket message loop against malformed/hostile input, `clean_text` |
| `test_character_pool.py` | Hand-curated character data integrity |

## Regression tests

Several tests exist because the bug they describe actually shipped. Each was
verified by reintroducing the original bug and confirming the test fails:

- **The tie-vote freeze.** `_voting_timeout` ran as a task, and resolving the
  vote cleared the room's timers — cancelling the task it was running inside.
  `Task.cancel()` on the running task isn't a no-op; it raises `CancelledError`
  at the next suspension point, so the next round never started and the reveal
  broadcast was torn up mid-loop. Only the *timeout* path triggered it, which is
  why it looked intermittent.
- **The round-advance watchdog.** Defence in depth for the above: a stranded
  reveal screen is the worst failure mode in the game, so a second task forces
  the round forward if the first never completes.
- **The round cap.** Crew running out of rounds is itself a loss, or a stalling
  imposter could survive on ties forever.
- **Settings frozen for the room's lifetime.** `REVEAL` had no exit except
  starting another round, so after a game ended the room could never reach the
  lobby again and settings couldn't be changed without everyone leaving.
- **`[1,2,3]` killing a connection handler.** Valid JSON that isn't an object
  reached `.get()`, raised `AttributeError`, and left a ghost player nothing
  ever cleaned up.
- **Imposter secrecy.** The server never sends the imposter the real character.
  The reconnect snapshot is a second place it could leak, and an easy one to
  forget, so it's asserted separately.

## Notes

- Tests resolve roles from `room.imposter_ids` rather than assuming an index,
  since imposter assignment is random. The RNG is also seeded per test so a
  failure is reproducible.
- `pytest.ini` sets `asyncio_mode = auto`, so `async def test_...` works without
  decorating each test.
