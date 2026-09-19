import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { api } from "./api.js";
import { state, onStateChange, notifyStateChange } from "./state.js";
import { loadPref, savePref } from "./uiPrefs.js";

// Room space is meters, Z-up (matches the backend's IK core). Three.js is
// Y-up by default, so the whole scene root is rotated -90deg around X to
// bring room-space (x, y, z-up) into Three's (x, z, y-up) without every
// other module needing to know about the swap.
//
// The 3D view is deliberately fixtures/objects only: it never edits the
// room shape or safety zones directly. Those define the space everything
// else is placed relative to, so changing them is a bigger, more
// consequential action (it can rescale/move every fixture and object --
// see Room.apply_shape() on the backend) and stays in the dedicated 2D
// Room Shape editor and the Safety Zones modal instead.

let scene, camera, renderer, controls, roomRoot, staticGroup, zonesGroup, objectsGroup, fixturesGroup;
let transformControls;
let fixtureMeshes = new Map(); // fixture id -> {group, body, beam}
let objectMeshes = new Map(); // room object id -> {group, kind}
let zoneMeshes = new Map();
let aimSurfaces = []; // meshes the click-to-aim raycaster can hit (floor/walls/ceiling)
let gizmoAttachedFixtureId = null;
let selectedObjectId = null; // a room object selected by clicking it in 3D (non-wall only)
let suppressNextClick = false;

// The WS broadcast triggers a state-change ~10x/second even when nothing
// structural changed. Rebuilding the whole scene graph that often would
// tear the fixture/object mesh the drag gizmo is attached to out from
// under an in-progress drag (the same class of bug the sidebar buttons
// had) -- so static room geometry and safety zones only rebuild when
// their own data actually changes, and fixtures/objects are updated in
// place (position/color/selection) rather than destroyed and recreated
// every tick.
let lastStaticKey = "";
let lastZonesKey = "";

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
  zonesGroup = new THREE.Group();
  roomRoot.add(zonesGroup);
  objectsGroup = new THREE.Group();
  roomRoot.add(objectsGroup);
  fixturesGroup = new THREE.Group();
  roomRoot.add(fixturesGroup);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5);
  dir.position.set(5, 10, 5);
  scene.add(dir);

  initGizmo();

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
// the single selected fixture or a clicked room object (person/box/
// surface -- walls need two points, so they're not gizmo-draggable).
// Disables orbiting while dragging so the two controls don't fight over
// the mouse, and persists the new position only once the drag ends.
function initGizmo() {
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
      // drag just ended -- persist the new position, and swallow the
      // click event the browser fires right after mouseup so releasing
      // the gizmo doesn't also re-aim/re-select at wherever the pointer
      // happened to land.
      if (gizmoAttachedFixtureId) persistGizmoFixturePosition();
      else if (selectedObjectId) persistGizmoObjectPosition();
      suppressNextClick = true;
    }
  });
}

