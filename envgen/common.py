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


PRESERVED_ASSETS = ("pano", "pano_source", "surround")


def preserve_assets(out_dir, world, keys=PRESERVED_ASSETS):
    """Carry asset entries written by other tools (Marble sky backdrop `pano` = "sky_pano.jpg" + `pano_source`,
    `surround`) over from the existing world.json into the one about to be written, if the referenced file
    still exists in the world folder. Call BEFORE write_json(world.json)."""
    out_dir = Path(out_dir)
    try:
        prev = json.loads((out_dir / "world.json").read_text()).get("assets", {})
    except Exception:  # noqa: BLE001 - first run / unreadable
        return
    a = world.setdefault("assets", {})
    for k in keys:
        v = prev.get(k)
        if v is None:
            continue
        if k.endswith("_source"):                                  # metadata goes with its file
            base = prev.get(k[: -len("_source")])
            if base and (out_dir / base).exists():
                a[k] = v
        elif isinstance(v, str) and (out_dir / v).exists():
            a[k] = v


def refresh_surround(out_dir, world):
    """Rebuild data/worlds/<id>/surround.glb (envgen/surround.py) from the freshly written heightmap and keep
    world.json assets.surround. Call AFTER world.json / heightmap.raw / terrain.glb are written. If the
    generator fails, an existing surround.glb is still referenced (it may then be stale)."""
    out_dir = Path(out_dir)
    try:
        from . import surround as SR
        old = SR.DATA_DIR
        SR.DATA_DIR = out_dir.parent                               # same worlds dir as this pipeline
        try:
            path, mesh = SR.build(world["id"])
        finally:
            SR.DATA_DIR = old
        world.setdefault("assets", {})["surround"] = "surround.glb"
        print(f"  surround: rebuilt {path.name} ({len(mesh.faces)} tris)")
    except Exception as e:  # noqa: BLE001 - surroundings are cosmetic
        print(f"  WARNING: surround.glb not rebuilt: {e}")
        if (out_dir / "surround.glb").exists():
            world.setdefault("assets", {})["surround"] = "surround.glb"
            write_json(out_dir / "world.json", world)


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
    labels = []
    for s in world["sites"]:
        x, y = to_px(s["pos"])
        col = SITE_COLORS.get(s["type"], (255, 255, 255))
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=col, outline=(0, 0, 0), width=2)
        tw = d.textlength(s["name"], font=font) if hasattr(d, "textlength") else 8 * len(s["name"])
        labels.append(((x + 11, y - 8) if x + 11 + tw < px - 2 else (x - 11 - tw, y - 8), s["name"]))
    def arrow(p, hdg, col, L=22.0):
        """arrow of L px centred on world point p, pointing along heading hdg (0 = +z = up in the image)."""
        a = np.radians(hdg)
        u = np.array([np.sin(a), -np.cos(a)])                    # image y grows downward
        n = np.array([-u[1], u[0]])
        c = np.array(to_px(p))
        tip, tail = c + u * L / 2, c - u * L / 2
        d.line([tuple(tail), tuple(tip)], fill=(0, 0, 0), width=4)
        d.line([tuple(tail), tuple(tip)], fill=col, width=2)
        d.polygon([tuple(tip + u * 3), tuple(tip - u * 4 + n * 4), tuple(tip - u * 4 - n * 4)], fill=col,
                  outline=(0, 0, 0))

    for t in world["trucks"]:
        x, y = to_px(t["spawn"])
        d.rectangle([x - 3, y - 3, x + 3, y + 3], fill=(255, 220, 0), outline=(0, 0, 0))
    for s in world["sites"]:
        for key, col in (("load_point", (60, 255, 120)), ("dump_point", (255, 80, 200))):
            if s.get(key):
                q = s[key]
                arrow(np.array([q["pos3"][0], q["pos3"][2]]), q["heading_deg"], col)
        for q in s.get("slots", []):
            arrow(np.array([q["pos3"][0], q["pos3"][2]]), q["heading_deg"], (255, 220, 0), L=12.0)
    for e in world.get("equipment", []):
        x, y = to_px((e["pos3"][0], e["pos3"][2]))
        d.rectangle([x - 4, y - 4, x + 4, y + 4], fill=(255, 140, 0), outline=(0, 0, 0))
    for xy, txt in labels:                                       # labels last, on top of the markers
        d.text(xy, txt, fill=(255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
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


def road_entry(rid, name, typ, frm, to, poly, hfun, width, step=5.0):
    """poly: 2D centreline ordered from -> to; hfun(xz (N,2)) -> heights from the FINAL terrain.
    step=None keeps poly's vertices as given (caller already spaced / validated them)."""
    p = resample(poly, step) if step else np.round(np.asarray(poly, float), 2)
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
            if 0 <= rr < h and 0 <= cc < w and cost[rr, cc] > 0 and (
                    not (dr and dc) or (cost[rr, c] > 0 and cost[r, cc] > 0)):  # corner rule
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
def _site_setup(H, cost, origin, cell, margin_m=8.0, pad_m=20.0, avoid=None, fixed=None):
    """Shared fields + a separation-aware picker for automatic site placement."""
    from types import SimpleNamespace
    from scipy import ndimage
    from scipy.sparse.csgraph import connected_components
    # largest component under the road builder's own connectivity (8-neighbour with the corner rule)
    _, lab = connected_components(grade_graph(H, cost, cell, hard_max=None), directed=False)
    lab = np.where((cost > 0).ravel(), lab, -1)
    sizes = np.bincount(lab[lab >= 0])
    comp = (lab == sizes.argmax()).reshape(H.shape)
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
    ext = max(H.shape) * cell
    clear_pad = np.minimum(clear, pad_m) / pad_m
    infra_sep = max(30.0, 0.12 * ext)
    chosen = []

    def pick(score, sep, where=None, strict=False):
        """argmax of score over ok (& where) cells, >= sep m from earlier picks. strict: no relaxing of
        `where` / sep; returns None when nothing qualifies."""
        if avoid is not None:
            score = score - 8.0 * avoid
        base = ok if where is None else (ok & where)
        if not base.any() and where is not None:      # too tight for the edge margin: any passable cell
            base = comp & where
        if not base.any():
            if strict:
                return None
            base = ok
        for relax in ((1.0,) if strict else (1.0, 0.6, 0.3, 0.0)):
            s = np.where(base, score, -np.inf)
            for rc, _ in chosen:
                s[np.hypot(rr - rc[0], cc - rc[1]) * cell < sep * relax] = -np.inf
            if np.isfinite(s).any():
                break
        if strict and not np.isfinite(s).any():
            return None
        rc = np.unravel_index(int(np.argmax(s)), s.shape)
        chosen.append((rc, sep))
        return rc

    pinned = {}
    for sid, xz in (fixed or {}).items():
        r0, c0 = world_to_cell(xz, origin, cell)
        dd = np.hypot(rr - r0, cc - c0) * cell
        cand = np.where(comp & (dd <= 15.0), 3 * clear_pad + np.minimum(edge, 15) / 5 - dd / 10, -np.inf)
        if not np.isfinite(cand).any():
            cand = np.where(comp, -dd, -np.inf)
        rc = np.unravel_index(int(np.argmax(cand)), cand.shape)
        chosen.append((rc, infra_sep))
        pinned[sid] = rc
    return SimpleNamespace(comp=comp, sl=sl, flat=flat, clear=clear, edge=edge, ok=ok, hn=hn, rr=rr, cc=cc,
                           ext=ext, clear_pad=clear_pad, infra_sep=infra_sep, chosen=chosen, pick=pick,
                           pinned=pinned, dist=lambda rc: np.hypot(rr - rc[0], cc - rc[1]) * cell)


def place_sites_auto(H, cost, origin, cell, n_ore=3, margin_m=8.0, pad_m=20.0, avoid=None, fixed=None):
    """PIT layout. Ore sites on the pit floor / low benches; crusher, dump, parking, fuel on flat high ground
    toward the edges (away from the pit). `avoid` (bool grid, e.g. vegetation) is penalised. All sites lie in
    the largest passable component, >= margin_m from impassable cells / the grid border.
    fixed: {site_id: [x, z]} pins crusher/dump/parking/fuel near a hand-picked spot (snapped within 15 m to
    the most open passable cell); auto-placed sites keep their separation from pinned ones.
    Returns sites (ids ore_1.., crusher, dump, parking, fuel)."""
    S = _site_setup(H, cost, origin, cell, margin_m, pad_m, avoid, fixed)
    comp, clear, hn, rr, cc, ext = S.comp, S.clear, S.hn, S.rr, S.cc, S.ext
    pick, chosen, pinned, clear_pad, infra_sep = S.pick, S.chosen, S.pinned, S.clear_pad, S.infra_sep
    low = comp & (hn < 0.2)
    pc = (rr[low].mean(), cc[low].mean()) if low.any() else ((H.shape[0] - 1) / 2, (H.shape[1] - 1) / 2)
    rad = np.hypot(rr - pc[0], cc - pc[1])
    rad = rad / max(rad[comp].max(), 1)                       # 0 at pit centre .. 1 farthest passable cell
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
    pk = lambda sid, score, sep: pinned[sid] if sid in pinned else pick(score, sep)
    rc_crusher = pk("crusher", 3 * clear_pad + 2.0 * hn + 0.5 * rad - 2.0 * (hn < 0.5), infra_sep)
    rc_dump = pk("dump", 3 * clear_pad + 1.0 * hn + 2.0 * rad - 2.0 * (hn < 0.4), infra_sep)
    rc_park = pk("parking", 3 * clear_pad + 2.0 * hn + 1.0 * rad - 2.0 * (hn < 0.5), infra_sep)
    dpk = np.hypot(rr - rc_park[0], cc - rc_park[1]) * cell        # fuel bay 25-70 m from the ready line
    near_other = np.zeros(H.shape, bool)                       # keep the fuel bay off the other pads
    for rc, _ in chosen:
        if rc != rc_park:
            near_other |= np.hypot(rr - rc[0], cc - rc[1]) * cell < 35.0
    rc_fuel = pk("fuel", np.where((dpk >= 25) & (dpk <= 70), 3 * clear_pad + hn, -5 + clear_pad) - 10.0 * near_other,
                 20.0)
    for sid, name, rc in [("crusher", "Primary Crusher", rc_crusher), ("dump", "Waste Dump", rc_dump),
                          ("parking", "Ready Line", rc_park), ("fuel", "Fuel Bay", rc_fuel)]:
        sites.append({"id": sid, "type": sid, "name": name, "pos": cell_to_world(rc, origin, cell)})
    return sites


def _plane_fit(H, mask, origin, cell):
    """Least-squares plane y = gx*x + gz*z + c over mask cells. Returns (gx, gz, r2)."""
    rr, cc = np.nonzero(mask)
    x, z, y = origin[0] + (cc + 0.5) * cell, origin[1] + (rr + 0.5) * cell, H[rr, cc]
    A = np.stack([x, z, np.ones_like(x)], 1)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    r2 = 1 - np.var(y - A @ coef) / max(np.var(y), 1e-9)
    return float(coef[0]), float(coef[1]), float(r2)


def detect_layout(H, hole, origin, cell):
    """'hillside' when the terrain rises mostly to one side (a plane explains much of it) and the low ground
    reaches the scan edge; else 'pit' (low ground enclosed by a rim)."""
    from scipy import ndimage
    scanned = ~hole
    lo, hi = np.percentile(H[scanned], [2, 98])
    low = scanned & (H < lo + 0.25 * (hi - lo))
    boundary = scanned & ~ndimage.binary_erosion(scanned, iterations=2)
    low_edge = float(low[boundary].mean()) if boundary.any() else 0.0
    _, _, r2 = _plane_fit(H, scanned, origin, cell)
    layout = "hillside" if (r2 > 0.3 and low_edge > 0.12) else "pit"
    print(f"  layout auto-detect: plane R2 {r2:.2f}, low ground on scan edge {low_edge:.0%} -> {layout}")
    return layout


def grade_reachable(H, cost, cell, start_rc, gmax):
    """Cells reachable from start_rc without ever exceeding grade gmax (8-neighbour, smoothed H)."""
    from scipy.sparse.csgraph import breadth_first_order
    G = grade_graph(H, cost, cell, hard_max=gmax)
    order = breadth_first_order(G, start_rc[0] * H.shape[1] + start_rc[1], directed=False,
                                return_predecessors=False)
    m = np.zeros(H.size, bool)
    m[order] = True
    return m.reshape(H.shape)


def place_sites_hillside(H, cost, origin, cell, n_ore=3, avoid=None, fixed=None, margin_m=6.0, pad_m=20.0):
    """HILLSIDE quarry layout (benches cut into a hill face, plant on the floor at its base):
    crusher on the quarry floor (largest low flat area) on the side away from the face, beside the plant;
    ore = working faces on flat bench ground at different levels up the face (Lower/Middle/Upper Face);
    dump on flat low ground >= 60 m from the crusher; parking + fuel together on the floor >= 50 m from the
    crusher, away from the faces (fuel 20-50 m from parking)."""
    S = _site_setup(H, cost, origin, cell, margin_m, pad_m, avoid, fixed)
    comp, flat, clear, hn, sl = S.comp, S.flat, S.clear, S.hn, S.sl
    pick, pinned, clear_pad, dist = S.pick, S.pinned, S.clear_pad, S.dist
    gx, gz, _ = _plane_fit(H, comp, origin, cell)
    g = np.hypot(gx, gz) or 1.0
    X = origin[0] + (S.cc + 0.5) * cell
    Z = origin[1] + (S.rr + 0.5) * cell
    up = (X * gx + Z * gz) / g
    up = (up - up[comp].min()) / max(np.ptp(up[comp]), 1e-6)          # 0 = base of the face .. 1 = top
    floor_ref = float(np.percentile(H[comp], 5))
    floor = comp & flat & (H < floor_ref + 8.0)                     # quarry floor: low flat ground
    if floor.sum() < 50:
        floor = comp & (H < floor_ref + 8.0)
    floor_h = float(np.median(H[floor]))
    pk = lambda sid, score, sep, where=None: pinned[sid] if sid in pinned else pick(score, sep, where)

    # crusher: floor, on the side away from the face (where the plant is)
    rc_crusher = pk("crusher", 2 * clear_pad + 10.0 * (1 - up), S.infra_sep, floor)
    dcr = dist(rc_crusher)
    # working faces: flat bench ground on the face, at the toe of the bench above (steep ground within 15 m),
    # one per height band between floor + 8 m and the highest bench, with turning room (clear radius)
    from scipy import ndimage
    steep_d = ndimage.distance_transform_edt(~(sl > 30)) * cell
    bench_flat = comp & (sl < 10)                                    # bench floors in scans are rough / tilted
    bclear = ndimage.distance_transform_edt(np.pad(bench_flat, 1))[1:-1, 1:-1] * cell
    face_ground = bench_flat & (H > floor_h + 8.0)
    top = float(np.percentile(H[face_ground], 95)) if face_ground.any() else floor_h + 30
    # band 0 = toe of the lowest bench, on the floor; the rest split the face height above floor + 8 m
    bands = [bench_flat & (H >= floor_h - 3) & (H <= floor_h + 8.0) & (steep_d <= 12) & (S.edge >= 3)]
    edges = np.linspace(floor_h + 8.0, top, max(n_ore, 2))
    for i in range(n_ore - 1):
        bands.append(face_ground & (H >= edges[i]) & (H <= (edges[i + 1] if i < n_ore - 2 else 1e9)))
    # per band, prefer ground the crusher can reach without ever exceeding 10% (then 12, 15, 20 %, any)
    reach = {}
    faces, face_gmax = [], []

    def pick_face(i, where, extra_score=0.0):
        for gmax in (0.10, 0.12, 0.15, 0.20, None):
            if gmax not in reach:
                reach[gmax] = grade_reachable(H, cost, cell, rc_crusher, gmax) if gmax else comp
            for need in (10.0, 7.0, 5.0, 3.5):
                sc = 3 * np.minimum(bclear, 15) / 15 + 1.5 * (steep_d <= 15) + extra_score
                rc = pick(sc, max(35.0, 0.12 * S.ext), where & reach[gmax] & (bclear >= need) & (dcr >= 50),
                          strict=True)
                if rc is not None:
                    print(f"  face band {i}: y {H[rc]:.1f}, clear radius {bclear[rc]:.1f} m, crusher reachable at "
                          f"<= {round(gmax * 100) if gmax else 'any'}% grade")
                    return rc, gmax
        return None, None

    # lowest + highest band first, then every intermediate face is constrained to lie BETWEEN them: strictly
    # between in elevation, and in plan roughly on the way from the lower to the upper face (projection onto
    # the lower->upper segment in the middle of it, limited sideways offset)
    order_b = [0, len(bands) - 1] + list(range(1, len(bands) - 1)) if len(bands) > 1 else [0]
    got = {}
    for i in order_b[:2]:
        rc, gm = pick_face(i, bands[i])
        if rc is not None:
            got[i] = (rc, gm)
    if 0 in got and (len(bands) - 1) in got and len(bands) > 2:
        lo_rc, hi_rc = got[0][0], got[len(bands) - 1][0]
        h_lo, h_hi = float(H[lo_rc]), float(H[hi_rc])
        p_lo, p_hi = np.array(lo_rc, float), np.array(hi_rc, float)
        dv = p_hi - p_lo
        L2 = max(float(dv @ dv), 1e-9)
        t_seg = ((S.rr - p_lo[0]) * dv[0] + (S.cc - p_lo[1]) * dv[1]) / L2
        perp = np.abs((S.rr - p_lo[0]) * dv[1] - (S.cc - p_lo[1]) * dv[0]) / np.sqrt(L2) * cell
        seg_len = np.sqrt(L2) * cell
        up_lo, up_hi = float(up[lo_rc]), float(up[hi_rc])
        nm = len(bands) - 2
        for k, i in enumerate(order_b[2:]):
            f = (k + 1) / (nm + 1)                                  # target fraction lower -> upper
            h_t = h_lo + f * (h_hi - h_lo)
            margin = max(3.0, 0.15 * (h_hi - h_lo))
            between_h = (H > h_lo + margin) & (H < h_hi - margin)
            between_up = (up > min(up_lo, up_hi)) & (up < max(up_lo, up_hi))
            sc = -4.0 * np.abs(H - h_t) / max(h_hi - h_lo, 1e-6) - 2.0 * np.abs(t_seg - f)
            rc = None
            for tw, pw in ((0.2, 0.35), (0.15, 0.5), (0.1, 0.65)):
                win = between_h & between_up & (t_seg > f - 0.5 + tw) & (t_seg < f + 0.5 - tw) & \
                    (perp <= max(40.0, pw * seg_len))
                rc, gm = pick_face(i, bench_flat & win, sc)
                if rc is not None:
                    break
            if rc is None:                                          # last resort: elevation only
                rc, gm = pick_face(i, comp & between_h, sc)
            if rc is not None:
                got[i] = (rc, gm)
    for i in sorted(got):
        faces.append(got[i][0])
        face_gmax.append(got[i][1])
    while len(faces) < min(2, n_ore):                                # fallback: anything on the face
        faces.append(pick(3 * clear_pad + up, 30.0, face_ground))
    faces.sort(key=lambda rc: H[rc])
    nf = len(faces)
    names = {1: ["Working Face"], 2: ["Lower Face", "Upper Face"]}.get(
        nf, ["Lower Face", "Middle Face", "Upper Face"] + [f"Face {k}" for k in range(4, nf + 1)])
    sites = [{"id": f"ore_{i+1}", "type": "ore", "name": names[i], "pos": cell_to_world(rc, origin, cell),
              "tonnes": [8000, 6000, 4500, 4000][i % 4]} for i, rc in enumerate(faces)]
    near_face = np.min([dist(rc) for rc in faces], axis=0)
    # haul corridor: straight lines crusher -> each face; parking / fuel / dump keep off it
    def seg_dist(p, q):
        d = np.array(q, float) - np.array(p, float)
        t = np.clip(((S.rr - p[0]) * d[0] + (S.cc - p[1]) * d[1]) / max(d @ d, 1e-9), 0, 1)
        return np.hypot(S.rr - (p[0] + t * d[0]), S.cc - (p[1] + t * d[1])) * cell
    corridor = np.min([seg_dist(rc_crusher, rc) for rc in faces], axis=0)
    # ready line + fuel bay together on the floor, >= 50 m from the crusher, away from faces and haul corridor
    park_ok = floor & (dcr >= 50) & (corridor >= 15) & (near_face >= 40)
    rc_park = pk("parking", 3 * clear_pad + 4.0 * (1 - up) + np.minimum(corridor, 40) / 40, 20.0, park_ok)
    # fuel bay: short spur from the ready line -> use driving distance (grade-weighted) from parking
    from scipy.sparse.csgraph import dijkstra
    Gd = grade_graph(H, np.where(cost > 0, 1.0, -1.0), cell)
    dpk = dijkstra(Gd, indices=rc_park[0] * H.shape[1] + rc_park[1]).reshape(H.shape)
    ddu = np.full(H.shape, 1e3)                                     # dump not placed yet
    rc_fuel = pinned.get("fuel")
    low_ground = comp & (H < floor_ref + 8.0)
    for m in [floor & (dpk >= 20) & (dpk <= 50) & (dcr >= 50) & (corridor >= 15) & (near_face >= 40),
              low_ground & (dpk >= 15) & (dpk <= 60) & (dcr >= 50) & (ddu >= 25),
              low_ground & (dpk >= 15) & (dpk <= 80) & (dcr >= 45)]:
        if rc_fuel is None:
            rc_fuel = pick(3 * clear_pad - dpk / 50, 15.0, m, strict=True)
    if rc_fuel is None:
        rc_fuel = pick(3 * clear_pad - dpk / 20, 15.0, (dcr >= 50))
    # dump: low flat spoil ground (floor edge / bench end), far from the crusher, the ready line and the haul
    # corridor into the crusher
    dsc = 3 * clear_pad + np.minimum(dcr, 150) / 50 - 2.0 * hn + np.minimum(corridor, 40) / 20
    base = (dcr >= 60) & (dist(rc_park) >= 40) & (dist(rc_fuel) >= 30)
    rc_dump = pinned.get("dump")
    for m in [base & low_ground & (near_face >= 30), base & low_ground & (near_face >= 20),
              base & (hn < 0.5) & (near_face >= 35), base & (near_face >= 35), base & (near_face >= 20), base]:
        if rc_dump is None:
            rc_dump = pick(dsc, 30.0, m, strict=True)
    if rc_dump is None:
        rc_dump = pick(dsc, S.infra_sep, dcr >= 60)
    for sid, name, rc in [("crusher", "Primary Crusher", rc_crusher), ("dump", "Waste Dump", rc_dump),
                          ("parking", "Ready Line", rc_park), ("fuel", "Fuel Bay", rc_fuel)]:
        sites.append({"id": sid, "type": sid, "name": name, "pos": cell_to_world(rc, origin, cell)})
    print(f"  hillside: floor {floor.sum() * cell * cell:.0f} m2 at y {floor_h:.1f}; uphill dir "
          f"[{gx / g:.2f}, {gz / g:.2f}]; face band edges {np.round(edges, 1).tolist()}")
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


# ---------------------------------------------------------------- operating points (equipment, load / dump, slots)
def _heading(d):
    """heading_deg (0 = +z, clockwise) of a 2D direction [dx, dz]."""
    return round(float(np.degrees(np.arctan2(d[0], d[1]))) % 360, 1)


def _road_end_dir(world, sid, frm=None, back_m=8.0):
    """Unit direction a truck travels when it ARRIVES at site sid along the road graph (from node frm, default
    the first road touching sid), measured over the last ~back_m m of that road."""
    roads = world["roads"]
    road = None
    if frm is not None:
        rt = road_route(roads, frm, sid)
        if rt:
            road = next(r for r in roads if r["id"] == rt[-1])
    if road is None:
        road = next((r for r in roads if sid in (r["from"], r["to"])), None)
    if road is None:
        return None
    pts = np.array([[q[0], q[2]] for q in road["points"]])
    if road["from"] == sid:
        pts = pts[::-1]                                     # ordered toward sid
    end = pts[-1]
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts[::-1], axis=0), axis=1))])
    k = int(np.searchsorted(s, back_m))
    prev = pts[::-1][min(max(k, 1), len(pts) - 1)]
    d = end - prev
    return d / max(np.linalg.norm(d), 1e-6)


