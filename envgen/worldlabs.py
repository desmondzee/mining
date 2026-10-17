"""World Labs Marble pipeline -> data/worlds/<id>/

    # generate (needs WLT_API_KEY; --concept also needs OPENAI_API_KEY)
    python -m envgen.worldlabs --prompt "..." --id marble_pit [--image p.png] [--concept] [--draft]
    # fetch an existing world
    python -m envgen.worldlabs --world-id <id> --id marble_pit
    # rebuild world.json / navgrid from already-downloaded files (no credits)
    python -m envgen.worldlabs --from-dir data/worlds/marble_pit --id marble_pit

Coordinate assumptions (docs.worldlabs.ai "Rendering SPZ" + export specs):
  * Marble assets are in an OpenCV frame (+x right-ish, +y DOWN, +z forward), arbitrary units.
  * metric = raw * metric_scale_factor; then metric.y -= ground_plane_offset (still OpenCV frame),
    which puts the ground at y = 0.
  * OpenCV -> our Y-up frame: negate y and z (a 180 deg rotation about X).
  The collider GLB is assumed to share the splat frame. `--up auto` (default) checks this
  against the mesh normals and falls back to treating the GLB as already Y-up (glTF frame).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import common as C

API = "https://api.worldlabs.ai/marble/v1"
CONCEPT_PROMPT = (
    "Photorealistic oblique photo of a large open-pit copper mine, taken from about 10-15 m above the "
    "ground on the pit rim, looking across the pit. Terraced benches with steep rock faces step down "
    "into the pit, a wide gravel haul road spirals down the walls, the pit floor is visible. Clear flat "
    "ground plane in the foreground with a gravel pad, strong sense of depth, ochre and grey rock with "
    "greenish copper-oxide streaks, dusty arid landscape, clear daylight, no people, no text."
)


# ---------------------------------------------------------------- HTTP helpers
def _key(name):
    k = os.environ.get(name)
    if not k:
        sys.exit(f"{name} is not set")
    return k


def _wlt(method, path, **kw):
    import requests
    r = requests.request(method, API + path, headers={"WLT-Api-Key": _key("WLT_API_KEY")}, timeout=120, **kw)
    if r.status_code >= 400:
        sys.exit(f"World Labs {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json()


def download(url, dest):
    import requests
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f"  downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def concept_image(out: Path) -> Path:
    import requests
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    print(f"generating concept image with {model} ...")
    r = requests.post(f"{base}/images/generations", timeout=300,
                      headers={"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"},
                      json={"model": model, "prompt": CONCEPT_PROMPT, "size": "1536x1024", "n": 1})
    if r.status_code >= 400:
        sys.exit(f"OpenAI images -> {r.status_code}: {r.text[:500]}")
    d = r.json()["data"][0]
    path = out / "concept.png"
    if d.get("b64_json"):
        path.write_bytes(base64.b64decode(d["b64_json"]))
    else:
        download(d["url"], path)
    print(f"  saved {path}")
    return path


def generate(prompt, image, model, name, seed=None):
    if image:
        ext = Path(image).suffix.lstrip(".").lower() or "png"
        wp = {"type": "image", "text_prompt": prompt or None, "is_pano": "false",
              "image_prompt": {"source": "data_base64", "extension": ext,
                               "data_base64": base64.b64encode(Path(image).read_bytes()).decode()}}
    else:
        wp = {"type": "text", "text_prompt": prompt}
    body = {"display_name": name[:64], "model": model, "world_prompt": wp}
    if seed is not None:
        body["seed"] = seed
    op = _wlt("POST", "/worlds:generate", json=body)
    oid = op["operation_id"]
    print(f"operation {oid} started (model {model}); polling every 10 s ...")
    t0 = time.time()
    while not op.get("done"):
        time.sleep(10)
        op = _wlt("GET", f"/operations/{oid}")
        print(f"  {time.time() - t0:5.0f}s  {json.dumps(op.get('metadata') or {})[:120]}")
    if op.get("error"):
        sys.exit(f"generation failed: {op['error']}")
    if op.get("cost"):
        print(f"  cost: {op['cost'].get('total_credits')} credits")
    resp = op.get("response") or {}
    world = resp.get("world", resp) if isinstance(resp, dict) else {}
    if "assets" not in world:  # response may only carry the id
        world = _wlt("GET", f"/worlds/{world.get('world_id') or world.get('id')}")
    return world, op


def fetch_assets(world, out: Path):
    (out / "marble_world.json").write_text(json.dumps(world, indent=2))
    a = world.get("assets") or {}
    spz = (a.get("splats") or {}).get("spz_urls") or {}
    want = {"splat_500k.spz": ["500k", "full_res"], "splat_100k.spz": ["100k", "150k", "500k"]}
    for fname, keys in want.items():
        url = next((spz[k] for k in keys if spz.get(k)), None)
        if url:
            download(url, out / fname)
    if (a.get("mesh") or {}).get("collider_mesh_url"):
        download(a["mesh"]["collider_mesh_url"], out / "collider.glb")
    if (a.get("imagery") or {}).get("pano_url"):
        download(a["imagery"]["pano_url"], out / "pano.jpg")
    if a.get("thumbnail_url"):
        download(a["thumbnail_url"], out / "thumbnail.jpg")


# ---------------------------------------------------------------- collider -> heightmap
def load_collider(path, scale, ground_offset, up="auto"):
    import trimesh
    mesh = trimesh.load(path, force="mesh", process=False)
    v = np.asarray(mesh.vertices, float) * scale

    def opencv(v):
        v = v.copy()
        v[:, 1] -= ground_offset
        v[:, 1:] *= -1
        return v

    def gltf(v):
        v = v.copy()
        v[:, 1] += ground_offset
        return v

    cands = {"opencv": opencv(v), "gltf": gltf(v)}
    if up == "auto":
        def up_score(vv):
            m = trimesh.Trimesh(vv, mesh.faces, process=False)
            return float((m.face_normals[:, 1] * m.area_faces).sum())
        scores = {k: up_score(vv) for k, vv in cands.items()}
        up = max(scores, key=scores.get)
        print(f"  frame auto-detect (area-weighted up-normal): {scores} -> {up}")
    return trimesh.Trimesh(cands[up], mesh.faces, process=False), up


def rasterize(mesh, cell=None, max_cells=220):
    """Top-surface heightmap by downward ray casting at cell centres."""
    from scipy import ndimage
    v = mesh.vertices
    lo = np.percentile(v[:, [0, 2]], 1, axis=0)
    hi = np.percentile(v[:, [0, 2]], 99, axis=0)
    ext = hi - lo
    cell = cell or max(0.5, round(float(ext.max()) / max_cells, 2))
    w, h = int(np.ceil(ext[0] / cell)), int(np.ceil(ext[1] / cell))
    origin = [float(lo[0]), float(lo[1])]
    cx = origin[0] + (np.arange(w) + 0.5) * cell
    cz = origin[1] + (np.arange(h) + 0.5) * cell
    X, Z = np.meshgrid(cx, cz)
    top = v[:, 1].max() + 5
    orig = np.stack([X.ravel(), np.full(X.size, top), Z.ravel()], 1)
    dirs = np.tile([0.0, -1.0, 0.0], (len(orig), 1))
    t0 = time.time()
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
    except Exception:
        from trimesh.ray.ray_triangle import RayMeshIntersector
    loc, ray_idx, _ = RayMeshIntersector(mesh).intersects_location(orig, dirs, multiple_hits=True)
    Hflat = np.full(X.size, -np.inf)
    np.maximum.at(Hflat, ray_idx, loc[:, 1])  # highest hit = top surface
    H = Hflat.reshape(h, w)
    print(f"  raycast {w}x{h} cells @ {cell} m in {time.time() - t0:.1f}s, hit {np.isfinite(H).mean():.0%}")
    valid = np.isfinite(H)
    dist, (ir, ic) = ndimage.distance_transform_edt(~valid, return_indices=True)
    H = ndimage.median_filter(H[ir, ic], size=3)
    hole = dist > 3  # large gaps (no geometry) -> impassable
    return H, origin, cell, hole


def place_sites(H, cost, origin, cell, n_ore=3):
    """Pick ore sites low in the terrain and crusher/dump/parking/fuel on large flat open areas."""
    from scipy import ndimage
    comp = C.largest_component(cost > 0)
    flat = comp & (C.slope_deg(H, cell) < 6)
    clear = ndimage.distance_transform_edt(np.pad(flat, 1))[1:-1, 1:-1] * cell  # m to non-flat/edge
    ext = max(H.shape) * cell
    min_sep = ext * 0.18
    chosen = []

    def pick(score):
        s = score.copy()
        for rc in chosen:
            rr, cc = np.ogrid[: H.shape[0], : H.shape[1]]
            s[np.hypot(rr - rc[0], cc - rc[1]) * cell < min_sep] = -np.inf
        rc = np.unravel_index(np.argmax(s), s.shape)
        if not np.isfinite(s[rc]):
            rc = np.unravel_index(np.argmax(np.where(comp, clear, -np.inf)), s.shape)
        chosen.append(rc)
        return rc

    hn = (H - H[comp].min()) / max(np.ptp(H[comp]), 1e-6)
    base = np.where(comp, np.minimum(clear, 12.0), -np.inf)
    sites = []
    ore_names = ["Pit Floor", "Lower Bench", "Upper Bench", "Side Bench"]
    for i in range(n_ore):  # low + reasonably open
        rc = pick(base - 25 * hn)
        sites.append({"id": f"ore_{i+1}", "type": "ore", "name": ore_names[i % 4],
                      "pos": C.cell_to_world(rc, origin, cell), "tonnes": [8000, 6000, 4500][i % 3]})
    for sid, typ, name in [("crusher", "crusher", "Primary Crusher"), ("dump", "dump", "Waste Dump"),
                           ("parking", "parking", "Ready Line"), ("fuel", "fuel", "Fuel Bay")]:
        rc = pick(np.where(comp, clear + 5 * hn, -np.inf))
        sites.append({"id": sid, "type": typ, "name": name, "pos": C.cell_to_world(rc, origin, cell)})

    # trucks: nearest distinct passable cells around parking, ~2 truck lengths apart
    pr = chosen[n_ore + 2]
    rr, cc = np.nonzero(comp)
    order = np.argsort(np.hypot(rr - pr[0], cc - pr[1]))
    spawns = []
    for k in order:
        p = (rr[k], cc[k])
        if all(np.hypot(p[0] - q[0], p[1] - q[1]) * cell >= 6 for q in spawns):
            spawns.append(p)
        if len(spawns) == 5:
            break
    trucks = [{"id": f"T{i+1}", "callsign": f"Truck {i+1}", "spawn": C.cell_to_world(p, origin, cell),
               "heading_deg": 0, "capacity_t": 200} for i, p in enumerate(spawns)]
    return sites, trucks


def derive_roads(H, cost, origin, cell, sites):
    """Marble worlds have no authored roads: least-cost (A*) navgrid paths between sites, smoothed."""
    from scipy.ndimage import gaussian_filter1d
    by = {s["id"]: s for s in sites}
    ores = [s["id"] for s in sites if s["type"] == "ore"]
    pairs = ([("parking", o) for o in ores] + [(o, "crusher") for o in ores] + [(o, "dump") for o in ores]
             + [("parking", "fuel")])
    hfun = lambda xz: C.bilinear(H, origin, cell, xz, centred=True)
    roads = []
    for a, b in pairs:
        pa, pb = by[a]["pos"], by[b]["pos"]
        cells = C.least_cost_path(cost, C.world_to_cell(pa, origin, cell), C.world_to_cell(pb, origin, cell))
        if cells is None:
            print(f"  no path {a} -> {b}")
            continue
        xz = np.array([C.cell_to_world(rc, origin, cell) for rc in cells], float)
        xz[0], xz[-1] = pa, pb
        if len(xz) > 4:  # smooth interior, keep endpoints pinned on the sites
            sm = gaussian_filter1d(xz, sigma=2.0, axis=0, mode="nearest")
            sm[0], sm[-1] = pa, pb
            xz = sm
        roads.append(C.road_entry(f"{a}_to_{b}", f"{by[a]['name']} to {by[b]['name']}", "haul", a, b,
                                  xz, hfun, 12.0))
    for st in sites:
        st["pos3"] = C.pos3(st["pos"], hfun)
    return roads


def build_world(out: Path, wid, name, up="auto", cell=None):
    meta = json.loads((out / "marble_world.json").read_text()) if (out / "marble_world.json").exists() else {}
    sem = ((meta.get("assets") or {}).get("splats") or {}).get("semantics_metadata") or {}
    scale = float(sem.get("metric_scale_factor") or 1.0)
    offset = float(sem.get("ground_plane_offset") or 0.0)
    collider = out / "collider.glb"
    if not collider.exists():
        sys.exit(f"{collider} not found")
    print(f"building navgrid from {collider} (scale {scale}, ground offset {offset})")
    mesh, frame = load_collider(collider, scale, offset, up)
    H, origin, cell, hole = rasterize(mesh, cell)
    sl = C.slope_deg(H, cell)
    cost = C.cost_from_slope(sl)
    cost[hole] = -1.0
    sites, trucks = place_sites(H, cost, origin, cell)
    roads = derive_roads(H, cost, origin, cell, sites)
    size = [round(H.shape[1] * cell, 2), round(H.shape[0] * cell, 2)]

    nav = C.navgrid_dict(H, cost, origin, cell)
    C.write_json(out / "navgrid.json", nav, compact=True)

    # colors: dirt -> rock by slope, darker when low; holes dark
    hn = (H - H.min()) / max(np.ptp(H), 1e-6)
    rock = C.smoothstep((sl - 15) / 20)[..., None]
    col = np.array([0.62, 0.52, 0.38]) * (1 - rock) + np.array([0.45, 0.43, 0.42]) * rock
    col = col * (0.8 + 0.2 * hn[..., None])
    col[hole] = (0.2, 0.2, 0.2)
    C.export_terrain_glb(out / "terrain.glb", H, col, origin, size, n=min(321, max(H.shape)))
    C.write_heightmap_raw(out, H, origin, size)

    has = lambda f: f if (out / f).exists() else None
    caption = (meta.get("assets") or {}).get("caption")
    world = {
        "id": wid, "name": name or meta.get("display_name") or "Marble Pit", "source": "marble",
        "size_m": size, "origin": origin,
        "assets": {"splat": has("splat_500k.spz"), "splat_lowres": has("splat_100k.spz"),
                   "collider": "collider.glb", "terrain_mesh": "terrain.glb", "pano": has("pano.jpg"),
                   "concept": has("concept.png"), "heightmap_raw": "heightmap.raw",
                   "heightmap_meta": "heightmap_meta.json", "preview": "preview.png",
                   "thumbnail": has("thumbnail.jpg")},
        "splat_transform": {"scale": scale, "ground_offset": offset,
                            "rotation_euler_deg": [180, 0, 0] if frame == "opencv" else [0, 0, 0],
                            "frame": frame,
                            "order": "p = raw*scale; p.y -= ground_offset (raw frame); then rotate"},
        "sites": sites, "junctions": [], "roads": roads,
        "trucks": trucks, "navgrid": "navgrid.json",
        "marble": {"world_id": meta.get("world_id"), "marble_url": meta.get("world_marble_url"),
                   "caption": caption, "model": meta.get("model")},
    }
    errs = C.check_road_graph(world)
    if errs:
        print(f"  WARNING road graph: {errs}")
    C.write_json(out / "world.json", world)
    C.write_preview(out / "preview.png", H, col, cell, world)
    bad = C.check_reachability(world, nav)
    print(f"wrote {out}: {nav['width']}x{nav['height']} cells @ {cell} m, size {size} m, "
          f"passable {(cost > 0).mean():.0%}, roads {len(roads)}, unreachable: {bad or 'none'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", default="marble_pit")
    ap.add_argument("--name", default=None)
    ap.add_argument("--prompt", default="Aerial-to-ground view of an open-pit copper mine with terraced "
                    "benches, steep rock faces, a spiral gravel haul road and a flat pit floor, arid landscape")
    ap.add_argument("--image", help="image prompt (png/jpg/webp)")
    ap.add_argument("--concept", action="store_true", help="make a concept image with OpenAI first")
    ap.add_argument("--model", default="marble-1.1-plus")
    ap.add_argument("--draft", action="store_true", help="use marble-1.0-draft (cheap iteration)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--world-id", help="fetch an existing Marble world instead of generating")
    ap.add_argument("--from-dir", help="rebuild from already-downloaded files in this directory")
    ap.add_argument("--up", choices=["auto", "opencv", "gltf"], default="auto", help="collider frame")
    ap.add_argument("--cell", type=float, help="navgrid cell size in m (default: extent/220, >= 0.5)")
    a = ap.parse_args()
    from .concepts import load_env
    load_env()

    if not a.from_dir:
        _key("WLT_API_KEY")
        if a.concept:
            _key("OPENAI_API_KEY")
    out = C.WORLDS / a.id
    out.mkdir(parents=True, exist_ok=True)
    if a.from_dir:
        src = Path(a.from_dir).resolve()
        if src != out.resolve():
            import shutil
            for f in src.iterdir():
                if f.is_file() and f.name not in ("world.json", "navgrid.json"):
                    shutil.copy2(f, out / f.name)
    elif a.world_id:
        fetch_assets(_wlt("GET", f"/worlds/{a.world_id}"), out)
    else:
        image = a.image
        if a.concept:
            image = str(concept_image(out))
        elif image:
            import shutil
            shutil.copy2(image, out / "concept.png")
        model = "marble-1.0-draft" if a.draft else a.model
        world, op = generate(a.prompt, image, model, a.name or f"Physical Mining {a.id}", a.seed)
        world.setdefault("model", model)
        fetch_assets(world, out)
        (out / "marble_operation.json").write_text(json.dumps({k: v for k, v in op.items() if k != "response"}, indent=2))
    build_world(out, a.id, a.name, a.up, a.cell)


if __name__ == "__main__":
    main()
