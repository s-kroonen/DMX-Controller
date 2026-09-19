"""The one thing that must never happen: a bad/unplugged DMX4ALL port
taking the whole backend down with it. These tests exercise the
resilience wrapper in AppContext without needing real hardware, using an
obviously-invalid port path so the underlying pyserial open() fails."""

import pytest

from app import context as context_module
import threading

from app.dmx.dmx4all import Dmx4AllConfig, Dmx4AllOutput, block_write_bytes, plan_blocks
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


class FakeSerial:
    """Scripted device: records writes, answers 'G' to writes unless told otherwise."""

    def __init__(self, replies=None, fail_write=False, **_kwargs):
        self.writes: list[bytes] = []
        self.replies = list(replies or [])
        self.fail_write = fail_write
        self.closed = False

    def reset_input_buffer(self): pass

    def write(self, data):
        if self.fail_write:
            raise OSError("access denied")
        self.writes.append(bytes(data))

    def flush(self): pass

    def read(self, n=1):
        return self.replies.pop(0) if self.replies else b"G"

    def close(self): self.closed = True


def _output(fake=None, **cfg):
    output = object.__new__(Dmx4AllOutput)  # skip __init__'s pyserial check
    output.config = Dmx4AllConfig(port="ignored", **cfg)
    output._serial = fake or FakeSerial()
    output._io_lock = threading.Lock()
    output._last_sent = None
    output._last_refresh = 0.0
    output.link_lost = False
    output._thread = None
    output._stop = threading.Event()
    output.frames_sent = 0
    output.last_frame_error = None
    output.frame_hz = 40.0
    output._buffer = bytearray(512)
    output._lock = threading.Lock()
    return output


def test_block_write_bytes_match_the_documented_example():
    # manufacturer doc: channels 10-15 = 100,120,140,150,255,10  ->  FF 09 00 06 64 78 8C 96 FF 0A
    data = bytes([100, 120, 140, 150, 255, 10])
    assert block_write_bytes(10, data) == bytes.fromhex("FF 09 00 06 64 78 8C 96 FF 0A")
    assert block_write_bytes(257, b"") == bytes([0xFF, 0x00, 0x01, 1, 1])  # high byte for 257+
    with pytest.raises(ValueError):
        block_write_bytes(1, b"")
    with pytest.raises(ValueError):
        block_write_bytes(1, bytes(256))
    with pytest.raises(ValueError):
        block_write_bytes(512, b"")  # runs past channel 512


def test_never_uses_the_unsupported_fast_mode_headers():
    output = _output(refresh_channels=8)
    frame = bytearray(512)
    frame[300] = 5
    blocks = plan_blocks(bytes(frame), set(range(8)) | {300})
    for start, data in blocks:
        assert block_write_bytes(start, data)[0] == 0xFF  # block header, never 0xE2/0xE3


def test_plan_blocks_bridges_small_gaps_but_splits_big_ones_and_banks():
    frame = bytearray(512)
    for idx, v in ((2, 1), (6, 2), (254, 3), (258, 4)):
        frame[idx] = v
    blocks = plan_blocks(bytes(frame), {2, 6, 254, 258})
    # 3..7 bridged (gap 4); 255 alone (far away); 259 alone (other bank)
    assert [(start, len(d)) for start, d in blocks] == [(3, 5), (255, 1), (259, 1)]
    assert blocks[0][1] == bytes(frame[2:7])


def test_plan_blocks_splits_bank_boundary_even_when_adjacent():
    frame = bytes(range(256)) * 2
    blocks = plan_blocks(frame, {255, 256})
    assert [(start, len(d)) for start, d in blocks] == [(256, 1), (257, 1)]


def test_first_frame_sends_low_channels_and_nonzero_high_channels():
    output = _output(refresh_channels=4)
    frame = bytearray(512)
    frame[1] = 7      # channel 2
    frame[299] = 9    # channel 300
    blocks = output.frame_blocks(bytes(frame), now=100.0)
    assert blocks[0] == (1, bytes([0, 7, 0, 0]))
    assert blocks[1] == (300, bytes([9]))


