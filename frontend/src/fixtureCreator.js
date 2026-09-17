import { api } from "./api.js";
import { reloadRoomAndGroups } from "./main_data.js";

const FUNCTIONS = [
  "pan", "pan_fine", "tilt", "tilt_fine", "dimmer",
  "red", "green", "blue", "white", "amber", "uv",
  "strobe", "shutter", "zoom", "focus",
  "color_wheel", "gobo_wheel", "gobo_rotation", "prism", "mode", "macro",
];

let customRowCount = 0;

export function initFixtureCreator() {
  document.getElementById("btn-fixture-creator").onclick = () => openModal();
  document.querySelector("#modal-fixture-creator .modal-close").onclick = () => closeModal();

  const grid = document.getElementById("fc-function-channels");
  grid.innerHTML = "";
  for (const fn of FUNCTIONS) {
    const label = document.createElement("label");
    label.textContent = fn;
    const input = document.createElement("input");
    input.type = "number";
    input.min = 1;
    input.dataset.role = fn;
    label.appendChild(input);
    grid.appendChild(label);
  }

  document.getElementById("fc-add-custom").onclick = () => addCustomRow();
  document.getElementById("fc-submit").onclick = submitFixture;
  document.getElementById("fc-qxf-import").onclick = importQxf;
}

function openModal() {
  document.getElementById("modal-fixture-creator").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-fixture-creator").classList.add("hidden");
}

function addCustomRow() {
  customRowCount += 1;
  const container = document.getElementById("fc-custom-list");
  const row = document.createElement("div");
  row.className = "custom-channel-row";
  row.innerHTML = `
    <input type="number" placeholder="Ch #" class="custom-channel-num">
    <input type="text" placeholder="Label (e.g. Fog rate)" class="custom-channel-label">
    <input type="number" placeholder="Default" class="custom-channel-default" value="0">
    <button type="button" class="custom-channel-remove">x</button>
  `;
  row.querySelector(".custom-channel-remove").onclick = () => row.remove();
  container.appendChild(row);
}

function collectCustomChannels() {
  return [...document.querySelectorAll("#fc-custom-list .custom-channel-row")]
    .map((row) => ({
      channel: Number(row.querySelector(".custom-channel-num").value),
      label: row.querySelector(".custom-channel-label").value,
      default: Number(row.querySelector(".custom-channel-default").value) || 0,
      min_value: 0,
      max_value: 255,
    }))
    .filter((c) => c.channel && c.label);
}

function collectFunctionChannels() {
  const channels = {};
  document.querySelectorAll("#fc-function-channels input").forEach((input) => {
    if (input.value) channels[input.dataset.role] = Number(input.value);
  });
  return channels;
}

async function submitFixture() {
  const payload = {
    name: document.getElementById("fc-name").value || "Custom Fixture",
    manufacturer: document.getElementById("fc-manufacturer").value,
    mode: document.getElementById("fc-mode").value,
    fixture_type: document.getElementById("fc-type").value,
    channel_count: Number(document.getElementById("fc-channel-count").value),
    channels: collectFunctionChannels(),
    custom_channels: collectCustomChannels(),
    pan_range_deg: Number(document.getElementById("fc-pan-range").value),
    tilt_range_deg: Number(document.getElementById("fc-tilt-range").value),
    defaults: {},
  };
  await api.saveProfile(payload);
  await reloadRoomAndGroups();
  alert(`Saved fixture profile "${payload.name}". It's now available in Patch.`);
}

async function importQxf() {
  const input = document.getElementById("fc-qxf-file");
  if (!input.files.length) {
    alert("Choose a .qxf file first.");
    return;
  }
  const profiles = await api.importQxf(input.files[0]);
  await reloadRoomAndGroups();
  alert(`Imported ${profiles.length} mode(s) from the .qxf file.`);
}
