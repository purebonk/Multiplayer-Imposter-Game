const screens = document.querySelectorAll(".screen");

const nameForm = document.getElementById("name-form");
const nameInput = document.getElementById("name-input");
const nameError = document.getElementById("name-error");

const homePlayerName = document.getElementById("home-player-name");
const createBtn = document.getElementById("create-btn");
const joinForm = document.getElementById("join-form");
const joinBtn = document.getElementById("join-btn");
const joinCodeInput = document.getElementById("join-code-input");
const joinError = document.getElementById("join-error");

const lobbyRoomCode = document.getElementById("lobby-room-code");
const lobbyPlayerList = document.getElementById("lobby-player-list");
const settingsHostControls = document.getElementById("settings-host-controls");
const settingsReadonly = document.getElementById("settings-readonly");
const timerSelect = document.getElementById("timer-select");
const difficultySelect = document.getElementById("difficulty-select");
const imposterCountSelect = document.getElementById("imposter-count-select");
const imposterHintCheckbox = document.getElementById("imposter-hint-checkbox");
const hostHint = document.getElementById("host-hint");
const startGameBtn = document.getElementById("start-game-btn");
const startingLoading = document.getElementById("starting-loading");

const gameStatusBar = document.getElementById("game-status-bar");
const turnHeading = document.getElementById("turn-heading");
const turnTimerDisplay = document.getElementById("turn-timer-display");
const roleBanner = document.getElementById("role-banner");
const hintForm = document.getElementById("hint-form");
const hintInput = document.getElementById("hint-input");
const hintSubmitBtn = document.getElementById("hint-submit-btn");
const hintsSoFar = document.getElementById("hints-so-far");

const gameStatusBarVoting = document.getElementById("game-status-bar-voting");
const hintsList = document.getElementById("hints-list");
const votingTimerDisplay = document.getElementById("voting-timer-display");
const votingPanel = document.getElementById("voting-panel");
const voteList = document.getElementById("vote-list");
const voteProgress = document.getElementById("vote-progress");
const spectatorNote = document.getElementById("spectator-note");

const revealText = document.getElementById("reveal-text");
const nextRoundLoading = document.getElementById("next-round-loading");
const characterDetailsBlock = document.getElementById("character-details-block");
const viewDetailsBtn = document.getElementById("view-details-btn");
const detailsLoading = document.getElementById("details-loading");
const detailsError = document.getElementById("details-error");
const detailsPanel = document.getElementById("details-panel");
const detailsImage = document.getElementById("details-image");
const detailsAbout = document.getElementById("details-about");
const newRoundBtn = document.getElementById("new-round-btn");
const newRoundLoading = document.getElementById("new-round-loading");

const MIN_PLAYERS = 3;
const MAX_IMPOSTERS = 3;
const DETAILS_FETCH_TIMEOUT_MS = 5000;

// One random ID per page load (per tab). This is what lets the server tell
// "this tab tried to join again" apart from "a different tab/device joined"
// — two independent WebSocket connections otherwise look identical to it.
const tabSessionId = crypto.randomUUID();

let socket = null;
let myName = "";
let myId = null;
let hostId = null;
let players = [];
let myRoomCode = "";
let timerSeconds = 30;
let difficulty = "easy";
let giveImposterHint = true;
let numImposters = 1;

// Captured once from game_started and reused every round — the character
// and role don't change between elimination rounds within the same game.
let myRole = null;
let myCharacter = null;
let myAnimeTitle = null;
let myHint = null;
let myTeammates = [];

let roundNumber = 0;
let remainingPlayers = [];

let turnCountdownInterval = null;
let votingCountdownInterval = null;

function showScreen(id) {
  for (const s of screens) s.hidden = s.id !== id;
}

function isBeforeRoomJoin() {
  return document.getElementById("screen-lobby").hidden
    && document.getElementById("screen-hints").hidden
    && document.getElementById("screen-voting").hidden
    && document.getElementById("screen-reveal").hidden;
}

function validImposterCounts(playerCount) {
  const options = [];
  for (let n = 1; n <= MAX_IMPOSTERS; n++) {
    if (n < playerCount / 2) options.push(n);
  }
  return options;
}

nameForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = nameInput.value.trim();
  nameError.textContent = "";
  if (!name) {
    nameError.textContent = "Enter a name first.";
    return;
  }
  myName = name;
  homePlayerName.textContent = myName;
  showScreen("screen-home");
});

