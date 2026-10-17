"""Surroundings apron: a large ground mesh so a scanned/generated site doesn't float in space.

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

    # Inside: 4 m under the min height within a 12 m neighbourhood (never pokes through cliffs).
    cell = sx / (n - 1)
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

    out = smoothstep(half * 0.9, half * 1.8, d)
    hills = smoothstep(half * 2.5, R * 0.6, d)
    ground = rim + 2 + 4 * n2(x, z) + hills * (60 + 45 * n2(x * 0.4, z * 0.4))
    y = inner + (ground - inner) * out
    # The 0.9x blend start lies inside the footprint: never let it rise above the scan there.
    y = np.where(d <= half, np.minimum(y, f_raw(x, z) - 0.5), y)

    # Colours: preview's muted greens/browns, scan border colours in the transition band.
    g = 0.5 + 0.5 * n2(x * 2.1, z * 1.7)
    b = 0.5 + 0.5 * n2(x * 0.9 + 300, z * 1.1 - 200)  # second field: brownish patches
    green = np.stack([0.20 + 0.12 * g, 0.27 + 0.10 * g, 0.13 + 0.05 * g], 1)
    brown = np.stack([0.34 + 0.10 * g, 0.29 + 0.08 * g, 0.20 + 0.05 * g], 1)
    rgb = green + (brown - green) * (smoothstep(0.55, 0.85, b) * 0.6)[:, None]
    tc = terrain_colors(wdir, world)
    if tc is not None:
        f_c = [sampler(tc[..., i], (ox, oz), (sx, sz)) for i in range(3)]
        scan_rgb = np.stack([fc(x, z) for fc in f_c], 1)
        w = (1 - smoothstep(half * 1.0, half * 1.5, d))[:, None]
        rgb = scan_rgb * w + rgb * (1 - w)

    verts = np.stack([x, y, z], 1)
    nz, nx = X.shape
    I, J = np.meshgrid(np.arange(nz - 1), np.arange(nx - 1), indexing="ij")
    v00 = (I * nx + J).ravel()
    v01, v10, v11 = v00 + 1, v00 + nx, v00 + nx + 1
    faces = np.concatenate([np.stack([v00, v10, v01], 1), np.stack([v01, v10, v11], 1)])  # +Y up
    rgba = np.concatenate([np.clip(rgb * 255, 0, 255), np.full((len(verts), 1), 255)], 1).astype(np.uint8)
    mesh = trimesh.Trimesh(verts, faces, vertex_colors=rgba, process=False)
    _ = mesh.vertex_normals
    path = wdir / "surround.glb"
    mesh.export(path)

    # world.json: re-read right before writing (another process may have rewritten it).
    wj = wdir / "world.json"
    cur = json.loads(wj.read_text())
    cur.setdefault("assets", {})["surround"] = "surround.glb"
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
