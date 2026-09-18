from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..context import get_context
from ..dmx.dmx4all import Dmx4AllOutput, list_serial_ports
from ..dmx.usb_procs import find_holders
from ..fixtures.qxf_import import parse_qxf
from ..fixtures.schema import CustomChannel, FixtureProfile
from ..groups.model import Group
from ..room.model import (
    FixtureInstance,
    Orientation,
    RoomDimensions,
    RoomObject,
    SafetyZone,
    Vec2,
    Vec3,
)
from ..show.animation import Animation, AnimationTrack, Keyframe

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- fixtures

@router.get("/fixtures/profiles")
def list_profiles():
    ctx = get_context()
    return [p.to_dict() for p in ctx.library.list()]


class CustomChannelIn(BaseModel):
    channel: int
    label: str
    default: int = 0
    min_value: int = 0
    max_value: int = 255


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


@router.post("/fixtures/profiles")
def create_or_update_profile(payload: FixtureProfileIn):
    """The fixture creator: define a new type, or edit a user-saved one."""
    ctx = get_context()
    profile_id = payload.id or f"custom-{uuid.uuid4().hex[:8]}"
    profile = FixtureProfile(
        id=profile_id,
        name=payload.name,
        manufacturer=payload.manufacturer,
        mode=payload.mode,
        channel_count=payload.channel_count,
        channels=payload.channels,
        custom_channels=[CustomChannel(**c.model_dump()) for c in payload.custom_channels],
        pan_range_deg=payload.pan_range_deg,
        tilt_range_deg=payload.tilt_range_deg,
        defaults=payload.defaults,
        fixture_type=payload.fixture_type,
    )
    ctx.library.save(profile)
    return profile.to_dict()


@router.delete("/fixtures/profiles/{profile_id}")
def delete_profile(profile_id: str):
    ctx = get_context()
    ctx.library.delete(profile_id)
    return {"ok": True}


@router.post("/fixtures/import-qxf")
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


@router.put("/room")
def update_room(payload: RoomIn):
    ctx = get_context()
    ctx.engine.room.name = payload.name
    ctx.engine.room.dimensions = RoomDimensions(**payload.dimensions.model_dump())
    ctx.engine.room.floor_points = [Vec2(**p.model_dump()) for p in payload.floor_points]
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


@router.post("/room/fixtures")
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
        position=Vec3(**payload.position.model_dump()),
        orientation=Orientation(**payload.orientation.model_dump()),
        group_ids=payload.group_ids,
        inverted_pan=payload.inverted_pan,
        inverted_tilt=payload.inverted_tilt,
    )
    ctx.engine.add_fixture(instance)
    ctx.persist_room()
    return instance.to_dict()


@router.put("/room/fixtures/{fixture_id}")
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
        position=Vec3(**payload.position.model_dump()),
        orientation=Orientation(**payload.orientation.model_dump()),
        group_ids=payload.group_ids,
        inverted_pan=payload.inverted_pan,
        inverted_tilt=payload.inverted_tilt,
    )
    ctx.engine.room.fixtures[fixture_id] = instance
    ctx.persist_room()
    return instance.to_dict()


@router.delete("/room/fixtures/{fixture_id}")
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


@router.post("/room/safety-zones")
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


@router.delete("/room/safety-zones/{zone_id}")
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


@router.post("/room/objects")
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
        position=Vec3(**payload.position.model_dump()),
        end_position=Vec3(**payload.end_position.model_dump()) if payload.end_position else None,
        thickness=payload.thickness,
        width=payload.width,
        depth=payload.depth,
        height=payload.height,
        color=payload.color,
    )
    ctx.engine.room.add_object(obj)
    ctx.persist_room()
    return obj.to_dict()


@router.put("/room/objects/{object_id}")
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
        position=Vec3(**payload.position.model_dump()),
        end_position=Vec3(**payload.end_position.model_dump()) if payload.end_position else None,
        thickness=payload.thickness,
        width=payload.width,
        depth=payload.depth,
        height=payload.height,
        color=payload.color,
    )
    ctx.engine.room.objects[object_id] = obj
    ctx.persist_room()
    return obj.to_dict()


@router.delete("/room/objects/{object_id}")
def delete_room_object(object_id: str):
    ctx = get_context()
    ctx.engine.room.remove_object(object_id)
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


@router.post("/groups")
def create_group(payload: GroupIn):
    ctx = get_context()
    group_id = payload.id or f"grp-{uuid.uuid4().hex[:8]}"
    group = Group(id=group_id, name=payload.name, fixture_ids=payload.fixture_ids,
                  color=payload.color)
    ctx.engine.add_group(group)
    ctx.persist_groups()
    return group.to_dict()


@router.delete("/groups/{group_id}")
def delete_group(group_id: str):
    ctx = get_context()
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


@router.post("/control/color")
def control_color(payload: TargetColorIn):
    get_context().engine.set_color(payload.target_id, payload.red, payload.green,
                                    payload.blue, payload.white)
    return {"ok": True}


class TargetValueIn(BaseModel):
    target_id: str
    value: int


@router.post("/control/dimmer")
def control_dimmer(payload: TargetValueIn):
    get_context().engine.set_dimmer(payload.target_id, payload.value)
    return {"ok": True}


@router.post("/control/strobe")
def control_strobe(payload: TargetValueIn):
    get_context().engine.set_strobe(payload.target_id, payload.value)
    return {"ok": True}


class PanTiltIn(BaseModel):
    target_id: str
    pan: int
    tilt: int
    pan_fine: int = 0
    tilt_fine: int = 0


@router.post("/control/pan-tilt")
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


@router.post("/control/aim")
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
    target_point: Optional[Vec3In] = None
    color: Optional[tuple[int, int, int]] = None
    dimmer: Optional[int] = None
    strobe: Optional[int] = None


class AnimationTrackIn(BaseModel):
    target_id: str
    keyframes: list[KeyframeIn] = []


class AnimationIn(BaseModel):
    id: Optional[str] = None
    name: str
    tracks: list[AnimationTrackIn] = []
    loop: bool = True


@router.get("/animations")
def list_animations():
    ctx = get_context()
    return [a.to_dict() for a in ctx.animations.values()]


@router.post("/animations")
def save_animation(payload: AnimationIn):
    ctx = get_context()
    animation_id = payload.id or f"anim-{uuid.uuid4().hex[:8]}"
    tracks = [
        AnimationTrack(
            target_id=t.target_id,
            keyframes=[
                Keyframe(
                    time_s=k.time_s,
                    target_point=Vec3(**k.target_point.model_dump()) if k.target_point else None,
                    color=k.color,
                    dimmer=k.dimmer,
                    strobe=k.strobe,
                )
                for k in t.keyframes
            ],
        )
        for t in payload.tracks
    ]
    animation = Animation(id=animation_id, name=payload.name, tracks=tracks, loop=payload.loop)
    ctx.animations[animation_id] = animation
    ctx.persist_animations()
    return animation.to_dict()


@router.delete("/animations/{animation_id}")
def delete_animation(animation_id: str):
    ctx = get_context()
    ctx.stop_animation(animation_id)
    ctx.animations.pop(animation_id, None)
    ctx.persist_animations()
    return {"ok": True}


@router.post("/animations/{animation_id}/play")
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
    return get_context().engine.snapshot()
