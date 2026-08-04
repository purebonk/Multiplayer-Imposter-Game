const screens = document.querySelectorAll(".screen");

const createBtn = document.getElementById("create-btn");
const roomCodeDisplay = document.getElementById("room-code-display");
const joinBtn = document.getElementById("join-btn");
const joinCodeInput = document.getElementById("join-code-input");
const joinNameInput = document.getElementById("join-name-input");
const joinError = document.getElementById("join-error");

const lobbyRoomCode = document.getElementById("lobby-room-code");
const lobbyPlayerList = document.getElementById("lobby-player-list");
const settingsHostControls = document.getElementById("settings-host-controls");
const settingsReadonly = document.getElementById("settings-readonly");
const timerSelect = document.getElementById("timer-select");
const difficultySelect = document.getElementById("difficulty-select");
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

let socket = null;
let myId = null;
let hostId = null;
let players = [];
let myRoomCode = "";
let timerSeconds = 30;
let difficulty = "easy";

let turnCountdownInterval = null;
let discussionInterval = null;

function showScreen(id) {
  for (const s of screens) s.hidden = s.id !== id;
}

function isOnHomeScreen() {
  return !document.getElementById("screen-home").hidden;
}

createBtn.addEventListener("click", async () => {
  const res = await fetch("/api/rooms", { method: "POST" });
  const data = await res.json();
  roomCodeDisplay.textContent = `Room code: ${data.room_code}`;
  joinCodeInput.value = data.room_code;
});

joinBtn.addEventListener("click", () => {
  const code = joinCodeInput.value.trim().toUpperCase();
  const name = joinNameInput.value.trim() || "Player";
  joinError.textContent = "";

  if (!code) {
    joinError.textContent = "Enter a room code first.";
    return;
  }

  myRoomCode = code;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(
    `${protocol}://${location.host}/ws/${code}?name=${encodeURIComponent(name)}`
  );

  socket.addEventListener("message", (event) => {
    handleMessage(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    if (!isOnHomeScreen()) {
      alert("Disconnected from the room.");
    }
  });
});

function handleMessage(data) {
  switch (data.type) {
    case "welcome":
      myId = data.player_id;
      hostId = data.host_id;
      players = data.players;
      timerSeconds = data.timer_seconds;
      difficulty = data.difficulty;
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
      if (isOnHomeScreen()) {
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
  } else {
    const timerLabel = timerSeconds === null ? "No limit" : `${timerSeconds}s`;
    settingsReadonly.textContent = `Timer: ${timerLabel} · Difficulty: ${difficulty}`;
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
    })
  );
}

timerSelect.addEventListener("change", sendSettingsUpdate);
difficultySelect.addEventListener("change", sendSettingsUpdate);

startGameBtn.addEventListener("click", () => {
  socket.send(JSON.stringify({ type: "start_game" }));
});

function enterHintsScreen(data) {
  hintsSoFar.innerHTML = "";
  hintInput.value = "";

  if (data.your_role === "imposter") {
    const hint = data.hint;
    roleBanner.textContent = `You are the IMPOSTER. Clue: ${hint.role_hint}, genres: ${hint.genres.join(", ")}. Bluff a one-word hint on your turn!`;
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
  for (const p of players) {
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
