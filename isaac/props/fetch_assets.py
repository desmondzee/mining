import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from procedural import merge  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
API = "https://api.polyhaven.com"
ROCKS = ["namaqualand_boulder_02", "namaqualand_boulder_03", "namaqualand_boulder_04", "namaqualand_boulder_05",
         "namaqualand_boulder_06", "rock_07"]
PROPS = {"concrete_road_barrier": 12000, "barrel_03": 3000, "Barrel_01": 3000, "portable_generator": 12000,
         "covered_car": 14000}
LOD0_TRIS = 20000
LOD1_TRIS = 3000
TEX_MAX = 2048
Y_TO_Z = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])


def download(asset: str, raw: Path, res: str = "2k") -> Path:
    import requests
    files = requests.get(f"{API}/files/{asset}", timeout=60).json()
    g = files["gltf"][res]["gltf"]
    out = raw / asset
    out.mkdir(parents=True, exist_ok=True)
    for rel, url in [(Path(g["url"]).name, g["url"])] + [(k, v["url"]) for k, v in g["include"].items()]:
        dst = out / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            dst.write_bytes(r.content)
    info = requests.get(f"{API}/info/{asset}", timeout=60).json()
    (out / "info.json").write_text(json.dumps(info))
    return out / Path(g["url"]).name


