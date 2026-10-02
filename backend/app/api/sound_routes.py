"""REST API for sound input, beat analysis and sound-to-light functions."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..audio.capture import AudioUnavailable
from ..audio.functions import function_types_for_ui
from ..context import get_context

router = APIRouter(prefix="/api/audio")


def _sound():
    return get_context().sound


def _check_desync(desync: dict) -> None:
    granularity = desync.get("granularity")
    if granularity is not None and granularity not in ("off", "group", "light"):
        raise HTTPException(400, "desync.granularity must be 'off', 'group' or 'light'")
    mode = desync.get("mode")
    if mode is not None and mode not in ("beat", "timer"):
        raise HTTPException(400, "desync.mode must be 'beat' or 'timer'")
    amount = desync.get("amount")
    if amount is not None and (not isinstance(amount, (int, float)) or amount < 0):
        raise HTTPException(400, "desync.amount must be a number >= 0")


@router.get("/devices")
def audio_devices():
    """Inputs to choose from. Loopback devices capture what this PC is playing."""
    try:
        return _sound().devices()
    except AudioUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.get("/status")
def audio_status():
    return _sound().status()


class SelectDeviceIn(BaseModel):
    device_id: str


@router.post("/select")
def audio_select(payload: SelectDeviceIn):
    try:
        device = _sound().select_device(payload.device_id)
    except AudioUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"device": device}


@router.post("/start")
def audio_start():
    try:
        _sound().start()
    except (ValueError, KeyError, AudioUnavailable) as exc:
        raise HTTPException(400, str(exc).strip("'\""))
    return _sound().status()


@router.post("/stop")
def audio_stop():
    _sound().stop()
    return _sound().status()


class ConfigIn(BaseModel):
    gain: Optional[float] = None
    sensitivity: Optional[float] = None
    beat_source: Optional[str] = None
    sound_enabled: Optional[bool] = None   # legacy on/off (on = both)
    sound_mode: Optional[str] = None       # off | color | motion | both


@router.post("/config")
def audio_config(payload: ConfigIn):
    runs_effects = payload.sound_enabled or payload.sound_mode not in (None, "off")
    if runs_effects and get_context().mode != "show":
        raise HTTPException(409, "Sound-to-light effects only run in show mode.")
    try:
        _sound().configure(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _sound().status()


@router.post("/tap")
def audio_tap():
    bpm = _sound().tap()
    return {"tap_bpm": round(bpm, 1)}


class BpmIn(BaseModel):
    bpm: float


@router.post("/bpm")
def audio_set_bpm(payload: BpmIn):
    _sound().set_bpm(payload.bpm)
    return _sound().status()


# ---------------------------------------------------------------- functions

@router.get("/function-types")
def audio_function_types():
    return function_types_for_ui()


class FunctionIn(BaseModel):
    type: str
    targets: list[str] = []
    params: dict = {}
    enabled: bool = True
    zones: list[str] = []   # restrict to these zones of the targets; empty = every zone
    # spread the chase across time instead of showing it to every light at once (uses_desync
    # functions only); see SoundFunction.desync. Omitted fields fall back to DEFAULT_DESYNC.
    desync: dict = {}


class FunctionPatch(BaseModel):
    targets: Optional[list[str]] = None
    params: Optional[dict] = None
    enabled: Optional[bool] = None
    zones: Optional[list[str]] = None
    desync: Optional[dict] = None


@router.post("/functions")
def audio_add_function(payload: FunctionIn):
    _check_desync(payload.desync)
    try:
        fn = _sound().add_function(payload.type, payload.targets, payload.params, payload.enabled,
                                   zones=payload.zones, desync=payload.desync)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return fn.to_dict()


@router.patch("/functions/{fn_id}")
def audio_update_function(fn_id: str, payload: FunctionPatch):
    if payload.desync is not None:
        _check_desync(payload.desync)
    fn = _sound().update_function(fn_id, payload.targets, payload.enabled, payload.params,
                                  zones=payload.zones, desync=payload.desync)
    return fn.to_dict()


@router.delete("/functions/{fn_id}")
def audio_remove_function(fn_id: str):
    _sound().remove_function(fn_id)
    return {"ok": True}


class ReorderIn(BaseModel):
    order: list[str]   # function ids, topmost/highest-priority first


@router.post("/functions/reorder")
def audio_reorder_functions(payload: ReorderIn):
    """Set the priority order: the first id wins when two enabled functions of the same category
    (color/motion) target the same fixture. Ids missing from `order` keep their place at the end."""
    functions = _sound().reorder_functions(payload.order)
    return [fn.to_dict() for fn in functions]
