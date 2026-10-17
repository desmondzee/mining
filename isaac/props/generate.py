import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geometry as G  # noqa: E402
import procedural as P  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WORLD_DIR = REPO / "data/worlds/quarry"
ASSETS_DIR = REPO / "isaac/assets/props"

SEED = 20261017
TOE_BOULDERS = 70
BERM_BOULDERS = 35
SPOIL_BOULDERS = 30
ISOLATED_BOULDERS = 15
DEBRIS_ROCKS = 400
ROCK_PILES = 8
PILE_BOULDERS = (4, 7)

BOULDER_D = (0.6, 1.5)
ISOLATED_D = (1.5, 3.0)
DEBRIS_D = (0.12, 0.45)
BURY = (0.15, 0.4)
TILT_FOLLOW = 0.6
OBSTACLE_MIN_R = 0.5

CORRIDOR_MARGIN = 2.0
PAD_RADIUS = 12.0
CROSSING_CLEAR = 2.5
EQUIPMENT_CLEAR = 10.0
QUARRY_REACH = 60.0
QUARRY_MAX_Y = -30.0
MAX_SLOPE_DEG = 32.0

BERM_HEIGHT = 1.5
BERM_BASE = 4.0
BERM_TOP = 1.0
BERM_STEP = 1.0
BERM_TOE_GAP = 0.5
BERM_DROP = 3.0
BERM_MAX_FILL = 6.0
BERM_PROBE = 20.0
BERM_JUNCTION_GAP = 25.0
BERM_SITE_GAP = 26.0
BERM_CROSSING_GAP = 5.0
BERM_MIN_RUN = 12.0
BERM_FILL_GAP = 6.0

POLYHAVEN_ROCKS = ["namaqualand_boulder_02", "namaqualand_boulder_03", "namaqualand_boulder_04",
                   "namaqualand_boulder_05", "namaqualand_boulder_06", "rock_07"]
PROCEDURAL_ROCKS = 8
PROCEDURAL_MOUNDS = 4
ROCK_MATERIALS = ["rock", "rock_warm", "rock_grey"]
SIGNS = {"speed40": "SPEED\nLIMIT\n40", "pedestrians": "PEDESTRIAN\nCROSSING"}


def rock_asset(i: int) -> str:
    return f"procedural/rock_{i:02d}.usdc"


def build_assets(out: Path) -> None:
    import usdio
    from PIL import Image, ImageDraw, ImageFont
    proc = out / "procedural"
    for i in range(PROCEDURAL_ROCKS):
        v0, f0 = P.rock(1000 + i, subdiv=4)
        v1, f1 = P.rock(1000 + i, subdiv=2)
        mat = ROCK_MATERIALS[i % len(ROCK_MATERIALS)]
        lods = {"lod0": [{"name": "mesh", "v": v0, "f": f0, "uv": P.box_uv(v0, f0, 0.5), "normals": P.vertex_normals(v0, f0), "material": mat}],
                "lod1": [{"name": "mesh", "v": v1, "f": f1, "uv": P.box_uv(v1, f1, 0.5), "normals": P.vertex_normals(v1, f1), "material": mat}]}
        usdio.write_asset(proc / f"rock_{i:02d}.usdc", f"rock_{i:02d}", [], (v1, f1), "../textures", lods=lods)
    for i in range(PROCEDURAL_MOUNDS):
        v, f = P.mound(2000 + i, 1.0, 0.45)
        usdio.write_asset(proc / f"mound_{i}.usdc", f"mound_{i}", [{"name": "mesh", "v": v, "f": f, "material": "dirt",
                          "uv": P.box_uv(v, f, 0.25), "normals": P.vertex_normals(v, f)}], (v, f), "../textures",
                          approximation="convexHull")
    c = P.cone()
    usdio.write_asset(proc / "cone.usdc", "cone", [{"name": n, "v": v, "f": f, "material": m} for n, v, f, m in c["parts"]],
                      P.merge([(v, f) for _, v, f, _ in c["parts"]]), "../textures")
    t = P.fuel_tank()
    usdio.write_asset(proc / "fuel_tank.usdc", "fuel_tank", [{"name": n, "v": v, "f": f, "material": m} for n, v, f, m in t["parts"]],
                      P.merge([(v, f) for _, v, f, _ in t["parts"]]), "../textures", approximation="convexDecomposition")
    tex = out / "textures"
    for key, text in SIGNS.items():
        img = Image.new("RGB", (512, 512), (250, 250, 245) if key == "speed40" else (250, 200, 20))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 501, 501], outline=(20, 20, 20), width=14)
        if key == "speed40":
            d.ellipse([60, 150, 452, 500 - 8], outline=(200, 20, 20), width=40)
            d.text((256, 82), "SPEED LIMIT", fill=(20, 20, 20), anchor="mm", font=ImageFont.load_default(size=58))
            d.text((256, 325), "40", fill=(20, 20, 20), anchor="mm", font=ImageFont.load_default(size=170))
        else:
            d.multiline_text((256, 256), text, fill=(20, 20, 20), anchor="mm", align="center", spacing=30,
                             font=ImageFont.load_default(size=64))
        img.save(tex / f"sign_{key}.jpg", quality=88)
        s = P.sign()
        parts = []
        for n, v, f, m in s["parts"]:
            part = {"name": n, "v": v, "f": f, "material": m if m != "sign_face" else f"sign_{key}"}
            if m == "sign_face":
                part["uv"] = s["face_uv"]
            parts.append(part)
        usdio.write_asset(proc / f"sign_{key}.usdc", f"sign_{key}", parts, P.merge([(v, f) for _, v, f, _ in s["parts"]]),
                          "../textures", materials={f"sign_{key}": {"files": {"diff": f"../textures/sign_{key}.jpg"}, "roughness": 0.4}})


