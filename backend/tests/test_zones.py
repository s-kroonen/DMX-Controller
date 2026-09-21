"""Zones: independently colored sections of one fixture (a light bar's spots and derbies).

Fixture used: Eurolite KLS-120 FX in 21ch mode at DMX address 40, so channel n of its map
lands on DMX channel 39 + n. A plain RGB PAR (address 20) and the Beamz check that
non-zone fixtures behave exactly as before."""

import json
import time

import pytest

from app.audio.analyzer import AudioFeatures
from app.audio.service import SoundService
from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.fixtures.schema import ChannelRange, FixtureProfile, Zone
from app.room.model import FixtureInstance, Room
from app.show.engine import ShowEngine
from app.storage import Storage

KLS = "eurolite-kls-120-fx-21ch"
PAR = "generic-par-rgb-4ch"
BASE = 40


def dmx(n):
    """DMX channel of KLS channel n (1-indexed in the manufacturer's map)."""
    return BASE + n - 1


SPOT1 = [dmx(c) for c in (3, 4, 5, 6)]
SPOT2 = [dmx(c) for c in (7, 8, 9, 10)]
DERBY1 = [dmx(c) for c in (12, 13, 14, 15)]
DERBY2 = [dmx(c) for c in (16, 17, 18, 19)]


@pytest.fixture()
def engine():
    room = Room()
    room.add_fixture(FixtureInstance(id="bar", name="Bar", profile_id=KLS, start_address=BASE))
    room.add_fixture(FixtureInstance(id="par", name="Par", profile_id=PAR, start_address=20))
    return ShowEngine(room, FixtureLibrary(), SimulatedDmxOutput())


def vals(engine, channels):
    return [engine.dmx.get_channel(c) for c in channels]


# ---- the profile ---------------------------------------------------------------------

def test_kls_channel_map_matches_the_manufacturers_21ch_table():
    p = FixtureLibrary().get(KLS)
    assert p.channel_count == 21 and p.fixture_type == "light_bar"
    assert p.channels == {"dimmer": 1, "strobe": 2}
    zones = {z.id: z for z in p.zones}
    assert list(zones) == ["derby1", "spot1", "spot2", "derby2"]     # left to right as seen from the front
    assert zones["spot1"].channels == {"red": 3, "green": 4, "blue": 5, "white": 6}
    assert zones["spot2"].channels == {"red": 7, "green": 8, "blue": 9, "white": 10}
    assert zones["derby1"].channels == {"red": 12, "green": 13, "blue": 14, "white": 15}
    assert zones["derby2"].channels == {"red": 16, "green": 17, "blue": 18, "white": 19}
    assert [z.kind for z in p.zones] == ["derby", "spot", "spot", "derby"]
    used = {c for z in p.zones for c in z.channels.values()} | set(p.channels.values()) \
        | {c.channel for c in p.custom_channels}
    assert used == set(range(1, 22))                       # every channel accounted for, none twice
    assert p.pan_range_deg is None and not p.has_pan_tilt() and p.has_color()


def test_kls_custom_channels_carry_the_documented_ranges():
    p = FixtureLibrary().get(KLS)
    by = {c.channel: c for c in p.custom_channels}
    strobe_presets = [("Off", 0, 9), ("Slow", 10, 129), ("Medium", 130, 254), ("Max", 255, 255)]
    assert by[2].label == "Spot Strobe" and by[11].label == "Derby Strobe"   # both strobes have their own control
    assert [(r.label, r.min, r.max) for r in by[2].ranges] == strobe_presets
    assert [(r.label, r.min, r.max) for r in by[11].ranges] == strobe_presets
    assert [(r.label, r.min, r.max) for r in by[20].ranges] == [
        ("Off", 0, 9), ("Low", 10, 29), ("Medium", 30, 49), ("Fast", 50, 255)]
    assert [(r.label, r.min, r.max) for r in by[21].ranges] == [
        ("Off", 0, 9), ("Auto 1", 10, 49), ("Auto 2", 50, 89), ("Auto 3", 90, 129),
        ("Sound 1", 130, 169), ("Sound 2", 170, 209), ("Sound 3", 210, 255)]
    # the ranges are presets: they tile the whole channel, so the slider and buttons always agree
    for ch in (2, 11, 20, 21):
        spans = by[ch].ranges
        assert spans[0].min == 0 and spans[-1].max == 255
        assert all(a.max + 1 == b.min for a, b in zip(spans, spans[1:]))


