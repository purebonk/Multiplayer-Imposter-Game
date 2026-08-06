const screens = document.querySelectorAll(".screen");

const nameForm = document.getElementById("name-form");
const nameInput = document.getElementById("name-input");
const nameError = document.getElementById("name-error");
const avatarPicker = document.getElementById("avatar-picker");
const avatarNameLabel = document.getElementById("avatar-name-label");

const homePlayerName = document.getElementById("home-player-name");
const createBtn = document.getElementById("create-btn");
const joinForm = document.getElementById("join-form");
const joinBtn = document.getElementById("join-btn");
const joinCodeInput = document.getElementById("join-code-input");
const joinError = document.getElementById("join-error");

const lobbyRoomCode = document.getElementById("lobby-room-code");
const copyLinkBtn = document.getElementById("copy-link-btn");
const previousRoundsCard = document.getElementById("previous-rounds-card");
const previousRounds = document.getElementById("previous-rounds");
const lobbyPlayerCards = document.getElementById("lobby-player-cards");
const settingsHostControls = document.getElementById("settings-host-controls");
const settingsReadonly = document.getElementById("settings-readonly");
const timerSelect = document.getElementById("timer-select");
const difficultySelect = document.getElementById("difficulty-select");
const imposterCountSelect = document.getElementById("imposter-count-select");
const imposterHintCheckbox = document.getElementById("imposter-hint-checkbox");
const imposterModeSelect = document.getElementById("imposter-mode-select");
const lastChanceCheckbox = document.getElementById("last-chance-checkbox");

const guessTitle = document.getElementById("guess-title");
const guessSub = document.getElementById("guess-sub");
const guessTimer = document.getElementById("guess-timer");
const guessForm = document.getElementById("guess-form");
const guessInput = document.getElementById("guess-input");
const guessSubmitBtn = document.getElementById("guess-submit-btn");
const guessWaiting = document.getElementById("guess-waiting");
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
const hintsPlayerCards = document.getElementById("hints-player-cards");

const gameStatusBarVoting = document.getElementById("game-status-bar-voting");
const votingPlayerCards = document.getElementById("voting-player-cards");
const votingTimerDisplay = document.getElementById("voting-timer-display");
const votingPanel = document.getElementById("voting-panel");
const voteList = document.getElementById("vote-list");
const voteProgress = document.getElementById("vote-progress");
const spectatorNote = document.getElementById("spectator-note");

const outcomeBanner = document.getElementById("outcome-banner");
const outcomeEmoji = document.getElementById("outcome-emoji");
const outcomeTitle = document.getElementById("outcome-title");
const outcomeSub = document.getElementById("outcome-sub");
const revealRoles = document.getElementById("reveal-roles");
const revealRolesHeading = document.getElementById("reveal-roles-heading");
const imposterCards = document.getElementById("imposter-cards");
const revealCharacter = document.getElementById("reveal-character");
const revealCharacterName = document.getElementById("reveal-character-name");
const revealCharacterAnime = document.getElementById("reveal-character-anime");
const roundHistoryCard = document.getElementById("round-history-card");
const roundHistory = document.getElementById("round-history");
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
const REACTION_BUBBLE_MS = 2000;
const REACTION_TEXT_BUBBLE_MS = 3400; // text needs longer on screen than an emoji
const TIMER_URGENT_AT = 5; // seconds left when the countdown starts pulsing

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
let imposterMode = "blind";
let lastChanceGuess = true;
let myDecoyMode = false;
let guessCountdownInterval = null;

let avatarRoster = [];
let myAvatarId = null;

// Fetched from the server so the picker can only ever offer things the
// server will actually accept. Falls back to a minimal emoji-only set if
// the request fails, so the feature degrades instead of disappearing.
let reactionOptions = { emojis: ["👀", "🤔", "😂", "💀", "🔥"], phrases: [], maxFreeText: 40 };

/* ------------------------- remembered identity ------------------------- */
// Declared up here because restoreRememberedName() runs during the initial
// synchronous pass; consts declared further down would still be in their
// temporal dead zone and throw.
const STORAGE_NAME = "animeImposter.name";
const STORAGE_AVATAR = "animeImposter.avatarId";
// Everything needed to reclaim a seat after a refresh. The token is the only
// part the server actually trusts.
const STORAGE_SESSION = "animeImposter.session";

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // private mode / storage disabled — just don't remember
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* non-fatal */
  }
}

function storageRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* non-fatal */
  }
}

function saveSession(roomCode, token) {
  storageSet(STORAGE_SESSION, JSON.stringify({ room_code: roomCode, token }));
}

function loadSession() {
  try {
    const parsed = JSON.parse(storageGet(STORAGE_SESSION) || "null");
    return parsed && parsed.room_code && parsed.token ? parsed : null;
  } catch {
    return null;
  }
}

function clearSession() {
  storageRemove(STORAGE_SESSION);
}

// Captured once from game_started and reused every round — the character
// and role don't change between elimination rounds within the same game.
let myRole = null;
let myCharacter = null;
let myAnimeTitle = null;
let myHint = null;
let myTeammates = [];

let roundNumber = 0;
let maxRounds = 0;
let remainingPlayers = [];
let hintsByPlayerId = {};
let currentTurnPlayerId = null;

