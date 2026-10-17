"""Gymnasium-style RL environment on top of isaac/sim (no physics or sensor code of its own).

Everything physical comes from isaac/sim: `Sim` (world.json loader, truck spawn, `kinematics.Truck` bicycle model +
`GroundFollow`, pedestrians, excavator boxes), `raycast.Caster` (Warp depth / lidar / ground rays) and `sensors.Rig`
(Unity ray patterns and mounts). This module only adds the RL loop: goals and routes on the road graph, reward,
collision / off-road checks, observation packing, an optional KeepInBounds wrapper and a multi-truck variant.

    from isaac.rl_env import QuarryEnv, KeepInBounds
    env = QuarryEnv("data/worlds/quarry", render="headless")        # Warp sensors only, no RTX: fast
    env = KeepInBounds(env)                                          # optional: penalty (default) or mode="clamp"
    obs, info = env.reset(seed=0, options={"goal": "crusher"})    # goal: site/junction id, (x, z) or None = random
    obs, reward, terminated, truncated, info = env.step({"throttle": 0.6, "steer": 0.0})   # or np.array([0.6, 0.0])

render = "headless" (no RGB: zeros, rgb_valid = 0), "dry" (isaac/sim main.py DryRenderer, black frames), "isaac"
(starts SimulationApp + isaac/sim/scene.py IsaacRenderer: RTX RGB; run under Isaac's python.sh), or any object with
update(sim, dt) and render() -> {camera: HxWx3 uint8} (tests pass a mock).
raycast = "auto" (Warp if importable), True, False (heightmap ground, no depth/lidar: depth_valid = 0).

obs = {rgb (360, 640, 3) uint8, rgb_valid 0/1, depth (36, 64) float32 m (100 = miss), depth_valid 0/1,
       lidar (7200, 3) float32 sensor-local m zero padded, lidar_count, pose [x, z, yaw], speed [m/s], goal [x, z]}
action = {throttle 0..1, steer -1..1 (positive = right)} or [throttle, steer]. Control 15 Hz, physics substeps 1/30 s
(isaac/sim/main.py SUBSTEP). Reward is pluggable: reward_fn(env, truck, prev, now) -> (reward, terminated, info).
Default: progress along the road-graph route, time cost, collision (terminate) and off-road penalties, success
within 4 m (brain policy ARRIVE_RADIUS).

`MultiTruckEnv` steps every truck of one world at once ({id: action} -> {id: obs}, ...): one physics + one batched
Warp cast serves N agents, the cheap vectorised option. `excavators=True` animates the excavators
(isaac/excavators.py) so depth / lidar see the swinging arms.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from isaac.sim.kinematics import GroundFollow  # noqa: E402
from isaac.sim.pedestrians import PEDESTRIAN_RADIUS, make_walkers  # noqa: E402
from isaac.sim.sensors import (  # noqa: E402
    CAM_H,
    CAM_W,
    DEPTH_H,
    DEPTH_RANGE,
    DEPTH_W,
    LIDAR_CHANNELS,
    LIDAR_COLS,
    LIDAR_RANGE,
    depth_image,
    lidar_points,
)
from isaac.sim.sim import Sim  # noqa: E402

try:  # optional
    import gymnasium as _gym

    _Base = _gym.Env
except ImportError:  # plain class with the same API
    _gym = None
    _Base = object

QUARRY = ROOT / "data/worlds/quarry"
CONFIG = ROOT / "isaac/sim/config.json"
ARRIVE_RADIUS = 4.0       # brain/brain/policy.py ARRIVE_RADIUS
CONTROL_HZ = 15.0
SUBSTEP = 1 / 30          # isaac/sim/main.py SUBSTEP
MAX_STEPS = 15 * 300      # 5 min of sim time
MAX_POINTS = LIDAR_COLS * LIDAR_CHANNELS
OFFROAD_SLACK = 1.0       # m beyond width/2 before off-road counts
SPAWN_TRIES = 20

# KeepInBounds defaults (not in Unity's TruckAgent; brain planner TRUCK_HALF_W, HD465 length 9.35 / 2)
BOUNDS_MARGIN = 5.0
FOOTPRINT_HALF_WIDTH = 2.12
FOOTPRINT_HALF_LENGTH = 4.7
SLIDE_SPEED_FACTOR = 0.5
MIN_SLIDE_FRACTION = 0.2
GUARD_RANK = {"ok": 0, "slide": 1, "blocked": 2}


@dataclass
class RewardConfig:
    progress: float = 1.0      # per metre of route progress
    time: float = 0.01         # per step
    collision: float = 10.0    # and terminate
    offroad: float = 0.1       # per step
    success: float = 100.0     # and terminate


# ------------------------------------------------------------------------------------------------------- geometry
def polyline_project(route: np.ndarray, p: np.ndarray, start_seg: int = 0) -> tuple[float, int, float]:
    """(arc length of the closest point, its segment, distance) searching segments from start_seg on."""
    a, b = route[start_seg:-1], route[start_seg + 1 :]
    ab = b - a
    l2 = np.maximum((ab**2).sum(1), 1e-12)
    t = np.clip(((p - a) * ab).sum(1) / l2, 0, 1)
    q = a + ab * t[:, None]
    d = np.hypot(*(q - p).T)
    i = int(np.argmin(d))
    cum = np.concatenate([[0], np.cumsum(np.sqrt((np.diff(route, axis=0) ** 2).sum(1)))])
    return float(cum[start_seg + i] + t[i] * math.sqrt(l2[i])), start_seg + i, float(d[i])


def obb_overlap(c1, yaw1, half1, c2, yaw2, half2) -> bool:
    """2D separating-axis test of two yawed rectangles (half = (half_width_x, half_length_z))."""
    def axes(yaw):
        s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        return np.array([[c, -s], [s, c]])  # right, forward
    a, b, d = axes(yaw1), axes(yaw2), np.asarray(c2, float) - np.asarray(c1, float)
    for ax in (*a, *b):
        r1 = abs(a[0] @ ax) * half1[0] + abs(a[1] @ ax) * half1[1]
        r2 = abs(b[0] @ ax) * half2[0] + abs(b[1] @ ax) * half2[1]
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
            self.roads.append((pts, r.width_m, r.one_way))

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
        for pts, width, _ in self.roads:
            _, _, d = polyline_project(pts, p)
            best = min(best, d - width / 2)
        return best


def default_reward(env, truck, prev: dict, now: dict, cfg: RewardConfig = RewardConfig()):
    info = {"progress_m": prev["remaining"] - now["remaining"], "remaining_m": now["remaining"]}
    r = cfg.progress * info["progress_m"] - cfg.time
    terminated = False
    if now["collision"]:
        r -= cfg.collision
        terminated = True
    if now["offroad"]:
        r -= cfg.offroad
    if now["goal_dist"] <= env.arrive_radius:
        r += cfg.success
        terminated = True
        info["success"] = True
    return r, terminated, info


# ------------------------------------------------------------------------------------------------------ renderers
def make_renderer(kind, sim, cache: str | Path | None = None, config: str | Path | dict | None = None):
    """'headless' -> None, 'dry' -> isaac/sim main.py DryRenderer, 'isaac' -> SimulationApp + IsaacRenderer, or the
    object itself. Returns (renderer, close_fn)."""
    if kind in (None, "headless"):
        return None, lambda: None
    if kind == "dry":
        from isaac.sim.main import DryRenderer

        return DryRenderer(sim), lambda: None
    if kind == "isaac":
        cfg = config if isinstance(config, dict) else json.loads(Path(config or CONFIG).read_text())
        cfg = {**cfg, "views": []}
        from isaacsim import SimulationApp

        app = SimulationApp({"headless": True, "renderer": cfg.get("renderer", "RealTimePathTracing"), "width": CAM_W, "height": CAM_H})
        from isaac.sim.scene import IsaacRenderer

        renderer = IsaacRenderer(app, sim, argparse.Namespace(cache=str(cache or ROOT / "downloads/isaac/cache"), fx="off"), cfg)
        return renderer, app.close
    return kind, lambda: None


def raycast_flag(raycast) -> bool:
    if raycast == "auto":
        from isaac.sim.main import raycast_enabled

        return raycast_enabled("auto")
    return bool(raycast)


# --------------------------------------------------------------------------------------------------------------- env
class QuarryEnv(_Base):
    metadata = {"render_modes": [], "render_fps": CONTROL_HZ}

    def __init__(self, world_dir: str | Path = QUARRY, truck_id: str | None = None, render="headless", raycast="auto",
                 reward_fn=None, reward_cfg: RewardConfig | None = None, control_hz: float = CONTROL_HZ,
                 substep: float = SUBSTEP, max_steps: int = MAX_STEPS, arrive_radius: float = ARRIVE_RADIUS,
                 spawn: str = "default", excavators: bool = False, radio_url: str = "", cache: str | Path | None = None,
                 config: str | Path | dict | None = None, sim: Sim | None = None) -> None:
        self.sim = sim or Sim(world_dir, raycast=raycast_flag(raycast), views=[])
        self.excavators = None
        if excavators:
            from isaac.excavators import install

            self.excavators = install(self.sim, None, radio_url, cache=cache)
        self.renderer, self._close = make_renderer(render, self.sim, cache, config)
        if self.excavators is not None and self.renderer is not None and getattr(self.renderer, "stage", None) is not None:
            self.excavators.build_usd(self.renderer.stage, cache or ROOT / "downloads/isaac/cache")
        cfg = reward_cfg or RewardConfig()
        self.reward_fn = reward_fn or (lambda env, t, prev, now: default_reward(env, t, prev, now, cfg))
        self.control_dt, self.substep, self.max_steps = 1.0 / control_hz, substep, max_steps
        self.arrive_radius, self.spawn_mode = arrive_radius, spawn
        self.graph = RoadGraph(self.sim.world)
        self.spawns = {t.id: (t.x, t.z, t.yaw) for t in self.sim.trucks}
        self.truck_id = truck_id or self.sim.trucks[0].id
        self.rng = np.random.default_rng()
        self.goals: dict[str, dict] = {}
        self.substep_hooks: list = []     # fn(env, prev_poses {id: (x, z, yaw)}) after every physics substep
        self.steps = 0
        self._images: dict = {}
        if _gym is not None:
            from gymnasium import spaces

            self.action_space = spaces.Box(np.array([0.0, -1.0], np.float32), np.array([1.0, 1.0], np.float32))
            self.observation_space = spaces.Dict({
                "rgb": spaces.Box(0, 255, (CAM_H, CAM_W, 3), np.uint8),
                "rgb_valid": spaces.Discrete(2),
                "depth": spaces.Box(0, DEPTH_RANGE, (DEPTH_H, DEPTH_W), np.float32),
                "depth_valid": spaces.Discrete(2),
                "lidar": spaces.Box(-LIDAR_RANGE, LIDAR_RANGE, (MAX_POINTS, 3), np.float32),
                "lidar_count": spaces.Discrete(MAX_POINTS + 1),
                "pose": spaces.Box(-np.inf, np.inf, (3,), np.float32),
                "speed": spaces.Box(0, 8, (1,), np.float32),
                "goal": spaces.Box(-np.inf, np.inf, (2,), np.float32),
            })

    # -- helpers
    def agent_ids(self) -> list[str]:
        return [self.truck_id]

    def truck(self, truck_id: str | None = None):
        tid = truck_id or self.truck_id
        return next(t for t in self.sim.trucks if t.id == tid)

    def place(self, t, x: float, z: float, yaw: float) -> None:
        t.x, t.z, t.yaw, t.speed, t.throttle, t.steer, t.received = x, z, yaw % 360.0, 0.0, 0.0, 0.0, -math.inf
        t.y = self.sim.height(x, z)
        f = self.sim.follow[t.id] = GroundFollow(self.sim.truck_size)
        f.update(t, self.sim.probe, 0.0)

    def random_road_pose(self, t) -> tuple[float, float, float] | None:
        """Random pose on a road centreline (10-90 % of its length), heading along it (either way if two-way)."""
        if not self.graph.roads:
            return None
        half = (self.sim.truck_size[0] / 2, self.sim.truck_size[2] / 2)
        for _ in range(SPAWN_TRIES):
            pts, _, one_way = self.graph.roads[int(self.rng.integers(len(self.graph.roads)))]
            seg = np.diff(pts, axis=0)
            cum = np.concatenate([[0], np.cumsum(np.hypot(*seg.T))])
            s = self.rng.uniform(0.1, 0.9) * cum[-1]
            i = min(int(np.searchsorted(cum, s, side="right")) - 1, len(seg) - 1)
            p = pts[i] + seg[i] * (s - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
            yaw = math.degrees(math.atan2(seg[i][0], seg[i][1])) + (180.0 if not one_way and self.rng.random() < 0.5 else 0.0)
            if not any(obb_overlap(p, yaw, half, (o.x, o.z), o.yaw, half) for o in self.sim.trucks if o is not t):
                return float(p[0]), float(p[1]), yaw % 360.0
        return None

    def resolve_goal(self, goal) -> tuple[np.ndarray, str | None]:
        if goal is None:
            ids = sorted(s.id for s in self.sim.world.sites) or sorted(self.graph.nodes)
            if not ids:
                b = self.sim.world.bounds
                return self.rng.uniform([b[0] * 0.8, b[1] * 0.8], [b[2] * 0.8, b[3] * 0.8]), None
            goal = ids[int(self.rng.integers(len(ids)))]
        if isinstance(goal, str):
            return self.graph.nodes[goal].copy(), goal
        return np.asarray(goal, float)[:2], None

    def set_goal(self, t, goal) -> None:
        pos, gid = self.resolve_goal(goal)
        route = self.graph.route(np.array([t.x, t.z]), pos, gid)
        total, _, _ = polyline_project(route, route[-1])
        self.goals[t.id] = {"goal": pos, "goal_id": gid, "route": route, "total": total, "seg": 0}

    def measure(self, t) -> dict:
        g = self.goals[t.id]
        p = np.array([t.x, t.z])
        s, seg, _ = polyline_project(g["route"], p, max(0, g["seg"] - 1))
        g["seg"] = seg
        half = (self.sim.truck_size[0] / 2, self.sim.truck_size[2] / 2)
        collision = any(obb_overlap(p, t.yaw, half, (o.x, o.z), o.yaw, half) for o in self.sim.trucks if o is not t)
        collision |= any(obb_overlap(p, t.yaw, half, (b.x, b.z), b.yaw, (b.size[0] / 2, b.size[2] / 2)) for b in self.sim.excavators)
        collision |= any(obb_overlap(p, t.yaw, half, (w.x, w.z), 0.0, (PEDESTRIAN_RADIUS, PEDESTRIAN_RADIUS)) for w in self.sim.walkers)
        return {"remaining": g["total"] - s, "goal_dist": float(np.hypot(*(g["goal"] - p))), "collision": bool(collision),
                "offroad": self.graph.offroad_distance(p) > OFFROAD_SLACK if self.graph.roads else False}

    def sense(self, indices: list[int]) -> tuple[list, list]:
        """Depth + lidar for the given trucks through isaac/sim's Warp caster (one batched cast)."""
        sim = self.sim
        sim.sync_obstacles()
        if self.renderer is not None:
            self.renderer.update(sim, self.control_dt)
            self._images = self.renderer.render()
        if sim.caster is None:
            return [None] * len(indices), [np.zeros((0, 3), np.float32)] * len(indices)
        if len(indices) == len(sim.trucks):
            return sim.depths(), sim.lidars()
        origins, dirs, ignore, sizes = [], [], [], []
        for i in indices:
            t = sim.trucks[i]
            pos, rot = sim.position(t), sim.rotation(t)
            for rays in (sim.rig.depth_rays, sim.rig.lidar_rays):
                o, d = rays(pos, rot)
                origins.append(o)
                dirs.append(d)
                ignore.append(np.full(len(d), i))
                sizes.append(len(d))
        dist = sim.caster.cast(np.concatenate(origins), np.concatenate(dirs), max(DEPTH_RANGE, LIDAR_RANGE), np.concatenate(ignore))
        parts = np.split(dist, np.cumsum(sizes)[:-1])
        depths, lidars = [], []
        for k in range(len(indices)):
            d, li = parts[2 * k].copy(), parts[2 * k + 1].copy()
            d[d > DEPTH_RANGE] = np.inf
            li[li > LIDAR_RANGE] = np.inf
            depths.append(depth_image(d, sim.rig.depth_dirs))
            lidars.append(lidar_points(li, sim.rig.lidar_dirs))
        return depths, lidars

    def observe(self, t, depth, lidar) -> dict:
        rgb = self._images.get(t.id) if self._images else None
        valid = rgb is not None and getattr(rgb, "shape", None) == (CAM_H, CAM_W, 3)
        pts = np.asarray(lidar, np.float32)[:MAX_POINTS]
        padded = np.zeros((MAX_POINTS, 3), np.float32)
        padded[: len(pts)] = pts
        return {
            "rgb": np.asarray(rgb, np.uint8) if valid else np.zeros((CAM_H, CAM_W, 3), np.uint8),
            "rgb_valid": int(valid),
            "depth": (depth if depth is not None else np.full((DEPTH_H, DEPTH_W), DEPTH_RANGE)).astype(np.float32),
            "depth_valid": int(depth is not None),
            "lidar": padded,
            "lidar_count": len(pts),
            "pose": np.array([t.x, t.z, t.yaw], np.float32),
            "speed": np.array([t.speed], np.float32),
            "goal": self.goals[t.id]["goal"].astype(np.float32),
        }

    def physics(self) -> None:
        n = max(1, round(self.control_dt / self.substep))
        dt = self.control_dt / n
        for _ in range(n):
            prev = {t.id: (t.x, t.z, t.yaw) for t in self.sim.trucks}
            self.sim.step(dt, self.sim.time + dt)
            for hook in self.substep_hooks:
                hook(self, prev)

    def command(self, t, action) -> None:
        throttle, steer = (action["throttle"], action["steer"]) if isinstance(action, dict) else (float(action[0]), float(action[1]))
        t.command(throttle, steer, self.sim.time)

    def reset_world(self, seed: int | None) -> None:
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        for t in self.sim.trucks:
            self.place(t, *self.spawns[t.id])
        self.sim.walkers = make_walkers(self.sim.paths)
        for w in self.sim.walkers:
            w.y = self.sim.height(w.x, w.z)
        self.steps = 0

    def spawn(self, t, options: dict) -> None:
        pose = options.get("spawn_pose")
        if pose is None and options.get("spawn", self.spawn_mode) == "road":
            pose = self.random_road_pose(t)
        if pose is not None:
            self.place(t, *pose)

    # -- gym API
    def reset(self, seed: int | None = None, options: dict | None = None):
        options = options or {}
        self.reset_world(seed)
        self.truck_id = options.get("truck_id") or self.truck_id
        t = self.truck()
        self.spawn(t, options)
        self.set_goal(t, options.get("goal"))
        self._prev = self.measure(t)
        i = self.sim.trucks.index(t)
        (depth,), (lidar,) = self.sense([i])
        return self.observe(t, depth, lidar), {"route": self.goals[t.id]["route"], "goal_id": self.goals[t.id]["goal_id"]}

    def step(self, action):
        t = self.truck()
        self.command(t, action)
        self.physics()
        self.steps += 1
        now = self.measure(t)
        reward, terminated, info = self.reward_fn(self, t, self._prev, now)
        self._prev = now
        truncated = not terminated and self.steps >= self.max_steps
        (depth,), (lidar,) = self.sense([self.sim.trucks.index(t)])
        info.update({k: now[k] for k in ("collision", "offroad", "goal_dist")})
        return self.observe(t, depth, lidar), float(reward), bool(terminated), bool(truncated), info

    def close(self) -> None:
        for link in getattr(self.excavators, "links", []):
            link.stop()
        self._close()


