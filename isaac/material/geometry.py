from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from isaac.sim import world as W

from . import rig as R

SHELL = 0.05
BUCKET_BIN = 0.1
BUCKET_NX, BUCKET_NZ = 9, 24
BUCKET_INSET = 0.06
BED_CELL = 0.2
BED_SAMPLES = 12
BED_STEP = 0.35
BED_HEAP = 0.9


@dataclass
class BucketShape:
    x: np.ndarray
    z: np.ndarray
    bottom: np.ndarray
    lip: np.ndarray
    dome: np.ndarray
    corners: np.ndarray
    cell: float

    @property
    def struck(self) -> float:
        return float(np.sum(self.lip - self.bottom) * self.cell)

    @property
    def heaped(self) -> float:
        return self.struck + float(np.sum(self.dome) * self.cell)


@dataclass
class BedShape:
    u: np.ndarray
    v: np.ndarray
    floor: np.ndarray
    rim: float
    cell: float

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.v), len(self.u)

    @property
    def heaped(self) -> float:
        top = np.maximum(self.rim + BED_HEAP - self.floor, 0.0)
        return float(top.sum() * self.cell * self.cell)


def shell_profile(points: np.ndarray, half_width: float) -> tuple[np.ndarray, np.ndarray]:
    inner = points[np.abs(points[:, 0]) < half_width]
    edges = np.arange(inner[:, 2].min(), inner[:, 2].max() + BUCKET_BIN, BUCKET_BIN)
    idx = np.digitize(inner[:, 2], edges) - 1
    zs, ys = [], []
    for k in range(len(edges) - 1):
        sel = idx == k
        if sel.any():
            zs.append((edges[k] + edges[k + 1]) / 2)
            ys.append(inner[sel, 1].min())
    return np.array(zs), np.array(ys)


@lru_cache(maxsize=1)
def bucket_shape(repo_path: str = str(W.REPO / R.RIGGED_GLB)) -> BucketShape:
    from pathlib import Path

    pts = np.concatenate([p.points for p in R.part_glbs(Path(repo_path))["bucket"].primitives])
    half = float(np.abs(pts[:, 0]).max()) - SHELL - BUCKET_INSET
    zs, ys = shell_profile(pts, half)
    front = pts[:, 2] < zs.min() + BUCKET_BIN
    tip = np.array([pts[front, 2].min(), pts[front, 1].max()])
    rear = (pts[:, 2] > zs.min()) & (pts[:, 2] < 0.0) & (np.abs(pts[:, 0]) > half)
    back_z = float(np.percentile(pts[rear, 2], 99))
    back = np.array([back_z, float(pts[(np.abs(pts[:, 2] - back_z) < BUCKET_BIN) & (np.abs(pts[:, 0]) > half), 1].max())])
    z0, z1 = tip[0] + BUCKET_INSET * 2, back[0] - BUCKET_INSET
    x = np.linspace(-half, half, BUCKET_NX)
    z = np.linspace(z0, z1, BUCKET_NZ)
    gx, gz = np.meshgrid(x, z)
    bottom = np.interp(gz, zs, ys) + SHELL
    lip = tip[1] + (gz - tip[0]) * (back[1] - tip[1]) / (back[0] - tip[0])
    bottom = np.minimum(bottom, lip)
    edge = np.minimum(np.minimum(gx - x[0], x[-1] - gx), np.minimum(gz - z[0], z[-1] - gz))
    dome = np.minimum(edge * np.tan(np.radians(30.0)), 0.35)
    corners = np.array([[sx, tip[1] + (sz - tip[0]) * (back[1] - tip[1]) / (back[0] - tip[0]), sz] for sx in (x[0], x[-1]) for sz in (z0, z1)])
    cell = (x[1] - x[0]) * (z[1] - z[0])
    return BucketShape(gx.ravel(), gz.ravel(), bottom.ravel(), lip.ravel(), dome.ravel(), corners, float(cell))


def body_primitive(glb):
    return min(glb.primitives, key=lambda p: p.points[:, 2].min())