// Accumulated client-side across the whole game so the end screen can show a
// "how it played out" recap — the argument-settling part of a social
// deduction game. Needs no server support; it's all broadcast already.
let roundLog = [];

let turnCountdownInterval = null;
let votingCountdownInterval = null;
let reactionCooldownUntil = 0;
// Suppresses the "connection lost" alert when the player chose to leave.
let leavingDeliberately = false;
// Resolved by the `reconnected` message so attemptReconnect() knows it worked.
let reconnectResolver = null;

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

/* ------------------------------ avatars ------------------------------ */

function avatarNode(player, className) {
  // Server sends a resolved image path when the portrait exists and an
  // emoji when it doesn't, so the client never has to know the roster.
  if (player.avatar_image) {
    const img = document.createElement("img");
    img.className = className;
    img.src = player.avatar_image;
    img.alt = player.avatar_name || "";
    return img;
  }
  const span = document.createElement("span");
  span.className = `${className}-emoji`;
  span.textContent = player.avatar_emoji || "🎭";
  return span;
}

async function loadAvatarRoster() {
  try {
    const res = await fetch("/api/avatars");
    const data = await res.json();
    avatarRoster = data.avatars || [];
  } catch {
    avatarRoster = [];
  }
  if (!avatarRoster.length) return;

  // Reuse last session's pick if it's still a valid character, otherwise
  // random so a first-time player never stares at an empty selection.
  const remembered = storageGet(STORAGE_AVATAR);
  myAvatarId = avatarRoster.some((a) => a.id === remembered)
    ? remembered
    : avatarRoster[Math.floor(Math.random() * avatarRoster.length)].id;
  renderAvatarPicker();
}

function restoreRememberedName() {
  const remembered = storageGet(STORAGE_NAME);
  if (remembered) nameInput.value = remembered;
}

function renderAvatarPicker() {
  avatarPicker.innerHTML = "";
  for (const entry of avatarRoster) {
    const btn = document.createElement("button");
    btn.type = "button"; // must not submit the name form
    btn.className = "avatar-choice";
    btn.title = `${entry.name} — ${entry.series}`;
    btn.dataset.avatarId = entry.id;

    if (entry.image) {
      const img = document.createElement("img");
      img.src = entry.image;
      img.alt = entry.name;
      btn.appendChild(img);
    } else {
      const span = document.createElement("span");
      span.className = "fallback-emoji";
      span.textContent = entry.emoji;
      btn.appendChild(span);
    }

    btn.addEventListener("click", () => selectAvatar(entry.id));
    avatarPicker.appendChild(btn);
  }
  selectAvatar(myAvatarId);
}

function selectAvatar(avatarId) {
  myAvatarId = avatarId;
  for (const btn of avatarPicker.children) {
    btn.classList.toggle("avatar-selected", btn.dataset.avatarId === avatarId);
  }
  const entry = avatarRoster.find((a) => a.id === avatarId);
  avatarNameLabel.textContent = entry ? `${entry.name} · ${entry.series}` : "";
}

async function loadReactionOptions() {
  try {
    const res = await fetch("/api/reaction-options");
    const data = await res.json();
    reactionOptions = {
      emojis: data.emojis || [],
      phrases: data.phrases || [],
      maxFreeText: data.max_free_text || 40,
    };
  } catch {
    // keep the built-in fallback
  }
}

/* ---------------------------- leave room ---------------------------- */

function leaveRoom() {
  leavingDeliberately = true;
  // Clear first: if the socket closes before the server's ack lands, a
  // refresh must not try to crawl back into a room they chose to leave.
  clearSession();
  try {
    socket?.send(JSON.stringify({ type: "leave_room" }));
    socket?.close();
  } catch {
    /* already gone */
  }
  socket = null;

  // Reset per-game state so the next join starts clean.
  players = [];
  remainingPlayers = [];
  hintsByPlayerId = {};
  roundLog = [];
  currentTurnPlayerId = null;
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);
  if (votingCountdownInterval) clearInterval(votingCountdownInterval);
  if (guessCountdownInterval) clearInterval(guessCountdownInterval);

  createBtn.disabled = false;
  joinBtn.disabled = false;
  showScreen("screen-home");
  leavingDeliberately = false;
}

for (const id of ["leave-room-btn", "leave-room-btn-hints", "leave-room-btn-voting"]) {
  document.getElementById(id)?.addEventListener("click", () => {
    if (confirm("Leave this room?")) leaveRoom();
  });
}

/* ------------------------------- boot ------------------------------- */

async function boot() {
  await loadAvatarRoster();
  loadReactionOptions();
  restoreRememberedName();

  // Try to reclaim a seat before showing any screen, so a refresh lands the
  // player back where they were instead of flashing the name form.
  const resumed = await attemptReconnect();
  if (!resumed) {
    socket = null;
    showScreen("screen-name");
  }
}

boot();

/* --------------------------- player cards --------------------------- */

// container -> Map(playerId -> {el, statusEl, ...}). Cards are updated in
// place rather than rebuilt so in-flight reaction bubbles and open pickers
// survive a re-render triggered by an unrelated event.
const cardRegistries = new Map();

