"""Filesystem paths that differ between running from source and running as
a packaged (PyInstaller-frozen) executable.

Frozen, the source tree is unpacked to a one-off temp directory (onefile)
or sits read-only next to the exe (onedir) -- neither is a place to persist
user data across runs, and a onefile build's temp dir is wiped on every
launch. Source checkouts keep exactly the paths they've always used.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Where bundled read-only resources (the frontend/ tree) live: the
    PyInstaller extraction dir when frozen, the repo root otherwise."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


def user_data_dir() -> Path:
    """Where persistent data (room/groups/animations/fixtures/audio config)
    lives. Source checkout: backend/data, same as always (tests override
    this directly via Storage(data_dir=...), so it's untouched by this).
    Frozen: a stable per-user directory outside the executable, since the
    install location may not be writable and a onefile build's own temp
    dir doesn't survive between runs."""
    if not is_frozen():
        return Path(__file__).resolve().parent.parent / "data"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "DMXController" / "data"
