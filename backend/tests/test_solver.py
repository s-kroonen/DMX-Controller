"""The multi-point calibration solver: recover a head's mounting, offsets and (with enough marks) its
position from where its beam was when it sat on marks of known position."""

import math
import random

import pytest

from app.room.ik import compute_pan_tilt
from app.room.model import Orientation, Vec3
from app.room.solver import HeadParams, Observation, solve

PAN_RANGE, TILT_RANGE = 540.0, 270.0

# marks in a 6 x 8 m room, 3.5 m high: floor corners and centre, and two on the walls
MARKS = [Vec3(0, 0, 0), Vec3(6, 0, 0), Vec3(6, 8, 0), Vec3(0, 8, 0), Vec3(3, 4, 0),
         Vec3(0, 4, 2.0), Vec3(6, 4, 2.0), Vec3(3, 8, 2.5)]


def observe(true: HeadParams, marks=MARKS, noise_deg=0.0, seed=1):
    """What an operator would record: the pan/tilt at which the beam sits on each mark."""
    rng = random.Random(seed)
    out = []
    for m in marks:
        r = compute_pan_tilt(true.position, Orientation(true.yaw_deg, true.pitch_deg, 0.0), m, PAN_RANGE, TILT_RANGE,
                             true.inverted_pan, true.inverted_tilt, true.pan_offset_deg, true.tilt_offset_deg)
        assert r.in_range, "test setup: mark out of the head's range"
        out.append(Observation(m, r.pan_angle_deg + rng.gauss(0, noise_deg), r.tilt_angle_deg + rng.gauss(0, noise_deg)))
    return out


HUNG = HeadParams(Vec3(2.0, 3.0, 3.4), yaw_deg=40.0, pitch_deg=0.0, pan_offset_deg=7.0, tilt_offset_deg=-4.0)
ON_WALL = HeadParams(Vec3(0.2, 2.0, 2.6), yaw_deg=90.0, pitch_deg=90.0, pan_offset_deg=-5.0, tilt_offset_deg=3.0)


def wrong(true: HeadParams, **changes):
    """What the app had before calibrating: a mounting that is a few degrees off."""
    return HeadParams(**{**true.__dict__, "yaw_deg": true.yaw_deg + 12, "pitch_deg": true.pitch_deg + 6,
                         "pan_offset_deg": 0.0, "tilt_offset_deg": 0.0, **changes})


def test_a_hung_head_recovers_its_offsets_and_mounting_exactly():
    obs = observe(HUNG)
    fit = solve(wrong(HUNG), obs)
    assert fit.rms_before_deg > 3.0 and fit.rms_deg < 0.01
    # the fit explains every mark; on a hung head yaw and pan offset are the same turn (the pan angle is
    # measured minus the offset), so compare yaw minus offset, and the tilt offset which is its own degree
    d = (fit.params.yaw_deg - fit.params.pan_offset_deg) - (HUNG.yaw_deg - HUNG.pan_offset_deg)
    assert (d + 180) % 360 - 180 == pytest.approx(0, abs=0.05)
    assert fit.params.tilt_offset_deg == pytest.approx(HUNG.tilt_offset_deg, abs=0.05)


def test_a_head_on_a_wall_recovers_every_parameter():
    obs = observe(ON_WALL, marks=[Vec3(3, 0, 0), Vec3(3, 8, 0), Vec3(5, 4, 0), Vec3(2, 6, 3.0), Vec3(4, 1, 1.0),
                                  Vec3(5, 7, 2.0)])
    fit = solve(wrong(ON_WALL), obs)
    assert fit.rms_deg < 0.01
    assert fit.params.yaw_deg == pytest.approx(ON_WALL.yaw_deg, abs=0.05)
    assert fit.params.pitch_deg == pytest.approx(ON_WALL.pitch_deg, abs=0.05)
    assert fit.params.pan_offset_deg == pytest.approx(ON_WALL.pan_offset_deg, abs=0.05)
    assert fit.params.tilt_offset_deg == pytest.approx(ON_WALL.tilt_offset_deg, abs=0.05)


