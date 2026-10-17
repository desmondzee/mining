import math
from itertools import permutations
from pathlib import Path

import numpy as np
import pytest

from brain import planning
from brain.fakesim import Truck as SimTruck
from brain.planning.driver import LIDAR_HEIGHT, lidar_ranges, split_lidar
from brain.planning.navmap import load
from brain.policy import act
from brain.world import Truck

WORLDS = Path(__file__).resolve().parents[2] / "data" / "worlds"


@pytest.fixture(params=["quarry", "default"])
def nav(request, monkeypatch):
    if not (WORLDS / request.param / "navgrid.json").is_file():
        pytest.skip(f"no {request.param} world")
    nav = load(WORLDS / request.param)
    monkeypatch.setattr(planning, "nav_map", lambda: nav)
    return nav


def site(nav, type_: str) -> tuple[float, float]:
    return next(nav.sites[k] for k, t in nav.site_types.items() if t == type_)


def scan(nav, truck: Truck, obstacles: list[tuple[float, float]], radius: float = 1.5) -> np.ndarray:
    yaw = math.radians(truck.yaw)
    s, c = math.sin(yaw), math.cos(yaw)
    sensor_y = nav.ground_at(truck.x, truck.z) + LIDAR_HEIGHT
    ring = np.radians(np.arange(0, 360, 10))
    pts = []
    for ox, oz in obstacles:
        for h in (0.5, 1.5, 2.5):
            wx, wz = ox + radius * np.sin(ring), oz + radius * np.cos(ring)
            dx, dz = wx - truck.x, wz - truck.z
            wy = nav.ground_at(wx, wz) + h
            pts.append(np.stack([dx * c - dz * s, wy - sensor_y, dx * s + dz * c], axis=1))
    return np.concatenate(pts).astype(np.float32) if pts else np.zeros((0, 3), np.float32)


TRUCK_HALF_W = 2.12


def body_over_edge(nav, truck: Truck) -> bool:
    yaw = math.radians(truck.yaw)
    rx, rz = math.cos(yaw) * TRUCK_HALF_W, -math.sin(yaw) * TRUCK_HALF_W
    xs = np.array([truck.x, truck.x + rx, truck.x - rx])
    zs = np.array([truck.z, truck.z + rz, truck.z - rz])
    return bool(nav.blocked_at(xs, zs).any())


def narrowest_route_point(nav, route: np.ndarray, skip: int = 20) -> int:
    """Index of the route point closest to a blocked cell, away from the two ends."""
    br, bc = np.nonzero(nav.blocked)
    blocked_xy = np.stack([nav.ox + (bc + 0.5) * nav.cell, nav.oz + (br + 0.5) * nav.cell], 1)
    gap = [np.abs(blocked_xy - p).max(axis=1).min() for p in route[skip:-skip]]
    return skip + int(np.argmin(gap))


def run(nav, start, goal, obstacles=(), ticks=3000):
    route = planning.plan_route(nav, start, goal)
    head = route[4] - route[0]
    sim = SimTruck("t", start[0], start[1], math.degrees(math.atan2(head[0], head[1])))
    truck = Truck("t", x=sim.x, z=sim.z, yaw=sim.yaw, goal=goal, path=route.tolist())
    clear, statuses = math.inf, set()
    for tick in range(ticks):
        a = act(truck, None, None, scan(nav, truck, list(obstacles)), now=tick * 0.1)
        statuses.add(a.status)
        if a.status == "arrived":
            return True, clear, statuses
        truck.throttle, truck.steer = sim.throttle, sim.steer = a.throttle, a.steer
        sim.step(0.1)
        truck.x, truck.z, truck.yaw, truck.speed = sim.x, sim.z, sim.yaw, sim.speed
        assert not body_over_edge(nav, truck), (truck.x, truck.z, truck.yaw)
        clear = min([clear] + [math.hypot(ox - truck.x, oz - truck.z) for ox, oz in obstacles])
    return False, clear, statuses


def test_every_site_pair_has_a_route(nav):
    for a, b in permutations(nav.sites.values(), 2):
        assert planning.plan_route(nav, a, b) is not None


def test_routes_follow_roads(nav):
    a, b = site(nav, "parking"), site(nav, "crusher")
    road = nav.road_route(a, b)
    assert road is not None
    route = planning.plan_route(nav, a, b)
    assert not nav.blocked_at(route[:, 0], route[:, 1]).any()


