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
from typing import Dict, List, Optional, Tuple

import numpy as np

from .world import World, chaikin, resample

MAX_V = 8.0
MAX_STEER = math.radians(30.0)
WHEELBASE = 5.0
ACCEL = 1.5  # 1/s speed response
DT = 0.1
HORIZON = 3.0
RDT = 0.2
N_ROLL = int(round(HORIZON / RDT))
CAR_R = 5.0  # truck + light vehicle combined radius
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
MAX_TICKS = 1500


@dataclass
class Mover:
    pts: np.ndarray  # (k, 2) road centreline, 1 m spacing
    s: float
    direction: int
    speed: float
    static: bool = False
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
    def __init__(self, world: World, n_movers: Tuple[int, int] = (2, 5), seed: Optional[int] = None):
        self.world = world
        self.n_movers = n_movers
        self.rng = np.random.default_rng(seed)
        self.road_pts = [resample(r, 1.0) for r in world.roads if len(r) > 1]

    # ---- episode ----
    def reset(self, start: Optional[str] = None, goal: Optional[str] = None, load: Optional[float] = None) -> Dict[str, np.ndarray]:
        w = self.world
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
            self._place(m)
            # Do not start a vehicle on top of the truck.
            for _ in range(40):
                if math.hypot(m.x - self.truck.x, m.z - self.truck.z) >= 30:
                    break
                m.s = (m.s + 7) % (len(pts) - 1)
                self._place(m)
            movers.append(m)
        return movers

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
            d_truck = math.hypot(m.x - tr.x, m.z - tr.z)
            perp = self._place(m)
            m.x, m.z = px, pz
            m.cool = max(0.0, m.cool - dt)
            # Light vehicles give way to haul trucks: pull off the road until the truck passes.
            if m.side and self.hold_time > 3.0:
                # Deadlock breaker: the truck has waited 3 s, so drive away along the road.
                m.side, m.cool = 0, 10.0
                ahead = m.pts[max(0, min(L, int(round(m.s + 5))))]
                m.direction = 1 if math.hypot(ahead[0] - tr.x, ahead[1] - tr.z) > d_truck else -1
            if d_truck < 22 and not m.side and not m.cool:
                base = np.array([m.x - perp[0] * m.off, m.z - perp[1] * m.off])
                pv = np.array(perp)
                away = 1 if np.sum((base + pv - [tr.x, tr.z]) ** 2) > np.sum((base - pv - [tr.x, tr.z]) ** 2) else -1
                ok = [s for s in (away, -away) if not w.blocked_at(*(base + pv * 12 * s))]
                m.side = ok[0] if ok else 0
                if not ok:
                    m.direction = -m.direction
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
        tan = np.tan(CAND_STEER * MAX_STEER)
        for k in range(N_ROLL * sub):
            v = v + (CAND_SPEED - v) * min(1.0, ACCEL * DT)
            yaw = yaw + v / WHEELBASE * tan * DT
            x = x + v * np.sin(yaw) * DT
            z = z + v * np.cos(yaw) * DT
            fine[:, k, 0], fine[:, k, 1] = x, z
        pts = fine[:, sub - 1 :: sub]
        hit = self.world.blocked_at(fine[..., 0], fine[..., 1]).any(axis=1)
        clear = np.full(N_CAND, np.inf)
        if self.movers:
            times = RDT * np.arange(1, N_ROLL + 1)
            mp = np.array([[m.x, m.z] for m in self.movers])
            mv = np.array([[m.vx, m.vz] for m in self.movers])
            fut = mp[:, None, :] + mv[:, None, :] * times[None, :, None]  # (M, T, 2)
            d = np.linalg.norm(pts[:, None, :, :] - fut[None], axis=-1) - CAR_R  # (C, M, T)
            d0 = np.linalg.norm(mp - [tr.x, tr.z], axis=1) - CAR_R
            # Too close and still closing in. Moving away from a near vehicle is allowed.
            hit |= ((d < 1.5) & (d < d0[None, :, None] - 0.05)).any(axis=(1, 2))
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

    # ---- observation ----
    def lidar(self) -> np.ndarray:
        tr = self.truck
        ang = tr.yaw - LIDAR_FOV / 2 + (np.arange(N_RAYS) + 0.5) * LIDAR_FOV / N_RAYS
        dist = np.arange(1.0, LIDAR_RANGE + 1.0)
        xs = tr.x + np.sin(ang)[:, None] * dist[None]
        zs = tr.z + np.cos(ang)[:, None] * dist[None]
        hit = self.world.blocked_at(xs, zs)
        for m in self.movers:
            hit |= (xs - m.x) ** 2 + (zs - m.z) ** 2 < (CAR_R * 0.6) ** 2
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

        clear = min((math.hypot(m.x - tr.x, m.z - tr.z) - CAR_R for m in self.movers), default=math.inf)
        self.min_clear = min(self.min_clear, clear)
        collided = clear < 0 or bool(self.world.blocked_at(tr.x, tr.z))
        self._plan()
        progress = (self.route_idx - before) * ROUTE_STEP
        end = self.route[-1]
        arrived = math.hypot(end[0] - tr.x, end[1] - tr.z) < ARRIVE_RADIUS

        reward = 0.1 * progress - 0.02
        if clear < 2.0:
            reward -= 0.5
        if collided:
            reward -= 10.0
        if arrived:
            reward += 10.0
        done = collided or arrived or self.ticks >= MAX_TICKS
        info = {"collided": collided, "arrived": arrived, "clear": clear, "stops": self.stops, "t": self.t}
        return self.observe(), reward, done, info
