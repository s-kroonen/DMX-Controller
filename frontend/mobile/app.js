import { api, connectWebSocket } from "../src/api.js";
import { state, onStateChange, notifyStateChange } from "../src/state.js";
import { loadInitialData } from "../src/main_data.js";
import { initAim3D, resizeAim3D } from "./aim3d.js";
import { logicalFromRaw } from "../src/roleRange.js";

// A radically simpler surface for phones than the desktop UI: instead of
// overlaid floating windows and a 3D scene that assume a lot of screen,
// this is a small set of full-height main screens (Color / Control / Aim,
// switched with a segmented control) plus side drawers for things that
// are menus rather than screens you'd "leave" -- which lights are
// selected, which show (animation/pattern) is running or being edited,
// and sound input config. Drawers slide over the content and back; they
// don't replace it, so e.g. picking a different light doesn't lose your
// place on the Aim screen. Reuses the same api.js/state.js/main_data.js
// as the desktop UI -- only the layout differs.

const pctToLogical = (pct) => Math.round((Number(pct) * 255) / 100);

function currentTargetIds() {
  return [...state.selection];
}

function sendToSelection(call) {
  const ids = state.expandedFixtureIds();
  if (ids.length === 0) return;
  call(ids).catch(console.error);
}

// -------------------------------------------------------------- drawers

let openDrawer = null;
let aim3dStarted = false;

function initDrawers() {
  document.querySelectorAll(".drawer-tab").forEach((btn) => {
    btn.onclick = () => toggleDrawer(btn.dataset.drawer);
  });
  document.getElementById("drawer-close").onclick = () => closeDrawer();
  document.getElementById("drawer-backdrop").onclick = () => closeDrawer();
}

function toggleDrawer(name) {
  if (openDrawer === name) { closeDrawer(); return; }
  openDrawer = name;
  document.getElementById("drawer").classList.remove("hidden");
  document.getElementById("drawer-backdrop").classList.remove("hidden");
  document.querySelectorAll(".drawer-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.drawer === name);
  });
  document.querySelectorAll(".drawer-pane").forEach((pane) => {
    pane.classList.toggle("hidden", pane.id !== `drawer-${name}`);
  });
  document.getElementById("drawer-title").textContent =
    name.charAt(0).toUpperCase() + name.slice(1);
  if (name === "shows") { renderAnimList(); renderPatternList(); renderPointList(); }
  if (name === "sound") { refreshSoundDevices(); renderSoundStatus(); }
  if (name === "more") renderMoreDmxStatus();
}

function closeDrawer() {
  openDrawer = null;
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("drawer-backdrop").classList.add("hidden");
  document.querySelectorAll(".drawer-tab").forEach((btn) => btn.classList.remove("active"));
}

// -------------------------------------------------------------- main screens

function initMainTabs() {
  document.querySelectorAll(".main-tab").forEach((btn) => {
    btn.onclick = () => showMainScreen(btn.dataset.screen);
  });
}

function showMainScreen(name) {
  document.querySelectorAll(".screen").forEach((el) => {
    el.classList.toggle("hidden", el.id !== `screen-${name}`);
  });
  document.querySelectorAll(".main-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.screen === name);
  });
  if (name === "aim") {
    if (!aim3dStarted) {
      aim3dStarted = true;
      initAim3D(document.getElementById("aim3d-container"));
    } else {
      resizeAim3D();
    }
  }
}

// -------------------------------------------------------------- lights drawer

let lastGroupIds = "";
let lastFixtureIds = "";

