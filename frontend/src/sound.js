import { api } from "./api.js";
import { state } from "./state.js";
import { openPanel } from "./panelWindows.js";
import { loadPref, savePref } from "./uiPrefs.js";

// Sound panel: pick the audio input (a microphone/line-in, or a LOOPBACK of
// this PC's output when the music plays on this machine), watch the level/band
// meters and the beat recogniser (BPM + beat dot, tap tempo as a fallback), and
// build freestyler-style sound-to-light functions from a schema the backend
// serves -- so adding a function type there needs no UI change.
//
// Functions are numbered #1, #2, ... in priority order: when two enabled functions of the same
// category would both drive the same fixture (or the same zone of one), the topmost wins outright
// (see the backend's SoundService.tick) -- Up/Down on a card moves it, saved via /functions/reorder.
// Each card can be collapsed to just its header (persisted per function id) to keep a long list
// manageable; #snd-functions is a CSS grid, so a wide/resized panel shows several cards per row
// instead of one long column. The panel itself is resizable (a native drag handle at its corner);
// the size is remembered too.

const POLL_MS = 120;
let functionTypes = [];
let lastFunctionsSig = "";
let lastBeatCount = null;
let lastDeviceSig = "";
let pollTimer = null;
let currentMode = "off";
const patchTimers = new Map();
const collapsed = new Set(loadPref("sound:collapsed", []));
// A checkbox click updates its local set (chosen/pickedZones) and queues a debounced PATCH, but the
// next poll can land before that PATCH has actually reached the server -- if a rebuild happens then
// (e.g. because some OTHER field changed, or focus wasn't on the checkbox to hold the rebuild off,
// which desktop can't rely on and mobile taps often don't preserve at all), it used to rebuild the
// checkbox from the server's still-stale fn.targets/fn.zones and silently revert the click, so
// unticking a target or a zone looked like it needed a second click to "stick". pendingFields keeps
// each function's not-yet-confirmed targets/zones so a rebuild always prefers them over the server's
// possibly-stale copy; an entry is cleared once a poll shows the server actually caught up.
const pendingFields = new Map(); // fn.id -> {targets?: string[], zones?: string[]}

function sameIdSet(a, b) {
  if (a.length !== b.length) return false;
  const bs = new Set(b);
  return a.every((x) => bs.has(x));
}

function reconcilePending(functions) {
  for (const f of functions) {
    const p = pendingFields.get(f.id);
    if (!p) continue;
    const targetsDone = !("targets" in p) || sameIdSet(p.targets, f.targets);
    const zonesDone = !("zones" in p) || sameIdSet(p.zones, f.zones || []);
    if (targetsDone && zonesDone) pendingFields.delete(f.id);
  }
}

const el = (id) => document.getElementById(id);

export async function initSoundPanel({ resizable = true } = {}) {
  const btn = el("btn-sound");
  if (btn) {
    btn.onclick = () => {
      openPanel("sound");
      refreshDevices();
    };
  }

  el("snd-refresh").onclick = refreshDevices;
  el("snd-device").onchange = async () => {
    const id = el("snd-device").value;
    if (!id) return;
    try {
      await api.audioSelect(id);
    } catch (e) {
      setStatusLine(e.message, true);
    }
  };
  el("snd-toggle").onclick = toggleListening;
  el("snd-tap").onclick = async () => {
    await api.audioTap().catch(console.error);
    el("snd-source").value = "tap";
  };
  el("snd-source").onchange = () => api.audioConfig({ beat_source: el("snd-source").value }).catch(console.error);
  document.querySelectorAll("#snd-mode [data-mode]").forEach((btn) => {
    btn.onclick = () => api.audioConfig({ sound_mode: btn.dataset.mode }).catch(console.error);
  });
  el("snd-gain").onchange = () => api.audioConfig({ gain: Number(el("snd-gain").value) }).catch(console.error);
  el("snd-sens").onchange = () => api.audioConfig({ sensitivity: Number(el("snd-sens").value) }).catch(console.error);
  el("snd-gain").oninput = () => (el("snd-gain-val").textContent = Number(el("snd-gain").value).toFixed(1));
  el("snd-sens").oninput = () => (el("snd-sens-val").textContent = Number(el("snd-sens").value).toFixed(1));

  el("snd-add").onclick = addFunction;

  try {
    functionTypes = await api.audioFunctionTypes();
    const select = el("snd-add-type");
    select.innerHTML = "";
    for (const t of functionTypes) {
      const opt = document.createElement("option");
      opt.value = t.type;
      opt.textContent = t.label;
      opt.title = t.description;
      select.appendChild(opt);
    }
  } catch (e) {
    console.error("sound function types", e);
  }

  await refreshDevices();
  pollTimer = setInterval(poll, POLL_MS);
  if (resizable) initResizable();
}

