import { api } from "./api.js";
import { state } from "./state.js";
import { reloadRoomAndGroups } from "./main_data.js";

// A room is rarely a perfect rectangle. This is a plain 2D floor-plan
// editor: click empty space to place a new corner, or drag an existing
// one to move it -- any count of corners, any shape, not necessarily
// convex. This is the ONLY place the room shape is edited; moving a
// point here rescales fixtures/objects/zones proportionally on save
// (Room.apply_shape() on the backend), which is too consequential an
// action to do by dragging in the 3D view. That view is fixtures/objects
// only. The 3D scene extrudes whatever outline is saved here up to the
// ceiling height. Points are stored/sent in meters, room-space X/Y; the
// canvas just maps a fixed pixels-per-meter scale around a center origin.
//
// A dragged/typed/clicked point is always kept inside the fallback
// width/depth box (the rectangle used when there is no drawn shape) --
// drawn as a dashed outline -- so the shape can never define a fixture
// position outside what apply_shape()/clamp_position() would accept
// anyway. Dragging and placing snap to a grid: hold Shift for a fine
// grid, Ctrl for a rough one, neither for a free (unsnapped) placement.
// Points can also be typed exactly in the list below the canvas, for a
// room whose measurements are already known.

const PX_PER_METER = 20;
const HIT_RADIUS_PX = 14;
const FINE_SNAP_M = 0.1;
const ROUGH_SNAP_M = 0.5;
let points = []; // [{x, y}] in meters
let draggingIndex = null;
let mouseDownPos = null;
let highlightIndex = null; // hovering/focusing a point's row in the list below highlights it on the canvas
let lastListCount = -1; // rebuild the point-list rows only when the count changes, to keep a field's focus

export function initRoomShapeModal() {
  document.getElementById("btn-room-shape").onclick = () => openModal();
  document.querySelector("#modal-room-shape .modal-close").onclick = () => closeModal();

  const canvas = document.getElementById("rs-canvas");

  canvas.addEventListener("mousedown", (evt) => {
    const { px, py } = eventToPixels(canvas, evt);
    mouseDownPos = { px, py };
    draggingIndex = hitTestPoint(px, py);
    canvas.style.cursor = draggingIndex !== null ? "grabbing" : "crosshair";
  });

  window.addEventListener("mousemove", (evt) => {
    if (draggingIndex === null) {
      // hover feedback: show a grab cursor over a draggable corner
      if (document.getElementById("modal-room-shape").classList.contains("hidden")) return;
      const { px, py } = eventToPixels(canvas, evt);
      canvas.style.cursor = hitTestPoint(px, py) !== null ? "grab" : "crosshair";
      return;
    }
    const { px, py } = eventToPixels(canvas, evt);
    points[draggingIndex] = pixelsToMeters(px, py, evt);
    redrawCanvas(); // dragging never adds/removes a point -- just move it, without touching the list's focus
  });

  window.addEventListener("mouseup", (evt) => {
    if (draggingIndex !== null) {
      draggingIndex = null;
      mouseDownPos = null;
      canvas.style.cursor = "crosshair";
      syncPointList(); // the drag is done: now reflect the final position in the list's inputs
      return;
    }
    if (!mouseDownPos) return;
    const { px, py } = eventToPixels(canvas, evt);
    const moved = Math.hypot(px - mouseDownPos.px, py - mouseDownPos.py);
    mouseDownPos = null;
    if (moved > 4) return; // was a drag over empty canvas, not a click -- ignore
    if (px < 0 || py < 0 || px > canvas.width || py > canvas.height) return; // released outside canvas
    points.push(pixelsToMeters(px, py, evt));
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

  // Shrinking the fallback box below an already-placed point's position pulls that point back in,
  // same as dragging it to the edge would.
  document.getElementById("rs-width").addEventListener("input", clampAllAndRedraw);
  document.getElementById("rs-depth").addEventListener("input", clampAllAndRedraw);
}

function clampAllAndRedraw() {
  points = points.map((p) => clampToBounds(p));
  redraw();
}

// Convert a mouse event to canvas-*internal* pixel coordinates. The
// canvas is drawn at its `width`/`height` attribute resolution, but its
// on-screen CSS box can differ slightly (e.g. a 1px border on each side
// makes getBoundingClientRect() a couple of pixels bigger than the
// drawing buffer) -- scaling by the ratio keeps hit-testing and dragging
// accurate instead of drifting off by a few pixels.
function eventToPixels(canvas, evt) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    px: (evt.clientX - rect.left) * scaleX,
    py: (evt.clientY - rect.top) * scaleY,
  };
}

