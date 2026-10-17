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
