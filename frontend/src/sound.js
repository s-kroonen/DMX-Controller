import { api } from "./api.js";
import { state } from "./state.js";
import { isPanelOpen, openPanel } from "./panelWindows.js";

// Sound panel: pick the audio input (a microphone/line-in, or a LOOPBACK of
// this PC's output when the music plays on this machine), watch the level/band
// meters and the beat recogniser (BPM + beat dot, tap tempo as a fallback), and
// build freestyler-style sound-to-light functions from a schema the backend
// serves -- so adding a function type there needs no UI change.

const POLL_MS = 120;
let functionTypes = [];
let lastFunctionsSig = "";
let lastBeatCount = null;
let lastDeviceSig = "";
let pollTimer = null;
let currentMode = "off";
const patchTimers = new Map();

const el = (id) => document.getElementById(id);

export async function initSoundPanel() {
  el("btn-sound").onclick = () => {
    openPanel("sound");
    refreshDevices();
  };

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
  if (!isPanelOpen("sound")) return;
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

function renderFunctions(functions) {
  // Rebuild only when something structural/edited-elsewhere changed, so polling
  // never steals focus from an input being dragged or typed in.
  const sig = JSON.stringify([currentMode, functions.map((f) => [f.id, f.enabled, f.error, f.targets, f.zones, f.params])]);
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

  const root = el("snd-functions");
  root.innerHTML = "";
  if (!functions.length) {
    root.innerHTML = '<div class="hint">No functions yet. Add one below, then switch Sound-to-light on.</div>';
    return;
  }
  for (const fn of functions) root.appendChild(functionCard(fn));
}

function targetChoices() {
  return [
    ...state.groups.map((g) => ({ id: g.id, label: `[group] ${g.name}` })),
    ...state.room.fixtures.map((f) => ({ id: f.id, label: f.name })),
  ];
}

function functionCard(fn) {
  const schema = schemaFor(fn.type);
  const card = document.createElement("div");
  card.className = "fn-card";
  card.dataset.fn = fn.id;

  const head = document.createElement("div");
  head.className = "fn-head";
  const enable = document.createElement("input");
  enable.type = "checkbox";
  enable.checked = fn.enabled;
  enable.onchange = () => patchLater(fn.id, { enabled: enable.checked });
  const title = document.createElement("b");
  title.textContent = schema ? schema.label : fn.type;
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
    lastFunctionsSig = "";
    renderFunctions((await api.audioStatus()).functions);
  };
  head.append(enable, title, tag, del);
  card.appendChild(head);

  // targets
  const targets = document.createElement("div");
  targets.className = "fn-targets";
  const chosen = new Set(fn.targets);
  for (const t of targetChoices()) {
    const lab = document.createElement("label");
    lab.className = "chip";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = chosen.has(t.id);
    cb.onchange = () => {
      cb.checked ? chosen.add(t.id) : chosen.delete(t.id);
      patchLater(fn.id, { targets: [...chosen] });
    };
    lab.append(cb, ` ${t.label}`);
    targets.appendChild(lab);
  }
  card.appendChild(targets);

  // zones: only for functions that honour them, and only if a target has zones to pick
  const zones = schema && schema.uses_zones ? state.zonesForFixtures(state.expandIds(fn.targets)) : [];
  if (zones.length) {
    const box = document.createElement("div");
    box.className = "fn-targets fn-zones";
    const pickedZones = new Set(fn.zones || []);
    const push = () => patchLater(fn.id, { zones: [...pickedZones] });
    const zoneChip = (label, checked, onChange) => {
      const lab = document.createElement("label");
      lab.className = "chip zone";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.onchange = () => onChange(cb);
      lab.append(cb, ` ${label}`);
      box.appendChild(lab);
    };
    zoneChip("all zones", pickedZones.size === 0, (cb) => {
      pickedZones.clear();
      push();
      box.querySelectorAll("input").forEach((other) => { if (other !== cb) other.checked = false; });
      cb.checked = true;
    });
    for (const z of zones) {
      zoneChip(z.label, pickedZones.has(z.id), (cb) => {
        cb.checked ? pickedZones.add(z.id) : pickedZones.delete(z.id);
        box.querySelector("input").checked = pickedZones.size === 0;
        push();
      });
    }
    card.appendChild(box);
  }

  // parameters
  for (const p of schema ? schema.params : []) {
    const value = fn.params[p.key] ?? p.default;
    card.appendChild(paramRow(fn, p, value));
  }

  const err = document.createElement("div");
  err.className = "fn-error";
  err.textContent = fn.error || "";
  card.appendChild(err);
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
