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
WALL_SNAP_M = 1.0
OBSTACLE_RADIUS = 4.0
OBSTACLE_REACH = MAX_V * HORIZON + OBSTACLE_RADIUS + 2.0
HIT_MARGIN = 0.5
REPLAN_AFTER = 4.0
STATIC_CELL_M = 2.0
STATIC_AFTER = 1.5
ROUTE_CHECK_AHEAD = 30

Scorer = Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]


@dataclass
class Decision:
    throttle: float
    steer: float
    status: str
    choice: int
    candidates: np.ndarray
    safe: np.ndarray
    obstacles: np.ndarray
    scores: np.ndarray
    planner: str


def plan_route(
    nav: NavMap, start: tuple[float, float], goal: tuple[float, float], avoid: np.ndarray | None = None
) -> np.ndarray | None:
    if avoid is not None and len(avoid):
        blocked = nav.cells_near(avoid, OBSTACLE_RADIUS + 1.0)
        pts = nav.astar(start, goal, extra_blocked=blocked)
        if pts is None:
            pts = nav.astar(start, goal, inflate=False, extra_blocked=blocked)
    else:
        pts = nav.road_route(start, goal)
        if pts is None:
            pts = nav.astar(start, goal)
        if pts is None:
            pts = nav.astar(start, goal, inflate=False)
    if pts is None:
        return None
    return resample(chaikin(np.vstack([start, pts, goal]), 3), ROUTE_STEP)


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
    wall = np.zeros(len(world), bool)
    for ox, oz in ((0, 0), (WALL_SNAP_M, 0), (-WALL_SNAP_M, 0), (0, WALL_SNAP_M), (0, -WALL_SNAP_M)):
        wall |= nav.blocked_at(world[:, 0] + ox, world[:, 1] + oz)
    dynamic = world[~wall]
    near = dynamic[np.hypot(dynamic[:, 0] - x, dynamic[:, 1] - z) < OBSTACLE_REACH]
    return local, np.unique(np.round(near), axis=0)


def lidar_ranges(local: np.ndarray) -> np.ndarray:
    ranges = np.full(N_RAYS, LIDAR_RANGE)
    if len(local):
        bearing = np.arctan2(local[:, 0], local[:, 1])
        idx = np.floor((bearing + LIDAR_FOV / 2) / (LIDAR_FOV / N_RAYS)).astype(int)
        ok = (idx >= 0) & (idx < N_RAYS)
        np.minimum.at(ranges, idx[ok], np.hypot(local[ok, 0], local[ok, 1]))
    return np.clip(np.ceil(ranges), 1.0, LIDAR_RANGE)


def grid_ranges(nav: NavMap, x: float, z: float, yaw: float) -> np.ndarray:
    ang = yaw - LIDAR_FOV / 2 + (np.arange(N_RAYS) + 0.5) * LIDAR_FOV / N_RAYS
    dist = np.arange(1.0, LIDAR_RANGE + 1.0)
    hit = nav.blocked_at(x + np.sin(ang)[:, None] * dist[None], z + np.cos(ang)[:, None] * dist[None])
    return dist[np.where(hit.any(axis=1), hit.argmax(axis=1), len(dist) - 1)]


