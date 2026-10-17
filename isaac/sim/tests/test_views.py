import numpy as np
import pytest

from isaac.sim.kinematics import Truck
from isaac.sim.views import View, view_pose


class FakeSim:
    time = 0.0
    trucks = [Truck("truck-0", 10.0, 20.0, 90.0, y=5.0)]

    def height(self, x, z):
        return 1.0


def test_chase_behind_and_looking_at_truck():
    v = View.from_dict({"name": "drone", "truck": "truck-0", "distance": 30, "elevation": 30, "smooth": 0, "unknown": 1})
    p, r = view_pose(v, FakeSim(), 0.1)
    assert p[0] < 10 - 20 and p[1] > 5 + 10 and p[2] == pytest.approx(20.0)
    to_truck = np.array([10, 7, 20]) - p
    np.testing.assert_allclose(r[:, 2], to_truck / np.linalg.norm(to_truck), atol=1e-9)
    assert r[1, 1] > 0 and abs(r[1, 0]) < 1e-9


def test_fixed_and_orbit():
    v = View.from_dict({"name": "a", "mode": "fixed", "position": [0, 100, 0], "look_at": [0, 0, 50]})
    _, r = view_pose(v, FakeSim(), 0.1)
    assert r[2, 2] > 0 and r[1, 2] < 0
    o = View.from_dict({"name": "b", "mode": "orbit", "center": [5, 5], "radius": 100, "position": [0, 50, 0]})
    p, _ = view_pose(o, FakeSim(), 0.1)
    np.testing.assert_allclose(p, [5, 51, 105])
