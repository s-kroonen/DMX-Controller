"""Edit / show mode, saved rooms in the backend, and keeping saved fixture profiles current."""

import json

import pytest
from fastapi.testclient import TestClient

from app import context as context_module
from app.fixtures.library import BUNDLED_DIR
from app.main import app
from app.storage import Storage

KLS = "eurolite-kls-120-fx-21ch"
PAR = "generic-par-rgb-4ch"
HEAD = "generic-moving-head-16ch"


def make_client(monkeypatch, data_dir):
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage", lambda data_dir_=None: Storage(data_dir=data_dir))
    return TestClient(app)


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture()
def client(monkeypatch, data_dir):
    with make_client(monkeypatch, data_dir) as c:
        yield c
    context_module.reset_context()


def mode(client, value):
    return client.put("/api/mode", json={"mode": value})


def add_fixture(client, fid="f1", profile=PAR, address=1):
    return client.post("/api/room/fixtures", json={"id": fid, "name": fid, "profile_id": profile,
                                                   "start_address": address})


# ---- the mode -----------------------------------------------------------------------------------------

def test_the_app_starts_in_edit_mode(client):
    assert client.get("/api/mode").json() == {"mode": "edit"}
    assert client.get("/api/snapshot").json()["mode"] == "edit"


def test_an_unknown_mode_is_refused(client):
    assert mode(client, "party").status_code == 400
    assert client.get("/api/mode").json()["mode"] == "edit"


def test_the_mode_is_in_every_snapshot(client):
    mode(client, "show")
    assert client.get("/api/snapshot").json()["mode"] == "show"
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["mode"] == "show"


EDIT_ONLY = [
    ("post", "/api/room/fixtures", {"name": "x", "profile_id": PAR, "start_address": 1}),
    ("put", "/api/room", {"name": "n", "dimensions": {"width": 5, "depth": 5, "height": 3}, "floor_points": []}),
    ("post", "/api/groups", {"name": "g", "fixture_ids": []}),
    ("post", "/api/room/safety-zones", {"name": "z", "min_corner": {"x": 0, "y": 0, "z": 0},
                                        "max_corner": {"x": 1, "y": 1, "z": 1}}),
    ("post", "/api/room/objects", {"name": "o", "kind": "box", "position": {"x": 0, "y": 0, "z": 0}}),
    ("post", "/api/fixtures/profiles", {"name": "p", "channel_count": 1}),
    ("post", "/api/animations", {"name": "a", "tracks": []}),
    ("post", "/api/rooms", {"name": "saved"}),
    ("post", "/api/rooms/new", {"name": "fresh"}),
    ("post", "/api/config/import", {}),
]


@pytest.mark.parametrize("method,path,body", EDIT_ONLY)
def test_editing_is_refused_in_show_mode(client, method, path, body):
    mode(client, "show")
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 409 and "edit mode" in r.json()["detail"]


def test_a_fixture_cannot_be_moved_or_deleted_in_show_mode(client):
    add_fixture(client)
    mode(client, "show")
    assert client.put("/api/room/fixtures/f1", json={"name": "f1", "profile_id": PAR, "start_address": 5}
                      ).status_code == 409
    assert client.delete("/api/room/fixtures/f1").status_code == 409
    assert client.delete("/api/groups/all").status_code in (400, 409)
    assert [f["id"] for f in client.get("/api/room").json()["fixtures"]] == ["f1"]


def test_aiming_and_running_are_refused_in_edit_mode(client):
    add_fixture(client, "h1", HEAD)
    assert client.post("/api/control/aim", json={"target_id": "h1", "x": 1, "y": 1, "z": 0}).status_code == 409
    assert client.post("/api/control/pan-tilt", json={"target_id": "h1", "pan": 1, "tilt": 1}).status_code == 409
    a = client.post("/api/animations", json={"name": "a", "tracks": []}).json()
    assert client.post(f"/api/animations/{a['id']}/play").status_code == 409
    assert client.post("/api/audio/config", json={"sound_mode": "color"}).status_code == 409
    assert client.post("/api/audio/config", json={"sound_enabled": True}).status_code == 409
    assert client.post("/api/audio/config", json={"sound_mode": "off", "gain": 2.0}).status_code == 200


