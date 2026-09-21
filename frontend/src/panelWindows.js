import { loadPref, savePref } from "./uiPrefs.js";

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
  { id: "sound", label: "Sound" },
];

function prefKey(panelId) {
  return `panel:${panelId}`;
}

function elementFor(panelId) {
  return document.getElementById(`panel-${panelId}`);
}

export function listPanels() {
  return PANELS;
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

export function initPanelWindows() {
  for (const { id } of PANELS) {
    const el = elementFor(id);
    if (!el) continue;

    const saved = loadPref(prefKey(id), null);
    if (saved) {
      if (saved.left) el.style.left = saved.left;
      if (saved.top) el.style.top = saved.top;
      el.style.display = saved.open === false ? "none" : "";
    }

    makeDraggable(el, () => savePanelPref(id));

    const closeBtn = el.querySelector(".panel-close");
    if (closeBtn) {
      closeBtn.onclick = () => closePanel(id);
    }
  }
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
