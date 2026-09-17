import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

// A room is rarely a perfect rectangle. This is a plain 2D floor-plan
// editor: click to place corners (any count, any shape, not necessarily
// convex), and the 3D scene extrudes that outline up to the ceiling
// height. Points are stored/sent in meters, room-space X/Y; the canvas
// just maps a fixed pixels-per-meter scale around a center origin.

const PX_PER_METER = 20;
let points = []; // [{x, y}] in meters

export function initRoomShapeModal() {
  document.getElementById("btn-room-shape").onclick = () => openModal();
  document.querySelector("#modal-room-shape .modal-close").onclick = () => closeModal();

  const canvas = document.getElementById("rs-canvas");
  canvas.addEventListener("click", (evt) => {
    const rect = canvas.getBoundingClientRect();
    const px = evt.clientX - rect.left;
    const py = evt.clientY - rect.top;
    const meterX = (px - canvas.width / 2) / PX_PER_METER;
    const meterY = (py - canvas.height / 2) / PX_PER_METER;
    points.push({ x: round2(meterX), y: round2(meterY) });
    redraw();
  });

  document.getElementById("rs-undo").onclick = () => {
    points.pop();
    redraw();
  };
  document.getElementById("rs-clear").onclick = () => {
    points = [];
    redraw();
  };
  document.getElementById("rs-save").onclick = submitRoomShape;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function openModal() {
  document.getElementById("rs-name").value = state.room.name || "";
  document.getElementById("rs-width").value = state.room.dimensions?.width ?? 10;
  document.getElementById("rs-depth").value = state.room.dimensions?.depth ?? 10;
  document.getElementById("rs-height").value = state.room.dimensions?.height ?? 4;
  points = (state.room.floor_points || []).map((p) => ({ x: p.x, y: p.y }));
  redraw();
  document.getElementById("modal-room-shape").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-room-shape").classList.add("hidden");
}

function redraw() {
  const canvas = document.getElementById("rs-canvas");
  const ctx = canvas.getContext("2d");
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // grid, 1m spacing
  ctx.strokeStyle = "#2a2f36";
  ctx.lineWidth = 1;
  for (let m = -20; m <= 20; m++) {
    const x = cx + m * PX_PER_METER;
    const y = cy + m * PX_PER_METER;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }
  // origin axes
  ctx.strokeStyle = "#4a5058";
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, canvas.height); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(canvas.width, cy); ctx.stroke();

  if (points.length > 0) {
    ctx.strokeStyle = "#3a7bd5";
    ctx.fillStyle = "rgba(58, 123, 213, 0.15)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = cx + p.x * PX_PER_METER;
      const y = cy + p.y * PX_PER_METER;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    if (points.length > 2) ctx.closePath();
    ctx.fill();
    ctx.stroke();

    points.forEach((p, i) => {
      const x = cx + p.x * PX_PER_METER;
      const y = cy + p.y * PX_PER_METER;
      ctx.fillStyle = i === 0 ? "#ffffff" : "#3a7bd5";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  document.getElementById("rs-point-count").textContent =
    points.length === 0 ? "0 points (rectangle fallback will be used)" :
    points.length < 3 ? `${points.length} point(s) -- need at least 3 to close a shape` :
    `${points.length} points`;
}

async function submitRoomShape() {
  if (points.length > 0 && points.length < 3) {
    alert("Need at least 3 points to define a floor shape (or clear all to use the rectangle fallback).");
    return;
  }
  await api.updateRoom({
    name: document.getElementById("rs-name").value || "Untitled Room",
    dimensions: {
      width: Number(document.getElementById("rs-width").value),
      depth: Number(document.getElementById("rs-depth").value),
      height: Number(document.getElementById("rs-height").value),
    },
    floor_points: points,
  });
  await reloadRoomAndGroups();
  closeModal();
}