def test_show_mode_allows_aiming_and_running(client):
    add_fixture(client, "h1", HEAD)
    a = client.post("/api/animations", json={"name": "a", "tracks": []}).json()
    mode(client, "show")
    assert client.post("/api/control/aim", json={"target_id": "h1", "x": 1, "y": 1, "z": 0}).status_code == 200
    assert client.post(f"/api/animations/{a['id']}/play").status_code == 200
    assert client.post("/api/audio/config", json={"sound_mode": "color"}).status_code == 200


def test_colors_dimmer_strobe_and_blackout_work_in_both_modes(client):
    add_fixture(client)
    for m in ("edit", "show"):
        mode(client, m)
        assert client.post("/api/control/color", json={"target_id": "f1", "red": 5, "green": 6, "blue": 7}).status_code == 200
        assert client.post("/api/control/dimmer", json={"target_ids": ["f1"], "value": 100}).status_code == 200
        assert client.post("/api/control/strobe", json={"target_ids": ["f1"], "value": 0}).status_code == 200
        assert client.post("/api/control/blackout").status_code == 200


def test_switching_to_edit_stops_effects_and_running_shows(client):
    a = client.post("/api/animations", json={"name": "a", "tracks": []}).json()
    mode(client, "show")
    client.post(f"/api/animations/{a['id']}/play")
    client.post("/api/audio/config", json={"sound_mode": "both"})
    ctx = context_module.get_context()
    assert ctx.players and ctx.sound.sound_mode == "both"
    mode(client, "edit")
    assert not ctx.players and not ctx.pattern_players
    assert ctx.sound.sound_mode == "off"


# ---- saved rooms ---------------------------------------------------------------------------------------

def test_a_room_can_be_saved_listed_and_loaded_back(client, data_dir):
    add_fixture(client, "f1"), add_fixture(client, "f2", address=10)
    client.post("/api/groups", json={"name": "Front", "fixture_ids": ["f1"]})
    saved = client.post("/api/rooms", json={"name": "Club Nord"}).json()
    assert saved["id"] == "club-nord"
    listing = client.get("/api/rooms").json()
    assert listing["current"] == "Club Nord"
    row = next(r for r in listing["rooms"] if r["id"] == "club-nord")
    assert row["name"] == "Club Nord" and row["fixtures"] == 2 and row["groups"] == 2 and row["saved_at"]
    assert (data_dir / "saved_rooms" / "club-nord.json").exists()

    client.delete("/api/room/fixtures/f2")
    client.delete("/api/room/fixtures/f1")
    assert client.get("/api/room").json()["fixtures"] == []
    assert client.post("/api/rooms/club-nord/load").status_code == 200
    room = client.get("/api/room").json()
    assert sorted(f["id"] for f in room["fixtures"]) == ["f1", "f2"] and room["name"] == "Club Nord"
    assert any(g["name"] == "Front" for g in client.get("/api/groups").json())


def test_saving_under_the_same_name_replaces_the_snapshot(client):
    add_fixture(client, "f1")
    client.post("/api/rooms", json={"name": "Hall"})
    add_fixture(client, "f2", address=10)
    client.post("/api/rooms", json={"name": "Hall"})
    rooms = [r for r in client.get("/api/rooms").json()["rooms"] if not r["backup"]]
    assert len(rooms) == 1 and rooms[0]["fixtures"] == 2


