import * as THREE from "three";

// The calibration target point as a small magenta ball with a cross through it, drawn in both the
// desktop and the phone 3D views (room-space, Z-up: the marker is added to the room root group).
// Its position follows state.calibrationPoint.

export function makeCalibrationMarker() {
  const group = new THREE.Group();
  const mat = new THREE.MeshBasicMaterial({ color: 0xff40ff, depthTest: false, transparent: true, opacity: 0.9 });
  group.add(new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), mat));
  for (const [sx, sy, sz] of [[0.7, 0.02, 0.02], [0.02, 0.7, 0.02], [0.02, 0.02, 0.7]]) {
    group.add(new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat));
  }
  group.renderOrder = 10;
  group.visible = false;
  return group;
}

export function updateCalibrationMarker(marker, point, show) {
  marker.visible = !!point && show;
  if (point) marker.position.set(point.x, point.y, point.z);
}
