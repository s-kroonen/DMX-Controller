import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { api } from "./api.js";
import { state, onStateChange } from "./state.js";
import { loadPref, savePref } from "./uiPrefs.js";

// Room space is meters, Z-up (matches the backend's IK core). Three.js is
// Y-up by default, so the whole scene root is rotated -90deg around X to
// bring room-space (x, y, z-up) into Three's (x, z, y-up) without every
// other module needing to know about the swap.

let scene, camera, renderer, controls, roomRoot, staticGroup, fixturesGroup;
let transformControls;
let fixtureMeshes = new Map(); // fixture id -> {group, body, beam}
let zoneMeshes = new Map();
let aimSurfaces = []; // meshes the click-to-aim raycaster can hit (floor/walls/ceiling)
let gizmoAttachedFixtureId = null;
let suppressNextClick = false;

// The WS broadcast triggers a state-change ~10x/second even when nothing
// structural changed. Rebuilding the whole scene graph that often would
// tear the fixture mesh the drag gizmo is attached to out from under an
// in-progress drag (the same class of bug the sidebar buttons had) --
// so static room geometry only rebuilds when its own data actually
// changes, and fixtures are updated in place (position/color/selection)
// rather than destroyed and recreated every tick.
let lastStaticKey = "";

const CAMERA_PREF_KEY = "camera3d";

export function initScene3D(container) {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e1013);

  camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.1, 500);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);

  const savedCamera = loadPref(CAMERA_PREF_KEY, null);
  if (savedCamera) {
    camera.position.set(savedCamera.pos.x, savedCamera.pos.y, savedCamera.pos.z);
    controls.target.set(savedCamera.target.x, savedCamera.target.y, savedCamera.target.z);
  } else {
    camera.position.set(8, 8, 10);
    controls.target.set(0, 1.5, 0);
  }
  controls.update();
  controls.addEventListener("end", saveCameraPref);

  roomRoot = new THREE.Group();
  roomRoot.rotation.x = -Math.PI / 2; // room-space Z-up -> Three Y-up
  scene.add(roomRoot);

  staticGroup = new THREE.Group();
  roomRoot.add(staticGroup);
  fixturesGroup = new THREE.Group();
  roomRoot.add(fixturesGroup);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5);
  dir.position.set(5, 10, 5);
  scene.add(dir);

  initGizmo(container);

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

function saveCameraPref() {
  savePref(CAMERA_PREF_KEY, {
    pos: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
    target: { x: controls.target.x, y: controls.target.y, z: controls.target.z },
  });
}

// A drag-to-reposition gizmo (XYZ arrows, like a 3D-slicer/CAD tool) for
// the single selected fixture. Attaches/detaches as the selection
// changes (multi-select and groups don't get a gizmo -- there's no
// single "the" position to drag), disables orbiting while dragging so
// the two controls don't fight over the mouse, and persists the new
// position via the room API only once the drag ends (not on every
// intermediate frame, to avoid spamming the backend mid-drag).
function initGizmo(container) {
  transformControls = new TransformControls(camera, renderer.domElement);
  transformControls.setMode("translate");
  transformControls.setSpace("world");
  transformControls.setSize(0.8);
  transformControls.visible = false;
  transformControls.enabled = false;
  scene.add(transformControls);

  transformControls.addEventListener("dragging-changed", (event) => {
    controls.enabled = !event.value;
    if (!event.value) {
      // drag just ended -- persist the fixture's new position, and
      // swallow the click event the browser fires right after mouseup
      // so releasing the gizmo doesn't also re-aim the fixture at
      // wherever the pointer happened to land.
      persistGizmoFixturePosition();
      suppressNextClick = true;
    }
  });
}

function persistGizmoFixturePosition() {
  if (!gizmoAttachedFixtureId) return;
  const entry = fixtureMeshes.get(gizmoAttachedFixtureId);
  const fixture = state.fixtureById(gizmoAttachedFixtureId);
  if (!entry || !fixture) return;
  const pos = entry.group.position;
  api.updateFixture(fixture.id, {
    name: fixture.name,
    profile_id: fixture.profile_id,
    universe: fixture.universe,
    start_address: fixture.start_address,
    position: { x: round3(pos.x), y: round3(pos.y), z: round3(pos.z) },
    orientation: fixture.orientation,
    group_ids: fixture.group_ids,
    inverted_pan: fixture.inverted_pan,
    inverted_tilt: fixture.inverted_tilt,
  }).catch(console.error);
}

