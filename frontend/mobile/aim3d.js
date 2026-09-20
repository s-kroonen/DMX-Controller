import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { api } from "../src/api.js";
import { state, onStateChange, notifyStateChange } from "../src/state.js";
import { createFixtureMesh } from "../src/scene3d.js";

// A smaller, view+tap-to-aim-only version of the desktop 3D view -- same
// per-type fixture body models and the same beam ("light path") math via
// the shared createFixtureMesh(), just no gizmo/dragging (repositioning
// fixtures stays a desktop editing task). Only the interaction surface is
// different, not what it looks like or how a fixture's aim is shown.
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

// Mirrors scene3d.js's updateFixtures() -- same body model, same
// yaw/pitch/roll composition, same beam ("light path") math -- minus the
// gizmo drag-skip branches, since nothing here ever drags a fixture.
function updateFixtures() {
  const currentIds = new Set(state.room.fixtures.map((f) => f.id));
  for (const [id, entry] of fixtureMeshes) {
    if (!currentIds.has(id)) {
      fixturesGroup.remove(entry.group);
      fixtureMeshes.delete(id);
    }
  }

  for (const fixture of state.room.fixtures) {
    let entry = fixtureMeshes.get(fixture.id);
    if (entry && entry.profileId !== fixture.profile_id) {
      fixturesGroup.remove(entry.group);
      fixtureMeshes.delete(fixture.id);
      entry = null;
    }
    if (!entry) {
      entry = createFixtureMesh(fixture);
      fixturesGroup.add(entry.group);
      fixtureMeshes.set(fixture.id, entry);
    }

    entry.group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);
    const yawRad = THREE.MathUtils.degToRad(fixture.orientation?.yaw_deg || 0);
    entry.group.rotation.z = -yawRad;
    const pitchRad = THREE.MathUtils.degToRad(fixture.orientation?.pitch_deg ?? 180);
    entry.pitchGroup.rotation.x = pitchRad - Math.PI / 2;
    entry.rollGroup.rotation.y = THREE.MathUtils.degToRad(fixture.orientation?.roll_deg || 0);

    entry.bodyMaterial.color.set(state.isSelected(fixture.id) ? 0xffffff : entry.bodyMaterial.userData.baseColor);

    const fixtureState = state.fixtureState[fixture.id];
    const color = fixtureState && fixtureState.values
      ? new THREE.Color(
          (fixtureState.values.red || 0) / 255,
          (fixtureState.values.green || 0) / 255,
          (fixtureState.values.blue || 0) / 255,
        )
      : new THREE.Color(1, 1, 1);

    let beamEnd = new THREE.Vector3(fixture.position.x, fixture.position.y, 0);
    if (fixtureState && fixtureState.last_target) {
      beamEnd = new THREE.Vector3(
        fixtureState.last_target.x, fixtureState.last_target.y, fixtureState.last_target.z
      );
    }
    // Beam endpoint is computed in room space, like fixture.position, but
    // it's a child of rollGroup (inside pitchGroup, inside group) -- needs
    // the inverse of all three rotations, in reverse order, to land in
    // rollGroup's local space. See scene3d.js's updateFixtures() for the
    // derivation; kept in sync with it by hand since it's only ~15 lines.
    const positions = entry.beam.geometry.attributes.position;
    const dx = beamEnd.x - entry.group.position.x;
    const dy = beamEnd.y - entry.group.position.y;
    const dz = beamEnd.z - entry.group.position.z;
    const cosYaw = Math.cos(entry.group.rotation.z);
    const sinYaw = Math.sin(entry.group.rotation.z);
    const afterYawX = dx * cosYaw + dy * sinYaw;
    const afterYawY = -dx * sinYaw + dy * cosYaw;
    const afterYawZ = dz;
    const cosPitch = Math.cos(entry.pitchGroup.rotation.x);
    const sinPitch = Math.sin(entry.pitchGroup.rotation.x);
    const afterPitchX = afterYawX;
    const afterPitchY = afterYawY * cosPitch + afterYawZ * sinPitch;
    const afterPitchZ = -afterYawY * sinPitch + afterYawZ * cosPitch;
    const cosRoll = Math.cos(entry.rollGroup.rotation.y);
    const sinRoll = Math.sin(entry.rollGroup.rotation.y);
    const localX = afterPitchX * cosRoll - afterPitchZ * sinRoll;
    const localY = afterPitchY;
    const localZ = afterPitchX * sinRoll + afterPitchZ * cosRoll;
    positions.setXYZ(0, 0, 0, 0);
    positions.setXYZ(1, localX, localY, localZ);
    positions.needsUpdate = true;

    const beamBlocked = fixtureState && fixtureState.blocked_by_safety_zone;
    entry.beam.material.color.set(beamBlocked ? 0xff0000 : color);
  }
}

function onTap(evt) {
  const rect = renderer.domElement.getBoundingClientRect();
  const mouse = new THREE.Vector2(
    ((evt.clientX - rect.left) / rect.width) * 2 - 1,
    -((evt.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(mouse, camera);

  // Tapping a fixture selects it, same as the desktop 3D view -- and,
  // same fix as there, only the body model is a hit target (not the beam,
  // which can stretch across the whole room to wherever it's aimed).
  const bodyList = [...fixtureMeshes.values()].map((e) => e.bodyGroup);
  const fixtureHits = raycaster.intersectObjects(bodyList, true);
  if (fixtureHits.length > 0) {
    let node = fixtureHits[0].object;
    while (node && !node.userData.fixtureId) node = node.parent;
    if (node) {
      state.selection.clear();
      state.selection.add(node.userData.fixtureId);
      notifyStateChange();
      return;
    }
  }

  if (state.selection.size === 0) return;
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
