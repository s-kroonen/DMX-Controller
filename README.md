# DMX Controller

A self-built, freestyler-style DMX lighting controller with real 3D room
mapping: aim moving heads/lasers at points in your actual room instead of
juggling raw pan/tilt values, with safety zones, groups, overlaid
RGB/strobe/pan-tilt control windows, a fixture creator, and a keyframed
animation engine -- served as a web app from a Python backend that talks to
a DMX4ALL Mini-USB interface.

See `dmx-controller-design.md`-equivalent design notes inline in the code;
the architecture follows the build order below.

## Architecture

```
backend/   FastAPI service: fixture profiles, room/IK/safety, show engine,
           animation engine, DMX4ALL serial driver, REST + WebSocket API
frontend/  Vanilla JS + Three.js web UI (no build step) served by the backend
docs/      DMX4ALL protocol investigation notes
```

Everything above `backend/app/dmx/` works purely in terms of channel
values -- fixtures, groups, the IK core, safety zones, and animations
never touch a serial port directly. Swap `SimulatedDmxOutput` for
`Dmx4AllOutput` (or vice versa) with zero changes anywhere else.

## Running it

**Windows (recommended): one command from the repo root**

```powershell
.
un.ps1                # real dongle on COM7 -> http://localhost:8000
.
un.ps1 -Port COM5     # a different COM port
.
un.ps1 -Sim           # no hardware (simulated output)
.
un.ps1 -Lan           # also reachable from a phone/tablet on the LAN
```

Only one process can hold the COM port. If the UI says the port is busy, close
FreeStyler / DMX-Configurator / any other copy of this backend (or use **DMX
Setup -> Kill other USB/DMX processes**), then **Reconnect**. Your patch,
groups and room are saved in `backend/data/` (git-ignored). After pulling
frontend changes, hard-refresh the browser (Ctrl+Shift+R) -- the JS is cached.

**Manual / other platforms:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000` (or `http://<this-pc's-LAN-IP>:8000` from
a phone/tablet on the same network -- no extra setup needed, it's a plain
responsive web app).

Run the test suite:

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

### Connecting the real DMX4ALL dongle

By default the backend runs against `SimulatedDmxOutput` (no hardware
needed -- useful for building rooms/fixtures/animations at a desk).

**Easiest path -- from the running UI, no restart needed:** start the
backend normally, open **DMX Setup** in the top bar, pick (or type) the
port, and hit Connect (the driver does the DMX4ALL `C?`/`G` handshake). A failed attempt reports the
error and falls back to the simulator automatically -- it never crashes
the backend, so it's safe to try ports one after another. The same panel has a **raw channel test** (set one
channel's value directly, bypassing fixtures/groups entirely) for the
"does anything move at all" sanity check before trusting anything built
on top of the driver.

**Alternative -- set it at startup via environment variables:**

