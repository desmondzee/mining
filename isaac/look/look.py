import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

PRESETS = Path(__file__).with_name("presets.json")
SKY = "/World/Sky"
SUN = "/World/Sun"
LOOKS = "/World/Looks"
SCAN = "/World/Scan"
SURROUND = "/World/Surround"
TRUCKS = "/World/Trucks"
EQUIPMENT = "/World/Equipment"
PANO_W = 1024
SUN_QUANTILE = 0.9995
TONEMAP = {"clamp": 0, "linear": 1, "reinhard": 2, "reinhard_mod": 3, "hable_alu": 4, "hable_uc2": 5, "aces": 6, "iray": 7}


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load_preset(preset: str = "day", path: Path = PRESETS, **overrides) -> dict:
    data = json.loads(Path(path).read_text())
    if preset not in data["presets"]:
        raise KeyError(f"look preset {preset!r} not in {sorted(data['presets'])}")
    return merge(merge(data["defaults"], data["presets"][preset]), overrides)


def sun_vector(elevation: float, azimuth: float) -> np.ndarray:
    e, a = math.radians(elevation), math.radians(azimuth)
    return np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])


def pano_sun(image: np.ndarray) -> tuple[float, float, float]:
    lum = image[..., :3].astype(np.float32).mean(axis=2)
    upper = lum[: lum.shape[0] // 2]
    mask = upper >= np.quantile(upper, SUN_QUANTILE)
    ys, xs = np.nonzero(mask)
    w = upper[ys, xs]
    angle = xs / lum.shape[1] * 2 * math.pi
    u = (math.atan2((w * np.sin(angle)).sum(), (w * np.cos(angle)).sum()) / (2 * math.pi)) % 1.0
    v = float((w * ys).sum() / w.sum()) / lum.shape[0]
    return u, v, float(upper[mask].mean() / max(upper.mean(), 1e-6))


def latlong_azimuth(u: float, rotation: float, offset: float, sign: float) -> float:
    return (offset + sign * u * 360.0 + rotation) % 360.0


def dome_rotation_for(u: float, azimuth: float, offset: float, sign: float) -> float:
    return (azimuth - offset - sign * u * 360.0) % 360.0


def sky_variant(src: Path, cache: Path, remove_sun: float, saturation: float, gamma: float, tint: list[float]) -> Path:
    from PIL import Image

    key = hashlib.sha1(f"{src.resolve()}|{src.stat().st_mtime_ns}|{remove_sun}|{saturation}|{gamma}|{tint}".encode()).hexdigest()[:10]
    out = cache / f"sky_{key}.png"
    if out.exists():
        return out
    img = np.asarray(Image.open(src).convert("RGB")).astype(np.float32) / 255.0
    h, w = img.shape[:2]
    if remove_sun > 0:
        small = np.asarray(Image.fromarray((img * 255).astype(np.uint8)).resize((PANO_W, PANO_W // 2)))
        u, v, _ = pano_sun(small)
        yy, xx = np.mgrid[0:h, 0:w]
        lat = (0.5 - (yy + 0.5) / h) * math.pi
        lon = (xx + 0.5) / w * 2 * math.pi
        s_lat, s_lon = (0.5 - v) * math.pi, u * 2 * math.pi
        cosd = np.sin(lat) * math.sin(s_lat) + np.cos(lat) * math.cos(s_lat) * np.cos(lon - s_lon)
        dist = np.degrees(np.arccos(np.clip(cosd, -1, 1)))
        ring = (dist > remove_sun) & (dist < remove_sun * 1.6) & (yy < h // 2)
        fill = np.median(img[ring], axis=0) if ring.any() else img[: h // 2].mean(axis=(0, 1))
        k = np.clip((remove_sun - dist) / (remove_sun * 0.6), 0, 1)[..., None] ** 0.7
        img = img * (1 - k) + np.minimum(img, fill) * k
    grey = img.mean(axis=2, keepdims=True)
    img = grey + (img - grey) * saturation
    img = np.clip(img, 0, 1) ** gamma * np.asarray(tint, np.float32)
    cache.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out)
    return out


def render_settings(cfg: dict) -> dict:
    ex, fog, tm = cfg["exposure"], cfg["fog"], cfg["tonemap"]
    s = {
        "/rtx/directLighting/domeLight/denoisingTechnique": 0,
        "/rtx/post/tonemap/op": TONEMAP[tm["op"]],
        "/rtx/post/tonemap/filmIso": float(ex["iso"] * 2.0 ** ex["ev"]),
        "/rtx/post/tonemap/cameraShutter": float(ex["shutter"]),
        "/rtx/post/tonemap/fNumber": float(ex["fnumber"]),
        "/rtx/post/tonemap/whitepoint": list(map(float, tm["whitepoint"])),
        "/rtx/post/histogram/enabled": bool(ex["auto"]),
        "/rtx/post/histogram/useExposureClamping": True,
        "/rtx/post/histogram/minEV": float(ex["min_ev"]),
        "/rtx/post/histogram/maxEV": float(ex["max_ev"]),
        "/rtx/post/histogram/whiteScale": float(ex["white_scale"]),
        "/rtx/post/histogram/tau": float(ex["tau"]),
        "/rtx/post/colorcorr/enabled": True,
        "/rtx/post/colorcorr/saturation": [float(tm["saturation"])] * 3,
        "/rtx/post/colorcorr/contrast": [float(tm["contrast"])] * 3,
        "/rtx/post/colorcorr/gain": list(map(float, tm["gain"])),
        "/rtx/post/colorcorr/gamma": [float(tm["gamma"])] * 3,
        "/rtx/post/colorcorr/offset": [float(tm["offset"])] * 3,
        "/rtx/shadows/enabled": bool(cfg["sun"]["shadows"]),
        "/rtx/fog/enabled": bool(fog["enabled"]),
        "/rtx/fog/fogColor": list(map(float, fog["color"])),
        "/rtx/fog/fogColorIntensity": float(fog["intensity"]),
        "/rtx/fog/fogStartDist": float(fog["start"]),
        "/rtx/fog/fogEndDist": float(fog["end"]),
        "/rtx/fog/fogDistanceDensity": float(fog["density"]),
        "/rtx/fog/fogZup/enabled": True,
        "/rtx/fog/fogStartHeight": float(fog["height"]),
        "/rtx/fog/fogHeightDensity": float(fog["height_density"]),
        "/rtx/fog/fogHeightFalloff": float(fog["height_falloff"]),
    }
    s.update(cfg.get("rtx", {}))
    return s


class Look:
    def __init__(self, stage, cfg: dict, world_dir: Path | None, cache: Path | None) -> None:
        self.stage, self.cfg = stage, cfg
        self.world_dir = Path(world_dir) if world_dir else None
        self.cache = Path(cache) if cache else None
        self.dome_rotation = 0.0
        self.sun_dir = sun_vector(45.0, 220.0)

    def apply(self) -> "Look":
        import carb

        settings = carb.settings.get_settings()
        for key, value in render_settings(self.cfg).items():
            settings.set(key, value)
        self.sky()
        self.sun()
        self.materials()
        return self

    def update(self, dt: float) -> None:
        drift = float(self.cfg["sky"]["drift_deg_per_s"])
        if drift:
            self.dome_rotation = (self.dome_rotation + drift * dt) % 360.0
            self.set_dome_xform()

    def pano(self) -> Path | None:
        name = self.cfg["sky"]["texture"]
        if not name:
            return None
        p = Path(name)
        if not p.is_absolute() and self.world_dir is not None:
            p = self.world_dir / p
        return p if p.exists() else None

    def sky(self) -> None:
        from pxr import Gf, Sdf, UsdGeom, UsdLux

        sky = self.cfg["sky"]
        dome = UsdLux.DomeLight.Define(self.stage, SKY)
        src = self.pano()
        sun = self.cfg["sun"]
        elevation, azimuth = float(sun["elevation"]), sun["azimuth"]
        if src is not None:
            from PIL import Image

            u, v, _ = pano_sun(np.asarray(Image.open(src).convert("RGB").resize((PANO_W, PANO_W // 2))))
            pano_elev = 90.0 - v * 180.0
            if azimuth == "pano":
                azimuth = float(sky["pano_azimuth"])
            if sun["elevation"] == "pano":
                elevation = pano_elev
            self.dome_rotation = dome_rotation_for(u, float(azimuth), float(sky["latlong_offset"]), float(sky["latlong_sign"]))
            tex = src
            if self.cache is not None and (sky["remove_sun_deg"] or sky["saturation"] != 1 or sky["gamma"] != 1 or sky["tint"] != [1, 1, 1]):
                tex = sky_variant(src, self.cache, float(sky["remove_sun_deg"]), float(sky["saturation"]), float(sky["gamma"]), sky["tint"])
            dome.CreateTextureFileAttr().Set(Sdf.AssetPath(str(tex)))
            dome.CreateTextureFormatAttr().Set(UsdLux.Tokens.latlong)
        else:
            azimuth = 220.0 if azimuth == "pano" else float(azimuth)
            elevation = 45.0 if elevation == "pano" else elevation
            dome.CreateTextureFileAttr().Set(Sdf.AssetPath(""))
        self.elevation, self.azimuth = float(elevation), float(azimuth)
        dome.CreateIntensityAttr().Set(float(sky["intensity"]))
        dome.CreateExposureAttr().Set(0.0)
        dome.CreateColorAttr().Set(Gf.Vec3f(*sky["color"]))
        prim = dome.GetPrim()
        prim.CreateAttribute("visibleInPrimaryRay", Sdf.ValueTypeNames.Bool).Set(bool(sky["visible"]))
        self.set_dome_xform()
        UsdGeom.Imageable(prim).MakeVisible()

    def set_dome_xform(self) -> None:
        from pxr import Gf

        m = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), self.dome_rotation))
        set_transform(self.stage.GetPrimAtPath(SKY), m)

    def sun(self) -> None:
        from pxr import Gf, UsdLux

        sun = self.cfg["sun"]
        light = UsdLux.DistantLight.Define(self.stage, SUN)
        light.CreateIntensityAttr().Set(float(sun["intensity"]))
        light.CreateAngleAttr().Set(float(sun["angle"]))
        light.CreateColorAttr().Set(Gf.Vec3f(*sun["color"]))
        light.CreateExposureAttr().Set(0.0)
        self.sun_dir = sun_vector(self.elevation, self.azimuth)
        z = self.sun_dir
        x = np.cross([0.0, 0.0, 1.0], z)
        x = x / np.linalg.norm(x) if np.linalg.norm(x) > 1e-9 else np.array([1.0, 0.0, 0.0])
        m = np.eye(4)
        m[:3, :3] = np.column_stack([x, np.cross(z, x), z])
        set_transform(light.GetPrim(), Gf.Matrix4d(m.T.tolist()))

    def materials(self) -> None:
        mats = self.cfg["materials"]
        tune_preview(self.stage, SCAN, mats["scan"])
        for key, root in (("truck", TRUCKS), ("excavator", EQUIPMENT)):
            tune_preview(self.stage, root, mats[key]["default"], mats[key].get("by_name", {}))
        self.surround(mats["surround"])

    def surround(self, cfg: dict) -> None:
        from pxr import Sdf, Usd, UsdGeom, UsdShade

        prim = self.stage.GetPrimAtPath(SURROUND)
        if not prim:
            return
        meshes = [p for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh) and UsdGeom.PrimvarsAPI(p).HasPrimvar("displayColor")]
        if not meshes:
            return
        mode = cfg["mode"]
        if mode == "unbound":
            for p in meshes:
                UsdShade.MaterialBindingAPI.Apply(p).UnbindAllBindings()
            return
        path = f"{LOOKS}/surround"
        mat = UsdShade.Material.Define(self.stage, path)
        if mode == "mdl":
            shader = UsdShade.Shader.Define(self.stage, path + "/Shader")
            shader.SetSourceAsset(Sdf.AssetPath(str(Path(__file__).with_name("vertex_colour.mdl"))), "mdl")
            shader.SetSourceAssetSubIdentifier("vertex_colour", "mdl")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(cfg["roughness"]))
            shader.CreateInput("gain", Sdf.ValueTypeNames.Float).Set(float(cfg["gain"]))
            out = shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
            for name in ("surface", "displacement", "volume"):
                mat.CreateOutput(f"mdl:{name}", Sdf.ValueTypeNames.Token).ConnectToSource(out)
        else:
            shader = UsdShade.Shader.Define(self.stage, path + "/Surface")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(cfg["roughness"]))
            shader.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
            shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set((0.02, 0.02, 0.02))
            reader = UsdShade.Shader.Define(self.stage, path + "/Color")
            reader.CreateIdAttr("UsdPrimvarReader_float3")
            reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("displayColor")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(reader.ConnectableAPI(), "result")
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        for p in meshes:
            UsdShade.MaterialBindingAPI.Apply(p).Bind(mat, UsdShade.Tokens.strongerThanDescendants)


def set_transform(prim, matrix) -> None:
    from pxr import UsdGeom

    xf = UsdGeom.Xformable(prim)
    attr = prim.GetAttribute("xformOp:transform")
    xf.ClearXformOpOrder()
    op = UsdGeom.XformOp(attr) if attr else xf.AddTransformOp()
    op.Set(matrix)
    xf.SetXformOpOrder([op])


def tune_preview(stage, root: str, cfg: dict, by_name: dict | None = None) -> None:
    from pxr import Gf, Sdf, Usd

    top = stage.GetPrimAtPath(root)
    if not top:
        return
    for prim in Usd.PrimRange(top):
        if prim.GetTypeName() != "Shader":
            continue
        sid = prim.GetAttribute("info:id").Get()
        c = merge(cfg, (by_name or {}).get(prim.GetParent().GetName(), {}))
        if sid == "UsdUVTexture" and "tint" in c:
            prim.CreateAttribute("inputs:scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*c["tint"], 1.0))
        if sid != "UsdPreviewSurface":
            continue
        if "roughness" in c:
            prim.CreateAttribute("inputs:roughness", Sdf.ValueTypeNames.Float).Set(float(c["roughness"]))
        if "metallic" in c:
            prim.CreateAttribute("inputs:metallic", Sdf.ValueTypeNames.Float).Set(float(c["metallic"]))
        if "specular" in c:
            s = float(c["specular"])
            prim.CreateAttribute("inputs:useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
            prim.CreateAttribute("inputs:specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(s, s, s))
        if "clearcoat" in c:
            prim.CreateAttribute("inputs:clearcoat", Sdf.ValueTypeNames.Float).Set(float(c["clearcoat"]))
            prim.CreateAttribute("inputs:clearcoatRoughness", Sdf.ValueTypeNames.Float).Set(float(c.get("clearcoat_roughness", 0.1)))


def apply_look(stage, preset: str = "day", world_dir: str | Path | None = None, cache: str | Path | None = None, **overrides) -> Look:
    return Look(stage, load_preset(preset, **overrides), world_dir, cache).apply()