def test_later_frames_send_only_changes_until_refresh_is_due():
    output = _output(refresh_channels=2, refresh_interval_s=2.0)
    frame = bytearray(512)
    output.frame_blocks(bytes(frame), now=100.0)

    assert output.frame_blocks(bytes(frame), now=100.5) == []  # nothing changed

    frame[28] = 255  # channel 29
    assert output.frame_blocks(bytes(frame), now=100.6) == [(29, bytes([255]))]
    assert output.frame_blocks(bytes(frame), now=100.7) == []

    # refresh due: low channels re-sent even though unchanged
    assert output.frame_blocks(bytes(frame), now=102.5) == [(1, bytes([0, 0]))]


def test_send_frame_writes_block_packets_and_checks_each_ack():
    fake = FakeSerial()
    output = _output(fake, refresh_channels=2)
    frame = bytearray(512)
    frame[0] = 200
    output._send_frame(bytes(frame))
    assert fake.writes == [bytes([0xFF, 0, 0, 2, 200, 0])]
    assert output.link_lost is False


def test_missing_ack_is_an_error_and_forces_full_resend():
    fake = FakeSerial(replies=[b""])  # device says nothing
    output = _output(fake, refresh_channels=1)
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        output._send_frame(bytes(512))
    assert output._last_sent is None
    assert output.link_lost is True


def test_failed_write_forces_full_resend():
    output = _output(FakeSerial(fail_write=True), refresh_channels=1)
    with pytest.raises(OSError):
        output._send_frame(bytes(512))
    assert output._last_sent is None


def test_start_syncs_state_before_releasing_blackout(monkeypatch):
    import app.dmx.dmx4all as mod

    fake = FakeSerial()
    monkeypatch.setattr(mod, "serial", type("S", (), {
        "Serial": lambda **kw: fake, "EIGHTBITS": 8, "PARITY_NONE": "N", "STOPBITS_ONE": 1}))
    output = Dmx4AllOutput(Dmx4AllConfig(port="COMX", refresh_channels=4))
    output.set_channel(2, 77)
    output.start()
    output.stop()
    kinds = [w[:1] if w[0] == 0xFF else w for w in fake.writes]
    assert fake.writes[0] == b"C?"                                   # handshake
    assert fake.writes[1] == bytes([0xFF, 0, 0, 4, 0, 77, 0, 0])      # state first...
    assert fake.writes[2] == b"B0"                                   # ...then blackout off
    assert fake.closed


def test_failed_handshake_raises_and_closes_port(monkeypatch):
    import app.dmx.dmx4all as mod

    fake = FakeSerial(replies=[b""])
    monkeypatch.setattr(mod, "serial", type("S", (), {
        "Serial": lambda **kw: fake, "EIGHTBITS": 8, "PARITY_NONE": "N", "STOPBITS_ONE": 1}))
    with pytest.raises(RuntimeError, match="handshake"):
        Dmx4AllOutput(Dmx4AllConfig(port="COMX")).open()
    assert fake.closed


def test_read_back_and_blackout_query_parse_replies():
    output = _output(FakeSerial(replies=[b"240G", b"1G", b"0G"]))
    assert output.read_back(6) == 240
    assert output.read_blackout() is True
    assert output.read_blackout() is False
    assert output._serial.writes[0] == b"C005?"  # 1-indexed channel 6 -> 0-based 005


def test_write_timeout_is_configured_on_the_serial_port(monkeypatch):
    import app.dmx.dmx4all as mod

    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return FakeSerial()

    monkeypatch.setattr(mod, "serial", type("S", (), {
        "Serial": factory, "EIGHTBITS": 8, "PARITY_NONE": "N", "STOPBITS_ONE": 1}))
    Dmx4AllOutput(Dmx4AllConfig(port="COMX", write_timeout_s=0.25)).open()
    assert seen["write_timeout"] == 0.25


def test_failed_write_marks_link_lost_and_status_reports_it():
    output = _output(FakeSerial(fail_write=True), refresh_channels=1)
    with pytest.raises(OSError):
        output._send_frame(bytes(512))
    assert output.status()["link_lost"] is True


