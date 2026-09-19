"""IK against a physical model of a moving head, for every way it can be mounted.

Ground truth (measured on a real standing unit): home (tilt centre) is along the
pan axis away from the base -- straight up when standing; at pan centre, tilt+
leans toward the display side; pan+ swings counter-clockwise from above.
"""

import itertools
import math
import random

import pytest

from app.room.ik import (
    beam_direction,
    beam_from_pan_tilt,
    compute_pan_tilt,
    mount_frame,
)
from app.room.model import Orientation, Vec3


def dot(a, b):
    return a.x * b.x + a.y * b.y + a.z * b.z


def unit(v):
    n = math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
    return Vec3(v.x / n, v.y / n, v.z / n)


def to(pos, target):
    return unit(Vec3(target.x - pos.x, target.y - pos.y, target.z - pos.z))


# ---- mount frame -------------------------------------------------------------

def test_home_axis_follows_the_pitch_convention():
    down = mount_frame(Orientation(yaw_deg=0, pitch_deg=0)).home
    side = mount_frame(Orientation(yaw_deg=0, pitch_deg=90)).home
    up = mount_frame(Orientation(yaw_deg=0, pitch_deg=180)).home
    assert down == pytest.approx((0, 0, -1), abs=1e-9)
    assert side == pytest.approx((0, 1, 0), abs=1e-9)        # yaw 0 faces +Y
    assert up == pytest.approx((0, 0, 1), abs=1e-9)


@pytest.mark.parametrize("pitch", [0, 180])
@pytest.mark.parametrize("yaw", [0, 90, 180, 270])
def test_front_is_the_yaw_direction_for_hung_and_standing_units(pitch, yaw):
    frame = mount_frame(Orientation(yaw_deg=yaw, pitch_deg=pitch))
    assert frame.front == pytest.approx((math.sin(math.radians(yaw)), math.cos(math.radians(yaw)), 0), abs=1e-9)


@pytest.mark.parametrize("pitch", [0, 180])
def test_pan_axis_points_down_so_pan_sense_is_the_same_standing_or_hanging(pitch):
    assert mount_frame(Orientation(pitch_deg=pitch)).pan_axis == pytest.approx((0, 0, -1), abs=1e-9)


# ---- the measured facts about a standing head -------------------------------------

STANDING = Orientation(yaw_deg=0, pitch_deg=180)


def test_standing_head_at_tilt_centre_points_straight_up():
    d = beam_from_pan_tilt(STANDING, 0, 0)
    assert (d.x, d.y, d.z) == pytest.approx((0, 0, 1), abs=1e-9)


def test_standing_head_tilt_plus_leans_toward_the_front_at_pan_centre():
    d = beam_from_pan_tilt(STANDING, 0, 68)           # DMX 192 is about +68 degrees
    assert d.y > 0.5 and d.z > 0.3                     # yaw 0 faces +Y: leans forward, still upward


def test_standing_head_pan_plus_swings_counter_clockwise_from_above_when_inverted():
    # measured: pan 128 -> 192 swung the beam counter-clockwise seen from above.
    # IK's own pan sense is clockwise, so this head needs inverted_pan.
    before = beam_from_pan_tilt(STANDING, 0, 68, inverted_pan=True)
    after = beam_from_pan_tilt(STANDING, 64, 68, inverted_pan=True)
    cross_z = before.x * after.y - before.y * after.x
    assert cross_z > 0                                 # positive z = counter-clockwise seen from above


def test_ceiling_needs_less_tilt_than_the_floor_for_a_standing_head():
    """The reported bug: 'point at the floor and tilt goes up, ceiling goes down'."""
    pos = Vec3(0, 0, 0.2)
    ceiling = compute_pan_tilt(pos, STANDING, Vec3(0, 1.5, 4.0), 540, 270)
    floor = compute_pan_tilt(pos, STANDING, Vec3(0, 3.0, 0.0), 540, 270)
    assert 0 < ceiling.tilt_angle_deg < 45             # nearly straight up
    assert floor.tilt_angle_deg > 90                   # below horizontal
    assert floor.tilt_dmx > ceiling.tilt_dmx           # floor = higher tilt DMX than ceiling
    assert ceiling.in_range and floor.in_range


