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
    throw new Error(`${method} ${path} failed: ${resp.status} ${text}`);
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

  listGroups: () => req("GET", "/groups"),
  createGroup: (group) => req("POST", "/groups", group),
  deleteGroup: (id) => req("DELETE", `/groups/${id}`),

  setColor: (targetId, red, green, blue, white) =>
    req("POST", "/control/color", { target_id: targetId, red, green, blue, white }),
  setDimmer: (targetId, value) => req("POST", "/control/dimmer", { target_id: targetId, value }),
  setStrobe: (targetId, value) => req("POST", "/control/strobe", { target_id: targetId, value }),
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

  dmxPorts: () => req("GET", "/dmx/ports"),
  dmxStatus: () => req("GET", "/dmx/status"),
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
