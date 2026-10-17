from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

from . import geometry as G
from .model import Model

ROOT = "/World/Material"
DEFAULT_COLOUR = (0.20, 0.15, 0.11)
ROCK_DARKEN = 0.75
DUST_COLOUR = (0.55, 0.47, 0.38)
DUST_OPACITY = 0.14
PILE_RINGS, PILE_SEGMENTS = 8, 24
ROCK_SEEDS = (3, 7, 11)
HIDDEN = (0.0, 0.0, -1000.0)


def gf(m: np.ndarray) -> Gf.Matrix4d:
    return Gf.Matrix4d(np.asarray(m, float).T.tolist())


def vec3(a: np.ndarray) -> Vt.Vec3fArray:
    return Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(a.reshape(-1, 3), np.float32))


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def face_colour(world_dir: str | Path, x: float, z: float, radius: float = 6.0):
    import json

    from PIL import Image

    d = Path(world_dir)
    raw = json.loads((d / "world.json").read_text())
    img = np.asarray(Image.open(d / "minimap.png").convert("RGB"), float) / 255
    (ox, oz), (sx, sz) = raw["origin"], raw["size_m"]
    h, w = img.shape[:2]
    u = (x - ox) / sx * w
    v = (1 - (z - oz) / sz) * h
    r = max(1, int(radius / sx * w))
    patch = img[max(0, int(v) - r): int(v) + r + 1, max(0, int(u) - r): int(u) + r + 1].reshape(-1, 3)
    if not len(patch):
        return DEFAULT_COLOUR
    c = srgb_to_linear(np.median(patch, axis=0))
    return tuple(float(v) for v in c / max(c.mean(), 1e-6) * np.mean(DEFAULT_COLOUR))


def material(stage, path: str, colour, roughness: float = 0.95, opacity: float = 1.0, vertex: bool = False) -> UsdShade.Material:
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    if opacity < 1:
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
    diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    if vertex:
        reader = UsdShade.Shader.Define(stage, path + "/Color")
        reader.CreateIdAttr("UsdPrimvarReader_float3")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
        diffuse.ConnectToSource(reader.ConnectableAPI(), "result")
    else:
        diffuse.Set(Gf.Vec3f(*colour))
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def grid_mesh(stage, path: str, points: np.ndarray, faces: np.ndarray, mat, colour, seed: int) -> UsdGeom.Mesh:
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(vec3(points))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(faces), faces.shape[1], np.int32)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces.reshape(-1).astype(np.int32)))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    rng = np.random.default_rng(seed)
    n = len(points.reshape(-1, 3))
    tint = np.clip(np.asarray(colour)[None] * rng.uniform(0.75, 1.2, (n, 1)) * rng.uniform(0.93, 1.07, (n, 3)), 0, 1)
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex).Set(vec3(tint))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    mesh.CreateExtentAttr([Gf.Vec3f(-1e3, -1e3, -1e3), Gf.Vec3f(1e3, 1e3, 1e3)])
    return mesh


