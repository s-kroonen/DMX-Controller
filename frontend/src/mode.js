import { api } from "./api.js";
import { state } from "./state.js";

// Edit / show mode, shared by the desktop and phone UIs. The backend holds the mode (so every
// screen agrees, and refuses what the mode does not allow); this module shows it, switches it,
// and lets the markup follow it:
//
//   <body data-mode="edit|show">, and CSS hides  .edit-only  in show mode and  .show-only  in
//   edit mode, so a screen only offers what works right now.
//
// Edit: patch and move fixtures, change the room, groups and saved rooms.
// Show: run effects and shows, point moving heads. Colors, dimmer, strobe, custom channels and
// blackout work in both.

const listeners = [];

export function isEdit() {
  return state.mode === "edit";
}

export function isShow() {
  return state.mode === "show";
}

// fn(mode) runs whenever the mode changes (and once right away with the current one)
export function onModeChange(fn) {
  listeners.push(fn);
  fn(state.mode || "edit");
}

// Called on every state change; cheap when the mode did not change.
export function applyMode() {
  const mode = state.mode || "edit";
  if (document.body.dataset.mode === mode) return;
  document.body.dataset.mode = mode;
  document.querySelectorAll(".mode-switch button").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.mode === mode));
  for (const fn of listeners) fn(mode);
}

export function initModeSwitch() {
  document.querySelectorAll(".mode-switch button").forEach((btn) => {
    btn.onclick = async () => {
      const wanted = btn.dataset.mode;
      if (wanted === state.mode) return;
      // leaving show mode stops the effects and shows: never by accident in the middle of a show
      if (wanted === "edit" && !confirm("Switch to edit mode? Running effects and shows stop.")) return;
      try {
        await api.setMode(wanted);
        state.mode = wanted;
        applyMode();
      } catch (err) {
        notify(err.message);
      }
    };
  });
  window.addEventListener("dmx-notice", (evt) => notify(evt.detail));
  applyMode();
}

// A short message that does not block the page (the backend refusing something the mode forbids)
export function notify(message) {
  let box = document.getElementById("toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast";
    document.body.appendChild(box);
  }
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(box._timer);
  box._timer = setTimeout(() => box.classList.remove("show"), 3500);
}
