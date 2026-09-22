"""Multi-point calibration solver: work out where a moving head really is and how it is mounted.

The operator points the head at several marks whose room positions are known (room corners from the
room shape, saved points, measured spots) and records the pan/tilt it took to put the beam exactly on
each. Every recording says: "from where the head is, the beam direction that goes with THIS pan/tilt
points at THAT mark". Fitting the head's parameters so all of those statements hold at once is a
small nonlinear least-squares problem (the same one as locating a camera from known landmarks).

Parameters that can be fitted, all of which the app already stores per fixture:

  yaw, pitch      the mounting orientation (see ik.py: pitch places the head's home direction,
                  yaw is the compass direction of its front)
  pan offset      a rotation about the pan axis. Its effect equals a roll of the mount, so it is the
                  third rotational degree of freedom; on an upright or hanging head it is
                  indistinguishable from yaw (both turn about the vertical), which the fit reports
                  as an equivalent split rather than failing
  tilt offset     the tilt zero (a separate degree of freedom: it turns about an axis that moves
                  with pan)
  position (x,y,z)  optional: with enough well-spread marks the position can be found too

The residual is the angle between the beam direction the fit predicts and the direction from the head
to the mark, so it does not care which of the two ways of reaching a point the head used (direct, or
flipped over). Wrong invert flags are found by trying all four combinations.

Precision is limited by how exactly the beam was centred on each mark and how well the marks are
known; the residual per mark shows which one is off.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Optional

import numpy as np

from .ik import beam_from_pan_tilt
from .model import Orientation, Vec3


@dataclasses.dataclass
class Observation:
    """The beam was on `point` when the head was at these pan/tilt angles (degrees, as the IK reports)."""

    point: Vec3
    pan_angle_deg: float
    tilt_angle_deg: float


@dataclasses.dataclass
class HeadParams:
    position: Vec3
    yaw_deg: float
    pitch_deg: float
    pan_offset_deg: float = 0.0
    tilt_offset_deg: float = 0.0
    inverted_pan: bool = False
    inverted_tilt: bool = False


@dataclasses.dataclass
class Fit:
    params: HeadParams
    rms_deg: float                 # after the fit
    rms_before_deg: float          # with the parameters the head had
    residuals_deg: list[float]     # per observation, after the fit
    solved_position: bool
    changed_flags: bool
    warnings: list[str]


def dmx_to_angle(coarse: int, fine: int, range_deg: float) -> float:
    """The angle (degrees from mechanical centre) a 16-bit pan/tilt value stands for: the inverse of the IK's
    angle -> DMX mapping."""
    fraction = ((int(coarse) << 8) | int(fine)) / 65535.0
    return fraction * range_deg - range_deg / 2.0


# -- geometry ---------------------------------------------------------------------------------------

def _direction(params: HeadParams, obs: Observation, position: np.ndarray, yaw: float, pitch: float,
               pan_off: float, tilt_off: float) -> np.ndarray:
    """Predicted unit beam direction minus the unit direction to the mark (a 3-vector whose length is
    about the pointing error in radians)."""
    beam = beam_from_pan_tilt(
        Orientation(math.degrees(yaw), math.degrees(pitch), 0.0),
        obs.pan_angle_deg, obs.tilt_angle_deg, params.inverted_pan, params.inverted_tilt,
        math.degrees(pan_off), math.degrees(tilt_off))
    to_mark = np.array([obs.point.x, obs.point.y, obs.point.z]) - position
    norm = np.linalg.norm(to_mark)
    to_mark = to_mark / norm if norm > 1e-9 else to_mark
    return np.array([beam.x, beam.y, beam.z]) - to_mark


def _residual_vector(x: np.ndarray, params: HeadParams, observations: list[Observation], solve_position: bool,
                     start: np.ndarray) -> np.ndarray:
    yaw, pitch, pan_off, tilt_off = x[0], x[1], x[2], x[3]
    position = x[4:7] if solve_position else np.array([params.position.x, params.position.y, params.position.z])
    parts = [_direction(params, o, position, yaw, pitch, pan_off, tilt_off) for o in observations]
    # a very weak pull toward the starting values keeps directions the marks cannot see (yaw against the
    # pan offset on an upright head) where they were, instead of wandering
    prior = 1e-3 * (x - start)
    return np.concatenate(parts + [prior])


def _angle_error_deg(params: HeadParams, obs: Observation, position: np.ndarray) -> float:
    beam = beam_from_pan_tilt(
        Orientation(params.yaw_deg, params.pitch_deg, 0.0), obs.pan_angle_deg, obs.tilt_angle_deg,
        params.inverted_pan, params.inverted_tilt, params.pan_offset_deg, params.tilt_offset_deg)
    b = np.array([beam.x, beam.y, beam.z])
    t = np.array([obs.point.x, obs.point.y, obs.point.z]) - position
    t = t / max(np.linalg.norm(t), 1e-9)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(b, t))))))


def _rms(params: HeadParams, observations: list[Observation]) -> tuple[float, list[float]]:
    pos = np.array([params.position.x, params.position.y, params.position.z])
    errors = [_angle_error_deg(params, o, pos) for o in observations]
    return math.sqrt(sum(e * e for e in errors) / len(errors)), errors


# -- least squares -------------------------------------------------------------------------------------

def _levenberg_marquardt(fun, x0: np.ndarray, iterations: int = 80) -> tuple[np.ndarray, float]:
    # one iteration never moves an angle by more than ~30 degrees or the position by more than 0.5 m:
    # a far position makes every mark look the same direction, which is a trap for big steps
    caps = np.array([0.5] * 4 + [0.5] * (x0.size - 4))
    x = x0.copy()
    r = fun(x)
    cost = float(r @ r)
    lam = 1e-2
    for _ in range(iterations):
        jac = np.empty((r.size, x.size))
        for j in range(x.size):
            step = np.zeros_like(x)
            step[j] = 1e-6
            jac[:, j] = (fun(x + step) - r) / 1e-6
        gradient = jac.T @ r
        hessian = jac.T @ jac
        improved = False
        for _ in range(8):
            try:
                delta = np.linalg.solve(hessian + lam * np.diag(np.diag(hessian) + 1e-9), -gradient)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            delta = delta * min(1.0, float(np.min(caps / np.maximum(np.abs(delta), 1e-12))))
            trial = x + delta
            trial_r = fun(trial)
            trial_cost = float(trial_r @ trial_r)
            if trial_cost < cost:
                improved = True
                x, r, cost = trial, trial_r, trial_cost
                lam = max(lam / 3, 1e-9)
                break
            lam *= 5
        if not improved or float(np.linalg.norm(delta)) < 1e-10:
            break
    return x, cost


def _wrap180(angle_deg: float) -> float:
    a = angle_deg % 360.0
    return a - 360.0 if a > 180.0 else a


def _normalise(yaw_deg: float, pitch_deg: float, pan_offset_deg: float) -> tuple[float, float, float]:
    """The same physical mounting written with pitch in 0..180 and yaw in 0..360. Pitch p with yaw y
    puts home where pitch 360-p with yaw y+180 does, but that frame's front points the other way, which
    is exactly a pan offset of 180 degrees, so the offset is adjusted to match."""
    pitch = pitch_deg % 360.0
    yaw = yaw_deg
    if pitch > 180.0:
        pitch = 360.0 - pitch
        yaw += 180.0
        pan_offset_deg -= 180.0
    return yaw % 360.0, pitch, _wrap180(pan_offset_deg)


def _keep_pan_offset(fitted: HeadParams, wanted_offset: float, observations: list[Observation]) -> HeadParams:
    """On a head whose pan axis is vertical (hung or upright) yaw and the pan offset are the same turn, so the
    fit may report any split of it (the marks cannot tell). Report the one that keeps the pan offset the
    head already had and puts the rest in yaw, whichever sign of yaw does that."""
    if abs(math.sin(math.radians(fitted.pitch_deg))) > 0.02:
        return fitted                                   # a tilted axis: yaw and the offset are distinct
    reference, _ = _rms(fitted, observations)
    shift = _wrap180(fitted.pan_offset_deg - wanted_offset)
    for sign in (1.0, -1.0):
        candidate = dataclasses.replace(fitted, yaw_deg=(fitted.yaw_deg + sign * shift) % 360.0,
                                        pan_offset_deg=wanted_offset)
        if _rms(candidate, observations)[0] <= reference + 0.01:
            return candidate
    return fitted


def _fit_once(params: HeadParams, observations: list[Observation], solve_position: bool,
              start: np.ndarray) -> tuple[np.ndarray, float]:
    return _levenberg_marquardt(
        lambda x: _residual_vector(x, params, observations, solve_position, start), start)


def solve(params: HeadParams, observations: list[Observation], solve_position: bool = False) -> Fit:
    """Fit the head's orientation and pan/tilt offsets (and its position when `solve_position`) to the
    observations, starting from `params`. Raises ValueError with too few marks."""
    minimum = 5 if solve_position else 3
    if len(observations) < minimum:
        raise ValueError(f"need at least {minimum} marks to solve {'the position too' if solve_position else 'the mounting'}"
                         f" (got {len(observations)})")

    before, _ = _rms(params, observations)
    warnings: list[str] = []
    if _spread_is_poor(params, observations):
        warnings.append("The marks are nearly in a line as seen from the head, so the result is uncertain. "
                        "Use marks in different directions (floor corners and something high on a wall).")

    def run(flags: tuple[bool, bool]) -> tuple[HeadParams, float]:
        base = dataclasses.replace(params, inverted_pan=flags[0], inverted_tilt=flags[1])
        best: Optional[tuple[np.ndarray, float]] = None
        start_angles = [(math.radians(params.yaw_deg), math.radians(params.pitch_deg))]
        for _round in range(2):
            for yaw0, pitch0 in start_angles:
                x0 = np.array([yaw0, pitch0, math.radians(params.pan_offset_deg), math.radians(params.tilt_offset_deg)])
                x, cost = _fit_once(base, observations, False, x0)
                if best is None or cost < best[1]:
                    best = (x, cost)
            rms_now = math.sqrt(best[1] / max(len(observations), 1)) * 180 / math.pi
            if rms_now < 1.0 or _round == 1:
                break
            # not close yet: also try the other compass quarters and the head mounted the other way up
            start_angles = [(math.radians(params.yaw_deg + k * 90.0), math.radians(p))
                            for k in range(4) for p in (params.pitch_deg, 180.0 - params.pitch_deg)]
        x = best[0]
        if solve_position:
            # the mounting first (position as entered), then everything together from that good start
            x0 = np.array([x[0], x[1], x[2], x[3], params.position.x, params.position.y, params.position.z])
            x, _cost = _fit_once(base, observations, True, x0)
        yaw, pitch, pan_off = _normalise(math.degrees(x[0]), math.degrees(x[1]), math.degrees(x[2]))
        pos = Vec3(float(x[4]), float(x[5]), float(x[6])) if solve_position else params.position
        fitted = HeadParams(pos, yaw, pitch, pan_off, _wrap180(math.degrees(x[3])), flags[0], flags[1])
        fitted = _keep_pan_offset(fitted, params.pan_offset_deg, observations)
        return fitted, _rms(fitted, observations)[0]

    current_flags = (params.inverted_pan, params.inverted_tilt)
    best_params, best_rms = run(current_flags)
    changed = False
    if best_rms > 1.5:   # the current invert flags do not explain the marks: try the others
        for flags in itertools.product((False, True), repeat=2):
            if flags == current_flags:
                continue
            candidate, rms = run(flags)
            if rms < best_rms - 0.5:
                best_params, best_rms, changed = candidate, rms, True
    if changed:
        warnings.append("The invert pan / invert tilt flags that fit best differ from the ones set; they are "
                        "included in the result.")
    rms, residuals = _rms(best_params, observations)
    if rms > 2.0:
        warnings.append(f"The marks still miss by {rms:.1f} deg on average. Check that the beam was centred on each "
                        "mark, that the marks' positions are right (room size), and the pan/tilt ranges in the profile.")
    if solve_position:
        moved = math.dist((best_params.position.x, best_params.position.y, best_params.position.z),
                          (params.position.x, params.position.y, params.position.z))
        if moved > 1.0:
            warnings.append(f"The fitted position is {moved:.2f} m from the one entered. Check it before applying.")
    return Fit(best_params, rms, before, residuals, solve_position, changed, warnings)


def _spread_is_poor(params: HeadParams, observations: list[Observation]) -> bool:
    """True when the marks, seen from the head, lie close to one plane through the head (or one line):
    then some parameters cannot be told apart."""
    pos = np.array([params.position.x, params.position.y, params.position.z])
    dirs = []
    for o in observations:
        d = np.array([o.point.x, o.point.y, o.point.z]) - pos
        n = np.linalg.norm(d)
        if n > 1e-9:
            dirs.append(d / n)
    if len(dirs) < 3:
        return True
    singular = np.linalg.svd(np.array(dirs), compute_uv=False)
    return bool(singular[2] / math.sqrt(len(dirs)) < 0.12)   # the directions barely leave a plane
