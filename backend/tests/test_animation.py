from app.dmx.simulator import SimulatedDmxOutput
from app.fixtures.library import FixtureLibrary
from app.room.model import FixtureInstance, Room, Vec3
from app.show.animation import Animation, AnimationTrack, Keyframe, sample_track
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
