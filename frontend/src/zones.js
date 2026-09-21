import { api } from "./api.js";
import { state, onStateChange } from "./state.js";
import { isPanelOpen, openPanel } from "./panelWindows.js";

// Zones window: some fixtures are really several lights in one housing -- a light bar's
// two spots and two derbies. Their profile declares each as a zone, and this window picks
// which zones the controls act on: the RGB window colors only the chosen zones, and the
// brightness slider here dims only those. Nothing is chosen = every zone. Fixtures with
// no zones (a moving head, a PAR) never show up here and are unaffected.
//
// The chosen zones live in state.zoneFilter and are read by the RGB window and (per
// function, separately) by the Sound window.

const el = (id) => document.getElementById(id);
let lastChipSig = "";
let autoOpened = false;

// Flash one zone white for a moment so you can see which physical light it is (handy for
// mapping a bar's layout), then put its previous color back.
export async function flashZone(fixtureId, zoneId, ms = 1200) {
  const prev = state.fixtureState[fixtureId]?.zones?.[zoneId];
  const [r, g, b, w] = prev ? prev.color : [0, 0, 0, 0];
  await api.setColor(fixtureId, 255, 255, 255, 255, [zoneId]);
  setTimeout(() => api.setColor(fixtureId, r, g, b, w, [zoneId]).catch(console.error), ms);
}

export function initZonesPanel() {
  el("zones-dim").addEventListener("input", () => {
    const pct = Number(el("zones-dim").value);
    el("zones-dim-pct").textContent = `${pct}%`;
    const ids = state.expandedFixtureIds();
    if (ids.length === 0) return;
    // an explicit zone list is what makes the engine dim zones instead of the master dimmer
    api.setDimmer(ids, Math.round((pct * 255) / 100), chosenOrAllZoneIds()).catch(console.error);
  });
  onStateChange(render);
  render();
}

function selectionZones() {
  return state.zonesForFixtures(state.expandedFixtureIds());
}

function chosenOrAllZoneIds() {
  const zones = selectionZones();
  const chosen = zones.filter((z) => state.zoneFilter.has(z.id)).map((z) => z.id);
  return chosen.length ? chosen : zones.map((z) => z.id);
}

function zoneColorCss(zoneId) {
  for (const fid of state.expandedFixtureIds()) {
    const z = state.fixtureState[fid]?.zones?.[zoneId];
    if (z) {
      const [r, g, b, w] = z.color;
      const k = z.dimmer / 255;
      const mix = (c) => Math.min(255, Math.round((c + w) * k));
      return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
    }
  }
  return "rgb(40, 40, 40)";
}

function render() {
  const zones = selectionZones();
  for (const id of [...state.zoneFilter]) {
    if (!zones.some((z) => z.id === id)) state.zoneFilter.delete(id); // no longer in the selection
  }
  const hint = el("zones-hint");
  const controls = el("zones-controls");
  if (zones.length === 0) {
    hint.textContent = "The selected fixtures have no separate zones (only fixtures like a light bar with "
      + "independent spots/derbies do). Select one to control its zones here.";
    controls.classList.add("hidden");
    lastChipSig = "";
    return;
  }
  hint.textContent = "Pick which zones the RGB window colors and the brightness slider dims. "
    + "Nothing picked = every zone.";
  controls.classList.remove("hidden");
  if (!autoOpened && !isPanelOpen("zones")) {
    autoOpened = true; // once per page load, so a zoned fixture's controls are easy to find
    openPanel("zones");
  }

  const sig = JSON.stringify([zones.map((z) => z.id), [...state.zoneFilter]]);
  if (sig !== lastChipSig) {
    lastChipSig = sig;
    buildChips(zones);
    buildIdentify(zones);
  }
  updateSwatches();
  syncBrightness();
}

function buildIdentify(zones) {
  const box = el("zones-identify");
  box.innerHTML = "";
  for (const z of zones) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "zone-chip";
    btn.textContent = `Flash ${z.label}`;
    btn.title = "Flash this zone white on the selected fixtures";
    btn.onclick = () => {
      for (const fid of state.expandedFixtureIds()) {
        if (state.zonesForFixtures([fid]).some((zone) => zone.id === z.id)) flashZone(fid, z.id).catch(console.error);
      }
    };
    box.appendChild(btn);
  }
}

function buildChips(zones) {
  const box = el("zones-chips");
  box.innerHTML = "";
  const add = (label, active, onClick, zoneId) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "zone-chip" + (active ? " active" : "");
    if (zoneId) {
      const dot = document.createElement("i");
      dot.className = "zone-dot";
      dot.dataset.zone = zoneId;
      btn.appendChild(dot);
    }
    btn.appendChild(document.createTextNode(label));
    btn.onclick = onClick;
    box.appendChild(btn);
  };

  add("All", state.zoneFilter.size === 0, () => { state.zoneFilter.clear(); render(); });

  const kinds = [...new Set(zones.map((z) => z.kind))].filter((k) => zones.filter((z) => z.kind === k).length > 1);
  for (const kind of kinds) {
    const ids = zones.filter((z) => z.kind === kind).map((z) => z.id);
    const exactly = ids.length === state.zoneFilter.size && ids.every((id) => state.zoneFilter.has(id));
    add(`All ${plural(kind)}`, exactly, () => {
      state.zoneFilter.clear();
      if (!exactly) ids.forEach((id) => state.zoneFilter.add(id));
      render();
    });
  }
  for (const z of zones) {
    add(z.label, state.zoneFilter.has(z.id), () => {
      if (state.zoneFilter.has(z.id)) state.zoneFilter.delete(z.id);
      else state.zoneFilter.add(z.id);
      render();
    }, z.id);
  }
}

function plural(word) {
  return word.endsWith("y") ? `${word.slice(0, -1)}ies` : `${word}s`;
}

function updateSwatches() {
  document.querySelectorAll("#zones-chips .zone-dot").forEach((dot) => {
    dot.style.background = zoneColorCss(dot.dataset.zone);
  });
}

function syncBrightness() {
  const slider = el("zones-dim");
  if (document.activeElement === slider) return;
  const wanted = chosenOrAllZoneIds();
  for (const fid of state.expandedFixtureIds()) {
    const st = state.fixtureState[fid]?.zones;
    const first = st && wanted.map((id) => st[id]).find(Boolean);
    if (first) {
      const pct = Math.round((first.dimmer * 100) / 255);
      slider.value = pct;
      el("zones-dim-pct").textContent = `${pct}%`;
      return;
    }
  }
}
