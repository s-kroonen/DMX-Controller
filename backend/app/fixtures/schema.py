"""Fixture profile schema.

A fixture *profile* describes a fixture type (channel layout, ranges) --
not a physical unit. Physical placement (DMX address, 3D position) lives
in room.model.FixtureInstance instead, since the same profile is reused
across many instances/venues.

Channel roles are split into two buckets:

  - well-known "functions" (pan, tilt, dimmer, red, ...) that the IK core,
    color picker, and strobe/shutter panel understand structurally
  - arbitrary "custom" channels/sliders (macro, gobo, prism speed, ...)
    that don't map to any built-in function but still need a slider/knob
    in the UI -- this is the "custom sliders for features that don't map
    to any function" requirement.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

# Channel roles the rest of the system has structural knowledge of.
KNOWN_FUNCTIONS = (
    "pan", "pan_fine",
    "tilt", "tilt_fine",
    "dimmer",
    "red", "green", "blue", "white", "amber", "uv",
    "strobe", "shutter",
    "zoom", "focus",
    "color_wheel", "gobo_wheel", "gobo_rotation",
    "prism",
    "mode",
    "macro",
)


@dataclasses.dataclass
class CustomChannel:
    """A channel with no built-in function -- rendered as a plain slider."""

    channel: int
    label: str
    default: int = 0
    min_value: int = 0
    max_value: int = 255


@dataclasses.dataclass
class FixtureProfile:
    id: str  # stable slug, e.g. "beamz-mhl108-mkii-11ch"
    name: str
    manufacturer: str = ""
    mode: str = ""
    channel_count: int = 0
    # role -> 1-indexed DMX channel offset within the fixture's footprint
    channels: dict[str, int] = dataclasses.field(default_factory=dict)
    custom_channels: list[CustomChannel] = dataclasses.field(default_factory=list)
    pan_range_deg: Optional[float] = 540.0
    tilt_range_deg: Optional[float] = 270.0
    defaults: dict[str, int] = dataclasses.field(default_factory=dict)
    fixture_type: str = "generic"  # "moving_head" | "par" | "laser" | "generic"

    def has_function(self, name: str) -> bool:
        return name in self.channels

    def channel_for(self, name: str) -> Optional[int]:
        return self.channels.get(name)

    def has_pan_tilt(self) -> bool:
        return self.has_function("pan") and self.has_function("tilt")

    def has_color(self) -> bool:
        return all(self.has_function(c) for c in ("red", "green", "blue"))

    def has_strobe(self) -> bool:
        return self.has_function("strobe") or self.has_function("shutter")

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "FixtureProfile":
        custom = [CustomChannel(**c) for c in d.get("custom_channels", [])]
        kwargs = {**d, "custom_channels": custom}
        return FixtureProfile(**kwargs)