// The fallback rectangle (used when no shape is drawn) is centered on the origin, so it also
// defines the box a drawn point may never leave: Room.apply_shape()'s rectangle fallback and
// clamp_position() both work the same way on the backend.
function bounds() {
  const hw = Math.max(0.1, Number(document.getElementById("rs-width").value) || 10) / 2;
  const hd = Math.max(0.1, Number(document.getElementById("rs-depth").value) || 10) / 2;
  return { hw, hd };
}

function clampToBounds(p) {
  const { hw, hd } = bounds();
  return { x: round2(Math.max(-hw, Math.min(hw, p.x))), y: round2(Math.max(-hd, Math.min(hd, p.y))) };
}

// px/py in canvas pixels -> a point in meters, snapped (per the held modifier key, if `evt` is
// given) and clamped to the fallback width/depth box.
function pixelsToMeters(px, py, evt) {
  const canvas = document.getElementById("rs-canvas");
  let x = (px - canvas.width / 2) / PX_PER_METER;
  let y = (py - canvas.height / 2) / PX_PER_METER;
  const snap = evt && evt.shiftKey ? FINE_SNAP_M : evt && evt.ctrlKey ? ROUGH_SNAP_M : null;
  if (snap) {
    x = Math.round(x / snap) * snap;
    y = Math.round(y / snap) * snap;
  }
  return clampToBounds({ x, y });
}

function hitTestPoint(px, py) {
  const canvas = document.getElementById("rs-canvas");
  const cx = canvas.width / 2, cy = canvas.height / 2;
  for (let i = points.length - 1; i >= 0; i--) {
    const x = cx + points[i].x * PX_PER_METER;
    const y = cy + points[i].y * PX_PER_METER;
    if (Math.hypot(px - x, py - y) <= HIT_RADIUS_PX) return i;
  }
  return null;
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
  lastListCount = -1; // force the point list to rebuild for the newly opened room
  redraw();
  document.getElementById("modal-room-shape").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-room-shape").classList.add("hidden");
}

// The polygon's edges as [pointA, pointB, indexA, indexB] pairs, in the order drawn. Two points
// make one open segment (no closing edge); three or more close into a polygon.
function edgeList() {
  if (points.length < 2) return [];
  const edges = [];
  for (let i = 0; i < points.length; i++) {
    const j = i + 1 === points.length ? 0 : i + 1;
    if (i + 1 === points.length && points.length < 3) break; // no closing edge for an open segment
    edges.push([points[i], points[j], i, j]);
  }
  return edges;
}

function centroidPx(cx, cy) {
  if (points.length === 0) return { x: cx, y: cy };
  const sx = points.reduce((sum, p) => sum + p.x, 0) / points.length;
  const sy = points.reduce((sum, p) => sum + p.y, 0) / points.length;
  return { x: cx + sx * PX_PER_METER, y: cy + sy * PX_PER_METER };
}

function redraw() {
  redrawCanvas();
  renderPointList();
}

