"""GLB -> USD conversion with trimesh + pure pxr (usd-core). No Isaac Sim needed.

Every vertex is baked into the target frame on load (`M4`, a 4x4 column-vector matrix applied after the glTF node
transforms), so the written USD needs no extra xformOps except where a rig needs pivots (excavator joints, truck bed).

Materials: glTF PBR -> UsdPreviewSurface (diffuseColor texture/factor, metallic, roughness, opacity). Vertex colours
(COLOR_0, e.g. surround.glb) -> primvars:displayColor with no material bound, which RTX/Storm render directly.
UVs: trimesh already flips glTF's top-left V to the bottom-left (OpenGL/USD) convention on load, so st = uv.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np
import trimesh
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt


def sanitize(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name)) or "_"
    return "_" + s if s[0].isdigit() else s


@dataclass
class Part:
    node: str                      # glTF node name that instances the geometry
    geom: str                      # geometry name
    ancestors: list[str]           # node names from the node's parent up to the root
    points: np.ndarray             # (N, 3) baked into the target frame
    faces: np.ndarray              # (F, 3) int
    normals: np.ndarray | None = None
    uv: np.ndarray | None = None
    colors: np.ndarray | None = None   # (N, 3) float 0..1 vertex colours
    material: object | None = None
    material_name: str = ""


@dataclass
class GlbScene:
    path: str
    parts: list[Part] = field(default_factory=list)
    node_world: dict = field(default_factory=dict)  # node name -> 4x4 baked (M4 @ glTF world)

    def node_origin(self, name: str) -> np.ndarray:
        return self.node_world[name][:3, 3].copy()

    def bounds(self, parts: list[Part] | None = None) -> np.ndarray:
        ps = np.concatenate([p.points for p in (parts or self.parts)])
        return np.stack([ps.min(0), ps.max(0)])


def load_glb(path: str, M4: np.ndarray | None = None, normals: bool = True) -> GlbScene:
    """Load a GLB with node hierarchy and bake every vertex with M4 @ T_node_world."""
    M4 = np.eye(4) if M4 is None else np.asarray(M4, dtype=np.float64)
    scene = trimesh.load(path, force="scene", process=False)
    g = scene.graph
    parent: dict[str, str] = {}
    for a, b, _d in g.to_edgelist():
        parent[b] = a
    out = GlbScene(path=path)
    for node in g.nodes:
        try:
            T, _ = g.get(frame_to=node)
        except Exception:
            continue
        out.node_world[node] = M4 @ T
    R = None
    for node in g.nodes_geometry:
        T, gname = g[node]
        geom = scene.geometry[gname]
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        Mf = M4 @ T
        v = np.asarray(geom.vertices, dtype=np.float64)
        pts = v @ Mf[:3, :3].T + Mf[:3, 3]
        part = Part(node=node, geom=gname, ancestors=[], points=pts,
                    faces=np.asarray(geom.faces, dtype=np.int64))
        anc, cur = [], parent.get(node)
        while cur is not None:
            anc.append(cur)
            cur = parent.get(cur)
        part.ancestors = anc
        if normals:
            R = Mf[:3, :3]
            n = np.asarray(geom.vertex_normals, dtype=np.float64) @ np.linalg.inv(R).T  # normal matrix
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            part.normals = n
        vis = geom.visual
        if getattr(vis, "kind", None) == "texture" or isinstance(vis, trimesh.visual.TextureVisuals):
            if getattr(vis, "uv", None) is not None and len(vis.uv) == len(v):
                part.uv = np.asarray(vis.uv, dtype=np.float64)
            part.material = getattr(vis, "material", None)
            part.material_name = getattr(part.material, "name", None) or ""
        elif getattr(vis, "kind", None) == "vertex":
            vc = np.asarray(vis.vertex_colors)
            if len(vc) == len(v):
                part.colors = vc[:, :3].astype(np.float64) / 255.0
        elif getattr(vis, "kind", None) == "face":
            fc = np.asarray(vis.face_colors)[:, :3].astype(np.float64) / 255.0
            part.colors = None
            part.material = None
            part.material_name = ""
            part._face_color = fc.mean(0)  # type: ignore[attr-defined]
        out.parts.append(part)
    return out


class UsdMeshWriter:
    """Writes Parts as UsdGeom.Mesh prims with UsdPreviewSurface materials into a stage."""

    def __init__(self, stage: Usd.Stage, layer_dir: str, tex_dir: str, looks_path: str):
        self.stage = stage
        self.layer_dir = layer_dir
        self.tex_dir = tex_dir
        self.looks_path = Sdf.Path(looks_path)
        self._materials: dict[int, UsdShade.Material] = {}
        self._used_names: set[str] = set()
        os.makedirs(tex_dir, exist_ok=True)

    def _unique(self, base: str) -> str:
        n, i = sanitize(base), 1
        name = n
        while name in self._used_names:
            i += 1
            name = f"{n}_{i}"
        self._used_names.add(name)
        return name

    def _save_texture(self, img, base: str) -> str:
        from PIL import Image  # noqa: F401  (trimesh hands us PIL images)
        img = img.convert("RGBA") if img.mode not in ("RGB", "RGBA", "L") else img
        has_alpha = img.mode == "RGBA" and img.getextrema()[3][0] < 255
        if has_alpha:
            fn = base + ".png"
            img.save(os.path.join(self.tex_dir, fn))
        else:
            fn = base + ".jpg"
            img.convert("RGB").save(os.path.join(self.tex_dir, fn), quality=92)
        return os.path.relpath(os.path.join(self.tex_dir, fn), self.layer_dir).replace(os.sep, "/")

    def material_for(self, mat, display_name: str = "") -> UsdShade.Material | None:
        if mat is None:
            return None
        key = id(mat)
        if key in self._materials:
            return self._materials[key]
        name = self._unique(getattr(mat, "name", None) or display_name or "Material")
        mpath = self.looks_path.AppendChild(name)
        material = UsdShade.Material.Define(self.stage, mpath)
        shader = UsdShade.Shader.Define(self.stage, mpath.AppendChild("PreviewSurface"))
        shader.CreateIdAttr("UsdPreviewSurface")
        factor = getattr(mat, "baseColorFactor", None)
        if factor is None:
            factor = getattr(mat, "diffuse", None)
        f = np.array([255, 255, 255, 255] if factor is None else factor, dtype=np.float64)
        if f.max() > 1.0:
            f = f / 255.0
        if f.shape[0] == 3:
            f = np.append(f, 1.0)
        metallic = getattr(mat, "metallicFactor", None)
        rough = getattr(mat, "roughnessFactor", None)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(0.0 if metallic is None else metallic))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(0.8 if rough is None else rough))
        if f[3] < 0.999:
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(f[3]))
        tex = getattr(mat, "baseColorTexture", None)
        if tex is None:
            tex = getattr(mat, "image", None)
        if tex is not None:
            rel = self._save_texture(tex, name)
            st = UsdShade.Shader.Define(self.stage, mpath.AppendChild("st"))
            st.CreateIdAttr("UsdPrimvarReader_float2")
            st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            uvtex = UsdShade.Shader.Define(self.stage, mpath.AppendChild("diffuseTexture"))
            uvtex.CreateIdAttr("UsdUVTexture")
            uvtex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(rel)
            uvtex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            uvtex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
            uvtex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
            uvtex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*[float(x) for x in f]))
            uvtex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
            uvtex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                uvtex.ConnectableAPI(), "rgb")
        else:
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[float(x) for x in f[:3]]))
        shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        self._materials[key] = material
        return material

    def add_mesh(self, parent: str, name: str, part: Part, offset=(0.0, 0.0, 0.0), *,
                 collision: bool = False, materials: bool = True, normals: bool = True,
                 double_sided: bool = False) -> UsdGeom.Mesh:
        path = Sdf.Path(parent).AppendChild(sanitize(name))
        mesh = UsdGeom.Mesh.Define(self.stage, path)
        pts = (part.points - np.asarray(offset, dtype=np.float64)).astype(np.float32)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(part.faces), 3, dtype=np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(part.faces.reshape(-1).astype(np.int32)))
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, pts.min(0))), Gf.Vec3f(*map(float, pts.max(0)))]))
        if double_sided:
            mesh.CreateDoubleSidedAttr(True)
        if normals and part.normals is not None:
            mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(part.normals.astype(np.float32)))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        pv = UsdGeom.PrimvarsAPI(mesh)
        if materials and part.uv is not None:
            st = pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
            st.Set(Vt.Vec2fArray.FromNumpy(part.uv.astype(np.float32)))
        if part.colors is not None:
            dc = pv.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)
            dc.Set(Vt.Vec3fArray.FromNumpy(part.colors.astype(np.float32)))
        elif hasattr(part, "_face_color"):
            mesh.CreateDisplayColorAttr([Gf.Vec3f(*map(float, part._face_color))])
        if materials and part.material is not None:
            m = self.material_for(part.material, part.material_name)
            if m is not None:
                UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(m)
        if collision:
            apply_static_collision(mesh.GetPrim())
        return mesh


def apply_static_collision(prim: Usd.Prim) -> None:
    """Static triangle-mesh collider (no rigid body => static in PhysX)."""
    UsdPhysics.CollisionAPI.Apply(prim)
    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr(UsdPhysics.Tokens.none)


def new_layer_stage(path: str, root: str, kind: str = "component") -> tuple[Usd.Stage, Usd.Prim]:
    """Create a Z-up, metre stage with an Xform default prim."""
    if os.path.exists(path):
        os.remove(path)
    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    prim = UsdGeom.Xform.Define(stage, "/" + root).GetPrim()
    stage.SetDefaultPrim(prim)
    Usd.ModelAPI(prim).SetKind(kind)
    return stage, prim


def convert_static(glb: str, out_usd: str, root: str, M4: np.ndarray, *, collision: bool,
                   materials: bool = True, normals: bool = True, double_sided: bool = False,
                   tex_dir: str | None = None) -> dict:
    """World-frame static scenery/collider GLB -> one USD layer (one Mesh per glTF geometry node)."""
    sc = load_glb(glb, M4, normals=normals)
    stage, rootp = new_layer_stage(out_usd, root)
    layer_dir = os.path.dirname(os.path.abspath(out_usd))
    w = UsdMeshWriter(stage, layer_dir, tex_dir or os.path.join(layer_dir, "textures"), f"/{root}/Looks")
    names: set[str] = set()
    tris = 0
    for p in sc.parts:
        n = sanitize(p.node)
        while n in names:
            n += "_"
        names.add(n)
        w.add_mesh(f"/{root}", n, p, collision=collision, materials=materials, normals=normals,
                   double_sided=double_sided)
        tris += len(p.faces)
    stage.GetRootLayer().Save()
    b = sc.bounds()
    return {"usd": out_usd, "meshes": len(sc.parts), "tris": tris, "bounds": b.tolist()}
