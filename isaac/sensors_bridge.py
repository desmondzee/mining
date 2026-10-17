"""Truck sensors -> brain messages (frame 15 Hz, lidar 10 Hz, actors 10 Hz), and the cores that produce them.

Sensor semantics (spec "Cameras" / "Lidar", SensorCamera.cs / LidarSensor.cs):
* RGB: RTX camera 640 x 360, hFOV 90, Replicator `rgb` annotator (row 0 = top -> sent as JPEG q80, flip=false;
  or raw RGBA bottom-up with flip=true like Unity's raw path).
* Depth: 64 x 36 float32, perpendicular metres, row 0 = top, miss = 100. Raycast grid through the pixel-cell centres,
  like Unity's RaycastCommand grid (NOT the RTX depth annotator, which would need resampling and sees the debug layer).
* Lidar: 240 x 30 beams, cell-centre angles az = -60 + (j + 0.5) * 0.5, el = -20 + (i + 0.5) * 1.0 deg, range 100 m,
  misses dropped, points in sensor-local Unity axes (x right, y up, z forward), int16 cm on the wire.
The ray patterns, mounts and the Warp caster are isaac/sim/sensors.py + raycast.py (shared with isaac/sim/main.py);
this module only assembles messages, adds a numpy caster + `FakeCore` (no Isaac, no Warp, no GLBs) for tests / RL, and
`make_core` to build either backend.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from isaac.brain_bridge import frame_message, lidar_message  # noqa: E402
from isaac.sim import world as W  # noqa: E402
from isaac.sim.kinematics import GroundFollow, Truck  # noqa: E402
from isaac.sim.pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS, make_walkers  # noqa: E402
from isaac.sim.sensors import (  # noqa: E402,F401
    CAM_H,
    CAM_W,
    DEPTH_H,
    DEPTH_RANGE,
    DEPTH_W,
    HFOV,
    LIDAR_CHANNELS,
    LIDAR_COLS,
    LIDAR_HFOV,
    LIDAR_RANGE,
    LIDAR_VMAX,
    LIDAR_VMIN,
    Rig,
    cam_field,
    lidar_directions,
)
from isaac.sim.sim import Sim  # noqa: E402

DEFAULT_TRUCK_SIZE = (4.24, 4.4, 9.35)   # Komatsu HD465 (width, height, length) when the GLB is not on disk
DEFAULT_EXCAVATOR_SIZE = (4.5, 4.0, 10.0)
FLAT_HALF = 100.0                         # fakesim flat world
SKY = np.array([150, 180, 215], np.uint8)
GROUND = np.array([140, 120, 94], np.uint8)  # spec ground colour (0.55, 0.47, 0.37)


def lidar_angles() -> tuple[np.ndarray, np.ndarray]:
    """(azimuth, elevation) in degrees, spec formula; directions = isaac.sim.sensors.lidar_directions (channel-major)."""
    az = -LIDAR_HFOV / 2 + (np.arange(LIDAR_COLS) + 0.5) * LIDAR_HFOV / LIDAR_COLS
    el = LIDAR_VMIN + (np.arange(LIDAR_CHANNELS) + 0.5) * (LIDAR_VMAX - LIDAR_VMIN) / LIDAR_CHANNELS
    return az, el


# ---------------------------------------------------------------------------------------------------- numpy caster
class NumpyCaster:
    """Drop-in for isaac.sim.raycast.Caster without Warp: flat ground plane + oriented boxes + vertical cylinders."""

    def __init__(self, ground_y: float = 0.0, bounds: list[float] | None = None, wall_height: float = 15.0) -> None:
        self.ground_y = float(ground_y)
        self.bounds = bounds
        self.wall_height = wall_height
        self.set_boxes(np.zeros((0, 3)), np.zeros((0, 3, 3)), np.zeros((0, 3)))
        self.set_cylinders(np.zeros((0, 3)), PEDESTRIAN_RADIUS, PEDESTRIAN_HEIGHT)

    def set_boxes(self, centres, rotations, half) -> None:
        self.box_c, self.box_r, self.box_h = np.asarray(centres, float).reshape(-1, 3), np.asarray(rotations, float).reshape(-1, 3, 3), np.asarray(half, float).reshape(-1, 3)

    def set_cylinders(self, bases, radius: float, height: float) -> None:
        self.cyl, self.cyl_r, self.cyl_h = np.asarray(bases, float).reshape(-1, 3), float(radius), float(height)

    def set_props(self, points, tris) -> None:  # API parity; props are ignored in the fake world
        pass

    def cast(self, origins, dirs, max_t: float, ignore=-1) -> np.ndarray:
        o, d = np.asarray(origins, float), np.asarray(dirs, float)
        n = len(o)
        best = np.full(n, np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (self.ground_y - o[:, 1]) / d[:, 1]
            best = np.where((d[:, 1] < 0) & (t >= 0), t, best)
            if self.bounds is not None:  # fakesim-style walls at the bounds
                xmin, zmin, xmax, zmax = self.bounds
                for axis, lo, hi in ((0, xmin, xmax), (2, zmin, zmax)):
                    tw = np.where(d[:, axis] > 0, (hi - o[:, axis]) / d[:, axis], np.where(d[:, axis] < 0, (lo - o[:, axis]) / d[:, axis], np.inf))
                    y = o[:, 1] + tw * d[:, 1]
                    best = np.where((tw >= 0) & (y >= self.ground_y) & (y <= self.ground_y + self.wall_height), np.minimum(best, tw), best)
            if len(self.box_c):
                ign = np.broadcast_to(np.asarray(ignore), (n,))
                ol = np.einsum("nmk,mkj->nmj", o[:, None, :] - self.box_c[None], self.box_r)
                dl = np.einsum("nk,mkj->nmj", d, self.box_r)
                t1, t2 = (-self.box_h[None] - ol) / dl, (self.box_h[None] - ol) / dl
                tmin = np.nanmax(np.minimum(t1, t2), axis=2)
                tmax = np.nanmin(np.maximum(t1, t2), axis=2)
                hit = (tmax >= np.maximum(tmin, 0)) & (np.arange(len(self.box_c))[None] != ign[:, None])
                tb = np.where(hit, np.where(tmin > 0, tmin, tmax), np.inf)
                best = np.minimum(best, tb.min(axis=1))
            if len(self.cyl):
                ox, oz = o[:, None, 0] - self.cyl[None, :, 0], o[:, None, 2] - self.cyl[None, :, 2]
                a = d[:, None, 0] ** 2 + d[:, None, 2] ** 2
                b = 2 * (ox * d[:, None, 0] + oz * d[:, None, 2])
                c = ox**2 + oz**2 - self.cyl_r**2
                disc = b * b - 4 * a * c
                tc = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
                y = o[:, None, 1] + tc * d[:, None, 1]
                ok = (disc >= 0) & (tc >= 0) & (y >= self.cyl[None, :, 1]) & (y <= self.cyl[None, :, 1] + self.cyl_h)
                best = np.minimum(best, np.where(ok, tc, np.inf).min(axis=1))
        best[best > max_t] = np.inf
        return best

    def probe(self, xs, zs, tops, lengths) -> np.ndarray:
        tops, lengths = np.asarray(tops, float), np.asarray(lengths, float)
        return np.where((tops >= self.ground_y) & (tops - self.ground_y <= lengths), self.ground_y, np.nan)

    def height(self, x: float, z: float, ref: float = 1e4, length: float = 2e4) -> float:
        return self.ground_y


# -------------------------------------------------------------------------------------------------------- fake core
class FakeCore(Sim):
    """isaac.sim.sim.Sim with the GLB / Warp / Isaac parts replaced: flat ground at y = 0, fakesim walls at the bounds,
    world.json roads/sites/spawns/equipment, crossing pedestrians. Same step/sensor code as the Isaac backend.
    Lidar y gets the nav-grid terrain offset like fakesim (planner ground filter)."""

    def __init__(self, world_dir: str | Path | None = None, n_trucks: int = 3, truck_size=DEFAULT_TRUCK_SIZE,
                 terrain_offset: bool = True) -> None:
        self.views = []
        self.truck_size = tuple(float(v) for v in truck_size)
        self.truck_matrix = np.eye(4)
        if world_dir:
            self.world = W.load_world(world_dir)
        else:
            self.world = W.World(Path("."), "flat", [-FLAT_HALF, -FLAT_HALF, FLAT_HALF, FLAT_HALF], [], [], [],
                                 [W.TruckSpec(f"truck-{i}", -15.0 + 15.0 * i, -70.0, 0.0) for i in range(n_trucks)], [], np.eye(4))
        self.caster = NumpyCaster(0.0, self.world.bounds)
        self.probe, self.height = self.caster.probe, self.caster.height
        self.excavators = [W.Box(e["id"], float(e["pos3"][0]), 0.0, float(e["pos3"][2]), float(e.get("heading_deg", 0.0)),
                                 DEFAULT_EXCAVATOR_SIZE, DEFAULT_EXCAVATOR_SIZE[1] / 2)
                           for e in self.world.equipment if "excavat" in str(e.get("type", "excavator")).lower()]
        self.paths = W.crossing_paths(self.world) if self.world.roads else [[(float(a), float(b)) for a, b in p] for p in
                                                                             [[(-30, -40), (30, -40)], [(-10, -10), (10, -10), (10, 10), (-10, 10)], [(-50, 20), (-10, 60)], [(40, -20), (40, 40)]]]
        self.walkers = make_walkers(self.paths)
        specs = W.spawn_trucks(self.world, self.truck_size, lambda x, z: 0.0, n_trucks) if self.world.roads else self.world.trucks[:n_trucks]
        self.trucks = [Truck(t.id, t.x, t.z, t.yaw, 0.0) for t in specs]
        self.follow = {t.id: GroundFollow(self.truck_size) for t in self.trucks}
        for t in self.trucks:
            self.follow[t.id].update(t, self.probe, 0.0)
        self.rig = Rig(self.truck_size)
        self.time = 0.0
        self.terrain = None
        nav = Path(world_dir) / "navgrid.json" if world_dir else None
        if terrain_offset and nav is not None and nav.exists():
            raw = json.loads(nav.read_text())
            self.terrain = (np.asarray(raw["heights"], float).reshape(raw["height"], raw["width"]), float(raw["cell_m"]), raw["origin"])

    def ground_at(self, x, z) -> np.ndarray:
        h, cell, (ox, oz) = self.terrain
        c = np.clip(np.floor((np.asarray(x) - ox) / cell).astype(int), 0, h.shape[1] - 1)
        r = np.clip(np.floor((np.asarray(z) - oz) / cell).astype(int), 0, h.shape[0] - 1)
        return h[r, c]

    def lidars(self) -> list[np.ndarray]:
        scans = super().lidars()
        if self.terrain is None:
            return scans
        for t, pts in zip(self.trucks, scans):
            if len(pts):
                yaw = math.radians(t.yaw)
                wx = t.x + pts[:, 2] * math.sin(yaw) + pts[:, 0] * math.cos(yaw)
                wz = t.z + pts[:, 2] * math.cos(yaw) - pts[:, 0] * math.sin(yaw)
                pts[:, 1] += (self.ground_at(wx, wz) - self.ground_at(t.x, t.z)).astype(np.float32)
        return scans


def hello_with_swing(core) -> dict:
    """core.hello() with every excavator obstacle radius widened to its swing envelope (half the box diagonal), so the
    one-shot hello (BrainClient sends it once per connection) stays valid while animate.py swings the house.
    Excavators come first in Sim.hello's obstacle list (then props.json obstacles)."""
    h = getattr(core, "hello_raw", core.hello)()
    for i, b in enumerate(core.excavators):
        if i < len(h["obstacles"]):
            h["obstacles"][i]["r"] = float(math.hypot(b.size[0], b.size[2]) / 2)
    return h


