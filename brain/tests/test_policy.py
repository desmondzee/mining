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


def test_route_steers_toward_lookahead_point():
    route = [[0, 0], [0, 50], [50, 50]]
    t = Truck("t", goal=(50, 50), route=route)
    assert act(t, None, CLEAR).steer == pytest.approx(0)
    t = Truck("t", z=45, goal=(50, 50), route=route, route_s=40)
    a = act(t, None, CLEAR)
    assert a.status == "driving" and a.steer > 0.3 and t.route_s == pytest.approx(45)


def test_route_progress_ignores_later_passes_nearby():
    route = [[0, 0], [0, 40], [6, 40], [6, 0]]
    t = Truck("t", z=5, goal=(6, 0), route=route)
    act(t, None, CLEAR)
    assert t.route_s == pytest.approx(5)


def test_route_avoidance_still_overrides():
    depth = CLEAR.copy()
    depth[:, 24:40] = 3.0
    a = act(Truck("t", goal=(0, 50), route=[[0, 0], [0, 50]]), None, depth)
    assert a.status == "avoiding" and a.throttle < 0.5


def test_ignores_obstacles_beyond_goal():
    depth = CLEAR.copy()
    depth[:, 24:40] = 5.0
    assert act(Truck("t", goal=(0, 4.5), route=[[0, 0], [0, 4.5]]), None, depth).status == "driving"
    assert act(Truck("t", goal=(0, 4.5)), None, depth).status == "driving"
    assert act(Truck("t", goal=(0, 8)), None, depth).status == "avoiding"


def test_near_goal_avoidance_slows_without_swerving():
    depth = CLEAR.copy()
    depth[:, 24:40] = 12.0
    depth[:, 40:] = 9.0
    a = act(Truck("t", goal=(0, 15), route=[[0, 0], [0, 15]]), None, depth)
    assert a.status == "avoiding" and a.steer == pytest.approx(0) and a.throttle < 0.5


def test_final_goal_inside_turn_circle_drives_straight_first():
    t = Truck("t", goal=(5, 2), route=[[0, 0], [5, 2]], route_s=5)
    assert act(t, None, CLEAR).steer == 0
    t = Truck("t", goal=(20, 3), route=[[0, 0], [20, 3]], route_s=12)
    assert act(t, None, CLEAR).steer > 0.5


def test_follows_route_and_arrives():
    from brain.fakesim import Truck as SimTruck
    from brain.routing import cumulative, project

    route = [[0, 0], [0, 60], [40, 60], [40, 120]]
    pts = [tuple(p) for p in route]
    cum = cumulative(pts)
    sim = SimTruck("t", 0, -5, 0)
    t = Truck("t", goal=(40, 120), route=route)
    worst = 0.0
    for _ in range(1500):
        t.x, t.z, t.yaw = sim.x, sim.z, sim.yaw
        a = act(t, None, CLEAR)
        if a.status == "arrived":
            break
        sim.throttle, sim.steer = a.throttle, a.steer
        sim.step(1 / 15)
        worst = max(worst, project(pts, cum, sim.x, sim.z)[0])
    assert a.status == "arrived" and worst < 5
