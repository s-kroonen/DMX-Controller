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
desktop/   Packages backend+frontend into a double-clickable Windows exe
           (pywebview native window + system tray, PyInstaller build)
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

## Packaged desktop app (Windows exe)

`desktop/` wraps the same backend + frontend into a double-clickable app --
no terminal, no venv, nothing to install on the target machine. It's the
same FastAPI server and the same web UI, not a rewrite; the exe just starts
the server in-process and shows it in a native window instead of a browser
tab.

- The server still binds `0.0.0.0`, so phones and other PCs on the LAN can
  connect exactly as with `run.ps1 -Lan` -- the tray icon's tooltip lists
  this machine's LAN addresses.
- The window opens via **pywebview**, which on Windows uses the Edge
  WebView2 runtime (full Chromium) -- the Three.js 3D view renders the same
  as in a normal Edge/Chrome tab. WebView2 ships with Windows 10/11 by
  default; on the rare machine without it, Windows offers to install it
  automatically the first time an app needs it.
- Closing the window hides it to a system tray icon instead of quitting --
  other devices may still be actively controlling lights through it. Use
  the tray icon's **Quit** to actually stop the server.
- Double-launching the exe never starts a second server (which would fight
  the first for the DMX port): it detects one already running and just
  opens another window onto it.
- Packaged data (room/groups/animations/fixtures/sound config) lives in
  `%LOCALAPPDATA%\DMXController\data` instead of `backend/data/`, since the
  install location isn't guaranteed writable and isn't a stable path for a
  onefile build. Running from source (`uvicorn`/`run.ps1`) is unaffected --
  it still uses `backend/data/`.

**Building it** (once, on Windows, with `backend/requirements.txt` and
`desktop/requirements.txt` both installed into the active venv):

```powershell
pip install -r backend\requirements.txt -r desktop\requirements.txt
pyinstaller desktop\dmx_controller.spec
```

Output: `dist\DMX Controller\DMX Controller.exe`. `desktop/server_lifecycle.py`
(the server start/stop/probe logic, no GUI dependency) has its own test
suite (`pytest desktop/tests`); the pywebview/tray window behavior itself
needs a real Windows display to verify by hand -- it can't run headless in
CI.

## What's here

- **Fixture profile system** (`app/fixtures/`): JSON fixture-type
  definitions (channel map, pan/tilt range, custom/unmapped channels),
  bundled profiles for a generic 16ch moving head, the Beamz MHL108 MKII,
  a 4ch RGB PAR, a generic laser, and a 2ch smoke machine, plus a QLC+
  `.qxf` importer so you
  can pull in fixtures from their community library instead of hand-typing
  every channel map. The in-app **Fixture Creator** lets you define new
  fixture types (including custom sliders that don't map to any built-in
  function) without touching a file.
