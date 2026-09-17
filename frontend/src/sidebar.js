import { state, notifyStateChange } from "./state.js";

// The WS broadcast triggers a state-change notification ~10x/second even
// when nothing selection-relevant changed. Rebuilding the button DOM that
// often tears elements out from under in-flight clicks/drags, so buttons
// are only rebuilt when the underlying id list actually changes; otherwise
// just the "selected" class is refreshed on the existing nodes.

let lastGroupIds = "";
let lastFixtureIds = "";

export function renderSidebar() {
  renderGroupButtons();
  renderFixtureButtons();
  renderSelectionSummary();
}

function renderGroupButtons() {
  const container = document.getElementById("group-buttons");
  const idKey = state.groups.map((g) => g.id).join(",");
  if (idKey !== lastGroupIds) {
    lastGroupIds = idKey;
    container.innerHTML = "";
    for (const group of state.groups) {
      const btn = document.createElement("button");
      btn.dataset.id = group.id;
      btn.style.background = group.color;
      btn.textContent = group.name;
      btn.title = `${group.fixture_ids.length} fixture(s)`;
      btn.onclick = (evt) => {
        state.toggleSelection(group.id, !evt.shiftKey);
        notifyStateChange();
      };
      container.appendChild(btn);
    }
  }
  updateSelectedClasses(container);
}

function renderFixtureButtons() {
  const container = document.getElementById("fixture-buttons");
  const idKey = state.room.fixtures.map((f) => f.id).join(",");
  if (idKey !== lastFixtureIds) {
    lastFixtureIds = idKey;
    container.innerHTML = "";
    for (const fixture of state.room.fixtures) {
      const btn = document.createElement("button");
      btn.dataset.id = fixture.id;
      btn.textContent = fixture.name;
      btn.onclick = (evt) => {
        state.toggleSelection(fixture.id, !evt.shiftKey);
        notifyStateChange();
      };
      container.appendChild(btn);
    }
  }
  updateSelectedClasses(container);
}

function updateSelectedClasses(container) {
  for (const btn of container.children) {
    btn.className = "select-btn" + (state.isSelected(btn.dataset.id) ? " selected" : "");
  }
}

function renderSelectionSummary() {
  const el = document.getElementById("selection-text");
  if (state.selection.size === 0) {
    el.textContent = "Nothing selected";
    return;
  }
  const names = [...state.selection].map((id) => {
    const fx = state.fixtureById(id);
    if (fx) return fx.name;
    const grp = state.groupById(id);
    if (grp) return `[group] ${grp.name}`;
    return id;
  });
  el.textContent = names.join(", ");
}