function persistGizmoFixturePosition() {
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

function persistGizmoObjectPosition() {
  const entry = objectMeshes.get(selectedObjectId);
  const obj = (state.room.objects || []).find((o) => o.id === selectedObjectId);
  if (!entry || !obj) return;
  const pos = entry.group.position;
  // box/surface meshes are rendered centered on their vertical extent
  // (base + height/2) while the API's position is the *base*; person
  // markers are already stored/rendered at their ground point.
  const baseZ = obj.kind === "person" ? pos.z : pos.z - obj.height / 2;
  api.updateRoomObject(obj.id, {
    name: obj.name,
    kind: obj.kind,
    position: { x: round3(pos.x), y: round3(pos.y), z: round3(baseZ) },
    end_position: obj.end_position || null,
    thickness: obj.thickness,
    width: obj.width,
    depth: obj.depth,
    height: obj.height,
    color: obj.color,
  }).catch(console.error);
}

function round3(n) {
  return Math.round(n * 1000) / 1000;
}

// Only one fixture (not a group, not multi-select) or one clicked object
// gets the drag gizmo -- there's no single position to drag otherwise.
// A sidebar fixture selection always wins over a lingering object pick.
// `transformControls.object` (three.js's own record of what's attached)
// is the source of truth for "what's currently attached", so this never
// depends on tracking a parallel "previous" value by hand.
function updateGizmoAttachment() {
  const selection = [...state.selection];
  const singleFixtureId =
    selection.length === 1 && state.fixtureById(selection[0]) ? selection[0] : null;

  if (selection.length > 0) selectedObjectId = null; // any sidebar selection wins

  gizmoAttachedFixtureId = singleFixtureId;

  const wantedMesh = singleFixtureId
    ? fixtureMeshes.get(singleFixtureId)?.group
    : selectedObjectId
    ? objectMeshes.get(selectedObjectId)?.group
    : null;

  if ((transformControls.object || null) === (wantedMesh || null)) return;

  if (wantedMesh) {
    transformControls.attach(wantedMesh);
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
  const staticKey = JSON.stringify({ floor: state.room.floor_points, dims: state.room.dimensions });
  if (staticKey !== lastStaticKey) {
    lastStaticKey = staticKey;
    rebuildRoomShell();
  }

  const zonesKey = JSON.stringify(state.room.safety_zones);
  if (zonesKey !== lastZonesKey) {
    lastZonesKey = zonesKey;
    rebuildSafetyZones();
  }

  updateFixtures();
  updateObjects();
  updateGizmoAttachment();
}

function rebuildRoomShell() {
  clearGroup(staticGroup);
  aimSurfaces = [];

  const floorPoints = effectiveFloorPoints();
  const height = state.room.dimensions?.height ?? 4;
  buildRoomShell(floorPoints, height);
}

// Mirrors the backend's Room.effective_floor_points(): use the drawn
// polygon when there is one, otherwise fall back to a simple rectangle
// sized from dimensions.width/depth so a room with no shape drawn yet
// still renders something sensible. The polygon itself is only ever
// edited in the 2D Room Shape modal.
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

function rebuildSafetyZones() {
  clearGroup(zonesGroup);
  zoneMeshes.clear();

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
    zonesGroup.add(line);
    zoneMeshes.set(zone.id, line);
  }
}

// Visual/spatial reference objects (not safety-relevant): wall segments,
// person-scale markers, box obstacles, and raised surfaces/platforms, so
// operators can sanity-check aim against real obstacles and get a sense
// of scale in the 3D view. Non-wall objects are updated in place (like
// fixtures) so they can be gizmo-dragged; walls (defined by two points)
// are simpler to just rebuild since they aren't drag targets here.
function updateObjects() {
  const objects = state.room.objects || [];
  const currentIds = new Set(objects.filter((o) => o.kind !== "wall").map((o) => o.id));

  for (const [id, entry] of objectMeshes) {
    if (!currentIds.has(id)) {
      objectsGroup.remove(entry.group);
      objectMeshes.delete(id);
      if (selectedObjectId === id) selectedObjectId = null;
    }
  }

  // walls are rebuilt wholesale on any object-list change (cheap, not draggable)
  const wallsKey = JSON.stringify(objects.filter((o) => o.kind === "wall"));
  if (wallsKey !== objectsGroup.userData.wallsKey) {
    objectsGroup.userData.wallsKey = wallsKey;
    for (const child of [...objectsGroup.children]) {
      if (child.userData.isWall) objectsGroup.remove(child);
    }
    for (const obj of objects) {
      if (obj.kind !== "wall" || !obj.end_position) continue;
      const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(obj.color || "#888888"), side: THREE.DoubleSide });
      const wall = buildQuadWall(obj.position, obj.end_position, obj.position.z, obj.position.z + obj.height, mat);
      wall.userData.isWall = true;
      objectsGroup.add(wall);
    }
  }

  for (const obj of objects) {
    if (obj.kind === "wall") continue;
    let entry = objectMeshes.get(obj.id);
    if (!entry) {
      entry = createObjectMesh(obj);
      objectMeshes.set(obj.id, entry);
      objectsGroup.add(entry.group);
    }
    const isDraggingThis = selectedObjectId === obj.id && transformControls.dragging;
    if (!isDraggingThis) {
      const baseZ = obj.kind === "person" ? obj.position.z : obj.position.z + obj.height / 2;
      entry.group.position.set(obj.position.x, obj.position.y, baseZ);
    }
  }
}

