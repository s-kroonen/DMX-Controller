"""Sound-to-light: functions, service (tap tempo, persistence), capture and API.

No audio hardware: the analyzer is stubbed with settable features, and capture
uses a fake source. Fixtures: two 14ch heads (shared dimmer/strobe ch 6 / 20)
and a Beamz (separate dimmer 33 / strobe 38)."""

import time

import numpy as np
import pytest

from app.audio.analyzer import AudioFeatures
from app.audio.capture import AudioCapture
from app.audio.service import SoundService
from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.room.model import FixtureInstance, Room
from app.show.engine import ShowEngine
from app.storage import Storage

HEAD = "generic-rgbw-moving-head-14ch"
BEAMZ = "beamz-mhl108-mkii-11ch"
H1_DIM, B_DIM = 6, 33
H1_RED, H1_GREEN, H1_BLUE = 7, 8, 9
H1_PAN, H1_TILT = 1, 3


class StubAnalyzer:
    def __init__(self):
        self.f = AudioFeatures()
        self.gain = 1.0
        self.sensitivity = 1.0

    def features(self):
        import dataclasses
        return dataclasses.replace(self.f)

    def process(self, x):
        pass


@pytest.fixture()
def engine():
    room = Room()
    room.add_fixture(FixtureInstance(id="h1", name="H1", profile_id=HEAD, start_address=1))
    room.add_fixture(FixtureInstance(id="h2", name="H2", profile_id=HEAD, start_address=15))
    room.add_fixture(FixtureInstance(id="bz", name="Beamz", profile_id=BEAMZ, start_address=29))
    return ShowEngine(room, FixtureLibrary(), SimulatedDmxOutput())


@pytest.fixture()
def service(engine, tmp_path):
    svc = SoundService(engine, Storage(data_dir=tmp_path / "data"),
                       device_lister=lambda: [], device_finder=lambda *a, **k: None)
    svc.analyzer = StubAnalyzer()
    svc._seen_analyzer = svc.analyzer   # baseline at beat_count 0, as after a real device open
    svc.sound_enabled = True
    yield svc
    svc.shutdown()


def ch(engine, n):
    return engine.dmx.get_channel(n)


def beat(service):
    service.analyzer.f.beat_count += 1


def tick(service, n=1, dt=0.025):
    for i in range(n):
        service.tick(dt, time.monotonic() + i * dt)


# ---- functions ---------------------------------------------------------------

def test_vu_dimmer_follows_the_level(service, engine):
    service.add_function("vu_dimmer", ["bz"], {"attack_ms": 0, "release_ms": 0})
    service.analyzer.f.level = 1.0
    tick(service)
    assert ch(engine, B_DIM) == 255
    service.analyzer.f.level = 0.5
    tick(service)
    assert ch(engine, B_DIM) == 128
    service.analyzer.f.level = 0.0
    tick(service)
    assert ch(engine, B_DIM) == 0


def test_vu_dimmer_release_is_gradual(service, engine):
    service.add_function("vu_dimmer", ["bz"], {"attack_ms": 0, "release_ms": 500})
    service.analyzer.f.level = 1.0
    tick(service)
    service.analyzer.f.level = 0.0
    tick(service, 2)
    assert 0 < ch(engine, B_DIM) < 255


def test_vu_dimmer_maps_through_each_fixtures_own_range(service, engine):
    service.add_function("vu_dimmer", ["h1", "bz"], {"attack_ms": 0, "release_ms": 0})
    service.analyzer.f.level = 1.0
    tick(service)
    assert ch(engine, H1_DIM) == 134      # head: top of its 10-134 dimmer band
    assert ch(engine, B_DIM) == 255       # Beamz: raw


def test_beat_flash_snaps_up_then_decays(service, engine):
    service.add_function("beat_flash", ["bz"], {"min_pct": 0, "max_pct": 100, "decay_ms": 200})
    tick(service)
    assert ch(engine, B_DIM) == 0
    beat(service)
    tick(service)
    assert ch(engine, B_DIM) > 200
    tick(service, 20)
    assert ch(engine, B_DIM) == 0


