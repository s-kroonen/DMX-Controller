"""The built-in "All lights" group always holds every fixture: patching, deleting, editing the
group, saved data that predates a fixture, and config imports can never leave a light out."""

import json

import pytest
from fastapi.testclient import TestClient

from app import context as context_module
from app.main import app
from app.storage import Storage


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


def add(client, fixture_id, address=1):
    return client.post("/api/room/fixtures", json={
        "id": fixture_id, "name": fixture_id, "profile_id": "generic-par-rgb-4ch", "start_address": address})


def all_group(client):
    return next(g for g in client.get("/api/groups").json() if g["id"] == "all")


def test_the_group_exists_on_a_fresh_install(client):
    g = all_group(client)
    assert g["name"] == "All lights" and g["fixture_ids"] == []


def test_a_patched_fixture_joins_all_lights_automatically(client):
    add(client, "a"), add(client, "b", 10)
    assert all_group(client)["fixture_ids"] == ["a", "b"]


def test_a_deleted_fixture_leaves_every_group(client):
    add(client, "a"), add(client, "b", 10)
    client.delete("/api/room/fixtures/a")
    assert all_group(client)["fixture_ids"] == ["b"]


def test_all_lights_cannot_be_deleted(client):
    add(client, "a")
    assert client.delete("/api/groups/all").status_code == 400
    assert all_group(client)["fixture_ids"] == ["a"]


def test_all_lights_cannot_be_emptied_or_narrowed(client):
    add(client, "a"), add(client, "b", 10)
    r = client.put("/api/groups/all", json={"name": "Everything", "fixture_ids": ["a"], "color": "#00ff00"})
    assert r.status_code == 200
    assert r.json()["fixture_ids"] == ["a", "b"]                # membership is forced...
    assert r.json()["name"] == "Everything" and r.json()["color"] == "#00ff00"   # ...name and color are free
    client.put("/api/groups/all", json={"name": "Everything", "fixture_ids": [], "color": "#00ff00"})
    assert all_group(client)["fixture_ids"] == ["a", "b"]


def test_other_groups_stay_editable(client):
    add(client, "a"), add(client, "b", 10)
    g = client.post("/api/groups", json={"name": "Front", "fixture_ids": ["a"]}).json()
    assert client.put(f"/api/groups/{g['id']}", json={"name": "Front", "fixture_ids": ["b"], "color": "#111111"}
                      ).json()["fixture_ids"] == ["b"]
    assert client.delete(f"/api/groups/{g['id']}").status_code == 200


def test_adding_a_fixture_is_saved_to_groups_json(client, data_dir):
    add(client, "a")
    saved = json.loads((data_dir / "groups.json").read_text())
    assert [g for g in saved if g["id"] == "all"][0]["fixture_ids"] == ["a"]


def test_startup_repairs_a_group_that_is_missing_lights(monkeypatch, data_dir):
    """What happened on the real rig: the light was patched, the group file predated it."""
    with make_client(monkeypatch, data_dir) as c:
        add(c, "a"), add(c, "b", 10)
    context_module.reset_context()
    groups_path = data_dir / "groups.json"
    groups_path.write_text(json.dumps([{"id": "all", "name": "All lights", "fixture_ids": ["a"], "color": "#ff0000"}]))
    with make_client(monkeypatch, data_dir) as c:
        assert all_group(c)["fixture_ids"] == ["a", "b"]
        assert json.loads(groups_path.read_text())[0]["fixture_ids"] == ["a", "b"]   # and fixed on disk
    context_module.reset_context()


def test_startup_recreates_a_deleted_all_group(monkeypatch, data_dir):
    with make_client(monkeypatch, data_dir) as c:
        add(c, "a")
    context_module.reset_context()
    (data_dir / "groups.json").write_text("[]")
    with make_client(monkeypatch, data_dir) as c:
        assert all_group(c)["fixture_ids"] == ["a"]
    context_module.reset_context()


def test_importing_a_config_puts_every_fixture_in_all_lights(client):
    add(client, "a"), add(client, "b", 10)
    bundle = client.get("/api/config/export").json()
    bundle["groups"] = []                                         # a config with no groups at all
    bundle["room"]["fixtures"].append({**bundle["room"]["fixtures"][0], "id": "c", "start_address": 20})
    assert client.post("/api/config/import", json=bundle).status_code == 200
    assert all_group(client)["fixture_ids"] == ["a", "b", "c"]


def test_importing_a_config_whose_all_group_is_stale_fixes_it(client):
    add(client, "a"), add(client, "b", 10)
    bundle = client.get("/api/config/export").json()
    for g in bundle["groups"]:
        if g["id"] == "all":
            g["fixture_ids"] = ["a"]
    client.post("/api/config/import", json=bundle)
    assert all_group(client)["fixture_ids"] == ["a", "b"]


def test_all_lights_targets_the_new_fixture(client):
    add(client, "a"), add(client, "b", 10)
    client.post("/api/control/color", json={"target_id": "all", "red": 9, "green": 8, "blue": 7})
    values = client.get("/api/snapshot").json()["fixture_state"]
    assert values["a"]["values"]["red"] == 9 and values["b"]["values"]["red"] == 9