def unit_heights(assets: Path) -> dict[str, float]:
    out = {rock_asset(i): float(np.ptp(P.rock(1000 + i, subdiv=2)[0][:, 2])) for i in range(PROCEDURAL_ROCKS)}
    manifest = assets / "polyhaven/manifest.json"
    if manifest.exists():
        for k, m in json.loads(manifest.read_text()).items():
            out[m["file"]] = m["native_m"][2] * m["scale_to_unit"]
    return out


class Site:
    def __init__(self, world_dir: Path, assets: Path, seed: int) -> None:
        from terrain import Terrain
        self.terrain = Terrain(world_dir)
        self.world = self.terrain.world
        self.roads = G.Roads(self.world)
        self.crossings = G.crossing_segments(self.world)
        self.pads = np.array(list(G.nav_points(self.world).values()), float)
        self.junctions = np.array([[j["pos"][0], j["pos"][2]] for j in self.world["junctions"]], float)
        self.equipment = np.array([[e["pos3"][0], e["pos3"][2]] for e in self.world.get("equipment", [])], float).reshape(-1, 2)
        self.bounds = self.world["bounds"]
        self.rng = np.random.default_rng(seed)
        self.heights = unit_heights(assets)
        self.rocks = [f"polyhaven/{a}.usdc" for a in POLYHAVEN_ROCKS if f"polyhaven/{a}.usdc" in self.heights]
        self.placed: list[dict] = []
        self.disks: list[tuple[float, float, float]] = []
        self.radii: list[float] = []
        self.berm_lines: list[np.ndarray] = []
        self.berm_tree = None
        self.counter = 0

    def clear(self, xz: np.ndarray, r: float, crossing: float = CROSSING_CLEAR, overlap: float = 0.9) -> bool:
        x, z = xz
        b = self.bounds
        if not (b[0] + r < x < b[2] - r and b[1] + r < z < b[3] - r):
            return False
        if self.roads.clearance(xz)[0] < CORRIDOR_MARGIN + r:
            return False
        if np.min(np.hypot(*(self.pads - xz).T)) < PAD_RADIUS + r:
            return False
        if len(self.equipment) and np.min(np.hypot(*(self.equipment - xz).T)) < EQUIPMENT_CLEAR + r:
            return False
        if any(G.segment_distance(xz, s)[0] < crossing + r for s in self.crossings):
            return False
        if self.berm_tree is not None and self.berm_tree.query(xz)[0] < BERM_BASE / 2 + r * 0.5:
            return False
        for px, pz, pr in self.disks:
            if (px - x) ** 2 + (pz - z) ** 2 < ((pr + r) * overlap) ** 2:
                return False
        return True

    def in_quarry(self, xz: np.ndarray) -> bool:
        return float(self.roads.distances(xz).min()) < QUARRY_REACH

    def add(self, kind: str, asset: str, xz, y: float, rot: np.ndarray, scale, r: float, obstacle: bool,
            fallback: str | None = None, fallback_scale=None, disk: float | None = None) -> dict:
        self.counter += 1
        e = {"id": f"{kind}_{self.counter:04d}", "kind": kind, "asset": asset, "fallback": fallback,
             "pos": [round(float(xz[0]), 3), round(float(y), 3), round(float(xz[1]), 3)],
             "rot_deg": [round(a, 3) for a in G.matrix_to_euler(rot)],
             "scale": [round(float(s), 4) for s in scale],
             "obstacle": {"x": round(float(xz[0]), 2), "z": round(float(xz[1]), 2), "r": round(float(r), 2)} if obstacle else None,
             "collision": True}
        if fallback is not None:
            e["fallback_scale"] = [round(float(s), 4) for s in fallback_scale]
        self.placed.append(e)
        self.radii.append(float(r))
        self.disks.append((float(xz[0]), float(xz[1]), float(disk if disk is not None else r)))
        return e

    def rock(self, kind: str, xz, d: float, max_slope: float = MAX_SLOPE_DEG, overlap: float = 0.9,
             crossing: float = CROSSING_CLEAR, pile: tuple | None = None) -> dict | None:
        rng = self.rng
        sx, sz, sy = d * rng.uniform(0.85, 1.15), d * rng.uniform(0.85, 1.15), d * rng.uniform(0.8, 1.15)
        r = 0.55 * max(sx, sz)
        xz = np.asarray(xz, float)
        if not self.clear(xz, r, crossing, overlap):
            return None
        if pile is None:
            y, n, lo = self.terrain.frame(xz[None], r)
            y, n = float(y[0]), n[0]
            if math.isnan(y) or y > QUARRY_MAX_Y or math.degrees(math.acos(n[1])) > max_slope:
                return None
        else:
            y, n = pile
        big = d >= BOULDER_D[0] and kind != "debris"
        fb = rock_asset(int(rng.integers(PROCEDURAL_ROCKS)))
        asset = self.rocks[int(rng.integers(len(self.rocks)))] if big and self.rocks else fb
        h = sy * self.heights[asset]
        up = (1 - TILT_FOLLOW) * np.array([0.0, 1.0, 0.0]) + TILT_FOLLOW * n
        rot = G.align_up(up) @ G.rot_y(rng.uniform(0, 360))
        y -= rng.uniform(*BURY) * h
        fscale = [sx, sy * self.heights[asset] / self.heights[fb], sz]
        return self.add(kind, asset, xz, y, rot, [sx, sy, sz], r, big and r >= OBSTACLE_MIN_R,
                        fb if asset != fb else None, fscale if asset != fb else None)