class MultiTruckEnv(QuarryEnv):
    """All trucks of one world as agents: reset -> ({id: obs}, {id: info}); step({id: action}) -> dicts per truck."""

    def agent_ids(self) -> list[str]:
        return [t.id for t in self.sim.trucks]

    def reset(self, seed: int | None = None, options: dict | None = None):
        options = options or {}
        self.reset_world(seed)
        goals = options.get("goals") or {}
        self._prevs = {}
        for t in self.sim.trucks:
            self.spawn(t, {"spawn": options.get("spawn", self.spawn_mode)})
            self.set_goal(t, goals.get(t.id))
            self._prevs[t.id] = self.measure(t)
        depths, lidars = self.sense(list(range(len(self.sim.trucks))))
        obs = {t.id: self.observe(t, depths[i], lidars[i]) for i, t in enumerate(self.sim.trucks)}
        return obs, {t.id: {"route": self.goals[t.id]["route"], "goal_id": self.goals[t.id]["goal_id"]} for t in self.sim.trucks}

    def step(self, actions: dict):
        for t in self.sim.trucks:
            if actions.get(t.id) is not None:
                self.command(t, actions[t.id])
        self.physics()
        self.steps += 1
        depths, lidars = self.sense(list(range(len(self.sim.trucks))))
        obs, rew, term, trunc, infos = {}, {}, {}, {}, {}
        for i, t in enumerate(self.sim.trucks):
            now = self.measure(t)
            r, term[t.id], infos[t.id] = self.reward_fn(self, t, self._prevs[t.id], now)
            rew[t.id] = float(r)
            infos[t.id].update({k: now[k] for k in ("collision", "offroad", "goal_dist")})
            self._prevs[t.id] = now
            trunc[t.id] = not term[t.id] and self.steps >= self.max_steps
            obs[t.id] = self.observe(t, depths[i], lidars[i])
        return obs, rew, term, trunc, infos


