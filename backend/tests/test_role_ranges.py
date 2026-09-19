"""Shared dimmer/strobe channels: the logical 0-255 sliders must map into the
fixture's real sub-ranges instead of spilling a 'full' dimmer into strobe."""

import json

import pytest

from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import BUNDLED_DIR, FixtureLibrary
from app.fixtures.schema import FixtureProfile, RoleRange
from app.room.model import FixtureInstance, Room
from app.show.engine import ShowEngine

PROFILE_ID = "generic-rgbw-moving-head-14ch"


@pytest.fixture()
def head():
    profile = FixtureLibrary().get(PROFILE_ID)
    assert profile is not None
    return profile


def test_dimmer_stays_inside_10_to_134_and_zero_is_off(head):
    assert head.raw_value("dimmer", 0) == 0          # off
    assert head.raw_value("dimmer", 1) == 10         # lowest dimmer step
    assert head.raw_value("dimmer", 255) == 134      # brightest dimmer, still not strobe
    raws = [head.raw_value("dimmer", v) for v in range(256)]
    assert max(raws) == 134
    assert all(a <= b for a, b in zip(raws, raws[1:]))  # monotonic
    assert all(10 <= r <= 134 for r in raws[1:])


def test_strobe_stays_inside_135_to_239_and_zero_is_full_on(head):
    assert head.raw_value("strobe", 0) == 240        # no strobe -> full on
    assert head.raw_value("strobe", 1) == 135        # slowest strobe
    assert head.raw_value("strobe", 255) == 239      # fastest strobe, not the "full on" band
    raws = [head.raw_value("strobe", v) for v in range(1, 256)]
    assert all(135 <= r <= 239 for r in raws)
    assert all(a <= b for a, b in zip(raws, raws[1:]))


def test_roles_without_a_range_pass_through_and_values_are_clamped(head):
    assert head.raw_value("red", 200) == 200
    assert head.raw_value("pan", 300) == 255
    assert head.raw_value("pan", -5) == 0


def test_profile_roundtrips_through_json_with_ranges(head):
    again = FixtureProfile.from_dict(json.loads(json.dumps(head.to_dict())))
    assert again.role_ranges["dimmer"] == RoleRange(min=10, max=134, zero=0)
    assert again.raw_value("strobe", 128) == head.raw_value("strobe", 128)


def test_profiles_without_ranges_still_load():
    beamz = FixtureLibrary().get("beamz-mhl108-mkii-11ch")
    assert beamz.role_ranges == {}
    assert beamz.raw_value("dimmer", 200) == 200


def _engine():
    room = Room()
    room.add_fixture(FixtureInstance(id="h1", name="H1", profile_id=PROFILE_ID, start_address=1))
    room.add_fixture(FixtureInstance(id="h2", name="H2", profile_id=PROFILE_ID, start_address=15))
    return ShowEngine(room, FixtureLibrary(), SimulatedDmxOutput())


def test_engine_full_dimmer_slider_never_reaches_strobe_range():
    engine = _engine()
    for logical in (0, 1, 128, 200, 255):
        engine.set_dimmer("h1", logical)
        raw = engine.dmx.get_channel(6)  # head 1: dimmer/strobe channel is offset 6
        assert raw < 135, (logical, raw)
        assert engine.state_for("h1").values["dimmer"] == logical  # UI still sees logical


def test_engine_strobe_writes_the_strobe_band():
    engine = _engine()
    engine.set_strobe("h2", 255)
    assert engine.dmx.get_channel(20) == 239   # head 2: 15 + 6 - 1
    engine.set_strobe("h2", 1)
    assert engine.dmx.get_channel(20) == 135


def test_default_dimmer_is_full_brightness_not_strobe():
    engine = _engine()
    assert engine.dmx.get_channel(6) == 134
    assert engine.dmx.get_channel(20) == 134


def test_resaving_a_profile_from_the_creator_keeps_its_ranges(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import context as context_module
    from app.main import app
    from app.storage import Storage

    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage",
                        lambda data_dir=None: Storage(data_dir=tmp_path / "data"))
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    try:
        client = TestClient(app)
        profile = client.get("/api/fixtures/profiles").json()
        head = next(p for p in profile if p["id"] == PROFILE_ID)
        head.pop("role_ranges")  # what the creator UI sends: no ranges field
        saved = client.post("/api/fixtures/profiles", json=head).json()
        assert saved["role_ranges"]["dimmer"] == {"min": 10, "max": 134, "zero": 0}
    finally:
        context_module.reset_context()