def set_excavator_yaw(core, excavator_id: str, yaw: float) -> None:
    """Hook for isaac/animate.py: keep the raycast box of an excavator aligned with its animated house yaw."""
    for b in core.excavators:
        if b.id == excavator_id:
            b.yaw = float(yaw) % 360.0


# ---------------------------------------------------------------------------------------------- synthetic pictures
def shade_depth(depth: np.ndarray, w: int = CAM_W, h: int = CAM_H) -> np.ndarray:
    """Stand-in RGB for the fake core: depth-shaded 640 x 360 (sky where depth = range). Not used with Isaac."""
    d = np.repeat(np.repeat(depth, h // depth.shape[0] + 1, 0)[:h], w // depth.shape[1] + 1, 1)[:, :w]
    k = np.clip(1 - d / DEPTH_RANGE, 0, 1)[..., None]
    img = (GROUND * (0.35 + 0.65 * k)).astype(np.uint8)
    img[d >= DEPTH_RANGE - 1e-3] = SKY
    return img


def overview_image(core, w: int = CAM_W, h: int = CAM_H) -> np.ndarray:
    from PIL import Image, ImageDraw

    xmin, zmin, xmax, zmax = core.world.bounds
    s = h / (zmax - zmin)
    img = Image.new("RGB", (w, h), tuple(int(v) for v in GROUND))
    d = ImageDraw.Draw(img)
    px = lambda x, z: (w / 2 + (x - (xmin + xmax) / 2) * s, h - (z - zmin) * s)  # noqa: E731  image top = +z
    for r in core.world.roads:
        d.line([px(p[0], p[-1]) for p in r.points], fill=(175, 160, 135), width=max(1, int(r.width_m * s)))
    for t in core.trucks:
        x, y = px(t.x, t.z)
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(240, 180, 40))
    for wk in core.walkers:
        x, y = px(wk.x, wk.z)
        d.point((x, y), fill=(255, 120, 20))
    return np.asarray(img)


# ------------------------------------------------------------------------------------------------- message assembly
class SensorSuite:
    """Per-truck sensor messages for one core. Camera names: "overview" + truck ids (+ core.views) as in hello."""

    def __init__(self, core) -> None:
        self.core = core

    def lidar_messages(self) -> list[bytes]:
        return [lidar_message(t.id, pts, t.pose()) for t, pts in zip(self.core.trucks, self.core.lidars())]

    def frame_messages(self, images: dict | None = None, encoding: str = "jpeg", send_cam: bool = True) -> list[bytes]:
        core = self.core
        depths = core.depths()
        out = []
        over = images.get("overview") if images else None
        out.append(frame_message("overview", over if over is not None else overview_image(core), encoding=encoding))
        for i, (t, depth) in enumerate(zip(core.trucks, depths)):
            rgb = images.get(t.id) if images else None
            if rgb is None:
                rgb = shade_depth(depth) if depth is not None else np.zeros((CAM_H, CAM_W, 3), np.uint8)
            cam = cam_field(*core.camera_pose(i)) if send_cam else None
            out.append(frame_message(t.id, rgb, depth, truck=t.id, pose=t.pose(), speed=t.speed, cam=cam, encoding=encoding))
        for v in getattr(core, "views", []):
            if images and v.name in images:
                out.append(frame_message(v.name, images[v.name], encoding=encoding))
        return out


# ------------------------------------------------------------------------------------------------------ core factory
def make_core(world_dir: str | Path | None, backend: str = "fake", keep_in_bounds: bool = True, cache: str | None = None,
              fps: float = 15.0, n_trucks: int = 3, headless: bool = True):
    """Returns (core, render, close). backend 'fake' = FakeCore (no Isaac). backend 'isaac' = isaac.sim Sim with Warp
    raycasts and the RTX IsaacRenderer from isaac/sim/scene.py (must run under Isaac's python.sh)."""
    from isaac.truck import install_controllers

    if backend == "fake":
        core = FakeCore(world_dir, n_trucks)
        install_controllers(core, keep_in_bounds=keep_in_bounds and bool(world_dir))
        core.hello_raw, core.hello = core.hello, (lambda: hello_with_swing(core))
        return core, None, lambda: None

    from isaacsim import SimulationApp  # Isaac only; must exist before any omni import

    import argparse

    config = json.loads((ROOT / "isaac/sim/config.json").read_text())
    config["fps"] = fps
    app = SimulationApp({"headless": headless, "renderer": config.get("renderer", "RealTimePathTracing"), "width": 640, "height": 360})
    from isaac.sim.main import raycast_enabled
    from isaac.sim.scene import IsaacRenderer

    core = Sim(world_dir, raycast=raycast_enabled("auto"), views=[])
    install_controllers(core, keep_in_bounds=keep_in_bounds)
    args = argparse.Namespace(cache=cache or str(ROOT / "downloads/isaac/cache"), fx="off")
    renderer = IsaacRenderer(app, core, args, config)
    core.hello_raw, core.hello = core.hello, (lambda: hello_with_swing(core))

    def render() -> dict:
        renderer.update(core, 1.0 / fps)
        return renderer.render()

    return core, render, app.close

