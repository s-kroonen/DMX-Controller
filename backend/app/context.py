"""Process-wide application state: the one ShowEngine, DMX output, and
fixture library a running backend instance owns. Kept as a lazily-built
singleton so route modules can import `get_context()` without needing
FastAPI dependency wiring for something this simple, and so tests can
call `reset_context()` between cases.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .dmx.dmx4all import Dmx4AllConfig, Dmx4AllOutput
from .dmx.interface import DmxOutput
from .dmx.simulator import SimulatedDmxOutput
from .audio.service import SoundService
from .dmx.usb_procs import kill_holders
from .fixtures.library import FixtureLibrary
from .fixtures.schema import FixtureProfile
from .groups.model import Group
from .room.model import Room
from .show.animation import Animation, AnimationPlayer, PatternAnimation, PatternPlayer
from .show.engine import ShowEngine
from .storage import Storage

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(self):
        self.storage = Storage()
        self.library = FixtureLibrary(user_dir=self.storage.fixtures_dir)
        room = self.storage.load_room()

        # A bad/unplugged DMX4ALL port must never take the whole backend
        # down with it -- this is the one thing standing between "no
        # lights" and "no UI to fix it from", so any failure here falls
        # back to the simulator and is surfaced via dmx_error/api/dmx/status
        # instead of raising out of __init__ (which main.py's startup
        # calls eagerly).
        self.dmx_config: Optional[Dmx4AllConfig] = None
        # Last config we tried to use, kept after a failure/disconnect so the
        # Reconnect button knows where to go back to.
        self.last_dmx_config: Optional[Dmx4AllConfig] = None
        self.dmx_error: Optional[str] = None
        self.dmx = self._build_initial_dmx_output()

        self.engine = ShowEngine(room, self.library, self.dmx)
        stored_groups = self.storage.load_groups()
        stored_dicts = [g.to_dict() for g in stored_groups]
        self.engine.set_groups(stored_groups)
        if stored_dicts != [g.to_dict() for g in self.engine.groups.values()]:
            self.persist_groups()   # the built-in "All lights" group was missing (or missing lights)
        self.animations: dict[str, Animation] = {
            a.id: a for a in self.storage.load_animations()
        }
        self.players: dict[str, AnimationPlayer] = {}
        self.patterns: dict[str, PatternAnimation] = {
            p.id: p for p in self.storage.load_patterns()
        }
        self.pattern_players: dict[str, PatternPlayer] = {}
        self.dmx.start()  # no-op if _build_initial_dmx_output already started it
        self.sound = SoundService(self.engine, self.storage)

    def _build_initial_dmx_output(self) -> DmxOutput:
        port = os.environ.get("DMX4ALL_PORT")
        if not port:
            return SimulatedDmxOutput()
        baud = int(os.environ.get("DMX4ALL_BAUD", "38400"))
        config = Dmx4AllConfig(port=port, baud_rate=baud)
        self.last_dmx_config = config
        output, error = self._try_start_dmx4all(config)
        if error:
            self.dmx_error = error
            logger.warning(
                "DMX4ALL connect failed at startup (port=%s): %s -- falling back to simulator",
                port, error,
            )
            return SimulatedDmxOutput()
        self.dmx_config = config
        return output

    @staticmethod
    def _try_start_dmx4all(config: Dmx4AllConfig) -> tuple[DmxOutput, Optional[str]]:
        """Attempt to build and start a real DMX4ALL output. Never raises --
        returns (output, None) on success or (a fresh unstarted
        SimulatedDmxOutput, error message) on any failure (missing
        pyserial, bad port name, device not plugged in, permission denied,
        ...)."""
        try:
            output = Dmx4AllOutput(config)
            output.start()
        except Exception as exc:  # noqa: BLE001 -- surfaced via API, never crashes the app
            return SimulatedDmxOutput(), str(exc)
        return output, None

    def connect_dmx4all(self, port: str, baud_rate: int = 38400) -> None:
        """Swap the live DMX output to a real DMX4ALL interface, e.g. from
        the UI's DMX Setup panel. Raises on failure -- the caller (the API
        route) turns that into a 400. If a *different* output was running
        it is left untouched; if the failed attempt needed the port that
        the current output holds (same port), see _open_dmx4all."""
        self._open_dmx4all(Dmx4AllConfig(port=port, baud_rate=baud_rate))

    def reconnect_dmx4all(self, kill_other_holders: bool = False) -> list[dict]:
        """Re-open the DMX4ALL port after an unplug / stall / "Access is
        denied". Releases our own handle first (a COM port is exclusive, so
        opening a second handle to the same port would fail against
        ourselves), optionally kills other processes that may be holding
        it, then reconnects with the last-used config. Returns the list of
        killed processes. Raises on failure, leaving the simulator running
        so the backend stays usable."""
        config = self.dmx_config or self.last_dmx_config
        if config is None:
            raise RuntimeError("no DMX4ALL connection to reconnect -- use Connect first")
        killed: list[dict] = []
        if kill_other_holders:
            killed = self.kill_usb_holders(release_own_port=True)
        self._open_dmx4all(config)
        return killed

    def kill_usb_holders(self, release_own_port: bool = False) -> list[dict]:
        """Kill other processes that may hold the dongle (lighting apps,
        stray copies of this backend). Never touches this process."""
        if release_own_port and isinstance(self.dmx, Dmx4AllOutput):
            self._release_to_simulator()
        return kill_holders()

    def disconnect_dmx4all(self) -> None:
        """Fall back to the simulator -- e.g. to free the COM port, or
        after a failed hardware test."""
        self._swap_dmx_output(self._fresh_simulator(self.dmx))
        self.dmx_config = None
        self.dmx_error = None

    def _open_dmx4all(self, config: Dmx4AllConfig) -> None:
        old = self.dmx
        holds_same_port = isinstance(old, Dmx4AllOutput) and old.config.port == config.port
        if holds_same_port:
            old.stop()  # release the exclusive COM port before re-opening it
        try:
            new_output = Dmx4AllOutput(config)
            new_output._buffer = bytearray(old.snapshot())  # keep channel state across the swap
            new_output.start()  # raises here if the port can't be opened / doesn't handshake
        except Exception as exc:  # noqa: BLE001
            self.last_dmx_config = config
            if holds_same_port:
                self._swap_dmx_output(self._fresh_simulator(old), stop_old=False)
                self.dmx_config = None
                self.dmx_error = str(exc)
            raise
        self._swap_dmx_output(new_output, stop_old=not holds_same_port)
        self.dmx_config = config
        self.last_dmx_config = config
        self.dmx_error = None

    def _release_to_simulator(self) -> None:
        self._swap_dmx_output(self._fresh_simulator(self.dmx))
        self.dmx_config = None

    @staticmethod
    def _fresh_simulator(previous: DmxOutput) -> SimulatedDmxOutput:
        sim = SimulatedDmxOutput()
        sim._buffer = bytearray(previous.snapshot())
        sim.start()
        return sim

    def _swap_dmx_output(self, new_output: DmxOutput, stop_old: bool = True) -> None:
        old_output = self.dmx
        self.dmx = new_output
        self.engine.dmx = new_output
        if stop_old:
            old_output.stop()

    # -- persistence --------------------------------------------------

    def persist_room(self) -> None:
        self.storage.save_room(self.engine.room)
        self.persist_groups()   # patching a fixture also updates the "All lights" group

    def persist_groups(self) -> None:
        self.storage.save_groups(list(self.engine.groups.values()))

    def persist_animations(self) -> None:
        self.storage.save_animations(list(self.animations.values()))

    def persist_patterns(self) -> None:
        self.storage.save_patterns(list(self.patterns.values()))

    # -- whole-config export/import -------------------------------------
    #
    # A venue's full setup in one portable file: room (size/shape/fixture
    # placement/safety zones), groups, animations, patterns, sound config,
    # and every fixture profile any of it needs -- so the DMX channel
    # layout for each fixture is known immediately on whatever machine the
    # file is loaded on, not just the one that exported it. DMX interface
    # settings (COM port, baud rate) are deliberately left out: they name a
    # physical port on the exporting machine and mean nothing on another.

    CONFIG_VERSION = 1

    def export_config(self) -> dict:
        return {
            "version": self.CONFIG_VERSION,
            "room": self.engine.room.to_dict(),
            "groups": [g.to_dict() for g in self.engine.groups.values()],
            "animations": [a.to_dict() for a in self.animations.values()],
            "patterns": [p.to_dict() for p in self.patterns.values()],
            "audio": self.sound.to_dict(),
            "fixture_profiles": [p.to_dict() for p in self.library.list()],
        }

    def import_config(self, data: dict) -> None:
        """Replace room/groups/animations/patterns/sound config wholesale.
        Raises ValueError (never partially applies) if the room references a
        fixture profile neither the import nor this install's library
        provides."""
        for profile_dict in data.get("fixture_profiles", []):
            profile = FixtureProfile.from_dict(profile_dict)
            if self.library.get(profile.id) is None:
                self.library.save(profile)

        room = Room.from_dict(data["room"])
        missing = {
            fixture.profile_id for fixture in room.fixtures.values()
            if self.library.get(fixture.profile_id) is None
        }
        if missing:
            raise ValueError(f"missing fixture profile(s): {', '.join(sorted(missing))}")

        for animation_id in list(self.players):
            self.stop_animation(animation_id)
        for pattern_id in list(self.pattern_players):
            self.stop_pattern(pattern_id)

        self.engine.load_room(room)
        self.engine.set_groups(Group.from_dict(g) for g in data.get("groups", []))
        self.animations = {a.id: a for a in
                            (Animation.from_dict(x) for x in data.get("animations", []))}
        self.patterns = {p.id: p for p in
                          (PatternAnimation.from_dict(x) for x in data.get("patterns", []))}
        self.sound.import_config(data.get("audio") or {})

        self.persist_room()
        self.persist_groups()
        self.persist_animations()
        self.persist_patterns()

    # -- animation playback --------------------------------------------

    def play_animation(self, animation_id: str) -> None:
        self.stop_animation(animation_id)
        animation = self.animations[animation_id]
        player = AnimationPlayer(self.engine, animation)
        player.start()
        self.players[animation_id] = player

    def stop_animation(self, animation_id: str) -> None:
        player = self.players.pop(animation_id, None)
        if player:
            player.stop()

    def play_pattern(self, pattern_id: str) -> None:
        self.stop_pattern(pattern_id)
        pattern = self.patterns[pattern_id]
        player = PatternPlayer(self.engine, pattern)
        player.start()
        self.pattern_players[pattern_id] = player

    def stop_pattern(self, pattern_id: str) -> None:
        player = self.pattern_players.pop(pattern_id, None)
        if player:
            player.stop()

    def shutdown(self) -> None:
        self.sound.shutdown()
        for player in list(self.players.values()):
            player.stop()
        for player in list(self.pattern_players.values()):
            player.stop()
        self.dmx.stop()


_context: Optional[AppContext] = None


def get_context() -> AppContext:
    global _context
    if _context is None:
        _context = AppContext()
    return _context


def reset_context() -> None:
    global _context
    if _context is not None:
        _context.shutdown()
    _context = None
