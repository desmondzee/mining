import math
import time

import numpy as np
from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

from . import dust
from .gravel import Gravel, rear_contacts, truck_frame
from .ground import GroundMap
from .presets import resolve

ROOT = "/World/TruckFx"
FLOW_LAYER = 7
FLOW_EXTENSION = "omni.flowusd"
FLOW_SETTINGS = ["/rtx/flow/enabled", "/rtx/flow/pathTracingEnabled", "/rtx/flow/rayTracedReflectionsEnabled", "/rtx/flow/rayTracedTranslucencyEnabled"]
WIND_BOX = 600.0
WIND_COUPLE = 0.6
SMOKE_FADE = 0.35
SMOKE_DAMPING = 0.04
VELOCITY_DAMPING = 0.02
RAY_ATTENUATION = 0.12
SHADOW_ATTENUATION = 0.4
SUN_SHARE = 0.8
DUST_EXPOSURE = 0.3
PALETTE = 8
ROCK_SHAPES = 3
ROCK_TONES = (0.15, 0.5, 0.85)
ROCK_DARKEN = 0.72
GRAVEL_SLIP_GAIN = 0.5
UINT_ATTRS = {"resolution", "numSteps", "maxStepsPerSimulate", "blockMinLifetime", "numSubSteps", "velocitySubSteps"}


def enable_flow() -> bool:
    try:
        import carb
        import omni.kit.app
    except ImportError:
        return False
    ok = omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(FLOW_EXTENSION, True)
    settings = carb.settings.get_settings()
    for key in FLOW_SETTINGS:
        settings.set(key, True)
    return bool(ok)


def value_type(name: str, value):
    if isinstance(value, bool):
        return Sdf.ValueTypeNames.Bool
    if isinstance(value, int):
        return Sdf.ValueTypeNames.UInt if name in UINT_ATTRS else Sdf.ValueTypeNames.Int
    if isinstance(value, float):
        return Sdf.ValueTypeNames.Float
    if isinstance(value, Gf.Vec3f):
        return Sdf.ValueTypeNames.Float3
    if isinstance(value, Vt.Vec4fArray):
        return Sdf.ValueTypeNames.Float4Array
    if isinstance(value, Vt.FloatArray):
        return Sdf.ValueTypeNames.FloatArray
    raise TypeError(f"{name}: {type(value)}")


def author(stage, path: str, type_name: str, attrs: dict):
    prim = stage.DefinePrim(path, type_name)
    for name, value in attrs.items():
        attr = prim.GetAttribute(name) or prim.CreateAttribute(name, value_type(name, value))
        attr.Set(value)
    return prim


def rock_mesh(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    t = (1 + 5**0.5) / 2
    v = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    f = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
                  [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    v *= rng.uniform(0.75, 1.15, (len(v), 1))
    v *= rng.uniform([0.8, 0.7, 0.5], [1.2, 1.0, 0.8])
    return v * 0.5, f


def sun_direction(elevation: float, azimuth: float) -> Gf.Vec3f:
    e, a = math.radians(elevation), math.radians(azimuth)
    return Gf.Vec3f(math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))


def quath_array(quat: np.ndarray) -> Vt.QuathArray:
    return Vt.QuathArray.FromNumpy(np.ascontiguousarray(quat[:, [1, 2, 3, 0]], np.float16))


def vec3(v) -> Gf.Vec3f:
    return Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))


