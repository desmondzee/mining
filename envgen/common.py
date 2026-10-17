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
        d.text((x + 11, y - 8), s["name"], fill=(255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
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
