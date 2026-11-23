# TODO

## Open

- [ ] **Zone picker still showing after deselecting a zoned target (Sound window).** Reported on the
  live rig: after unticking the KLS-120 on a Beat flash card (only "Moving Heads" left), the zone
  chips were still shown. A fresh page load of the same rig rendered it correctly (no zone box,
  `zones: []` server-side), so the likely cause is a tab running pre-fix JS -- but it was never
  re-tested after a hard refresh (Ctrl+Shift+R). Re-test; if it reproduces, note which checkbox was
  clicked (the fixture's own vs. a group's) and look at `renderZoneBox` / `pendingFields` in
  `frontend/src/sound.js`.
- [ ] **Document LAN access / firewall in the README.** `desktop/launcher.py` always binds
  `0.0.0.0:8000`, `run.ps1` only does with `-Lan`. Windows Firewall only had "Python" inbound rules
  for the *Public* profile, so a Private home network silently dropped connections from other
  devices. Fix was an inbound TCP 8000 rule for Private/Domain
  (`New-NetFirewallRule -DisplayName "DMX Controller (8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private,Domain`, admin PowerShell). Also worth a note: mesh
  routers (Deco) may have client/AP isolation on.
- [ ] **Commit the staged Sound/mobile/persistence work** (desync, priority + attribute stacking,
  mobile Sound parity, restart persistence, DMX Setup in show mode). Message is in
  `.git/claude/commit-msg-sound-priority-colors-ui.txt`.

- [ ] **5-point calculation (desktop).** Not written down anywhere yet -- the nearest existing thing
  is the "Solve mounting from marks" solver (3+ marks, README "Solving a head's mounting"). Define
  what the 5-point version should compute (e.g. a head's position + mounting from exactly 5 marks,
  or a room/fixture placement fit) and where it lives in the Calibrate window.
- [ ] **5-point calculation on the mobile UI** (the Calibrate tab shares `calibrationView.js`, so
  this should come mostly for free once the desktop version exists).

## Features

- [x] **DMX link auto-reconnect watchdog.** Retry the DMX4ALL connection in the background when the
  link drops, instead of waiting for a click on "LINK LOST". Goal: back online fast during a show.
- [x] **Access control for LAN use** (simple 4-8 digit PIN; see README "Access PIN"). Possible later: per-device names, HTTPS.
- [ ] **Scenes and cue list.** Save a look (colors, aim, effects) and recall it with a fade; today
  only animations and sound functions persist.
- [ ] **Sound presets.** Save and load a whole sound function set per song or genre instead of
  rebuilding it.
- [ ] **Undo/redo in edit mode** for patching, groups and room edits.
- [ ] **Automatic backups.** Timestamped copy of `backend/data/` before imports, room switches and
  restarts.
- [ ] **Other protocols.** Art-Net/sACN output for more than one universe; MIDI/OSC input for a
  hardware controller.
- [ ] **Mobile as an installable app (PWA)**, with the screen kept awake during a show.
- [ ] **Frontend tests and CI.** The backend has 412 tests, the JS has none.
- [ ] **Import Freestyler fixtures.** Add an importer next to the existing QLC+ `.qxf` import
  (`POST /api/fixtures/import-qxf`) that turns Freestyler fixture files into fixture profiles.
  First step: check which Freestyler fixture file format and version to support, and get sample
  files.

## AR / VR experiment

Goal: use this repo as a testbed for AR and VR development -- placing/aiming fixtures in a real or
virtual room. Targets: **Meta Quest 2** (VR / passthrough) and a **normal phone** (AR). Does not have
to be one project, but everything lives in this repo (suggested folder: `xr/`). Unity is the obvious
base; the aim is to try something new instead.

### 1. Research
- [ ] Compare candidate stacks against both targets (Quest 2 and phone):
  - **WebXR + Three.js** -- the frontend already uses Three.js, runs in the Quest Browser (VR,
    passthrough is limited on Quest 2) and Android Chrome via ARCore (AR); iOS Safari has no WebXR.
  - **Godot 4 (OpenXR)** -- native Quest 2 builds, Android phone AR via plugins.
  - **Unreal / Unity** -- heaviest, most mature (known baseline to compare against).
  - **Babylon.js / A-Frame** -- other WebXR frameworks.
- [ ] Decide what XR is *for*: walk-through preview of the rig with live DMX state, aiming moving
  heads by pointing, placing fixtures in a real room (phone AR), or room scanning (README lists AR
  room scanning as a future phase).
- [ ] Check Quest 2 constraints (no depth/room mesh API like Quest 3, 6DoF controllers, hand
  tracking, browser vs. sideloaded app) and phone constraints (ARCore/ARKit support, plane detection).
- [ ] Decide how XR clients talk to the backend (existing REST + websocket `/api`, `/ws`, over LAN --
  see the firewall item above).

### 2. Setup
- [ ] Create the `xr/` folder and pick the stack from the research step.
- [ ] Dev loop: serve over HTTPS on the LAN (WebXR needs a secure context), headset and phone
  connect to the dev machine, document it in the README.
- [ ] Hello-world scene on each target (Quest 2 and phone) that connects to the backend and shows
  the room and fixtures from `/api/snapshot`.

### 3. Development
- [ ] VR: walk through the virtual room with fixtures and beams, live DMX/fixture state via `/ws`.
- [ ] VR: aim a moving head by pointing a controller (reuse the room-space `aim` endpoint).
- [ ] AR (phone): anchor the room to the real space, place fixtures where they really hang.
- [ ] AR/VR: feed measured points back into calibration (ties in with the 5-point calculation).

## Done this round

- Sound effects stack by attribute (dimmer / color / strobe / pan-tilt) instead of the topmost
  blocking everything on a fixture.
- Mobile Sound drawer has the full function editor (shared `sound.js`).
- Mode and DMX port persist across a backend restart; floating windows reset to closed on restart.
- DMX Setup reachable in show mode.
