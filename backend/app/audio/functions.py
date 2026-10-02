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
    # The fixture ids this particular function is allowed to act on this tick (see
    # SoundService.tick's priority claiming); None = no restriction (every target's fixtures are
    # free), used by tests that call a runtime directly. Mutated by the service once per function
    # per tick -- functions are stepped strictly sequentially within one tick, so this is safe.
    allowed_ids: Optional[frozenset] = None


DEFAULT_DESYNC = {"granularity": "off", "mode": "beat", "amount": 1}


@dataclasses.dataclass
class SoundFunction:
    id: str
    type: str
    targets: list[str] = dataclasses.field(default_factory=list)
    enabled: bool = True
    params: dict = dataclasses.field(default_factory=dict)
    # limit to these zones of the target fixtures that have zones (a light bar's "spot1",
    # "derby2", ...); empty = every zone. Lights without zones are driven as normal whatever is
    # chosen. Only functions with `uses_zones` look at it.
    zones: list[str] = dataclasses.field(default_factory=list)
    # Spread a chase/chase-like function's own color sequence across time instead of showing the
    # same color everywhere at once -- NOT a color of its own per light (only functions with
    # `uses_desync` look at it): {"granularity": "off"|"group"|"light", "mode": "beat"|"timer",
    # "amount": N}. "group" -> each entry of `targets` (a picked group, or an individually picked
    # fixture) is its own unit and stays internally in sync; "light" -> every individual light/zone
    # is its own unit, even within one group. Unit 0 (the first target/light) always shows the
    # function's live output; unit i shows what it looked like `i * amount` beats ago (mode
    # "beat") or `i * amount` milliseconds ago (mode "timer", independent of BPM). Same palette,
    # same order, same "every N beats" -- just replayed later for each unit.
    desync: dict = dataclasses.field(default_factory=lambda: dict(DEFAULT_DESYNC))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SoundFunction":
        return SoundFunction(id=d["id"], type=d["type"], targets=list(d.get("targets", [])),
                             enabled=bool(d.get("enabled", True)), params=dict(d.get("params", {})),
                             zones=list(d.get("zones", [])),
                             desync={**DEFAULT_DESYNC, **(d.get("desync") or {})})


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


def _zones(fn: SoundFunction) -> Optional[list[str]]:
    """The zone filter to hand the engine: None (every zone) when none are chosen."""
    return list(fn.zones) or None


def _resolved_allowed(fn: SoundFunction, ctx: "StepContext") -> list[str]:
    """fn.targets resolved to fixture ids, filtered to ctx.allowed_ids (the priority claim for
    this tick, see StepContext) when one is set."""
    ids = ctx.engine.resolve_fixture_ids(fn.targets)
    if ctx.allowed_ids is None:
        return ids
    return [fid for fid in ids if fid in ctx.allowed_ids]


def _apply(fn: SoundFunction, ctx: "StepContext", send: Callable[[list[str], Optional[list[str]]], None]) -> None:
    """Run `send(fixture_ids, zones)` for the function's lights it is allowed to act on this tick.
    The zone choice narrows the lights that have zones; a light without zones (a moving head next
    to a light bar in one group) has nothing to narrow and is driven as normal, so it is not
    silently dropped from the effect."""
    zones = _zones(fn)
    allowed = _resolved_allowed(fn, ctx)
    if zones is None:
        send(allowed, None)
        return
    zoned, plain = [], []
    for fid in allowed:
        (zoned if ctx.engine.profile_for(fid).zones else plain).append(fid)
    if zoned:
        send(zoned, zones)
    if plain:
        send(plain, None)


# -- desync: replaying a chase's own output, delayed, per group or per light ------------------------
#
# A chase (BeatColor, ZoneChase) computes ONE shared, evolving value each tick (a color, or a
# zone_chase "frame": which cells are lit and in what color) from the operator's palette/every/
# pattern settings -- unchanged, still computed exactly as before. Desync does not add a color or
# a pattern of its own: it records that shared value's history (only when it actually changes) and,
# for every "unit" after the first, shows what the SAME value looked like some beats or
# milliseconds ago instead of the live value -- so several units can be doing different parts of
# the identical sequence at once, purely by being offset in time.

