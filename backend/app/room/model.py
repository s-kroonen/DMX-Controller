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
class Vec2:
    x: float
    y: float


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
    width: float = 10.0  # meters, X axis -- used only as a fallback rectangle
    depth: float = 10.0  # meters, Y axis    when Room.floor_points is empty
    height: float = 4.0  # meters, Z axis (up) -- ceiling height either way


def default_rectangle_floor(dimensions: RoomDimensions) -> list[Vec2]:
    """A room's real floor is rarely a perfect rectangle -- Room.floor_points
    holds an arbitrary polygon drawn in the 2D room-shape editor. This is
    only the fallback used until the user has drawn a real shape."""
    hw, hd = dimensions.width / 2.0, dimensions.depth / 2.0
    return [Vec2(-hw, -hd), Vec2(hw, -hd), Vec2(hw, hd), Vec2(-hw, hd)]


@dataclasses.dataclass
class RoomObject:
    """A reference object placed in the 3D scene -- a wall segment, a
    person-scale marker, a box obstacle, or a raised surface/platform.
    Purely visual/spatial reference (unlike SafetyZone, it does not block
    beams) so operators can sanity-check fixture aim against real
    obstacles and get a sense of scale.

    kind == "wall": position is the segment start, end_position the end;
      thickness and height apply, width/depth are unused.
    kind == "person": position is the ground point; height is the
      person's height, width/depth default to a body-width footprint.
    kind in ("box", "surface"): position is the base center; width/depth/
      height give its footprint and height (a "surface" is just a low,
      flat box representing a platform/table/DJ booth).
    """

    id: str
    name: str
    kind: str  # "wall" | "person" | "box" | "surface"
    position: Vec3
    end_position: Optional[Vec3] = None
    thickness: float = 0.1
    width: float = 0.5
    depth: float = 0.5
    height: float = 1.8
    color: str = "#888888"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "position": dataclasses.asdict(self.position),
            "end_position": dataclasses.asdict(self.end_position) if self.end_position else None,
            "thickness": self.thickness,
            "width": self.width,
            "depth": self.depth,
            "height": self.height,
            "color": self.color,
        }

    @staticmethod
    def from_dict(d: dict) -> "RoomObject":
        return RoomObject(
            id=d["id"],
            name=d["name"],
            kind=d["kind"],
            position=Vec3(**d["position"]),
            end_position=Vec3(**d["end_position"]) if d.get("end_position") else None,
            thickness=d.get("thickness", 0.1),
            width=d.get("width", 0.5),
            depth=d.get("depth", 0.5),
            height=d.get("height", 1.8),
            color=d.get("color", "#888888"),
        )


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
    # Arbitrary polygon (in room-space X/Y, ordered, not necessarily
    # rectangular or 4-sided) drawn in the 2D room-shape editor and
    # extruded to dimensions.height for the 3D view. Empty means "not
    # drawn yet" -- effective_floor_points() falls back to a rectangle.
    floor_points: list[Vec2] = dataclasses.field(default_factory=list)
    fixtures: dict[str, FixtureInstance] = dataclasses.field(default_factory=dict)
    safety_zones: dict[str, SafetyZone] = dataclasses.field(default_factory=dict)
    objects: dict[str, RoomObject] = dataclasses.field(default_factory=dict)

    def effective_floor_points(self) -> list[Vec2]:
        if len(self.floor_points) >= 3:
            return self.floor_points
        return default_rectangle_floor(self.dimensions)

    def add_fixture(self, fixture: FixtureInstance) -> None:
        self.fixtures[fixture.id] = fixture

    def remove_fixture(self, fixture_id: str) -> None:
        self.fixtures.pop(fixture_id, None)

    def add_safety_zone(self, zone: SafetyZone) -> None:
        self.safety_zones[zone.id] = zone

    def remove_safety_zone(self, zone_id: str) -> None:
        self.safety_zones.pop(zone_id, None)

    def add_object(self, obj: RoomObject) -> None:
        self.objects[obj.id] = obj

    def remove_object(self, object_id: str) -> None:
        self.objects.pop(object_id, None)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dimensions": dataclasses.asdict(self.dimensions),
            "floor_points": [dataclasses.asdict(p) for p in self.floor_points],
            "fixtures": [f.to_dict() for f in self.fixtures.values()],
            "safety_zones": [z.to_dict() for z in self.safety_zones.values()],
            "objects": [o.to_dict() for o in self.objects.values()],
        }

    @staticmethod
    def from_dict(d: dict) -> "Room":
        room = Room(
            name=d.get("name", "Untitled Room"),
            dimensions=RoomDimensions(**d.get("dimensions", {})),
            floor_points=[Vec2(**p) for p in d.get("floor_points", [])],
        )
        for f in d.get("fixtures", []):
            room.add_fixture(FixtureInstance.from_dict(f))
        for z in d.get("safety_zones", []):
            room.add_safety_zone(SafetyZone.from_dict(z))
        for o in d.get("objects", []):
            room.add_object(RoomObject.from_dict(o))
        return room
