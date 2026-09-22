from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ..context import get_context
from ..dmx.dmx4all import Dmx4AllOutput, list_serial_ports
from ..dmx.usb_procs import find_holders
from ..fixtures.qxf_import import parse_qxf
from ..fixtures.schema import ChannelRange, CustomChannel, FixtureProfile, RoleRange, Zone
from ..groups.model import ALL_GROUP_ID, Group
from ..room.solver import HeadParams, Observation, dmx_to_angle, solve as solve_head
from ..room.model import (
    AnimationPoint,
    FixtureInstance,
    Orientation,
    RoomDimensions,
    RoomObject,
    SafetyZone,
    Vec2,
    Vec3,
)
from ..show.animation import Animation, AnimationTrack, Keyframe, PatternAnimation

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- edit / show mode
#
# The rig is either being set up ("edit": patch fixtures, move them, change the room, groups and
# shows) or being run ("show": effects, shows, aiming moving heads). Each side is refused, with
# 409, while the other mode is active, so nothing is moved by accident during a show and nothing
# runs while the room is being edited. Colors, dimmer, strobe, custom channels and blackout work
# in both modes.

def require_edit():
    if get_context().mode != "edit":
        raise HTTPException(409, "This is only possible in edit mode.")


def require_show():
    if get_context().mode != "show":
        raise HTTPException(409, "This is only possible in show mode.")


EDIT = [Depends(require_edit)]
SHOW = [Depends(require_show)]


class ModeIn(BaseModel):
    mode: str


@router.get("/mode")
def get_mode():
    return {"mode": get_context().mode}


@router.put("/mode")
def set_mode(payload: ModeIn):
    ctx = get_context()
    try:
        ctx.set_mode(payload.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"mode": ctx.mode}


# ---------------------------------------------------------------- calibration
#
# Checking where the heads really point. Edit mode only, and separate from the show-mode aim
# above so the show-mode guard stays as it is: every head aims at ONE shared point (or follows one
# point moving along a path) and the operator watches whether the beams stay together.

class PointIn(BaseModel):
    x: float
    y: float
    z: float


class CalibrationAimIn(PointIn):
    target_ids: list[str]


class CalibrationBeamIn(BaseModel):
    target_ids: list[str]
    on: bool


class CalibrationOffsetsIn(BaseModel):
    fixture_id: str
    pan_offset_deg: Optional[float] = None
    tilt_offset_deg: Optional[float] = None
    inverted_pan: Optional[bool] = None
    inverted_tilt: Optional[bool] = None
    point: Optional[PointIn] = None   # re-aim at this point after the change, so the effect is visible


class CalibrationSweepIn(BaseModel):
    target_ids: list[str]
    points: list[PointIn]
    seconds_per_leg: float = 3.0


class MarkObservationIn(PointIn):
    """The beam was on the mark at (x, y, z) when the head's pan/tilt channels read these values."""

    pan: int
    pan_fine: int = 0
    tilt: int
    tilt_fine: int = 0


class CalibrationSolveIn(BaseModel):
    fixture_id: str
    observations: list[MarkObservationIn]
    solve_position: bool = False


class CalibrationApplyIn(BaseModel):
    fixture_id: str
    yaw_deg: float
    pitch_deg: float
    pan_offset_deg: float
    tilt_offset_deg: float
    inverted_pan: bool
    inverted_tilt: bool
    position: Optional[PointIn] = None


class CalibrationPanTiltIn(BaseModel):
    target_id: str
    pan: int
    tilt: int
    pan_fine: int = 0
    tilt_fine: int = 0


def _fixture_ids(ids: list[str]) -> list[str]:
    return get_context().engine.resolve_fixture_ids(ids)


@router.post("/calibration/aim", dependencies=EDIT)
def calibration_aim(payload: CalibrationAimIn):
    ctx = get_context()
    ctx.stop_sweep()
    results = ctx.engine.calibration_aim(_fixture_ids(payload.target_ids), Vec3(payload.x, payload.y, payload.z))
    return {"results": results}


@router.post("/calibration/beam", dependencies=EDIT)
def calibration_beam(payload: CalibrationBeamIn):
    ctx = get_context()
    ids = _fixture_ids(payload.target_ids)
    if payload.on:
        ctx.engine.beam_on(ids)
    else:
        ctx.engine.beam_off(ids)
    return {"beam": ctx.engine.beam_ids()}


