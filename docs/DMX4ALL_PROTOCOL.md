# DMX4ALL Mini-USB-DMX interface -- protocol (verified on hardware)

Device: FTDI VID `0403` / PID `C850`, virtual COM port (COM7 here). The `I`
command reports `USB DMX-Interface V3.36 (c) 2000-2004 Markus Siwek`.
Reference: *DMX4ALL PC-Interface Interface-Commands* (manufacturer PDF, 2011).

38400 baud, 8N1, no handshake. Commands we use:

| Purpose | Send | Reply |
|---|---|---|
| Connection check | `C?` | `G` |
| **Block write (our output path)** | `FF <start_lo> <start_hi> <count> <data...>` (0-based start, count <= 255) | `G` |
| Write one channel (ASCII) | `C005L240` (0-based channel, value) | `G` |
| Read a channel back | `C005?` | `240G` |
| Blackout on / off / query | `B1` / `B0` / `B?` | `G` / `G` / `0G`,`1G` |
| Number of DMX-OUT slots | `N?` | e.g. `224G` |

Manufacturer example: channels 10-15 = 100,120,140,150,255,10 ->
`FF 09 00 06 64 78 8C 96 FF 0A`.

The interface generates the DMX512 signal itself and holds the last value of
every slot, so only changes need sending. `Dmx4AllOutput` writes changed runs
as acknowledged blocks, does a slow full refresh of the low channels, and
raises if any block isn't answered with `G`.

## Things that bit us on real hardware

- **Blackout.** The interface can be left in blackout (`B?` -> `1G`), which
  forces all outputs to zero regardless of what is written. The driver sends
  its full state and then `B0` on every connect.
- **Fast mode does not work on this firmware.** The PDF also lists
  unacknowledged `E2 <ch> <val>` / `E3 <ch> <val>` single-channel writes
  (used by the ofxDmx4All addon). On V3.36 they do nothing, and an earlier
  version of this driver that used them left the device buffer full of its own
  header bytes (`226, 31, 0, 226, 32, 255, ...`), which fixtures saw as random
  values (a Beamz MHL108 kept falling into auto mode). Don't reintroduce them.
- **XLR pin assignment.** The interface has a setup menu (`S`; options
  `1` 19200 baud, `2` 38400 baud, `3` International pinout, `4` Martin pinout;
  `I` shows the current one). Ours was on **Martin**, which swaps the data
  lines versus standard DMX: buffer and blackout were all correct but the
  fixtures stayed dark. Fixed by `S` then `3` (stored on the dongle, persists
  across replugs). `S` leaves the dongle waiting for a key -- it answers
  nothing, not even `C?`, until you pick an option.
- **Verify with read-back, not by eyeballing lights.** `GET /api/dmx/readback?channels=1-40`
  returns what the dongle holds next to what the backend thinks it sent.
- Only one process can hold the COM port; see the DMX Setup panel's Reconnect
  and "Kill other USB/DMX processes" buttons.

Implementation: `backend/app/dmx/dmx4all.py` (the only file that touches the wire).

## Hardware validation checklist

- [x] Port opens at 38400 and answers `C?` with `G`.
- [x] Block writes are acknowledged and read back identically.
- [x] Blackout is released on connect (`B?` -> `0G`).
- [ ] All three fixtures show red.
- [ ] Beamz stays out of auto mode.
- [ ] Pan/tilt sweep smoothly (16-bit fine channels).
