"""[exc-aim] Scripted excavator dig/dump cycle per machine: dig at the toe of the highwall, dump over the truck bed
at the face's load_point. Precomputed once and stored in world.json equipment[]:

    dig_yaw_deg   world heading (0 = +z, atan2(dx, dz)) from the slew axis to the face (steepest terrain rise)
    dump_yaw_deg  world heading from the slew axis to the truck bed centre at the load_point
    dig_target    pos3 of the bucket pin at the toe of the face (bucket teeth on the ground)
    dump_target   pos3 of the bucket pin over the bed centre (bed top + DUMP_CLEAR_M), clamped to reach
    exc_keys_deg  dig-cycle keyframes [phase, house, boom, stick, bucket] (deg, house relative to heading_deg,
                  signs as assets/models/excavator/rig.json), played by envgen/preview/index.html and isaac/animate.py
    exc_period_s  cycle period
    exc_aim       diagnostics (reach, distances, warnings)

2-link planar IK in the boom plane using the rig.json pins (boom foot -> boom/stick pin -> stick/bucket pin), with the
model scaled like the preview / Isaac (max(bbox x, z) = 10 m, origin = bbox bottom centre).

    python -m envgen.exc_aim [data/worlds/quarry]       # updates world.json in place, prints a report
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RIG = os.path.join(REPO, "assets/models/excavator/rig.json")
GLB = os.path.join(REPO, "assets/models/excavator/excavator_rigged.glb")
EXC_LENGTH_M = 10.0          # preview EXC_LENGTH / build_stage EXC_LENGTH_M
BED_TOP_M = 4.5              # HD465 bed top above the truck origin (preview: bedPivot bbox max y - truck y)
BED_BACK_M = 0.0             # bed centre is only ~0.35 m behind the truck origin and trucks may arrive either way round
DUMP_CLEAR_M = 1.5           # bucket pin above the bed top when dumping
TEETH_M = 1.4                # bucket pin above the ground when the open bucket's teeth touch it
REACH_FRAC = 0.95
BED_R_M = 7.0               # nudge target: bed centre this far from the slew axis (tail swing ~3 m + half truck)
LIMITS = {"boom": (-35.0, 25.0), "stick": (-65.0, 5.0), "bucket": (-135.0, 10.0)}
# bucket pitch (boom + stick + bucket, deg) per key, from the original hand-tuned EXC_KEYS, so the bucket keeps the
# same attitude (open at the face, curled when full, tipped over the bed) whatever boom/stick the IK picks
PHASES = [0.00, 0.12, 0.30, 0.42, 0.60, 0.70, 0.78, 1.00]
PITCH = [-90, -115, -8, -19, -29, -157, -147, -90]


def _glb_bbox(path):
    f = open(path, "rb").read()
    n = struct.unpack("<I", f[12:16])[0]
    g = json.loads(f[20:20 + n])

    def mat(nd):
        if "matrix" in nd:
            return np.array(nd["matrix"]).reshape(4, 4).T
        x, y, z, w = nd.get("rotation", [0, 0, 0, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4)
        M[:3, :3] = R @ np.diag(nd.get("scale", [1, 1, 1]))
        M[:3, 3] = nd.get("translation", [0, 0, 0])
        return M
    lo, hi = np.full(3, 1e9), np.full(3, -1e9)

    def walk(i, P):
        nonlocal lo, hi
        nd = g["nodes"][i]
        M = P @ mat(nd)
        for pr in (g["meshes"][nd["mesh"]]["primitives"] if "mesh" in nd else []):
            a = g["accessors"][pr["attributes"]["POSITION"]]
            for c in range(8):
                q = (M @ np.array([a["max"][k] if c >> k & 1 else a["min"][k] for k in range(3)] + [1]))[:3]
                lo, hi = np.minimum(lo, q), np.maximum(hi, q)
        for ch in nd.get("children", []):
            walk(ch, M)
    for r in g["scenes"][0]["nodes"]:
        walk(r, np.eye(4))
    return lo, hi


class Arm:
    """Boom plane geometry in metres: r = horizontal distance from the slew axis along the house forward,
    h = height above the excavator's ground (pos3 y)."""

    def __init__(self):
        rig = json.load(open(RIG))["joints"]
        lo, hi = _glb_bbox(GLB) if os.path.exists(GLB) else (np.array([-1.94, 0.065, -3.72]), np.array([1.95, 6.0, 6.2]))
        size = hi - lo
        self.s = s = EXC_LENGTH_M / max(size[0], size[2])
        cz, y0 = (lo[2] + hi[2]) / 2, lo[1]
        P = {k: np.array(rig[k]["pivot_m"], float) for k in ("house", "boom", "stick", "bucket")}
        self.slew_z = (P["house"][2] - cz) * s                    # slew axis along the excavator's forward (m)
        self.B = np.array([(P["boom"][2] - P["house"][2]) * s, (P["boom"][1] - y0) * s])   # boom foot (r, h)
        S = np.array([P["stick"][2], P["stick"][1]]) - np.array([P["boom"][2], P["boom"][1]])
        K = np.array([P["bucket"][2], P["bucket"][1]]) - np.array([P["stick"][2], P["stick"][1]])
        self.L1, self.L2 = np.linalg.norm(S) * s, np.linalg.norm(K) * s
        self.a1 = math.atan2(S[1], S[0])                          # rest elevations
        self.a2 = math.atan2(K[1], K[0])
        self.reach = REACH_FRAC * (self.L1 + self.L2)

    def fk(self, boom, stick):
        tb, ts = math.radians(boom), math.radians(stick)
        e = self.B + self.L1 * np.array([math.cos(self.a1 - tb), math.sin(self.a1 - tb)])
        return e + self.L2 * np.array([math.cos(self.a2 - tb - ts), math.sin(self.a2 - tb - ts)])

    def ik(self, r, h):
        """-> (boom, stick) deg (clamped), achieved (r, h), clamped_to_reach flag."""
        d = np.array([r, h]) - self.B
        dist = float(np.linalg.norm(d))
        lo = abs(self.L1 - self.L2) + 0.2
        far = dist > self.reach
        dist_c = min(max(dist, lo), self.reach)
        d = d / max(dist, 1e-9) * dist_c
        al = math.atan2(d[1], d[0]) + math.acos(np.clip((self.L1 ** 2 + dist_c ** 2 - self.L2 ** 2) / (2 * self.L1 * dist_c), -1, 1))
        elbow = self.L1 * np.array([math.cos(al), math.sin(al)])
        be = math.atan2(d[1] - elbow[1], d[0] - elbow[0])
        boom = math.degrees(self.a1 - al)
        stick = math.degrees(self.a2 - be) - boom
        boom = float(np.clip(boom, *LIMITS["boom"]))
        stick = float(np.clip(stick, *LIMITS["stick"]))
        return boom, stick, self.fk(boom, stick), far