def _road_tree(world, step=0.5):
    from scipy.spatial import cKDTree
    pts = []
    for r in world["roads"]:
        P = np.array([[q[0], q[2]] for q in r["points"]])
        if len(P) >= 2:
            pts.append(resample(P, step))
        else:
            pts.append(P)
    return cKDTree(np.concatenate(pts))


def road_ends(world):
    return np.array([[r["points"][e][0], r["points"][e][2]] for r in world["roads"] for e in (0, -1)])


def add_operating_points(world, cost, heights, origin, cell, truck=None, hfun=None, slot_gap=None, ring=(8.0, 10.0),
                         end_tol=5.0):
    """Adds (in place) the driving-policy stop poses and the loading equipment, all on the navgrid:
      ore sites      -> "load_point": {pos3, heading_deg}  (road end at the face; heading = arrival direction)
      crusher / dump -> "dump_point": {pos3, heading_deg}  (road end at the tip; heading = arrival direction)
      parking        -> "slots": [{pos3, heading_deg} x n_trucks]  (side by side, facing out along the exit road)
      world["equipment"] -> one excavator per ore site, `ring` m from its load point toward the highwall / uphill,
                         off the road centrelines, beside (not ahead of / behind) the truck, facing the load point.
    Trucks' spawn / heading are set to the slots. cost / heights: navgrid arrays [rows=z, cols=x] (cell centres).
    Returns the list of problems from check_operating_points (empty = OK)."""
    from scipy import ndimage
    if hfun is None:
        hfun = lambda xz: bilinear(heights, origin, cell, xz, centred=True)
    h, w = cost.shape
    sites = {s["id"]: s for s in world["sites"]}
    park = next(s for s in world["sites"] if s["type"] == "parking")
    reach = reachable_from(cost, world_to_cell(park["pos"], origin, cell))
    sl = slope_deg(heights, cell)
    d_imp = ndimage.distance_transform_edt(np.pad(cost > 0, 1))[1:-1, 1:-1] * cell   # m to impassable / border
    tree = _road_tree(world)

    def cell_ok(xz):
        r, c = world_to_cell(xz, origin, cell)
        return 0 <= r < h and 0 <= c < w and bool(reach[r, c]), (r, c)

    def pose(xz, d):
        return {"pos3": pos3(xz, hfun), "heading_deg": _heading(d)}

    crusher = next((s for s in world["sites"] if s["type"] == "crusher"), None)
    ores = [s for s in world["sites"] if s["type"] == "ore"]
    first_ore = ores[0]["id"] if ores else None
    for s in world["sites"]:
        if s["type"] == "ore":
            d = _road_end_dir(world, s["id"], crusher["id"] if crusher else None)
            s["load_point"] = pose(s["pos"], d if d is not None else [0.0, 1.0])
        elif s["type"] in ("crusher", "dump"):
            d = _road_end_dir(world, s["id"], first_ore)
            s["dump_point"] = pose(s["pos"], d if d is not None else [0.0, 1.0])

    # ---- excavators
    equipment = []
    rads = np.arange(ring[0], ring[1] + 1e-6, 0.5)
    angs = np.radians(np.arange(0, 360, 7.5))
    for k, s in enumerate(ores):
        lp = np.array(s["load_point"]["pos3"])[[0, 2]]
        fwd = _road_end_dir(world, s["id"], crusher["id"] if crusher else None)
        fwd = np.array([0.0, 1.0]) if fwd is None else fwd
        y0 = float(hfun([lp])[0])
        # highwall = steep / impassable ground rising above the bay; drop-offs = ground falling away below it
        steep = (cost <= 0) | (sl > 25)
        d_wall = ndimage.distance_transform_edt(~(steep & (heights > y0 + 3.0))) * cell
        d_drop = ndimage.distance_transform_edt(~(steep & (heights < y0 - 3.0))) * cell
        best = None
        tiers = ((7.5, (60, 130), 2.5, 4.0), (6.5, (50, 140), 2.0, 4.0), (6.0, (45, 150), 1.5, 3.0),
                 (5.5, (40, 160), 1.5, 2.0), (5.0, (30, 170), 1.0, 0.0))
        for road_clear, (amin, amax), imp_min, drop_min in tiers:
            cands = []
            for rad in rads:
                for a in angs:
                    q = lp + rad * np.array([np.sin(a), np.cos(a)])      # a: heading-style angle
                    ok, (r, c) = cell_ok(q)
                    if not ok or sl[r, c] > 12.0:
                        continue
                    phi = np.degrees(np.arccos(np.clip((q - lp) @ fwd / rad, -1, 1)))   # 0 = dead ahead
                    if not (amin <= phi <= amax):
                        continue
                    dr, _ = tree.query(q)
                    if dr < road_clear:
                        continue
                    dy = float(hfun([q])[0]) - y0
                    if abs(dy) > 4.0:                                      # toe of the face: ~level with the bay
                        continue
                    if d_imp[r, c] < imp_min or d_drop[r, c] < drop_min:   # own footprint; not on a crest
                        continue
                    score = (3.0 * (1 - min(d_wall[r, c], 25.0) / 25.0)     # toward the highwall above the bay
                             - 2.0 * (1 - min(d_drop[r, c], 10.0) / 10.0)   # away from crest / drop-offs
                             + 0.4 * np.clip(dy, -2, 3)                     # uphill side
                             - 0.08 * sl[r, c] - 0.15 * abs(rad - 9.0)
                             + 1.0 * np.sin(np.radians(phi)) + 0.02 * min(dr, 15.0))
                    cands.append((score, q))
            if cands:
                best = max(cands, key=lambda t: t[0])[1]
                break
        if best is None:
            print(f"  WARNING: no excavator spot for {s['id']}")
            continue
        equipment.append({"id": f"exc-{k}", "type": "excavator", "name": f"Excavator {k + 1}", "face": s["id"],
                          "pos3": pos3(best, hfun), "heading_deg": _heading(lp - best), "model": "excavator"})
    world["equipment"] = equipment

    # ---- parking slots (side by side, facing along the exit road), trucks spawn on them
    n = max(len(world.get("trucks", [])), 3)
    out = _road_end_dir(world, park["id"])                     # arrival direction -> exit is the reverse
    ex = -out if out is not None else np.array([0.0, 1.0])
    side = np.array([ex[1], -ex[0]])
    tw = getattr(truck, "width_m", 4.24)
    gap = slot_gap or max(tw + 0.6, min(max(6.0, 1.6 * tw), 2 * (end_tol - 0.1) / max(n - 1, 1)))
    p0 = np.array(park["pos"], float)
    best = None
    for back in (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0):          # slide the row along the exit if a slot is blocked
        for g in (gap, 0.95 * gap):
            row = [p0 + ex * back + side * g * (i - (n - 1) / 2) for i in range(n)]
            if all(cell_ok(q)[0] for q in row):
                best = row
                break
        if best:
            break
    if best is None:
        best = [p0 + side * gap * (i - (n - 1) / 2) for i in range(n)]
    park["slots"] = [pose(q, ex) for q in best]
    for i, (t, sl_) in enumerate(zip(world.get("trucks", []), park["slots"])):
        t["spawn"] = [sl_["pos3"][0], sl_["pos3"][2]]
        t["heading_deg"] = sl_["heading_deg"]
        t["slot"] = i
    return check_operating_points(world, cost, origin, cell, end_tol=max(end_tol, gap * (n - 1) / 2 + 0.05))


