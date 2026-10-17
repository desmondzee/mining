"""Procedural open-pit copper mine (no API keys), built to an explicit design spec.

    python -m envgen.procedural --seed 42 --id default --truck komatsu_hd465

All pit / haul-road geometry comes from envgen.mine_spec.MineSpec (design truck
Komatsu HD465-7 by default; CAT 785D / 797F, Komatsu 930E presets): horizontal benches of `bench_height_m` with faces at
`face_angle_deg` and catch berms of `berm_width_m`; a spiral ramp at
`ramp_grade_pct`, `ramp_width_m` wide, with 2 % cross-fall to the highwall and a
safety windrow (`safety_berm_height_m`, ≥ MSHA mid-axle height) on its crest
edge; surface haul roads `road_width_m` wide (3.5 × truck width, two lanes).
See envgen/DESIGN_SPEC.md.

Writes $DATA_DIR/worlds/<id>/ : world.json, navgrid.json, terrain.glb,
heightmap.raw + heightmap_meta.json, preview.png.
"""
from __future__ import annotations

import argparse
import math

import numpy as np
from scipy.ndimage import gaussian_filter1d, maximum_filter1d
from scipy.spatial import cKDTree

from . import common as C
from . import mine_spec as MS

RES = 0.5             # master grid spacing (m)
CELL = 2.0            # navgrid cell (m)
PIT_C = np.array([0.0, 0.0])  # pit centre [x, z]

# globals set per build (size depends on the design truck)
SIZE = 640.0
ORIGIN = (-320.0, -320.0)

# zones for coloring
NATURAL, PIT, ROAD, PAD, ORE, BERM = 0, 1, 2, 3, 4, 5


# ---------------------------------------------------------------- generic helpers
def carve(H, zone, pts, hs, width, shoulder, zone_val=ROAD, crown=None, batter=None):
    """Carve a road along densified polyline pts (N,2) with centreline heights hs (N,).

    crown: cross-fall (fraction) from the centreline to both edges. batter: cut/fill side slope
    (V/H) from the road edge out to `shoulder`; None = smooth blend."""
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
    hr = hs[idx] - (crown or 0.0) * np.minimum(d, width / 2)
    if batter is None:
        t = C.smoothstep((d - width / 2) / shoulder)
        H[r, c] = hr * (1 - t) + H[r, c] * t
    else:
        e = np.maximum(d - width / 2, 0) * batter
        H[r, c] = hr + np.clip(H[r, c] - hr, -e, e)
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


def surface_road(H, zone, poly, width, h_start=None, h_end=None, crown=0.02):
    pts = densify(poly)
    hs = gaussian_filter1d(sample(H, pts), sigma=40, mode="nearest")  # sigma in samples (0.5 m)
    s = np.linspace(0, 1, len(pts))
    if h_start is not None:
        hs += (h_start - hs[0]) * (1 - s)
    if h_end is not None:
        hs += (h_end - hs[-1]) * s
    carve(H, zone, pts, hs, width, 20.0, crown=crown, batter=0.5)  # 2H:1V cut/fill batters


