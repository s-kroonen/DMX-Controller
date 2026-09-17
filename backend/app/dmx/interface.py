"""Abstract DMX output interface.

Everything above this layer (fixtures, IK, groups, animation, API) works
purely in terms of "512 channel values, 0-255, universe 1..N". Nothing
outside this package is allowed to touch a serial port directly. This is
what lets the simulator and the real DMX4ALL driver be swapped freely,
and is what the design doc calls out as the single point of hardware
contact.
"""

from __future__ import annotations

import abc
import threading
import time
from typing import Optional

DMX_CHANNELS = 512


class DmxOutput(abc.ABC):
    """A continuously-transmitted 512-channel DMX universe."""

    def __init__(self, frame_hz: float = 40.0):
        self.frame_hz = frame_hz
        self._buffer = bytearray(DMX_CHANNELS)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.frames_sent = 0
        self.last_frame_error: Optional[str] = None

    # -- public API -------------------------------------------------

    def set_channel(self, channel: int, value: int) -> None:
        """channel is 1-indexed (1..512), value is 0..255."""
        if not 1 <= channel <= DMX_CHANNELS:
            raise ValueError(f"channel {channel} out of range 1..{DMX_CHANNELS}")
        with self._lock:
            self._buffer[channel - 1] = max(0, min(255, int(value)))

    def set_channels(self, values: dict[int, int]) -> None:
        with self._lock:
            for ch, val in values.items():
                if not 1 <= ch <= DMX_CHANNELS:
                    raise ValueError(f"channel {ch} out of range 1..{DMX_CHANNELS}")
                self._buffer[ch - 1] = max(0, min(255, int(val)))

    def get_channel(self, channel: int) -> int:
        with self._lock:
            return self._buffer[channel - 1]

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buffer)

    def blackout(self) -> None:
        with self._lock:
            self._buffer = bytearray(DMX_CHANNELS)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # -- internals ----------------------------------------------------

    def _run_loop(self) -> None:
        period = 1.0 / self.frame_hz
        while not self._stop.is_set():
            start = time.monotonic()
            frame = self.snapshot()
            try:
                self._send_frame(frame)
                self.last_frame_error = None
                self.frames_sent += 1
            except Exception as exc:  # noqa: BLE001 - surface to status API
                self.last_frame_error = str(exc)
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, period - elapsed))

    @abc.abstractmethod
    def _send_frame(self, frame: bytes) -> None:
        """Send one full 512-channel frame. Called from the frame-loop thread."""

    def status(self) -> dict:
        return {
            "frame_hz": self.frame_hz,
            "frames_sent": self.frames_sent,
            "last_frame_error": self.last_frame_error,
            "running": bool(self._thread and self._thread.is_alive()),
        }
