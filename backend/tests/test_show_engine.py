from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.groups.model import Group
from app.room.model import FixtureInstance, Room, SafetyZone, Vec3
from app.show.engine import ShowEngine


def make_engine():
    room = Room()
    room.add_fixture(FixtureInstance(
        id="mh1", name="Moving Head 1", profile_id="generic-moving-head-16ch",
        start_address=1, position=Vec3(0, 0, 3),
    ))
    room.add_fixture(FixtureInstance(
        id="par1", name="PAR 1", profile_id="generic-par-rgb-4ch",
        start_address=20,
    ))
    library = FixtureLibrary()
    dmx = SimulatedDmxOutput()
    return ShowEngine(room, library, dmx)


def test_set_color_single_fixture():
    engine = make_engine()
    engine.set_color("par1", 10, 20, 30)
    assert engine.dmx.get_channel(20) == 255  # PAR dimmer channel untouched, default 0 buffer
    assert engine.dmx.get_channel(21) == 10  # red is channel 2 of a 4ch PAR starting at 20 -> 21
    assert engine.dmx.get_channel(22) == 20
    assert engine.dmx.get_channel(23) == 30


def test_group_applies_to_all_members():
    engine = make_engine()
    engine.add_group(Group(id="g1", name="Everything", fixture_ids=["mh1", "par1"]))
    engine.set_color("g1", 100, 150, 200)
    assert engine.dmx.get_channel(21) == 100  # par red
    mh_profile = engine.profile_for("mh1")
    red_ch = engine.room.fixtures["mh1"].channel_for(mh_profile.channel_for("red"))
    assert engine.dmx.get_channel(red_ch) == 100


def test_aim_at_point_sets_pan_tilt():
    engine = make_engine()
    result = engine.aim_at_point("mh1", Vec3(0, 0, 0))
    assert result["mh1"]["ok"] is True
    profile = engine.profile_for("mh1")
    pan_ch = engine.room.fixtures["mh1"].channel_for(profile.channel_for("pan"))
    assert engine.dmx.get_channel(pan_ch) > 0  # some pan value was written


def test_aim_at_point_blocked_by_safety_zone():
    engine = make_engine()
    engine.room.add_safety_zone(SafetyZone(
        id="crowd", name="Crowd", min_corner=Vec3(-5, -5, 0), max_corner=Vec3(5, 5, 1.8),
    ))
    result = engine.aim_at_point("mh1", Vec3(1, 1, 0.5))
    assert result["mh1"]["ok"] is False
    assert engine.state_for("mh1").blocked_by_safety_zone == "crowd"


def test_blackout_clears_dmx_and_state():
    engine = make_engine()
    engine.set_color("par1", 200, 200, 200)
    engine.blackout()
    assert engine.dmx.get_channel(21) == 0
    assert engine.state_for("par1").values == {}