- **Room model + IK core** (`app/room/`): real 3D fixture positions/
  mounting orientation, and `compute_pan_tilt()` -- a pure, unit-tested
  function that turns a 3D target point into pan/tilt DMX values (with
  fine-channel sub-degree resolution across the fixture's full measured
  mechanical range). A fixture profile's pan/tilt range isn't assumed to be
  360 -- it defaults to 540/270 and is set per-profile in the Fixture
  Creator (which can also load and edit an existing profile now, not just
  create new ones), so a fixture that physically pans further than a full
  turn maps its whole real range across 0-255 instead of wrapping early. A
  per-fixture pan/tilt calibration trim (`pan_offset_deg`/`tilt_offset_deg`,
  in the Details panel's General tab) corrects a fixture whose own
  mechanical zero is a little off, without having to re-derive its mounting
  yaw/pitch to compensate.
- **Safety zones**: axis-aligned no-go volumes. Every computed aim is
  checked against a ray/box intersection before it reaches hardware, and
  blocked (with the reason surfaced to the UI) instead of just clamped to
  a pan/tilt range like consumer software. Fixture and room-object
  positions are also clamped to the room's actual floor footprint and
  height on every save (`Room.clamp_position()`) -- a fixture living
  outside the walls the show doesn't have isn't a position the software
  should let you send to hardware.
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
- **Animation engine** (`app/show/animation.py`): two kinds, both
  movement-only for now (color is a separate, later concern):
  - **Pattern** animations (`PatternAnimation`/`PatternPlayer`) are
    freestyler-style -- a raw pan/tilt shape (circle, figure-8, linear,
    square) computed straight from elapsed time and sent directly to the
    fixture(s)' pan/tilt channels, no room-space math, no IK.
  - **Path** animations (`Animation`/`AnimationTrack`/`Keyframe`) are the
    calculated kind: keyframes reference room-space target points plus
    color/dimmer/strobe, not raw channel values, so the IK core is what
    turns a keyframed path into correct pan/tilt every tick -- avoiding
    the ellipse/faceting distortion you get keyframing raw pan/tilt
    directly. A keyframe can point at a named, reusable `AnimationPoint`
    (`Room.animation_points`, its own **3D Points** section in the
    Animations modal) instead of only a one-off coordinate, so several
    fixtures can share the same point set but each visit them in their
    own order/timing by just building their own track -- and each
    track's `time_offset_s` shifts its own playback clock within the
    same shared animation, for staging the same pattern per fixture
    instead of running everything in lockstep. Running several fixtures
    on genuinely different animations just means playing separate
    Animation objects independently.
- **Room shape + objects** (`app/room/model.py`): a room is rarely a
  perfect rectangle, so `Room.floor_points` holds an arbitrary polygon
  (any number of sides, drawn -- and redrawn -- as a 2D floor plan in the
  **Room Shape** editor, where you can click empty space to add a corner
  or drag an existing one to move it) extruded up to a ceiling height for
  the 3D view; it falls back to a simple width/depth rectangle until a
  shape is drawn. Editing the room's shape is deliberately confined to
  that 2D editor and never done in the 3D view: it's a bigger,
  consequential action (it can move everything relative to it), so
  `Room.apply_shape()` rescales every fixture, object, and safety zone
  proportionally on save -- anchored on the floor's center -- so they
  keep their relative placement instead of ending up outside the new
  walls or floating at the wrong height. The **Objects** editor adds
  visual/spatial reference objects into the scene -- wall segments,
  person-scale markers (for a sense of scale), box obstacles, and raised
  surfaces/platforms. These are purely visual reference, unlike Safety
  Zones, which actually block beams; both are edited only through their
  own modals for the same reason as the room shape.
- **Web UI** (`frontend/`): draggable, overlaid RGB / strobe-shutter /
  pan-tilt / custom-channel control windows that all apply live to
  whatever fixture(s) or group is currently selected; a Three.js 3D scene
  of the room (arbitrary floor polygon, walls, objects) with fixtures and
  beams, click-to-aim on any wall/floor/ceiling surface; a fixture patch
  screen; the fixture creator; a 2D room-shape editor; an objects editor;
  a safety zone editor; and a keyframe animation editor. Three.js is
  vendored locally (`frontend/vendor/three/`) so the controller runs with
  no internet access at the venue.
  - The 3D view is fixtures/objects only -- click a fixture directly in
    the scene (or via the sidebar) or a room object (person/box/surface --
    walls need two points, so they're not draggable this way) to attach a
    drag gizmo (XYZ arrows, like a 3D-slicer/CAD tool) and reposition it;
    the new position saves once you release (clamped into the room's actual
    footprint/height, same as every other write path). A Move/Yaw/Pitch
    toggle over the 3D view switches the gizmo between position and the two
    independent mounting-angle rings for a selected fixture: Yaw always
    rotates around world-vertical, Pitch rotates around the fixture's own
    (already-yawed) horizontal axis -- two separate rings rather than one
    combined one, since a single ring can only ever drive one axis, and two
    nested nodes keep the two rotations from tangling into each other.
    Room objects have no orientation, so Yaw/Pitch are disabled for them.
    Multi-select and group selections don't get a gizmo (there's no single
    position to drag). Room shape and safety zones are never edited here --
    see above for why.
  - The **Details** panel, under the fixture list in the sidebar (not a
    popup like everything else), loads the currently selected fixture's
    full data across three sub-tabs -- **Position** (X/Y/Z, mounting
    yaw/pitch), **General** (name/profile/patch address, pan/tilt
    inversion, and a pan/tilt calibration offset trim), and **Groups**
    (membership checkboxes) -- for editing or deleting that one fixture.
    It's collapsible, and whether it's collapsed persists across a refresh
    (localStorage, like the floating panels).
  - The **Groups** and **Fixtures** sections of the left menu manage
    themselves: **+ Add** creates a group (name, colour, which fixtures) or
    opens the Patch window for a new fixture, and the **Edit** button at
    the top of the menu shows a pencil and a cross on every entry (edit
    opens the group dialog or the Patch window filled in with that fixture;
    the cross asks, then deletes). Outside edit mode those buttons are
    hidden. The built-in All lights group can be edited (name, colour) but
    not deleted.
  - Fixtures render with a model shaped for their `fixture_type` --
    moving head (base/yoke/head), smoke machine (box + nozzle), PAR can
    (cylinder), or a plain box for anything else -- with a bright green
    arrow on every fixture showing which way it faces (its mounting
    `orientation.yaw_deg`). A moving head is drawn as a STATIC picture of
    the unit, never following live pan/tilt: base at the bottom, the display
    (blue panel) on the front with the green arrow pointing out of it, and
    the head parked pointing to its own LEFT. Because it never points where
    the beam points, it can't be mistaken for the real aim (the beam line
    is that). Mounting rotates the whole picture: standing = base down,
    hanging = upside down, wall = on its side.
  - Each floating control window remembers whether it's open/closed and
    where you left it (localStorage, per browser) across a page refresh.
    A closed window is never stranded -- the **Windows** menu in the top
    bar lists every one with its current state and reopens it on click.
    The 3D camera's position/orbit target persist the same way.

