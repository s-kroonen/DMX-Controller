"""Process-wide application state: the one ShowEngine, DMX output, and
fixture library a running backend instance owns. Kept as a lazily-built
singleton so route modules can import `get_context()` without needing
FastAPI dependency wiring for something this simple, and so tests can
call `reset_context()` between cases.
"""

from __future__ import annotations

import os
from typing import Optional

from .dmx.dmx4all import Dmx4AllConfig, Dmx4AllOutput
from .dmx.interface import DmxOutput
from .dmx.simulator import SimulatedDmxOutput
from .fixtures.library import FixtureLibrary
from .show.animation import Animation, AnimationPlayer
from .show.engine import ShowEngine
from .storage import Storage


class AppContext:
    def __init__(self):
        self.storage = Storage()
        self.library = FixtureLibrary(user_dir=self.storage.fixtures_dir)
        room = self.storage.load_room()
        self.dmx = self._build_dmx_output()
        self.engine = ShowEngine(room, self.library, self.dmx)
        for group in self.storage.load_groups():
            self.engine.add_group(group)
        self.animations: dict[str, Animation] = {
            a.id: a for a in self.storage.load_animations()
        }
        self.players: dict[str, AnimationPlayer] = {}
        self.dmx.start()

    def _build_dmx_output(self) -> DmxOutput:
        port = os.environ.get("DMX4ALL_PORT")
        if port:
            protocol = os.environ.get("DMX4ALL_PROTOCOL", "passthrough")
            baud = int(os.environ.get("DMX4ALL_BAUD", "250000"))
            config = Dmx4AllConfig(port=port, baud_rate=baud, protocol=protocol)  # type: ignore[arg-type]
            return Dmx4AllOutput(config)
        return SimulatedDmxOutput()

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
