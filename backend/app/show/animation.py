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
import threading
import time
from typing import Optional

from ..room.model import Vec3
from .engine import ShowEngine


@dataclasses.dataclass
class Keyframe:
    time_s: float
    target_point: Optional[Vec3] = None
    color: Optional[tuple[int, int, int]] = None
    dimmer: Optional[int] = None
    strobe: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "time_s": self.time_s,
            "target_point": dataclasses.asdict(self.target_point) if self.target_point else None,
            "color": list(self.color) if self.color else None,
            "dimmer": self.dimmer,
            "strobe": self.strobe,
        }

    @staticmethod
    def from_dict(d: dict) -> "Keyframe":
        return Keyframe(
            time_s=d["time_s"],
            target_point=Vec3(**d["target_point"]) if d.get("target_point") else None,
            color=tuple(d["color"]) if d.get("color") else None,
            dimmer=d.get("dimmer"),
            strobe=d.get("strobe"),
        )


@dataclasses.dataclass
class AnimationTrack:
    target_id: str  # fixture id or group id
    keyframes: list[Keyframe] = dataclasses.field(default_factory=list)

    def sorted_keyframes(self) -> list[Keyframe]:
        return sorted(self.keyframes, key=lambda k: k.time_s)

    def duration_s(self) -> float:
        kfs = self.keyframes
        return max((k.time_s for k in kfs), default=0.0)

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "keyframes": [k.to_dict() for k in self.keyframes]}

    @staticmethod
    def from_dict(d: dict) -> "AnimationTrack":
        return AnimationTrack(
            target_id=d["target_id"],
            keyframes=[Keyframe.from_dict(k) for k in d.get("keyframes", [])],
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


def sample_track(track: AnimationTrack, time_s: float) -> dict:
    """Interpolate a track's keyframes at time_s -> dict of resolved values
    (target_point / color / dimmer / strobe), each key omitted if the
    track has no keyframes carrying that field."""
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
    if before.target_point is not None and after.target_point is not None:
        result["target_point"] = _lerp_vec3(before.target_point, after.target_point, t)
    elif before.target_point is not None:
        result["target_point"] = before.target_point

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
        for track in self.animation.tracks:
            values = sample_track(track, time_s)
            if "target_point" in values:
                self.engine.aim_at_point(track.target_id, values["target_point"])
            if "color" in values:
                r, g, b = values["color"]
                self.engine.set_color(track.target_id, r, g, b)
            if "dimmer" in values:
                self.engine.set_dimmer(track.target_id, values["dimmer"])
            if "strobe" in values:
                self.engine.set_strobe(track.target_id, values["strobe"])