function renderLightsDrawer() {
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

function initClearSelection() {
  document.getElementById("btn-clear-selection").onclick = () => {
    state.selection.clear();
    notifyStateChange();
  };
}

// A <select> of every group + fixture, for picking an animation/pattern
// target -- much friendlier on a phone than typing a raw id.
function populateTargetSelect(select, currentValue) {
  select.innerHTML = "";
  const groupOpt = document.createElement("optgroup");
  groupOpt.label = "Groups";
  for (const g of state.groups) {
    const opt = document.createElement("option");
    opt.value = g.id; opt.textContent = g.name;
    groupOpt.appendChild(opt);
  }
  const fixtureOpt = document.createElement("optgroup");
  fixtureOpt.label = "Fixtures";
  for (const f of state.room.fixtures) {
    const opt = document.createElement("option");
    opt.value = f.id; opt.textContent = f.name;
    fixtureOpt.appendChild(opt);
  }
  select.appendChild(groupOpt);
  select.appendChild(fixtureOpt);
  if (currentValue) select.value = currentValue;
}

// -------------------------------------------------------------- color screen

function initColorScreen() {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  const push = () => {
    currentTargetIds().forEach((id) =>
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

// -------------------------------------------------------------- control screen

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

let padDragging = false;

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
  pad.addEventListener("pointerdown", (e) => { padDragging = true; pad.setPointerCapture(e.pointerId); setFromPad(e); });
  pad.addEventListener("pointermove", (e) => { if (e.buttons || e.pressure > 0) setFromPad(e); });
  pad.addEventListener("pointerup", () => { padDragging = false; });
  pad.addEventListener("pointercancel", () => { padDragging = false; });
}

// ---- read-back sync: reflect a fixture's ACTUAL current values into the
// Color/Control screens, mirroring panels.js on desktop, so a change made
// elsewhere (the other UI, an animation, another phone) shows up here too.
function isBeingEdited(el) {
  return !!el && document.activeElement === el;
}

function representativeFixture() {
  for (const id of state.expandedFixtureIds()) {
    const fixture = state.fixtureById(id);
    if (fixture) return fixture;
  }
  return null;
}

function syncControlsFromState() {
  const fixture = representativeFixture();
  if (!fixture) return;
  const profile = state.profileById(fixture.profile_id);
  const values = state.fixtureState[fixture.id]?.values;
  if (!profile || !values) return;
  syncRgbFromState(values);
  syncStrobeFromState(profile, values);
  syncPanTiltFromState(values);
}

function syncRgbFromState(values) {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  if ([red, green, blue, white].some(isBeingEdited)) return;
  if (red && "red" in values) red.value = values.red;
  if (green && "green" in values) green.value = values.green;
  if (blue && "blue" in values) blue.value = values.blue;
  if (white && "white" in values) white.value = values.white;
}

function syncStrobeFromState(profile, values) {
  const dimmerSlider = document.getElementById("dimmer-slider");
  const strobeSlider = document.getElementById("strobe-slider");
  if (isBeingEdited(dimmerSlider) || isBeingEdited(strobeSlider)) return;
  if ("dimmer" in values) {
    const logical = logicalFromRaw(profile, "dimmer", values.dimmer);
    setPct("dimmer-slider", "dimmer-pct", Math.round((logical * 100) / 255));
  }
  if ("strobe" in values || "shutter" in values) {
    const role = "strobe" in values ? "strobe" : "shutter";
    const logical = logicalFromRaw(profile, role, values[role]);
    setPct("strobe-slider", "strobe-pct", Math.round((logical * 100) / 255));
  }
}

function syncPanTiltFromState(values) {
  const pad = document.getElementById("xy-pad");
  const dot = document.getElementById("xy-dot");
  if (padDragging || !pad || !dot) return;
  if ("pan" in values && "tilt" in values) {
    dot.style.left = `${(values.pan / 255) * 100}%`;
    dot.style.top = `${(values.tilt / 255) * 100}%`;
  }
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

// -------------------------------------------------------------- shows: points

async function submitPoint() {
  const name = document.getElementById("pt-name").value || "Point";
  const position = {
    x: Number(document.getElementById("pt-x").value),
    y: Number(document.getElementById("pt-y").value),
    z: Number(document.getElementById("pt-z").value),
  };
  await api.addAnimationPoint({ name, position });
  await refreshRoom();
  renderPointList();
}

function renderPointList() {
  const container = document.getElementById("pt-list");
  container.innerHTML = "";
  for (const point of state.room.animation_points || []) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span>${point.name} (${point.position.x}, ${point.position.y}, ${point.position.z})</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => { await api.deleteAnimationPoint(point.id); await refreshRoom(); renderPointList(); };
    row.appendChild(del);
    container.appendChild(row);
  }
}

async function refreshRoom() {
  state.room = await api.getRoom();
  notifyStateChange();
}

// -------------------------------------------------------------- shows: patterns

function patternEditorHtml() {
  return `
    <label>Name <input class="pat-name" type="text" placeholder="Circle sweep"></label>
    <label>Target <select class="pat-target"></select></label>
    <label>Shape
      <select class="pat-shape">
        <option value="circle">Circle</option>
        <option value="figure8">Figure 8</option>
        <option value="linear">Linear (pan only)</option>
        <option value="square">Square</option>
      </select>
    </label>
    <label>Speed (Hz) <input class="pat-speed" type="number" step="0.05" value="0.2"></label>
    <label>Pan center (0-255) <input class="pat-pan-center" type="number" min="0" max="255" value="128"></label>
    <label>Tilt center (0-255) <input class="pat-tilt-center" type="number" min="0" max="255" value="128"></label>
    <label>Pan size <input class="pat-pan-size" type="number" min="0" max="255" value="80"></label>
    <label>Tilt size <input class="pat-tilt-size" type="number" min="0" max="255" value="80"></label>
    <div class="btn-row">
      <button class="pat-save">Save</button>
      <button class="pat-cancel">Cancel</button>
    </div>
  `;
}

function openPatternEditor(pattern) {
  const editor = document.getElementById("pattern-editor");
  editor.innerHTML = patternEditorHtml();
  editor.classList.remove("hidden");
  populateTargetSelect(editor.querySelector(".pat-target"), pattern?.target_id);
  if (pattern) {
    editor.querySelector(".pat-name").value = pattern.name;
    editor.querySelector(".pat-shape").value = pattern.shape;
    editor.querySelector(".pat-speed").value = pattern.speed_hz;
    editor.querySelector(".pat-pan-center").value = pattern.pan_center;
    editor.querySelector(".pat-tilt-center").value = pattern.tilt_center;
    editor.querySelector(".pat-pan-size").value = pattern.pan_size;
    editor.querySelector(".pat-tilt-size").value = pattern.tilt_size;
  }
  editor.querySelector(".pat-cancel").onclick = () => editor.classList.add("hidden");
  editor.querySelector(".pat-save").onclick = async () => {
    await api.savePattern({
      id: pattern?.id,
      name: editor.querySelector(".pat-name").value || "Pattern",
      target_id: editor.querySelector(".pat-target").value,
      shape: editor.querySelector(".pat-shape").value,
      speed_hz: Number(editor.querySelector(".pat-speed").value) || 0,
      pan_center: Number(editor.querySelector(".pat-pan-center").value) || 0,
      tilt_center: Number(editor.querySelector(".pat-tilt-center").value) || 0,
      pan_size: Number(editor.querySelector(".pat-pan-size").value) || 0,
      tilt_size: Number(editor.querySelector(".pat-tilt-size").value) || 0,
    });
    editor.classList.add("hidden");
    renderPatternList();
  };
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
    const editBtn = document.createElement("button");
    editBtn.textContent = "Edit";
    editBtn.onclick = () => openPatternEditor(pattern);
    const delBtn = document.createElement("button");
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => { await api.deletePattern(pattern.id); renderPatternList(); };
    row.append(playBtn, stopBtn, editBtn, delBtn);
    container.appendChild(row);
  }
  if (patterns.length === 0) container.innerHTML = '<div class="hint">No patterns yet.</div>';
}

// -------------------------------------------------------------- shows: animations

function keyframeRowHtml() {
  const points = state.room.animation_points || [];
  const pointOptions = ['<option value="">(raw X/Y/Z)</option>']
    .concat(points.map((p) => `<option value="${p.id}">${p.name}</option>`))
    .join("");
  return `
    <label>t(s) <input type="number" step="0.1" class="kf-time" value="0"></label>
    <label>Point <select class="kf-point">${pointOptions}</select></label>
    <label>X <input type="number" step="0.1" class="kf-x"></label>
    <label>Y <input type="number" step="0.1" class="kf-y"></label>
    <label>Z <input type="number" step="0.1" class="kf-z"></label>
    <label>Dimmer (0-255, optional) <input type="number" class="kf-dimmer"></label>
    <button type="button" class="kf-remove wide-btn">Remove keyframe</button>
  `;
}

function addKeyframeRow(container, keyframe) {
  const row = document.createElement("div");
  row.className = "keyframe-row";
  row.innerHTML = keyframeRowHtml();
  if (keyframe) {
    row.querySelector(".kf-time").value = keyframe.time_s;
    if (keyframe.point_id) row.querySelector(".kf-point").value = keyframe.point_id;
    if (keyframe.target_point) {
      row.querySelector(".kf-x").value = keyframe.target_point.x;
      row.querySelector(".kf-y").value = keyframe.target_point.y;
      row.querySelector(".kf-z").value = keyframe.target_point.z;
    }
    if (keyframe.dimmer !== null && keyframe.dimmer !== undefined) row.querySelector(".kf-dimmer").value = keyframe.dimmer;
  }
  row.querySelector(".kf-remove").onclick = () => row.remove();
  container.appendChild(row);
}

function collectKeyframes(container) {
  return [...container.querySelectorAll(".keyframe-row")].map((row) => {
    const time_s = Number(row.querySelector(".kf-time").value) || 0;
    const pointId = row.querySelector(".kf-point").value;
    const x = row.querySelector(".kf-x").value;
    const y = row.querySelector(".kf-y").value;
    const z = row.querySelector(".kf-z").value;
    const dimmerVal = row.querySelector(".kf-dimmer").value;
    const kf = { time_s };
    if (pointId) kf.point_id = pointId;
    else if (x !== "" && y !== "" && z !== "") kf.target_point = { x: Number(x), y: Number(y), z: Number(z) };
    if (dimmerVal !== "") kf.dimmer = Number(dimmerVal);
    return kf;
  });
}

function animationEditorHtml() {
  return `
    <label>Name <input class="anim-name" type="text" placeholder="Sweep"></label>
    <label>Target <select class="anim-target"></select></label>
    <label><input class="anim-loop" type="checkbox" checked> Loop</label>
    <label>Start offset (s) -- for staging the same pattern per fixture <input class="anim-offset" type="number" step="0.1" value="0"></label>
    <div class="anim-keyframes"></div>
    <button type="button" class="anim-add-kf wide-btn">+ Add keyframe</button>
    <div class="btn-row">
      <button class="anim-save">Save</button>
      <button class="anim-cancel">Cancel</button>
    </div>
  `;
}

// The desktop UI's authoring model (and this one) edits ONE track per
// Animation. An animation with other tracks (e.g. built some other way)
// keeps those tracks untouched -- only the first is shown/edited here --
// so saving never silently deletes a track this simple editor can't show.
function openAnimationEditor(animation) {
  const editor = document.getElementById("anim-editor");
  editor.innerHTML = animationEditorHtml();
  editor.classList.remove("hidden");
  const track = animation?.tracks?.[0];
  populateTargetSelect(editor.querySelector(".anim-target"), track?.target_id);
  const kfContainer = editor.querySelector(".anim-keyframes");
  if (animation) {
    editor.querySelector(".anim-name").value = animation.name;
    editor.querySelector(".anim-loop").checked = animation.loop;
    editor.querySelector(".anim-offset").value = track?.time_offset_s || 0;
    for (const kf of track?.keyframes || []) addKeyframeRow(kfContainer, kf);
  }
  editor.querySelector(".anim-add-kf").onclick = () => addKeyframeRow(kfContainer);
  editor.querySelector(".anim-cancel").onclick = () => editor.classList.add("hidden");
  editor.querySelector(".anim-save").onclick = async () => {
    const newTrack = {
      target_id: editor.querySelector(".anim-target").value,
      keyframes: collectKeyframes(kfContainer),
      time_offset_s: Number(editor.querySelector(".anim-offset").value) || 0,
    };
    const otherTracks = (animation?.tracks || []).slice(1);
    await api.saveAnimation({
      id: animation?.id,
      name: editor.querySelector(".anim-name").value || "Animation",
      loop: editor.querySelector(".anim-loop").checked,
      tracks: [newTrack, ...otherTracks],
    });
    editor.classList.add("hidden");
    renderAnimList();
  };
}

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
    const editBtn = document.createElement("button");
    editBtn.textContent = "Edit";
    editBtn.onclick = () => openAnimationEditor(anim);
    const delBtn = document.createElement("button");
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => { await api.deleteAnimation(anim.id); renderAnimList(); };
    row.append(playBtn, stopBtn, editBtn, delBtn);
    container.appendChild(row);
  }
  if (animations.length === 0) container.innerHTML = '<div class="hint">No animations yet.</div>';
}