# ----------------------------------------------------------------------------------------------------- KeepInBounds
@dataclass
class NavGrid:
    """data/worlds/<id>/navgrid.json: row-major cost[r * width + c], r along z, c along x, cost < 0 = impassable."""

    width: int
    height: int
    cell: float
    ox: float
    oz: float
    cost: np.ndarray  # (height, width)

    @classmethod
    def load(cls, path: str | Path) -> "NavGrid":
        raw = json.loads(Path(path).read_text())
        w, h = int(raw["width"]), int(raw["height"])
        return cls(w, h, float(raw["cell_m"]), float(raw["origin"][0]), float(raw["origin"][1]), np.asarray(raw["cost"], np.float32).reshape(h, w))

    @classmethod
    def from_mask(cls, passable: np.ndarray, cell: float, ox: float, oz: float) -> "NavGrid":
        return cls(passable.shape[1], passable.shape[0], cell, ox, oz, np.where(passable, 1.0, -1.0).astype(np.float32))

    def passable(self, xs, zs) -> np.ndarray:
        c = np.floor((np.asarray(xs, float) - self.ox) / self.cell).astype(int)
        r = np.floor((np.asarray(zs, float) - self.oz) / self.cell).astype(int)
        inside = (r >= 0) & (c >= 0) & (r < self.height) & (c < self.width)
        out = np.zeros(np.shape(c), bool)
        out[inside] = self.cost[r[inside], c[inside]] >= 0
        return out


