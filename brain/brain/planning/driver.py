import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .navmap import NavMap, chaikin, resample

MAX_V = 8.0
MAX_STEER = math.radians(30.0)
WHEELBASE = 5.0
ACCEL = 1.5
DT = 0.1
HORIZON = 3.0
RDT = 0.2
N_ROLL = int(round(HORIZON / RDT))
STEERS = np.array([-1, -0.75, -0.5, -0.3, -0.15, 0, 0.15, 0.3, 0.5, 0.75, 1.0])
SPEEDS = np.array([8.0, 5.0, 2.5, 0.0])
CAND_STEER = np.repeat(STEERS, len(SPEEDS))
CAND_SPEED = np.tile(SPEEDS, len(STEERS))
N_CAND = len(CAND_STEER)
HOLD_ACTION = int(np.flatnonzero((CAND_STEER == 0) & (CAND_SPEED == 0))[0])

N_RAYS = 60
LIDAR_FOV = math.radians(120.0)
LIDAR_RANGE = 100.0
ROUTE_POINTS = 8
ROUTE_SPACING = 5.0
ROUTE_STEP = 2.0
OBS_DIM = N_RAYS + 2 * ROUTE_POINTS + 3
CAND_DIM = 2 * N_ROLL + 3

ARRIVE_RADIUS = 5.0
TRUCK_HALF_W = 2.12
EDGE_MARGIN = 0.5
LIDAR_HEIGHT = 3.0
MIN_OBSTACLE_HEIGHT = 0.8
OBSTACLE_RADIUS = 4.0
OBSTACLE_REACH = MAX_V * HORIZON + OBSTACLE_RADIUS + 2.0

Scorer = Callable[[np.ndarray, np.ndarray, np.ndarray], int]


@dataclass
class Decision:
    throttle: float
    steer: float
    status: str
    choice: int
    candidates: np.ndarray
    safe: np.ndarray


def plan_route(nav: NavMap, start: tuple[float, float], goal: tuple[float, float]) -> np.ndarray | None:
    cells = nav.astar(start, goal)
    if cells is None:
        return None
    pts = np.vstack([start, cells, goal])
    return resample(chaikin(pts, 3), ROUTE_STEP)


def ego(points: np.ndarray, x: float, z: float, yaw: float) -> np.ndarray:
    d = points - np.array([x, z])
    s, c = math.sin(yaw), math.cos(yaw)
    return np.stack([d[..., 0] * s + d[..., 1] * c, d[..., 0] * c - d[..., 1] * s], axis=-1)


def split_lidar(
    nav: NavMap, x: float, z: float, yaw: float, points: np.ndarray | None, lidar_height: float
) -> tuple[np.ndarray, np.ndarray]:
    if points is None or len(points) == 0:
        return np.zeros((0, 2)), np.zeros((0, 2))
    s, c = math.sin(yaw), math.cos(yaw)
    wx = x + points[:, 2] * s + points[:, 0] * c
    wz = z + points[:, 2] * c - points[:, 0] * s
    sensor_y = nav.ground_at(x, z) + lidar_height
    with np.errstate(invalid="ignore"):
        above = sensor_y + points[:, 1] - nav.ground_at(wx, wz) > MIN_OBSTACLE_HEIGHT
    local = points[above][:, [0, 2]]
    world = np.stack([wx[above], wz[above]], axis=1)
    dynamic = world[~nav.inflated_at(world[:, 0], world[:, 1])]
    near = dynamic[np.hypot(dynamic[:, 0] - x, dynamic[:, 1] - z) < OBSTACLE_REACH]
    return local, np.unique(np.round(near), axis=0)


def lidar_ranges(local: np.ndarray) -> np.ndarray:
    ranges = np.full(N_RAYS, LIDAR_RANGE)
    if len(local):
        bearing = np.arctan2(local[:, 0], local[:, 1])
        idx = np.floor((bearing + LIDAR_FOV / 2) / (LIDAR_FOV / N_RAYS)).astype(int)
        ok = (idx >= 0) & (idx < N_RAYS)
        np.minimum.at(ranges, idx[ok], np.minimum(np.hypot(local[ok, 0], local[ok, 1]), LIDAR_RANGE))
    return ranges


