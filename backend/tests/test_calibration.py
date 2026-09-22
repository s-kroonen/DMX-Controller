"""Calibration tools (edit mode): aim every head at one point, nudge offsets, a dim beam that is
put back afterwards, and a sweep that moves one point along a path."""

import time

import pytest
from fastapi.testclient import TestClient

from app import context as context_module
from app.main import app
from app.storage import Storage

HEAD = "generic-moving-head-16ch"
PAR = "generic-par-rgb-4ch"
KLS = "eurolite-kls-120-fx-21ch"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "data"))
    with TestClient(app) as c:
        yield c
    context_module.reset_context()


def add(client, fid, profile, address, x=0.0, y=0.0, z=3.0, pitch=0.0):
    r = client.post("/api/room/fixtures", json={
        "id": fid, "name": fid, "profile_id": profile, "start_address": address,
        "position": {"x": x, "y": y, "z": z}, "orientation": {"yaw_deg": 0, "pitch_deg": pitch, "roll_deg": 0}})
    assert r.status_code == 200, r.text


def two_heads(client):
    add(client, "h1", HEAD, 1, x=-2.0)
    add(client, "h2", HEAD, 20, x=2.0)


def aim(client, ids, x=0.0, y=4.0, z=0.0):
    return client.post("/api/calibration/aim", json={"target_ids": ids, "x": x, "y": y, "z": z})


def test_every_head_aims_at_the_same_point_and_reports_where_it_ended_up(client):
    two_heads(client)
    r = aim(client, ["h1", "h2"]).json()["results"]
    assert set(r) == {"h1", "h2"}
    for res in r.values():
        assert res["ok"] and "pan_angle_deg" in res and "tilt_angle_deg" in res
        assert 0 <= res["pan_dmx"] <= 255 and 0 <= res["tilt_dmx"] <= 255
    assert r["h1"]["pan_dmx"] != r["h2"]["pan_dmx"]          # mirrored positions, so different pan


def test_a_fixture_without_pan_tilt_is_reported_not_moved(client):
    add(client, "par", PAR, 40)
    res = aim(client, ["par"]).json()["results"]["par"]
    assert res["ok"] is False and "no pan/tilt" in res["reason"]


def test_a_group_can_be_the_target(client):
    two_heads(client)
    r = aim(client, ["all"]).json()["results"]
    assert set(r) == {"h1", "h2"}


def test_offsets_move_the_head_and_are_saved(client, tmp_path):
    two_heads(client)
    before = aim(client, ["h1"]).json()["results"]["h1"]["pan_dmx"]
    r = client.post("/api/calibration/offsets", json={"fixture_id": "h1", "pan_offset_deg": 10.0,
                                                      "point": {"x": 0, "y": 4, "z": 0}}).json()
    assert r["fixture"]["pan_offset_deg"] == 10.0
    assert r["result"]["pan_dmx"] != before                  # the head was re-aimed with the new offset
    saved = client.get("/api/room").json()["fixtures"]
    assert next(f for f in saved if f["id"] == "h1")["pan_offset_deg"] == 10.0
    assert (tmp_path / "data" / "room.json").read_text().count('"pan_offset_deg": 10.0') == 1


def test_offsets_are_clamped_and_flags_can_change(client):
    two_heads(client)
    r = client.post("/api/calibration/offsets", json={"fixture_id": "h1", "tilt_offset_deg": 999,
                                                      "inverted_pan": True}).json()["fixture"]
    assert r["tilt_offset_deg"] == 180.0 and r["inverted_pan"] is True
    assert client.post("/api/calibration/offsets", json={"fixture_id": "nope"}).status_code == 404


def test_calibration_is_refused_in_show_mode(client):
    two_heads(client)
    client.put("/api/mode", json={"mode": "show"})
    for path, body in [
        ("/api/calibration/aim", {"target_ids": ["h1"], "x": 0, "y": 4, "z": 0}),
        ("/api/calibration/beam", {"target_ids": ["h1"], "on": True}),
        ("/api/calibration/offsets", {"fixture_id": "h1", "pan_offset_deg": 1}),
        ("/api/calibration/sweep", {"target_ids": ["h1"], "points": [{"x": 0, "y": 1, "z": 0}, {"x": 1, "y": 1, "z": 0}]}),
        ("/api/calibration/pan-tilt", {"target_id": "h1", "pan": 1, "tilt": 1}),
    ]:
        assert client.post(path, json=body).status_code == 409, path


