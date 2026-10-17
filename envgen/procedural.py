"""Procedural open-pit copper mine (no API keys).

    python -m envgen.procedural --seed 42 --id default

Writes $DATA_DIR/worlds/<id>/ : world.json, navgrid.json, terrain.glb,
heightmap.raw + heightmap_meta.json, preview.png.
"""
from __future__ import annotations

import argparse

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from . import common as C

SIZE = 400.0          # m, square world
ORIGIN = (-200.0, -200.0)
RES = 0.5             # master grid spacing (m)
CELL = 2.0            # navgrid cell (m)

PIT_C = np.array([-20.0, 20.0])  # pit centre [x, z]
PIT_R = 120.0         # rim radius
PIT_RF = 30.0         # floor radius
BENCH_H = 10.0
N_BENCH = 8
FACE_FRAC = 0.3       # fraction of each bench step taken up by the steep face
RAMP_GRADE = 0.09
ROAD_W = 14.0

PADS = {  # id: (type, name, pos, radius)
    "crusher": ("crusher", "Primary Crusher", (150.0, 60.0), 22.0),
    "dump_south": ("dump", "South Waste Dump", (-70.0, -160.0), 26.0),
    "parking": ("parking", "Ready Line", (150.0, -120.0), 24.0),
    "fuel": ("fuel", "Fuel Bay", (95.0, -160.0), 16.0),
}

# zones for coloring
NATURAL, PIT, ROAD, PAD, ORE = 0, 1, 2, 3, 4


def carve(H, zone, pts, hs, width, shoulder, zone_val=ROAD):
    """Carve a road along densified polyline pts (N,2) with centreline heights hs (N,)."""
    n = H.shape[0]
    tree = cKDTree(pts)
    lo = np.floor((pts.min(0) - width / 2 - shoulder - np.array(ORIGIN)) / RES).astype(int).clip(0, n - 1)
    hi = np.ceil((pts.max(0) + width / 2 + shoulder - np.array(ORIGIN)) / RES).astype(int).clip(0, n - 1)
    cols, rows = np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1)
    CC, RR = np.meshgrid(cols, rows)
    q = np.stack([ORIGIN[0] + CC * RES, ORIGIN[1] + RR * RES], -1).reshape(-1, 2)
    d, idx = tree.query(q, distance_upper_bound=width / 2 + shoulder)
    ok = np.isfinite(d)
    r, c, d, idx = RR.ravel()[ok], CC.ravel()[ok], d[ok], idx[ok]
    t = C.smoothstep((d - width / 2) / shoulder)
    H[r, c] = hs[idx] * (1 - t) + H[r, c] * t
    on = d <= width / 2
    zone[r[on], c[on]] = zone_val


def densify(poly, step=0.5):
    poly = np.asarray(poly, float)
    out = [poly[0]]
    for a, b in zip(poly[:-1], poly[1:]):
        k = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.extend(a + (b - a) * (np.arange(1, k + 1) / k)[:, None])
    return np.array(out)


def sample(H, pts):
    from scipy.ndimage import map_coordinates
    return map_coordinates(H, [(pts[:, 1] - ORIGIN[1]) / RES, (pts[:, 0] - ORIGIN[0]) / RES], order=1)


def surface_road(H, zone, poly, h_start=None, h_end=None):
    pts = densify(poly)
    hs = gaussian_filter1d(sample(H, pts), sigma=40, mode="nearest")  # sigma in samples (0.5 m)
    s = np.linspace(0, 1, len(pts))
    if h_start is not None:
        hs += (h_start - hs[0]) * (1 - s)
    if h_end is not None:
        hs += (h_end - hs[-1]) * s
    carve(H, zone, pts, hs, ROAD_W, 6.0)