function createObjectMesh(obj) {
  const color = new THREE.Color(obj.color || "#888888");
  const group = new THREE.Group();
  group.userData.objectId = obj.id;

  if (obj.kind === "person") {
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
    group.position.set(obj.position.x, obj.position.y, obj.position.z);
    return { group, kind: obj.kind };
  }

  // "box" / "surface" -- centered box mesh; group sits at base + height/2
  // so it renders in the same place, but position.z reflects the mesh
  // center (see persistGizmoObjectPosition()) when translating a drag
  // back to the API's base-position convention.
  const box = new THREE.Mesh(
    new THREE.BoxGeometry(obj.width, obj.depth, obj.height),
    new THREE.MeshStandardMaterial({ color, transparent: obj.kind === "surface", opacity: 0.85 })
  );
  group.add(box);
  group.position.set(obj.position.x, obj.position.y, obj.position.z + obj.height / 2);
  return { group, kind: obj.kind };
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
    // Rebuild the body if the fixture was re-patched onto a different
    // profile (fixture_type may have changed) -- rare, but cheap to check.
    if (entry && entry.profileId !== fixture.profile_id) {
      fixturesGroup.remove(entry.group);
      fixtureMeshes.delete(fixture.id);
      entry = null;
    }
    if (!entry) {
      entry = createFixtureMesh(fixture);
      fixtureMeshes.set(fixture.id, entry);
      fixturesGroup.add(entry.group);
    }

    // Don't stomp on a position that's actively being dragged by the gizmo.
    if (gizmoAttachedFixtureId !== fixture.id || !transformControls.dragging) {
      entry.group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);
    }

    // The body model is authored facing +Y at zero rotation; rotate it (and
    // the front-direction arrow) to the fixture's mounted "front" so you can
    // see at a glance which way it's calibrated to call pan/tilt zero.
    const yawRad = THREE.MathUtils.degToRad(fixture.orientation?.yaw_deg || 0);
    entry.bodyGroup.rotation.z = -yawRad;
    const frontDir = entry.arrow.userData.frontDir.set(Math.sin(yawRad), Math.cos(yawRad), 0);
    entry.arrow.setDirection(frontDir);

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
    const positions = entry.beam.geometry.attributes.position;
    const localEnd = beamEnd.clone().sub(entry.group.position);
    positions.setXYZ(0, 0, 0, 0);
    positions.setXYZ(1, localEnd.x, localEnd.y, localEnd.z);
    positions.needsUpdate = true;

    const beamBlocked = fixtureState && fixtureState.blocked_by_safety_zone;
    entry.beam.material.color.set(beamBlocked ? 0xff0000 : color);
  }
}

// Body shapes are authored so their "front" faces local +Y at zero
// rotation -- updateFixtures() then rotates the whole bodyGroup by the
// fixture's mounted orientation.yaw_deg so the model actually shows which
// way it's calibrated to call pan/tilt zero, not just a featureless ball.
// A bright green arrow is added on top regardless of body shape, since a
// small shape asymmetry can be hard to read at a glance across a room.
function buildFixtureBody(fixtureType, material) {
  const bodyGroup = new THREE.Group();
  const lensMat = new THREE.MeshStandardMaterial({ color: 0xfff2b0, emissive: 0x554400 });

  if (fixtureType === "moving_head") {
    const base = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.22, 0.12), material);
    base.position.z = -0.06;
    bodyGroup.add(base);
    const armGeo = new THREE.BoxGeometry(0.04, 0.16, 0.04);
    const armL = new THREE.Mesh(armGeo, material); armL.position.set(-0.09, 0.03, 0.05); bodyGroup.add(armL);
    const armR = new THREE.Mesh(armGeo, material); armR.position.set(0.09, 0.03, 0.05); bodyGroup.add(armR);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.1, 16, 16), material);
    head.position.set(0, 0.04, 0.09);
    bodyGroup.add(head);
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.045, 16), lensMat);
    lens.position.set(0, 0.04 + 0.099, 0.09);
    lens.rotation.x = -Math.PI / 2; // face +Y (front)
    bodyGroup.add(lens);
    return bodyGroup;
  }

  if (fixtureType === "smoke" || fixtureType === "fog") {
    const box = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.2, 0.18), material);
    bodyGroup.add(box);
    const nozzle = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, 0.08, 12), material);
    nozzle.position.set(0, 0.14, 0);
    bodyGroup.add(nozzle);
    return bodyGroup;
  }

  if (fixtureType === "par") {
    const can = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 0.26, 16), material);
    bodyGroup.add(can); // cylinder's own axis is +Y by default -- already front/back
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.1, 16), lensMat);
    lens.position.set(0, 0.13, 0);
    lens.rotation.x = -Math.PI / 2;
    bodyGroup.add(lens);
    return bodyGroup;
  }

  // "other" / generic / laser -- a plain box with a front marker disc so
  // even the fallback shape is never ambiguous about which way it faces.
  const box = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.22, 0.22), material);
  bodyGroup.add(box);
  const marker = new THREE.Mesh(new THREE.CircleGeometry(0.07, 16), lensMat);
  marker.position.set(0, 0.111, 0);
  marker.rotation.x = -Math.PI / 2;
  bodyGroup.add(marker);
  return bodyGroup;
}