class BoundsGuard:
    """Truck footprint vs world.json bounds (minus a margin) and the nav grid's impassable cells."""

    def __init__(self, bounds, navgrid: NavGrid | None = None, margin: float = BOUNDS_MARGIN,
                 half_width: float = FOOTPRINT_HALF_WIDTH, half_length: float = FOOTPRINT_HALF_LENGTH) -> None:
        xmin, zmin, xmax, zmax = (float(v) for v in bounds)
        self.lo = np.array([xmin + margin, zmin + margin])
        self.hi = np.array([xmax - margin, zmax - margin])
        self.navgrid, self.half_width, self.half_length = navgrid, half_width, half_length

    @classmethod
    def for_world(cls, world, **kw) -> "BoundsGuard":
        nav = world.dir / "navgrid.json"
        return cls(world.bounds, NavGrid.load(nav) if nav.exists() else None, **kw)

    def footprint(self, x: float, z: float, yaw: float) -> np.ndarray:
        s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        fwd, right = np.array([s, c]), np.array([c, -s])
        return np.array([[x, z]]) + np.array([fwd * a + right * b for a in (self.half_length, -self.half_length) for b in (self.half_width, -self.half_width)])

    def blocked_corners(self, x: float, z: float, yaw: float) -> int:
        pts = self.footprint(x, z, yaw)
        out = int(np.sum(np.any((pts < self.lo) | (pts > self.hi), axis=1)))
        if self.navgrid is not None:
            out = max(out, int(np.sum(~self.navgrid.passable(pts[:, 0], pts[:, 1]))))
        return out

    def clamp(self, x: float, z: float) -> tuple[float, float]:
        return float(np.clip(x, self.lo[0], self.hi[0])), float(np.clip(z, self.lo[1], self.hi[1]))

    def resolve(self, prev: tuple[float, float, float], nxt: tuple[float, float, float]) -> tuple[tuple[float, float, float], str]:
        """prev / nxt = (x, z, yaw) -> accepted pose and 'ok' | 'slide' | 'blocked'."""
        before = self.blocked_corners(*prev)

        def ok(p) -> bool:
            n = self.blocked_corners(*p)
            return n == 0 or (before > 0 and n <= before)  # already touching: allow moves that add no corners

        x, z = self.clamp(nxt[0], nxt[1])
        if ok((x, z, nxt[2])):
            return (x, z, nxt[2]), "ok" if (x, z) == (nxt[0], nxt[1]) else "slide"
        dx, dz = x - prev[0], z - prev[1]
        step = math.hypot(dx, dz)
        for p in ((prev[0] + dx, prev[1] + dz, prev[2]), (prev[0] + dx, prev[1], prev[2]), (prev[0], prev[1] + dz, prev[2])):
            p = (*self.clamp(p[0], p[1]), p[2])
            if math.hypot(p[0] - prev[0], p[1] - prev[1]) > max(1e-6, MIN_SLIDE_FRACTION * step) and ok(p):
                return p, "slide"
        return prev, "blocked"


