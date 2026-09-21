import { state, notifyStateChange } from "./state.js";
import { openGroupModal, deleteGroupWithConfirm, ALL_GROUP_ID } from "./groupModal.js";
import { openPatchModal, deleteFixtureWithConfirm } from "./patch.js";

// The left menu: groups and fixtures as buttons. Click selects (shift-click adds). In edit mode
// (the Edit button at the top) every item also shows edit and delete buttons, and the + buttons
// add a group / fixture. Groups use their own dialog; fixtures go through the Patch window.
//
// The WS broadcast triggers a state-change notification ~10x/second even
// when nothing selection-relevant changed. Rebuilding the button DOM that
// often tears elements out from under in-flight clicks/drags, so buttons
// are only rebuilt when what they show actually changes (ids, names, colours);
// otherwise just the "selected" class is refreshed on the existing nodes.

let lastGroupKey = "";
let lastFixtureKey = "";

export function initSidebar() {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("sidebar-edit");
  toggle.onclick = () => {
    const on = !sidebar.classList.contains("editing");
    sidebar.classList.toggle("editing", on);
    toggle.classList.toggle("active", on);
    toggle.textContent = on ? "Done" : "Edit";
  };
  document.getElementById("group-add").onclick = () => openGroupModal();
  document.getElementById("fixture-add").onclick = () => openPatchModal();
}

export function renderSidebar() {
  renderGroupButtons();
  renderFixtureButtons();
  renderSelectionSummary();
}

function miniButton(text, title, onClick, className = "") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `mini-btn ${className}`.trim();
  btn.textContent = text;
  btn.title = title;
  btn.onclick = (evt) => { evt.stopPropagation(); onClick(); };
  return btn;
}

// One menu entry: the select button plus the edit-mode buttons (hidden by CSS outside edit mode)
function buildItem(id, label, { color, onEdit, onDelete, noun }) {
  const item = document.createElement("div");
  item.className = "select-item";
  item.dataset.id = id;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "select-btn";
  btn.textContent = label;
  if (color) btn.style.background = color;
  btn.onclick = (evt) => {
    state.toggleSelection(id, !evt.shiftKey);
    notifyStateChange();
  };
  item.appendChild(btn);
  const tools = document.createElement("span");
  tools.className = "item-tools";
  tools.appendChild(miniButton("✎", `Edit ${noun}`, onEdit));
  if (onDelete) tools.appendChild(miniButton("✕", `Delete ${noun}`, onDelete, "danger"));
  item.appendChild(tools);
  return item;
}

function renderGroupButtons() {
  const container = document.getElementById("group-buttons");
  const key = JSON.stringify(state.groups.map((g) => [g.id, g.name, g.color, g.fixture_ids.length]));
  if (key !== lastGroupKey) {
    lastGroupKey = key;
    container.innerHTML = "";
    for (const group of state.groups) {
      const item = buildItem(group.id, group.name, {
        color: group.color,
        noun: "group",
        onEdit: () => openGroupModal(state.groupById(group.id) || group),
        onDelete: group.id === ALL_GROUP_ID ? null : () => deleteGroupWithConfirm(state.groupById(group.id) || group),
      });
      item.firstChild.title = `${group.fixture_ids.length} fixture(s)`;
      container.appendChild(item);
    }
  }
  updateSelectedClasses(container);
}

function renderFixtureButtons() {
  const container = document.getElementById("fixture-buttons");
  const key = JSON.stringify(state.room.fixtures.map((f) => [f.id, f.name]));
  if (key !== lastFixtureKey) {
    lastFixtureKey = key;
    container.innerHTML = "";
    for (const fixture of state.room.fixtures) {
      container.appendChild(buildItem(fixture.id, fixture.name, {
        noun: "fixture",
        onEdit: () => openPatchModal(state.fixtureById(fixture.id) || fixture),
        onDelete: () => deleteFixtureWithConfirm(state.fixtureById(fixture.id) || fixture),
      }));
    }
  }
  updateSelectedClasses(container);
}

function updateSelectedClasses(container) {
  for (const item of container.children) {
    item.firstChild.classList.toggle("selected", state.isSelected(item.dataset.id));
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
