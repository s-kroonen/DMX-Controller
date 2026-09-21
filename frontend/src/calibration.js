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
    if (!(f.id in cal.original)) {
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

export async function aimAll() {
  const ids = includedIds();
  if (!ids.length) return;
  const { results } = await api.calAim(ids, currentPoint());
  Object.assign(cal.results, results);
  changed();
}

export async function aimOne(id) {
  const { results } = await api.calAim([id], currentPoint());
  Object.assign(cal.results, results);
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

// Leaving calibration (closing the window, going to show mode) puts the lights back
export async function finishCalibration() {
  if (isSweeping()) await api.calSweepStop().catch(console.error);
  if (beamIsOn()) await api.calBeam(beamIds(), false).catch(console.error);
}