function connectToRoom(code) {
  // Guards against a double-click (or any other path) opening a second
  // WebSocket while one is already live — the client-side half of
  // preventing duplicate joins; see main.py for the server-side half,
  // which is the part that actually can't be bypassed.
  if (socket && socket.readyState <= WebSocket.OPEN) return;

  myRoomCode = code;
  createBtn.disabled = true;
  joinBtn.disabled = true;

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(
    `${protocol}://${location.host}/ws/${code}?name=${encodeURIComponent(myName)}&session_id=${tabSessionId}`
  );

  socket.addEventListener("message", (event) => {
    handleMessage(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    createBtn.disabled = false;
    joinBtn.disabled = false;
    if (!isBeforeRoomJoin()) {
      alert("Disconnected from the room.");
    }
  });
}

createBtn.addEventListener("click", async () => {
  createBtn.disabled = true;
  const res = await fetch("/api/rooms", { method: "POST" });
  const data = await res.json();
  connectToRoom(data.room_code);
});

joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const code = joinCodeInput.value.trim().toUpperCase();
  joinError.textContent = "";

  if (!code) {
    joinError.textContent = "Enter a room code first.";
    return;
  }

  connectToRoom(code);
});

function handleMessage(data) {
  switch (data.type) {
    case "welcome":
      myId = data.player_id;
      hostId = data.host_id;
      players = data.players;
      timerSeconds = data.timer_seconds;
      difficulty = data.difficulty;
      giveImposterHint = data.give_imposter_hint;
      numImposters = data.num_imposters;
      renderLobby();
      showScreen("screen-lobby");
      break;
    case "player_joined":
    case "player_left":
      players = data.players;
      hostId = data.host_id;
      renderLobby();
      break;
    case "settings_updated":
      timerSeconds = data.timer_seconds;
      difficulty = data.difficulty;
      giveImposterHint = data.give_imposter_hint;
      numImposters = data.num_imposters;
      renderLobby();
      break;
    case "game_started":
      handleGameStarted(data);
      break;
    case "round_started":
      handleRoundStarted(data);
      break;
    case "turn_started":
      handleTurnStarted(data);
      break;
    case "hint_given":
      appendHintSoFar(data);
      break;
    case "hints_revealed":
      enterVotingScreen(data.hints, data.timer_seconds);
      break;
    case "vote_progress":
      voteProgress.textContent = `${data.voted_count}/${data.total} votes cast`;
      break;
    case "round_reveal":
      handleRoundReveal(data);
      break;
    case "error":
      startingLoading.hidden = true;
      newRoundLoading.hidden = true;
      startGameBtn.disabled = players.length < MIN_PLAYERS;
      newRoundBtn.disabled = false;
      if (isBeforeRoomJoin()) {
        joinError.textContent = data.message;
      } else {
        alert(data.message);
      }
      break;
  }
}

function renderLobby() {
  lobbyRoomCode.textContent = myRoomCode;
  lobbyPlayerList.innerHTML = "";
  for (const p of players) {
    const li = document.createElement("li");
    let label = p.name;
    if (p.id === hostId) label += " (host)";
    if (p.id === myId) label += " (you)";
    li.textContent = label;
    lobbyPlayerList.appendChild(li);
  }

  const isHost = myId === hostId;
  settingsHostControls.hidden = !isHost;
  settingsReadonly.hidden = isHost;
  if (isHost) {
    timerSelect.value = timerSeconds === null ? "none" : String(timerSeconds);
    difficultySelect.value = difficulty;
    imposterHintCheckbox.checked = giveImposterHint;

    const valid = new Set(validImposterCounts(players.length));
    for (const option of imposterCountSelect.options) {
      option.disabled = !valid.has(Number(option.value));
    }
    imposterCountSelect.value = String(numImposters);
  } else {
    const timerLabel = timerSeconds === null ? "No limit" : `${timerSeconds}s`;
    const hintLabel = giveImposterHint ? "on" : "off";
    settingsReadonly.textContent =
      `Timer: ${timerLabel} · Difficulty: ${difficulty} · Imposters: ${numImposters} · Imposter hint: ${hintLabel}`;
  }

  startGameBtn.hidden = !isHost;
  startGameBtn.disabled = players.length < MIN_PLAYERS;
  hostHint.textContent =
    isHost && players.length < MIN_PLAYERS
      ? `Need at least ${MIN_PLAYERS} players to start.`
      : "";
}