# ---------------------------------------------------------------- benched-wall model
class Wall:
    """Horizontal benches at absolute levels F + k*Hb: face (horizontal f) then catch berm (b)."""

    def __init__(self, spec, floor):
        self.F, self.Hb = floor, spec.bench_height_m
        self.f, self.b = spec.face_horizontal_m, spec.berm_width_m

    def _split(self, h0):
        D = np.maximum((np.asarray(h0, float) - self.F) / self.Hb, 0)
        k = np.floor(D)
        return k, D - k

    def climb(self, h0, d):
        """Wall height at horizontal distance d outward of a ledge (ramp edge / floor) at height h0.

        Absolute bench levels: the face in progress is finished ((1-fr)*f), then a partial catch berm
        ((1-fr)*b, so bench positions vary continuously with h0), then standard benches."""
        h0 = np.asarray(h0, float)
        d = np.maximum(d, 0)
        k, fr = self._split(h0)
        f1, b1 = (1 - fr) * self.f, (1 - fr) * self.b
        L1 = self.F + (k + 1) * self.Hb
        x = d - f1 - b1
        j = np.floor(np.maximum(x, 0) / (self.f + self.b))
        rem = np.maximum(x, 0) - j * (self.f + self.b)
        std = L1 + self.Hb * (j + np.minimum(1.0, rem / self.f))
        return np.where(d < f1, h0 + d * self.Hb / self.f, np.where(d < f1 + b1, L1, std))

    def dist(self, h0, h1):
        """Horizontal distance for climb(h0, .) to first reach h1 (h1 >= h0)."""
        h0, h1 = np.asarray(h0, float), np.asarray(h1, float)
        k, fr = self._split(h0)
        f1, b1 = (1 - fr) * self.f, (1 - fr) * self.b
        L1 = self.F + (k + 1) * self.Hb
        D1 = np.maximum((h1 - L1) / self.Hb, 0)
        jj = np.maximum(np.ceil(D1) - 1, 0)
        std = f1 + b1 + jj * (self.f + self.b) + (D1 - jj) * self.f
        return np.where(h1 <= L1, np.maximum(h1 - h0, 0) * self.f / self.Hb, std)


APRON_M, TAPER_M = 40.0, 60.0   # level run-out beyond the ramp foot, and its taper back to the floor radius


def floor_outer(q, a0, spec):
    """Outer radius of the level pit floor at unrolled angle q in (-2pi, 0] (q = psi of the ray - 2pi).

    The floor is enlarged to a0 + Wt over the 40 m run-out sector clockwise of the ramp foot so trucks
    leave the ramp onto level ground, then tapers back to floor_radius_m (no seams in the walls)."""
    Rf, Wt = spec.floor_radius_m, spec.ramp_width_m + spec.berm_base_m
    rc = a0 + spec.berm_base_m + spec.ramp_width_m / 2
    ap, tp = APRON_M / rc, TAPER_M / rc
    return Rf + (a0 + Wt - Rf) * C.smoothstep((np.asarray(q) + ap + tp) / tp)


def design_ramp(spec, wall, rim):
    """Spiral ramp cut into the benched wall, built bottom-up.

    psi = unrolled angle from the ramp foot (0) upward. For each psi the band (windrow + running
    surface, total Wt) has inner-edge radius a(psi). The wall between the previous anchor on the same ray
    (pit floor, or the ramp leg one turn lower) and the band is a stack of design benches, so
    a(psi) >= outer(prev) + wall.dist(h_prev, h); a(psi) is the smooth upper envelope of that.
    """
    W, wb = spec.ramp_width_m, spec.berm_base_m
    Wt = W + wb
    g = spec.ramp_grade_pct / 100
    Rf, F = spec.floor_radius_m, wall.F
    dpsi = 0.002
    psi = np.arange(0, 6 * np.pi, dpsi)
    a = np.full_like(psi, Rf + 20.0)
    win = 600

    def heights(a):
        rc = a + wb + W / 2
        ds = np.hypot(rc * dpsi, np.diff(rc, prepend=rc[0]))
        return F + g * (np.cumsum(ds) - ds[0])

    for _ in range(8):
        h = heights(a)
        lower = psi >= 2 * np.pi
        fo = floor_outer(psi - 2 * np.pi, a[0], spec)
        o_prev = np.where(lower, np.interp(psi - 2 * np.pi, psi, a) + Wt, fo)
        h_prev = np.where(lower, np.interp(psi - 2 * np.pi, psi, h), F)
        need = o_prev + wall.dist(h_prev, h)
        env = gaussian_filter1d(maximum_filter1d(need, size=win, mode="nearest"), win / 4, mode="nearest")
        a = np.maximum(env, need)
    h = heights(a)
    top = int(np.searchsorted(h, rim))
    psi_top = float(np.interp(rim, h[: top + 1], psi[: top + 1]))
    keep = psi <= psi_top + 1e-9
    psi, a, h = np.append(psi[keep], psi_top), np.append(a[keep], np.interp(psi_top, psi, a)), \
        np.append(h[keep], rim)
    return psi, a, h