@dataclasses.dataclass
class _HistoryEntry:
    beat_index: int
    at: float
    value: Any


class _History:
    def __init__(self, max_len: int = 512):
        self._entries: list[_HistoryEntry] = []
        self._max_len = max_len

    def record(self, beat_index: int, at: float, value: Any) -> None:
        if self._entries and self._entries[-1].value == value:
            return
        self._entries.append(_HistoryEntry(beat_index, at, value))
        if len(self._entries) > self._max_len:
            del self._entries[: len(self._entries) - self._max_len]

    def _at(self, key: Callable[["_HistoryEntry"], float], target: float) -> Any:
        if not self._entries:
            return None
        result = self._entries[0].value
        for entry in self._entries:
            if key(entry) > target:
                break
            result = entry.value
        return result

    def at_beat(self, beat_index: float) -> Any:
        return self._at(lambda e: e.beat_index, beat_index)

    def at_time(self, at: float) -> Any:
        return self._at(lambda e: e.at, at)


def _desync_units(fn: SoundFunction, ctx: "StepContext", cells: list,
                  cell_fixture: Callable[[Any], str]) -> list[list]:
    """Partition `cells` (opaque cell keys -- a fixture id for BeatColor, a zone/fixture cell for
    ZoneChase) into desync units, in the order they should be delayed: unit 0 is always shown live,
    unit i shows the function's output as of `i * amount` ago. `cell_fixture(cell)` returns the
    fixture id a cell belongs to.
    granularity "off" -> a single unit holding every cell (today's shared behaviour, no delay).
    "light" -> one unit per cell, so every zone/fixture is its own desync unit.
    "group" -> one unit per entry of `fn.targets`, in the order they were picked (so a whole picked
    group, or an individually picked fixture, moves together); a fixture already claimed by an
    earlier target isn't repeated in a later one. A cell whose fixture belongs to none of the named
    targets (shouldn't normally happen) becomes one trailing unit of its own."""
    granularity = (fn.desync or {}).get("granularity", "off")
    if granularity == "light":
        return [[c] for c in cells]
    if granularity == "group":
        units: list[list] = []
        used_fixtures: set[str] = set()
        for target_id in fn.targets:
            try:
                fixture_ids = set(ctx.engine.resolve_fixture_ids([target_id])) - used_fixtures
            except KeyError:
                continue
            if not fixture_ids:
                continue
            unit_cells = [c for c in cells if cell_fixture(c) in fixture_ids]
            if unit_cells:
                units.append(unit_cells)
            used_fixtures |= fixture_ids
        covered = {c for unit in units for c in unit}
        leftover = [c for c in cells if c not in covered]
        if leftover:
            units.append(leftover)
        return units
    return [cells] if cells else []


def _unit_delay(fn: SoundFunction, unit_index: int) -> tuple[str, float]:
    """("beat", N) or ("time", seconds) to look this unit's value up at, from a History. Unit 0 is
    always live (handled by the caller without a lookup)."""
    mode = (fn.desync or {}).get("mode", "beat")
    amount = max(0.0, float((fn.desync or {}).get("amount", 1) or 0))
    if mode == "timer":
        return "time", unit_index * amount / 1000.0
    return "beat", unit_index * amount


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
        self._send(level, lambda: _apply(fn, ctx, lambda ids, z: ctx.engine.set_dimmer(ids, level, zones=z)))


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
        self._send(level, lambda: _apply(fn, ctx, lambda ids, z: ctx.engine.set_dimmer(ids, level, zones=z)))


