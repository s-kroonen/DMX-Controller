import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";
import { flashZone } from "./zones.js";

const FUNCTIONS = [
  "pan", "pan_fine", "tilt", "tilt_fine", "dimmer",
  "red", "green", "blue", "white", "amber", "uv",
  "strobe", "shutter", "zoom", "focus",
  "color_wheel", "gobo_wheel", "gobo_rotation", "prism", "mode", "macro",
];

let customRowCount = 0;
let loadedProfile = null; // the profile currently in the form (null = a new one)

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

  document.getElementById("fc-existing-profile").onchange = onExistingProfileChange;
  document.getElementById("fc-add-custom").onclick = () => addCustomRow();
  document.getElementById("fc-add-zone").onclick = () => addZoneRow();
  document.getElementById("fc-zones-sort").onclick = sortZonesLeftToRight;
  document.getElementById("fc-submit").onclick = submitFixture;
  document.getElementById("fc-qxf-import").onclick = importQxf;
}

function openModal() {
  refreshExistingProfileSelect();
  document.getElementById("modal-fixture-creator").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-fixture-creator").classList.add("hidden");
}

// Profiles are otherwise add-only through this form -- POST /fixtures/
// profiles already upserts by id, so editing an existing one (e.g. to fix
// its pan/tilt mechanical range) just needs the form to load that
// profile's data first and keep sending the same id on save.
function refreshExistingProfileSelect() {
  const select = document.getElementById("fc-existing-profile");
  const previous = select.value;
  select.innerHTML = '<option value="">New profile</option>';
  for (const profile of state.profiles) {
    const opt = document.createElement("option");
    opt.value = profile.id;
    opt.textContent = `${profile.name} (${profile.channel_count}ch)`;
    select.appendChild(opt);
  }
  select.value = [...select.options].some((o) => o.value === previous) ? previous : "";
}

function onExistingProfileChange() {
  const id = document.getElementById("fc-existing-profile").value;
  if (!id) {
    clearForm();
    return;
  }
  const profile = state.profiles.find((p) => p.id === id);
  if (profile) fillForm(profile);
}

// ---- zones -------------------------------------------------------------------------------------------------

// The functions a zone can own a channel for. To let zones map another function, add it here (and to
// ZONE_ROLES in backend/app/fixtures/schema.py) and give the Zones window a control for it.
const ZONE_ROLE_FIELDS = [
  { role: "red", short: "R" }, { role: "green", short: "G" }, { role: "blue", short: "B" },
  { role: "white", short: "W" }, { role: "dimmer", short: "Dim", title: "the zone's own dimmer channel" },
  { role: "strobe", short: "Strobe", title: "the zone's strobe channel (may be shared with other zones)" },
];

function addZoneRow(existing) {
  const container = document.getElementById("fc-zone-list");
  const row = document.createElement("div");
  row.className = "zone-row";
  row.dataset.zoneId = existing ? existing.id : "";
  row.innerHTML = `
    <button type="button" class="zone-flash" title="Flash this zone white on the fixture chosen above">&#9889;</button>
    <input type="text" class="zone-label" placeholder="Label (Spot 1)">
    <input type="text" class="zone-kind" list="fc-zone-kinds" placeholder="kind">
    <input type="number" class="zone-position" step="0.05" min="-1" max="1" placeholder="pos">
    <button type="button" class="zone-remove">x</button>
    <div class="zone-chs">
      ${ZONE_ROLE_FIELDS.map((f) => `<div class="zone-ch-wrap" title="${f.title || f.role + " channel"}"><span>${f.short}</span><input type="number" min="1" class="zone-ch" data-role="${f.role}"></div>`).join("")}
    </div>
  `;
  if (existing) {
    row.querySelector(".zone-label").value = existing.label;
    row.querySelector(".zone-kind").value = existing.kind || "";
    row.querySelector(".zone-position").value = existing.position ?? "";
    row.querySelectorAll(".zone-ch").forEach((input) => {
      const value = (existing.channels || {})[input.dataset.role];
      input.value = value === undefined ? "" : value;
    });
  }
  row.querySelector(".zone-remove").onclick = () => row.remove();
  row.querySelector(".zone-flash").onclick = () => {
    const fixtureId = document.getElementById("fc-zone-test-fixture").value;
    if (!fixtureId) { alert("Patch a fixture with this profile first (Patch window), then pick it in \"Flash on\"."); return; }
    if (!row.dataset.zoneId) { alert("Save the profile first so the zone exists, then flash it."); return; }
    flashZone(fixtureId, row.dataset.zoneId).catch(console.error);
  };
  container.appendChild(row);
}

