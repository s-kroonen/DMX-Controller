// How a fixture is mounted decides where its "home" points -- the direction the
// beam takes at tilt centre, along the pan axis away from the base. That is what
// pitch_deg stores (0 = home straight down, 90 = horizontal, 180 = straight up),
// so a picker for the three real-world cases beats a bare number: a unit
// standing on the floor or on a stage box has home = UP (180), one hanging from
// a truss has home = DOWN (0), one screwed to a wall is sideways (90).

export const MOUNTS = [
  { value: "standing", label: "Standing on floor / stage (beam home: up)", pitch: 180 },
  { value: "hanging", label: "Hanging from truss / ceiling (beam home: down)", pitch: 0 },
  { value: "wall", label: "On a wall / sideways (beam home: horizontal)", pitch: 90 },
  { value: "custom", label: "Custom angle", pitch: null },
];

export const DEFAULT_PITCH = 180; // standing: how a moving head is normally set up

function mountForPitch(pitch) {
  const match = MOUNTS.find((m) => m.pitch !== null && Math.abs(m.pitch - Number(pitch)) < 0.5);
  return match ? match.value : "custom";
}

// Keep a Mounting <select> and its Pitch <input> in step: picking a mounting
// sets the pitch, and typing a pitch (or a gizmo drag) updates the picker.
export function wireMountSelect(selectId, pitchId) {
  const select = document.getElementById(selectId);
  const pitch = document.getElementById(pitchId);
  select.innerHTML = MOUNTS.map((m) => `<option value="${m.value}">${m.label}</option>`).join("");
  const sync = () => { select.value = mountForPitch(pitch.value); };
  select.addEventListener("change", () => {
    const mount = MOUNTS.find((m) => m.value === select.value);
    if (mount && mount.pitch !== null) {
      pitch.value = mount.pitch;
      pitch.dispatchEvent(new Event("input", { bubbles: true }));
      pitch.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  pitch.addEventListener("input", sync);
  pitch.addEventListener("change", sync);
  sync();
  return sync;
}
