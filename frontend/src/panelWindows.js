import { loadPref, savePref } from "./uiPrefs.js";
import { onModeChange } from "./mode.js";

// Chrome behavior shared by every floating control window: dragging,
// closing, and -- the actual point of this module -- remembering
// position/open-closed state across a page refresh, plus a registry so
// a "Windows" menu can reopen anything that got closed (otherwise a
// closed window with no reopen path is gone until you clear storage).

const PANELS = [
  { id: "rgb", label: "RGB / Color" },
  { id: "strobe", label: "Strobe / Shutter" },
  { id: "pantilt", label: "Pan / Tilt" },
  { id: "zones", label: "Zones" },
  { id: "custom", label: "Custom Channels" },
  { id: "sound", label: "Sound", mode: "show" },
  { id: "calibrate", label: "Calibrate", mode: "edit", menu: false }, // has its own header button
];

// Each mode remembers its own layout: the control windows are what you run the show with, but in
// edit mode they are test tools and start closed.
const currentMode = () => document.body.dataset.mode || "edit";

function prefKey(panelId, mode = currentMode()) {
  return `panel:${mode}:${panelId}`;
}

const markupDefaults = new Map(); // panel id -> {display, left, top} as written in index.html

function elementFor(panelId) {
  return document.getElementById(`panel-${panelId}`);
}

// The windows the Controls / Test tools menu offers in the current mode
export function listPanels() {
  const mode = currentMode();
  return PANELS.filter((panel) => panel.menu !== false && (!panel.mode || panel.mode === mode));
}

export function isPanelOpen(panelId) {
  const el = elementFor(panelId);
  return el ? el.style.display !== "none" : false;
}

export function openPanel(panelId) {
  const el = elementFor(panelId);
  if (!el) return;
  el.style.display = "";
  savePanelPref(panelId);
}

export function closePanel(panelId) {
  const el = elementFor(panelId);
  if (!el) return;
  el.style.display = "none";
  savePanelPref(panelId);
  document.dispatchEvent(new CustomEvent("panel-closed", { detail: panelId }));
}

function savePanelPref(panelId) {
  const el = elementFor(panelId);
  if (!el) return;
  savePref(prefKey(panelId), {
    open: el.style.display !== "none",
    left: el.style.left || null,
    top: el.style.top || null,
  });
}

// Show the layout this mode last had; with none saved, show mode uses the markup's defaults and
// edit mode starts with every window closed.
function applyLayout(mode) {
  for (const { id } of PANELS) {
    const el = elementFor(id);
    if (!el) continue;
    const defaults = markupDefaults.get(id);
    const saved = loadPref(prefKey(id, mode), null) || (mode === "show" ? loadPref(`panel:${id}`, null) : null);
    el.style.left = (saved && saved.left) || defaults.left;
    el.style.top = (saved && saved.top) || defaults.top;
    if (saved) el.style.display = saved.open === false ? "none" : "";
    else el.style.display = mode === "edit" ? "none" : defaults.display;
  }
}

export function initPanelWindows() {
  for (const { id } of PANELS) {
    const el = elementFor(id);
    if (!el) continue;
    markupDefaults.set(id, { display: el.style.display, left: el.style.left, top: el.style.top });

    makeDraggable(el, () => savePanelPref(id));

    const closeBtn = el.querySelector(".panel-close");
    if (closeBtn) {
      closeBtn.onclick = () => closePanel(id);
    }
  }
  onModeChange(applyLayout); // runs now for the current mode, and again on every switch
}

function makeDraggable(panel, onDragEnd) {
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
    if (dragging) {
      dragging = false;
      onDragEnd();
    }
  });
}
