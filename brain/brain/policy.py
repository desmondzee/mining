"""Truck policy: drive straight at the goal, avoid obstacles seen in the depth image.

Deliberately simple. The RGB frame is passed in so a detector can be added later
without changing the call sites.
"""

import math
from dataclasses import dataclass

import numpy as np

from .world import Truck

ARRIVE_RADIUS = 4.0  # metres
STOP_DIST = 6.0  # obstacle this close straight ahead: crawl and turn hard
SLOW_DIST = 15.0  # obstacle this close: slow down and bias away
STEER_GAIN_DEG = 45.0  # heading error that gives full steer


@dataclass
class Action:
    throttle: float  # 0..1
    steer: float  # -1 (left) .. 1 (right)
    status: str


def heading_error(truck: Truck, gx: float, gz: float) -> float:
    """Signed degrees to turn (positive = right) to face the goal."""
    bearing = math.degrees(math.atan2(gx - truck.x, gz - truck.z))
    return (bearing - truck.yaw + 180.0) % 360.0 - 180.0


def sector_ranges(depth: np.ndarray) -> tuple[float, float, float]:
    """Nearest depth in the left / centre / right thirds of the middle band of rows."""
    h, w = depth.shape
    band = depth[int(h * 0.35) : int(h * 0.65)]
    band = np.where(np.isfinite(band) & (band > 0), band, np.inf)
    thirds = np.array_split(band, 3, axis=1)
    return tuple(float(t.min()) if t.size else math.inf for t in thirds)


def act(truck: Truck, rgb: np.ndarray | None, depth: np.ndarray | None) -> Action:
    if truck.goal is None:
        return Action(0.0, 0.0, "idle")

    gx, gz = truck.goal
    if math.hypot(gx - truck.x, gz - truck.z) < ARRIVE_RADIUS:
        return Action(0.0, 0.0, "arrived")

    steer = max(-1.0, min(1.0, heading_error(truck, gx, gz) / STEER_GAIN_DEG))
    # Turning on the spot isn't possible, so keep some speed through big turns.
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