def test_beat_color_steps_through_the_palette_on_each_beat(service, engine):
    service.add_function("beat_color", ["h1"], {"palette": ["#ff0000", "#00ff00", "#0000ff"], "every": 1})
    colors = []
    for _ in range(4):
        beat(service)
        tick(service)
        colors.append((ch(engine, H1_RED), ch(engine, H1_GREEN), ch(engine, H1_BLUE)))
    assert colors == [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 0)]


def test_beat_color_every_two_beats(service, engine):
    service.add_function("beat_color", ["h1"], {"palette": ["#ff0000", "#00ff00"], "every": 2})
    seen = []
    for _ in range(6):
        beat(service)
        tick(service)
        seen.append(ch(engine, H1_RED))
    assert len(set(seen)) == 2 and seen.count(255) >= 2   # changes only every other beat
    changes = sum(1 for a, b in zip(seen, seen[1:]) if a != b)
    assert changes <= 3


def test_color_organ_maps_bands_to_rgb(service, engine):
    service.add_function("color_organ", ["h1"], {"smooth_ms": 10})
    service.analyzer.f.bass, service.analyzer.f.mid, service.analyzer.f.high = 1.0, 0.0, 0.5
    tick(service, 10, dt=0.05)
    assert ch(engine, H1_RED) == 255 and ch(engine, H1_GREEN) == 0
    assert 100 < ch(engine, H1_BLUE) < 150


def test_beat_movement_cycles_positions(service, engine):
    positions = [{"pan": 10, "tilt": 20}, {"pan": 200, "tilt": 220}]
    service.add_function("beat_movement", ["h1"], {"positions": positions, "every": 1})
    seen = []
    for _ in range(3):
        beat(service)
        tick(service)
        seen.append((ch(engine, H1_PAN), ch(engine, H1_TILT)))
    assert seen == [(10, 20), (200, 220), (10, 20)]


def test_beat_strobe_bursts_then_reopens(service, engine):
    service.add_function("beat_strobe", ["bz"], {"every": 1, "burst_ms": 100, "speed_pct": 100})
    beat(service)
    tick(service)
    assert ch(engine, 38) == 255            # Beamz strobe channel at max during the burst
    tick(service, 10)                        # 250 ms later
    assert ch(engine, 38) == 0               # back to open


def test_master_switch_off_stops_all_functions(service, engine):
    service.add_function("beat_flash", ["bz"], {"min_pct": 0})
    service.sound_enabled = False
    beat(service)
    tick(service)
    assert ch(engine, B_DIM) == 0


def test_disabling_a_function_leaves_the_others_running(service, engine):
    a = service.add_function("beat_color", ["h1"], {"palette": ["#ff0000"], "every": 1})
    service.add_function("beat_flash", ["bz"], {"min_pct": 0})
    service.update_function(a.id, enabled=False)
    beat(service)
    tick(service)
    assert ch(engine, H1_RED) == 0 and ch(engine, B_DIM) > 0


def test_a_function_with_a_deleted_target_reports_an_error_instead_of_crashing(service, engine):
    fn = service.add_function("beat_flash", ["ghost"], {})
    beat(service)
    tick(service)                                     # must not raise
    assert fn.id in service.errors
    assert service.status()["functions"][0]["error"]


def test_removing_a_strobe_function_mid_burst_reopens_the_shutter(service, engine):
    fn = service.add_function("beat_strobe", ["h1"], {"every": 1, "burst_ms": 5000, "speed_pct": 100})
    beat(service)
    tick(service)
    assert ch(engine, H1_DIM) >= 135        # strobing (shared channel)
    service.remove_function(fn.id)
    assert ch(engine, H1_DIM) < 135         # opened again, not stuck strobing


def test_unknown_function_type_is_rejected(service):
    with pytest.raises(ValueError):
        service.add_function("nope", [])


# ---- beat sources / tap tempo -------------------------------------------------

def test_audio_beats_are_counted_between_ticks_even_if_several_arrive(service, engine):
    service.add_function("beat_color", ["h1"], {"palette": ["#ff0000", "#00ff00"], "every": 1})
    service.analyzer.f.beat_count += 3
    tick(service)
    assert service._beat_index == 3