function redrawCanvas() {
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

  // the fallback width/depth box -- a drawn point can never leave it
  const { hw, hd } = bounds();
  ctx.save();
  ctx.strokeStyle = "#c98a2c";
  ctx.setLineDash([5, 4]);
  ctx.lineWidth = 1.5;
  ctx.strokeRect(cx - hw * PX_PER_METER, cy - hd * PX_PER_METER, hw * 2 * PX_PER_METER, hd * 2 * PX_PER_METER);
  ctx.restore();

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

    // side lengths, labelled at each edge's midpoint, offset outward from the shape's centroid so
    // the text sits clear of the fill; the edges touching the point being dragged (or hovered from
    // the list below) are drawn larger/brighter so a specific measurement is easy to pick out.
    const centroid = centroidPx(cx, cy);
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const [a, b, ia, ib] of edgeList()) {
      const ax = cx + a.x * PX_PER_METER, ay = cy + a.y * PX_PER_METER;
      const bx = cx + b.x * PX_PER_METER, by = cy + b.y * PX_PER_METER;
      const mx = (ax + bx) / 2, my = (ay + by) / 2;
      let nx = mx - centroid.x, ny = my - centroid.y;
      const nlen = Math.hypot(nx, ny) || 1;
      nx /= nlen; ny /= nlen;
      const active = draggingIndex !== null
        ? (ia === draggingIndex || ib === draggingIndex)
        : (highlightIndex !== null && (ia === highlightIndex || ib === highlightIndex));
      const length = Math.hypot(b.x - a.x, b.y - a.y);
      const label = `${length.toFixed(2)} m`;
      const lx = mx + nx * (active ? 16 : 13), ly = my + ny * (active ? 16 : 13);
      ctx.font = active ? "bold 12px sans-serif" : "11px sans-serif";
      const w = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(14, 16, 19, 0.8)";
      ctx.fillRect(lx - w / 2 - 3, ly - 8, w + 6, 16);
      ctx.fillStyle = active ? "#ffd27a" : "#9aa2ac";
      ctx.fillText(label, lx, ly);
    }

    points.forEach((p, i) => {
      const x = cx + p.x * PX_PER_METER;
      const y = cy + p.y * PX_PER_METER;
      const active = i === draggingIndex || i === highlightIndex;
      ctx.fillStyle = i === 0 ? "#ffffff" : active ? "#ffd27a" : "#3a7bd5";
      ctx.beginPath();
      ctx.arc(x, y, active ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  document.getElementById("rs-point-count").textContent =
    points.length === 0 ? "0 points (rectangle fallback will be used)" :
    points.length < 3 ? `${points.length} point(s) -- need at least 3 to close a shape` :
    `${points.length} points`;
}

// The editable list of points below the canvas: rebuilt only when the point count changes (add,
// remove, undo, clear, opening the modal), so typing in a field is never interrupted by a rebuild.
// A drag or a width/depth clamp instead calls syncPointList(), which only updates values -- and
// skips whichever field currently has focus.
function renderPointList() {
  const container = document.getElementById("rs-point-list");
  if (points.length === lastListCount) {
    syncPointList();
    return;
  }
  lastListCount = points.length;
  container.innerHTML = "";
  points.forEach((p, i) => {
    const row = document.createElement("div");
    row.className = "rs-point-row";
    row.innerHTML = `
      <span class="rs-point-index">${i === 0 ? "1 (start)" : i + 1}</span>
      <label>X (m) <input type="number" step="0.05" class="rs-point-x"></label>
      <label>Y (m) <input type="number" step="0.05" class="rs-point-y"></label>
      <button type="button" class="rs-point-remove" title="Remove this point">x</button>
    `;
    const xInput = row.querySelector(".rs-point-x");
    const yInput = row.querySelector(".rs-point-y");
    xInput.value = p.x;
    yInput.value = p.y;
    const apply = () => {
      points[i] = clampToBounds({ x: Number(xInput.value) || 0, y: Number(yInput.value) || 0 });
      xInput.value = points[i].x;
      yInput.value = points[i].y;
      redrawCanvas(); // the list itself is untouched, so the field just edited keeps focus
    };
    xInput.addEventListener("input", apply);
    yInput.addEventListener("input", apply);
    const highlight = (on) => { highlightIndex = on ? i : null; redrawCanvas(); };
    row.addEventListener("mouseenter", () => highlight(true));
    row.addEventListener("mouseleave", () => highlight(false));
    xInput.addEventListener("focus", () => highlight(true));
    yInput.addEventListener("focus", () => highlight(true));
    xInput.addEventListener("blur", () => highlight(false));
    yInput.addEventListener("blur", () => highlight(false));
    row.querySelector(".rs-point-remove").onclick = () => {
      points.splice(i, 1);
      highlightIndex = null;
      redraw();
    };
    container.appendChild(row);
  });
  if (points.length === 0) container.innerHTML = '<div class="hint">No points yet -- click the canvas to place one.</div>';
}

function syncPointList() {
  const rows = document.querySelectorAll("#rs-point-list .rs-point-row");
  rows.forEach((row, i) => {
    const p = points[i];
    if (!p) return;
    const xInput = row.querySelector(".rs-point-x");
    const yInput = row.querySelector(".rs-point-y");
    if (document.activeElement !== xInput) xInput.value = p.x;
    if (document.activeElement !== yInput) yInput.value = p.y;
  });
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
