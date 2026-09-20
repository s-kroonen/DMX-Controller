"""SoundService: audio input + beat analysis + sound-to-light functions.

Owns the capture thread, the analyzer (rebuilt at the device's sample rate on
every open) and a ~40 Hz tick thread that runs the enabled sound functions.
Config (chosen input, gain, functions, ...) persists to data/audio.json.

Beat source: "audio" uses the beat recogniser; "tap" runs a metronome from the
operator's tap tempo (Freestyler-style) for music the recogniser can't follow.
Functions only see "beats since last tick" and a running beat index, so they
don't care which source produced them.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

import numpy as np

from .analyzer import AudioAnalyzer, AudioFeatures
from .capture import AudioCapture, find_device, list_devices
from .functions import (FUNCTION_TYPES, SOUND_MODES, SoundFunction, StepContext,
                        category_active, default_params, make_runtime, validate)

TICK_HZ = 40.0
TAP_RESET_S = 2.5   # a longer pause than this starts a new tap sequence


class SoundService:
    def __init__(self, engine: Any, storage: Any,
                 capture_factory: Optional[Callable[..., AudioCapture]] = None,
                 device_lister: Callable[[], list[dict]] = list_devices,
                 device_finder: Callable[..., Optional[dict]] = find_device):
        self.engine = engine
        self.storage = storage
        self._list_devices = device_lister
        self._find_device = device_finder

        self.gain = 1.0
        self.sensitivity = 1.0
        self.beat_source = "audio"       # "audio" | "tap"
        self.sound_mode = "off"          # "off" | "color" | "motion" | "both": which functions react
        self.tap_bpm = 0.0
        self.device: Optional[dict] = None   # {"id","name","is_loopback"} last selected
        self.functions: dict[str, SoundFunction] = {}
        self.errors: dict[str, str] = {}

        self.analyzer = AudioAnalyzer()
        make_capture = capture_factory or AudioCapture
        self.capture = make_capture(self._on_samples, on_open=self._on_open)

        self._runtimes: dict[str, Any] = {}
        self._tick_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

        self._seen_analyzer: Optional[AudioAnalyzer] = None
        self._seen_beats = 0
        self._beat_index = 0
        self._taps: list[float] = []
        self._next_tap_beat = 0.0
        self._last_beat_at = 0.0
        self._tap_beats_pending = 0

        self._load()
        if self.sound_mode != "off":
            self._ensure_tick()

    @property
    def sound_enabled(self) -> bool:
        return self.sound_mode != "off"

    @sound_enabled.setter
    def sound_enabled(self, on: bool) -> None:
        """Legacy on/off switch: on means both color and motion."""
        if on and self.sound_mode == "off":
            self.sound_mode = "both"
        elif not on:
            self.sound_mode = "off"

    # -- capture plumbing ---------------------------------------------------

    def _on_open(self, samplerate: int) -> None:
        self.analyzer = AudioAnalyzer(sample_rate=samplerate, gain=self.gain,
                                      sensitivity=self.sensitivity)

    def _on_samples(self, samples: np.ndarray) -> None:
        self.analyzer.process(samples)

    def devices(self) -> list[dict]:
        return self._list_devices()

    def select_device(self, device_id: str) -> dict:
        """Choose the input; if capture is running it switches over."""
        with self._lock:
            device = self._find_device(device_id, None)
            if device is None:
                raise KeyError(f"no audio input with id {device_id!r}")
            self.device = {"id": device["id"], "name": device["name"],
                           "is_loopback": device["is_loopback"]}
            was_running = self.capture.running
            self._persist()
        if was_running:
            self.start()
        return self.device

    def start(self) -> None:
        with self._lock:
            if self.device is None:
                raise ValueError("choose an audio input first")
            device = self._find_device(self.device["id"], self.device["name"])
            if device is None:
                raise KeyError(f"audio input {self.device['name']!r} is not available")
            self.capture.start(device)

    def stop(self) -> None:
        self.capture.stop()

    # -- config ------------------------------------------------------------

    def configure(self, gain: Optional[float] = None, sensitivity: Optional[float] = None,
                  beat_source: Optional[str] = None, sound_enabled: Optional[bool] = None,
                  sound_mode: Optional[str] = None) -> None:
        with self._lock:
            if gain is not None:
                self.gain = max(0.1, min(4.0, float(gain)))
                self.analyzer.gain = self.gain
            if sensitivity is not None:
                self.sensitivity = max(0.2, min(3.0, float(sensitivity)))
                self.analyzer.sensitivity = self.sensitivity
            if beat_source is not None:
                if beat_source not in ("audio", "tap"):
                    raise ValueError("beat_source must be 'audio' or 'tap'")
                self.beat_source = beat_source
            if sound_enabled is not None:
                self.sound_enabled = bool(sound_enabled)
            if sound_mode is not None:
                if sound_mode not in SOUND_MODES:
                    raise ValueError(f"sound_mode must be one of {', '.join(SOUND_MODES)}")
                self.sound_mode = sound_mode
            if sound_enabled is not None or sound_mode is not None:
                if self.sound_mode != "off":
                    self._ensure_tick()
                # let go of anything the new mode no longer covers (e.g. a strobe burst)
                for fn in self.functions.values():
                    if not category_active(self.sound_mode, fn.type):
                        self._release(fn)
            self._persist()

    # -- tap tempo -----------------------------------------------------------

    def tap(self, now: Optional[float] = None) -> float:
        """Register a tap; switches the beat source to 'tap'. Returns the BPM
        (0 until there are two taps)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._taps and now - self._taps[-1] > TAP_RESET_S:
                self._taps = []
            self._taps.append(now)
            self._taps = self._taps[-8:]
            if len(self._taps) >= 2:
                intervals = np.diff(self._taps)
                self.tap_bpm = float(max(30.0, min(300.0, 60.0 / float(np.mean(intervals)))))
            self.beat_source = "tap"
            self._next_tap_beat = now + (60.0 / self.tap_bpm if self.tap_bpm else 1e9)
            self._tap_beats_pending = 1  # the tap itself is a beat
            self._persist()
            return self.tap_bpm

    def set_bpm(self, bpm: float) -> None:
        with self._lock:
            self.tap_bpm = max(30.0, min(300.0, float(bpm)))
            self.beat_source = "tap"
            self._next_tap_beat = time.monotonic() + 60.0 / self.tap_bpm
            self._persist()

    # -- functions -----------------------------------------------------------

    def add_function(self, fn_type: str, targets: list[str], params: Optional[dict] = None,
                     enabled: bool = True) -> SoundFunction:
        if fn_type not in FUNCTION_TYPES:
            raise ValueError(f"unknown sound function type {fn_type!r}")
        with self._lock:
            fn = SoundFunction(id=f"snd-{uuid.uuid4().hex[:8]}", type=fn_type, targets=list(targets),
                               enabled=enabled, params={**default_params(fn_type), **(params or {})})
            self.functions[fn.id] = fn
            self._persist()
            return fn

    def update_function(self, fn_id: str, targets: Optional[list[str]] = None,
                        enabled: Optional[bool] = None, params: Optional[dict] = None) -> SoundFunction:
        with self._lock:
            fn = self.functions[fn_id]
            if targets is not None:
                self._release(fn)   # let go of the old targets first
                fn.targets = list(targets)
            if params is not None:
                fn.params.update(params)
            if enabled is not None:
                if fn.enabled and not enabled:
                    self._release(fn)
                fn.enabled = bool(enabled)
            self.errors.pop(fn_id, None)
            self._persist()
            return fn

    def remove_function(self, fn_id: str) -> None:
        with self._lock:
            fn = self.functions.pop(fn_id)
            self._release(fn)
            self._runtimes.pop(fn_id, None)
            self.errors.pop(fn_id, None)
            self._persist()

    def _release(self, fn: SoundFunction) -> None:
        runtime = self._runtimes.get(fn.id)
        if runtime is not None:
            try:
                runtime.release(fn, self.engine)
            except Exception:  # noqa: BLE001
                pass

    def _release_all(self) -> None:
        for fn in self.functions.values():
            self._release(fn)

    # -- tick loop -----------------------------------------------------------

    def _ensure_tick(self) -> None:
        if self._tick_thread and self._tick_thread.is_alive():
            return
        self._stop.clear()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True, name="sound-tick")
        self._tick_thread.start()

    def _tick_loop(self) -> None:
        period = 1.0 / TICK_HZ
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(period)
            now = time.monotonic()
            dt, last = now - last, now
            try:
                self.tick(dt, now)
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self.errors["_engine"] = f"{type(exc).__name__}: {exc}"

    def _collect_beats(self, now: float) -> tuple[int, AudioFeatures]:
        """(new beats since last call, features to hand the functions)."""
        analyzer = self.analyzer
        features = analyzer.features()
        if self.beat_source == "tap":
            beats = 0
            pending = self._tap_beats_pending
            if pending:
                beats += pending
                self._tap_beats_pending = 0
            if self.tap_bpm > 0:
                period = 60.0 / self.tap_bpm
                while now >= self._next_tap_beat:
                    beats += 1
                    self._next_tap_beat += period
                    if self._next_tap_beat < now - period:   # fell far behind (e.g. a stall): resync
                        self._next_tap_beat = now + period
            if beats:
                self._last_beat_at = now
            features.bpm = self.tap_bpm
            features.bpm_confidence = 1.0 if self.tap_bpm else 0.0
            features.beat_pulse = float(np.exp(-(now - self._last_beat_at) / 0.15)) if self._last_beat_at else 0.0
            return beats, features

        if analyzer is not self._seen_analyzer:        # device (re)opened: new analyzer, new count
            self._seen_analyzer = analyzer
            self._seen_beats = features.beat_count
        beats = max(0, features.beat_count - self._seen_beats)
        self._seen_beats = features.beat_count
        return beats, features

    def tick(self, dt: float, now: float) -> None:
        """One step of every enabled function (also called directly by tests)."""
        with self._lock:
            beats, features = self._collect_beats(now)
            self._beat_index += beats
            ctx = StepContext(engine=self.engine, features=features, beats=beats,
                              beat_index=self._beat_index, dt=dt, now=now)
            if not self.sound_enabled:
                return
            for fn in list(self.functions.values()):
                if not fn.enabled or validate(fn) or not category_active(self.sound_mode, fn.type):
                    continue
                runtime = self._runtimes.get(fn.id)
                if runtime is None:
                    runtime = self._runtimes[fn.id] = make_runtime(fn.type)
                try:
                    runtime.step(fn, ctx)
                    self.errors.pop(fn.id, None)
                except Exception as exc:  # noqa: BLE001 - e.g. a target fixture was deleted
                    self.errors[fn.id] = f"{type(exc).__name__}: {exc}"

    # -- status ----------------------------------------------------------------

    def status(self) -> dict:
        features = self.analyzer.features()
        bpm = self.tap_bpm if self.beat_source == "tap" else features.bpm
        return {
            "capture": self.capture.status(),
            "device": self.device,
            "features": features.to_dict(),
            "bpm": round(float(bpm), 1),
            "bpm_confidence": round(float(features.bpm_confidence), 2),
            "config": {"gain": self.gain, "sensitivity": self.sensitivity,
                       "beat_source": self.beat_source, "sound_enabled": self.sound_enabled,
                       "sound_mode": self.sound_mode, "tap_bpm": round(self.tap_bpm, 1)},
            "functions": [{**fn.to_dict(), "error": self.errors.get(fn.id)}
                          for fn in self.functions.values()],
            "engine_error": self.errors.get("_engine"),
        }

    # -- persistence -------------------------------------------------------------

    def to_dict(self) -> dict:
        """The full persisted config shape -- shared by _persist() and
        config export."""
        return {
            "device": self.device, "gain": self.gain, "sensitivity": self.sensitivity,
            "beat_source": self.beat_source, "tap_bpm": self.tap_bpm,
            "sound_mode": self.sound_mode,
            "functions": [fn.to_dict() for fn in self.functions.values()],
        }

    def _persist(self) -> None:
        self.storage.save_audio(self.to_dict())

    def _load(self) -> None:
        data = self.storage.load_audio()
        if data:
            self.apply_config(data)

    def import_config(self, data: dict) -> None:
        """Config import: stop capture first -- the device id and every
        function's targets may no longer make sense (different machine,
        different room), so don't leave stale runtimes pointing at fixtures
        that just disappeared. The operator picks the input and re-enables
        sound afterwards, same as a fresh setup."""
        with self._lock:
            self.stop()
            self._runtimes = {}
            self.errors = {}
            self.apply_config(data)
            self._persist()

    def apply_config(self, data: dict) -> None:
        """Apply a full config dict -- shared by startup load (from
        data/audio.json) and config import (from an uploaded bundle)."""
        self.device = data.get("device")
        self.gain = float(data.get("gain", 1.0))
        self.sensitivity = float(data.get("sensitivity", 1.0))
        self.beat_source = data.get("beat_source", "audio")
        self.tap_bpm = float(data.get("tap_bpm", 0.0))
        mode = data.get("sound_mode")
        if mode not in SOUND_MODES:   # files from before modes existed only had an on/off flag
            mode = "both" if data.get("sound_enabled") else "off"
        self.sound_mode = mode
        self.functions = {}
        for raw in data.get("functions", []):
            fn = SoundFunction.from_dict(raw)
            self.functions[fn.id] = fn
        self.analyzer.gain = self.gain
        self.analyzer.sensitivity = self.sensitivity
        self._next_tap_beat = time.monotonic() + (60.0 / self.tap_bpm if self.tap_bpm else 1e9)

    def shutdown(self) -> None:
        self._stop.set()
        self.capture.stop()
        if self._tick_thread:
            self._tick_thread.join(timeout=2.0)
        self._release_all()
