"""Shared helpers for world generation: noise, slope cost, reachability, exports.

Conventions (see CONTRACT.md): metres, Y up, X east, Z north. Grids are indexed
[row, col] = [z index, x index]; row 0 is the southern (min z) edge.
"""
from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or (REPO / "data"))
WORLDS = DATA_DIR / "worlds"

MAX_SLOPE_DEG = 20.0      # steeper than this -> impassable
CLIFF_SLOPE_DEG = 35.0    # any sub-sample steeper than this in a cell -> impassable


# ---------------------------------------------------------------- noise
def value_noise(x, z, seed=0):
    """Smooth 2D value noise in [0, 1] at arbitrary float coords (lattice spacing 1)."""
    ix, iz = np.floor(x).astype(np.int64), np.floor(z).astype(np.int64)
    fx, fz = x - ix, z - iz
    ux, uz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)

    def h(a, b):
        v = (a * 73856093) ^ (b * 19349663) ^ (seed * 83492791 + 0x5bd1e995)
        v = (v ^ (v >> 13)) * 1274126177
        v = v ^ (v >> 16)
        return (v & 0xFFFFFF).astype(np.float64) / float(0xFFFFFF)

    a, b = h(ix, iz), h(ix + 1, iz)
    c, d = h(ix, iz + 1), h(ix + 1, iz + 1)
    return (a + (b - a) * ux) * (1 - uz) + (c + (d - c) * ux) * uz


def fbm(x, z, seed=0, octaves=5, freq=1 / 120.0, gain=0.5):
    """Fractal noise in roughly [-1, 1]."""
    total, amp, norm = np.zeros_like(x, dtype=np.float64), 1.0, 0.0
    for o in range(octaves):
        total += amp * (value_noise(x * freq, z * freq, seed + o * 1013) * 2 - 1)
        norm += amp
        amp *= gain
        freq *= 2.0
    return total / norm


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- slope / cost
def slope_deg(H, spacing):
    gz, gx = np.gradient(H, spacing)
    return np.degrees(np.arctan(np.hypot(gx, gz)))


def cost_from_slope(slope_mean, slope_max=None, road=None):
    """1.0 = road; off-road 1.3 + 0.15/deg; > MAX_SLOPE_DEG (or cliffs) -> -1."""
    cost = 1.3 + 0.15 * slope_mean
    if road is not None:
        cost = np.where(road, 1.0, cost)
    bad = slope_mean > MAX_SLOPE_DEG
    if slope_max is not None:
        bad |= slope_max > CLIFF_SLOPE_DEG
    return np.where(bad, -1.0, cost)


def block_pool(A, k, fn):
    """Pool non-overlapping k x k blocks of A (shape must be multiple of k)."""
    h, w = A.shape[0] // k, A.shape[1] // k
    return fn(A[: h * k, : w * k].reshape(h, k, w, k), axis=(1, 3))


# ---------------------------------------------------------------- reachability
NB8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def reachable_from(cost, start):
    """BFS over passable cells (cost > 0), 8-connected. start = (row, col)."""
    h, w = cost.shape
    seen = np.zeros_like(cost, dtype=bool)
    if cost[start] <= 0:
        return seen
    seen[start] = True
    q = deque([start])
    while q:
        r, c = q.popleft()
        for dr, dc in NB8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not seen[rr, cc] and cost[rr, cc] > 0:
                seen[rr, cc] = True
                q.append((rr, cc))
    return seen


def largest_component(passable):
    from scipy import ndimage
    lab, n = ndimage.label(passable, structure=np.ones((3, 3)))
    if n == 0:
        return passable
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == sizes.argmax()


