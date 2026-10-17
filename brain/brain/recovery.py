import math
import time

import numpy as np

from . import planning
from .planning import driver
from .world import Truck

STUCK_AFTER = 3.0
REVERSE_SPEED = 1.5
REVERSE_DIST = 10.0
REAR_CHECK = 3.0

_stuck_since: dict[str, float] = {}
_reversing: dict[str, tuple[float, float]] = {}


def reset() -> None:
    _stuck_since.clear()
    _reversing.clear()


def rear_clear(nav: planning.NavMap, truck: Truck) -> bool:
    yaw = math.radians(truck.yaw)
    back = np.arange(0.5, REAR_CHECK + 0.5, 0.5)
    xs, zs = truck.x - np.sin(yaw) * back, truck.z - np.cos(yaw) * back
    return not driver.off_road(nav, xs, zs, np.full(len(back), yaw), driver.TRUCK_HALF_W + driver.EDGE_MARGIN).any()


def reverse(truck: Truck, nav: planning.NavMap, decision: driver.Decision, now: float | None = None) -> float | None:
    """Throttle for a straight reverse out of a dead end, or None to keep the planner's choice.

    A truck with no safe forward candidate for STUCK_AFTER seconds backs up REVERSE_DIST metres while
    the nav grid behind it is clear, then replans from where it stopped."""
    now = time.monotonic() if now is None else now
    start = _reversing.get(truck.id)
    if start is not None:
        if math.hypot(truck.x - start[0], truck.z - start[1]) < REVERSE_DIST and rear_clear(nav, truck):
            return -REVERSE_SPEED / driver.MAX_V
        del _reversing[truck.id]
        _stuck_since.pop(truck.id, None)
        route = planning.plan_route(nav, (truck.x, truck.z), truck.goal)
        if route is not None:
            truck.path, truck.path_idx = route.round(2).tolist(), 0
        return None
    if len(decision.safe) and (decision.safe & (driver.CAND_SPEED > 0)).any():
        _stuck_since.pop(truck.id, None)
        return None
    if now - _stuck_since.setdefault(truck.id, now) > STUCK_AFTER and rear_clear(nav, truck):
        _reversing[truck.id] = (truck.x, truck.z)
        return -REVERSE_SPEED / driver.MAX_V
    return None
