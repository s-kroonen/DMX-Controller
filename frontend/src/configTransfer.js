import { api } from "./api.js";

// Whole-venue config export/import: one JSON file holding room
// size/shape/fixture placement, safety zones, objects, groups, animations,
// patterns, sound config, and every fixture profile in use (bundled or
// custom) -- so loading it on a different install still knows every
// fixture's DMX channels without anything else to set up first.

export function initConfigModal() {
  document.getElementById("btn-config").onclick = () => openModal();
  document.querySelector("#modal-config .modal-close").onclick = () => closeModal();
  document.getElementById("cfg-export").onclick = exportConfig;
  document.getElementById("cfg-import").onclick = importConfig;
  document.getElementById("cfg-room-save").onclick = saveRoom;
  document.getElementById("cfg-room-new").onclick = newRoom;
}

// ---- rooms saved in the backend --------------------------------------------------------------------

const roomStatus = () => document.getElementById("cfg-room-status");

// Saved fixture profiles that were brought up to date while saving or loading
function profileNote(result) {
  const updates = (result && result.profile_updates) || [];
  if (!updates.length) return "";
  return ` Updated fixture profile${updates.length > 1 ? "s" : ""} to the current version: `
    + `${updates.map((u) => u.name).join(", ")} (the old copy is kept in data/fixtures/_replaced).`;
}

async function refreshRooms() {
  const box = document.getElementById("cfg-room-list");
  const { current, rooms } = await api.listRooms();
  document.getElementById("cfg-room-current").textContent = current || "(unnamed)";
  const nameInput = document.getElementById("cfg-room-name");
  if (!nameInput.value) nameInput.value = current || "";
  box.innerHTML = "";
  if (rooms.length === 0) box.innerHTML = '<div class="hint">No saved rooms yet.</div>';
  for (const room of rooms) {
    const row = document.createElement("div");
    row.className = "room-row" + (room.name === current ? " current" : "");
    const info = document.createElement("span");
    info.innerHTML = `${room.backup ? "" : "<b></b>"}<span class="room-meta"></span>`;
    if (!room.backup) info.querySelector("b").textContent = room.name;
    else info.prepend(document.createTextNode(room.name));
    info.querySelector(".room-meta").textContent =
      ` ${room.fixtures} fixture(s), ${room.groups} group(s) -- ${room.saved_at || ""}`;
    const load = document.createElement("button");
    load.type = "button";
    load.textContent = "Load";
    load.onclick = () => loadRoom(room);
    row.append(info, load);
    if (!room.backup) {
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "Delete";
      del.onclick = async () => {
        if (!confirm(`Delete the saved room "${room.name}"? The current room is not affected.`)) return;
        await api.deleteRoom(room.id).catch((e) => alert(e.message));
        refreshRooms();
      };
      row.appendChild(del);
    }
    box.appendChild(row);
  }
}

async function saveRoom() {
  const name = document.getElementById("cfg-room-name").value.trim();
  if (!name) {
    alert("Give the room a name.");
    return;
  }
  try {
    const saved = await api.saveRoom(name);
    roomStatus().textContent = `Saved "${saved.name}".${profileNote(saved)}`;
    await refreshRooms();
  } catch (e) {
    roomStatus().textContent = `Save failed: ${e.message}`;
  }
}

async function loadRoom(room) {
  if (!confirm(`Load "${room.name}"? It replaces the current room; the current room is kept as "Before switching".`)) return;
  try {
    const result = await api.loadRoom(room.id);
    const note = profileNote(result);
    if (note) alert(`Loaded "${result.name}".${note}`);
    location.reload();
  } catch (e) {
    roomStatus().textContent = `Load failed: ${e.message}`;
  }
}

async function newRoom() {
  const name = prompt("Name for the new, empty room:");
  if (!name || !name.trim()) return;
  if (!confirm(`Start "${name.trim()}" as an empty room? The current room is kept as "Before switching" `
    + "(save it under a name first to keep it for good).")) return;
  try {
    await api.newRoom(name.trim());
    location.reload();
  } catch (e) {
    roomStatus().textContent = `Could not start a new room: ${e.message}`;
  }
}

function openModal() {
  document.getElementById("modal-config").classList.remove("hidden");
  document.getElementById("cfg-import-status").textContent = "";
  document.getElementById("cfg-room-name").value = "";
  roomStatus().textContent = "";
  refreshRooms().catch((e) => { roomStatus().textContent = e.message; });
}

function closeModal() {
  document.getElementById("modal-config").classList.add("hidden");
}

async function exportConfig() {
  try {
    const bundle = await api.exportConfig();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `dmx-controller-config-${stamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(`Export failed: ${e.message}`);
  }
}

async function importConfig() {
  const status = document.getElementById("cfg-import-status");
  const file = document.getElementById("cfg-import-file").files[0];
  if (!file) {
    alert("Pick a config file first.");
    return;
  }
  if (!confirm("This replaces the current room, groups, animations, patterns and sound config. Continue?")) {
    return;
  }
  status.textContent = "Importing...";
  try {
    const text = await file.text();
    const bundle = JSON.parse(text);
    const result = await api.importConfig(bundle);
    const note = profileNote(result);
    if (note) alert(`Import complete.${note}`);
    status.textContent = "Import complete -- reloading...";
    location.reload();
  } catch (e) {
    status.textContent = `Import failed: ${e.message}`;
  }
}