def berm_runs(site: Site) -> list[dict]:
    T, W = site.terrain, site.world
    runs = []
    for k, road in enumerate(W["roads"]):
        P3 = G.resample(np.array(road["points"], float), BERM_STEP)
        xz, ry = P3[:, [0, 2]], P3[:, 1]
        t = np.gradient(xz, axis=0)
        t = ndimage.uniform_filter1d(t, 7, axis=0, mode="nearest")
        t /= np.linalg.norm(t, axis=1, keepdims=True)
        nrm = np.stack([t[:, 1], -t[:, 0]], 1)
        half = G.corridor_half(road)
        c = half + BERM_TOE_GAP + BERM_BASE / 2
        for side in (1, -1):
            centre = xz + side * nrm * c
            probes = np.stack([xz + side * nrm * (c + o) for o in np.arange(2.0, BERM_PROBE + 0.1, 2.0)], 1)
            hp = T.heightmap(probes.reshape(-1, 2)).reshape(len(xz), -1)
            hc = T.heightmap(centre)
            soft = (ry - hp.min(axis=1) >= BERM_DROP) & (hc - ry < 1.0) & (ry - hc < BERM_MAX_FILL) & ~np.isnan(hc)
            others = site.roads.distances(centre) - np.array(site.roads.halves)[None, :]
            own = site.roads.distances(centre)[:, k]
            others[:, k] = np.inf
            hard = (others.min(axis=1) > BERM_BASE / 2 + 1.0) & (own > c - 0.3)
            hard &= np.hypot(*(centre[:, None, :] - site.pads[None]).transpose(2, 0, 1)).min(axis=1) > BERM_SITE_GAP
            if len(site.junctions):
                hard &= np.hypot(*(centre[:, None, :] - site.junctions[None]).transpose(2, 0, 1)).min(axis=1) > BERM_JUNCTION_GAP
            for s in site.crossings:
                hard &= G.segment_distance(centre, s) > BERM_CROSSING_GAP
            if len(site.equipment):
                hard &= np.hypot(*(centre[:, None, :] - site.equipment[None]).transpose(2, 0, 1)).min(axis=1) > EQUIPMENT_CLEAR
            ok = soft & hard
            if ok.any():
                ok = ndimage.binary_closing(ok, np.ones(int(BERM_FILL_GAP / BERM_STEP))) & hard
            lab, n = ndimage.label(ok)
            for i in range(1, n + 1):
                idx = np.nonzero(lab == i)[0]
                if len(idx) * BERM_STEP < BERM_MIN_RUN:
                    continue
                runs.append({"road": road["id"], "side": "right" if side > 0 else "left",
                             "stations": centre[idx], "normals": side * nrm[idx], "floor": ry[idx]})
    return runs


