import importlib.util
import json
import math
from pathlib import Path

import carb
import numpy as np
import omni.replicator.core as rep
import omni.timeline
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

from . import world as W
from .kinematics import yaw_matrix
from .pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS
from .sensors import CAM_H, CAM_W, HFOV, OVERVIEW_HEIGHT
from .usdbuild import TO_ISAAC, camera_matrix, convert, isaac_matrix, sun_matrix

APERTURE = 20.955
PERSON_COLOUR = (1.0, 0.47, 0.08)
WARMUP = 30
PROPS_LOADER = "isaac/props/load.py"
PROPS_JSON = "props.json"


def gf(m: np.ndarray) -> Gf.Matrix4d:
    return Gf.Matrix4d(np.asarray(m, float).T.tolist())


def pinhole(stage, path: str, hfov: float, w: int, h: int) -> UsdGeom.Camera:
    cam = UsdGeom.Camera.Define(stage, path)
    cam.CreateFocalLengthAttr(APERTURE / 2 / math.tan(math.radians(hfov / 2)))
    cam.CreateHorizontalApertureAttr(APERTURE)
    cam.CreateVerticalApertureAttr(APERTURE * h / w)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 8000.0))
    return cam


def stage_triangles(stage, root: str) -> tuple[np.ndarray, np.ndarray] | None:
    cache = UsdGeom.XformCache()
    pts, tris, base = [], [], 0
    prim = stage.GetPrimAtPath(root)
    if not prim:
        return None
    for p in Usd.PrimRange(prim):
        if not p.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(p)
        v = np.asarray(mesh.GetPointsAttr().Get(), np.float64)
        counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
        idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
        if not len(v) or not len(counts):
            continue
        xf = np.asarray(cache.GetLocalToWorldTransform(p), np.float64)
        v = v @ xf[:3, :3] + xf[3, :3]
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        fan = [(s, s + k, s + k + 1) for s, c in zip(starts, counts) for k in range(1, c - 1)]
        if not fan:
            continue
        pts.append(v[:, [0, 2, 1]])
        tris.append(idx[np.array(fan)][:, ::-1] + base)
        base += len(v)
    if not pts:
        return None
    return np.concatenate(pts), np.concatenate(tris)


