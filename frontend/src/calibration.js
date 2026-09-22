import { api } from "./api.js";
import { state, notifyStateChange } from "./state.js";

// Client side of the calibration tools (edit mode), shared by the desktop Calibrate window and the
// phone's Calibrate screen. The idea: point every pan/tilt head at ONE room-space point and look at
// where the beams land -- if they do not meet, that head's offsets (or its position / mounting) are
// off. A sweep moves the point along a path so a badly calibrated head visibly drifts away from the
// spot the others stay on, which is much easier to see than a single static point.
//
// The target point lives in state.calibrationPoint (both 3D views draw a marker at it), the operator's
// choices in `cal`, and the backend's beam/sweep state comes back in every snapshot as
// state.calibration.

export const cal = {
  excluded: new Set(),   // fixtures the operator unticked; everything else with pan/tilt takes part
  results: {},           // fixture id -> what the last aim reported
  original: {},          // fixture id -> its offsets when first seen, for Reset
  step: 0.5,             // degrees per nudge
  shape: "line-x",       // sweep path: line-x | line-y | circle | saved
  span: 1.5,             // meters from the point to the end of the line / the circle radius
  seconds: 3,            // seconds per leg of the sweep
  observations: {},      // fixture id -> [{x, y, z, label, pan, pan_fine, tilt, tilt_fine}] recorded for the solver
  fits: {},              // fixture id -> the last solve result, until applied or discarded
  raw: {},               // fixture id -> {pan, tilt}: the 16-bit pan/tilt the head was last sent
  stepSize: 16,          // 16-bit units per steering click (1 = finest, 256 = one coarse step)
  solvePosition: false,  // also fit the head's position (needs more marks)
};

const listeners = new Set();
export function onCalibrationChange(fn) {
  listeners.add(fn);
}
function changed() {
  listeners.forEach((fn) => fn());
}

export function panTiltFixtures() {
  return state.room.fixtures.filter((f) => {
    const profile = state.profileById(f.profile_id);
    return !!(profile && profile.channels && profile.channels.pan && profile.channels.tilt);
  });
}

export function includedIds() {
  return panTiltFixtures().map((f) => f.id).filter((id) => !cal.excluded.has(id));
}

export function setIncluded(id, on) {
  if (on) cal.excluded.delete(id);
  else cal.excluded.add(id);
  changed();
}

export function rememberOriginals() {
  for (const f of panTiltFixtures()) {
    if (!cal.original[f.id]) {
      cal.original[f.id] = {
        pan_offset_deg: f.pan_offset_deg || 0, tilt_offset_deg: f.tilt_offset_deg || 0,
        inverted_pan: !!f.inverted_pan, inverted_tilt: !!f.inverted_tilt,
      };
    }
  }
}

// the middle of the room, at head height for someone standing
function defaultPoint() {
  const pts = state.room.floor_points || [];
  if (pts.length) {
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    return { x: round((Math.min(...xs) + Math.max(...xs)) / 2), y: round((Math.min(...ys) + Math.max(...ys)) / 2), z: 1.5 };
  }
  const d = state.room.dimensions || { width: 10, depth: 10 };
  return { x: round(d.width / 2), y: round(d.depth / 2), z: 1.5 };
}

const round = (n) => Math.round(n * 100) / 100;

export function currentPoint() {
  if (!state.calibrationPoint) state.calibrationPoint = defaultPoint();
  return state.calibrationPoint;
}

// Move the target point (the 3D views draw a marker at it); optionally aim the heads there too
export function setPoint(p, aim = false) {
  state.calibrationPoint = { x: round(p.x), y: round(p.y), z: round(p.z) };
  notifyStateChange();
  changed();
  return aim ? aimAll() : Promise.resolve();
}

function takeResults(results) {
  Object.assign(cal.results, results);
  for (const [id, r] of Object.entries(results)) {
    if (r.ok) cal.raw[id] = { pan: r.pan_dmx * 256 + (r.pan_fine_dmx || 0), tilt: r.tilt_dmx * 256 + (r.tilt_fine_dmx || 0) };
  }
}

export async function aimAll() {
  const ids = includedIds();
  if (!ids.length) return;
  const { results } = await api.calAim(ids, currentPoint());
  takeResults(results);
  changed();
}

export async function aimOne(id) {
  const { results } = await api.calAim([id], currentPoint());
  takeResults(results);
  changed();
}

// field: pan_offset_deg | tilt_offset_deg | inverted_pan | inverted_tilt
export async function setOffsetField(id, field, value) {
  const { result } = await api.calOffsets({ fixture_id: id, [field]: value, point: currentPoint() });
  if (result) cal.results[id] = result;
  changed();
}

export async function nudge(id, field, direction) {
  const fixture = state.fixtureById(id);
  if (!fixture) return;
  const next = Math.round(((fixture[field] || 0) + direction * cal.step) * 1000) / 1000;
  await setOffsetField(id, field, next);
}

export async function resetOffsets(id) {
  rememberOriginals();
  const original = cal.original[id];
  if (!original) return;
  const { result } = await api.calOffsets({ fixture_id: id, ...original, point: currentPoint() });
  if (result) cal.results[id] = result;
  changed();
}

export const beamIds = () => state.calibration.beam || [];
export const beamIsOn = () => beamIds().length > 0;

export async function setBeam(on, ids = includedIds()) {
  await api.calBeam(ids, on);
}

// Only this head lit and aimed; the others' beams off so it is the only one to look at
export async function solo(id) {
  const others = includedIds().filter((other) => other !== id);
  await api.calBeam(others, false);
  await api.calBeam([id], true);
  await aimOne(id);
}

