import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { api } from "../src/api.js";
import { state, onStateChange } from "../src/state.js";

// A deliberately lightweight 3D view for mobile "Aim" -- view + tap-to-aim
// only, no gizmo/dragging (repositioning fixtures is a desktop editing
// task, not something Aim needs), and fixtures are plain spheres rather
// than the desktop's per-type body models. This is its own small module
// instead of reusing the desktop's scene3d.js so mobile never depends on
// (or risks breaking) the gizmo-editing code that module also owns.
//
// Same room-space convention as the desktop 3D view: meters, Z-up,
// mapped into Three's Y-up world by rotating the whole root -90deg
// around X, so children work directly in room-space coordinates.

let scene, camera, renderer, controls, roomRoot, fixturesGroup;
let aimSurfaces = [];
let fixtureMeshes = new Map();
let lastStaticKey = "";
let container;

export function initAim3D(hostElement) {
  container = hostElement;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e1013);

  camera = new THREE.PerspectiveCamera(60, 1, 0.1, 500);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  container.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  camera.position.set(6, 6, 8);
  controls.target.set(0, 1.2, 0);
  controls.update();

  roomRoot = new THREE.Group();
  roomRoot.rotation.x = -Math.PI / 2;
  scene.add(roomRoot);
  fixturesGroup = new THREE.Group();
  roomRoot.add(fixturesGroup);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5);
  dir.position.set(5, 10, 5);
  scene.add(dir);

  resize();
  window.addEventListener("resize", resize);
  // "click" (not pointerdown) -- browsers suppress it after a drag, for
  // touch and mouse alike, so orbiting the camera never also fires an aim.
  renderer.domElement.addEventListener("click", onTap);

  onStateChange(rebuild);
  rebuild();
  animate();
}

function resize() {
  if (!container) return;
  const w = container.clientWidth || 1;
  const h = container.clientHeight || 1;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function effectiveFloorPoints() {
  const drawn = state.room.floor_points || [];
  if (drawn.length >= 3) return drawn;
  const dims = state.room.dimensions || { width: 10, depth: 10 };
  const hw = dims.width / 2, hd = dims.depth / 2;
  return [{ x: -hw, y: -hd }, { x: hw, y: -hd }, { x: hw, y: hd }, { x: -hw, y: hd }];
}

function clearGroup(group) {
  while (group.children.length) group.remove(group.children[0]);
}

function rebuild() {
  const staticKey = JSON.stringify({ floor: state.room.floor_points, dims: state.room.dimensions });
  if (staticKey !== lastStaticKey) {
    lastStaticKey = staticKey;
    rebuildRoomShell();
  }
  updateFixtures();
}

function rebuildRoomShell() {
  // Rebuild everything except fixturesGroup, which updateFixtures() owns.
  for (const child of [...roomRoot.children]) {
    if (child !== fixturesGroup) roomRoot.remove(child);
  }
  aimSurfaces = [];

  const floorPoints = effectiveFloorPoints();
  const height = state.room.dimensions?.height ?? 4;
  const shape = new THREE.Shape(floorPoints.map((p) => new THREE.Vector2(p.x, p.y)));

  const floorMesh = new THREE.Mesh(
    new THREE.ShapeGeometry(shape),
    new THREE.MeshStandardMaterial({ color: 0x22262c, side: THREE.DoubleSide })
  );
  roomRoot.add(floorMesh);
  aimSurfaces.push(floorMesh);

  const bounds = floorPoints.reduce(
    (acc, p) => ({
      minX: Math.min(acc.minX, p.x), maxX: Math.max(acc.maxX, p.x),
      minY: Math.min(acc.minY, p.y), maxY: Math.max(acc.maxY, p.y),
    }),
    { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity }
  );
  const grid = new THREE.GridHelper(
    Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 1), 20, 0x444444, 0x2a2a2a
  );
  grid.rotation.x = Math.PI / 2;
  grid.position.z = 0.001;
  roomRoot.add(grid);

  const ceiling = new THREE.Mesh(
    new THREE.ShapeGeometry(shape),
    new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.05, side: THREE.DoubleSide })
  );
  ceiling.position.z = height;
  roomRoot.add(ceiling);
  aimSurfaces.push(ceiling);

  const wallMat = new THREE.MeshStandardMaterial({ color: 0x3a7bd5, transparent: true, opacity: 0.08, side: THREE.DoubleSide });
  for (let i = 0; i < floorPoints.length; i++) {
    const p1 = floorPoints[i];
    const p2 = floorPoints[(i + 1) % floorPoints.length];
    const vertices = new Float32Array([
      p1.x, p1.y, 0, p2.x, p2.y, 0, p2.x, p2.y, height,
      p1.x, p1.y, 0, p2.x, p2.y, height, p1.x, p1.y, height,
    ]);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
    geo.computeVertexNormals();
    const wall = new THREE.Mesh(geo, wallMat);
    roomRoot.add(wall);
    aimSurfaces.push(wall);
  }
}

function updateFixtures() {
  const currentIds = new Set(state.room.fixtures.map((f) => f.id));
  for (const [id, entry] of fixtureMeshes) {
    if (!currentIds.has(id)) {
      fixturesGroup.remove(entry.mesh);
      fixtureMeshes.delete(id);
    }
  }
  for (const fixture of state.room.fixtures) {
    let entry = fixtureMeshes.get(fixture.id);
    if (!entry) {
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(0.12, 16, 16),
        new THREE.MeshStandardMaterial({ color: 0x3a7bd5 })
      );
      fixturesGroup.add(mesh);
      entry = { mesh };
      fixtureMeshes.set(fixture.id, entry);
    }
    entry.mesh.position.set(fixture.position.x, fixture.position.y, fixture.position.z);
    entry.mesh.material.color.set(state.isSelected(fixture.id) ? 0xffffff : 0x3a7bd5);
  }
}

function onTap(evt) {
  if (state.selection.size === 0) return;
  const rect = renderer.domElement.getBoundingClientRect();
  const mouse = new THREE.Vector2(
    ((evt.clientX - rect.left) / rect.width) * 2 - 1,
    -((evt.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(mouse, camera);
  const hits = raycaster.intersectObjects(aimSurfaces, false);
  if (hits.length === 0) return;
  const point = hits[0].point.clone();
  roomRoot.worldToLocal(point);
  for (const targetId of state.selection) {
    api.aim(targetId, point.x, point.y, point.z).catch(console.error);
  }
}

export function resizeAim3D() {
  resize();
}
