"""IK core: 3D target point -> pan/tilt DMX values.

This is the pure-math foundation the rest of the system builds on --
live surface-dragging, animations, and safety-zone checks all just call
`compute_pan_tilt` / `beam_direction` with different target points. No
hardware, no UI, no state: everything here is a plain function you can
unit test against known angles and positions.

Coordinate system: right-handed, Z is up, meters. Azimuth is measured like a
compass: 0 = +Y, +90 = +X.

Physical model of a moving head (verified on a real standing unit):

  * The BASE holds the PAN axis. `home` (B) is the direction the beam points
    at tilt centre: along the pan axis, away from the base. A fixture standing
    on the floor has home = up; one hanging from a truss has home = down; one
    on a wall has home = horizontal.
  * `orientation.pitch_deg` places home: 0 = straight down (hung), 90 =
    horizontal, 180 = straight up (standing). `yaw_deg` is the compass
    direction the front (display side) faces, i.e. the direction the beam
    leans toward at pan centre.
  * Tilt is the angle AWAY from home, and tilt+ leans the beam toward the
    current pan direction. So tilt never depends on whether the unit stands or
    hangs -- only home does.
  * Pan+ is clockwise seen from above for a vertical axis (the same for
    standing and hanging); `inverted_pan` flips it, `inverted_tilt` makes
    tilt+ lean AWAY from the pan direction instead.

A target can be reached two ways: (pan, +tilt) or (pan + 180, -tilt) (the head
flips over). `compute_pan_tilt` takes the direct one unless it is out of range
and the flipped one is in range.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Optional

from .model import FixtureInstance, Orientation, SafetyZone, Vec3

_Vec = tuple[float, float, float]


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


# -- small vector helpers ---------------------------------------------------

def _dot(a: _Vec, b: _Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: _Vec, b: _Vec) -> _Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _scale(a: _Vec, k: float) -> _Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _add(a: _Vec, b: _Vec) -> _Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: _Vec, b: _Vec) -> _Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a: _Vec) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: _Vec) -> _Vec:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _normalize_180(angle_deg: float) -> float:
    a = angle_deg % 360.0
    if a > 180.0:
        a -= 360.0
    return a


# -- mount frame ----------------------------------------------------------------

@dataclasses.dataclass
class MountFrame:
    home: _Vec       # B: beam direction at tilt centre (along the pan axis, away from the base)
    front: _Vec      # F0: direction the beam leans toward at pan centre (perpendicular to home)
    pan_axis: _Vec   # A': the axis pan+ rotates about (right-hand), pointing downward-ish


def mount_frame(orientation: Orientation) -> MountFrame:
    yaw = math.radians(orientation.yaw_deg)
    pitch = math.radians(orientation.pitch_deg)
    horizontal: _Vec = (math.sin(yaw), math.cos(yaw), 0.0)   # compass direction yaw
    home = _add(_scale(horizontal, math.sin(pitch)), (0.0, 0.0, -math.cos(pitch)))

    # front = the yaw direction, made perpendicular to home. For a vertical pan axis
    # (hung or standing) that is the yaw direction itself; only an exactly sideways
    # mount (home parallel to yaw) needs a fallback (up).
    proj = _sub(horizontal, _scale(home, _dot(horizontal, home)))
    if _norm(proj) < 1e-6:
        proj = _sub((0.0, 0.0, 1.0), _scale(home, home[2]))
    front = _unit(proj)

    # pan+ = right-hand rotation about the downward-pointing pan axis, which for a
    # vertical axis is clockwise from above whether the unit stands or hangs
    pan_axis = home if home[2] <= 0 else _scale(home, -1.0)
    return MountFrame(home=home, front=front, pan_axis=pan_axis)


def _direction_to(fixture_position: Vec3, target_point: Vec3) -> Optional[_Vec]:
    d = (target_point.x - fixture_position.x, target_point.y - fixture_position.y,
         target_point.z - fixture_position.z)
    return None if _norm(d) < 1e-9 else _unit(d)


def _pan_tilt_from_direction(frame: MountFrame, d: _Vec) -> tuple[float, float]:
    """(phi, alpha) in degrees: alpha = angle of the beam away from home (0..180),
    phi = the pan (right-hand about the pan axis, from `front`) that puts a
    positive-tilt lean toward the target. Direct branch; see compute_pan_tilt."""
    alpha = math.degrees(math.atan2(_norm(_cross(d, frame.home)), _dot(d, frame.home)))
    perp = _sub(d, _scale(frame.home, _dot(d, frame.home)))
    if _norm(perp) < 1e-9:
        return 0.0, alpha              # straight along the pan axis: pan is arbitrary
    phi = math.degrees(math.atan2(_dot(_cross(frame.front, perp), frame.pan_axis),
                                  _dot(frame.front, perp)))
    return _normalize_180(phi), alpha


def beam_direction(
    fixture_position: Vec3,
    fixture_orientation: Orientation,
    target_point: Vec3,
) -> tuple[float, float, Vec3]:
    """Return (pan_angle_deg, tilt_angle_deg, unit_direction_vector).

    pan/tilt are the direct-branch angles for a normal fixture: tilt is the
    angle away from the fixture's home axis (always 0..180), pan is measured
    from its front direction, clockwise-from-above for a vertical axis.
    """
    d = _direction_to(fixture_position, target_point)
    if d is None:
        # target coincides with fixture position -- direction undefined,
        # default to straight down (typical moving-head home position)
        return 0.0, 0.0, Vec3(0.0, 0.0, -1.0)
    phi, alpha = _pan_tilt_from_direction(mount_frame(fixture_orientation), d)
    return phi, alpha, Vec3(*d)


def beam_from_pan_tilt(
    orientation: Orientation,
    pan_angle_deg: float,
    tilt_angle_deg: float,
    inverted_pan: bool = False,
    inverted_tilt: bool = False,
    pan_offset_deg: float = 0.0,
    tilt_offset_deg: float = 0.0,
) -> Vec3:
    """Forward kinematics: where the beam points for given pan/tilt angles (the
    same angles compute_pan_tilt reports). The inverse of the IK, used to check it."""
    frame = mount_frame(orientation)
    pan = (pan_angle_deg - pan_offset_deg) * (-1.0 if inverted_pan else 1.0)
    tilt = (tilt_angle_deg - tilt_offset_deg) * (-1.0 if inverted_tilt else 1.0)
    p, t = math.radians(pan), math.radians(tilt)
    across = _cross(frame.pan_axis, frame.front)
    lean = _add(_scale(frame.front, math.cos(p)), _scale(across, math.sin(p)))  # front rotated by pan
    d = _add(_scale(frame.home, math.cos(t)), _scale(lean, math.sin(t)))
    return Vec3(*_unit(d))


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
    pan_half, tilt_half = pan_range_deg / 2.0, tilt_range_deg / 2.0
    d = _direction_to(fixture_position, target_point)
    if d is None:
        phi, alpha = 0.0, 0.0
    else:
        phi, alpha = _pan_tilt_from_direction(mount_frame(fixture_orientation), d)

    # tilt+ leans toward the pan direction (or away from it when inverted).
    # Two ways to reach the target: the direct one, and flipping the head over.
    sign = -1.0 if inverted_tilt else 1.0
    direct = (phi, sign * alpha)
    flipped = (phi + 180.0, -sign * alpha)

    def finalise(pan: float, tilt: float) -> tuple[float, float]:
        pan = _normalize_180(pan)
        if inverted_pan:
            pan = -pan
        return pan + pan_offset_deg, tilt + tilt_offset_deg

    def fits(pan: float, tilt: float) -> bool:
        return abs(pan) <= pan_half + 1e-9 and abs(tilt) <= tilt_half + 1e-9

    pan_angle, tilt_angle = finalise(*direct)
    if not fits(pan_angle, tilt_angle):
        alt_pan, alt_tilt = finalise(*flipped)
        if fits(alt_pan, alt_tilt):
            pan_angle, tilt_angle = alt_pan, alt_tilt

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