def test_tap_tempo_computes_bpm_and_switches_source(service):
    t = 100.0
    for i in range(5):
        bpm = service.tap(now=t + i * 0.5)
    assert bpm == pytest.approx(120, abs=0.5)
    assert service.beat_source == "tap"


def test_a_long_pause_starts_a_new_tap_sequence(service):
    service.tap(now=10.0)
    service.tap(now=10.5)
    bpm = service.tap(now=20.0)          # long gap: this tap alone, bpm unchanged from before
    assert service._taps == [20.0]


def test_tap_metronome_generates_beats_at_the_tapped_tempo(service, engine):
    service.add_function("beat_color", ["h1"], {"palette": ["#ff0000", "#00ff00"], "every": 1})
    t0 = time.monotonic()
    service.tap(now=t0)
    service.tap(now=t0 + 0.5)            # 120 BPM
    for i in range(1, 21):               # 2 seconds of ticks
        service.tick(0.1, t0 + 0.5 + i * 0.1)
    assert 4 <= service._beat_index <= 7  # ~4 metronome beats + the taps


def test_status_reports_the_effective_bpm(service):
    service.set_bpm(140)
    assert service.status()["bpm"] == 140
    service.configure(beat_source="audio")
    service.analyzer.f.bpm = 100
    assert service.status()["bpm"] == 100


def test_configure_clamps_and_rejects_bad_sources(service):
    service.configure(gain=99, sensitivity=0)
    assert service.gain == 4.0 and service.sensitivity == 0.2
    with pytest.raises(ValueError):
        service.configure(beat_source="magic")


# ---- persistence ----------------------------------------------------------------

def test_functions_and_settings_survive_a_restart(engine, tmp_path):
    storage = Storage(data_dir=tmp_path / "data")
    first = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *a, **k: None)
    first.device = {"id": "loopback:Speakers", "name": "Speakers", "is_loopback": True}
    first.configure(gain=1.5, sensitivity=0.8, sound_enabled=True)
    fn = first.add_function("beat_color", ["h1"], {"every": 4})
    first.shutdown()

    again = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *a, **k: None)
    try:
        assert again.gain == 1.5 and again.sensitivity == 0.8 and again.sound_enabled
        assert again.device["name"] == "Speakers"
        assert again.functions[fn.id].params["every"] == 4
        assert again.functions[fn.id].targets == ["h1"]
    finally:
        again.shutdown()


def test_a_corrupt_config_file_is_ignored(engine, tmp_path):
    storage = Storage(data_dir=tmp_path / "data")
    storage.audio_path.write_text("{not json")
    svc = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *a, **k: None)
    assert svc.functions == {}
    svc.shutdown()


# ---- capture (fake source) --------------------------------------------------------

class FakeSource:
    samplerate = 44100

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.closed = False

    def read_available(self):
        return self.chunks.pop(0) if self.chunks else None

    def close(self):
        self.closed = True


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_capture_delivers_samples_and_reports_the_rate():
    got, opened = [], []
    source = FakeSource([np.ones(512, dtype=np.float32) * 0.1 for _ in range(5)])
    cap = AudioCapture(lambda x: got.append(len(x)), on_open=opened.append, source_factory=lambda d: source)
    cap.start({"id": "x", "name": "Fake"})
    assert wait_for(lambda: sum(got) >= 512 * 5)
    cap.stop()
    assert opened == [44100]
    assert source.closed and not cap.running


def test_capture_feeds_silence_when_the_device_goes_quiet():
    """WASAPI loopback sends nothing while nothing plays; the analyzer clock must still advance."""
    total = []
    cap = AudioCapture(lambda x: total.append(len(x)), source_factory=lambda d: FakeSource())
    cap.start({"id": "x", "name": "Quiet"})
    time.sleep(0.6)
    cap.stop()
    assert sum(total) / 44100 == pytest.approx(0.6, abs=0.25)


def test_capture_errors_are_reported_not_raised():
    def boom(device):
        raise OSError("[Errno -9996] Invalid device")

    cap = AudioCapture(lambda x: None, source_factory=boom)
    cap.start({"id": "mic", "name": "Mic"})
    assert wait_for(lambda: cap.error is not None)
    assert "Invalid device" in cap.error and not cap.running
    assert cap.status()["error"]


