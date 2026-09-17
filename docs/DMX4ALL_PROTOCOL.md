# DMX4ALL Mini-USB Interface — protocol investigation notes

The DMX4ALL Mini-USB interface enumerates as a virtual COM port but does not
speak Enttec's "Open DMX USB" / DMX-USB-PRO framing. Nobody has captured the
real byte stream for this project yet — this file tracks how to do that and
what to try in the meantime. The driver that consumes this is
`backend/app/dmx/dmx4all.py`; only that file needs to change once the real
protocol is known.

## How to capture the real protocol

1. **USB capture (best option).** On Windows, use Wireshark with USBPcap
   enabled, filter to the DMX4ALL's USB device, and run FreeStyler or
   DMX-Configurator (whichever officially supports the dongle) driving a
   fixture through a simple, recognizable pattern (e.g. dimmer channel 1
   sweeping 0→255). The captured bulk/interrupt OUT transfers are the exact
   bytes sent per frame. Compare frame lengths and headers across a few
   frames to find the fixed vs. variable parts.

2. **Serial capture (if it truly is a plain virtual COM port).** On Linux/
   macOS, a null-modem or a tool like `socat`/`com0com` can sit between the
   vendor app and the device to log raw bytes — only works if the OS driver
   doesn't require a proprietary kernel-mode component.

3. **Prior art.** Check OLA (Open Lighting Architecture) plugins and
   hobbyist GitHub repos for existing DMX4ALL / compatible-vendor USB-DMX
   implementations before reverse engineering from scratch — someone may
   have already done this.

## What to look for once you have a capture

- Baud rate the virtual COM port is opened at (try 250000 first — the DMX
  bus rate — then whatever the vendor software actually requests).
- Fixed header bytes (many vendor protocols start with a magic byte or two).
- Whether channel count is transmitted, or always assumed to be 512.
- Whether there's a checksum byte and how it's computed (sum, XOR, CRC).
- Fixed footer/terminator byte(s).
- Whether it always sends a full 512-channel frame or only up to the
  highest non-zero channel used.

## Current implementation status

`Dmx4AllOutput` in `backend/app/dmx/dmx4all.py` implements two candidate
framings, selectable via `Dmx4AllConfig.protocol`:

- `"passthrough"` — raw 512 bytes, no framing, DMX-bus baud rate. Try this
  first; some USB-DMX bridges genuinely are this simple.
- `"framed"` — `header + channel_count(u16 LE) + 512 data bytes +
  [checksum] + footer`, with header/footer/checksum all configurable. A
  reasonable starting guess shaped like Enttec/uDMX-style protocols if
  passthrough doesn't move the fixture.

Until validated, run the backend with `SimulatedDmxOutput` (the default)
for all UI/animation/IK development — it behaves identically from the API
layer up, so nothing above the DMX driver needs to change once the real
protocol is confirmed and wired in via `Dmx4AllOutput`.

## Validation checklist against the real MHL108

- [ ] Port opens without error at the chosen baud rate.
- [ ] Hardcoding dimmer channel to 255 turns the fixture's beam on.
- [ ] Hardcoding pan/tilt channels sweeps smoothly, not in visible steps,
      at the configured frame rate (try 30–44 Hz).
- [ ] Frame rate stays stable under sustained transmission (no drift/
      stutter after a few minutes).
