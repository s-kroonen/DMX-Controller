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
needed -- useful for building rooms/fixtures/animations at a desk). To
send real DMX, set environment variables before starting the backend:

```bash
export DMX4ALL_PORT=/dev/ttyUSB0      # or COM3 on Windows
export DMX4ALL_PROTOCOL=passthrough    # or "framed" -- see docs/DMX4ALL_PROTOCOL.md
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**The DMX4ALL wire protocol is not yet verified against real hardware** --
see `docs/DMX4ALL_PROTOCOL.md` for how to capture and confirm it, and the
validation checklist to run through with a real fixture (e.g. an MHL108)
once you have the dongle plugged in. Only `backend/app/dmx/dmx4all.py`
needs to change once the real byte format is confirmed.

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
  simultaneously without switching views (freestyler-style). The
  Strobe/Shutter window has separate Dimmer and Strobe Speed sliders; on
  fixtures that multiplex both onto one physical channel (common on cheap
  PARs -- see the bundled `cheap-par-shared-dimmer-strobe-4ch` profile),
  the UI detects the shared channel and greys out whichever slider you
  didn't touch last, since only one of them is actually in effect.
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

- DMX4ALL protocol bytes are a best-effort placeholder (see above) until
  validated against real hardware with a USB capture.
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