def test_master_strobe_uses_10_to_255_and_zero_is_off():
    p = FixtureLibrary().get(KLS)
    assert p.raw_value("strobe", 0) == 0
    assert p.raw_value("strobe", 1) == 10
    assert p.raw_value("strobe", 255) == 255


def test_profile_roundtrips_through_json_with_zones_and_ranges():
    p = FixtureLibrary().get(KLS)
    again = FixtureProfile.from_dict(json.loads(json.dumps(p.to_dict())))
    assert [z.id for z in again.zones] == [z.id for z in p.zones]
    assert again.zones[0].channels["blue"] == 14
    assert again.custom_channels[3].ranges[6] == ChannelRange("Sound 3", 210, 255)


def test_a_plain_fixture_is_one_implicit_main_zone():
    par = FixtureLibrary().get(PAR)
    assert not par.has_zones()
    zones = par.zone_list()
    assert [z.id for z in zones] == ["main"] and zones[0].channels["red"] == par.channels["red"]
    smoke = FixtureLibrary().get("generic-smoke-machine-2ch")
    assert smoke is not None and smoke.zone_list() == []     # no color channels -> no zones at all


# ---- engine: color by zone ---------------------------------------------------------------

def test_set_color_without_zones_lights_every_zone(engine):
    engine.set_color("bar", 255, 0, 0)
    for zone in (SPOT1, SPOT2, DERBY1, DERBY2):
        assert vals(engine, zone) == [255, 0, 0, 0]


def test_set_color_only_touches_the_named_zones(engine):
    engine.set_color("bar", 0, 0, 255, zones=["derby1"])
    assert vals(engine, DERBY1) == [0, 0, 255, 0]
    assert vals(engine, SPOT1 + SPOT2 + DERBY2) == [0] * 12


def test_white_is_kept_when_only_rgb_is_set(engine):
    engine.set_color("bar", 10, 20, 30, white=200, zones=["spot1"])
    engine.set_color("bar", 50, 60, 70, zones=["spot1"])
    assert vals(engine, SPOT1) == [50, 60, 70, 200]


def test_zone_dimmer_scales_the_color_of_just_that_zone(engine):
    engine.set_color("bar", 200, 100, 0)
    engine.set_dimmer("bar", 128, zones=["spot2"])
    assert vals(engine, SPOT2) == [round(200 * 128 / 255), round(100 * 128 / 255), 0, 0]
    assert vals(engine, SPOT1) == [200, 100, 0, 0]          # other zones untouched
    assert engine.dmx.get_channel(dmx(1)) == 255            # master dimmer untouched
    engine.set_dimmer("bar", 255, zones=["spot2"])
    assert vals(engine, SPOT2) == [200, 100, 0, 0]


def test_a_new_color_respects_the_zones_current_brightness(engine):
    engine.set_dimmer("bar", 0, zones=["derby2"])
    engine.set_color("bar", 255, 255, 255)
    assert vals(engine, DERBY2) == [0, 0, 0, 0]             # stays dark: its brightness is 0
    engine.set_dimmer("bar", 255, zones=["derby2"])
    assert vals(engine, DERBY2) == [255, 255, 255, 0]


def test_master_dimmer_and_strobe_are_separate_channels(engine):
    engine.set_dimmer("bar", 100)
    assert engine.dmx.get_channel(dmx(1)) == 100
    assert vals(engine, SPOT1) == [0, 0, 0, 0]              # color untouched by the master dimmer
    engine.set_strobe("bar", 255)
    assert engine.dmx.get_channel(dmx(2)) == 255
    engine.set_strobe("bar", 1)
    assert engine.dmx.get_channel(dmx(2)) == 10             # slowest strobe is DMX 10, not 1
    engine.set_strobe("bar", 0)
    assert engine.dmx.get_channel(dmx(2)) == 0


def test_default_master_dimmer_is_full_so_colors_show_immediately(engine):
    assert engine.dmx.get_channel(dmx(1)) == 255


def test_custom_channels_write_their_own_dmx_channel(engine):
    engine.set_custom("bar", "Derby Motor", 40)
    assert engine.dmx.get_channel(dmx(20)) == 40
    engine.set_custom("bar", "Built-in Program (overrides colors)", 175)
    assert engine.dmx.get_channel(dmx(21)) == 175
    engine.set_custom("bar", "Derby Strobe", 120)
    assert engine.dmx.get_channel(dmx(11)) == 120


