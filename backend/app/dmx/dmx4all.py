"""DMX4ALL Mini-USB-DMX interface driver.

Verified against real hardware: FTDI VID 0403 / PID C850, reports itself as
"USB DMX-Interface V3.36 (c) 2000-2004 Markus Siwek" on a virtual COM port.
Official reference: "DMX4ALL PC-Interface Interface-Commands" (2011).

Wire protocol:

  * 38400 baud, 8N1, no handshake.
  * "C?" -> "G"                      connection check
  * Block write  0xFF <start_lo> <start_hi> <count> <data...>  -> "G"
        start is 0-based, count <= 255. This is what we use for output: it
        is acknowledged, so a dropped/garbled write is detectable.
  * ASCII write  "C005L240" -> "G"   (channel 005 0-based, value 240)
  * Read back    "C005?"    -> "240G"
  * "B1" / "B0" -> "G"               blackout on / off, "B?" -> "0G"/"1G"
  * "N?" -> "224G"                   number of DMX-OUT slots the device emits

NOT supported on this firmware: the 0xE2/0xE3 "fast mode" single-channel
writes (unacknowledged). They do nothing here, and an earlier version of
this driver that used them left the device buffer full of its own header
bytes -- so don't reintroduce them.

Gotchas found on real hardware:
  * The interface can be left in BLACKOUT (outputs zero no matter what is
    written). We send B0 on connect.
  * It holds the last value of every slot and generates DMX itself, so
    only changes need sending; a slow full refresh of the low channels
    guards against a device reset.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Optional

from .interface import DMX_CHANNELS, DmxOutput

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - pyserial may not be installed in dev
    serial = None  # type: ignore


BLOCK_HEADER = 0xFF
MAX_BLOCK = 255
ACK = b"G"


@dataclasses.dataclass
class Dmx4AllConfig:
    port: str
    baud_rate: int = 38400
    # Bounds every read (ack wait) and write so an unplugged/stalled dongle
    # raises quickly instead of blocking the frame loop (and the API thread
    # that stops it).
    timeout_s: float = 0.5
    write_timeout_s: float = 0.5
    # Channels 1..refresh_channels are re-sent in full every
    # refresh_interval_s; anything above is only sent when it changes.
    refresh_channels: int = 64
    refresh_interval_s: float = 2.0
    handshake: bool = True


def block_write_bytes(start_channel: int, values: bytes) -> bytes:
    """One acknowledged block-write packet. start_channel is 1-indexed."""
    if not 1 <= len(values) <= MAX_BLOCK:
        raise ValueError(f"block must be 1..{MAX_BLOCK} bytes, got {len(values)}")
    start = start_channel - 1
    if start < 0 or start + len(values) > DMX_CHANNELS:
        raise ValueError(f"block {start_channel}+{len(values)} outside 1..{DMX_CHANNELS}")
    return bytes((BLOCK_HEADER, start & 0xFF, start >> 8, len(values))) + bytes(values)


MAX_GAP = 8  # unchanged channels bridged inside one block (a new block costs ~4 bytes + an ack)


def plan_blocks(frame: bytes, changed: set[int]) -> list[tuple[int, bytes]]:
    """Turn a set of changed 0-based indices into (start_channel, data)
    blocks: runs of changes (bridging gaps up to MAX_GAP), split so a block
    is <=255 bytes and never straddles the 256-channel bank boundary."""
    blocks: list[tuple[int, bytes]] = []
    run_start: Optional[int] = None
    prev: Optional[int] = None

    def flush(lo: int, hi: int) -> None:
        for chunk_start in range(lo, hi + 1, MAX_BLOCK):
            blocks.append((chunk_start + 1, bytes(frame[chunk_start:min(chunk_start + MAX_BLOCK, hi + 1)])))

    for i in sorted(changed):
        new_run = (
            run_start is None
            or i - prev > MAX_GAP
            or i // 256 != run_start // 256
        )
        if new_run:
            if run_start is not None:
                flush(run_start, prev)
            run_start = i
        prev = i
    if run_start is not None:
        flush(run_start, prev)
    return blocks


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
        self._io_lock = threading.Lock()  # one serial transaction at a time
        self._last_sent: Optional[bytearray] = None
        self._last_refresh = 0.0
        self.link_lost = False

    # -- connection -----------------------------------------------------

    def open(self) -> None:
        self._serial = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.config.timeout_s,
            write_timeout=self.config.write_timeout_s,
        )
        self._last_sent = None  # force a full refresh on the first frame
        if self.config.handshake:
            try:
                self._handshake()
            except Exception:
                self.close()
                raise

    def _command(self, data: bytes, reply_len: int = 1) -> bytes:
        """One serial transaction: write, then read the reply. Caller must
        hold _io_lock (or be single-threaded, as during open())."""
        assert self._serial is not None
        self._serial.reset_input_buffer()
        self._serial.write(data)
        self._serial.flush()
        return self._serial.read(reply_len)

    def _expect_ack(self, data: bytes, what: str) -> None:
        reply = self._command(data)
        if reply != ACK:
            raise RuntimeError(
                f"DMX4ALL did not acknowledge {what} on {self.config.port} "
                f"(expected {ACK!r}, got {reply!r})"
            )

    def _handshake(self) -> None:
        self._expect_ack(b"C?", "the handshake -- wrong port/baud, or not a DMX4ALL interface:")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def start(self) -> None:
        if self._serial is None:
            self.open()
        # Push our full state to the device *before* releasing blackout, so
        # whatever stale data it was holding never reaches the fixtures.
        try:
            self._send_frame(self.snapshot())
            self.set_blackout(False)
        except Exception:
            self.close()
            raise
        super().start()

    def stop(self) -> None:
        super().stop()
        self.close()

    # -- device state ---------------------------------------------------

    def set_blackout(self, enabled: bool) -> None:
        """The interface's own blackout: forces all outputs to zero
        regardless of the buffer. The interface can power up / be left in
        this state, so we always send B0 on connect."""
        with self._io_lock:
            self._expect_ack(b"B1" if enabled else b"B0", "blackout")

    def read_blackout(self) -> bool:
        with self._io_lock:
            reply = self._command(b"B?", 2)
        if reply not in (b"0G", b"1G"):
            raise RuntimeError(f"unexpected blackout reply {reply!r}")
        return reply == b"1G"

    def read_back(self, channel: int) -> int:
        """The value the *device* is holding for a channel (1-indexed) --
        independent of what we think we sent."""
        with self._io_lock:
            reply = self._command(f"C{channel - 1:03d}?".encode(), 4)
        if not (reply.endswith(ACK) and reply[:-1].isdigit()):
            raise RuntimeError(f"unexpected read-back reply {reply!r}")
        return int(reply[:-1])

    def status(self) -> dict:
        status = super().status()
        status["link_lost"] = self.link_lost
        return status

    # -- frame output ---------------------------------------------------

    def frame_blocks(self, frame: bytes, now: Optional[float] = None) -> list[tuple[int, bytes]]:
        """Blocks to write for this frame: changed channels, plus a full
        refresh of the low channels when due (or on the first frame).
        Updates the sent-state, so call once per frame actually written."""
        assert len(frame) == DMX_CHANNELS
        now = time.monotonic() if now is None else now
        low = set(range(min(self.config.refresh_channels, DMX_CHANNELS)))
        if self._last_sent is None:
            # Nothing known about the device's state: send the low channels
            # and any non-zero channel above them.
            self._last_sent = bytearray(DMX_CHANNELS)
            changed = low | {i for i in range(DMX_CHANNELS) if frame[i]}
            self._last_refresh = now
        else:
            changed = {i for i in range(DMX_CHANNELS) if frame[i] != self._last_sent[i]}
            if now - self._last_refresh >= self.config.refresh_interval_s:
                changed |= low
                self._last_refresh = now
        blocks = plan_blocks(frame, changed)
        for start_channel, data in blocks:
            self._last_sent[start_channel - 1:start_channel - 1 + len(data)] = data
        return blocks

    def _send_frame(self, frame: bytes) -> None:
        if self._serial is None:
            raise RuntimeError("serial port not open")
        blocks = self.frame_blocks(frame)
        try:
            with self._io_lock:
                for start_channel, data in blocks:
                    self._expect_ack(block_write_bytes(start_channel, data),
                                     f"block write at channel {start_channel}")
        except Exception:
            self._last_sent = None  # unknown what arrived; resend everything
            self.link_lost = True
            raise
        self.link_lost = False


def list_serial_ports() -> list[dict]:
    """Enumerate available COM/serial ports for the UI's port picker."""
    if serial is None:
        return []
    from serial.tools import list_ports  # type: ignore

    return [
        {"device": p.device, "description": p.description, "hwid": p.hwid}
        for p in list_ports.comports()
    ]