def check_operating_points(world, cost, origin, cell, end_tol=5.0, road_clear=5.0):
    """Problems with load/dump points, parking slots and excavators (empty list = OK)."""
    h, w = cost.shape
    park = next(s for s in world["sites"] if s["type"] == "parking")
    reach = reachable_from(cost, world_to_cell(park["pos"], origin, cell))
    ends = road_ends(world)
    tree = _road_tree(world)
    errs = []

    def on_grid(p, what):
        r, c = world_to_cell([p[0], p[2]], origin, cell)
        if not (0 <= r < h and 0 <= c < w and reach[r, c]):
            errs.append(f"{what} at {p} is not on a reachable passable cell")
            return False
        return True

    for s in world["sites"]:
        pts = [("load_point", s.get("load_point"))] if s["type"] == "ore" else \
            [("dump_point", s.get("dump_point"))] if s["type"] in ("crusher", "dump") else \
            [(f"slots[{i}]", q) for i, q in enumerate(s.get("slots", []))] if s["type"] == "parking" else []
        if s["type"] == "parking" and len(s.get("slots", [])) < len(world.get("trucks", [])):
            errs.append(f"parking has {len(s.get('slots', []))} slots for {len(world['trucks'])} trucks")
        for name, q in pts:
            if q is None:
                errs.append(f"{s['id']}: missing {name}")
                continue
            p = q["pos3"]
            on_grid(p, f"{s['id']}.{name}")
            de = float(np.min(np.hypot(ends[:, 0] - p[0], ends[:, 1] - p[2])))
            if de > end_tol:
                errs.append(f"{s['id']}.{name} is {de:.1f} m from the nearest road end (> {end_tol:.1f})")
    for i, (t, q) in enumerate(zip(world.get("trucks", []), park.get("slots", []))):
        if np.hypot(t["spawn"][0] - q["pos3"][0], t["spawn"][1] - q["pos3"][2]) > 0.05:
            errs.append(f"{t['id']} spawn != parking slot {i}")
    ores = {s["id"] for s in world["sites"] if s["type"] == "ore"}
    faces = [e["face"] for e in world.get("equipment", []) if e["type"] == "excavator"]
    if sorted(faces) != sorted(ores):
        errs.append(f"excavators cover {sorted(faces)}, ore faces {sorted(ores)}")
    for e in world.get("equipment", []):
        on_grid(e["pos3"], e["id"])
        dr, _ = tree.query([e["pos3"][0], e["pos3"][2]])
        if dr < road_clear:
            errs.append(f"{e['id']} is {dr:.1f} m from a road centreline (< {road_clear})")
    return errs


