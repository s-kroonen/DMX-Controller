import socket

import pytest
from fastapi import FastAPI

from desktop.server_lifecycle import is_already_running, start_server, wait_until_ready


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def tiny_app():
    app = FastAPI()

    @app.get("/api/room")
    def room():
        return {"ok": True}

    return app


def test_not_running_before_start(tiny_app):
    port = _free_port()
    assert is_already_running("127.0.0.1", port) is False


def test_start_serves_and_stop_releases_port(tiny_app):
    port = _free_port()
    handle = start_server(tiny_app, host="127.0.0.1", port=port)
    try:
        assert wait_until_ready("127.0.0.1", port, timeout=5.0) is True
        assert is_already_running("127.0.0.1", port) is True
    finally:
        handle.stop()

    # after a clean stop, a second launch should see the port as free again
    assert is_already_running("127.0.0.1", port) is False


def test_zero_bind_is_probed_via_loopback(tiny_app):
    port = _free_port()
    handle = start_server(tiny_app, host="0.0.0.0", port=port)
    try:
        assert wait_until_ready("0.0.0.0", port, timeout=5.0) is True
    finally:
        handle.stop()
