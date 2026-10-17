import math
import os
from pathlib import Path

import numpy as np

from . import world as W
from .glb import read_glb
from .kinematics import GroundFollow, Truck, yaw_matrix
from .pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS, make_walkers
from .sensors import DEPTH_RANGE, LIDAR_RANGE, Rig, depth_image, lidar_points
from .views import View, view_pose
from .excavators import JOINTS, TRUCK_SIDE_YAW, DigRig, RadioPoller

PEDESTRIAN_PROBE = 30.0
LOAD_REACH = 16.0
LOAD_SPEED = 0.5
AIM_RANGE = (40.0, 160.0)


class Sim:
    def __init__(self, world_dir: str | Path, raycast: bool = True, repo: Path = W.REPO, views: list[dict] | None = None, lidar_level: bool = True,
                 extra_excavators: list[dict] | None = None) -> None:
        self.lidar_level = lidar_level
        self.views = [View.from_dict(v) for v in views or []]
        self.world = W.load_world(world_dir)
        self.truck_matrix, self.truck_size = W.truck_box(repo)
        self.caster = None
        if raycast:
            from .raycast import Caster

            ground = read_glb(self.world.asset("collider")).triangles()
            surround_path = self.world.asset("surround")
            surround = read_glb(surround_path).triangles() if surround_path and surround_path.exists() else None
            self.caster = Caster(ground, surround)
            prober = Caster(ground, surround, device="cpu")
            height = prober.height
            self.probe = prober.probe
        else:
            hm = W.heightmap(self.world)
            height = hm
            self.probe = lambda xs, zs, tops, lengths: np.array([hm(x, z) for x, z in zip(xs, zs)])
        self.height = height
        self.excavators = W.excavators(self.world, height, repo, extra_excavators or [])
        idle = {e["id"] for e in extra_excavators or []}
        self.rigs = {b.id: DigRig(b.id, digging=b.id in idle) for b in self.excavators}
        self.rig_matrix, _ = W.model_box(repo / W.EXCAVATOR_GLB, horizontal=W.EXCAVATOR_LENGTH)
        nodes = read_glb(repo / W.EXCAVATOR_GLB).nodes
        self.rig_origins = {j: (self.rig_matrix @ nodes[j][0][:, 3])[:3] for j in JOINTS if j in nodes}
        radio = os.environ.get("RADIO_URL")
        self.radio = RadioPoller(radio, self.rigs).start() if radio else None
        self.paths = W.crossing_paths(self.world)
        self.walkers = make_walkers(self.paths)
        for w in self.walkers:
            w.y = height(w.x, w.z)
        self.trucks = [Truck(t.id, t.x, t.z, t.yaw, height(t.x, t.z)) for t in W.spawn_trucks(self.world, self.truck_size, height)]
        self.follow = {t.id: GroundFollow(self.truck_size) for t in self.trucks}
        for t in self.trucks:
            self.follow[t.id].update(t, self.probe, 0.0)
        self.rig = Rig(self.truck_size)
        self.time = 0.0

    def hello(self) -> dict:
        h = W.hello(self.world, [t.id for t in self.trucks], self.paths, [b.circle() for b in self.excavators] + W.prop_obstacles(self.world))
        h["cameras"] += [v.name for v in self.views]
        return h

    def view_poses(self, dt: float) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        return {v.name: view_pose(v, self, dt) for v in self.views}

    def command(self, truck_id: str, throttle: float, steer: float, now: float) -> None:
        for t in self.trucks:
            if t.id == truck_id:
                t.command(throttle, steer, now)

    def step(self, dt: float, now: float) -> None:
        self.time += dt
        for t in self.trucks:
            t.step(dt, now)
        follows = [self.follow[t.id] for t in self.trucks]
        pts = np.concatenate([f.points(t) for f, t in zip(follows, self.trucks)])
        refs = np.repeat([f.ref(t) for f, t in zip(follows, self.trucks)], 5)
        heights = follows[0].sample(self.probe, pts[:, 0], pts[:, 2], refs) if follows else []
        for i, (f, t) in enumerate(zip(follows, self.trucks)):
            f.apply(t, heights[5 * i : 5 * i + 5], dt)
        for rig in self.rigs.values():
            rig.step(dt)
        for w in self.walkers:
            w.step(dt)
        if self.walkers:
            ys = np.array([w.y for w in self.walkers])
            h = self.probe(np.array([w.x for w in self.walkers]), np.array([w.z for w in self.walkers]), ys + PEDESTRIAN_PROBE, np.full(len(ys), PEDESTRIAN_PROBE * 2))
            for w, y in zip(self.walkers, np.where(np.isfinite(h), h, ys)):
                w.y = float(y)

    def loading(self) -> dict[str, str]:
        out = {}
        for t in self.trucks:
            if abs(t.speed) > LOAD_SPEED or not self.excavators:
                continue
            d, b = min((math.hypot(t.x - b.x, t.z - b.z), b.id) for b in self.excavators)
            if d < LOAD_REACH:
                out[t.id] = b
        return out

    def aim(self, targets: dict[str, tuple[float, float]]) -> None:
        for b in self.excavators:
            rig, target = self.rigs[b.id], targets.get(b.id)
            rig.dump_yaw = TRUCK_SIDE_YAW
            if target is None:
                continue
            local = yaw_matrix(self.excavator_yaw(b)).T @ np.array([target[0] - b.x, 0.0, target[1] - b.z])
            yaw = math.degrees(math.atan2(local[0], local[2]))
            if AIM_RANGE[0] <= abs(yaw) <= AIM_RANGE[1]:
                rig.dump_yaw = yaw

    def excavator_yaw(self, box) -> float:
        return self.radio.headings.get(box.id, box.yaw) if self.radio else box.yaw

    def joint_frames(self, box) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        rot = self.rigs[box.id].joint_rotations()
        p, r = np.array([box.x, box.y, box.z]), yaw_matrix(self.excavator_yaw(box))
        frames, prev = {}, np.zeros(3)
        for j in JOINTS:
            if j not in self.rig_origins:
                continue
            p = p + r @ (self.rig_origins[j] - prev)
            r = r @ rot[j]
            prev = self.rig_origins[j]
            frames[j] = (p, r)
        return frames

    def excavator_state(self) -> list[dict]:
        out = []
        for b in self.excavators:
            rig = self.rigs[b.id]
            frames = self.joint_frames(b)
            bucket = frames.get("bucket")
            out.append({
                "id": b.id, "digging": rig.digging, "phase": rig.phase, "pose_deg": list(rig.pose),
                "bucket": None if bucket is None else {"position": bucket[0].tolist(), "rotation": bucket[1].tolist()},
            })
        return out

    def fx_state(self) -> list[tuple]:
        return [(t.id, (t.x, t.y, t.z), t.yaw, t.speed, [tuple(map(float, c)) for c in self.follow[t.id].contacts]) for t in self.trucks]

    def rotation(self, t: Truck) -> np.ndarray:
        return self.follow[t.id].rotation(t.yaw)

    def position(self, t: Truck) -> np.ndarray:
        return np.array([t.x, t.y, t.z])

    def sync_obstacles(self) -> None:
        if not self.caster:
            return
        boxes = [(self.position(t), self.rotation(t), self.truck_size) for t in self.trucks]
        boxes += [(np.array([b.x, b.y, b.z]), yaw_matrix(b.yaw), b.size) for b in self.excavators]
        centres = np.array([p + r @ [0, s[1] / 2, 0] for p, r, s in boxes])
        self.caster.set_boxes(centres, np.array([r for _, r, _ in boxes]), np.array([np.array(s) / 2 for _, _, s in boxes]))
        self.caster.set_cylinders(np.array([[w.x, w.y, w.z] for w in self.walkers]).reshape(-1, 3), PEDESTRIAN_RADIUS, PEDESTRIAN_HEIGHT)

    def _cast_all(self, rays, max_range: float) -> list[np.ndarray]:
        origins, dirs, ignore = [], [], []
        for i, t in enumerate(self.trucks):
            o, d = rays(self.position(t), self.rotation(t))
            origins.append(o)
            dirs.append(d)
            ignore.append(np.full(len(d), i))
        dist = self.caster.cast(np.concatenate(origins), np.concatenate(dirs), max_range, np.concatenate(ignore))
        return np.split(dist, len(self.trucks))

    def depths(self) -> list[np.ndarray | None]:
        if not self.caster or not self.trucks:
            return [None] * len(self.trucks)
        return [depth_image(d, self.rig.depth_dirs) for d in self._cast_all(self.rig.depth_rays, DEPTH_RANGE)]

    def lidars(self) -> list[np.ndarray]:
        if not self.caster or not self.trucks:
            return [np.zeros((0, 3), np.float32) for _ in self.trucks]
        scans = [lidar_points(d, self.rig.lidar_dirs) for d in self._cast_all(self.rig.lidar_rays, LIDAR_RANGE)]
        if not self.lidar_level:
            return scans
        out = []
        for t, pts in zip(self.trucks, scans):
            level = yaw_matrix(t.yaw).T @ self.rotation(t)
            out.append((pts @ level.T).astype(np.float32))
        return out

    def actors(self) -> list[dict]:
        return [w.state() for w in self.walkers]

    def camera_pose(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        t = self.trucks[index]
        r = self.rotation(t)
        return self.position(t) + r @ self.rig.camera, r

