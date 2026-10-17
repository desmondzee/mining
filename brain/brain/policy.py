import math
from dataclasses import dataclass

import numpy as np

from .routing import cumulative, point_at, project
from .world import Truck

ARRIVE_RADIUS = 4.0
LOOKAHEAD = 12.0
PROGRESS_WINDOW = 25.0
PROGRESS_BACK = 5.0
APPROACH_DIST = 20.0
APPROACH_MIN_THROTTLE = 0.3
WHEELBASE = 5.0
MAX_STEER_DEG = 30.0
STOP_DIST = 6.0
SLOW_DIST = 15.0
STEER_GAIN_DEG = 45.0


@dataclass
class Action:
    throttle: float
    steer: float
    status: str


def heading_error(truck: Truck, gx: float, gz: float) -> float:
    bearing = math.degrees(math.atan2(gx - truck.x, gz - truck.z))
    return (bearing - truck.yaw + 180.0) % 360.0 - 180.0


def clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def inside_turn_circle(truck: Truck, tx: float, tz: float, side: float) -> bool:
    radius = WHEELBASE / math.tan(math.radians(MAX_STEER_DEG))
    yaw = math.radians(truck.yaw)
    cx = truck.x + side * radius * math.cos(yaw)
    cz = truck.z - side * radius * math.sin(yaw)
    return math.hypot(tx - cx, tz - cz) < radius


def pursuit_steer(truck: Truck, tx: float, tz: float, final: bool = False) -> float:
    alpha = math.radians(heading_error(truck, tx, tz))
    if final and inside_turn_circle(truck, tx, tz, math.copysign(1.0, alpha)):
        return 0.0
    if abs(alpha) >= math.pi / 2:
        return math.copysign(1.0, alpha)
    ld = max(math.hypot(tx - truck.x, tz - truck.z), 1e-6)
    return clamp(math.degrees(math.atan(2 * WHEELBASE * math.sin(alpha) / ld)) / MAX_STEER_DEG)


def follow_route(truck: Truck) -> tuple[float, float] | None:
    pts = [(p[0], p[1]) for p in truck.route]
    if len(pts) == 1:
        pts.insert(0, (truck.x, truck.z))
    cum = cumulative(pts)
    d, s, _, _ = project(pts, cum, truck.x, truck.z, truck.route_s - PROGRESS_BACK, truck.route_s + PROGRESS_WINDOW)
    if math.isfinite(d):
        truck.route_s = max(truck.route_s, s)
    remaining = cum[-1] - truck.route_s
    if remaining <= LOOKAHEAD and math.hypot(pts[-1][0] - truck.x, pts[-1][1] - truck.z) < ARRIVE_RADIUS:
        return None
    tx, tz = point_at(pts, cum, truck.route_s + LOOKAHEAD)
    throttle = max(APPROACH_MIN_THROTTLE, min(1.0, remaining / APPROACH_DIST))
    return pursuit_steer(truck, tx, tz, remaining <= LOOKAHEAD), throttle


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
) -> Action:
    if truck.goal is None:
        return Action(0.0, 0.0, "idle")

    gx, gz = truck.goal
    goal_dist = math.hypot(gx - truck.x, gz - truck.z)
    if truck.route:
        follow = follow_route(truck)
        if follow is None:
            return Action(0.0, 0.0, "arrived")
        steer, scale = follow
        throttle = (1.0 - 0.6 * abs(steer)) * scale
    else:
        if goal_dist < ARRIVE_RADIUS:
            return Action(0.0, 0.0, "arrived")
        steer = clamp(heading_error(truck, gx, gz) / STEER_GAIN_DEG)
        throttle = 1.0 - 0.6 * abs(steer)
    status = "driving"

    if depth is not None:
        left, centre, right = (r if r < goal_dist else math.inf for r in sector_ranges(depth))
        away = 1.0 if right > left else -1.0
        if centre < STOP_DIST:
            return Action(0.15, away, "avoiding")
        if centre < SLOW_DIST or min(left, right) < STOP_DIST:
            closeness = 1.0 - min(centre, SLOW_DIST) / SLOW_DIST
            if goal_dist > APPROACH_DIST:
                steer = clamp(steer + away * (0.5 + closeness))
            throttle *= 0.5
            status = "avoiding"

    return Action(throttle, steer, status)