def test_zone_state_is_reported_for_the_ui_and_cleared_by_blackout(engine):
    engine.set_color("bar", 1, 2, 3, white=4, zones=["spot1"])
    engine.set_dimmer("bar", 77, zones=["spot1"])
    zones = engine.state_for("bar").to_dict()["zones"]
    assert zones["spot1"] == {"color": [1, 2, 3, 4], "dimmer": 77}
    assert "spot2" not in zones
    assert "zones" in engine.snapshot()["fixture_state"]["bar"]
    engine.blackout()
    assert engine.state_for("bar").to_dict()["zones"] == {}
    assert vals(engine, SPOT1) == [0, 0, 0, 0]


# ---- mixed selections: a bar and a plain PAR ----------------------------------------------

def test_a_plain_par_is_colored_normally_when_zones_are_not_named(engine):
    engine.set_color(["bar", "par"], 9, 8, 7)
    assert vals(engine, SPOT1) == [9, 8, 7, 0]
    par = FixtureLibrary().get(PAR)
    assert engine.dmx.get_channel(20 + par.channels["red"] - 1) == 9


def test_naming_a_bar_zone_skips_the_plain_par(engine):
    engine.set_color(["bar", "par"], 200, 0, 0, zones=["spot1"])
    assert vals(engine, SPOT1) == [200, 0, 0, 0]
    par = FixtureLibrary().get(PAR)
    assert engine.dmx.get_channel(20 + par.channels["red"] - 1) == 0


def test_naming_main_targets_only_the_plain_fixture(engine):
    engine.set_color(["bar", "par"], 200, 0, 0, zones=["main"])
    assert vals(engine, SPOT1 + DERBY1) == [0] * 8
    par = FixtureLibrary().get(PAR)
    assert engine.dmx.get_channel(20 + par.channels["red"] - 1) == 200


def test_master_dimmer_on_a_mixed_selection_reaches_both(engine):
    engine.set_dimmer(["bar", "par"], 90)
    assert engine.dmx.get_channel(dmx(1)) == 90
    par = FixtureLibrary().get(PAR)
    assert engine.dmx.get_channel(20 + par.channels["dimmer"] - 1) == 90


def test_zones_for_lists_declared_zones_across_a_selection(engine):
    assert [z["id"] for z in engine.zones_for(["bar", "par"])] == ["derby1", "spot1", "spot2", "derby2"]
    assert engine.zones_for("par") == []
    assert engine.zones_for("bar")[0] == {"id": "derby1", "label": "Derby 1", "kind": "derby"}


# ---- a profile whose zones have their own dimmer channel ----------------------------------------

def test_a_zone_with_its_own_dimmer_channel_is_not_color_scaled(tmp_path):
    lib = FixtureLibrary(user_dir=tmp_path / "fx")
    lib.save(FixtureProfile(
        id="two-cell", name="Two cell", channel_count=8, fixture_type="light_bar",
        zones=[Zone("a", "A", "cell", {"dimmer": 1, "red": 2, "green": 3, "blue": 4}),
               Zone("b", "B", "cell", {"dimmer": 5, "red": 6, "green": 7, "blue": 8})]))
    room = Room()
    room.add_fixture(FixtureInstance(id="f", name="F", profile_id="two-cell", start_address=1))
    eng = ShowEngine(room, lib, SimulatedDmxOutput())
    eng.set_color("f", 200, 100, 50, zones=["a"])
    eng.set_dimmer("f", 128, zones=["a"])
    assert [eng.dmx.get_channel(c) for c in (1, 2, 3, 4)] == [128, 200, 100, 50]   # dimmer channel, color raw


def test_a_zoned_fixture_without_a_master_dimmer_dims_all_zones_on_plain_dimmer(tmp_path):
    lib = FixtureLibrary(user_dir=tmp_path / "fx")
    lib.save(FixtureProfile(
        id="bar-nomaster", name="Bar", channel_count=6, fixture_type="light_bar",
        zones=[Zone("a", "A", "cell", {"red": 1, "green": 2, "blue": 3}),
               Zone("b", "B", "cell", {"red": 4, "green": 5, "blue": 6})]))
    room = Room()
    room.add_fixture(FixtureInstance(id="f", name="F", profile_id="bar-nomaster", start_address=1))
    eng = ShowEngine(room, lib, SimulatedDmxOutput())
    eng.set_color("f", 100, 100, 100)
    eng.set_dimmer("f", 51)                                   # no master dimmer channel -> zone dimmers
    assert [eng.dmx.get_channel(c) for c in range(1, 7)] == [20] * 6


