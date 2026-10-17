import json
import math

import numpy as np
import pytest

from isaac.sim import protocol
from isaac.sim import world as W

QUARRY = W.REPO / "data/worlds/quarry"


@pytest.fixture(scope="module")
def world():
    return W.load_world(QUARRY)


@pytest.fixture(scope="module")
def height(world):
    return W.heightmap(world)


def test_roads_sorted_ordinal(world):
    assert [r.id for r in world.roads] == sorted(r.id for r in world.roads)
    assert [r.id for r in world.roads][:4] == ["haul_2", "haul_4", "haul_5", "haul_6"]


def test_crossing_geometry(world):
    for road, (p0, p1) in zip(world.roads, W.crossing_paths(world)):
        a, b = np.array(p0), np.array(p1)
        assert math.dist(p0, p1) == pytest.approx(road.width_m + W.CROSSING_MARGIN)
        mid = (a + b) / 2
        pts = road.points[:, [0, 2]]
        seg = np.diff(pts, axis=0)
        lengths = np.hypot(*seg.T)
        cum = np.concatenate([[0], np.cumsum(lengths)])
        i = min(int(np.searchsorted(cum, cum[-1] / 2, side="left")) - 1, len(seg) - 1)
        i = max(i, 0)
        t = seg[i] / lengths[i]
        expected = pts[i] + t * (cum[-1] / 2 - cum[i])
        np.testing.assert_allclose(mid, expected, atol=1e-6)
        assert abs(np.dot(b - a, t)) < 1e-6
        n = np.array([t[1], -t[0]])
        assert np.dot(b - a, n) > 0


def test_crossing_simple_road():
    road = W.Road("r", "a", "b", 10.0, False, np.array([[0, 0, 0], [0, 0, 100.0]]))
    assert W.crossing_path(road) == [(-11.0, 50.0), (11.0, 50.0)]


def test_truck_box_is_komatsu_length():
    m, size = W.truck_box()
    assert size[2] == pytest.approx(W.TRUCK_LENGTH)
    assert 3.5 < size[1] < 5.0 and 4.0 < size[0] < 6.0


def test_spawn_spreads_and_aims(world, height):
    _, size = W.truck_box()
    specs = W.spawn_trucks(world, size, height)
    assert [s.id for s in specs] == ["truck-0", "truck-1", "truck-2"]
    for a, b in zip(specs, specs[1:]):
        assert math.dist((a.x, a.z), (b.x, b.z)) >= size[0] + W.SPAWN_GAP - 1e-6
    spur = next(r for r in world.roads if r.id == "spur_7")
    yaw = W.aim_heading(world, spur.points[0][0], spur.points[0][2])
    assert all(s.yaw == pytest.approx(yaw) for s in specs)
    parking = next(s for s in world.sites if s.id == "parking")
    for s in specs:
        assert math.dist((s.x, s.z), (parking.pos[0], parking.pos[2])) < 25


def test_excavator_obstacles(world, height):
    boxes = W.excavators(world, height)
    assert len(boxes) == 3
    for b, e in zip(boxes, world.equipment):
        c = b.circle()
        assert (c["x"], c["z"]) == (e["pos3"][0], e["pos3"][2])
        assert 4.9 < c["r"] < 7.5


def test_hello_payload(world):
    paths = W.crossing_paths(world)
    h = W.hello(world, ["truck-0", "truck-1", "truck-2"], paths, [{"x": 0.0, "z": 0.0, "r": 5.0}] * 3)
    assert h["cameras"] == ["overview", "truck-0", "truck-1", "truck-2"]
    assert h["bounds"] == [-362.5, -362.5, 362.5, 362.5]
    assert (len(h["roads"]), len(h["sites"]), len(h["junctions"]), len(h["paths"])) == (8, 7, 2, 4)
    assert set(h["roads"][0]) == {"id", "from", "to", "width_m", "one_way", "points"}
    assert set(h["sites"][0]) == {"id", "type", "name", "x", "z"}
    assert len(protocol.encode(h)) < protocol.MAX_HEADER
    json.dumps(h)


def test_walk_speeds_in_range():
    s = W.walk_speeds(12)
    assert len(s) == 12 and all(W.MIN_WALK_SPEED <= v <= W.MAX_WALK_SPEED for v in s)
    assert s == W.walk_speeds(12)


def test_heightmap_matches_road_heights(world, height):
    errs = [abs(height(p[0], p[2]) - p[1]) for r in world.roads for p in r.points]
    assert np.median(errs) < 1.0


def test_prop_obstacles(tmp_path):
    import shutil

    shutil.copy(QUARRY / "world.json", tmp_path / "world.json")
    w = W.load_world(tmp_path)
    assert W.prop_obstacles(w) == []
    (tmp_path / "props.json").write_text(json.dumps({"props": [{"pos": [1, 2, 3], "obstacle": {"x": 1, "z": 3, "r": 2.5}}, {"pos": [0, 0, 0], "obstacle": None}]}))
    assert W.prop_obstacles(w) == [{"x": 1.0, "z": 3.0, "r": 2.5}]
    (tmp_path / "props.json").write_text(json.dumps([{"obstacle": {"x": 4, "z": 5, "r": 1}}]))
    assert W.prop_obstacles(w) == [{"x": 4.0, "z": 5.0, "r": 1.0}]
