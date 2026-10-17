import math

import numpy as np
import pytest

from isaac.sim import sensors as S
from isaac.sim.pedestrians import Walker, make_walkers


def test_walkers_start_fractions_and_ids():
    paths = [[(0.0, 0.0), (30.0, 0.0)], [(0.0, 10.0), (0.0, 40.0)]]
    walkers = make_walkers(paths)
    assert [w.id for w in walkers] == [f"Pedestrian_{i:02d}" for i in range(6)]
    assert [round(w.x, 6) for w in walkers[:3]] == [0.0, 10.0, 20.0]
    assert all(1.2 <= w.speed <= 1.6 for w in walkers)


def test_ping_pong():
    w = Walker("p", [(0.0, 0.0), (10.0, 0.0)], 0.0, 1.0)
    xs = []
    for _ in range(int(22 / 0.05)):
        w.step(0.05)
        xs.append(w.x)
    assert max(xs) > 9.7 and min(xs[len(xs) // 2 :]) < 1.0
    assert 0 < w.x < 5


def test_walker_ground_follow():
    w = Walker("p", [(0.0, 0.0), (10.0, 0.0)], 0.0, 1.0)
    w.step(0.1, lambda x, z, ref: 5.0 + x)
    assert w.y == pytest.approx(5.0 + w.x)


def test_lidar_pattern():
    d = S.lidar_directions()
    assert d.shape == (7200, 3)
    np.testing.assert_allclose(np.linalg.norm(d, axis=1), 1)
    el = np.degrees(np.arcsin(d[:, 1]))
    az = np.degrees(np.arctan2(d[:, 0], d[:, 2]))
    assert el[0] == pytest.approx(-19.5) and el[-1] == pytest.approx(9.5)
    assert az[0] == pytest.approx(-59.75) and az[239] == pytest.approx(59.75)
    assert az[240] == pytest.approx(-59.75) and el[240] == pytest.approx(-18.5)


def test_depth_rays_row0_top_and_perpendicular():
    d = S.depth_directions()
    assert d.shape == (64 * 36, 3)
    grid = d.reshape(36, 64, 3)
    assert grid[0, 32, 1] > 0 and grid[-1, 32, 1] < 0
    assert grid[18, 0, 0] < 0 and grid[18, -1, 0] > 0
    half = math.degrees(math.atan2(grid[18, -1, 0], grid[18, -1, 2]))
    assert half == pytest.approx(45 - 45 / 64 * 0.98, abs=0.8)
    dist = np.full(len(d), 10.0)
    dist[0] = np.inf
    img = S.depth_image(dist, d)
    assert img.shape == (36, 64) and img[0, 0] == S.DEPTH_RANGE
    np.testing.assert_allclose(img.reshape(-1)[1:], 10.0 * d[1:, 2], rtol=1e-6)


def test_vfov_and_mounts():
    assert S.vfov() == pytest.approx(58.7155, abs=1e-3)
    cam, lidar = S.mounts((5.0, 4.0, 9.0))
    np.testing.assert_allclose(cam, [0, 3.2, 4.7])
    np.testing.assert_allclose(lidar, [0, 2.4, 4.7])


def test_lidar_points_sensor_frame():
    d = S.lidar_directions()[:3]
    pts = S.lidar_points(np.array([10.0, np.inf, 20.0]), d)
    assert pts.shape == (2, 3)
    np.testing.assert_allclose(np.linalg.norm(pts, axis=1), [10, 20], rtol=1e-6)


def unity_euler_matrix(yaw, pitch, roll):
    y, p, r = np.radians([yaw, pitch, roll])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    return ry @ rx @ rz


@pytest.mark.parametrize("yaw,pitch,roll", [(0, 0, 0), (58.2, 5.0, -3.0), (250.0, -12.0, 7.5), (359.0, 30.0, 0.0)])
def test_cam_field_unity_euler(yaw, pitch, roll):
    r = unity_euler_matrix(yaw, pitch, roll)
    cam = S.cam_field(np.array([1.0, 2.0, 3.0]), r)
    assert cam["yaw"] == pytest.approx(yaw) and cam["pitch"] == pytest.approx(pitch) and cam["roll"] == pytest.approx(roll)
    assert (cam["x"], cam["y"], cam["z"], cam["hfov"]) == (1.0, 2.0, 3.0, 90.0)
    if pitch > 0:
        assert (r @ [0, 0, 1])[1] < 0
