import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DT = 1 / 30
TRUCK_AT = (-5.4, -0.5)
LIFT_DEG = 25.0
CAMERAS = {
    "oblique": ((-22.0, -20.0, 13.0), (-4.0, -1.0, 3.0), 55.0, 1280, 720),
    "side": ((-5.0, -26.0, 5.0), (-4.0, -1.0, 3.5), 45.0, 1280, 720),
    "bed": ((-5.4, -7.0, 17.0), (-5.4, -1.6, 3.0), 40.0, 960, 720),
    "bucket": ((3.0, -11.0, 8.0), (-4.0, -1.5, 4.5), 50.0, 960, 720),
}


def look_at(eye, target) -> "np.ndarray":
    import numpy as np

    eye, target = np.array(eye, float), np.array(target, float)
    back = eye - target
    back /= np.linalg.norm(back)
    right = np.cross([0.0, 0.0, 1.0], back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = right, up, back, eye
    return m


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--cycles", type=int, default=3)
    p.add_argument("--save-every", type=int, default=6)
    p.add_argument("--no-material", action="store_true")
    p.add_argument("--perf", action="store_true")
    args = p.parse_args()

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RealTimePathTracing", "width": 1280, "height": 720})

    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from PIL import Image
    from pxr import Gf, UsdGeom, UsdLux, Vt

    from isaac.material import rig as R
    from isaac.material.stage import BucketMaterial, gf
    from isaac.sim import world as W
    from isaac.sim.usdbuild import convert, sun_matrix, write_mesh_usd

    out, cache = Path(args.out), Path(args.cache)
    out.mkdir(parents=True, exist_ok=True)
    stage = omni.usd.get_context().get_stage()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    ground = UsdGeom.Mesh.Define(stage, "/World/Ground")
    ground.CreatePointsAttr([Gf.Vec3f(-200, -200, 0), Gf.Vec3f(200, -200, 0), Gf.Vec3f(200, 200, 0), Gf.Vec3f(-200, 200, 0)])
    ground.CreateFaceVertexCountsAttr([4])
    ground.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    ground.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.27, 0.22)])
    dome = UsdLux.DomeLight.Define(stage, "/World/Sky")
    dome.CreateIntensityAttr(700.0)
    pano = ROOT / "data/worlds/quarry/sky_pano.jpg"
    if pano.exists():
        dome.CreateTextureFileAttr(str(pano))
        dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(3000.0)
    sun.CreateAngleAttr(0.53)
    sun.CreateColorAttr(Gf.Vec3f(1.0, 0.95, 0.88))
    UsdGeom.Xformable(sun).AddTransformOp().Set(sun_matrix(40.0, 220.0))

    base = R.base_matrix(0.0, 0.0, 0.0, 0.0)
    rig = R.ExcavatorRig("exc-0", base, phase=0.9, lift_deg=LIFT_DEG)
    ops = {}
    for part, glb in R.part_glbs().items():
        usd = cache / f"material_exc_{part}.usdc"
        if not usd.exists():
            write_mesh_usd(glb, usd, part)
        xf = UsdGeom.Xform.Define(stage, f"/World/Excavator/{part}")
        ops[part] = xf.AddTransformOp()
        stage.DefinePrim(f"/World/Excavator/{part}/mesh", "Xform").GetReferences().AddReference(str(usd))

    truck_m, _ = W.truck_box()
    truck_usd = convert(W.REPO / W.TRUCK_GLB, cache, "truck", truck_m)
    truck = np.eye(4)
    truck[:3, 3] = [*TRUCK_AT, 0.0]
    txf = UsdGeom.Xform.Define(stage, "/World/Trucks/truck_0")
    txf.AddTransformOp().Set(gf(truck))
    stage.DefinePrim("/World/Trucks/truck_0/body", "Xform").GetReferences().AddReference(str(truck_usd))

    mat = None
    if not args.no_material:
        mat = BucketMaterial(stage, [{"id": "exc-0", "ground": 0.0, "colour": (0.20, 0.15, 0.11)}],
                             [{"id": "truck-0", "prim": "/World/Trucks/truck_0"}], {"seed": 1})

    annotators = []
    for name, (eye, target, hfov, w, h) in CAMERAS.items():
        cam = UsdGeom.Camera.Define(stage, f"/World/Cams/{name}")
        cam.CreateFocalLengthAttr(20.955 / 2 / math.tan(math.radians(hfov / 2)))
        cam.CreateHorizontalApertureAttr(20.955)
        cam.CreateVerticalApertureAttr(20.955 * h / w)
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 2000.0))
        cam.AddTransformOp().Set(gf(look_at(eye, target)))
        rp = rep.create.render_product(cam.GetPath().pathString, (w, h))
        ann = rep.AnnotatorRegistry.get_annotator("rgb")
        ann.attach(rp)
        annotators.append((name, ann))

    def pose_rig():
        for part, m in rig.matrices().items():
            ops[part].Set(gf(m))

    pose_rig()
    omni.timeline.get_timeline_interface().play()
    for _ in range(30):
        app.update()

    frames = int(args.cycles * R.CYCLE_S / DT)
    tail = int(4.0 / DT)
    mat_ms, frame_ms, log = [], [], []
    start = time.perf_counter()
    for i in range(frames + tail):
        t0 = time.perf_counter()
        if i < frames:
            rig.step(DT, True)
        else:
            rig.step(DT, False)
        pose_rig()
        name, pt = rig.state()
        if mat is not None:
            if i == frames + 15:
                mat.empty_truck("truck-0")
            t1 = time.perf_counter()
            mat.update(DT, [("exc-0", rig.bucket(), name, pt)], [("truck-0", truck, "exc-0")])
            mat_ms.append((time.perf_counter() - t1) * 1000)
        app.update()
        frame_ms.append((time.perf_counter() - t0) * 1000)
        if mat is not None and i % 15 == 0:
            a = mat.model.audit()
            log.append({"t": round(i * DT, 2), "phase": name, "bucket_t": round(a["bucket"], 2), "bed_t": round(a["beds"], 2),
                        "pile_t": round(a["piles"], 2), "air_t": round(a["air"], 2), "load": round(mat.load("truck-0"), 3)})
        if not args.perf and i % args.save_every == 0:
            for cname, ann in annotators:
                data = np.asarray(ann.get_data())
                if data.ndim == 3 and data.size:
                    Image.fromarray(data[..., :3]).save(out / f"{cname}_{i:05d}.jpg", quality=88)
    span = time.perf_counter() - start
    stats = {"frames": frames + tail, "fps": (frames + tail) / span, "frame_ms_mean": float(np.mean(frame_ms)),
             "material_ms_mean": float(np.mean(mat_ms)) if mat_ms else 0.0, "material_ms_p95": float(np.percentile(mat_ms, 95)) if mat_ms else 0.0,
             "material": mat is not None, "cameras": len(annotators)}
    print(json.dumps(stats), flush=True)
    (out / ("perf_off.json" if args.no_material else "perf.json")).write_text(json.dumps(stats, indent=1))
    (out / "log.json").write_text(json.dumps(log, indent=1)) if log else None
    app.close()


if __name__ == "__main__":
    main()