def surface_samples(points: np.ndarray, triangles: np.ndarray, k: int = BED_SAMPLES) -> np.ndarray:
    tri = points[triangles]
    a, b = np.meshgrid(np.arange(k + 1), np.arange(k + 1))
    keep = a + b <= k
    w1, w2 = a[keep] / k, b[keep] / k
    w0 = 1 - w1 - w2
    return (tri[:, None, 0] * w0[None, :, None] + tri[:, None, 1] * w1[None, :, None] + tri[:, None, 2] * w2[None, :, None]).reshape(-1, 3)


@lru_cache(maxsize=1)
def bed_shape(cell: float = BED_CELL) -> BedShape:
    from isaac.sim.glb import read_glb

    matrix, _ = W.truck_box()
    body = body_primitive(read_glb(W.REPO / W.TRUCK_GLB))
    pts = surface_samples(body.points, body.triangles)
    pts = pts @ matrix[:3, :3].T + matrix[:3, 3]
    pts = pts[:, [0, 2, 1]]
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    nu, nv = int(np.ceil((hi[0] - lo[0]) / cell)), int(np.ceil((hi[1] - lo[1]) / cell))
    iu = np.clip(((pts[:, 0] - lo[0]) / cell).astype(int), 0, nu - 1)
    iv = np.clip(((pts[:, 1] - lo[1]) / cell).astype(int), 0, nv - 1)
    top = np.full((nv, nu), -np.inf)
    np.maximum.at(top, (iv, iu), pts[:, 2])
    mid_u = (lo[0] + hi[0]) / 2
    centre = int((mid_u - lo[0]) / cell)
    row_max = top.max(axis=1)
    rim = float(np.median(row_max[np.isfinite(row_max)]))
    mid = top[:, centre - 2 : centre + 3]
    rows = np.where(np.all(np.isfinite(mid), axis=1) & (np.median(mid, axis=1) < rim - BED_STEP))[0]
    rows = np.arange(rows.min(), rows.max() + 1)
    floor_rows = np.median(mid[rows], axis=1)
    widths = []
    for r in rows:
        ok = top[r] < floor_rows[r - rows[0]] + BED_STEP
        left = centre
        while left > 0 and ok[left - 1]:
            left -= 1
        right = centre
        while right < nu - 1 and ok[right + 1]:
            right += 1
        widths.append(min(centre - left, right - centre))
    half = int(np.median(widths))
    kernel = np.array([0.25, 0.5, 0.25])
    smooth = np.convolve(np.pad(floor_rows, 1, mode="edge"), kernel, mode="valid")
    u = lo[0] + (centre + 0.5) * cell + np.arange(-half, half + 1) * cell
    v = lo[1] + (rows + 0.5) * cell
    floor = np.repeat(smooth[:, None], len(u), axis=1)
    return BedShape(u, v, floor, rim, cell)


def grid_faces(nv: int, nu: int) -> np.ndarray:
    i = np.arange(nv - 1)[:, None] * nu + np.arange(nu - 1)[None, :]
    i = i.ravel()
    return np.stack([i, i + 1, i + nu + 1, i + nu], axis=1)


def grid_normals(p: np.ndarray) -> np.ndarray:
    du = np.gradient(p, axis=1)
    dv = np.gradient(p, axis=0)
    n = np.cross(du, dv)
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


def icosphere(level: int = 2) -> tuple[np.ndarray, np.ndarray]:
    t = (1 + 5**0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
         (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    verts = [np.array(p, float) / np.linalg.norm(p) for p in v]
    faces = f
    for _ in range(level):
        cache, out = {}, []

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = out
    return np.array(verts), np.array(faces)


def rock(seed: int, level: int = 2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    v, f = icosphere(level)
    dirs = rng.normal(size=(6, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    cut = np.max(v @ dirs.T - rng.uniform(0.35, 0.7, 6), axis=1)
    r = 1 - 0.6 * np.maximum(cut, 0) + 0.06 * np.sin(v @ rng.normal(size=3) * 5)
    p = v * r[:, None] * rng.uniform([0.8, 0.7, 0.5], [1.1, 1.0, 0.75])
    n = np.zeros_like(p)
    fn = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    return p * 0.5, f, n
