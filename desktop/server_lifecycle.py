"""Runs the FastAPI app's uvicorn server in a background thread inside this
same process, instead of a subprocess -- so the desktop shell (launcher.py)
can start and cleanly stop it without process-management plumbing.

Deliberately has no GUI dependency (no pywebview/pystray import) so it can
be exercised by plain pytest; the desktop shell around it needs a real
display and can't be tested here.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

import uvicorn


class ServerHandle:
    def __init__(self, config: uvicorn.Config):
        self.server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.server.run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        if self._thread:
            self._thread.join(timeout=timeout)


def _probe_host(host: str) -> str:
    # 0.0.0.0 means "all interfaces", not something you can connect *to* --
    # loopback always reaches a server bound that way.
    return "127.0.0.1" if host == "0.0.0.0" else host


def is_already_running(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if something is already answering on host:port -- e.g. a
    previous launch of this app. Double-launching the packaged exe must
    never start a second DMX output fighting the first for the same
    hardware, so the caller checks this before start_server()."""
    try:
        with urllib.request.urlopen(f"http://{_probe_host(host)}:{port}/api/room", timeout=timeout):
            return True
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


def wait_until_ready(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_already_running(host, port):
            return True
        time.sleep(0.1)
    return False


def start_server(app, host: str = "0.0.0.0", port: int = 8000) -> ServerHandle:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    handle = ServerHandle(config)
    handle.start()
    return handle
