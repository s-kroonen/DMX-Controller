from app.room.model import Room, RoomDimensions, RoomObject, Vec2, Vec3


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