def world_to_cell(pos, origin, cell_m):
    return int((pos[1] - origin[1]) // cell_m), int((pos[0] - origin[0]) // cell_m)


def cell_to_world(rc, origin, cell_m):
    return [round(origin[0] + (rc[1] + 0.5) * cell_m, 2), round(origin[1] + (rc[0] + 0.5) * cell_m, 2)]


def check_reachability(world, nav):
    """Return list of site/truck ids not reachable from the first parking site."""
    cost = np.asarray(nav["cost"], dtype=np.float64).reshape(nav["height"], nav["width"])
    o, cm = nav["origin"], nav["cell_m"]
    start = next(s for s in world["sites"] if s["type"] == "parking")["pos"]
    seen = reachable_from(cost, world_to_cell(start, o, cm))
    bad = []
    for item in world["sites"] + world["trucks"]:
        p = item.get("pos") or item.get("spawn")
        r, c = world_to_cell(p, o, cm)
        if not (0 <= r < nav["height"] and 0 <= c < nav["width"] and seen[r, c]):
            bad.append(item["id"])
    return bad


# ---------------------------------------------------------------- writers
def write_json(path, obj, compact=False):
    path = Path(path)
    with path.open("w") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"))
        else:
            json.dump(obj, f, indent=2)


def navgrid_dict(Hc, cost, origin, cell_m):
    return {
        "width": int(Hc.shape[1]), "height": int(Hc.shape[0]), "cell_m": float(cell_m),
        "origin": [float(origin[0]), float(origin[1])],
        "heights": [round(float(v), 2) for v in Hc.ravel()],
        "cost": [round(float(v), 2) for v in cost.ravel()],
    }


def write_heightmap_raw(out_dir, H, origin, size_m, res=513):
    """Resample H (grid nodes spanning the full extent) to res x res uint16 LE."""
    from scipy.ndimage import map_coordinates
    rr = np.linspace(0, H.shape[0] - 1, res)
    cc = np.linspace(0, H.shape[1] - 1, res)
    R, C = np.meshgrid(rr, cc, indexing="ij")
    Hs = map_coordinates(H, [R, C], order=1)
    lo, hi = float(Hs.min()), float(Hs.max())
    q = np.round((Hs - lo) / max(hi - lo, 1e-6) * 65535).astype("<u2")
    (Path(out_dir) / "heightmap.raw").write_bytes(q.tobytes())
    write_json(Path(out_dir) / "heightmap_meta.json", {
        "file": "heightmap.raw", "resolution": res, "format": "uint16 little-endian, row-major",
        "row0": "south edge (min z); col0 = west edge (min x)",
        "size_m": list(size_m), "origin": list(origin),
        "min_height_m": round(lo, 3), "max_height_m": round(hi, 3),
        "height_range_m": round(hi - lo, 3),
        "note": "world_y = min_height_m + value/65535 * height_range_m. Unity: terrain size = (size_m[0], height_range_m, size_m[1]), position y = min_height_m; may need 'Flip Vertically' depending on importer.",
    })


def export_terrain_glb(path, H, colors, origin, size_m, n=321):
    """Grid mesh (Y up) with RGBA vertex colors, resampled to n x n vertices."""
    import trimesh
    from scipy.ndimage import map_coordinates
    rr = np.linspace(0, H.shape[0] - 1, n)
    cc = np.linspace(0, H.shape[1] - 1, n)
    R, C = np.meshgrid(rr, cc, indexing="ij")
    Hs = map_coordinates(H, [R, C], order=1)
    cols = np.stack([map_coordinates(colors[..., k], [R, C], order=1) for k in range(3)], -1)
    xs = origin[0] + np.linspace(0, size_m[0], n)
    zs = origin[1] + np.linspace(0, size_m[1], n)
    X, Z = np.meshgrid(xs, zs)
    verts = np.stack([X, Hs, Z], -1).reshape(-1, 3)
    i = np.arange(n - 1)
    I, J = np.meshgrid(i, i, indexing="ij")
    v00 = (I * n + J).ravel()
    v01, v10, v11 = v00 + 1, v00 + n, v00 + n + 1
    faces = np.concatenate([np.stack([v00, v10, v01], 1), np.stack([v01, v10, v11], 1)])  # +Y normals
    rgba = np.concatenate([np.clip(cols.reshape(-1, 3) * 255, 0, 255), np.full((len(verts), 1), 255)], 1)
    mesh = trimesh.Trimesh(verts, faces, vertex_colors=rgba.astype(np.uint8), process=False)
    _ = mesh.vertex_normals
    mesh.export(path)


def hillshade(H, spacing, az=315, alt=45):
    gz, gx = np.gradient(H, spacing)
    slope = np.arctan(np.hypot(gx, gz))
    aspect = np.arctan2(-gx, gz)
    a, e = np.radians(az), np.radians(alt)
    hs = np.sin(e) * np.cos(slope) + np.cos(e) * np.sin(slope) * np.cos(a - aspect)
    return np.clip(hs, 0, 1)


SITE_COLORS = {"crusher": (230, 60, 50), "dump": (150, 90, 200), "ore": (40, 200, 170),
               "parking": (60, 140, 240), "fuel": (250, 200, 40)}


def write_preview(path, H, colors, spacing, world, px=800):
    """Top-down colored hillshade with sites and trucks marked (north up)."""
    from PIL import Image, ImageDraw, ImageFont
    shade = 0.45 + 0.75 * hillshade(H, spacing)[..., None]
    img = np.clip(colors * shade, 0, 1)[::-1]  # flip so north is up
    im = Image.fromarray((img * 255).astype(np.uint8)).resize((px, px), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    (ox, oz), (sx, sz) = world["origin"], world["size_m"]

    def to_px(p):
        return (p[0] - ox) / sx * px, (1 - (p[1] - oz) / sz) * px

    for r in world.get("roads", []):
        d.line([to_px((q[0], q[2])) for q in r["points"]], fill=(255, 255, 255) if r["type"] != "spur" else (255, 230, 150),
               width=2)
    for j in world.get("junctions", []):
        x, y = to_px((j["pos"][0], j["pos"][2]))
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 255), outline=(0, 0, 0))
    for s in world["sites"]:
        x, y = to_px(s["pos"])
        col = SITE_COLORS.get(s["type"], (255, 255, 255))
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=col, outline=(0, 0, 0), width=2)
        tw = d.textlength(s["name"], font=font) if hasattr(d, "textlength") else 8 * len(s["name"])
        d.text((x + 11, y - 8) if x + 11 + tw < px - 2 else (x - 11 - tw, y - 8), s["name"], fill=(255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    for t in world["trucks"]:
        x, y = to_px(t["spawn"])
        d.rectangle([x - 3, y - 3, x + 3, y + 3], fill=(255, 220, 0), outline=(0, 0, 0))
    im.save(path)


# ---------------------------------------------------------------- roads (CONTRACT "Roads and labels")
def bilinear(H, origin, spacing, xz, centred=False):
    """Bilinear terrain height at world [x, z] points. centred=True: H values sit at cell centres."""
    from scipy.ndimage import map_coordinates
    xz = np.atleast_2d(np.asarray(xz, float))
    off = 0.5 if centred else 0.0
    return map_coordinates(H, [(xz[:, 1] - origin[1]) / spacing - off, (xz[:, 0] - origin[0]) / spacing - off],
                           order=1, mode="nearest")


def resample(poly, step=5.0):
    """Resample a 2D polyline at ~step m spacing, keeping both endpoints exactly."""
    poly = np.asarray(poly, float)
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])
    t = np.linspace(0, s[-1], max(1, int(round(s[-1] / step))) + 1)
    return np.stack([np.interp(t, s, poly[:, 0]), np.interp(t, s, poly[:, 1])], 1)