def test_a_new_room_is_empty_and_the_old_one_is_kept_as_previous(client):
    add_fixture(client, "f1")
    client.post("/api/audio/functions", json={"type": "beat_color", "targets": ["f1"]})
    assert client.post("/api/rooms/new", json={"name": "Next venue"}).status_code == 200
    assert client.get("/api/room").json()["fixtures"] == [] and client.get("/api/room").json()["name"] == "Next venue"
    groups = client.get("/api/groups").json()
    assert [g["id"] for g in groups] == ["all"] and groups[0]["fixture_ids"] == []
    assert client.get("/api/audio/status").json()["functions"] == []
    previous = [r for r in client.get("/api/rooms").json()["rooms"] if r["backup"]]
    assert previous and previous[0]["fixtures"] == 1
    assert client.post(f"/api/rooms/{previous[0]['id']}/load").status_code == 200      # and the switch can be undone
    assert [f["id"] for f in client.get("/api/room").json()["fixtures"]] == ["f1"]


def test_loading_a_room_keeps_the_room_it_replaces(client):
    add_fixture(client, "f1")
    client.post("/api/rooms", json={"name": "A"})
    client.delete("/api/room/fixtures/f1")
    add_fixture(client, "f9", address=50)
    client.post("/api/rooms/a/load")
    previous = next(r for r in client.get("/api/rooms").json()["rooms"] if r["backup"])
    client.post(f"/api/rooms/{previous['id']}/load")
    assert [f["id"] for f in client.get("/api/room").json()["fixtures"]] == ["f9"]


def test_saved_rooms_can_be_deleted_and_unknown_ones_404(client):
    client.post("/api/rooms", json={"name": "Gone"})
    assert client.delete("/api/rooms/gone").status_code == 200
    assert client.delete("/api/rooms/gone").status_code == 404
    assert client.post("/api/rooms/nope/load").status_code == 404
    assert client.post("/api/rooms", json={"name": "   "}).status_code == 400


def test_the_all_group_is_right_after_loading_a_room_saved_without_it(client, data_dir):
    add_fixture(client, "f1")
    client.post("/api/rooms", json={"name": "Old"})
    path = data_dir / "saved_rooms" / "old.json"
    bundle = json.loads(path.read_text())
    bundle["groups"] = []
    path.write_text(json.dumps(bundle))
    client.post("/api/rooms/old/load")
    assert next(g for g in client.get("/api/groups").json() if g["id"] == "all")["fixture_ids"] == ["f1"]


# ---- keeping saved fixture profiles current ------------------------------------------------------------

def stale_kls():
    """What the user had on disk: the bar as it was before zone strobe, saved with default
    pan/tilt ranges by the fixture details Save."""
    d = json.loads((BUNDLED_DIR / "eurolite_kls_120_fx_21ch.json").read_text())
    d["channels"]["strobe"] = 2
    d["role_mirrors"] = {"strobe": [11]}
    for z in d["zones"]:
        z["channels"].pop("strobe")
    d["custom_channels"].insert(0, {"channel": 2, "label": "Spot Strobe", "default": 0, "min_value": 0,
                                    "max_value": 255, "ranges": []})
    d["pan_range_deg"], d["tilt_range_deg"] = 540.0, 270.0
    d.pop("customized", None)
    return d


def write_profile(data_dir, data):
    (data_dir / "fixtures").mkdir(parents=True, exist_ok=True)
    (data_dir / "fixtures" / f"{data['id']}.json").write_text(json.dumps(data))


