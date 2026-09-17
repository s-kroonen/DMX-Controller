import shutil

import pytest
from fastapi.testclient import TestClient

from app import context as context_module
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.storage import Storage

    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(
        context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "data")
    )
    with TestClient(app) as c:
        yield c
    context_module.reset_context()


def test_list_fixture_profiles(client):
    resp = client.get("/api/fixtures/profiles")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert "generic-par-rgb-4ch" in ids


def test_add_fixture_and_control_color(client):
    resp = client.post("/api/room/fixtures", json={
        "name": "PAR A",
        "profile_id": "generic-par-rgb-4ch",
        "start_address": 1,
    })
    assert resp.status_code == 200
    fixture_id = resp.json()["id"]

    resp = client.post("/api/control/color", json={
        "target_id": fixture_id, "red": 10, "green": 20, "blue": 30,
    })
    assert resp.status_code == 200

    snap = client.get("/api/snapshot").json()
    assert snap["fixture_state"][fixture_id]["values"]["red"] == 10


def test_group_creation_and_listing(client):
    fx = client.post("/api/room/fixtures", json={
        "name": "PAR A", "profile_id": "generic-par-rgb-4ch", "start_address": 1,
    }).json()
    resp = client.post("/api/groups", json={"name": "All PARs", "fixture_ids": [fx["id"]]})
    assert resp.status_code == 200
    groups = client.get("/api/groups").json()
    assert len(groups) == 1
    assert groups[0]["fixture_ids"] == [fx["id"]]


def test_aim_endpoint_blocked_by_safety_zone(client):
    fx = client.post("/api/room/fixtures", json={
        "name": "MH1", "profile_id": "generic-moving-head-16ch", "start_address": 1,
        "position": {"x": 0, "y": 0, "z": 3},
    }).json()
    client.post("/api/room/safety-zones", json={
        "name": "Crowd",
        "min_corner": {"x": -5, "y": -5, "z": 0},
        "max_corner": {"x": 5, "y": 5, "z": 1.8},
    })
    resp = client.post("/api/control/aim", json={
        "target_id": fx["id"], "x": 1, "y": 1, "z": 0.5,
    })
    assert resp.json()[fx["id"]]["ok"] is False


def test_blackout(client):
    fx = client.post("/api/room/fixtures", json={
        "name": "PAR A", "profile_id": "generic-par-rgb-4ch", "start_address": 1,
    }).json()
    client.post("/api/control/color", json={"target_id": fx["id"], "red": 200, "green": 0, "blue": 0})
    client.post("/api/control/blackout")
    snap = client.get("/api/snapshot").json()
    assert snap["fixture_state"][fx["id"]]["values"] == {}


def test_update_room_shape_with_custom_floor_polygon(client):
    resp = client.put("/api/room", json={
        "name": "L-Shaped Venue",
        "dimensions": {"width": 10, "depth": 10, "height": 3.5},
        "floor_points": [
            {"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 3},
            {"x": 3, "y": 3}, {"x": 3, "y": 6}, {"x": 0, "y": 6},
        ],
    })
    assert resp.status_code == 200
    room = client.get("/api/room").json()
    assert room["name"] == "L-Shaped Venue"
    assert len(room["floor_points"]) == 6


def test_room_objects_crud(client):
    wall = client.post("/api/room/objects", json={
        "name": "North Wall", "kind": "wall",
        "position": {"x": -5, "y": 5, "z": 0},
        "end_position": {"x": 5, "y": 5, "z": 0},
        "thickness": 0.2, "height": 3.0,
    })
    assert wall.status_code == 200
    wall_id = wall.json()["id"]

    person = client.post("/api/room/objects", json={
        "name": "Reference person", "kind": "person",
        "position": {"x": 0, "y": 0, "z": 0}, "height": 1.75,
    })
    assert person.status_code == 200

    room = client.get("/api/room").json()
    assert len(room["objects"]) == 2

    resp = client.delete(f"/api/room/objects/{wall_id}")
    assert resp.status_code == 200
    room = client.get("/api/room").json()
    assert len(room["objects"]) == 1


def test_wall_object_requires_end_position(client):
    resp = client.post("/api/room/objects", json={
        "name": "Bad Wall", "kind": "wall", "position": {"x": 0, "y": 0, "z": 0},
    })
    assert resp.status_code == 400


def test_room_object_invalid_kind_rejected(client):
    resp = client.post("/api/room/objects", json={
        "name": "Mystery", "kind": "spaceship", "position": {"x": 0, "y": 0, "z": 0},
    })
    assert resp.status_code == 400