def pos3(xz, hfun):
    return [round(float(xz[0]), 2), round(float(hfun([xz])[0]), 2), round(float(xz[1]), 2)]


def road_entry(rid, name, typ, frm, to, poly, hfun, width):
    """poly: 2D centreline ordered from -> to; hfun(xz (N,2)) -> heights from the FINAL terrain."""
    p = resample(poly)
    y = hfun(p)
    ds = np.linalg.norm(np.diff(p, axis=0), axis=1)
    grade = float(np.mean(np.abs(np.diff(y)) / np.maximum(ds, 1e-6)) * 100) if len(ds) else 0.0
    return {"id": rid, "name": name, "type": typ, "from": frm, "to": to, "width_m": width,
            "grade_pct": round(grade, 1), "one_way": False,
            "points": [[round(float(a), 2), round(float(b), 2), round(float(c), 2)] for (a, c), b in zip(p, y)]}


def check_road_graph(world, tol=1.0):
    """Return list of problems: unknown endpoints, endpoint/position mismatch, orphan sites, disconnection."""
    nodes = {n["id"]: n for n in world["sites"] + world.get("junctions", [])}
    parent = {k: k for k in nodes}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    errs, used = [], set()
    for r in world.get("roads", []):
        for end, pt in ((r["from"], r["points"][0]), (r["to"], r["points"][-1])):
            if end not in nodes:
                errs.append(f"{r['id']}: unknown endpoint {end}")
                continue
            used.add(end)
            q = nodes[end].get("pos3") or nodes[end]["pos"]
            if np.hypot(pt[0] - q[0], pt[2] - q[-1]) > tol:
                errs.append(f"{r['id']}: endpoint {end} off by {np.hypot(pt[0] - q[0], pt[2] - q[-1]):.1f} m")
        if r["from"] in nodes and r["to"] in nodes:
            parent[find(r["from"])] = find(r["to"])
    errs += [f"site {s['id']} is not a road endpoint" for s in world["sites"] if s["id"] not in used]
    errs += [f"junction {j['id']} unused" for j in world.get("junctions", []) if j["id"] not in used]
    if len({find(k) for k in nodes}) > 1:
        errs.append(f"road graph has {len({find(k) for k in nodes})} components")
    return errs


