// Regression check for the beam line in scene3d.js updateFixtures(): the drawn end must land on the
// aim target for every mounting/yaw/roll. Run: node frontend/tests/beam_math.check.mjs
// (Mirrors the group hierarchy in createFixtureMesh; keep them in step.)
import * as THREE from "../vendor/three/build/three.module.js";

// same hierarchy as scene3d.js: scene > roomRoot(-90deg about X) > fixturesGroup > group > pitchGroup > mountGroup > rollGroup
let worst = 0;
let cases = 0;
for (const [x, y, z] of [[0, 0, 0], [0.38, 4.119, 0.2], [-4.3, -4.0, 1.9]]) {
  for (const yaw of [0, 90, 180, 270]) {
    for (const pitch of [0, 45, 90, 135, 180]) {
      for (const roll of [0, 30]) {
        const scene = new THREE.Scene();
        const roomRoot = new THREE.Group();
        roomRoot.rotation.x = -Math.PI / 2;
        scene.add(roomRoot);
        const fixturesGroup = new THREE.Group();
        roomRoot.add(fixturesGroup);
        const group = new THREE.Group();
        group.position.set(x, y, z);
        group.rotation.z = -THREE.MathUtils.degToRad(yaw);
        fixturesGroup.add(group);
        const pitchGroup = new THREE.Group();
        pitchGroup.rotation.x = THREE.MathUtils.degToRad(pitch) - Math.PI / 2;
        group.add(pitchGroup);
        const mountGroup = new THREE.Group();
        mountGroup.rotation.y = pitch <= 90 ? Math.PI : 0;
        pitchGroup.add(mountGroup);
        const rollGroup = new THREE.Group();
        rollGroup.rotation.y = THREE.MathUtils.degToRad(roll);
        mountGroup.add(rollGroup);

        for (const target of [[1, 2, 3], [-3, 5, 0], [x, y, 2.5], [x + 2, y, z]]) {
          const beamEnd = new THREE.Vector3(...target);              // ROOM space, as in updateFixtures()
          rollGroup.updateWorldMatrix(true, false);
          const local = rollGroup.worldToLocal(fixturesGroup.localToWorld(beamEnd.clone()));
          const world = rollGroup.localToWorld(local.clone());       // where the drawn line ends
          const expected = new THREE.Vector3(target[0], target[2], -target[1]); // room (x,y,z) -> three (x,z,-y)
          worst = Math.max(worst, world.distanceTo(expected));
          cases += 1;
        }
      }
    }
  }
}
console.log(`cases: ${cases}, worst error between drawn beam end and target: ${worst.toExponential(2)} m`);
process.exit(worst < 1e-9 ? 0 : 1);
