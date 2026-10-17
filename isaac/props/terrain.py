import json
from pathlib import Path

import numpy as np


class Terrain:
    def __init__(self, world_dir: Path) -> None:
        import trimesh
        self.dir = Path(world_dir)
        self.world = json.loads((self.dir / "world.json").read_text())
        scene = trimesh.load(self.dir / self.world["assets"]["collider"], force="scene")
        self.mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
        meta = json.loads((self.dir / self.world["assets"]["heightmap_meta"]).read_text())
        n = meta["resolution"]
        raw = np.fromfile(self.dir / meta["file"], "<u2").reshape(n, n).astype(float)
        self.hm = meta["min_height_m"] + raw / 65535 * meta["height_range_m"]
        self.hm_origin = np.array(meta["origin"], float)
        self.hm_cell = meta["size_m"][0] / (n - 1)
        self.top = float(self.mesh.bounds[1, 1]) + 10.0

    def heightmap(self, xz: np.ndarray) -> np.ndarray:
        xz = np.atleast_2d(np.asarray(xz, float))
        g = (xz - self.hm_origin) / self.hm_cell
        n = self.hm.shape[0]
        c = np.clip(g[:, 0], 0, n - 1.001)
        r = np.clip(g[:, 1], 0, n - 1.001)
        c0, r0 = c.astype(int), r.astype(int)
        fc, fr = c - c0, r - r0
        h = self.hm
        return ((h[r0, c0] * (1 - fc) + h[r0, c0 + 1] * fc) * (1 - fr)
                + (h[r0 + 1, c0] * (1 - fc) + h[r0 + 1, c0 + 1] * fc) * fr)

    def raycast(self, xz: np.ndarray) -> np.ndarray:
        xz = np.atleast_2d(np.asarray(xz, float))
        out = np.full(len(xz), np.nan)
        if not len(xz):
            return out
        o = np.c_[xz[:, 0], np.full(len(xz), self.top), xz[:, 1]]
        d = np.tile([0.0, -1.0, 0.0], (len(xz), 1))
        loc, ri, _ = self.mesh.ray.intersects_location(o, d, multiple_hits=False)
        out[ri] = loc[:, 1]
        return out

    def ground(self, xz: np.ndarray) -> np.ndarray:
        y = self.raycast(xz)
        bad = np.isnan(y)
        if bad.any():
            y[bad] = self.heightmap(np.atleast_2d(xz)[bad])
        return y

    def frame(self, xz: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        xz = np.atleast_2d(np.asarray(xz, float))
        r = np.broadcast_to(np.asarray(r, float), (len(xz),))
        offs = np.array([[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]], float)
        pts = (xz[:, None, :] + offs[None] * np.maximum(r, 0.5)[:, None, None]).reshape(-1, 2)
        y = self.raycast(pts).reshape(len(xz), 5)
        hit = ~np.isnan(y[:, 0])
        y = np.where(np.isnan(y), y[:, :1], y)
        dx = (y[:, 1] - y[:, 2]) / (2 * np.maximum(r, 0.5))
        dz = (y[:, 3] - y[:, 4]) / (2 * np.maximum(r, 0.5))
        n = np.stack([-dx, np.ones_like(dx), -dz], 1)
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        lo = np.nanmin(y, axis=1)
        return np.where(hit, y[:, 0], np.nan), n, lo