def check_face_order(world):
    """Hillside: Middle Face must lie between Lower and Upper Face in elevation, in road distance from the
    crusher and (roughly) in plan. Returns a list of problems and a summary dict."""
    by = {s["name"]: s for s in world["sites"] if s["type"] == "ore"}
    if not all(k in by for k in ("Lower Face", "Middle Face", "Upper Face")):
        return [], {}
    L, M, U = by["Lower Face"], by["Middle Face"], by["Upper Face"]
    roads = {r["id"]: r for r in world["roads"]}
    crusher = next(s for s in world["sites"] if s["type"] == "crusher")["id"]

    def route_len(sid):
        rt = road_route(world["roads"], crusher, sid)
        if rt is None:
            return float("inf")
        return float(sum(np.sum(np.hypot(np.diff(np.array(roads[r]["points"])[:, 0]),
                                         np.diff(np.array(roads[r]["points"])[:, 2]))) for r in rt))
    dl, dm, du = route_len(L["id"]), route_len(M["id"]), route_len(U["id"])
    pl, pm, pu = (np.array(s["pos"], float) for s in (L, M, U))
    seg = pu - pl
    t = float((pm - pl) @ seg / max(seg @ seg, 1e-9))
    perp = float(abs((pm - pl)[0] * seg[1] - (pm - pl)[1] * seg[0]) / max(np.linalg.norm(seg), 1e-9))
    errs = []
    if not (L["pos3"][1] < M["pos3"][1] < U["pos3"][1]):
        errs.append(f"elevation not Lower < Middle < Upper: {L['pos3'][1]}, {M['pos3'][1]}, {U['pos3'][1]}")
    if not (dl < dm < du):
        errs.append(f"route distance from crusher not Lower < Middle < Upper: {dl:.0f}, {dm:.0f}, {du:.0f}")
    if not (0.1 < t < 0.9) or perp > max(40.0, 0.65 * np.linalg.norm(seg)):
        errs.append(f"Middle Face not between in plan: t {t:.2f} along Lower->Upper, {perp:.0f} m sideways")
    return errs, {"y": [L["pos3"][1], M["pos3"][1], U["pos3"][1]], "route_m": [round(dl), round(dm), round(du)],
                  "plan_t": round(t, 2), "plan_offset_m": round(perp, 1)}