function slug(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function collectZones() {
  const used = new Set();
  const zones = [];
  for (const row of document.querySelectorAll("#fc-zone-list .zone-row")) {
    const label = row.querySelector(".zone-label").value.trim();
    if (!label) continue;
    const channels = {};
    row.querySelectorAll(".zone-ch").forEach((input) => {
      if (input.value) channels[input.dataset.role] = Number(input.value);
    });
    let id = row.dataset.zoneId || slug(label) || `zone${zones.length + 1}`;
    for (let n = 2; used.has(id); n += 1) id = `${slug(label) || "zone"}${n}`;
    used.add(id);
    row.dataset.zoneId = id;
    const pos = row.querySelector(".zone-position").value;
    zones.push({
      id, label, kind: row.querySelector(".zone-kind").value.trim() || "cell", channels,
      position: pos === "" ? null : Number(pos),
    });
  }
  return zones;
}

function sortZonesLeftToRight() {
  const container = document.getElementById("fc-zone-list");
  const rows = [...container.querySelectorAll(".zone-row")];
  const pos = (row) => {
    const v = row.querySelector(".zone-position").value;
    return v === "" ? 0 : Number(v);
  };
  rows.sort((a, b) => pos(a) - pos(b)).forEach((row) => container.appendChild(row));
}

function refreshTestFixtureSelect() {
  const select = document.getElementById("fc-zone-test-fixture");
  const profileId = document.getElementById("fc-existing-profile").value;
  const previous = select.value;
  select.innerHTML = "";
  const fixtures = state.room.fixtures.filter((f) => f.profile_id === profileId);
  if (fixtures.length === 0) {
    select.innerHTML = '<option value="">(no patched fixture uses this profile)</option>';
    return;
  }
  for (const f of fixtures) {
    const opt = document.createElement("option");
    opt.value = f.id;
    opt.textContent = f.name;
    select.appendChild(opt);
  }
  select.value = fixtures.some((f) => f.id === previous) ? previous : fixtures[0].id;
}

function clearForm() {
  document.getElementById("fc-name").value = "";
  document.getElementById("fc-manufacturer").value = "";
  document.getElementById("fc-mode").value = "";
  document.getElementById("fc-type").value = "generic";
  document.getElementById("fc-channel-count").value = 8;
  document.getElementById("fc-pan-range").value = 540;
  document.getElementById("fc-tilt-range").value = 270;
  document.querySelectorAll("#fc-function-channels input").forEach((input) => { input.value = ""; });
  document.getElementById("fc-custom-list").innerHTML = "";
  document.getElementById("fc-zone-list").innerHTML = "";
  loadedProfile = null;
  refreshTestFixtureSelect();
}

function fillForm(profile) {
  document.getElementById("fc-name").value = profile.name;
  document.getElementById("fc-manufacturer").value = profile.manufacturer || "";
  document.getElementById("fc-mode").value = profile.mode || "";
  document.getElementById("fc-type").value = profile.fixture_type || "generic";
  document.getElementById("fc-channel-count").value = profile.channel_count;
  document.getElementById("fc-pan-range").value = profile.pan_range_deg ?? 540;
  document.getElementById("fc-tilt-range").value = profile.tilt_range_deg ?? 270;

  document.querySelectorAll("#fc-function-channels input").forEach((input) => {
    const value = (profile.channels || {})[input.dataset.role];
    input.value = value === undefined ? "" : value;
  });

  document.getElementById("fc-custom-list").innerHTML = "";
  for (const custom of profile.custom_channels || []) {
    addCustomRow(custom);
  }
  document.getElementById("fc-zone-list").innerHTML = "";
  for (const zone of profile.zones || []) {
    addZoneRow(zone);
  }
  loadedProfile = profile;
  refreshTestFixtureSelect();
}

function addCustomRow(existing) {
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
  if (existing) {
    row.querySelector(".custom-channel-num").value = existing.channel;
    row.querySelector(".custom-channel-label").value = existing.label;
    row.querySelector(".custom-channel-default").value = existing.default;
  }
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
  const existingId = document.getElementById("fc-existing-profile").value || undefined;
  const channels = collectFunctionChannels();
  const hasPanTilt = "pan" in channels && "tilt" in channels;
  const payload = {
    id: existingId,
    name: document.getElementById("fc-name").value || "Custom Fixture",
    manufacturer: document.getElementById("fc-manufacturer").value,
    mode: document.getElementById("fc-mode").value,
    fixture_type: document.getElementById("fc-type").value,
    channel_count: Number(document.getElementById("fc-channel-count").value),
    channels,
    custom_channels: collectCustomChannels(),
    // pan/tilt ranges only mean something for a fixture that has pan AND tilt channels
    pan_range_deg: hasPanTilt ? Number(document.getElementById("fc-pan-range").value) : null,
    tilt_range_deg: hasPanTilt ? Number(document.getElementById("fc-tilt-range").value) : null,
    // re-saving an existing profile must not wipe its defaults (e.g. a bar's master dimmer at full)
    defaults: loadedProfile ? loadedProfile.defaults || {} : {},
    zones: collectZones(),
  };
  const saved = await api.saveProfile(payload);
  await reloadRoomAndGroups();
  refreshExistingProfileSelect();
  document.getElementById("fc-existing-profile").value = saved.id;
  onExistingProfileChange(); // reload the form from what was saved (zone ids, kept ranges)
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
