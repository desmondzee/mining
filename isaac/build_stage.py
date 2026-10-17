#!/usr/bin/env python3
"""Build an Isaac Sim stage from an envgen world. Pure pxr (usd-core) + trimesh: runs without Isaac Sim.

    isaac/.venv.nosync/bin/python isaac/build_stage.py quarry default [--no-visual] [--data-dir data/worlds]

Writes data/worlds/<id>/isaac/:
    world.usda                  the stage (open this in Isaac Sim, or use isaac/load_world.py)
    meshes/collider.usdc        static triangle-mesh collider (purpose=guide when a visual mesh exists)
    meshes/visual.usdc          textured scan (scan worlds), scan_transform baked in
    meshes/surround.usdc        vertex-coloured apron, rendered + static collider (ground-probe fallback)
    meshes/terrain.usdc         procedural worlds without collider.glb: rendered + collider
    models/komatsu_hd465.usdc   truck, +X forward, +Z up, origin bottom centre, 9.35 m, bed on a hinge Xform
    models/excavator.usdc       excavator, nested joint Xforms tracks/house/boom/stick/bucket
    textures/                   extracted glTF textures

Frame: stage upAxis = Z, metersPerUnit = 1, kilogramsPerUnit = 1. Contract (x, y, z) is baked into every point as
Isaac (x, -z, y) (see mine_frames.py). Why bake instead of keeping Y-up or rotating /World: Isaac Sim's defaults
(PhysX gravity, World/SimulationContext, cameras' "world" axes, RTX lidar frame, ROS bridges, isaacsim.core poses)
all assume Z-up. A rotated parent works for static props but makes every rigid body / sensor pose read back in a
rotated parent frame, which is a constant source of bugs for kinematic vehicles and the brain bridge.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, Vt

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import glb2usd  # noqa: E402
from mine_frames import C4, G4, contract_to_isaac, heading_to_yaw_deg, yaw_quat_wxyz  # noqa: E402

TRUCK_GLB_CANDIDATES = [os.path.join(REPO, "assets/models/komatsu_hd465/komatsu_hd465.glb"),
                        os.path.join(REPO, "unity/Assets/Models/Trucks/komatsu_hd465.glb")]
TRUCK_RIG = os.path.join(REPO, "assets/models/komatsu_hd465/rig.json")
TRUCK_LENGTH_M = 9.35            # spec.md: Komatsu length; scaled uniformly so the bbox length = this
EXC_GLB_RIGGED = os.path.join(REPO, "assets/models/excavator/excavator_rigged.glb")
EXC_GLB = os.path.join(REPO, "assets/models/excavator/excavator.glb")
EXC_RIG = os.path.join(REPO, "assets/models/excavator/rig.json")
EXC_LENGTH_M = 10.0              # envgen/preview/index.html EXC_LENGTH (scaled so max(bbox x, z) = this)
EXC_JOINTS = ("house", "boom", "stick", "bucket")   # parent chain: root(tracks) -> house -> boom -> stick -> bucket
EXC_JOINT_PARENT = {"house": None, "boom": "house", "stick": "boom", "bucket": "stick"}
EXC_JOINT_AXIS = {"house": "Z", "boom": "Y", "stick": "Y", "bucket": "Y"}  # Isaac model frame (glTF +Y -> Z, +X -> Y)
ROAD_LIFT_M = 0.15
ROAD_COLORS = {"ramp": (1.0, 0.55, 0.1), "haul": (1.0, 0.85, 0.2), "spur": (0.6, 0.8, 1.0)}


# ----------------------------------------------------------------------------------------------- helpers
def vec3(p) -> Gf.Vec3d:
    return Gf.Vec3d(*(float(x) for x in p))


def set_attr(prim: Usd.Prim, name: str, value, typ=None):
    if typ is None:
        if isinstance(value, bool):
            typ = Sdf.ValueTypeNames.Bool
        elif isinstance(value, (int, float)):
            typ = Sdf.ValueTypeNames.Double
        elif isinstance(value, str):
            typ = Sdf.ValueTypeNames.String
        else:
            typ = Sdf.ValueTypeNames.Double3
            value = vec3(value)
    prim.CreateAttribute(name, typ, custom=True).Set(value)


def place(prim: Usd.Prim, pos_isaac, yaw_deg: float | None = None, *, rotate_op: bool = False):
    """translate + orient (quat) or translate + rotateZ, in that order."""
    x = UsdGeom.Xformable(prim)
    x.ClearXformOpOrder()
    x.AddTranslateOp().Set(vec3(pos_isaac))
    if yaw_deg is not None:
        if rotate_op:
            x.AddRotateZOp().Set(float(yaw_deg))
        else:
            w, qx, qy, qz = yaw_quat_wxyz(yaw_deg)
            x.AddOrientOp().Set(Gf.Quatf(w, qx, qy, qz))


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def find_vec3(d, keys):
    """First 3-vector found under any of `keys` (searches nested dicts)."""
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v):
                return np.array(v, dtype=np.float64)
        for v in d.values():
            r = find_vec3(v, keys)
            if r is not None:
                return r
    return None


class Heightmap:
    """heightmap.raw sampler (contract frame) for points that lack a y."""

    def __init__(self, wdir: str):
        self.ok = False
        meta = read_json(os.path.join(wdir, "heightmap_meta.json"))
        raw = os.path.join(wdir, "heightmap.raw")
        if not meta or not os.path.exists(raw):
            return
        n = int(meta["resolution"])
        self.h = np.fromfile(raw, dtype="<u2").reshape(n, n).astype(np.float64)
        self.h = meta["min_height_m"] + self.h / 65535.0 * meta["height_range_m"]
        self.n, self.origin, self.size = n, np.array(meta["origin"], float), np.array(meta["size_m"], float)
        self.ok = True

    def y(self, x: float, z: float) -> float:
        if not self.ok:
            return 0.0
        u = (np.array([x, z]) - self.origin) / self.size * (self.n - 1)   # col (x), row (z); row 0 = south
        c, r = np.clip(u, 0, self.n - 1.001)
        c0, r0 = int(c), int(r)
        fc, fr = c - c0, r - r0
        h = self.h
        return float((h[r0, c0] * (1 - fc) + h[r0, c0 + 1] * fc) * (1 - fr)
                     + (h[r0 + 1, c0] * (1 - fc) + h[r0 + 1, c0 + 1] * fc) * fr)


# ----------------------------------------------------------------------------------------------- models
def _normalize_model(sc: glb2usd.GlbScene, length_m: float, length_axes=(0,)) -> tuple[float, np.ndarray]:
    """Uniform scale so max bbox extent over `length_axes` = length_m; origin = bottom centre. In place."""
    b = sc.bounds()
    size = b[1] - b[0]
    s = length_m / max(size[i] for i in length_axes)
    c = np.array([(b[0, 0] + b[1, 0]) / 2, (b[0, 1] + b[1, 1]) / 2, b[0, 2]])
    for p in sc.parts:
        p.points = (p.points - c) * s
    for k, m in sc.node_world.items():
        m = m.copy()
        m[:3, 3] = (m[:3, 3] - c) * s
        sc.node_world[k] = m
    return s, c


def convert_truck(out_usd: str, tex_dir: str) -> dict:
    """Komatsu HD465 -> USD. Bed meshes (material 'DumpBed') go under /komatsu_hd465/BedHinge (xformOp:rotateY:tip;
    tip angle = -rotateY, i.e. tip_bed(deg) writes rotateY:tip = -deg so the front of the bed rises)."""
    truck_glb = next((p for p in TRUCK_GLB_CANDIDATES if os.path.exists(p)), None)
    if truck_glb is None:
        raise SystemExit(f"Komatsu GLB not found in {TRUCK_GLB_CANDIDATES}")
    sc = glb2usd.load_glb(truck_glb, G4)   # glTF (Y up, +Z fwd) -> +X fwd, +Z up
    s, c = _normalize_model(sc, TRUCK_LENGTH_M, (0,))
    rig = read_json(TRUCK_RIG) or {}
    bed_cfg = rig.get("dump_bed", {}) if isinstance(rig, dict) else {}
    bed_mat = bed_cfg.get("material", "DumpBed").lower()
    bed = [p for p in sc.parts if p.material_name.lower() == bed_mat or "bed" in (p.node + p.geom).lower()]
    chassis = [p for p in sc.parts if p not in bed]
    pivot_src = "bbox"
    pivot = None
    max_tip = float(bed_cfg.get("tip_deg", 50.0))
    # rig.json (glTF scene frame of the GLB, +Y up, +Z forward): top-level hinge_m (chassis hinge pins) wins over
    # dump_bed.hinge_m (older bed tail-lip value).
    hinge = rig.get("hinge_m") if isinstance(rig, dict) else None
    if not (isinstance(hinge, list) and len(hinge) == 3):
        hinge = bed_cfg.get("hinge_m")
    if isinstance(hinge, list) and len(hinge) == 3:
        pivot = (G4[:3, :3] @ np.asarray(hinge, float) - c) * s
        pivot_src = f"rig.json hinge_m {hinge} (glTF)"
    if pivot is None and bed:
        bb = sc.bounds(bed)
        pivot = np.array([bb[0, 0], 0.0, bb[0, 2]])   # rear end (-forward), lateral centre, bottom of the bed
    if pivot is None:
        pivot = np.zeros(3)
    stage, root = glb2usd.new_layer_stage(out_usd, "komatsu_hd465")
    w = glb2usd.UsdMeshWriter(stage, os.path.dirname(os.path.abspath(out_usd)), tex_dir, "/komatsu_hd465/Looks")
    UsdGeom.Xform.Define(stage, "/komatsu_hd465/Chassis")
    for p in chassis:
        w.add_mesh("/komatsu_hd465/Chassis", p.material_name or p.node, p)
    hinge = UsdGeom.Xform.Define(stage, "/komatsu_hd465/BedHinge")
    hinge.AddTranslateOp().Set(vec3(pivot))
    hinge.AddRotateYOp(opSuffix="tip").Set(0.0)
    UsdGeom.Xform.Define(stage, "/komatsu_hd465/BedHinge/Bed")
    for p in bed:
        w.add_mesh("/komatsu_hd465/BedHinge/Bed", p.material_name or p.node, p, offset=pivot)
    b = sc.bounds()
    for k, v in (("mine:length_m", float(b[1, 0] - b[0, 0])), ("mine:width_m", float(b[1, 1] - b[0, 1])),
                 ("mine:height_m", float(b[1, 2] - b[0, 2])), ("mine:bedMaxTipDeg", max_tip), ("mine:bedRaiseS", float(bed_cfg.get("raise_s", 3.0))),
                 ("mine:bedLowerS", float(bed_cfg.get("lower_s", 2.5))),
                 ("mine:modelScale", float(s))):
        set_attr(root, k, v)
    set_attr(root, "mine:bedPivot", pivot)
    set_attr(root, "mine:bedPivotSource", pivot_src)
    set_attr(root, "mine:frame", "+X forward, +Y left, +Z up, origin bottom centre, metres")
    UsdGeom.ModelAPI(root).SetExtentsHint(Vt.Vec3fArray([Gf.Vec3f(*map(float, b[0])), Gf.Vec3f(*map(float, b[1]))]))
    set_attr(root, "mine:sourceGlb", os.path.relpath(truck_glb, REPO))
    stage.GetRootLayer().Save()
    return {"usd": out_usd, "bed_meshes": len(bed), "chassis_meshes": len(chassis), "pivot": pivot.round(3).tolist(),
            "pivot_source": pivot_src, "size": (b[1] - b[0]).round(2).tolist()}


def convert_excavator(out_usd: str, tex_dir: str) -> dict:
    """Excavator -> USD with nested joint Xforms (each joint origin at its pin, from the rigged GLB node origins or
    rig.json `pins`). Drive with xformOp:rotateZ:joint (house, + = toward cab/+Y side) and xformOp:rotateY:joint
    (boom/stick/bucket, + = boom down / stick in / bucket curl), the same signs as envgen/preview EXC_KEYS."""
    glb = EXC_GLB_RIGGED if os.path.exists(EXC_GLB_RIGGED) else EXC_GLB
    sc = glb2usd.load_glb(glb, G4)
    # preview scales so max(bbox x, z) in glTF = 10 m; glTF x,z = Isaac y,x
    s, c = _normalize_model(sc, EXC_LENGTH_M, (0, 1))
    rigged = all(j in sc.node_world for j in EXC_JOINTS)
    pins = {}
    pin_src = "none"
    if rigged:
        pins = {j: sc.node_origin(j) for j in EXC_JOINTS}
        pin_src = "glb node origins"
        rig = read_json(EXC_RIG)
        if isinstance(rig, dict) and isinstance(rig.get("pins"), dict):
            pass
        if isinstance(rig, dict) and isinstance(rig.get("joints"), dict):   # rig.json joints.<j>.pivot_m (absolute)
            for j in EXC_JOINTS:
                v = (rig["joints"].get(j) or {}).get("pivot_m")
                if isinstance(v, (list, tuple)) and len(v) == 3:
                    pins[j] = (G4[:3, :3] @ np.asarray(v, float) - c) * s
                    pin_src = "rig.json joints.pivot_m (glTF space)"
        if isinstance(rig, dict) and isinstance(rig.get("pins"), dict):
            for j in EXC_JOINTS:
                v = rig["pins"].get(j)
                if isinstance(v, dict):
                    v = v.get("pos") or v.get("pivot")
                if isinstance(v, (list, tuple)) and len(v) == 3:
                    pins[j] = (G4[:3, :3] @ np.asarray(v, float) - c) * s   # rig.json pins in glTF model space
                    pin_src = "rig.json pins (glTF space)"
    stage, root = glb2usd.new_layer_stage(out_usd, "excavator")
    w = glb2usd.UsdMeshWriter(stage, os.path.dirname(os.path.abspath(out_usd)), tex_dir, "/excavator/Looks")
    paths = {None: "/excavator"}
    if rigged:
        for j in EXC_JOINTS:
            par = EXC_JOINT_PARENT[j]
            path = f"{paths[par]}/{j}"
            x = UsdGeom.Xform.Define(stage, path)
            rel = pins[j] - (pins[par] if par else np.zeros(3))
            x.AddTranslateOp().Set(vec3(rel))
            (x.AddRotateZOp if EXC_JOINT_AXIS[j] == "Z" else x.AddRotateYOp)(opSuffix="joint").Set(0.0)
            set_attr(x.GetPrim(), "mine:pin", pins[j])
            paths[j] = path
        UsdGeom.Xform.Define(stage, "/excavator/tracks")
    else:
        UsdGeom.Xform.Define(stage, "/excavator/Body")
    counts: dict[str, int] = {}
    for p in sc.parts:
        joint = next((n for n in [p.node] + p.ancestors if n in EXC_JOINTS), None) if rigged else None
        if joint:
            parent, off = paths[joint] + "/geom", pins[joint]
        else:
            parent, off = ("/excavator/tracks" if rigged else "/excavator/Body"), np.zeros(3)
        if not stage.GetPrimAtPath(parent):
            UsdGeom.Xform.Define(stage, parent)
        w.add_mesh(parent, p.geom, p, offset=off)
        counts[joint or "tracks"] = counts.get(joint or "tracks", 0) + 1
    b = sc.bounds()
    set_attr(root, "mine:rigged", bool(rigged))
    set_attr(root, "mine:pinSource", pin_src)
    set_attr(root, "mine:modelScale", float(s))
    set_attr(root, "mine:frame", "+X forward (boom), +Y left (cab side), +Z up, origin bottom centre, metres")
    UsdGeom.ModelAPI(root).SetExtentsHint(Vt.Vec3fArray([Gf.Vec3f(*map(float, b[0])), Gf.Vec3f(*map(float, b[1]))]))
    stage.GetRootLayer().Save()
    return {"usd": out_usd, "source": os.path.relpath(glb, REPO), "rigged": rigged, "pin_source": pin_src,
            "meshes_per_joint": counts, "pins": {k: v.round(3).tolist() for k, v in pins.items()},
            "size": (b[1] - b[0]).round(2).tolist()}


# ----------------------------------------------------------------------------------------------- world
def build_world(world_id: str, data_dir: str, with_visual: bool = True, rebuild_meshes: bool = True) -> str:
    wdir = os.path.join(data_dir, world_id)
    world = read_json(os.path.join(wdir, "world.json"))
    if world is None:
        raise SystemExit(f"no world.json in {wdir}")
    out = os.path.join(wdir, "isaac")
    mdir, gdir, tdir = (os.path.join(out, d) for d in ("meshes", "models", "textures"))
    for d in (out, mdir, gdir, tdir):
        os.makedirs(d, exist_ok=True)
    assets = world.get("assets", {}) or {}
    hm = Heightmap(wdir)
    report: dict = {"world": world_id}

    def asset(key):
        n = assets.get(key)
        p = os.path.join(wdir, n) if n else None
        return p if p and os.path.exists(p) else None

    collider, visual, surround, terrain = asset("collider"), asset("visual"), asset("surround"), asset("terrain_mesh")
    pano = asset("pano")

    def conv(name, glb, M4, **kw):
        usd = os.path.join(mdir, f"{name}.usdc")
        if rebuild_meshes or not os.path.exists(usd):
            t = time.time()
            r = glb2usd.convert_static(glb, usd, name.capitalize(), M4, tex_dir=tdir, **kw)
            r["seconds"] = round(time.time() - t, 1)
            report[name] = r
            print(f"  {name}: {r['meshes']} meshes, {r['tris']} tris, {r['seconds']} s", flush=True)
        return os.path.relpath(usd, out)

    layers = {}
    if collider:
        layers["collider"] = conv("collider", collider, C4, collision=True, materials=False, normals=False)
    if visual and with_visual:
        st = world.get("scan_transform") or {}
        S = np.array(st.get("matrix") or np.eye(4), dtype=np.float64)
        layers["visual"] = conv("visual", visual, C4 @ S, collision=False, double_sided=True)
    if terrain and not collider:
        layers["terrain"] = conv("terrain", terrain, C4, collision=True)
    if surround:
        layers["surround"] = conv("surround", surround, C4, collision=True, materials=False)

    if rebuild_meshes or not os.path.exists(os.path.join(gdir, "komatsu_hd465.usdc")):
        report["truck"] = convert_truck(os.path.join(gdir, "komatsu_hd465.usdc"), tdir)
        report["excavator"] = convert_excavator(os.path.join(gdir, "excavator.usdc"), tdir)
        print(f"  truck: {report['truck']}\n  excavator: {report['excavator']}", flush=True)

    # ---- stage
    path = os.path.join(out, "world.usda")
    if os.path.exists(path):
        os.remove(path)
    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(0)
    wp = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(wp)
    wp.SetCustomDataByKey("mine:worldId", world_id)
    wp.SetCustomDataByKey("mine:worldJson", "../world.json")
    wp.SetCustomDataByKey("mine:frame", "Isaac (X, Y, Z) = contract (x, -z, y); yaw = heading_deg - 90 (CCW about +Z)")
    stage.GetRootLayer().customLayerData = {"mine:generator": "isaac/build_stage.py", "mine:worldId": world_id}

    ps = UsdPhysics.Scene.Define(stage, "/physicsScene")
    ps.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    ps.CreateGravityMagnitudeAttr(9.81)

    # terrain
    UsdGeom.Scope.Define(stage, "/World/Terrain")
    for name, rel in layers.items():
        x = UsdGeom.Xform.Define(stage, f"/World/Terrain/{name.capitalize()}")
        x.GetPrim().GetReferences().AddReference(rel)
        if name == "collider" and "visual" in layers:
            x.CreatePurposeAttr(UsdGeom.Tokens.guide)   # PhysX still collides; RTX (camera/lidar) ignores it
        set_attr(x.GetPrim(), "mine:role", {"collider": "ground collider", "visual": "rendered scan",
                                            "terrain": "rendered ground + collider",
                                            "surround": "apron scenery + fallback collider"}[name])
    if "visual" not in layers and "collider" in layers and "terrain" not in layers:
        print("  note: no visual mesh; collider is rendered (purpose default)")

    # lights
    UsdGeom.Scope.Define(stage, "/World/Lights")
    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/Sky")
    dome.CreateIntensityAttr(1000.0)
    if pano:
        dome.CreateTextureFileAttr(os.path.relpath(pano, out))
        dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Sun")
    sun.CreateIntensityAttr(3000.0)
    sun.CreateAngleAttr(0.53)
    sx = UsdGeom.Xformable(sun.GetPrim())
    sx.AddRotateZOp().Set(35.0)     # azimuth
    sx.AddRotateXOp().Set(40.0)     # 50 deg elevation (light shines down its local -Z)

    # sites + operating points
    UsdGeom.Scope.Define(stage, "/World/Sites")
    n_points = 0
    for s in world.get("sites", []):
        p3 = s.get("pos3") or [s["pos"][0], hm.y(*s["pos"]), s["pos"][1]]
        sp = UsdGeom.Xform.Define(stage, f"/World/Sites/{glb2usd.sanitize(s['id'])}").GetPrim()
        P = contract_to_isaac(p3)
        place(sp, P)
        set_attr(sp, "mine:id", s["id"])
        set_attr(sp, "mine:type", s.get("type", ""))
        set_attr(sp, "mine:name", s.get("name", s["id"]))
        if "tonnes" in s:
            set_attr(sp, "mine:tonnes", float(s["tonnes"]))
        set_attr(sp, "mine:contractPos3", p3)
        pts = []
        for key in ("load_point", "dump_point"):
            if isinstance(s.get(key), dict):
                pts.append((key, s[key]))
        for i, slot in enumerate(s.get("slots", []) or []):
            pts.append((f"slot_{i}", slot))
        for key, pose in pts:
            q = UsdGeom.Xform.Define(stage, sp.GetPath().AppendChild(key)).GetPrim()
            Q = contract_to_isaac(pose["pos3"])
            place(q, Q - P, heading_to_yaw_deg(pose.get("heading_deg", 0.0)))
            set_attr(q, "mine:heading_deg", float(pose.get("heading_deg", 0.0)))
            set_attr(q, "mine:contractPos3", pose["pos3"])
            n_points += 1

    UsdGeom.Scope.Define(stage, "/World/Junctions")
    for j in world.get("junctions", []):
        jp = UsdGeom.Xform.Define(stage, f"/World/Junctions/{glb2usd.sanitize(j['id'])}").GetPrim()
        place(jp, contract_to_isaac(j["pos"]))
        set_attr(jp, "mine:id", j["id"])
        set_attr(jp, "mine:name", j.get("name", j["id"]))
        set_attr(jp, "mine:contractPos3", j["pos"])

    # roads (guide purpose: visible in the viewport with guides on, invisible to RTX camera/lidar)
    UsdGeom.Scope.Define(stage, "/World/Roads")
    for r in world.get("roads", []):
        pts = contract_to_isaac(np.array(r["points"], dtype=np.float64))
        pts[:, 2] += ROAD_LIFT_M
        c = UsdGeom.BasisCurves.Define(stage, f"/World/Roads/{glb2usd.sanitize(r['id'])}")
        c.CreateTypeAttr(UsdGeom.Tokens.linear)
        c.CreateCurveVertexCountsAttr(Vt.IntArray([len(pts)]))
        c.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts.astype(np.float32)))
        c.CreateWidthsAttr(Vt.FloatArray([float(r.get("width_m", 15.0))]))
        c.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        c.CreatePurposeAttr(UsdGeom.Tokens.guide)
        c.CreateDisplayColorAttr([Gf.Vec3f(*ROAD_COLORS.get(r.get("type"), (1, 1, 1)))])
        c.CreateExtentAttr(UsdGeom.Curves.ComputeExtent(c.GetPointsAttr().Get(), c.GetWidthsAttr().Get()))
        pr = c.GetPrim()
        for k in ("id", "name", "type", "from", "to"):
            if k in r:
                set_attr(pr, f"mine:{k}", str(r[k]))
        set_attr(pr, "mine:width_m", float(r.get("width_m", 15.0)))
        set_attr(pr, "mine:grade_pct", float(r.get("grade_pct", 0.0)))
        set_attr(pr, "mine:one_way", bool(r.get("one_way", False)))

    # excavators
    UsdGeom.Scope.Define(stage, "/World/Equipment")
    for e in world.get("equipment", []):
        if e.get("type", "excavator") != "excavator":
            continue
        ep = UsdGeom.Xform.Define(stage, f"/World/Equipment/{glb2usd.sanitize(e['id'])}").GetPrim()
        ep.GetReferences().AddReference("models/excavator.usdc")
        place(ep, contract_to_isaac(e["pos3"]), heading_to_yaw_deg(e.get("heading_deg", 0.0)), rotate_op=True)
        for k in ("id", "name", "face", "model"):
            if k in e:
                set_attr(ep, f"mine:{k}", str(e[k]))
        set_attr(ep, "mine:heading_deg", float(e.get("heading_deg", 0.0)))
        set_attr(ep, "mine:state", "holding")

    # truck spawns (load_world.py spawns the trucks themselves under /World/Trucks)
    UsdGeom.Scope.Define(stage, "/World/Spawns")
    slots = next((s.get("slots") for s in world.get("sites", []) if s.get("type") == "parking" and s.get("slots")), [])
    for i, t in enumerate(world.get("trucks", [])):
        sx_, sz_ = t["spawn"]
        slot = t.get("slot", i)
        if slots and slot is not None and slot < len(slots) and \
                math.hypot(slots[slot]["pos3"][0] - sx_, slots[slot]["pos3"][2] - sz_) < 0.5:
            p3 = slots[slot]["pos3"]
        else:
            p3 = [sx_, hm.y(sx_, sz_), sz_]
        tp = UsdGeom.Xform.Define(stage, f"/World/Spawns/{glb2usd.sanitize(t['id'])}").GetPrim()
        place(tp, contract_to_isaac(p3), heading_to_yaw_deg(t.get("heading_deg", 0.0)))
        for k in ("id", "callsign", "model"):
            if k in t:
                set_attr(tp, f"mine:{k}", str(t[k]))
        set_attr(tp, "mine:heading_deg", float(t.get("heading_deg", 0.0)))
        set_attr(tp, "mine:contractPos3", p3)
    UsdGeom.Scope.Define(stage, "/World/Trucks")

    stage.GetRootLayer().Save()
    with open(os.path.join(out, "build_report.json"), "w") as f:
        json.dump(report, f, indent=1, default=str)
    return path


# ----------------------------------------------------------------------------------------------- validate
def validate(path: str) -> None:
    stage = Usd.Stage.Open(path)
    assert stage, path
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z and UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    counts: dict[str, int] = {}
    tris = colliders = 0
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        t = str(prim.GetTypeName()) or "(none)"
        counts[t] = counts.get(t, 0) + 1
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            colliders += 1
        if t == "Mesh":
            tris += len(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or [])
    print(f"  reopened {os.path.relpath(path, REPO)}: upAxis={UsdGeom.GetStageUpAxis(stage)} "
          f"mpu={UsdGeom.GetStageMetersPerUnit(stage)} defaultPrim={stage.GetDefaultPrim().GetPath()}")
    print("  prim types:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"  mesh triangles={tris:,} colliders(CollisionAPI)={colliders}")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                              [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.guide])
    for child in ["/World/Terrain/" + p.GetName() for p in stage.GetPrimAtPath("/World/Terrain").GetChildren()] + \
            ["/World/Sites", "/World/Roads", "/World/Equipment", "/World/Spawns"]:
        pr = stage.GetPrimAtPath(child)
        if not pr:
            continue
        r = cache.ComputeWorldBound(pr).ComputeAlignedRange()
        if r.IsEmpty():
            print(f"  {child}: {len(pr.GetChildren())} Xforms (no geometry)")
        else:
            print(f"  bounds {child}: min={tuple(round(v, 1) for v in r.GetMin())} max={tuple(round(v, 1) for v in r.GetMax())}")
    for pr in stage.GetPrimAtPath("/World/Spawns").GetChildren():
        m = UsdGeom.Xformable(pr).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        fwd = m.TransformDir(Gf.Vec3d(1, 0, 0))
        print(f"  spawn {pr.GetName()}: pos={tuple(round(v, 2) for v in m.ExtractTranslation())} "
              f"heading={pr.GetAttribute('mine:heading_deg').Get():.1f} fwd_isaac={tuple(round(v, 3) for v in fwd)}")
    for pr in stage.GetPrimAtPath("/World/Equipment").GetChildren():
        b = stage.GetPrimAtPath(pr.GetPath().AppendPath("house/boom/stick/bucket"))
        print(f"  {pr.GetName()}: bucket joint prim {'found' if b else 'MISSING'}")
    unresolved = [str(p.GetPath()) for p in stage.Traverse() if p.HasAuthoredReferences() and not p.GetChildren()]
    if unresolved:
        print("  WARNING unresolved references:", unresolved)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worlds", nargs="*", default=["quarry", "default"])
    ap.add_argument("--data-dir", default=os.path.join(REPO, "data/worlds"))
    ap.add_argument("--no-visual", action="store_true", help="skip the (large) textured scan conversion")
    ap.add_argument("--keep-meshes", action="store_true", help="reuse existing meshes/*.usdc and models/*.usdc")
    a = ap.parse_args(argv)
    for wid in a.worlds:
        t = time.time()
        print(f"[{wid}] building", flush=True)
        path = build_world(wid, a.data_dir, with_visual=not a.no_visual, rebuild_meshes=not a.keep_meshes)
        print(f"[{wid}] wrote {os.path.relpath(path, REPO)} in {time.time() - t:.1f} s")
        validate(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