@router.post("/calibration/offsets", dependencies=EDIT)
def calibration_offsets(payload: CalibrationOffsetsIn):
    ctx = get_context()
    instance = ctx.engine.room.fixtures.get(payload.fixture_id)
    if instance is None:
        raise HTTPException(404, "fixture not found")
    if payload.pan_offset_deg is not None:
        instance.pan_offset_deg = max(-180.0, min(180.0, round(payload.pan_offset_deg, 3)))
    if payload.tilt_offset_deg is not None:
        instance.tilt_offset_deg = max(-180.0, min(180.0, round(payload.tilt_offset_deg, 3)))
    if payload.inverted_pan is not None:
        instance.inverted_pan = payload.inverted_pan
    if payload.inverted_tilt is not None:
        instance.inverted_tilt = payload.inverted_tilt
    ctx.persist_room()
    result = None
    if payload.point is not None and ctx.sweep_player is None:   # a running sweep picks the change up by itself
        p = payload.point
        result = ctx.engine.calibration_aim([payload.fixture_id], Vec3(p.x, p.y, p.z)).get(payload.fixture_id)
    return {"fixture": instance.to_dict(), "result": result}


def _head_params(instance) -> HeadParams:
    return HeadParams(Vec3(instance.position.x, instance.position.y, instance.position.z),
                      instance.orientation.yaw_deg, instance.orientation.pitch_deg,
                      instance.pan_offset_deg, instance.tilt_offset_deg, instance.inverted_pan,
                      instance.inverted_tilt)


def _params_dict(p: HeadParams) -> dict:
    return {"yaw_deg": round(p.yaw_deg, 3), "pitch_deg": round(p.pitch_deg, 3),
            "pan_offset_deg": round(p.pan_offset_deg, 3), "tilt_offset_deg": round(p.tilt_offset_deg, 3),
            "inverted_pan": p.inverted_pan, "inverted_tilt": p.inverted_tilt,
            "position": {"x": round(p.position.x, 3), "y": round(p.position.y, 3), "z": round(p.position.z, 3)}}


@router.post("/calibration/solve", dependencies=EDIT)
def calibration_solve(payload: CalibrationSolveIn):
    """Fit the head's mounting (and, with enough marks, its position) to where the operator had the beam
    on marks of known position. Nothing is changed: /calibration/apply writes the result."""
    ctx = get_context()
    instance = ctx.engine.room.fixtures.get(payload.fixture_id)
    if instance is None:
        raise HTTPException(404, "fixture not found")
    profile = ctx.engine.profile_for(payload.fixture_id)
    if not profile.has_pan_tilt():
        raise HTTPException(400, "that fixture has no pan/tilt")
    pan_range, tilt_range = profile.pan_range_deg or 540.0, profile.tilt_range_deg or 270.0
    observations = [Observation(Vec3(o.x, o.y, o.z), dmx_to_angle(o.pan, o.pan_fine, pan_range),
                                dmx_to_angle(o.tilt, o.tilt_fine, tilt_range)) for o in payload.observations]
    try:
        fit = solve_head(_head_params(instance), observations, payload.solve_position)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"before": _params_dict(_head_params(instance)), "after": _params_dict(fit.params),
            "rms_before_deg": round(fit.rms_before_deg, 3), "rms_deg": round(fit.rms_deg, 3),
            "residuals_deg": [round(r, 3) for r in fit.residuals_deg], "solved_position": fit.solved_position,
            "changed_flags": fit.changed_flags, "warnings": fit.warnings}


@router.post("/calibration/apply", dependencies=EDIT)
def calibration_apply(payload: CalibrationApplyIn):
    ctx = get_context()
    instance = ctx.engine.room.fixtures.get(payload.fixture_id)
    if instance is None:
        raise HTTPException(404, "fixture not found")
    instance.orientation.yaw_deg = round(payload.yaw_deg, 3)
    instance.orientation.pitch_deg = round(payload.pitch_deg, 3)
    instance.pan_offset_deg = max(-180.0, min(180.0, round(payload.pan_offset_deg, 3)))
    instance.tilt_offset_deg = max(-180.0, min(180.0, round(payload.tilt_offset_deg, 3)))
    instance.inverted_pan = payload.inverted_pan
    instance.inverted_tilt = payload.inverted_tilt
    if payload.position is not None:
        p = payload.position
        instance.position = ctx.engine.room.clamp_position(Vec3(p.x, p.y, p.z))
    ctx.persist_room()
    return {"fixture": instance.to_dict()}


@router.post("/calibration/sweep", dependencies=EDIT)
def calibration_sweep(payload: CalibrationSweepIn):
    ctx = get_context()
    try:
        ctx.start_sweep(_fixture_ids(payload.target_ids), [Vec3(p.x, p.y, p.z) for p in payload.points],
                        max(0.5, payload.seconds_per_leg))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"sweeping": True}


@router.post("/calibration/sweep/stop", dependencies=EDIT)
def calibration_sweep_stop():
    get_context().stop_sweep()
    return {"sweeping": False}


@router.post("/calibration/pan-tilt", dependencies=EDIT)
def calibration_pan_tilt(payload: CalibrationPanTiltIn):
    """Raw pan/tilt for the Test tools window in edit mode (checking channel mapping and range)."""
    get_context().stop_sweep()
    get_context().engine.set_raw_pan_tilt(payload.target_id, payload.pan, payload.tilt,
                                          payload.pan_fine, payload.tilt_fine)
    return {"ok": True}