function initShowsDrawer() {
  for (const btn of document.querySelectorAll("#drawer-shows .subtab-btn")) {
    btn.onclick = () => {
      for (const b of document.querySelectorAll("#drawer-shows .subtab-btn")) b.classList.toggle("active", b === btn);
      for (const pane of document.querySelectorAll("#drawer-shows .subtab-pane")) {
        pane.classList.toggle("hidden", pane.dataset.subtabPane !== btn.dataset.subtab);
      }
    };
  }
  document.getElementById("anim-new").onclick = () => openAnimationEditor(null);
  document.getElementById("pattern-new").onclick = () => openPatternEditor(null);
  document.getElementById("pt-add").onclick = submitPoint;
}

// -------------------------------------------------------------- sound drawer

async function refreshSoundDevices() {
  const select = document.getElementById("sound-device");
  try {
    const devices = await api.audioDevices();
    const status = await api.audioStatus();
    select.innerHTML = "";
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = d.id; opt.textContent = d.name;
      select.appendChild(opt);
    }
    if (status.device) select.value = status.device.id;
    select.onchange = () => api.audioSelect(select.value).catch(console.error);
  } catch {
    select.innerHTML = '<option value="">(unavailable)</option>';
  }
}

async function renderSoundStatus() {
  const el = document.getElementById("sound-status");
  try {
    const status = await api.audioStatus();
    el.textContent = status.capture.running
      ? `Listening to ${status.capture.device_name || "input"}`
      : "Stopped";
  } catch {
    el.textContent = "Sound-to-light not available.";
  }
}

