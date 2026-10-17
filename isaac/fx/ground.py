import hashlib
import io
import json
import struct
from pathlib import Path

import numpy as np

BAKE_VERSION = "2"
CELL = 1.0
TOP_BAND = 1.0
BARYCENTRIC = np.array([[1 / 3, 1 / 3, 1 / 3], [2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]])
TEXTURE_MAX = 4096
ROAD_DUST = 0.55
ROAD_GRAVEL = 0.85
SLOPE_ROCK = (15.0, 35.0)
DUST_LUMA = (0.22, 0.55)
DUST_SAT = (0.18, 0.5)
GREEN = (0.015, 0.06)
VARIANCE = (0.03, 0.09)
DUST_LIFT = 1.12
DUST_GREY = 0.25
FIELDS = ("color", "dust", "gravel", "road", "slope", "height", "coverage", "surface")
COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def smoothstep(x, lo: float, hi: float):
    t = np.clip((np.asarray(x, float) - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


class GroundMap:
    def __init__(self, origin, cell: float, color, dust, gravel, road, slope, height, coverage, surface=None) -> None:
        self.origin = np.asarray(origin, float)
        self.cell = float(cell)
        self.color = np.asarray(color, np.float32)
        self.dust = np.asarray(dust, np.float32)
        self.gravel = np.asarray(gravel, np.float32)
        self.road = np.asarray(road, np.float32)
        self.slope = np.asarray(slope, np.float32)
        self.height = np.asarray(height, np.float32)
        self.coverage = np.asarray(coverage, bool)
        self.surface = self.height if surface is None else np.asarray(surface, np.float32)
        self.shape = self.dust.shape

    @classmethod
    def uniform(cls, color=(0.66, 0.57, 0.45), dust: float = 1.0, gravel: float = 1.0, size: float = 4000.0) -> "GroundMap":
        one = np.ones((1, 1), np.float32)
        return cls((-size / 2, -size / 2), size, np.array(color, np.float32)[None, None], one * dust, one * gravel, one * 0, one * 0, one * 0, one > 0)

    @classmethod
    def load(cls, path) -> "GroundMap":
        d = np.load(path)
        return cls(d["origin"], float(d["cell"]), *(d[k] for k in FIELDS))

    def save(self, path) -> None:
        path = Path(path)
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, origin=self.origin, cell=self.cell, **{k: getattr(self, k) for k in FIELDS})
        tmp.replace(path)

    def index(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        j = np.clip(((np.asarray(x, float) - self.origin[0]) / self.cell).astype(np.int64), 0, self.shape[1] - 1)
        i = np.clip(((np.asarray(y, float) - self.origin[1]) / self.cell).astype(np.int64), 0, self.shape[0] - 1)
        return i, j

    def sample(self, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        i, j = self.index(x, y)
        return self.dust[i, j], self.gravel[i, j], self.color[i, j]

    def height_at(self, x, y) -> np.ndarray:
        return self.height[self.index(x, y)]

    def palette(self, n: int = 8) -> np.ndarray:
        cols = self.color[self.coverage] if self.coverage.any() else self.color.reshape(-1, 3)
        luma = cols @ np.array([0.299, 0.587, 0.114], np.float32)
        order = np.argsort(luma)
        q = np.linspace(0.02, 0.98, n)
        return np.array([cols[order[int(p * (len(order) - 1))]] for p in q], np.float32)

    def tone(self, color, palette: np.ndarray) -> np.ndarray:
        luma = np.asarray(color, float) @ [0.299, 0.587, 0.114]
        pl = palette @ np.array([0.299, 0.587, 0.114])
        return np.interp(luma, pl, np.linspace(0.0, 1.0, len(pl)))


def read_glb(path: Path):
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + n])
    bin_start = 20 + n + 8
    views = gltf["bufferViews"]

    def view(i: int) -> bytes:
        v = views[i]
        o = bin_start + v.get("byteOffset", 0)
        return data[o : o + v["byteLength"]]

    def accessor(i: int) -> np.ndarray:
        a = gltf["accessors"][i]
        v = views[a["bufferView"]]
        dtype, width = np.dtype(COMPONENT[a["componentType"]]), WIDTH[a["type"]]
        o = bin_start + v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", dtype.itemsize * width)
        arr = np.ndarray((a["count"], width), dtype, data, o, (stride, dtype.itemsize))
        return arr.astype(np.float64 if dtype.kind == "f" else np.int64)

    def matrix(node: dict) -> np.ndarray:
        if "matrix" in node:
            return np.array(node["matrix"], float).reshape(4, 4).T
        m = np.eye(4)
        if "scale" in node:
            m = np.diag([*node["scale"], 1.0]) @ m
        if "rotation" in node:
            x, y, z, w = node["rotation"]
            r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            m = np.block([[r, np.zeros((3, 1))], [np.zeros((1, 3)), np.ones((1, 1))]]) @ m
        if "translation" in node:
            t = np.eye(4)
            t[:3, 3] = node["translation"]
            m = t @ m
        return m

    prims = []

    def walk(i: int, parent: np.ndarray) -> None:
        node = gltf["nodes"][i]
        m = parent @ matrix(node)
        if "mesh" in node:
            for p in gltf["meshes"][node["mesh"]]["primitives"]:
                if "TEXCOORD_0" not in p["attributes"]:
                    continue
                pts = accessor(p["attributes"]["POSITION"])
                prims.append((pts @ m[:3, :3].T + m[:3, 3], accessor(p["attributes"]["TEXCOORD_0"]), accessor(p["indices"]).reshape(-1, 3)))
        for c in node.get("children", []):
            walk(c, m)

    for root in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        walk(root, np.eye(4))
    image = gltf["images"][gltf["textures"][0]["source"]]
    return prims, view(image["bufferView"])


def decode(jpeg: bytes) -> np.ndarray:
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg))
    img.draft("RGB", (TEXTURE_MAX, TEXTURE_MAX))
    img = img.convert("RGB")
    if max(img.size) > TEXTURE_MAX:
        img = img.resize((min(img.width, TEXTURE_MAX), min(img.height, TEXTURE_MAX)))
    return np.asarray(img, np.float32) / 255.0


