"""IK core: 3D target point -> pan/tilt DMX values.

This is the pure-math foundation the rest of the system builds on --
live surface-dragging, animations, and safety-zone checks all just call
`compute_pan_tilt` / `beam_direction` with different target points. No
hardware, no UI, no state: everything here is a plain function you can
unit test against known angles and positions.

Coordinate system: right-handed, Z is up, meters. A fixture's
"orientation" describes how it's physically mounted: yaw_deg rotates its
zero-pan direction around Z, pitch_deg tilts its zero-tilt direction off
of straight down (0) toward horizontal (90) -- e.g. a fixture hung
straight down from a truss vs. one mounted on a wall aiming outward.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Optional

from .model import FixtureInstance, Orientation, SafetyZone, Vec3


@dataclasses.dataclass
class PanTiltResult:
    pan_angle_deg: float  # signed angle from fixture's mechanical center
    tilt_angle_deg: float
    pan_dmx: int  # coarse channel, 0-255
    pan_fine_dmx: int  # fine channel, 0-255 (0 if fixture has no fine channel)
    tilt_dmx: int
    tilt_fine_dmx: int
    in_range: bool  # False if the target required clamping outside the fixture's range


def _angle_to_dmx(angle_deg: float, range_deg: float) -> tuple[int, int, bool]:
    """Map a signed angle (0 = mechanical center) into 16-bit DMX space
    (coarse, fine) across the fixture's full mechanical range."""
    half = range_deg / 2.0
    clamped = max(-half, min(half, angle_deg))
    in_range = clamped == angle_deg
    # normalize to 0..1 across the full range, then to 16-bit resolution
    fraction = (clamped + half) / range_deg if range_deg > 0 else 0.5
    fraction = max(0.0, min(1.0, fraction))
    value_16bit = round(fraction * 65535)
    coarse = (value_16bit >> 8) & 0xFF
    fine = value_16bit & 0xFF
    return coarse, fine, in_range


def beam_direction(
    fixture_position: Vec3,
    fixture_orientation: Orientation,
    target_point: Vec3,
) -> tuple[float, float, Vec3]:
    """Return (pan_angle_deg, tilt_angle_deg, unit_direction_vector).

    pan_angle_deg is signed, relative to the fixture's mounted zero
    direction (yaw_deg). tilt_angle_deg is signed, relative to the
    fixture's mounted zero tilt (pitch_deg): 0 = fixture's home tilt,
    positive = toward horizontal/up from home.
    """
    dx = target_point.x - fixture_position.x
    dy = target_point.y - fixture_position.y
    dz = target_point.z - fixture_position.z

    horizontal_dist = math.hypot(dx, dy)
    if horizontal_dist < 1e-9 and abs(dz) < 1e-9:
        # target coincides with fixture position -- direction undefined,
        # default to straight down (typical moving-head home position)
        return 0.0, 0.0, Vec3(0.0, 0.0, -1.0)

    world_yaw_deg = math.degrees(math.atan2(dx, dy))  # 0 = +Y, +90 = +X
    world_pitch_deg = math.degrees(math.atan2(horizontal_dist, -dz))
    # world_pitch_deg: 0 = straight down, 90 = horizontal, 180 = straight up

    pan_angle_deg = _normalize_180(world_yaw_deg - fixture_orientation.yaw_deg)
    tilt_angle_deg = _normalize_180(world_pitch_deg - fixture_orientation.pitch_deg)

    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    direction = Vec3(dx / length, dy / length, dz / length)
    return pan_angle_deg, tilt_angle_deg, direction


def _normalize_180(angle_deg: float) -> float:
    a = angle_deg % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def compute_pan_tilt(
    fixture_position: Vec3,
    fixture_orientation: Orientation,
    target_point: Vec3,
    pan_range_deg: float,
    tilt_range_deg: float,
    inverted_pan: bool = False,
    inverted_tilt: bool = False,
    pan_offset_deg: float = 0.0,
    tilt_offset_deg: float = 0.0,
) -> PanTiltResult:
    """Compute DMX pan/tilt values (with fine-channel resolution) that
    aim `fixture` at `target_point` in room space.

    Uses the fixture's full measured mechanical range (e.g. 540 degrees)
    for sub-degree resolution via the fine channel, rather than the
    coarse-only 8-bit mapping FreeStyler's FX generator uses.

    pan_offset_deg/tilt_offset_deg are a fine calibration trim -- applied
    after inversion, on top of the mounting orientation -- for a fixture
    whose own mechanical zero is a little off from where the mounting
    orientation says it should be, without having to re-derive yaw/pitch.
    """
    pan_angle, tilt_angle, _direction = beam_direction(
        fixture_position, fixture_orientation, target_point
    )
    if inverted_pan:
        pan_angle = -pan_angle
    if inverted_tilt:
        tilt_angle = -tilt_angle
    pan_angle += pan_offset_deg
    tilt_angle += tilt_offset_deg

    pan_coarse, pan_fine, pan_in_range = _angle_to_dmx(pan_angle, pan_range_deg)
    tilt_coarse, tilt_fine, tilt_in_range = _angle_to_dmx(tilt_angle, tilt_range_deg)

    return PanTiltResult(
        pan_angle_deg=pan_angle,
        tilt_angle_deg=tilt_angle,
        pan_dmx=pan_coarse,
        pan_fine_dmx=pan_fine,
        tilt_dmx=tilt_coarse,
        tilt_fine_dmx=tilt_fine,
        in_range=pan_in_range and tilt_in_range,
    )


def ray_intersects_box(origin: Vec3, direction: Vec3, box_min: Vec3, box_max: Vec3,
                        max_distance: float = 1000.0) -> bool:
    """Slab-method ray/AABB intersection test, used to check whether a
    fixture's computed beam vector passes through a safety-zone volume
    before the value is ever sent to hardware."""
    t_min, t_max = 0.0, max_distance
    o = (origin.x, origin.y, origin.z)
    d = (direction.x, direction.y, direction.z)
    lo = (box_min.x, box_min.y, box_min.z)
    hi = (box_max.x, box_max.y, box_max.z)

    for axis in range(3):
        if abs(d[axis]) < 1e-12:
            if o[axis] < lo[axis] or o[axis] > hi[axis]:
                return False
            continue
        inv_d = 1.0 / d[axis]
        t1 = (lo[axis] - o[axis]) * inv_d
        t2 = (hi[axis] - o[axis]) * inv_d
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return False
    return True


def target_violates_safety_zones(
    fixture_position: Vec3,
    fixture_orientation: Orientation,
    target_point: Vec3,
    zones: list[SafetyZone],
    max_distance: float = 1000.0,
) -> Optional[SafetyZone]:
    """Return the first enabled safety zone the beam toward target_point
    passes through, or None if the beam is clear."""
    _pan, _tilt, direction = beam_direction(fixture_position, fixture_orientation, target_point)
    for zone in zones:
        if not zone.enabled:
            continue
        if ray_intersects_box(fixture_position, direction, zone.min_corner, zone.max_corner,
                               max_distance=max_distance):
            return zone
    return None
