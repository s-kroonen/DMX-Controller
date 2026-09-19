"""Audio input: device listing (including loopback) and a capture thread.

Uses PyAudioWPatch (a PyAudio fork with WASAPI loopback) on Windows.
"Loopback" devices capture what the computer is playing -- pick one when the
music comes from this PC (Spotify, a DJ app, a browser) instead of a
microphone. Every output device has a loopback twin.

(soundcard was tried first and rejected: its Windows backend corrupts the heap
on this setup, and a native crash takes the whole DMX backend down with it.)

WASAPI loopback delivers *no data at all* while nothing is playing, so the
capture loop never waits for a full buffer: PortAudio's callback drops blocks
into a queue, and when the device is quiet the loop feeds the analyzer
silence to keep its clock (and its beat/tempo decay) in step with real time.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Callable, Optional

import numpy as np

from .analyzer import HOP


class AudioUnavailable(RuntimeError):
    """The audio backend can't be used here (not Windows, library missing, ...)."""


def _pyaudio():
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(
            f"audio capture needs PyAudioWPatch (Windows): {exc}") from exc
    return pyaudio


def _device_id(name: str, is_loopback: bool) -> str:
    # PortAudio indices shift when devices come and go; names are stable.
    return f"{'loopback' if is_loopback else 'input'}:{name}"


def list_devices() -> list[dict]:
    """Every input we can capture from. Loopback devices (what this PC is
    playing) come first, the default output's loopback flagged as default."""
    pyaudio = _pyaudio()
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        try:
            default_out = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
        except Exception:  # noqa: BLE001
            default_out = None
        devices = []
        for lb in pa.get_loopback_device_info_generator():
            name = lb["name"].removesuffix(" [Loopback]")
            devices.append({
                "id": _device_id(name, True), "name": name,
                "channels": int(lb["maxInputChannels"]), "rate": int(lb["defaultSampleRate"]),
                "is_loopback": True, "is_default": name == default_out,
                "index": int(lb["index"]),
            })
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if (d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0
                    and not d.get("isLoopbackDevice")):
                devices.append({
                    "id": _device_id(d["name"], False), "name": d["name"],
                    "channels": int(d["maxInputChannels"]), "rate": int(d["defaultSampleRate"]),
                    "is_loopback": False, "is_default": False, "index": i,
                })
        devices.sort(key=lambda d: (not d["is_loopback"], not d["is_default"], d["name"].lower()))
        return devices
    finally:
        pa.terminate()


def find_device(device_id: Optional[str], device_name: Optional[str] = None) -> Optional[dict]:
    """Resolve a saved selection by id, falling back to name."""
    devices = list_devices()
    for d in devices:
        if device_id and d["id"] == device_id:
            return d
    for d in devices:
        if device_name and d["name"] == device_name:
            return d
    return None


class _PortAudioSource:
    """One open capture stream; PortAudio's callback thread fills a queue."""

    def __init__(self, device: dict):
        pyaudio = _pyaudio()
        self._pa = pyaudio.PyAudio()
        self.samplerate = int(device["rate"])
        self._channels = max(1, int(device["channels"]))
        self._queue: collections.deque[np.ndarray] = collections.deque(maxlen=512)
        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32, channels=self._channels, rate=self.samplerate,
                input=True, input_device_index=device["index"], frames_per_buffer=HOP,
                stream_callback=self._callback)
        except Exception:
            self._pa.terminate()
            raise

    def _callback(self, in_data, frame_count, time_info, status):
        block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self._channels)
        self._queue.append(block.mean(axis=1))
        return (None, 0)  # paContinue

    def read_available(self) -> Optional[np.ndarray]:
        chunks = []
        while self._queue:
            chunks.append(self._queue.popleft())
        return np.concatenate(chunks) if chunks else None

    def close(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            self._pa.terminate()


class AudioCapture:
    """Runs a capture thread that hands mono float32 blocks to `on_samples`.
    `on_open(samplerate)` fires once the device is open, before any samples,
    so the consumer can build its analyzer for the device's rate."""

    def __init__(self, on_samples: Callable[[np.ndarray], None],
                 on_open: Optional[Callable[[int], None]] = None,
                 source_factory: Optional[Callable[[dict], object]] = None):
        self.on_samples = on_samples
        self.on_open = on_open
        self._source_factory = source_factory or _PortAudioSource  # tests inject fakes
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.device: Optional[dict] = None
        self.samplerate: Optional[int] = None
        self.error: Optional[str] = None
        self.blocks = 0
        self.last_data_at: Optional[float] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, device: dict) -> None:
        self.stop()
        self._stop.clear()
        self.device = device
        self.error = None
        self.blocks = 0
        self.last_data_at = None
        self._thread = threading.Thread(target=self._run, args=(device,), daemon=True,
                                        name="audio-capture")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None

    def status(self) -> dict:
        idle = None if self.last_data_at is None else round(time.monotonic() - self.last_data_at, 2)
        return {
            "running": self.running,
            "device_id": self.device["id"] if self.device else None,
            "device_name": self.device["name"] if self.device else None,
            "is_loopback": bool(self.device and self.device.get("is_loopback")),
            "samplerate": self.samplerate,
            "error": self.error,
            "seconds_since_audio": idle,
        }

    # -- thread -------------------------------------------------------

    def _run(self, device: dict) -> None:
        source = None
        try:
            source = self._source_factory(device)
            self.samplerate = source.samplerate
            if self.on_open:
                self.on_open(source.samplerate)
            started = time.monotonic()
            fed = 0.0  # seconds of audio handed to the analyzer
            while not self._stop.is_set():
                data = source.read_available()
                now = time.monotonic()
                if data is not None and len(data):
                    self.on_samples(data)
                    fed += len(data) / source.samplerate
                    self.last_data_at = now
                    self.blocks += 1
                else:
                    time.sleep(0.004)
                # device quiet (loopback while nothing plays): keep the analyzer's clock moving
                deficit = (now - started) - fed
                quiet_for = now - (self.last_data_at or started)
                if deficit > 0.05 and quiet_for > 0.05:
                    self.on_samples(np.zeros(int(deficit * source.samplerate), dtype=np.float32))
                    fed += deficit
        except Exception as exc:  # noqa: BLE001 - surfaced through status()
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:  # noqa: BLE001
                    pass
