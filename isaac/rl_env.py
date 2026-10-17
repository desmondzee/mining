"""Gymnasium-style RL environment on the same truck/sensor code as the brain bridge (no duplicate physics).

    from isaac.rl_env import IsaacQuarryEnv
    env = IsaacQuarryEnv(backend="fake")                # "isaac" under Isaac's python.sh (RTX RGB, Warp raycasts)
    obs, info = env.reset(seed=0, truck_id="truck-0", goal="crusher_1")   # goal: site/junction id, (x, z) or None
    obs, reward, terminated, truncated, info = env.step({"throttle": 0.6, "steer": 0.0})

obs = {rgb (360, 640, 3) uint8, depth (36, 64) float32 m, lidar (7200, 3) float32 sensor-local m (zero padded),
       lidar_count int, pose [x, z, yaw], speed [m/s], goal [x, z]}.
Action = {throttle 0..1, steer -1..1 (positive = right)} or an array [throttle, steer]. Control at 15 Hz (camera rate),
physics substeps 1/60 s. Reward is pluggable (`reward_fn(env, prev, now) -> (reward, terminated, info)`); the default
rewards progress along the road-graph route, penalises collisions / off-road / wall contact, success inside 4 m.

`MultiTruckQuarryEnv` steps all trucks of one world at once (dict actions / dict observations) - the cheap
vectorised option: one physics/sensor pass serves N agents. The policy controls only throttle and steer.
"""

from __future__ import annotations

import heapq
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from isaac.sensors_bridge import CAM_H, CAM_W, DEPTH_H, DEPTH_W, LIDAR_CHANNELS, LIDAR_COLS, make_core, shade_depth  # noqa: E402
from isaac.sim.kinematics import GroundFollow  # noqa: E402
from isaac.sim.pedestrians import PEDESTRIAN_RADIUS  # noqa: E402

try:  # optional
    import gymnasium as _gym

    _Base = _gym.Env
except ImportError:  # plain class with the same API
    _gym = None
    _Base = object

ARRIVE_RADIUS = 4.0       # brain/brain/policy.py:ARRIVE_RADIUS
CONTROL_HZ = 15.0
SUBSTEP = 1 / 60
MAX_STEPS = 15 * 300      # 5 min of sim time
MAX_POINTS = LIDAR_COLS * LIDAR_CHANNELS
OFFROAD_SLACK = 1.0       # m beyond width/2 before off-road counts


@dataclass
class RewardConfig:
    progress: float = 1.0      # per metre of route progress
    time: float = 0.01         # per step
    collision: float = 10.0    # and terminate
    offroad: float = 0.1       # per step
    wall: float = 0.5          # per step the KeepInBounds guard blocked/slid (or would be outside the grid)
    success: float = 100.0     # and terminate


# ------------------------------------------------------------------------------------------------------- geometry
def polyline_project(route: np.ndarray, p: np.ndarray, start_seg: int = 0) -> tuple[float, int, float]:
    """(arc length of the closest point, its segment, distance) searching segments from start_seg on."""
    a, b = route[start_seg:-1], route[start_seg + 1 :]
    ab = b - a
    L2 = np.maximum((ab**2).sum(1), 1e-12)
    t = np.clip(((p - a) * ab).sum(1) / L2, 0, 1)
    q = a + ab * t[:, None]
    d = np.hypot(*(q - p).T)
    i = int(np.argmin(d))
    cum = np.concatenate([[0], np.cumsum(np.sqrt((np.diff(route, axis=0) ** 2).sum(1)))])
    return float(cum[start_seg + i] + t[i] * math.sqrt(L2[i])), start_seg + i, float(d[i])


def obb_overlap(c1, yaw1, half1, c2, yaw2, half2) -> bool:
    """2D separating-axis test of two yawed rectangles (half = (half_width_x, half_length_z))."""
    def axes(yaw):
        s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        return np.array([[c, -s], [s, c]])  # right, forward
    A, B, d = axes(yaw1), axes(yaw2), np.asarray(c2, float) - np.asarray(c1, float)
    for ax in (*A, *B):
        r1 = abs(A[0] @ ax) * half1[0] + abs(A[1] @ ax) * half1[1]
        r2 = abs(B[0] @ ax) * half2[0] + abs(B[1] @ ax) * half2[1]
        if abs(d @ ax) > r1 + r2:
            return False
    return True


