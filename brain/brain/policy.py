import math
import os
from dataclasses import dataclass

import numpy as np

from . import planning
from .world import Truck

ARRIVE_RADIUS = 4.0
STOP_DIST = 6.0
SLOW_DIST = 15.0
STEER_GAIN_DEG = 45.0
GROUND_SLOPE = math.tan(math.radians(20.0))
GROUND_TOL = 0.2
COLUMN_DEG = 0.5
SIM_MAX_SPEED = 8.0
MAX_SPEED = 5.0
GOAL_SPEED = 3.0
GOAL_SLOW_DIST = 25.0


@dataclass
class Action:
    throttle: float
    steer: float
    status: str


def heading_error(truck: Truck, gx: float, gz: float) -> float:
    bearing = math.degrees(math.atan2(gx - truck.x, gz - truck.z))
    return (bearing - truck.yaw + 180.0) % 360.0 - 180.0


def sensor_rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return ry @ rx @ rz


def ground_mask(points: np.ndarray, world: np.ndarray, origin: np.ndarray, lidar_height: float) -> np.ndarray:
    """Walk each lidar column outwards and mark returns that continue the ground at no more than
    GROUND_SLOPE from the last ground return; steeper rises are obstacles."""
    col = np.round(np.degrees(np.arctan2(points[:, 0], points[:, 2])) / COLUMN_DEG).astype(int)
    dist = np.hypot(world[:, 0] - origin[0], world[:, 2] - origin[2])
    order = np.lexsort((dist, col))
    cols, inverse, counts = np.unique(col[order], return_inverse=True, return_counts=True)
    rank = np.arange(len(order)) - np.repeat(np.cumsum(counts) - counts, counts)
    prev_d = np.zeros(len(cols))
    prev_y = np.full(len(cols), origin[1] - lidar_height)
    ground = np.zeros(len(points), bool)
    for k in range(int(rank.max(initial=-1)) + 1):
        at = rank == k
        idx, c = order[at], inverse[at]
        ok = world[idx, 1] - prev_y[c] <= GROUND_SLOPE * (dist[idx] - prev_d[c]) + GROUND_TOL
        ground[idx[ok]] = True
        prev_d[c[ok]], prev_y[c[ok]] = dist[idx[ok]], world[idx[ok], 1]
    return ground


def planner_points(
    points: np.ndarray, sensor: dict, truck: Truck, ground: float, lidar_height: float
) -> np.ndarray:
    """Re-express sensor-frame points as the planner expects them: a level sensor lidar_height above
    the ground at the truck centre, facing the truck heading. Ground returns are dropped first, since
    the scanned terrain has bumps the coarse nav grid heights do not."""
    rot = sensor_rotation(sensor["yaw"], sensor.get("pitch", 0.0), sensor.get("roll", 0.0))
    origin = np.array([sensor["x"], sensor["y"], sensor["z"]])
    world = points @ rot.T + origin
    world = world[~ground_mask(points, world, origin, lidar_height)]
    dx, dz = world[:, 0] - truck.x, world[:, 2] - truck.z
    s, c = math.sin(math.radians(truck.yaw)), math.cos(math.radians(truck.yaw))
    y = world[:, 1] - ground - lidar_height
    return np.stack([dx * c - dz * s, y, dx * s + dz * c], axis=1).astype(np.float32)


def speed_cap(truck: Truck) -> float:
    cap = float(os.environ.get("MAX_SPEED", MAX_SPEED))
    if truck.goal is not None and math.hypot(truck.goal[0] - truck.x, truck.goal[1] - truck.z) < GOAL_SLOW_DIST:
        cap = min(cap, GOAL_SPEED)
    return cap / SIM_MAX_SPEED


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
    sensor: dict | None = None,
) -> Action:
    truck.planner = "learned" if planning.scorer() is not None else "rules"
    if truck.goal is None:
        return Action(0.0, 0.0, "idle")

    gx, gz = truck.goal
    if math.hypot(gx - truck.x, gz - truck.z) < ARRIVE_RADIUS:
        return Action(0.0, 0.0, "arrived")

    nav = planning.nav_map()
    if truck.path and nav is not None:
        ground = float(nav.ground_at(truck.x, truck.z))
        if points is not None and sensor is not None and math.isfinite(ground):
            points = planner_points(points, sensor, truck, ground, planning.lidar_height())
        d = planning.follow(truck, nav, points, planning.scorer(), planning.lidar_height(), now)
        return Action(min(d.throttle, speed_cap(truck)), d.steer, d.status)

    steer = max(-1.0, min(1.0, heading_error(truck, gx, gz) / STEER_GAIN_DEG))
    throttle = min(1.0 - 0.6 * abs(steer), speed_cap(truck))
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