# ---------------------------------------------------------------- fixtures

@router.get("/fixtures/profiles")
def list_profiles():
    ctx = get_context()
    return [p.to_dict() for p in ctx.library.list()]


class ChannelRangeIn(BaseModel):
    label: str
    min: int
    max: int
    speed: bool = False


class CustomChannelIn(BaseModel):
    channel: int
    label: str
    default: int = 0
    min_value: int = 0
    max_value: int = 255
    ranges: list[ChannelRangeIn] = []


class ZoneIn(BaseModel):
    id: str
    label: str
    kind: str = "cell"
    channels: dict[str, int] = {}
    position: Optional[float] = None


class RoleRangeIn(BaseModel):
    min: int
    max: int
    zero: int = 0


class FixtureProfileIn(BaseModel):
    id: Optional[str] = None
    name: str
    manufacturer: str = ""
    mode: str = ""
    channel_count: int
    channels: dict[str, int] = {}
    custom_channels: list[CustomChannelIn] = []
    pan_range_deg: Optional[float] = 540.0
    tilt_range_deg: Optional[float] = 270.0
    defaults: dict[str, int] = {}
    fixture_type: str = "generic"
    # None = keep whatever the existing profile with this id has (the fixture
    # creator UI doesn't edit ranges, so re-saving must not silently drop them)
    role_ranges: Optional[dict[str, RoleRangeIn]] = None
    # likewise for zones: the creator UI doesn't edit them, so None keeps the existing ones
    zones: Optional[list[ZoneIn]] = None
    # the Fixture Creator sends True: the structure was edited on purpose, so a newer bundled
    # version must not replace it. None keeps what the existing profile has.
    customized: Optional[bool] = None


@router.post("/fixtures/profiles", dependencies=EDIT)
def create_or_update_profile(payload: FixtureProfileIn):
    """The fixture creator: define a new type, or edit a user-saved one."""
    ctx = get_context()
    profile_id = payload.id or f"custom-{uuid.uuid4().hex[:8]}"
    existing = ctx.library.get(profile_id)
    if payload.role_ranges is not None:
        role_ranges = {r: RoleRange(**v.model_dump()) for r, v in payload.role_ranges.items()}
    else:
        role_ranges = dict(existing.role_ranges) if existing else {}
    if payload.zones is not None:
        zones = [Zone(**z.model_dump()) for z in payload.zones]
    else:
        zones = list(existing.zones) if existing else []
    # a custom channel saved without named ranges keeps the ones it already had
    kept_ranges = {c.channel: c.ranges for c in existing.custom_channels} if existing else {}
    custom_channels = []
    for c in payload.custom_channels:
        data = c.model_dump()
        ranges = [ChannelRange(**r) for r in data.pop("ranges")] or list(kept_ranges.get(c.channel, []))
        custom_channels.append(CustomChannel(**data, ranges=ranges))
    profile = FixtureProfile(
        id=profile_id,
        name=payload.name,
        manufacturer=payload.manufacturer,
        mode=payload.mode,
        channel_count=payload.channel_count,
        channels=payload.channels,
        custom_channels=custom_channels,
        pan_range_deg=payload.pan_range_deg,
        tilt_range_deg=payload.tilt_range_deg,
        defaults=payload.defaults,
        fixture_type=payload.fixture_type,
        role_ranges=role_ranges,
        zones=zones,
        customized=bool(payload.customized) if payload.customized is not None
        else bool(existing.customized) if existing else True,
    )
    ctx.library.save(profile)
    return profile.to_dict()


@router.delete("/fixtures/profiles/{profile_id}", dependencies=EDIT)
def delete_profile(profile_id: str):
    ctx = get_context()
    ctx.library.delete(profile_id)
    return {"ok": True}


