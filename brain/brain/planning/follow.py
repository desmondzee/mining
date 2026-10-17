import math
import time

import numpy as np

from ..world import Truck
from . import driver
from .driver import Decision, Scorer
from .navmap import NavMap

REPLAN_COOLDOWN = 1.0

_obstacle_seen: dict[str, dict[tuple[int, int], float]] = {}
_last_replan: dict[str, float] = {}
_waiting_since: dict[str, float] = {}


def reset() -> None:
    _obstacle_seen.clear()
    _last_replan.clear()
    _waiting_since.clear()


def drivable_from(truck: Truck, route: np.ndarray) -> bool:
    if len(route) < 2:
        return False
    dx, dz = route[min(5, len(route) - 1)] - route[0]
    bearing = math.degrees(math.atan2(dx, dz))
    return abs((bearing - truck.yaw + 180.0) % 360.0 - 180.0) < 90.0


def follow(
    truck: Truck,
    nav: NavMap,
    points: np.ndarray | None,
    scorer: Scorer | None,
    lidar_height: float,
    now: float | None = None,
) -> Decision:
    now = time.monotonic() if now is None else now
    decision, truck.path_idx = driver.drive(
        nav,
        np.asarray(truck.path),
        truck.path_idx,
        truck.x,
        truck.z,
        truck.yaw,
        truck.speed,
        truck.steer,
        points,
        scorer,
        lidar_height,
    )
    truck.ghost_paths = np.round(decision.candidates[decision.safe], 1).tolist()

    static = driver.static_obstacles(_obstacle_seen.setdefault(truck.id, {}), decision.obstacles, now)
    if decision.throttle > 0 or truck.speed > 0.3:
        _waiting_since.pop(truck.id, None)
    else:
        _waiting_since.setdefault(truck.id, now)
    waited = now - _waiting_since.get(truck.id, now) > driver.REPLAN_AFTER
    cooled = now - _last_replan.get(truck.id, -math.inf) >= REPLAN_COOLDOWN
    if cooled and (waited or driver.route_blocked(np.asarray(truck.path), truck.path_idx, static)):
        avoid = decision.obstacles if waited else static
        route = driver.plan_route(nav, (truck.x, truck.z), truck.goal, avoid=avoid)
        if route is not None and drivable_from(truck, route):
            truck.path, truck.path_idx = route.round(2).tolist(), 0
        _last_replan[truck.id] = now
        if waited:
            _waiting_since[truck.id] = now
    return decision