# ---------------------------------------------------------------- grade-aware road network (least-cost tree)
def grade_graph(H, cost, cell, max_grade=0.10, smooth=1.0, k1=100.0, k2=2000.0, hard_max=None):
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
        if dr and dc:  # corner rule: a diagonal step needs both orthogonal neighbours passable
            ok &= (cost[slice(r0 + dr, r1 + dr), slice(c0, c1)] > 0) & (cost[slice(r0, r1), slice(c0 + dc, c1 + dc)] > 0)
        L = np.hypot(dr, dc) * cell
        g = np.abs(Hs[b] - Hs[a]) / L
        if hard_max is not None:
            ok &= g <= hard_max
        ex = np.maximum(g - max_grade, 0.0)
        wt = L * 0.5 * (cost[a] + cost[b]) * (1.0 + k1 * ex + k2 * ex ** 2)
        rows.append(idx[a][ok]); cols.append(idx[b][ok]); wts.append(wt[ok])
    n = h * w
    return sparse.csr_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))


def build_road_network(H, cost, origin, cell, sites, road_width=15.0, ramp_width=16.5, max_grade=0.10,
                       order=None, snap_m=12.0, spur_to=None):
    """Connect sites into a tree of roads by repeatedly joining the next site to the existing network along
    its least-cost (grade-aware) path; joining mid-road splits that road at a new junction.

    spur_to: {site_id: node_id} forces that site to connect only to the given node (short spur).
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
        if spur_to and sid in spur_to and spur_to[sid] in node_cell:   # e.g. fuel bay only via the ready line
            own = {node_cell[spur_to[sid]]: ("node", spur_to[sid])}
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
        raw = np.array([cell_to_world(divmod(f, w), origin, cell) for f in path], float)
        raw[0], raw[-1] = node_pos[a], node_pos[b]
        ok = lambda P: polyline_valid(P, cost, origin, cell)
        xz = safe_smooth(raw, ok, sigma=1.0)
        xz = simplify_valid(xz, cost, origin, cell, step=5.0, tol=0.3 * cell)
        if not ok(xz):
            print(f"  WARNING road {a}->{b}: centreline crosses impassable cells")
        y = hfun(xz)
        dh = float(abs(y[-1] - y[0]))
        L = float(np.linalg.norm(np.diff(xz, axis=0), axis=1).sum())
        types = {by[n]["type"] for n in (a, b) if n in by}
        typ = "ramp" if dh >= 8 and dh / max(L, 1) > 0.03 else ("spur" if types & {"parking", "fuel"} and L < 120
                                                                 else "haul")
        name = lambda n: by[n]["name"] if n in by else f"Junction {n.split('_')[1]}"
        rid = f"{typ}_{k + 1}"
        out.append(road_entry(rid, f"{name(a)} - {name(b)}", typ, a, b, xz, hfun,
                              ramp_width if typ == "ramp" else road_width, step=None))
    for st in sites:
        st["pos3"] = pos3(st["pos"], hfun)
    jl = [{"id": j, "name": f"Junction {j.split('_')[1]}", "pos": pos3(node_pos[j], hfun)} for j in junctions]
    # road surface mask (centreline buffered to half width), passable cells only
    d = ndimage.distance_transform_edt(~centre.reshape(h, w)) * cell
    mask = (d <= road_width / 2) & (cost > 0)
    return jl, out, mask


def polyline_valid(P, cost, origin, cell, step=None, return_bad=False):
    """True if every point along the polyline (sampled every cell/10, after rounding vertices to 0.01 m as
    the JSON does) lies in a passable cell. return_bad: also the per-segment validity array."""
    P = np.round(np.asarray(P, float), 2)
    step = step or cell / 10
    h, w = cost.shape
    seg_ok = np.ones(max(len(P) - 1, 0), bool)
    for i in range(len(P) - 1):
        L = float(np.hypot(*(P[i + 1] - P[i])))
        t = np.linspace(0, 1, max(2, int(np.ceil(L / step)) + 1))[:, None]
        q = P[i] + t * (P[i + 1] - P[i])
        r = np.floor((q[:, 1] - origin[1]) / cell).astype(int)
        c = np.floor((q[:, 0] - origin[0]) / cell).astype(int)
        inside = (r >= 0) & (r < h) & (c >= 0) & (c < w)
        seg_ok[i] = inside.all() and bool((cost[r.clip(0, h - 1), c.clip(0, w - 1)] > 0).all())
    return (bool(seg_ok.all()), seg_ok) if return_bad else bool(seg_ok.all())


def safe_smooth(raw, ok, sigma=1.0, max_iter=20):
    """Gaussian-smooth a cell-centre path, but fall back to the raw (valid) vertices wherever a smoothed
    segment would enter an impassable cell; the fallback window grows until the whole line is valid."""
    from scipy.ndimage import gaussian_filter1d, binary_dilation
    if len(raw) <= 4:
        return raw
    sm = gaussian_filter1d(raw, sigma=sigma, axis=0, mode="nearest")
    sm[0], sm[-1] = raw[0], raw[-1]
    use_raw = np.zeros(len(raw), bool)
    for _ in range(max_iter):
        P = np.where(use_raw[:, None], raw, sm)
        valid, seg_ok = _seg_check(ok, P)
        if valid:
            return P
        bad = np.zeros(len(raw), bool)
        bad[:-1] |= ~seg_ok
        bad[1:] |= ~seg_ok
        use_raw |= binary_dilation(bad, iterations=int(np.ceil(3 * sigma)))
    return raw


def _seg_check(ok, P):
    """ok is polyline_valid bound to a grid; evaluate per segment."""
    seg = np.array([ok(P[i:i + 2]) for i in range(len(P) - 1)], bool)
    return bool(seg.all()), seg


def simplify_valid(P, cost, origin, cell, step=5.0, tol=None):
    """Drop vertices greedily: keep a chord i->j only if it is <= step m long, stays within tol of the
    dropped vertices (bends survive) and is itself passable. Endpoints are kept exactly."""
    P = np.asarray(P, float)
    tol = 0.3 * cell if tol is None else tol
    keep, i, n = [0], 0, len(P)
    while i < n - 1:
        j_best = i + 1
        for j in range(i + 2, n):
            ch = P[j] - P[i]
            L = float(np.hypot(*ch))
            if L > step:
                break
            mid = P[i + 1:j] - P[i]
            dev = np.abs(mid[:, 0] * ch[1] - mid[:, 1] * ch[0]) / max(L, 1e-9)
            if dev.max() > tol or not polyline_valid(P[[i, j]], cost, origin, cell):
                break
            j_best = j
        keep.append(j_best)
        i = j_best
    return P[keep]


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


def road_route(roads, a, b):
    """Road ids along the (tree) road graph from node a to node b, or None."""
    from collections import deque
    adj = {}
    for r in roads:
        adj.setdefault(r["from"], []).append((r["to"], r["id"]))
        adj.setdefault(r["to"], []).append((r["from"], r["id"]))
    prev, q = {a: None}, deque([a])
    while q:
        u = q.popleft()
        for v, rid in adj.get(u, []):
            if v not in prev:
                prev[v] = (u, rid)
                q.append(v)
    if b not in prev:
        return None
    out, k = [], b
    while prev[k] is not None:
        k, rid = prev[k]
        out.append(rid)
    return out[::-1]


def haul_cycle_roads(world):
    """Road ids on any ore -> crusher / ore -> dump route."""
    types = {s["id"]: s["type"] for s in world["sites"]}
    ids = set()
    for o in [k for k, t in types.items() if t == "ore"]:
        for d in [k for k, t in types.items() if t in ("crusher", "dump")]:
            ids |= set(road_route(world["roads"], o, d) or [])
    return ids



# ---------------------------------------------------------------- road corridors + footprint-aware nav check
NAV_MIN_CORRIDOR_M = 10.0      # every road must leave at least this much clear width in the navgrid
NAV_PAD_RADIUS_M = 12.0        # open pad around every site / load / dump point / parking slot (8.5 m turning radius)
NAV_MARGIN_M = 0.5             # clearance added to the truck half-width when eroding the passable mask


def _cell_centres(shape, origin, cell):
    h, w = shape
    X = origin[0] + (np.arange(w) + 0.5) * cell
    Z = origin[1] + (np.arange(h) + 0.5) * cell
    return np.meshgrid(X, Z)


def road_polylines(world, step=0.25):
    """2D centrelines of every road, resampled every `step` m: [(road, (N, 2) array)]."""
    out = []
    for r in world.get("roads", []):
        P = np.array([[q[0], q[2]] for q in r["points"]], float)
        out.append((r, resample(P, step) if len(P) >= 2 else P))
    return out


def nav_points(world):
    """{label: [x, z]} for every site pos, load / dump point and parking slot."""
    pts = {}
    for s in world["sites"]:
        pts[s["id"]] = list(s["pos"])
        for k in ("load_point", "dump_point"):
            if s.get(k):
                pts[f"{s['id']}.{k}"] = [s[k]["pos3"][0], s[k]["pos3"][2]]
        for i, q in enumerate(s.get("slots", [])):
            pts[f"{s['id']}.slots[{i}]"] = [q["pos3"][0], q["pos3"][2]]
    return pts


NAV_PAD_MAX_DH_M = 3.0         # pads only open ground within this height of the pad centre (no bench faces)


def carve_road_corridors(cost, origin, cell, world, forbid=None, heights=None, min_width=NAV_MIN_CORRIDOR_M,
                         pad_radius=NAV_PAD_RADIUS_M, pad_max_dh=NAV_PAD_MAX_DH_M, road_cost=1.0):
    """Open the drivable road corridors in the navgrid (returns a new cost array; heights are untouched).

    Every cell whose centre lies within max(width_m, min_width)/2 of a road centreline (plan distance), or within
    pad_radius of a site / load point / dump point / parking slot, becomes passable with road_cost, even where
    the slope rule marked it impassable (scan noise, vegetation, steep cells beside a cut road). Cells in
    `forbid` (bool grid, e.g. outside the scan footprint / no-scan border) are never opened. With `heights`
    (navgrid cell-centre heights), a pad only opens cells within pad_max_dh of the height at its centre, so a pad
    at a bench bay does not turn the bench face behind it into a 'road' linking two levels."""
    from scipy.spatial import cKDTree
    cost = np.array(cost, float, copy=True)
    X, Z = _cell_centres(cost.shape, origin, cell)
    Q = np.stack([X.ravel(), Z.ravel()], 1)
    open_ = np.zeros(cost.size, bool)
    for r, P in road_polylines(world):
        rad = max(float(r.get("width_m") or 0.0), min_width) / 2
        lo, hi = P.min(0) - rad - cell, P.max(0) + rad + cell
        box = np.all((Q >= lo) & (Q <= hi), axis=1)
        d, _ = cKDTree(P).query(Q[box], distance_upper_bound=rad + 1e-6)
        idx = np.nonzero(box)[0]
        open_[idx[np.isfinite(d)]] = True
    pts = np.array(list(nav_points(world).values()), float)
    Hf = None if heights is None else np.asarray(heights, float).ravel()
    for p in pts:
        near = np.hypot(Q[:, 0] - p[0], Q[:, 1] - p[1]) <= pad_radius + 1e-6
        if Hf is not None:
            r, c = world_to_cell(p, origin, cell)
            r, c = min(max(r, 0), cost.shape[0] - 1), min(max(c, 0), cost.shape[1] - 1)
            near &= np.abs(Hf - Hf[r * cost.shape[1] + c]) <= pad_max_dh
        open_ |= near
    open_ = open_.reshape(cost.shape)
    if forbid is not None:
        open_ &= ~forbid
    cost[open_] = road_cost
    return cost


def _corner_rule_labels(mask):
    """Connected components of `mask` under 8-connectivity with the corner rule (a diagonal step needs both
    orthogonal neighbours in the mask), same as the A* / road builder. Returns labels (-1 outside mask)."""
    from scipy.sparse.csgraph import connected_components
    G = grade_graph(np.zeros(mask.shape), np.where(mask, 1.0, -1.0), 1.0, smooth=0)
    _, lab = connected_components(G, directed=False)
    return np.where(mask.ravel(), lab, -1).reshape(mask.shape)


def corridor_clearance(cost, origin, cell, P, search=20.0):
    """Plan distance (m) from each point in P (N, 2) to the nearest impassable cell (as a square) or to the
    grid edge, capped at `search`."""
    from scipy.spatial import cKDTree
    h, w = cost.shape
    rr, cc = np.nonzero(cost <= 0)
    # the outside of the grid counts as blocked: add a ring of virtual cells around it
    ring_c = np.concatenate([np.arange(-1, w + 1), np.arange(-1, w + 1), np.full(h, -1), np.full(h, w)])
    ring_r = np.concatenate([np.full(w + 2, -1), np.full(w + 2, h), np.arange(h), np.arange(h)])
    cx = origin[0] + (np.concatenate([cc, ring_c]) + 0.5) * cell
    cz = origin[1] + (np.concatenate([rr, ring_r]) + 0.5) * cell
    B = np.stack([cx, cz], 1)
    tree = cKDTree(B)
    out = np.full(len(P), search)
    for i, nb in enumerate(tree.query_ball_point(P, search + cell)):
        if nb:
            dx = np.maximum(np.abs(B[nb, 0] - P[i, 0]) - cell / 2, 0)
            dz = np.maximum(np.abs(B[nb, 1] - P[i, 1]) - cell / 2, 0)
            out[i] = min(search, float(np.hypot(dx, dz).min()))
    return out


def navgrid_check(world, cost, origin, cell, truck_width=None, margin=NAV_MARGIN_M, min_width=NAV_MIN_CORRIDOR_M):
    """Footprint-aware navigability check of a navgrid against the world's sites and roads.

    1. Erode the passable mask by a disk of radius truck_width/2 + margin (a cell survives when its centre is at
       least that far from every impassable cell / the grid edge), so the eroded grid is where the truck's
       centre can be without its body touching blocked ground.
    2. Every pair of sites, and every pair of all nav points (sites + load / dump points + parking slots), must
       be connected in the eroded mask (8-connected, corner rule).
    3. Minimum corridor width along each road = 2 x the minimum clearance (centre -> nearest blocked cell) sampled
       every 0.5 m along its centreline; must be >= min_width.
    Returns (problems, summary dict for world.design.navgrid_check)."""
    from itertools import combinations
    if truck_width is None:
        truck_width = float(world.get("design", {}).get("design_truck", {}).get("width_m", 4.24))
    rad = truck_width / 2 + margin
    h, w = cost.shape
    X, Z = _cell_centres(cost.shape, origin, cell)
    clr = corridor_clearance(cost, origin, cell, np.stack([X.ravel(), Z.ravel()], 1),
                             search=rad + 2 * cell).reshape(cost.shape)
    eroded = (cost > 0) & (clr >= rad)
    lab = _corner_rule_labels(eroded)
    pts = nav_points(world)
    comp = {}
    for k, p in pts.items():
        r, c = world_to_cell(p, origin, cell)
        comp[k] = int(lab[r, c]) if (0 <= r < h and 0 <= c < w) else -1
    errs = [f"{k} at {pts[k]} is not drivable for a {truck_width:.2f} m truck (+{margin} m margin)"
            for k, v in comp.items() if v < 0]
    site_ids = [s["id"] for s in world["sites"]]
    sp = list(combinations(site_ids, 2))
    s_ok = [(a, b) for a, b in sp if comp[a] >= 0 and comp[a] == comp[b]]
    ap = list(combinations(list(pts), 2))
    a_ok = [(a, b) for a, b in ap if comp[a] >= 0 and comp[a] == comp[b]]
    if len(s_ok) < len(sp):
        errs.append(f"site pairs connected {len(s_ok)}/{len(sp)}; not: "
                    + ", ".join(f"{a}-{b}" for a, b in sp if (a, b) not in s_ok))
    if len(a_ok) < len(ap):
        errs.append(f"nav point pairs connected {len(a_ok)}/{len(ap)}")
    widths = {}
    for r, P in road_polylines(world, step=0.5):
        wmin = 2 * float(corridor_clearance(cost, origin, cell, P, search=max(min_width, 20.0)).min())
        widths[r["id"]] = round(wmin, 1)
        if wmin < min_width - 1e-6:
            errs.append(f"road {r['id']}: min navgrid corridor width {wmin:.1f} m < {min_width} m")
    summary = {"truck_width_m": truck_width, "erosion_radius_m": round(rad, 2), "min_required_width_m": min_width,
               "min_corridor_width_m": widths,
               "pairs_connected": f"{len(s_ok)}/{len(sp)}", "nav_point_pairs_connected": f"{len(a_ok)}/{len(ap)}",
               "nav_points": len(pts),
               "method": "passable mask (cost > 0) eroded by truck_width/2 + margin; pairs connected 8-way with the "
                         "corner rule; corridor width = 2 x min centreline clearance to the nearest blocked cell"}
    return errs, summary