@router.post("/fixtures/import-qxf", dependencies=EDIT)
async def import_qxf(file: UploadFile):
    ctx = get_context()
    contents = await file.read()
    tmp_path = ctx.storage.data_dir / f"_upload_{uuid.uuid4().hex}.qxf"
    tmp_path.write_bytes(contents)
    try:
        profiles = parse_qxf(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    for profile in profiles:
        ctx.library.save(profile)
    return [p.to_dict() for p in profiles]


# ---------------------------------------------------------------- room

@router.get("/room")
def get_room():
    return get_context().engine.room.to_dict()


class RoomDimensionsIn(BaseModel):
    width: float
    depth: float
    height: float


class Vec2In(BaseModel):
    x: float
    y: float


class RoomIn(BaseModel):
    name: str
    dimensions: RoomDimensionsIn
    floor_points: list[Vec2In] = []


@router.put("/room", dependencies=EDIT)
def update_room(payload: RoomIn):
    """Redrawing the room shape only ever happens here (the 2D editor) --
    never by dragging in the 3D view, which is fixtures/objects only.
    Room.apply_shape() rescales every fixture, object, and safety zone
    proportionally so they keep their relative placement when the shape
    or height changes, instead of ending up outside the new walls."""
    ctx = get_context()
    ctx.engine.room.name = payload.name
    ctx.engine.room.apply_shape(
        [Vec2(**p.model_dump()) for p in payload.floor_points],
        RoomDimensions(**payload.dimensions.model_dump()),
    )
    ctx.persist_room()
    return ctx.engine.room.to_dict()


class Vec3In(BaseModel):
    x: float
    y: float
    z: float


class OrientationIn(BaseModel):
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


class FixtureInstanceIn(BaseModel):
    id: Optional[str] = None
    name: str
    profile_id: str
    universe: int = 1
    start_address: int = 1
    position: Vec3In = Vec3In(x=0, y=0, z=0)
    orientation: OrientationIn = OrientationIn()
    group_ids: list[str] = []
    inverted_pan: bool = False
    inverted_tilt: bool = False
    pan_offset_deg: float = 0.0
    tilt_offset_deg: float = 0.0


@router.post("/room/fixtures", dependencies=EDIT)
def add_fixture(payload: FixtureInstanceIn):
    ctx = get_context()
    if ctx.library.get(payload.profile_id) is None:
        raise HTTPException(404, f"unknown fixture profile {payload.profile_id!r}")
    fixture_id = payload.id or f"fx-{uuid.uuid4().hex[:8]}"
    instance = FixtureInstance(
        id=fixture_id,
        name=payload.name,
        profile_id=payload.profile_id,
        universe=payload.universe,
        start_address=payload.start_address,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
        orientation=Orientation(**payload.orientation.model_dump()),
        group_ids=payload.group_ids,
        inverted_pan=payload.inverted_pan,
        inverted_tilt=payload.inverted_tilt,
        pan_offset_deg=payload.pan_offset_deg,
        tilt_offset_deg=payload.tilt_offset_deg,
    )
    ctx.engine.add_fixture(instance)
    ctx.persist_room()
    return instance.to_dict()


@router.put("/room/fixtures/{fixture_id}", dependencies=EDIT)
def update_fixture(fixture_id: str, payload: FixtureInstanceIn):
    ctx = get_context()
    if fixture_id not in ctx.engine.room.fixtures:
        raise HTTPException(404, "fixture not found")
    instance = FixtureInstance(
        id=fixture_id,
        name=payload.name,
        profile_id=payload.profile_id,
        universe=payload.universe,
        start_address=payload.start_address,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
        orientation=Orientation(**payload.orientation.model_dump()),
        group_ids=payload.group_ids,
        inverted_pan=payload.inverted_pan,
        inverted_tilt=payload.inverted_tilt,
        pan_offset_deg=payload.pan_offset_deg,
        tilt_offset_deg=payload.tilt_offset_deg,
    )
    ctx.engine.room.fixtures[fixture_id] = instance
    ctx.persist_room()
    return instance.to_dict()


@router.delete("/room/fixtures/{fixture_id}", dependencies=EDIT)
def delete_fixture(fixture_id: str):
    ctx = get_context()
    ctx.engine.remove_fixture(fixture_id)
    ctx.persist_room()
    return {"ok": True}


class SafetyZoneIn(BaseModel):
    id: Optional[str] = None
    name: str
    min_corner: Vec3In
    max_corner: Vec3In
    enabled: bool = True


@router.post("/room/safety-zones", dependencies=EDIT)
def add_safety_zone(payload: SafetyZoneIn):
    ctx = get_context()
    zone_id = payload.id or f"zone-{uuid.uuid4().hex[:8]}"
    zone = SafetyZone(
        id=zone_id,
        name=payload.name,
        min_corner=Vec3(**payload.min_corner.model_dump()),
        max_corner=Vec3(**payload.max_corner.model_dump()),
        enabled=payload.enabled,
    )
    ctx.engine.room.add_safety_zone(zone)
    ctx.persist_room()
    return zone.to_dict()


@router.delete("/room/safety-zones/{zone_id}", dependencies=EDIT)
def delete_safety_zone(zone_id: str):
    ctx = get_context()
    ctx.engine.room.remove_safety_zone(zone_id)
    ctx.persist_room()
    return {"ok": True}


class RoomObjectIn(BaseModel):
    id: Optional[str] = None
    name: str
    kind: str  # "wall" | "person" | "box" | "surface"
    position: Vec3In
    end_position: Optional[Vec3In] = None
    thickness: float = 0.1
    width: float = 0.5
    depth: float = 0.5
    height: float = 1.8
    color: str = "#888888"


VALID_OBJECT_KINDS = {"wall", "person", "box", "surface"}


@router.post("/room/objects", dependencies=EDIT)
def add_room_object(payload: RoomObjectIn):
    if payload.kind not in VALID_OBJECT_KINDS:
        raise HTTPException(400, f"unknown object kind {payload.kind!r}")
    if payload.kind == "wall" and payload.end_position is None:
        raise HTTPException(400, "wall objects require end_position")
    ctx = get_context()
    object_id = payload.id or f"obj-{uuid.uuid4().hex[:8]}"
    obj = RoomObject(
        id=object_id,
        name=payload.name,
        kind=payload.kind,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
        end_position=ctx.engine.room.clamp_position(Vec3(**payload.end_position.model_dump())) if payload.end_position else None,
        thickness=payload.thickness,
        width=payload.width,
        depth=payload.depth,
        height=payload.height,
        color=payload.color,
    )
    ctx.engine.room.add_object(obj)
    ctx.persist_room()
    return obj.to_dict()


@router.put("/room/objects/{object_id}", dependencies=EDIT)
def update_room_object(object_id: str, payload: RoomObjectIn):
    ctx = get_context()
    if object_id not in ctx.engine.room.objects:
        raise HTTPException(404, "object not found")
    if payload.kind not in VALID_OBJECT_KINDS:
        raise HTTPException(400, f"unknown object kind {payload.kind!r}")
    obj = RoomObject(
        id=object_id,
        name=payload.name,
        kind=payload.kind,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
        end_position=ctx.engine.room.clamp_position(Vec3(**payload.end_position.model_dump())) if payload.end_position else None,
        thickness=payload.thickness,
        width=payload.width,
        depth=payload.depth,
        height=payload.height,
        color=payload.color,
    )
    ctx.engine.room.objects[object_id] = obj
    ctx.persist_room()
    return obj.to_dict()


@router.delete("/room/objects/{object_id}", dependencies=EDIT)
def delete_room_object(object_id: str):
    ctx = get_context()
    ctx.engine.room.remove_object(object_id)
    ctx.persist_room()
    return {"ok": True}


# ---------------------------------------------------------- animation points

class AnimationPointIn(BaseModel):
    id: Optional[str] = None
    name: str
    position: Vec3In


@router.post("/room/points", dependencies=EDIT)
def add_animation_point(payload: AnimationPointIn):
    ctx = get_context()
    point_id = payload.id or f"pt-{uuid.uuid4().hex[:8]}"
    point = AnimationPoint(
        id=point_id, name=payload.name,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
    )
    ctx.engine.room.add_animation_point(point)
    ctx.persist_room()
    return point.to_dict()


@router.put("/room/points/{point_id}", dependencies=EDIT)
def update_animation_point(point_id: str, payload: AnimationPointIn):
    ctx = get_context()
    if point_id not in ctx.engine.room.animation_points:
        raise HTTPException(404, "point not found")
    point = AnimationPoint(
        id=point_id, name=payload.name,
        position=ctx.engine.room.clamp_position(Vec3(**payload.position.model_dump())),
    )
    ctx.engine.room.add_animation_point(point)
    ctx.persist_room()
    return point.to_dict()


@router.delete("/room/points/{point_id}", dependencies=EDIT)
def delete_animation_point(point_id: str):
    ctx = get_context()
    ctx.engine.room.remove_animation_point(point_id)
    ctx.persist_room()
    return {"ok": True}


# ---------------------------------------------------------------- groups

@router.get("/groups")
def list_groups():
    return [g.to_dict() for g in get_context().engine.groups.values()]


class GroupIn(BaseModel):
    id: Optional[str] = None
    name: str
    fixture_ids: list[str] = []
    color: str = "#3a7bd5"


@router.post("/groups", dependencies=EDIT)
def create_group(payload: GroupIn):
    ctx = get_context()
    group_id = payload.id or f"grp-{uuid.uuid4().hex[:8]}"
    group = Group(id=group_id, name=payload.name, fixture_ids=payload.fixture_ids,
                  color=payload.color)
    ctx.engine.add_group(group)
    ctx.persist_groups()
    return ctx.engine.groups[group_id].to_dict()


@router.put("/groups/{group_id}", dependencies=EDIT)
def update_group(group_id: str, payload: GroupIn):
    ctx = get_context()
    if group_id not in ctx.engine.groups:
        raise HTTPException(status_code=404, detail="group not found")
    group = Group(id=group_id, name=payload.name, fixture_ids=payload.fixture_ids,
                  color=payload.color)
    ctx.engine.add_group(group)   # the "All lights" group keeps every fixture whatever is sent
    ctx.persist_groups()
    return ctx.engine.groups[group_id].to_dict()


@router.delete("/groups/{group_id}", dependencies=EDIT)
def delete_group(group_id: str):
    ctx = get_context()
    if group_id == ALL_GROUP_ID:
        raise HTTPException(status_code=400, detail="the All lights group is built in and cannot be deleted")
    ctx.engine.remove_group(group_id)
    ctx.persist_groups()
    return {"ok": True}


# ---------------------------------------------------------------- control

class TargetColorIn(BaseModel):
    target_id: str
    red: int
    green: int
    blue: int
    white: Optional[int] = None
    # limit to these zones (ids from the fixtures' profiles); None = every zone
    zones: Optional[list[str]] = None


@router.post("/control/color")
def control_color(payload: TargetColorIn):
    get_context().engine.set_color(payload.target_id, payload.red, payload.green,
                                    payload.blue, payload.white, zones=payload.zones)
    return {"ok": True}


class TargetValueIn(BaseModel):
    target_id: str
    value: int


class LightTargets(BaseModel):
    """One fixture/group id, or the whole selection. Dimmer/strobe/shutter
    take the whole selection in ONE call because whether the fixtures share a
    dimmer/strobe channel is judged across the whole set."""

    target_id: Optional[str] = None
    target_ids: Optional[list[str]] = None

    def targets(self) -> list[str]:
        ids = list(self.target_ids or [])
        if self.target_id:
            ids.append(self.target_id)
        if not ids:
            raise HTTPException(422, "target_id or target_ids is required")
        return ids


class LightValueIn(LightTargets):
    value: int
    # dimmer: brightness of just these zones (fixtures that declare zones); None = master dimmer
    # strobe: strobe just these zones (fixtures whose zones have strobe channels); None = everything
    zones: Optional[list[str]] = None


class ShutterIn(LightTargets):
    closed: bool


@router.post("/control/dimmer")
def control_dimmer(payload: LightValueIn):
    get_context().engine.set_dimmer(payload.targets(), payload.value, zones=payload.zones)
    return {"ok": True}


@router.post("/control/strobe")
def control_strobe(payload: LightValueIn):
    also = get_context().engine.set_strobe(payload.targets(), payload.value, zones=payload.zones)
    return {"ok": True, "also_strobed": also}   # zones that share a strobe channel with a chosen one


@router.post("/control/shutter")
def control_shutter(payload: ShutterIn):
    """Closed/Open buttons: closed = dark (values remembered), open = light on, no strobe."""
    get_context().engine.set_shutter(payload.targets(), payload.closed)
    return {"ok": True}


class PanTiltIn(BaseModel):
    target_id: str
    pan: int
    tilt: int
    pan_fine: int = 0
    tilt_fine: int = 0


@router.post("/control/pan-tilt", dependencies=SHOW)
def control_pan_tilt(payload: PanTiltIn):
    get_context().engine.set_raw_pan_tilt(
        payload.target_id, payload.pan, payload.tilt, payload.pan_fine, payload.tilt_fine
    )
    return {"ok": True}


class AimIn(BaseModel):
    target_id: str
    x: float
    y: float
    z: float
    allow_unsafe: bool = False


@router.post("/control/aim", dependencies=SHOW)
def control_aim(payload: AimIn):
    result = get_context().engine.aim_at_point(
        payload.target_id, Vec3(payload.x, payload.y, payload.z), payload.allow_unsafe
    )
    return result


class CustomControlIn(BaseModel):
    target_id: str
    label: str
    value: int


@router.post("/control/custom")
def control_custom(payload: CustomControlIn):
    get_context().engine.set_custom(payload.target_id, payload.label, payload.value)
    return {"ok": True}


@router.post("/control/blackout")
def control_blackout():
    get_context().engine.blackout()
    return {"ok": True}


# ---------------------------------------------------------------- animations

class KeyframeIn(BaseModel):
    time_s: float
    point_id: Optional[str] = None
    target_point: Optional[Vec3In] = None
    color: Optional[tuple[int, int, int]] = None
    dimmer: Optional[int] = None
    strobe: Optional[int] = None


class AnimationTrackIn(BaseModel):
    target_id: str
    keyframes: list[KeyframeIn] = []
    time_offset_s: float = 0.0


class AnimationIn(BaseModel):
    id: Optional[str] = None
    name: str
    tracks: list[AnimationTrackIn] = []
    loop: bool = True


@router.get("/animations")
def list_animations():
    ctx = get_context()
    return [a.to_dict() for a in ctx.animations.values()]


@router.post("/animations", dependencies=EDIT)
def save_animation(payload: AnimationIn):
    ctx = get_context()
    animation_id = payload.id or f"anim-{uuid.uuid4().hex[:8]}"
    tracks = [
        AnimationTrack(
            target_id=t.target_id,
            keyframes=[
                Keyframe(
                    time_s=k.time_s,
                    point_id=k.point_id,
                    target_point=Vec3(**k.target_point.model_dump()) if k.target_point else None,
                    color=k.color,
                    dimmer=k.dimmer,
                    strobe=k.strobe,
                )
                for k in t.keyframes
            ],
            time_offset_s=t.time_offset_s,
        )
        for t in payload.tracks
    ]
    animation = Animation(id=animation_id, name=payload.name, tracks=tracks, loop=payload.loop)
    ctx.animations[animation_id] = animation
    ctx.persist_animations()
    return animation.to_dict()


@router.delete("/animations/{animation_id}", dependencies=EDIT)
def delete_animation(animation_id: str):
    ctx = get_context()
    ctx.stop_animation(animation_id)
    ctx.animations.pop(animation_id, None)
    ctx.persist_animations()
    return {"ok": True}


@router.post("/animations/{animation_id}/play", dependencies=SHOW)
def play_animation(animation_id: str):
    ctx = get_context()
    if animation_id not in ctx.animations:
        raise HTTPException(404, "animation not found")
    ctx.play_animation(animation_id)
    return {"ok": True}


@router.post("/animations/{animation_id}/stop")
def stop_animation(animation_id: str):
    get_context().stop_animation(animation_id)
    return {"ok": True}


# ------------------------------------------------------------------ patterns
#
# "Basic" freestyler-style animations: a raw pan/tilt movement shape sent
# straight to the fixture(s), no room-space calculation and no color (that's
# a separate, later concern) -- see PatternAnimation/PatternPlayer.

class PatternIn(BaseModel):
    id: Optional[str] = None
    name: str
    target_id: str
    shape: str = "circle"
    speed_hz: float = 0.2
    pan_center: int = 128
    tilt_center: int = 128
    pan_size: int = 80
    tilt_size: int = 80
    phase_deg: float = 0.0


@router.get("/patterns")
def list_patterns():
    return [p.to_dict() for p in get_context().patterns.values()]


@router.post("/patterns", dependencies=EDIT)
def save_pattern(payload: PatternIn):
    ctx = get_context()
    pattern_id = payload.id or f"pat-{uuid.uuid4().hex[:8]}"
    pattern = PatternAnimation(id=pattern_id, **payload.model_dump(exclude={"id"}))
    ctx.patterns[pattern_id] = pattern
    ctx.persist_patterns()
    return pattern.to_dict()


@router.delete("/patterns/{pattern_id}", dependencies=EDIT)
def delete_pattern(pattern_id: str):
    ctx = get_context()
    ctx.stop_pattern(pattern_id)
    ctx.patterns.pop(pattern_id, None)
    ctx.persist_patterns()
    return {"ok": True}


@router.post("/patterns/{pattern_id}/play", dependencies=SHOW)
def play_pattern(pattern_id: str):
    ctx = get_context()
    if pattern_id not in ctx.patterns:
        raise HTTPException(404, "pattern not found")
    ctx.play_pattern(pattern_id)
    return {"ok": True}


@router.post("/patterns/{pattern_id}/stop")
def stop_pattern(pattern_id: str):
    get_context().stop_pattern(pattern_id)
    return {"ok": True}


# ---------------------------------------------------------------- dmx / misc

@router.get("/dmx/ports")
def dmx_ports():
    return list_serial_ports()


def _dmx_status_payload(ctx) -> dict:
    status = ctx.dmx.status()
    status["mode"] = "dmx4all" if isinstance(ctx.dmx, Dmx4AllOutput) else "simulator"
    status["connect_error"] = ctx.dmx_error
    status.setdefault("link_lost", False)
    last = ctx.dmx_config or ctx.last_dmx_config
    status["last_port"] = last.port if last else None
    status["can_reconnect"] = last is not None
    if ctx.dmx_config is not None:
        status["port"] = ctx.dmx_config.port
        status["baud_rate"] = ctx.dmx_config.baud_rate
    else:
        status["port"] = None
        status["baud_rate"] = None
    return status


@router.get("/dmx/status")
def dmx_status():
    return _dmx_status_payload(get_context())


class DmxConnectIn(BaseModel):
    port: str
    baud_rate: int = 38400


@router.post("/dmx/connect")
def dmx_connect(payload: DmxConnectIn):
    """Swap the live output to a real DMX4ALL interface without
    restarting the backend -- from the UI's DMX Setup panel. A failed
    attempt leaves whatever was running (simulator or a previously
    working connection) untouched."""
    ctx = get_context()
    try:
        ctx.connect_dmx4all(payload.port, payload.baud_rate)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the caller, never a 500 crash
        raise HTTPException(400, f"failed to connect to {payload.port!r}: {exc}")
    return _dmx_status_payload(ctx)


class DmxReconnectIn(BaseModel):
    kill_other_holders: bool = False


@router.post("/dmx/reconnect")
def dmx_reconnect(payload: DmxReconnectIn = DmxReconnectIn()):
    """Re-open the last-used DMX4ALL port (after an unplug, a stalled write,
    or "Access is denied"), optionally killing other processes that hold
    the port first."""
    ctx = get_context()
    try:
        killed = ctx.reconnect_dmx4all(kill_other_holders=payload.kill_other_holders)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"reconnect failed: {exc}")
    status = _dmx_status_payload(ctx)
    status["killed"] = killed
    return status


