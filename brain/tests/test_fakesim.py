import numpy as np
import pytest

from brain.fakesim import LIDAR_HEIGHT, LIDAR_RANGE, FakeQuarry


@pytest.fixture
def quarry():
    q = FakeQuarry(1)
    q.rocks = np.array([[0.0, 20.0, 2.0, 3.0, 0]])
    q.pedestrians = []
    q.trucks[0].x, q.trucks[0].z, q.trucks[0].yaw = 0.0, 0.0, 0.0
    return q


def test_lidar_sees_rock_ahead_in_sensor_frame(quarry):
    pts = quarry.scan_lidar(quarry.trucks[0])
    rock = pts[(np.abs(pts[:, 0]) < 1) & (pts[:, 1] > -LIDAR_HEIGHT + 0.1)]
    assert rock[:, 2].min() == pytest.approx(18.0, abs=0.2)
    assert np.linalg.norm(pts, axis=1).max() <= LIDAR_RANGE


def test_lidar_rotates_with_truck(quarry):
    quarry.trucks[0].yaw = 90.0
    pts = quarry.scan_lidar(quarry.trucks[0])
    above_ground = pts[pts[:, 1] > -LIDAR_HEIGHT + 0.1]
    assert not np.any((np.abs(above_ground[:, 0]) < 1) & (above_ground[:, 2] < 25))


def test_pedestrian_visible_in_camera_depth_and_lidar(quarry):
    from brain.fakesim import Pedestrian

    quarry.rocks = quarry.rocks[:0]
    quarry.pedestrians = [Pedestrian("p", [(0.0, 10.0), (0.0, 12.0)])]
    rgb, depth = quarry.render_truck(quarry.trucks[0])
    person_px = (rgb[:, 320, 0] > 200) & (rgb[:, 320, 2] < 60)
    assert person_px.any()
    assert depth[person_px, 320] == pytest.approx(9.7, abs=0.1)
    pts = quarry.scan_lidar(quarry.trucks[0])
    person = pts[(np.abs(pts[:, 0]) < 0.5) & (pts[:, 1] > -LIDAR_HEIGHT + 0.1)]
    assert person[:, 2].min() == pytest.approx(9.7, abs=0.2)


def test_pedestrians_follow_paths():
    from brain.fakesim import Pedestrian

    loop = Pedestrian("a", [(0, 0), (2, 0), (2, 2)])
    pingpong = Pedestrian("b", [(0, 0), (2, 0)])
    for _ in range(100):
        loop.step(0.1)
        pingpong.step(0.1)
    assert 0 <= pingpong.x <= 2 and pingpong.z == 0
    assert loop.target in (0, 1, 2)


def test_hello_and_actors_shape():
    q = FakeQuarry(3)
    hello, actors = q.hello(), q.actors()
    assert len(hello["obstacles"]) == len(q.rocks) and len(hello["paths"]) == 4
    assert len(actors["pedestrians"]) == 12
    assert {"id", "x", "z", "yaw"} <= set(actors["pedestrians"][0])


def test_start_fraction_spreads_pedestrians():
    from brain.fakesim import Pedestrian

    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    p = Pedestrian("p", square, start=0.5)
    assert (p.x, p.z) == pytest.approx((10, 10)) and p.target == 2
    line = Pedestrian("q", [(0, 0), (10, 0)], start=1 / 3)
    assert line.x == pytest.approx(10 / 3) and line.target == 1


def quarry_world():
    from pathlib import Path

    from brain.fakesim import load_world

    return load_world(Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry")


def test_world_mode_hello():
    from brain.fakesim import EQUIPMENT_RADIUS

    q = FakeQuarry(3, world=quarry_world())
    hello = q.hello()
    assert hello["bounds"] == [-217.5, -217.5, 217.5, 217.5]
    assert len(hello["roads"]) == 8 and len(hello["sites"]) == 7 and len(hello["junctions"]) == 2
    assert {"id", "from", "to", "width_m", "one_way", "points"} <= set(hello["roads"][0])
    assert set(hello["roads"][0]["points"][0]) == {"x", "z"}
    assert {"id", "type", "name", "x", "z"} <= set(hello["sites"][0])
    assert [o["r"] for o in hello["obstacles"]] == [EQUIPMENT_RADIUS] * 3
    assert (q.trucks[0].id, q.trucks[0].x, q.trucks[0].z, q.trucks[0].yaw) == ("truck-0", -69.7, -39.84, 75.9)
    assert len(hello["paths"]) == 4 and len(q.actors()["pedestrians"]) == 12
    q.render_overview()


def test_crossing_paths_follow_contract():
    from brain.fakesim import CROSSING_EXTRA, crossing_paths
    from brain.routing import cumulative, project

    roads = quarry_world()["roads"]
    paths = crossing_paths(roads)
    first4 = sorted(roads, key=lambda r: r["id"])[:4]
    assert [r["id"] for r in first4] == ["haul_1", "haul_2", "haul_3", "haul_6"]
    for road, (a, b) in zip(first4, paths):
        pts = [(p["x"], p["z"]) for p in road["points"]]
        cum = cumulative(pts)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d, s, _, i = project(pts, cum, *mid)
        assert d == pytest.approx(0, abs=1e-6) and s == pytest.approx(cum[-1] / 2)
        assert np.hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(road["width_m"] + CROSSING_EXTRA)
        (ax, az), (bx, bz) = pts[i], pts[i + 1]
        assert (b[0] - a[0]) * (bx - ax) + (b[1] - a[1]) * (bz - az) == pytest.approx(0, abs=1e-6)


def test_crossing_paths_match_unity_on_quarry():
    from brain.fakesim import crossing_paths

    unity = [
        [(-27.41, -4.98), (-39.46, 19.18)],
        [(-131.61, 16.83), (-126.85, 43.41)],
        [(31.33, 57.19), (31.33, 84.19)],
        [(-5.30, -13.04), (11.04, -34.53)],
    ]
    paths = crossing_paths(quarry_world()["roads"])
    assert np.array(paths) == pytest.approx(np.array(unity), abs=0.05)


def test_crossing_runs_left_to_right():
    from brain.fakesim import crossing_paths

    road = {"id": "r", "width_m": 8, "points": [{"x": 0, "z": 0}, {"x": 0, "z": 10}]}
    (a, b), = crossing_paths([road])
    assert a == pytest.approx((-10, 5)) and b == pytest.approx((10, 5))
