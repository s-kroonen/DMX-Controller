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

  getRoom: () => req("GET", "/room"),
  updateRoom: (room) => req("PUT", "/room", room),
  addFixture: (fixture) => req("POST", "/room/fixtures", fixture),
  updateFixture: (id, fixture) => req("PUT", `/room/fixtures/${id}`, fixture),
  deleteFixture: (id) => req("DELETE", `/room/fixtures/${id}`),
  addSafetyZone: (zone) => req("POST", "/room/safety-zones", zone),
  deleteSafetyZone: (id) => req("DELETE", `/room/safety-zones/${id}`),

  addRoomObject: (obj) => req("POST", "/room/objects", obj),
  updateRoomObject: (id, obj) => req("PUT", `/room/objects/${id}`, obj),
  deleteRoomObject: (id) => req("DELETE", `/room/objects/${id}`),

  listGroups: () => req("GET", "/groups"),
  createGroup: (group) => req("POST", "/groups", group),
  updateGroup: (id, group) => req("PUT", `/groups/${id}`, group),
  deleteGroup: (id) => req("DELETE", `/groups/${id}`),

  setColor: (targetId, red, green, blue, white) =>
    req("POST", "/control/color", { target_id: targetId, red, green, blue, white }),
  // dimmer/strobe/shutter take the WHOLE selection (array of fixture/group ids) in one call:
  // the engine judges shared-vs-separate dimmer/strobe channels across the whole set.
  setDimmer: (targetIds, value) => req("POST", "/control/dimmer", { target_ids: targetIds, value }),
  setStrobe: (targetIds, value) => req("POST", "/control/strobe", { target_ids: targetIds, value }),
  setShutter: (targetIds, closed) => req("POST", "/control/shutter", { target_ids: targetIds, closed }),
  setPanTilt: (targetId, pan, tilt, panFine = 0, tiltFine = 0) =>
    req("POST", "/control/pan-tilt", { target_id: targetId, pan, tilt, pan_fine: panFine, tilt_fine: tiltFine }),
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