def test_noisy_recordings_give_a_result_within_a_degree():
    obs = observe(ON_WALL, marks=[Vec3(3, 0, 0), Vec3(3, 8, 0), Vec3(5, 4, 0), Vec3(2, 6, 3.0), Vec3(4, 1, 1.0),
                                  Vec3(5, 7, 2.0)], noise_deg=0.3)
    fit = solve(wrong(ON_WALL), obs)
    assert fit.rms_deg < 0.6
    assert fit.params.yaw_deg == pytest.approx(ON_WALL.yaw_deg, abs=1.5)
    assert fit.params.tilt_offset_deg == pytest.approx(ON_WALL.tilt_offset_deg, abs=1.5)


def test_the_position_can_be_found_from_room_marks():
    """Position not known: start 40 cm and 30 cm off and let the marks (room corners and wall spots) find it."""
    obs = observe(HUNG)
    start = wrong(HUNG, position=Vec3(HUNG.position.x + 0.4, HUNG.position.y - 0.3, HUNG.position.z - 0.25))
    fit = solve(start, obs, solve_position=True)
    assert fit.solved_position and fit.rms_deg < 0.02
    assert fit.params.position.x == pytest.approx(HUNG.position.x, abs=0.03)
    assert fit.params.position.y == pytest.approx(HUNG.position.y, abs=0.03)
    assert fit.params.position.z == pytest.approx(HUNG.position.z, abs=0.03)


def test_the_position_needs_more_marks_than_the_mounting():
    obs = observe(HUNG)
    solve(wrong(HUNG), obs[:3])                                     # three are enough for the mounting
    with pytest.raises(ValueError, match="at least 5"):
        solve(wrong(HUNG), obs[:4], solve_position=True)
    with pytest.raises(ValueError, match="at least 3"):
        solve(wrong(HUNG), obs[:2])


def test_wrong_invert_flags_are_found():
    true = HeadParams(**{**HUNG.__dict__, "inverted_pan": True})
    obs = observe(true)
    fit = solve(HeadParams(**{**HUNG.__dict__, "inverted_pan": False, "pan_offset_deg": 0, "tilt_offset_deg": 0}), obs)
    assert fit.changed_flags and fit.params.inverted_pan is True and fit.params.inverted_tilt is False
    assert fit.rms_deg < 0.05 and any("invert" in w for w in fit.warnings)


def test_the_right_invert_flags_are_kept():
    fit = solve(wrong(HUNG), observe(HUNG))
    assert not fit.changed_flags and not any("invert" in w for w in fit.warnings)


def test_an_upright_head_fits_although_yaw_and_pan_offset_cannot_be_told_apart():
    upright = HeadParams(Vec3(3.0, 4.0, 0.1), yaw_deg=30.0, pitch_deg=180.0, pan_offset_deg=0.0, tilt_offset_deg=2.0)
    marks = [Vec3(0, 0, 3.5), Vec3(6, 0, 3.0), Vec3(6, 8, 3.2), Vec3(0, 8, 3.0), Vec3(3, 8, 1.8), Vec3(0, 4, 2.5)]
    fit = solve(wrong(upright), observe(upright, marks=marks))
    assert fit.rms_deg < 0.02


def test_marks_that_need_the_head_flipped_over_still_fit():
    """Marks behind a head standing at a wall are reached with the pan turned 180 and the tilt negated;
    the fit compares directions, so which way round it was reached does not matter."""
    obs = observe(ON_WALL, marks=[Vec3(3, 0, 0), Vec3(3, 8, 0), Vec3(-0.0, 2, 1.0), Vec3(5, 4, 0), Vec3(2, 6, 3.0)])
    assert solve(wrong(ON_WALL), obs).rms_deg < 0.02


def test_marks_in_a_line_are_flagged_as_uncertain():
    line = [Vec3(1, 3, 0), Vec3(2, 3, 0), Vec3(3, 3, 0), Vec3(4, 3, 0)]
    fit = solve(wrong(HUNG), observe(HUNG, marks=line))
    assert any("nearly in a line" in w for w in fit.warnings)