def test_a_stale_copy_of_a_bundled_profile_is_fixed_at_startup(monkeypatch, data_dir):
    write_profile(data_dir, stale_kls())
    with make_client(monkeypatch, data_dir) as c:
        kls = next(p for p in c.get("/api/fixtures/profiles").json() if p["id"] == KLS)
        assert all("strobe" in z["channels"] for z in kls["zones"])          # the current layout
        assert "strobe" not in kls["channels"]
        assert kls["pan_range_deg"] is None                                   # a bar has no pan/tilt
    context_module.reset_context()
    assert not (data_dir / "fixtures" / f"{KLS}.json").exists()               # identical to the bundled one: dropped
    backups = list((data_dir / "fixtures" / "_replaced").glob(f"{KLS}.*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["role_mirrors"] == {"strobe": [11]}


def test_tuned_pan_tilt_ranges_survive_a_refresh(monkeypatch, data_dir):
    head = json.loads((BUNDLED_DIR / "moving_head_generic_16ch.json").read_text())
    head["tilt_range_deg"] = 200.0                       # tuned on the real fixture
    head["name"] = head["name"] + " (old name)"          # and otherwise a stale snapshot
    write_profile(data_dir, head)
    with make_client(monkeypatch, data_dir) as c:
        got = next(p for p in c.get("/api/fixtures/profiles").json() if p["id"] == head["id"])
        assert got["tilt_range_deg"] == 200.0 and not got["name"].endswith("(old name)")
    context_module.reset_context()
    assert (data_dir / "fixtures" / f"{head['id']}.json").exists()           # kept: it differs by the tuned range


def test_a_profile_customized_in_the_creator_is_left_alone(monkeypatch, data_dir):
    mine = stale_kls()
    mine["customized"] = True
    write_profile(data_dir, mine)
    with make_client(monkeypatch, data_dir) as c:
        kls = next(p for p in c.get("/api/fixtures/profiles").json() if p["id"] == KLS)
        assert kls["channels"].get("strobe") == 2                            # still the user's version
    context_module.reset_context()
    assert (data_dir / "fixtures" / f"{KLS}.json").exists()


def test_the_users_own_profiles_are_never_touched(client, data_dir):
    client.post("/api/fixtures/profiles", json={"id": "mine", "name": "Mine", "channel_count": 3,
                                                "channels": {"dimmer": 1}, "fixture_type": "par"})
    before = (data_dir / "fixtures" / "mine.json").read_text()
    context_module.get_context().library.refresh_overrides()
    assert (data_dir / "fixtures" / "mine.json").read_text() == before


def test_the_creator_marks_a_bundled_profile_it_edited_as_customized(client, data_dir):
    par = next(p for p in client.get("/api/fixtures/profiles").json() if p["id"] == PAR)
    par["name"] = "My PAR"
    par["customized"] = True
    saved = client.post("/api/fixtures/profiles", json=par).json()
    assert saved["customized"] is True
    context_module.get_context().library.refresh_overrides()
    assert json.loads((data_dir / "fixtures" / f"{PAR}.json").read_text())["name"] == "My PAR"


def test_loading_a_room_refreshes_a_stale_profile_and_says_so(client, data_dir):
    add_fixture(client, "bar", KLS, 40)
    client.post("/api/rooms", json={"name": "Bar room"})
    write_profile(data_dir, stale_kls())                 # the stale copy reappears (an old file restored, say)
    context_module.get_context().library.reload()
    r = client.post("/api/rooms/bar-room/load").json()
    assert [n["id"] for n in r["profile_updates"]] == [KLS]
    kls = next(p for p in client.get("/api/fixtures/profiles").json() if p["id"] == KLS)
    assert all("strobe" in z["channels"] for z in kls["zones"])


def test_saving_a_room_refreshes_a_stale_profile_first(client, data_dir):
    add_fixture(client, "bar", KLS, 40)
    write_profile(data_dir, stale_kls())
    context_module.get_context().library.reload()
    r = client.post("/api/rooms", json={"name": "Saved with fix"}).json()
    assert [n["id"] for n in r["profile_updates"]] == [KLS]
    bundle = json.loads((data_dir / "saved_rooms" / "saved-with-fix.json").read_text())
    kls = next(p for p in bundle["fixture_profiles"] if p["id"] == KLS)
    assert all("strobe" in z["channels"] for z in kls["zones"])              # the snapshot carries the fixed profile


def test_importing_a_config_does_not_reinstate_a_stale_profile(client):
    add_fixture(client, "bar", KLS, 40)
    bundle = client.get("/api/config/export").json()
    for i, p in enumerate(bundle["fixture_profiles"]):
        if p["id"] == KLS:
            bundle["fixture_profiles"][i] = stale_kls()
    assert client.post("/api/config/import", json=bundle).status_code == 200
    kls = next(p for p in client.get("/api/fixtures/profiles").json() if p["id"] == KLS)
    assert all("strobe" in z["channels"] for z in kls["zones"])