// The panel is natively CSS-resizable (a drag handle at its corner); remember whatever size the
// operator leaves it at, and restore it so a wide/tall layout (which turns the function list into
// a multi-column grid, see the CSS) doesn't reset every reload. Desktop only -- mobile's drawer
// doesn't offer resizing, so initSoundPanel({resizable:false}) skips this entirely.
function initResizable() {
  const panel = el("panel-sound");
  if (!panel) return;
  const saved = loadPref("sound:size", null);
  if (saved && saved.width) {
    panel.style.width = `${saved.width}px`;
    panel.style.height = `${saved.height}px`;
  }
  let saveTimer = null;
  new ResizeObserver(() => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      savePref("sound:size", { width: panel.offsetWidth, height: panel.offsetHeight });
    }, 300);
  }).observe(panel);
}

// ---- input devices -----------------------------------------------------------

async function refreshDevices() {
  const select = el("snd-device");
  try {
    const devices = await api.audioDevices();
    const status = await api.audioStatus();
    const chosen = status.device ? status.device.id : "";
    const sig = JSON.stringify([devices.map((d) => d.id), chosen]);
    if (sig === lastDeviceSig) return;
    lastDeviceSig = sig;

    select.innerHTML = '<option value="">-- choose an input --</option>';
    const groups = [
      ["Loopback: what this PC is playing", devices.filter((d) => d.is_loopback)],
      ["Inputs: microphone / line-in", devices.filter((d) => !d.is_loopback)],
    ];
    for (const [label, list] of groups) {
      if (!list.length) continue;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const d of list) {
        const opt = document.createElement("option");
        opt.value = d.id;
        opt.textContent = d.name + (d.is_default ? "  (default output)" : "");
        og.appendChild(opt);
      }
      select.appendChild(og);
    }
    select.value = chosen;
    setStatusLine("", false);
  } catch (e) {
    select.innerHTML = '<option value="">(audio unavailable)</option>';
    setStatusLine(e.message, true);
  }
}

async function toggleListening() {
  try {
    const status = await api.audioStatus();
    if (status.capture.running) {
      await api.audioStop();
    } else {
      if (!el("snd-device").value) {
        setStatusLine("Choose an input first.", true);
        return;
      }
      await api.audioStart();
    }
  } catch (e) {
    setStatusLine(e.message, true);
  }
}

function setStatusLine(text, isError) {
  const line = el("snd-status");
  line.textContent = text;
  line.style.color = isError ? "#ff8080" : "";
}

// ---- live status ------------------------------------------------------------------

async function poll() {
  // Works for both the desktop floating panel and the mobile drawer pane: offsetParent is null
  // whenever any ancestor (panel or drawer) is display:none, regardless of which chrome it's in.
  if (!el("snd-functions") || !el("snd-functions").offsetParent) return;
  let status;
  try {
    status = await api.audioStatus();
  } catch {
    return;
  }
  renderStatus(status);
}

