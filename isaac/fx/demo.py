import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DT = 1 / 30
SPEED = 8.0
GAP = 28.0
TRUCKS = 3
PATH_LENGTH = 2 * GAP + 7 * SPEED + 20.0
CAM_W, CAM_H = 640, 360
HFOV = 90.0
APERTURE = 20.955
CAMERA_HEIGHT = 0.8 * 4.4
CAMERA_FRONT = 9.35 / 2 + 0.2
CHASE = (-20.0, 9.0, 7.0)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--world", default="/data/worlds/quarry")
    p.add_argument("--cache", default="/cache")
    p.add_argument("--out", default="/out/demo")
    p.add_argument("--config", default=str(ROOT / "isaac/sim/config.json"))
    p.add_argument("--preset", default="high")
    p.add_argument("--grounds", default="road,dark")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--preroll", type=float, default=2.5)
    p.add_argument("--measure", default="none,low,high")
    p.add_argument("--measure-seconds", type=float, default=15.0)
    p.add_argument("--dust-brightness", type=float, default=1.0)
    p.add_argument("--dust-density", type=float, default=1.0)
    p.add_argument("--wind", type=float, nargs=3, default=[1.5, 0.5, 0.0])
    return p.parse_args()


def find_path(ground, dusty: bool, seed: int = 0, tries: int = 40000, inner: float = 330.0):
    rng = np.random.default_rng(seed)
    cov = np.argwhere(ground.coverage)
    s = np.arange(0.0, PATH_LENGTH, 2.0)
    check = np.ones(len(s), bool) if dusty else s >= 2 * GAP - 4.0
    max_slope, max_gap = (8.0, 0.8) if dusty else (10.0, 1.5)
    best, best_score = None, -np.inf
    for _ in range(tries):
        i, j = cov[rng.integers(len(cov))]
        start = ground.origin + (np.array([j, i]) + 0.5) * ground.cell
        yaw = rng.uniform(0, 360)
        d = np.array([math.sin(math.radians(yaw)), math.cos(math.radians(yaw))])
        pts = start + s[:, None] * d
        ii, jj = ground.index(pts[check, 0], pts[check, 1])
        if not ground.coverage[ii, jj].all() or ground.slope[ii, jj].max() > max_slope or np.abs(pts[check]).max() > inner:
            continue
        if np.abs(ground.surface[ii, jj] - ground.height[ii, jj]).max() > max_gap:
            continue
        dust = ground.dust[ii, jj]
        luma = ground.color[ii, jj] @ np.array([0.299, 0.587, 0.114])
        score = dust.mean() - dust.std() if dusty else -dust.mean() - dust.std() - 0.5 * luma.mean()
        if score > best_score:
            best, best_score = (start, yaw), score
    return best, best_score


