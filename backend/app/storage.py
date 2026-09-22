"""Simple JSON-file persistence for one venue's room/groups/animations.

No database -- this is a single-operator lighting controller running on
one PC at a venue. A `data/` directory next to the backend holds:

  data/room.json        Room (dimensions, fixture instances, safety zones)
  data/groups.json       List of Group
  data/animations.json   List of Animation
  data/fixtures/         User-created/edited fixture profiles (FixtureLibrary user_dir)
"""

from __future__ import annotations

import json
from pathlib import Path

from .groups.model import Group
from .paths import user_data_dir
from .room.model import Room
from .show.animation import Animation, PatternAnimation

DEFAULT_DATA_DIR = user_data_dir()


class Storage:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def room_path(self) -> Path:
        return self.data_dir / "room.json"

    @property
    def groups_path(self) -> Path:
        return self.data_dir / "groups.json"

    @property
    def animations_path(self) -> Path:
        return self.data_dir / "animations.json"

    @property
    def audio_path(self) -> Path:
        return self.data_dir / "audio.json"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def patterns_path(self) -> Path:
        return self.data_dir / "patterns.json"

    def load_room(self) -> Room:
        if self.room_path.exists():
            return Room.from_dict(json.loads(self.room_path.read_text()))
        return Room()

    def save_room(self, room: Room) -> None:
        self.room_path.write_text(json.dumps(room.to_dict(), indent=2))

    def load_groups(self) -> list[Group]:
        if self.groups_path.exists():
            return [Group.from_dict(g) for g in json.loads(self.groups_path.read_text())]
        return []

    def save_groups(self, groups: list[Group]) -> None:
        self.groups_path.write_text(json.dumps([g.to_dict() for g in groups], indent=2))

    def load_audio(self) -> dict:
        """Sound input + functions config; {} if none saved (or unreadable)."""
        if self.audio_path.exists():
            try:
                return json.loads(self.audio_path.read_text())
            except ValueError:
                return {}
        return {}

    def save_audio(self, data: dict) -> None:
        self.audio_path.write_text(json.dumps(data, indent=2))

    def load_animations(self) -> list[Animation]:
        if self.animations_path.exists():
            return [Animation.from_dict(a) for a in json.loads(self.animations_path.read_text())]
        return []

    def save_animations(self, animations: list[Animation]) -> None:
        self.animations_path.write_text(
            json.dumps([a.to_dict() for a in animations], indent=2)
        )

    def load_patterns(self) -> list[PatternAnimation]:
        if self.patterns_path.exists():
            return [PatternAnimation.from_dict(p) for p in json.loads(self.patterns_path.read_text())]
        return []

    def save_patterns(self, patterns: list[PatternAnimation]) -> None:
        self.patterns_path.write_text(
            json.dumps([p.to_dict() for p in patterns], indent=2)
        )