@router.get("/dmx/holders")
def dmx_holders():
    """Processes that could be holding the dongle (what kill-holders would kill)."""
    return find_holders()


@router.post("/dmx/kill-holders")
def dmx_kill_holders():
    """Kill every other process that may be holding the DMX interface --
    lighting apps (FreeStyler, DMX-Configurator, ...) and stray copies of
    this backend. Frees our own handle first so it isn't the thing in the
    way; press Reconnect afterwards."""
    ctx = get_context()
    killed = ctx.kill_usb_holders(release_own_port=True)
    status = _dmx_status_payload(ctx)
    status["killed"] = killed
    return status


@router.get("/dmx/readback")
def dmx_readback(channels: str = "1-16"):
    """What the *dongle itself* is holding (not what we think we sent) --
    e.g. `?channels=6,7,20,21,33,34` or `?channels=1-16`. Also reports the
    interface's own blackout flag, which forces all outputs to zero."""
    ctx = get_context()
    if not isinstance(ctx.dmx, Dmx4AllOutput):
        raise HTTPException(400, "not connected to a DMX4ALL interface")
    wanted: list[int] = []
    for part in channels.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            wanted += range(int(lo), int(hi) + 1)
        elif part:
            wanted.append(int(part))
    if not wanted or len(wanted) > 128 or any(not 1 <= c <= 512 for c in wanted):
        raise HTTPException(400, "channels must be 1..128 values within 1-512")
    try:
        return {
            "blackout": ctx.dmx.read_blackout(),
            "device": {c: ctx.dmx.read_back(c) for c in wanted},
            "ours": {c: ctx.dmx.get_channel(c) for c in wanted},
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"read-back failed: {exc}")


