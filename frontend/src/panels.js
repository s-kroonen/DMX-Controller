import { api } from "./api.js";
import { state, onStateChange } from "./state.js";

function currentTargetIds() {
  return [...state.selection];
}

function forEachTarget(fn) {
  const ids = currentTargetIds();
  if (ids.length === 0) return;
  ids.forEach(fn);
}

export function initPanels() {
  makeDraggable(document.getElementById("panel-rgb"));
  makeDraggable(document.getElementById("panel-strobe"));
  makeDraggable(document.getElementById("panel-pantilt"));
  makeDraggable(document.getElementById("panel-custom"));

  document.querySelectorAll(".panel-close").forEach((btn) => {
    btn.onclick = (e) => {
      e.target.closest(".floating-panel").style.display = "none";
    };
  });

  document.querySelectorAll(".toggle-btn").forEach((btn) => {
    if (btn.id === "btn-view-3d") {
      btn.onclick = () => btn.classList.toggle("active");
    }
  });

  initRgbPanel();
  initStrobePanel();
  initPanTiltPanel();

  onStateChange(renderCustomPanel);
}

function makeDraggable(panel) {
  const header = panel.querySelector(".panel-header");
  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  header.addEventListener("mousedown", (e) => {
    if (e.target.classList.contains("panel-close")) return;
    dragging = true;
    const rect = panel.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    panel.style.left = `${e.clientX - offsetX}px`;
    panel.style.top = `${e.clientY - offsetY}px`;
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
  });
}

function initRgbPanel() {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  const picker = document.getElementById("rgb-picker");
  const dimmer = document.getElementById("dimmer-slider");

  const pushColor = () => {
    forEachTarget((id) =>
      api.setColor(id, Number(red.value), Number(green.value), Number(blue.value), Number(white.value))
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

  dimmer.addEventListener("input", () => onDimmerInput(Number(dimmer.value)));

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

const DIMMER_SLIDERS = [["dimmer-slider", "dimmer-pct"], ["strobe-dimmer-slider", "strobe-dimmer-pct"]];

function setSlider(sliderId, pctId, pct) {
  const slider = document.getElementById(sliderId);
  if (slider) slider.value = pct;
  const label = document.getElementById(pctId);
  if (label) label.textContent = `${Math.round(pct)}%`;
}

function setDimmerUi(pct) {
  DIMMER_SLIDERS.forEach(([slider, label]) => setSlider(slider, label, pct));
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

  let dragging = false;
  pad.addEventListener("mousedown", (e) => { dragging = true; setFromPad(e); });
  window.addEventListener("mousemove", (e) => { if (dragging) setFromPad(e); });
  window.addEventListener("mouseup", () => { dragging = false; });
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