function createCard(player, opts) {
  const el = document.createElement("div");
  el.className = "player-card";

  const avatar = avatarNode(player, "player-avatar");
  el.appendChild(avatar);

  const text = document.createElement("div");
  text.className = "player-text";
  const nameEl = document.createElement("span");
  nameEl.className = "player-name";
  const statusEl = document.createElement("span");
  statusEl.className = "player-status";
  text.appendChild(nameEl);
  text.appendChild(statusEl);
  el.appendChild(text);

  let reactBtn = null;
  let picker = null;
  // Reactions are self-expression, so the trigger lives on your OWN card --
  // the bubble pops there, and everyone sees it.
  if (opts.allowReactions && player.id === myId) {
    ({ reactBtn, picker } = buildReactionPicker(el));
  }

  return { el, avatar, nameEl, statusEl, reactBtn, picker };
}

function buildReactionPicker(cardEl) {
  const reactBtn = document.createElement("button");
  reactBtn.type = "button";
  reactBtn.className = "react-btn";
  reactBtn.textContent = "💬";
  reactBtn.title = "Say something";
  reactBtn.setAttribute("aria-label", "Say something");

  const picker = document.createElement("div");
  picker.className = "reaction-picker";
  picker.hidden = true;

  const close = () => {
    picker.hidden = true;
    reactBtn.classList.remove("is-open");
    cardEl.classList.remove("is-picking");
    showAccuseView(false);
  };

  const send = (kind, value) => {
    sendReaction(kind, value);
    close();
  };

  // --- main view: emojis, phrases, accusation entry, free text ---
  const mainView = document.createElement("div");
  mainView.className = "reaction-view";

  const emojiRow = document.createElement("div");
  emojiRow.className = "reaction-emoji-row";
  for (const emoji of reactionOptions.emojis) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "reaction-emoji";
    b.textContent = emoji;
    b.addEventListener("click", (e) => { e.stopPropagation(); send("emoji", emoji); });
    emojiRow.appendChild(b);
  }
  mainView.appendChild(emojiRow);

  const phraseWrap = document.createElement("div");
  phraseWrap.className = "reaction-phrases";
  for (const phrase of reactionOptions.phrases) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "reaction-chip";
    b.textContent = phrase;
    b.addEventListener("click", (e) => { e.stopPropagation(); send("phrase", phrase); });
    phraseWrap.appendChild(b);
  }
  mainView.appendChild(phraseWrap);

  const accuseBtn = document.createElement("button");
  accuseBtn.type = "button";
  accuseBtn.className = "reaction-chip reaction-accuse-open";
  accuseBtn.textContent = "I think it's…";
  accuseBtn.addEventListener("click", (e) => { e.stopPropagation(); showAccuseView(true); });
  mainView.appendChild(accuseBtn);

  const freeForm = document.createElement("form");
  freeForm.className = "reaction-free";
  const freeInput = document.createElement("input");
  freeInput.type = "text";
  freeInput.placeholder = "say something…";
  freeInput.maxLength = reactionOptions.maxFreeText;
  const freeSend = document.createElement("button");
  freeSend.type = "submit";
  freeSend.textContent = "→";
  freeSend.title = "Send";
  freeForm.appendChild(freeInput);
  freeForm.appendChild(freeSend);
  freeForm.addEventListener("click", (e) => e.stopPropagation());
  freeForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = freeInput.value.trim();
    if (!text) return; // server rejects empty too; this just avoids a wasted send
    freeInput.value = "";
    send("free", text);
  });
  mainView.appendChild(freeForm);

  // --- accusation view: pick which player you're calling out ---
  const accuseView = document.createElement("div");
  accuseView.className = "reaction-view reaction-accuse-list";
  accuseView.hidden = true;

  function showAccuseView(show) {
    if (show) {
      // Rebuilt on open so the list reflects who's actually still in the
      // room right now, not who was there when this card was created.
      accuseView.innerHTML = "";
      const back = document.createElement("button");
      back.type = "button";
      back.className = "reaction-chip reaction-back";
      back.textContent = "← back";
      back.addEventListener("click", (e) => { e.stopPropagation(); showAccuseView(false); });
      accuseView.appendChild(back);

      const roster = (remainingPlayers.length ? remainingPlayers : players).filter(
        (p) => p.id !== myId
      );
      for (const p of roster) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "reaction-chip";
        b.textContent = p.name;
        // Sends the player id, not the name -- the server writes the actual
        // "I think it's <name>" text so it can't be forged.
        b.addEventListener("click", (e) => { e.stopPropagation(); send("accusation", p.id); });
        accuseView.appendChild(b);
      }
    }
    accuseView.hidden = !show;
    mainView.hidden = show;
  }

  picker.appendChild(mainView);
  picker.appendChild(accuseView);
  picker.addEventListener("click", (e) => e.stopPropagation());

  reactBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    const opening = picker.hidden;
    closeAllPickers();
    if (opening) {
      showAccuseView(false);
      picker.hidden = false;
      reactBtn.classList.add("is-open");
      // Lifts this card above its grid siblings so the popover isn't clipped
      // behind the cards that come after it in DOM order.
      cardEl.classList.add("is-picking");
    }
  });

  cardEl.appendChild(reactBtn);
  cardEl.appendChild(picker);
  return { reactBtn, picker, close };
}

function closeAllPickers() {
  for (const registry of cardRegistries.values()) {
    for (const entry of registry.values()) {
      if (entry.picker) {
        entry.picker.hidden = true;
        entry.reactBtn.classList.remove("is-open");
        entry.el.classList.remove("is-picking");
      }
    }
  }
}

