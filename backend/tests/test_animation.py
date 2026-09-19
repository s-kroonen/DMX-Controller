from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.room.model import AnimationPoint, FixtureInstance, Room, Vec3
from app.show.animation import (
    Animation,
    AnimationTrack,
    Keyframe,
    PatternAnimation,
    PatternPlayer,
    pattern_pan_tilt,
    sample_track,
)
from app.show.engine import ShowEngine


def make_engine():
    room = Room()
    room.add_fixture(FixtureInstance(
        id="mh1", name="Moving Head 1", profile_id="generic-moving-head-16ch",
        position=Vec3(0, 0, 3),
    ))
    return ShowEngine(room, FixtureLibrary(), SimulatedDmxOutput())


def test_sample_track_interpolates_target_point():
    track = AnimationTrack(target_id="mh1", keyframes=[
        Keyframe(time_s=0.0, target_point=Vec3(0, 0, 0)),
        Keyframe(time_s=2.0, target_point=Vec3(2, 0, 0)),
    ])
    mid = sample_track(track, 1.0)
    assert mid["target_point"].x == 1.0


def test_sample_track_interpolates_color():
    track = AnimationTrack(target_id="mh1", keyframes=[
        Keyframe(time_s=0.0, color=(0, 0, 0)),
        Keyframe(time_s=1.0, color=(255, 0, 0)),
    ])
    quarter = sample_track(track, 0.25)
    assert quarter["color"][0] == 64  # round(255 * 0.25)


def test_sample_track_clamps_before_and_after():
    track = AnimationTrack(target_id="mh1", keyframes=[
        Keyframe(time_s=1.0, dimmer=100),
        Keyframe(time_s=2.0, dimmer=200),
    ])
    assert sample_track(track, 0.0)["dimmer"] == 100
    assert sample_track(track, 5.0)["dimmer"] == 200


def test_animation_duration_is_max_keyframe_time():
    anim = Animation(id="a1", name="Test", tracks=[
        AnimationTrack(target_id="mh1", keyframes=[
            Keyframe(time_s=0.0), Keyframe(time_s=4.5),
        ])
    ])
    assert anim.duration_s() == 4.5


def test_sample_track_resolves_point_id_against_library():
    points = {"p1": AnimationPoint(id="p1", name="Left", position=Vec3(0, 0, 0)),
              "p2": AnimationPoint(id="p2", name="Right", position=Vec3(2, 0, 0))}
    track = AnimationTrack(target_id="mh1", keyframes=[
        Keyframe(time_s=0.0, point_id="p1"),
        Keyframe(time_s=2.0, point_id="p2"),
    ])
    mid = sample_track(track, 1.0, points)
    assert mid["target_point"].x == 1.0


def test_sample_track_point_id_moves_with_the_point():
    points = {"p1": AnimationPoint(id="p1", name="Left", position=Vec3(5, 5, 0))}
    track = AnimationTrack(target_id="mh1", keyframes=[Keyframe(time_s=0.0, point_id="p1")])
    assert sample_track(track, 0.0, points)["target_point"].x == 5


def test_track_time_offset_stages_playback():
    engine = make_engine()
    anim = Animation(id="a1", name="Staged", loop=True, tracks=[
        AnimationTrack(target_id="mh1", time_offset_s=1.0, keyframes=[
            Keyframe(time_s=0.0, dimmer=0),
            Keyframe(time_s=2.0, dimmer=200),
        ]),
    ])
    from app.show.animation import AnimationPlayer
    player = AnimationPlayer(engine, anim)
    # at animation-time 0.5, this track (offset by -1.0, wrapped by the
    # 2.0s duration) should sample at 1.5, not 0.5 -- proving the offset
    # actually shifts this track's own clock rather than being ignored.
    player._apply_frame(0.5)
    profile = engine.profile_for("mh1")
    dimmer_ch = engine.room.fixtures["mh1"].channel_for(profile.channel_for("dimmer"))
    assert engine.dmx.get_channel(dimmer_ch) == 150


def test_pattern_circle_stays_within_bounds():
    for t in (0.0, 0.5, 1.0, 1.7, 3.3):
        pan, tilt = pattern_pan_tilt("circle", t, speed_hz=0.5,
                                      pan_center=128, tilt_center=128, pan_size=80, tilt_size=80)
        assert 0 <= pan <= 255 and 0 <= tilt <= 255


def test_pattern_circle_is_periodic():
    a = pattern_pan_tilt("circle", 0.0, 1.0, 128, 128, 80, 80)
    b = pattern_pan_tilt("circle", 1.0, 1.0, 128, 128, 80, 80)  # one full cycle later
    assert a == b


def test_pattern_player_start_stop_writes_raw_pan_tilt():
    engine = make_engine()
    pattern = PatternAnimation(id="p1", name="Circle", target_id="mh1",
                                shape="circle", speed_hz=0.0,  # frozen -> deterministic value
                                pan_center=200, tilt_center=50, pan_size=0, tilt_size=0)
    player = PatternPlayer(engine, pattern, tick_hz=50)
    player.start()
    import time
    time.sleep(0.1)
    player.stop()
    profile = engine.profile_for("mh1")
    pan_ch = engine.room.fixtures["mh1"].channel_for(profile.channel_for("pan"))
    tilt_ch = engine.room.fixtures["mh1"].channel_for(profile.channel_for("tilt"))
    assert engine.dmx.get_channel(pan_ch) == 200
    assert engine.dmx.get_channel(tilt_ch) == 50


def test_animation_player_applies_frame_to_engine():
    engine = make_engine()
    anim = Animation(id="a1", name="Sweep", tracks=[
        AnimationTrack(target_id="mh1", keyframes=[
            Keyframe(time_s=0.0, target_point=Vec3(0, 0, 0), dimmer=50),
            Keyframe(time_s=1.0, target_point=Vec3(1, 0, 0), dimmer=250),
        ])
    ], loop=False)
    from app.show.animation import AnimationPlayer
    player = AnimationPlayer(engine, anim, tick_hz=50)
    player._apply_frame(0.5)
    profile = engine.profile_for("mh1")
    dimmer_ch = engine.room.fixtures["mh1"].channel_for(profile.channel_for("dimmer"))
    assert engine.dmx.get_channel(dimmer_ch) == 150
