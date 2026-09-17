import { api } from "./api.js";

export function initAnimationsModal() {
  document.getElementById("btn-animations").onclick = () => openModal();
  document.querySelector("#modal-animations .modal-close").onclick = () => closeModal();
  document.getElementById("anim-add-keyframe").onclick = () => addKeyframeRow();
  document.getElementById("anim-submit").onclick = submitAnimation;
}

function openModal() {
  renderAnimationList();
  document.getElementById("modal-animations").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-animations").classList.add("hidden");
}

function addKeyframeRow() {
  const container = document.getElementById("anim-keyframes");
  const row = document.createElement("div");
  row.className = "keyframe-row";
  row.innerHTML = `
    <label>t(s) <input type="number" step="0.1" class="kf-time" value="0"></label>
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
    const x = row.querySelector(".kf-x").value;
    const y = row.querySelector(".kf-y").value;
    const z = row.querySelector(".kf-z").value;
    const dimmerVal = row.querySelector(".kf-dimmer").value;
    const kf = { time_s };
    if (x !== "" && y !== "" && z !== "") {
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
  if (!target) {
    alert("Enter a fixture or group id as the track target (see it on hover in the sidebar, or copy from Patch).");
    return;
  }
  const keyframes = collectKeyframes();
  await api.saveAnimation({
    name,
    loop,
    tracks: [{ target_id: target, keyframes }],
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
    playBtn.onclick = () => api.playAnimation(anim.id).catch(console.error);
    const stopBtn = document.createElement("button");
    stopBtn.textContent = "Stop";
    stopBtn.onclick = () => api.stopAnimation(anim.id).catch(console.error);
    const delBtn = document.createElement("button");
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
