import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";
import { loadPref, savePref } from "./uiPrefs.js";
import { wireMountSelect } from "./mounting.js";

// Inline, non-modal fixture detail editor living under the fixture list in
// the sidebar (unlike every other editor, which is a popup) -- loads the
// currently selected fixture's full data (position/rotation/limits/groups)
// for editing, and remembers whether it's collapsed across a refresh the
// same way floating panels remember open/closed state.

const COLLAPSED_PREF_KEY = "fixtureDetailsCollapsed";
const ALL_GROUP_ID = "all"; // the built-in group that always holds every fixture

let lastLoadedFixtureId = null;
// Set the moment any field is edited, cleared on save or on switching to a
// different fixture -- the WS snapshot broadcasts ~10x/second, and without
// this an edit gets silently overwritten by the next broadcast the instant
// focus leaves the field (blur, not just "still focused", was the actual
// window the old focus-only check missed).
let formDirty = false;
let syncDetailsMount = () => {};

export function initFixtureDetailsPanel() {
  syncDetailsMount = wireMountSelect("fd-mount", "fd-pitch");
  const header = document.getElementById("fixture-details-toggle");
  header.onclick = () => setCollapsed(!isCollapsed());

  document.getElementById("fd-save").onclick = saveFixture;
  document.getElementById("fd-delete").onclick = deleteFixture;

  for (const btn of document.querySelectorAll(".subtab-btn")) {
    btn.onclick = () => setActiveSubtab(btn.dataset.subtab);
  }

  const form = document.getElementById("fixture-details-form");
  form.addEventListener("input", () => { formDirty = true; });
  form.addEventListener("change", () => { formDirty = true; });

  refreshProfileSelect();
  applyCollapsed(loadPref(COLLAPSED_PREF_KEY, false));
}

function setActiveSubtab(name) {
  for (const btn of document.querySelectorAll(".subtab-btn")) {
    btn.classList.toggle("active", btn.dataset.subtab === name);
  }
  for (const pane of document.querySelectorAll(".subtab-pane")) {
    pane.classList.toggle("hidden", pane.dataset.subtabPane !== name);
  }
}

function isCollapsed() {
  return document.getElementById("fixture-details-section").classList.contains("collapsed");
}

function setCollapsed(collapsed) {
  applyCollapsed(collapsed);
  savePref(COLLAPSED_PREF_KEY, collapsed);
}

function applyCollapsed(collapsed) {
  document.getElementById("fixture-details-section").classList.toggle("collapsed", collapsed);
  document.getElementById("fixture-details-body").classList.toggle("hidden", collapsed);
  document.getElementById("fixture-details-caret").textContent = collapsed ? "▸" : "▾";
}

function refreshProfileSelect() {
  const select = document.getElementById("fd-profile");
  select.innerHTML = "";
  for (const profile of state.profiles) {
    const opt = document.createElement("option");
    opt.value = profile.id;
    opt.textContent = `${profile.name} (${profile.channel_count}ch)`;
    select.appendChild(opt);
  }
}

function currentSingleFixture() {
  const selection = [...state.selection];
  if (selection.length !== 1) return null;
  return state.fixtureById(selection[0]) || null;
}

export function renderFixtureDetailsPanel() {
  const fixture = currentSingleFixture();
  const empty = document.getElementById("fixture-details-empty");
  const form = document.getElementById("fixture-details-form");

  if (!fixture) {
    lastLoadedFixtureId = null;
    empty.classList.remove("hidden");
    form.classList.add("hidden");
    return;
  }

  empty.classList.add("hidden");
  form.classList.remove("hidden");

  // Don't stomp on an unsaved edit -- only (re)load when the selected
  // fixture changes, or when the form has no pending edits (e.g. a gizmo
  // drag just moved this same fixture and nothing here was touched).
  const fixtureChanged = fixture.id !== lastLoadedFixtureId;
  if (fixtureChanged) formDirty = false;
  if (fixtureChanged || !formDirty) {
    fillForm(fixture);
  }

  renderGroupCheckboxes(fixture);

  lastLoadedFixtureId = fixture.id;
}

function fillForm(fixture) {
  refreshProfileSelect();
  document.getElementById("fd-name").value = fixture.name;
  document.getElementById("fd-profile").value = fixture.profile_id;
  document.getElementById("fd-universe").value = fixture.universe;
  document.getElementById("fd-address").value = fixture.start_address;
  document.getElementById("fd-x").value = fixture.position.x;
  document.getElementById("fd-y").value = fixture.position.y;
  document.getElementById("fd-z").value = fixture.position.z;
  document.getElementById("fd-yaw").value = fixture.orientation.yaw_deg;
  document.getElementById("fd-pitch").value = fixture.orientation.pitch_deg;
  syncDetailsMount();
  document.getElementById("fd-roll").value = fixture.orientation.roll_deg || 0;
  document.getElementById("fd-invert-pan").checked = fixture.inverted_pan;
  document.getElementById("fd-invert-tilt").checked = fixture.inverted_tilt;
  document.getElementById("fd-pan-offset").value = fixture.pan_offset_deg || 0;
  document.getElementById("fd-tilt-offset").value = fixture.tilt_offset_deg || 0;

  // Pan/tilt mechanical range lives on the PROFILE (shared by every
  // instance of that fixture type), not the instance -- editing it here
  // is a shortcut into the profile rather than a fixture field.
  const profile = state.profileById(fixture.profile_id);
  document.getElementById("fd-pan-range").value = profile?.pan_range_deg ?? 540;
  document.getElementById("fd-tilt-range").value = profile?.tilt_range_deg ?? 270;
}