def test_the_show_mode_aim_is_still_refused_in_edit_mode(client):
    two_heads(client)
    assert client.post("/api/control/aim", json={"target_id": "h1", "x": 0, "y": 4, "z": 0}).status_code == 409


def dmx(client_ctx, fid, role):
    engine = client_ctx.engine
    profile = engine.profile_for(fid)
    return engine.dmx.get_channel(engine.room.fixtures[fid].channel_for(profile.channels[role]))


def test_the_beam_lights_a_head_dimly_and_puts_it_back(client):
    two_heads(client)
    ctx = context_module.get_context()
    client.post("/api/control/color", json={"target_id": "h1", "red": 10, "green": 20, "blue": 30})
    assert client.post("/api/calibration/beam", json={"target_ids": ["h1"], "on": True}).json()["beam"] == ["h1"]
    assert dmx(ctx, "h1", "red") == 51 and dmx(ctx, "h1", "green") == 0 and dmx(ctx, "h1", "blue") == 0
    assert dmx(ctx, "h1", "dimmer") > 0                                      # a beam you can see
    assert client.get("/api/snapshot").json()["calibration"]["beam"] == ["h1"]
    assert client.post("/api/calibration/beam", json={"target_ids": ["h1"], "on": False}).json()["beam"] == []
    assert [dmx(ctx, "h1", r) for r in ("red", "green", "blue")] == [10, 20, 30]      # exactly as before


def test_the_beam_on_a_light_bar_restores_its_zone_colors(client):
    add(client, "bar", KLS, 40)
    client.post("/api/control/color", json={"target_id": "bar", "red": 0, "green": 200, "blue": 0, "zones": ["spot1"]})
    ctx = context_module.get_context()
    client.post("/api/calibration/beam", json={"target_ids": ["bar"], "on": True})
    zones = ctx.engine.state_for("bar").zones
    assert all(zones[z]["color"][0] == 51 for z in ("spot1", "spot2", "derby1", "derby2"))
    client.post("/api/calibration/beam", json={"target_ids": ["bar"], "on": False})
    zones = ctx.engine.state_for("bar").zones
    assert zones["spot1"]["color"][:3] == [0, 200, 0] and zones["spot2"]["color"][:3] == [0, 0, 0]


def test_a_second_beam_on_does_not_overwrite_the_saved_state(client):
    two_heads(client)
    ctx = context_module.get_context()
    client.post("/api/control/color", json={"target_id": "h1", "red": 7, "green": 8, "blue": 9})
    for _ in range(2):
        client.post("/api/calibration/beam", json={"target_ids": ["h1"], "on": True})
    client.post("/api/calibration/beam", json={"target_ids": ["h1"], "on": False})
    assert [dmx(ctx, "h1", r) for r in ("red", "green", "blue")] == [7, 8, 9]


def test_a_sweep_moves_every_head_along_the_path_and_stops(client):
    two_heads(client)
    ctx = context_module.get_context()
    body = {"target_ids": ["h1", "h2"], "seconds_per_leg": 0.5,
            "points": [{"x": -1, "y": 4, "z": 0}, {"x": 1, "y": 4, "z": 0}]}
    assert client.post("/api/calibration/sweep", json=body).json() == {"sweeping": True}
    assert client.get("/api/snapshot").json()["calibration"]["sweeping"] is True
    seen = set()
    for _ in range(12):
        time.sleep(0.05)
        seen.add((dmx(ctx, "h1", "pan"), dmx(ctx, "h2", "pan")))
    assert len(seen) > 3                                                # the pan values move along the path
    client.post("/api/calibration/sweep/stop")
    assert client.get("/api/snapshot").json()["calibration"]["sweeping"] is False
    time.sleep(0.15)
    settled = dmx(ctx, "h1", "pan")
    time.sleep(0.15)
    assert dmx(ctx, "h1", "pan") == settled                             # nothing moves after the stop


def test_a_sweep_needs_two_points(client):
    two_heads(client)
    r = client.post("/api/calibration/sweep", json={"target_ids": ["h1"], "points": [{"x": 0, "y": 4, "z": 0}]})
    assert r.status_code == 400