def main() -> None:
    args = parse_args()
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RealTimePathTracing", "width": CAM_W, "height": CAM_H, "anti_aliasing": 0})
    import carb
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from PIL import Image
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

    from isaac.fx.fx import ROOT as FX_ROOT
    from isaac.fx.fx import TruckFx
    from isaac.fx.ground import load_or_bake

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
    settings = carb.settings.get_settings()
    for key, value in config.get("rtx", {}).items():
        settings.set(key, value)
    t0 = time.time()
    ground = load_or_bake(args.world, args.cache)
    print(f"DEMO ground map {ground.shape} in {time.time() - t0:.1f}s", flush=True)

    stage = omni.usd.get_context().get_stage()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    cache = Path(args.cache)
    newest = lambda pat: max((p for p in cache.glob(pat) if ".tmp" not in p.name), key=lambda p: p.stat().st_mtime)
    stage.DefinePrim("/World/Scan", "Xform").GetReferences().AddReference(str(newest("visual_*.usdc")))
    tint = config.get("scan", {}).get("tint")
    for prim in Usd.PrimRange(stage.GetPrimAtPath("/World/Scan")):
        if tint and prim.GetTypeName() == "Shader" and prim.GetAttribute("info:id").Get() == "UsdUVTexture":
            prim.CreateAttribute("inputs:scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*tint, 1.0))
    sun_cfg, dome_cfg = config.get("sun", {}), config.get("dome", {})
    dome = UsdLux.DomeLight.Define(stage, "/World/Sky")
    pano = Path(args.world) / "sky_pano.jpg"
    if pano.exists():
        dome.CreateTextureFileAttr(str(pano))
        dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
    dome.CreateIntensityAttr(float(dome_cfg.get("intensity", 700.0)))
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(float(sun_cfg.get("intensity", 3000.0)))
    sun.CreateAngleAttr(float(sun_cfg.get("angle", 0.53)))
    sun.CreateColorAttr(Gf.Vec3f(*sun_cfg.get("color", [1.0, 0.95, 0.88])))
    elev, azim = float(sun_cfg.get("elevation", 40.0)), float(sun_cfg.get("azimuth", 220.0))
    e, a = math.radians(elev), math.radians(azim)
    z = np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])
    x = np.cross([0.0, 0.0, 1.0], z)
    x /= np.linalg.norm(x)
    m = np.eye(4)
    m[:3, :3] = np.column_stack([x, np.cross(z, x), z])
    UsdGeom.Xformable(sun).AddTransformOp().Set(Gf.Matrix4d(m.T.tolist()))

    truck_usd = str(newest("truck_*.usdc"))
    ids = [f"truck-{k}" for k in range(TRUCKS)]
    ops, cams = [], []
    for tid in ids:
        path = f"/World/Trucks/{tid.replace('-', '_')}"
        ops.append(UsdGeom.Xform.Define(stage, path).AddTransformOp())
        stage.DefinePrim(path + "/body", "Xform").GetReferences().AddReference(truck_usd)
        cam = UsdGeom.Camera.Define(stage, path + "/front_camera")
        cam.CreateFocalLengthAttr(APERTURE / 2 / math.tan(math.radians(HFOV / 2)))
        cam.CreateHorizontalApertureAttr(APERTURE)
        cam.CreateVerticalApertureAttr(APERTURE * CAM_H / CAM_W)
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 8000.0))
        local = np.eye(4)
        local[:3, :3] = np.column_stack([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
        local[:3, 3] = [0.0, CAMERA_FRONT, CAMERA_HEIGHT]
        cam.AddTransformOp().Set(Gf.Matrix4d(local.T.tolist()))
        cams.append(cam.GetPath().pathString)
    chase = UsdGeom.Camera.Define(stage, "/World/Chase")
    chase.CreateFocalLengthAttr(APERTURE / 2 / math.tan(math.radians(60.0 / 2)))
    chase.CreateHorizontalApertureAttr(APERTURE)
    chase.CreateVerticalApertureAttr(APERTURE * CAM_H / CAM_W)
    chase_op = chase.AddTransformOp()
    names = ["chase", *ids]
    anns = []
    for path in ["/World/Chase", *cams]:
        rp = rep.create.render_product(path, (CAM_W, CAM_H))
        ann = rep.AnnotatorRegistry.get_annotator("rgb")
        ann.attach(rp)
        anns.append(ann)

    timeline = omni.timeline.get_timeline_interface()
    timeline.set_time_codes_per_second(1 / DT)
    timeline.set_target_framerate(1 / DT)
    settings.set("/app/player/useFixedTimeStepping", True)
    timeline.play()

    def pose(start, yaw, s):
        f = np.array([math.sin(math.radians(yaw)), math.cos(math.radians(yaw))])
        right = np.array([math.cos(math.radians(yaw)), -math.sin(math.radians(yaw)), 0.0])
        p = start + f * s
        hf = float(ground.height_at(*(p + f * 2.1)))
        hr = float(ground.height_at(*(p - f * 2.1)))
        hl = float(ground.height_at(*(p - right[:2] * 1.5)))
        hrr = float(ground.height_at(*(p + right[:2] * 1.5)))
        fwd = np.array([f[0] * 4.2, f[1] * 4.2, hf - hr])
        fwd /= np.linalg.norm(fwd)
        side = np.array([right[0] * 3.0, right[1] * 3.0, hrr - hl])
        up = np.cross(side, fwd)
        up /= np.linalg.norm(up)
        rx = np.cross(fwd, up)
        pos = np.array([p[0], p[1], (hf + hr + hl + hrr) / 4])
        mat = np.eye(4)
        mat[:3, :3] = np.column_stack([rx, fwd, up])
        mat[:3, 3] = pos
        return pos, mat

    def look(eye, target):
        return Gf.Matrix4d().SetLookAt(Gf.Vec3d(*map(float, eye)), Gf.Vec3d(*map(float, target)), Gf.Vec3d(0, 0, 1)).GetInverse()

    def step_scene(fx, start, yaw, s, speed):
        states = []
        for k, (tid, op) in enumerate(zip(ids, ops)):
            pos, mat = pose(start, yaw, s - k * GAP)
            op.Set(Gf.Matrix4d(mat.T.tolist()))
            states.append((tid, pos, yaw, speed, None))
        lead = states[0][1]
        f = np.array([math.sin(math.radians(yaw)), math.cos(math.radians(yaw)), 0.0])
        r = np.array([math.cos(math.radians(yaw)), -math.sin(math.radians(yaw)), 0.0])
        chase_op.Set(look(lead + f * CHASE[0] + r * CHASE[1] + [0, 0, CHASE[2]], lead - f * 6 + [0, 0, 1.5]))
        if fx is not None:
            fx.update(DT, states, ground.height_at)

    def make_fx(preset):
        if stage.GetPrimAtPath(FX_ROOT):
            stage.RemovePrim(FX_ROOT)
        if preset == "none":
            return None
        return TruckFx(stage, ids, preset, ground, wind=args.wind, seed=1, dust_brightness=args.dust_brightness, dust_density=args.dust_density,
                       sun_intensity=float(sun_cfg.get("intensity", 3000.0)), sun_elevation=elev, sun_azimuth=azim, dome_intensity=float(dome_cfg.get("intensity", 700.0)))

    def frames():
        return [np.asarray(a.get_data())[..., :3] for a in anns]

    report = {}
    for kind in [g for g in args.grounds.split(",") if g]:
        (start, yaw), score = find_path(ground, kind == "road")
        ii, jj = ground.index(*(start + 0.0))
        print(f"DEMO path {kind}: start {start.round(1)} yaw {yaw:.0f} score {score:.2f} dust@start {ground.dust[ii, jj]:.2f} colour {ground.color[ii, jj].round(2)}", flush=True)
        fx = make_fx(args.preset)
        s = 2 * GAP
        for _ in range(int(args.preroll / DT)):
            step_scene(fx, start, yaw, s, SPEED)
            app.update()
            s += SPEED * DT
        strips = {n: [] for n in names}
        t_prev = timeline.get_current_time()
        n_frames = int(args.seconds / DT)
        for f in range(n_frames):
            step_scene(fx, start, yaw, s, SPEED)
            app.update()
            s += SPEED * DT
            if f % 15 == 0:
                imgs = frames()
                for n, img in zip(names, imgs):
                    strips[n].append(img)
                    if n in ("chase", "truck-1"):
                        Image.fromarray(img).save(out / f"{kind}_{n}_{f:03d}.png")
        dt_tl = (timeline.get_current_time() - t_prev) / n_frames
        for n in ("chase", "truck-1"):
            Image.fromarray(np.concatenate(strips[n], axis=1)).save(out / f"{kind}_{n}_strip.png")
        st = fx.surface_state("truck-0") if fx else None
        report[kind] = {"start": start.tolist(), "yaw": yaw, "timeline_dt": dt_tl, "fx_cpu_ms": fx.timing() if fx else 0.0,
                        "dust": None if st is None else st["dust"].tolist(), "gravel": None if st is None else st["gravel"].tolist(),
                        "stones": fx.gravel.count() if fx else 0}
        print(f"DEMO {kind}: {report[kind]}", flush=True)

    (start, yaw), _ = find_path(ground, True)
    for preset in [p for p in args.measure.split(",") if p]:
        fx = make_fx(preset)
        s = 2 * GAP
        for _ in range(30):
            step_scene(fx, start, yaw, s, SPEED)
            app.update()
            s += SPEED * DT
        result = {}
        for mode in ("free", "30fps"):
            n, t_start, cpu = 0, time.time(), 0.0
            wall0 = time.time()
            nxt = time.perf_counter()
            while time.time() - t_start < args.measure_seconds:
                c0 = time.perf_counter()
                step_scene(fx, start, yaw, s, SPEED)
                cpu += time.perf_counter() - c0
                app.update()
                frames()
                s += SPEED * DT
                n += 1
                if mode == "30fps":
                    nxt += DT
                    delay = nxt - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        nxt = time.perf_counter()
            span = time.time() - t_start
            result[mode] = {"fps": n / span, "step_ms": cpu / n * 1000, "wall": [wall0, time.time()]}
        result["fx_cpu_ms"] = fx.timing() if fx else 0.0
        report[f"measure_{preset}"] = result
        print(f"DEMO measure {preset}: {json.dumps(result)}", flush=True)
    (out / "report.json").write_text(json.dumps(report, indent=1))
    timeline.stop()
    app.close()


if __name__ == "__main__":
    main()
