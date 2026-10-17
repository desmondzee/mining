"""Import a photogrammetry scan (GLB, e.g. a Sketchfab drone scan of a quarry) as a world.

    python -m envgen.scan <scan.glb> --id quarry [--extent 260] [--metric] [--target-tris 300000]
                          [--up auto|+y|-y|+z|-z|+x|-x] [--cell 1.3] [--ref-pct 90] [--name "..."]

Writes data/worlds/<id>/:
  visual.glb      the original GLB, byte-for-byte (textured; what Unity renders, after scan_transform)
  collider.glb    the transformed mesh, decimated to ~--target-tris, no materials (world frame)
  navgrid.json, heightmap.raw + heightmap_meta.json, terrain.glb, preview.png, world.json

Transform (recorded in world.json "scan_transform"): p_world = scale * (R @ p_gltf) + translation, where
p_gltf is a vertex of visual.glb with its node transforms applied (glTF scene space, Y up) and R is the
axis-aligned rotation that puts the scan's up axis on +Y. XZ bbox centre -> origin; the --ref-pct
percentile of terrain height (default 90th: rim / surrounding ground) -> y = 0, so the pit is negative.
Without --metric the scan is uniformly scaled so its larger XZ extent = --extent m (fits the sim bounds).
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import numpy as np

from . import common as C
from . import mine_spec as MS

# up axis of the raw scan -> (proper rotation R with R @ up = +Y, its XYZ euler in degrees). Euler convention:
# active, right-handed, extrinsic X then Y then Z, i.e. R = Rz(ez) @ Ry(ey) @ Rx(ex) (each entry uses one axis).
ROT = {
    "+y": (np.eye(3), [0, 0, 0]),
    "+z": (np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float), [-90, 0, 0]),
    "-z": (np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float), [90, 0, 0]),
    "-y": (np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], float), [180, 0, 0]),
    "+x": (np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float), [0, 0, 90]),
    "-x": (np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float), [0, 0, -90]),
}


def load_scan(path):
    """All mesh geometry of the GLB in scene space (node transforms applied) + per-vertex RGB (0..1) sampled
    from the base-colour texture where possible (None if no colour)."""
    import trimesh
    scene = trimesh.load(path, force="scene", process=False)
    V, F, VC, n = [], [], [], 0
    have_col = True
    for g in scene.dump(concatenate=False):
        if not isinstance(g, trimesh.Trimesh) or len(g.faces) == 0:
            continue
        col = None
        try:
            vis = g.visual
            if vis.kind == "texture":
                vis = vis.to_color()
            if vis.kind in ("vertex", "face"):
                col = np.asarray(vis.vertex_colors, float)[:, :3] / 255.0
        except Exception as e:  # noqa: BLE001 - colour is cosmetic
            print(f"  (no colour for a sub-mesh: {e})")
        if col is None:
            have_col = False
            col = np.full((len(g.vertices), 3), 0.55)
        V.append(np.asarray(g.vertices, float)); F.append(np.asarray(g.faces) + n); VC.append(col)
        n += len(g.vertices)
    if not V:
        raise SystemExit(f"{path}: no triangle meshes")
    return np.concatenate(V), np.concatenate(F), (np.concatenate(VC) if have_col else None)


def detect_up(V, F, force="auto"):
    if force != "auto":
        return force
    tri = V[F]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])   # |n| = 2 * area
    s = nrm.sum(0) / max(np.linalg.norm(nrm, axis=1).sum(), 1e-12)
    ext = np.ptp(V, axis=0)
    k = int(np.argmax(np.abs(s)))
    up = ("+" if s[k] > 0 else "-") + "xyz"[k]
    if abs(s[k]) < 0.2:  # normals inconclusive (closed / inconsistent winding): thinnest bbox axis, glTF says +Y
        k = int(np.argmin(ext))
        up = "+" + "xyz"[k]
    print(f"  up-axis auto-detect: area-weighted mean normal {np.round(s, 3)}, bbox {np.round(ext, 2)} -> {up}")
    return up


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb")
    ap.add_argument("--id", default="quarry")
    ap.add_argument("--name", default=None)
    ap.add_argument("--extent", type=float, default=260.0, help="larger XZ extent after scaling (m)")
    ap.add_argument("--metric", action="store_true", help="scan is already in metres: keep its scale")
    ap.add_argument("--target-tris", type=int, default=300_000, help="collider.glb triangle budget")
    ap.add_argument("--up", default="auto", choices=["auto"] + list(ROT))
    ap.add_argument("--cell", type=float, help="navgrid cell (m), default extent/200")
    ap.add_argument("--ref-pct", type=float, default=90.0, help="terrain height percentile mapped to y=0")
    ap.add_argument("--n-ore", type=int, default=3)
    ap.add_argument("--layout", choices=["auto", "hillside", "pit"], default="auto",
                    help="site layout rules: pit (ore on the floor, plant on the rim) or hillside (plant on the "
                         "floor at the base of the face, ore on bench faces); auto detects")
    ap.add_argument("--site", action="append", default=[], metavar="ID=X,Z",
                    help="pin crusher/dump/parking/fuel near world [x, z] (repeatable), e.g. crusher=-88,12")
    ap.add_argument("--steep-note", action="append", default=[], help="extra text for design.notes")
    ap.add_argument("--truck", default=MS.DEFAULT_TRUCK, choices=list(MS.TRUCKS))
    a = ap.parse_args()
    import trimesh

    T0 = time.time()
    src = Path(a.glb).resolve()
    out = C.WORLDS / a.id
    out.mkdir(parents=True, exist_ok=True)
    vis = out / "visual.glb"
    if not (vis.exists() and vis.resolve() == src):
        shutil.copyfile(src, vis)
    print(f"[scan] {src.name} ({src.stat().st_size / 1e6:.1f} MB) -> {out}")

    V, F, VC = load_scan(src)
    print(f"  loaded {len(V)} verts, {len(F)} tris in {time.time() - T0:.1f}s (colour: {VC is not None})")

    # ---- orientation, scale, recentre
    up = detect_up(V, F, a.up)
    R, euler = ROT[up]
    V = V @ R.T
    lo, hi = np.percentile(V[:, [0, 2]], [0.2, 99.8], axis=0)       # robust to floaters
    ctr = (lo + hi) / 2
    native = hi - lo
    s = 1.0 if a.metric else a.extent / float(native.max())
    t = np.array([-s * ctr[0], 0.0, -s * ctr[1]])
    V = V * s + t
    E = float(np.ceil(native.max() * s / 2) * 2) if a.metric else float(a.extent)
    E = max(E, 10.0)
    print(f"  native XZ extent {native.round(3)} (scan units) -> scale {s:.4f} -> {(native * s).round(1)} m;"
          f" grid {E:.0f} m square")

    # ---- heightmap by raycasting (full-res mesh with embree; decimated collider otherwise)
    mesh = trimesh.Trimesh(V, F, process=False)
    cell = a.cell or max(0.5, round(E / 200, 2))
    n = int(round(E / cell))
    cell = E / n
    origin = [-E / 2, -E / 2]
    _, embree = C.ray_intersector(trimesh.Trimesh(V[:3], [[0, 1, 2]], process=False))

    t1 = time.time()
    col_mesh = trimesh.Trimesh(V, F, process=False)
    col_mesh.merge_vertices(merge_tex=True, merge_norm=True)
    col_mesh.remove_unreferenced_vertices()
    if len(col_mesh.faces) > a.target_tris:
        try:
            col_mesh = col_mesh.simplify_quadric_decimation(face_count=a.target_tris)
        except Exception as e:  # noqa: BLE001
            print(f"  decimation unavailable ({e}); collider keeps all {len(col_mesh.faces)} tris")
    print(f"  collider {len(col_mesh.faces)} tris ({time.time() - t1:.1f}s)")

    H, tri = C.raycast_grid(mesh if embree else col_mesh, origin, cell, n, n)
    hit = np.isfinite(H)
    extra = []
    if VC is not None and embree:
        fc = VC[F].mean(1)
        cg = np.where(hit[..., None], fc[np.clip(tri, 0, None)], np.nan)
        extra = [cg]
    H, hole, extra = C.fill_holes(H, gap_cells=3, extra=extra)
    from scipy.ndimage import gaussian_filter
    Hb = H.copy()
    for _ in range(3):                                               # soften nearest-fill streaks in holes
        Hb = np.where(hole, gaussian_filter(Hb, 4), H)
    H = Hb
    hole[[0, -1], :] = True                                          # border ring impassable
    hole[:, [0, -1]] = True
    y0 = float(np.percentile(H[~hole], a.ref_pct))
    H -= y0
    t[1] = -y0
    V[:, 1] -= y0
    col_mesh.vertices[:, 1] -= y0
    floor = float(np.percentile(H[~hole], 1))
    depth = -floor
    print(f"  terrain: rim (p{a.ref_pct:.0f}) -> y=0, floor (p1) {floor:.1f} m, max {H[~hole].max():.1f} m;"
          f" depth {depth:.1f} m, depth/extent {depth / E:.3f}; holes {hole.mean():.0%}")
    if depth < 3 or depth > 150:
        print(f"  WARNING: pit depth {depth:.1f} m looks unrealistic (expected 3-150 m) - check --metric/--extent/--up")
    if not 0.01 < depth / E < 0.6:
        print(f"  WARNING: depth/extent {depth / E:.3f} is odd - wrong up axis or scale?")
    if a.metric and E > 260:
        print(f"  WARNING: metric extent {E:.0f} m exceeds the default sim bounds [-130, 130]")
    col_mesh.export(out / "collider.glb")

    # ---- cost, sites, roads
    spec = MS.preset(a.truck)
    sl = C.slope_deg(H, cell)
    cost = C.cost_from_slope(sl)
    cost[hole] = -1.0
    veg = None
    if extra:                                                        # green-dominant texture = vegetation
        r_, g_, b_ = extra[0][..., 0], extra[0][..., 1], extra[0][..., 2]
        veg = (g_ > 1.04 * r_) & (g_ > 1.04 * b_)
        print(f"  vegetation (from texture) {veg[~hole].mean():.0%} of scanned cells, avoided for sites")
    if veg is not None:                                              # driving through trees: allowed, but costly
        from scipy.ndimage import binary_opening
        veg = binary_opening(veg, iterations=1)
        cost = np.where((cost > 0) & veg, cost * 2.0, cost)
    fixed = {k: [float(v) for v in xz.split(",")] for k, xz in (p.split("=") for p in a.site)}
    layout = C.detect_layout(H, hole, origin, cell) if a.layout == "auto" else a.layout
    if layout == "hillside":
        sites = C.place_sites_hillside(H, cost, origin, cell, n_ore=a.n_ore, avoid=veg, fixed=fixed)
        ores = sorted([x for x in sites if x["type"] == "ore"], key=lambda x: x["pos"][1] * 0 + C.bilinear(
            H, origin, cell, [x["pos"]], centred=True)[0])
        order = ["crusher"] + [x["id"] for x in ores] + ["dump", "parking", "fuel"]
        spur = {"fuel": "parking"}
    else:
        sites = C.place_sites_auto(H, cost, origin, cell, n_ore=a.n_ore, avoid=veg, fixed=fixed)
        order, spur = None, {"fuel": "parking"}
    junctions, roads, road_mask = C.build_road_network(H, cost, origin, cell, sites, spec.road_width_m,
                                                       spec.ramp_width_m, max_grade=0.10, order=order,
                                                       spur_to=spur)
    cost = np.where(road_mask & (cost > 0), 1.0, cost)
    trucks = C.make_trucks(sites, roads, cost, origin, cell, spec.truck)
    grades = C.road_grade_stats(roads)
    sustained = C.road_grade_stats(roads, window=50.0)
    haul = C.haul_cycle_roads({"sites": sites, "roads": roads})
    steep = [r for r in roads if sustained[r["id"]] > 10.0]
    notes = ("Scanned terrain (" + layout + " layout): geometry is as-scanned. Roads are least-cost paths on the "
             "navgrid with grades above 10% strongly penalised; widths follow the design truck spec.")
    for r in steep:
        notes += (f" EXCEPTION: {r['id']} ({r['name']}) is {sustained[r['id']]}% sustained over 50 m "
                  f"(max {grades[r['id']]}% over 20 m); "
                  + ("ON the haul cycle - terrain allows nothing flatter." if r["id"] in haul
                     else "off the haul cycle."))
    best = None
    for o in [x for x in sites if x["type"] == "ore"]:
        route = C.road_route(roads, o["id"], "crusher") or []
        worst = max([sustained[r] for r in route] or [0.0])
        if best is None or worst < best[2]:
            best = (o, route, worst)
    if best:
        notes += (f" Main demo haul cycle: {best[0]['name']} ({best[0]['id']}) -> crusher via {', '.join(best[1])}, "
                  f"max {best[2]}% sustained.")
    notes += "".join(" " + t for t in a.steep_note)

    # ---- colours, outputs
    col = C.terrain_colors(H, sl, hole)
    if extra:
        col = np.clip(extra[0], 0, 1)
        col[hole] = (0.15, 0.15, 0.15)
    Hn = C.centres_to_nodes(H)
    Cn = np.stack([C.centres_to_nodes(col[..., k]) for k in range(3)], -1)
    size = [E, E]
    nav = C.navgrid_dict(H, cost, origin, cell)
    C.write_json(out / "navgrid.json", nav, compact=True)
    C.export_terrain_glb(out / "terrain.glb", Hn, Cn, origin, size, n=min(321, n + 1))
    C.write_heightmap_raw(out, Hn, origin, size, res=513)

    M = np.eye(4)
    M[:3, :3] = s * R
    M[:3, 3] = t
    design = spec.to_dict()
    world = {
        "id": a.id, "name": a.name or src.stem.replace("_", " ").title(), "source": "scan",
        "size_m": size, "origin": origin, "bounds": [origin[0], origin[1], origin[0] + E, origin[1] + E],
        "assets": {"visual": "visual.glb", "collider": "collider.glb", "terrain_mesh": "terrain.glb",
                   "splat": None, "pano": None, "concept": None, "heightmap_raw": "heightmap.raw",
                   "heightmap_meta": "heightmap_meta.json", "preview": "preview.png"},
        "scan_transform": {
            "scale": round(s, 6), "rotation_euler_deg": euler, "rotation_matrix": R.tolist(),
            "translation": [round(float(v), 4) for v in t], "up_axis": up, "frame": "gltf",
            "matrix": [[round(float(v), 6) for v in row] for row in M],
            "order": "p_world = scale * (R @ p_gltf) + translation; p_gltf = visual.glb vertex in glTF scene space "
                     "(node transforms applied, Y up). matrix = same, 4x4 row-major, column vectors.",
            "native_extent_xz": [round(float(v), 4) for v in native], "metric": bool(a.metric),
            "ref_percentile": a.ref_pct, "source_file": src.name},
        "sites": sites, "junctions": junctions, "roads": roads, "trucks": trucks, "navgrid": "navgrid.json",
        "design": {"design_truck": design["design_truck"], "road_width_m": spec.road_width_m,
                   "ramp_width_m": spec.ramp_width_m, "ramp_grade_pct": spec.ramp_grade_pct,
                   "max_road_grade_pct": 10.0, "safety_berm_height_m": design["safety_berm_height_m"],
                   "references": design["references"],
                   "notes": notes, "layout": layout, "haul_cycle_roads": sorted(haul),
                   "as_built": {"pit_depth_m": round(depth, 1), "road_max_grade_pct_20m": grades,
                                "road_sustained_grade_pct_50m": sustained}},
        "scan": {"pit_depth_m": round(depth, 1), "floor_y": round(floor, 2),
                 "max_y": round(float(H[~hole].max()), 2), "hole_fraction": round(float(hole.mean()), 3),
                 "tris": int(len(F)), "collider_tris": int(len(col_mesh.faces))},
    }
    errs = C.check_road_graph(world)
    op_errs = C.add_operating_points(world, cost, H, origin, cell, spec.truck,
                                     hfun=lambda xz: C.bilinear(H, origin, cell, xz, centred=True))
    face_errs, face_sum = C.check_face_order(world) if layout == "hillside" else ([], {})
    if face_sum:
        world["design"]["face_order"] = face_sum
    C.preserve_assets(out, world)                                    # keep pano / pano_source / surround
    C.write_json(out / "world.json", world)
    C.refresh_surround(out, world)                                   # surround.glb matches the new heightmap
    C.write_preview(out / "preview.png", H, col, cell, world)
    bad = C.check_reachability(world, nav)
    print(f"  sites: " + ", ".join(f"{x['id']}@{x['pos3']}" for x in sites))
    print(f"  roads ({len(roads)}, junctions {len(junctions)}): id from->to len | mean / sustained50 / max20 grade")
    for r in roads:
        L = sum(np.hypot(p[0] - q[0], p[2] - q[2]) for p, q in zip(r["points"][:-1], r["points"][1:]))
        print(f"    {r['id']:8s} {r['from']:>8s} -> {r['to']:<8s} {L:5.0f} m | {r['grade_pct']:4.1f} / "
              f"{sustained[r['id']]:4.1f} / {grades[r['id']]:4.1f} %{'  HAUL' if r['id'] in haul else ''}")
    by = {x["id"]: np.array(x["pos"]) for x in sites}
    print(f"  fuel<->crusher {np.linalg.norm(by['fuel'] - by['crusher']):.0f} m, parking<->crusher "
          f"{np.linalg.norm(by['parking'] - by['crusher']):.0f} m, fuel<->parking "
          f"{np.linalg.norm(by['fuel'] - by['parking']):.0f} m, dump<->crusher {np.linalg.norm(by['dump'] - by['crusher']):.0f} m")
    if steep:
        print(f"  design.notes exceptions: {[r['id'] for r in steep]}")
    for x in sites:
        for k in ("load_point", "dump_point"):
            if k in x:
                print(f"  {x['id']:8s} {k}: {x[k]['pos3']} hdg {x[k]['heading_deg']}")
        for i, q in enumerate(x.get("slots", [])):
            print(f"  {x['id']:8s} slot {i}: {q['pos3']} hdg {q['heading_deg']}")
    for e in world["equipment"]:
        print(f"  {e['id']} @ {e['face']}: {e['pos3']} hdg {e['heading_deg']}")
    if face_sum:
        print(f"  face order (Lower, Middle, Upper): {face_sum}")
    print(f"  road graph: {errs or 'OK'}; unreachable: {bad or 'none'}; operating points: {op_errs or 'OK'}; "
          f"face order: {face_errs or 'OK'}")
    assert not errs, errs
    assert not bad, bad
    assert not op_errs, op_errs
    assert not face_errs, face_errs
    print(f"wrote {out} in {time.time() - T0:.1f}s: {n}x{n} cells @ {cell:.2f} m, passable {(cost > 0).mean():.0%}")


if __name__ == "__main__":
    main()