function sendSettingsUpdate() {
  if (myId !== hostId) return;
  const raw = timerSelect.value;
  const newTimerSeconds = raw === "none" ? null : parseInt(raw, 10);
  socket.send(
    JSON.stringify({
      type: "update_settings",
      timer_seconds: newTimerSeconds,
      difficulty: difficultySelect.value,
      give_imposter_hint: imposterHintCheckbox.checked,
      num_imposters: parseInt(imposterCountSelect.value, 10),
    })
  );
}

timerSelect.addEventListener("change", sendSettingsUpdate);
difficultySelect.addEventListener("change", sendSettingsUpdate);
imposterHintCheckbox.addEventListener("change", sendSettingsUpdate);
imposterCountSelect.addEventListener("change", sendSettingsUpdate);

startGameBtn.addEventListener("click", () => {
  startGameBtn.disabled = true;
  startingLoading.hidden = false;
  socket.send(JSON.stringify({ type: "start_game" }));
});

function handleGameStarted(data) {
  startingLoading.hidden = true;
  myRole = data.your_role;
  myCharacter = data.character;
  myAnimeTitle = data.anime_title || null;
  myHint = data.hint || null;
  myTeammates = data.teammates || [];
}

function roleBannerText() {
  if (myRole === "imposter") {
    const teammateText = myTeammates.length
      ? ` Your fellow imposter${myTeammates.length > 1 ? "s" : ""}: ${myTeammates.join(", ")}.`
      : "";
    const hintText = myHint
      ? ` Clue: ${myHint.role_hint}, genres: ${myHint.genres.join(", ")}.`
      : " No hint this round — bluff carefully!";
    return `You are the IMPOSTER.${hintText}${teammateText}`;
  }
  return `Character: ${myCharacter} (${myAnimeTitle})`;
}

function amEliminated() {
  return !remainingPlayers.some((p) => p.id === myId);
}

function handleRoundStarted(data) {
  startingLoading.hidden = true;
  nextRoundLoading.hidden = true;
  newRoundLoading.hidden = true;

  roundNumber = data.round_number;
  remainingPlayers = data.remaining_players;

  hintsSoFar.innerHTML = "";
  hintInput.value = "";
  // Locked by default until this player's own turn_started arrives — without
  // this, the input sits enabled for everyone in the gap between
  // round_started and the first turn_started broadcast, since nothing else
  // disables it yet.
  hintInput.disabled = true;
  hintSubmitBtn.disabled = true;

  const statusText = `Round ${roundNumber} · ${data.remaining_count} players remain`
    + (amEliminated() ? " · You were ejected — spectating" : "");
  gameStatusBar.textContent = statusText;
  gameStatusBarVoting.textContent = statusText;

  roleBanner.textContent = roleBannerText();

  showScreen("screen-hints");
}

function handleTurnStarted(data) {
  const isMyTurn = data.player_id === myId;

  turnHeading.textContent = isMyTurn
    ? `Your turn! (${data.turn_number}/${data.total_turns})`
    : `Waiting for ${data.player_name}... (${data.turn_number}/${data.total_turns})`;

  hintInput.disabled = !isMyTurn;
  hintSubmitBtn.disabled = !isMyTurn;
  if (isMyTurn) hintInput.focus();

  startTurnCountdown(data.timer_seconds);
}

function startTurnCountdown(seconds) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);

  if (seconds === null) {
    turnTimerDisplay.textContent = "No time limit";
    return;
  }

  let remaining = seconds;
  turnTimerDisplay.textContent = `${remaining}s remaining`;
  turnCountdownInterval = setInterval(() => {
    remaining -= 1;
    turnTimerDisplay.textContent = remaining > 0 ? `${remaining}s remaining` : "Time's up";
    if (remaining <= 0) clearInterval(turnCountdownInterval);
  }, 1000);
}

hintForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const hint = hintInput.value.trim();
  if (!hint || hintInput.disabled) return;
  socket.send(JSON.stringify({ type: "submit_hint", hint }));
  hintInput.value = "";
  hintInput.disabled = true;
  hintSubmitBtn.disabled = true;
});

function appendHintSoFar(data) {
  const li = document.createElement("li");
  li.textContent = `${data.name}: ${data.hint}`;
  hintsSoFar.appendChild(li);
}