document.addEventListener("click", closeAllPickers);

function statusFor(player, opts) {
  // A dropped player's seat is being held — say so on the card rather than
  // leaving it looking frozen or silently vanishing mid-round.
  if (player.connected === false) {
    return { text: "Reconnecting…", className: "player-status is-reconnecting-text" };
  }
  // Lobby and the end-game roles grid both show the player's chosen
  // character; only the in-game modes show hint/turn state.
  if (opts.mode === "lobby" || opts.mode === "reveal") {
    return { text: player.avatar_name || "", className: "player-status" };
  }
  const hint = hintsByPlayerId[player.id];
  if (hint) return { text: `“${hint}”`, className: "player-status has-hint" };
  if (player.id === currentTurnPlayerId) {
    return { text: "Choosing…", className: "player-status is-choosing" };
  }
  return { text: "…", className: "player-status" };
}

function updateCard(entry, player, opts) {
  const tags = [];
  if (player.id === myId) tags.push("you");
  // Host matters in the lobby (they hold the settings + start button); once
  // the game is running it's noise, so it's dropped from in-game cards.
  if (player.id === hostId && opts.mode === "lobby") tags.push("host");
  entry.nameEl.textContent = tags.length ? `${player.name} (${tags.join(", ")})` : player.name;

  const status = statusFor(player, opts);
  entry.statusEl.textContent = status.text;
  entry.statusEl.className = status.className;

  entry.el.classList.toggle("is-you", player.id === myId);
  // `connected` is absent on payloads that predate it (e.g. the imposter
  // profiles snapshotted at game start), so only treat an explicit false
  // as disconnected.
  entry.el.classList.toggle("is-reconnecting", player.connected === false);
  entry.el.classList.toggle(
    "is-active",
    opts.mode !== "lobby" && player.id === currentTurnPlayerId && !hintsByPlayerId[player.id]
  );
}

function renderPlayerCards(container, list, opts) {
  let registry = cardRegistries.get(container);
  if (!registry) {
    registry = new Map();
    cardRegistries.set(container, registry);
  }

  const seen = new Set();
  for (const player of list) {
    seen.add(player.id);
    let entry = registry.get(player.id);
    if (!entry) {
      entry = createCard(player, opts);
      registry.set(player.id, entry);
      container.appendChild(entry.el);
    }
    updateCard(entry, player, opts);
  }

  for (const [playerId, entry] of registry) {
    if (!seen.has(playerId)) {
      entry.el.remove();
      registry.delete(playerId);
    }
  }
}

/* ---------------------------- reactions ---------------------------- */

function sendReaction(kind, value) {
  // Client-side throttle is politeness only; reactions.py enforces the real
  // cooldown and the spam lockout server-side, uniformly across every kind.
  if (Date.now() < reactionCooldownUntil) return;
  reactionCooldownUntil = Date.now() + 1200;
  socket.send(JSON.stringify({ type: "send_reaction", kind, value }));
}

function popReaction(fromId, text, isEmoji) {
  const lifetime = isEmoji ? REACTION_BUBBLE_MS : REACTION_TEXT_BUBBLE_MS;
  for (const registry of cardRegistries.values()) {
    const entry = registry.get(fromId);
    if (!entry || !entry.el.isConnected || entry.el.offsetParent === null) continue;
    const bubble = document.createElement("span");
    // Text needs to sit longer than an emoji because it has to be read.
    bubble.className = isEmoji ? "reaction-bubble" : "reaction-bubble is-text";
    bubble.textContent = text;
    entry.el.appendChild(bubble);
    setTimeout(() => bubble.remove(), lifetime);
  }
}

/* ------------------------------ flow ------------------------------ */

// An invite link (…/?room=ABCD) drops friends straight onto the join form
// with the code already filled in, so nobody has to read letters aloud.
const inviteRoomCode = new URLSearchParams(location.search).get("room");
if (inviteRoomCode) {
  joinCodeInput.value = inviteRoomCode.trim().toUpperCase().slice(0, 4);
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
  storageSet(STORAGE_NAME, myName);
  if (myAvatarId) storageSet(STORAGE_AVATAR, myAvatarId);
  homePlayerName.textContent = myName;
  showScreen("screen-home");
});

