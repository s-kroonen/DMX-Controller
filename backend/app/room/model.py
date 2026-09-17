"""Room model: geometry, fixture instances, and safety zones for one venue.

Profiles (fixture *type*) live in fixtures/. This module holds physical
*instances*: which profile a unit uses, its DMX start address/universe,
and its position + mounting orientation in the current room -- since the
same physical fixture ends up in a different spot every venue.
"""

from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass
class Vec3:
    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclasses.dataclass
class Orientation:
    """Mounting orientation in degrees. yaw = rotation around Z (up),
    pitch = tilt of the fixture's home/zero direction, roll usually 0."""

    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


@dataclasses.dataclass
class FixtureInstance:
    id: str
    name: str
    profile_id: str
    universe: int = 1
    start_address: int = 1  # 1-indexed DMX address
    position: Vec3 = dataclasses.field(default_factory=lambda: Vec3(0, 0, 0))
    orientation: Orientation = dataclasses.field(default_factory=Orientation)
    group_ids: list[str] = dataclasses.field(default_factory=list)
    inverted_pan: bool = False
    inverted_tilt: bool = False

    def channel_for(self, offset: int) -> int:
        """offset is 1-indexed within the fixture's own footprint."""
        return self.start_address + offset - 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "profile_id": self.profile_id,
            "universe": self.universe,
            "start_address": self.start_address,
            "position": dataclasses.asdict(self.position),
            "orientation": dataclasses.asdict(self.orientation),
            "group_ids": self.group_ids,
            "inverted_pan": self.inverted_pan,
            "inverted_tilt": self.inverted_tilt,
        }

    @staticmethod
    def from_dict(d: dict) -> "FixtureInstance":
        return FixtureInstance(
            id=d["id"],
            name=d["name"],
            profile_id=d["profile_id"],
            universe=d.get("universe", 1),
            start_address=d.get("start_address", 1),
            position=Vec3(**d.get("position", {"x": 0, "y": 0, "z": 0})),
            orientation=Orientation(**d.get("orientation", {})),
            group_ids=d.get("group_ids", []),
            inverted_pan=d.get("inverted_pan", False),
            inverted_tilt=d.get("inverted_tilt", False),
        )


@dataclasses.dataclass
class RoomDimensions:
    width: float = 10.0  # meters, X axis
    depth: float = 10.0  # meters, Y axis
    height: float = 4.0  # meters, Z axis (up)


@dataclasses.dataclass
class SafetyZone:
    """An axis-aligned box (in room space) that a beam may never enter.

    A rotated box would need a full OBB test; axis-aligned covers the
    common "don't point into the crowd" / "don't point above head height
    near the bar" cases from the design doc while staying simple to edit
    and to intersect against.
    """

    id: str
    name: str
    min_corner: Vec3
    max_corner: Vec3
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "min_corner": dataclasses.asdict(self.min_corner),
            "max_corner": dataclasses.asdict(self.max_corner),
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(d: dict) -> "SafetyZone":
        return SafetyZone(
            id=d["id"],
            name=d["name"],
            min_corner=Vec3(**d["min_corner"]),
            max_corner=Vec3(**d["max_corner"]),
            enabled=d.get("enabled", True),
        )


@dataclasses.dataclass
class Room:
    name: str = "Untitled Room"
    dimensions: RoomDimensions = dataclasses.field(default_factory=RoomDimensions)
    fixtures: dict[str, FixtureInstance] = dataclasses.field(default_factory=dict)
    safety_zones: dict[str, SafetyZone] = dataclasses.field(default_factory=dict)

    def add_fixture(self, fixture: FixtureInstance) -> None:
        self.fixtures[fixture.id] = fixture

    def remove_fixture(self, fixture_id: str) -> None:
        self.fixtures.pop(fixture_id, None)

    def add_safety_zone(self, zone: SafetyZone) -> None:
        self.safety_zones[zone.id] = zone

    def remove_safety_zone(self, zone_id: str) -> None:
        self.safety_zones.pop(zone_id, None)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dimensions": dataclasses.asdict(self.dimensions),
            "fixtures": [f.to_dict() for f in self.fixtures.values()],
            "safety_zones": [z.to_dict() for z in self.safety_zones.values()],
        }

    @staticmethod
    def from_dict(d: dict) -> "Room":
        room = Room(
            name=d.get("name", "Untitled Room"),
            dimensions=RoomDimensions(**d.get("dimensions", {})),
        )
        for f in d.get("fixtures", []):
            room.add_fixture(FixtureInstance.from_dict(f))
        for z in d.get("safety_zones", []):
            room.add_safety_zone(SafetyZone.from_dict(z))
        return room