def _heightfun(wdir):
    meta = json.load(open(os.path.join(wdir, "heightmap_meta.json")))
    N = meta["resolution"]
    hm = np.fromfile(os.path.join(wdir, "heightmap.raw"), dtype="<u2").reshape(N, N).astype(float)
    hm = meta["min_height_m"] + hm / 65535.0 * meta["height_range_m"]
    (ox, oz), (sx, sz) = meta["origin"], meta["size_m"]

    def hf(x, z):   # same bilinear sampling as the preview's groundY
        c = (x - ox) / sx * (N - 1)
        r = (z - oz) / sz * (N - 1)
        c0, r0 = int(min(max(math.floor(c), 0), N - 2)), int(min(max(math.floor(r), 0), N - 2))
        fc, fr = c - c0, r - r0
        return (hm[r0, c0] * (1 - fc) * (1 - fr) + hm[r0, c0 + 1] * fc * (1 - fr)
                + hm[r0 + 1, c0] * (1 - fc) * fr + hm[r0 + 1, c0 + 1] * fc * fr)
    return hf


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def aim(world, wdir, arm=None, log=print, nudge=True):
    arm = arm or Arm()
    hf = _heightfun(wdir)
    sites = {s["id"]: s for s in world["sites"]}
    for e in world.get("equipment", []):
        if e.get("type", "excavator") != "excavator":
            continue
        warn = []
        lp = sites[e["face"]]["load_point"]
        lh = math.radians(lp["heading_deg"])
        bx, bz = lp["pos3"][0] - BED_BACK_M * math.sin(lh), lp["pos3"][2] - BED_BACK_M * math.cos(lh)
        if nudge:   # trivial placement fix: slide the machine along the line to the bed so the bed is BED_R_M away
            moved = 0.0
            for _ in range(3):
                hd = e["heading_deg"]
                ax = e["pos3"][0] + math.sin(math.radians(hd)) * arm.slew_z
                az = e["pos3"][2] + math.cos(math.radians(hd)) * arm.slew_z
                R = math.hypot(bx - ax, bz - az)
                if abs(R - BED_R_M) < 0.05 or (R <= arm.B[0] + arm.reach - 0.5 and moved == 0.0):
                    break
                ux, uz = (bx - ax) / R, (bz - az) / R
                nx, nz = e["pos3"][0] + ux * (R - BED_R_M), e["pos3"][2] + uz * (R - BED_R_M)
                moved += math.hypot(nx - e["pos3"][0], nz - e["pos3"][2])
                e["pos3"] = [round(nx, 2), round(float(hf(nx, nz)), 2), round(nz, 2)]
                e["heading_deg"] = round(math.degrees(math.atan2(lp["pos3"][0] - nx, lp["pos3"][2] - nz)) % 360.0, 2)
            if moved:
                warn.append(f"moved {moved:.1f} m toward the truck bed so it is within reach (was out of reach)")
        hd = e["heading_deg"]
        fx, fz = math.sin(math.radians(hd)), math.cos(math.radians(hd))
        ex, ey, ez = e["pos3"]
        ax, az = ex + fx * arm.slew_z, ez + fz * arm.slew_z               # slew axis (world xz)
        # --- dig: direction of steepest rise in a 8-15 m ring, toe = where the ground starts rising
        best = None
        for a in np.arange(0, 360, 5.0):
            sa, ca = math.sin(math.radians(a)), math.cos(math.radians(a))
            rise = np.mean([hf(ax + r * sa, az + r * ca) for r in np.arange(8, 15.01, 1.0)]) - ey
            if best is None or rise > best[1]:
                best = (float(a), float(rise))
        dig_yaw, rise = best
        sa, ca = math.sin(math.radians(dig_yaw)), math.cos(math.radians(dig_yaw))
        toe = None
        for r in np.arange(3.0, 15.01, 0.25):
            if hf(ax + r * sa, az + r * ca) > ey + 1.0:
                toe = r - 0.5
                break
        if toe is None:
            toe = 7.0
            warn.append(f"no face rise found within 15 m (max mean rise {rise:.1f} m) - digging at {toe} m")
        dig_r = float(np.clip(toe + 0.5, 5.5, arm.B[0] + arm.reach - 0.3))   # bite into the toe, outside the tracks
        dig_h = hf(ax + dig_r * sa, az + dig_r * ca) - ey + TEETH_M
        if toe > arm.B[0] + arm.reach - 0.3:
            warn.append(f"face toe at {toe:.1f} m is beyond reach ({arm.B[0] + arm.reach:.1f} m) - move the excavator "
                        f"{toe - arm.B[0] - arm.reach + 0.3:.1f} m toward heading {dig_yaw:.0f}")
        # --- dump: bed centre at the load point
        bed_top = lp["pos3"][1] + BED_TOP_M
        dump_yaw = math.degrees(math.atan2(bx - ax, bz - az)) % 360.0
        dump_R = math.hypot(bx - ax, bz - az)
        dump_h = bed_top + DUMP_CLEAR_M - ey
        d_boom, d_stick, d_ach, far = arm.ik(dump_R, dump_h)
        if far or abs(d_ach[0] - dump_R) > 0.3:
            warn.append(f"bed centre at {dump_R:.1f} m from the slew axis is out of reach (max {arm.B[0] + arm.reach:.1f} m): "
                        f"dumping at {d_ach[0]:.1f} m - move the excavator {dump_R - d_ach[0]:.1f} m toward heading {dump_yaw:.0f}")
        # --- keyframes (house relative to heading; dump yaw taken within +-180 of the dig yaw)
        hp = _wrap(dump_yaw - hd)
        hg = hp + _wrap(dig_yaw - dump_yaw)
        drag_r, lift_r = max(dig_r - 2.0, 4.8), max(dig_r - 1.0, 5.5)
        poses = [  # (house, r, h)
            (hg, dig_r, dig_h + 1.6),            # 0.00 at the face, bucket raised
            (hg, dig_r, dig_h),                  # 0.12 teeth on the ground at the toe, bucket open
            (hg, drag_r, dig_h + 0.2),           # 0.30 drag in + curl
            (hg, lift_r, dig_h + 3.5),           # 0.42 lift a full bucket
            (hp, d_ach[0], dump_h + 0.8),        # 0.60 swung over the bed, bucket above it
            (hp, d_ach[0], dump_h),              # 0.70 over the bed centre, open
            (hp, d_ach[0], dump_h + 0.3),        # 0.78
            (hg, dig_r, dig_h + 1.6),            # 1.00 back at the face
        ]
        keys = []
        for ph, pitch, (house, r, h) in zip(PHASES, PITCH, poses):
            b, s_, _, _ = arm.ik(r, h)
            k = float(np.clip(pitch - b - s_, *LIMITS["bucket"]))
            keys.append([ph, round(house, 2), round(b, 2), round(s_, 2), round(k, 2)])
        period = 10.0 if abs(hp - hg) > 120 else 8.0
        pin = lambda yaw, r, h: [round(ax + r * math.sin(math.radians(yaw)), 3), round(ey + h, 3),
                                 round(az + r * math.cos(math.radians(yaw)), 3)]
        e.update({
            "dig_yaw_deg": round(dig_yaw, 2), "dump_yaw_deg": round(dump_yaw, 2),
            "dig_target": pin(dig_yaw, dig_r, dig_h), "dump_target": pin(dump_yaw, float(d_ach[0]), float(d_ach[1])),
            "exc_keys_deg": keys, "exc_period_s": period,
            "exc_aim": {"bed_centre": [round(bx, 3), round(bed_top, 3), round(bz, 3)], "bed_dist_m": round(dump_R, 2),
                        "face_toe_m": round(toe, 2), "face_rise_m": round(rise, 2),
                        "max_reach_m": round(float(arm.B[0] + arm.reach), 2),
                        "dump_pose_deg": [round(hp, 2), round(d_boom, 2), round(d_stick, 2), keys[5][4]],
                        "warnings": warn},
        })
        log(f"  {e['id']} @ {e['face']}: heading {hd:.1f}  dig_yaw {dig_yaw:.1f} (house {hg:+.1f}, toe {toe:.1f} m, rise "
            f"{rise:.1f} m)  dump_yaw {dump_yaw:.1f} (house {hp:+.1f})  bed {dump_R:.1f} m / reach "
            f"{arm.B[0] + arm.reach:.1f} m  dump pose boom {d_boom:.1f} stick {d_stick:.1f} bucket {keys[5][4]:.1f}  "
            f"pin {d_ach[0]:.2f} m @ {d_ach[1] + ey - bed_top:.2f} m over bed top  period {period:.0f} s")
        for w in warn:
            log(f"    WARNING {e['id']}: {w}")
    return world


def main(argv):
    wdir = os.path.abspath(argv[1] if len(argv) > 1 else os.path.join(REPO, "data/worlds/quarry"))
    path = os.path.join(wdir, "world.json")
    world = json.load(open(path))
    arm = Arm()
    print(f"[exc-aim] arm: scale {arm.s:.4f}  slew axis {arm.slew_z:+.2f} m fwd  boom foot r {arm.B[0]:.2f} h {arm.B[1]:.2f}"
          f"  L1 {arm.L1:.2f}  L2 {arm.L2:.2f}  max reach {arm.B[0] + arm.reach:.2f} m from slew axis")
    aim(world, wdir, arm)
    with open(path, "w") as f:
        json.dump(world, f, indent=2)
    print(f"[exc-aim] wrote {path}")


if __name__ == "__main__":
    main(sys.argv)
