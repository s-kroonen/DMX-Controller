"""Fixture profile schema.

A fixture *profile* describes a fixture type (channel layout, ranges) --
not a physical unit. Physical placement (DMX address, 3D position) lives
in room.model.FixtureInstance instead, since the same profile is reused
across many instances/venues.

Channel roles are split into three buckets:

  - well-known "functions" (pan, tilt, dimmer, red, ...) that the IK core,
    color picker, and strobe/shutter panel understand structurally
  - **zones**: independently colored sections of one fixture (the two spots
    and two derbies of a light bar, the cells of a pixel bar, ...). A zone
    owns its own red/green/blue/white channels. A fixture with no explicit
    zones behaves as if it had one, "main", built from the top-level roles
    -- so every existing profile keeps working unchanged.
  - arbitrary "custom" channels/sliders (macro, gobo, prism speed, motor
    speed, built-in programs, ...) that don't map to any built-in function
    but still need a control in the UI. A custom channel can carry named
    ranges ("Low" / "Medium" / "Fast", "Sound 2") so the UI can offer presets
    instead of a bare 0-255 slider.
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

# Roles a zone can own (a subset of the above).
# A zone's channels may be shared with other zones (two spots on one strobe channel): the
# fixture simply cannot drive them independently, and the UI warns about it.
ZONE_ROLES = ("red", "green", "blue", "white", "amber", "uv", "dimmer", "strobe")
MAIN_ZONE_ID = "main"


@dataclasses.dataclass
class ChannelRange:
    """A named span of a custom channel's DMX values, e.g. "Medium" 30-49. The UI shows a
    preset button per range (it sets the channel to the span's min) next to a slider that
    always covers the whole channel; the span the value falls in lights its button.

    `speed` is no longer used (the slider is always there); it is kept so older profiles load."""

    label: str
    min: int
    max: int
    speed: bool = False


@dataclasses.dataclass
class CustomChannel:
    """A channel with no built-in function -- rendered as a slider, or as
    named presets when `ranges` is given."""

    channel: int
    label: str
    default: int = 0
    min_value: int = 0
    max_value: int = 255
    ranges: list[ChannelRange] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Zone:
    """An independently controllable colored section of a fixture.

    `channels` maps the zone's roles (red/green/blue/white, optionally its own
    dimmer) to 1-indexed DMX channel offsets within the fixture's footprint.
    `kind` is a free-form family ("spot", "derby", "cell", "main") the UI uses
    to offer quick selections ("all derbies")."""

    id: str
    label: str
    kind: str = "cell"
    channels: dict[str, int] = dataclasses.field(default_factory=dict)
    # where the zone sits along the fixture for the 3D picture, -1 (one end) .. +1 (the
    # other); None spreads the zones evenly in the order listed
    position: Optional[float] = None


@dataclasses.dataclass
class RoleRange:
    """Where a role's usable values live inside its physical DMX channel.

    Needed when several roles share one channel (e.g. a single dimmer/strobe
    channel: 10-134 dims, 135-239 strobes, 240-255 is full on). The UI and
    animations always work in a logical 0-255; raw_value() maps that into the
    range so a "full" dimmer slider never spills into strobe territory.

      logical 0        -> zero  (the "off"/"none" value for this role)
      logical 1..255   -> linearly min..max
    """

    min: int
    max: int
    zero: int = 0


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
    # "moving_head" | "par" | "laser" | "light_bar" | "generic" | ... (picks the 3D model)
    fixture_type: str = "generic"
    # role -> RoleRange; roles not listed pass straight through 1:1
    role_ranges: dict[str, RoleRange] = dataclasses.field(default_factory=dict)
    # independently colored sections; empty = the fixture is one implicit "main" zone
    zones: list[Zone] = dataclasses.field(default_factory=list)

    def has_function(self, name: str) -> bool:
        return name in self.channels

    def channel_for(self, name: str) -> Optional[int]:
        return self.channels.get(name)

    def has_pan_tilt(self) -> bool:
        return self.has_function("pan") and self.has_function("tilt")

    def has_color(self) -> bool:
        if all(self.has_function(c) for c in ("red", "green", "blue")):
            return True
        return any(all(c in z.channels for c in ("red", "green", "blue")) for z in self.zones)

    def has_strobe(self) -> bool:
        return self.has_function("strobe") or self.has_function("shutter") or self.has_zone_strobe()

    def has_zone_strobe(self) -> bool:
        """True if any zone has its own strobe channel (a light bar's spots and derbies)."""
        return any("strobe" in z.channels for z in self.zones)

    def zone_strobe_sharing(self, zone_ids) -> list[str]:
        """Zones that are NOT in `zone_ids` but strobe together with one of them because they
        share its strobe channel (empty when every selected zone strobes independently)."""
        chosen = set(zone_ids)
        channels = {z.channels["strobe"] for z in self.zones if z.id in chosen and "strobe" in z.channels}
        return [z.id for z in self.zones
                if z.id not in chosen and z.channels.get("strobe") in channels]

    # -- zones ---------------------------------------------------------

    def has_zones(self) -> bool:
        """True if the profile declares its own zones (more than the implicit main one)."""
        return bool(self.zones)

    def zone_list(self) -> list[Zone]:
        """The zones to control: the declared ones, or one implicit "main" zone
        built from the top-level color roles (empty if the fixture has no color)."""
        if self.zones:
            return self.zones
        roles = {r: self.channels[r] for r in ("red", "green", "blue", "white") if r in self.channels}
        if not roles:
            return []
        return [Zone(id=MAIN_ZONE_ID, label="Main", kind="main", channels=roles)]

    def zone(self, zone_id: str) -> Optional[Zone]:
        return next((z for z in self.zone_list() if z.id == zone_id), None)

    def raw_value(self, role: str, value: int) -> int:
        """Logical 0-255 slider value -> the raw DMX value for this role."""
        value = max(0, min(255, int(value)))
        rng = self.role_ranges.get(role)
        if rng is None:
            return value
        if value == 0:
            return rng.zero
        return round(rng.min + (value - 1) / 254 * (rng.max - rng.min))

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "FixtureProfile":
        # Saved profiles outlive the code: keys this version no longer has (an older layout's
        # `role_mirrors`, say) are dropped instead of making the whole library fail to load.
        custom = []
        for c in d.get("custom_channels", []):
            ranges = [ChannelRange(**_known(ChannelRange, r)) for r in (c.get("ranges") or [])]
            custom.append(CustomChannel(**{**_known(CustomChannel, c), "ranges": ranges}))
        ranges = {role: RoleRange(**_known(RoleRange, r)) for role, r in (d.get("role_ranges") or {}).items()}
        zones = [Zone(**_known(Zone, z)) for z in (d.get("zones") or [])]
        kwargs = {**_known(FixtureProfile, d), "custom_channels": custom, "role_ranges": ranges, "zones": zones}
        return FixtureProfile(**kwargs)


def _known(cls, data: dict) -> dict:
    """`data` without the keys `cls` has no field for."""
    fields = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in fields}
