import math
from itertools import combinations

import numpy as np
import pytest

from brain import planning
from brain.fakesim import Truck as SimTruck
from brain.planning.driver import LIDAR_HEIGHT, lidar_ranges, split_lidar
from brain.policy import act
from brain.world import Truck

NAV = planning.nav_map()
pytestmark = pytest.mark.skipif(NAV is None, reason="no default world")


def site(type_: str) -> tuple[float, float]:
    return next(NAV.sites[k] for k, t in NAV.site_types.items() if t == type_)


def scan(truck: Truck, obstacles: list[tuple[float, float]]) -> np.ndarray:
    yaw = math.radians(truck.yaw)
    s, c = math.sin(yaw), math.cos(yaw)
    sensor_y = NAV.ground_at(truck.x, truck.z) + LIDAR_HEIGHT
    ring = np.radians(np.arange(0, 360, 10))
    pts = []
    for ox, oz in obstacles:
        for h in (0.5, 1.5, 2.5):
            wx, wz = ox + 1.5 * np.sin(ring), oz + 1.5 * np.cos(ring)
            dx, dz = wx - truck.x, wz - truck.z
            wy = NAV.ground_at(wx, wz) + h
            pts.append(np.stack([dx * c - dz * s, wy - sensor_y, dx * s + dz * c], axis=1))
    return np.concatenate(pts).astype(np.float32) if pts else np.zeros((0, 3), np.float32)


def run(start, goal, obstacles=(), ticks=1500):
    route = planning.plan_route(NAV, start, goal)
    head = route[4] - route[0]
    sim = SimTruck("t", start[0], start[1], math.degrees(math.atan2(head[0], head[1])))
    truck = Truck("t", x=sim.x, z=sim.z, yaw=sim.yaw, goal=goal, path=route.tolist())
    clear, statuses = math.inf, set()
    for _ in range(ticks):
        a = act(truck, None, None, scan(truck, list(obstacles)))
        statuses.add(a.status)
        if a.status == "arrived":
            return True, clear, statuses
        truck.throttle, truck.steer = sim.throttle, sim.steer = a.throttle, a.steer
        sim.step(0.1)
        truck.x, truck.z, truck.yaw, truck.speed = sim.x, sim.z, sim.yaw, sim.speed
        assert not NAV.blocked_at(truck.x, truck.z), (truck.x, truck.z)
        clear = min([clear] + [math.hypot(ox - truck.x, oz - truck.z) for ox, oz in obstacles])
    return False, clear, statuses


def test_every_site_pair_has_a_route():
    for a, b in combinations(NAV.sites.values(), 2):
        assert planning.plan_route(NAV, a, b) is not None


def test_drives_parking_to_crusher():
    arrived, _, statuses = run(site("parking"), site("crusher"))
    assert arrived and statuses <= {"driving", "arrived"}


def test_avoids_vehicle_parked_on_route():
    start, goal = site("parking"), site("crusher")
    route = planning.plan_route(NAV, start, goal)
    blocker = tuple(route[20])
    arrived, clear, statuses = run(start, goal, [blocker])
    assert clear > 3.0
    assert "avoiding" in statuses or "holding" in statuses


def test_ground_points_are_not_obstacles():
    truck = Truck("t", *site("parking"))
    yaw = math.radians(truck.yaw)
    ground = np.array([[0.0, -LIDAR_HEIGHT, d] for d in range(5, 60, 5)], np.float32)
    bump = NAV.ground_at(truck.x, truck.z + ground[:, 2]) - NAV.ground_at(truck.x, truck.z)
    ground[:, 1] += bump
    local, obstacles = split_lidar(NAV, truck.x, truck.z, yaw, ground, LIDAR_HEIGHT)
    assert len(local) == 0 and len(obstacles) == 0


def test_lidar_bins_run_left_to_right():
    ranges = lidar_ranges(np.array([[-10.0, 10.0], [0.5, 20.0], [10.0, 10.0]]))
    hits = np.flatnonzero(ranges < 100)
    assert len(hits) == 3 and hits[0] < 30 <= hits[1] < hits[2]
    assert ranges[30] == pytest.approx(math.hypot(0.5, 20.0))
