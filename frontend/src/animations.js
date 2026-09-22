import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

// Two animation kinds, both movement-only for now (color is a separate,
// later concern): "Pattern" animations are freestyler-style -- a raw
// pan/tilt shape sent straight to the fixture(s), no room-space math.
// "Path" animations are the existing calculated keyframe engine, now
// able to reference shared, named 3D Points instead of only raw
// one-off coordinates, so several fixtures can be assigned the same
// point set but visit them in a different order/timing (per-track
// keyframe list + time_offset_s for staged playback).

export function initAnimationsModal() {
  document.getElementById("btn-animations").onclick = () => openModal();
  document.querySelector("#modal-animations .modal-close").onclick = () => closeModal();

  document.getElementById("pt-add").onclick = submitPoint;
  document.getElementById("anim-add-keyframe").onclick = () => addKeyframeRow();
  document.getElementById("anim-submit").onclick = submitAnimation;
  document.getElementById("pat-submit").onclick = submitPattern;
}

async function openModal() {
  await reloadRoomAndGroups();
  renderPointList();
  renderAnimationList();
  renderPatternList();
  document.getElementById("modal-animations").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-animations").classList.add("hidden");
}

// -------------------------------------------------------------- points

async function submitPoint() {
  const name = document.getElementById("pt-name").value || "Point";
  const position = {
    x: Number(document.getElementById("pt-x").value),
    y: Number(document.getElementById("pt-y").value),
    z: Number(document.getElementById("pt-z").value),
  };
  await api.addAnimationPoint({ name, position });
  await reloadRoomAndGroups();
  renderPointList();
}

function renderPointList() {
  const container = document.getElementById("pt-list");
  container.innerHTML = "";
  for (const point of state.room.animation_points || []) {
    const row = document.createElement("div");
    row.className = "zone-row";
    row.innerHTML = `<span>${point.name} (${point.position.x}, ${point.position.y}, ${point.position.z})</span>`;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      await api.deleteAnimationPoint(point.id);
      await reloadRoomAndGroups();
      renderPointList();
    };
    row.appendChild(del);
    container.appendChild(row);
  }
}

// -------------------------------------------------------- path animations

function addKeyframeRow() {
  const container = document.getElementById("anim-keyframes");
  const row = document.createElement("div");
  row.className = "keyframe-row";
  const points = state.room.animation_points || [];
  const pointOptions = ['<option value="">(raw X/Y/Z below)</option>']
    .concat(points.map((p) => `<option value="${p.id}">${p.name}</option>`))
    .join("");
  row.innerHTML = `
    <label>t(s) <input type="number" step="0.1" class="kf-time" value="0"></label>
    <label>Point <select class="kf-point">${pointOptions}</select></label>
    <label>X <input type="number" step="0.1" class="kf-x"></label>
    <label>Y <input type="number" step="0.1" class="kf-y"></label>
    <label>Z <input type="number" step="0.1" class="kf-z"></label>
    <label>Dimmer <input type="number" class="kf-dimmer" placeholder="0-255"></label>
    <button type="button" class="kf-remove">Remove</button>
  `;
  row.querySelector(".kf-remove").onclick = () => row.remove();
  container.appendChild(row);
}

function collectKeyframes() {
  return [...document.querySelectorAll("#anim-keyframes .keyframe-row")].map((row) => {
    const time_s = Number(row.querySelector(".kf-time").value) || 0;
    const pointId = row.querySelector(".kf-point").value;
    const x = row.querySelector(".kf-x").value;
    const y = row.querySelector(".kf-y").value;
    const z = row.querySelector(".kf-z").value;
    const dimmerVal = row.querySelector(".kf-dimmer").value;
    const kf = { time_s };
    if (pointId) {
      kf.point_id = pointId;
    } else if (x !== "" && y !== "" && z !== "") {
      kf.target_point = { x: Number(x), y: Number(y), z: Number(z) };
    }
    if (dimmerVal !== "") kf.dimmer = Number(dimmerVal);
    return kf;
  });
}

async function submitAnimation() {
  const name = document.getElementById("anim-name").value || "Animation";
  const target = document.getElementById("anim-target").value;
  const loop = document.getElementById("anim-loop").checked;
  const timeOffset = Number(document.getElementById("anim-offset").value) || 0;
  if (!target) {
    alert("Enter a fixture or group id as the track target (see it on hover in the sidebar, or copy from Patch).");
    return;
  }
  const keyframes = collectKeyframes();
  await api.saveAnimation({
    name,
    loop,
    tracks: [{ target_id: target, keyframes, time_offset_s: timeOffset }],
  });
  renderAnimationList();
}

async function renderAnimationList() {
  const container = document.getElementById("anim-list");
  const animations = await api.listAnimations();
  container.innerHTML = "";
  for (const anim of animations) {
    const row = document.createElement("div");
    row.className = "anim-row";
    row.innerHTML = `<span>${anim.name} (${anim.tracks.length} track(s), ${anim.loop ? "loop" : "once"})</span>`;
    const playBtn = document.createElement("button");
    playBtn.textContent = "Play";
    playBtn.className = "show-only";
    playBtn.onclick = () => api.playAnimation(anim.id).catch(console.error);
    const stopBtn = document.createElement("button");
    stopBtn.textContent = "Stop";
    stopBtn.className = "show-only";
    stopBtn.onclick = () => api.stopAnimation(anim.id).catch(console.error);
    const delBtn = document.createElement("button");
    delBtn.className = "edit-only";
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => {
      await api.deleteAnimation(anim.id);
      renderAnimationList();
    };
    row.appendChild(playBtn);
    row.appendChild(stopBtn);
    row.appendChild(delBtn);
    container.appendChild(row);
  }
}

// ----------------------------------------------------------- pattern animations

async function submitPattern() {
  const name = document.getElementById("pat-name").value || "Pattern";
  const target = document.getElementById("pat-target").value;
  if (!target) {
    alert("Enter a fixture or group id as the target.");
    return;
  }
  await api.savePattern({
    name,
    target_id: target,
    shape: document.getElementById("pat-shape").value,
    speed_hz: Number(document.getElementById("pat-speed").value) || 0,
    pan_center: Number(document.getElementById("pat-pan-center").value) || 0,
    tilt_center: Number(document.getElementById("pat-tilt-center").value) || 0,
    pan_size: Number(document.getElementById("pat-pan-size").value) || 0,
    tilt_size: Number(document.getElementById("pat-tilt-size").value) || 0,
  });
  renderPatternList();
}

async function renderPatternList() {
  const container = document.getElementById("pat-list");
  const patterns = await api.listPatterns();
  container.innerHTML = "";
  for (const pattern of patterns) {
    const row = document.createElement("div");
    row.className = "anim-row";
    row.innerHTML = `<span>${pattern.name} (${pattern.shape}, ${pattern.speed_hz}Hz)</span>`;
    const playBtn = document.createElement("button");
    playBtn.textContent = "Play";
    playBtn.className = "show-only";
    playBtn.onclick = () => api.playPattern(pattern.id).catch(console.error);
    const stopBtn = document.createElement("button");
    stopBtn.textContent = "Stop";
    stopBtn.className = "show-only";
    stopBtn.onclick = () => api.stopPattern(pattern.id).catch(console.error);
    const delBtn = document.createElement("button");
    delBtn.className = "edit-only";
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => {
      await api.deletePattern(pattern.id);
      renderPatternList();
    };
    row.appendChild(playBtn);
    row.appendChild(stopBtn);
    row.appendChild(delBtn);
    container.appendChild(row);
  }
}
