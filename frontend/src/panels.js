import { api } from "./api.js";
import { state, onStateChange } from "./state.js";
import { initPanelWindows } from "./panelWindows.js";
import { logicalFromRaw } from "./roleRange.js";

function currentTargetIds() {
  return [...state.selection];
}

function forEachTarget(fn) {
  const ids = currentTargetIds();
  if (ids.length === 0) return;
  ids.forEach(fn);
}

export function initPanels() {
  initPanelWindows();

  document.querySelectorAll(".toggle-btn").forEach((btn) => {
    if (btn.id === "btn-view-3d") {
      btn.onclick = () => btn.classList.toggle("active");
    }
  });

  initRgbPanel();
  initStrobePanel();
  initPanTiltPanel();

  onStateChange(renderCustomPanel);
  onStateChange(syncCustomRangesFromState);
  onStateChange(syncControlsFromState);
}

// ---- read-back sync: reflect a fixture's ACTUAL current values into the
// controls, so a change made elsewhere (another session, an animation, a
// page reload) shows up here too -- not just write-only sliders. Skips a
// control the operator is actively touching so an incoming WS tick can't
// yank a slider out from under a drag.
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
  syncRgbFromState(values, profile, state.fixtureState[fixture.id]?.zones);
  syncStrobeFromState(profile, values);
  syncPanTiltFromState(profile, values);
}

function syncRgbFromState(values, profile, zoneState) {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  if ([red, green, blue, white].some(isBeingEdited)) return;
  if (profile && profile.zones && profile.zones.length) {
    // a fixture with zones has no single color: show the first chosen (or first) zone's
    const chosen = profile.zones.map((z) => z.id).filter((id) => !state.zoneFilter.size || state.zoneFilter.has(id));
    const zone = zoneState && chosen.map((id) => zoneState[id]).find(Boolean);
    if (zone) {
      [red.value, green.value, blue.value, white.value] = zone.color;
    }
    return;
  }
  if (red && "red" in values) red.value = values.red;
  if (green && "green" in values) green.value = values.green;
  if (blue && "blue" in values) blue.value = values.blue;
  if (white && "white" in values) white.value = values.white;
}

function syncStrobeFromState(profile, values) {
  const dimmerSlider = document.getElementById("strobe-dimmer-slider");
  const strobeSlider = document.getElementById("strobe-slider");
  if (isBeingEdited(dimmerSlider) || isBeingEdited(strobeSlider)) return;
  if ("dimmer" in values) {
    const logical = logicalFromRaw(profile, "dimmer", values.dimmer);
    setDimmerUi(Math.round((logical * 100) / 255));
  }
  if ("strobe" in values || "shutter" in values) {
    const raw = "strobe" in values ? values.strobe : values.shutter;
    const role = "strobe" in values ? "strobe" : "shutter";
    const logical = logicalFromRaw(profile, role, raw);
    setStrobeUi(Math.round((logical * 100) / 255));
  }
}

function syncPanTiltFromState(profile, values) {
  const pan = document.getElementById("pan-slider");
  const tilt = document.getElementById("tilt-slider");
  const pad = document.getElementById("xy-pad");
  const dot = document.getElementById("xy-dot");
  if (isBeingEdited(pan) || isBeingEdited(tilt) || padDragging) return;
  if (pan && "pan" in values) pan.value = values.pan;
  if (tilt && "tilt" in values) tilt.value = values.tilt;
  if (pad && dot && "pan" in values && "tilt" in values) {
    dot.style.left = `${(values.pan / 255) * 100}%`;
    dot.style.top = `${(values.tilt / 255) * 100}%`;
  }
}

function initRgbPanel() {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  const picker = document.getElementById("rgb-picker");

  const pushColor = () => {
    forEachTarget((id) =>
      api.setColor(id, Number(red.value), Number(green.value), Number(blue.value), Number(white.value),
        state.zoneFilterList())
        .catch(console.error)
    );
  };
  [red, green, blue, white].forEach((el) => el.addEventListener("input", pushColor));

  picker.addEventListener("input", () => {
    const hex = picker.value;
    red.value = parseInt(hex.slice(1, 3), 16);
    green.value = parseInt(hex.slice(3, 5), 16);
    blue.value = parseInt(hex.slice(5, 7), 16);
    pushColor();
  });

  const palette = document.getElementById("rgb-palette");
  const swatches = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff",
                     "#ffffff", "#ff8000", "#8000ff", "#000000"];
  for (const color of swatches) {
    const sw = document.createElement("div");
    sw.style.background = color;
    sw.onclick = () => {
      picker.value = color;
      picker.dispatchEvent(new Event("input"));
    };
    palette.appendChild(sw);
  }
}

