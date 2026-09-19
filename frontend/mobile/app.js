import { api, connectWebSocket } from "../src/api.js";
import { state, onStateChange, notifyStateChange } from "../src/state.js";
import { loadInitialData } from "../src/main_data.js";

// A radically simpler surface for phones/tablets than the desktop UI: one
// screen at a time (bottom tab bar) instead of overlaid floating windows,
// and no 3D scene at all (a full Three.js view isn't usable on a small
// touch screen) -- the Aim tab is a flat, top-down floor plan instead.
// Reuses the same backend API and the same api.js/state.js modules as the
// desktop UI (no separate state model to keep in sync).

const pctToLogical = (pct) => Math.round((Number(pct) * 255) / 100);

function currentTargetIds() {
  return [...state.selection];
}

function sendToSelection(call) {
  const ids = state.expandedFixtureIds();
  if (ids.length === 0) return;
  call(ids).catch(console.error);
}

// -------------------------------------------------------------- tab bar

function initTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.onclick = () => showScreen(btn.dataset.screen);
  });
}

function showScreen(name) {
  document.querySelectorAll(".screen").forEach((el) => {
    el.classList.toggle("hidden", el.id !== `screen-${name}`);
  });
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.screen === name);
  });
  if (name === "aim") drawAimCanvas();
  if (name === "shows") { renderAnimList(); renderPatternList(); }
  if (name === "more") { renderDmxStatus(); renderSoundStatus(); }
}

// -------------------------------------------------------------- select screen

let lastGroupIds = "";
let lastFixtureIds = "";

function renderSelectScreen() {
  renderTileGrid("group-buttons", state.groups, lastGroupIds, (k) => { lastGroupIds = k; });
  renderTileGrid("fixture-buttons", state.room.fixtures, lastFixtureIds, (k) => { lastFixtureIds = k; });
}

function renderTileGrid(containerId, items, lastKey, setLastKey) {
  const container = document.getElementById(containerId);
  const key = items.map((it) => it.id).join(",");
  if (key !== lastKey) {
    setLastKey(key);
    container.innerHTML = "";
    for (const item of items) {
      const btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.dataset.id = item.id;
      btn.textContent = item.name;
      btn.onclick = () => {
        state.toggleSelection(item.id, false); // mobile: tap always adds/removes, no modifier keys
        notifyStateChange();
      };
      container.appendChild(btn);
    }
  }
  for (const btn of container.children) {
    btn.classList.toggle("selected", state.isSelected(btn.dataset.id));
  }
}

function selectionSummaryText() {
  if (state.selection.size === 0) return "Nothing selected";
  return [...state.selection].map((id) => {
    const fx = state.fixtureById(id);
    if (fx) return fx.name;
    const grp = state.groupById(id);
    return grp ? `[group] ${grp.name}` : id;
  }).join(", ");
}

function renderSelectionBars() {
  const text = selectionSummaryText();
  const control = document.getElementById("control-selection-bar");
  const aim = document.getElementById("aim-selection-bar");
  if (control) control.textContent = text;
  if (aim) aim.textContent = text;
}

// -------------------------------------------------------------- control screen

function initColorCard() {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  const push = () => {
    const ids = currentTargetIds();
    ids.forEach((id) =>
      api.setColor(id, Number(red.value), Number(green.value), Number(blue.value), Number(white.value))
        .catch(console.error)
    );
  };
  [red, green, blue, white].forEach((el) => el.addEventListener("input", push));

  const palette = document.getElementById("rgb-palette");
  const swatches = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff", "#ffffff", "#ff8000"];
  for (const color of swatches) {
    const sw = document.createElement("div");
    sw.style.background = color;
    sw.onclick = () => {
      red.value = parseInt(color.slice(1, 3), 16);
      green.value = parseInt(color.slice(3, 5), 16);
      blue.value = parseInt(color.slice(5, 7), 16);
      push();
    };
    palette.appendChild(sw);
  }
}

