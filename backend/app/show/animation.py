"""Animation/timeline engine.

Keyframes reference room-space target points plus color/dimmer/strobe --
not raw channel values -- so the IK core is what turns a keyframed shape
into pan/tilt DMX every tick. This is what makes animated shapes come out
geometrically correct instead of the ellipse/faceting distortion you get
keyframing raw pan/tilt values directly (FreeStyler's FX generator
problem called out in the design doc).

A track targets one fixture or group id and has its own keyframe list;
an Animation is just a named bundle of tracks that play back together on
a shared clock.
"""

from __future__ import annotations

import dataclasses
import math
import threading
import time
from typing import Optional

from ..room.model import AnimationPoint, Vec3
from .engine import ShowEngine


@dataclasses.dataclass
class Keyframe:
    time_s: float
    # A keyframe aims at EITHER a named, reusable AnimationPoint (point_id,
    # resolved against Room.animation_points at sample time so moving the
    # point updates every animation referencing it) OR a raw embedded
    # target_point -- point_id wins when both are set. Several fixtures can
    # share the same point library but visit the points in a different
    # order/timing by just building their own track's keyframe list.
    point_id: Optional[str] = None
    target_point: Optional[Vec3] = None
    color: Optional[tuple[int, int, int]] = None
    dimmer: Optional[int] = None
    strobe: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "time_s": self.time_s,
            "point_id": self.point_id,
            "target_point": dataclasses.asdict(self.target_point) if self.target_point else None,
            "color": list(self.color) if self.color else None,
            "dimmer": self.dimmer,
            "strobe": self.strobe,
        }

    @staticmethod
    def from_dict(d: dict) -> "Keyframe":
        return Keyframe(
            time_s=d["time_s"],
            point_id=d.get("point_id"),
            target_point=Vec3(**d["target_point"]) if d.get("target_point") else None,
            color=tuple(d["color"]) if d.get("color") else None,
            dimmer=d.get("dimmer"),
            strobe=d.get("strobe"),
        )


@dataclasses.dataclass
class AnimationTrack:
    target_id: str  # fixture id or group id
    keyframes: list[Keyframe] = dataclasses.field(default_factory=list)
    # Shifts this track's own playback clock within the shared animation --
    # lets several fixtures run the SAME keyframe pattern staggered ("staged")
    # instead of in lockstep ("sync", the default at offset 0), without
    # duplicating/retiming the keyframes themselves.
    time_offset_s: float = 0.0

    def sorted_keyframes(self) -> list[Keyframe]:
        return sorted(self.keyframes, key=lambda k: k.time_s)

    def duration_s(self) -> float:
        kfs = self.keyframes
        return max((k.time_s for k in kfs), default=0.0)

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "keyframes": [k.to_dict() for k in self.keyframes],
            "time_offset_s": self.time_offset_s,
        }

    @staticmethod
    def from_dict(d: dict) -> "AnimationTrack":
        return AnimationTrack(
            target_id=d["target_id"],
            keyframes=[Keyframe.from_dict(k) for k in d.get("keyframes", [])],
            time_offset_s=d.get("time_offset_s", 0.0),
        )


@dataclasses.dataclass
class Animation:
    id: str
    name: str
    tracks: list[AnimationTrack] = dataclasses.field(default_factory=list)
    loop: bool = True

    def duration_s(self) -> float:
        return max((t.duration_s() for t in self.tracks), default=0.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tracks": [t.to_dict() for t in self.tracks],
            "loop": self.loop,
        }

    @staticmethod
    def from_dict(d: dict) -> "Animation":
        return Animation(
            id=d["id"],
            name=d["name"],
            tracks=[AnimationTrack.from_dict(t) for t in d.get("tracks", [])],
            loop=d.get("loop", True),
        )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_vec3(a: Vec3, b: Vec3, t: float) -> Vec3:
    return Vec3(_lerp(a.x, b.x, t), _lerp(a.y, b.y, t), _lerp(a.z, b.z, t))


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(_lerp(a[i], b[i], t)) for i in range(3))  # type: ignore[return-value]


def _keyframe_point(keyframe: Keyframe, points: dict[str, AnimationPoint]) -> Optional[Vec3]:
    if keyframe.point_id is not None:
        point = points.get(keyframe.point_id)
        return point.position if point else None
    return keyframe.target_point


def sample_track(
    track: AnimationTrack, time_s: float, points: Optional[dict[str, AnimationPoint]] = None
) -> dict:
    """Interpolate a track's keyframes at time_s -> dict of resolved values
    (target_point / color / dimmer / strobe), each key omitted if the
    track has no keyframes carrying that field. `points` resolves any
    keyframe referencing a shared AnimationPoint by id."""
    points = points or {}
    kfs = track.sorted_keyframes()
    if not kfs:
        return {}
    if time_s <= kfs[0].time_s:
        before, after, t = kfs[0], kfs[0], 0.0
    elif time_s >= kfs[-1].time_s:
        before, after, t = kfs[-1], kfs[-1], 0.0
    else:
        before, after = kfs[0], kfs[-1]
        for i in range(len(kfs) - 1):
            if kfs[i].time_s <= time_s <= kfs[i + 1].time_s:
                before, after = kfs[i], kfs[i + 1]
                break
        span = after.time_s - before.time_s
        t = (time_s - before.time_s) / span if span > 0 else 0.0

    result: dict = {}
    before_point, after_point = _keyframe_point(before, points), _keyframe_point(after, points)
    if before_point is not None and after_point is not None:
        result["target_point"] = _lerp_vec3(before_point, after_point, t)
    elif before_point is not None:
        result["target_point"] = before_point

    if before.color is not None and after.color is not None:
        result["color"] = _lerp_color(before.color, after.color, t)
    elif before.color is not None:
        result["color"] = before.color

    if before.dimmer is not None and after.dimmer is not None:
        result["dimmer"] = round(_lerp(before.dimmer, after.dimmer, t))
    elif before.dimmer is not None:
        result["dimmer"] = before.dimmer

    if before.strobe is not None and after.strobe is not None:
        result["strobe"] = round(_lerp(before.strobe, after.strobe, t))
    elif before.strobe is not None:
        result["strobe"] = before.strobe

    return result


