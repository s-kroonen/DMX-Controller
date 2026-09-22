"""Fixture profile library: load bundled + user-created profiles from disk."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .schema import FixtureProfile

BUNDLED_DIR = Path(__file__).parent / "profiles"


class FixtureLibrary:
    def __init__(self, user_dir: Optional[Path] = None):
        self.user_dir = user_dir
        self._profiles: dict[str, FixtureProfile] = {}
        self._bundled: dict[str, FixtureProfile] = {}
        self.reload()

    def reload(self) -> None:
        self._profiles = {}
        self._load_dir(BUNDLED_DIR)
        self._bundled = dict(self._profiles)
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

    def refresh_overrides(self) -> list[dict]:
        """Bring saved copies of bundled profiles up to date with the bundled version.

        A copy in the user's data folder shadows the bundled profile of the same id, so when
        the bundled one gains something (zones, a strobe channel, ...) the old copy keeps hiding
        it. Copies the user did not customize in the Fixture Creator are only snapshots, so each
        is rebuilt from the bundled profile, keeping the pan/tilt ranges tuned on it (those
        belong to the physical fixture). The old file is kept in `<user dir>/_replaced/`. A copy
        that ends up identical to the bundled profile is removed. Returns one note per change:
        {"id", "name", "action": "updated" | "removed", "backup"}."""
        notes: list[dict] = []
        if self.user_dir is None or not self.user_dir.exists():
            return notes
        for path in sorted(self.user_dir.glob("*.json")):
            try:
                override = FixtureProfile.from_dict(json.loads(path.read_text()))
            except Exception:  # noqa: BLE001 - an unreadable file is reported by reload(), not here
                continue
            bundled = self._bundled.get(override.id)
            if bundled is None or override.customized:
                continue  # the user's own profile, or a bundled one they deliberately changed
            current = FixtureProfile.from_dict(bundled.to_dict())
            if bundled.has_pan_tilt():
                if override.pan_range_deg is not None:
                    current.pan_range_deg = override.pan_range_deg
                if override.tilt_range_deg is not None:
                    current.tilt_range_deg = override.tilt_range_deg
            if current.to_dict() == override.to_dict():
                continue
            backup_dir = self.user_dir / "_replaced"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"{override.id}.{time.strftime('%Y%m%d-%H%M%S')}.json"
            backup.write_text(path.read_text())
            if current.to_dict() == bundled.to_dict():
                path.unlink()
                action = "removed"
            else:
                path.write_text(json.dumps(current.to_dict(), indent=2))
                action = "updated"
            self._profiles[override.id] = current
            notes.append({"id": override.id, "name": current.name, "action": action, "backup": str(backup)})
        return notes

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
