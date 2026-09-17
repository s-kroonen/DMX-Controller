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

  dimmer.addEventListener("input", () => {
    forEachTarget((id) => api.setDimmer(id, Number(dimmer.value)).catch(console.error));
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

function initStrobePanel() {
  const slider = document.getElementById("strobe-slider");
  slider.addEventListener("input", () => {
    forEachTarget((id) => api.setStrobe(id, Number(slider.value)).catch(console.error));
  });
  document.querySelectorAll("#panel-strobe [data-strobe]").forEach((btn) => {
    btn.onclick = () => {
      const value = Number(btn.dataset.strobe);
      slider.value = value;
      forEachTarget((id) => api.setStrobe(id, value).catch(console.error));
    };
  });
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