def build(seed=42):
    n = int(SIZE / RES) + 1
    xs = ORIGIN[0] + np.arange(n) * RES
    X, Z = np.meshgrid(xs, ORIGIN[1] + np.arange(n) * RES)
    rng = np.random.default_rng(seed)

    # --- base terrain: gentle rolling ground, rising slightly to the north-west
    H = 8.0 * C.fbm(X, Z, seed, octaves=5, freq=1 / 160) + 0.02 * (Z - X)
    zone = np.zeros(H.shape, np.uint8)
    rim_level = float(np.median(H))

    # --- terraced pit (radius slightly perturbed by noise for a natural outline)
    dx, dz = X - PIT_C[0], Z - PIT_C[1]
    r = np.hypot(dx, dz) * (1 + 0.06 * C.fbm(X, Z, seed + 7, octaves=2, freq=1 / 90))
    W = (PIT_R - PIT_RF) / N_BENCH
    t = np.clip((PIT_R - r) / W, 0, N_BENCH)
    frac = t - np.floor(t)
    level = np.minimum(np.floor(t) + C.smoothstep((frac - (1 - FACE_FRAC)) / FACE_FRAC), N_BENCH)
    pit_h = rim_level - BENCH_H * level + 0.3 * C.fbm(X, Z, seed + 3, octaves=3, freq=1 / 15)
    blend = C.smoothstep((r - PIT_R) / 20.0)  # 0 inside rim -> 1 at rim+20
    H = np.where(r < PIT_R, pit_h, rim_level * (1 - blend) + H * blend)
    zone[r < PIT_R] = PIT

    # --- pads (flattened)
    for pid, (_, _, pos, rad) in PADS.items():
        d = np.hypot(X - pos[0], Z - pos[1])
        h0 = float(H[d < rad].mean())
        tt = C.smoothstep((d - rad) / 15.0)
        H = h0 * (1 - tt) + H * tt
        zone[d < rad] = PAD

    # --- spiral haul ramp from rim to floor, ~9% grade, starting on the east side
    depth_total = BENCH_H * N_BENCH
    L = depth_total / RAMP_GRADE
    theta0 = np.arctan2(PADS["crusher"][2][1] - PIT_C[1], PADS["crusher"][2][0] - PIT_C[0])
    ss = np.arange(0, L + 0.5, 0.5)
    rr = PIT_R + 4 - (PIT_R + 4 - PIT_RF - 6) * (ss / L)
    dth = np.sqrt(np.maximum(0.25 - np.diff(rr, prepend=rr[0]) ** 2, 1e-6)) / rr
    th = theta0 + np.cumsum(dth)  # counter-clockwise descent
    ramp = np.stack([PIT_C[0] + rr * np.cos(th), PIT_C[1] + rr * np.sin(th)], 1)
    ramp_h = rim_level - RAMP_GRADE * ss
    ramp_top, ramp_bot = ramp[0], ramp[-1]

    # --- surface haul roads (carved before ramp so the ramp wins at the junction)
    hub = ramp_top + (ramp_top - PIT_C) / np.linalg.norm(ramp_top - PIT_C) * 18
    P = {k: np.array(v[2]) for k, v in PADS.items()}
    surf = [  # (id, name, from, to, polyline) -- recorded as roads after all carving
        ("ramp_access", "Ramp Access", "j_ramp_top", "j_hub", [ramp_top, hub]),
        ("crusher_road", "Crusher Road", "j_hub", "crusher", [hub, P["crusher"]]),
        ("parking_road", "Ready Line Road", "j_hub", "parking", [hub, (150, -40), P["parking"]]),
        ("fuel_road", "Fuel Bay Road", "parking", "fuel", [P["parking"], P["fuel"]]),
        ("dump_road", "South Dump Road", "fuel", "dump_south", [P["fuel"], (20, -170), P["dump_south"]]),
    ]
    surface_road(H, zone, [ramp_top, hub, P["crusher"]], h_start=rim_level)
    surface_road(H, zone, [ramp_top, hub, (150, -40), P["parking"]], h_start=rim_level)
    surface_road(H, zone, [P["parking"], P["fuel"]])
    surface_road(H, zone, [P["fuel"], (20, -170), P["dump_south"]])
    carve(H, zone, ramp, ramp_h, ROAD_W, 5.0)

    # --- ore sites: pit floor + two benches, each linked to the ramp by a graded spur
    ore, spurs = [], []
    floor_h = rim_level - depth_total
    fp = PIT_C + np.array([4.0, -6.0])
    ore.append(("pit_floor", "Pit Floor", fp, 9000))
    # bench loading areas: pull-outs beside the ramp where it passes a given bench depth
    for lvl, tonnes in [(3, 6000), (5, 4500)]:
        hb = rim_level - BENCH_H * lvl
        j = int(np.argmin(np.abs(ramp_h - hb)))
        out_dir = (ramp[j] - PIT_C) / np.linalg.norm(ramp[j] - PIT_C)
        p = ramp[j] + out_dir * 8.0  # on the outer (bench) side of the ramp
        d = np.hypot(X - p[0], Z - p[1])
        tt = C.smoothstep((d - 11) / 5)
        H = ramp_h[j] * (1 - tt) + H * tt
        compass = ["East", "North-East", "North", "North-West", "West", "South-West", "South", "South-East"]
        cname = compass[int(round(np.degrees(np.arctan2(out_dir[1], out_dir[0])) / 45)) % 8]
        sid = f"pit_{cname.lower().replace('-', '')}"
        ore.append((sid, f"{cname} Bench", p, tonnes))
        spurs.append((j, sid, cname))
    carve(H, zone, ramp, ramp_h, ROAD_W, 5.0)  # re-carve so spurs never break the ramp
    # floor loading area
    d = np.hypot(X - fp[0], Z - fp[1])
    H = np.where(d < PIT_RF - 2, floor_h + 0.3 * C.fbm(X, Z, seed + 9, 2, 1 / 20), H)
    floor_rd = densify([ramp_bot, fp])
    carve(H, zone, floor_rd, np.full(len(floor_rd), floor_h), ROAD_W, 4.0)

    # ore tint zone
    for _, _, p, _ in ore:
        zone[(np.hypot(X - p[0], Z - p[1]) < 16) & (zone != ROAD)] = ORE

    # --- road graph (sampled from the FINAL heightmap)
    hfun = lambda xz: sample(H, np.asarray(xz, float))
    junc = {"j_ramp_top": ("Ramp Top", ramp_top), "j_hub": ("Rim Hub", hub), "j_ramp_bottom": ("Ramp Bottom", ramp_bot)}
    roads = [C.road_entry(i, nm, "haul", a, b, poly, hfun, ROAD_W) for i, nm, a, b, poly in surf]
    cuts, prev = [0], "j_ramp_top"
    for j, sid, cname in sorted(spurs):
        jid = f"j_{sid[4:]}_spur"
        junc[jid] = (f"{cname} Spur Junction", ramp[j])
        roads.append(C.road_entry(f"{sid}_spur", f"{cname} Bench Spur", "spur", jid, sid,
                                  [ramp[j], dict((o[0], o[2]) for o in ore)[sid]], hfun, ROAD_W))
        cuts.append(j)
    cuts.append(len(ramp) - 1)
    nodes = ["j_ramp_top"] + [f"j_{sid[4:]}_spur" for _, sid, _ in sorted(spurs)] + ["j_ramp_bottom"]
    for k, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
        roads.append(C.road_entry(f"ramp_main_{k + 1}", f"Main Haul Ramp ({k + 1}/{len(cuts) - 1})", "ramp",
                                  nodes[k], nodes[k + 1], ramp[a:b + 1], hfun, ROAD_W))
    roads.append(C.road_entry("floor_road", "Pit Floor Road", "haul", "j_ramp_bottom", "pit_floor",
                              [ramp_bot, fp], hfun, ROAD_W))
    junctions = [{"id": k, "name": nm, "pos": C.pos3(p, hfun)} for k, (nm, p) in junc.items()]

    # --- colors
    sl = C.slope_deg(H, RES)
    noise = C.fbm(X, Z, seed + 21, octaves=4, freq=1 / 25)[..., None]
    palette = {
        NATURAL: (0.62, 0.52, 0.38), PIT: (0.50, 0.44, 0.38), ROAD: (0.70, 0.68, 0.64),
        PAD: (0.66, 0.64, 0.60), ORE: (0.30, 0.55, 0.50),
    }
    col = np.zeros(H.shape + (3,))
    for z, c in palette.items():
        col[zone == z] = c
    rock = C.smoothstep((sl - 28) / 15)[..., None]
    col = col * (1 - rock) + np.array([0.42, 0.40, 0.39]) * rock
    col = np.clip(col * (1 + 0.12 * noise), 0, 1)

    # --- sites & trucks
    sites = [{"id": k, "type": v[0], "name": v[1], "pos": [round(v[2][0], 1), round(v[2][1], 1)]}
             for k, v in PADS.items()]
    sites += [{"id": i, "type": "ore", "name": nm, "pos": [round(float(p[0]), 1), round(float(p[1]), 1)],
               "tonnes": tn} for i, nm, p, tn in ore]
    for st in sites:
        st["pos3"] = C.pos3(st["pos"], hfun)
    pk = P["parking"]
    trucks = [{"id": f"T{i+1}", "callsign": f"Truck {i+1}",
               "spawn": [round(float(pk[0] - 14 + 7 * i), 1), round(float(pk[1] + 6), 1)],
               "heading_deg": 0, "capacity_t": 200} for i in range(5)]
    meta = {"rim_level": rim_level, "floor_level": floor_h, "ramp_length_m": L,
            "ramp_top": ramp_top.round(1).tolist(), "ramp_bottom": ramp_bot.round(1).tolist()}
    meta["graph"] = (junctions, roads)
    return H, zone, col, sl, sites, trucks, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--id", default="default")
    ap.add_argument("--name", default="Copper Ridge Pit")
    a = ap.parse_args()

    out = C.WORLDS / a.id
    out.mkdir(parents=True, exist_ok=True)
    H, zone, col, sl, sites, trucks, meta = build(a.seed)
    junctions, roads = meta.pop("graph")

    # navgrid: 2 m cells = 4x4 blocks of the 0.5 m master grid (cell centres at node 4c+2)
    k = int(CELL / RES)
    Hc = H[2::k, 2::k][: int(SIZE / CELL), : int(SIZE / CELL)]
    s_mean = C.block_pool(sl, k, np.mean)
    s_max = C.block_pool(sl, k, np.max)
    road = C.block_pool(np.isin(zone, (ROAD, PAD)).astype(float), k, np.mean) > 0.5
    cost = C.cost_from_slope(s_mean, s_max, road)
    nav = C.navgrid_dict(Hc, cost, ORIGIN, CELL)
    C.write_json(out / "navgrid.json", nav, compact=True)

    world = {
        "id": a.id, "name": a.name, "source": "procedural",
        "size_m": [SIZE, SIZE], "origin": list(ORIGIN),
        "assets": {"splat": None, "collider": None, "terrain_mesh": "terrain.glb", "pano": None,
                   "concept": None, "heightmap_raw": "heightmap.raw", "preview": "preview.png",
                   "heightmap_meta": "heightmap_meta.json"},
        "splat_transform": {"scale": 1.0, "ground_offset": 0.0, "rotation_euler_deg": [0, 0, 0]},
        "sites": sites, "junctions": junctions, "roads": roads,
        "trucks": trucks, "navgrid": "navgrid.json",
        "generator": {"seed": a.seed, **{k2: (round(v, 2) if isinstance(v, float) else v) for k2, v in meta.items()}},
    }
    errs = C.check_road_graph(world)
    assert not errs, f"road graph invalid: {errs}"
    C.write_json(out / "world.json", world)
    C.export_terrain_glb(out / "terrain.glb", H, col, ORIGIN, (SIZE, SIZE))
    C.write_heightmap_raw(out, H, ORIGIN, (SIZE, SIZE))
    C.write_preview(out / "preview.png", H, col, RES, world)

    bad = C.check_reachability(world, nav)
    passable = (cost > 0).mean()
    print(f"wrote {out}  heights {H.min():.1f}..{H.max():.1f} m  passable {passable:.0%}  "
          f"sites {len(sites)}  roads {len(roads)}  junctions {len(junctions)}  trucks {len(trucks)}  unreachable: {bad or 'none'}")


if __name__ == "__main__":
    main()
