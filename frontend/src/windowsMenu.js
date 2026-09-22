import { listPanels, isPanelOpen, openPanel, closePanel } from "./panelWindows.js";
import { onModeChange } from "./mode.js";

// The one place to get a closed control window back: closing a panel
// with no reopen path would strand it until localStorage is cleared, so
// this renders a dropdown of every registered panel with its open/closed
// state and a click-to-toggle.

export function initWindowsMenu() {
  const button = document.getElementById("btn-windows-menu");
  const dropdown = document.getElementById("windows-menu-dropdown");

  button.onclick = (evt) => {
    evt.stopPropagation();
    const willOpen = dropdown.classList.contains("hidden");
    if (willOpen) renderMenu();
    dropdown.classList.toggle("hidden", !willOpen);
  };

  document.addEventListener("click", (evt) => {
    if (!dropdown.contains(evt.target) && evt.target !== button) {
      dropdown.classList.add("hidden");
    }
  });

  // the same windows are called test tools while setting up and controls while running the show
  onModeChange((mode) => {
    button.innerHTML = `${mode === "edit" ? "Test tools" : "Controls"} &#9662;`;
    dropdown.classList.add("hidden");
    renderMenu();
  });
  initSetupMenu();
}

// Patch, Fixture Creator, Room Shape, Objects and Safety Zones live under one Setup button
function initSetupMenu() {
  const button = document.getElementById("btn-setup-menu");
  const dropdown = document.getElementById("setup-menu-dropdown");
  button.onclick = (evt) => {
    evt.stopPropagation();
    dropdown.classList.toggle("hidden");
  };
  dropdown.addEventListener("click", () => dropdown.classList.add("hidden"));
  document.addEventListener("click", (evt) => {
    if (!dropdown.contains(evt.target) && evt.target !== button) dropdown.classList.add("hidden");
  });
}

function renderMenu() {
  const dropdown = document.getElementById("windows-menu-dropdown");
  dropdown.innerHTML = "";
  for (const panel of listPanels()) {
    const open = isPanelOpen(panel.id);
    const btn = document.createElement("button");
    btn.innerHTML = `<span>${panel.label}</span><span class="win-state">${open ? "shown" : "closed -- click to open"}</span>`;
    btn.onclick = () => {
      if (open) {
        closePanel(panel.id);
      } else {
        openPanel(panel.id);
      }
      renderMenu();
    };
    dropdown.appendChild(btn);
  }
}