function round3(n) {
  return Math.round(n * 1000) / 1000;
}

// Only one fixture (not a group, not multi-select) gets the drag gizmo --
// there's no single position to drag otherwise.
function updateGizmoAttachment() {
  const selection = [...state.selection];
  const singleFixtureId =
    selection.length === 1 && state.fixtureById(selection[0]) ? selection[0] : null;

  if (singleFixtureId === gizmoAttachedFixtureId) return;

  gizmoAttachedFixtureId = singleFixtureId;
  if (singleFixtureId && fixtureMeshes.has(singleFixtureId)) {
    transformControls.attach(fixtureMeshes.get(singleFixtureId).group);
    transformControls.visible = true;
    transformControls.enabled = true;
  } else {
    transformControls.detach();
    transformControls.visible = false;
    transformControls.enabled = false;
  }
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
  const staticKey = JSON.stringify({
    floor: state.room.floor_points,
    dims: state.room.dimensions,
    zones: state.room.safety_zones,
    objects: state.room.objects,
  });
  if (staticKey !== lastStaticKey) {
    lastStaticKey = staticKey;
    rebuildStaticGeometry();
  }
  updateFixtures();
  updateGizmoAttachment();
}

function rebuildStaticGeometry() {
  clearGroup(staticGroup);
  zoneMeshes.clear();
  aimSurfaces = [];

  const floorPoints = effectiveFloorPoints();
  const height = state.room.dimensions?.height ?? 4;
  buildRoomShell(floorPoints, height);
  buildRoomObjects();
  buildSafetyZones();
}

// Mirrors the backend's Room.effective_floor_points(): use the drawn
// polygon when there is one, otherwise fall back to a simple rectangle
// sized from dimensions.width/depth so a room with no shape drawn yet
// still renders something sensible.
function effectiveFloorPoints() {
  const drawn = state.room.floor_points || [];
  if (drawn.length >= 3) return drawn;
  const dims = state.room.dimensions || { width: 10, depth: 10 };
  const hw = dims.width / 2, hd = dims.depth / 2;
  return [
    { x: -hw, y: -hd }, { x: hw, y: -hd }, { x: hw, y: hd }, { x: -hw, y: hd },
  ];
}

function buildRoomShell(floorPoints, height) {
  const shape = new THREE.Shape(floorPoints.map((p) => new THREE.Vector2(p.x, p.y)));

  const floorMat = new THREE.MeshStandardMaterial({ color: 0x22262c, side: THREE.DoubleSide });
  const floorMesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), floorMat);
  floorMesh.name = "floor";
  staticGroup.add(floorMesh);
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
  staticGroup.add(grid);

  const ceilingMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, transparent: true, opacity: 0.05, side: THREE.DoubleSide,
  });
  const ceiling = new THREE.Mesh(new THREE.ShapeGeometry(shape), ceilingMat);
  ceiling.position.z = height;
  ceiling.name = "ceiling";
  staticGroup.add(ceiling);
  aimSurfaces.push(ceiling);

  // One vertical quad per polygon edge -- works for any number of sides,
  // convex or not, not just a 4-wall rectangle.
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x3a7bd5, transparent: true, opacity: 0.08, side: THREE.DoubleSide,
  });
  for (let i = 0; i < floorPoints.length; i++) {
    const p1 = floorPoints[i];
    const p2 = floorPoints[(i + 1) % floorPoints.length];
    const wall = buildQuadWall(p1, p2, 0, height, wallMat);
    wall.name = `wall-${i}`;
    staticGroup.add(wall);
    aimSurfaces.push(wall);
  }
}

function buildQuadWall(p1, p2, zBottom, zTop, material) {
  const vertices = new Float32Array([
    p1.x, p1.y, zBottom,
    p2.x, p2.y, zBottom,
    p2.x, p2.y, zTop,
    p1.x, p1.y, zBottom,
    p2.x, p2.y, zTop,
    p1.x, p1.y, zTop,
  ]);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
  geo.computeVertexNormals();
  return new THREE.Mesh(geo, material);
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
    staticGroup.add(line);
    zoneMeshes.set(zone.id, line);
  }
}