def least_cost_path(cost, start, goal):
    """A* over an 8-connected cost grid (cost <= 0 impassable). start/goal = (row, col). Returns list of cells."""
    import heapq
    h, w = cost.shape
    cmin = float(cost[cost > 0].min())
    g = np.full(cost.shape, np.inf)
    prev = -np.ones(cost.shape, np.int64)
    g[start] = 0.0
    heu = lambda r, c: np.hypot(r - goal[0], c - goal[1]) * cmin
    pq = [(heu(*start), start)]
    while pq:
        _, (r, c) = heapq.heappop(pq)
        if (r, c) == goal:
            break
        for dr, dc in NB8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and cost[rr, cc] > 0:
                ng = g[r, c] + np.hypot(dr, dc) * 0.5 * (cost[r, c] + cost[rr, cc])
                if ng < g[rr, cc]:
                    g[rr, cc], prev[rr, cc] = ng, r * w + c
                    heapq.heappush(pq, (ng + heu(rr, cc), (rr, cc)))
    if not np.isfinite(g[goal]):
        return None
    path, k = [], goal[0] * w + goal[1]
    while k >= 0:
        path.append(divmod(int(k), w))
        k = prev[path[-1]]
    return path[::-1]


# ---------------------------------------------------------------- mesh -> heightmap (worldlabs collider, scans)
def ray_intersector(mesh):
    """Embree (embreex) if installed (fast on millions of tris), else trimesh's pure-numpy intersector."""
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
        return RayMeshIntersector(mesh), True
    except Exception:
        from trimesh.ray.ray_triangle import RayMeshIntersector
        return RayMeshIntersector(mesh), False


def raycast_grid(mesh, origin, cell, w, h):
    """Top-surface heights by downward rays at cell centres (Y up).

    Returns (H, tri): H[h, w] with NaN where a ray hit nothing (mesh holes / outside the scan),
    tri[h, w] = index of the top-hit face or -1. Holes in the mesh just produce misses.
    """
    import time
    cx = origin[0] + (np.arange(w) + 0.5) * cell
    cz = origin[1] + (np.arange(h) + 0.5) * cell
    X, Z = np.meshgrid(cx, cz)
    top = float(mesh.vertices[:, 1].max()) + 5.0
    orig = np.stack([X.ravel(), np.full(X.size, top), Z.ravel()], 1)
    dirs = np.tile([0.0, -1.0, 0.0], (len(orig), 1))
    t0 = time.time()
    rmi, embree = ray_intersector(mesh)
    loc, ray_idx, tri_idx = rmi.intersects_location(orig, dirs, multiple_hits=True)
    Hf = np.full(X.size, -np.inf)
    np.maximum.at(Hf, ray_idx, loc[:, 1])  # highest hit = top surface
    tri = np.full(X.size, -1, np.int64)
    best = loc[:, 1] >= Hf[ray_idx]
    tri[ray_idx[best]] = tri_idx[best]
    H = np.where(np.isfinite(Hf), Hf, np.nan).reshape(h, w)
    print(f"  raycast {w}x{h} cells @ {cell} m on {len(mesh.faces)} tris in {time.time() - t0:.1f}s "
          f"({'embree' if embree else 'numpy'}), hit {np.isfinite(H).mean():.0%}")
    return H, tri.reshape(h, w)