class IsaacRenderer:
    def __init__(self, app, sim, args, config: dict) -> None:
        self.app, self.sim, self.config = app, sim, config
        settings = carb.settings.get_settings()
        for key, value in config.get("rtx", {}).items():
            settings.set(key, value)
        cache = Path(args.cache)
        stage = self.stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.Xform.Define(stage, "/World")
        world = sim.world

        self.reference("/World/Scan", convert(world.asset("visual"), cache, "visual", world.scan_matrix))
        self.tune_scan(config.get("scan", {}))
        surround = world.asset("surround")
        if surround and surround.exists():
            self.reference("/World/Surround", convert(surround, cache, "surround", np.eye(4), config.get("surround", {}).get("tint")))
        self.lights(world, config)

        ex_matrix, _ = W.model_box(W.REPO / W.EXCAVATOR_GLB, horizontal=W.EXCAVATOR_LENGTH)
        ex_usd = convert(W.REPO / W.EXCAVATOR_GLB, cache, "excavator", ex_matrix)
        for b in sim.excavators:
            xf = UsdGeom.Xform.Define(stage, f"/World/Equipment/{b.id.replace('-', '_')}")
            xf.AddTransformOp().Set(isaac_matrix(yaw_matrix(b.yaw), np.array([b.x, b.y, b.z])))
            self.reference(xf.GetPath().pathString + "/model", ex_usd)

        self.add_props(world, sim)

        truck_usd = convert(W.REPO / W.TRUCK_GLB, cache, "truck", sim.truck_matrix)
        self.trucks, cameras = [], []
        for t in sim.trucks:
            path = f"/World/Trucks/{t.id.replace('-', '_')}"
            xf = UsdGeom.Xform.Define(stage, path)
            self.trucks.append(xf.AddTransformOp())
            self.reference(path + "/body", truck_usd)
            cam = pinhole(stage, path + "/front_camera", HFOV, CAM_W, CAM_H)
            local = np.eye(4)
            local[:3, :3] = np.column_stack([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
            local[:3, 3] = TO_ISAAC[:3, :3] @ sim.rig.camera
            cam.AddTransformOp().Set(gf(local))
            cameras.append((t.id, cam.GetPath().pathString, CAM_W, CAM_H))
        self.fx = self.make_fx(sim, args.fx)

        xmin, zmin, xmax, zmax = world.bounds
        height = zmax - zmin
        over = UsdGeom.Camera.Define(stage, "/World/Overview")
        over.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
        over.CreateHorizontalApertureAttr(height * CAM_W / CAM_H * 10)
        over.CreateVerticalApertureAttr(height * 10)
        over.CreateClippingRangeAttr(Gf.Vec2f(1.0, OVERVIEW_HEIGHT * 3))
        top = np.eye(4)
        top[:3, 3] = [(xmin + xmax) / 2, (zmin + zmax) / 2, OVERVIEW_HEIGHT]
        over.AddTransformOp().Set(gf(top))
        cameras.insert(0, ("overview", "/World/Overview", CAM_W, CAM_H))

        self.views = {}
        for v in sim.views:
            cam = pinhole(stage, f"/World/Views/{v.name}", v.hfov, v.width, v.height)
            self.views[v.name] = cam.AddTransformOp()
            cameras.append((v.name, cam.GetPath().pathString, v.width, v.height))

        self.people = []
        for w in sim.walkers:
            path = f"/World/People/{w.id}"
            xf = UsdGeom.Xform.Define(stage, path)
            self.people.append(xf.AddTransformOp())
            body = UsdGeom.Capsule.Define(stage, path + "/body")
            body.CreateAxisAttr(UsdGeom.Tokens.z)
            body.CreateRadiusAttr(PEDESTRIAN_RADIUS)
            body.CreateHeightAttr(PEDESTRIAN_HEIGHT - 2 * PEDESTRIAN_RADIUS)
            body.CreateDisplayColorAttr([Gf.Vec3f(*PERSON_COLOUR)])
            body.AddTranslateOp().Set(Gf.Vec3d(0, 0, PEDESTRIAN_HEIGHT / 2))

        self.annotators = []
        for name, path, w, h in cameras:
            rp = rep.create.render_product(path, (w, h))
            ann = rep.AnnotatorRegistry.get_annotator("rgb")
            ann.attach(rp)
            self.annotators.append((name, ann, np.zeros((h, w, 3), np.uint8)))
        self.update(sim, 0.0)
        omni.timeline.get_timeline_interface().play()
        for _ in range(WARMUP):
            app.update()

    def reference(self, path: str, usd: Path) -> None:
        self.stage.DefinePrim(path, "Xform").GetReferences().AddReference(str(usd))

    def tune_scan(self, scan: dict) -> None:
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Scan")):
            if prim.GetTypeName() != "Shader":
                continue
            shader_id = prim.GetAttribute("info:id").Get()
            if shader_id == "UsdUVTexture" and "tint" in scan:
                prim.CreateAttribute("inputs:scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scan["tint"], 1.0))
            if shader_id == "UsdPreviewSurface":
                if "roughness" in scan:
                    prim.CreateAttribute("inputs:roughness", Sdf.ValueTypeNames.Float).Set(float(scan["roughness"]))
                if "specular" in scan:
                    prim.CreateAttribute("inputs:useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
                    s = float(scan["specular"])
                    prim.CreateAttribute("inputs:specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(s, s, s))

    def lights(self, world, config: dict) -> None:
        dome_cfg, sun_cfg = config.get("dome", {}), config.get("sun", {})
        dome = UsdLux.DomeLight.Define(self.stage, "/World/Sky")
        pano = world.asset("pano")
        if dome_cfg.get("texture", True) and pano and pano.exists():
            dome.CreateTextureFileAttr(str(pano))
            dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
        dome.CreateIntensityAttr(float(dome_cfg.get("intensity", 700.0)))
        UsdGeom.Xformable(dome).AddRotateXYZOp().Set(Gf.Vec3f(*dome_cfg.get("rotation", [0.0, 0.0, 0.0])))
        sun = UsdLux.DistantLight.Define(self.stage, "/World/Sun")
        sun.CreateIntensityAttr(float(sun_cfg.get("intensity", 3000.0)))
        sun.CreateAngleAttr(float(sun_cfg.get("angle", 0.53)))
        sun.CreateColorAttr(Gf.Vec3f(*sun_cfg.get("color", [1.0, 1.0, 1.0])))
        UsdGeom.Xformable(sun).AddTransformOp().Set(sun_matrix(float(sun_cfg.get("elevation", 40.0)), float(sun_cfg.get("azimuth", 220.0))))

    def add_props(self, world, sim) -> None:
        loader, props = W.REPO / PROPS_LOADER, world.dir / PROPS_JSON
        if not (loader.exists() and props.exists()):
            return
        spec = importlib.util.spec_from_file_location("mining_props_load", loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.add_props(self.stage, json.loads(props.read_text()), W.REPO / "assets")
        tris = stage_triangles(self.stage, "/World/Props")
        if tris is not None and sim.caster is not None:
            sim.caster.set_props(*tris)
        print(f"props: {props} loaded, {0 if tris is None else len(tris[1])} triangles for raycasts", flush=True)

    def make_fx(self, sim, preset: str):
        if preset == "off":
            return None
        try:
            from isaac.fx import TruckFx
        except ImportError:
            return None
        trucks = [{"id": t.id, "prim": f"/World/Trucks/{t.id.replace('-', '_')}", "size": list(sim.truck_size)} for t in sim.trucks]
        print(f"fx: isaac.fx TruckFx preset {preset}", flush=True)
        return TruckFx(self.stage, {"preset": preset, "trucks": trucks})

    def update(self, sim, dt: float) -> None:
        if self.fx is not None:
            self.fx.update(dt, sim.fx_state())
        for op, t in zip(self.trucks, sim.trucks):
            op.Set(isaac_matrix(sim.rotation(t), sim.position(t)))
        for op, w in zip(self.people, sim.walkers):
            op.Set(isaac_matrix(yaw_matrix(w.yaw), np.array([w.x, w.y, w.z])))
        self.view_poses = sim.view_poses(dt)
        for name, (p, r) in self.view_poses.items():
            self.views[name].Set(camera_matrix(r, p))

    def render(self) -> dict:
        self.app.update()
        out = {}
        for name, ann, black in self.annotators:
            data = np.asarray(ann.get_data())
            out[name] = data[..., :3].copy() if data.ndim == 3 and data.size else black
        return out