function connectToRoom(code, reconnectToken = null) {
  // Guards against a double-click (or any other path) opening a second
  // WebSocket while one is already live — the client-side half of
  // preventing duplicate joins; see main.py for the server-side half,
  // which is the part that actually can't be bypassed.
  if (socket && socket.readyState <= WebSocket.OPEN) return;

  myRoomCode = code;
  createBtn.disabled = true;
  joinBtn.disabled = true;

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({
    name: myName,
    session_id: tabSessionId,
    avatar_id: myAvatarId || "",
  });
  if (reconnectToken) params.set("reconnect_token", reconnectToken);
  socket = new WebSocket(`${protocol}://${location.host}/ws/${code}?${params}`);

  socket.addEventListener("message", (event) => {
    handleMessage(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    createBtn.disabled = false;
    joinBtn.disabled = false;
    // A deliberate leave clears the session first, so this only fires for
    // real drops. Refreshing is the recovery path, so say so rather than
    // just reporting a failure.
    if (!isBeforeRoomJoin() && !leavingDeliberately) {
      alert("Connection lost. Refresh within a few seconds to rejoin your seat.");
    }
  });
}

/* ------------------------- reconnect on load ------------------------- */

async function attemptReconnect() {
  const saved = loadSession();
  if (!saved) return false;

  const name = storageGet(STORAGE_NAME);
  if (!name) {
    clearSession();
    return false;
  }
  myName = name;
  homePlayerName.textContent = myName;

  // Give the socket a moment to either resume or be refused; if the room is
  // gone or the token is stale the server closes it and we fall through to
  // the normal home screen rather than hanging on a dead reconnect.
  return await new Promise((resolve) => {
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      if (!ok) clearSession();
      resolve(ok);
    };

    reconnectResolver = () => done(true);
    connectToRoom(saved.room_code, saved.token);
    socket.addEventListener("close", () => done(false));
    setTimeout(() => done(false), 5000);
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

/* --------------------------- copy room code --------------------------- */

let copyResetTimer = null;

lobbyRoomCode.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(myRoomCode);
  } catch {
    return; // clipboard blocked (insecure context / denied) — stay silent rather than alert
  }
  lobbyRoomCode.textContent = "Copied!";
  lobbyRoomCode.classList.add("copied");
  clearTimeout(copyResetTimer);
  copyResetTimer = setTimeout(() => {
    lobbyRoomCode.textContent = myRoomCode;
    lobbyRoomCode.classList.remove("copied");
  }, 1200);
});

let linkResetTimer = null;

copyLinkBtn.addEventListener("click", async () => {
  const link = `${location.origin}/?room=${encodeURIComponent(myRoomCode)}`;
  try {
    await navigator.clipboard.writeText(link);
  } catch {
    return; // clipboard blocked — the code itself is still copyable
  }
  copyLinkBtn.textContent = "✅ Link copied!";
  clearTimeout(linkResetTimer);
  linkResetTimer = setTimeout(() => {
    copyLinkBtn.textContent = "🔗 Copy invite link";
  }, 1400);
});

/* --------------------------- message handling --------------------------- */

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
      imposterMode = data.imposter_mode;
      lastChanceGuess = data.last_chance_guess;
      if (data.reconnect_token) saveSession(myRoomCode, data.reconnect_token);
      renderLobby();
      showScreen("screen-lobby");
      break;
    case "reconnected":
      handleReconnected(data);
      break;
    case "player_joined":
    case "player_left":
    case "player_status":
      players = data.players;
      hostId = data.host_id;
      // Keep the in-game roster in sync: refresh connected flags so cards can
      // show "Reconnecting…" mid-round, AND drop anyone who has actually left
      // the room. Mapping alone would update a departed player's fields but
      // leave their card on screen forever.
      if (data.players) {
        const present = new Map(data.players.map((p) => [p.id, p]));
        remainingPlayers = remainingPlayers
          .filter((p) => present.has(p.id))
          .map((p) => present.get(p.id));
      }
      renderLobby();
      refreshInGameCards();
      break;
    case "settings_updated":
      timerSeconds = data.timer_seconds;
      difficulty = data.difficulty;
      giveImposterHint = data.give_imposter_hint;
      numImposters = data.num_imposters;
      imposterMode = data.imposter_mode;
      lastChanceGuess = data.last_chance_guess;
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
      handleHintGiven(data);
      break;
    case "hints_revealed":
      enterVotingScreen(data.hints, data.timer_seconds);
      break;
    case "vote_progress":
      voteProgress.textContent = `${data.voted_count}/${data.total} votes cast`;
      break;
    case "guess_started":
      enterGuessScreen(data);
      break;
    case "round_reveal":
      handleRoundReveal(data);
      break;
    case "reaction":
      // Always the sender's own card — a speech bubble points at whoever is
      // speaking. There is no target in the protocol at all.
      popReaction(data.from_id, data.text, data.is_emoji);
      break;
    case "reaction_blocked":
      // Server says we're going too fast; mirror its timer locally so the
      // client stops sending instead of hammering a rejected endpoint.
      reactionCooldownUntil = Date.now() + data.retry_in * 1000;
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

function refreshInGameCards() {
  if (!document.getElementById("screen-hints").hidden) {
    renderPlayerCards(hintsPlayerCards, remainingPlayers, { mode: "hints", allowReactions: true });
  }
  if (!document.getElementById("screen-voting").hidden) {
    renderPlayerCards(votingPlayerCards, remainingPlayers, { mode: "voting", allowReactions: true });
  }
}

