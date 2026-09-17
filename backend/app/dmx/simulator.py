"""In-memory DMX output used when no dongle is attached (dev / demo / tests).

Records the last N frames sent so the UI or a test can assert on what
would have gone out over the wire, without needing real hardware.
"""

from __future__ import annotations

import collections
from typing import Deque

from .interface import DmxOutput


class SimulatedDmxOutput(DmxOutput):
    def __init__(self, frame_hz: float = 40.0, history: int = 5):
        super().__init__(frame_hz=frame_hz)
        self.history: Deque[bytes] = collections.deque(maxlen=history)

    def _send_frame(self, frame: bytes) -> None:
        self.history.append(frame)
