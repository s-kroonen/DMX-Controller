import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

export function initPatchModal() {
  document.getElementById("btn-patch").onclick = () => openModal();
  document.querySelector("#modal-patch .modal-close").onclick = () => closeModal();

  refreshProfileSelect();
  document.getElementById("patch-submit").onclick = submitPatch;
  document.getElementById("group-create-btn").onclick = submitGroup;
}

function openModal() {
  refreshProfileSelect();
  renderPatchList();
  renderGroupFixturePicker();
  document.getElementById("modal-patch").classList.remove("hidden");
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
  await api.addFixture(payload);
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
    const profile = state.profileById(fixture.profile_id);
    row.innerHTML = `<span>${fixture.name} — ${profile ? profile.name : fixture.profile_id} @${fixture.start_address}</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
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