def fill_holes(H, gap_cells=3, median=3, extra=()):
    """Fill NaN cells from the nearest valid cell (+ 3x3 median). Returns (H, hole, extra_filled).

    hole = cells farther than gap_cells from any hit (real gaps / outside the scan) -> callers make them
    impassable. `extra` arrays (same [h, w] leading shape, e.g. colours) are filled the same way.
    """
    from scipy import ndimage
    valid = np.isfinite(H)
    if not valid.any():
        raise ValueError("no ray hit the mesh")
    dist, (ir, ic) = ndimage.distance_transform_edt(~valid, return_indices=True)
    H = H[ir, ic]
    if median:
        H = ndimage.median_filter(H, size=median)
    return H, dist > gap_cells, [e[ir, ic] for e in extra]


def centres_to_nodes(H):
    """Cell-centred grid (h, w) -> node grid (h+1, w+1) spanning exactly the cell extent."""
    P = np.pad(H, 1, mode="edge")
    return 0.25 * (P[:-1, :-1] + P[1:, :-1] + P[:-1, 1:] + P[1:, 1:])


def terrain_colors(H, sl, hole=None):
    """Dirt -> rock by slope, darker when low; holes dark grey. Returns [h, w, 3] in 0..1."""
    hn = (H - H.min()) / max(np.ptp(H), 1e-6)
    rock = smoothstep((sl - 15) / 20)[..., None]
    col = np.array([0.62, 0.52, 0.38]) * (1 - rock) + np.array([0.45, 0.43, 0.42]) * rock
    col = col * (0.8 + 0.2 * hn[..., None])
    if hole is not None:
        col[hole] = (0.2, 0.2, 0.2)
    return col