def test_a_mark_that_is_off_shows_up_in_its_residual():
    obs = observe(HUNG)
    obs[2] = Observation(obs[2].point, obs[2].pan_angle_deg + 12.0, obs[2].tilt_angle_deg)     # a bad recording
    fit = solve(wrong(HUNG), obs)
    assert fit.residuals_deg.index(max(fit.residuals_deg)) == 2
    assert max(fit.residuals_deg) > 3.0 and any("still miss" in w for w in fit.warnings)


def test_a_far_off_starting_mounting_is_still_found():
    start = HeadParams(**{**HUNG.__dict__, "yaw_deg": HUNG.yaw_deg + 140, "pan_offset_deg": 0, "tilt_offset_deg": 0})
    assert solve(start, observe(HUNG)).rms_deg < 0.05


def _random_head(rng):
    pitch = rng.choice([0.0, 90.0, 180.0, 60.0, 130.0])
    return HeadParams(Vec3(rng.uniform(1, 5), rng.uniform(1, 7), rng.uniform(0.2, 3.3)), rng.uniform(0, 360), pitch,
                      rng.uniform(-10, 10), rng.uniform(-8, 8))


def test_random_heads_are_recovered_and_predict_new_marks():
    """A spread of mountings and offsets, starting 15 degrees off with slightly noisy recordings: the fit must
    explain the marks and then aim well at marks it was not given."""
    rng = random.Random(7)
    checked = 0
    while checked < 12:
        true = _random_head(rng)
        marks = [Vec3(rng.uniform(0, 6), rng.uniform(0, 8), rng.choice([0.0, 0.0, 1.5, 3.0])) for _ in range(9)]
        try:
            obs = observe(true, marks=marks, noise_deg=0.1, seed=checked)
        except AssertionError:
            continue                               # a mark this head cannot physically reach: pick another head
        start = HeadParams(**{**true.__dict__, "yaw_deg": true.yaw_deg + rng.uniform(-15, 15),
                              "pitch_deg": true.pitch_deg + rng.uniform(-8, 8), "pan_offset_deg": 0.0,
                              "tilt_offset_deg": 0.0})
        fit = solve(start, obs[:6])
        assert fit.rms_deg < 0.5, (true, fit.rms_deg)
        for extra in obs[6:]:                      # marks that were held back
            from app.room.solver import _angle_error_deg
            import numpy as np
            err = _angle_error_deg(fit.params, extra, np.array([true.position.x, true.position.y, true.position.z]))
            assert err < 1.0, (true, err)
        checked += 1


@pytest.mark.parametrize("pitch", [0.0, 180.0])
@pytest.mark.parametrize("inverted_pan", [False, True])
def test_a_vertical_axis_head_reports_the_split_that_keeps_its_pan_offset(pitch, inverted_pan):
    """Yaw and pan offset are one turn on a hung or upright head; the answer keeps the offset it had (here 2)
    and puts the difference in yaw, so it reads as a mounting direction rather than 176 degrees of offset."""
    true = HeadParams(Vec3(3.0, 4.0, 0.2 if pitch == 180.0 else 3.4), yaw_deg=191.0, pitch_deg=pitch,
                      pan_offset_deg=-3.0, tilt_offset_deg=4.0, inverted_pan=inverted_pan)
    marks = [Vec3(0, 0, 3.5), Vec3(6, 0, 3.0), Vec3(6, 8, 3.2), Vec3(0, 8, 3.0)] if pitch == 180.0 else MARKS
    start = HeadParams(**{**true.__dict__, "yaw_deg": 180.0, "pan_offset_deg": 2.0, "tilt_offset_deg": 0.0})
    fit = solve(start, observe(true, marks=marks))
    assert fit.rms_deg < 0.02
    assert fit.params.pan_offset_deg == pytest.approx(2.0, abs=0.01)
    assert fit.params.tilt_offset_deg == pytest.approx(4.0, abs=0.05)