// ---- dimmer / strobe / shutter (freestyler-style) ------------------------
//
// Sliders show 0-100 %. Values go to the backend as 0-255 and the ENGINE maps
// them onto each fixture's real dimmer/strobe DMX values (fixtures differ a
// lot), so mixed fixtures behave alike. The UI only mirrors the operator
// rules so the sliders tell the truth:
//   * every selected fixture has ONE shared dimmer/strobe channel:
//       touch strobe -> dimmer drops to 0;  touch dimmer -> strobe drops to 0
//   * a mixed selection (e.g. a moving head + a Beamz): sliders are held and
//     everything strobes together, each mapped to its own values.

const pctToLogical = (pct) => Math.round((Number(pct) * 255) / 100);

function setSlider(sliderId, pctId, pct) {
  const slider = document.getElementById(sliderId);
  if (slider) slider.value = pct;
  const label = document.getElementById(pctId);
  if (label) label.textContent = `${Math.round(pct)}%`;
}

// Dimmer lives only in the Strobe/Shutter (light control) window -- not
// duplicated in the RGB panel.
function setDimmerUi(pct) {
  setSlider("strobe-dimmer-slider", "strobe-dimmer-pct", pct);
}

function setStrobeUi(pct) {
  setSlider("strobe-slider", "strobe-pct", pct);
}

function currentDimmerPct() {
  return Number(document.getElementById("strobe-dimmer-slider").value);
}

// True when every selected fixture that has a dimmer/strobe at all shares
// one channel for them (same rule the engine applies).
function selectionSharesLightChannel() {
  let relevant = 0;
  let shared = 0;
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

function sendToSelection(call) {
  const ids = state.expandedFixtureIds();
  if (ids.length === 0) return;
  call(ids).catch(console.error);
}

function onDimmerInput(pct) {
  setDimmerUi(pct);
  if (selectionSharesLightChannel()) setStrobeUi(0);
  sendToSelection((ids) => api.setDimmer(ids, pctToLogical(pct)));
}

function onStrobeInput(pct) {
  setStrobeUi(pct);
  if (pct > 0 && selectionSharesLightChannel()) setDimmerUi(0);
  sendToSelection((ids) => api.setStrobe(ids, pctToLogical(pct)));
}

function onShutter(closed) {
  if (!closed) {
    setStrobeUi(0);
    // "Open" is never dark: a shared-channel selection that was left at 0 by strobing goes to full
    if (selectionSharesLightChannel() && currentDimmerPct() === 0) setDimmerUi(100);
  }
  sendToSelection((ids) => api.setShutter(ids, closed));
}

function initStrobePanel() {
  document.getElementById("strobe-dimmer-slider").addEventListener("input", (e) =>
    onDimmerInput(Number(e.target.value)));
  document.getElementById("strobe-slider").addEventListener("input", (e) =>
    onStrobeInput(Number(e.target.value)));

  document.querySelectorAll("#panel-strobe [data-strobe]").forEach((btn) => {
    btn.onclick = () => onStrobeInput(Number(btn.dataset.strobe));
  });
  document.getElementById("shutter-closed").onclick = () => onShutter(true);
  document.getElementById("shutter-open").onclick = () => onShutter(false);
}

let padDragging = false;

function initPanTiltPanel() {
  const pan = document.getElementById("pan-slider");
  const panFine = document.getElementById("pan-fine-slider");
  const tilt = document.getElementById("tilt-slider");
  const tiltFine = document.getElementById("tilt-fine-slider");
  const pad = document.getElementById("xy-pad");
  const dot = document.getElementById("xy-dot");

  const pushPanTilt = () => {
    forEachTarget((id) =>
      api.setPanTilt(id, Number(pan.value), Number(tilt.value), Number(panFine.value), Number(tiltFine.value))
        .catch(console.error)
    );
  };
  [pan, panFine, tilt, tiltFine].forEach((el) => el.addEventListener("input", pushPanTilt));

  function setFromPad(evt) {
    const rect = pad.getBoundingClientRect();
    const fx = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width));
    const fy = Math.min(1, Math.max(0, (evt.clientY - rect.top) / rect.height));
    dot.style.left = `${fx * 100}%`;
    dot.style.top = `${fy * 100}%`;
    pan.value = Math.round(fx * 255);
    tilt.value = Math.round(fy * 255);
    pushPanTilt();
  }

  pad.addEventListener("mousedown", (e) => { padDragging = true; setFromPad(e); });
  window.addEventListener("mousemove", (e) => { if (padDragging) setFromPad(e); });
  window.addEventListener("mouseup", () => { padDragging = false; });
}