function createFixtureMesh(fixture) {
  const profile = state.profileById(fixture.profile_id);
  const fixtureType = profile?.fixture_type || "generic";

  const group = new THREE.Group();
  group.position.set(fixture.position.x, fixture.position.y, fixture.position.z);
  group.userData.fixtureId = fixture.id;

  const baseColor = 0x3a7bd5;
  const bodyMaterial = new THREE.MeshStandardMaterial({ color: baseColor });
  bodyMaterial.userData.baseColor = baseColor;

  const bodyGroup = buildFixtureBody(fixtureType, bodyMaterial);
  group.add(bodyGroup);

  // Unmistakable front-direction indicator, independent of body shape,
  // so "where is the front" always has one clear answer regardless of
  // how subtle a given model's asymmetry is.
  const arrow = new THREE.ArrowHelper(
    new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 0), 0.3, 0x33ff66, 0.09, 0.05
  );
  arrow.userData.frontDir = new THREE.Vector3(0, 1, 0);
  group.add(arrow);

  const beamGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0, 0, -1),
  ]);
  const beam = new THREE.Line(beamGeo, new THREE.LineBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.8,
  }));
  group.add(beam);

  return { group, bodyGroup, bodyMaterial, arrow, beam, profileId: fixture.profile_id };
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

  // Clicking a fixture selects it directly in the 3D view (same as
  // clicking its sidebar button) instead of aiming, and takes priority
  // over everything else so you can always grab exactly what you clicked.
  const fixtureMeshList = [...fixtureMeshes.values()].map((e) => e.group);
  const fixtureHits = raycaster.intersectObjects(fixtureMeshList, true);
  if (fixtureHits.length > 0) {
    let node = fixtureHits[0].object;
    while (node && !node.userData.fixtureId) node = node.parent;
    if (node) {
      selectedObjectId = null;
      state.selection.clear();
      state.selection.add(node.userData.fixtureId);
      notifyStateChange();
      return;
    }
  }

  // Clicking a draggable object selects it (and gives it the gizmo)
  // instead of aiming, and takes priority over the floor/wall aim-click.
  const objectMeshList = [...objectMeshes.values()].map((e) => e.group);
  const objectHits = raycaster.intersectObjects(objectMeshList, true);
  if (objectHits.length > 0) {
    let node = objectHits[0].object;
    while (node && !node.userData.objectId) node = node.parent;
    if (node) {
      selectedObjectId = node.userData.objectId;
      state.selection.clear();
      notifyStateChange();
      return;
    }
  }

  const hits = raycaster.intersectObjects(aimSurfaces, false);
  if (hits.length === 0) {
    selectedObjectId = null;
    return;
  }
  const point = hits[0].point.clone();
  roomRoot.worldToLocal(point);

  if (state.selection.size === 0) {
    return;
  }
  for (const targetId of state.selection) {
    api.aim(targetId, point.x, point.y, point.z).catch(console.error);
  }
}
