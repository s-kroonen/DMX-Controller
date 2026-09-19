from app.room.model import FixtureInstance, Room, RoomDimensions, RoomObject, SafetyZone, Vec2, Vec3


def test_effective_floor_points_falls_back_to_rectangle():
    room = Room(dimensions=RoomDimensions(width=10, depth=6, height=3))
    points = room.effective_floor_points()
    assert len(points) == 4
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    assert min(xs) == -5 and max(xs) == 5
    assert min(ys) == -3 and max(ys) == 3


def test_effective_floor_points_uses_custom_polygon_when_drawn():
    room = Room()
    room.floor_points = [Vec2(0, 0), Vec2(4, 0), Vec2(4, 3), Vec2(2, 5), Vec2(0, 3)]
    points = room.effective_floor_points()
    assert len(points) == 5
    assert points[3].x == 2 and points[3].y == 5


def test_room_object_wall_round_trip():
    room = Room()
    wall = RoomObject(
        id="w1", name="North wall", kind="wall",
        position=Vec3(-5, 5, 0), end_position=Vec3(5, 5, 0),
        thickness=0.2, height=3.0,
    )
    room.add_object(wall)
    restored = Room.from_dict(room.to_dict())
    assert restored.objects["w1"].kind == "wall"
    assert restored.objects["w1"].end_position.x == 5


def test_room_object_person_round_trip():
    room = Room()
    person = RoomObject(id="p1", name="Reference person", kind="person",
                         position=Vec3(1, 1, 0), height=1.75)
    room.add_object(person)
    restored = Room.from_dict(room.to_dict())
    assert restored.objects["p1"].height == 1.75
    assert restored.objects["p1"].end_position is None


def test_room_round_trip_preserves_floor_points_and_objects():
    room = Room(name="Venue A")
    room.floor_points = [Vec2(0, 0), Vec2(3, 0), Vec2(3, 3)]
    room.add_object(RoomObject(id="b1", name="DJ booth", kind="surface",
                                position=Vec3(0, 0, 0), width=2, depth=1, height=1))
    restored = Room.from_dict(room.to_dict())
    assert len(restored.floor_points) == 3
    assert "b1" in restored.objects


def test_apply_shape_rescales_fixture_position_proportionally():
    room = Room(dimensions=RoomDimensions(width=10, depth=10, height=4))
    # fixture sits at the room's center -- should stay at the new center
    room.add_fixture(FixtureInstance(id="f1", name="MH1", profile_id="p",
                                      position=Vec3(0, 0, 2)))
    # fixture sits at a corner (5, 5) of the old 10x10 room -- at the edge
    room.add_fixture(FixtureInstance(id="f2", name="MH2", profile_id="p",
                                      position=Vec3(5, 5, 2)))

    # double the footprint and the height, same center
    room.apply_shape([], RoomDimensions(width=20, depth=20, height=8))

    assert room.fixtures["f1"].position.x == 0
    assert room.fixtures["f1"].position.y == 0
    assert room.fixtures["f1"].position.z == 4  # height doubled too

    assert room.fixtures["f2"].position.x == 10  # stayed at the (now bigger) edge
    assert room.fixtures["f2"].position.y == 10
    assert room.fixtures["f2"].position.z == 4


def test_apply_shape_rescales_objects_and_safety_zones():
    room = Room(dimensions=RoomDimensions(width=10, depth=10, height=4))
    room.add_object(RoomObject(id="w1", name="Wall", kind="wall",
                                position=Vec3(-5, -5, 0), end_position=Vec3(5, -5, 0),
                                height=3))
    room.add_safety_zone(SafetyZone(id="z1", name="Crowd",
                                     min_corner=Vec3(-5, -5, 0), max_corner=Vec3(5, 5, 1.8)))

    room.apply_shape([], RoomDimensions(width=20, depth=10, height=4))  # widen X only

    wall = room.objects["w1"]
    assert wall.position.x == -10 and wall.position.y == -5
    assert wall.end_position.x == 10 and wall.end_position.y == -5

    zone = room.safety_zones["z1"]
    assert zone.min_corner.x == -10 and zone.max_corner.x == 10
    assert zone.min_corner.y == -5 and zone.max_corner.y == 5  # depth unchanged


def test_apply_shape_no_op_when_shape_unchanged():
    room = Room(dimensions=RoomDimensions(width=10, depth=10, height=4))
    room.add_fixture(FixtureInstance(id="f1", name="MH1", profile_id="p",
                                      position=Vec3(3, -2, 1.5)))
    room.apply_shape([], RoomDimensions(width=10, depth=10, height=4))
    assert room.fixtures["f1"].position.x == 3
    assert room.fixtures["f1"].position.y == -2
    assert room.fixtures["f1"].position.z == 1.5


def test_apply_shape_recenters_when_floor_points_move_off_center():
    room = Room()
    room.floor_points = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
    room.add_fixture(FixtureInstance(id="f1", name="MH1", profile_id="p",
                                      position=Vec3(5, 5, 2)))  # at the old center (5,5)

    # shift the whole floor 10m in +X, same size
    room.apply_shape(
        [Vec2(10, 0), Vec2(20, 0), Vec2(20, 10), Vec2(10, 10)],
        room.dimensions,
    )
    assert room.fixtures["f1"].position.x == 15  # followed the new center
    assert room.fixtures["f1"].position.y == 5