@router.post("/dmx/disconnect")
def dmx_disconnect():
    ctx = get_context()
    ctx.disconnect_dmx4all()
    return _dmx_status_payload(ctx)


class RawChannelIn(BaseModel):
    channel: int
    value: int


@router.post("/dmx/raw")
def dmx_raw(payload: RawChannelIn):
    """Bypass fixtures/groups entirely and hardcode one channel -- the
    validation step from docs/DMX4ALL_PROTOCOL.md: e.g. set a moving
    head's dimmer channel to 255 and confirm the beam turns on before
    trusting anything built on top of the driver."""
    get_context().dmx.set_channel(payload.channel, payload.value)
    return {"ok": True}


@router.post("/dmx/raw/blackout")
def dmx_raw_blackout():
    get_context().dmx.blackout()
    return {"ok": True}


@router.get("/snapshot")
def snapshot():
    return get_context().snapshot()


# ---------------------------------------------------------------- config export/import

@router.get("/config/export")
def export_config():
    """The whole venue setup as one portable JSON document: room
    size/shape/fixture placement, groups, animations, patterns, sound
    config, and every fixture profile in use -- so importing it on another
    machine needs nothing else to know each fixture's DMX channels."""
    return get_context().export_config()


@router.post("/config/import", dependencies=EDIT)
def import_config(payload: dict):
    ctx = get_context()
    try:
        ctx.import_config(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, f"invalid config file: {exc}")
    return {**ctx.export_config(), "profile_updates": ctx.profile_notes}