// Same shared-channel rule as the desktop Strobe/Shutter window: if every
// selected fixture's dimmer and strobe are the same physical DMX channel,
// touching one silently zeroes the other so the sliders never lie about
// what the fixture is actually doing.
function selectionSharesLightChannel() {
  let relevant = 0, shared = 0;
  for (const fid of state.expandedFixtureIds()) {
    const fixture = state.fixtureById(fid);
    const profile = fixture && state.profileById(fixture.profile_id);
    if (!profile) continue;
    const dimmerCh = profile.channels.dimmer;
    const strobeCh = profile.channels.strobe ?? profile.channels.shutter;
    if (dimmerCh === undefined && strobeCh === undefined) continue;
    relevant += 1;
    if (dimmerCh !== undefined && dimmerCh === strobeCh) shared += 1;
  }
  return relevant > 0 && relevant === shared;
}

function setPct(sliderId, pctId, pct) {
  document.getElementById(sliderId).value = pct;
  document.getElementById(pctId).textContent = `${Math.round(pct)}%`;
}

function initDimmerStrobeCard() {
  const onDimmer = (pct) => {
    setPct("dimmer-slider", "dimmer-pct", pct);
    if (selectionSharesLightChannel()) setPct("strobe-slider", "strobe-pct", 0);
    sendToSelection((ids) => api.setDimmer(ids, pctToLogical(pct)));
  };
  const onStrobe = (pct) => {
    setPct("strobe-slider", "strobe-pct", pct);
    if (pct > 0 && selectionSharesLightChannel()) setPct("dimmer-slider", "dimmer-pct", 0);
    sendToSelection((ids) => api.setStrobe(ids, pctToLogical(pct)));
  };
  document.getElementById("dimmer-slider").addEventListener("input", (e) => onDimmer(Number(e.target.value)));
  document.getElementById("strobe-slider").addEventListener("input", (e) => onStrobe(Number(e.target.value)));
  document.querySelectorAll("#screen-control [data-strobe]").forEach((btn) => {
    btn.onclick = () => onStrobe(Number(btn.dataset.strobe));
  });
  document.getElementById("shutter-closed").onclick = () => sendToSelection((ids) => api.setShutter(ids, true));
  document.getElementById("shutter-open").onclick = () => {
    setPct("strobe-slider", "strobe-pct", 0);
    if (selectionSharesLightChannel() && Number(document.getElementById("dimmer-slider").value) === 0) {
      setPct("dimmer-slider", "dimmer-pct", 100);
    }
    sendToSelection((ids) => api.setShutter(ids, false));
  };
}

function initPanTiltPad() {
  const pad = document.getElementById("xy-pad");
  const dot = document.getElementById("xy-dot");
  const setFromPad = (evt) => {
    const rect = pad.getBoundingClientRect();
    const fx = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width));
    const fy = Math.min(1, Math.max(0, (evt.clientY - rect.top) / rect.height));
    dot.style.left = `${fx * 100}%`;
    dot.style.top = `${fy * 100}%`;
    const pan = Math.round(fx * 255);
    const tilt = Math.round(fy * 255);
    currentTargetIds().forEach((id) => api.setPanTilt(id, pan, tilt).catch(console.error));
  };
  // Pointer events (not mouse) so this works uniformly for touch and mouse.
  pad.addEventListener("pointerdown", (e) => { pad.setPointerCapture(e.pointerId); setFromPad(e); });
  pad.addEventListener("pointermove", (e) => { if (e.buttons || e.pressure > 0) setFromPad(e); });
}

let lastCustomKey = "";

function renderCustomCard() {
  const container = document.getElementById("custom-sliders");
  const fixtureIds = state.expandedFixtureIds();
  const withCustom = fixtureIds
    .map((id) => state.fixtureById(id))
    .filter(Boolean)
    .map((fixture) => ({ fixture, profile: state.profileById(fixture.profile_id) }))
    .filter(({ profile }) => profile && profile.custom_channels && profile.custom_channels.length > 0);

  const key = withCustom.map(({ fixture }) => fixture.id).join(",");
  if (key === lastCustomKey) return;
  lastCustomKey = key;
  container.innerHTML = "";

  if (withCustom.length === 0) {
    container.innerHTML = '<div class="hint">No custom channels for the current selection.</div>';
    return;
  }
  for (const { fixture, profile } of withCustom) {
    for (const custom of profile.custom_channels) {
      const label = document.createElement("label");
      label.textContent = `${fixture.name}: ${custom.label}`;
      const input = document.createElement("input");
      input.type = "range";
      input.min = custom.min_value ?? 0;
      input.max = custom.max_value ?? 255;
      input.value = custom.default ?? 0;
      input.addEventListener("input", () =>
        api.setCustom(fixture.id, custom.label, Number(input.value)).catch(console.error)
      );
      label.appendChild(input);
      container.appendChild(label);
    }
  }
}

