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


def _set_up_venue(client, custom_profile_id="custom-abc123"):
    """A room with one bundled-profile fixture and one custom-profile
    fixture, a group, a pattern, an animation, and a non-default sound
    config -- one of everything export/import needs to round-trip."""
    client.post("/api/fixtures/profiles", json={
        "id": custom_profile_id,
        "name": "My Custom Moving Head",
        "channel_count": 4,
        "channels": {"pan": 1, "tilt": 2, "dimmer": 3, "strobe": 4},
        "fixture_type": "moving_head",
    })
    par = client.post("/api/room/fixtures", json={
        "name": "PAR A", "profile_id": "generic-par-rgb-4ch", "start_address": 1,
    }).json()
    custom = client.post("/api/room/fixtures", json={
        "name": "Custom MH", "profile_id": custom_profile_id, "start_address": 10,
    }).json()
    client.put("/api/room", json={
        "name": "Main Stage",
        "dimensions": {"width": 12, "depth": 8, "height": 5},
        "floor_points": [],
    })
    group = client.post("/api/groups", json={
        "name": "All", "fixture_ids": [par["id"], custom["id"]],
    }).json()
    pattern = client.post("/api/patterns", json={
        "name": "Sweep", "target_id": custom["id"], "shape": "circle",
    }).json()
    animation = client.post("/api/animations", json={
        "name": "Fade", "tracks": [{"target_id": par["id"], "keyframes": [
            {"time_s": 0, "target_point": {"x": 0, "y": 0, "z": 1}},
        ]}],
    }).json()
    client.post("/api/audio/config", json={"gain": 2.5, "sensitivity": 1.7, "sound_mode": "color"})
    return {"par": par, "custom": custom, "group": group, "pattern": pattern, "animation": animation}


def test_export_includes_referenced_custom_profile(client):
    venue = _set_up_venue(client)
    bundle = client.get("/api/config/export").json()

    assert bundle["room"]["name"] == "Main Stage"
    assert len(bundle["room"]["fixtures"]) == 2
    assert len(bundle["groups"]) == 1
    assert len(bundle["patterns"]) == 1
    assert len(bundle["animations"]) == 1
    assert bundle["audio"]["gain"] == 2.5

    profile_ids = {p["id"] for p in bundle["fixture_profiles"]}
    assert "custom-abc123" in profile_ids
    assert "generic-par-rgb-4ch" in profile_ids  # bundled profiles are included too


def test_import_restores_full_config_on_a_fresh_install(client, tmp_path, monkeypatch):
    from app.storage import Storage

    venue = _set_up_venue(client)
    bundle = client.get("/api/config/export").json()

    # Simulate "a different machine": brand-new empty storage dir, so the
    # custom profile referenced by the room does NOT already exist there.
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(
        context_module, "Storage", lambda data_dir=None: Storage(data_dir=tmp_path / "fresh_data")
    )
    with TestClient(app) as fresh_client:
        assert fresh_client.get("/api/fixtures/profiles").json()
        profile_ids_before = {p["id"] for p in fresh_client.get("/api/fixtures/profiles").json()}
        assert "custom-abc123" not in profile_ids_before

        resp = fresh_client.post("/api/config/import", json=bundle)
        assert resp.status_code == 200

        room = fresh_client.get("/api/room").json()
        assert room["name"] == "Main Stage"
        assert len(room["fixtures"]) == 2

        profile_ids_after = {p["id"] for p in fresh_client.get("/api/fixtures/profiles").json()}
        assert "custom-abc123" in profile_ids_after

        groups = fresh_client.get("/api/groups").json()
        assert len(groups) == 1
        patterns = fresh_client.get("/api/patterns").json()
        assert len(patterns) == 1
        animations = fresh_client.get("/api/animations").json()
        assert len(animations) == 1
        audio_status = fresh_client.get("/api/audio/status").json()
        assert audio_status["config"]["gain"] == 2.5
        assert audio_status["config"]["sound_mode"] == "color"

        # the DMX-critical bit: setting color on the re-imported custom
        # fixture must work, proving its profile (and channel map) resolved
        custom_id = venue["custom"]["id"]
        control_resp = fresh_client.post("/api/control/color", json={
            "target_id": custom_id, "red": 5, "green": 6, "blue": 7,
        })
        # a moving-head-type custom profile has no red/green/blue channels
        # mapped, so this should just no-op rather than error
        assert control_resp.status_code == 200


def test_import_rejects_room_with_missing_profile(client):
    bundle = {
        "version": 1,
        "room": {
            "name": "Broken", "dimensions": {"width": 5, "depth": 5, "height": 3},
            "floor_points": [], "safety_zones": [], "objects": [], "animation_points": [],
            "fixtures": [
                {
                    "id": "fx-1", "name": "Ghost", "profile_id": "nonexistent-profile",
                    "universe": 1, "start_address": 1,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "orientation": {"yaw_deg": 0, "pitch_deg": 180, "roll_deg": 0},
                    "group_ids": [], "inverted_pan": False, "inverted_tilt": False,
                    "pan_offset_deg": 0, "tilt_offset_deg": 0,
                }
            ],
        },
        "groups": [], "animations": [], "patterns": [], "audio": {}, "fixture_profiles": [],
    }
    resp = client.post("/api/config/import", json=bundle)
    assert resp.status_code == 400

    # the previous (valid, empty) config must still be intact -- an
    # all-or-nothing import, never a partial one
    room = client.get("/api/room").json()
    assert room["fixtures"] == []


def test_import_stops_running_animations_and_patterns(client):
    venue = _set_up_venue(client)
    client.post(f"/api/patterns/{venue['pattern']['id']}/play")
    client.post(f"/api/animations/{venue['animation']['id']}/play")

    bundle = client.get("/api/config/export").json()
    resp = client.post("/api/config/import", json=bundle)
    assert resp.status_code == 200
    # importing rebuilds animations/patterns dicts wholesale -- the players
    # keyed by the old ids must not be left dangling
    from app.context import get_context
    ctx = get_context()
    assert ctx.players == {}
    assert ctx.pattern_players == {}