# ---- sound functions with zones -----------------------------------------------------------------

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
def service(engine, tmp_path):
    svc = SoundService(engine, Storage(data_dir=tmp_path / "data"),
                       device_lister=lambda: [], device_finder=lambda *a, **k: None)
    svc.analyzer = StubAnalyzer()
    svc._seen_analyzer = svc.analyzer
    svc.configure(sound_mode="both")
    yield svc
    svc.shutdown()


def beat(service):
    service.analyzer.f.beat_count += 1


def tick(service, n=1, dt=0.025):
    for i in range(n):
        service.tick(dt, time.monotonic() + i * dt)


def test_beat_color_on_chosen_zones_leaves_the_others_alone(service, engine):
    service.add_function("beat_color", ["bar"], {"palette": ["#ff0000"], "every": 1}, zones=["derby1", "derby2"])
    beat(service)
    tick(service)
    assert vals(engine, DERBY1) == [255, 0, 0, 0] and vals(engine, DERBY2) == [255, 0, 0, 0]
    assert vals(engine, SPOT1 + SPOT2) == [0] * 8


def test_vu_dimmer_with_zones_dims_only_those_zones(service, engine):
    engine.set_color("bar", 200, 200, 200)
    service.add_function("vu_dimmer", ["bar"], {"attack_ms": 0, "release_ms": 0}, zones=["spot1"])
    service.analyzer.f.level = 0.0
    tick(service)
    assert vals(engine, SPOT1) == [0, 0, 0, 0]
    assert vals(engine, SPOT2) == [200, 200, 200, 0]
    assert engine.dmx.get_channel(dmx(1)) == 255


def test_zone_chase_walks_the_zones_on_the_beat(service, engine):
    service.add_function("zone_chase", ["bar"], {"palette": ["#ff0000"], "every": 1, "pattern": "sequence"})
    order = []
    for _ in range(5):
        beat(service)
        tick(service)
        lit = [name for name, ch in (("spot1", SPOT1), ("spot2", SPOT2), ("derby1", DERBY1), ("derby2", DERBY2))
               if vals(engine, ch)[0] == 255]
        order.append(lit)
    assert order == [["derby1"], ["spot1"], ["spot2"], ["derby2"], ["derby1"]]      # left to right


def test_zone_chase_ping_pong_bounces_without_repeating_the_ends(service, engine):
    service.add_function("zone_chase", ["bar"], {"palette": ["#00ff00"], "every": 1, "pattern": "ping_pong"})
    seen = []
    for _ in range(7):
        beat(service)
        tick(service)
        seen.append([i for i, ch in enumerate((DERBY1, SPOT1, SPOT2, DERBY2)) if vals(engine, ch)[1] == 255][0])
    assert seen == [0, 1, 2, 3, 2, 1, 0]


def test_zone_chase_alternate_is_a_checkerboard(service, engine):
    service.add_function("zone_chase", ["bar"], {"palette": ["#0000ff"], "every": 1, "pattern": "alternate"})
    beat(service)
    tick(service)
    # zone order is left to right (derby1, spot1, spot2, derby2): step 0 lights positions 0 and 2
    assert [vals(engine, ch)[2] for ch in (DERBY1, SPOT1, SPOT2, DERBY2)] == [255, 0, 255, 0]
    beat(service)
    tick(service)
    assert [vals(engine, ch)[2] for ch in (DERBY1, SPOT1, SPOT2, DERBY2)] == [0, 255, 0, 255]


def test_zone_chase_restricted_to_zones_only_chases_those(service, engine):
    service.add_function("zone_chase", ["bar"], {"palette": ["#ff0000"], "every": 1}, zones=["derby1", "derby2"])
    for _ in range(3):
        beat(service)
        tick(service)
    assert vals(engine, SPOT1 + SPOT2) == [0] * 8


def test_zone_chase_ignores_fixtures_without_zones(service, engine):
    service.add_function("zone_chase", ["par"], {"every": 1})
    beat(service)
    tick(service)                                            # nothing to chase: no error, no output
    assert not service.errors


