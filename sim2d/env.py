"""2D haul-truck driving environment for training the local driving policy.

One haul truck follows an A* route on the real nav grid while light vehicles
move along the roads. Each tick (0.1 s) the env builds 44 candidate 3 s paths
from the kinematic bicycle model (truck limits from spec.md). The action is
the index of the candidate to drive (design 3: "pick a path"). The baseline
expert scores the candidates by hand; the policy learns to replace it.

Yaw follows spec.md: radians here, 0 = +z, clockwise positive, so the forward
vector is (sin yaw, cos yaw) in x / z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
import os
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .world import World, chaikin, load, resample

# Worlds used for training and evaluation, comma-separated; worker processes inherit it.
DEFAULT_WORLDS = os.environ.get("SIM2D_WORLDS", "default,quarry")
# What the safety mask and the clearance feature know about movers; worker processes inherit it.
#   oracle: true positions and velocities, predicted 3 s ahead
#   lidar:  only lidar hits on movers, treated as still obstacles, as the brain does
DEFAULT_MOVERS = os.environ.get("SIM2D_MOVERS", "oracle")
MOVER_MODES = ("oracle", "lidar")
# Episode seeds kept for tests: the 2026-10-17 rows (1000+) and evaluate.EVAL_SEED (900000+).
TEST_SEEDS = (range(1000, 1100), range(900_000, 1_000_000))


def check_train_seeds(seeds) -> None:
    """Refuse training seeds that are also test seeds: make_env(seed) gives the same trip in both."""
    lo, hi = min(seeds), max(seeds)
    for r in TEST_SEEDS:
        if lo < r.stop and hi >= r.start:
            raise ValueError("training seeds %d-%d overlap test seeds %d-%d" % (lo, hi, r.start, r.stop - 1))


_WORLD_CACHE: Dict[str, World] = {}


def make_env(seed: Optional[int] = None, worlds: str = DEFAULT_WORLDS, movers: str = DEFAULT_MOVERS) -> "DriveEnv":
    for w in worlds.split(","):
        if w not in _WORLD_CACHE:
            _WORLD_CACHE[w] = load(w)
    return DriveEnv([_WORLD_CACHE[w] for w in worlds.split(",")], seed=seed, movers=movers)

MAX_V = 8.0
MAX_STEER = math.radians(30.0)
WHEELBASE = 5.0
ACCEL = 1.5  # 1/s speed response
DT = 0.1
HORIZON = 3.0
RDT = 0.2
N_ROLL = int(round(HORIZON / RDT))
TRUCK_R = 3.5
TRUCK_HALF_W = 2.12  # Komatsu HD465-7, 4.24 m wide
EDGE_MARGIN = 0.5  # extra room a candidate path must keep to a road edge, m
VEHICLE_R = 1.5  # light vehicle; truck + vehicle = 5 m
PED_R = 0.3  # spec.md pedestrian collider radius
PED_SPEED = (1.2, 1.6)  # spec.md walking speed range, m/s
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
ROUTE_STEP = 2.0  # route resample step, m
OBS_DIM = N_RAYS + 2 * ROUTE_POINTS + 3
CAND_DIM = 2 * N_ROLL + 3  # path points, steer, target speed, predicted clearance

ARRIVE_RADIUS = 5.0
HIT_MARGIN = 0.5  # m of gap a candidate path must keep from a mover it closes in on
REPLAN_AFTER = 4.0  # s of waiting before re-planning the route around movers
MAX_TICKS = 1500
HOLD_FREE_CLEAR = 15.0  # m: a stop with no mover nearer than this is idle waiting and costs reward

# Lidar-only mover mode, the same rules as brain/brain/planning/driver.py.
SCAN_RAYS = 240  # Unity / Isaac lidar: 240 columns over 120 deg
SCAN_STEP = 0.5  # m between wall samples along a ray
WALL_SNAP_M = 1.0  # hits this close to a blocked cell are taken as wall, not as a mover
OBSTACLE_RADIUS = 4.0  # truck reach around a still obstacle point
OBSTACLE_REACH = MAX_V * HORIZON + OBSTACLE_RADIUS + 2.0


@dataclass
class Mover:
    pts: np.ndarray  # (k, 2) road centreline, 1 m spacing
    s: float
    direction: int
    speed: float
    static: bool = False
    pedestrian: bool = False  # pedestrians never give way
    radius: float = VEHICLE_R
    side: int = 0
    off: float = 0.0
    cool: float = 0.0
    x: float = 0.0
    z: float = 0.0
    vx: float = 0.0
    vz: float = 0.0


@dataclass
class Truck:
    x: float
    z: float
    yaw: float
    v: float = 0.0
    steer: float = 0.0
    load: float = 0.0
    trail: List[Tuple[float, float]] = field(default_factory=list)


def ego(points: np.ndarray, x: float, z: float, yaw: float) -> np.ndarray:
    """World x/z points -> truck frame (forward, right) in metres."""
    d = points - np.array([x, z])
    s, c = math.sin(yaw), math.cos(yaw)
    fwd = d[..., 0] * s + d[..., 1] * c
    right = d[..., 0] * c - d[..., 1] * s
    return np.stack([fwd, right], axis=-1)


class DriveEnv:
    def __init__(
        self,
        worlds: Union[World, Sequence[World]],
        n_movers: Tuple[int, int] = (1, 3),
        n_peds: Tuple[int, int] = (2, 6),
        seed: Optional[int] = None,
        movers: str = DEFAULT_MOVERS,
    ):
        """Each reset picks one of `worlds` at random. `movers` is one of MOVER_MODES."""
        if movers not in MOVER_MODES:
            raise ValueError(f"movers must be one of {MOVER_MODES}, not {movers!r}")
        self.worlds = [worlds] if isinstance(worlds, World) else list(worlds)
        self.n_movers = n_movers
        self.n_peds = n_peds
        self.mover_info = movers
        self.obstacles = np.zeros((0, 2))
        self.rng = np.random.default_rng(seed)
        self._roads = {w.name: [resample(r, 1.0) for r in w.roads if len(r) > 1] for w in self.worlds}
        self.world = self.worlds[0]

    # ---- episode ----
    def reset(self, start: Optional[str] = None, goal: Optional[str] = None, load: Optional[float] = None) -> Dict[str, np.ndarray]:
        self.world = w = self.worlds[self.rng.integers(len(self.worlds))]
        self.road_pts = self._roads[w.name]
        ids = list(w.sites)
        for _ in range(50):
            a = start or ids[self.rng.integers(len(ids))]
            b = goal or ids[self.rng.integers(len(ids))]
            if a == b:
                continue
            path = w.astar(w.sites[a], w.sites[b])
            if path is not None and len(path) > 5:
                break
        else:
            raise RuntimeError("no route found between sites")
        self.start_id, self.goal_id = a, b
        self.route = resample(chaikin(path, 3), ROUTE_STEP)
        head = self.route[min(4, len(self.route) - 1)] - self.route[0]
        self.truck = Truck(x=float(self.route[0, 0]), z=float(self.route[0, 1]), yaw=math.atan2(head[0], head[1]))
        self.truck.load = float(self.rng.uniform(0, 1)) if load is None else load
        self.movers = self._spawn_movers()
        self.t = 0.0
        self.ticks = 0
        self.hold_time = 0.0
        self.route_idx = 0
        self.min_clear = math.inf
        self.stops = 0
        self.replans = 0
        self._was_moving = False
        self._plan()
        return self.observe()

    def _spawn_movers(self) -> List[Mover]:
        lo, hi = self.n_movers
        k = int(self.rng.integers(lo, hi + 1))
        movers = []
        for _ in range(k):
            pts = self.road_pts[self.rng.integers(len(self.road_pts))]
            m = Mover(pts=pts, s=float(self.rng.uniform(0, len(pts) - 1)), direction=int(self.rng.choice([-1, 1])), speed=float(self.rng.uniform(2.5, 5.0)))
            movers.append(m)
        lo, hi = self.n_peds
        for _ in range(int(self.rng.integers(lo, hi + 1))):
            m = self._pedestrian()
            if m is not None:
                movers.append(m)
        for m in movers:
            self._place(m)
            # Do not start a mover on top of the truck.
            for _ in range(40):
                if math.hypot(m.x - self.truck.x, m.z - self.truck.z) >= 30:
                    break
                m.s = (m.s + 7) % (len(m.pts) - 1)
                self._place(m)
        return movers

    def _pedestrian(self) -> Optional[Mover]:
        """A walker that crosses a random road back and forth, as in the Unity scene."""
        road = self.road_pts[self.rng.integers(len(self.road_pts))]
        if len(road) < 4:
            return None
        i = int(self.rng.integers(1, len(road) - 1))
        d = road[i + 1] - road[i - 1]
        perp = np.array([d[1], -d[0]]) / (np.hypot(*d) or 1.0)
        half = float(self.rng.uniform(12, 20))
        line = road[i] + perp * np.arange(-half, half + 1.0)[:, None]
        free = ~self.world.blocked_at(line[:, 0], line[:, 1])
        mid = len(line) // 2
        lo, hi = mid, mid
        while lo > 0 and free[lo - 1]:
            lo -= 1
        while hi < len(line) - 1 and free[hi + 1]:
            hi += 1
        if hi - lo < 6:
            return None
        pts = line[lo : hi + 1]
        return Mover(pts=pts, s=float(self.rng.uniform(0, len(pts) - 1)), direction=int(self.rng.choice([-1, 1])),
                     speed=float(self.rng.uniform(*PED_SPEED)), pedestrian=True, radius=PED_R)

    def _place(self, m: Mover) -> Tuple[float, float]:
        L = len(m.pts) - 1
        i = min(L - 1, int(m.s))
        f = m.s - i
        a, b = m.pts[i], m.pts[i + 1]
        seg = b - a
        n = math.hypot(*seg) or 1.0
        perp = np.array([seg[1] / n, -seg[0] / n])
        p = a + seg * f + perp * m.off
        m.x, m.z = float(p[0]), float(p[1])
        return float(perp[0]), float(perp[1])

    def _step_movers(self, dt: float) -> None:
        w, tr = self.world, self.truck
        for m in self.movers:
            if m.static:
                continue
            px, pz = m.x, m.z
            L = len(m.pts) - 1
            if m.pedestrian:
                m.s += m.direction * m.speed * dt
                if m.s >= L:
                    m.s, m.direction = float(L), -1
                elif m.s <= 0:
                    m.s, m.direction = 0.0, 1
                self._place(m)
                m.vx, m.vz = (m.x - px) / dt, (m.z - pz) / dt
                continue
            d_truck = math.hypot(m.x - tr.x, m.z - tr.z)
            perp = self._place(m)
            m.x, m.z = px, pz
            s_before, off_before = m.s, m.off
            m.cool = max(0.0, m.cool - dt)
            # Light vehicles give way to haul trucks: pull off the road until the truck passes.
            ahead = m.pts[max(0, min(L, int(round(m.s + 5))))]
            away_dir = 1 if math.hypot(ahead[0] - tr.x, ahead[1] - tr.z) > d_truck else -1
            if m.side and self.hold_time > 3.0:
                # Deadlock breaker: the truck has waited 3 s, so drive away along the road.
                m.side, m.cool = 0, 10.0
                m.direction = away_dir
            if d_truck < 22 and not m.side and not m.cool:
                base = np.array([m.x - perp[0] * m.off, m.z - perp[1] * m.off])
                pv = np.array(perp)
                away = 1 if np.sum((base + pv - [tr.x, tr.z]) ** 2) > np.sum((base - pv - [tr.x, tr.z]) ** 2) else -1
                ok = [s for s in (away, -away) if not w.blocked_at(*(base + pv * 12 * s))]
                m.side = ok[0] if ok else 0
                if not ok:
                    # No room to pull off (narrow road): back away from the truck once and keep going.
                    m.direction, m.cool = away_dir, 5.0
            elif d_truck > 28:
                m.side = 0
            want = m.side * 12.0
            m.off += max(-3 * dt, min(3 * dt, want - m.off))
            if not m.side and abs(m.off) < 0.5:
                m.s += m.direction * m.speed * dt
                if m.s >= L:
                    m.s, m.direction = float(L), -1
                elif m.s <= 0:
                    m.s, m.direction = 0.0, 1
            self._place(m)
            # A light vehicle never drives into the truck, forward or sideways; it waits instead.
            gap = math.hypot(m.x - tr.x, m.z - tr.z) - TRUCK_R - m.radius
            if gap < 1.0 and gap < d_truck - TRUCK_R - m.radius:
                m.s, m.off = s_before, off_before
                self._place(m)
            m.vx, m.vz = (m.x - px) / dt, (m.z - pz) / dt

    # ---- candidates and expert ----
    def _plan(self) -> None:
        tr = self.truck
        v = np.full(N_CAND, tr.v)
        yaw = np.full(N_CAND, tr.yaw)
        x = np.full(N_CAND, tr.x)
        z = np.full(N_CAND, tr.z)
        # Integrate at DT so thin walls cannot fall between samples; keep every RDT for features.
        sub = int(round(RDT / DT))
        fine = np.empty((N_CAND, N_ROLL * sub, 2))
        fine_yaw = np.empty((N_CAND, N_ROLL * sub))
        tan = np.tan(CAND_STEER * MAX_STEER)
        for k in range(N_ROLL * sub):
            v = v + (CAND_SPEED - v) * min(1.0, ACCEL * DT)
            yaw = yaw + v / WHEELBASE * tan * DT
            x = x + v * np.sin(yaw) * DT
            z = z + v * np.cos(yaw) * DT
            fine[:, k, 0], fine[:, k, 1] = x, z
            fine_yaw[:, k] = yaw
        pts = fine[:, sub - 1 :: sub]
        # The whole body must stay on the road: on a narrow road a swerve that puts a side
        # over the edge is unsafe, so the only safe choice left is to stop.
        hit = self._off_road(fine[..., 0], fine[..., 1], fine_yaw, TRUCK_HALF_W + EDGE_MARGIN).any(axis=1)
        clear = np.full(N_CAND, np.inf)
        if self.mover_info == "lidar":
            self.obstacles = obs = self.scan_obstacles()
            if len(obs):
                d = np.linalg.norm(pts[:, :, None, :] - obs[None, None], axis=-1)  # (C, T, P)
                d0 = np.hypot(obs[:, 0] - tr.x, obs[:, 1] - tr.z) - OBSTACLE_RADIUS
                # Still points: a path may not come closer than HIT_MARGIN, or than the point already is.
                limit = OBSTACLE_RADIUS + np.minimum(HIT_MARGIN, d0 - 0.05)
                hit |= (d < np.maximum(limit, 0.0)[None, None, :]).any(axis=(1, 2))
                clear = d.min(axis=(1, 2)) - OBSTACLE_RADIUS
        elif self.movers:
            times = RDT * np.arange(1, N_ROLL + 1)
            mp = np.array([[m.x, m.z] for m in self.movers])
            mv = np.array([[m.vx, m.vz] for m in self.movers])
            fut = mp[:, None, :] + mv[:, None, :] * times[None, :, None]  # (M, T, 2)
            reach = TRUCK_R + np.array([m.radius for m in self.movers])
            d = np.linalg.norm(pts[:, None, :, :] - fut[None], axis=-1) - reach[None, :, None]  # (C, M, T)
            d0 = np.linalg.norm(mp - [tr.x, tr.z], axis=1) - reach
            # Too close and still closing in. Moving away from a near vehicle is allowed.
            hit |= ((d < HIT_MARGIN) & (d < d0[None, :, None] - 0.05)).any(axis=(1, 2))
            clear = d.min(axis=(1, 2))
        self.cand_pts, self.cand_hit, self.cand_clear = pts, hit, clear

        ni = self._nearest_idx()
        tgt = self.route[min(len(self.route) - 1, ni + 12)]
        end = pts[:, -1, :]
        window = self.route[ni : ni + 30]
        cross = np.linalg.norm(end[:, None, :] - window[None], axis=-1).min(axis=1)
        self.expert_score = (
            np.linalg.norm(end - tgt, axis=1)
            + 1.5 * cross
            + 4 * np.abs(CAND_STEER - tr.steer)
            - 1.2 * CAND_SPEED
            + 10 * np.maximum(0, 1 - clear / 10)
        )

    def _replan_around(self) -> bool:
        """The truck has waited too long: plan a new A* route that treats nearby movers as walls."""
        w, tr = self.world, self.truck
        self.hold_time = 0.0
        yy, xx = np.mgrid[0 : w.n, 0 : w.n]
        cx, cz = w.ox + (xx + 0.5) * w.cell, w.oz + (yy + 0.5) * w.cell
        extra = np.zeros((w.n, w.n), dtype=bool)
        if self.mover_info == "lidar":
            for ox, oz in self.obstacles:
                extra |= (cx - ox) ** 2 + (cz - oz) ** 2 < OBSTACLE_RADIUS**2
        else:
            for m in self.movers:
                if math.hypot(m.x - tr.x, m.z - tr.z) < 40:
                    extra |= (cx - m.x) ** 2 + (cz - m.z) ** 2 < (TRUCK_R + m.radius) ** 2
        tr_r, tr_c = w.cell_of(tr.x, tr.z)
        extra[tr_r, tr_c] = False
        path = w.astar((tr.x, tr.z), tuple(self.route[-1]), extra_blocked=extra)
        if path is None or len(path) < 2:
            return False
        path[0] = (tr.x, tr.z)
        self.route = resample(chaikin(path, 3), ROUTE_STEP)
        self.route_idx = 0
        self.replans += 1
        return True

    def _off_road(self, x, z, yaw, half_w: float) -> np.ndarray:
        """True where any point across the truck width at (x, z, yaw) is on a blocked cell.

        Points are at most half a cell apart, so a one-cell blocked strip cannot sit between two of them.
        """
        w = self.world
        rx, rz = np.cos(yaw), -np.sin(yaw)
        out = np.zeros(np.shape(x), dtype=bool)
        for off in np.linspace(-half_w, half_w, int(math.ceil(2 * half_w / (w.cell / 2))) + 1):
            out |= w.blocked_at(x + rx * off, z + rz * off)
        return out

    def _nearest_idx(self) -> int:
        lo = max(0, self.route_idx - 5)
        seg = self.route[lo : self.route_idx + 40]
        i = int(np.argmin(np.sum((seg - [self.truck.x, self.truck.z]) ** 2, axis=1))) + lo
        self.route_idx = max(self.route_idx, i)
        return self.route_idx

    def safe_mask(self) -> np.ndarray:
        return ~self.cand_hit

    def expert_action(self) -> int:
        score = np.where(self.cand_hit, np.inf, self.expert_score)
        return int(np.argmin(score)) if np.isfinite(score).any() else HOLD_ACTION

    def scan_hits(self) -> np.ndarray:
        """World x/z of lidar rays that hit a mover before a wall, within OBSTACLE_REACH (before the wall filter)."""
        tr, w = self.truck, self.world
        if not self.movers:
            return np.zeros((0, 2))
        ang = tr.yaw - LIDAR_FOV / 2 + (np.arange(SCAN_RAYS) + 0.5) * LIDAR_FOV / SCAN_RAYS
        u = np.stack([np.sin(ang), np.cos(ang)], axis=1)  # (R, 2)
        c = np.array([[m.x - tr.x, m.z - tr.z] for m in self.movers])  # (M, 2)
        r = np.array([m.radius for m in self.movers])
        proj = u @ c.T  # (R, M)
        disc = proj**2 - (np.sum(c**2, axis=1) - r**2)[None]
        t = np.where(disc >= 0, proj - np.sqrt(np.maximum(disc, 0)), np.inf)
        t = np.where(t > 0, t, np.inf).min(axis=1)  # first mover along each ray
        steps = np.arange(SCAN_STEP, OBSTACLE_REACH + SCAN_STEP, SCAN_STEP)
        blocked = w.blocked_at(tr.x + u[:, :1] * steps[None], tr.z + u[:, 1:] * steps[None])
        wall = np.where(blocked.any(axis=1), steps[blocked.argmax(axis=1)], np.inf)
        keep = (t < wall) & (t < OBSTACLE_REACH)
        return np.array([tr.x, tr.z]) + u[keep] * t[keep, None]

    def scan_obstacles(self) -> np.ndarray:
        """Mover points as brain.planning.driver.split_lidar returns them: wall hits dropped, rounded to 1 m."""
        p = self.scan_hits()
        if not len(p):
            return np.zeros((0, 2))
        wall = np.zeros(len(p), dtype=bool)
        for ox, oz in ((0, 0), (WALL_SNAP_M, 0), (-WALL_SNAP_M, 0), (0, WALL_SNAP_M), (0, -WALL_SNAP_M)):
            wall |= self.world.blocked_at(p[:, 0] + ox, p[:, 1] + oz)
        p = p[~wall]
        return np.unique(np.round(p), axis=0) if len(p) else np.zeros((0, 2))

    # ---- observation ----
    def lidar(self) -> np.ndarray:
        tr = self.truck
        ang = tr.yaw - LIDAR_FOV / 2 + (np.arange(N_RAYS) + 0.5) * LIDAR_FOV / N_RAYS
        dist = np.arange(1.0, LIDAR_RANGE + 1.0)
        xs = tr.x + np.sin(ang)[:, None] * dist[None]
        zs = tr.z + np.cos(ang)[:, None] * dist[None]
        hit = self.world.blocked_at(xs, zs)
        for m in self.movers:
            # Rays are 1 m samples, so a 0.3 m pedestrian needs a wider hit radius to be seen.
            r = 0.8 if m.pedestrian else 2 * m.radius
            hit |= (xs - m.x) ** 2 + (zs - m.z) ** 2 < r * r
        first = np.where(hit.any(axis=1), hit.argmax(axis=1), len(dist) - 1)
        return dist[first]

    def observe(self) -> Dict[str, np.ndarray]:
        tr = self.truck
        ni = self._nearest_idx()
        step = int(ROUTE_SPACING / ROUTE_STEP)
        idx = np.minimum(len(self.route) - 1, ni + step * np.arange(1, ROUTE_POINTS + 1))
        route_ego = ego(self.route[idx], tr.x, tr.z, tr.yaw)
        state = np.array([tr.v / MAX_V, tr.steer, tr.load])
        vec = np.concatenate([self.lidar() / LIDAR_RANGE, route_ego.ravel() / 40.0, state]).astype(np.float32)
        shape = ego(self.cand_pts, tr.x, tr.z, tr.yaw).reshape(N_CAND, 2 * N_ROLL) / 30.0
        clear = np.minimum(self.cand_clear, 20.0) / 20.0
        cands = np.concatenate([shape, CAND_STEER[:, None], CAND_SPEED[:, None] / MAX_V, clear[:, None]], axis=1)
        return {"obs": vec, "cands": cands.astype(np.float32), "mask": self.safe_mask()}

    # ---- step ----
    def step(self, action: int):
        tr = self.truck
        before = self.route_idx
        steer, vt = float(CAND_STEER[action]), float(CAND_SPEED[action])
        self.t += DT
        self.ticks += 1
        self._step_movers(DT)
        tr.v += (vt - tr.v) * min(1.0, ACCEL * DT)
        tr.steer = steer
        tr.yaw += tr.v / WHEELBASE * math.tan(steer * MAX_STEER) * DT
        tr.x += tr.v * math.sin(tr.yaw) * DT
        tr.z += tr.v * math.cos(tr.yaw) * DT
        tr.trail.append((tr.x, tr.z))
        if tr.v > 1:
            self._was_moving = True
        if self._was_moving and tr.v < 0.3:
            self.stops += 1
            self._was_moving = False
        self.hold_time = self.hold_time + DT if tr.v < 0.3 else 0.0

        clear = min((math.hypot(m.x - tr.x, m.z - tr.z) - TRUCK_R - m.radius for m in self.movers), default=math.inf)
        self.min_clear = min(self.min_clear, clear)
        off_road = bool(self._off_road(tr.x, tr.z, tr.yaw, TRUCK_HALF_W))
        collided = clear < 0 or off_road
        if self.hold_time >= REPLAN_AFTER and self._replan_around():
            before = 0
        self._plan()
        progress = (self.route_idx - before) * ROUTE_STEP
        end = self.route[-1]
        arrived = math.hypot(end[0] - tr.x, end[1] - tr.z) < ARRIVE_RADIUS

        timeout = self.ticks >= MAX_TICKS and not (collided or arrived)
        reward = 0.1 * progress - 0.02
        # The near-miss cost grows as the gap closes, so a 1.9 m pass costs little and a 0.2 m pass costs much.
        reward -= 0.5 * max(0.0, 1.0 - clear / 2.0)
        if tr.v < 0.3 and clear > HOLD_FREE_CLEAR:
            reward -= 0.05
        if collided:
            reward -= 10.0
        if arrived:
            reward += 10.0
        if timeout:
            reward -= 5.0
        done = collided or arrived or timeout
        info = {"collided": collided, "off_road": off_road, "arrived": arrived, "clear": clear, "stops": self.stops, "t": self.t}
        return self.observe(), reward, done, info