class RoadGraph:
    """Sites/junctions joined by world.json roads (one_way honoured); routes are concatenated road polylines."""

    def __init__(self, world) -> None:
        self.nodes = {s.id: np.array([s.pos[0], s.pos[2]]) for s in world.sites} | {j.id: np.array([j.pos[0], j.pos[2]]) for j in world.junctions}
        self.edges: dict[str, list[tuple[str, float, np.ndarray]]] = {}
        self.roads = []
        for r in world.roads:
            pts = np.asarray(r.points, float)[:, [0, -1]]
            length = float(np.sqrt((np.diff(pts, axis=0) ** 2).sum(1)).sum())
            self.edges.setdefault(r.start, []).append((r.end, length, pts))
            if not r.one_way:
                self.edges.setdefault(r.end, []).append((r.start, length, pts[::-1]))
            self.roads.append((pts, r.width_m))

    def nearest_node(self, p) -> str | None:
        ids = [n for n in self.nodes if n in self.edges]
        return min(ids, key=lambda n: np.hypot(*(self.nodes[n] - p))) if ids else None

    def route(self, start: np.ndarray, goal: np.ndarray, goal_id: str | None = None) -> np.ndarray:
        src = self.nearest_node(start)
        dst = goal_id if goal_id in self.nodes else self.nearest_node(goal)
        if src is None or dst is None:
            return np.array([start, goal])
        dist, prev, heap = {src: 0.0}, {}, [(0.0, src)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == dst or d > dist.get(n, math.inf):
                continue
            for m, w, pts in self.edges.get(n, []):
                if d + w < dist.get(m, math.inf):
                    dist[m], prev[m] = d + w, (n, pts)
                    heapq.heappush(heap, (d + w, m))
        if dst != src and dst not in prev:
            return np.array([start, goal])
        legs, n = [], dst
        while n != src:
            n, pts = prev[n][0], prev[n][1]
            legs.append(pts)
        path = np.concatenate([start[None], *legs[::-1], goal[None]]) if legs else np.array([start, goal])
        return path[np.r_[True, np.any(np.diff(path, axis=0) != 0, axis=1)]]

    def offroad_distance(self, p) -> float:
        """Distance beyond the nearest road's half width (<= 0 = on a road); inf when the world has no roads."""
        best = math.inf
        for pts, width in self.roads:
            _, _, d = polyline_project(pts, p)
            best = min(best, d - width / 2)
        return best


def default_reward(env: "IsaacQuarryEnv", truck, prev: dict, now: dict, cfg: RewardConfig = RewardConfig()):
    info = {"progress_m": prev["remaining"] - now["remaining"], "remaining_m": now["remaining"]}
    r = cfg.progress * info["progress_m"] - cfg.time
    terminated = False
    if now["collision"]:
        r -= cfg.collision
        terminated = True
    if now["offroad"]:
        r -= cfg.offroad
    if now["wall"]:
        r -= cfg.wall
    if now["goal_dist"] <= env.arrive_radius:
        r += cfg.success
        terminated = True
        info["success"] = True
    return r, terminated, info


# --------------------------------------------------------------------------------------------------------------- env
class IsaacQuarryEnv(_Base):
    metadata = {"render_modes": [], "render_fps": CONTROL_HZ}

    def __init__(self, world_dir: str | Path | None = ROOT / "data/worlds/quarry", backend: str = "fake", truck_id: str | None = None,
                 keep_in_bounds: bool = False, reward_fn=None, reward_cfg: RewardConfig | None = None, control_hz: float = CONTROL_HZ,
                 substep: float = SUBSTEP, max_steps: int = MAX_STEPS, arrive_radius: float = ARRIVE_RADIUS, rgb: bool = True,
                 core=None, render=None) -> None:
        if core is None:
            core, render, self._close = make_core(world_dir, backend=backend, keep_in_bounds=keep_in_bounds, fps=control_hz)
        else:
            self._close = lambda: None
        self.core, self.render_fn = core, render
        self.reward_fn = reward_fn or (lambda env, t, prev, now: default_reward(env, t, prev, now, reward_cfg or RewardConfig()))
        self.control_dt, self.substep, self.max_steps = 1.0 / control_hz, substep, max_steps
        self.arrive_radius, self.use_rgb = arrive_radius, rgb
        self.graph = RoadGraph(core.world)
        self.spawns = {t.id: (t.x, t.z, t.yaw) for t in core.trucks}
        self.truck_id = truck_id or core.trucks[0].id
        self.rng = np.random.default_rng()
        self.goals: dict[str, dict] = {}
        self.steps = 0
        self._images: dict | None = None
        if _gym is not None:
            from gymnasium import spaces

            self.action_space = spaces.Box(np.array([0.0, -1.0], np.float32), np.array([1.0, 1.0], np.float32))
            self.observation_space = spaces.Dict({
                "rgb": spaces.Box(0, 255, (CAM_H, CAM_W, 3), np.uint8),
                "depth": spaces.Box(0, 100, (DEPTH_H, DEPTH_W), np.float32),
                "lidar": spaces.Box(-100, 100, (MAX_POINTS, 3), np.float32),
                "lidar_count": spaces.Discrete(MAX_POINTS + 1),
                "pose": spaces.Box(-np.inf, np.inf, (3,), np.float32),
                "speed": spaces.Box(0, 8, (1,), np.float32),
                "goal": spaces.Box(-np.inf, np.inf, (2,), np.float32),
            })

    # -- helpers
    def truck(self, truck_id: str | None = None):
        tid = truck_id or self.truck_id
        return next(t for t in self.core.trucks if t.id == tid)

    def _place(self, t, x: float, z: float, yaw: float) -> None:
        t.x, t.z, t.yaw, t.speed, t.throttle, t.steer, t.received = x, z, yaw % 360.0, 0.0, 0.0, 0.0, -math.inf
        f = self.core.follow[t.id] = GroundFollow(self.core.truck_size)
        f.update(t, self.core.probe, 0.0)

    def _resolve_goal(self, goal) -> tuple[np.ndarray, str | None]:
        if goal is None:
            ids = sorted(s.id for s in self.core.world.sites) or sorted(self.graph.nodes)
            if not ids:
                b = self.core.world.bounds
                return self.rng.uniform([b[0] * 0.8, b[1] * 0.8], [b[2] * 0.8, b[3] * 0.8]), None
            goal = ids[int(self.rng.integers(len(ids)))]
        if isinstance(goal, str):
            return self.graph.nodes[goal].copy(), goal
        return np.asarray(goal, float)[:2], None

    def _set_goal(self, t, goal) -> None:
        pos, gid = self._resolve_goal(goal)
        route = self.graph.route(np.array([t.x, t.z]), pos, gid)
        total, _, _ = polyline_project(route, route[-1])
        self.goals[t.id] = {"goal": pos, "goal_id": gid, "route": route, "total": total, "seg": 0}

    def _measure(self, t) -> dict:
        g = self.goals[t.id]
        p = np.array([t.x, t.z])
        s, seg, _ = polyline_project(g["route"], p, max(0, g["seg"] - 1))
        g["seg"] = seg
        half = (self.core.truck_size[0] / 2, self.core.truck_size[2] / 2)
        collision = any(obb_overlap(p, t.yaw, half, (o.x, o.z), o.yaw, (self.core.truck_size[0] / 2, self.core.truck_size[2] / 2))
                        for o in self.core.trucks if o is not t)
        collision |= any(obb_overlap(p, t.yaw, half, (b.x, b.z), b.yaw, (b.size[0] / 2, b.size[2] / 2)) for b in self.core.excavators)
        collision |= any(obb_overlap(p, t.yaw, half, (w.x, w.z), 0.0, (PEDESTRIAN_RADIUS, PEDESTRIAN_RADIUS)) for w in self.core.walkers)
        guard = getattr(t, "guard", None)
        wall = getattr(t, "guard_status", "ok") != "ok" if guard else False
        return {"remaining": g["total"] - s, "goal_dist": float(np.hypot(*(g["goal"] - p))), "collision": bool(collision),
                "offroad": self.graph.offroad_distance(p) > OFFROAD_SLACK if self.graph.roads else False, "wall": wall}

    def _obs(self, i: int, depths, lidars) -> dict:
        t = self.core.trucks[i]
        depth = depths[i] if depths[i] is not None else np.full((DEPTH_H, DEPTH_W), 100.0, np.float32)
        rgb = None
        if self.use_rgb:
            rgb = self._images.get(t.id) if self._images else None
            if rgb is None:
                rgb = shade_depth(depth)
        pts = lidars[i][:MAX_POINTS]
        lidar = np.zeros((MAX_POINTS, 3), np.float32)
        lidar[: len(pts)] = pts
        return {"rgb": rgb if rgb is not None else np.zeros((CAM_H, CAM_W, 3), np.uint8), "depth": depth.astype(np.float32),
                "lidar": lidar, "lidar_count": len(pts), "pose": np.array([t.x, t.z, t.yaw], np.float32),
                "speed": np.array([t.speed], np.float32), "goal": self.goals[t.id]["goal"].astype(np.float32)}

    def _sense(self):
        self.core.sync_obstacles()
        self._images = self.render_fn() if (self.render_fn and self.use_rgb) else None
        return self.core.depths(), self.core.lidars()

    def _physics(self) -> None:
        n = max(1, round(self.control_dt / self.substep))
        for _ in range(n):
            self.core.step(self.control_dt / n, self.core.time)

    # -- gym API
    def reset(self, seed: int | None = None, truck_id: str | None = None, goal=None, options: dict | None = None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        options = options or {}
        self.truck_id = truck_id or options.get("truck_id") or self.truck_id
        goal = goal if goal is not None else options.get("goal")
        for t in self.core.trucks:
            self._place(t, *self.spawns[t.id])
        self.steps = 0
        t = self.truck()
        self._set_goal(t, goal)
        self._prev = self._measure(t)
        depths, lidars = self._sense()
        i = self.core.trucks.index(t)
        return self._obs(i, depths, lidars), {"route": self.goals[t.id]["route"], "goal_id": self.goals[t.id]["goal_id"]}

    def step(self, action):
        t = self.truck()
        throttle, steer = (action["throttle"], action["steer"]) if isinstance(action, dict) else (float(action[0]), float(action[1]))
        t.command(throttle, steer, self.core.time)
        self._physics()
        self.steps += 1
        now = self._measure(t)
        reward, terminated, info = self.reward_fn(self, t, self._prev, now)
        self._prev = now
        truncated = not terminated and self.steps >= self.max_steps
        depths, lidars = self._sense()
        info.update({k: now[k] for k in ("collision", "offroad", "wall", "goal_dist")}, guard=getattr(t, "guard_status", "ok"))
        return self._obs(self.core.trucks.index(t), depths, lidars), float(reward), bool(terminated), bool(truncated), info

    def close(self) -> None:
        self._close()


class MultiTruckQuarryEnv(IsaacQuarryEnv):
    """All trucks of one world as agents: reset -> ({id: obs}, info); step({id: action}) -> dicts per truck."""

    def reset(self, seed: int | None = None, goals: dict | None = None, options: dict | None = None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        goals = goals or {}
        for t in self.core.trucks:
            self._place(t, *self.spawns[t.id])
        self.steps = 0
        self._prevs = {}
        for t in self.core.trucks:
            self._set_goal(t, goals.get(t.id))
            self._prevs[t.id] = self._measure(t)
        depths, lidars = self._sense()
        return {t.id: self._obs(i, depths, lidars) for i, t in enumerate(self.core.trucks)}, {}

    def step(self, actions: dict):
        for t in self.core.trucks:
            a = actions.get(t.id)
            if a is not None:
                throttle, steer = (a["throttle"], a["steer"]) if isinstance(a, dict) else (float(a[0]), float(a[1]))
                t.command(throttle, steer, self.core.time)
        self._physics()
        self.steps += 1
        depths, lidars = self._sense()
        obs, rew, term, trunc, infos = {}, {}, {}, {}, {}
        for i, t in enumerate(self.core.trucks):
            now = self._measure(t)
            rew[t.id], term[t.id], infos[t.id] = self.reward_fn(self, t, self._prevs[t.id], now)
            self._prevs[t.id] = now
            trunc[t.id] = not term[t.id] and self.steps >= self.max_steps
            obs[t.id] = self._obs(i, depths, lidars)
        return obs, rew, term, trunc, infos