def test_switching_to_show_mode_stops_the_sweep_and_puts_the_beam_back(client):
    two_heads(client)
    ctx = context_module.get_context()
    client.post("/api/control/color", json={"target_id": "h1", "red": 5, "green": 6, "blue": 7})
    client.post("/api/calibration/beam", json={"target_ids": ["h1"], "on": True})
    client.post("/api/calibration/sweep", json={"target_ids": ["h1"], "seconds_per_leg": 1.0,
                                                "points": [{"x": 0, "y": 4, "z": 0}, {"x": 1, "y": 4, "z": 0}]})
    client.put("/api/mode", json={"mode": "show"})
    snap = client.get("/api/snapshot").json()["calibration"]
    assert snap == {"sweeping": False, "sweep_ids": [], "beam": []}
    assert [dmx(ctx, "h1", r) for r in ("red", "green", "blue")] == [5, 6, 7]


def test_offsets_during_a_sweep_do_not_fight_it(client):
    two_heads(client)
    client.post("/api/calibration/sweep", json={"target_ids": ["h1"], "seconds_per_leg": 1.0,
                                                "points": [{"x": 0, "y": 4, "z": 0}, {"x": 1, "y": 4, "z": 0}]})
    r = client.post("/api/calibration/offsets", json={"fixture_id": "h1", "pan_offset_deg": 3,
                                                      "point": {"x": 0, "y": 4, "z": 0}}).json()
    assert r["result"] is None and r["fixture"]["pan_offset_deg"] == 3       # saved; the sweep uses it next tick
    client.post("/api/calibration/sweep/stop")


def test_raw_pan_tilt_works_in_edit_mode_for_test_tools(client):
    two_heads(client)
    ctx = context_module.get_context()
    assert client.post("/api/calibration/pan-tilt", json={"target_id": "h1", "pan": 100, "tilt": 50}).status_code == 200
    assert dmx(ctx, "h1", "pan") == 100 and dmx(ctx, "h1", "tilt") == 50


# ---- solving a head's mounting from marks ---------------------------------------------------------------

MARKS = [(0, 0, 0), (6, 0, 0), (6, 8, 0), (0, 8, 0), (3, 4, 0), (0, 4, 2.0), (6, 4, 2.0)]


def record_marks(client, fid, marks=MARKS):
    """Aim at each mark with the head's TRUE mounting and record the pan/tilt it took: what an operator
    who steers the beam onto each mark would have written down."""
    obs = []
    for x, y, z in marks:
        r = client.post("/api/calibration/aim", json={"target_ids": [fid], "x": x, "y": y, "z": z}).json()["results"][fid]
        assert r["ok"] and r["in_range"], (x, y, z, r)
        obs.append({"x": x, "y": y, "z": z, "pan": r["pan_dmx"], "pan_fine": r["pan_fine_dmx"],
                    "tilt": r["tilt_dmx"], "tilt_fine": r["tilt_fine_dmx"]})
    return obs


def set_fixture(client, fid, **fields):
    current = next(f for f in client.get("/api/room").json()["fixtures"] if f["id"] == fid)
    body = {"name": current["name"], "profile_id": current["profile_id"], "start_address": current["start_address"],
            "position": current["position"], "orientation": current["orientation"],
            "pan_offset_deg": current["pan_offset_deg"], "tilt_offset_deg": current["tilt_offset_deg"],
            "inverted_pan": current["inverted_pan"], "inverted_tilt": current["inverted_tilt"], **fields}
    assert client.put(f"/api/room/fixtures/{fid}", json=body).status_code == 200


