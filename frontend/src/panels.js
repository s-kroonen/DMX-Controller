import { api } from "./api.js";
import { state, onStateChange } from "./state.js";
import { initPanelWindows } from "./panelWindows.js";

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
}

function initRgbPanel() {
  const red = document.getElementById("rgb-red");
  const green = document.getElementById("rgb-green");
  const blue = document.getElementById("rgb-blue");
  const white = document.getElementById("rgb-white");
  const picker = document.getElementById("rgb-picker");

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

// Some fixtures multiplex dimmer and strobe/shutter onto the SAME physical
// DMX channel (e.g. a single "Dimmer/Strobe" channel where low values dim
// and higher values ramp strobe speed). When that's true for the current
// selection, only one of the two sliders can actually be "in effect" at
// once -- whichever the operator touched last -- so the other is greyed
// out as a visual "this value is stale" cue rather than left implying it's
// still live. When the fixture has genuinely separate channels, both
// sliders are always fully active.
let strobeLastTouched = null; // "dimmer" | "strobe" | null
let lastStrobeSelectionKey = "";

function sharesChannelWithStrobe() {
  const fixtureIds = state.expandedFixtureIds();
  for (const fid of fixtureIds) {
    const fixture = state.fixtureById(fid);
    if (!fixture) continue;
    const profile = state.profileById(fixture.profile_id);
    if (!profile) continue;
    const dimmerCh = profile.channels.dimmer;
    const strobeCh = profile.channels.strobe ?? profile.channels.shutter;
    if (dimmerCh !== undefined && strobeCh !== undefined && dimmerCh === strobeCh) {
      return true;
    }
  }
  return false;
}

function updateStrobeGreying() {
  const selectionKey = state.expandedFixtureIds().slice().sort().join(",");
  if (selectionKey !== lastStrobeSelectionKey) {
    lastStrobeSelectionKey = selectionKey;
    strobeLastTouched = null;
  }

  const shared = sharesChannelWithStrobe();
  const dimmerLabel = document.getElementById("strobe-dimmer-label");
  const speedLabel = document.getElementById("strobe-speed-label");
  const hint = document.getElementById("strobe-shared-hint");
  hint.classList.toggle("hidden", !shared);

  if (!shared) {
    dimmerLabel.classList.remove("greyed-out");
    speedLabel.classList.remove("greyed-out");
    return;
  }
  dimmerLabel.classList.toggle("greyed-out", strobeLastTouched === "strobe");
  speedLabel.classList.toggle("greyed-out", strobeLastTouched === "dimmer");
}

function initStrobePanel() {
  const dimmerSlider = document.getElementById("strobe-dimmer-slider");
  const speedSlider = document.getElementById("strobe-slider");

  dimmerSlider.addEventListener("input", () => {
    strobeLastTouched = "dimmer";
    forEachTarget((id) => api.setDimmer(id, Number(dimmerSlider.value)).catch(console.error));
    updateStrobeGreying();
  });

  speedSlider.addEventListener("input", () => {
    strobeLastTouched = "strobe";
    forEachTarget((id) => api.setStrobe(id, Number(speedSlider.value)).catch(console.error));
    updateStrobeGreying();
  });

  document.querySelectorAll("#panel-strobe [data-strobe]").forEach((btn) => {
    btn.onclick = () => {
      const value = Number(btn.dataset.strobe);
      speedSlider.value = value;
      strobeLastTouched = "strobe";
      forEachTarget((id) => api.setStrobe(id, value).catch(console.error));
      updateStrobeGreying();
    };
  });

  onStateChange(updateStrobeGreying);
  updateStrobeGreying();
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