class KeepInBounds:
    """Optional wrapper. mode="penalty" (default for RL): dynamics untouched, `penalty` per step while any footprint
    corner is outside the bounds margin / on an impassable nav cell (info["out_of_bounds"] = corners).
    mode="clamp": hard guard after every physics substep (slide along the wall at half speed, or stop), and the same
    penalty while it acts (info["guard"] = ok | slide | blocked). `terminate_after` ends an episode after N steps out."""

    def __init__(self, env, mode: str = "penalty", penalty: float = 0.5, guard: BoundsGuard | None = None,
                 terminate_after: int | None = None) -> None:
        if mode not in ("penalty", "clamp"):
            raise ValueError(f"mode must be 'penalty' or 'clamp', not {mode!r}")
        self.env, self.mode, self.penalty, self.terminate_after = env, mode, penalty, terminate_after
        self.guard = guard or BoundsGuard.for_world(env.sim.world)
        self.status: dict[str, str] = {}
        self.out_steps: dict[str, int] = {}
        if mode == "clamp":
            env.substep_hooks.append(self._clamp)

    def __getattr__(self, name):
        return getattr(self.env, name)

    def _clamp(self, env, prev: dict) -> None:
        for t in env.sim.trucks:
            if t.id not in env.agent_ids():
                continue
            (t.x, t.z, t.yaw), st = self.guard.resolve(prev[t.id], (t.x, t.z, t.yaw))
            if st == "blocked":
                t.speed = 0.0
            elif st == "slide":
                t.speed *= SLIDE_SPEED_FACTOR
            if GUARD_RANK[st] > GUARD_RANK[self.status.get(t.id, "ok")]:
                self.status[t.id] = st

    def _judge(self, tid: str, reward: float, terminated: bool, info: dict) -> tuple[float, bool]:
        t = self.env.truck(tid)
        corners = self.guard.blocked_corners(t.x, t.z, t.yaw)
        status = self.status.pop(tid, "ok")
        hit = corners > 0 or status != "ok"
        info.update(out_of_bounds=corners, guard=status if self.mode == "clamp" else ("out" if corners else "ok"))
        self.out_steps[tid] = self.out_steps.get(tid, 0) + 1 if hit else 0
        if hit:
            reward -= self.penalty
        if self.terminate_after and self.out_steps[tid] >= self.terminate_after:
            terminated = True
            info["out_of_bounds_terminated"] = True
        return reward, terminated

    def reset(self, *a, **kw):
        self.status.clear()
        self.out_steps.clear()
        return self.env.reset(*a, **kw)

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        if isinstance(reward, dict):
            for tid in reward:
                reward[tid], terminated[tid] = self._judge(tid, reward[tid], terminated[tid], info[tid])
            return obs, reward, terminated, truncated, info
        reward, terminated = self._judge(self.env.truck_id, reward, terminated, info)
        return obs, float(reward), bool(terminated), truncated, info

    def close(self) -> None:
        self.env.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="random-policy rollout (speed check)")
    p.add_argument("--world", default=str(QUARRY))
    p.add_argument("--render", default="headless", choices=["headless", "dry", "isaac"])
    p.add_argument("--steps", type=int, default=150)
    p.add_argument("--excavators", action="store_true")
    a = p.parse_args()
    import time

    env = KeepInBounds(QuarryEnv(a.world, render=a.render, excavators=a.excavators, spawn="road"))
    obs, info = env.reset(seed=0)
    t0, total = time.time(), 0.0
    for _ in range(a.steps):
        obs, r, term, trunc, info = env.step(np.array([0.6, np.random.uniform(-0.3, 0.3)]))
        total += r
        if term or trunc:
            obs, info = env.reset()
    el = time.time() - t0
    print(f"{a.steps} steps in {el:.1f}s ({a.steps / el:.0f} steps/s), return {total:.1f}, lidar {obs['lidar_count']}, "
          f"depth_valid {obs['depth_valid']}, rgb_valid {obs['rgb_valid']}")
    env.close()