// Group membership actually lives on Group.fixture_ids (the group is the
// source of truth for who's in it), not on the fixture -- so toggling a
// checkbox here updates that group's member list via PUT /api/groups/{id}
// rather than anything on the fixture itself.
//
// The rows are built once per (fixture, group list) and afterwards only their
// checked state is refreshed in place, so a click is never interrupted by a
// rebuild. The built-in "All lights" group always holds everyone, so it is a
// plain note rather than a checkbox that could not be changed.
let groupRowsKey = "";

function renderGroupCheckboxes(fixture) {
  const container = document.getElementById("fd-groups");
  const key = JSON.stringify([fixture.id, state.groups.map((g) => [g.id, g.name])]);
  if (key !== groupRowsKey) {
    groupRowsKey = key;
    container.innerHTML = "";
    for (const group of state.groups) {
      if (group.id === ALL_GROUP_ID) {
        const note = document.createElement("div");
        note.className = "hint";
        note.textContent = `\u2713 ${group.name} (every fixture is always in it)`;
        container.appendChild(note);
        continue;
      }
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.dataset.group = group.id;
      cb.onchange = () => toggleGroupMembership(fixture.id, group.id, cb);
      label.append(cb, document.createTextNode(group.name));
      container.appendChild(label);
    }
    if (container.children.length === 0 || state.groups.every((g) => g.id === ALL_GROUP_ID)) {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = "No other groups yet -- use + Add under Groups in the menu.";
      container.appendChild(hint);
    }
  }
  container.querySelectorAll("input[data-group]").forEach((cb) => {
    if (cb.disabled) return; // a change is being saved
    const group = state.groupById(cb.dataset.group);
    cb.checked = !!group && group.fixture_ids.includes(fixture.id);
  });
}

async function toggleGroupMembership(fixtureId, groupId, cb) {
  const wanted = cb.checked;
  cb.disabled = true;
  try {
    const group = state.groupById(groupId); // read now, not when the row was built
    const others = group.fixture_ids.filter((id) => id !== fixtureId);
    await api.updateGroup(groupId, {
      name: group.name, color: group.color, fixture_ids: wanted ? [...others, fixtureId] : others,
    });
  } catch (err) {
    console.error(err);
  }
  cb.disabled = false;
  await reloadRoomAndGroups(); // the checkbox then shows what is really saved
  notifyStateChange();
}

async function saveFixture() {
  const fixture = currentSingleFixture();
  if (!fixture) return;
  const payload = {
    name: document.getElementById("fd-name").value || fixture.name,
    profile_id: document.getElementById("fd-profile").value,
    universe: Number(document.getElementById("fd-universe").value),
    start_address: Number(document.getElementById("fd-address").value),
    position: {
      x: Number(document.getElementById("fd-x").value),
      y: Number(document.getElementById("fd-y").value),
      z: Number(document.getElementById("fd-z").value),
    },
    orientation: {
      yaw_deg: Number(document.getElementById("fd-yaw").value),
      pitch_deg: Number(document.getElementById("fd-pitch").value),
      roll_deg: Number(document.getElementById("fd-roll").value) || 0,
    },
    inverted_pan: document.getElementById("fd-invert-pan").checked,
    inverted_tilt: document.getElementById("fd-invert-tilt").checked,
    pan_offset_deg: Number(document.getElementById("fd-pan-offset").value) || 0,
    tilt_offset_deg: Number(document.getElementById("fd-tilt-offset").value) || 0,
  };
  await api.updateFixture(fixture.id, payload);

  const profile = state.profileById(payload.profile_id);
  const panRange = Number(document.getElementById("fd-pan-range").value) || 540;
  const tiltRange = Number(document.getElementById("fd-tilt-range").value) || 270;
  // Only a fixture that has pan AND tilt has a mechanical range to save. Saving it for any other
  // fixture (a light bar) wrote a copy of the whole profile into the user's data with default
  // ranges, which then shadowed the bundled profile forever.
  const canPanTilt = !!(profile && profile.channels && profile.channels.pan && profile.channels.tilt);
  if (canPanTilt && (profile.pan_range_deg !== panRange || profile.tilt_range_deg !== tiltRange)) {
    await api.saveProfile({ ...profile, pan_range_deg: panRange, tilt_range_deg: tiltRange });
  }

  formDirty = false;
  await reloadRoomAndGroups();
  notifyStateChange();
}

async function deleteFixture() {
  const fixture = currentSingleFixture();
  if (!fixture) return;
  if (!confirm(`Delete fixture "${fixture.name}"?`)) return;
  await api.deleteFixture(fixture.id);
  state.selection.delete(fixture.id);
  lastLoadedFixtureId = null;
  await reloadRoomAndGroups();
  notifyStateChange();
}