```bash
export DMX4ALL_PORT=/dev/ttyUSB0      # or COM7 on Windows (baud defaults to 38400)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

A bad port here also falls back to the simulator instead of crashing the
backend -- check `GET /api/dmx/status` (or the DMX Setup panel) for
`connect_error` if lights aren't moving.

The DMX4ALL wire protocol (38400 baud, `C?`/`G` handshake, acknowledged
block writes, blackout release) is documented in `docs/DMX4ALL_PROTOCOL.md`; the handshake is verified
against a real dongle. Only `backend/app/dmx/dmx4all.py` touches the wire.

## What's here

- **Fixture profile system** (`app/fixtures/`): JSON fixture-type
  definitions (channel map, pan/tilt range, custom/unmapped channels),
  bundled profiles for a generic 16ch moving head, the Beamz MHL108 MKII,
  a 4ch RGB PAR, and a generic laser, plus a QLC+ `.qxf` importer so you
  can pull in fixtures from their community library instead of hand-typing
  every channel map. The in-app **Fixture Creator** lets you define new
  fixture types (including custom sliders that don't map to any built-in
  function) without touching a file.
- **Room model + IK core** (`app/room/`): real 3D fixture positions/
  mounting orientation, and `compute_pan_tilt()` -- a pure, unit-tested
  function that turns a 3D target point into pan/tilt DMX values (with
  fine-channel sub-degree resolution across the fixture's full measured
  mechanical range).
- **Safety zones**: axis-aligned no-go volumes. Every computed aim is
  checked against a ray/box intersection before it reaches hardware, and
  blocked (with the reason surfaced to the UI) instead of just clamped to
  a pan/tilt range like consumer software.
- **Show engine + groups** (`app/show/engine.py`, `app/groups/`): the live
  state for controlling one fixture or a whole group at once -- color,
  dimmer, strobe/shutter, raw pan/tilt, custom-channel sliders, and 3D
  aiming, all independent so RGB + strobe + pan/tilt can be driven
  simultaneously without switching views (freestyler-style). The Strobe/Shutter window is freestyler-style: Dimmer and Strobe sliders in
  0-100 % (plus Off/Slow/Med/Max strobe presets and Closed/Open), independent
  of what any fixture's DMX channels look like. The engine maps each percentage
  onto that fixture's real values via per-role `role_ranges` in its profile
  (e.g. the bundled 14ch head puts dimmer at DMX 10-134 and strobe at 135-239
  on ONE shared channel), so mixed fixtures behave alike. When every selected
  fixture shares one dimmer/strobe channel, touching strobe drops the dimmer to
  0 and touching the dimmer stops the strobe; in a mixed selection (e.g. a head
  plus a Beamz with separate channels) the dimmer is held and everything strobes
  together.
- **Animation engine** (`app/show/animation.py`): keyframes reference
  room-space target points plus color/dimmer/strobe, not raw channel
  values, so the IK core is what turns a keyframed path into correct
  pan/tilt every tick -- avoiding the ellipse/faceting distortion you get
  keyframing raw pan/tilt directly.
- **Room shape + objects** (`app/room/model.py`): a room is rarely a
  perfect rectangle, so `Room.floor_points` holds an arbitrary polygon
  (any number of sides, drawn as a 2D floor plan in the **Room Shape**
  editor) extruded up to a ceiling height for the 3D view; it falls back
  to a simple width/depth rectangle until a shape is drawn. The
  **Objects** editor adds visual/spatial reference objects into the scene
  -- wall segments, person-scale markers (for a sense of scale), box
  obstacles, and raised surfaces/platforms. These are purely visual
  reference, unlike Safety Zones, which actually block beams.
- **Web UI** (`frontend/`): draggable, overlaid RGB / strobe-shutter /
  pan-tilt / custom-channel control windows that all apply live to
  whatever fixture(s) or group is currently selected; a Three.js 3D scene
  of the room (arbitrary floor polygon, walls, objects) with fixtures and
  beams, click-to-aim on any wall/floor/ceiling surface; a fixture patch
  screen; the fixture creator; a 2D room-shape editor; an objects editor;
  a safety zone editor; and a keyframe animation editor. Three.js is
  vendored locally (`frontend/vendor/three/`) so the controller runs with
  no internet access at the venue.

## Known limitations / next steps

- Light-level behaviour of the bundled profiles is unverified against real fixtures
  except where noted in the profile.
- Safety zones are still axis-aligned boxes (not arbitrary polygons) --
  covers the common cases from the design doc without a full BSP/mesh
  volume model. Room *floor shape* is a full arbitrary polygon now, but
  walls are always vertical (no sloped ceilings/walls).
- Fixture placement is manual entry (position/orientation fields) rather
  than a calibration flow (aim at known points, solve for position) --
  the IK core and API already support arbitrary positions, so calibration
  can be added as a pure frontend feature later.
- AR room scanning is an explicit future phase (separate app), not
  attempted here -- rooms are built manually (2D floor plan + objects) in
  the web UI for now.