def test_holder_matching_rules():
    from app.dmx.usb_procs import is_holder_candidate

    assert is_holder_candidate("FreeStyler.exe", [])
    assert is_holder_candidate("DMX-Configurator.exe", [])
    assert is_holder_candidate("python.exe", ["python", "-m", "uvicorn", "app.main:app", "--port", "8000"])
    assert is_holder_candidate("python3.13.exe", ["python", "-m", "uvicorn", "app.main:app"])
    assert not is_holder_candidate("python.exe", ["python", "train.py"])
    assert not is_holder_candidate("chrome.exe", ["chrome", "--flag"])


def test_a_shell_that_merely_mentions_the_backend_is_not_a_holder():
    from app.dmx.usb_procs import is_holder_candidate

    # e.g. the bash.exe that launched the backend: the text is one giant argv element
    assert not is_holder_candidate(
        "bash.exe",
        ["bash.exe", "-c", "nohup python -m uvicorn app.main:app --port 8000 &"],
    )


def test_find_holders_never_returns_this_process_or_its_parents():
    import os
    from app.dmx.usb_procs import find_holders

    assert os.getpid() not in {h["pid"] for h in find_holders()}


class _FakeDmx4All(SimulatedDmxOutput):
    """Stands in for Dmx4AllOutput; records order of open/close per port."""
    events: list = []
    fail_open = False

    def __init__(self, config):
        super().__init__()
        self.config = config

    def start(self):
        if _FakeDmx4All.fail_open:
            raise OSError("port busy")
        _FakeDmx4All.events.append(("open", self.config.port))
        super().start()

    def stop(self):
        _FakeDmx4All.events.append(("close", self.config.port))
        super().stop()


@pytest.fixture()
def fake_hw(monkeypatch):
    _FakeDmx4All.events = []
    _FakeDmx4All.fail_open = False
    monkeypatch.setattr(context_module, "Dmx4AllOutput", _FakeDmx4All)
    return _FakeDmx4All


def test_reconnect_without_any_previous_connection_raises(ctx, monkeypatch):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    with pytest.raises(RuntimeError):
        context_module.get_context().reconnect_dmx4all()


def test_reconnect_releases_port_before_reopening_and_keeps_channel_state(ctx, monkeypatch, fake_hw):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    app_ctx = context_module.get_context()
    app_ctx.connect_dmx4all("COM7")
    app_ctx.dmx.set_channel(29, 200)
    fake_hw.events.clear()

    app_ctx.reconnect_dmx4all()

    # exclusive COM port: our own handle must be closed before the re-open
    assert fake_hw.events == [("close", "COM7"), ("open", "COM7")]
    assert app_ctx.dmx.get_channel(29) == 200
    assert app_ctx.dmx_config.port == "COM7"
    assert app_ctx.engine.dmx is app_ctx.dmx


def test_failed_reconnect_falls_back_to_simulator_but_remembers_port(ctx, monkeypatch, fake_hw):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    app_ctx = context_module.get_context()
    app_ctx.connect_dmx4all("COM7")

    fake_hw.fail_open = True
    with pytest.raises(OSError):
        app_ctx.reconnect_dmx4all()

    assert isinstance(app_ctx.dmx, SimulatedDmxOutput)
    assert app_ctx.dmx.status()["running"] is True
    assert app_ctx.dmx_config is None
    assert "port busy" in app_ctx.dmx_error
    assert app_ctx.last_dmx_config.port == "COM7"

    fake_hw.fail_open = False  # plug it back in
    app_ctx.reconnect_dmx4all()  # the button works again
    assert app_ctx.dmx_config.port == "COM7"
    assert app_ctx.dmx_error is None


def test_kill_usb_holders_releases_own_port_first(ctx, monkeypatch, fake_hw):
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    monkeypatch.setattr(context_module, "kill_holders", lambda: [{"pid": 1, "name": "FreeStyler.exe", "killed": True}])
    app_ctx = context_module.get_context()
    app_ctx.connect_dmx4all("COM7")
    killed = app_ctx.kill_usb_holders(release_own_port=True)
    assert killed[0]["name"] == "FreeStyler.exe"
    assert isinstance(app_ctx.dmx, SimulatedDmxOutput)
    assert app_ctx.last_dmx_config.port == "COM7"