def test_service_rebuilds_the_analyzer_at_the_devices_sample_rate(engine, tmp_path):
    source = FakeSource()
    source.samplerate = 48000
    device = {"id": "loopback:Fake", "name": "Fake", "is_loopback": True, "rate": 48000}
    svc = SoundService(engine, Storage(data_dir=tmp_path / "data"),
                       capture_factory=lambda on_samples, on_open: AudioCapture(
                           on_samples, on_open=on_open, source_factory=lambda d: source),
                       device_lister=lambda: [device], device_finder=lambda *a, **k: device)
    try:
        svc.select_device("loopback:Fake")
        svc.start()
        assert wait_for(lambda: svc.analyzer.sample_rate == 48000)
        assert svc.status()["capture"]["samplerate"] == 48000
    finally:
        svc.shutdown()


def test_start_without_a_selected_input_is_a_clear_error(service):
    with pytest.raises(ValueError, match="choose an audio input"):
        service.start()


# ---- API --------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import context as context_module
    from app.main import app

    device = {"id": "loopback:Speakers", "name": "Speakers", "channels": 2, "rate": 44100,
              "is_loopback": True, "is_default": True, "index": 1}
    source = FakeSource()
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "data"))
    monkeypatch.setattr(context_module, "SoundService", lambda engine, storage: SoundService(
        engine, storage,
        capture_factory=lambda on_samples, on_open: AudioCapture(
            on_samples, on_open=on_open, source_factory=lambda d: source),
        device_lister=lambda: [device], device_finder=lambda *a, **k: device))
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    try:
        yield TestClient(app)
    finally:
        context_module.reset_context()


def test_api_lists_devices_and_full_start_stop_flow(client):
    devices = client.get("/api/audio/devices").json()
    assert devices[0]["is_loopback"] and devices[0]["is_default"]
    assert client.post("/api/audio/start").status_code == 400          # nothing chosen yet
    assert client.post("/api/audio/select", json={"device_id": "loopback:Speakers"}).status_code == 200
    status = client.post("/api/audio/start").json()
    assert status["device"]["name"] == "Speakers"
    assert wait_for(lambda: client.get("/api/audio/status").json()["capture"]["running"])
    assert not client.post("/api/audio/stop").json()["capture"]["running"]


def test_api_function_crud_and_config(client):
    types = client.get("/api/audio/function-types").json()
    assert {t["type"] for t in types} >= {"vu_dimmer", "beat_flash", "beat_color", "color_organ",
                                            "beat_strobe", "beat_movement"}
    fn = client.post("/api/audio/functions", json={"type": "beat_flash", "targets": ["x"]}).json()
    assert fn["params"]["decay_ms"] == 250                              # defaults filled in
    client.patch(f"/api/audio/functions/{fn['id']}", json={"params": {"decay_ms": 400}, "enabled": False})
    got = client.get("/api/audio/status").json()["functions"][0]
    assert got["params"]["decay_ms"] == 400 and got["enabled"] is False
    assert client.post("/api/audio/functions", json={"type": "bogus"}).status_code == 400
    assert client.delete(f"/api/audio/functions/{fn['id']}").status_code == 200
    assert client.get("/api/audio/status").json()["functions"] == []

    client.put("/api/mode", json={"mode": "show"})           # effects only run in show mode
    cfg = client.post("/api/audio/config", json={"gain": 2.0, "sound_enabled": True}).json()["config"]
    assert cfg["gain"] == 2.0 and cfg["sound_enabled"] is True
    assert client.post("/api/audio/config", json={"beat_source": "magic"}).status_code == 400


def test_api_tap_and_bpm(client):
    client.post("/api/audio/tap")
    r = client.post("/api/audio/tap")
    assert r.status_code == 200
    assert client.post("/api/audio/bpm", json={"bpm": 128}).json()["bpm"] == 128


# ---- modes: color / motion / both ---------------------------------------------------

def _color_and_motion(service):
    service.add_function("beat_color", ["h1"], {"palette": ["#ff0000"], "every": 1})
    service.add_function("beat_movement", ["h1"], {"positions": [{"pan": 200, "tilt": 210}], "every": 1})