function handleReconnected(data) {
  // Restore identity and settings exactly as the welcome path would.
  myId = data.player_id;
  hostId = data.host_id;
  players = data.players;
  timerSeconds = data.timer_seconds;
  difficulty = data.difficulty;
  giveImposterHint = data.give_imposter_hint;
  numImposters = data.num_imposters;
  imposterMode = data.imposter_mode;
  lastChanceGuess = data.last_chance_guess;
  if (data.reconnect_token) saveSession(myRoomCode, data.reconnect_token);

  if (reconnectResolver) {
    reconnectResolver();
    reconnectResolver = null;
  }

  if (data.room_state === "lobby") {
    renderLobby();
    showScreen("screen-lobby");
    return;
  }

  // Mid-game: rebuild the role, roster and this round's hints, then land on
  // whichever screen the room is actually on.
  myRole = data.your_role;
  myCharacter = data.character;
  myAnimeTitle = data.anime_title || null;
  myDecoyMode = data.decoy_mode === true;
  myTeammates = data.teammates || [];
  roundNumber = data.round_number;
  maxRounds = data.max_rounds;
  remainingPlayers = data.remaining_players || [];
  currentTurnPlayerId = data.current_turn_player_id || null;

  hintsByPlayerId = {};
  for (const h of data.hints || []) hintsByPlayerId[h.player_id] = h.hint;

  const statusText = `Round ${roundNumber} of ${maxRounds} · ${remainingPlayers.length} players remain`
    + (data.eliminated ? " · spectating" : "");
  gameStatusBar.textContent = statusText;
  gameStatusBarVoting.textContent = statusText;
  roleBanner.textContent = roleBannerText();

  if (data.room_state === "voting") {
    enterVotingScreen(data.hints || [], timerSeconds);
    return;
  }
  if (data.room_state === "guessing") {
    enterGuessScreen({
      guesser_id: data.guesser_id,
      guesser_name: (players.find((p) => p.id === data.guesser_id) || {}).name || "Someone",
      seconds: null,
    });
    return;
  }

  // hints / starting / reveal all resume on the hint screen; the next
  // turn_started or round_reveal broadcast will correct it within seconds.
  renderPlayerCards(hintsPlayerCards, remainingPlayers, { mode: "hints", allowReactions: true });
  const myTurn = currentTurnPlayerId === myId;
  hintInput.disabled = !myTurn;
  hintSubmitBtn.disabled = !myTurn;
  turnHeading.textContent = myTurn ? "Your turn!" : "Back in the game";
  showScreen("screen-hints");
}

function renderLobby() {
  lobbyRoomCode.textContent = myRoomCode;
  renderPlayerCards(lobbyPlayerCards, players, { mode: "lobby", allowReactions: true });

  const isHost = myId === hostId;
  settingsHostControls.hidden = !isHost;
  settingsReadonly.hidden = isHost;
  if (isHost) {
    timerSelect.value = timerSeconds === null ? "none" : String(timerSeconds);
    difficultySelect.value = difficulty;
    imposterHintCheckbox.checked = giveImposterHint;
    imposterModeSelect.value = imposterMode;
    lastChanceCheckbox.checked = lastChanceGuess;
    // The genre hint only exists to give a blind imposter something to work
    // with; in "similar" mode they already have a same-series character.
    imposterHintCheckbox.disabled = imposterMode === "similar";

    const valid = new Set(validImposterCounts(players.length));
    for (const option of imposterCountSelect.options) {
      option.disabled = !valid.has(Number(option.value));
    }
    imposterCountSelect.value = String(numImposters);
  } else {
    const timerLabel = timerSeconds === null ? "No limit" : `${timerSeconds}s`;
    const modeLabel = imposterMode === "similar" ? "similar character" : "no clue";
    settingsReadonly.textContent =
      `Timer: ${timerLabel} · Difficulty: ${difficulty} · Imposters: ${numImposters}`
      + ` · Imposter gets: ${modeLabel} · Last-chance guess: ${lastChanceGuess ? "on" : "off"}`;
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
      imposter_mode: imposterModeSelect.value,
      last_chance_guess: lastChanceCheckbox.checked,
    })
  );
}

timerSelect.addEventListener("change", sendSettingsUpdate);
difficultySelect.addEventListener("change", sendSettingsUpdate);
imposterHintCheckbox.addEventListener("change", sendSettingsUpdate);
imposterCountSelect.addEventListener("change", sendSettingsUpdate);
imposterModeSelect.addEventListener("change", sendSettingsUpdate);
lastChanceCheckbox.addEventListener("change", sendSettingsUpdate);

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
  myDecoyMode = data.decoy_mode === true;
}

function roleBannerText() {
  if (myRole === "imposter") {
    const teammateText = myTeammates.length
      ? ` Your fellow imposter${myTeammates.length > 1 ? "s" : ""}: ${myTeammates.join(", ")}.`
      : "";
    if (myDecoyMode) {
      // They hold a real character from the right series -- the trap is that
      // it is not the one everyone else has.
      return `🕵️ You are the IMPOSTER. Your character: ${myCharacter} — but the crew has a DIFFERENT one. Blend in.${teammateText}`;
    }
    const hintText = myHint
      ? ` Clue: ${myHint.role_hint}, genres: ${myHint.genres.join(", ")}.`
      : " No hint this round — bluff carefully!";
    return `🕵️ You are the IMPOSTER.${hintText}${teammateText}`;
  }
  return `🎴 Character: ${myCharacter} (${myAnimeTitle})`;
}

function amEliminated() {
  return !remainingPlayers.some((p) => p.id === myId);
}