def decimate(v: np.ndarray, f: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    import fast_simplification
    if len(f) <= target:
        return v, f
    vo, fo = fast_simplification.simplify(v.astype(np.float32), f.astype(np.int32), target_reduction=1 - target / len(f))
    return np.asarray(vo, float), np.asarray(fo, int)


def transfer_uv(src, uv: np.ndarray, v: np.ndarray, f: np.ndarray) -> np.ndarray:
    import trimesh
    _, _, tri = trimesh.proximity.closest_point(src, v[f].mean(axis=1))
    out = np.zeros((len(f), 3, 2))
    for k in range(3):
        b = trimesh.triangles.points_to_barycentric(src.triangles[tri], v[f[:, k]])
        out[:, k] = np.einsum("ij,ijk->ik", b, uv[src.faces[tri]])
    out[..., 1] = 1 - out[..., 1]
    return out.reshape(-1, 2)


def save_image(img, path: Path, mode: str) -> str:
    img = img.convert(mode)
    if max(img.size) > TEX_MAX:
        img = img.resize((TEX_MAX, TEX_MAX * img.size[1] // img.size[0]), 1)
    img.save(path, quality=90)
    return path.name


def submeshes(gltf: Path) -> list:
    import trimesh
    scene = trimesh.load(gltf, force="scene", process=False)
    out = []
    for name in scene.graph.nodes_geometry:
        xf, geom = scene.graph[name]
        m = scene.geometry[geom].copy()
        m.apply_transform(xf)
        out.append(m)
    return out


def material_spec(m, tex: Path, key: str) -> dict:
    mat = getattr(m.visual, "material", None)
    if mat is None:
        return {"color": (0.5, 0.5, 0.5), "roughness": 0.8}
    if not hasattr(mat, "baseColorTexture"):
        mat = mat.to_pbr()
    files = {}
    if mat.baseColorTexture is not None:
        files["diff"] = "textures/" + save_image(mat.baseColorTexture, tex / f"{key}_diff.jpg", "RGB")
    if mat.normalTexture is not None:
        files["nor"] = "textures/" + save_image(mat.normalTexture, tex / f"{key}_nor.jpg", "RGB")
    if mat.metallicRoughnessTexture is not None:
        files["arm"] = "textures/" + save_image(mat.metallicRoughnessTexture, tex / f"{key}_arm.jpg", "RGB")
    if not files:
        c = np.asarray(mat.baseColorFactor if mat.baseColorFactor is not None else [128, 128, 128, 255], float)[:3]
        return {"color": tuple(c / (255 if c.max() > 1 else 1)), "roughness": float(mat.roughnessFactor or 0.8)}
    return {"files": files, "tex": True, "roughness": 0.85}


def build_mesh(asset: str, gltf: Path, out: Path, stash: Path, rock: bool, budget: int) -> None:
    import trimesh
    tex = out / "textures"
    tex.mkdir(parents=True, exist_ok=True)
    meshes = submeshes(gltf)
    for m in meshes:
        m.vertices = m.vertices @ Y_TO_Z.T
    allv = np.concatenate([m.vertices for m in meshes])
    lo, hi = allv.min(0), allv.max(0)
    centre = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    native = hi - lo
    scale = 1.0 / max(native[0], native[1]) if rock else 1.0
    total = sum(len(m.faces) for m in meshes)
    arrays, mats = {}, {}
    for i, m in enumerate(meshes):
        v0 = (m.vertices - centre) * scale
        uv = np.asarray(m.visual.uv, float) if getattr(m.visual, "uv", None) is not None else None
        key = f"{asset}_m{i}"
        mats[key] = material_spec(m, tex, key)
        src = trimesh.Trimesh(v0, m.faces, process=False)
        weld = trimesh.Trimesh(v0, m.faces, process=True)
        share = len(m.faces) / total
        for lod, tris in (("lod0", budget), ("lod1", LOD1_TRIS)):
            v, f = decimate(weld.vertices, weld.faces, max(64, int(tris * share)))
            arrays[f"{lod}_{i}_v"], arrays[f"{lod}_{i}_f"] = v, f
            if uv is not None:
                arrays[f"{lod}_{i}_uv"] = transfer_uv(src, uv, v, f)
    stash.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(stash / f"{asset}.npz", **arrays)
    info = json.loads((gltf.parent / "info.json").read_text())
    meta = {"asset": asset, "kind": "boulder" if rock else "prop", "parts": len(meshes), "materials": mats,
            "native_m": [round(float(x), 3) for x in native], "scale_to_unit": round(scale, 6), "source_tris": int(total),
            "licence": "CC0", "url": f"https://polyhaven.com/a/{asset}", "authors": list(info.get("authors", {}).keys())}
    (stash / f"{asset}.json").write_text(json.dumps(meta))


def build_usd(asset: str, out: Path, stash: Path) -> dict:
    import usdio
    meta = json.loads((stash / f"{asset}.json").read_text())
    z = np.load(stash / f"{asset}.npz")
    lods = {}
    for lod in ("lod0", "lod1"):
        lods[lod] = [{"name": f"mesh_{i}", "v": z[f"{lod}_{i}_v"], "f": z[f"{lod}_{i}_f"], "material": f"{asset}_m{i}",
                      "uv": z[f"{lod}_{i}_uv"] if f"{lod}_{i}_uv" in z else None} for i in range(meta["parts"])]
    coll = merge([(p["v"], p["f"]) for p in lods["lod1"]])
    path = out / f"{asset}.usdc"
    rock = meta["kind"] == "boulder"
    usdio.write_asset(path, asset, [], coll, materials=meta["materials"], lods=lods,
                      approximation="convexHull" if rock else "convexDecomposition")
    meta.pop("materials")
    meta.update(file=f"polyhaven/{path.name}", tris_lod0=int(sum(len(p["f"]) for p in lods["lod0"])),
                tris_lod1=int(sum(len(p["f"]) for p in lods["lod1"])))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(REPO / "downloads/props/polyhaven"))
    ap.add_argument("--stash", default=str(REPO / "downloads/props/polyhaven_mesh"))
    ap.add_argument("--out", default=str(REPO / "isaac/assets/props/polyhaven"))
    ap.add_argument("--stage", choices=["mesh", "usd", "all"], default="all")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    raw, out, stash = Path(args.raw), Path(args.out), Path(args.stash)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    jobs = [(a, True, LOD0_TRIS) for a in ROCKS] + [(a, False, t) for a, t in PROPS.items()]
    for asset, rock, budget in jobs:
        if args.only and asset not in args.only:
            continue
        if args.stage in ("mesh", "all"):
            build_mesh(asset, download(asset, raw), out, stash, rock, budget)
            print("mesh", asset, flush=True)
        if args.stage in ("usd", "all"):
            manifest[asset] = build_usd(asset, out, stash)
            print(json.dumps(manifest[asset]), flush=True)
    if args.stage != "mesh":
        manifest_path.write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