class BeatColor(_Runtime):
    """Step through a palette on the beat. With desync off (the default) every target shows the
    same color at once, unchanged; with it on, later units replay this same evolving color as it
    looked some beats/milliseconds ago instead (see _desync_units()/_unit_delay())."""

    def __init__(self):
        super().__init__()
        self.index = -1
        self.history = _History()

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
        rgb = _hex_rgb(palette[self.index % len(palette)])
        self.history.record(ctx.beat_index, ctx.now, rgb)

        granularity = (fn.desync or {}).get("granularity", "off")
        if granularity == "off":
            self._send((rgb, self.index),
                      lambda: _apply(fn, ctx, lambda ids, z: ctx.engine.set_color(ids, *rgb, zones=z)))
            return

        zones = _zones(fn)
        units = _desync_units(fn, ctx, _resolved_allowed(fn, ctx), lambda fid: fid)
        for i, unit in enumerate(units):
            if i == 0:
                color = rgb
            else:
                mode, delay = _unit_delay(fn, i)
                color = (self.history.at_beat(ctx.beat_index - delay) if mode == "beat"
                        else self.history.at_time(ctx.now - delay)) or rgb
            if zones is None:
                ctx.engine.set_color(unit, *color)
                continue
            zoned = [fid for fid in unit if ctx.engine.profile_for(fid).zones]
            plain = [fid for fid in unit if fid not in zoned]
            if zoned:
                ctx.engine.set_color(zoned, *color, zones=zones)
            if plain:
                ctx.engine.set_color(plain, *color)