function handleRoundStarted(data) {
  startingLoading.hidden = true;
  nextRoundLoading.hidden = true;
  newRoundLoading.hidden = true;

  roundNumber = data.round_number;
  maxRounds = data.max_rounds;
  remainingPlayers = data.remaining_players;
  hintsByPlayerId = {};
  currentTurnPlayerId = null;

  // Round 1 of a brand-new game means any previous game's log is stale.
  if (data.round_number === 1) roundLog = [];
  roundLog.push({ round: data.round_number, hints: [], outcome: null });

  hintInput.value = "";
  // Locked by default until this player's own turn_started arrives — without
  // this, the input sits enabled for everyone in the gap between
  // round_started and the first turn_started broadcast, since nothing else
  // disables it yet.
  hintInput.disabled = true;
  hintSubmitBtn.disabled = true;

  const statusText = `Round ${roundNumber} of ${maxRounds} · ${data.remaining_count} players remain`
    + (amEliminated() ? " · spectating" : "");
  gameStatusBar.textContent = statusText;
  gameStatusBarVoting.textContent = statusText;

  roleBanner.textContent = roleBannerText();
  renderPlayerCards(hintsPlayerCards, remainingPlayers, { mode: "hints", allowReactions: true });

  showScreen("screen-hints");
}

function handleTurnStarted(data) {
  const isMyTurn = data.player_id === myId;
  currentTurnPlayerId = data.player_id;

  turnHeading.textContent = isMyTurn
    ? `Your turn! (${data.turn_number}/${data.total_turns})`
    : `${data.player_name} is thinking… (${data.turn_number}/${data.total_turns})`;

  hintInput.disabled = !isMyTurn;
  hintSubmitBtn.disabled = !isMyTurn;
  if (isMyTurn) hintInput.focus();

  renderPlayerCards(hintsPlayerCards, remainingPlayers, { mode: "hints", allowReactions: true });
  startTurnCountdown(data.timer_seconds);
}

function runCountdown(el, seconds, label, noLimitText, onTick) {
  if (seconds === null) {
    el.textContent = noLimitText;
    el.classList.remove("is-urgent");
    return null;
  }

  let remaining = seconds;
  const paint = () => {
    el.textContent = remaining > 0 ? label(remaining) : "⏰ Time's up";
    el.classList.toggle("is-urgent", remaining > 0 && remaining <= TIMER_URGENT_AT);
  };
  paint();

  const id = setInterval(() => {
    remaining -= 1;
    paint();
    if (remaining <= 0) clearInterval(id);
  }, 1000);
  return id;
}

function startTurnCountdown(seconds) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);
  turnCountdownInterval = runCountdown(
    turnTimerDisplay,
    seconds,
    (r) => `⏳ ${r}s remaining`,
    "No time limit"
  );
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

function handleHintGiven(data) {
  hintsByPlayerId[data.player_id] = data.hint;
  const current = roundLog[roundLog.length - 1];
  if (current) {
    const player = remainingPlayers.find((p) => p.id === data.player_id);
    current.hints.push({
      name: data.name,
      hint: data.hint,
      avatar_image: player ? player.avatar_image : null,
      avatar_emoji: player ? player.avatar_emoji : "🎭",
    });
  }
  renderPlayerCards(hintsPlayerCards, remainingPlayers, { mode: "hints", allowReactions: true });
}

function enterVotingScreen(hints, votingTimerSeconds) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);
  currentTurnPlayerId = null;
  for (const h of hints) hintsByPlayerId[h.player_id] = h.hint;

  renderPlayerCards(votingPlayerCards, remainingPlayers, { mode: "voting", allowReactions: true });
  renderPreviousRounds();

  const eliminated = amEliminated();
  spectatorNote.hidden = !eliminated;
  voteList.innerHTML = "";
  if (!eliminated) {
    // The server is what actually blocks a self-vote or a vote for someone
    // already ejected (game.py submit_vote) — this is just not offering
    // buttons that would always get rejected.
    for (const p of remainingPlayers.filter((p) => p.id !== myId)) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className = "ghost";
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
  votingCountdownInterval = runCountdown(
    votingTimerDisplay,
    seconds,
    (r) => `⏳ Discuss and vote — ${r}s`,
    "Discuss and vote — no time limit"
  );
}

function enterGuessScreen(data) {
  if (turnCountdownInterval) clearInterval(turnCountdownInterval);
  if (votingCountdownInterval) clearInterval(votingCountdownInterval);

  const isMe = data.guesser_id === myId;
  guessTitle.textContent = isMe ? "You were caught!" : `${data.guesser_name} was caught!`;
  guessSub.textContent = isMe
    ? "Name the character to steal the win."
    : "They get one guess at the character. If they get it, they win.";

  guessForm.hidden = !isMe;
  guessWaiting.hidden = isMe;
  guessWaiting.textContent = `Waiting for ${data.guesser_name}…`;
  guessInput.value = "";
  guessInput.disabled = false;
  guessSubmitBtn.disabled = false;

  if (guessCountdownInterval) clearInterval(guessCountdownInterval);
  guessCountdownInterval = runCountdown(
    guessTimer,
    data.seconds,
    (r) => `⏳ ${r}s to answer`,
    "No time limit"
  );

  showScreen("screen-guess");
  if (isMe) guessInput.focus();
}

guessForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const guess = guessInput.value.trim();
  if (!guess) return;
  socket.send(JSON.stringify({ type: "submit_guess", guess }));
  guessInput.disabled = true;
  guessSubmitBtn.disabled = true;
  guessWaiting.textContent = "Locked in…";
  guessWaiting.hidden = false;
});

let revealedCharacterName = null;

