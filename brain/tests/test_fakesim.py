import numpy as np
import pytest

from brain.fakesim import LIDAR_HEIGHT, LIDAR_RANGE, FakeQuarry


@pytest.fixture
def quarry():
    q = FakeQuarry(1)
    q.rocks = np.array([[0.0, 20.0, 2.0, 3.0]])
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
