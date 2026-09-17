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
from .fixtures.library import FixtureLibrary
from .show.animation import Animation, AnimationPlayer
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
        self.dmx_error: Optional[str] = None
        self.dmx = self._build_initial_dmx_output()

        self.engine = ShowEngine(room, self.library, self.dmx)
        for group in self.storage.load_groups():
            self.engine.add_group(group)
        self.animations: dict[str, Animation] = {
            a.id: a for a in self.storage.load_animations()
        }
        self.players: dict[str, AnimationPlayer] = {}
        self.dmx.start()  # no-op if _build_initial_dmx_output already started it

    def _build_initial_dmx_output(self) -> DmxOutput:
        port = os.environ.get("DMX4ALL_PORT")
        if not port:
            return SimulatedDmxOutput()
        protocol = os.environ.get("DMX4ALL_PROTOCOL", "passthrough")
        baud = int(os.environ.get("DMX4ALL_BAUD", "250000"))
        config = Dmx4AllConfig(port=port, baud_rate=baud, protocol=protocol)  # type: ignore[arg-type]
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

    def connect_dmx4all(self, port: str, protocol: str = "passthrough",
                         baud_rate: int = 250000) -> None:
        """Swap the live DMX output to a real DMX4ALL interface, e.g. from
        the UI's DMX Setup panel while iterating on port/protocol against
        real hardware. Raises on failure -- the caller (the API route)
        turns that into a 400 -- and leaves the previous output running
        untouched so a bad attempt doesn't kill whatever was already
        working."""
        config = Dmx4AllConfig(port=port, baud_rate=baud_rate, protocol=protocol)  # type: ignore[arg-type]
        new_output = Dmx4AllOutput(config)
        new_output.start()  # raises here if the port can't be opened
        self._swap_dmx_output(new_output)
        self.dmx_config = config
        self.dmx_error = None

    def disconnect_dmx4all(self) -> None:
        """Fall back to the simulator -- e.g. to free the COM port, or
        after a failed hardware test."""
        new_output = SimulatedDmxOutput()
        new_output.start()
        self._swap_dmx_output(new_output)
        self.dmx_config = None
        self.dmx_error = None

    def _swap_dmx_output(self, new_output: DmxOutput) -> None:
        old_output = self.dmx
        self.dmx = new_output
        self.engine.dmx = new_output
        old_output.stop()

    # -- persistence --------------------------------------------------

    def persist_room(self) -> None:
        self.storage.save_room(self.engine.room)

    def persist_groups(self) -> None:
        self.storage.save_groups(list(self.engine.groups.values()))

    def persist_animations(self) -> None:
        self.storage.save_animations(list(self.animations.values()))

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

    def shutdown(self) -> None:
        for player in list(self.players.values()):
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
