import argparse
import copy
import json
import math
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STILL_W, STILL_H = 960, 540
WARM = 12


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--world", default=str(ROOT / "data/worlds/quarry"))
    p.add_argument("--cache", default=str(ROOT / "downloads/isaac/cache"))
    p.add_argument("--out", default="/out")
    p.add_argument("--plan", default="")
    p.add_argument("--frames", type=int, default=90)
    p.add_argument("--no-fps", action="store_true")
    p.add_argument("--no-stills", action="store_true")
    p.add_argument("--sky-cams", action="store_true")
    return p.parse_args()


def default_plan() -> list[dict]:
    from isaac.look.look import PRESETS

    return [{"name": "before", "before": True}] + [{"name": n, "preset": n} for n in json.loads(PRESETS.read_text())["presets"]]


def gpu_sampler():
    try:
        return subprocess.Popen(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits", "-lms", "250"], stdout=subprocess.PIPE, text=True)
    except OSError:
        return None


def gpu_mean(proc) -> float | None:
    if proc is None:
        return None
    proc.terminate()
    vals = [float(v) for v in proc.communicate()[0].split() if v.replace(".", "").isdigit()]
    return sum(vals) / len(vals) if vals else None


def null_rep(rep):
    ann = types.SimpleNamespace(attach=lambda *a, **k: None, get_data=lambda: [])
    return types.SimpleNamespace(
        create=types.SimpleNamespace(render_product=lambda *a, **k: None),
        AnnotatorRegistry=types.SimpleNamespace(get_annotator=lambda *a, **k: ann),
        orchestrator=rep.orchestrator,
    )


def main() -> None:
    args = parse_args()
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RealTimePathTracing", "width": 640, "height": 360})
    import numpy as np
    import omni.replicator.core as rep
    from PIL import Image
    from pxr import Gf, UsdGeom

    import isaac.sim.scene as scene
    from isaac.look.look import apply_look
    from isaac.sim.sensors import CAM_H, CAM_W
    from isaac.sim.sim import Sim
    from isaac.sim.usdbuild import to_isaac

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text()) if args.plan else default_plan()
    config = json.loads((ROOT / "isaac/sim/config.json").read_text())
    config["views"] = []
    sim = Sim(args.world, raycast=True, views=[])
    real_rep, scene.rep = scene.rep, null_rep(rep)
    r = scene.IsaacRenderer(app, sim, types.SimpleNamespace(cache=args.cache, fx="off"), config)
    scene.rep = real_rep
    stage = r.stage
    world_dir = Path(args.world)
    snapshot = copy.deepcopy([t.__dict__ for t in sim.trucks]), copy.deepcopy(sim.follow)

    def reset():
        for t, d in zip(sim.trucks, snapshot[0]):
            t.__dict__.update(copy.deepcopy(d))
        sim.follow = copy.deepcopy(snapshot[1])
        sim.time = 0.0
        r.update(sim, 0.0)

    def drive(dt=1 / 30):
        now = sim.time
        for t in sim.trucks:
            sim.command(t.id, 0.35, 0.0, now)
        sim.step(dt, now)
        r.update(sim, dt)

    def step():
        rep.orchestrator.step(rt_subframes=1, pause_timeline=False)

    def apply(item):
        if item.get("before"):
            return None
        return apply_look(stage, item.get("preset", "day"), world_dir=world_dir, cache=Path(args.cache) / "look", **item.get("overrides", {}))

    results = {}
    if not args.no_fps:
        truck_cams = [f"/World/Trucks/{t.id.replace('-', '_')}/front_camera" for t in sim.trucks]
        rp = rep.create.render_product_tiled(truck_cams, (CAM_W, CAM_H))
        ann = rep.AnnotatorRegistry.get_annotator("rgb")
        ann.attach(rp)
        for item in plan:
            reset()
            apply(item)
            for _ in range(WARM):
                drive()
                step()
            gpu = gpu_sampler()
            hashes, t0 = set(), time.time()
            for _ in range(args.frames):
                drive()
                step()
                hashes.add(hash(np.asarray(ann.get_data())[::7, ::7].tobytes()))
            dt = time.time() - t0
            util = gpu_mean(gpu)
            results[item["name"]] = {"fresh_fps": round(len(hashes) / dt, 2), "step_fps": round(args.frames / dt, 2), "gpu_util": None if util is None else round(util, 1)}
            Image.fromarray(np.asarray(ann.get_data())[..., :3]).save(out / f"tiled_{item['name']}.jpg", quality=92)
            print(f"LOOKBENCH {item['name']} {json.dumps(results[item['name']])}", flush=True)
        ann.detach()
        (out / "fps.json").write_text(json.dumps(results, indent=1))

    if args.no_stills:
        app.close()
        return
    reset()
    sites = np.array([to_isaac(s.pos) for s in sim.world.sites])
    road = np.array([to_isaac(p) for rd in sim.world.roads for p in rd.points])
    centre = sites.mean(axis=0)
    radius = float(np.percentile(np.linalg.norm(road[:, :2] - centre[:2], axis=1), 90))
    t0 = sim.trucks[0]
    base = to_isaac(np.array([t0.x, t0.y, t0.z]))

    def look_cam(name, eye, target, hfov):
        cam = UsdGeom.Camera.Define(stage, f"/World/LookCams/{name}")
        cam.CreateFocalLengthAttr(20.955 / 2 / math.tan(math.radians(hfov / 2)))
        cam.CreateHorizontalApertureAttr(20.955)
        cam.CreateVerticalApertureAttr(20.955 * STILL_H / STILL_W)
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.5, 20000))
        m = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*map(float, eye)), Gf.Vec3d(*map(float, target)), Gf.Vec3d(0, 0, 1)).GetInverse()
        UsdGeom.Xformable(cam).AddTransformOp().Set(m)
        return cam.GetPath().pathString

    a, e = math.radians(225.0), math.radians(38.0)
    dist = radius * 1.9
    pit_eye = centre + dist * np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])
    cams = {
        "road": f"/World/Trucks/{t0.id.replace('-', '_')}/front_camera",
        "pit": look_cam("pit", pit_eye, centre, 60.0),
        "horizon": look_cam("horizon", base + [0, 0, 30], base + [math.sin(math.radians(40)) * 500, math.cos(math.radians(40)) * 500, 30 + 500 * math.tan(math.radians(14))], 100.0),
    }
    if args.sky_cams:
        for az in (0, 90, 180, 270):
            d = [math.sin(math.radians(az)), math.cos(math.radians(az)), math.tan(math.radians(25))]
            cams[f"sky{az:03d}"] = look_cam(f"sky{az:03d}", base + [0, 0, 40], base + [0, 0, 40] + np.array(d) * 100, 90.0)
    anns = {}
    for name, path in cams.items():
        prod = rep.create.render_product(path, (STILL_W, STILL_H))
        an = rep.AnnotatorRegistry.get_annotator("rgb")
        an.attach(prod)
        anns[name] = an
    for item in plan:
        look = apply(item)
        for _ in range(WARM + 6):
            step()
        stats = {}
        for name, an in anns.items():
            img = np.asarray(an.get_data())[..., :3]
            Image.fromarray(img).save(out / f"{name}_{item['name']}.jpg", quality=92)
            stats[name] = [round(float(img.mean()), 1), round(float((img.max(axis=2) >= 250).mean() * 100), 2)]
        extra = {} if look is None else {"sun_elev": round(look.elevation, 1), "sun_az": round(look.azimuth, 1), "dome_rot": round(look.dome_rotation, 1)}
        print(f"LOOKSTILL {item['name']} mean/clip%={json.dumps(stats)} {json.dumps(extra)}", flush=True)
    app.close()


if __name__ == "__main__":
    main()