def test_sound_function_zones_persist(engine, tmp_path):
    storage = Storage(data_dir=tmp_path / "data")
    a = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *x, **k: None)
    fn = a.add_function("beat_color", ["bar"], zones=["spot1", "spot2"])
    a.shutdown()
    b = SoundService(engine, storage, device_lister=lambda: [], device_finder=lambda *x, **k: None)
    assert b.functions[fn.id].zones == ["spot1", "spot2"]
    b.update_function(fn.id, zones=[])
    assert b.functions[fn.id].zones == []
    b.shutdown()


def test_function_types_say_which_ones_use_zones():
    from app.audio.functions import function_types_for_ui

    uses = {t["type"]: t["uses_zones"] for t in function_types_for_ui()}
    assert uses["zone_chase"] and uses["beat_color"] and uses["vu_dimmer"] and uses["color_organ"]
    assert not uses["beat_movement"] and not uses["beat_strobe"]


# ---- API ---------------------------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import context as context_module
    from app.main import app

    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "data"))
    monkeypatch.delenv("DMX4ALL_PORT", raising=False)
    try:
        yield TestClient(app)
    finally:
        context_module.reset_context()


def test_api_color_and_dimmer_take_a_zone_list(client):
    client.post("/api/room/fixtures", json={"id": "bar", "name": "Bar", "profile_id": KLS, "start_address": BASE})
    assert client.post("/api/control/color", json={"target_id": "bar", "red": 10, "green": 20, "blue": 30,
                                                     "zones": ["spot2"]}).status_code == 200
    assert client.post("/api/control/dimmer", json={"target_ids": ["bar"], "value": 100,
                                                      "zones": ["spot2"]}).status_code == 200
    zones = client.get("/api/snapshot").json()["fixture_state"]["bar"]["zones"]
    assert zones["spot2"] == {"color": [10, 20, 30, 0], "dimmer": 100}
    assert "spot1" not in zones


def test_api_serves_zones_and_ranges_in_the_profile(client):
    profiles = client.get("/api/fixtures/profiles").json()
    kls = next(p for p in profiles if p["id"] == KLS)
    assert [z["id"] for z in kls["zones"]] == ["derby1", "spot1", "spot2", "derby2"]
    assert kls["custom_channels"][2]["ranges"][3] == {"label": "Fast", "min": 50, "max": 255, "speed": False}


def test_resaving_a_profile_from_the_creator_keeps_zones_and_channel_ranges(client):
    profiles = client.get("/api/fixtures/profiles").json()
    kls = next(p for p in profiles if p["id"] == KLS)
    kls.pop("zones")                                          # the creator UI sends no zones...
    for c in kls["custom_channels"]:
        c["ranges"] = []                                      # ...and no named ranges
    saved = client.post("/api/fixtures/profiles", json=kls).json()
    assert [z["id"] for z in saved["zones"]] == ["derby1", "spot1", "spot2", "derby2"]
    assert len(saved["custom_channels"][3]["ranges"]) == 7


def test_api_sound_function_zones(client):
    fn = client.post("/api/audio/functions", json={"type": "zone_chase", "targets": ["x"],
                                                     "zones": ["spot1"]}).json()
    assert fn["zones"] == ["spot1"]
    fn = client.patch(f"/api/audio/functions/{fn['id']}", json={"zones": ["spot1", "derby2"]}).json()
    assert fn["zones"] == ["spot1", "derby2"]
    types = client.get("/api/audio/function-types").json()
    assert next(t for t in types if t["type"] == "zone_chase")["uses_zones"] is True


# ---- editing zones from the Fixture Creator ------------------------------------------------------

def _profile_payload(client, **changes):
    kls = next(p for p in client.get("/api/fixtures/profiles").json() if p["id"] == KLS)
    kls.update(changes)
    return kls


def test_creator_can_reorder_and_reposition_zones(client):
    zones = _profile_payload(client)["zones"]
    by_id = {z["id"]: z for z in zones}
    by_id["derby1"]["position"], by_id["derby2"]["position"] = 1.0, -1.0      # swap the ends
    saved = client.post("/api/fixtures/profiles", json=_profile_payload(client, zones=[by_id[i] for i in ("derby2", "spot1", "spot2", "derby1")])).json()
    assert [z["id"] for z in saved["zones"]] == ["derby2", "spot1", "spot2", "derby1"]
    assert {z["id"]: z["position"] for z in saved["zones"]}["derby1"] == 1.0


