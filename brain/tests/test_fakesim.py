from pathlib import Path

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

    world = quarry_world()
    q = FakeQuarry(3, world=world)
    hello = q.hello()
    assert hello["bounds"] == world["bounds"] and hello["bounds"][2] > 200
    assert len(hello["roads"]) == 8 and len(hello["sites"]) == 7 and len(hello["junctions"]) == 2
    assert {"id", "from", "to", "width_m", "one_way", "points"} <= set(hello["roads"][0])
    assert set(hello["roads"][0]["points"][0]) == {"x", "z"}
    assert {"id", "type", "name", "x", "z"} <= set(hello["sites"][0])
    assert [o["r"] for o in hello["obstacles"]] == [EQUIPMENT_RADIUS] * 3
    assert (q.trucks[0].id, q.trucks[0].x, q.trucks[0].z, q.trucks[0].yaw) == world["trucks"][0]
    assert len(hello["paths"]) == 4 and len(q.actors()["pedestrians"]) == 12
    q.render_overview()


def test_crossing_paths_follow_contract():
    from brain.fakesim import CROSSING_EXTRA, crossing_paths

    roads = quarry_world()["roads"]
    first4 = sorted(roads, key=lambda r: r["id"].encode())[:4]
    expected = []
    for road in first4:
        pts = np.array([(p["x"], p["z"]) for p in road["points"]])
        cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        i = int(np.searchsorted(cum, cum[-1] / 2)) - 1
        mid = pts[i] + (pts[i + 1] - pts[i]) * (cum[-1] / 2 - cum[i]) / (cum[i + 1] - cum[i])
        t = (pts[i + 1] - pts[i]) / np.linalg.norm(pts[i + 1] - pts[i])
        n = np.array([t[1], -t[0]])
        half = (road["width_m"] + CROSSING_EXTRA) / 2
        expected.append([mid - n * half, mid + n * half])
    assert np.array(crossing_paths(roads)) == pytest.approx(np.array(expected), abs=0.01)


def test_crossing_runs_left_to_right():
    from brain.fakesim import crossing_paths

    road = {"id": "r", "width_m": 8, "points": [{"x": 0, "z": 0}, {"x": 0, "z": 10}]}
    (a, b), = crossing_paths([road])
    assert a == pytest.approx((-10, 5)) and b == pytest.approx((10, 5))


def test_world_lidar_ground_follows_navgrid():
    import math

    from brain.planning.driver import split_lidar
    from brain.planning.navmap import load

    world = quarry_world()
    nav = load(Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry")
    pts = next(r for r in world["roads"] if r["id"].startswith("ramp"))["points"]
    i = len(pts) // 2
    pose = (pts[i]["x"], pts[i]["z"], math.degrees(math.atan2(pts[i + 1]["x"] - pts[i]["x"], pts[i + 1]["z"] - pts[i]["z"])))
    found = []
    for w in ({**world, "terrain": None}, world):
        q = FakeQuarry(1, world=w)
        q.pedestrians = []
        t = q.trucks[0]
        t.x, t.z, t.yaw = pose
        found.append(len(split_lidar(nav, t.x, t.z, math.radians(t.yaw), q.scan_lidar(t), LIDAR_HEIGHT)[1]))
    assert found[0] > 0 and found[1] == 0


def test_overview_zoom_renders_subregion_at_full_resolution(quarry):
    from brain.fakesim import OVERVIEW_PX, square_box

    assert square_box([0.1, 0.2, 0.3, 0.6]) == pytest.approx([0.0, 0.2, 0.4, 0.6])
    quarry.rocks = np.array([[20.0, 20.0, 8.0, 3.0, 0]])
    rock = lambda img: np.all(img == (110, 110, 115), axis=-1)
    full = quarry.render_overview()
    quarry.view_box = [0.5, 0.3, 0.7, 0.5]
    zoomed = quarry.render_overview()
    assert zoomed.shape == full.shape == (OVERVIEW_PX, OVERVIEW_PX, 3)
    assert rock(zoomed).sum() == pytest.approx(np.pi * (8 * OVERVIEW_PX / 200 * 5) ** 2, rel=0.03)
    ys, xs = np.nonzero(rock(zoomed))
    assert xs.mean() == pytest.approx(OVERVIEW_PX / 2, abs=2) and ys.mean() == pytest.approx(OVERVIEW_PX / 2, abs=2)
    quarry.view_box = None
    np.testing.assert_array_equal(quarry.render_overview(), full)