def build_berms(site: Site, runs: list[dict], out: Path | None) -> list[dict]:
    rng = np.random.default_rng(SEED + 1)
    meshes, entries = [], []
    for i, run in enumerate(runs):
        v, f, uv = P.windrow(run["stations"], run["normals"], site.terrain.ground, BERM_HEIGHT * rng.uniform(0.9, 1.1),
                             BERM_BASE, BERM_TOP, rng, floor=run["floor"])
        meshes.append((v, f, uv))
        line = run["stations"]
        entries.append({"id": f"berm_{i:03d}", "road": run["road"], "side": run["side"], "prim": f"berm_{i:03d}",
                        "length_m": round(float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum()), 1),
                        "height_m": BERM_HEIGHT, "base_m": BERM_BASE,
                        "line": [[round(float(a), 2), round(float(b), 2)] for a, b in line[::4]] + [[round(float(line[-1][0]), 2), round(float(line[-1][1]), 2)]]})
        site.berm_lines.append(line)
    if site.berm_lines:
        from scipy.spatial import cKDTree
        site.berm_tree = cKDTree(np.concatenate(site.berm_lines))
    if out is not None and meshes:
        import usdio
        from pxr import UsdGeom, UsdPhysics
        stage, root = usdio.new_stage(out, "berms")
        mat = usdio.material(stage, "/berms", "dirt", usdio.MATERIALS["dirt"], "../textures")
        for e, (v, f, uv) in zip(entries, meshes):
            vi = v @ G.S.T
            fi = f[:, ::-1]
            m = usdio.mesh(stage, f"/berms/{e['prim']}", vi, fi, uv[fi.reshape(-1)], P.vertex_normals(vi, fi), mat)
            UsdPhysics.CollisionAPI.Apply(m.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr("none")
        stage.GetRootLayer().Save()
    return entries


def toe_candidates(site: Site) -> np.ndarray:
    T = site.terrain
    h = T.hm
    gz, gx = np.gradient(h, T.hm_cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    up = ndimage.maximum_filter(h, size=9) - h
    toe = (slope < 20) & (up > 4) & (h < QUARRY_MAX_Y)
    r, c = np.nonzero(toe)
    xz = np.stack([T.hm_origin[0] + c * T.hm_cell, T.hm_origin[1] + r * T.hm_cell], 1)
    d = site.roads.distances(xz).min(axis=1)
    cl = site.roads.clearance(xz)
    keep = (d < QUARRY_REACH) & (cl > CORRIDOR_MARGIN + 1)
    return xz[keep]


def place_clusters(site: Site, centres: np.ndarray, total: int, size: tuple, spread: float, per: tuple, kind: str) -> int:
    rng, n, tries = site.rng, 0, 0
    while n < total and tries < total * 40 and len(centres):
        tries += 1
        c = centres[int(rng.integers(len(centres)))]
        for _ in range(int(rng.integers(per[0], per[1] + 1))):
            d = float(np.exp(rng.uniform(np.log(size[0]), np.log(size[1]))))
            if site.rock(kind, c + rng.normal(0, spread, 2), d) is not None:
                n += 1
                if n >= total:
                    break
    return n


def place_isolated(site: Site) -> int:
    rng, n, tries = site.rng, 0, 0
    while n < ISOLATED_BOULDERS and tries < 4000:
        tries += 1
        k = int(rng.integers(len(site.roads.lines)))
        L = site.roads.lines[k]
        i = int(rng.integers(len(L)))
        t = site.roads.tangent(k, i)
        nrm = np.array([t[1], -t[0]]) * rng.choice([-1, 1])
        d = rng.uniform(*ISOLATED_D)
        off = site.roads.halves[k] + CORRIDOR_MARGIN + 0.55 * d * 1.15 + rng.uniform(0.5, 8.0)
        xz = L[i] + nrm * off
        g = site.terrain.raycast(xz[None])[0]
        if math.isnan(g) or abs(g - site.roads.y[k][i]) > 1.5:
            continue
        if site.rock("boulder", xz, d) is not None:
            n += 1
    return n


def place_berm_rocks(site: Site, runs: list[dict]) -> int:
    rng, n, tries = site.rng, 0, 0
    while n < BERM_BOULDERS and tries < 3000 and runs:
        tries += 1
        run = runs[int(rng.integers(len(runs)))]
        i = int(rng.integers(len(run["stations"])))
        base = run["stations"][i] + run["normals"][i] * (BERM_BASE / 2 + rng.uniform(0.8, 3.5))
        for _ in range(int(rng.integers(1, 4))):
            d = float(np.exp(rng.uniform(np.log(BOULDER_D[0]), np.log(1.2))))
            if site.rock("boulder", base + rng.normal(0, 1.2, 2), d, max_slope=40) is not None:
                n += 1
    return n


def place_piles(site: Site, centres: list[np.ndarray]) -> tuple[int, int]:
    rng, piles, rocks, tries = site.rng, 0, 0, 0
    while piles < ROCK_PILES and tries < 2000:
        tries += 1
        c = centres[int(rng.integers(len(centres)))] + rng.normal(0, 12, 2)
        radius, height = rng.uniform(4.0, 7.0), rng.uniform(2.0, 3.5)
        if not site.clear(c, radius + 1.0):
            continue
        y, n, lo = site.terrain.frame(c[None], radius)
        if math.isnan(y[0]) or y[0] > QUARRY_MAX_Y or math.degrees(math.acos(n[0][1])) > 12:
            continue
        piles += 1
        rot = G.align_up(n[0]) @ G.rot_y(rng.uniform(0, 360))
        mound = f"procedural/mound_{int(rng.integers(PROCEDURAL_MOUNDS))}.usdc"
        site.add("rock_pile", mound, c, float(lo[0]) - 0.1, rot, [radius, height / 0.45, radius], radius, True)
        mound_disk = site.disks.pop()
        for _ in range(int(rng.integers(PILE_BOULDERS[0], PILE_BOULDERS[1] + 1))):
            a, rr = rng.uniform(0, 2 * np.pi), radius * math.sqrt(rng.uniform(0.0, 0.8))
            p = c + rr * np.array([math.cos(a), math.sin(a)])
            surf = float(lo[0]) - 0.1 + height * max(0.0, 1 - rr / radius) ** 0.9
            d = float(rng.uniform(0.6, 1.4))
            nn = np.array([math.cos(a) * 0.6, 1.0, math.sin(a) * 0.6])
            if site.rock("boulder", p, d, overlap=0.7, pile=(surf, nn / np.linalg.norm(nn))) is not None:
                rocks += 1
        site.disks.append(mound_disk)
    return piles, rocks


def place_debris(site: Site, centres: np.ndarray) -> int:
    rng, n, tries = site.rng, 0, 0
    anchors = np.concatenate([centres] + [np.array([[e["pos"][0], e["pos"][2]] for e in site.placed])])
    while n < DEBRIS_ROCKS and tries < DEBRIS_ROCKS * 20:
        tries += 1
        c = anchors[int(rng.integers(len(anchors)))] + rng.normal(0, 4.0, 2)
        d = float(np.exp(rng.uniform(np.log(DEBRIS_D[0]), np.log(DEBRIS_D[1]))))
        if site.rock("debris", c, d, max_slope=40, overlap=1.0) is not None:
            n += 1
    return n


def free_spot(site: Site, centre, r: float, rmin: float, rmax: float, max_slope: float = 10.0, crossing: float = CROSSING_CLEAR):
    rng = site.rng
    for _ in range(600):
        a, d = rng.uniform(0, 2 * np.pi), rng.uniform(rmin, rmax)
        xz = np.asarray(centre, float) + d * np.array([math.cos(a), math.sin(a)])
        if not site.clear(xz, r, crossing):
            continue
        y, n, lo = site.terrain.frame(xz[None], r)
        if math.isnan(y[0]) or math.degrees(math.acos(n[0][1])) > max_slope:
            continue
        return xz, float(lo[0]), n[0]
    return None


def facing_road(site: Site, xz: np.ndarray) -> float:
    k, i, _ = site.roads.nearest(xz)
    return G.heading_deg(site.roads.tangent(k, i))


def place_site_props(site: Site) -> dict:
    W, rng, counts = site.world, site.rng, {}
    sites = {s["id"]: s for s in W["sites"]}
    ph = site.heights

    def put(kind, asset, xz, y, heading, r, obstacle, n=None, scale=(1, 1, 1)):
        rot = (G.align_up(n) if n is not None else np.eye(3)) @ G.rot_y(heading)
        counts[kind] = counts.get(kind, 0) + 1
        return site.add(kind, asset, xz, y, rot, scale, r, obstacle)

    fuel = sites.get("fuel")
    if fuel:
        spot = free_spot(site, fuel["pos"], 4.6, 16.0, 32.0, 8.0)
        if spot:
            xz, y, n = spot
            h = facing_road(site, xz)
            put("fuel_tank", "procedural/fuel_tank.usdc", xz, y, h, 4.6, True, n)
            tank_disk = site.disks.pop()
            t = np.array([math.sin(math.radians(h)), math.cos(math.radians(h))])
            side = np.array([t[1], -t[0]])
            if "polyhaven/barrel_03.usdc" in ph:
                for j in range(6):
                    p = xz + side * (2.6 + 0.7 * (j // 3)) + t * (-0.7 + 0.7 * (j % 3)) + rng.normal(0, 0.04, 2)
                    if site.clear(p, 0.35, overlap=1.0):
                        put("drum", ["polyhaven/barrel_03.usdc", "polyhaven/Barrel_01.usdc"][j % 2], p,
                            float(site.terrain.ground(p[None])[0]), rng.uniform(0, 360), 0.35, False)
            if "polyhaven/portable_generator.usdc" in ph:
                p = xz - side * 2.4 + t * 3.0
                if site.clear(p, 0.6):
                    put("generator", "polyhaven/portable_generator.usdc", p, float(site.terrain.ground(p[None])[0]), h + 90, 0.6, False)
            site.disks.append(tank_disk)
    park = sites.get("parking")
    if park and "polyhaven/concrete_road_barrier.usdc" in ph:
        spot = free_spot(site, park["pos"], 6.0, 16.0, 30.0, 8.0)
        if spot:
            xz, y, n = spot
            h = facing_road(site, xz)
            t = np.array([math.sin(math.radians(h)), math.cos(math.radians(h))])
            for j in range(-3, 4):
                p = xz + t * j * 1.58
                if site.clear(p, 0.8, overlap=1.0):
                    put("barrier", "polyhaven/concrete_road_barrier.usdc", p, float(site.terrain.ground(p[None])[0]), h + 90, 0.8, True)
        if "polyhaven/covered_car.usdc" in ph:
            spot = free_spot(site, park["pos"], 2.6, 15.0, 28.0, 6.0)
            if spot:
                xz, y, n = spot
                put("light_vehicle", "polyhaven/covered_car.usdc", xz, y, facing_road(site, xz) + rng.uniform(-20, 20), 2.6, True, n)
    for seg in site.crossings:
        mid = seg.mean(axis=0)
        k, i, _ = site.roads.nearest(mid)
        t = site.roads.tangent(k, i)
        nrm = np.array([t[1], -t[0]])
        off = site.roads.halves[k] + CORRIDOR_MARGIN + 0.6
        for s in (-1, 1):
            for a in (-1, 1):
                p = mid + nrm * s * off + t * a * 3.0
                if site.clear(p, 0.25, crossing=2.0):
                    put("cone", "procedural/cone.usdc", p, float(site.terrain.ground(p[None])[0]), rng.uniform(0, 360), 0.25, False)
            for back in (12.0, 16.0, 9.0, 20.0, 25.0):
                p = mid + nrm * s * (off + 1.0) - t * s * back
                if site.clear(p, 0.5):
                    put("sign", "procedural/sign_pedestrians.usdc", p, float(site.terrain.ground(p[None])[0]),
                        G.heading_deg(t * s), 0.5, False)
                    break
    for road in W["roads"]:
        if road["type"] != "spur":
            continue
        k = next(j for j, r in enumerate(W["roads"]) if r["id"] == road["id"])
        L = site.roads.lines[k]
        for start in (True, False):
            for along in (30, 40, 50, 60, 70):
                i = min(len(L) - 1, along * 2) if start else max(0, len(L) - 1 - along * 2)
                t = site.roads.tangent(k, i)
                fwd = t if start else -t
                nrm = np.array([fwd[1], -fwd[0]])
                p = L[i] + nrm * (site.roads.halves[k] + CORRIDOR_MARGIN + 1.0)
                if site.clear(p, 0.5):
                    put("sign", "procedural/sign_speed40.usdc", p, float(site.terrain.ground(p[None])[0]), G.heading_deg(fwd), 0.5, False)
                    break
    return counts


def navgrid_report(site: Site, world_dir: Path) -> dict:
    sys.path.insert(0, str(REPO))
    from envgen.common import navgrid_check
    nav = json.loads((world_dir / site.world["navgrid"]).read_text())
    cost = np.asarray(nav["cost"], float).reshape(nav["height"], nav["width"])
    origin, cell = nav["origin"], nav["cell_m"]
    base_errs, base = navgrid_check(site.world, cost, origin, cell)
    stamped = stamp(cost, origin, cell, site)
    errs, summary = navgrid_check(site.world, stamped, origin, cell)
    return {"baseline": {"problems": base_errs, "pairs_connected": base["pairs_connected"],
                         "nav_point_pairs_connected": base["nav_point_pairs_connected"]},
            "with_props": {"problems": errs, "pairs_connected": summary["pairs_connected"],
                           "nav_point_pairs_connected": summary["nav_point_pairs_connected"],
                           "min_corridor_width_m": summary["min_corridor_width_m"]},
            "blocked_cells_added": int(((stamped <= 0) & (cost > 0)).sum())}


def stamp(cost: np.ndarray, origin, cell: float, site: Site) -> np.ndarray:
    out = cost.copy()
    h, w = cost.shape
    X = origin[0] + (np.arange(w) + 0.5) * cell
    Z = origin[1] + (np.arange(h) + 0.5) * cell
    disks = [(e["obstacle"]["x"], e["obstacle"]["z"], e["obstacle"]["r"]) for e in site.placed if e["obstacle"]]
    disks += [(float(x), float(z), BERM_BASE / 2) for line in site.berm_lines for x, z in line]
    for x, z, r in disks:
        c0, c1 = np.searchsorted(X, [x - r - cell, x + r + cell])
        r0, r1 = np.searchsorted(Z, [z - r - cell, z + r + cell])
        if c1 <= c0 or r1 <= r0:
            continue
        dx = np.maximum(np.abs(X[c0:c1] - x) - cell / 2, 0)
        dz = np.maximum(np.abs(Z[r0:r1] - z) - cell / 2, 0)
        hit = dz[:, None] ** 2 + dx[None, :] ** 2 < r * r
        out[r0:r1, c0:c1][hit] = -1.0
    return out


def generate(world_dir: Path = WORLD_DIR, assets: Path = ASSETS_DIR, seed: int = SEED, write_usd: bool = True,
             check: bool = True) -> dict:
    site = Site(world_dir, assets, seed)
    runs = berm_runs(site)
    berm_usd = assets / "generated" / f"{site.world['id']}_berms.usdc"
    berms = build_berms(site, runs, berm_usd if write_usd else None)
    for b in berms:
        b["asset"] = f"generated/{berm_usd.name}"
    toes = toe_candidates(site)
    sel = toes[site.rng.permutation(len(toes))[: max(1, len(toes) // 4)]] if len(toes) else toes
    spoil = [np.array(s["pos"], float) for s in site.world["sites"] if s["type"] in ("dump", "crusher", "ore")]
    stats = {}
    stats["isolated_boulders"] = place_isolated(site)
    stats["rock_piles"], stats["pile_boulders"] = place_piles(site, spoil)
    stats["toe_boulders"] = place_clusters(site, sel, TOE_BOULDERS, BOULDER_D, 2.5, (2, 6), "boulder")
    stats["berm_boulders"] = place_berm_rocks(site, runs)
    ring = []
    for s in spoil:
        for a in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            for rr in (18.0, 26.0, 34.0, 42.0):
                ring.append(s + rr * np.array([math.cos(a), math.sin(a)]))
    stats["spoil_boulders"] = place_clusters(site, np.array(ring), SPOIL_BOULDERS, BOULDER_D, 2.0, (1, 4), "boulder")
    stats["site_props"] = place_site_props(site)
    stats["debris_rocks"] = place_debris(site, sel)
    stats["berms"] = len(berms)
    stats["berm_length_m"] = round(sum(b["length_m"] for b in berms), 1)
    stats["props"] = len(site.placed)
    stats["obstacles"] = sum(1 for e in site.placed if e["obstacle"])
    xz = np.array([[e["pos"][0], e["pos"][2]] for e in site.placed])
    rs = np.array(site.radii)
    stats["min_corridor_clearance_m"] = round(float((site.roads.clearance(xz) - rs).min()), 2)
    stats["min_pad_clearance_m"] = round(float((np.hypot(*(xz[:, None] - site.pads[None]).transpose(2, 0, 1)).min(axis=1) - rs).min()), 2)
    stats["min_crossing_clearance_m"] = round(float(min((G.segment_distance(xz, s) - rs).min() for s in site.crossings)), 2)
    out = {"version": 1, "world": site.world["id"], "seed": seed,
           "frame": "world.json: Y up, x east, z north, metres",
           "rotation": "rot_deg = [rx, ry, rz] degrees in the world frame, R = Ry(ry) @ Rx(rx) @ Rz(rz); ry is the heading (yaw_matrix)",
           "asset_frame": "USD Z up, metres, origin at the base centre, forward +Y; rocks normalised to 1 m max horizontal extent",
           "isaac": "stage matrix = S @ R @ diag(scale) @ S, translation S @ pos, S swaps y and z (isaac/sim/usdbuild.py TO_ISAAC)",
           "props": site.placed, "berms": berms, "stats": stats}
    if check:
        out["navgrid_check"] = navgrid_report(site, world_dir)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default=str(WORLD_DIR))
    ap.add_argument("--assets", default=str(ASSETS_DIR))
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--build-assets", action="store_true")
    ap.add_argument("--no-usd", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    world, assets = Path(args.world), Path(args.assets)
    if args.build_assets:
        build_assets(assets)
    data = generate(world, assets, args.seed, not args.no_usd, not args.no_check)
    out = Path(args.out) if args.out else world / "props.json"
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(json.dumps(data["stats"], indent=1))
    if "navgrid_check" in data:
        print(json.dumps(data["navgrid_check"], indent=1))


if __name__ == "__main__":
    main()
