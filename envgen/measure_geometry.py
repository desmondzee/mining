"""Measure as-built pit / haul-road geometry from a world's heightmap.raw and compare with world.json "design".

    python -m envgen.measure_geometry --id default

Measures (from the 16-bit heightmap only, not from generator internals):
bench face angle, bench height, catch-berm width, inter-ramp + overall slope,
ramp grade, ramp running width, cross-fall, safety-windrow height, surface road width, floor diameter.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

from . import common as C


def load_heightmap(world_dir):
    meta = json.loads((Path(world_dir) / "heightmap_meta.json").read_text())
    n = meta["resolution"]
    q = np.frombuffer((Path(world_dir) / "heightmap.raw").read_bytes(), "<u2").reshape(n, n).astype(float)
    H = meta["min_height_m"] + q / 65535 * meta["height_range_m"]
    spacing = meta["size_m"][0] / (n - 1)
    return H, meta["origin"], spacing


def _sampler(H, origin, spacing):
    def f(xz):
        xz = np.atleast_2d(xz)
        return map_coordinates(H, [(xz[:, 1] - origin[1]) / spacing, (xz[:, 0] - origin[0]) / spacing],
                               order=1, mode="nearest")
    return f


def _cross(xs, hs, level):
    """First x where hs crosses level going up (linear interpolation)."""
    i = np.nonzero((hs[:-1] < level) & (hs[1:] >= level))[0]
    if not len(i):
        return None
    i = i[0]
    return xs[i] + (level - hs[i]) / (hs[i + 1] - hs[i]) * (xs[i + 1] - xs[i])


def measure(world_dir, world=None):
    world = world or json.loads((Path(world_dir) / "world.json").read_text())
    d = world["design"]
    H, origin, sp = load_heightmap(world_dir)
    h = _sampler(H, origin, sp)
    g = world["generator"]
    c = np.array([0.0, 0.0])
    rim, floor = g["rim_level"], g["floor_level"]
    Hb = d["bench_height_m"]
    step = 0.1

    # ---- benches along 72 rays: full-height faces only (ramp-cut partial faces are skipped)
    faces, berms, overall, crest_r = [], [], [], []
    for ang in np.radians(np.arange(0, 360, 5)):
        u = np.array([np.cos(ang), np.sin(ang)])
        xs = np.arange(0, g["pit_crest_radius_m"] + 20, step)
        hs = h(c + xs[:, None] * u)
        hs = np.maximum.accumulate(hs)  # profile is monotone outward except windrows; ignore those bumps
        x_floor = _cross(xs, hs, floor + 0.05)
        x_crest = _cross(xs, hs, rim - 0.05)
        if x_floor is not None and x_crest is not None:
            overall.append(np.degrees(np.arctan((rim - floor) / (x_crest - x_floor))))
            crest_r.append(x_crest)
        prev_crest = None
        for k in range(int(round((rim - floor) / Hb))):
            L0 = floor + k * Hb
            x10, x90 = _cross(xs, hs, L0 + 0.1 * Hb), _cross(xs, hs, L0 + 0.9 * Hb)
            x00, x100 = _cross(xs, hs, L0 + 0.02 * Hb), _cross(xs, hs, L0 + 0.98 * Hb)
            if None in (x10, x90, x00, x100):
                prev_crest = None
                continue
            # a clean design face: whole rise happens within ~1.6x the 10-90 % run (no ramp ledge inside)
            f_design = Hb / np.tan(np.radians(d["face_angle_deg"]))
            if (x100 - x00) > 1.35 * (x90 - x10) + 2 * sp or (x90 - x10) > 2 * f_design + 2 * sp:
                prev_crest = None
                continue
            ang_f = np.degrees(np.arctan(0.8 * Hb / max(x90 - x10, 1e-6)))
            fh = (x90 - x10) / 0.8
            toe, crest = x10 - 0.1 * fh, x90 + 0.1 * fh
            faces.append(ang_f)
            if prev_crest is not None:
                bw = toe - prev_crest
                if 0 < bw < 3 * d["berm_width_m"]:
                    berms.append(bw)
            prev_crest = crest

    # ---- ramp: grade along centreline, running width / cross-fall / windrow across it
    ramp_pts = []
    for r in world["roads"]:
        if r["type"] == "ramp":
            ramp_pts.extend(r["points"] if not ramp_pts else r["points"][1:])
    P = np.array(ramp_pts)[:, [0, 2]]
    P = C.resample(P, 1.0)
    hp = h(P)
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    grade_mean = abs(hp[-1] - hp[0]) / s[-1] * 100
    win = 20
    g20 = np.abs(hp[win:] - hp[:-win]) / (s[win:] - s[:-win]) * 100
    widths, xfall, wrow = [], [], []
    spur_pts = np.array([j["pos"] for j in world["junctions"]])[:, [0, 2]]
    for i in range(30, len(P) - 30, 15):
        if np.min(np.linalg.norm(spur_pts - P[i], axis=1)) < 25:
            continue
        t = P[i + 2] - P[i - 2]
        t /= np.linalg.norm(t)
        nrm = np.array([-t[1], t[0]])
        if np.dot(nrm, P[i] - c) < 0:
            nrm = -nrm  # +nrm points outward (highwall side)
        off = np.arange(-30, 30, step)
        prof = h(P[i] + off[:, None] * nrm)
        h0 = hp[i]
        slope = np.degrees(np.arctan(np.abs(np.gradient(prof, step))))
        ok = (np.abs(prof - h0) < 0.6) & (slope < 10.0)
        j0 = len(off) // 2
        if not ok[j0]:
            continue
        lo = j0
        while lo > 0 and ok[lo - 1]:
            lo -= 1
        hi = j0
        while hi < len(off) - 1 and ok[hi + 1]:
            hi += 1
        # refine edges: extend while within 5 cm of the fitted cross-fall plane (removes slope-filter blur)
        seg = slice(lo + 10, hi - 10)
        if hi - lo <= 40:
            continue
        pf = np.polyfit(off[seg], prof[seg], 1)
        xfall.append(-pf[0] * 100)  # + = falls toward highwall
        dev = np.abs(prof - np.polyval(pf, off))
        while lo > 0 and dev[lo - 1] < 0.05:
            lo -= 1
        while hi < len(off) - 1 and dev[hi + 1] < 0.05:
            hi += 1
        widths.append(off[hi] - off[lo])
        inner = prof[max(lo - 80, 0):lo + 1]
        if len(inner):
            wrow.append(float(inner.max() - prof[lo]))

    # ---- surface road width (crusher road): flat-across region around centreline
    sw = []
    for r in world["roads"]:
        if r["id"] != "crusher_road":
            continue
        Q = C.resample(np.array(r["points"])[:, [0, 2]], 1.0)
        for i in range(15, len(Q) - 15, 5):
            t = Q[i + 2] - Q[i - 2]
            t /= np.linalg.norm(t)
            nrm = np.array([-t[1], t[0]])
            off = np.arange(-25, 25, step)
            prof = h(Q[i] + off[:, None] * nrm)
            crown = d["cross_fall_pct"] / 100 * np.abs(off)  # surface roads are crowned
            ok = np.abs(prof - (prof[len(off) // 2] - crown)) < 0.05
            j0 = len(off) // 2
            lo, hi = j0, j0
            while lo > 0 and ok[lo - 1]:
                lo -= 1
            while hi < len(off) - 1 and ok[hi + 1]:
                hi += 1
            sw.append(off[hi] - off[lo])

    # floor diameter: flat region at floor level through the centre
    fl = []
    for ang in np.radians(np.arange(0, 180, 15)):
        u = np.array([np.cos(ang), np.sin(ang)])
        xs = np.arange(-80, 80, step)
        ok = np.abs(h(c + xs[:, None] * u) - floor) < 0.3
        j0 = len(xs) // 2
        lo, hi = j0, j0
        while lo > 0 and ok[lo - 1]:
            lo -= 1
        while hi < len(xs) - 1 and ok[hi + 1]:
            hi += 1
        fl.append(xs[hi] - xs[lo])

    med = lambda a: round(float(np.median(a)), 2) if len(a) else None
    rng = lambda a: [round(float(np.percentile(a, 10)), 2), round(float(np.percentile(a, 90)), 2)] if len(a) else None
    berms_all = berms
    berms = [x for x in berms_all if x >= 0.8 * d["berm_width_m"]]
    iqr = lambda a: [round(float(np.percentile(a, 25)), 2), round(float(np.percentile(a, 75)), 2)] if len(a) else None
    fa, bw = med(faces), med(berms)
    return {
        "method": "measured from heightmap.raw (16-bit, %.3f m/px) by envgen.measure_geometry" % sp,
        "bench_face_angle_deg": {"spec": d["face_angle_deg"], "measured_median": fa, "p25_p75": iqr(faces), "n": len(faces)},
        "berm_width_m": {"spec": d["berm_width_m"], "measured_median": bw, "p25_p75": iqr(berms), "n": len(berms),
                         "all_berms_median": med(berms_all), "n_all": len(berms_all),
                         "note": "measured_median = full catch berms (>= 0.8 x spec). Berms right above a ramp leg "
                                 "are partial by design (the ramp is the catch surface there)"},
        "inter_ramp_angle_deg": {"spec": d["inter_ramp_angle_deg"],
                                 "measured": round(float(np.degrees(np.arctan(Hb / (Hb / np.tan(np.radians(fa)) + bw)))), 1)
                                 if fa and bw else None},
        "overall_slope_deg": {"spec_one_ramp_leg": d["overall_slope_deg"], "spec_two_ramp_legs": d["overall_slope_deg_two_ramp_legs"],
                              "measured_median": med(overall), "p10_p90": rng(overall)},
        "pit_crest_radius_m": {"measured_min_max": [round(float(min(crest_r)), 1), round(float(max(crest_r)), 1)] if crest_r else None},
        "ramp_grade_pct": {"spec": d["ramp_grade_pct"], "measured_mean": round(float(grade_mean), 2),
                           "measured_max_20m": round(float(g20.max()), 2)},
        "ramp_running_width_m": {"spec": d["ramp_width_m"], "measured_median": med(widths), "p10_p90": rng(widths), "n": len(widths)},
        "ramp_cross_fall_pct": {"spec": d["cross_fall_pct"], "measured_median": med(xfall)},
        "safety_berm_height_m": {"spec": d["safety_berm_height_m"], "measured_median": med(wrow), "p10_p90": rng(wrow)},
        "surface_road_width_m": {"spec": d["road_width_m"], "measured_median": med(sw), "p10_p90": rng(sw), "n": len(sw)},
        "floor_diameter_m": {"spec_min": d["design_truck"]["clearance_diameter_m"], "measured_min": round(float(min(fl)), 1)},
    }


def report(m):
    lines = [f"{'quantity':24s} {'spec':>10s} {'measured':>10s}  detail"]
    for k, v in m.items():
        if not isinstance(v, dict):
            continue
        spec = next((v[x] for x in ("spec", "spec_one_ramp_leg", "spec_min") if x in v), "")
        meas = next((v[x] for x in ("measured_median", "measured_mean", "measured", "measured_min", "measured_min_max") if x in v), "")
        extra = {kk: vv for kk, vv in v.items() if kk not in ("spec", "measured_median", "measured_mean", "measured")}
        lines.append(f"{k:24s} {str(spec):>10s} {str(meas):>10s}  {extra}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="default")
    a = ap.parse_args()
    print(report(measure(C.WORLDS / a.id)))
