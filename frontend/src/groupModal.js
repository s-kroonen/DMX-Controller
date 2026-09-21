import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

// New / edit group dialog, opened from the Groups section of the left menu. Groups are edited
// here (name, colour, which fixtures) instead of in the Patch window. The built-in "All lights"
// group can be renamed and recoloured, but always holds every fixture.

export const ALL_GROUP_ID = "all";
const el = (id) => document.getElementById(id);
let editingId = null;

export function initGroupModal() {
  document.querySelector("#modal-group .modal-close").onclick = closeGroupModal;
  el("group-save").onclick = saveGroup;
  el("group-name").addEventListener("keydown", (e) => { if (e.key === "Enter") saveGroup(); });
}

function closeGroupModal() {
  el("modal-group").classList.add("hidden");
}

// group: an existing group to edit, or nothing for a new one
export function openGroupModal(group) {
  editingId = group ? group.id : null;
  const isAll = !!group && group.id === ALL_GROUP_ID;
  el("group-modal-title").textContent = group ? "Edit group" : "New group";
  el("group-save").textContent = group ? "Save group" : "Create group";
  el("group-name").value = group ? group.name : "";
  el("group-color").value = group ? group.color : "#3a7bd5";
  el("group-all-note").classList.toggle("hidden", !isAll);

  const box = el("group-fixture-checks");
  box.innerHTML = "";
  if (state.room.fixtures.length === 0) box.innerHTML = '<div class="hint">No fixtures patched yet.</div>';
  for (const fixture of state.room.fixtures) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = fixture.id;
    cb.checked = isAll || (!!group && group.fixture_ids.includes(fixture.id));
    cb.disabled = isAll;
    label.append(cb, document.createTextNode(fixture.name));
    box.appendChild(label);
  }
  el("modal-group").classList.remove("hidden");
  el("group-name").focus();
}

async function saveGroup() {
  const name = el("group-name").value.trim();
  if (!name) {
    alert("Give the group a name.");
    return;
  }
  const payload = {
    name,
    color: el("group-color").value,
    fixture_ids: [...document.querySelectorAll("#group-fixture-checks input:checked")].map((cb) => cb.value),
  };
  try {
    if (editingId) await api.updateGroup(editingId, payload);
    else await api.createGroup(payload);
  } catch (err) {
    console.error(err);
    alert("Could not save the group.");
    return;
  }
  closeGroupModal();
  await reloadRoomAndGroups();
  notifyStateChange();
}

export async function deleteGroupWithConfirm(group) {
  if (group.id === ALL_GROUP_ID) return;
  if (!confirm(`Delete group "${group.name}"? The fixtures stay.`)) return;
  await api.deleteGroup(group.id);
  state.selection.delete(group.id);
  await reloadRoomAndGroups();
  notifyStateChange();
}
