import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

export function initRoomObjectsModal() {
  document.getElementById("btn-room-objects").onclick = () => openModal();
  document.querySelector("#modal-room-objects .modal-close").onclick = () => closeModal();
  document.getElementById("ro-kind").addEventListener("change", updateFieldVisibility);
  document.getElementById("ro-submit").onclick = submitObject;
  updateFieldVisibility();
}

function openModal() {
  renderList();
  updateFieldVisibility();
  document.getElementById("modal-room-objects").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-room-objects").classList.add("hidden");
}

function updateFieldVisibility() {
  const kind = document.getElementById("ro-kind").value;
  document.getElementById("ro-end-fieldset").style.display = kind === "wall" ? "" : "none";
  document.getElementById("ro-thickness-label").style.display = kind === "wall" ? "" : "none";
  document.getElementById("ro-width-label").style.display = kind === "wall" ? "none" : "";
  document.getElementById("ro-depth-label").style.display = kind === "wall" ? "none" : "";
  document.getElementById("ro-pos-legend").textContent =
    kind === "wall" ? "Start point" : kind === "person" ? "Ground position" : "Base position (center)";
}

async function submitObject() {
  const kind = document.getElementById("ro-kind").value;
  const payload = {
    name: document.getElementById("ro-name").value || kind,
    kind,
    position: {
      x: Number(document.getElementById("ro-x").value),
      y: Number(document.getElementById("ro-y").value),
      z: Number(document.getElementById("ro-z").value),
    },
    width: Number(document.getElementById("ro-width").value),
    depth: Number(document.getElementById("ro-depth").value),
    height: Number(document.getElementById("ro-height").value),
    thickness: Number(document.getElementById("ro-thickness").value),
    color: document.getElementById("ro-color").value,
  };
  if (kind === "wall") {
    payload.end_position = {
      x: Number(document.getElementById("ro-ex").value),
      y: Number(document.getElementById("ro-ey").value),
      z: Number(document.getElementById("ro-ez").value),
    };
  }
  if (kind === "person") {
    payload.width = 0.5;
    payload.depth = 0.5;
  }
  await api.addRoomObject(payload);
  await reloadRoomAndGroups();
  renderList();
}

function renderList() {
  const container = document.getElementById("ro-list");
  container.innerHTML = "";
  for (const obj of state.room.objects || []) {
    const row = document.createElement("div");
    row.className = "zone-row";
    const desc = obj.kind === "wall"
      ? `wall ${obj.position.x},${obj.position.y} -> ${obj.end_position.x},${obj.end_position.y}`
      : `${obj.kind} @ ${obj.position.x},${obj.position.y},${obj.position.z}`;
    row.innerHTML = `<span>${obj.name} (${desc})</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      await api.deleteRoomObject(obj.id);
      await reloadRoomAndGroups();
      renderList();
    };
    row.appendChild(del);
    container.appendChild(row);
  }
}
