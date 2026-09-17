import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { api } from "./api.js";
import { state, onStateChange } from "./state.js";

// Room space is meters, Z-up (matches the backend's IK core). Three.js is
// Y-up by default, so the whole scene root is rotated -90deg around X to
// bring room-space (x, y, z-up) into Three's (x, z, y-up) without every
// other module needing to know about the swap.

let scene, camera, renderer, controls, roomRoot;
let fixtureMeshes = new Map(); // fixture id -> {group, beam}
let zoneMeshes = new Map();
let floorMesh;

export function initScene3D(container) {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e1013);

  camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.1, 500);
  camera.position.set(8, 8, 10);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 1.5, 0);

  roomRoot = new THREE.Group();
  roomRoot.rotation.x = -Math.PI / 2; // room-space Z-up -> Three Y-up
  scene.add(roomRoot);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5);
  dir.position.set(5, 10, 5);
  scene.add(dir);

  window.addEventListener("resize", () => {
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
  });

  renderer.domElement.addEventListener("click", (evt) => onSceneClick(evt, container));

  onStateChange(rebuildScene);
  rebuildScene();
  animate();
  return { scene, camera, renderer };
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function clearGroup(group) {
  while (group.children.length) group.remove(group.children[0]);
}

function rebuildScene() {
  clearGroup(roomRoot);
  fixtureMeshes.clear();
  zoneMeshes.clear();

  const dims = state.room.dimensions || { width: 10, depth: 10, height: 4 };
  buildRoomShell(dims);
  buildSafetyZones();
  buildFixtures();
}

function buildRoomShell(dims) {
  const floorGeo = new THREE.PlaneGeometry(dims.width, dims.depth);
  const floorMat = new THREE.MeshStandardMaterial({ color: 0x22262c, side: THREE.DoubleSide });
  floorMesh = new THREE.Mesh(floorGeo, floorMat);
  floorMesh.name = "floor";
  roomRoot.add(floorMesh);

  const grid = new THREE.GridHelper(Math.max(dims.width, dims.depth), 20, 0x444444, 0x2a2a2a);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = 0.001;
  roomRoot.add(grid);

  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x3a7bd5, transparent: true, opacity: 0.08, side: THREE.DoubleSide,
  });

  const backWall = new THREE.Mesh(new THREE.PlaneGeometry(dims.width, dims.height), wallMat);
  backWall.position.set(0, dims.depth / 2, dims.height / 2);
  backWall.rotation.x = Math.PI / 2;
  backWall.name = "wall-back";
  roomRoot.add(backWall);

  const sideWall = new THREE.Mesh(new THREE.PlaneGeometry(dims.depth, dims.height), wallMat);
  sideWall.position.set(-dims.width / 2, 0, dims.height / 2);
  sideWall.rotation.y = Math.PI / 2;
  sideWall.rotation.z = Math.PI / 2;
  sideWall.name = "wall-side";
  roomRoot.add(sideWall);

  const ceilingMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, transparent: true, opacity: 0.05, side: THREE.DoubleSide,
  });
  const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(dims.width, dims.depth), ceilingMat);
  ceiling.position.set(0, 0, dims.height);
  ceiling.name = "ceiling";
  roomRoot.add(ceiling);
}

function buildSafetyZones() {
  for (const zone of state.room.safety_zones || []) {
    const size = {
      x: zone.max_corner.x - zone.min_corner.x,
      y: zone.max_corner.y - zone.min_corner.y,
      z: zone.max_corner.z - zone.min_corner.z,
    };
    const center = {
      x: (zone.max_corner.x + zone.min_corner.x) / 2,
      y: (zone.max_corner.y + zone.min_corner.y) / 2,
      z: (zone.max_corner.z + zone.min_corner.z) / 2,
    };
    const geo = new THREE.BoxGeometry(size.x, size.y, size.z);
    const edges = new THREE.EdgesGeometry(geo);
    const line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({
      color: zone.enabled ? 0xd5423a : 0x555555,
    }));
    line.position.set(center.x, center.y, center.z);
    roomRoot.add(line);
    zoneMeshes.set(zone.id, line);
  }
}

function buildFixtures() {
  for (const fixture of state.room.fixtures) {
    const group = new THREE.Group();
    group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);

    const bodyColor = state.isSelected(fixture.id) ? 0xffffff : 0x3a7bd5;
    const body = new THREE.Mesh(
      new THREE.SphereGeometry(0.12, 12, 12),
      new THREE.MeshStandardMaterial({ color: bodyColor })
    );
    group.add(body);

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
    const beamGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      beamEnd.clone().sub(group.position),
    ]);
    const beamBlocked = fixtureState && fixtureState.blocked_by_safety_zone;
    const beam = new THREE.Line(beamGeo, new THREE.LineBasicMaterial({
      color: beamBlocked ? 0xff0000 : color, transparent: true, opacity: 0.8,
    }));
    group.add(beam);

    roomRoot.add(group);
    fixtureMeshes.set(fixture.id, { group, beam });
  }
}

function onSceneClick(evt, container) {
  const rect = container.getBoundingClientRect();
  const mouse = new THREE.Vector2(
    ((evt.clientX - rect.left) / rect.width) * 2 - 1,
    -((evt.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(mouse, camera);
  const targets = roomRoot.children.filter((c) =>
    ["floor", "wall-back", "wall-side", "ceiling"].includes(c.name)
  );
  const hits = raycaster.intersectObjects(targets, false);
  if (hits.length === 0) return;
  const point = hits[0].point.clone();
  roomRoot.worldToLocal(point);

  if (state.selection.size === 0) {
    return;
  }
  for (const targetId of state.selection) {
    api.aim(targetId, point.x, point.y, point.z).catch(console.error);
  }
}