# ---------------------------------------------------------------- saved rooms
#
# Whole-venue snapshots kept in the backend (the same document as a config export). Saving,
# loading and starting a new room are edit-mode actions; loading or starting a new room keeps the
# room being left as "_previous" so the switch can be undone.

class SavedRoomIn(BaseModel):
    name: str


@router.get("/rooms")
def list_saved_rooms():
    ctx = get_context()
    return {"current": ctx.engine.room.name, "rooms": ctx.storage.list_saved_rooms()}


@router.post("/rooms", dependencies=EDIT)
def save_room(payload: SavedRoomIn):
    ctx = get_context()
    try:
        saved = ctx.save_room_as(payload.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**saved, "profile_updates": ctx.profile_notes}


@router.post("/rooms/new", dependencies=EDIT)
def new_room(payload: SavedRoomIn):
    ctx = get_context()
    ctx.new_room(payload.name)
    return {"ok": True, "name": ctx.engine.room.name}


@router.post("/rooms/{room_id}/load", dependencies=EDIT)
def load_saved_room(room_id: str):
    ctx = get_context()
    try:
        ctx.load_saved_room(room_id)
    except KeyError:
        raise HTTPException(404, "no saved room with that id")
    except ValueError as exc:
        raise HTTPException(400, f"could not load that room: {exc}")
    return {"ok": True, "name": ctx.engine.room.name, "profile_updates": ctx.profile_notes}


@router.delete("/rooms/{room_id}", dependencies=EDIT)
def delete_saved_room(room_id: str):
    if not get_context().delete_saved_room(room_id):
        raise HTTPException(404, "no saved room with that id")
    return {"ok": True}