# ---------------------------------------------------------------- auto site placement (worlds without authored layout)
def place_sites_auto(H, cost, origin, cell, n_ore=3, margin_m=8.0, pad_m=20.0, avoid=None):
    """Ore sites on the pit floor / low benches; crusher, dump, parking, fuel on flat high ground toward
    the edges (away from the pit). `avoid` (bool grid, e.g. vegetation) is penalised. All sites lie in the largest passable component, >= margin_m from
    impassable cells / the grid border. Returns sites (ids ore_1.., crusher, dump, parking, fuel)."""
    from scipy import ndimage
    comp = largest_component(cost > 0)
    sl = slope_deg(H, cell)
    flat = comp & (sl < 6)
    clear = ndimage.distance_transform_edt(np.pad(flat, 1))[1:-1, 1:-1] * cell   # m to non-flat / edge
    edge = ndimage.distance_transform_edt(np.pad(comp, 1))[1:-1, 1:-1] * cell    # m to impassable / edge
    ok = comp & (edge >= margin_m)
    if ok.sum() < 50:
        ok = comp
    lo, hi = np.percentile(H[comp], [2, 98])
    hn = np.clip((H - lo) / max(hi - lo, 1e-6), 0, 1)
    rr, cc = np.indices(H.shape)
    low = comp & (hn < 0.2)
    pc = (rr[low].mean(), cc[low].mean()) if low.any() else ((H.shape[0] - 1) / 2, (H.shape[1] - 1) / 2)
    rad = np.hypot(rr - pc[0], cc - pc[1])
    rad = rad / max(rad[comp].max(), 1)                       # 0 at pit centre .. 1 farthest passable cell
    ext = max(H.shape) * cell
    chosen = []

    def pick(score, sep):
        if avoid is not None:
            score = score - 8.0 * avoid
        for relax in (1.0, 0.6, 0.3, 0.0):
            s = np.where(ok, score, -np.inf)
            for rc, _ in chosen:
                s[np.hypot(rr - rc[0], cc - rc[1]) * cell < sep * relax] = -np.inf
            if np.isfinite(s).any():
                break
        rc = np.unravel_index(int(np.argmax(s)), s.shape)
        chosen.append((rc, sep))
        return rc

    sites = []
    clear_ore = np.minimum(clear, 12.0) / 12.0
    targets = [0.0, 0.2, 0.35, 0.1]                       # floor first, then benches part-way up
    compass = ["East", "North-East", "North", "North-West", "West", "South-West", "South", "South-East"]
    for i in range(n_ore):
        rc = pick(2.0 * clear_ore - 4.0 * np.abs(hn - targets[i % 4]) - 2.0 * (hn > 0.55), max(30.0, 0.12 * ext))
        ang = np.degrees(np.arctan2(rc[0] - pc[0], rc[1] - pc[1]))  # 0 = east, CCW (rows = +z = north)
        d = compass[int(round(ang / 45)) % 8]
        name = "Pit Floor" if i == 0 else (f"{d} Floor" if hn[rc] < 0.12 else f"{d} Bench")
        sites.append({"id": f"ore_{i+1}", "type": "ore", "name": name,
                      "pos": cell_to_world(rc, origin, cell), "tonnes": [8000, 6000, 4500][i % 3]})
    clear_pad = np.minimum(clear, pad_m) / pad_m
    infra_sep = max(30.0, 0.12 * ext)
    rc_crusher = pick(3 * clear_pad + 2.0 * hn + 0.5 * rad - 2.0 * (hn < 0.5), infra_sep)
    rc_dump = pick(3 * clear_pad + 1.0 * hn + 2.0 * rad - 2.0 * (hn < 0.4), infra_sep)
    rc_park = pick(3 * clear_pad + 2.0 * hn + 1.0 * rad - 2.0 * (hn < 0.5), infra_sep)
    dpk = np.hypot(rr - rc_park[0], cc - rc_park[1]) * cell        # fuel bay 25-70 m from the ready line
    near_other = np.zeros(H.shape, bool)                       # keep the fuel bay off the other pads
    for rc, _ in chosen[:-1]:
        near_other |= np.hypot(rr - rc[0], cc - rc[1]) * cell < 35.0
    rc_fuel = pick(np.where((dpk >= 25) & (dpk <= 70), 3 * clear_pad + hn, -5 + clear_pad) - 10.0 * near_other, 20.0)
    for sid, name, rc in [("crusher", "Primary Crusher", rc_crusher), ("dump", "Waste Dump", rc_dump),
                          ("parking", "Ready Line", rc_park), ("fuel", "Fuel Bay", rc_fuel)]:
        sites.append({"id": sid, "type": sid, "name": name, "pos": cell_to_world(rc, origin, cell)})
    return sites


def make_trucks(sites, roads, cost, origin, cell, truck=None, n=3):
    """n trucks `truck-0..` lined up at the parking site, facing along the first road leaving it.
    heading_deg: 0 = +z, clockwise (Unity eulerAngles.y) = atan2(dx, dz)."""
    pk = next(s for s in sites if s["type"] == "parking")
    p = np.array(pk["pos"], float)
    out = None
    for r in roads:
        pts = np.array([[q[0], q[2]] for q in r["points"]])
        if r["from"] == pk["id"]:
            out = pts
        elif r["to"] == pk["id"]:
            out = pts[::-1]
        if out is not None:
            break
    d = (out[min(4, len(out) - 1)] - p) if out is not None else np.array([0.0, 1.0])
    d = d / max(np.linalg.norm(d), 1e-6)
    heading = round(float(np.degrees(np.arctan2(d[0], d[1]))) % 360, 1)
    side = np.array([d[1], -d[0]])
    width = getattr(truck, "width_m", 4.0)
    gap = max(6.0, 1.6 * width)
    passable = largest_component(cost > 0)
    pr, pc_ = np.nonzero(passable)
    trucks, used = [], []
    for i in range(n):
        q = p + side * gap * (i - (n - 1) / 2)
        r, c = world_to_cell(q, origin, cell)
        ok = 0 <= r < cost.shape[0] and 0 <= c < cost.shape[1] and passable[r, c]
        if not ok or any(np.hypot(*(q - u)) < gap * 0.9 for u in used):
            dd = np.hypot(pr * cell - (q[1] - origin[1]), pc_ * cell - (q[0] - origin[0]))
            for k in np.argsort(dd):
                cand = np.array(cell_to_world((pr[k], pc_[k]), origin, cell))
                if all(np.hypot(*(cand - u)) >= gap * 0.9 for u in used):
                    q = cand
                    break
        used.append(q)
        trucks.append({"id": f"truck-{i}", "callsign": f"Truck {i}",
                       "spawn": [round(float(q[0]), 1), round(float(q[1]), 1)], "heading_deg": heading,
                       "capacity_t": getattr(truck, "payload_t", 200), "model": getattr(truck, "name", None)})
    return trucks