// Visual/spatial reference objects (not safety-relevant): wall segments,
// person-scale markers, box obstacles, and raised surfaces/platforms, so
// operators can sanity-check aim against real obstacles and get a sense
// of scale in the 3D view.
function buildRoomObjects() {
  for (const obj of state.room.objects || []) {
    const color = new THREE.Color(obj.color || "#888888");
    if (obj.kind === "wall" && obj.end_position) {
      const mat = new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide });
      const wall = buildQuadWall(obj.position, obj.end_position, obj.position.z, obj.position.z + obj.height, mat);
      staticGroup.add(wall);
      continue;
    }
    if (obj.kind === "person") {
      const group = new THREE.Group();
      group.position.set(obj.position.x, obj.position.y, obj.position.z);
      const bodyHeight = obj.height * 0.78;
      const body = new THREE.Mesh(
        new THREE.CapsuleGeometry(0.18, bodyHeight - 0.36, 4, 8),
        new THREE.MeshStandardMaterial({ color })
      );
      body.position.z = bodyHeight / 2 + 0.18;
      body.rotation.x = Math.PI / 2;
      group.add(body);
      const head = new THREE.Mesh(
        new THREE.SphereGeometry(0.12, 12, 12),
        new THREE.MeshStandardMaterial({ color })
      );
      head.position.z = bodyHeight + 0.18 + 0.12;
      group.add(head);
      staticGroup.add(group);
      continue;
    }
    // "box" and "surface" -- a box/platform whose base sits at obj.position
    const box = new THREE.Mesh(
      new THREE.BoxGeometry(obj.width, obj.depth, obj.height),
      new THREE.MeshStandardMaterial({ color, transparent: obj.kind === "surface", opacity: 0.85 })
    );
    box.position.set(obj.position.x, obj.position.y, obj.position.z + obj.height / 2);
    staticGroup.add(box);
  }
}

// Fixtures are updated in place -- position/color/selection-highlight/
// beam geometry all get set on the existing mesh -- rather than being
// destroyed and recreated every tick, both because that's wasteful at
// 10Hz and because it would rip the gizmo's attached object out from
// under an in-progress drag.
function updateFixtures() {
  const currentIds = new Set(state.room.fixtures.map((f) => f.id));

  for (const [id, entry] of fixtureMeshes) {
    if (!currentIds.has(id)) {
      fixturesGroup.remove(entry.group);
      fixtureMeshes.delete(id);
      if (gizmoAttachedFixtureId === id) {
        transformControls.detach();
        transformControls.visible = false;
        transformControls.enabled = false;
        gizmoAttachedFixtureId = null;
      }
    }
  }

  for (const fixture of state.room.fixtures) {
    let entry = fixtureMeshes.get(fixture.id);
    if (!entry) {
      entry = createFixtureMesh(fixture);
      fixtureMeshes.set(fixture.id, entry);
      fixturesGroup.add(entry.group);
    }

    // Don't stomp on a position that's actively being dragged by the gizmo.
    if (gizmoAttachedFixtureId !== fixture.id || !transformControls.dragging) {
      entry.group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);
    }

    entry.body.material.color.set(state.isSelected(fixture.id) ? 0xffffff : 0x3a7bd5);

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
    const positions = entry.beam.geometry.attributes.position;
    const localEnd = beamEnd.clone().sub(entry.group.position);
    positions.setXYZ(0, 0, 0, 0);
    positions.setXYZ(1, localEnd.x, localEnd.y, localEnd.z);
    positions.needsUpdate = true;

    const beamBlocked = fixtureState && fixtureState.blocked_by_safety_zone;
    entry.beam.material.color.set(beamBlocked ? 0xff0000 : color);
  }
}

function createFixtureMesh(fixture) {
  const group = new THREE.Group();
  group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);

  const body = new THREE.Mesh(
    new THREE.SphereGeometry(0.12, 12, 12),
    new THREE.MeshStandardMaterial({ color: 0x3a7bd5 })
  );
  group.add(body);

  const beamGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0, 0, -1),
  ]);
  const beam = new THREE.Line(beamGeo, new THREE.LineBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.8,
  }));
  group.add(beam);

  return { group, body, beam };
}

function onSceneClick(evt, container) {
  if (suppressNextClick) {
    suppressNextClick = false;
    return;
  }
  if (transformControls.dragging) return; // don't aim while dragging the gizmo
  const rect = container.getBoundingClientRect();
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

  if (state.selection.size === 0) {
    return;
  }
  for (const targetId of state.selection) {
    api.aim(targetId, point.x, point.y, point.z).catch(console.error);
  }
}
