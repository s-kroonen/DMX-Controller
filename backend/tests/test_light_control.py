"""Freestyler-style dimmer / strobe / shutter behaviour.

Fixtures used: two 14ch heads (ONE shared dimmer+strobe channel, ch6 of each:
DMX 6 and 20) and a Beamz MHL108 (SEPARATE dimmer ch5 and strobe ch10: DMX 33
and 38, no range mapping)."""

import pytest

from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.room.model import FixtureInstance, Room
from app.show.engine import ShowEngine

HEAD = "generic-rgbw-moving-head-14ch"
BEAMZ = "beamz-mhl108-mkii-11ch"
H1_CH, H2_CH = 6, 20         # shared dimmer/strobe channel of each head
B_DIM, B_STROBE = 33, 38     # Beamz separate channels


@pytest.fixture()
def engine():
    room = Room()
    room.add_fixture(FixtureInstance(id="h1", name="H1", profile_id=HEAD, start_address=1))
    room.add_fixture(FixtureInstance(id="h2", name="H2", profile_id=HEAD, start_address=15))
    room.add_fixture(FixtureInstance(id="bz", name="Beamz", profile_id=BEAMZ, start_address=29))
    return ShowEngine(room, FixtureLibrary(), SimulatedDmxOutput())


def logical(pct):
    return round(pct * 255 / 100)


def ch(engine, n):
    return engine.dmx.get_channel(n)


def vals(engine, fid):
    return engine.state_for(fid).values


# ---- all fixtures share one channel (heads only) -------------------------

def test_shared_only_touching_strobe_zeroes_the_dimmer(engine):
    engine.set_dimmer(["h1", "h2"], logical(80))
    assert vals(engine, "h1")["dimmer"] == logical(80)

    engine.set_strobe(["h1", "h2"], logical(50))

    assert vals(engine, "h1")["dimmer"] == 0 and vals(engine, "h2")["dimmer"] == 0
    for n in (H1_CH, H2_CH):
        assert 135 <= ch(engine, n) <= 239           # strobe band on the wire


def test_shared_only_touching_dimmer_zeroes_the_strobe(engine):
    engine.set_strobe(["h1", "h2"], logical(100))
    engine.set_dimmer(["h1", "h2"], logical(60))

    assert vals(engine, "h1")["strobe"] == 0
    for n in (H1_CH, H2_CH):
        assert 10 <= ch(engine, n) <= 134            # dimmer band again


def test_shared_only_strobe_off_returns_the_channel_to_dimmer_which_is_now_zero(engine):
    engine.set_strobe(["h1"], logical(100))
    engine.set_strobe(["h1"], 0)
    assert ch(engine, H1_CH) == 0                    # dimmer went to 0 when strobe was touched


def test_a_single_shared_fixture_selected_alone_follows_the_same_rule(engine):
    engine.set_dimmer("h1", 255)
    engine.set_strobe("h1", 128)
    assert vals(engine, "h1")["dimmer"] == 0


# ---- mixed selection (head + Beamz) --------------------------------------

def test_mixed_selection_holds_dimmer_when_strobe_is_adjusted(engine):
    engine.set_dimmer(["h1", "bz"], logical(60))
    engine.set_strobe(["h1", "bz"], logical(100))

    assert vals(engine, "h1")["dimmer"] == logical(60)   # held, not zeroed
    assert vals(engine, "bz")["dimmer"] == logical(60)
    assert ch(engine, H1_CH) == 239                      # head strobes at its max...
    assert ch(engine, B_STROBE) == 255                   # ...and so does the Beamz (own mapping)
    assert ch(engine, B_DIM) == logical(60)              # Beamz keeps its brightness


def test_mixed_selection_strobe_off_restores_the_held_dimmer_on_the_head(engine):
    engine.set_dimmer(["h1", "bz"], logical(60))
    engine.set_strobe(["h1", "bz"], logical(100))
    engine.set_strobe(["h1", "bz"], 0)

    assert ch(engine, H1_CH) == engine.profile_for("h1").raw_value("dimmer", logical(60))
    assert ch(engine, B_STROBE) == 0
    assert ch(engine, B_DIM) == logical(60)