export const isSweeping = () => !!state.calibration.sweeping;

export function sweepPath() {
  const p = currentPoint();
  const s = cal.span;
  if (cal.shape === "line-y") return [{ ...p, y: p.y - s }, { ...p, y: p.y + s }];
  if (cal.shape === "circle") {
    return Array.from({ length: 8 }, (_, i) => {
      const a = (i / 8) * Math.PI * 2;
      return { x: p.x + Math.cos(a) * s, y: p.y + Math.sin(a) * s, z: p.z };
    });
  }
  if (cal.shape === "saved") {
    return (state.room.animation_points || []).map((pt) => ({ ...pt.position }));
  }
  return [{ ...p, x: p.x - s }, { ...p, x: p.x + s }];
}

export async function startSweep() {
  const ids = includedIds();
  const points = sweepPath();
  if (!ids.length) throw new Error("No pan/tilt fixtures to sweep.");
  if (points.length < 2) throw new Error("A sweep needs at least two points (add saved points in Animations).");
  await api.calSweep(ids, points, cal.seconds);
}

export async function stopSweep() {
  await api.calSweepStop();
}

// ---- the solver: known marks, steering the beam onto them, recording, solving -----------------------------

// Points whose room position is known without measuring: the saved points and the room's corners
// (from the room shape), on the floor and at the ceiling.
export function marks() {
  const list = [];
  for (const pt of state.room.animation_points || []) {
    list.push({ id: `pt:${pt.id}`, group: "Saved points", label: pt.name, position: { ...pt.position } });
  }
  const dims = state.room.dimensions || { width: 10, depth: 10, height: 4 };
  const floor = (state.room.floor_points && state.room.floor_points.length)
    ? state.room.floor_points
    : [{ x: 0, y: 0 }, { x: dims.width, y: 0 }, { x: dims.width, y: dims.depth }, { x: 0, y: dims.depth }];
  floor.forEach((p, i) => list.push({
    id: `cf:${i}`, group: "Room corners, floor", label: `Corner ${i + 1} (${round(p.x)}, ${round(p.y)})`,
    position: { x: p.x, y: p.y, z: 0 },
  }));
  floor.forEach((p, i) => list.push({
    id: `ct:${i}`, group: "Room corners, ceiling", label: `Corner ${i + 1} at ${round(dims.height)} m`,
    position: { x: p.x, y: p.y, z: dims.height },
  }));
  return list;
}

function markLabel(p) {
  const hit = marks().find((m) => Math.abs(m.position.x - p.x) < 0.005 && Math.abs(m.position.y - p.y) < 0.005
    && Math.abs(m.position.z - p.z) < 0.005);
  return hit ? hit.label : `(${p.x}, ${p.y}, ${p.z})`;
}

function rawFor(id) {
  if (cal.raw[id]) return cal.raw[id];
  const v = (state.fixtureState[id] && state.fixtureState[id].values) || {};
  if (v.pan === undefined || v.tilt === undefined) return null;
  return { pan: v.pan * 256 + (v.pan_fine || 0), tilt: v.tilt * 256 + (v.tilt_fine || 0) };
}

// Move the head's raw 16-bit pan or tilt by `direction` steps of cal.stepSize (to put the beam exactly on a mark)
export async function steer(id, axis, direction) {
  let raw = rawFor(id);
  if (!raw) {
    await aimOne(id);
    raw = rawFor(id);
    if (!raw) return;
  }
  const next = { ...raw, [axis]: Math.max(0, Math.min(65535, raw[axis] + direction * cal.stepSize)) };
  cal.raw[id] = next;
  await api.calPanTilt(id, next.pan >> 8, next.tilt >> 8, next.pan & 255, next.tilt & 255);
  changed();
}

// The head's beam is on the current target point right now: keep that as one observation
export function record(id) {
  const raw = rawFor(id);
  if (!raw) throw new Error("Aim the head first, then steer the beam onto the mark.");
  const p = currentPoint();
  (cal.observations[id] = cal.observations[id] || []).push({
    x: p.x, y: p.y, z: p.z, label: markLabel(p),
    pan: raw.pan >> 8, pan_fine: raw.pan & 255, tilt: raw.tilt >> 8, tilt_fine: raw.tilt & 255,
  });
  delete cal.fits[id];
  changed();
}

export function removeObservation(id, index) {
  (cal.observations[id] || []).splice(index, 1);
  delete cal.fits[id];
  changed();
}

export async function solveFor(id) {
  const obs = cal.observations[id] || [];
  cal.fits[id] = await api.calSolve(id, obs, cal.solvePosition);
  changed();
}

export async function applyFit(id) {
  const fit = cal.fits[id];
  if (!fit) return;
  const a = fit.after;
  await api.calApply({
    fixture_id: id, yaw_deg: a.yaw_deg, pitch_deg: a.pitch_deg, pan_offset_deg: a.pan_offset_deg,
    tilt_offset_deg: a.tilt_offset_deg, inverted_pan: a.inverted_pan, inverted_tilt: a.inverted_tilt,
    position: fit.solved_position ? a.position : undefined,
  });
  delete cal.fits[id];
  delete cal.raw[id];
  cal.original[id] = null; // the applied values are the new starting point for Reset
  changed();
}

export function discardFit(id) {
  delete cal.fits[id];
  changed();
}

// Leaving calibration (closing the window, going to show mode) puts the lights back
export async function finishCalibration() {
  if (isSweeping()) await api.calSweepStop().catch(console.error);
  if (beamIsOn()) await api.calBeam(beamIds(), false).catch(console.error);
}
