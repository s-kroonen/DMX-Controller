import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

// The form doubles as both "patch a new fixture" and "edit an existing
// one" -- editingFixtureId tracks which mode we're in. Clicking "Edit" on
// a patched fixture loads its current values into the same fields
// instead of always creating a new fixture on submit (the bug this fixes:
// there was no way to see/adjust a fixture's actual numbers after
// patching it, only to nudge it roughly with the 3D gizmo).
let editingFixtureId = null;

export function initPatchModal() {
  document.getElementById("btn-patch").onclick = () => openModal();
  document.querySelector("#modal-patch .modal-close").onclick = () => closeModal();

  refreshProfileSelect();
  document.getElementById("patch-submit").onclick = submitPatch;
  document.getElementById("patch-cancel-edit").onclick = () => stopEditing();
  document.getElementById("group-create-btn").onclick = submitGroup;
}

function openModal() {
  refreshProfileSelect();
  renderPatchList();
  renderGroupFixturePicker();
  document.getElementById("modal-patch").classList.remove("hidden");
}

function fillForm(fixture) {
  document.getElementById("patch-name").value = fixture.name;
  document.getElementById("patch-profile").value = fixture.profile_id;
  document.getElementById("patch-universe").value = fixture.universe;
  document.getElementById("patch-address").value = fixture.start_address;
  document.getElementById("patch-x").value = fixture.position.x;
  document.getElementById("patch-y").value = fixture.position.y;
  document.getElementById("patch-z").value = fixture.position.z;
  document.getElementById("patch-yaw").value = fixture.orientation.yaw_deg;
  document.getElementById("patch-pitch").value = fixture.orientation.pitch_deg;
  document.getElementById("patch-invert-pan").checked = fixture.inverted_pan;
  document.getElementById("patch-invert-tilt").checked = fixture.inverted_tilt;
}

function clearForm() {
  document.getElementById("patch-name").value = "";
  document.getElementById("patch-universe").value = 1;
  document.getElementById("patch-address").value = 1;
  document.getElementById("patch-x").value = 0;
  document.getElementById("patch-y").value = 0;
  document.getElementById("patch-z").value = 3;
  document.getElementById("patch-yaw").value = 0;
  document.getElementById("patch-pitch").value = 0;
  document.getElementById("patch-invert-pan").checked = false;
  document.getElementById("patch-invert-tilt").checked = false;
}

function startEditing(fixture) {
  editingFixtureId = fixture.id;
  fillForm(fixture);
  document.getElementById("patch-submit").textContent = "Update Fixture";
  document.getElementById("patch-cancel-edit").classList.remove("hidden");
  renderPatchList();
}

function stopEditing() {
  editingFixtureId = null;
  clearForm();
  document.getElementById("patch-submit").textContent = "Add Fixture";
  document.getElementById("patch-cancel-edit").classList.add("hidden");
  renderPatchList();
}

function closeModal() {
  document.getElementById("modal-patch").classList.add("hidden");
}

function refreshProfileSelect() {
  const select = document.getElementById("patch-profile");
  select.innerHTML = "";
  for (const profile of state.profiles) {
    const opt = document.createElement("option");
    opt.value = profile.id;
    opt.textContent = `${profile.name} (${profile.channel_count}ch)`;
    select.appendChild(opt);
  }
}

async function submitPatch() {
  const payload = {
    name: document.getElementById("patch-name").value || "New Fixture",
    profile_id: document.getElementById("patch-profile").value,
    universe: Number(document.getElementById("patch-universe").value),
    start_address: Number(document.getElementById("patch-address").value),
    position: {
      x: Number(document.getElementById("patch-x").value),
      y: Number(document.getElementById("patch-y").value),
      z: Number(document.getElementById("patch-z").value),
    },
    orientation: {
      yaw_deg: Number(document.getElementById("patch-yaw").value),
      pitch_deg: Number(document.getElementById("patch-pitch").value),
      roll_deg: 0,
    },
    inverted_pan: document.getElementById("patch-invert-pan").checked,
    inverted_tilt: document.getElementById("patch-invert-tilt").checked,
  };
  if (!payload.profile_id) {
    alert("No fixture profiles available -- create one first.");
    return;
  }
  if (editingFixtureId) {
    await api.updateFixture(editingFixtureId, payload);
  } else {
    await api.addFixture(payload);
  }
  stopEditing();
  await reloadRoomAndGroups();
  renderPatchList();
  renderGroupFixturePicker();
}

function renderPatchList() {
  const container = document.getElementById("patch-list");
  container.innerHTML = "";
  for (const fixture of state.room.fixtures) {
    const row = document.createElement("div");
    row.className = "patch-row";
    if (fixture.id === editingFixtureId) row.classList.add("selected");
    const profile = state.profileById(fixture.profile_id);
    const span = document.createElement("span");
    span.textContent = `${fixture.name} — ${profile ? profile.name : fixture.profile_id} @${fixture.start_address} (${fixture.position.x}, ${fixture.position.y}, ${fixture.position.z})`;
    row.appendChild(span);
    const edit = document.createElement("button");
    edit.textContent = "Edit";
    edit.onclick = () => startEditing(fixture);
    row.appendChild(edit);
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      if (fixture.id === editingFixtureId) stopEditing();
      await api.deleteFixture(fixture.id);
      await reloadRoomAndGroups();
      renderPatchList();
      renderGroupFixturePicker();
    };
    row.appendChild(del);
    container.appendChild(row);
  }
}

function renderGroupFixturePicker() {
  const container = document.getElementById("group-fixture-picker");
  container.innerHTML = "";
  for (const fixture of state.room.fixtures) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = fixture.id;
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + fixture.name));
    container.appendChild(label);
  }

  const groupList = document.createElement("div");
  groupList.style.marginTop = "10px";
  for (const group of state.groups) {
    const row = document.createElement("div");
    row.className = "group-row";
    row.innerHTML = `<span>${group.name} (${group.fixture_ids.length})</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      await api.deleteGroup(group.id);
      await reloadRoomAndGroups();
      renderGroupFixturePicker();
    };
    row.appendChild(del);
    groupList.appendChild(row);
  }
  container.appendChild(groupList);
}

async function submitGroup() {
  const name = document.getElementById("group-name-input").value;
  if (!name) return;
  const checked = [...document.querySelectorAll("#group-fixture-picker input[type=checkbox]:checked")];
  const fixtureIds = checked.map((cb) => cb.value);
  await api.createGroup({ name, fixture_ids: fixtureIds });
  await reloadRoomAndGroups();
  document.getElementById("group-name-input").value = "";
  renderGroupFixturePicker();
  notifyStateChange();
}