function setMeter(id, value) {
  el(id).style.width = `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function renderStatus(status) {
  const f = status.features;
  const live = status.capture.running;
  setMeter("m-level", live ? f.level : 0);
  setMeter("m-bass", live ? f.bass : 0);
  setMeter("m-mid", live ? f.mid : 0);
  setMeter("m-high", live ? f.high : 0);

  const beats = status.config.beat_source === "tap" ? null : f.beat_count;
  if (beats !== null && lastBeatCount !== null && beats !== lastBeatCount) flashBeat();
  lastBeatCount = beats;
  if (status.config.beat_source === "tap") el("snd-beat-dot").style.opacity = String(0.25 + 0.75 * f.beat_pulse);

  el("snd-bpm").textContent = status.bpm ? `${Math.round(status.bpm)} BPM` : "-- BPM";
  el("snd-bpm").style.opacity = status.bpm_confidence >= 0.3 || status.config.beat_source === "tap" ? "1" : "0.55";

  const running = status.capture.running;
  el("snd-toggle").textContent = running ? "Stop listening" : "Start listening";
  el("snd-toggle").classList.toggle("active", running);
  if (document.activeElement !== el("snd-source")) el("snd-source").value = status.config.beat_source;
  currentMode = status.config.sound_mode;
  document.querySelectorAll("#snd-mode [data-mode]").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.mode === currentMode));
  syncSlider("snd-gain", "snd-gain-val", status.config.gain);
  syncSlider("snd-sens", "snd-sens-val", status.config.sensitivity);

  if (status.capture.error) {
    setStatusLine(friendlyError(status.capture.error), true);
  } else if (running && status.capture.is_loopback && (status.capture.seconds_since_audio ?? 0) > 1.5) {
    setStatusLine("Listening, but nothing is playing on that output.", false);
  } else if (running) {
    setStatusLine(`Listening to ${status.capture.device_name}`, false);
  } else if (!el("snd-status").dataset.sticky) {
    setStatusLine("", false);
  }
  if (status.engine_error) setStatusLine(`Sound engine: ${status.engine_error}`, true);

  renderFunctions(status.functions);
}

function friendlyError(error) {
  if (/-9996|-9999|Invalid device|Unanticipated host error/.test(error)) {
    return `Windows won't open this input (${error}). Check it is enabled and not in use: Settings > System > Sound > Input, and Privacy > Microphone.`;
  }
  return error;
}

function syncSlider(sliderId, labelId, value) {
  const slider = el(sliderId);
  if (document.activeElement === slider) return;
  slider.value = value;
  el(labelId).textContent = Number(value).toFixed(1);
}

function flashBeat() {
  const dot = el("snd-beat-dot");
  dot.classList.remove("flash");
  void dot.offsetWidth; // restart the animation
  dot.classList.add("flash");
}

// ---- functions ------------------------------------------------------------------------

function selectionTargets() {
  const sel = [...state.selection];
  return sel.length ? sel : state.room.fixtures.map((f) => f.id);
}

async function addFunction() {
  const type = el("snd-add-type").value;
  if (!type) return;
  try {
    await api.audioAddFunction({ type, targets: selectionTargets() });
    lastFunctionsSig = "";
    renderFunctions((await api.audioStatus()).functions);
  } catch (e) {
    setStatusLine(e.message, true);
  }
}

function schemaFor(type) {
  return functionTypes.find((t) => t.type === type);
}

function patchLater(id, body) {
  clearTimeout(patchTimers.get(id));
  patchTimers.set(id, setTimeout(() => api.audioPatchFunction(id, body).catch(console.error), 200));
}

let lastRenderedFunctions = [];

function renderFunctions(functions) {
  reconcilePending(functions); // drop any pending target/zone edit the server has now caught up with

  // Rebuild only when something structural/edited-elsewhere changed (including the order, since
  // it's part of the array being mapped below), so polling never steals focus from an input being
  // dragged or typed in.
  const sig = JSON.stringify([currentMode,
    functions.map((f) => [f.id, f.enabled, f.error, f.targets, f.zones, f.desync, f.params])]);
  if (sig === lastFunctionsSig) return;
  if (document.activeElement && el("snd-functions").contains(document.activeElement)
      && lastFunctionsSig !== "") {
    // user is mid-edit; keep the DOM, update only the error lines
    functions.forEach((f) => {
      const err = document.querySelector(`[data-fn="${f.id}"] .fn-error`);
      if (err) err.textContent = f.error || "";
    });
    return;
  }
  lastFunctionsSig = sig;
  lastRenderedFunctions = functions;

  const root = el("snd-functions");
  root.innerHTML = "";
  if (!functions.length) {
    root.innerHTML = '<div class="hint">No functions yet. Add one below, then switch Sound-to-light on.</div>';
    return;
  }
  functions.forEach((fn, i) => root.appendChild(functionCard(fn, i, functions.length)));
}

// Swap this function with its neighbour and save the new priority order (see functions.py's
// tick(): dict/list order is priority, first = highest, so index 0 always wins an overlap).
async function reorderTo(fromIndex, toIndex) {
  if (toIndex < 0 || toIndex >= lastRenderedFunctions.length) return;
  const ids = lastRenderedFunctions.map((f) => f.id);
  const [moved] = ids.splice(fromIndex, 1);
  ids.splice(toIndex, 0, moved);
  lastFunctionsSig = ""; // the order change must force a rebuild even though no field value changed
  try {
    await api.audioReorderFunctions(ids);
  } catch (e) {
    setStatusLine(e.message, true);
  }
  renderFunctions((await api.audioStatus()).functions);
}