def pit_surface(X, Z, spec, wall, ramp, theta0, rim):
    """Evaluate the analytic pit at every grid node. Returns H, zone, info dict."""
    psi_t, a_t, h_t = ramp
    psi_top = psi_t[-1]
    W, wb, Wt = spec.ramp_width_m, spec.berm_base_m, spec.ramp_width_m + spec.berm_base_m
    cf = spec.cross_fall_pct / 100
    hb = spec.berm_height_m
    side = math.tan(math.radians(spec.berm_repose_deg))
    Rf, F = spec.floor_radius_m, wall.F

    dx, dz = X - PIT_C[0], Z - PIT_C[1]
    r = np.hypot(dx, dz)
    base = np.mod(np.arctan2(dz, dx) - theta0, 2 * np.pi)  # psi increases counter-clockwise

    H = np.full(X.shape, np.nan)
    zone = np.full(X.shape, PIT, np.uint8)
    above_psi = np.full(X.shape, np.nan)   # psi of the ramp leg directly below this wall pixel
    o_prev = floor_outer(base - 2 * np.pi, a_t[0], spec)
    h_prev = np.full(X.shape, F)
    below_o, below_h = o_prev.copy(), h_prev.copy()   # outer edge / height of the ledge directly below
    H[r < o_prev] = F
    rc_t = a_t + wb + W / 2
    drds_t = np.gradient(a_t, psi_t) / np.hypot(rc_t, np.gradient(a_t, psi_t))
    for m in range(int(psi_top // (2 * np.pi)) + 2):
        p = base + 2 * np.pi * m
        valid = (p >= psi_t[0]) & (p <= psi_top)
        am = np.interp(p, psi_t, a_t)
        hm = np.interp(p, psi_t, h_t)
        gm = spec.ramp_grade_pct / 100 * np.interp(p, psi_t, drds_t)
        gap = valid & (r >= o_prev) & (r < am)
        H[gap] = np.minimum(wall.climb(h_prev[gap], r[gap] - o_prev[gap]), hm[gap])
        band = valid & (r >= am) & (r < am + Wt)
        x = r - am
        # cross-fall drains toward the highwall; + grade*dr/ds keeps it perpendicular to travel
        h_run = hm + (gm - cf) * (x - (wb + W / 2))
        h_in = hm - (gm - cf) * W / 2                   # running-surface height at the crest edge
        drop = hm - h_prev
        need_berm = np.clip((drop - hb) / hb, 0, 1)    # windrow only where the drop-off exceeds mid-axle height
        tri = np.clip(np.minimum(x, wb - x) * side, 0, hb) * need_berm  # trapezoid, sides at repose angle
        hbnd = np.where(x < wb, h_in + tri, h_run)
        H[band] = hbnd[band]
        zone[band & (x >= wb)] = ROAD
        zone[band & (x < wb) & (need_berm > 0.5)] = BERM
        zone[band & (x < wb) & (need_berm <= 0.5)] = ROAD
        o_prev = np.where(valid, am + Wt, o_prev)
        h_prev = np.where(valid, hm, h_prev)
        here = valid & (r >= am)
        above_psi = np.where(here, p, above_psi)
        below_o = np.where(here, am + Wt, below_o)
        below_h = np.where(here, hm, below_h)
    rest = np.isnan(H)
    H[rest] = wall.climb(h_prev[rest], r[rest] - o_prev[rest])
    H = np.minimum(H, rim)
    info = {"r": r, "o_prev": below_o, "h_prev": below_h, "above_psi": above_psi}
    return H, zone, info


def ramp_xy(psi, theta0, a, spec):
    rc = a + spec.berm_base_m + spec.ramp_width_m / 2
    th = theta0 + psi
    return np.stack([PIT_C[0] + rc * np.cos(th), PIT_C[1] + rc * np.sin(th)], 1)


# ---------------------------------------------------------------- build
def crest_radius(spec, theta0=0.0):
    """Max crest radius of the designed pit (rim at y=0), evaluated on a polar sample."""
    wall = Wall(spec, -spec.depth_m)
    ramp = design_ramp(spec, wall, 0.0)
    rr = np.arange(0, 400, 0.5)
    th = np.radians(np.arange(0, 360, 2.0))
    R, T = np.meshgrid(rr, th)
    Hp, _, _ = pit_surface(PIT_C[0] + R * np.cos(T), PIT_C[1] + R * np.sin(T), spec, wall, ramp, theta0, 0.0)
    return float(R[Hp < -1e-3].max()), ramp


def build(seed=42, spec=None, size=None):
    """size: world edge (m). None = auto from the pit. With a size, benches are dropped until the pit fits."""
    global SIZE, ORIGIN
    spec = spec or MS.preset()
    W_road, W_ramp = spec.road_width_m, spec.ramp_width_m
    cd = spec.truck.clearance_diameter_m
    rp = {"crusher": max(10.0, 0.75 * cd), "dump_south": max(12.0, 0.9 * cd),
          "parking": max(10.0, 0.75 * cd), "fuel": max(8.0, 0.5 * cd)}
    margin = max(rp.values()) + 5
    if size:
        while True:
            r_crest_est, ramp0 = crest_radius(spec)
            limit = size / 2 - margin - W_road / 2 - 3
            if r_crest_est <= limit or spec.n_benches <= 2:
                break
            spec.notes.append(f"n_benches {spec.n_benches} -> {spec.n_benches - 1}: crest {r_crest_est:.0f} m "
                              f"> {limit:.0f} m allowed by the {size:.0f} m world")
            spec.n_benches -= 1
        SIZE = float(size)
    else:
        r_crest_est, ramp0 = crest_radius(spec)
        SIZE = float(math.ceil(2 * (r_crest_est + margin + W_road + 10) / 20.0) * 20)
    ORIGIN = (-SIZE / 2, -SIZE / 2)
    psi_t, a_t, h_t = ramp0

    n = int(SIZE / RES) + 1
    xs = ORIGIN[0] + np.arange(n) * RES
    X, Z = np.meshgrid(xs, ORIGIN[1] + np.arange(n) * RES)

    # --- base terrain: gentle rolling ground, rising slightly to the north-west
    H0 = 4.0 * C.fbm(X, Z, seed, octaves=5, freq=1 / 120) + 0.01 * (Z - X)
    rim_level = float(np.median(H0))
    floor_h = rim_level - spec.depth_m
    wall = Wall(spec, floor_h)
    ramp = (psi_t, a_t, h_t + rim_level)
    psi_t, a_t, h_t = ramp

    # ramp top exits on the east side (between crusher NE and ready line SE); psi increases CCW going up
    theta_top = 0.0
    theta0 = theta_top - psi_t[-1]

    Hp, zone, info = pit_surface(X, Z, spec, wall, ramp, theta0, rim_level)
    pit_mask = Hp < rim_level - 1e-3
    zp = zone.copy()
    r = info["r"]
    r_crest = float(r[pit_mask].max())
    blend = C.smoothstep((r - r_crest - 10.0) / 40.0)
    G = rim_level * (1 - blend) + H0 * blend
    H = np.where(pit_mask, Hp, G)
    zone[~pit_mask] = NATURAL
    zone[pit_mask & (zone == NATURAL)] = PIT

    # --- pads (flattened) in the corners of the lease
    c = SIZE / 2 - margin
    pos = {"crusher": (c, c), "parking": (c, -c), "dump_south": (-c, -c),
           "fuel": (c, -c + rp["parking"] + rp["fuel"] + 8)}
    names = {"crusher": ("crusher", "Primary Crusher"), "dump_south": ("dump", "South Waste Dump"),
             "parking": ("parking", "Ready Line"), "fuel": ("fuel", "Fuel Bay")}
    PADS = {}
    for pid in ("crusher", "dump_south", "parking", "fuel"):
        p0, rad = (round(pos[pid][0], 1), round(pos[pid][1], 1)), rp[pid]
        PADS[pid] = (*names[pid], p0, rad)
        d = np.hypot(X - p0[0], Z - p0[1])
        h0 = float(H[d < rad].mean())
        tt = C.smoothstep((d - rad) / max(8.0, 0.6 * rad))
        H = h0 * (1 - tt) + H * tt
        zone[d < rad] = PAD

    # --- ramp centreline (bottom -> top)
    ramp_c = ramp_xy(psi_t, theta0, a_t, spec)
    on = np.ones(len(psi_t), bool)
    ramp_top, ramp_bot = ramp_c[-1], ramp_c[0]
    tang = ramp_c[-1] - ramp_c[-20]
    tang /= np.linalg.norm(tang)
    ang_h = theta_top + 12.0 / max(r_crest, 1.0)
    hub = PIT_C + (r_crest + W_road / 2 + 3) * np.array([math.cos(ang_h), math.sin(ang_h)])
    hub = np.clip(hub, ORIGIN[0] + W_road / 2 + 2, -ORIGIN[0] - W_road / 2 - 2)

    # --- surface haul roads (two-lane, spec width)
    P = {k: np.array(v[2]) for k, v in PADS.items()}
    surf = [
        ("ramp_access", "Ramp Access", "j_ramp_top", "j_hub", [ramp_top, ramp_top + tang * 6.0, hub]),
        ("crusher_road", "Crusher Road", "j_hub", "crusher", [hub, P["crusher"]]),
        ("fuel_road", "Fuel Bay Road", "j_hub", "fuel", [hub, P["fuel"]]),
        ("parking_road", "Ready Line Road", "fuel", "parking", [P["fuel"], P["parking"]]),
        ("dump_road", "South Dump Road", "parking", "dump_south", [P["parking"], P["dump_south"]]),
    ]
    for _, _, _, _, poly in surf:
        surface_road(H, zone, poly, W_road, h_start=rim_level if poly[0] is ramp_top else None,
                     crown=spec.cross_fall_pct / 100)
    # roads never fill into the pit: restore the designed pit surface
    H = np.where(pit_mask, Hp, H)
    zone = np.where(pit_mask, zp, zone)

    # --- bench loading bays: cut into the highwall on the outer side of the ramp at bench levels
    ore, spurs = [], []
    # bay depth: ~0.65 x turning-clearance diameter; the truck turns using bay + ramp width (3-point turn)
    bay_depth = max(12.0, math.ceil(0.65 * spec.truck.clearance_diameter_m))
    bay_len = max(25.0, 1.5 * spec.truck.clearance_diameter_m)
    surface = np.isin(zone, (ROAD, PAD)) & ~pit_mask
    above = info["above_psi"]
    compass = ["East", "North-East", "North", "North-West", "West", "South-West", "South", "South-East"]
    N = spec.n_benches
    lvls = sorted({min(max(int(round(N * q)), 1), N - 1) for q in (0.4, 0.7)})
    for lvl, tonnes in zip(lvls, (6000, 4500)):
        hk = rim_level - spec.bench_height_m * lvl
        pk = float(np.interp(hk, h_t, psi_t))
        ak = float(np.interp(pk, psi_t, a_t))
        rc_k = ak + spec.berm_base_m + W_ramp / 2
        o_k = ak + spec.berm_base_m + W_ramp
        sel = np.isfinite(above) & (np.abs(above - pk) * rc_k < bay_len / 2 + 40) & (r >= info["o_prev"]) & ~surface
        u = r[sel] - info["o_prev"][sel]
        v = (above[sel] - pk) * rc_k
        # bay depth tapers to zero over 40 m either side so the benches above wrap around it smoothly
        tau = 1 - C.smoothstep((np.abs(v) - bay_len / 2) / 40.0)
        hc = info["h_prev"][sel]
        h_base = hk * tau + hc * (1 - tau)
        hband = hc - spec.cross_fall_pct / 100 * W_ramp / 2
        dep = bay_depth * tau
        floor_b = h_base + (hband - h_base) * (1 - C.smoothstep(u / np.maximum(0.45 * dep, 1e-3)))
        cut = np.where(u < dep, floor_b, wall.climb(h_base, u - dep))
        old = H[sel]
        H[sel] = np.minimum(old, cut)
        zs = zone[sel]
        zs[(zs == NATURAL) & (H[sel] < old - 0.3)] = PIT
        zs[(u < dep) & (tau > 0.5)] = ROAD
        zone[sel] = zs
        th = theta0 + pk
        out_dir = np.array([math.cos(th), math.sin(th)])
        p_site = PIT_C + out_dir * (o_k + bay_depth / 2)
        cname = compass[int(round(math.degrees(math.atan2(out_dir[1], out_dir[0])) / 45)) % 8]
        sid = f"pit_{cname.lower().replace('-', '')}"
        ore.append((sid, f"{cname} Bench", p_site, tonnes))
        jidx = int(np.argmin(np.abs(psi_t[on] - pk)))
        spurs.append((jidx, sid, cname))

    # floor loading area
    fp = PIT_C + np.array([4.0, -6.0])
    ore.insert(0, ("pit_floor", "Pit Floor", fp, 9000))
    floor_rd = densify([ramp_bot, fp])
    tree = cKDTree(floor_rd)
    near = tree.query(np.stack([X.ravel(), Z.ravel()], 1), distance_upper_bound=W_road / 2)[0].reshape(X.shape)
    zone[np.isfinite(near) & (np.abs(H - floor_h) < 0.3)] = ROAD

    # ore tint zone
    for _, _, p, _ in ore:
        zone[(np.hypot(X - p[0], Z - p[1]) < 14) & (zone != ROAD) & (zone != BERM)] = ORE

    # --- road graph (sampled from the FINAL heightmap); ramp ordered top -> bottom
    hfun = lambda xz: sample(H, np.asarray(xz, float))
    ramp_td = ramp_c[::-1]
    nr = len(ramp_c)
    junc = {"j_ramp_top": ("Ramp Top", ramp_top), "j_hub": ("Rim Hub", hub), "j_ramp_bottom": ("Ramp Bottom", ramp_bot)}
    roads = [C.road_entry(i, nm, "haul", a, b, poly, hfun, W_road) for i, nm, a, b, poly in surf]
    cuts, nodes = [0], ["j_ramp_top"]
    site_pos = {o[0]: o[2] for o in ore}
    for jidx, sid, cname in sorted(spurs, key=lambda t: -t[0]):  # top-most first
        k = nr - 1 - jidx
        jid = f"j_{sid[4:]}_spur"
        junc[jid] = (f"{cname} Spur Junction", ramp_td[k])
        roads.append(C.road_entry(f"{sid}_spur", f"{cname} Bench Spur", "spur", jid, sid,
                                  [ramp_td[k], site_pos[sid]], hfun, W_road))
        cuts.append(k)
        nodes.append(jid)
    cuts.append(nr - 1)
    nodes.append("j_ramp_bottom")
    for k, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
        roads.append(C.road_entry(f"ramp_main_{k + 1}", f"Main Haul Ramp ({k + 1}/{len(cuts) - 1})", "ramp",
                                  nodes[k], nodes[k + 1], ramp_td[a:b + 1], hfun, W_ramp))
    roads.append(C.road_entry("floor_road", "Pit Floor Road", "haul", "j_ramp_bottom", "pit_floor",
                              [ramp_bot, fp], hfun, W_road))
    junctions = [{"id": k, "name": nm, "pos": C.pos3(p, hfun)} for k, (nm, p) in junc.items()]

    # --- colors
    sl = C.slope_deg(H, RES)
    noise = C.fbm(X, Z, seed + 21, octaves=4, freq=1 / 25)[..., None]
    palette = {
        NATURAL: (0.62, 0.52, 0.38), PIT: (0.50, 0.44, 0.38), ROAD: (0.70, 0.68, 0.64),
        PAD: (0.66, 0.64, 0.60), ORE: (0.30, 0.55, 0.50), BERM: (0.47, 0.36, 0.26),
    }
    col = np.zeros(H.shape + (3,))
    for z, c in palette.items():
        col[zone == z] = c
    rock = (C.smoothstep((sl - 28) / 15) * (zone != BERM))[..., None]
    col = col * (1 - rock) + np.array([0.42, 0.40, 0.39]) * rock
    col = np.clip(col * (1 + 0.12 * noise), 0, 1)

    # --- sites & trucks
    sites = [{"id": k, "type": v[0], "name": v[1], "pos": [round(v[2][0], 1), round(v[2][1], 1)]}
             for k, v in PADS.items()]
    sites += [{"id": i, "type": "ore", "name": nm, "pos": [round(float(p[0]), 1), round(float(p[1]), 1)],
               "tonnes": tn} for i, nm, p, tn in ore]
    for st in sites:
        st["pos3"] = C.pos3(st["pos"], hfun)
    # 3 trucks on the ready line, facing the exit toward the fuel bay.
    # heading_deg: 0 = +z (north), clockwise positive (Unity eulerAngles.y) -> atan2(dx, dz)
    pk_, ex = P["parking"], P["fuel"] - P["parking"]
    heading = round(float(np.degrees(np.arctan2(ex[0], ex[1]))) % 360, 1)
    side = np.array([ex[1], -ex[0]]) / np.linalg.norm(ex)
    gap = max(6.0, 1.6 * spec.truck.width_m)
    trucks = [{"id": f"truck-{i}", "callsign": f"Truck {i}",
               "spawn": [round(float(pk_[0] + side[0] * gap * (i - 1)), 1), round(float(pk_[1] + side[1] * gap * (i - 1)), 1)],
               "heading_deg": heading, "capacity_t": spec.truck.payload_t, "model": spec.truck.name} for i in range(3)]

    # curvature of the as-designed ramp centreline
    dxy = np.gradient(ramp_c, axis=0)
    ddxy = np.gradient(dxy, axis=0)
    curv = np.abs(dxy[:, 0] * ddxy[:, 1] - dxy[:, 1] * ddxy[:, 0]) / np.maximum(np.hypot(*dxy.T) ** 3, 1e-9)
    curv = gaussian_filter1d(curv, 10)
    seg = np.linalg.norm(np.diff(ramp_c, axis=0), axis=1).sum()
    meta = {"rim_level": rim_level, "floor_level": floor_h, "ramp_length_m": float(seg),
            "ramp_turns": float(psi_t[-1] / (2 * np.pi)), "ramp_runout_m": 40.0,
            "ramp_min_centreline_radius_m": float(1 / curv.max()),
            "pit_crest_radius_m": r_crest, "floor_radius_m": spec.floor_radius_m,
            "ramp_top": ramp_top.round(1).tolist(), "ramp_bottom": ramp_bot.round(1).tolist(),
            "design_truck": spec.truck.key}
    meta["graph"] = (junctions, roads)
    return H, zone, col, sl, sites, trucks, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--id", default="default")
    ap.add_argument("--name", default="Copper Ridge Pit")
    ap.add_argument("--truck", default=MS.DEFAULT_TRUCK, choices=sorted(MS.TRUCKS),
                    help="design truck preset (drives road width, berm height, bench height ...)")
    ap.add_argument("--size", type=float, default=None,
                    help="world edge in m (default: 260 for komatsu_hd465 to match the 200-260 m sim quarry; "
                         "auto for the big trucks)")
    a = ap.parse_args()

    spec = MS.preset(a.truck)
    size = a.size if a.size else (260.0 if a.truck == "komatsu_hd465" else None)
    out = C.WORLDS / a.id
    out.mkdir(parents=True, exist_ok=True)
    H, zone, col, sl, sites, trucks, meta = build(a.seed, spec, size)
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

    design = spec.to_dict()
    world = {
        "id": a.id, "name": a.name, "source": "procedural",
        "size_m": [SIZE, SIZE], "origin": list(ORIGIN),
        "bounds": [ORIGIN[0], ORIGIN[1], ORIGIN[0] + SIZE, ORIGIN[1] + SIZE],  # [xmin, zmin, xmax, zmax]
        "assets": {"splat": None, "collider": None, "terrain_mesh": "terrain.glb", "pano": None,
                   "concept": None, "heightmap_raw": "heightmap.raw", "preview": "preview.png",
                   "heightmap_meta": "heightmap_meta.json"},
        "splat_transform": {"scale": 1.0, "ground_offset": 0.0, "rotation_euler_deg": [0, 0, 0]},
        "sites": sites, "junctions": junctions, "roads": roads,
        "trucks": trucks, "navgrid": "navgrid.json",
        "generator": {"seed": a.seed, **{k2: (round(v, 2) if isinstance(v, float) else v) for k2, v in meta.items()}},
        "design": design,
    }
    errs = C.check_road_graph(world)
    assert not errs, f"road graph invalid: {errs}"
    # load / dump stop poses, ready-line slots (trucks spawn on them) and one excavator per ore face
    op_errs = C.add_operating_points(world, cost, Hc, ORIGIN, CELL, spec.truck,
                                     hfun=lambda xz: sample(H, np.atleast_2d(np.asarray(xz, float))))
    assert not op_errs, f"operating points invalid: {op_errs}"
    for r in roads:
        want = spec.ramp_width_m if r["type"] == "ramp" else spec.road_width_m
        assert r["width_m"] == want, (r["id"], r["width_m"], want)
    res = 1025 if SIZE > 500 else 513
    C.export_terrain_glb(out / "terrain.glb", H, col, ORIGIN, (SIZE, SIZE), n=321 if SIZE <= 300 else 401)
    C.write_heightmap_raw(out, H, ORIGIN, (SIZE, SIZE), res=res)

    from . import measure_geometry as MG
    asb = MG.measure(out, world)
    asb["ramp_min_centreline_radius_m"] = {"spec_min": spec.min_curve_radius_m,
                                           "measured": round(meta["ramp_min_centreline_radius_m"], 1),
                                           "ok": meta["ramp_min_centreline_radius_m"] >= spec.min_curve_radius_m}
    design["as_built"] = asb
    C.preserve_assets(out, world)                                    # keep pano / pano_source / surround
    C.write_json(out / "world.json", world)
    C.refresh_surround(out, world)                                   # surround.glb matches the new heightmap
    C.write_preview(out / "preview.png", H, col, RES, world)

    bad = C.check_reachability(world, nav)
    passable = (cost > 0).mean()
    print(f"wrote {out}  size {SIZE:.0f} m  heights {H.min():.1f}..{H.max():.1f} m  passable {passable:.0%}  "
          f"sites {len(sites)}  roads {len(roads)}  junctions {len(junctions)}  trucks {len(trucks)}  "
          f"unreachable: {bad or 'none'}")
    print(f"design truck {spec.truck.name}: violations {design['violations'] or 'none'}; "
          f"warnings {design['warnings'] or 'none'}")
    print(MG.report(asb))
    for x in sites:
        for k2 in ("load_point", "dump_point"):
            if k2 in x:
                print(f"  {x['id']:10s} {k2}: {x[k2]['pos3']} hdg {x[k2]['heading_deg']}")
        for i, q in enumerate(x.get("slots", [])):
            print(f"  {x['id']:10s} slot {i}: {q['pos3']} hdg {q['heading_deg']}")
    for e in world["equipment"]:
        print(f"  {e['id']} @ {e['face']}: {e['pos3']} hdg {e['heading_deg']}")


if __name__ == "__main__":
    main()
