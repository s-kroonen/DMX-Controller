import { state, onStateChange } from "./state.js";
import { notify } from "./mode.js";
import {
  cal, onCalibrationChange, panTiltFixtures, includedIds, setIncluded, rememberOriginals, currentPoint,
  setPoint, aimAll, aimOne, nudge, setOffsetField, resetOffsets, setBeam, beamIsOn, beamIds, solo,
  isSweeping, startSweep, stopSweep,
} from "./calibration.js";

// The calibration controls, mounted into a container by the desktop Calibrate window and by the
// phone's Calibrate screen (same behaviour, each app's own CSS): the target point, beam / aim / sweep
// buttons, and one row per pan/tilt fixture with offset nudge buttons, invert flags and what the last
// aim reported.
//
// opts.onPick: desktop only -- start "click a surface in 3D" to set the point.
// opts.compact: bigger touch targets and a shorter hint (phone).

const fmt = (n) => (Number.isFinite(n) ? n.toFixed(1) : "--");

export function mountCalibrationView(container, opts = {}) {
  container.classList.add("cal-view");
  container.innerHTML = `
    <div class="hint cal-hint"></div>
    <fieldset class="cal-point">
      <legend>Target point (meters)</legend>
      <div class="cal-xyz">
        <label>X <input type="number" step="0.1" data-axis="x"></label>
        <label>Y <input type="number" step="0.1" data-axis="y"></label>
        <label>Z <input type="number" step="0.1" data-axis="z"></label>
      </div>
      <div class="cal-nudge-row"></div>
      <div class="cal-point-tools">
        <select class="cal-saved"></select>
        <button type="button" class="cal-pick">Pick in 3D</button>
      </div>
    </fieldset>
    <div class="cal-actions">
      <button type="button" class="cal-beam">Beam on</button>
      <button type="button" class="cal-aim primary-btn">Aim all here</button>
    </div>
    <fieldset class="cal-sweep">
      <legend>Sweep -- one point moves, every head follows</legend>
      <div class="cal-sweep-row">
        <select class="cal-shape">
          <option value="line-x">Line along X</option>
          <option value="line-y">Line along Y</option>
          <option value="circle">Circle</option>
          <option value="saved">Through saved points</option>
        </select>
        <label>Size <input type="number" class="cal-span" min="0.2" step="0.1"> m</label>
        <label>Leg <input type="number" class="cal-seconds" min="0.5" step="0.5"> s</label>
      </div>
      <button type="button" class="cal-sweep-btn">Start sweep</button>
    </fieldset>
    <label class="cal-step-label">Nudge step
      <select class="cal-step">
        <option value="0.1">0.1&deg;</option><option value="0.5">0.5&deg;</option>
        <option value="1">1&deg;</option><option value="5">5&deg;</option>
      </select>
    </label>
    <div class="cal-rows"></div>`;
  container.querySelector(".cal-hint").textContent = opts.compact
    ? "Aim all the heads at one point and watch whether the beams meet. Nudge a head's offsets until they do."
    : "Aim the heads at one point and look where the beams land. If they do not meet, nudge that head's "
      + "pan / tilt offset until they do. A sweep moves the point so drift is easy to see. Saved offsets "
      + "apply to shows too.";

  const $ = (sel) => container.querySelector(sel);
  // On a phone the heads are what you work with: the sweep goes below them
  if (opts.compact) container.querySelector(".cal-rows").after(container.querySelector(".cal-sweep"));
  const guard = (fn) => async (...args) => {
    try { await fn(...args); } catch (err) { notify(err.message); }
  };

  // ---- point
  container.querySelectorAll(".cal-xyz input").forEach((input) => {
    input.onchange = guard(() => {
      const p = { ...currentPoint(), [input.dataset.axis]: Number(input.value) };
      return setPoint(p, true);
    });
  });
  const nudgeRow = $(".cal-nudge-row");
  for (const [axis, delta] of [["x", -0.5], ["x", 0.5], ["y", -0.5], ["y", 0.5], ["z", -0.25], ["z", 0.25]]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `${axis.toUpperCase()}${delta > 0 ? "+" : "−"}`;
    btn.title = `Move the point ${Math.abs(delta)} m along ${axis.toUpperCase()} and re-aim`;
    btn.onclick = guard(() => setPoint({ ...currentPoint(), [axis]: currentPoint()[axis] + delta }, true));
    nudgeRow.appendChild(btn);
  }
  const saved = $(".cal-saved");
  saved.onchange = guard(() => {
    const point = (state.room.animation_points || []).find((pt) => pt.id === saved.value);
    saved.value = "";
    return point ? setPoint(point.position, true) : undefined;
  });
  if (opts.onPick) $(".cal-pick").onclick = () => opts.onPick();
  else $(".cal-pick").classList.add("hidden");

  // ---- actions
  $(".cal-beam").onclick = guard(() => setBeam(!beamIsOn()));
  $(".cal-aim").onclick = guard(async () => {
    if (!beamIsOn()) await setBeam(true);   // aiming an unlit head shows nothing
    await aimAll();
  });
  $(".cal-shape").onchange = (e) => { cal.shape = e.target.value; };
  $(".cal-span").onchange = (e) => { cal.span = Math.max(0.2, Number(e.target.value) || 1.5); };
  $(".cal-seconds").onchange = (e) => { cal.seconds = Math.max(0.5, Number(e.target.value) || 3); };
  $(".cal-step").onchange = (e) => { cal.step = Number(e.target.value); };
  $(".cal-sweep-btn").onclick = guard(async () => {
    if (isSweeping()) { await stopSweep(); return; }
    if (!beamIsOn()) await setBeam(true);
    await startSweep();
  });

  let rowsKey = "";

  function buildRows() {
    const rows = $(".cal-rows");
    rows.innerHTML = "";
    rememberOriginals();
    const fixtures = panTiltFixtures();
    if (!fixtures.length) rows.innerHTML = '<div class="hint">No pan/tilt fixtures are patched.</div>';
    for (const f of fixtures) {
      const row = document.createElement("div");
      row.className = "cal-row";
      row.dataset.fid = f.id;
      row.innerHTML = `
        <div class="cal-head">
          <label><input type="checkbox" class="cal-inc"> <b class="cal-name"></b></label>
          <span class="cal-status"></span>
        </div>
        <div class="cal-off">
          <span class="cal-off-label">Pan</span>
          <button type="button" data-f="pan_offset_deg" data-d="-1">−</button>
          <input type="number" step="0.1" data-f="pan_offset_deg">
          <button type="button" data-f="pan_offset_deg" data-d="1">+</button>
          <span class="cal-off-label">Tilt</span>
          <button type="button" data-f="tilt_offset_deg" data-d="-1">−</button>
          <input type="number" step="0.1" data-f="tilt_offset_deg">
          <button type="button" data-f="tilt_offset_deg" data-d="1">+</button>
        </div>
        <div class="cal-flags">
          <label><input type="checkbox" data-flag="inverted_pan"> invert pan</label>
          <label><input type="checkbox" data-flag="inverted_tilt"> invert tilt</label>
        </div>
        <div class="cal-read"></div>
        <div class="cal-btns">
          <button type="button" class="cal-one">Aim</button>
          <button type="button" class="cal-solo">Solo</button>
          <button type="button" class="cal-reset">Reset</button>
        </div>`;
      row.querySelector(".cal-name").textContent = f.name;
      row.querySelector(".cal-inc").onchange = (e) => setIncluded(f.id, e.target.checked);
      row.querySelectorAll("button[data-f]").forEach((btn) => {
        btn.onclick = guard(() => nudge(f.id, btn.dataset.f, Number(btn.dataset.d)));
      });
      row.querySelectorAll("input[type=number][data-f]").forEach((input) => {
        input.onchange = guard(() => setOffsetField(f.id, input.dataset.f, Number(input.value) || 0));
      });
      row.querySelectorAll("input[data-flag]").forEach((input) => {
        input.onchange = guard(() => setOffsetField(f.id, input.dataset.flag, input.checked));
      });
      row.querySelector(".cal-one").onclick = guard(async () => {
        if (!beamIds().includes(f.id)) await setBeam(true, [f.id]);
        await aimOne(f.id);
      });
      row.querySelector(".cal-solo").onclick = guard(() => solo(f.id));
      row.querySelector(".cal-reset").onclick = guard(() => resetOffsets(f.id));
      rows.appendChild(row);
    }
  }

  function statusFor(id) {
    const r = cal.results[id];
    if (!r) return "";
    if (!r.ok) return r.reason || "not aimed";
    return r.in_range === false ? "out of range (clamped)" : "on the point";
  }

  function refresh() {
    if (container.offsetParent === null) return;   // not showing: skip the work
    const key = JSON.stringify(panTiltFixtures().map((f) => [f.id, f.name]));
    if (key !== rowsKey) { rowsKey = key; buildRows(); }
    const p = currentPoint();
    container.querySelectorAll(".cal-xyz input").forEach((input) => {
      if (document.activeElement !== input) input.value = p[input.dataset.axis];
    });
    const savedSelect = $(".cal-saved");
    const points = state.room.animation_points || [];
    const savedKey = JSON.stringify(points.map((pt) => [pt.id, pt.name]));
    if (savedSelect.dataset.key !== savedKey) {
      savedSelect.dataset.key = savedKey;
      savedSelect.innerHTML = '<option value="">Saved point...</option>'
        + points.map((pt) => `<option value="${pt.id}">${pt.name}</option>`).join("");
    }
    const beam = beamIsOn();
    const beamBtn = $(".cal-beam");
    beamBtn.textContent = beam ? "Beam off (restore)" : "Beam on";
    beamBtn.classList.toggle("active", beam);
    const sweeping = isSweeping();
    const sweepBtn = $(".cal-sweep-btn");
    sweepBtn.textContent = sweeping ? "Stop sweep" : "Start sweep";
    sweepBtn.classList.toggle("active", sweeping);
    for (const [sel, value] of [[".cal-shape", cal.shape], [".cal-span", cal.span], [".cal-seconds", cal.seconds],
      [".cal-step", cal.step]]) {
      const el = $(sel);
      if (document.activeElement !== el) el.value = value;
    }
    const included = new Set(includedIds());
    container.querySelectorAll(".cal-row").forEach((row) => {
      const f = state.fixtureById(row.dataset.fid);
      if (!f) return;
      const inc = row.querySelector(".cal-inc");
      if (document.activeElement !== inc) inc.checked = included.has(f.id);
      row.querySelectorAll("input[type=number][data-f]").forEach((input) => {
        if (document.activeElement !== input) input.value = f[input.dataset.f] || 0;
      });
      row.querySelectorAll("input[data-flag]").forEach((input) => {
        if (document.activeElement !== input) input.checked = !!f[input.dataset.flag];
      });
      const r = cal.results[f.id];
      row.querySelector(".cal-status").textContent = statusFor(f.id);
      row.classList.toggle("bad", !!r && (!r.ok || r.in_range === false));
      row.querySelector(".cal-read").textContent = r && r.ok
        ? `pan ${fmt(r.pan_angle_deg)}° (DMX ${r.pan_dmx})   tilt ${fmt(r.tilt_angle_deg)}° (DMX ${r.tilt_dmx})`
        : "";
      row.classList.toggle("lit", beamIds().includes(f.id));
    });
  }

  onStateChange(refresh);
  onCalibrationChange(refresh);
  return { refresh };
}