function targetChoices() {
  return [
    ...state.groups.map((g) => ({ id: g.id, label: `[group] ${g.name}` })),
    ...state.room.fixtures.map((f) => ({ id: f.id, label: f.name })),
  ];
}

const DESYNC_DEFAULT = { granularity: "off", mode: "beat", amount: 1 };

// The desync control group: granularity (off/group/light), mode (beat/timer) and the amount per
// unit -- see backend/app/audio/functions.py's SoundFunction.desync docstring for the exact
// semantics. Kept as one self-contained builder since it's identical for every uses_desync type.
function desyncBox(fn) {
  const box = document.createElement("div");
  box.className = "fn-desync";
  const d = { ...DESYNC_DEFAULT, ...(fn.desync || {}) };

  const set = (patch) => {
    Object.assign(d, patch);
    fn.desync = { ...d };
    patchLater(fn.id, { desync: { ...d } });
    render();
  };

  const granularity = document.createElement("select");
  granularity.innerHTML = `
    <option value="off">No desync -- every light together</option>
    <option value="group">Per group -- each picked group/light its own timing, in sync inside itself</option>
    <option value="light">Per light -- every light on its own, even within a group</option>`;
  granularity.onchange = () => set({ granularity: granularity.value });

  const mode = document.createElement("select");
  mode.innerHTML = `
    <option value="beat">Beat offset</option>
    <option value="timer">Timer offset (ms, ignores tempo)</option>`;
  mode.onchange = () => set({ mode: mode.value });

  const amount = document.createElement("input");
  amount.type = "number";
  amount.min = "0";
  amount.step = mode.value === "timer" ? "10" : "1";
  amount.onchange = () => set({ amount: Math.max(0, Number(amount.value) || 0) });

  const amountLabel = document.createElement("span");

  function render() {
    granularity.value = d.granularity;
    mode.value = d.mode;
    amount.value = d.amount;
    amount.step = d.mode === "timer" ? "10" : "1";
    mode.parentElement.classList.toggle("hidden", d.granularity === "off");
    amount.parentElement.classList.toggle("hidden", d.granularity === "off");
    amountLabel.textContent = d.mode === "timer" ? "ms per unit" : "beats per unit";
  }

  const modeRow = document.createElement("label");
  modeRow.append("Offset by ", mode);
  const amountRow = document.createElement("label");
  amountRow.append(amount, " ", amountLabel);

  box.append("Desync: ", granularity, modeRow, amountRow);
  render();
  return box;
}