class BucketMaterial:
    def __init__(self, stage, excavators_cfg: list[dict], trucks_cfg: list[dict], config: dict | None = None,
                 sites: list[tuple[float, float, float]] | None = None) -> None:
        self.stage = stage
        self.model = Model(G.bucket_shape(), G.bed_shape(), excavators_cfg, trucks_cfg, config, sites)
        UsdGeom.Xform.Define(stage, ROOT)
        m = self.model
        s, b = m.bucket, m.bed
        self.bucket_faces = G.grid_faces(G.BUCKET_NZ, G.BUCKET_NX)
        self.bed_faces = G.grid_faces(*b.shape)
        self.buckets, self.bucket_ops, self.piles, self.pile_ops = [], [], [], []
        colours = []
        for i, e in enumerate(excavators_cfg):
            name = e["id"].replace("-", "_")
            colour = tuple(e.get("colour") or DEFAULT_COLOUR)
            colours.append(colour)
            mat = material(stage, f"{ROOT}/Looks/{name}", colour, vertex=True)
            xf = UsdGeom.Xform.Define(stage, f"{ROOT}/{name}_bucket")
            self.bucket_ops.append(xf.AddTransformOp())
            pts = m.bucket_points()[i].reshape(G.BUCKET_NZ, G.BUCKET_NX, 3)
            self.buckets.append(grid_mesh(stage, f"{ROOT}/{name}_bucket/heap", pts, self.bucket_faces, mat, colour, i))
            pxf = UsdGeom.Xform.Define(stage, f"{ROOT}/{name}_pile")
            self.pile_ops.append(pxf.AddTransformOp())
            ring = m.pile_mesh(PILE_RINGS, PILE_SEGMENTS)[i]
            self.piles.append(grid_mesh(stage, f"{ROOT}/{name}_pile/mound", ring, self.pile_faces(), mat, colour, 100 + i))
        self.colour = colours[0] if colours else DEFAULT_COLOUR
        bed_mat = material(stage, f"{ROOT}/Looks/bed", self.colour, vertex=True)
        self.beds, self.bed_ops = [], []
        for k, t in enumerate(trucks_cfg):
            name = t["id"].replace("-", "_")
            if t.get("prim"):
                path = f"{t['prim']}/material_load"
                UsdGeom.Xform.Define(stage, path)
                self.bed_ops.append(None)
            else:
                path = f"{ROOT}/{name}_load"
                self.bed_ops.append(UsdGeom.Xform.Define(stage, path).AddTransformOp())
            self.beds.append(grid_mesh(stage, path + "/mound", m.bed_points()[k], self.bed_faces, bed_mat, self.colour, 200 + k))
        self.instancer = self.make_instancer(np.array(self.colour) * ROCK_DARKEN)
        self.dust = self.make_dust() if m.cfg.max_dust else None
        self.last_bed = np.full(len(trucks_cfg), -1.0)
        self.last_heap = [None] * len(excavators_cfg)
        self.last_pile = np.full(len(excavators_cfg), -1.0)
        self.chunks_visible = True

    def pile_faces(self) -> np.ndarray:
        r, s = PILE_RINGS, PILE_SEGMENTS
        i = np.arange(r - 1)[:, None] * s + np.arange(s)[None]
        j = np.arange(r - 1)[:, None] * s + (np.arange(s)[None] + 1) % s
        return np.stack([i, i + s, j + s, j], -1).reshape(-1, 4)

    def make_instancer(self, colour) -> UsdGeom.PointInstancer:
        stage = self.stage
        inst = UsdGeom.PointInstancer.Define(stage, f"{ROOT}/chunks")
        mat = material(stage, f"{ROOT}/Looks/rock", tuple(colour), roughness=0.9)
        protos = []
        for k, seed in enumerate(ROCK_SEEDS):
            p, f, n = G.rock(seed)
            mesh = UsdGeom.Mesh.Define(stage, f"{ROOT}/chunks/protos/rock{k}")
            mesh.CreatePointsAttr(vec3(p))
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(f), 3, np.int32)))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(f.reshape(-1).astype(np.int32)))
            mesh.CreateNormalsAttr(vec3(n))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
            mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
            protos.append(mesh.GetPath())
        inst.CreatePrototypesRel().SetTargets(protos)
        pos, quat, scale = self.model.instances()
        self.count = len(pos)
        inst.CreateProtoIndicesAttr(Vt.IntArray.FromNumpy((np.arange(self.count) % len(protos)).astype(np.int32)))
        inst.CreatePositionsAttr(vec3(pos))
        inst.CreateScalesAttr(vec3(np.repeat(scale[:, None], 3, 1)))
        inst.CreateOrientationsAttr(self.quats(quat))
        inst.CreateExtentAttr([Gf.Vec3f(-1e4, -1e4, -1e4), Gf.Vec3f(1e4, 1e4, 1e4)])
        return inst

    def make_dust(self) -> UsdGeom.PointInstancer:
        stage = self.stage
        inst = UsdGeom.PointInstancer.Define(stage, f"{ROOT}/dust")
        mat = material(stage, f"{ROOT}/Looks/dust", DUST_COLOUR, roughness=1.0, opacity=DUST_OPACITY)
        p, f, n = G.rock(1, level=2)
        mesh = UsdGeom.Mesh.Define(stage, f"{ROOT}/dust/protos/puff")
        mesh.CreatePointsAttr(vec3(p))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(f), 3, np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(f.reshape(-1).astype(np.int32)))
        mesh.CreateNormalsAttr(vec3(n))
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
        inst.CreatePrototypesRel().SetTargets([mesh.GetPath()])
        pos, size = self.model.dust()
        inst.CreateProtoIndicesAttr(Vt.IntArray.FromNumpy(np.zeros(len(pos), np.int32)))
        inst.CreatePositionsAttr(vec3(pos))
        inst.CreateScalesAttr(vec3(np.repeat(size[:, None], 3, 1)))
        inst.CreateExtentAttr([Gf.Vec3f(-1e4, -1e4, -1e4), Gf.Vec3f(1e4, 1e4, 1e4)])
        return inst

    @staticmethod
    def quats(q: np.ndarray) -> Vt.QuathArray:
        return Vt.QuathArray.FromNumpy(np.ascontiguousarray(q[:, [1, 2, 3, 0]], np.float16))

    def update(self, dt: float, excavators: list, trucks: list) -> None:
        m = self.model
        m.update(dt, excavators, trucks)
        heaps = m.bucket_points()
        for i in range(len(m.exc_ids)):
            self.bucket_ops[i].Set(gf(m.matrix[i]))
            if not np.array_equal(heaps[i], self.last_heap[i]):
                self.last_heap[i] = heaps[i].copy()
                self.buckets[i].GetPointsAttr().Set(vec3(heaps[i]))
            if not np.isnan(m.pile_pos[i, 0]) and m.pile[i] != self.last_pile[i]:
                self.last_pile[i] = m.pile[i]
                t = np.eye(4)
                t[:3, 3] = m.pile_pos[i]
                self.pile_ops[i].Set(gf(t))
                self.piles[i].GetPointsAttr().Set(vec3(m.pile_mesh(PILE_RINGS, PILE_SEGMENTS)[i]))
        pts = None
        for k in range(len(m.truck_ids)):
            if self.bed_ops[k] is not None:
                self.bed_ops[k].Set(gf(m.truck_matrix[k]))
            total = float(m.height[k].sum())
            if total != self.last_bed[k]:
                self.last_bed[k] = total
                pts = m.bed_points() if pts is None else pts
                self.beds[k].GetPointsAttr().Set(vec3(pts[k]))
        pos, quat, scale = m.instances()
        visible = bool(scale.any())
        if visible or self.chunks_visible:
            self.chunks_visible = visible
            pos = np.where(scale[:, None] > 0, pos, HIDDEN)
            self.instancer.GetPositionsAttr().Set(vec3(pos))
            self.instancer.GetScalesAttr().Set(vec3(np.repeat(scale[:, None], 3, 1)))
            self.instancer.GetOrientationsAttr().Set(self.quats(quat))
        if self.dust is None:
            return
        dpos, dsize = m.dust()
        self.dust.GetPositionsAttr().Set(vec3(np.where(dsize[:, None] > 0, dpos, HIDDEN)))
        self.dust.GetScalesAttr().Set(vec3(np.repeat(dsize[:, None], 3, 1)))

    def empty_truck(self, ident: str) -> None:
        self.model.empty_truck(ident)

    def load(self, ident: str) -> float:
        return self.model.load(ident)

    def tonnes(self, ident: str) -> float:
        return self.model.tonnes(ident)

    def meshes(self) -> list[tuple[str, np.ndarray, np.ndarray]]:
        m = self.model
        out = []
        tri = np.concatenate([self.bed_faces[:, [0, 1, 2]], self.bed_faces[:, [0, 2, 3]]])
        pts = m.bed_points()
        for k, ident in enumerate(m.truck_ids):
            if m.height[k].sum() <= 0:
                continue
            p = pts[k].reshape(-1, 3)
            mt = m.truck_matrix[k]
            out.append((ident, p @ mt[:3, :3].T + mt[:3, 3], tri))
        return out

    def aim_points(self) -> dict[str, tuple[float, float]]:
        m, b = self.model, self.model.bed
        centre = np.array([b.u.mean(), b.v.mean(), 0.0, 1.0])
        return {u: tuple((m.truck_matrix[k] @ centre)[:2]) for k, u in enumerate(m.under) if u}


def sites_from_world(world, radius: float = 30.0) -> list[tuple[float, float, float]]:
    return [(float(s.pos[0]), float(s.pos[2]), radius) for s in world.sites if s.type in ("crusher", "dump")]


__all__ = ["BucketMaterial", "face_colour", "sites_from_world"]