def test_creator_can_add_a_zone_and_remove_all_zones(client):
    kls = _profile_payload(client)
    extra = {"id": "extra", "label": "Extra", "kind": "par", "channels": {"red": 1, "green": 2, "blue": 3}, "position": None}
    saved = client.post("/api/fixtures/profiles", json={**kls, "zones": kls["zones"] + [extra]}).json()
    assert saved["zones"][-1]["id"] == "extra" and saved["zones"][-1]["position"] is None
    cleared = client.post("/api/fixtures/profiles", json={**kls, "zones": []}).json()     # an explicit empty list removes them
    assert cleared["zones"] == []


def test_a_zone_position_is_optional_and_round_trips():
    z = Zone("a", "A")
    assert z.position is None
    p = FixtureProfile(id="p", name="P", zones=[Zone("a", "A", "spot", {"red": 1}, position=-0.5)])
    assert FixtureProfile.from_dict(json.loads(json.dumps(p.to_dict()))).zones[0].position == -0.5


def test_kls_layout_matches_the_real_bar_left_to_right_from_the_front():
    """Measured on the real light (viewer in front): Derby 1, Spot 1, Spot 2, Derby 2; colors
    verified per zone (spot1 red, spot2 green, derby1 blue, derby2 white)."""
    p = FixtureLibrary().get(KLS)
    left_to_right = [z.id for z in sorted(p.zones, key=lambda z: z.position)]
    assert left_to_right == ["derby1", "spot1", "spot2", "derby2"]
    assert [z.kind for z in sorted(p.zones, key=lambda z: z.position)] == ["derby", "spot", "spot", "derby"]


# ---- one strobe control strobes everything: role mirrors ------------------------------------------

def test_kls_master_strobe_also_drives_the_derby_strobe_channel(engine):
    """Measured on the real bar: channel 2 alone strobes only the spots and channel 11 only the
    derbies, so the single Strobe control has to write both."""
    engine.set_strobe("bar", 255)
    assert engine.dmx.get_channel(dmx(2)) == 255 and engine.dmx.get_channel(dmx(11)) == 255
    engine.set_strobe("bar", 1)
    assert engine.dmx.get_channel(dmx(2)) == 10 and engine.dmx.get_channel(dmx(11)) == 10
    engine.set_strobe("bar", 0)
    assert engine.dmx.get_channel(dmx(2)) == 0 and engine.dmx.get_channel(dmx(11)) == 0
    assert engine.state_for("bar").values["custom_11"] == 0        # the Derby Strobe control follows


def test_the_derby_strobe_control_still_works_on_its_own(engine):
    engine.set_custom("bar", "Derby Strobe", 200)
    assert engine.dmx.get_channel(dmx(11)) == 200 and engine.dmx.get_channel(dmx(2)) == 0


def test_closing_the_shutter_also_stops_the_derby_strobe(engine):
    engine.set_strobe("bar", 200)
    engine.set_shutter("bar", True)
    assert engine.dmx.get_channel(dmx(2)) == 0 and engine.dmx.get_channel(dmx(11)) == 0


def test_role_mirrors_round_trip_and_survive_a_creator_resave(client):
    kls = next(p for p in client.get("/api/fixtures/profiles").json() if p["id"] == KLS)
    assert kls["role_mirrors"] == {"strobe": [11]}
    kls.pop("role_mirrors")                                       # the creator UI doesn't send them
    saved = client.post("/api/fixtures/profiles", json=kls).json()
    assert saved["role_mirrors"] == {"strobe": [11]}


def test_a_profile_without_mirrors_is_unaffected(engine):
    engine.set_strobe("par", 200)                                 # a plain PAR: nothing extra is written
    assert engine.dmx.get_channel(dmx(11)) == 0


def test_the_spot_strobe_control_works_on_its_own_and_follows_the_master_strobe(engine):
    engine.set_custom("bar", "Spot Strobe", 130)
    assert engine.dmx.get_channel(dmx(2)) == 130 and engine.dmx.get_channel(dmx(11)) == 0   # derbies untouched
    engine.set_strobe("bar", 255)
    assert engine.state_for("bar").values["custom_2"] == 255       # the Spot Strobe control follows
