import hashlib
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from .glb import Glb, read_glb, transform

TO_ISAAC = np.array([[1.0, 0, 0, 0], [0, 0, 1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])
VERSION = "2"


def to_isaac(p: np.ndarray) -> np.ndarray:
    return np.asarray(p, float)[..., [0, 2, 1]]


def isaac_matrix(rotation: np.ndarray, position: np.ndarray) -> Gf.Matrix4d:
    s = TO_ISAAC[:3, :3]
    m = np.eye(4)
    m[:3, :3] = s @ rotation @ s
    m[:3, 3] = s @ position
    return Gf.Matrix4d(m.T.tolist())


def camera_matrix(rotation: np.ndarray, position: np.ndarray) -> Gf.Matrix4d:
    s = TO_ISAAC[:3, :3]
    m = np.eye(4)
    m[:3, :3] = np.column_stack([s @ rotation[:, 0], s @ rotation[:, 1], -(s @ rotation[:, 2])])
    m[:3, 3] = s @ position
    return Gf.Matrix4d(m.T.tolist())


def sun_matrix(elevation: float, azimuth: float) -> Gf.Matrix4d:
    e, a = np.radians(elevation), np.radians(azimuth)
    z = np.array([np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)])
    x = np.cross([0.0, 0.0, 1.0], z)
    x = x / np.linalg.norm(x) if np.linalg.norm(x) > 1e-9 else np.array([1.0, 0, 0])
    m = np.eye(4)
    m[:3, :3] = np.column_stack([x, np.cross(z, x), z])
    return Gf.Matrix4d(m.T.tolist())


def cache_key(src: Path, matrix: np.ndarray) -> str:
    st = src.stat()
    h = hashlib.sha1(f"{VERSION}|{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{np.round(matrix, 9).tobytes().hex()}".encode())
    return h.hexdigest()[:12]


def write_mesh_usd(glb: Glb, out: Path, name: str, parts: dict[str, tuple[str, np.ndarray]] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, f"/{name}")
    stage.SetDefaultPrim(root.GetPrim())
    textures = {}
    for i, (data, mime) in enumerate(glb.images):
        if data:
            path = out.parent / f"{out.stem.removesuffix('.tmp')}_tex{i}.{'jpg' if 'jpeg' in mime else 'png'}"
            path.write_bytes(data)
            textures[i] = path.name
    materials = []
    for i, m in enumerate(glb.materials):
        mat = UsdShade.Material.Define(stage, f"/{name}/Looks/m{i}")
        shader = UsdShade.Shader.Define(stage, f"/{name}/Looks/m{i}/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
        if m.image is not None and m.image in textures:
            st = UsdShade.Shader.Define(stage, f"/{name}/Looks/m{i}/st")
            st.CreateIdAttr("UsdPrimvarReader_float2")
            st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            tex = UsdShade.Shader.Define(stage, f"/{name}/Looks/m{i}/tex")
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures[m.image])
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
            tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*m.color))
            diffuse.ConnectToSource(tex.ConnectableAPI(), "rgb")
        else:
            diffuse.Set(Gf.Vec3f(*m.color[:3]))
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        materials.append(mat)
    vertex_colour = None
    if any(p.colors is not None and p.material is None for p in glb.primitives):
        vertex_colour = UsdShade.Material.Define(stage, f"/{name}/Looks/vertex_colour")
        shader = UsdShade.Shader.Define(stage, f"/{name}/Looks/vertex_colour/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        reader = UsdShade.Shader.Define(stage, f"/{name}/Looks/vertex_colour/Color")
        reader.CreateIdAttr("UsdPrimvarReader_float3")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(reader.ConnectableAPI(), "result")
        vertex_colour.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    for rel, offset in sorted((parts or {}).values(), key=lambda v: v[0].count("/")):
        UsdGeom.Xform.Define(stage, f"/{name}/{rel}").AddTranslateOp().Set(Gf.Vec3d(*map(float, offset)))
    for k, p in enumerate(glb.primitives):
        prefix = f"/{name}/{parts[p.node][0]}" if parts and p.node in parts else f"/{name}"
        mesh = UsdGeom.Mesh.Define(stage, f"{prefix}/mesh_{k}")
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(p.points.astype(np.float32)))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(p.triangles), 3, np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(p.triangles.reshape(-1).astype(np.int32)))
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        lo, hi = p.points.min(axis=0), p.points.max(axis=0)
        mesh.CreateExtentAttr([Gf.Vec3f(*lo.tolist()), Gf.Vec3f(*hi.tolist())])
        if p.normals is not None:
            mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(p.normals.astype(np.float32)))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        api = UsdGeom.PrimvarsAPI(mesh)
        if p.uvs is not None:
            uv = p.uvs.copy()
            uv[:, 1] = 1 - uv[:, 1]
            api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray.FromNumpy(uv.astype(np.float32)))
        if p.colors is not None:
            api.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex).Set(
                Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(p.colors[:, :3], np.float32))
            )
        if p.material is not None and p.material < len(materials):
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(materials[p.material])
            if glb.materials[p.material].double_sided:
                mesh.CreateDoubleSidedAttr(True)
        elif p.colors is not None:
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(vertex_colour)
    stage.GetRootLayer().Save()
    return out


def convert(src: Path, cache: Path, name: str, matrix: np.ndarray, tint: list[float] | None = None) -> Path:
    full = TO_ISAAC @ matrix
    key = cache_key(src, full if tint is None else np.vstack([full, [list(tint) + [0.0] * (4 - len(tint))]]))
    out = cache / f"{name}_{key}.usdc"
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.usdc")
    glb = transform(read_glb(src), full)
    if tint is not None:
        for p in glb.primitives:
            if p.colors is not None:
                p.colors = p.colors.copy()
                p.colors[:, :3] *= np.asarray(tint[:3], np.float32)
    write_mesh_usd(glb, tmp, name)
    tmp.replace(out)
    return out


def convert_rig(src: Path, cache: Path, name: str, matrix: np.ndarray, joints: tuple[str, ...]) -> Path:
    full = TO_ISAAC @ matrix
    out = cache / f"{name}_{cache_key(src, full)}.usdc"
    if out.exists():
        return out
    raw = read_glb(src)
    glb = transform(raw, full)
    origin = {j: (full @ raw.nodes[j][0][:, 3])[:3] for j in joints if j in raw.nodes}
    parts = {}
    for j in origin:
        chain, parent = [j], raw.nodes[j][1]
        while parent:
            if parent in origin:
                chain.insert(0, parent)
            parent = raw.nodes[parent][1]
        up = chain[-2] if len(chain) > 1 else None
        parts[j] = ("/".join(chain), origin[j] - (origin[up] if up else 0))
    for p in glb.primitives:
        if p.node in parts:
            p.points = p.points - origin[p.node]
    tmp = out.with_suffix(".tmp.usdc")
    write_mesh_usd(glb, tmp, name, parts)
    tmp.replace(out)
    return out
