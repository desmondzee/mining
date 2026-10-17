"""Truck control for the Isaac sim: Unity TruckAgent + MineGroundFollow, plus an optional KeepInBounds guard.

Physics is NOT reimplemented here. The bicycle model, command timeout and ground follow live in
`isaac/sim/kinematics.py` (desmondzee's Isaac sim on main, byte-for-byte the TruckAgent constants). This module adds:

* `bicycle_step`: a pure reference copy of TruckAgent.FixedUpdate, used by the tests to pin `kinematics.Truck` to Unity.
* `TruckController`: a `kinematics.Truck` subclass with the KeepInBounds guard (world bounds margin, nav-grid walls,
  lateral-acceleration speed cap). With `guard=None` it behaves exactly like TruckAgent.
* `install_controllers(sim, ...)`: swaps the trucks of an `isaac.sim.sim.Sim` (or the fake core) for controllers, so the
  bridge, the RL env and the Isaac renderer all drive the same objects.
* Axis mapping helpers (Isaac stage is Z up, Isaac (X, Y, Z) = world (x, z, y); see spec "Isaac sim") and a USD prim pose
  writer plus a PhysX scene-query ground probe for stages that were not built by `isaac/sim/scene.py`.

Frames: world = Unity numbers (x right/east, y up, z forward/north, yaw 0 = +z, clockwise positive, degrees).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from isaac.sim import kinematics as K  # noqa: E402
from isaac.sim.kinematics import (  # noqa: E402,F401  (re-exported for callers)
    ACCEL,
    COMMAND_TIMEOUT,
    MAX_SPEED,
    MAX_STEER_DEG,
    WHEELBASE,
    GroundFollow,
    Truck,
    yaw_matrix,
)

# KeepInBounds (deliberate difference from Unity's TruckAgent, on for the demo, off by default in rl_env)
BOUNDS_MARGIN = 5.0          # m inside world.json bounds
FOOTPRINT_HALF_WIDTH = 2.12  # m, = brain planner TRUCK_HALF_W
FOOTPRINT_HALF_LENGTH = 4.7  # m, ~ Komatsu HD465 length 9.35 / 2
LATERAL_ACCEL = 1.5          # m/s^2 cap on v^2 / R
BRAKE_DECEL = 3.0            # m/s^2 used when the truck is above the curvature cap
SLIDE_SPEED_FACTOR = 0.5     # speed kept when sliding along a wall
MIN_SLIDE_FRACTION = 0.2     # a slide must keep at least this fraction of the step, else it is a stop

# Isaac stage: Z up, metres, Isaac (X, Y, Z) = world (x, z, y). Improper (det -1) on purpose: it turns the left-handed
# Unity numbers into a right-handed stage without mirroring the picture, so RGB matches Unity column for column.
WORLD_TO_ISAAC = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])


def bicycle_step(x: float, z: float, yaw: float, speed: float, throttle: float, steer: float, dt: float,
                 max_speed: float = MAX_SPEED, max_steer_deg: float = MAX_STEER_DEG, wheelbase: float = WHEELBASE,
                 accel: float = ACCEL) -> tuple[float, float, float, float]:
    """Reference TruckAgent.FixedUpdate: speed first, then yaw, then move along the NEW heading."""
    throttle = min(1.0, max(0.0, throttle))
    steer = min(1.0, max(-1.0, steer))
    speed += (throttle * max_speed - speed) * min(1.0, dt * accel)
    yaw_rate = speed / wheelbase * math.tan(math.radians(steer * max_steer_deg))
    yaw = (yaw + math.degrees(yaw_rate) * dt) % 360.0
    x += math.sin(math.radians(yaw)) * speed * dt
    z += math.cos(math.radians(yaw)) * speed * dt
    return x, z, yaw, speed


def turn_radius(steer: float, wheelbase: float = WHEELBASE, max_steer_deg: float = MAX_STEER_DEG) -> float:
    t = abs(math.tan(math.radians(steer * max_steer_deg)))
    return math.inf if t < 1e-9 else wheelbase / t


def curvature_speed_cap(steer: float, lateral_accel: float = LATERAL_ACCEL) -> float:
    """v <= sqrt(a * R), R = wheelbase / tan(steer * 30 deg)."""
    r = turn_radius(steer)
    return math.inf if math.isinf(r) else math.sqrt(lateral_accel * r)


# ---------------------------------------------------------------------------------------------------------------- nav grid
@dataclass
class NavGrid:
    """`data/worlds/<id>/navgrid.json`: row-major `cost[r * width + c]`, r along z, c along x, cost < 0 = impassable."""

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
        cost = np.asarray(raw["cost"], np.float32).reshape(h, w)
        return cls(w, h, float(raw["cell_m"]), float(raw["origin"][0]), float(raw["origin"][1]), cost)

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


class KeepInBounds:
    """Guard applied after each bicycle step (not in Unity; see README "Brain bridge", deliberate differences)."""

    def __init__(self, bounds: list[float], navgrid: NavGrid | None = None, margin: float = BOUNDS_MARGIN,
                 half_width: float = FOOTPRINT_HALF_WIDTH, half_length: float = FOOTPRINT_HALF_LENGTH,
                 lateral_accel: float | None = LATERAL_ACCEL) -> None:
        xmin, zmin, xmax, zmax = (float(v) for v in bounds)
        self.lo = np.array([xmin + margin, zmin + margin])
        self.hi = np.array([xmax - margin, zmax - margin])
        self.navgrid = navgrid
        self.half_width, self.half_length = half_width, half_length
        self.lateral_accel = lateral_accel

    @classmethod
    def for_world(cls, world_dir: str | Path, **kw) -> "KeepInBounds":
        world_dir = Path(world_dir)
        bounds = json.loads((world_dir / "world.json").read_text())["bounds"]
        nav = world_dir / "navgrid.json"
        return cls(bounds, NavGrid.load(nav) if nav.exists() else None, **kw)

    def footprint(self, x: float, z: float, yaw: float) -> np.ndarray:
        s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        fwd, right = np.array([s, c]), np.array([c, -s])
        hw, hl = self.half_width, self.half_length
        return np.array([[x, z]]) + np.array([fwd * a + right * b for a in (hl, -hl) for b in (hw, -hw)])

    def blocked_corners(self, x: float, z: float, yaw: float) -> int:
        pts = self.footprint(x, z, yaw)
        out = int(np.sum(np.any((pts < self.lo) | (pts > self.hi), axis=1)))
        if self.navgrid is not None:
            out = max(out, int(np.sum(~self.navgrid.passable(pts[:, 0], pts[:, 1]))))
        return out

    def clamp(self, x: float, z: float) -> tuple[float, float]:
        return float(np.clip(x, self.lo[0], self.hi[0])), float(np.clip(z, self.lo[1], self.hi[1]))

    def resolve(self, prev: tuple[float, float, float], nxt: tuple[float, float, float]) -> tuple[tuple[float, float, float], str]:
        """prev / nxt = (x, z, yaw). Returns the accepted pose and 'ok' | 'slide' | 'blocked'."""
        before = self.blocked_corners(*prev)

        def ok(p) -> bool:
            n = self.blocked_corners(*p)
            return n == 0 or (before > 0 and n <= before)  # already touching a wall: allow moves that don't add corners

        x, z = self.clamp(nxt[0], nxt[1])
        cand = (x, z, nxt[2])
        if ok(cand):
            return cand, "ok" if (x, z) == (nxt[0], nxt[1]) else "slide"
        dx, dz = x - prev[0], z - prev[1]
        step = math.hypot(dx, dz)
        for p in ((prev[0] + dx, prev[1] + dz, prev[2]),   # move but keep the heading
                  (prev[0] + dx, prev[1], prev[2]),        # slide along x
                  (prev[0], prev[1] + dz, prev[2])):       # slide along z
            p = (*self.clamp(p[0], p[1]), p[2])
            moved = math.hypot(p[0] - prev[0], p[1] - prev[1])
            if moved > max(1e-6, MIN_SLIDE_FRACTION * step) and ok(p):
                return p, "slide"
        return prev, "blocked"


class TruckController(K.Truck):
    """kinematics.Truck (TruckAgent) + optional KeepInBounds. Same attributes, so isaac.sim.sim.Sim can drive it."""

    def __init__(self, truck_id: str, x: float, z: float, yaw: float, y: float = 0.0, guard: KeepInBounds | None = None) -> None:
        super().__init__(truck_id, x, z, yaw, y)
        self.guard = guard
        self.guard_status = "ok"
        self.blocked_steps = 0

    @classmethod
    def from_truck(cls, t: K.Truck, guard: KeepInBounds | None = None) -> "TruckController":
        c = cls(t.id, t.x, t.z, t.yaw, t.y, guard)
        c.speed, c.throttle, c.steer, c.received = t.speed, t.throttle, t.steer, t.received
        return c

    def step(self, dt: float, now: float) -> None:
        if self.guard is None:
            super().step(dt, now)
            return
        live = now - self.received <= COMMAND_TIMEOUT
        throttle, steer = (self.throttle, self.steer) if live else (0.0, 0.0)
        speed = self.speed + (throttle * MAX_SPEED - self.speed) * min(1.0, dt * ACCEL)
        if self.guard.lateral_accel:
            cap = curvature_speed_cap(steer, self.guard.lateral_accel)
            if speed > cap:
                speed = max(cap, self.speed - BRAKE_DECEL * dt)
                if speed > cap:  # still braking: open the turn so v^2/R stays within the cap
                    steer = math.copysign(math.degrees(math.atan(WHEELBASE * self.guard.lateral_accel / speed**2)) / MAX_STEER_DEG, steer)
        yaw_rate = speed / WHEELBASE * math.tan(math.radians(steer * MAX_STEER_DEG))
        yaw = (self.yaw + math.degrees(yaw_rate) * dt) % 360.0
        x = self.x + math.sin(math.radians(yaw)) * speed * dt
        z = self.z + math.cos(math.radians(yaw)) * speed * dt
        (self.x, self.z, self.yaw), self.guard_status = self.guard.resolve((self.x, self.z, self.yaw), (x, z, yaw))
        if self.guard_status == "blocked":
            speed = 0.0
            self.blocked_steps += 1
        elif self.guard_status == "slide":
            speed *= SLIDE_SPEED_FACTOR
        self.speed = speed


def install_controllers(sim, keep_in_bounds: bool = True, guard: KeepInBounds | None = None) -> list[TruckController]:
    """Replace sim.trucks (kinematics.Truck) by TruckControllers in place; ground follow state is keyed by id and kept."""
    if keep_in_bounds and guard is None:
        guard = KeepInBounds.for_world(sim.world.dir)
    sim.trucks = [TruckController.from_truck(t, guard if keep_in_bounds else None) for t in sim.trucks]
    return sim.trucks


# ------------------------------------------------------------------------------------------------------------ Isaac side
def to_isaac(p) -> np.ndarray:
    return np.asarray(p, float)[..., [0, 2, 1]]


def from_isaac(p) -> np.ndarray:
    return np.asarray(p, float)[..., [0, 2, 1]]


def isaac_pose(rotation: np.ndarray, position: np.ndarray) -> np.ndarray:
    """4x4 column-vector pose in the Isaac stage from a world rotation/position (same as usdbuild.isaac_matrix)."""
    s = WORLD_TO_ISAAC
    m = np.eye(4)
    m[:3, :3] = s @ rotation @ s
    m[:3, 3] = s @ position
    return m


class PrimPoseWriter:
    """Writes truck poses as kinematic xforms onto USD prims (one transform op per prim). For stages built outside
    isaac/sim/scene.py (e.g. isaac/build_stage.py); scene.py's IsaacRenderer already does this for its own prims.
    The prim's local frame must be Unity-like after the axis swap: model forward = Isaac +Y, up = Isaac +Z."""

    def __init__(self, stage, paths: dict[str, str]) -> None:
        from pxr import UsdGeom  # lazy: Isaac only

        self.ops = {}
        for truck_id, path in paths.items():
            xf = UsdGeom.Xformable(stage.GetPrimAtPath(path))
            xf.ClearXformOpOrder()
            self.ops[truck_id] = xf.AddTransformOp()

    def write(self, sim) -> None:
        from pxr import Gf

        for t in sim.trucks:
            op = self.ops.get(t.id)
            if op is not None:
                op.Set(Gf.Matrix4d(isaac_pose(sim.rotation(t), sim.position(t)).T.tolist()))


def physx_probe(ground_prefixes: tuple[str, ...] = ("/World/Scan", "/World/Collider", "/World/Ground", "/World/Surround")):
    """GroundFollow-compatible probe (xs, zs, tops, lengths) -> ground y (nan = miss) using PhysX scene queries straight
    down, keeping only hits on the world's ground prims (like MineGroundFollow's Collider.Raycast on the scan collider).
    Slow per ray in Python; the default path in isaac/sim uses Warp `mesh_query_ray` instead."""
    from omni.physx import get_physx_scene_query_interface  # lazy: Isaac only

    sq = get_physx_scene_query_interface()

    def probe(xs, zs, tops, lengths) -> np.ndarray:
        out = np.full(len(xs), np.nan)
        for i, (x, z, top, length) in enumerate(zip(xs, zs, tops, lengths)):
            best = [math.inf]

            def report(hit, best=best) -> bool:
                if str(hit.collision).startswith(ground_prefixes) and hit.distance < best[0]:
                    best[0] = hit.distance
                return True

            sq.raycast_all((float(x), float(z), float(top)), (0.0, 0.0, -1.0), float(length), report)
            if math.isfinite(best[0]):
                out[i] = top - best[0]
        return out

    return probe
