from pathlib import Path

import numpy as np
from pxr import Gf, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

MATERIALS = {
    "rock": {"tex": "rock", "roughness": 0.9, "tint": (0.92, 0.88, 0.8)},
    "rock_warm": {"tex": "rock", "roughness": 0.9, "tint": (0.95, 0.86, 0.72)},
    "rock_grey": {"tex": "rock", "roughness": 0.9, "tint": (0.8, 0.8, 0.78)},
    "dirt": {"tex": "dirt", "roughness": 0.95, "tint": (1.0, 0.97, 0.92)},
    "cone_orange": {"color": (0.95, 0.28, 0.03), "roughness": 0.55},
    "reflective_white": {"color": (0.92, 0.92, 0.9), "roughness": 0.25},
    "rubber_black": {"color": (0.03, 0.03, 0.03), "roughness": 0.85},
    "galvanised": {"color": (0.62, 0.63, 0.64), "roughness": 0.45, "metallic": 1.0},
    "tank_white": {"color": (0.82, 0.82, 0.78), "roughness": 0.5, "metallic": 0.2},
    "steel_dark": {"color": (0.12, 0.12, 0.12), "roughness": 0.6, "metallic": 0.8},
}


def new_stage(path: Path, name: str) -> tuple[Usd.Stage, UsdGeom.Xform]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, f"/{name}")
    stage.SetDefaultPrim(root.GetPrim())
    Usd.ModelAPI(root.GetPrim()).SetKind(Kind.Tokens.component)
    return stage, root


def texture(stage, mat_path: str, shader: UsdShade.Shader, st: UsdShade.Shader, file: str, name: str, out: str,
            colour_space: str, scale=None, bias=None):
    tex = UsdShade.Shader.Define(stage, f"{mat_path}/{name}")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(file)
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(colour_space)
    tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
    if scale is not None:
        tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scale))
    if bias is not None:
        tex.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*bias))
    kind = Sdf.ValueTypeNames.Float3 if out == "rgb" else Sdf.ValueTypeNames.Float
    tex.CreateOutput(out, kind)
    return tex


def material(stage, root: str, name: str, spec: dict, tex_dir: str | None) -> UsdShade.Material:
    path = f"{root}/Looks/{name}"
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/Surface")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(spec.get("metallic", 0.0)))
    tex = spec.get("tex") or spec.get("files")
    if tex and tex_dir is not None:
        files = spec["files"] if "files" in spec else {k: f"{tex_dir}/{tex}_{k}.jpg" for k in ("diff", "nor", "rough")}
        st = UsdShade.Shader.Define(stage, f"{path}/st")
        st.CreateIdAttr("UsdPrimvarReader_float2")
        st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        st.CreateOutput("result", Sdf.ValueTypeNames.Float2)
        tint = (*spec.get("tint", (1, 1, 1)), 1.0)
        d = texture(stage, path, sh, st, files["diff"], "diff", "rgb", "sRGB", scale=tint)
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(d.ConnectableAPI(), "rgb")
        if files.get("nor"):
            n = texture(stage, path, sh, st, files["nor"], "nor", "rgb", "raw", scale=(2, 2, 2, 1), bias=(-1, -1, -1, 0))
            sh.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(n.ConnectableAPI(), "rgb")
        if files.get("rough"):
            r = texture(stage, path, sh, st, files["rough"], "rough", "r", "raw")
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(r.ConnectableAPI(), "r")
        else:
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(spec.get("roughness", 0.9)))
        if files.get("arm"):
            a = texture(stage, path, sh, st, files["arm"], "arm", "g", "raw")
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(a.ConnectableAPI(), "g")
    else:
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*spec.get("color", (0.5, 0.5, 0.5))))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(spec.get("roughness", 0.9)))
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def mesh(stage, path: str, v: np.ndarray, f: np.ndarray, uv: np.ndarray | None = None, normals: np.ndarray | None = None,
         mat: UsdShade.Material | None = None) -> UsdGeom.Mesh:
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(v, np.float32)))
    m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(f), 3, np.int32)))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.ascontiguousarray(f, np.int32).reshape(-1)))
    m.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    m.CreateExtentAttr([Gf.Vec3f(*v.min(0).tolist()), Gf.Vec3f(*v.max(0).tolist())])
    if normals is not None:
        m.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(normals, np.float32)))
        m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    if uv is not None:
        interp = UsdGeom.Tokens.faceVarying if len(uv) == 3 * len(f) else UsdGeom.Tokens.vertex
        UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, interp).Set(
            Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(uv, np.float32)))
    if mat is not None:
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    return m


def collider(stage, path: str, v: np.ndarray, f: np.ndarray, approximation: str = "convexHull") -> UsdGeom.Mesh:
    m = mesh(stage, path, v, f)
    m.CreatePurposeAttr(UsdGeom.Tokens.guide)
    UsdPhysics.CollisionAPI.Apply(m.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr(approximation)
    return m


def write_asset(path: Path, name: str, parts: list[dict], collision: tuple[np.ndarray, np.ndarray] | None,
                tex_dir: str | None = "textures", approximation: str = "convexHull", materials: dict | None = None,
                lods: dict[str, list[dict]] | None = None) -> Path:
    stage, root = new_stage(path, name)
    specs = {**MATERIALS, **(materials or {})}
    mats = {}
    for p in parts + [q for v in (lods or {}).values() for q in v]:
        key = p["material"]
        if key not in mats:
            mats[key] = material(stage, f"/{name}", key, specs[key], tex_dir)
    for p in parts:
        mesh(stage, f"/{name}/{p['name']}", p["v"], p["f"], p.get("uv"), p.get("normals"), mats[p["material"]])
    if lods:
        UsdGeom.Xform.Define(stage, f"/{name}/render")
        vset = root.GetPrim().GetVariantSets().AddVariantSet("lod")
        for key, lod_parts in lods.items():
            vset.AddVariant(key)
            vset.SetVariantSelection(key)
            with vset.GetVariantEditContext():
                for p in lod_parts:
                    mesh(stage, f"/{name}/render/{p['name']}", p["v"], p["f"], p.get("uv"), p.get("normals"), mats[p["material"]])
        vset.SetVariantSelection(next(iter(lods)))
    if collision is not None:
        collider(stage, f"/{name}/collision", *collision, approximation=approximation)
    stage.GetRootLayer().Save()
    return path