def features(
    nav: NavMap,
    route: np.ndarray,
    ni: int,
    x: float,
    z: float,
    yaw: float,
    speed: float,
    last_steer: float,
    load: float,
    local: np.ndarray,
    pts: np.ndarray,
    clear: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    step = int(ROUTE_SPACING / ROUTE_STEP)
    idx = np.minimum(len(route) - 1, ni + step * np.arange(1, ROUTE_POINTS + 1))
    rays = np.minimum(lidar_ranges(local), grid_ranges(nav, x, z, yaw))
    obs = np.concatenate(
        [rays / LIDAR_RANGE, ego(route[idx], x, z, yaw).ravel() / 40.0, [speed / MAX_V, last_steer, load]]
    ).astype(np.float32)
    shape = ego(pts, x, z, yaw).reshape(N_CAND, 2 * N_ROLL) / 30.0
    cands = np.concatenate(
        [shape, CAND_STEER[:, None], CAND_SPEED[:, None] / MAX_V, np.minimum(clear, 20.0)[:, None] / 20.0], axis=1
    ).astype(np.float32)
    return obs, cands


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
    """True where any point across the truck width at (x, z, yaw) is on a blocked cell.

    Samples are at most half a cell apart, so a one-cell wall between the centre and a side is not missed.
    """
    n = int(math.ceil(2 * half_w / (nav.cell / 2))) + 1
    rx, rz = np.cos(yaw), -np.sin(yaw)
    out = np.zeros(np.shape(x), dtype=bool)
    for off in np.linspace(-half_w, half_w, n):
        out |= nav.blocked_at(x + rx * off, z + rz * off)
    return out


def static_obstacles(seen: dict[tuple[int, int], float], obstacles: np.ndarray, now: float) -> np.ndarray:
    cells = {tuple(c) for c in np.floor(obstacles / STATIC_CELL_M).astype(int).tolist()}
    first = {c: seen.get(c, now) for c in cells}
    seen.clear()
    seen.update(first)
    static = [c for c, t0 in first.items() if now - t0 >= STATIC_AFTER]
    return (np.array(static, dtype=float) + 0.5) * STATIC_CELL_M if static else np.zeros((0, 2))


def route_blocked(route: np.ndarray, route_idx: int, static: np.ndarray) -> bool:
    if not len(static) or not len(route):
        return False
    ahead = route[route_idx : route_idx + ROUTE_CHECK_AHEAD]
    d = np.linalg.norm(ahead[:, None, :] - static[None], axis=-1)
    return bool((d < OBSTACLE_RADIUS + STATIC_CELL_M).any())


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
    planner = "learned" if scorer is not None else "rules"
    ni = nearest_index(route, route_idx, x, z)
    end = route[-1]
    if math.hypot(end[0] - x, end[1] - z) < ARRIVE_RADIUS:
        none = np.zeros((0, N_ROLL, 2)), np.zeros(0, bool), np.zeros((0, 2)), np.zeros(0)
        return Decision(0.0, 0.0, "arrived", HOLD_ACTION, *none, planner), ni

    yaw = math.radians(yaw_deg)
    fine, pts, fine_yaw = rollout(x, z, yaw, speed)
    # The whole body must stay on the road. On a narrow road a swerve that puts a side over
    # the edge is unsafe, so when nothing else fits the truck holds.
    hit = off_road(nav, fine[..., 0], fine[..., 1], fine_yaw, TRUCK_HALF_W + EDGE_MARGIN).any(axis=1)
    local, obstacles = split_lidar(nav, x, z, yaw, points, lidar_height)
    clear = np.full(N_CAND, np.inf)
    if len(obstacles):
        obs = obstacles.astype(np.float32)
        flat = pts.astype(np.float32).reshape(-1, 2)
        dx = flat[:, 0, None] - obs[None, :, 0]
        dz = flat[:, 1, None] - obs[None, :, 1]
        d2 = (dx * dx + dz * dz).reshape(N_CAND, N_ROLL, len(obs))
        d0 = np.hypot(obs[:, 0] - x, obs[:, 1] - z) - OBSTACLE_RADIUS
        limit = OBSTACLE_RADIUS + np.minimum(HIT_MARGIN, d0 - 0.05)
        hit |= (d2 < np.square(np.maximum(limit, 0.0))[None, None, :]).any(axis=(1, 2))
        clear = np.sqrt(d2.min(axis=(1, 2))) - OBSTACLE_RADIUS
    safe = ~hit

    if scorer is not None:
        obs, cands = features(nav, route, ni, x, z, yaw, speed, last_steer, load, local, pts, clear)
        scores = np.asarray(scorer(obs, cands, safe), dtype=float)
    else:
        tgt = route[min(len(route) - 1, ni + 12)]
        ends = pts[:, -1, :]
        window = route[ni : ni + 30]
        cross = np.linalg.norm(ends[:, None, :] - window[None], axis=-1).min(axis=1)
        scores = -(
            np.linalg.norm(ends - tgt, axis=1)
            + 1.5 * cross
            + 4 * np.abs(CAND_STEER - last_steer)
            - 1.2 * CAND_SPEED
            + 10 * np.maximum(0, 1 - clear / 10)
        )
    choice = int(np.argmax(np.where(safe, scores, -np.inf))) if safe.any() else HOLD_ACTION

    if not safe[choice] or CAND_SPEED[choice] == 0:
        status = "holding"
    elif clear[choice] < 10:
        status = "avoiding"
    else:
        status = "driving"
    decision = Decision(
        float(CAND_SPEED[choice] / MAX_V), float(CAND_STEER[choice]), status, choice, pts, safe, obstacles, scores, planner
    )
    return decision, ni