def test_target_straight_above_a_standing_head_is_tilt_centre():
    r = compute_pan_tilt(Vec3(0, 0, 0.2), STANDING, Vec3(0, 0, 4), 540, 270)
    assert r.tilt_angle_deg == pytest.approx(0, abs=1e-6)
    assert 127 <= r.tilt_dmx <= 128


def test_the_users_head_1_setup_aims_toward_the_room():
    """head-1: standing at (0, 4.119, 0.2), yaw 180 (display faces -Y), pan inverted."""
    pos, orient = Vec3(0, 4.119, 0.2), Orientation(yaw_deg=180, pitch_deg=180)
    floor_ahead = compute_pan_tilt(pos, orient, Vec3(0, 1.0, 0.0), 540, 270, inverted_pan=True)
    assert abs(floor_ahead.pan_angle_deg) < 1e-6       # straight ahead of the display: pan centre
    assert floor_ahead.tilt_angle_deg > 90             # a floor spot ahead is below horizontal
    d = beam_from_pan_tilt(orient, floor_ahead.pan_angle_deg, floor_ahead.tilt_angle_deg, inverted_pan=True)
    assert dot(d, to(pos, Vec3(0, 1.0, 0.0))) > 0.999999


# ---- IK and forward kinematics agree, everywhere ----------------------------------------

@pytest.mark.parametrize("pitch", [0, 45, 90, 135, 180])
@pytest.mark.parametrize("yaw", [0, 90, 200])
@pytest.mark.parametrize("inv_pan,inv_tilt", list(itertools.product([False, True], repeat=2)))
def test_fk_of_the_ik_solution_hits_the_target(pitch, yaw, inv_pan, inv_tilt):
    rng = random.Random(pitch * 1000 + yaw * 10 + inv_pan * 2 + inv_tilt)
    orient = Orientation(yaw_deg=yaw, pitch_deg=pitch)
    pos = Vec3(rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(0, 3))
    checked = 0
    for _ in range(80):
        target = Vec3(rng.uniform(-6, 6), rng.uniform(-6, 6), rng.uniform(0, 4))
        if math.dist(pos.as_tuple(), target.as_tuple()) < 0.5:
            continue
        r = compute_pan_tilt(pos, orient, target, 540, 270, inverted_pan=inv_pan, inverted_tilt=inv_tilt)
        if not r.in_range:
            continue
        d = beam_from_pan_tilt(orient, r.pan_angle_deg, r.tilt_angle_deg,
                               inverted_pan=inv_pan, inverted_tilt=inv_tilt)
        assert dot(d, to(pos, target)) > 0.999999, (target, r)
        checked += 1
    assert checked > 20


def test_offsets_are_applied_consistently_in_both_directions():
    orient = Orientation(yaw_deg=30, pitch_deg=180)
    pos, target = Vec3(1, 1, 0.3), Vec3(-2, 3, 2)
    r = compute_pan_tilt(pos, orient, target, 540, 270, inverted_pan=True,
                         pan_offset_deg=4.0, tilt_offset_deg=-3.0)
    d = beam_from_pan_tilt(orient, r.pan_angle_deg, r.tilt_angle_deg, inverted_pan=True,
                           pan_offset_deg=4.0, tilt_offset_deg=-3.0)
    assert dot(d, to(pos, target)) > 0.999999


# ---- hung units behave exactly as before ----------------------------------------------

def _legacy(pos, orient, target, inv_pan=False, inv_tilt=False):
    dx, dy, dz = target.x - pos.x, target.y - pos.y, target.z - pos.z
    yaw = math.degrees(math.atan2(dx, dy))
    pitch = math.degrees(math.atan2(math.hypot(dx, dy), -dz))
    norm = lambda a: ((a % 360) - 360 if (a % 360) > 180 else (a % 360))
    pan, tilt = norm(yaw - orient.yaw_deg), norm(pitch - orient.pitch_deg)
    return (-pan if inv_pan else pan), (-tilt if inv_tilt else tilt)