## Mounting and aiming (pan/tilt)

A moving head's **home** is the direction its beam takes at tilt centre: along the pan axis,
away from the base. That is what a fixture's **Mounting** sets (Patch form and the Details
panel; stored as `pitch_deg`, 0 = home down, 90 = horizontal, 180 = home up):

- **Standing** on the floor or a stage box: home is *up* (the default -- almost every head
  is set up this way).
- **Hanging** from a truss: home is *down*.
- **On a wall**: horizontal.

Tilt is the angle *away from home* (tilt+ leans toward the pan direction), so it never
depends on whether the unit stands or hangs. **Yaw** is the compass direction the front
(display side) faces, i.e. where the beam leans at pan centre. Use *Invert pan* /
*Invert tilt* only when a unit's channels genuinely run the other way (a head whose pan
swings counter-clockwise from above needs *Invert pan*). Targets are reached either
directly or with the head flipped over (pan + 180, negative tilt) when only that fits the
ranges. The bundled generic head and the Beamz MHL108 both tilt 180 degrees (up to level either
side, measured), so they cannot aim below the horizon: such a target is flagged out of range and the tilt stops
at level.

## Edit mode and show mode

The whole UI (desktop and phone) is in one of two modes, chosen with the **Edit | Show** switch at the
top. The backend holds the mode, so every screen agrees, and it refuses (HTTP 409) what the current
mode does not allow. It starts in edit mode.

- **Edit**: patch, move and delete fixtures, room shape, objects, safety zones, groups, the Fixture
  Creator, DMX setup, animation/pattern editing, points, config and saved rooms. The 3D drag
  gizmo is on. Effects cannot run and heads cannot be aimed.
