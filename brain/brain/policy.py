import math
import time
from dataclasses import dataclass

import numpy as np

from . import planning
from .world import Truck

ARRIVE_RADIUS = 4.0
STOP_DIST = 6.0
SLOW_DIST = 15.0
STEER_GAIN_DEG = 45.0

REPLAN_COOLDOWN = 1.0

_obstacle_seen: dict[str, dict[tuple[int, int], float]] = {}
_last_replan: dict[str, float] = {}


@dataclass
class Action:
    throttle: float
    steer: float
    status: str


def heading_error(truck: Truck, gx: float, gz: float) -> float:
    bearing = math.degrees(math.atan2(gx - truck.x, gz - truck.z))
    return (bearing - truck.yaw + 180.0) % 360.0 - 180.0


def sector_ranges(depth: np.ndarray) -> tuple[float, float, float]:
    h, w = depth.shape
    band = depth[int(h * 0.35) : int(h * 0.65)]
    band = np.where(np.isfinite(band) & (band > 0), band, np.inf)
    thirds = np.array_split(band, 3, axis=1)
    return tuple(float(t.min()) if t.size else math.inf for t in thirds)


def act(
    truck: Truck,
    rgb: np.ndarray | None,
    depth: np.ndarray | None,
    points: np.ndarray | None = None,
    now: float | None = None,
) -> Action:
    if truck.goal is None:
        return Action(0.0, 0.0, "idle")

    gx, gz = truck.goal
    if math.hypot(gx - truck.x, gz - truck.z) < ARRIVE_RADIUS:
        return Action(0.0, 0.0, "arrived")

    nav = planning.nav_map()
    if truck.path and nav is not None:
        return follow_path(truck, nav, points, now)

    steer = max(-1.0, min(1.0, heading_error(truck, gx, gz) / STEER_GAIN_DEG))
    throttle = 1.0 - 0.6 * abs(steer)
    status = "driving"

    if depth is not None:
        left, centre, right = sector_ranges(depth)
        away = 1.0 if right > left else -1.0
        if centre < STOP_DIST:
            return Action(0.15, away, "avoiding")
        if centre < SLOW_DIST or min(left, right) < STOP_DIST:
            closeness = 1.0 - min(centre, SLOW_DIST) / SLOW_DIST
            steer = max(-1.0, min(1.0, steer + away * (0.5 + closeness)))
            throttle *= 0.5
            status = "avoiding"

    return Action(throttle, steer, status)


def drivable_from(truck: Truck, route: np.ndarray) -> bool:
    if len(route) < 2:
        return False
    dx, dz = route[min(5, len(route) - 1)] - route[0]
    bearing = math.degrees(math.atan2(dx, dz))
    return abs((bearing - truck.yaw + 180.0) % 360.0 - 180.0) < 90.0


def follow_path(truck: Truck, nav: planning.NavMap, points: np.ndarray | None, now: float | None = None) -> Action:
    now = time.monotonic() if now is None else now
    decision, truck.path_idx = planning.drive(
        nav,
        np.asarray(truck.path),
        truck.path_idx,
        truck.x,
        truck.z,
        truck.yaw,
        truck.speed,
        truck.steer,
        points,
        planning.scorer(),
        planning.lidar_height(),
    )
    truck.ghost_paths = np.round(decision.candidates[decision.safe], 1).tolist()

    seen = _obstacle_seen.setdefault(truck.id, {})
    static = planning.static_obstacles(seen, decision.obstacles, now)
    if decision.throttle > 0 or truck.speed > 0.3:
        truck.waiting_since = None
    elif truck.waiting_since is None:
        truck.waiting_since = now
    waited = truck.waiting_since is not None and now - truck.waiting_since > planning.REPLAN_AFTER
    cooled = now - _last_replan.get(truck.id, -math.inf) >= REPLAN_COOLDOWN
    if cooled and (waited or planning.route_blocked(np.asarray(truck.path), truck.path_idx, static)):
        avoid = decision.obstacles if waited else static
        route = planning.plan_route(nav, (truck.x, truck.z), truck.goal, avoid=avoid)
        if route is not None and drivable_from(truck, route):
            truck.path, truck.path_idx = route.round(2).tolist(), 0
        _last_replan[truck.id] = now
        if waited:
            truck.waiting_since = now
    return Action(decision.throttle, decision.steer, decision.status)