def test_drives_parking_to_crusher(nav):
    arrived, _, statuses = run(nav, site(nav, "parking"), site(nav, "crusher"))
    assert arrived and statuses <= {"driving", "arrived"}


def road_side_point(nav, route, i, offset):
    tangent = route[i + 1] - route[i]
    normal = np.array([tangent[1], -tangent[0]]) / np.hypot(*tangent)
    for side in (1, -1):
        p = route[i] + side * offset * normal
        if not nav.inflated_at(*p):
            return tuple(p)
    pytest.skip("no free road edge at this route point")


def test_passes_vehicle_parked_at_road_edge(nav):
    start, goal = site(nav, "parking"), site(nav, "crusher")
    route = planning.plan_route(nav, start, goal)
    blocker = road_side_point(nav, route, len(route) // 2, 3.0)
    arrived, clear, statuses = run(nav, start, goal, [blocker])
    assert arrived and clear > 3.0


def test_stops_safely_for_vehicle_blocking_road_centre(nav):
    start, goal = site(nav, "parking"), site(nav, "crusher")
    route = planning.plan_route(nav, start, goal)
    blocker = tuple(route[len(route) // 2])
    _, clear, statuses = run(nav, start, goal, [blocker], ticks=800)
    assert clear > 3.0
    assert "avoiding" in statuses or "holding" in statuses


def test_sees_pedestrian_next_to_road_wall(nav):
    truck = Truck("t", *site(nav, "parking"))
    band = np.argwhere(nav.inflated & ~nav.blocked)
    centres = np.array([nav.cell_centre(r, c) for r, c in band])
    near = centres[np.hypot(centres[:, 0] - truck.x, centres[:, 1] - truck.z) < 25]
    assert len(near), "no wall-side cell near the truck"
    seen = 0
    for person in near:
        truck.yaw = math.degrees(math.atan2(person[0] - truck.x, person[1] - truck.z))
        pts = scan(nav, truck, [tuple(person)], radius=0.3)
        _, obstacles = split_lidar(nav, truck.x, truck.z, math.radians(truck.yaw), pts, LIDAR_HEIGHT)
        seen += len(obstacles) > 0
    assert seen == len(near)


def test_ground_points_are_not_obstacles(nav):
    truck = Truck("t", *site(nav, "parking"))
    yaw = math.radians(truck.yaw)
    ground = np.array([[0.0, -LIDAR_HEIGHT, d] for d in range(5, 60, 5)], np.float32)
    ground[:, 1] += nav.ground_at(truck.x, truck.z + ground[:, 2]) - nav.ground_at(truck.x, truck.z)
    local, obstacles = split_lidar(nav, truck.x, truck.z, yaw, ground, LIDAR_HEIGHT)
    assert len(local) == 0 and len(obstacles) == 0


def test_lidar_bins_run_left_to_right():
    ranges = lidar_ranges(np.array([[-10.0, 10.0], [0.5, 20.0], [10.0, 10.0]]))
    hits = np.flatnonzero(ranges < 100)
    assert len(hits) == 3 and hits[0] < 30 <= hits[1] < hits[2]
    assert ranges[30] == pytest.approx(math.hypot(0.5, 20.0))


def test_holds_behind_vehicle_blocking_narrow_ramp(nav):
    start, goal = site(nav, "crusher"), site(nav, "ore")
    route = planning.plan_route(nav, start, goal)
    blocker = tuple(route[narrowest_route_point(nav, route)])
    arrived, clear, statuses = run(nav, start, goal, [blocker], ticks=700)
    assert not arrived and "holding" in statuses and clear > 3.0


@pytest.mark.parametrize("side", [-5.0, 5.0])
def test_keeps_body_on_narrow_ramp_next_to_vehicle(nav, side):
    # The vehicle leaves a gap on one side; the truck may pass only if its whole body fits.
    start, goal = site(nav, "crusher"), site(nav, "ore")
    route = planning.plan_route(nav, start, goal)
    i = narrowest_route_point(nav, route)
    d = (route[i + 1] - route[i]) / np.linalg.norm(route[i + 1] - route[i])
    blocker = tuple(route[i] + np.array([d[1], -d[0]]) * side)
    _, clear, _ = run(nav, start, goal, [blocker], ticks=700)
    assert clear > 3.0
