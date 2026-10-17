"""Isaac Sim 5.x loader: world.usda (from build_stage.py) + 3 Komatsu trucks + per-truck RTX camera and RTX lidar.

Standalone (on watson, from the Isaac Sim install dir; DGX Spark/aarch64 has no livestream, so headless):
    ./python.sh /path/to/repo/isaac/load_world.py --world quarry --headless [--radio http://127.0.0.1:8090]
        [--seconds 30] [--save-frames /tmp/isaac_frames] [--demo-drive]
pip install (isaacsim[all] wheels):  python isaac/load_world.py ...  (same flags)
Script Editor (GUI): exec(open('/path/to/repo/isaac/load_world.py').read()); then
    main_in_editor(world="quarry")       # builds the scene in the open app, no SimulationApp

Modular entry points (all take the open stage):
    build_world(world_id)                      open/reference world.usda (runs build_stage if missing)
    spawn_trucks(stage, n=3)                   kinematic trucks at /World/Spawns/*, TruckController each
    add_sensors(stage, trucks)                 RTX camera 640x360 90 deg hFOV + RTX lidar 120x30 deg 240x30 10 Hz 100 m
    read_sensors(sensors)                      dict per truck: rgb, depth (64x36), lidar points (brain frame)

Targets Isaac Sim 5.0/5.1 module names (isaacsim.core.api, isaacsim.sensors.camera, isaacsim.sensors.rtx command
IsaacSensorCreateRtxLidar with OmniLidar prims, replicator annotator IsaacCreateRTXLidarScanBuffer). 6.x moved the
lidar class to isaacsim.sensors.experimental.rtx; the command path used here is the least version-sensitive.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from mine_frames import (G3, heading_to_yaw_deg, isaac_pose_to_brain, isaac_sensor_points_to_unity,  # noqa: E402
                         quat_wxyz_to_yaw_deg, yaw_quat_wxyz)

# ---- spec.md values (Unity TruckAgent / SensorCamera / LidarSensor / QuarrySceneBuilder)
TRUCK_COUNT = 3
MAX_SPEED, MAX_STEER_DEG, WHEELBASE, ACCEL, CMD_TIMEOUT = 8.0, 30.0, 5.0, 1.5, 0.5
CAM_W, CAM_H, CAM_FPS, CAM_HFOV = 640, 360, 15, 90.0
DEPTH_W, DEPTH_H, DEPTH_RANGE = 64, 36, 100.0
LIDAR_HFOV, LIDAR_VMIN, LIDAR_VMAX = 120.0, -20.0, 10.0
LIDAR_COLS, LIDAR_CHANNELS, LIDAR_HZ, LIDAR_RANGE = 240, 30, 10, 100.0
SENSOR_OFFSET, CAM_HEIGHT_FRAC, LIDAR_HEIGHT_FRAC = 0.2, 0.8, 0.6
PHYSICS_DT = 1.0 / 60.0
GROUND_PREFIXES = ("/World/Terrain/Collider", "/World/Terrain/Terrain", "/World/Terrain/Surround")

simulation_app = None   # set in main() for standalone runs


# ============================================================================================ world
def world_usda_path(world_id: str, data_dir: str | None = None) -> str:
    return os.path.join(data_dir or os.path.join(REPO, "data/worlds"), world_id, "isaac", "world.usda")


def ensure_built(world_id: str, data_dir: str | None = None) -> str:
    """Run build_stage.py (needs pxr + trimesh, both present in Isaac's python) if world.usda is missing."""
    path = world_usda_path(world_id, data_dir)
    if not os.path.exists(path):
        print(f"[load_world] {path} missing, running build_stage.py", flush=True)
        subprocess.check_call([sys.executable, os.path.join(HERE, "build_stage.py"), world_id]
                              + (["--data-dir", data_dir] if data_dir else []))
    return path


def _update(n: int = 1) -> None:
    import omni.kit.app
    for _ in range(n):
        if simulation_app is not None:
            simulation_app.update()
        else:
            omni.kit.app.get_app().update()


def build_world(world_id: str = "quarry", data_dir: str | None = None, mode: str = "open"):
    """mode='open': open world.usda as the stage (keeps its /physicsScene, recommended).
    mode='reference': reference /World into the current stage (keeps whatever stage the app has)."""
    import omni.usd
    from pxr import UsdGeom
    path = ensure_built(world_id, data_dir)
    ctx = omni.usd.get_context()
    if mode == "open":
        ctx.open_stage(path)
        while ctx.get_stage_loading_status()[2] > 0:
            _update()
        _update(2)
        stage = ctx.get_stage()
    else:
        stage = ctx.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        prim = stage.DefinePrim("/World", "Xform")
        prim.GetReferences().AddReference(path)
        if not stage.GetPrimAtPath("/physicsScene"):
            from pxr import Gf, UsdPhysics
            ps = UsdPhysics.Scene.Define(stage, "/physicsScene")
            ps.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
            ps.CreateGravityMagnitudeAttr(9.81)
        _update(2)
    _ensure_models(stage, os.path.dirname(path))
    print(f"[load_world] stage ready: {path}", flush=True)
    return stage


def convert_with_asset_converter(src: str, dst: str) -> bool:
    """Fallback GLB -> USD through omni.kit.asset_converter (used only if build_stage's pxr models are missing)."""
    import asyncio
    from isaacsim.core.utils.extensions import enable_extension
    enable_extension("omni.kit.asset_converter")
    _update()
    import omni.kit.asset_converter as ac

    async def run():
        ctx = ac.AssetConverterContext()
        ctx.ignore_materials = False
        ctx.ignore_animations = True
        ctx.ignore_camera = True
        ctx.ignore_light = True
        ctx.use_meter_as_world_unit = True
        ctx.embed_textures = True
        task = ac.get_instance().create_converter_task(src, dst, None, ctx)
        ok = await task.wait_until_finished()
        if not ok:
            print(f"[load_world] asset converter failed {src}: {task.get_status()} {task.get_error_message()}")
        return ok

    fut = asyncio.ensure_future(run())
    while not fut.done():
        _update()
    return bool(fut.result())


def _ensure_models(stage, isaac_dir: str) -> None:
    """build_stage.py writes models/*.usdc with pxr. If they are missing, convert the GLBs with the asset converter
    into models/*_conv.usd (no bed hinge / joints then: glTF frame, wrapped by _model_wrapper_ops)."""
    mdir = os.path.join(isaac_dir, "models")
    os.makedirs(mdir, exist_ok=True)
    for name, srcs in (("komatsu_hd465", ["assets/models/komatsu_hd465/komatsu_hd465.glb",
                                          "unity/Assets/Models/Trucks/komatsu_hd465.glb"]),
                       ("excavator", ["assets/models/excavator/excavator_rigged.glb",
                                      "assets/models/excavator/excavator.glb"])):
        if os.path.exists(os.path.join(mdir, f"{name}.usdc")):
            continue
        src = next((os.path.join(REPO, s) for s in srcs if os.path.exists(os.path.join(REPO, s))), None)
        dst = os.path.join(mdir, f"{name}_conv.usd")
        if src and not os.path.exists(dst):
            convert_with_asset_converter(src, dst)


def _model_reference(isaac_dir: str, name: str) -> tuple[str, bool]:
    p = os.path.join(isaac_dir, "models", f"{name}.usdc")
    if os.path.exists(p):
        return p, True
    return os.path.join(isaac_dir, "models", f"{name}_conv.usd"), False


# ============================================================================================ trucks
class TruckController:
    """Unity TruckAgent (kinematic bicycle, yaw-only root) + MineGroundFollow (4 wheel rays, PhysX scene query).
    Root prim = yaw + ground height (kinematic rigid body). Child 'Model' gets the visual tilt; 'Sensors' stays
    level unless tilt_sensors=True (Unity default: off)."""

    def __init__(self, stage, root_path: str, truck_id: str, length: float, width: float, tilt_sensors=False):
        from pxr import UsdGeom
        self.stage, self.path, self.id = stage, root_path, truck_id
        self.root = stage.GetPrimAtPath(root_path)
        self.model = stage.GetPrimAtPath(root_path + "/Model")
        self.sensors = stage.GetPrimAtPath(root_path + "/Sensors")
        m = UsdGeom.Xformable(self.root).ComputeLocalToWorldTransform(0)
        t = m.ExtractTranslation()
        self.pos = np.array([t[0], t[1], t[2]], dtype=np.float64)
        q = m.ExtractRotationQuat()
        self.yaw = quat_wxyz_to_yaw_deg([q.GetReal(), *q.GetImaginary()])
        self.speed = 0.0
        self.throttle = self.steer = 0.0
        self.cmd_time = -1e9
        self.front, self.rear, self.half_track = 0.28 * length, -0.18 * length, 0.38 * width
        self.tilt_sensors = tilt_sensors
        self._ops = self._xform_ops(self.root)
        self._model_ops = self._xform_ops(self.model) if self.model else None
        self._sensor_ops = self._xform_ops(self.sensors) if self.sensors and tilt_sensors else None
        self.ground_z = self.pos[2]

    @staticmethod
    def _xform_ops(prim):
        from pxr import UsdGeom
        x = UsdGeom.Xformable(prim)
        ops = {op.GetOpName(): op for op in x.GetOrderedXformOps()}
        if "xformOp:translate" not in ops or "xformOp:orient" not in ops:
            x.ClearXformOpOrder()
            ops = {"xformOp:translate": x.AddTranslateOp(), "xformOp:orient": x.AddOrientOp()}
        return ops

    def set_action(self, throttle: float, steer: float, now: float) -> None:
        """Brain `action` message: throttle 0..1, steer -1..1 (Unity: + = right = clockwise heading)."""
        self.throttle, self.steer, self.cmd_time = float(throttle), float(steer), now

    def _ray_down(self, x: float, y: float, z_from: float, length: float) -> float | None:
        from omni.physx import get_physx_scene_query_interface
        best = [None]

        def report(hit):
            if str(hit.collision).startswith(GROUND_PREFIXES):
                if best[0] is None or hit.distance < best[0][0]:
                    best[0] = (hit.distance, hit.position[2])
            return True  # keep going: skip trucks / other colliders
        get_physx_scene_query_interface().raycast_all((x, y, z_from), (0.0, 0.0, -1.0), length, report)
        return None if best[0] is None else float(best[0][1])

    def ground(self) -> tuple[float, float, float]:
        """(z, pitch_deg, roll_deg) from 4 wheel contact rays; probe 8 m up / 150 m down, retry from 1000 m."""
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        zs = {}
        for name, (lx, ly) in {"fl": (self.front, self.half_track), "fr": (self.front, -self.half_track),
                               "rl": (self.rear, self.half_track), "rr": (self.rear, -self.half_track)}.items():
            wx, wy = self.pos[0] + c * lx - s * ly, self.pos[1] + s * lx + c * ly
            z = self._ray_down(wx, wy, self.pos[2] + 8.0, 158.0)
            if z is None:
                z = self._ray_down(wx, wy, 1000.0, 2000.0)
            zs[name] = z
        got = [v for v in zs.values() if v is not None]
        if not got:
            return self.pos[2], 0.0, 0.0
        for k in zs:
            zs[k] = zs[k] if zs[k] is not None else float(np.mean(got))
        zf, zr = (zs["fl"] + zs["fr"]) / 2, (zs["rl"] + zs["rr"]) / 2
        zl, zrt = (zs["fl"] + zs["rl"]) / 2, (zs["fr"] + zs["rr"]) / 2
        pitch = -math.degrees(math.atan2(zf - zr, self.front - self.rear))     # nose up = negative about +Y
        roll = math.degrees(math.atan2(zl - zrt, 2 * self.half_track))
        return float(np.mean(list(zs.values()))), max(-25, min(25, pitch)), max(-25, min(25, roll))

    def step(self, dt: float, now: float) -> None:
        from pxr import Gf
        throttle, steer = (self.throttle, self.steer) if now - self.cmd_time <= CMD_TIMEOUT else (0.0, 0.0)
        throttle, steer = min(1, max(0, throttle)), min(1, max(-1, steer))
        self.speed += (throttle * MAX_SPEED - self.speed) * min(1.0, dt * ACCEL)
        yaw_rate = self.speed / WHEELBASE * math.tan(math.radians(steer * MAX_STEER_DEG))   # rad/s, + = right
        self.yaw += math.degrees(yaw_rate * dt)   # Unity + = clockwise heading; yaw_isaac = heading - 90, same sign
        r = math.radians(self.yaw)
        self.pos[0] += math.cos(r) * self.speed * dt
        self.pos[1] += math.sin(r) * self.speed * dt
        z, pitch, roll = self.ground()
        if abs(z - self.ground_z) > 4.0:
            self.ground_z = z                                    # snap (spawn/teleport)
        else:
            k = 1 - math.exp(-dt / 0.06)
            self.ground_z = z if z > self.ground_z else self.ground_z + (z - self.ground_z) * k
        self.pos[2] = self.ground_z
        w, x, y, zq = yaw_quat_wxyz(self.yaw)
        self._ops["xformOp:translate"].Set(Gf.Vec3d(*self.pos))
        self._ops["xformOp:orient"].Set(Gf.Quatf(w, x, y, zq))
        tq = (Gf.Rotation(Gf.Vec3d(1, 0, 0), roll) * Gf.Rotation(Gf.Vec3d(0, 1, 0), pitch)).GetQuat()
        for ops in (self._model_ops, self._sensor_ops):
            if ops:
                ops["xformOp:orient"].Set(Gf.Quatf(tq.GetReal(), Gf.Vec3f(tq.GetImaginary())))

    def brain_pose(self) -> dict:
        return isaac_pose_to_brain(self.pos, self.yaw)


def spawn_trucks(stage, n: int = TRUCK_COUNT, isaac_dir: str | None = None) -> list[TruckController]:
    """/World/Trucks/<id> (kinematic RigidBody + box collider) > Model (reference) + Sensors, at /World/Spawns/*."""
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics
    if isaac_dir is None:
        isaac_dir = os.path.dirname(stage.GetRootLayer().realPath)
    ref, ours = _model_reference(isaac_dir, "komatsu_hd465")
    spawns = list(stage.GetPrimAtPath("/World/Spawns").GetChildren())[:n]
    trucks = []
    for sp in spawns:
        tid = sp.GetAttribute("mine:id").Get() if sp.HasAttribute("mine:id") else sp.GetName()
        path = f"/World/Trucks/{sp.GetName()}"
        root = UsdGeom.Xform.Define(stage, path)
        m = UsdGeom.Xformable(sp).ComputeLocalToWorldTransform(0)
        root.AddTranslateOp().Set(m.ExtractTranslation())
        q = m.ExtractRotationQuat()
        root.AddOrientOp().Set(Gf.Quatf(q.GetReal(), Gf.Vec3f(q.GetImaginary())))
        rp = root.GetPrim()
        rp.CreateAttribute("mine:id", Sdf.ValueTypeNames.String, custom=True).Set(tid)
        model = UsdGeom.Xform.Define(stage, path + "/Model")
        model.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
        model.AddOrientOp().Set(Gf.Quatf(1, 0, 0, 0))
        if ours:
            model.GetPrim().GetReferences().AddReference(os.path.relpath(ref, isaac_dir))
        else:   # converter output is in glTF frame (Y up, +Z fwd): child with the glTF->vehicle rotation, no bed rig
            inner = UsdGeom.Xform.Define(stage, path + "/Model/glTF")
            qg = Gf.Matrix3d(*G3.T.flatten().tolist()).ExtractRotation().GetQuat()   # Gf is row-vector: use G3^T
            inner.AddOrientOp().Set(Gf.Quatf(qg.GetReal(), Gf.Vec3f(qg.GetImaginary())))
            inner.GetPrim().GetReferences().AddReference(ref)
        UsdGeom.Xform.Define(stage, path + "/Sensors").AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
        UsdGeom.Xformable(stage.GetPrimAtPath(path + "/Sensors")).AddOrientOp().Set(Gf.Quatf(1, 0, 0, 0))
        mp = model.GetPrim()
        L = float(mp.GetAttribute("mine:length_m").Get() or 9.35) if mp.HasAttribute("mine:length_m") else 9.35
        W = float(mp.GetAttribute("mine:width_m").Get() or 4.24) if mp.HasAttribute("mine:width_m") else 4.24
        H = float(mp.GetAttribute("mine:height_m").Get() or 4.4) if mp.HasAttribute("mine:height_m") else 4.4
        # kinematic body (Unity: kinematic Rigidbody + BoxCollider = renderer bounds)
        UsdPhysics.RigidBodyAPI.Apply(rp).CreateKinematicEnabledAttr(True)
        box = UsdGeom.Cube.Define(stage, path + "/Collision")
        box.CreateSizeAttr(1.0)
        box.AddTranslateOp().Set(Gf.Vec3d(0, 0, H / 2))
        box.AddScaleOp().Set(Gf.Vec3f(L, W, H))
        box.CreatePurposeAttr(UsdGeom.Tokens.guide)
        UsdPhysics.CollisionAPI.Apply(box.GetPrim())
        rp.CreateAttribute("mine:dims", Sdf.ValueTypeNames.Float3, custom=True).Set(Gf.Vec3f(L, W, H))
        trucks.append(TruckController(stage, path, tid, L, W))
        print(f"[load_world] truck {tid} at {path} pos={tuple(round(v, 2) for v in m.ExtractTranslation())}")
    return trucks


# ============================================================================================ sensors
def _set_attr(prim, name, value, typ):
    a = prim.GetAttribute(name)
    if not a:
        a = prim.CreateAttribute(name, typ, custom=False)
    a.Set(value)


def lidar_emitter_elevations() -> list[float]:
    """spec.md cell centres: el = vmin + (i + 0.5) * (vmax - vmin) / channels."""
    return [LIDAR_VMIN + (i + 0.5) * (LIDAR_VMAX - LIDAR_VMIN) / LIDAR_CHANNELS for i in range(LIDAR_CHANNELS)]


def _configure_omnilidar(prim) -> None:
    """Generic rotary OmniLidar: 30 emitters at the spec elevations, 0.5 deg azimuth step (720 firings/rev at
    10 Hz = 7200 Hz), 100 m. The 120 deg window is cropped in software (read_lidar) so the azimuth convention of
    validStart/EndAzimuthDeg does not matter. Attribute names: omni.usd.schema.omni_sensors
    OmniSensorGenericLidarCoreAPI / OmniSensorGenericLidarCoreEmitterStateAPI (instance s001)."""
    from pxr import Sdf, Vt
    T = Sdf.ValueTypeNames
    core = "omni:sensor:Core:"
    _set_attr(prim, core + "scanType", "ROTARY", T.Token)
    _set_attr(prim, core + "numberOfEmitters", LIDAR_CHANNELS, T.UInt)
    _set_attr(prim, core + "numberOfChannels", LIDAR_CHANNELS, T.UInt)
    _set_attr(prim, core + "scanRateBaseHz", LIDAR_HZ, T.UInt)
    _set_attr(prim, core + "patternFiringRateHz", int(LIDAR_HZ * LIDAR_COLS * 360 / LIDAR_HFOV), T.UInt)
    _set_attr(prim, core + "nearRangeM", 0.5, T.Float)
    _set_attr(prim, core + "farRangeM", LIDAR_RANGE, T.Float)
    _set_attr(prim, core + "validStartAzimuthDeg", 0.0, T.Float)
    _set_attr(prim, core + "validEndAzimuthDeg", 360.0, T.Float)
    _set_attr(prim, "omni:sensor:tickRate", LIDAR_HZ, T.UInt)   # must equal scanRateBaseHz for full-scan accumulation
    api = "OmniSensorGenericLidarCoreEmitterStateAPI:s001"
    if api not in [str(s) for s in prim.GetAppliedSchemas()]:
        prim.AddAppliedSchema(api)
    es = core + "emitterState:s001:"
    n = LIDAR_CHANNELS
    _set_attr(prim, es + "azimuthDeg", Vt.FloatArray([0.0] * n), T.FloatArray)
    _set_attr(prim, es + "elevationDeg", Vt.FloatArray(lidar_emitter_elevations()), T.FloatArray)
    _set_attr(prim, es + "fireTimeNs", Vt.UIntArray([0] * n), T.UIntArray)
    _set_attr(prim, es + "channelId", Vt.UIntArray(list(range(1, n + 1))), T.UIntArray)


class TruckSensors:
    def __init__(self, truck: TruckController, camera, lidar_path: str, lidar_annotator, cam_mount, lidar_mount):
        self.truck, self.camera, self.lidar_path, self.lidar_annotator = truck, camera, lidar_path, lidar_annotator
        self.cam_mount, self.lidar_mount = cam_mount, lidar_mount
        self.lidar_frame = os.environ.get("MINE_LIDAR_FRAME", "auto")   # auto | sensor | world


def add_sensors(stage, trucks: list[TruckController], depth: bool = True) -> list[TruckSensors]:
    """Per truck: RTX camera (Sensors/camera) and RTX lidar (Sensors/lidar) at the front of the cab
    (spec: 0.2 m ahead of the collider, camera at 0.8 x truck height, lidar at 0.6 x)."""
    import omni.kit.commands
    import omni.replicator.core as rep
    from isaacsim.sensors.camera import Camera
    from pxr import Gf, UsdGeom
    out = []
    for t in trucks:
        L, W, H = stage.GetPrimAtPath(t.path).GetAttribute("mine:dims").Get()
        cam_mount = (L / 2 + SENSOR_OFFSET, 0.0, CAM_HEIGHT_FRAC * H)
        lidar_mount = (L / 2 + SENSOR_OFFSET, 0.0, LIDAR_HEIGHT_FRAC * H)
        cam_path = f"{t.path}/Sensors/camera"
        # Camera(orientation) uses Isaac "world" camera axes: identity = looking along the parent's +X, +Z up.
        cam = Camera(prim_path=cam_path, translation=np.array(cam_mount), orientation=np.array([1.0, 0, 0, 0]),
                     frequency=CAM_FPS, resolution=(CAM_W, CAM_H))
        # 90 deg hFOV: hFOV = 2 atan(hAperture / (2 f)); set the USD attributes directly (tenths of a stage unit)
        ucam = UsdGeom.Camera(stage.GetPrimAtPath(cam_path))
        ucam.GetHorizontalApertureAttr().Set(20.955)
        ucam.GetVerticalApertureAttr().Set(20.955 * CAM_H / CAM_W)
        ucam.GetFocalLengthAttr().Set(20.955 / 2 / math.tan(math.radians(CAM_HFOV / 2)))
        ucam.GetClippingRangeAttr().Set(Gf.Vec2f(0.1, 5000.0))
        lidar_path = f"{t.path}/Sensors/lidar"
        _, lidar = omni.kit.commands.execute("IsaacSensorCreateRtxLidar", path=lidar_path, parent=None, config=None,
                                             translation=Gf.Vec3d(*lidar_mount), orientation=Gf.Quatd(1, 0, 0, 0))
        lprim = lidar if (lidar is not None and hasattr(lidar, "GetPath")) else stage.GetPrimAtPath(lidar_path)
        _configure_omnilidar(lprim)
        rp = rep.create.render_product(str(lprim.GetPath()), resolution=(1, 1), name=f"{t.id}_lidar")
        ann = rep.AnnotatorRegistry.get_annotator("IsaacCreateRTXLidarScanBuffer")
        ann.attach([rp.path if hasattr(rp, "path") else rp])
        out.append(TruckSensors(t, cam, str(lprim.GetPath()), ann, cam_mount, lidar_mount))
        print(f"[load_world] sensors {t.id}: camera {cam_path} @ {tuple(round(v, 2) for v in cam_mount)}, "
              f"lidar {lidar_path} @ {tuple(round(v, 2) for v in lidar_mount)}")
    return out


def initialize_sensors(sensors: list[TruckSensors], depth: bool = True) -> None:
    """Call after world.reset() (Camera.initialize needs a running timeline)."""
    for s in sensors:
        s.camera.initialize()
        if depth:
            s.camera.add_distance_to_image_plane_to_frame()


def _lidar_to_sensor(s: TruckSensors, pts: np.ndarray) -> np.ndarray:
    """Scan-buffer points may come back in world or sensor frame depending on version/flags: detect and fix."""
    from pxr import UsdGeom
    stage = s.truck.stage
    m = UsdGeom.Xformable(stage.GetPrimAtPath(s.lidar_path)).ComputeLocalToWorldTransform(0)
    M = np.array(m, dtype=np.float64).T          # Gf is row-vector; transpose to column-vector
    origin = M[:3, 3]
    frame = s.lidar_frame
    if frame == "auto" and len(pts):
        frame = "world" if np.median(np.linalg.norm(pts - origin, axis=1)) < np.median(np.linalg.norm(pts, axis=1)) \
            else "sensor"
    if frame == "world":
        R = M[:3, :3]
        return (pts - origin) @ R          # R orthonormal: world -> sensor = R^T (p - o)
    return pts


def read_lidar(s: TruckSensors) -> np.ndarray:
    """Full scan cropped to the spec window, in brain/Unity sensor axes (x right, y up, z fwd), metres."""
    d = s.lidar_annotator.get_data()
    pts = np.asarray(d.get("data") if isinstance(d, dict) else d, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        return np.zeros((0, 3), np.float32)
    p = _lidar_to_sensor(s, pts)
    r = np.linalg.norm(p, axis=1)
    az = np.degrees(np.arctan2(p[:, 1], p[:, 0]))
    el = np.degrees(np.arcsin(np.clip(p[:, 2] / np.maximum(r, 1e-9), -1, 1)))
    keep = (np.abs(az) <= LIDAR_HFOV / 2) & (el >= LIDAR_VMIN - 0.5) & (el <= LIDAR_VMAX + 0.5) & \
           (r > 0.3) & (r <= LIDAR_RANGE)
    return isaac_sensor_points_to_unity(p[keep])[: LIDAR_COLS * LIDAR_CHANNELS]


def read_sensors(sensors: list[TruckSensors]) -> dict:
    """{truck_id: {rgb (H,W,3) uint8, depth (36,64) float32 m (miss = 100), lidar (N,3) f32, pose {x,z,yaw},
    speed}} - the inputs of brain protocol `frame` / `lidar` messages (spec.md Sim protocol)."""
    out = {}
    for s in sensors:
        rgba = s.camera.get_rgba()
        rgb = None if rgba is None or rgba.size == 0 else np.ascontiguousarray(rgba[..., :3]).astype(np.uint8)
        depth = None
        try:
            dm = s.camera.get_current_frame().get("distance_to_image_plane")
            if dm is not None and dm.size:
                ys = (np.arange(DEPTH_H) + 0.5) * dm.shape[0] / DEPTH_H
                xs = (np.arange(DEPTH_W) + 0.5) * dm.shape[1] / DEPTH_W
                depth = dm[ys.astype(int)][:, xs.astype(int)].astype(np.float32)
                depth[~np.isfinite(depth) | (depth > DEPTH_RANGE)] = DEPTH_RANGE
        except Exception:
            pass
        out[s.truck.id] = {"rgb": rgb, "depth": depth, "lidar": read_lidar(s),
                           "pose": s.truck.brain_pose(), "speed": s.truck.speed}
    return out


# ============================================================================================ main
def setup(world_id: str, data_dir: str | None, radio: str | None, demo_drive: bool):
    from isaacsim.core.api import World
    import animate
    stage = build_world(world_id, data_dir)
    world = World(stage_units_in_meters=1.0, physics_dt=PHYSICS_DT, rendering_dt=PHYSICS_DT)
    trucks = spawn_trucks(stage)
    sensors = add_sensors(stage, trucks)
    anim = animate.init(stage)
    link = animate.RadioLink(anim, radio).start() if radio else None
    world.reset()
    initialize_sensors(sensors)
    clock = {"t": 0.0}

    def on_physics(dt):
        clock["t"] += dt
        for i, t in enumerate(trucks):
            if demo_drive:   # gentle circle so motion / ground follow can be checked without the brain
                t.set_action(0.4, 0.3 if i % 2 else -0.3, clock["t"])
            t.step(dt, clock["t"])
        anim.update(dt)
    world.add_physics_callback("mine_trucks", on_physics)
    return world, stage, trucks, sensors, anim, link


def main(argv=None) -> int:
    global simulation_app
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="quarry")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--radio", default=None, help="RADIO_URL, enables the read-only RadioLink (e.g. http://127.0.0.1:8090)")
    ap.add_argument("--seconds", type=float, default=0.0, help="run time, 0 = until closed")
    ap.add_argument("--save-frames", default=None, help="dir: write truck camera PNGs + lidar .npy once per second")
    ap.add_argument("--demo-drive", action="store_true")
    ap.add_argument("--dig", action="store_true", help="start every excavator digging (no radio needed)")
    a, _ = ap.parse_known_args(argv)

    from isaacsim import SimulationApp
    simulation_app = SimulationApp({"headless": a.headless, "width": 1280, "height": 720,
                                    "renderer": "RayTracedLighting"})
    world, stage, trucks, sensors, anim, link = setup(a.world, a.data_dir, a.radio, a.demo_drive)
    if a.dig:
        for e in anim.excavators.values():
            e.set_state("digging")
        if trucks:
            anim.dump_cycle(trucks[0].id, 6.0)
    world.play()
    t0, last_save = time.time(), -1
    try:
        while simulation_app.is_running():
            world.step(render=True)
            el = time.time() - t0
            if a.save_frames and int(el) != last_save:
                last_save = int(el)
                _save(a.save_frames, read_sensors(sensors), last_save)
            if a.seconds and el > a.seconds:
                break
    finally:
        if link:
            link.stop()
        simulation_app.close()
    return 0


def _save(d: str, frames: dict, k: int) -> None:
    os.makedirs(d, exist_ok=True)
    from PIL import Image
    for tid, f in frames.items():
        if f["rgb"] is not None:
            Image.fromarray(f["rgb"]).save(os.path.join(d, f"{tid}_{k:04d}.png"))
        np.save(os.path.join(d, f"{tid}_{k:04d}_lidar.npy"), f["lidar"])
        print(f"[frames] {tid} t={k}s pose={f['pose']} lidar_pts={len(f['lidar'])}"
              f" rgb={'ok' if f['rgb'] is not None else 'none'}", flush=True)


def main_in_editor(world: str = "quarry", radio: str | None = None):
    """Script Editor: build in the running app (press Play afterwards)."""
    return setup(world, None, radio, demo_drive=False)


if __name__ == "__main__":
    sys.exit(main())