function enterVotingScreen(hints, votingTimerSeconds) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);

  hintsList.innerHTML = "";
  for (const h of hints) {
    const li = document.createElement("li");
    li.textContent = `${h.name}: ${h.hint}`;
    hintsList.appendChild(li);
  }

  const eliminated = amEliminated();
  votingPanel.hidden = false;
  spectatorNote.hidden = !eliminated;
  voteList.innerHTML = "";
  if (!eliminated) {
    // The server is what actually blocks a self-vote or a vote for someone
    // already ejected (game.py submit_vote) — this is just not offering
    // buttons that would always get rejected.
    for (const p of remainingPlayers.filter((p) => p.id !== myId)) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.textContent = `Vote ${p.name}`;
      btn.addEventListener("click", () => {
        socket.send(JSON.stringify({ type: "submit_vote", target_id: p.id }));
      });
      li.appendChild(btn);
      voteList.appendChild(li);
    }
  }
  voteProgress.textContent = "";

  showScreen("screen-voting");
  startVotingCountdown(votingTimerSeconds);
}

function startVotingCountdown(seconds) {
  if (votingCountdownInterval) clearInterval(votingCountdownInterval);

  if (seconds === null) {
    votingTimerDisplay.textContent = "Discuss and vote — no time limit";
    return;
  }

  let remaining = seconds;
  votingTimerDisplay.textContent = `Discuss and vote — ${remaining}s remaining`;
  votingCountdownInterval = setInterval(() => {
    remaining -= 1;
    votingTimerDisplay.textContent =
      remaining > 0 ? `Discuss and vote — ${remaining}s remaining` : "Time's up";
    if (remaining <= 0) clearInterval(votingCountdownInterval);
  }, 1000);
}

let revealedCharacterName = null;

function handleRoundReveal(data) {
  if (votingCountdownInterval) clearInterval(votingCountdownInterval);

  let text;
  if (data.reason === "disconnect" && !data.ejected_id) {
    text = "A player disconnected, changing the balance of the game.";
  } else if (data.tie) {
    text = "The vote was tied — no one was ejected.";
  } else {
    text = `${data.ejected_name} was ejected — they were ${data.was_imposter ? "an IMPOSTER" : "not an imposter"}.`;
  }

  if (data.game_over) {
    const outcome = data.winner === "crew" ? "Crew wins!" : "The imposters win!";
    text += ` ${outcome} The imposters were: ${data.all_imposters.join(", ")}. The character was ${data.character} (${data.anime_title}).`;
    newRoundBtn.hidden = myId !== hostId;
    nextRoundLoading.hidden = true;

    // The character is only ever revealed at game-over, never mid-game, so
    // this feature only makes sense to offer here -- and it's reset fresh
    // for each new game-over, not carried over from a previous game.
    revealedCharacterName = data.character;
    characterDetailsBlock.hidden = false;
    viewDetailsBtn.hidden = false;
    viewDetailsBtn.disabled = false;
    detailsLoading.hidden = true;
    detailsError.hidden = true;
    detailsPanel.hidden = true;
  } else {
    text += " Next round starting soon...";
    newRoundBtn.hidden = true;
    nextRoundLoading.hidden = false;
    characterDetailsBlock.hidden = true;
  }

  revealText.textContent = text;
  showScreen("screen-reveal");
}

viewDetailsBtn.addEventListener("click", async () => {
  viewDetailsBtn.disabled = true;
  detailsError.hidden = true;
  detailsPanel.hidden = true;
  detailsLoading.hidden = false;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DETAILS_FETCH_TIMEOUT_MS);

  try {
    const res = await fetch(
      `/api/character-details?character=${encodeURIComponent(revealedCharacterName)}`,
      { signal: controller.signal }
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Couldn't load extra details right now.");
    }
    const details = await res.json();
    detailsLoading.hidden = true;
    if (details.image_url) {
      detailsImage.src = details.image_url;
      detailsImage.hidden = false;
    } else {
      detailsImage.hidden = true;
    }
    detailsAbout.textContent = details.about;
    detailsPanel.hidden = false;
  } catch (err) {
    detailsLoading.hidden = true;
    const timedOut = err.name === "AbortError";
    detailsError.textContent = timedOut
      ? "Couldn't load extra details right now — the request took too long. Jikan (the anime database) might be temporarily unavailable."
      : "Couldn't load extra details right now — Jikan (the anime database) might be temporarily unavailable.";
    detailsError.hidden = false;
  } finally {
    clearTimeout(timeoutId);
    viewDetailsBtn.disabled = false;
  }
});

newRoundBtn.addEventListener("click", () => {
  newRoundBtn.disabled = true;
  newRoundLoading.hidden = false;
  socket.send(JSON.stringify({ type: "new_round" }));
});
