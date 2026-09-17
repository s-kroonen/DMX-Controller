"""The one thing that must never happen: a bad/unplugged DMX4ALL port
taking the whole backend down with it. These tests exercise the
resilience wrapper in AppContext without needing real hardware, using an
obviously-invalid port path so the underlying pyserial open() fails."""

import pytest

from app import context as context_module
from app.dmx.dmx4all import Dmx4AllConfig, Dmx4AllOutput
from app.dmx.simulator import SimulatedDmxOutput

BAD_PORT = "/dev/definitely-not-a-real-port-xyz"


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    from app.storage import Storage

    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(
        context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "data")
    )
    yield
    context_module.reset_context()


def test_bad_port_at_startup_falls_back_to_simulator_without_raising(ctx, monkeypatch):
    monkeypatch.setenv("DMX4ALL_PORT", BAD_PORT)
    app_ctx = context_module.get_context()  # must not raise
    assert isinstance(app_ctx.dmx, SimulatedDmxOutput)
    assert app_ctx.dmx_error is not None
    assert app_ctx.dmx.status()["running"] is True  # fallback simulator is actually running


def test_no_port_env_uses_simulator_cleanly(ctx, monkeypatch):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    app_ctx = context_module.get_context()
    assert isinstance(app_ctx.dmx, SimulatedDmxOutput)
    assert app_ctx.dmx_error is None
    assert app_ctx.dmx_config is None


def test_connect_dmx4all_raises_but_leaves_old_output_running(ctx, monkeypatch):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    app_ctx = context_module.get_context()
    original_dmx = app_ctx.dmx
    original_dmx.set_channel(1, 42)

    with pytest.raises(Exception):
        app_ctx.connect_dmx4all(BAD_PORT)

    assert app_ctx.dmx is original_dmx
    assert app_ctx.engine.dmx is original_dmx
    assert app_ctx.dmx.get_channel(1) == 42
    assert app_ctx.dmx.status()["running"] is True


def test_disconnect_falls_back_to_a_fresh_simulator(ctx, monkeypatch):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    app_ctx = context_module.get_context()
    app_ctx.disconnect_dmx4all()
    assert isinstance(app_ctx.dmx, SimulatedDmxOutput)
    assert app_ctx.dmx is app_ctx.engine.dmx
    assert app_ctx.dmx_config is None


def test_raw_frame_bytes_passthrough_is_exactly_512_bytes():
    output = object.__new__(Dmx4AllOutput)  # skip __init__'s pyserial check
    output.config = Dmx4AllConfig(port="ignored", protocol="passthrough")
    frame = bytes(range(256)) * 2
    assert output.raw_frame_bytes(frame) == frame


def test_raw_frame_bytes_framed_wraps_header_count_and_footer():
    output = object.__new__(Dmx4AllOutput)
    output.config = Dmx4AllConfig(
        port="ignored", protocol="framed",
        header_bytes=b"\x7e", footer_bytes=b"\xe7", use_checksum=False,
    )
    frame = bytes([1]) * 512
    result = output.raw_frame_bytes(frame)
    assert result[0:1] == b"\x7e"
    assert result[-1:] == b"\xe7"
    assert result[1:3] == (512).to_bytes(2, "little")
    assert result[3:-1] == frame
