import { state } from "./state.js";

const BASE = "/api";

async function req(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(BASE + path, opts);
  if (!resp.ok) {
    const text = await resp.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not JSON, use raw text */ }
    // 409 = the current mode does not allow this (another screen switched modes): say so
    if (resp.status === 409) window.dispatchEvent(new CustomEvent("dmx-notice", { detail }));
    throw new Error(detail);
  }
  return resp.json();
}

export const api = {
  listProfiles: () => req("GET", "/fixtures/profiles"),
  saveProfile: (profile) => req("POST", "/fixtures/profiles", profile),
  deleteProfile: (id) => req("DELETE", `/fixtures/profiles/${id}`),
  importQxf: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(BASE + "/fixtures/import-qxf", { method: "POST", body: form });
    if (!resp.ok) throw new Error("qxf import failed: " + (await resp.text()));
    return resp.json();
  },

  // calibration tools (edit mode)
  calAim: (ids, p) => req("POST", "/calibration/aim", { target_ids: ids, x: p.x, y: p.y, z: p.z }),
  calBeam: (ids, on) => req("POST", "/calibration/beam", { target_ids: ids, on }),
  calOffsets: (body) => req("POST", "/calibration/offsets", body),
  calSweep: (ids, points, seconds) =>
    req("POST", "/calibration/sweep", { target_ids: ids, points, seconds_per_leg: seconds }),
  calSweepStop: () => req("POST", "/calibration/sweep/stop"),
  calSolve: (fixtureId, observations, solvePosition) =>
    req("POST", "/calibration/solve", { fixture_id: fixtureId, observations, solve_position: solvePosition }),
  calApply: (body) => req("POST", "/calibration/apply", body),
  calPanTilt: (id, pan, tilt, panFine, tiltFine) =>
    req("POST", "/calibration/pan-tilt", { target_id: id, pan, tilt, pan_fine: panFine, tilt_fine: tiltFine }),

  getMode: () => req("GET", "/mode"),
  setMode: (mode) => req("PUT", "/mode", { mode }),
  // rooms saved in the backend
  listRooms: () => req("GET", "/rooms"),
  saveRoom: (name) => req("POST", "/rooms", { name }),
  newRoom: (name) => req("POST", "/rooms/new", { name }),
  loadRoom: (id) => req("POST", `/rooms/${encodeURIComponent(id)}/load`),
  deleteRoom: (id) => req("DELETE", `/rooms/${encodeURIComponent(id)}`),

  getRoom: () => req("GET", "/room"),
  updateRoom: (room) => req("PUT", "/room", room),
  addFixture: (fixture) => req("POST", "/room/fixtures", fixture),
  updateFixture: (id, fixture) => req("PUT", `/room/fixtures/${id}`, fixture),
  deleteFixture: (id) => req("DELETE", `/room/fixtures/${id}`),
  addSafetyZone: (zone) => req("POST", "/room/safety-zones", zone),
  deleteSafetyZone: (id) => req("DELETE", `/room/safety-zones/${id}`),

  addAnimationPoint: (point) => req("POST", "/room/points", point),
  updateAnimationPoint: (id, point) => req("PUT", `/room/points/${id}`, point),
  deleteAnimationPoint: (id) => req("DELETE", `/room/points/${id}`),

  addRoomObject: (obj) => req("POST", "/room/objects", obj),
  updateRoomObject: (id, obj) => req("PUT", `/room/objects/${id}`, obj),
  deleteRoomObject: (id) => req("DELETE", `/room/objects/${id}`),

  listGroups: () => req("GET", "/groups"),
  createGroup: (group) => req("POST", "/groups", group),
  updateGroup: (id, group) => req("PUT", `/groups/${id}`, group),
  deleteGroup: (id) => req("DELETE", `/groups/${id}`),

  // zones: optional list of zone ids (a light bar's spots/derbies); empty/omitted = every zone
  setColor: (targetId, red, green, blue, white, zones) =>
    req("POST", "/control/color", {
      target_id: targetId, red, green, blue, white, zones: zones && zones.length ? zones : null,
    }),
  // dimmer/strobe/shutter take the WHOLE selection (array of fixture/group ids) in one call:
  // the engine judges shared-vs-separate dimmer/strobe channels across the whole set.
  // zones given = brightness of just those zones (fixtures that declare zones); omitted = master dimmer
  setDimmer: (targetIds, value, zones) =>
    req("POST", "/control/dimmer", { target_ids: targetIds, value, zones: zones && zones.length ? zones : null }),
  // zones: strobe only these zones (fixtures whose zones have strobe channels); resolves to
  // { also_strobed: { fixtureId: [zone ids that share a strobe channel with a chosen one] } }
  setStrobe: (targetIds, value, zones) =>
    req("POST", "/control/strobe", { target_ids: targetIds, value, zones: zones && zones.length ? zones : null }),
  setShutter: (targetIds, closed) => req("POST", "/control/shutter", { target_ids: targetIds, closed }),
  // show mode drives heads through /control; in edit mode the raw window is a test tool
  setPanTilt: (targetId, pan, tilt, panFine = 0, tiltFine = 0) =>
    req("POST", state.mode === "edit" ? "/calibration/pan-tilt" : "/control/pan-tilt", { target_id: targetId, pan, tilt, pan_fine: panFine, tilt_fine: tiltFine }),
  aim: (targetId, x, y, z, allowUnsafe = false) =>
    req("POST", "/control/aim", { target_id: targetId, x, y, z, allow_unsafe: allowUnsafe }),
  setCustom: (targetId, label, value) =>
    req("POST", "/control/custom", { target_id: targetId, label, value }),
  blackout: () => req("POST", "/control/blackout"),

  listAnimations: () => req("GET", "/animations"),
  saveAnimation: (animation) => req("POST", "/animations", animation),
  deleteAnimation: (id) => req("DELETE", `/animations/${id}`),
  playAnimation: (id) => req("POST", `/animations/${id}/play`),
  stopAnimation: (id) => req("POST", `/animations/${id}/stop`),

  listPatterns: () => req("GET", "/patterns"),
  savePattern: (pattern) => req("POST", "/patterns", pattern),
  deletePattern: (id) => req("DELETE", `/patterns/${id}`),
  playPattern: (id) => req("POST", `/patterns/${id}/play`),
  stopPattern: (id) => req("POST", `/patterns/${id}/stop`),

  audioDevices: () => req("GET", "/audio/devices"),
  audioStatus: () => req("GET", "/audio/status"),
  audioSelect: (deviceId) => req("POST", "/audio/select", { device_id: deviceId }),
  audioStart: () => req("POST", "/audio/start"),
  audioStop: () => req("POST", "/audio/stop"),
  audioConfig: (config) => req("POST", "/audio/config", config),
  audioTap: () => req("POST", "/audio/tap"),
  audioFunctionTypes: () => req("GET", "/audio/function-types"),
  audioAddFunction: (fn) => req("POST", "/audio/functions", fn),
  audioPatchFunction: (id, patch) => req("PATCH", `/audio/functions/${id}`, patch),
  audioDeleteFunction: (id) => req("DELETE", `/audio/functions/${id}`),

  dmxPorts: () => req("GET", "/dmx/ports"),
  dmxStatus: () => req("GET", "/dmx/status"),
  dmxConnect: (port, baudRate) =>
    req("POST", "/dmx/connect", { port, baud_rate: baudRate }),
  dmxDisconnect: () => req("POST", "/dmx/disconnect"),
  dmxReconnect: (killOtherHolders = false) =>
    req("POST", "/dmx/reconnect", { kill_other_holders: killOtherHolders }),
  dmxHolders: () => req("GET", "/dmx/holders"),
  dmxKillHolders: () => req("POST", "/dmx/kill-holders"),
  dmxRaw: (channel, value) => req("POST", "/dmx/raw", { channel, value }),
  dmxRawBlackout: () => req("POST", "/dmx/raw/blackout"),
  snapshot: () => req("GET", "/snapshot"),

  exportConfig: () => req("GET", "/config/export"),
  importConfig: (bundle) => req("POST", "/config/import", bundle),
};

export function connectWebSocket(onMessage) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (evt) => {
    try {
      onMessage(JSON.parse(evt.data));
    } catch (e) {
      console.error("bad ws payload", e);
    }
  };
  ws.onclose = () => {
    setTimeout(() => connectWebSocket(onMessage), 2000);
  };
  return ws;
}