def surface_samples(prims, texture: np.ndarray, scan_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = texture.shape[:2]
    pts, cols = [], []
    for p, uv, tri in prims:
        wp = p @ scan_matrix[:3, :3].T + scan_matrix[:3, 3]
        for b in BARYCENTRIC:
            q = np.einsum("k,nkd->nd", b, wp[tri])
            t = np.einsum("k,nkd->nd", b, uv[tri])
            px = np.clip((t[:, 0] % 1.0 * w).astype(np.int64), 0, w - 1)
            py = np.clip((t[:, 1] % 1.0 * h).astype(np.int64), 0, h - 1)
            pts.append(q.astype(np.float32))
            cols.append(texture[py, px])
    return np.concatenate(pts), np.concatenate(cols)


def rasterize(pts: np.ndarray, cols: np.ndarray, origin, cell: float, shape) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ny, nx = shape
    j = ((pts[:, 0] - origin[0]) / cell).astype(np.int64)
    i = ((pts[:, 2] - origin[1]) / cell).astype(np.int64)
    ok = (i >= 0) & (i < ny) & (j >= 0) & (j < nx)
    idx, y, c = i[ok] * nx + j[ok], pts[ok, 1], cols[ok]
    top = np.full(nx * ny, -np.inf)
    np.maximum.at(top, idx, y)
    keep = y >= top[idx] - TOP_BAND
    idx, c = idx[keep], c[keep]
    count = np.bincount(idx, minlength=nx * ny).astype(np.float64)
    luma = c @ np.array([0.299, 0.587, 0.114])
    rgb = np.stack([np.bincount(idx, c[:, k], nx * ny) for k in range(3)], axis=1)
    l1, l2 = np.bincount(idx, luma, nx * ny), np.bincount(idx, luma * luma, nx * ny)
    n = np.maximum(count, 1)
    var = np.sqrt(np.maximum(l2 / n - (l1 / n) ** 2, 0.0))
    return (rgb / n[:, None]).reshape(ny, nx, 3), var.reshape(ny, nx), count.reshape(ny, nx) > 0, np.where(count > 0, top, np.nan).reshape(ny, nx)


def shift(a: np.ndarray, di: int, dj: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    out[max(di, 0) : h + min(di, 0), max(dj, 0) : w + min(dj, 0)] = a[max(-di, 0) : h + min(-di, 0), max(-dj, 0) : w + min(-dj, 0)]
    return out


def fill(values: np.ndarray, known: np.ndarray, iterations: int = 24) -> np.ndarray:
    out = np.where(known[..., None] if values.ndim == 3 else known, values, 0.0).astype(np.float64)
    have = known.copy()
    for _ in range(iterations):
        if have.all():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros(have.shape)
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = shift(have, di, dj)
            acc += shift(out, di, dj) * (sh[..., None] if out.ndim == 3 else sh)
            cnt += sh
        new = ~have & (cnt > 0)
        out[new] = (acc[new].T / cnt[new]).T if out.ndim == 3 else acc[new] / cnt[new]
        have |= new
    rest = out[have].mean(0) if have.any() else 0.0
    out[~have] = rest
    return out


def road_mask(roads: list, origin, cell: float, shape) -> np.ndarray:
    ny, nx = shape
    mask = np.zeros(shape, bool)
    for r in roads:
        pts = np.asarray(r["points"], float)
        pts = pts[:, [0, 2]] if pts.shape[1] == 3 else pts
        half = float(r.get("width_m", 10.0)) / 2
        rad = int(np.ceil(half / cell))
        di, dj = np.mgrid[-rad : rad + 1, -rad : rad + 1]
        disc = (di * di + dj * dj) * cell * cell <= half * half
        di, dj = di[disc], dj[disc]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(int(np.linalg.norm(b - a) / cell) + 1, 2)
            line = a + np.linspace(0, 1, n)[:, None] * (b - a)
            ci = ((line[:, 1] - origin[1]) / cell).astype(np.int64)
            cj = ((line[:, 0] - origin[0]) / cell).astype(np.int64)
            ii, jj = (ci[:, None] + di).ravel(), (cj[:, None] + dj).ravel()
            ok = (ii >= 0) & (ii < ny) & (jj >= 0) & (jj < nx)
            mask[ii[ok], jj[ok]] = True
    return mask


def heightmap_grid(world_dir: Path, origin, cell: float, shape) -> np.ndarray | None:
    meta_path = world_dir / "heightmap_meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    res = int(meta["resolution"])
    raw = np.fromfile(world_dir / meta["file"], "<u2").reshape(res, res).astype(np.float64)
    grid = meta["min_height_m"] + raw / 65535 * meta["height_range_m"]
    (ox, oz), (sx, sz) = meta["origin"], meta["size_m"]
    ny, nx = shape
    xs = origin[0] + (np.arange(nx) + 0.5) * cell
    zs = origin[1] + (np.arange(ny) + 0.5) * cell
    u = np.clip((xs - ox) / sx * (res - 1), 0, res - 1)
    v = np.clip((zs - oz) / sz * (res - 1), 0, res - 1)
    j0, i0 = np.minimum(u.astype(int), res - 2), np.minimum(v.astype(int), res - 2)
    fu, fv = (u - j0)[None, :], (v - i0)[:, None]
    g = grid[np.ix_(i0, j0)] * (1 - fu) * (1 - fv) + grid[np.ix_(i0, j0 + 1)] * fu * (1 - fv)
    return g + grid[np.ix_(i0 + 1, j0)] * (1 - fu) * fv + grid[np.ix_(i0 + 1, j0 + 1)] * fu * fv


def properties(rgb: np.ndarray, var: np.ndarray, road: np.ndarray, slope: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mx, mn = rgb.max(-1), rgb.min(-1)
    luma = rgb @ np.array([0.299, 0.587, 0.114])
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    green = rgb[..., 1] - (rgb[..., 0] + rgb[..., 2]) / 2
    veg = smoothstep(green, *GREEN)
    rock = smoothstep(slope, *SLOPE_ROCK)
    dusty = smoothstep(luma, *DUST_LUMA) * (1 - smoothstep(sat, *DUST_SAT)) * (1 - veg)
    dust = np.maximum(dusty, road * ROAD_DUST * (1 - veg)) * (1 - 0.7 * rock)
    gravel = np.maximum(road * ROAD_GRAVEL, smoothstep(var, *VARIANCE) * 0.7) * (1 - veg) * (1 - 0.5 * rock)
    grey = luma[..., None]
    color = np.clip((rgb * (1 - DUST_GREY) + grey * DUST_GREY) * DUST_LIFT, 0.0, 1.0)
    return color, np.clip(dust, 0, 1), np.clip(gravel, 0, 1)


def cache_key(world_dir: Path, cell: float) -> str:
    h = hashlib.sha1(BAKE_VERSION.encode())
    h.update((world_dir / "world.json").read_bytes())
    visual = world_dir / json.loads((world_dir / "world.json").read_text())["assets"]["visual"]
    h.update(f"{visual.stat().st_size}:{cell}".encode())
    return h.hexdigest()[:12]


def bake(world_dir, cache_dir, cell: float = CELL, log=print) -> Path:
    world_dir, cache_dir = Path(world_dir), Path(cache_dir)
    out = cache_dir / f"ground_{cache_key(world_dir, cell)}.npz"
    if out.exists():
        return out
    world = json.loads((world_dir / "world.json").read_text())
    x0, z0, x1, z1 = world["bounds"]
    origin = (x0, z0)
    shape = (int(np.ceil((z1 - z0) / cell)), int(np.ceil((x1 - x0) / cell)))
    st = world.get("scan_transform") or {}
    scan = np.array(st.get("matrix", np.eye(4)), float)
    prims, jpeg = read_glb(world_dir / world["assets"]["visual"])
    pts, cols = surface_samples(prims, decode(jpeg), scan)
    rgb, var, known, top = rasterize(pts, cols, origin, cell, shape)
    log(f"ground bake: {len(pts)} samples, coverage {known.mean():.2f}")
    rgb, var, surface = fill(rgb, known), fill(var, known), fill(np.nan_to_num(top), known)
    height = heightmap_grid(world_dir, origin, cell, shape)
    if height is None:
        height = surface
    gy, gx = np.gradient(height, cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    road = road_mask(world.get("roads", []), origin, cell, shape).astype(np.float32)
    color, dust, gravel = properties(rgb, var, road, slope)
    cache_dir.mkdir(parents=True, exist_ok=True)
    GroundMap(origin, cell, color, dust, gravel, road, slope, height, known, surface).save(out)
    return out


def load_or_bake(world_dir, cache_dir, cell: float = CELL, log=print) -> GroundMap:
    return GroundMap.load(bake(world_dir, cache_dir, cell, log))


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--cell", type=float, default=CELL)
    ap.add_argument("--preview", default="")
    a = ap.parse_args()
    t0 = time.time()
    path = bake(a.world, a.cache, a.cell)
    m = GroundMap.load(path)
    print(f"ground map {path} {m.shape} in {time.time() - t0:.1f}s; dust mean {m.dust.mean():.2f} gravel mean {m.gravel.mean():.2f} road {m.road.mean():.3f}")
    if a.preview:
        from PIL import Image

        tiles = [m.color, np.repeat(m.dust[..., None], 3, -1), np.repeat(m.gravel[..., None], 3, -1)]
        Image.fromarray((np.concatenate(tiles, axis=1)[::-1] * 255).astype(np.uint8)).save(a.preview)
