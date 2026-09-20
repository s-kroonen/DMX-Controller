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
}

function openModal() {
  document.getElementById("modal-config").classList.remove("hidden");
  document.getElementById("cfg-import-status").textContent = "";
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
    await api.importConfig(bundle);
    status.textContent = "Import complete -- reloading...";
    location.reload();
  } catch (e) {
    status.textContent = `Import failed: ${e.message}`;
  }
}