- **Show**: run sound-to-light effects, play animations and patterns, aim moving heads (3D
  click-to-aim, the Pan / Tilt window, the phone's Aim screen). Nothing can be moved or edited: those
  buttons are simply not shown, and the sidebar Details are read-only.
- **Both**: colors, dimmer, strobe, custom channels, zones, blackout, selecting groups and fixtures.

Switching to edit stops running animations, patterns and sound-to-light, and asks first so it is
never done by accident during a show. `PUT /api/mode {"mode": "edit"|"show"}`; every snapshot and
websocket message carries `mode`. Markup follows it with `<body data-mode>` and the `.edit-only` /
`.show-only` classes (`frontend/src/mode.js`).

## Calibrating the heads

In edit mode the **Calibrate** window (header button) and, on the phone, the **Calibrate** tab check
where the heads really point. Choose a target point (X/Y/Z, +/- buttons, a saved animation point,
"Pick in 3D" on the desktop, or tap a surface on the phone's Aim tab), then **Aim all here**: every
pan/tilt head points at it with a dim red beam (~20 %), and a magenta marker shows the point in 3D. If
the beams do not meet, nudge that head's pan/tilt offset (0.1-5 deg steps; each nudge re-aims and is
saved), flip invert pan/tilt, **Solo** one head, or **Reset** to the offsets it had when the window
opened. Each row shows the computed pan/tilt angles and DMX values and warns when a point is out of
range (a head that stands on the floor cannot tilt below level, so choose a wall or ceiling point).

**Sweep** moves the point along a line (X or Y), a circle or through the saved points, and every head
follows it, so a head that is off visibly drifts away from the spot the others stay on; nudging
offsets during a sweep takes effect immediately. Closing the window (or switching to show mode) stops
the sweep and puts every light back as it was.

### Solving a head's mounting (and position) from several marks

One shared point can only show that something is off. **Solve mounting from marks...** (under each head in
the Calibrate view, desktop and phone) finds out what: choose a mark whose room position is known (a
room corner on the floor or at the ceiling, taken from the room shape, or a saved point), **Aim** the
head at it, steer the beam exactly onto the mark with the pan/tilt buttons (step sizes from the finest
16-bit step to a coarse one), **Record**. With 3+ marks in different directions **Solve** fits the
head's yaw, pitch, pan offset and tilt offset; with 5+ it can also fit the head's **position**, so a
head whose position was never measured can be located from the room size alone. It also tries the four
invert-pan/tilt combinations if the current ones do not explain the marks. The result shows the
average miss before and after, the miss per mark (a bad recording stands out) and warnings (marks
nearly in a line, a position far from the entered one); **Apply** writes it to the fixture, **Discard**
drops it. Head standing on the floor: use ceiling marks (it cannot tilt below level).

Limits: precision is the beam-centring precision of the operator plus the accuracy of the room
shape; the pan/tilt ranges in the profile are taken as right (a wrong range shows as a residual
that grows with the angle); on a head whose pan axis is vertical (hung or upright) yaw and pan
offset are the same turn, so the fit keeps the pan offset the head already had and puts the rest in
yaw. Maths in `backend/app/room/solver.py` (nonlinear least squares on beam directions, so the
head's two ways of reaching a point do not matter); endpoints `POST /api/calibration/solve|apply`.

Endpoints (edit mode only, so the show-mode aim guard is unchanged): `POST /api/calibration/aim|beam|
offsets|sweep|sweep/stop|pan-tilt`; the snapshot carries `calibration` (`sweeping`, `beam`). The raw
RGB / Strobe / Pan-Tilt / Zones / Custom windows stay available in edit mode as **Test tools**, closed
until opened; each mode remembers its own window layout. Header buttons Patch, Fixture Creator, Room
Shape, Objects and Safety Zones are now under one **Setup** menu.

## Saved rooms

Besides export/import of a file, the backend keeps named snapshots of the whole venue (room,
fixtures, groups, shows, sound config and the fixture profiles they need) in
`backend/data/saved_rooms/`. In **Config** (edit mode): save the current room under a name, load a
saved one, delete one, or start a **new empty room** (fixtures, groups, shows and effects are
cleared; the sound input and levels are kept). Loading or starting a new room first keeps the room
being left as "Before switching", so a switch can be undone. `GET /api/rooms`, `POST /api/rooms`
(`{"name"}`), `POST /api/rooms/new`, `POST /api/rooms/{id}/load`, `DELETE /api/rooms/{id}`.

### Saved copies of bundled fixture profiles stay current

A copy of a bundled profile in `backend/data/fixtures/` shadows the bundled one, so when a newer
version gained something (zones, a strobe channel), an old copy hid it. Copies that were not edited
in the Fixture Creator (`customized`) are only snapshots, so they are rebuilt from the bundled
profile at startup, on save, on load and on config import, keeping the pan/tilt ranges tuned on
them; the old file goes to `backend/data/fixtures/_replaced/`. Profiles you made yourself, and
bundled ones you changed in the Fixture Creator, are never touched. The UI says which profiles were
updated when a room is saved, loaded or imported.

## The "All lights" group

`All lights` is built in: it always contains every fixture in the room. The backend adds a fixture
to it when you patch one, removes it when you delete one, and repairs it on startup and on a config
import (a config with no groups, or with a stale `All lights`, still ends up with every fixture in
it). It cannot be deleted or emptied; its name and colour can be changed.

## Fixtures with zones (light bars) and adding new fixture types

Not every fixture is a pan/tilt head or one lamp. A light bar like the **Eurolite LED KLS-120 FX**
(bundled as `eurolite-kls-120-fx-21ch`, 21-channel mode) is four lights in one housing: two spots
and two derbies, each with its own RGBW. A profile describes that with **zones**:

```json
"fixture_type": "light_bar",
"channels": { "dimmer": 1 },
"zones": [
  { "id": "spot1", "label": "Spot 1", "kind": "spot", "position": -1.0,
    "channels": { "red": 3, "green": 4, "blue": 5, "white": 6, "strobe": 2 } },
  { "id": "derby1", "label": "Derby 1", "kind": "derby", "position": -0.34,
    "channels": { "red": 12, "green": 13, "blue": 14, "white": 15, "strobe": 11 } }
],
"custom_channels": [
  { "channel": 20, "label": "Derby Motor", "ranges": [
      { "label": "Off", "min": 0, "max": 9 }, { "label": "Low", "min": 10, "max": 29 },
      { "label": "Medium", "min": 30, "max": 49 }, { "label": "Fast", "min": 50, "max": 255 } ] } ],
"role_ranges": { "strobe": { "min": 10, "max": 255, "zero": 0 } }
```

- **zones**: `channels` maps the zone's functions to DMX offsets: red/green/blue/white, optionally its
  own `dimmer` and its `strobe`. Zones may share a channel (the KLS's two spots are on one strobe
  channel, the two derbies on another), which means they cannot be strobed apart; the Zones window says
  so. Map a function on the zone instead of leaving it a custom channel, so it gets a proper control; `kind` groups them for quick picks ("All derbies"); `position` places the zone along the
  fixture in the 3D picture: -1 = left ... +1 = right as seen looking at the front. The order in the
  list is independent of it (it is the order zone chases walk), so a light bar whose lights are wired in
  a different order than they sit is fine. A fixture with no zones is one implicit "main" zone, so
  every other profile behaves exactly as before.
- **custom channel `ranges`**: named spans of a channel (a motor's Low/Medium/Fast, a fixture's
  built-in Auto/Sound programs, a strobe's Slow/Medium/Max). The Custom Channels window always shows
  the slider across the whole channel, plus a preset button per range that jumps to the start of that
  range; the button for the range the value is in stays lit. Without `ranges` you get just the slider.
- **strobe per zone**: the Zones window has a Strobe slider and Off/Slow/Med/Max presets that strobe the
  chosen zones (nothing chosen = all); the Strobe / Shutter window and a sound Beat strobe strobe every
  strobe channel of the fixture, and a sound Beat strobe can be limited to zones like the other zone
  functions. On the real KLS-120 FX channel 2 strobes only the spots and channel 11 only the derbies.
  A strobe channel that is shared by zones you did not pick strobes them too, and the window warns
  about it.
- **role_ranges** (as for the moving heads) squeeze the logical 0-100 % dimmer/strobe into the
  usable part of a channel, e.g. strobe DMX 10-255 with 0-9 meaning "off".
- The **Fixture Creator** edits zones (label, kind, position, and a channel each for R G B W, Dim and
  Strobe) and has a **Flash** button
  per zone that flashes that zone white on a patched fixture, so you can see which physical light
  is which while filling in positions; the Zones window has the same "Find a light" buttons.
  Named ranges of custom channels are kept when a profile is re-saved but are edited in the
  profile JSON (`backend/data/fixtures/*.json`, or bundle one in `backend/app/fixtures/profiles/`).

In the UI:

- The **Zones** window (opens when you select a zoned fixture; also in the Windows menu) picks
  which zones the controls act on: chips per zone, quick picks per kind, nothing picked = every
  zone. The **RGB** window then colors only those zones, and the **Zone brightness** slider dims
  only those (on a fixture with no per-zone dimmer channel this scales the zone's color). The
  master dimmer and strobe stay in the Strobe / Shutter window.
- **Sound**: every color effect (VU dimmer, Beat flash, Beat color chase, Beat strobe, Color organ and
  **Beat zone chase**, which walks the fixture's zones in turn on the beat: sequence, ping-pong,
  checkerboard or random) gets a zone picker on its card as soon as a selected light (or a light in a
  selected group) has zones. Pick the zones the effect drives; nothing picked = every zone. Lights
  without zones are driven as normal whatever is picked. Two effects can share one bar: a Beat zone
  chase on its spots and a Beat color chase on its derbies. The bar's own built-in Sound 1-3
  programs are in the Custom Channels window.
- **3D**: the model is picked by `fixture_type` (`buildFixtureBody` in `frontend/src/scene3d.js`;
  unknown types get a plain box). The light bar is drawn as a mounting bar on top with a head hanging
  below it per zone, each with the model for its `kind` (`LIGHT_HEAD_MODELS`: `spot`, `derby`, `par`;
  add more there), glowing with its zone's live color, and no beam line since there is nothing to aim. Add a new
  fixture family by adding a builder there; the Sound and Zones windows and the mobile Aim view work
  from the profile data, not per-fixture code.
- Mounting for a bar: its lights face the fixture's "home" direction, so on a stand pointing
  forward pick *On a wall / sideways* in the Mounting picker.

Trying things without touching your real venue data: in PowerShell,
`$env:DMX_DATA_DIR = "C:\temp\dmx-test"; .\run.ps1 -Sim` runs against another data folder.

## Sound-to-light

Open the **Sound** window (top bar). It works like Freestyler's audio section:

- **Input:** pick a microphone/line-in, or a **loopback** device -- the loopback of an
  output (speakers, headphones) captures exactly what this PC is playing, so
  Spotify/browser/DJ software drives the lights with no cable. Loopback devices are
  listed first; the default output is marked. (Windows, via WASAPI/PyAudioWPatch. A device
  that Windows refuses to open reports the reason instead of failing silently.)
- **Analysis:** level plus bass/mid/high meters (auto-gain, so volume doesn't matter),
  a beat recogniser and BPM read-out with a beat indicator, gain and beat-sensitivity
  sliders. **Tap** switches to tap tempo (a metronome from your taps) for music the
  recogniser can't follow; switch back with the Beat selector.
- **Functions** (add several, each with its own targets, e.g. the current selection):
  VU dimmer, Beat flash, Beat color chase, Color organ (bass/mid/high -> R/G/B),
  Beat strobe, Beat movement. Chases can run every 1/2/4/8 beats. Everything goes through
  the normal engine, so profile ranges, shared dimmer/strobe channels etc. still apply.
- **React to sound with: Off / Color / Motion / Both.** Color runs the dimmer, strobe and color
  functions; Motion runs pan/tilt functions; Both runs everything. Use *Color* when an animation
  owns the movement (sound never touches pan/tilt), *Motion* to leave colors alone. Functions
  outside the chosen mode are shown greyed out as paused.
- Inputs, mode and functions persist in `backend/data/audio.json`.

API: `GET /api/audio/devices|status|function-types`, `POST /api/audio/select|start|stop|config|tap|bpm` (`config` takes `sound_mode`),
`POST|PATCH|DELETE /api/audio/functions`.

- **Mobile UI** (`frontend/mobile/`, served at `/mobile/`): the desktop
  UI's floating windows and 3D scene need more screen than a phone has, so
  this is a deliberately different layout rather than a squeezed-down
  copy. A phone is routed here **automatically** -- an inline script in
  both `index.html`s checks `min(innerWidth, innerHeight)` against a
  700px breakpoint once at load and redirects if it doesn't match (a
  tablet stays on the desktop UI; a phone rotated to landscape doesn't
  flip back to it, since the check uses the smaller dimension). There is
  no manual toggle button -- just an "Open full desktop UI" link tucked
  in the More menu as an escape hatch.
  - **Left-edge drawer tabs** (Lights / Shows / Sound / More) are menus,
    not screens you navigate away from -- tapping one slides a panel in
    over the current screen and back, so picking a different light (or
    checking sound status) never loses your place on, say, the Aim
    screen. Lights is the group/fixture picker; Sound has an input
    device picker plus start/stop; More has DMX status/reconnect and the
    desktop-UI link.
  - **Shows** is where animations and patterns are actually authored on
    mobile now, not just played -- three sub-tabs (Animations / Patterns
    / Points) each with a list (play/stop/edit/delete) and a full edit
    form that replaces the list in place (list-\>detail navigation within
    the drawer). Target pickers are a `<select>` of every group/fixture
    instead of a raw id field.
  - **Color** and **Control** are separate main screens (segmented
    control alongside Aim) -- Color is just RGBW; Control has
    dimmer/strobe, a manual pan/tilt pad (pointer events, so it works for
    touch and mouse alike), and custom channels.
  - **Aim** is a real 3D view (`frontend/mobile/aim3d.js`), not a flat 2D
    floor plan with a height slider -- orbit/pinch to look around, tap a
    fixture to select it or any floor/wall/ceiling surface to aim the
    current selection there, through the same room-space `aim` endpoint
    the desktop 3D view uses. It shows the *same* per-type fixture body
    models and the same beam ("light path") -- `createFixtureMesh()` is
    exported from the desktop's `scene3d.js` and reused as-is, so a
    moving head looks like a moving head here too, not a placeholder
    sphere, and the yaw/pitch/roll + beam math is kept in sync with it by
    hand (small enough to duplicate safely). What's deliberately left out
    is the gizmo/dragging -- repositioning fixtures stays a desktop task,
    so mobile's 3D view never depends on (or risks breaking) that more
    complex, editing-focused code. Fixture click/tap-selection on both
    the desktop and mobile 3D views only hit-tests the actual body model,
    not the beam or front-arrow (which are much bigger and used to make
    clicking the floor near a beam's path misfire as "select this
    fixture" instead of aiming).
  - Reuses the same backend and the same `api.js`/`state.js`/
    `main_data.js` modules as the desktop UI (imported directly, no
    duplicated fetch/state logic) -- only the layout differs. Browser-mic
    sound input and gyroscope-based aiming are deliberately deferred to
    a later pass.

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
