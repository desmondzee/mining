import math
from pathlib import Path

import numpy as np
import omni.replicator.core as rep
import omni.timeline
import omni.usd
from pxr import Gf, UsdGeom, UsdLux

from . import world as W
from .kinematics import yaw_matrix
from .pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS
from .sensors import CAM_H, CAM_W, HFOV, OVERVIEW_HEIGHT
from .usdbuild import TO_ISAAC, convert, isaac_matrix

APERTURE = 20.955
DOME_INTENSITY = 600.0
SUN_INTENSITY = 1200.0
SUN_ROTATION = (35.0, 0.0, 40.0)
PERSON_COLOUR = (1.0, 0.47, 0.08)
WARMUP = 30


def gf(m: np.ndarray) -> Gf.Matrix4d:
    return Gf.Matrix4d(np.asarray(m, float).T.tolist())


class IsaacRenderer:
    def __init__(self, app, sim, args) -> None:
        self.app, self.sim = app, sim
        cache = Path(args.cache)
        stage = self.stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.Xform.Define(stage, "/World")
        world = sim.world

        self.reference("/World/Scan", convert(world.asset("visual"), cache, "visual", world.scan_matrix))
        surround = world.asset("surround")
        if surround and surround.exists():
            self.reference("/World/Surround", convert(surround, cache, "surround", np.eye(4)))

        dome = UsdLux.DomeLight.Define(stage, "/World/Sky")
        pano = world.asset("pano")
        if pano and pano.exists():
            dome.CreateTextureFileAttr(str(pano))
            dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
        dome.CreateIntensityAttr(DOME_INTENSITY)
        UsdGeom.Xformable(dome).AddRotateXYZOp().Set(Gf.Vec3f(*args.dome_rotation))
        sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
        sun.CreateIntensityAttr(SUN_INTENSITY)
        sun.CreateAngleAttr(0.5)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(*SUN_ROTATION))

        ex_matrix, _ = W.model_box(W.REPO / W.EXCAVATOR_GLB, horizontal=W.EXCAVATOR_LENGTH)
        ex_usd = convert(W.REPO / W.EXCAVATOR_GLB, cache, "excavator", ex_matrix)
        for b in sim.excavators:
            xf = UsdGeom.Xform.Define(stage, f"/World/Equipment/{b.id.replace('-', '_')}")
            xf.AddTransformOp().Set(isaac_matrix(yaw_matrix(b.yaw), np.array([b.x, b.y, b.z])))
            self.reference(xf.GetPath().pathString + "/model", ex_usd)

        truck_usd = convert(W.REPO / W.TRUCK_GLB, cache, "truck", sim.truck_matrix)
        focal = APERTURE / 2 / math.tan(math.radians(HFOV / 2))
        self.trucks, cameras = [], []
        for t in sim.trucks:
            path = f"/World/Trucks/{t.id.replace('-', '_')}"
            xf = UsdGeom.Xform.Define(stage, path)
            self.trucks.append(xf.AddTransformOp())
            self.reference(path + "/body", truck_usd)
            cam = UsdGeom.Camera.Define(stage, path + "/front_camera")
            cam.CreateFocalLengthAttr(focal)
            cam.CreateHorizontalApertureAttr(APERTURE)
            cam.CreateVerticalApertureAttr(APERTURE * CAM_H / CAM_W)
            cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 5000.0))
            local = np.eye(4)
            local[:3, :3] = np.column_stack([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
            local[:3, 3] = TO_ISAAC[:3, :3] @ sim.rig.camera
            cam.AddTransformOp().Set(gf(local))
            cameras.append((t.id, cam.GetPath().pathString))

        xmin, zmin, xmax, zmax = world.bounds
        height = zmax - zmin
        width = height * CAM_W / CAM_H
        over = UsdGeom.Camera.Define(stage, "/World/Overview")
        over.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
        over.CreateHorizontalApertureAttr(width * 10)
        over.CreateVerticalApertureAttr(height * 10)
        over.CreateClippingRangeAttr(Gf.Vec2f(1.0, OVERVIEW_HEIGHT * 3))
        top = np.eye(4)
        top[:3, 3] = [(xmin + xmax) / 2, (zmin + zmax) / 2, OVERVIEW_HEIGHT]
        over.AddTransformOp().Set(gf(top))
        cameras.insert(0, ("overview", "/World/Overview"))

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
        for name, path in cameras:
            rp = rep.create.render_product(path, (CAM_W, CAM_H))
            ann = rep.AnnotatorRegistry.get_annotator("rgb")
            ann.attach(rp)
            self.annotators.append((name, ann))
        self.black = np.zeros((CAM_H, CAM_W, 3), np.uint8)
        self.update(sim)
        omni.timeline.get_timeline_interface().play()
        for _ in range(WARMUP):
            app.update()

    def reference(self, path: str, usd: Path) -> None:
        self.stage.DefinePrim(path, "Xform").GetReferences().AddReference(str(usd))

    def update(self, sim) -> None:
        for op, t in zip(self.trucks, sim.trucks):
            op.Set(isaac_matrix(sim.rotation(t), sim.position(t)))
        for op, w in zip(self.people, sim.walkers):
            op.Set(isaac_matrix(yaw_matrix(w.yaw), np.array([w.x, w.y, w.z])))

    def render(self) -> dict:
        self.app.update()
        out = {}
        for name, ann in self.annotators:
            data = np.asarray(ann.get_data())
            out[name] = data[..., :3] if data.ndim == 3 and data.size else self.black
        return out

