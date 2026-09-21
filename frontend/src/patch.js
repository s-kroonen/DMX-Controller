import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";
import { wireMountSelect } from "./mounting.js";

// The Patch window: adds a fixture, or edits an existing one. It opens from the Patch button, or
// from the + / edit buttons in the left menu's Fixtures section.

const el = (id) => document.getElementById(id);
let editing = null; // the fixture being edited, or null when adding
let syncMount = () => {};

export function initPatchModal() {
  syncMount = wireMountSelect("patch-mount", "patch-pitch");
  el("btn-patch").onclick = () => openPatchModal();
  document.querySelector("#modal-patch .modal-close").onclick = closeModal;
  refreshProfileSelect();
  el("patch-submit").onclick = submitPatch;
}

// fixture: an existing fixture to edit, or nothing to patch a new one
export function openPatchModal(fixture) {
  editing = fixture || null;
  refreshProfileSelect();
  el("patch-title").textContent = editing ? "Edit Fixture" : "Patch Fixture";
  el("patch-submit").textContent = editing ? "Save Fixture" : "Add Fixture";
  if (editing) {
    el("patch-name").value = editing.name;
    el("patch-profile").value = editing.profile_id;
    el("patch-universe").value = editing.universe;
    el("patch-address").value = editing.start_address;
    el("patch-x").value = editing.position.x;
    el("patch-y").value = editing.position.y;
    el("patch-z").value = editing.position.z;
    el("patch-yaw").value = editing.orientation.yaw_deg;
    el("patch-pitch").value = editing.orientation.pitch_deg;
    el("patch-invert-pan").checked = editing.inverted_pan;
    el("patch-invert-tilt").checked = editing.inverted_tilt;
  } else {
    el("patch-name").value = "";
    el("patch-universe").value = 1;
    el("patch-address").value = nextFreeAddress();
    for (const id of ["patch-x", "patch-y", "patch-z", "patch-yaw"]) el(id).value = 0;
    el("patch-pitch").value = 180;
    el("patch-invert-pan").checked = false;
    el("patch-invert-tilt").checked = false;
  }
  syncMount();
  el("modal-patch").classList.remove("hidden");
  el("patch-name").focus();
}

function closeModal() {
  el("modal-patch").classList.add("hidden");
}

// First DMX address after the last patched fixture's channels (universe 1), as a starting suggestion
function nextFreeAddress() {
  let next = 1;
  for (const fixture of state.room.fixtures) {
    if (fixture.universe !== 1) continue;
    const profile = state.profileById(fixture.profile_id);
    next = Math.max(next, fixture.start_address + (profile ? profile.channel_count : 1));
  }
  return next <= 512 ? next : 1;
}

function refreshProfileSelect() {
  const select = el("patch-profile");
  const previous = select.value;
  select.innerHTML = "";
  for (const profile of state.profiles) {
    const opt = document.createElement("option");
    opt.value = profile.id;
    opt.textContent = `${profile.name} (${profile.channel_count}ch)`;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
}

async function submitPatch() {
  const payload = {
    name: el("patch-name").value || "New Fixture",
    profile_id: el("patch-profile").value,
    universe: Number(el("patch-universe").value),
    start_address: Number(el("patch-address").value),
    position: { x: Number(el("patch-x").value), y: Number(el("patch-y").value), z: Number(el("patch-z").value) },
    orientation: {
      yaw_deg: Number(el("patch-yaw").value),
      pitch_deg: Number(el("patch-pitch").value),
      roll_deg: editing ? editing.orientation.roll_deg || 0 : 0,
    },
    inverted_pan: el("patch-invert-pan").checked,
    inverted_tilt: el("patch-invert-tilt").checked,
  };
  if (!payload.profile_id) {
    alert("No fixture profiles available -- create one first.");
    return;
  }
  try {
    if (editing) {
      // this window doesn't show the calibration trim or the fixture's own group list: keep them
      await api.updateFixture(editing.id, {
        ...payload,
        pan_offset_deg: editing.pan_offset_deg || 0,
        tilt_offset_deg: editing.tilt_offset_deg || 0,
        group_ids: editing.group_ids || [],
      });
    } else {
      await api.addFixture(payload);
    }
  } catch (err) {
    console.error(err);
    alert("Could not save the fixture.");
    return;
  }
  closeModal();
  await reloadRoomAndGroups();
  notifyStateChange();
}

export async function deleteFixtureWithConfirm(fixture) {
  if (!confirm(`Delete fixture "${fixture.name}"?`)) return;
  await api.deleteFixture(fixture.id);
  state.selection.delete(fixture.id);
  await reloadRoomAndGroups();
  notifyStateChange();
}