function ejectionSummary(data) {
  if (data.reason === "disconnect" && !data.ejected_id) {
    return { emoji: "🚪", title: "Someone left", sub: "A player disconnected, changing the balance." };
  }
  if (data.tie) {
    return { emoji: "🤝", title: "Tied vote", sub: "No one was ejected this round." };
  }
  return {
    emoji: data.was_imposter ? "🔪" : "😬",
    title: `${data.ejected_name} was ejected`,
    sub: data.was_imposter ? "They were an imposter." : "They were not an imposter.",
  };
}

function renderRoundHistory(container, entries) {
  container.innerHTML = "";
  for (const entry of entries) {
    if (!entry.hints.length && !entry.outcome) continue;

    const block = document.createElement("div");
    block.className = "history-round";

    const label = document.createElement("p");
    label.className = "history-round-label";
    label.textContent = `Round ${entry.round}`;
    block.appendChild(label);

    for (const h of entry.hints) {
      const row = document.createElement("div");
      row.className = "history-hint";
      if (h.avatar_image) {
        const img = document.createElement("img");
        img.className = "history-avatar";
        img.src = h.avatar_image;
        img.alt = "";
        row.appendChild(img);
      }
      const name = document.createElement("span");
      name.className = "history-hint-name";
      name.textContent = `${h.name}:`;
      const hint = document.createElement("span");
      hint.textContent = `“${h.hint}”`;
      row.appendChild(name);
      row.appendChild(hint);
      block.appendChild(row);
    }

    if (entry.outcome) {
      const out = document.createElement("p");
      out.className = "history-outcome";
      out.textContent = entry.outcome;
      block.appendChild(out);
    }

    container.appendChild(block);
  }
}

function renderPreviousRounds() {
  // Only rounds already concluded -- showing the in-progress round here would
  // just duplicate the cards directly above it. This is the deduction aid:
  // by round 3 nobody remembers what someone said in round 1.
  const earlier = roundLog.slice(0, -1).filter((e) => e.hints.length);
  renderRoundHistory(previousRounds, earlier);
  previousRoundsCard.hidden = earlier.length === 0;
}

function handleRoundReveal(data) {
  if (votingCountdownInterval) clearInterval(votingCountdownInterval);
  if (guessCountdownInterval) clearInterval(guessCountdownInterval);

  const ejection = ejectionSummary(data);
  const guess = data.guess;

  // Record this round's outcome for the post-game recap before anything else.
  const current = roundLog[roundLog.length - 1];
  if (current && !current.outcome) {
    let line = data.tie
      ? "Tied vote — nobody ejected"
      : data.ejected_name
        ? `${data.ejected_name} ejected — ${data.was_imposter ? "imposter" : "crew"}`
        : "A player disconnected";
    if (guess) {
      line += guess.text
        ? ` · guessed “${guess.text}” (${guess.correct ? "correct!" : "wrong"})`
        : " · ran out of time to guess";
    }
    current.outcome = line;
  }

  outcomeBanner.classList.remove("crew-win", "imposter-win", "neutral");

  if (data.game_over) {
    let emoji, title, sub;
    if (data.reason === "guess") {
      emoji = "🎯";
      title = "Stolen at the buzzer!";
      sub = `${data.ejected_name} was ejected — then named the character and took the win.`;
      outcomeBanner.classList.add("imposter-win");
    } else if (data.timed_out) {
      emoji = "⏰";
      title = "Crew ran out of time";
      sub = "The imposters survived long enough to win.";
      outcomeBanner.classList.add("imposter-win");
    } else if (data.winner === "crew") {
      emoji = "🎉";
      title = "Crew wins!";
      sub = "Every imposter was found.";
      outcomeBanner.classList.add("crew-win");
    } else {
      emoji = "🔪";
      title = "Imposters win!";
      sub = "They outlasted the crew.";
      outcomeBanner.classList.add("imposter-win");
    }
    outcomeEmoji.textContent = emoji;
    outcomeTitle.textContent = title;
    // Who was ejected is spelled out in the roles card below, so the banner
    // only needs the final beat plus why the game ended.
    outcomeSub.textContent =
      data.reason === "guess" ? sub : `${ejection.title}. ${sub}`;

    const imposters = data.all_imposters || [];
    revealRolesHeading.textContent = imposters.length > 1 ? "The imposters were…" : "The imposter was…";
    renderPlayerCards(imposterCards, imposters, { mode: "reveal", allowReactions: false });
    revealRoles.hidden = false;

    revealCharacterName.textContent = data.character;
    revealCharacterAnime.textContent = data.anime_title;
    revealCharacter.hidden = false;

    renderRoundHistory(roundHistory, roundLog);
    roundHistoryCard.hidden = false;

    newRoundBtn.hidden = myId !== hostId;
    newRoundBtn.disabled = false;
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
    outcomeBanner.classList.add("neutral");
    outcomeEmoji.textContent = ejection.emoji;
    outcomeTitle.textContent = ejection.title;
    // A failed last-chance guess is a real beat -- say so, otherwise the
    // guess phase just silently vanishes for everyone watching.
    outcomeSub.textContent = guess
      ? `${ejection.sub} ${guess.text ? `They guessed “${guess.text}” — wrong.` : "They ran out of time."}`
      : ejection.sub;

    revealRoles.hidden = true;
    revealCharacter.hidden = true;
    roundHistoryCard.hidden = true;
    newRoundBtn.hidden = true;
    nextRoundLoading.hidden = false;
    characterDetailsBlock.hidden = true;
  }

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
