import json
import math
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom

S = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])


def _rot(rot_deg) -> np.ndarray:
    a, b, c = (math.radians(v) for v in rot_deg)
    rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return ry @ rx @ rz


def _read(props_json) -> dict:
    return props_json if isinstance(props_json, dict) else json.loads(Path(props_json).read_text())


def resolve(prop: dict, assets_dir) -> tuple[Path | None, list[float]]:
    assets_dir = Path(assets_dir)
    path = assets_dir / prop["asset"]
    if path.exists():
        return path, prop["scale"]
    if prop.get("fallback") and (assets_dir / prop["fallback"]).exists():
        return assets_dir / prop["fallback"], prop.get("fallback_scale") or prop["scale"]
    return None, prop["scale"]


def world_matrix(prop: dict, scale) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = _rot(prop["rot_deg"]) @ np.diag(scale)
    m[:3, 3] = prop["pos"]
    return m


def isaac_matrix(prop: dict, scale) -> Gf.Matrix4d:
    m = np.eye(4)
    m[:3, :3] = S @ _rot(prop["rot_deg"]) @ np.diag(scale) @ S
    m[:3, 3] = S @ np.asarray(prop["pos"], float)
    return Gf.Matrix4d(m.T.tolist())


def add_props(stage: Usd.Stage, props_json, assets_dir, root: str = "/World/Props", lod: str = "lod0",
              instanceable: bool = True, berms: bool = True, kinds: set[str] | None = None) -> dict:
    data = _read(props_json)
    assets_dir = Path(assets_dir)
    UsdGeom.Xform.Define(stage, root)
    added, missing = 0, []
    for prop in data["props"]:
        if kinds is not None and prop["kind"] not in kinds:
            continue
        path, scale = resolve(prop, assets_dir)
        if path is None:
            missing.append(prop["asset"])
            continue
        xf = UsdGeom.Xform.Define(stage, f"{root}/{prop['kind']}/{prop['id']}")
        prim = xf.GetPrim()
        prim.GetReferences().AddReference(str(path.resolve()))
        vs = prim.GetVariantSets()
        if vs.HasVariantSet("lod") and lod in vs.GetVariantSet("lod").GetVariantNames():
            vs.GetVariantSet("lod").SetVariantSelection(lod)
        xf.AddTransformOp().Set(isaac_matrix(prop, scale))
        if not prop.get("collision", True):
            col = prim.GetChild("collision")
            if col:
                col.SetActive(False)
        prim.SetInstanceable(instanceable)
        added += 1
    berm_files = sorted({b["asset"] for b in data.get("berms", [])}) if berms else []
    for i, rel in enumerate(berm_files):
        path = assets_dir / rel
        if not path.exists():
            missing.append(rel)
            continue
        UsdGeom.Xform.Define(stage, f"{root}/Berms_{i}").GetPrim().GetReferences().AddReference(str(path.resolve()))
    return {"props": added, "berm_files": len(berm_files), "missing": sorted(set(missing))}


def _mesh_triangles(prim: Usd.Prim) -> tuple[np.ndarray, np.ndarray]:
    m = UsdGeom.Mesh(prim)
    v = np.asarray(m.GetPointsAttr().Get(), float)
    counts = np.asarray(m.GetFaceVertexCountsAttr().Get(), int)
    idx = np.asarray(m.GetFaceVertexIndicesAttr().Get(), int)
    if (counts == 3).all():
        return v, idx.reshape(-1, 3)
    tris, k = [], 0
    for c in counts:
        tris += [[idx[k], idx[k + j], idx[k + j + 1]] for j in range(1, c - 1)]
        k += c
    return v, np.asarray(tris, int).reshape(-1, 3)


def asset_triangles(path: Path, prim: str = "collision") -> tuple[np.ndarray, np.ndarray]:
    stage = Usd.Stage.Open(str(path))
    root = stage.GetDefaultPrim()
    target = root.GetChild(prim) if prim else None
    prims = [target] if target and target.IsA(UsdGeom.Mesh) else [p for p in Usd.PrimRange(root) if p.IsA(UsdGeom.Mesh)]
    cache = UsdGeom.XformCache()
    vs, fs, n = [], [], 0
    for p in prims:
        v, f = _mesh_triangles(p)
        xf = np.asarray(cache.GetLocalToWorldTransform(p), float).T
        vs.append(v @ xf[:3, :3].T + xf[:3, 3])
        fs.append(f + n)
        n += len(v)
    return np.concatenate(vs), np.concatenate(fs)


def prop_triangles(props_json, assets_dir, prim: str = "collision", berms: bool = True,
                   kinds: set[str] | None = None) -> tuple[np.ndarray, np.ndarray]:
    data = _read(props_json)
    cache: dict[Path, tuple[np.ndarray, np.ndarray]] = {}
    vs, fs, n = [], [], 0
    for prop in data["props"]:
        if kinds is not None and prop["kind"] not in kinds:
            continue
        path, scale = resolve(prop, assets_dir)
        if path is None:
            continue
        if path not in cache:
            cache[path] = asset_triangles(path, prim)
        v, f = cache[path]
        m = world_matrix(prop, scale)
        vs.append((v @ S.T) @ m[:3, :3].T + m[:3, 3])
        fs.append(f[:, ::-1] + n)
        n += len(v)
    if berms:
        for rel in sorted({b["asset"] for b in data.get("berms", [])}):
            path = Path(assets_dir) / rel
            if path.exists():
                v, f = asset_triangles(path, "")
                vs.append(v @ S.T)
                fs.append(f[:, ::-1] + n)
                n += len(v)
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), int)
    return np.concatenate(vs), np.concatenate(fs)


def obstacles(props_json) -> list[dict]:
    return [dict(p["obstacle"], id=p["id"], kind=p["kind"]) for p in _read(props_json)["props"] if p.get("obstacle")]