class AnimationPlayer:
    """Plays one Animation against a ShowEngine on a background thread."""

    def __init__(self, engine: ShowEngine, animation: Animation, tick_hz: float = 30.0):
        self.engine = engine
        self.animation = animation
        self.tick_hz = tick_hz
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._start_time = 0.0
        self.playing = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._start_time = time.monotonic()
        self.playing = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.playing = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        duration = self.animation.duration_s()
        period = 1.0 / self.tick_hz
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._start_time
            if duration <= 0:
                break
            if self.animation.loop:
                t = elapsed % duration
            else:
                t = min(elapsed, duration)
                if elapsed > duration:
                    self.playing = False
                    break
            self._apply_frame(t)
            time.sleep(period)

    def _apply_frame(self, time_s: float) -> None:
        duration = self.animation.duration_s()
        points = self.engine.room.animation_points
        for track in self.animation.tracks:
            track_time = time_s - track.time_offset_s
            if self.animation.loop and duration > 0:
                track_time %= duration
            values = sample_track(track, track_time, points)
            if "target_point" in values:
                self.engine.aim_at_point(track.target_id, values["target_point"])
            if "color" in values:
                r, g, b = values["color"]
                self.engine.set_color(track.target_id, r, g, b)
            if "dimmer" in values:
                self.engine.set_dimmer(track.target_id, values["dimmer"])
            if "strobe" in values:
                self.engine.set_strobe(track.target_id, values["strobe"])


# ---------------------------------------------------------------- patterns
#
# The "basic" freestyler-style animation: a raw pan/tilt movement shape,
# computed directly from elapsed time with no room-space math and no IK --
# just a formula producing pan/tilt DMX values (0-255) each tick, same as
# a FreeStyler "FX generator" preset. No color; that's a separate concern
# added later.

PATTERN_SHAPES = ("circle", "figure8", "linear", "square")


def _clamp_dmx(value: float) -> int:
    return max(0, min(255, round(value)))


def pattern_pan_tilt(
    shape: str, elapsed_s: float, speed_hz: float,
    pan_center: int, tilt_center: int, pan_size: int, tilt_size: int, phase_deg: float = 0.0,
) -> tuple[int, int]:
    """Pure function (unit-testable): elapsed time + pattern params -> raw
    (pan, tilt) DMX values. `speed_hz` is cycles/second; `pan_size`/
    `tilt_size` are the max swing off-center in DMX units."""
    phase = math.radians(phase_deg)
    theta = 2 * math.pi * speed_hz * elapsed_s + phase

    if shape == "figure8":
        pan = pan_center + pan_size * math.sin(theta)
        tilt = tilt_center + tilt_size * math.sin(2 * theta) / 2
    elif shape == "linear":
        pan = pan_center + pan_size * math.sin(theta)
        tilt = tilt_center
    elif shape == "square":
        quarter = int((theta / (math.pi / 2)) % 4)
        pan = pan_center + pan_size * (1 if quarter in (0, 1) else -1)
        tilt = tilt_center + tilt_size * (1 if quarter in (1, 2) else -1)
    else:  # "circle" (default/fallback)
        pan = pan_center + pan_size * math.sin(theta)
        tilt = tilt_center + tilt_size * math.cos(theta)

    return _clamp_dmx(pan), _clamp_dmx(tilt)


@dataclasses.dataclass
class PatternAnimation:
    id: str
    name: str
    target_id: str  # fixture id or group id -- every resolved fixture gets the same values
    shape: str = "circle"
    speed_hz: float = 0.2
    pan_center: int = 128
    tilt_center: int = 128
    pan_size: int = 80
    tilt_size: int = 80
    phase_deg: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PatternAnimation":
        return PatternAnimation(**{k: v for k, v in d.items() if k in PatternAnimation.__dataclass_fields__})


class PatternPlayer:
    """Plays one PatternAnimation against a ShowEngine on a background
    thread -- mirrors AnimationPlayer's structure but writes raw pan/tilt
    role values directly instead of going through the IK core."""

    def __init__(self, engine: ShowEngine, pattern: PatternAnimation, tick_hz: float = 30.0):
        self.engine = engine
        self.pattern = pattern
        self.tick_hz = tick_hz
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._start_time = 0.0
        self.playing = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._start_time = time.monotonic()
        self.playing = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.playing = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        period = 1.0 / self.tick_hz
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._start_time
            pan, tilt = pattern_pan_tilt(
                self.pattern.shape, elapsed, self.pattern.speed_hz,
                self.pattern.pan_center, self.pattern.tilt_center,
                self.pattern.pan_size, self.pattern.tilt_size, self.pattern.phase_deg,
            )
            for fixture_id in self.engine.resolve_fixture_ids(self.pattern.target_id):
                self.engine.set_role_value(fixture_id, "pan", pan)
                self.engine.set_role_value(fixture_id, "tilt", tilt)
            time.sleep(period)
