import numpy as np
import pytest

from brain.policy import act, heading_error, sector_ranges
from brain.world import Truck

CLEAR = np.full((36, 64), 100.0, np.float32)


def test_idle_without_goal():
    assert act(Truck("t"), None, CLEAR).status == "idle"


def test_arrived_near_goal():
    t = Truck("t", x=10, z=10, goal=(11, 11))
    a = act(t, None, CLEAR)
    assert a.status == "arrived" and a.throttle == 0


@pytest.mark.parametrize(
    "goal,expected",
    [((0, 10), 0), ((10, 0), 90), ((-10, 0), -90), ((0, -10), -180)],
)
def test_heading_error_unity_convention(goal, expected):
    assert heading_error(Truck("t"), *goal) == pytest.approx(expected)


def test_drives_straight_at_clear_goal():
    a = act(Truck("t", goal=(0, 50)), None, CLEAR)
    assert a.status == "driving" and a.steer == pytest.approx(0) and a.throttle == pytest.approx(1)


def test_turns_toward_goal():
    assert act(Truck("t", goal=(50, 0)), None, CLEAR).steer > 0
    assert act(Truck("t", goal=(-50, 0)), None, CLEAR).steer < 0


def test_avoids_obstacle_ahead_toward_open_side():
    depth = CLEAR.copy()
    depth[:, 24:40] = 3.0
    depth[:, :20] = 10.0
    a = act(Truck("t", goal=(0, 50)), None, depth)
    assert a.status == "avoiding" and a.steer > 0 and a.throttle < 0.5


def test_sector_ranges_ignore_invalid_depth():
    depth = CLEAR.copy()
    depth[:, :] = np.nan
    depth[18, 40] = 7.0
    left, centre, right = sector_ranges(depth)
    assert left == np.inf and centre == pytest.approx(7.0) and right == np.inf

