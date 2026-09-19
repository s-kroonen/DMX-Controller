"""Sound-to-light functions (freestyler-style): audio features drive lights.

Each *function* is a configured instance -- a type (`vu_dimmer`, `beat_color`,
...), the fixtures/groups it drives, on/off, and a parameter dict edited from
the UI. A function runs as a small stateful *runtime* that the SoundEngine
ticks ~40x a second with the latest audio features, calling the same
ShowEngine methods the sliders use (so profiles' role ranges, shared
dimmer/strobe channels etc. all still apply).

Percent parameters are 0-100 like everywhere else in the UI; the engine maps
them onto each fixture's real DMX values.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Any, Callable, Optional

from .analyzer import AudioFeatures


def _logical(pct: float) -> int:
    return max(0, min(255, round(pct * 255 / 100)))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return 255, 255, 255
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclasses.dataclass
class StepContext:
    engine: Any                 # ShowEngine
    features: AudioFeatures
    beats: int                  # new beats since the previous tick
    beat_index: int             # running beat counter (for chases)
    dt: float                   # seconds since the previous tick
    now: float                  # monotonic seconds


@dataclasses.dataclass
class SoundFunction:
    id: str
    type: str
    targets: list[str] = dataclasses.field(default_factory=list)
    enabled: bool = True
    params: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SoundFunction":
        return SoundFunction(id=d["id"], type=d["type"], targets=list(d.get("targets", [])),
                             enabled=bool(d.get("enabled", True)), params=dict(d.get("params", {})))


# -- parameter schema (the UI renders forms from this) ---------------------

def num(key, label, default, lo, hi, step=1, unit=""):
    return {"key": key, "label": label, "kind": "number", "default": default,
            "min": lo, "max": hi, "step": step, "unit": unit}


def choice(key, label, default, options):
    return {"key": key, "label": label, "kind": "choice", "default": default, "options": options}


EVERY = choice("every", "Every", 1, [1, 2, 4, 8])
SOURCE = choice("source", "Follows", "level", ["level", "bass", "mid", "high"])
DEFAULT_PALETTE = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff"]


# -- runtimes ---------------------------------------------------------------

class _Runtime:
    def __init__(self):
        self._last_sent: Any = None

    def step(self, fn: SoundFunction, ctx: StepContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def release(self, fn: SoundFunction, engine: Any) -> None:
        """Called when the function is disabled/removed (optional tidy-up)."""

    def _send(self, key: Any, send: Callable[[], None]) -> None:
        """Only touch the engine when the value actually changed."""
        if key != self._last_sent:
            self._last_sent = key
            send()


def _p(fn: SoundFunction, key: str, default: Any) -> Any:
    return fn.params.get(key, default)


class VuDimmer(_Runtime):
    """Dimmer follows the loudness (or one band): a VU meter for the lights."""

    def __init__(self):
        super().__init__()
        self.value = 0.0

    def step(self, fn, ctx):
        raw = getattr(ctx.features, _p(fn, "source", "level"))
        target = max(0.0, min(1.0, raw * float(_p(fn, "gain", 1.0))))
        rate = (float(_p(fn, "attack_ms", 30)) if target > self.value
                else float(_p(fn, "release_ms", 250))) / 1000.0
        k = 1.0 if rate <= 0 else min(1.0, ctx.dt / rate)
        self.value += (target - self.value) * k
        lo, hi = float(_p(fn, "min_pct", 0)), float(_p(fn, "max_pct", 100))
        pct = lo + (hi - lo) * self.value
        level = _logical(pct)
        self._send(level, lambda: ctx.engine.set_dimmer(fn.targets, level))


class BeatFlash(_Runtime):
    """Snap to full on every beat, then fall back to the base level."""

    def __init__(self):
        super().__init__()
        self.pulse = 0.0

    def step(self, fn, ctx):
        every = max(1, int(_p(fn, "every", 1)))
        if ctx.beats and ctx.beat_index % every == 0:
            self.pulse = 1.0
        decay = max(20.0, float(_p(fn, "decay_ms", 250))) / 1000.0
        self.pulse = max(0.0, self.pulse - ctx.dt / decay)
        lo, hi = float(_p(fn, "min_pct", 5)), float(_p(fn, "max_pct", 100))
        level = _logical(lo + (hi - lo) * self.pulse)
        self._send(level, lambda: ctx.engine.set_dimmer(fn.targets, level))


class BeatColor(_Runtime):
    """Step through a palette on the beat."""

    def __init__(self):
        super().__init__()
        self.index = -1

    def step(self, fn, ctx):
        palette = _p(fn, "palette", DEFAULT_PALETTE) or DEFAULT_PALETTE
        every = max(1, int(_p(fn, "every", 1)))
        if ctx.beats and ctx.beat_index % every == 0:
            if _p(fn, "mode", "step") == "random" and len(palette) > 1:
                choices = [i for i in range(len(palette)) if i != self.index]
                self.index = random.choice(choices)
            else:
                self.index = (self.index + 1) % len(palette)
        if self.index < 0:
            return
        r, g, b = _hex_rgb(palette[self.index % len(palette)])
        self._send((r, g, b, self.index), lambda: ctx.engine.set_color(fn.targets, r, g, b))


class ColorOrgan(_Runtime):
    """Bass -> red, mids -> green, highs -> blue."""

    def __init__(self):
        super().__init__()
        self.smoothed = [0.0, 0.0, 0.0]

    def step(self, fn, ctx):
        f = ctx.features
        gain = float(_p(fn, "gain", 1.0))
        smooth = max(10.0, float(_p(fn, "smooth_ms", 120))) / 1000.0
        k = min(1.0, ctx.dt / smooth)
        for i, v in enumerate((f.bass, f.mid, f.high)):
            self.smoothed[i] += (min(1.0, v * gain) - self.smoothed[i]) * k
        r, g, b = (round(255 * v) for v in self.smoothed)
        self._send((r, g, b), lambda: ctx.engine.set_color(fn.targets, r, g, b))


class BeatStrobe(_Runtime):
    """A burst of strobe on each beat, then back to open."""

    def __init__(self):
        super().__init__()
        self.until = 0.0
        self.active = False

    def step(self, fn, ctx):
        every = max(1, int(_p(fn, "every", 4)))
        if ctx.beats and ctx.beat_index % every == 0:
            self.until = ctx.now + float(_p(fn, "burst_ms", 250)) / 1000.0
        if ctx.now < self.until:
            if not self.active:
                self.active = True
                speed = _logical(float(_p(fn, "speed_pct", 80)))
                ctx.engine.set_strobe(fn.targets, speed)
        elif self.active:
            self.active = False
            ctx.engine.set_shutter(fn.targets, False)   # open: light on, no strobe

    def release(self, fn, engine):
        if self.active:
            self.active = False
            engine.set_shutter(fn.targets, False)


class BeatMovement(_Runtime):
    """Step through pan/tilt positions on the beat (raw 0-255 slider values)."""

    DEFAULT_POSITIONS = [{"pan": 96, "tilt": 110}, {"pan": 160, "tilt": 110},
                         {"pan": 160, "tilt": 150}, {"pan": 96, "tilt": 150}]

    def __init__(self):
        super().__init__()
        self.index = -1

    def step(self, fn, ctx):
        positions = _p(fn, "positions", self.DEFAULT_POSITIONS) or self.DEFAULT_POSITIONS
        every = max(1, int(_p(fn, "every", 2)))
        if ctx.beats and ctx.beat_index % every == 0:
            self.index = (self.index + 1) % len(positions)
        if self.index < 0:
            return
        pos = positions[self.index % len(positions)]
        pan, tilt = int(pos["pan"]), int(pos["tilt"])
        self._send((pan, tilt), lambda: ctx.engine.set_raw_pan_tilt(fn.targets, pan, tilt))


# category: "color" = anything that drives light output (dimmer, strobe, color);
# "motion" = pan/tilt. The Sound window's mode (Color / Motion / Both) picks which
# categories react to sound, so e.g. an animation can own movement while sound
# owns the colors.
FUNCTION_TYPES: dict[str, dict] = {
    "vu_dimmer": {
        "label": "VU dimmer", "runtime": VuDimmer, "category": "color",
        "description": "Dimmer follows the loudness (or bass/mid/high).",
        "params": [SOURCE, num("min_pct", "Min", 0, 0, 100, 1, "%"), num("max_pct", "Max", 100, 0, 100, 1, "%"),
                   num("gain", "Gain", 1.0, 0.2, 4, 0.1), num("attack_ms", "Attack", 30, 0, 500, 5, "ms"),
                   num("release_ms", "Release", 250, 20, 2000, 10, "ms")],
    },
    "beat_flash": {
        "label": "Beat flash", "runtime": BeatFlash, "category": "color",
        "description": "Flash to full on the beat, then fade back.",
        "params": [EVERY, num("min_pct", "Base", 5, 0, 100, 1, "%"), num("max_pct", "Peak", 100, 0, 100, 1, "%"),
                   num("decay_ms", "Decay", 250, 20, 2000, 10, "ms")],
    },
    "beat_color": {
        "label": "Beat color chase", "runtime": BeatColor, "category": "color",
        "description": "Change color on the beat.",
        "params": [EVERY, choice("mode", "Order", "step", ["step", "random"]),
                   {"key": "palette", "label": "Colors", "kind": "palette", "default": DEFAULT_PALETTE}],
    },
    "color_organ": {
        "label": "Color organ", "runtime": ColorOrgan, "category": "color",
        "description": "Bass = red, mids = green, highs = blue.",
        "params": [num("gain", "Gain", 1.0, 0.2, 4, 0.1), num("smooth_ms", "Smooth", 120, 10, 1000, 10, "ms")],
    },
    "beat_strobe": {
        "label": "Beat strobe", "runtime": BeatStrobe, "category": "color",
        "description": "A short strobe burst on the beat, then back to open.",
        "params": [choice("every", "Every", 4, [1, 2, 4, 8]), num("burst_ms", "Burst", 250, 50, 2000, 10, "ms"),
                   num("speed_pct", "Speed", 80, 1, 100, 1, "%")],
    },
    "beat_movement": {
        "label": "Beat movement", "runtime": BeatMovement, "category": "motion",
        "description": "Step through pan/tilt positions on the beat.",
        "params": [choice("every", "Every", 2, [1, 2, 4, 8]),
                   {"key": "positions", "label": "Positions", "kind": "positions",
                    "default": BeatMovement.DEFAULT_POSITIONS}],
    },
}


def function_types_for_ui() -> list[dict]:
    return [{"type": t, "label": v["label"], "category": v["category"],
             "description": v["description"], "params": v["params"]}
            for t, v in FUNCTION_TYPES.items()]


SOUND_MODES = ("off", "color", "motion", "both")


def category_active(mode: str, fn_type: str) -> bool:
    """Does this sound mode let a function of this type react to sound?"""
    if mode == "both":
        return True
    return mode != "off" and FUNCTION_TYPES[fn_type]["category"] == mode


def default_params(fn_type: str) -> dict:
    return {p["key"]: (list(p["default"]) if isinstance(p["default"], list) else p["default"])
            for p in FUNCTION_TYPES[fn_type]["params"]}


def make_runtime(fn_type: str) -> _Runtime:
    return FUNCTION_TYPES[fn_type]["runtime"]()


def validate(fn: SoundFunction) -> Optional[str]:
    if fn.type not in FUNCTION_TYPES:
        return f"unknown sound function type {fn.type!r}"
    return None