function initSoundDrawer() {
  document.getElementById("sound-start").onclick = () => api.audioStart().then(renderSoundStatus).catch(renderSoundStatus);
  document.getElementById("sound-stop").onclick = () => api.audioStop().then(renderSoundStatus).catch(renderSoundStatus);
}

// -------------------------------------------------------------- more drawer

async function renderMoreDmxStatus() {
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

function initMoreDrawer() {
  document.getElementById("btn-dmx-reconnect").onclick = () =>
    api.dmxReconnect().then(renderMoreDmxStatus).catch(renderMoreDmxStatus);
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

function initBlackout() {
  document.getElementById("btn-blackout").onclick = () => {
    if (confirm("Blackout all fixtures?")) api.blackout().catch(console.error);
  };
}

// -------------------------------------------------------------- bootstrap

async function bootstrap() {
  initDrawers();
  initMainTabs();
  initClearSelection();
  initColorScreen();
  initDimmerStrobeCard();
  initPanTiltPad();
  initShowsDrawer();
  initSoundDrawer();
  initMoreDrawer();
  initBlackout();

  onStateChange(() => {
    renderLightsDrawer();
    document.getElementById("selection-bar").textContent = selectionSummaryText();
    renderCustomCard();
    updateDmxPill();
    syncControlsFromState();
  });

  // Desktop's sound panel polls continuously (sound.js); the drawer here
  // only refreshed once on open, so status (and other sessions' start/stop)
  // went stale while it stayed open. Poll the same way, but only while the
  // sound drawer is actually visible, to skip needless requests otherwise.
  setInterval(() => { if (openDrawer === "sound") renderSoundStatus(); }, 2000);

  await loadInitialData();

  connectWebSocket((snapshot) => {
    state.room = snapshot.room;
    state.groups = snapshot.groups;
    state.fixtureState = snapshot.fixture_state;
    state.dmxStatus = snapshot.dmx_status;
    notifyStateChange();
  });

  notifyStateChange();
}

bootstrap().catch((err) => {
  console.error("Failed to start mobile UI", err);
  alert("Failed to start UI -- check console. Is the backend running?");
});
