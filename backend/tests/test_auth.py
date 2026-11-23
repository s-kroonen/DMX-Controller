"""The LAN access PIN: off by default, never asked of this machine, and a wrong PIN locks out."""

import pytest
from fastapi.testclient import TestClient

from app import auth, context as context_module
from app.main import app
from app.storage import Storage


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "data"


def make(monkeypatch, data_dir, host):
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.setattr(context_module, "Storage", lambda data_dir_=None: Storage(data_dir=data_dir))
    return TestClient(app, client=(host, 50000))


@pytest.fixture()
def lan(monkeypatch, data_dir):          # another machine on the network
    auth._failures.clear()
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        yield c
    context_module.reset_context()


def local_client(monkeypatch, data_dir):
    return make(monkeypatch, data_dir, "127.0.0.1")


def set_pin(monkeypatch, data_dir, pin="1234"):
    with local_client(monkeypatch, data_dir) as c:
        assert c.put("/api/auth/pin", json={"pin": pin}).status_code == 200


def test_no_pin_set_means_no_check(lan):
    assert lan.get("/api/room").status_code == 200
    assert lan.get("/api/auth/status").json() == {"pin_set": False, "authorized": True, "local": False}


def test_a_lan_client_is_refused_until_it_logs_in(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        assert c.get("/api/room").status_code == 401
        assert c.get("/api/auth/status").json()["authorized"] is False
        assert c.post("/api/auth/login", json={"pin": "9999"}).status_code == 401
        assert c.get("/api/room").status_code == 401
        assert c.post("/api/auth/login", json={"pin": "1234"}).status_code == 200
        assert c.get("/api/room").status_code == 200       # the cookie is kept
        c.post("/api/auth/logout")
        assert c.get("/api/room").status_code == 401


def test_this_machine_is_never_asked(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with local_client(monkeypatch, data_dir) as c:
        assert c.get("/api/room").status_code == 200
        assert c.get("/api/auth/status").json() == {"pin_set": True, "authorized": True, "local": True}


def test_the_websocket_needs_the_pin_too(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws"):
                pass
        c.post("/api/auth/login", json={"pin": "1234"})
        with c.websocket_connect("/ws") as ws:
            assert "room" in ws.receive_json()


def test_static_files_stay_open_so_the_prompt_can_load(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        assert c.get("/").status_code == 200


def test_five_wrong_pins_lock_the_client_out_for_a_while(monkeypatch, data_dir):
    auth._failures.clear()
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        for _ in range(auth.MAX_FAILURES):
            assert c.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
        assert c.post("/api/auth/login", json={"pin": "1234"}).status_code == 429   # even the right one
    auth._failures.clear()


def test_the_pin_survives_a_restart_and_a_mode_change(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with local_client(monkeypatch, data_dir) as c:
        c.put("/api/mode", json={"mode": "show"})          # saves settings too: must not drop the PIN
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        assert c.get("/api/room").status_code == 401


def test_only_this_machine_or_a_logged_in_device_may_change_the_pin(monkeypatch, data_dir):
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        assert c.put("/api/auth/pin", json={"pin": "1234"}).status_code == 401   # nobody can claim an open server
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        assert c.put("/api/auth/pin", json={"pin": ""}).status_code == 401
        c.post("/api/auth/login", json={"pin": "1234"})
        assert c.put("/api/auth/pin", json={"pin": "abc"}).status_code == 400
        assert c.put("/api/auth/pin", json={"pin": ""}).json() == {"pin_set": False}
        assert c.get("/api/room").status_code == 200


def test_changing_the_pin_logs_other_devices_out(monkeypatch, data_dir):
    set_pin(monkeypatch, data_dir)
    with make(monkeypatch, data_dir, "192.168.1.50") as c:
        c.post("/api/auth/login", json={"pin": "1234"})
        assert c.get("/api/room").status_code == 200
        with local_client(monkeypatch, data_dir) as owner:
            owner.put("/api/auth/pin", json={"pin": "5678"})
    with make(monkeypatch, data_dir, "192.168.1.50") as c2:
        assert c2.get("/api/room").status_code == 401