def test_color_mode_reacts_with_color_but_leaves_movement_alone(service, engine):
    _color_and_motion(service)
    service.configure(sound_mode="color")
    beat(service)
    tick(service)
    assert ch(engine, H1_RED) == 255                       # color reacted
    assert ch(engine, H1_PAN) != 200 and ch(engine, H1_TILT) != 210   # motion did not


def test_motion_mode_moves_but_leaves_color_alone(service, engine):
    _color_and_motion(service)
    service.configure(sound_mode="motion")
    beat(service)
    tick(service)
    assert (ch(engine, H1_PAN), ch(engine, H1_TILT)) == (200, 210)
    assert ch(engine, H1_RED) == 0


def test_both_mode_runs_everything(service, engine):
    _color_and_motion(service)
    service.configure(sound_mode="both")
    beat(service)
    tick(service)
    assert ch(engine, H1_RED) == 255 and ch(engine, H1_PAN) == 200


def test_off_mode_runs_nothing(service, engine):
    _color_and_motion(service)
    service.configure(sound_mode="off")
    beat(service)
    tick(service)
    assert ch(engine, H1_RED) == 0 and ch(engine, H1_PAN) != 200


def test_sound_must_not_touch_channels_an_animation_owns(service, engine):
    """The point of the modes: an animation moves the head, sound only does color."""
    _color_and_motion(service)
    service.configure(sound_mode="color")
    engine.set_raw_pan_tilt("h1", 33, 44)                   # what an animation would be doing
    for _ in range(6):
        beat(service)
        tick(service)
    assert (ch(engine, H1_PAN), ch(engine, H1_TILT)) == (33, 44)   # untouched by sound
    assert ch(engine, H1_RED) == 255


def test_switching_away_from_a_category_releases_it(service, engine):
    service.add_function("beat_strobe", ["h1"], {"every": 1, "burst_ms": 5000, "speed_pct": 100})
    service.configure(sound_mode="both")
    beat(service)
    tick(service)
    assert ch(engine, H1_DIM) >= 135                        # strobing
    service.configure(sound_mode="motion")                  # color no longer covered
    assert ch(engine, H1_DIM) < 135                         # shutter re-opened, not stuck strobing


def test_legacy_sound_enabled_true_means_both_and_false_means_off(service):
    service.configure(sound_mode="off")
    service.configure(sound_enabled=True)
    assert service.sound_mode == "both"
    service.configure(sound_mode="color")
    service.configure(sound_enabled=True)
    assert service.sound_mode == "color"                    # already on: don't widen it
    service.configure(sound_enabled=False)
    assert service.sound_mode == "off"


def test_unknown_sound_mode_is_rejected(service):
    with pytest.raises(ValueError):
        service.configure(sound_mode="disco")


def test_mode_persists_and_old_files_with_only_an_on_off_flag_still_load(engine, tmp_path):
    storage = Storage(data_dir=tmp_path / "data")
    a = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *x, **k: None)
    a.configure(sound_mode="motion")
    a.shutdown()
    b = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *x, **k: None)
    assert b.sound_mode == "motion"
    b.shutdown()

    storage.save_audio({"sound_enabled": True, "functions": []})      # written before modes existed
    c = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *x, **k: None)
    assert c.sound_mode == "both"
    c.shutdown()


def test_function_types_carry_their_category():
    from app.audio.functions import function_types_for_ui

    cats = {t["type"]: t["category"] for t in function_types_for_ui()}
    assert cats["beat_movement"] == "motion"
    assert all(cats[t] == "color" for t in ("vu_dimmer", "beat_flash", "beat_color", "color_organ", "beat_strobe"))


def test_api_sound_mode(client):
    assert client.get("/api/audio/status").json()["config"]["sound_mode"] == "off"
    client.put("/api/mode", json={"mode": "show"})
    cfg = client.post("/api/audio/config", json={"sound_mode": "color"}).json()["config"]
    assert cfg["sound_mode"] == "color" and cfg["sound_enabled"] is True
    assert client.post("/api/audio/config", json={"sound_mode": "nope"}).status_code == 400
    cfg = client.post("/api/audio/config", json={"sound_enabled": False}).json()["config"]
    assert cfg["sound_mode"] == "off"