function functionCard(fn, index, total) {
  const schema = schemaFor(fn.type);
  const card = document.createElement("div");
  card.className = "fn-card";
  card.dataset.fn = fn.id;
  const isCollapsed = collapsed.has(fn.id);
  card.classList.toggle("collapsed", isCollapsed);

  const head = document.createElement("div");
  head.className = "fn-head";

  const num = document.createElement("span");
  num.className = "fn-num";
  num.textContent = `#${index + 1}`;
  num.title = "Priority: #1 wins when two enabled functions would drive the same light at once";

  const order = document.createElement("div");
  order.className = "fn-order";
  const up = document.createElement("button");
  up.type = "button"; up.textContent = "▲"; up.title = "Move up (higher priority)";
  up.disabled = index === 0;
  up.onclick = () => reorderTo(index, index - 1);
  const down = document.createElement("button");
  down.type = "button"; down.textContent = "▼"; down.title = "Move down (lower priority)";
  down.disabled = index === total - 1;
  down.onclick = () => reorderTo(index, index + 1);
  order.append(up, down);

  const collapseBtn = document.createElement("button");
  collapseBtn.type = "button";
  collapseBtn.className = "fn-collapse";
  collapseBtn.textContent = isCollapsed ? "▸" : "▾";
  collapseBtn.title = isCollapsed ? "Expand" : "Collapse";
  collapseBtn.onclick = () => {
    const now = card.classList.toggle("collapsed");
    collapseBtn.textContent = now ? "▸" : "▾";
    collapseBtn.title = now ? "Expand" : "Collapse";
    if (now) collapsed.add(fn.id); else collapsed.delete(fn.id);
    savePref("sound:collapsed", [...collapsed]);
  };

  const enable = document.createElement("input");
  enable.type = "checkbox";
  enable.checked = fn.enabled;
  enable.onchange = () => patchLater(fn.id, { enabled: enable.checked });
  const title = document.createElement("b");
  title.textContent = schema ? schema.label : fn.type;
  title.title = title.textContent;
  const category = schema ? schema.category : "color";
  const tag = document.createElement("span");
  tag.className = `fn-tag ${category}`;
  tag.textContent = category === "motion" ? "motion" : "color";
  const paused = !(currentMode === "both" || currentMode === category);
  card.classList.toggle("paused", paused);
  tag.title = paused ? `Paused: the mode is ${currentMode}` : "";
  const del = document.createElement("button");
  del.textContent = "x";
  del.className = "fn-del";
  del.onclick = async () => {
    await api.audioDeleteFunction(fn.id).catch(console.error);
    pendingFields.delete(fn.id);
    lastFunctionsSig = "";
    renderFunctions((await api.audioStatus()).functions);
  };
  head.append(num, order, collapseBtn, enable, title, tag, del);
  card.appendChild(head);

  const body = document.createElement("div");
  body.className = "fn-body";
  card.appendChild(body);

  // targets
  const targets = document.createElement("div");
  targets.className = "fn-targets";
  const pending = pendingFields.get(fn.id);
  // A rebuild can be forced (some other field changed, or -- especially on mobile -- a checkbox tap
  // doesn't reliably keep focus the way a desktop click does, so the "user is mid-edit" guard in
  // renderFunctions can't always hold it off) before our own last click's PATCH has reached the
  // server; prefer our own not-yet-confirmed selection over the server's possibly-stale copy so a
  // rebuild never silently reverts what was just clicked.
  const chosen = new Set(pending && "targets" in pending ? pending.targets : fn.targets);
  for (const t of targetChoices()) {
    const lab = document.createElement("label");
    lab.className = "chip";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = chosen.has(t.id);
    lab.append(cb, ` ${t.label}`);
    cb.onchange = () => {
      cb.checked ? chosen.add(t.id) : chosen.delete(t.id);
      const targetsNow = [...chosen];
      pendingFields.set(fn.id, { ...pendingFields.get(fn.id), targets: targetsNow });
      patchLater(fn.id, { targets: targetsNow });
      renderZoneBox(); // which lights are zoned can change with the target selection -- reflect it now,
      // not on the next poll (a target checkbox usually keeps focus, which held the whole card's rebuild)
    };
    targets.appendChild(lab);
  }
  body.appendChild(targets);

  // zones: only for functions that honour them, and only for targets that have zones to pick. Rebuilt
  // from the live `chosen` set (not the server's fn.targets) so ticking/unticking a target above updates
  // this immediately instead of waiting for the next poll.
  const zoneBox = document.createElement("div");
  zoneBox.className = "fn-targets fn-zones hidden";
  zoneBox.title = "Which zones this effect drives. Lights without zones are driven as normal.";
  body.appendChild(zoneBox);
  const pickedZones = new Set(pending && "zones" in pending ? pending.zones : fn.zones || []);
  const push = () => patchLater(fn.id, { zones: [...pickedZones] });
  function renderZoneBox() {
    const zones = schema && schema.uses_zones ? state.zonesForFixtures(state.expandIds([...chosen])) : [];
    zoneBox.classList.toggle("hidden", zones.length === 0);
    if (zones.length === 0) return;
    zoneBox.innerHTML = "";
    const zoneChip = (label, checked, onChange) => {
      const lab = document.createElement("label");
      lab.className = "chip zone";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.onchange = () => onChange(cb);
      lab.append(cb, ` ${label}`);
      zoneBox.appendChild(lab);
    };
    zoneChip("all zones", pickedZones.size === 0, (cb) => {
      pickedZones.clear();
      pendingFields.set(fn.id, { ...pendingFields.get(fn.id), zones: [] });
      push();
      zoneBox.querySelectorAll("input").forEach((other) => { if (other !== cb) other.checked = false; });
      cb.checked = true;
    });
    for (const z of zones) {
      zoneChip(z.label, pickedZones.has(z.id), (cb) => {
        cb.checked ? pickedZones.add(z.id) : pickedZones.delete(z.id);
        pendingFields.set(fn.id, { ...pendingFields.get(fn.id), zones: [...pickedZones] });
        zoneBox.querySelector("input").checked = pickedZones.size === 0;
        push();
      });
    }
  }
  renderZoneBox();

  // desync: only for chases (uses_desync) -- spread the SAME color sequence across time instead of
  // showing it to every light at once. "Per group" keeps each picked group/fixture in sync with
  // itself but offset from the others; "Per light" desyncs every individual light, even within one
  // group. The offset is either a beat count (locked to tempo) or a millisecond delay (its own
  // clock, independent of BPM) -- never a color of its own; palette/every/pattern still come from
  // the params above and are simply replayed later for each unit.
  if (schema && schema.uses_desync) {
    body.appendChild(desyncBox(fn));
  }

  // parameters
  for (const p of schema ? schema.params : []) {
    const value = fn.params[p.key] ?? p.default;
    body.appendChild(paramRow(fn, p, value));
  }

  const err = document.createElement("div");
  err.className = "fn-error";
  err.textContent = fn.error || "";
  body.appendChild(err);
  return card;
}