@pytest.mark.parametrize("inv_pan,inv_tilt", list(itertools.product([False, True], repeat=2)))
def test_a_hung_fixture_gets_the_same_angles_the_old_formula_gave(inv_pan, inv_tilt):
    rng = random.Random(7)
    orient = Orientation(yaw_deg=25, pitch_deg=0)
    pos = Vec3(0.5, -1, 3.5)
    for _ in range(100):
        target = Vec3(rng.uniform(-6, 6), rng.uniform(-6, 6), rng.uniform(0, 2))
        r = compute_pan_tilt(pos, orient, target, 540, 270, inverted_pan=inv_pan, inverted_tilt=inv_tilt)
        pan, tilt = _legacy(pos, orient, target, inv_pan, inv_tilt)
        assert r.pan_angle_deg == pytest.approx(pan, abs=1e-6)
        assert r.tilt_angle_deg == pytest.approx(tilt, abs=1e-6)


# ---- flipping the head over ---------------------------------------------------------------

def test_the_head_flips_over_when_only_the_flipped_solution_fits_the_pan_range():
    orient = Orientation(yaw_deg=0, pitch_deg=180)
    pos = Vec3(0, 0, 0.2)
    target = Vec3(-3, -2, 1.0)                        # behind and to the left: needs pan near 150
    direct_only = compute_pan_tilt(pos, orient, target, 540, 270)
    assert abs(direct_only.pan_angle_deg) > 90
    limited = compute_pan_tilt(pos, orient, target, 180, 270)       # pan only +-90
    assert limited.in_range
    assert abs(limited.pan_angle_deg) <= 90 and limited.tilt_angle_deg < 0   # flipped over: negative tilt
    d = beam_from_pan_tilt(orient, limited.pan_angle_deg, limited.tilt_angle_deg)
    assert dot(d, to(pos, target)) > 0.999999


def test_unreachable_targets_are_flagged_not_silently_wrong():
    r = compute_pan_tilt(Vec3(0, 0, 0.2), STANDING, Vec3(0, 0, -5), 540, 270)   # straight down through the base
    assert r.in_range is False


def test_beam_direction_still_returns_the_unit_vector_toward_the_target():
    pan, tilt, d = beam_direction(Vec3(1, 2, 3), STANDING, Vec3(1, 2, 0))
    assert (d.x, d.y, d.z) == pytest.approx((0, 0, -1), abs=1e-9)
    assert tilt == pytest.approx(180, abs=1e-6)                  # straight down is 180 degrees from home (up)


# ---- the real head's measured limits ------------------------------------------------

def _generic_head_profile():
    from app.fixtures.library import FixtureLibrary

    return FixtureLibrary().get("generic-rgbw-moving-head-14ch")


def test_the_generic_head_tilts_180_degrees_from_up_to_level_either_side():
    """Measured: tilt DMX 255 is exactly level, so the range is 180, not 270."""
    assert _generic_head_profile().tilt_range_deg == 180.0


def test_level_is_the_end_of_travel_and_below_level_is_flagged_out_of_range():
    pos = Vec3(0, 4.119, 0.2)
    orient = Orientation(yaw_deg=180, pitch_deg=180)
    level = compute_pan_tilt(pos, orient, Vec3(0, -10, 0.2), 540, 180, inverted_pan=True)
    assert level.tilt_angle_deg == pytest.approx(90, abs=1e-6)
    assert level.in_range and level.tilt_dmx == 255
    below = compute_pan_tilt(pos, orient, Vec3(0, 3.3, 0.0), 540, 180, inverted_pan=True)
    assert below.in_range is False                       # a standing head cannot aim below level
    assert below.tilt_dmx == 255                         # it goes to the end of travel instead


def test_the_users_side_target_needs_positive_pan_when_pan_is_inverted():
    """Measured: a target to the LEFT of the display side ended up on the left."""
    pos = Vec3(0, 4.119, 0.2)
    orient = Orientation(yaw_deg=180, pitch_deg=180)
    r = compute_pan_tilt(pos, orient, Vec3(2.5, 3.6, 2.5), 540, 180, inverted_pan=True)
    assert r.in_range
    assert r.pan_angle_deg == pytest.approx(78.27, abs=0.05)
    assert r.tilt_angle_deg == pytest.approx(47.99, abs=0.05)
