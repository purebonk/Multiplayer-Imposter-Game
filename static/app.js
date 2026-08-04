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
const imposterHintCheckbox = document.getElementById("imposter-hint-checkbox");
const hostHint = document.getElementById("host-hint");
const startGameBtn = document.getElementById("start-game-btn");

const turnHeading = document.getElementById("turn-heading");
const turnTimerDisplay = document.getElementById("turn-timer-display");
const roleBanner = document.getElementById("role-banner");
const hintInput = document.getElementById("hint-input");
const hintSubmitBtn = document.getElementById("hint-submit-btn");
const hintsSoFar = document.getElementById("hints-so-far");

const hintsList = document.getElementById("hints-list");
const discussionTimerDisplay = document.getElementById("discussion-timer-display");
const votingPanel = document.getElementById("voting-panel");
const voteList = document.getElementById("vote-list");
const voteProgress = document.getElementById("vote-progress");

const revealText = document.getElementById("reveal-text");
const newRoundBtn = document.getElementById("new-round-btn");

const MIN_PLAYERS = 3;
const DISCUSSION_SECONDS = 20;

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

let turnCountdownInterval = null;
let discussionInterval = null;

function showScreen(id) {
  for (const s of screens) s.hidden = s.id !== id;
}

function isBeforeRoomJoin() {
  return document.getElementById("screen-lobby").hidden
    && document.getElementById("screen-hints").hidden
    && document.getElementById("screen-voting").hidden
    && document.getElementById("screen-reveal").hidden;
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
      renderLobby();
      break;
    case "game_started":
      enterHintsScreen(data);
      break;
    case "turn_started":
      handleTurnStarted(data);
      break;
    case "hint_given":
      appendHintSoFar(data);
      break;
    case "hints_revealed":
      enterVotingScreen(data.hints);
      break;
    case "vote_progress":
      voteProgress.textContent = `${data.voted_count}/${data.total} votes cast`;
      break;
    case "round_reveal":
      enterRevealScreen(data);
      break;
    case "error":
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
  } else {
    const timerLabel = timerSeconds === null ? "No limit" : `${timerSeconds}s`;
    const hintLabel = giveImposterHint ? "on" : "off";
    settingsReadonly.textContent = `Timer: ${timerLabel} · Difficulty: ${difficulty} · Imposter hint: ${hintLabel}`;
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
    })
  );
}

timerSelect.addEventListener("change", sendSettingsUpdate);
difficultySelect.addEventListener("change", sendSettingsUpdate);
imposterHintCheckbox.addEventListener("change", sendSettingsUpdate);

startGameBtn.addEventListener("click", () => {
  socket.send(JSON.stringify({ type: "start_game" }));
});

function enterHintsScreen(data) {
  hintsSoFar.innerHTML = "";
  hintInput.value = "";
  // Locked by default until this player's own turn_started arrives — without
  // this, the input sits enabled for everyone in the gap between
  // game_started and the first turn_started broadcast, since nothing else
  // disables it. The server always rejected an out-of-turn submit_hint, but
  // the input looking clickable for everyone is what actually created the
  // "why is it everyone's turn at once" confusion.
  hintInput.disabled = true;
  hintSubmitBtn.disabled = true;

  if (data.your_role === "imposter") {
    if (data.hint) {
      roleBanner.textContent = `You are the IMPOSTER. Clue: ${data.hint.role_hint}, genres: ${data.hint.genres.join(", ")}. Bluff a one-word hint on your turn!`;
    } else {
      roleBanner.textContent = "You are the IMPOSTER. No hint this round — bluff carefully!";
    }
  } else {
    roleBanner.textContent = `Character: ${data.character} (${data.anime_title})`;
  }

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

hintSubmitBtn.addEventListener("click", () => {
  const hint = hintInput.value.trim();
  if (!hint) return;
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

function enterVotingScreen(hints) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);

  hintsList.innerHTML = "";
  for (const h of hints) {
    const li = document.createElement("li");
    li.textContent = `${h.name}: ${h.hint}`;
    hintsList.appendChild(li);
  }

  votingPanel.hidden = true;
  voteList.innerHTML = "";
  // The server is what actually blocks a self-vote (game.py submit_vote) —
  // this is just not offering a button that would always get rejected.
  for (const p of players.filter((p) => p.id !== myId)) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.textContent = `Vote ${p.name}`;
    btn.addEventListener("click", () => {
      socket.send(JSON.stringify({ type: "submit_vote", target_id: p.id }));
    });
    li.appendChild(btn);
    voteList.appendChild(li);
  }
  voteProgress.textContent = "";

  showScreen("screen-voting");
  startDiscussionCountdown();
}

function startDiscussionCountdown() {
  if (discussionInterval) clearInterval(discussionInterval);

  let remaining = DISCUSSION_SECONDS;
  discussionTimerDisplay.textContent = `Discuss! Voting opens in ${remaining}s`;
  discussionInterval = setInterval(() => {
    remaining -= 1;
    if (remaining > 0) {
      discussionTimerDisplay.textContent = `Discuss! Voting opens in ${remaining}s`;
    } else {
      discussionTimerDisplay.textContent = "Voting is open";
      votingPanel.hidden = false;
      clearInterval(discussionInterval);
    }
  }, 1000);
}

function enterRevealScreen(data) {
  if (discussionInterval) clearInterval(discussionInterval);

  revealText.textContent = `The imposter was ${data.imposter_name}. The character was ${data.character} (${data.anime_title}).`;
  newRoundBtn.hidden = myId !== hostId;

  showScreen("screen-reveal");
}

newRoundBtn.addEventListener("click", () => {
  socket.send(JSON.stringify({ type: "new_round" }));
});
