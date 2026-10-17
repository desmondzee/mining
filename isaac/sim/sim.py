from pathlib import Path

import numpy as np

from . import world as W
from .glb import read_glb
from .kinematics import GroundFollow, Truck, yaw_matrix
from .pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS, make_walkers
from .sensors import DEPTH_RANGE, LIDAR_RANGE, Rig, depth_image, lidar_points

PEDESTRIAN_PROBE = 30.0


class Sim:
    def __init__(self, world_dir: str | Path, raycast: bool = True, repo: Path = W.REPO) -> None:
        self.world = W.load_world(world_dir)
        self.truck_matrix, self.truck_size = W.truck_box(repo)
        self.caster = None
        if raycast:
            from .raycast import Caster

            ground = read_glb(self.world.asset("collider")).triangles()
            surround_path = self.world.asset("surround")
            surround = read_glb(surround_path).triangles() if surround_path and surround_path.exists() else None
            self.caster = Caster(ground, surround)
            height = self.caster.height
            self.probe = self.caster.probe
        else:
            hm = W.heightmap(self.world)
            height = hm
            self.probe = lambda xs, zs, tops, lengths: np.array([hm(x, z) for x, z in zip(xs, zs)])
        self.height = height
        self.excavators = W.excavators(self.world, height, repo)
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
        return W.hello(self.world, [t.id for t in self.trucks], self.paths, [b.circle() for b in self.excavators])

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
        for w in self.walkers:
            w.step(dt)
        if self.walkers:
            ys = np.array([w.y for w in self.walkers])
            h = self.probe(np.array([w.x for w in self.walkers]), np.array([w.z for w in self.walkers]), ys + PEDESTRIAN_PROBE, np.full(len(ys), PEDESTRIAN_PROBE * 2))
            for w, y in zip(self.walkers, np.where(np.isfinite(h), h, ys)):
                w.y = float(y)

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

    def depth(self, index: int) -> np.ndarray | None:
        if not self.caster:
            return None
        t = self.trucks[index]
        o, d = self.rig.depth_rays(self.position(t), self.rotation(t))
        return depth_image(self.caster.cast(o, d, DEPTH_RANGE, index), self.rig.depth_dirs)

    def lidar(self, index: int) -> np.ndarray:
        if not self.caster:
            return np.zeros((0, 3), np.float32)
        t = self.trucks[index]
        o, d = self.rig.lidar_rays(self.position(t), self.rotation(t))
        return lidar_points(self.caster.cast(o, d, LIDAR_RANGE, index), self.rig.lidar_dirs)

    def actors(self) -> list[dict]:
        return [w.state() for w in self.walkers]

    def camera_pose(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        t = self.trucks[index]
        r = self.rotation(t)
        return self.position(t) + r @ self.rig.camera, r