def test_a_whole_calibration_session_recovers_the_true_mounting(client):
    add(client, "h1", HEAD, 1, x=2.0, y=3.0, z=3.4)
    true = {"yaw_deg": 40.0, "pitch_deg": 0.0, "roll_deg": 0.0}
    set_fixture(client, "h1", orientation=true, pan_offset_deg=7.0, tilt_offset_deg=-4.0)
    observations = record_marks(client, "h1")
    set_fixture(client, "h1", orientation={"yaw_deg": 52.0, "pitch_deg": 6.0, "roll_deg": 0.0},
                pan_offset_deg=0.0, tilt_offset_deg=0.0)                   # what the app believed before

    fit = client.post("/api/calibration/solve", json={"fixture_id": "h1", "observations": observations}).json()
    assert fit["rms_before_deg"] > 3.0 and fit["rms_deg"] < 0.1
    assert fit["after"]["tilt_offset_deg"] == pytest.approx(-4.0, abs=0.2)
    assert client.get("/api/room").json()["fixtures"][0]["orientation"]["yaw_deg"] == 52.0     # solving changes nothing

    applied = client.post("/api/calibration/apply", json={
        "fixture_id": "h1", **{k: fit["after"][k] for k in ("yaw_deg", "pitch_deg", "pan_offset_deg", "tilt_offset_deg",
                                                              "inverted_pan", "inverted_tilt")}}).json()["fixture"]
    # the applied mounting explains every recording: the beam direction that goes with each recorded pan/tilt
    # points at its mark (compared as directions, since the head may reach a point by more than one pan/tilt)
    import math

    from app.fixtures.library import FixtureLibrary
    from app.room.ik import beam_from_pan_tilt
    from app.room.model import Orientation
    from app.room.solver import dmx_to_angle

    profile = FixtureLibrary().get(HEAD)
    for o in observations:
        d = beam_from_pan_tilt(
            Orientation(applied["orientation"]["yaw_deg"], applied["orientation"]["pitch_deg"], 0.0),
            dmx_to_angle(o["pan"], o["pan_fine"], profile.pan_range_deg), dmx_to_angle(o["tilt"], o["tilt_fine"], profile.tilt_range_deg),
            applied["inverted_pan"], applied["inverted_tilt"], applied["pan_offset_deg"], applied["tilt_offset_deg"])
        to_mark = (o["x"] - applied["position"]["x"], o["y"] - applied["position"]["y"], o["z"] - applied["position"]["z"])
        n = math.sqrt(sum(c * c for c in to_mark))
        cosine = (d.x * to_mark[0] + d.y * to_mark[1] + d.z * to_mark[2]) / n
        assert math.degrees(math.acos(min(1.0, cosine))) < 0.3
    assert applied["tilt_offset_deg"] == fit["after"]["tilt_offset_deg"]


def test_the_position_can_be_solved_and_applied(client):
    add(client, "h1", HEAD, 1, x=2.0, y=3.0, z=3.4)
    set_fixture(client, "h1", orientation={"yaw_deg": 40.0, "pitch_deg": 0.0, "roll_deg": 0.0}, pan_offset_deg=5.0)
    observations = record_marks(client, "h1")
    set_fixture(client, "h1", position={"x": 2.4, "y": 2.8, "z": 3.2}, orientation={"yaw_deg": 46.0, "pitch_deg": 4.0, "roll_deg": 0.0},
                pan_offset_deg=0.0)
    fit = client.post("/api/calibration/solve", json={"fixture_id": "h1", "observations": observations,
                                                      "solve_position": True}).json()
    assert fit["solved_position"] and fit["rms_deg"] < 0.2
    pos = fit["after"]["position"]
    assert (pos["x"], pos["y"], pos["z"]) == pytest.approx((2.0, 3.0, 3.4), abs=0.1)
    fixture = client.post("/api/calibration/apply", json={"fixture_id": "h1", **{
        k: fit["after"][k] for k in ("yaw_deg", "pitch_deg", "pan_offset_deg", "tilt_offset_deg", "inverted_pan",
                                     "inverted_tilt", "position")}}).json()["fixture"]
    assert fixture["position"]["x"] == pytest.approx(2.0, abs=0.1)


def test_solving_needs_enough_marks_and_a_pan_tilt_fixture(client):
    add(client, "h1", HEAD, 1, x=2.0, y=3.0, z=3.4)
    add(client, "par", PAR, 40)
    two = record_marks(client, "h1", MARKS[:2])
    assert client.post("/api/calibration/solve", json={"fixture_id": "h1", "observations": two}).status_code == 400
    assert client.post("/api/calibration/solve", json={"fixture_id": "par", "observations": two}).status_code == 400
    assert client.post("/api/calibration/solve", json={"fixture_id": "nope", "observations": two}).status_code == 404
    assert client.post("/api/calibration/apply", json={"fixture_id": "nope", "yaw_deg": 0, "pitch_deg": 0, "pan_offset_deg": 0,
                                                       "tilt_offset_deg": 0, "inverted_pan": False,
                                                       "inverted_tilt": False}).status_code == 404


def test_solving_and_applying_are_refused_in_show_mode(client):
    add(client, "h1", HEAD, 1, x=2.0, y=3.0, z=3.4)
    observations = record_marks(client, "h1")
    client.put("/api/mode", json={"mode": "show"})
    assert client.post("/api/calibration/solve", json={"fixture_id": "h1", "observations": observations}).status_code == 409
    assert client.post("/api/calibration/apply", json={"fixture_id": "h1", "yaw_deg": 0, "pitch_deg": 0, "pan_offset_deg": 0,
                                                       "tilt_offset_deg": 0, "inverted_pan": False,
                                                       "inverted_tilt": False}).status_code == 409