let lastCustomPanelKey = "";

function renderCustomPanel() {
  const container = document.getElementById("custom-sliders");
  const fixtureIds = state.expandedFixtureIds();
  const key = fixtureIds.slice().sort().join(",");
  if (key === lastCustomPanelKey) return; // avoid tearing out sliders mid-drag on every WS tick
  lastCustomPanelKey = key;
  container.innerHTML = "";

  const customByFixture = fixtureIds
    .map((id) => {
      const fixture = state.fixtureById(id);
      if (!fixture) return null;
      const profile = state.profileById(fixture.profile_id);
      if (!profile || !profile.custom_channels || profile.custom_channels.length === 0) return null;
      return { fixture, profile };
    })
    .filter(Boolean);

  if (customByFixture.length === 0) {
    container.innerHTML = '<div class="hint">No custom (unmapped) channels for the current selection.</div>';
    return;
  }

  for (const { fixture, profile } of customByFixture) {
    for (const custom of profile.custom_channels) {
      if (custom.ranges && custom.ranges.length) {
        container.appendChild(buildRangeControl(fixture, custom));
        continue;
      }
      const label = document.createElement("label");
      label.textContent = `${fixture.name}: ${custom.label}`;
      const input = document.createElement("input");
      input.type = "range";
      input.min = custom.min_value;
      input.max = custom.max_value;
      input.value = custom.default;
      input.addEventListener("input", () => {
        api.setCustom(fixture.id, custom.label, Number(input.value)).catch(console.error);
      });
      label.appendChild(input);
      container.appendChild(label);
    }
  }
}


// ---- custom channels with NAMED RANGES (a motor's Low/Medium/Fast, a strobe's Slow/Medium/Max, a
// fixture's built-in programs): a slider across the whole channel that is always there, plus one
// preset button per range that jumps to the start of that range. The button for the range the value
// is in stays lit. Sends the raw DMX value, like the plain custom sliders.

function rangeFor(custom, value) {
  return custom.ranges.find((r) => value >= r.min && value <= r.max) || null;
}

function buildRangeControl(fixture, custom) {
  const wrap = document.createElement("div");
  wrap.className = "range-control";
  wrap.dataset.fixture = fixture.id;
  wrap.dataset.channel = String(custom.channel);

  const title = document.createElement("div");
  title.className = "range-title";
  title.textContent = `${fixture.name}: ${custom.label}`;
  wrap.appendChild(title);

  const buttons = document.createElement("div");
  buttons.className = "btn-row wrap";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.className = "range-speed";
  slider.min = custom.min_value;
  slider.max = custom.max_value;
  const send = (value) => api.setCustom(fixture.id, custom.label, value).catch(console.error);

  // shows `value` on the slider and lights the preset for the range it falls in
  const show = (value) => {
    slider.value = value;
    const range = rangeFor(custom, value);
    buttons.querySelectorAll("button").forEach((b) => b.classList.toggle("active", !!range && b.dataset.label === range.label));
  };

  for (const range of custom.ranges) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = range.label;
    btn.dataset.label = range.label;
    btn.title = `DMX ${range.min}-${range.max}`;
    btn.onclick = () => { show(range.min); send(range.min); };
    buttons.appendChild(btn);
  }
  slider.addEventListener("input", () => { show(Number(slider.value)); send(Number(slider.value)); });

  wrap.append(buttons, slider);
  wrap._show = show;
  wrap._slider = slider;
  show(custom.default);
  return wrap;
}

function syncCustomRangesFromState() {
  document.querySelectorAll("#custom-sliders .range-control").forEach((wrap) => {
    const values = state.fixtureState[wrap.dataset.fixture]?.values;
    const value = values && values[`custom_${wrap.dataset.channel}`];
    if (value === undefined || isBeingEdited(wrap._slider)) return;
    wrap._show(value);
  });
}