class ZoneChase(_Runtime):
    """Step through a fixture's zones on the beat -- a light bar's spot 1 -> spot 2 -> derby 1 ->
    derby 2, ping-pong, a checkerboard, or random. A target with no zones (a plain moving head or
    PAR alongside a light bar) is not left out: it becomes one more cell of its own in the same
    chase, driven as a whole light instead of by zone -- so a mixed selection still chases as one
    sequence rather than silently ignoring the fixtures that have nothing to chase through.

    Desync (fn.desync) replays this same sequence with a per-unit delay instead of showing every
    light the identical moment of it at once -- see _desync_units()/_unit_delay(). With it off (the
    default), two fixtures sharing a zone id (identical light bars) still move in lockstep on it,
    exactly as before; with it on, zone ids are no longer merged across fixtures (each fixture's
    zone becomes its own cell), since the whole point is letting them differ."""

    def __init__(self):
        super().__init__()
        self.step_no = -1
        self.last_lit: Optional[int] = None
        self.frame: Optional[tuple[frozenset, tuple[int, int, int]]] = None
        self.sent: dict[tuple, tuple] = {}
        self.history = _History()

    def _lit(self, pattern: str, n: int) -> set:
        s = self.step_no
        if pattern == "ping_pong" and n > 1:
            cycle = 2 * n - 2
            i = s % cycle
            return {i if i < n else cycle - i}
        if pattern == "alternate":
            return {i for i in range(n) if i % 2 == s % 2}
        if pattern == "random" and n > 1:
            self.last_lit = random.choice([i for i in range(n) if i != self.last_lit])
            return {self.last_lit}
        return {s % n}

    def _cells(self, fn: SoundFunction, ctx: "StepContext", merge_zone_ids: bool) -> list[tuple]:
        """merge_zone_ids (desync off): [("zone", zone_id), ...] then [("fixture", fixture_id), ...]
        -- several fixtures sharing a zone id chase in lockstep on it, as one cell, same as always.
        Otherwise (desync on): [("zone", zone_id, fixture_id), ...] then the same fixture cells --
        each light's zone is independent, since desync needs to address it on its own."""
        allowed = ctx.allowed_ids
        zone_cells: list[tuple] = []
        seen_zone_ids: list[str] = []
        fixture_cells: list[tuple] = []
        for fid in ctx.engine.resolve_fixture_ids(fn.targets):
            if allowed is not None and fid not in allowed:
                continue
            profile = ctx.engine.profile_for(fid)
            if profile.zones:
                for z in profile.zones:
                    if fn.zones and z.id not in fn.zones:
                        continue
                    if merge_zone_ids:
                        if z.id not in seen_zone_ids:
                            seen_zone_ids.append(z.id)
                            zone_cells.append(("zone", z.id))
                    else:
                        zone_cells.append(("zone", z.id, fid))
            else:
                fixture_cells.append(("fixture", fid))
        return zone_cells + fixture_cells

    def step(self, fn, ctx):
        granularity = (fn.desync or {}).get("granularity", "off")
        cells = self._cells(fn, ctx, merge_zone_ids=(granularity == "off"))
        if not cells:
            return
        every = max(1, int(_p(fn, "every", 1)))
        if ctx.beats and ctx.beat_index % every == 0:
            self.step_no += 1
            n = len(cells)
            palette = _p(fn, "palette", DEFAULT_PALETTE) or DEFAULT_PALETTE
            color_mode = _p(fn, "color_mode", "per_pass")
            if color_mode == "per_step":
                idx = self.step_no % len(palette)
            elif color_mode == "per_pass":
                idx = (self.step_no // n) % len(palette)
            else:
                idx = 0
            self.frame = (frozenset(self._lit(_p(fn, "pattern", "sequence"), n)), _hex_rgb(palette[idx]))
        if self.frame is None:
            return
        self.history.record(ctx.beat_index, ctx.now, self.frame)

        if granularity == "off":
            self._write_frame(cells, self.frame, fn, ctx)
            return

        units = _desync_units(fn, ctx, cells, lambda cell: cell[-1])
        index_map = {cell: i for i, cell in enumerate(cells)}
        for i, unit_cells in enumerate(units):
            if i == 0:
                frame = self.frame
            else:
                mode, delay = _unit_delay(fn, i)
                frame = (self.history.at_beat(ctx.beat_index - delay) if mode == "beat"
                        else self.history.at_time(ctx.now - delay)) or self.frame
            self._write_frame(unit_cells, frame, fn, ctx, index_map)

    def _write_frame(self, cells_subset, frame, fn, ctx, index_map=None):
        lit, rgb = frame
        for cell in cells_subset:
            i = index_map[cell] if index_map is not None else cells_subset.index(cell)
            color = rgb if i in lit else (0, 0, 0)
            if self.sent.get(cell) != color:
                self.sent[cell] = color
                if cell[0] == "zone":
                    if len(cell) == 3:
                        ctx.engine.set_color([cell[2]], *color, zones=[cell[1]])
                    else:
                        ctx.engine.set_color(fn.targets, *color, zones=[cell[1]])
                else:
                    ctx.engine.set_color([cell[1]], *color)


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
        self._send((r, g, b), lambda: _apply(fn, ctx, lambda ids, z: ctx.engine.set_color(ids, r, g, b, zones=z)))


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
                _apply(fn, ctx, lambda ids, z: ctx.engine.set_strobe(ids, speed, zones=z))
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
        self._send((pan, tilt),
                   lambda: ctx.engine.set_raw_pan_tilt(_resolved_allowed(fn, ctx), pan, tilt))


# category: "color" = anything that drives light output (dimmer, strobe, color);
# "motion" = pan/tilt. The Sound window's mode (Color / Motion / Both) picks which
# categories react to sound, so e.g. an animation can own movement while sound
# owns the colors.
#
# attribute: which DMX channel group a function actually writes -- "dimmer", "color", "strobe" or
# "pan_tilt". Priority claiming (SoundService.tick's `claimed`) keys off THIS, not `category`: two
# "color"-category functions that touch different attributes (e.g. a beat_flash on dimmer and a
# beat_color chase on color, both #1/#2 on the same fixture) stack instead of the lower one being
# fully blocked, since they don't actually write the same channels. Only functions sharing both a
# fixture/zone AND an attribute genuinely conflict.
#
# uses_zones: the function honours a zone selection (a light bar's spots/derbies); the UI
# only offers the zone picker for those.
#
# uses_desync: the function can spread its own color sequence across time instead of showing it
# to every light at once -- per group or per individual light, offset by a beat count or by real
# time (see SoundFunction.desync); the UI only offers the desync controls for those.
FUNCTION_TYPES: dict[str, dict] = {
    "vu_dimmer": {
        "label": "VU dimmer", "runtime": VuDimmer, "category": "color", "attribute": "dimmer",
        "uses_zones": True,
        "description": "Dimmer follows the loudness (or bass/mid/high).",
        "params": [SOURCE, num("min_pct", "Min", 0, 0, 100, 1, "%"), num("max_pct", "Max", 100, 0, 100, 1, "%"),
                   num("gain", "Gain", 1.0, 0.2, 4, 0.1), num("attack_ms", "Attack", 30, 0, 500, 5, "ms"),
                   num("release_ms", "Release", 250, 20, 2000, 10, "ms")],
    },
    "beat_flash": {
        "label": "Beat flash", "runtime": BeatFlash, "category": "color", "attribute": "dimmer",
        "uses_zones": True,
        "description": "Flash to full on the beat, then fade back.",
        "params": [EVERY, num("min_pct", "Base", 5, 0, 100, 1, "%"), num("max_pct", "Peak", 100, 0, 100, 1, "%"),
                   num("decay_ms", "Decay", 250, 20, 2000, 10, "ms")],
    },
    "beat_color": {
        "label": "Beat color chase", "runtime": BeatColor, "category": "color", "attribute": "color",
        "uses_zones": True, "uses_desync": True,
        "description": "Change color on the beat.",
        "params": [EVERY, choice("mode", "Order", "step", ["step", "random"]),
                   {"key": "palette", "label": "Colors", "kind": "palette", "default": DEFAULT_PALETTE}],
    },
    "zone_chase": {
        "label": "Beat zone chase", "runtime": ZoneChase, "category": "color", "attribute": "color",
        "uses_zones": True, "uses_desync": True,
        "description": "Light a fixture's zones (e.g. a light bar's spots and derbies) in turn on the beat.",
        "params": [EVERY,
                   choice("pattern", "Pattern", "sequence", ["sequence", "ping_pong", "alternate", "random"]),
                   choice("color_mode", "Colors", "per_pass", ["fixed", "per_step", "per_pass"]),
                   {"key": "palette", "label": "Colors", "kind": "palette", "default": DEFAULT_PALETTE}],
    },
    "color_organ": {
        "label": "Color organ", "runtime": ColorOrgan, "category": "color", "attribute": "color",
        "uses_zones": True,
        "description": "Bass = red, mids = green, highs = blue.",
        "params": [num("gain", "Gain", 1.0, 0.2, 4, 0.1), num("smooth_ms", "Smooth", 120, 10, 1000, 10, "ms")],
    },
    "beat_strobe": {
        "label": "Beat strobe", "runtime": BeatStrobe, "category": "color", "attribute": "strobe",
        "uses_zones": True,
        "description": "A short strobe burst on the beat, then back to open.",
        "params": [choice("every", "Every", 4, [1, 2, 4, 8]), num("burst_ms", "Burst", 250, 50, 2000, 10, "ms"),
                   num("speed_pct", "Speed", 80, 1, 100, 1, "%")],
    },
    "beat_movement": {
        "label": "Beat movement", "runtime": BeatMovement, "category": "motion", "attribute": "pan_tilt",
        "description": "Step through pan/tilt positions on the beat.",
        "params": [choice("every", "Every", 2, [1, 2, 4, 8]),
                   {"key": "positions", "label": "Positions", "kind": "positions",
                    "default": BeatMovement.DEFAULT_POSITIONS}],
    },
}


def function_types_for_ui() -> list[dict]:
    return [{"type": t, "label": v["label"], "category": v["category"],
             "uses_zones": bool(v.get("uses_zones")),
             "uses_desync": bool(v.get("uses_desync")),
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
