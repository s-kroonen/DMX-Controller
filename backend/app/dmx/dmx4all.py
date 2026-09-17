"""DMX4ALL Mini-USB interface driver.

STATUS: experimental / unverified against real hardware in this repo.

The DMX4ALL Mini-USB-Interface enumerates as a virtual COM port but does
NOT speak the Enttec "Open DMX USB" / DMX-USB-PRO framing (0x7E ... 0xE7).
It uses a vendor-specific protocol. Nobody on this project has captured
the real byte stream yet (see docs/DMX4ALL_PROTOCOL.md for how to do
that), so this driver is written to be:

  1. Configurable, not hardcoded -- every framing detail below is a field
     on `Dmx4AllConfig` so a real USB capture can be dropped in without
     touching the send loop.
  2. Safe to run blind -- if the port can't be opened or a write fails,
     it raises so the frame-loop thread's `last_frame_error` surfaces it
     to the API/UI instead of silently doing nothing.
  3. Cheap to iterate on -- `raw_frame_bytes()` is the one function that
     needs to change once real protocol bytes are known; everything else
     (open/close, frame timing, error surfacing) already works.

Two candidate framings are implemented behind `protocol=`:

  "passthrough"   Sends the 512 channel bytes directly with no framing at
                  all, at DMX-bus baud (250000). This is what a "dumb"
                  USB-to-RS485 bridge would want, and is the first thing
                  worth trying against a real DMX4ALL Mini before assuming
                  the vendor protocol is more complex.

  "framed"        header_bytes + channel_count (2 bytes, little endian)
                  + 512 data bytes + optional checksum + footer_bytes.
                  This mirrors the general shape of most vendor USB-DMX
                  protocols (Enttec, uDMX, etc.) so it's a reasonable
                  starting guess if "passthrough" doesn't move the fixture.

Whichever one turns out to be correct (or if neither is and the real
protocol has to be captured with a USB analyzer / Wireshark+usbmon while
FreeStyler or DMX-Configurator drives the dongle), only this file needs
to change -- `DmxOutput` and everything above it is unaffected.
"""

from __future__ import annotations

import dataclasses
import struct
from typing import Literal, Optional

from .interface import DMX_CHANNELS, DmxOutput

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - pyserial may not be installed in dev
    serial = None  # type: ignore


Protocol = Literal["passthrough", "framed"]


@dataclasses.dataclass
class Dmx4AllConfig:
    port: str
    baud_rate: int = 250000
    protocol: Protocol = "passthrough"
    header_bytes: bytes = b"\x7e"
    footer_bytes: bytes = b"\xe7"
    use_checksum: bool = False
    timeout_s: float = 1.0


class Dmx4AllOutput(DmxOutput):
    def __init__(self, config: Dmx4AllConfig, frame_hz: float = 40.0):
        super().__init__(frame_hz=frame_hz)
        if serial is None:
            raise RuntimeError(
                "pyserial is not installed. `pip install pyserial` to use the "
                "real DMX4ALL driver, or use SimulatedDmxOutput instead."
            )
        self.config = config
        self._serial: Optional["serial.Serial"] = None

    def open(self) -> None:
        self._serial = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=self.config.timeout_s,
        )

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def start(self) -> None:
        if self._serial is None:
            self.open()
        super().start()

    def stop(self) -> None:
        super().stop()
        self.close()

    def raw_frame_bytes(self, frame: bytes) -> bytes:
        assert len(frame) == DMX_CHANNELS
        if self.config.protocol == "passthrough":
            return frame

        if self.config.protocol == "framed":
            body = struct.pack("<H", DMX_CHANNELS) + frame
            out = bytearray(self.config.header_bytes)
            out += body
            if self.config.use_checksum:
                out.append(sum(body) & 0xFF)
            out += self.config.footer_bytes
            return bytes(out)

        raise ValueError(f"unknown protocol {self.config.protocol!r}")

    def _send_frame(self, frame: bytes) -> None:
        if self._serial is None:
            raise RuntimeError("serial port not open")
        payload = self.raw_frame_bytes(frame)
        self._serial.write(payload)
        self._serial.flush()


def list_serial_ports() -> list[dict]:
    """Enumerate available COM/serial ports for the UI's port picker."""
    if serial is None:
        return []
    from serial.tools import list_ports  # type: ignore

    return [
        {"device": p.device, "description": p.description, "hwid": p.hwid}
        for p in list_ports.comports()
    ]