// -------------------------------------------------------------- aim screen

function effectiveFloorPoints() {
  const drawn = state.room.floor_points || [];
  if (drawn.length >= 3) return drawn;
  const dims = state.room.dimensions || { width: 10, depth: 10 };
  const hw = dims.width / 2, hd = dims.depth / 2;
  return [{ x: -hw, y: -hd }, { x: hw, y: -hd }, { x: hw, y: hd }, { x: -hw, y: hd }];
}

function aimCanvasTransform(canvas) {
  const points = effectiveFloorPoints();
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = Math.max(maxX - minX, 1), h = Math.max(maxY - minY, 1);
  const pad = 20;
  const scale = Math.min((canvas.width - pad * 2) / w, (canvas.height - pad * 2) / h);
  // room +Y is "away from the operator" -- draw it as up on screen (canvas Y grows down).
  const toScreen = (p) => ({
    x: pad + (p.x - minX) * scale,
    y: canvas.height - pad - (p.y - minY) * scale,
  });
  const toRoom = (sx, sy) => ({
    x: minX + (sx - pad) / scale,
    y: minY + (canvas.height - pad - sy) / scale,
  });
  return { points, toScreen, toRoom };
}

function drawAimCanvas() {
  const canvas = document.getElementById("aim-canvas");
  const ctx = canvas.getContext("2d");
  const { points, toScreen } = aimCanvasTransform(canvas);

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#1a1d22";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = "#3a7bd5";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const s = toScreen(p);
    if (i === 0) ctx.moveTo(s.x, s.y); else ctx.lineTo(s.x, s.y);
  });
  ctx.closePath();
  ctx.stroke();

  for (const fixture of state.room.fixtures) {
    const s = toScreen({ x: fixture.position.x, y: fixture.position.y });
    ctx.fillStyle = state.isSelected(fixture.id) ? "#ffffff" : "#9aa2ac";
    ctx.beginPath();
    ctx.arc(s.x, s.y, state.isSelected(fixture.id) ? 7 : 5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function initAimCanvas() {
  const canvas = document.getElementById("aim-canvas");
  const heightSlider = document.getElementById("aim-height");
  const heightVal = document.getElementById("aim-height-val");
  heightSlider.addEventListener("input", () => { heightVal.textContent = heightSlider.value; });

  canvas.addEventListener("pointerdown", (evt) => {
    const rect = canvas.getBoundingClientRect();
    const sx = ((evt.clientX - rect.left) / rect.width) * canvas.width;
    const sy = ((evt.clientY - rect.top) / rect.height) * canvas.height;
    const { toRoom } = aimCanvasTransform(canvas);
    const point = toRoom(sx, sy);
    const z = Number(heightSlider.value);
    const ids = currentTargetIds();
    if (ids.length === 0) return;
    ids.forEach((id) => api.aim(id, point.x, point.y, z).catch(console.error));
    drawAimCanvas();
  });
}

// -------------------------------------------------------------- shows screen

async function renderAnimList() {
  const container = document.getElementById("anim-list");
  const animations = await api.listAnimations();
  container.innerHTML = "";
  for (const anim of animations) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span>${anim.name}</span>`;
    const playBtn = document.createElement("button");
    playBtn.textContent = "Play";
    playBtn.onclick = () => api.playAnimation(anim.id).catch(console.error);
    const stopBtn = document.createElement("button");
    stopBtn.textContent = "Stop";
    stopBtn.onclick = () => api.stopAnimation(anim.id).catch(console.error);
    row.appendChild(playBtn);
    row.appendChild(stopBtn);
    container.appendChild(row);
  }
  if (animations.length === 0) container.innerHTML = '<div class="hint">No animations yet -- create them on the desktop UI.</div>';
}

async function renderPatternList() {
  const container = document.getElementById("pattern-list");
  const patterns = await api.listPatterns();
  container.innerHTML = "";
  for (const pattern of patterns) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span>${pattern.name}</span>`;
    const playBtn = document.createElement("button");
    playBtn.textContent = "Play";
    playBtn.onclick = () => api.playPattern(pattern.id).catch(console.error);
    const stopBtn = document.createElement("button");
    stopBtn.textContent = "Stop";
    stopBtn.onclick = () => api.stopPattern(pattern.id).catch(console.error);
    row.appendChild(playBtn);
    row.appendChild(stopBtn);
    container.appendChild(row);
  }
  if (patterns.length === 0) container.innerHTML = '<div class="hint">No patterns yet -- create them on the desktop UI.</div>';
}

// -------------------------------------------------------------- more screen

async function renderDmxStatus() {
  const el = document.getElementById("more-dmx-status");
  try {
    const status = await api.dmxStatus();
    el.textContent = status.running
      ? `Running (${status.frames_sent || 0} frames)${status.link_lost ? " -- LINK LOST" : ""}`
      : `Stopped${status.connect_error ? `: ${status.connect_error}` : ""}`;
  } catch {
    el.textContent = "Unable to load DMX status.";
  }
}

async function renderSoundStatus() {
  const el = document.getElementById("more-sound-status");
  try {
    const status = await api.audioStatus();
    el.textContent = status.capture.running
      ? `Listening to ${status.capture.device_name || "input"}`
      : "Stopped";
  } catch {
    el.textContent = "Sound-to-light not available.";
  }
}

function initMoreScreen() {
  document.getElementById("btn-dmx-reconnect").onclick = () =>
    api.dmxReconnect().then(renderDmxStatus).catch(renderDmxStatus);
  document.getElementById("btn-sound-start").onclick = () =>
    api.audioStart().then(renderSoundStatus).catch(renderSoundStatus);
  document.getElementById("btn-sound-stop").onclick = () =>
    api.audioStop().then(renderSoundStatus).catch(renderSoundStatus);
}

// -------------------------------------------------------------- top bar

function updateDmxPill() {
  const pill = document.getElementById("dmx-status");
  const status = state.dmxStatus || {};
  if (status.link_lost) {
    pill.textContent = "DMX: LINK LOST";
    pill.className = "status-pill error";
  } else if (status.running) {
    pill.textContent = "DMX: running";
    pill.className = "status-pill ok";
  } else {
    pill.textContent = "DMX: stopped";
    pill.className = "status-pill error";
  }
}

function initClearSelection() {
  document.getElementById("btn-clear-selection").onclick = () => {
    state.selection.clear();
    notifyStateChange();
  };
}

function initBlackout() {
  document.getElementById("btn-blackout").onclick = () => {
    if (confirm("Blackout all fixtures?")) api.blackout().catch(console.error);
  };
}

// -------------------------------------------------------------- bootstrap

async function bootstrap() {
  initTabs();
  initColorCard();
  initDimmerStrobeCard();
  initPanTiltPad();
  initAimCanvas();
  initMoreScreen();
  initClearSelection();
  initBlackout();

  onStateChange(() => {
    renderSelectScreen();
    renderSelectionBars();
    renderCustomCard();
    updateDmxPill();
  });

  await loadInitialData();

  connectWebSocket((snapshot) => {
    state.room = snapshot.room;
    state.groups = snapshot.groups;
    state.fixtureState = snapshot.fixture_state;
    state.dmxStatus = snapshot.dmx_status;
    notifyStateChange();
  });

  notifyStateChange();
  showScreen("select");
}

bootstrap().catch((err) => {
  console.error("Failed to start mobile UI", err);
  alert("Failed to start UI -- check console. Is the backend running?");
});
