import math

from app.room.ik import (
    compute_pan_tilt,
    ray_intersects_box,
    target_violates_safety_zones,
)
from app.room.model import Orientation, SafetyZone, Vec3


def test_straight_down_target_is_pan_tilt_zero():
    pos = Vec3(0, 0, 3)
    target = Vec3(0, 0, 0)  # directly below
    result = compute_pan_tilt(pos, Orientation(), target, pan_range_deg=540, tilt_range_deg=270)
    # world_pitch for straight down = 0, orientation.pitch_deg default 0 -> tilt_angle 0
    assert abs(result.tilt_angle_deg) < 1e-6
    # centered angle (0) maps to the midpoint of the 16-bit range
    assert 32640 <= (result.pan_dmx << 8 | result.pan_fine_dmx) <= 32900


def test_pan_angle_increases_toward_positive_x():
    pos = Vec3(0, 0, 3)
    target = Vec3(1, 0, 3)  # target at fixture height, offset in +X, +Y=0 so horizontal
    result = compute_pan_tilt(pos, Orientation(), target, pan_range_deg=540, tilt_range_deg=270)
    assert result.pan_angle_deg > 0


def test_out_of_range_target_is_clamped_and_flagged():
    pos = Vec3(0, 0, 0)
    target = Vec3(0, 100, 0.001)  # nearly 90 degrees tilt from straight down
    result = compute_pan_tilt(pos, Orientation(), target, pan_range_deg=540, tilt_range_deg=90)
    assert result.in_range is False
    assert result.tilt_dmx == 255  # clamped to max of range
    assert result.tilt_fine_dmx == 255


def test_inverted_pan_flips_sign():
    pos = Vec3(0, 0, 3)
    target = Vec3(1, 0, 3)
    normal = compute_pan_tilt(pos, Orientation(), target, 540, 270, inverted_pan=False)
    inverted = compute_pan_tilt(pos, Orientation(), target, 540, 270, inverted_pan=True)
    assert normal.pan_angle_deg == -inverted.pan_angle_deg


def test_ray_intersects_box_direct_hit():
    origin = Vec3(0, 0, 3)
    direction = Vec3(0, 0, -1)
    box_min = Vec3(-1, -1, 0)
    box_max = Vec3(1, 1, 1)
    assert ray_intersects_box(origin, direction, box_min, box_max) is True


def test_ray_intersects_box_miss():
    origin = Vec3(0, 0, 3)
    direction = Vec3(1, 0, 0)  # points sideways, away from the box below
    box_min = Vec3(-1, -1, 0)
    box_max = Vec3(1, 1, 1)
    assert ray_intersects_box(origin, direction, box_min, box_max) is False


def test_target_violates_safety_zone_blocks_beam_into_crowd():
    pos = Vec3(0, 0, 3)
    crowd_zone = SafetyZone(
        id="crowd",
        name="Crowd area",
        min_corner=Vec3(-5, -5, 0),
        max_corner=Vec3(5, 5, 1.8),
    )
    target_into_crowd = Vec3(2, 2, 0.5)
    hit = target_violates_safety_zones(pos, Orientation(), target_into_crowd, [crowd_zone])
    assert hit is crowd_zone

    target_over_crowd = Vec3(0, 0, 5)  # points straight up, clear of the zone
    clear = target_violates_safety_zones(pos, Orientation(), target_over_crowd, [crowd_zone])
    assert clear is None
