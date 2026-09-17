"""Fixture profile library: load bundled + user-created profiles from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .schema import FixtureProfile

BUNDLED_DIR = Path(__file__).parent / "profiles"


class FixtureLibrary:
    def __init__(self, user_dir: Optional[Path] = None):
        self.user_dir = user_dir
        self._profiles: dict[str, FixtureProfile] = {}
        self.reload()

    def reload(self) -> None:
        self._profiles = {}
        self._load_dir(BUNDLED_DIR)
        if self.user_dir is not None:
            self.user_dir.mkdir(parents=True, exist_ok=True)
            self._load_dir(self.user_dir)

    def _load_dir(self, directory: Path) -> None:
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                profile = FixtureProfile.from_dict(data)
                self._profiles[profile.id] = profile
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"failed to load fixture profile {path}: {exc}") from exc

    def list(self) -> list[FixtureProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.name)

    def get(self, profile_id: str) -> Optional[FixtureProfile]:
        return self._profiles.get(profile_id)

    def save(self, profile: FixtureProfile) -> None:
        """Persist a user-created/edited profile (fixture creator UI)."""
        if self.user_dir is None:
            raise RuntimeError("no user_dir configured; cannot save custom fixtures")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_dir / f"{profile.id}.json"
        path.write_text(json.dumps(profile.to_dict(), indent=2))
        self._profiles[profile.id] = profile

    def delete(self, profile_id: str) -> None:
        if self.user_dir is None:
            raise RuntimeError("no user_dir configured")
        path = self.user_dir / f"{profile_id}.json"
        if path.exists():
            path.unlink()
        self._profiles.pop(profile_id, None)