function paramRow(fn, p, value) {
  const row = document.createElement("div");
  row.className = "fn-param";
  const label = document.createElement("span");
  label.textContent = p.label;
  row.appendChild(label);
  const set = (v) => {
    fn.params[p.key] = v;
    patchLater(fn.id, { params: { [p.key]: v } });
  };

  if (p.kind === "number") {
    const input = document.createElement("input");
    input.type = "range";
    input.min = p.min; input.max = p.max; input.step = p.step; input.value = value;
    const val = document.createElement("span");
    val.className = "pct";
    val.textContent = `${value}${p.unit || ""}`;
    input.oninput = () => {
      val.textContent = `${input.value}${p.unit || ""}`;
      set(Number(input.value));
    };
    row.append(input, val);
  } else if (p.kind === "choice") {
    const select = document.createElement("select");
    for (const o of p.options) {
      const opt = document.createElement("option");
      opt.value = o; opt.textContent = o;
      select.appendChild(opt);
    }
    select.value = value;
    select.onchange = () => set(typeof p.options[0] === "number" ? Number(select.value) : select.value);
    row.appendChild(select);
  } else if (p.kind === "palette") {
    const wrap = document.createElement("span");
    wrap.className = "palette-edit";
    const render = (colors) => {
      wrap.innerHTML = "";
      colors.forEach((c, i) => {
        const input = document.createElement("input");
        input.type = "color"; input.value = c;
        input.onchange = () => { colors[i] = input.value; set([...colors]); };
        input.oncontextmenu = (e) => {
          e.preventDefault();
          if (colors.length > 1) { colors.splice(i, 1); set([...colors]); render(colors); }
        };
        wrap.appendChild(input);
      });
      const add = document.createElement("button");
      add.textContent = "+";
      add.onclick = () => { colors.push("#ffffff"); set([...colors]); render(colors); };
      wrap.appendChild(add);
    };
    render([...value]);
    row.appendChild(wrap);
    row.title = "Right-click a color to remove it";
  } else if (p.kind === "positions") {
    const wrap = document.createElement("span");
    wrap.className = "positions-edit";
    const render = (positions) => {
      wrap.innerHTML = "";
      positions.forEach((pos, i) => {
        const cell = document.createElement("span");
        cell.className = "pos-cell";
        for (const axis of ["pan", "tilt"]) {
          const input = document.createElement("input");
          input.type = "number"; input.min = 0; input.max = 255; input.value = pos[axis];
          input.title = axis;
          input.onchange = () => { pos[axis] = Math.max(0, Math.min(255, Number(input.value))); set(positions.map((x) => ({ ...x }))); };
          cell.appendChild(input);
        }
        const rm = document.createElement("button");
        rm.textContent = "x";
        rm.onclick = () => { if (positions.length > 1) { positions.splice(i, 1); set(positions.map((x) => ({ ...x }))); render(positions); } };
        cell.appendChild(rm);
        wrap.appendChild(cell);
      });
      const add = document.createElement("button");
      add.textContent = "+ pos";
      add.onclick = () => { positions.push({ pan: 128, tilt: 128 }); set(positions.map((x) => ({ ...x }))); render(positions); };
      wrap.appendChild(add);
    };
    render(value.map((x) => ({ ...x })));
    row.appendChild(wrap);
  }
  return row;
}
