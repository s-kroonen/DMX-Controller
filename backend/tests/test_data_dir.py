"""Where the venue data is kept. In the packaged exe it must live outside the build output:
dist/<app>/_internal/data is deleted by every rebuild, which once wiped a saved room."""

import importlib
import sys
from pathlib import Path

from app import paths, storage


def _reload_storage(monkeypatch, frozen: bool, env_dir: str | None = None):
    monkeypatch.setattr(paths, "is_frozen", lambda: frozen)
    if env_dir is None:
        monkeypatch.delenv("DMX_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("DMX_DATA_DIR", env_dir)
    return importlib.reload(storage)


def test_from_source_data_stays_in_backend_data(monkeypatch):
    try:
        module = _reload_storage(monkeypatch, frozen=False)
        assert module.DEFAULT_DATA_DIR == Path(storage.__file__).resolve().parent.parent / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(storage)


def test_packaged_exe_keeps_data_in_local_appdata_not_next_to_the_exe(monkeypatch, tmp_path):
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")
        module = _reload_storage(monkeypatch, frozen=True)
        assert module.DEFAULT_DATA_DIR == tmp_path / "DMXController" / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(storage)


def test_dmx_data_dir_still_wins(monkeypatch, tmp_path):
    try:
        module = _reload_storage(monkeypatch, frozen=True, env_dir=str(tmp_path / "elsewhere"))
        assert module.DEFAULT_DATA_DIR == tmp_path / "elsewhere"
    finally:
        monkeypatch.undo()
        importlib.reload(storage)