class TruckFx:
    def __init__(self, stage, trucks_cfg=None, preset: str = "high", ground=None, **overrides) -> None:
        self.stage = stage
        self.cfg = cfg = resolve(preset, **overrides)
        self.trucks = list(trucks_cfg or [])
        self.wind = np.asarray(cfg["wind"], float)
        if ground is None:
            ground = GroundMap.uniform(cfg["dust_color"])
        elif not isinstance(ground, GroundMap):
            ground = GroundMap.load(ground)
        self.ground = ground
        self.palette = ground.palette(PALETTE)
        self.surface = dust.Surface(ground, self.palette)
        self.flow = bool(cfg.get("dust")) and enable_flow()
        self.gravel = Gravel(cfg.get("gravel", 0), cfg.get("gravel_rate", 0.0), cfg["seed"], cfg["min_speed"], ROCK_SHAPES, len(ROCK_TONES))
        self.emitters: dict[str, list] = {}
        self.cpu_ms, self.steps = 0.0, 0
        if preset == "off":
            return
        UsdGeom.Scope.Define(stage, ROOT)
        if self.flow:
            self.build_flow()
        if self.gravel.cap:
            self.build_gravel()
        for t in self.trucks:
            self.truck_emitters(t)

    def build_flow(self) -> None:
        cfg, stage, root = self.cfg, self.stage, ROOT + "/Dust"
        sun_irr = cfg["sun_intensity"] * max(math.sin(math.radians(cfg["sun_elevation"])), 0.0)
        brightness = (sun_irr * SUN_SHARE + cfg["dome_intensity"]) / math.pi
        ambient = cfg["dome_intensity"] / (sun_irr + cfg["dome_intensity"])
        colours = Vt.Vec4fArray([Gf.Vec4f(float(c[0]), float(c[1]), float(c[2]), 1.0) for c in self.palette])
        UsdGeom.Xform.Define(stage, root)
        author(stage, root + "/simulate", "FlowSimulate", {
            "layer": FLOW_LAYER, "densityCellSize": float(cfg["cell"]), "stepsPerSecond": float(cfg["steps_per_second"]),
            "maxStepsPerSimulate": 2, "blockMinLifetime": 4, "enableLowPrecisionVelocity": True, "physicsCollisionEnabled": False,
        })
        author(stage, root + "/simulate/advection", "FlowAdvectionCombustionParams", {
            "combustionEnabled": False, "buoyancyPerTemp": 0.0, "buoyancyPerSmoke": 0.0, "coolingRate": 0.0, "gravity": Gf.Vec3f(0.0, 0.0, -9.81),
        })
        author(stage, root + "/simulate/advection/smoke", "FlowAdvectionChannelParams", {"fade": SMOKE_FADE, "damping": SMOKE_DAMPING})
        author(stage, root + "/simulate/advection/velocity", "FlowAdvectionChannelParams", {"fade": 0.0, "damping": VELOCITY_DAMPING})
        author(stage, root + "/simulate/advection/temperature", "FlowAdvectionChannelParams", {"fade": 0.0, "damping": 0.0})
        author(stage, root + "/simulate/vorticity", "FlowVorticityParams", {"enabled": cfg["vorticity"] > 0, "forceScale": float(cfg["vorticity"])})
        author(stage, root + "/simulate/summaryAllocate", "FlowSummaryAllocateParams", {"smokeThreshold": 0.01, "speedThreshold": 100.0})
        author(stage, root + "/offscreen", "FlowOffscreen", {"layer": FLOW_LAYER})
        author(stage, root + "/offscreen/colormap", "FlowRayMarchColormapParams", {
            "rgbaPoints": colours, "xPoints": Vt.FloatArray(np.linspace(0.0, 1.0, len(self.palette)).tolist()), "resolution": 64,
            "colorScalePoints": Vt.FloatArray([1.0] * len(self.palette)), "colorScale": float(brightness * DUST_EXPOSURE * cfg.get("dust_brightness", 1.0)),
        })
        author(stage, root + "/offscreen/shadow", "FlowShadowParams", {
            "enabled": True, "lightDirection": sun_direction(cfg["sun_elevation"], cfg["sun_azimuth"]), "isPointLight": False,
            "minIntensity": float(ambient), "attenuation": SHADOW_ATTENUATION, "numSteps": int(cfg["shadow_steps"]),
        })
        author(stage, root + "/render", "FlowRender", {"layer": FLOW_LAYER})
        author(stage, root + "/render/rayMarch", "FlowRayMarchParams", {"attenuation": RAY_ATTENUATION * cfg.get("dust_density", 1.0), "colorScale": 1.0})
        self.wind_box = author(stage, root + "/wind", "FlowEmitterBox", {
            "layer": FLOW_LAYER, "allocationScale": 0.0, "halfSize": Gf.Vec3f(WIND_BOX / 2, WIND_BOX / 2, 40.0),
            "velocity": vec3(self.wind), "coupleRateVelocity": WIND_COUPLE, "velocityIsWorldSpace": True,
            "smoke": 0.0, "coupleRateSmoke": 0.0, "temperature": 0.0, "coupleRateTemperature": 0.0, "fuel": 0.0, "coupleRateFuel": 0.0,
        })

    def rock_material(self, path: str, colour) -> UsdShade.Material:
        mat = UsdShade.Material.Define(self.stage, path)
        shader = UsdShade.Shader.Define(self.stage, path + "/surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(vec3(colour))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return mat

    def build_gravel(self) -> None:
        stage, root = self.stage, ROOT + "/Gravel"
        self.instancer = UsdGeom.PointInstancer.Define(stage, root)
        rng = np.random.default_rng([self.cfg["seed"], 9999])
        shapes = [rock_mesh(rng) for _ in range(ROCK_SHAPES)]
        protos = []
        for t, q in enumerate(ROCK_TONES):
            mat = self.rock_material(f"{root}/Looks/rock{t}", self.palette[int(q * (len(self.palette) - 1))] * ROCK_DARKEN)
            for i, (v, f) in enumerate(shapes):
                mesh = UsdGeom.Mesh.Define(stage, f"{root}/Prototypes/rock{t}_{i}")
                mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(v.astype(np.float32)))
                mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(f)))
                mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(f.ravel().tolist()))
                mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
                UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
                protos.append(mesh.GetPath())
        self.instancer.CreatePrototypesRel().SetTargets(protos)
        self.write_gravel()

    def truck_emitters(self, truck_id: str) -> list:
        if not self.flow:
            return []
        if truck_id not in self.emitters:
            safe = truck_id.replace("-", "_")
            specs = dust.emitters(np.zeros(3), 0.0, 0.0, rear_contacts(np.zeros(3), 0.0, self.cfg["rear_axle"], self.cfg["half_track"]), self.cfg)
            prims = [author(self.stage, f"{ROOT}/Dust/{safe}_{i}", "FlowEmitterSphere", {
                "layer": FLOW_LAYER, "radius": float(spec[1]), "radiusIsWorldSpace": True, "velocityIsWorldSpace": True,
                "smoke": dust.SMOKE, "coupleRateSmoke": 0.0, "coupleRateVelocity": 2.0,
                "temperature": 0.5, "coupleRateTemperature": 0.0, "fuel": 0.0, "coupleRateFuel": 0.0,
                "position": Gf.Vec3f(0.0, 0.0, -1000.0), "velocity": Gf.Vec3f(0.0, 0.0, 0.0), "enabled": False,
            }) for i, spec in enumerate(specs)]
            names = ("enabled", "position", "velocity", "coupleRateSmoke", "temperature", "coupleRateTemperature")
            self.emitters[truck_id] = [{n: p.GetAttribute(n) for n in names} for p in prims]
        return self.emitters[truck_id]

    def set_wind(self, wind) -> None:
        self.wind = np.asarray(wind, float)
        self.cfg["wind"] = list(self.wind)
        if self.flow:
            self.wind_box.GetAttribute("velocity").Set(vec3(self.wind))

    def update(self, dt: float, trucks, height=None) -> None:
        if self.cfg["preset"] == "off":
            return
        t0 = time.perf_counter()
        cfg, batch, centre = self.cfg, [], []
        for t in trucks:
            self.truck_emitters(t[0])
        with Sdf.ChangeBlock():
            for truck_id, pos, yaw, speed, contacts in trucks:
                pos = np.asarray(pos, float)
                contacts = rear_contacts(pos, yaw, cfg["rear_axle"], cfg["half_track"]) if contacts is None else np.asarray(contacts, float).reshape(-1, 3)
                forward, right = truck_frame(yaw)
                surface = self.surface.update(truck_id, dt, contacts, yaw, speed)
                scale = float(np.mean(surface["gravel"])) * (1.0 + GRAVEL_SLIP_GAIN * surface["slip"])
                batch.append((truck_id, contacts, forward, right, speed, scale, float(np.mean(surface["tone"]))))
                centre.append(pos)
                if self.flow:
                    for attrs, (p, _, v, level, tone) in zip(self.truck_emitters(truck_id), dust.emitters(pos, yaw, speed, contacts, cfg, surface)):
                        on = level > 0.0
                        attrs["enabled"].Set(on)
                        attrs["position"].Set(vec3(p))
                        attrs["velocity"].Set(vec3(v))
                        attrs["coupleRateSmoke"].Set(dust.COUPLE * level)
                        attrs["temperature"].Set(tone)
                        attrs["coupleRateTemperature"].Set(dust.COUPLE if on else 0.0)
            if self.flow and centre:
                self.wind_box.GetAttribute("position").Set(vec3(np.mean(centre, axis=0)))
            if self.gravel.cap:
                self.gravel.update(dt, batch, height)
                self.write_gravel()
        self.cpu_ms += (time.perf_counter() - t0) * 1000
        self.steps += 1

    def write_gravel(self) -> None:
        pos, quat, size, proto = self.gravel.render_state()
        inst = self.instancer
        inst.GetPositionsAttr().Set(Vt.Vec3fArray.FromNumpy(pos.astype(np.float32)))
        inst.GetOrientationsAttr().Set(quath_array(quat))
        inst.GetScalesAttr().Set(Vt.Vec3fArray.FromNumpy(np.repeat(size[:, None], 3, axis=1).astype(np.float32)))
        inst.GetProtoIndicesAttr().Set(Vt.IntArray.FromNumpy(proto.astype(np.int32)))

    def surface_state(self, truck_id: str) -> dict | None:
        return self.surface.state.get(truck_id)

    def sensor_spheres(self) -> tuple[np.ndarray, np.ndarray]:
        pos, _, size, _ = self.gravel.render_state()
        return pos, size / 2

    def timing(self) -> float:
        return self.cpu_ms / max(self.steps, 1)