# ---------------------------------------------------------------- grade-aware road network (least-cost tree)
def grade_graph(H, cost, cell, max_grade=0.10, smooth=1.0, k1=100.0, k2=2000.0):
    """Directed 8-neighbour sparse graph. Edge weight = length * mean cell cost * grade penalty, where the
    penalty grows steeply once |dh|/length exceeds max_grade (so paths follow ramps, not fall lines)."""
    from scipy import sparse
    from scipy.ndimage import gaussian_filter
    Hs = gaussian_filter(H, smooth) if smooth else H
    h, w = H.shape
    idx = np.arange(h * w).reshape(h, w)
    rows, cols, wts = [], [], []
    for dr, dc in NB8:
        r0, r1 = max(0, -dr), h - max(0, dr)
        c0, c1 = max(0, -dc), w - max(0, dc)
        a = (slice(r0, r1), slice(c0, c1))
        b = (slice(r0 + dr, r1 + dr), slice(c0 + dc, c1 + dc))
        ok = (cost[a] > 0) & (cost[b] > 0)
        L = np.hypot(dr, dc) * cell
        g = np.abs(Hs[b] - Hs[a]) / L
        ex = np.maximum(g - max_grade, 0.0)
        wt = L * 0.5 * (cost[a] + cost[b]) * (1.0 + k1 * ex + k2 * ex ** 2)
        rows.append(idx[a][ok]); cols.append(idx[b][ok]); wts.append(wt[ok])
    n = h * w
    return sparse.csr_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))


