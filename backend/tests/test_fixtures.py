from app.fixtures.library import FixtureLibrary


def test_bundled_profiles_load():
    lib = FixtureLibrary()
    profiles = lib.list()
    ids = {p.id for p in profiles}
    assert "generic-moving-head-16ch" in ids
    assert "generic-par-rgb-4ch" in ids
    assert "beamz-mhl108-mkii-11ch" in ids
    assert "generic-laser-8ch" in ids


def test_moving_head_has_pan_tilt_and_color():
    lib = FixtureLibrary()
    profile = lib.get("generic-moving-head-16ch")
    assert profile is not None
    assert profile.has_pan_tilt()
    assert profile.has_color()
    assert profile.has_strobe()


def test_par_has_no_pan_tilt():
    lib = FixtureLibrary()
    profile = lib.get("generic-par-rgb-4ch")
    assert profile is not None
    assert not profile.has_pan_tilt()
    assert profile.has_color()