def rollout(x: float, z: float, yaw: float, v0: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    v = np.full(N_CAND, v0)
    yaws = np.full(N_CAND, yaw)
    xs = np.full(N_CAND, x)
    zs = np.full(N_CAND, z)
    sub = int(round(RDT / DT))
    fine = np.empty((N_CAND, N_ROLL * sub, 2))
    fine_yaw = np.empty((N_CAND, N_ROLL * sub))
    tan = np.tan(CAND_STEER * MAX_STEER)
    for k in range(N_ROLL * sub):
        v = v + (CAND_SPEED - v) * min(1.0, ACCEL * DT)
        yaws = yaws + v / WHEELBASE * tan * DT
        xs = xs + v * np.sin(yaws) * DT
        zs = zs + v * np.cos(yaws) * DT
        fine[:, k, 0], fine[:, k, 1] = xs, zs
        fine_yaw[:, k] = yaws
    return fine, fine[:, sub - 1 :: sub], fine_yaw


def off_road(nav: NavMap, x, z, yaw, half_w: float) -> np.ndarray:
    """True where the centre or either side of the truck at (x, z, yaw) is on a blocked cell."""
    rx, rz = np.cos(yaw) * half_w, -np.sin(yaw) * half_w
    return nav.blocked_at(x, z) | nav.blocked_at(x + rx, z + rz) | nav.blocked_at(x - rx, z - rz)


def nearest_index(route: np.ndarray, start: int, x: float, z: float) -> int:
    lo = max(0, start - 5)
    seg = route[lo : start + 40]
    return max(start, int(np.argmin(np.sum((seg - [x, z]) ** 2, axis=1))) + lo)


def drive(
    nav: NavMap,
    route: np.ndarray,
    route_idx: int,
    x: float,
    z: float,
    yaw_deg: float,
    speed: float,
    last_steer: float,
    points: np.ndarray | None,
    scorer: Scorer | None = None,
    lidar_height: float = LIDAR_HEIGHT,
    load: float = 0.0,
) -> tuple[Decision, int]:
    ni = nearest_index(route, route_idx, x, z)
    end = route[-1]
    if math.hypot(end[0] - x, end[1] - z) < ARRIVE_RADIUS:
        return Decision(0.0, 0.0, "arrived", HOLD_ACTION, np.zeros((0, N_ROLL, 2)), np.zeros(0, bool)), ni

    yaw = math.radians(yaw_deg)
    fine, pts, fine_yaw = rollout(x, z, yaw, speed)
    # The whole body must stay on the road. On a narrow road a swerve that puts a side over
    # the edge is unsafe, so when nothing else fits the truck holds.
    hit = off_road(nav, fine[..., 0], fine[..., 1], fine_yaw, TRUCK_HALF_W + EDGE_MARGIN).any(axis=1)
    local, obstacles = split_lidar(nav, x, z, yaw, points, lidar_height)
    clear = np.full(N_CAND, np.inf)
    if len(obstacles):
        obs = obstacles.astype(np.float32)
        diff = pts.astype(np.float32)[:, :, None, :] - obs[None, None]
        d2 = np.einsum("ctmk,ctmk->ctm", diff, diff)
        d0 = np.hypot(obs[:, 0] - x, obs[:, 1] - z) - OBSTACLE_RADIUS
        limit = OBSTACLE_RADIUS + np.minimum(1.5, d0 - 0.05)
        hit |= (d2 < np.square(np.maximum(limit, 0.0))[None, None, :]).any(axis=(1, 2))
        clear = np.sqrt(d2.min(axis=(1, 2))) - OBSTACLE_RADIUS
    safe = ~hit

    if not safe.any():
        choice = HOLD_ACTION
    elif scorer is not None:
        step = int(ROUTE_SPACING / ROUTE_STEP)
        idx = np.minimum(len(route) - 1, ni + step * np.arange(1, ROUTE_POINTS + 1))
        obs = np.concatenate(
            [
                lidar_ranges(local) / LIDAR_RANGE,
                ego(route[idx], x, z, yaw).ravel() / 40.0,
                [speed / MAX_V, last_steer, load],
            ]
        ).astype(np.float32)
        shape = ego(pts, x, z, yaw).reshape(N_CAND, 2 * N_ROLL) / 30.0
        cands = np.concatenate(
            [shape, CAND_STEER[:, None], CAND_SPEED[:, None] / MAX_V, np.minimum(clear, 20.0)[:, None] / 20.0], axis=1
        ).astype(np.float32)
        choice = scorer(obs, cands, safe)
    else:
        tgt = route[min(len(route) - 1, ni + 12)]
        ends = pts[:, -1, :]
        window = route[ni : ni + 30]
        cross = np.linalg.norm(ends[:, None, :] - window[None], axis=-1).min(axis=1)
        score = (
            np.linalg.norm(ends - tgt, axis=1)
            + 1.5 * cross
            + 4 * np.abs(CAND_STEER - last_steer)
            - 1.2 * CAND_SPEED
            + 10 * np.maximum(0, 1 - clear / 10)
        )
        choice = int(np.argmin(np.where(safe, score, np.inf)))

    if not safe[choice] or CAND_SPEED[choice] == 0:
        status = "holding"
    elif clear[choice] < 10:
        status = "avoiding"
    else:
        status = "driving"
    return Decision(float(CAND_SPEED[choice] / MAX_V), float(CAND_STEER[choice]), status, choice, pts, safe), ni