def build_road_network(H, cost, origin, cell, sites, road_width=15.0, ramp_width=16.5, max_grade=0.10,
                       order=None, snap_m=12.0):
    """Connect sites into a tree of roads by repeatedly joining the next site to the existing network along
    its least-cost (grade-aware) path; joining mid-road splits that road at a new junction.

    Returns (junctions, roads, road_cells_mask). Sites get pos3. roads follow CONTRACT "Roads and labels".
    """
    from scipy.sparse.csgraph import dijkstra
    from scipy.ndimage import gaussian_filter1d
    from scipy import ndimage
    h, w = H.shape
    # a road needs a corridor ~road_width wide: routing cost rises near impassable cells (cliff tops, holes)
    half = road_width / 2
    clr = ndimage.distance_transform_edt(np.pad(cost > 0, 1))[1:-1, 1:-1] * cell
    rcost = np.where(cost > 0, cost * (1.0 + 2.0 * np.clip((half - clr) / half, 0, 1)), cost)
    G = grade_graph(H, rcost, cell, max_grade)
    by = {s["id"]: s for s in sites}
    if order is None:
        rank = {"crusher": 0, "dump": 1, "ore": 2, "parking": 3, "fuel": 4}   # parking / fuel join last (spurs)
        order = [s["id"] for s in sorted(sites, key=lambda s: rank.get(s["type"], 5))]
    cell_of = {sid: world_to_cell(by[sid]["pos"], origin, cell) for sid in order}
    cell_of = {k: (min(max(r, 0), h - 1), min(max(c, 0), w - 1)) for k, (r, c) in cell_of.items()}
    node_cell = {}            # node id -> flat cell index
    node_pos = {}             # node id -> [x, z]
    roads = []                # [from, to, [flat cells]]
    junctions = {}
    snap = max(1, int(round(snap_m / cell)))

    def add_node(nid, flat, pos):
        node_cell[nid], node_pos[nid] = flat, pos

    def owner_map():
        m = {}
        for ri, (_, _, path) in enumerate(roads):
            for i, f in enumerate(path):
                m.setdefault(f, (ri, i))
        for nid, f in node_cell.items():
            m[f] = ("node", nid)
        return m

    first = order[0]
    add_node(first, cell_of[first][0] * w + cell_of[first][1], by[first]["pos"])
    for sid in order[1:]:
        src = cell_of[sid][0] * w + cell_of[sid][1]
        own = owner_map()
        targets = np.fromiter(own.keys(), np.int64)
        dist, pred = dijkstra(G, indices=src, return_predecessors=True)
        dt = dist[targets]
        if not np.isfinite(dt).any():
            print(f"  no road from {sid} to the network")
            continue
        tgt = int(targets[int(np.argmin(dt))])
        path = [tgt]
        while path[-1] != src:
            path.append(int(pred[path[-1]]))
        path = path[::-1]                                  # src ... tgt
        add_node(sid, src, by[sid]["pos"])
        o = own[tgt]
        if o[0] != "node" and tgt == src:                  # site sits on an existing road: split it there
            ri, i = o
            a, b, rp = roads[ri]
            if 0 < i < len(rp) - 1:
                roads[ri] = [a, sid, rp[:i + 1]]
                roads.append([sid, b, rp[i:]])
                continue
            o = ("node", a if i == 0 else b)
        if o[0] == "node":
            join = o[1]
        else:
            ri, i = o
            a, b, rp = roads[ri]
            if i <= snap:                                  # near an endpoint: run on to it
                path, join = path + rp[:i][::-1], a
            elif len(rp) - 1 - i <= snap:
                path, join = path + rp[i + 1:], b
            else:                                          # split road ri at a new junction
                join = f"j_{len(junctions) + 1}"
                junctions[join] = True
                add_node(join, rp[i], cell_to_world(divmod(rp[i], w), origin, cell))
                roads[ri] = [a, join, rp[:i + 1]]
                roads.append([join, b, rp[i:]])
        if len(path) < 2:
            path = [src, node_cell[join]]
        roads.append([sid, join, path])

    hfun = lambda xz: bilinear(H, origin, cell, xz, centred=True)
    centre = np.zeros(h * w, bool)
    out = []
    for k, (a, b, path) in enumerate(roads):
        centre[path] = True
        xz = np.array([cell_to_world(divmod(f, w), origin, cell) for f in path], float)
        xz[0], xz[-1] = node_pos[a], node_pos[b]
        if len(xz) > 4:
            sm = gaussian_filter1d(xz, sigma=1.0, axis=0, mode="nearest")  # light: don't cut switchbacks
            sm[0], sm[-1] = xz[0], xz[-1]
            xz = sm
        y = hfun(xz)
        dh = float(abs(y[-1] - y[0]))
        L = float(np.linalg.norm(np.diff(xz, axis=0), axis=1).sum())
        types = {by[n]["type"] for n in (a, b) if n in by}
        typ = "ramp" if dh >= 8 and dh / max(L, 1) > 0.03 else ("spur" if types & {"parking", "fuel"} and L < 120
                                                                 else "haul")
        name = lambda n: by[n]["name"] if n in by else f"Junction {n.split('_')[1]}"
        rid = f"{typ}_{k + 1}"
        out.append(road_entry(rid, f"{name(a)} - {name(b)}", typ, a, b, xz, hfun,
                              ramp_width if typ == "ramp" else road_width))
    for st in sites:
        st["pos3"] = pos3(st["pos"], hfun)
    jl = [{"id": j, "name": f"Junction {j.split('_')[1]}", "pos": pos3(node_pos[j], hfun)} for j in junctions]
    # road surface mask (centreline buffered to half width), passable cells only
    d = ndimage.distance_transform_edt(~centre.reshape(h, w)) * cell
    mask = (d <= road_width / 2) & (cost > 0)
    return jl, out, mask


def road_grade_stats(roads, window=20.0):
    """Max grade (%) over ~window m along each road, from its 3D points."""
    res = {}
    for r in roads:
        p = np.asarray(r["points"], float)
        s = np.concatenate([[0], np.cumsum(np.hypot(np.diff(p[:, 0]), np.diff(p[:, 2])))])
        k = max(1, int(round(window / max(s[-1] / max(len(p) - 1, 1), 1e-6))))
        g = [abs(p[i + k, 1] - p[i, 1]) / max(s[i + k] - s[i], 1e-6) for i in range(len(p) - k)] or [r["grade_pct"] / 100]
        res[r["id"]] = round(100 * float(max(g)), 1)
    return res
