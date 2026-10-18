"""Surroundings: ground apron + procedural forest so a scanned/generated site sits in a believable landscape.

Port of the browser preview's inline apron (envgen/preview/index.html, `// --- surroundings:`).
Writes data/worlds/<id>/surround.glb (Y up, world frame = same as collider.glb / terrain.glb,
no scan_transform) and adds `"surround": "surround.glb"` to world.json assets.

    python -m envgen.surround --id quarry [--radius 3000]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

DATA_DIR = Path(os.environ.get("DATA_DIR", "data")) / "worlds"


def n2(x, z):
    """Same smooth noise as the preview (roughly [-1.75, 1.75])."""
    return (np.sin(x * 0.011) * np.cos(z * 0.013)
            + 0.5 * np.sin(x * 0.031 + z * 0.017)
            + 0.25 * np.cos(x * 0.07 - z * 0.05))


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def load_heightmap(wdir: Path):
    meta = json.loads((wdir / "heightmap_meta.json").read_text())
    n = meta["resolution"]
    raw = np.fromfile(wdir / "heightmap.raw", dtype="<u2").reshape(n, n)
    H = meta["min_height_m"] + raw.astype(np.float64) / 65535.0 * meta["height_range_m"]
    return H, meta  # H[row, col], row0 = south (min z), col0 = west (min x)


def axis_coords(c, h_in, R, fine=4.0, growth=1.1):
    """1D vertex coords: `fine` spacing within c +- h_in, geometric growth out to c +- R."""
    n_in = max(1, int(np.ceil(2 * h_in / fine)))
    inner = np.linspace(c - h_in, c + h_in, n_in + 1)
    outs, s, p = [], fine, h_in
    while p < R - 1e-6:
        s *= growth
        p = min(R, p + s)
        if R - p < 0.5 * s:
            p = R
        outs.append(p)
    outs = np.array(outs)
    return np.concatenate([c - outs[::-1], inner, c + outs])


def sampler(grid, origin, size_m):
    """Bilinear sampler over a [row=z, col=x] grid; coords clamped to the footprint edge."""
    from scipy.ndimage import map_coordinates
    n = grid.shape[0]

    def f(x, z):
        c = np.clip((x - origin[0]) / size_m[0] * (n - 1), 0, n - 1)
        r = np.clip((z - origin[1]) / size_m[1] * (n - 1), 0, n - 1)
        return map_coordinates(grid, [r, c], order=1, mode="nearest")
    return f


def terrain_colors(wdir: Path, world: dict):
    """Vertex colours of terrain.glb as a regular [z, x, 3] grid in 0..1, or None."""
    p = wdir / (world.get("assets", {}).get("terrain_mesh") or "terrain.glb")
    if not p.exists():
        return None
    try:
        import trimesh
        m = trimesh.load(p, force="mesh")
        if m.visual.kind != "vertex":
            return None
        v, col = m.vertices, m.visual.vertex_colors[:, :3].astype(np.float64) / 255.0
        n = int(round(np.sqrt(len(v))))
        if n * n != len(v):
            return None
        (x0, _, z0), (x1, _, z1) = m.bounds
        c = np.round((v[:, 0] - x0) / (x1 - x0) * (n - 1)).astype(int)
        r = np.round((v[:, 2] - z0) / (z1 - z0) * (n - 1)).astype(int)
        g = np.zeros((n, n, 3))
        g[r, c] = col
        return g
    except Exception as e:  # colours are optional
        print("surround: no terrain colours:", e)
        return None


# ----------------------------------------------------------------------------------------------------------------
# Procedural forest: tiling ground texture, tree scatter, minimal glTF writer (texture + COLOR_0, GPU instancing)
# ----------------------------------------------------------------------------------------------------------------
TEX_N = 2048
TEX_TILE_M = 32.0                      # one texture repeat covers 32 x 32 m (UV = world xz / TEX_TILE_M)
SUN_DIR = (-0.7296, 0.5472, -0.4104)          # towards the sun, glTF frame (matches the preview's DirectionalLight)
ARCH_NAMES = ("conifer", "pine", "broadleaf")
FOREST_TARGET = 18000


def _band_noise(n, lo, hi, rng, aniso=1.0):
    """Periodic (tileable) band-limited noise on an n x n grid, frequencies lo..hi cycles per tile, std 1."""
    f = np.fft.fftfreq(n) * n
    kx, ky = np.meshgrid(f, f * aniso)
    k = np.hypot(kx, ky)
    mask = ((k >= lo) & (k <= hi)).astype(np.float64) / np.maximum(k, 1.0)
    w = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    out = np.fft.ifft2(w * mask).real
    return out / (out.std() + 1e-9)


def _fbm(n, base, octaves, rng, gain=0.5):
    out, amp, tot = np.zeros((n, n)), 1.0, 0.0
    for i in range(octaves):
        lo = base * 2 ** i
        out += amp * _band_noise(n, lo, lo * 2, rng)
        tot += amp
        amp *= gain
    return out / tot


def ground_texture(seed=7, n=TEX_N):
    """Tileable forest-floor / grass / dirt albedo (sRGB uint8 [n, n, 3]); bright mean (~0.8) because the apron's
    vertex colours carry the large-scale hue (glTF multiplies COLOR_0 x baseColorTexture)."""
    rng = np.random.default_rng(seed)
    big = _fbm(n, 2, 3, rng)                    # grass vs litter patches (~4-16 m)
    mid = _fbm(n, 8, 3, rng)                    # tufts / dirt spots (~1-4 m)
    fine = _fbm(n, 64, 3, rng, gain=0.6)        # blades, needles (~1-15 cm)
    speck = _band_noise(n, 300, 700, rng)       # grit
    blades = _band_noise(n, 90, 260, rng, aniso=3.0)   # streaky grass
    grass = np.array([0.80, 0.86, 0.72])
    dry = np.array([0.90, 0.87, 0.76])
    litter = np.array([0.80, 0.75, 0.68])
    dirt = np.array([0.86, 0.80, 0.74])
    moss = np.array([0.74, 0.82, 0.68])
    t_lit = smoothstep(-0.2, 0.6, big)[..., None]
    t_dry = smoothstep(0.2, 0.9, mid + 0.4 * big)[..., None]
    t_dirt = smoothstep(0.75, 1.25, mid - 0.6 * big)[..., None]
    t_moss = smoothstep(0.6, 1.2, -big + 0.3 * mid)[..., None]
    c = grass + (litter - grass) * t_lit
    c = c + (dry - c) * t_dry * 0.6
    c = c + (moss - c) * t_moss * 0.7
    c = c + (dirt - c) * t_dirt * 0.6
    shade = 1.0 + 0.10 * fine + 0.07 * blades * (1 - t_dirt[..., 0]) + 0.06 * speck
    c = c * shade[..., None]
    return (np.clip(c, 0, 1) * 255).astype(np.uint8)


def _srgb_to_lin(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


class _Noise:
    """Smooth non-periodic 2D value noise over world xz (cubic interpolation of a random lattice)."""

    def __init__(self, seed, cell, n=192):
        self.g = np.random.default_rng(seed).standard_normal((n, n))
        self.cell, self.n = cell, n

    def __call__(self, x, z):
        from scipy.ndimage import map_coordinates
        u = (np.asarray(x) / self.cell) % self.n
        v = (np.asarray(z) / self.cell) % self.n
        return map_coordinates(self.g, [v.ravel(), u.ravel()], order=3, mode="grid-wrap").reshape(np.shape(x)) * 0.7


def make_tracks(half, R, rng, footprint_center=(0.0, 0.0)):
    """A few winding dirt tracks leaving the quarry rim (access road west, forest tracks elsewhere).
    Returns list of (polyline [k,2] xz, half_width_m)."""
    cx, cz = footprint_center
    out = []
    for ang, w, length in ((np.pi + 0.15, 5.0, R), (0.5 * np.pi + 0.3, 3.0, 0.75 * R), (-0.35, 3.0, 0.6 * R),
                           (-0.5 * np.pi - 0.4, 2.5, 0.5 * R)):
        d = np.array([np.cos(ang), np.sin(ang)])
        s = max(abs(d[0]), abs(d[1]))
        p = np.array([cx, cz]) + d / s * (half + 1.0)     # on the footprint square edge
        pts, heading = [p.copy()], ang
        travelled = 0.0
        while travelled < length:
            heading += rng.normal(0, 0.12) + 0.04 * np.sin(travelled / 180.0)
            heading = ang + np.clip(heading - ang, -0.9, 0.9)
            p = p + 20.0 * np.array([np.cos(heading), np.sin(heading)])
            pts.append(p.copy())
            travelled += 20.0
        out.append((np.array(pts), w))
    return out


def track_distance(tracks, R, cell=3.0):
    """Distance-to-nearest-track field (m) as a sampler f(x, z) over [-R, R]^2 (relative to origin)."""
    from scipy.ndimage import distance_transform_edt
    n = int(2 * R / cell) + 1
    occ = np.ones((n, n), bool)
    for pts, _ in tracks:
        for a, b in zip(pts[:-1], pts[1:]):
            k = int(np.ceil(np.hypot(*(b - a)) / (cell * 0.5))) + 1
            t = np.linspace(0, 1, k)[:, None]
            q = a + (b - a) * t
            c = np.clip(np.round((q[:, 0] + R) / cell).astype(int), 0, n - 1)
            r = np.clip(np.round((q[:, 1] + R) / cell).astype(int), 0, n - 1)
            occ[r, c] = False
    dist = distance_transform_edt(occ) * cell
    return sampler(dist, (-R, -R), (2 * R, 2 * R))


# --- tree archetypes (metres, base at origin, +Y up) -------------------------------------------------------------
def _cone(r, h, y0, seg, jitter=0.0, rng=None):
    a = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    rr = r * (1 + (rng.uniform(-jitter, jitter, seg) if rng is not None else 0))
    ring = np.stack([rr * np.cos(a), np.full(seg, y0), rr * np.sin(a)], 1)
    v = np.concatenate([ring, [[0, y0 + h, 0]], [[0, y0, 0]]])
    i = np.arange(seg)
    j = (i + 1) % seg
    f = np.concatenate([np.stack([i, np.full(seg, seg), j], 1), np.stack([i, j, np.full(seg, seg + 1)], 1)])
    return v, f


def _cyl(r0, r1, h, seg):
    a = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    v = np.concatenate([np.stack([r0 * np.cos(a), np.zeros(seg), r0 * np.sin(a)], 1),
                        np.stack([r1 * np.cos(a), np.full(seg, h), r1 * np.sin(a)], 1)])
    i = np.arange(seg)
    j = (i + 1) % seg
    f = np.concatenate([np.stack([i, i + seg, j], 1), np.stack([j, i + seg, j + seg], 1)])
    return v, f


def _assemble(parts):
    """parts: list of (verts, faces, rgb) -> flat-shaded (non-indexed-by-face-normal) arrays."""
    import trimesh
    P, N, C = [], [], []
    for v, f, rgb in parts:
        m = trimesh.Trimesh(v, f, process=False)
        m.fix_normals()
        tri = m.vertices[m.faces]                         # [F, 3, 3] flat shading reads well for low poly
        fn = np.repeat(m.face_normals[:, None], 3, 1)
        P.append(tri.reshape(-1, 3))
        N.append(fn.reshape(-1, 3))
        shade = 0.9 + 0.2 * (tri[..., 1].mean(1) - tri[..., 1].min()) / (np.ptp(tri[..., 1]) + 1e-6)
        C.append(np.repeat((np.asarray(rgb)[None] * shade[:, None])[:, None], 3, 1).reshape(-1, 3))
    P, N, C = (np.concatenate(a) for a in (P, N, C))
    return P.astype(np.float32), N.astype(np.float32), C, np.arange(len(P), dtype=np.uint32)


def tree_archetypes(seed=3):
    import trimesh
    rng = np.random.default_rng(seed)
    bark = (0.30, 0.21, 0.14)
    arch = {}
    # conifer (spruce): 4 stacked cones, ~16 m
    parts = [(*_cyl(0.35, 0.2, 3.0, 6), bark)]
    for k, (r, h, y0) in enumerate(((3.4, 6.0, 2.0), (2.8, 5.5, 5.0), (2.1, 5.0, 8.0), (1.3, 4.5, 11.3))):
        parts.append((*_cone(r, h, y0, 9, 0.12, rng), (0.11 + 0.01 * k, 0.24 + 0.02 * k, 0.12)))
    arch["conifer"] = _assemble(parts)
    # pine: tall bare trunk, compact crown, ~18 m
    parts = [(*_cyl(0.3, 0.18, 12.0, 6), (0.40, 0.25, 0.16))]
    for k, (r, h, y0) in enumerate(((2.6, 3.5, 10.0), (2.0, 3.5, 12.5), (1.1, 3.2, 15.0))):
        parts.append((*_cone(r, h, y0, 8, 0.15, rng), (0.17, 0.27 + 0.02 * k, 0.12)))
    arch["pine"] = _assemble(parts)
    # broadleaf: trunk + lumpy icosphere crown, ~12 m
    ico = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
    v = ico.vertices * (1 + rng.uniform(-0.12, 0.12, (len(ico.vertices), 1)))
    v = v * [4.2, 3.6, 4.2] + [0, 8.0, 0]
    parts = [(*_cyl(0.4, 0.25, 6.0, 6), bark), (v, ico.faces, (0.20, 0.33, 0.12))]
    ico2 = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
    v2 = ico2.vertices * (1 + rng.uniform(-0.15, 0.15, (len(ico2.vertices), 1))) * [2.6, 2.3, 2.6] + [1.6, 10.2, -0.8]
    parts.append((v2, ico2.faces, (0.23, 0.36, 0.13)))
    arch["broadleaf"] = _assemble(parts)
    return arch


# --- minimal glTF 2.0 binary writer -----------------------------------------------------------------------------
class GlbWriter:
    def __init__(self):
        self.bin = bytearray()
        self.g = {"asset": {"version": "2.0", "generator": "envgen.surround (procedural)"}, "scene": 0,
                  "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "accessors": [], "bufferViews": [],
                  "buffers": [], "materials": []}

    def _view(self, data: bytes, target=None):
        while len(self.bin) % 4:
            self.bin += b"\0"
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin += data
        self.g["bufferViews"].append(bv)
        return len(self.g["bufferViews"]) - 1

    def accessor(self, arr, typ, target=None, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        ct = {np.dtype(np.float32): 5126, np.dtype(np.uint32): 5125, np.dtype(np.uint8): 5121}[arr.dtype]
        acc = {"bufferView": self._view(arr.tobytes(), target), "componentType": ct, "count": int(arr.shape[0]),
               "type": typ}
        if normalized:
            acc["normalized"] = True
        if minmax:
            a2 = arr.reshape(arr.shape[0], -1)
            acc["min"] = a2.min(0).tolist()
            acc["max"] = a2.max(0).tolist()
        self.g["accessors"].append(acc)
        return len(self.g["accessors"]) - 1

    def image_material(self, jpg: bytes, name):
        g = self.g
        g.setdefault("images", []).append({"bufferView": self._view(jpg), "mimeType": "image/jpeg"})
        g.setdefault("samplers", []).append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
        g.setdefault("textures", []).append({"sampler": len(g["samplers"]) - 1, "source": len(g["images"]) - 1})
        g["materials"].append({"name": name, "pbrMetallicRoughness": {
            "baseColorTexture": {"index": len(g["textures"]) - 1}, "metallicFactor": 0.0, "roughnessFactor": 1.0}})
        return len(g["materials"]) - 1

    def plain_material(self, name):
        self.g["materials"].append({"name": name, "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                                                            "roughnessFactor": 0.95}})
        return len(self.g["materials"]) - 1

    def mesh(self, name, pos, nrm, idx, material, uv=None, rgba=None):
        attrs = {"POSITION": self.accessor(pos.astype(np.float32), "VEC3", 34962, minmax=True),
                 "NORMAL": self.accessor(nrm.astype(np.float32), "VEC3", 34962)}
        if uv is not None:
            attrs["TEXCOORD_0"] = self.accessor(uv.astype(np.float32), "VEC2", 34962)
        if rgba is not None:
            attrs["COLOR_0"] = self.accessor(rgba.astype(np.uint8), "VEC4", 34962, normalized=True)
        ind = self.accessor(idx.astype(np.uint32).ravel(), "SCALAR", 34963)
        self.g["meshes"].append({"name": name, "primitives": [
            {"attributes": attrs, "indices": ind, "material": material, "mode": 4}]})
        return len(self.g["meshes"]) - 1

    def node(self, name, mesh, instancing=None):
        nd = {"name": name, "mesh": mesh}
        if instancing:
            nd["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": instancing}}
            for k in ("extensionsUsed", "extensionsRequired"):
                if "EXT_mesh_gpu_instancing" not in self.g.setdefault(k, []):
                    self.g[k].append("EXT_mesh_gpu_instancing")
        self.g["nodes"].append(nd)
        self.g["scenes"][0]["nodes"].append(len(self.g["nodes"]) - 1)

    def write(self, path):
        import struct
        while len(self.bin) % 4:
            self.bin += b"\0"
        self.g["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.g, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        with open(path, "wb") as fh:
            fh.write(struct.pack("<III", 0x46546C67, 2, total))
            fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            fh.write(struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))


def _mesh_height_sampler(xs, zs, Y):
    """Exact height on the apron triangulation (faces v00-v10-v01 / v01-v10-v11) for points (x, z)."""
    def f(x, z):
        j = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
        i = np.clip(np.searchsorted(zs, z) - 1, 0, len(zs) - 2)
        u = np.clip((x - xs[j]) / (xs[j + 1] - xs[j]), 0, 1)
        v = np.clip((z - zs[i]) / (zs[i + 1] - zs[i]), 0, 1)
        y00, y01, y10, y11 = Y[i, j], Y[i, j + 1], Y[i + 1, j], Y[i + 1, j + 1]
        lo = y00 + u * (y01 - y00) + v * (y10 - y00)
        hi = y11 + (1 - u) * (y10 - y11) + (1 - v) * (y01 - y11)
        return np.where(u + v <= 1, lo, hi)
    return f


NODATA_RGB = 0x26 / 255.0      # scan.py paints footprint cells without scan data this flat grey


def scan_layers(wdir: Path, world: dict, H, meta):
    """Samplers describing the actual scan inside the square footprint (the scan is an irregular blob; the rest of the
    square is no-data). All take world (x, z):
      sdist   signed distance to the scan-coverage edge, m (<0 inside the scanned area)
      rgb     linear colour of the nearest scanned cell (continues the scan's colours outward)
      veg     0..1 vegetation (green-ness) of the nearest scanned cell
      slope   scan slope, deg;  h  scan height, m
      clear   distance to the nearest haul-road edge (width_m / 2) / site, m (large = far from infrastructure)"""
    from scipy.ndimage import distance_transform_edt, gaussian_filter
    ox, oz = meta["origin"]
    sx, sz = meta["size_m"]
    tc = terrain_colors(wdir, world)
    if tc is None:
        tc = np.full((H.shape[0], H.shape[1], 3), 0.3)
    n = tc.shape[0]
    cell = sx / (n - 1)
    nodata = np.all(np.abs(tc - NODATA_RGB) < 0.012, -1)
    if nodata.all():
        nodata[:] = False
    din = distance_transform_edt(~nodata) * cell
    dout, idx = distance_transform_edt(nodata, return_indices=True)
    sd = np.where(nodata, dout * cell, -din)
    near_rgb = tc[idx[0], idx[1]]
    lin = _srgb_to_lin(near_rgb)
    ex = near_rgb[..., 1] - np.maximum(near_rgb[..., 0], near_rgb[..., 2])
    veg = smoothstep(0.0, 0.035, gaussian_filter(ex, 1.0)) * smoothstep(0.08, 0.16, near_rgb[..., 1])
    cH = sx / (H.shape[0] - 1)
    gz, gx = np.gradient(H, cH)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    # haul roads (corridor width/2) and sites, rasterised at 2 m
    rc = 2.0
    m = int(sx / rc) + 1
    clear = np.full((m, m), 1e4)
    for r in world.get("roads", []):
        occ = np.ones((m, m), bool)
        P = np.asarray(r["points"], float)[:, [0, 2]]
        for a, b in zip(P[:-1], P[1:]):
            k = int(np.ceil(np.hypot(*(b - a)) / 1.0)) + 1
            q = a + (b - a) * np.linspace(0, 1, k)[:, None]
            cc = np.clip(np.round((q[:, 0] - ox) / rc).astype(int), 0, m - 1)
            rr = np.clip(np.round((q[:, 1] - oz) / rc).astype(int), 0, m - 1)
            occ[rr, cc] = False
        clear = np.minimum(clear, distance_transform_edt(occ) * rc - r.get("width_m", 15.0) / 2)
    occ = np.ones((m, m), bool)
    for s in world.get("sites", []):
        p = s.get("pos3") or [s["pos"][0], 0, s["pos"][1]]
        cc, rr = int(round((p[0] - ox) / rc)), int(round((p[2] - oz) / rc))
        if 0 <= cc < m and 0 <= rr < m:
            occ[rr, cc] = False
    clear = np.minimum(clear, distance_transform_edt(occ) * rc - 15.0)   # load/dump point + excavator swing
    half = max(sx, sz) / 2
    cx, cz = ox + sx / 2, oz + sz / 2
    sig = 30.0 / cell                      # outside the scan: smoothed (no streaks from nearest-cell extension)
    veg_s = gaussian_filter(veg, sig, mode="nearest")
    lin_s = np.stack([gaussian_filter(lin[..., i], sig, mode="nearest") for i in range(3)], -1)
    f_sd, f_v, f_vs = (sampler(a, (ox, oz), (sx, sz)) for a in (sd, veg, veg_s))
    f_rgb = [sampler(lin[..., i], (ox, oz), (sx, sz)) for i in range(3)]
    f_rgbs = [sampler(lin_s[..., i], (ox, oz), (sx, sz)) for i in range(3)]

    def sdist(x, z):                       # beyond the square: add the overshoot (the sampler clamps)
        out = np.maximum(np.maximum(np.abs(x - cx), np.abs(z - cz)) - half, 0.0)
        e = f_sd(x, z)
        return np.where(out > 0, np.maximum(e, 0.0) + out, e)

    def vegf(x, z):
        return np.where(sdist(x, z) > 0, f_vs(x, z), f_v(x, z))

    def rgbf(x, z):
        o = (sdist(x, z) > 0)[..., None]
        return np.where(o, np.stack([f(x, z) for f in f_rgbs], -1), np.stack([f(x, z) for f in f_rgb], -1))
    L = {"sdist": sdist, "veg": vegf, "rgb": rgbf, "slope": sampler(slope, (ox, oz), (sx, sz)),
         "h": sampler(H, (ox, oz), (sx, sz)), "_clear": sampler(clear, (ox, oz), (sx, sz))}

    def clear_f(x, z):           # outside the square there is no infrastructure
        inside = np.maximum(np.abs(x - cx), np.abs(z - cz)) <= half + 2
        return np.where(inside, L["_clear"](x, z), 1e4)
    L["clear"] = clear_f
    return L


CROWN_LIN = np.array([0.16, 0.29, 0.12])   # mean crown colour of the archetypes (COLOR_0), for tinting


def scatter_trees(half, R, cx, cz, height_at, slope_at, track_d, forest_n, clear_n, rng, scan,
                  target=FOREST_TARGET):
    """Jittered-grid (blue-noise-ish) candidates thinned by a density mask. Returns [k, 9] rows
    (archetype, x, y, z, scale, yaw, tint_r, tint_g, tint_b).
    Near the scan the forest continues the scan's own vegetation: trees ramp up right at the scan-coverage edge and
    reach ~60 m into the scanned area where it is green, flat-ish (<35 deg) and away from haul roads / sites."""
    reach = min(R * 0.92, half + 2300.0)
    cell = 8.0
    g = np.arange(cx - reach, cx + reach, cell)
    X, Z = np.meshgrid(g, g - cx + cz)
    x = (X + rng.uniform(0.1, 0.9, X.shape) * cell).ravel()
    z = (Z + rng.uniform(0.1, 0.9, Z.shape) * cell).ravel()
    d = np.maximum(np.abs(x - cx), np.abs(z - cz))
    sd = scan["sdist"](x, z)
    keep = (d < reach) & (sd > -60.0)
    x, z, d, sd = x[keep], z[keep], d[keep], sd[keep]
    inscan = sd < 0
    veg = scan["veg"](x, z)
    fN = forest_n(x, z) + 0.5 * forest_n(x * 2.7 + 911, z * 2.7 - 377)
    hill = smoothstep(half * 1.6, half * 3.5, d)
    ring = 1 - smoothstep(half + 450.0, half + 1000.0, d)            # continuous woodland framing the pit
    edge = 1 - smoothstep(0.0, 220.0, sd)                             # 1 at the scan edge, 0 220 m out
    forest = np.maximum(smoothstep(0.05, 0.35, fN + 0.3 * hill),    # dense bands, more on the hills
                        ring * smoothstep(-0.45, -0.1, fN))
    forest = np.maximum(forest * (1 - 0.6 * edge * (1 - veg)),       # thinner where the scan edge is bare rock
                        edge * veg * smoothstep(-0.8, -0.3, fN))      # dense where it continues the scanned forest
    far = 1.0 - 0.8 * smoothstep(half + 700.0, reach, d)              # thin out towards the horizon
    clear = smoothstep(-0.55, -0.35, clear_n(x, z))                   # 0 in clearings / meadows
    road = smoothstep(4.0, 12.0, track_d(x - cx, z - cz)) * smoothstep(3.0, 6.0, scan["clear"](x, z))
    p_out = forest * far * clear * road
    p_out = p_out * 0.9 + 0.04 * road * (1 - edge * (1 - veg))       # a few lone trees in meadows
    p_in = veg * (1 - smoothstep(15.0, 60.0, -sd)) * road * 0.95      # inner scan border, vegetated only
    p = np.where(inscan, p_in, p_out)
    slope = np.where(inscan | (d <= half), scan["slope"](x, z), slope_at(x, z))
    p = np.where(slope > 35.0, 0.0, p)
    must = edge * veg > 0.3                                           # never thin the seam band
    if p[~must].sum() + p[must].sum() > target:   # sharpen (p**k) rather than thin uniformly: dense stays dense
        budget = max(target - p[must].sum(), 0.3 * target)
        q = p[~must]
        lo, hi = 1.0, 40.0
        for _ in range(30):
            k = 0.5 * (lo + hi)
            lo, hi = (k, hi) if (q ** k).sum() > budget else (lo, k)
        q = q ** min(hi, 2.5)
        if q.sum() > budget:
            q = q * (budget / q.sum())
        p[~must] = q
    sel = rng.random(len(p)) < p
    x, z, d, sd, inscan, veg = x[sel], z[sel], d[sel], sd[sel], inscan[sel], veg[sel]
    # base height: on the scan where scanned (never floating), else on the apron triangulation
    y = np.where(inscan, scan["h"](x, z) - 0.3, height_at(x, z) - 0.4)
    near = 1 - smoothstep(0.0, 300.0, sd)
    # archetype: broadleaf near the scan (its forest is deciduous), conifers on the hills / far
    w_broad = 0.6 * near + 0.15
    w_pine = 0.25 + 0.1 * clear_n(x * 3, z * 3)
    r = rng.random(len(x))
    arch = np.where(r < w_broad, 2, np.where(r < w_broad + w_pine, 1, 0))
    scale = rng.uniform(0.7, 1.35, len(x)) * (1 + 0.25 * smoothstep(half + 600.0, reach, d)) * (1 - 0.15 * near)
    yaw = rng.uniform(0, 2 * np.pi, len(x))
    # tint: match the scan's canopy tones at the seam, fading to neutral (with per-tree jitter) away from it
    tgt = np.clip(scan["rgb"](x, z) / CROWN_LIN, 0.35, 1.6)
    wt = (np.clip(near * 1.3, 0, 1) * veg)[:, None]
    tint = (tgt * wt + 1.0 * (1 - wt)) * rng.uniform(0.85, 1.12, (len(x), 1)) * rng.uniform(0.94, 1.06, (len(x), 3))
    return np.concatenate([np.stack([arch, x, y, z, scale, yaw], 1), tint], 1)


def write_forest(wdir: Path, rows, arch):
    w = GlbWriter()
    mat = w.plain_material("tree")
    for a_i, name in enumerate(ARCH_NAMES):
        pos, nrm, col, idx = arch[name]
        rgba = np.concatenate([np.clip(col * 255, 0, 255), np.full((len(col), 1), 255)], 1).astype(np.uint8)
        mi = w.mesh(name, pos, nrm, idx, mat, rgba=rgba)
        r = rows[rows[:, 0] == a_i]
        if not len(r):
            continue
        t = r[:, 1:4].astype(np.float32)
        q = np.zeros((len(r), 4), np.float32)
        q[:, 1] = np.sin(r[:, 5] / 2)
        q[:, 3] = np.cos(r[:, 5] / 2)
        s = np.repeat(r[:, 4:5], 3, 1).astype(np.float32)
        w.node(f"trees_{name}", mi, {"TRANSLATION": w.accessor(t, "VEC3"), "ROTATION": w.accessor(q, "VEC4"),
                                     "SCALE": w.accessor(s, "VEC3")})
    w.write(wdir / "forest.glb")
    inst = {
        "frame": "glTF / world.json frame: +Y up, metres, same as collider.glb / surround.glb (no scan_transform)",
        "glb": "forest.glb",
        "archetypes": [{"id": i, "name": n, "mesh_node": n,
                        "height_m": round(float(arch[n][0][:, 1].max()), 2)} for i, n in enumerate(ARCH_NAMES)],
        "fields": ["archetype", "x", "y", "z", "scale", "yaw", "tint_r", "tint_g", "tint_b"],
        "tint": "multiplier on the archetype's vertex colour (matches the scan's canopy tones at the seam)",
        "yaw": "radians about +Y (glTF), applied after uniform scale; y = trunk base",
        "count": int(len(rows)),
        "instances": [[int(r[0])] + [round(float(v), 2) for v in r[1:4]] + [round(float(v), 3) for v in r[4:]]
                      for r in rows],
    }
    (wdir / "forest_instances.json").write_text(json.dumps(inst, separators=(",", ":")))


def build(world_id: str, radius: float = 3000.0, rim: float = 0.0, fine: float = 4.0):
    import trimesh
    from scipy.ndimage import minimum_filter

    wdir = DATA_DIR / world_id
    world = json.loads((wdir / "world.json").read_text())
    H, meta = load_heightmap(wdir)
    n = meta["resolution"]
    ox, oz = meta["origin"]
    sx, sz = meta["size_m"]
    cx, cz = ox + sx / 2, oz + sz / 2
    half = max(sx, sz) / 2
    R = max(radius, 3 * half)

    # Off the actual scan (no-data cells inside the square) the heightmap is only an extrapolated fill with radial
    # streaks: replace it by a heavily smoothed version, blended in over 5..60 m from the scan edge.
    from scipy.ndimage import gaussian_filter
    cell = sx / (n - 1)
    _sc = scan_layers(wdir, world, H, meta)
    gx_, gz_ = np.meshgrid(ox + np.arange(n) * cell, oz + np.arange(n) * cell)
    off = smoothstep(5.0, 60.0, _sc["sdist"](gx_.ravel(), gz_.ravel()).reshape(n, n))
    H = H + (gaussian_filter(H, 40.0 / cell, mode="nearest") - H) * off
    _sc = scan_layers(wdir, world, H, meta)
    # Inside: 4 m under the min height within a 12 m neighbourhood (never pokes through cliffs).
    k = 2 * int(np.ceil(12.0 / cell)) + 1
    Hmin = minimum_filter(H, size=k, mode="nearest")
    f_min = sampler(Hmin, (ox, oz), (sx, sz))
    f_raw = sampler(H, (ox, oz), (sx, sz))

    xs = axis_coords(cx, 1.5 * half, R, fine)
    zs = axis_coords(cz, 1.5 * half, R, fine)
    X, Z = np.meshgrid(xs, zs)  # [row=z, col=x]
    x, z = X.ravel(), Z.ravel()
    d = np.maximum(np.abs(x - cx), np.abs(z - cz))  # Chebyshev, like the preview

    # Near the footprint edge, slide from (min - 4) to (raw - 0.5) so the apron meets the scan edge
    # without a visible gap; outside the footprint the edge profile is continued (clamped sampling).
    e = smoothstep(half - 12.0, half, d)
    inner = (1 - e) * (f_min(x, z) - 4.0) + e * (f_raw(x, z) - 0.5)

    # off the scan the (smoothed) fill is the visible ground itself: sit on it instead of 4 m under it
    sd0 = _sc["sdist"](x, z)
    inner = inner + (f_raw(x, z) - 0.3 - inner) * smoothstep(5.0, 60.0, sd0)
    out = smoothstep(half * 0.9, half * 1.8, d)
    hills = smoothstep(half * 2.5, R * 0.6, d)
    ground = rim + 2 + 4 * n2(x, z) + hills * (60 + 45 * n2(x * 0.4, z * 0.4))
    y = inner + (ground - inner) * out
    # The 0.9x blend start lies inside the footprint: never let it rise above the scan there.
    y = np.where(d <= half, np.minimum(y, f_raw(x, z) - np.where(sd0 > 5.0, 0.3, 0.5)), y)

    # Large-scale colour (COLOR_0): forest floor / meadows / clearings / tracks / rock, scan border colours near the
    # footprint. The tiling ground texture adds the detail (glTF: COLOR_0 x baseColorTexture).
    rng = np.random.default_rng(1234)
    forest_n, clear_n, tint_n = _Noise(11, 90.0), _Noise(23, 70.0), _Noise(37, 25.0)
    tracks = make_tracks(half, R, rng, (cx, cz))
    track_d = track_distance(tracks, R + 50.0)
    g = 0.5 + 0.5 * n2(x * 2.1, z * 1.7)
    fN = forest_n(x, z) + 0.5 * forest_n(x * 2.7 + 911, z * 2.7 - 377)
    hillw = smoothstep(half * 1.6, half * 3.5, d)
    ring = 1 - smoothstep(half + 450.0, half + 1000.0, d)
    canopy = np.maximum(smoothstep(0.05, 0.35, fN + 0.3 * hillw),   # same mask as scatter_trees (forest = darker)
                        ring * smoothstep(-0.45, -0.1, fN))
    clear = smoothstep(-0.55, -0.35, clear_n(x, z))
    tn = tint_n(x, z)
    _edge = 1 - smoothstep(0.0, 220.0, _sc["sdist"](x, z))
    canopy = np.maximum(canopy, _edge * _sc["veg"](x, z))
    meadow = np.stack([0.095 + 0.03 * g, 0.15 + 0.04 * g, 0.045 + 0.012 * g], 1) * (1 + 0.12 * tn)[:, None]
    floor = np.stack([0.065 + 0.015 * g, 0.095 + 0.02 * g, 0.04 + 0.01 * g], 1) * (1 + 0.10 * tn)[:, None]
    dryc = np.array([0.20, 0.19, 0.09])
    dirt = np.array([0.22, 0.17, 0.11])
    rock = np.array([0.30, 0.29, 0.27])
    rgb = meadow + (floor - meadow) * (canopy * clear)[:, None]
    rgb = rgb + (dryc - rgb) * ((1 - clear) * 0.55)[:, None]          # dry clearings
    # slope of the apron (deg) from the grid
    nz_, nx_ = X.shape
    Y = y.reshape(nz_, nx_)
    gz, gx = np.gradient(Y, zs, xs)
    slope_grid = np.degrees(np.arctan(np.hypot(gx, gz)))
    rgb = rgb + (rock - rgb) * smoothstep(28.0, 40.0, slope_grid.ravel())[:, None]
    td = track_d(x - cx, z - cz)
    trk = 1 - smoothstep(3.0, 7.0, td)
    rgb = rgb + (dirt - rgb) * (trk * (d > half))[:, None]
    # near the scan: continue the scan's own (nearest scanned cell) colours, fading out ~60 m past its edge
    scan = _sc
    sdv = scan["sdist"](x, z)
    w = (1 - smoothstep(0.0, 60.0, sdv))[:, None]
    rgb = scan["rgb"](x, z) * w + rgb * (1 - w)

    # ground texture: rgb above is the wanted *final* linear colour; divide by the texture's mean colour so
    # COLOR_0 x texture keeps it (a texture-less renderer sees COLOR_0 alone, ~1.6x brighter: usable fallback).
    from PIL import Image
    import io
    tex = ground_texture()
    tex_mean = _srgb_to_lin(tex.reshape(-1, 3)[::97] / 255.0).mean(0)
    Image.fromarray(tex).save(wdir / "ground_texture.jpg", quality=88)
    buf = io.BytesIO()
    Image.fromarray(tex).save(buf, format="JPEG", quality=88)
    vcol = np.clip(rgb / tex_mean, 0, 1)

    verts = np.stack([x, y, z], 1)
    nz, nx = X.shape
    I, J = np.meshgrid(np.arange(nz - 1), np.arange(nx - 1), indexing="ij")
    v00 = (I * nx + J).ravel()
    v01, v10, v11 = v00 + 1, v00 + nx, v00 + nx + 1
    faces = np.concatenate([np.stack([v00, v10, v01], 1), np.stack([v01, v10, v11], 1)])  # +Y up
    rgba = np.concatenate([np.clip(vcol * 255, 0, 255), np.full((len(verts), 1), 255)], 1).astype(np.uint8)
    mesh = trimesh.Trimesh(verts, faces, vertex_colors=rgba, process=False)
    normals = mesh.vertex_normals
    uv = np.stack([x / TEX_TILE_M, z / TEX_TILE_M], 1)
    gw = GlbWriter()
    mat = gw.image_material(buf.getvalue(), "forest_floor")
    gw.node("surround", gw.mesh("surround", verts, normals, faces, mat, uv=uv, rgba=rgba))
    path = wdir / "surround.glb"
    gw.write(path)

    # forest
    h_at = _mesh_height_sampler(xs, zs, Y)
    def s_at(px, pz):
        j = np.clip(np.searchsorted(xs, px), 0, len(xs) - 1)
        i = np.clip(np.searchsorted(zs, pz), 0, len(zs) - 1)
        return slope_grid[i, j]
    rows = scatter_trees(half, R, cx, cz, h_at, s_at, track_d, forest_n, clear_n, rng, scan)
    write_forest(wdir, rows, tree_archetypes())
    counts = {n: int((rows[:, 0] == i).sum()) for i, n in enumerate(ARCH_NAMES)}
    print(f"surround: forest {len(rows)} trees {counts}, forest.glb {(wdir / 'forest.glb').stat().st_size / 1e6:.2f} MB")

    # world.json: re-read right before writing (another process may have rewritten it).
    wj = wdir / "world.json"
    cur = json.loads(wj.read_text())
    a = cur.setdefault("assets", {})
    a["surround"] = "surround.glb"
    a["forest"] = "forest.glb"
    a["forest_instances"] = "forest_instances.json"
    a["ground_texture"] = "ground_texture.jpg"
    cur["environment"] = {
        "sky": "procedural physical sky in the browser view (three.js Sky; ?sky=pano for assets.pano); "
               "assets.pano is kept for Isaac lighting presets",
        "sun_dir": list(SUN_DIR),
        "horizon_rgb": [0.78, 0.82, 0.85],
        "ground_texture_tile_m": TEX_TILE_M,
        "ground_texture_note": "surround.glb: COLOR_0 (large-scale colour / tex mean) x baseColorTexture, "
                               "UV = world (x, z) / tile_m, REPEAT",
        "forest_count": int(len(rows)),
        "tracks": [{"half_width_m": w_half, "points_xz": np.round(pts, 1).tolist()} for pts, w_half in tracks],
    }
    tmp = wj.with_suffix(".json.tmp")
    with tmp.open("w") as fh:
        json.dump(cur, fh, indent=2)
    os.replace(tmp, wj)
    return path, mesh


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", required=True)
    ap.add_argument("--radius", type=float, default=3000.0, help="half-size of the ground square, m")
    ap.add_argument("--rim", type=float, default=0.0, help="surrounding ground height, m (world y=0 is rim)")
    ap.add_argument("--fine", type=float, default=4.0, help="vertex spacing near the site, m")
    a = ap.parse_args(argv)
    path, m = build(a.id, a.radius, a.rim, a.fine)
    print(f"{path}: {len(m.vertices)} verts, {len(m.faces)} tris, "
          f"{path.stat().st_size / 1e6:.2f} MB, bbox {np.round(m.bounds, 2).tolist()}")


if __name__ == "__main__":
    main()
