import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

export function initSafetyModal() {
  document.getElementById("btn-safety-zones").onclick = () => openModal();
  document.querySelector("#modal-safety .modal-close").onclick = () => closeModal();
  document.getElementById("sz-submit").onclick = submitZone;
}

function openModal() {
  renderZoneList();
  document.getElementById("modal-safety").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-safety").classList.add("hidden");
}

async function submitZone() {
  const payload = {
    name: document.getElementById("sz-name").value || "Zone",
    min_corner: {
      x: Number(document.getElementById("sz-minx").value),
      y: Number(document.getElementById("sz-miny").value),
      z: Number(document.getElementById("sz-minz").value),
    },
    max_corner: {
      x: Number(document.getElementById("sz-maxx").value),
      y: Number(document.getElementById("sz-maxy").value),
      z: Number(document.getElementById("sz-maxz").value),
    },
  };
  await api.addSafetyZone(payload);
  await reloadRoomAndGroups();
  renderZoneList();
}

function renderZoneList() {
  const container = document.getElementById("sz-list");
  container.innerHTML = "";
  for (const zone of state.room.safety_zones) {
    const row = document.createElement("div");
    row.className = "zone-row";
    row.innerHTML = `<span>${zone.name} [${zone.min_corner.x},${zone.min_corner.y},${zone.min_corner.z}] -> [${zone.max_corner.x},${zone.max_corner.y},${zone.max_corner.z}]</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      await api.deleteSafetyZone(zone.id);
      await reloadRoomAndGroups();
      renderZoneList();
    };
    row.appendChild(del);
    container.appendChild(row);
  }
}