def test_same_percentage_maps_to_each_fixtures_own_values(engine):
    engine.set_strobe(["h1", "bz"], logical(50))
    assert ch(engine, H1_CH) == engine.profile_for("h1").raw_value("strobe", logical(50))
    assert ch(engine, B_STROBE) == logical(50)
    assert ch(engine, H1_CH) != ch(engine, B_STROBE)     # different hardware, same intent


def test_group_and_fixture_targets_are_judged_together(engine):
    from app.groups.model import Group

    engine.add_group(Group(id="g", name="G", fixture_ids=["h1", "h2"]))
    engine.set_dimmer(["g", "bz"], logical(70))          # heads via group + the Beamz
    engine.set_strobe(["g", "bz"], logical(100))
    assert vals(engine, "h1")["dimmer"] == logical(70)   # mixed -> held, not zeroed


def test_separate_channel_fixture_alone_never_zeroes_anything(engine):
    engine.set_dimmer("bz", logical(40))
    engine.set_strobe("bz", logical(100))
    assert vals(engine, "bz")["dimmer"] == logical(40)
    assert ch(engine, B_DIM) == logical(40)


# ---- shutter ----------------------------------------------------------------

def test_closed_darkens_everything_and_open_restores(engine):
    engine.set_dimmer(["h1", "bz"], logical(60))
    engine.set_shutter(["h1", "bz"], True)
    assert ch(engine, H1_CH) == 0 and ch(engine, B_DIM) == 0

    engine.set_shutter(["h1", "bz"], False)
    assert ch(engine, H1_CH) == engine.profile_for("h1").raw_value("dimmer", logical(60))
    assert ch(engine, B_DIM) == logical(60)


def test_open_after_strobing_is_never_dark_on_a_shared_fixture(engine):
    engine.set_strobe("h1", 255)          # dimmer -> 0
    engine.set_shutter("h1", False)       # Open
    assert vals(engine, "h1")["strobe"] == 0
    assert ch(engine, H1_CH) == 134       # full dimmer, not 0


def test_open_keeps_an_existing_dimmer_level(engine):
    engine.set_dimmer("h1", logical(30))
    engine.set_shutter("h1", False)
    assert vals(engine, "h1")["dimmer"] == logical(30)


def test_touching_a_slider_reopens_a_closed_shutter(engine):
    engine.set_dimmer("bz", logical(50))
    engine.set_shutter("bz", True)
    engine.set_dimmer("bz", logical(50))
    assert not engine.state_for("bz").shutter_closed
    assert ch(engine, B_DIM) == logical(50)


def test_closed_flag_is_reported_in_state(engine):
    engine.set_shutter("h1", True)
    assert engine.state_for("h1").to_dict()["shutter_closed"] is True


# ---- API ---------------------------------------------------------------------

def test_api_accepts_a_whole_selection_in_one_call(tmp_path, monkeypatch):
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
        for fid, prof, addr in (("h1", HEAD, 1), ("bz", BEAMZ, 29)):
            client.post("/api/room/fixtures", json={
                "id": fid, "name": fid, "profile_id": prof, "start_address": addr})
        client.post("/api/control/dimmer", json={"target_ids": ["h1", "bz"], "value": 153})
        r = client.post("/api/control/strobe", json={"target_ids": ["h1", "bz"], "value": 255})
        assert r.status_code == 200
        r = client.post("/api/control/shutter", json={"target_ids": ["h1", "bz"], "closed": True})
        assert r.status_code == 200
        state = client.get("/api/snapshot").json()["fixture_state"]
        assert state["h1"]["values"]["dimmer"] == 153       # mixed -> held
        assert state["bz"]["shutter_closed"] is True
        assert client.post("/api/control/dimmer", json={"value": 1}).status_code == 422  # no target
        # legacy single target_id still works
        assert client.post("/api/control/dimmer", json={"target_id": "h1", "value": 10}).status_code == 200
    finally:
        context_module.reset_context()
