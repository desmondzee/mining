"""First-run smoke test for the team Isaac pipeline (isaac/sim) on watson.

Runs inside Isaac Sim's python (headless), builds the same scene as isaac/sim/main.py (IsaacRenderer + Sim with Warp
raycasts), steps it, and prints a PASS/FAIL table. It never talks to the brain unless --brain is given (then it
only opens and closes a TCP connection).

    # inside the container (run_watson.sh smoke does this for you)
    /isaac-sim/python.sh /mining/isaac/smoke_test.py --world /data/worlds/quarry --cache /cache --out /logs/smoke
    # native install
    $ISAACSIM_PATH/python.sh isaac/smoke_test.py
    # no Isaac (Mac / CI): checks world, Warp raycasts, ground follow, drive, excavator cycle; no RTX
    uv run --with numpy --with pillow --with warp-lang python isaac/smoke_test.py --dry-run --excavators

Checks (each one is independent; a missing API is reported as SKIP/FAIL with the reason, never a crash):
  python        Python >= 3.12 (isaac/sim/main.py uses 3.12 f-string syntax; Isaac 6.x = 3.12, Isaac 5.1 = 3.11)
  isaac_app     SimulationApp starts headless; Isaac / Kit version
  warp          Warp importable -> raycasts on (depth + lidar + ground follow); off = no depth/lidar in the brain
  world         world.json loads, 3 trucks spawned
  guard         INFO: KeepInBounds in isaac/sim (only after isaac/patches/keep_in_bounds.patch; --keep-in-bounds)
  stage         IsaacRenderer built the stage (scan, trucks, cameras, rgb annotators)
  settle        300 physics frames at 1/60 s (+ RTX renders every --render-every frames), fps
  ground:<id>   Warp ray straight down hits terrain under the spawn, truck y within tolerance of it
  prim:<id>     USD truck prim pose == sim pose through the isaac/sim axis swap (x, z, y)
  rgb:<id>      RTX rgb annotator frame not empty / not black, PNG saved
  depth:<id>    64x36 depth stats (min / median / max, % misses)
  lidar:<id>    lidar point count (Warp 240x30 pattern, <= 7200)
  excavators    (--excavators) isaac/excavators.py installed: key source per machine, radio URL
  stage         ... and with --excavators the rigged parts under /World/Equipment/<id>/rig, static model inactive
  exc:<id>      (--excavators) dig cycle advanced during settle (house swing range), caster part boxes, USD joint moved
  drive         truck-0 throttle 0.5 / steer 0.2 for 5 s: moves, stays on the ground, stays inside bounds
  rtx_lidar     INFO only: is the isaacsim.sensors.rtx extension importable (isaac/sim does not use it)
  brain         INFO only (with --brain host:port): TCP connect
Outputs: <out>/report.json, <out>/<camera>.png (+ <camera>_after_drive.png), <out>/<truck>_lidar.npy
Exit code: 0 when no check FAILs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PHYSICS_DT = 1.0 / 60.0
GROUND_TOL = 1.5          # m: truck y (mean of 4 wheel rays) vs centre ray; slopes make this differ a little
DRIVE_THROTTLE, DRIVE_STEER = 0.5, 0.2


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, status: str, detail: str = "", **data) -> None:
        self.rows.append({"check": name, "status": status, "detail": detail, **data})
        print(f"[smoke] {status:<4} {name}: {detail}", flush=True)

    def run(self, name: str, fn, *a, **kw):
        """Run fn; an exception becomes FAIL (or SKIP for ImportError, i.e. API unavailable)."""
        try:
            return fn(*a, **kw)
        except ImportError as e:
            self.add(name, "SKIP", f"API unavailable: {e}")
        except Exception as e:  # noqa: BLE001
            self.add(name, "FAIL", f"{type(e).__name__}: {e}")
            traceback.print_exc()
        return None

    def failed(self) -> bool:
        return any(r["status"] == "FAIL" for r in self.rows)

    def table(self) -> str:
        w = max(len(r["check"]) for r in self.rows) if self.rows else 10
        lines = [f"{'CHECK':<{w}}  STATUS  DETAIL", f"{'-' * w}  ------  {'-' * 60}"]
        lines += [f"{r['check']:<{w}}  {r['status']:<6}  {r['detail']}" for r in self.rows]
        n = {s: sum(r["status"] == s for r in self.rows) for s in ("PASS", "FAIL", "WARN", "SKIP", "INFO")}
        lines.append(f"\nRESULT: {'FAIL' if self.failed() else 'PASS'}  " + " ".join(f"{k}={v}" for k, v in n.items()))
        return "\n".join(lines)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--world", default=str(ROOT / "data/worlds/quarry"))
    p.add_argument("--cache", default=str(ROOT / "downloads/isaac/cache"))
    p.add_argument("--out", default=str(ROOT / "isaac/logs/smoke"))
    p.add_argument("--config", default=str(ROOT / "isaac/sim/config.json"))
    p.add_argument("--renderer", default="", help="override config.json renderer (e.g. RaytracedLighting = faster)")
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--render-every", type=int, default=10, help="RTX render every N physics frames while settling")
    p.add_argument("--drive-seconds", type=float, default=5.0)
    p.add_argument("--keep-in-bounds", action="store_true", help="Sim(keep_in_bounds=True); needs isaac/patches/keep_in_bounds.patch")
    p.add_argument("--excavators", action="store_true", help="install isaac/excavators.py (rigged, animated excavators)")
    p.add_argument("--radio-url", default=None, help="radio for the excavators (default $RADIO_URL; '' = off)")
    p.add_argument("--brain", default="", help="host:port, INFO-only TCP connect test")
    p.add_argument("--dry-run", action="store_true", help="no Isaac: skip SimulationApp / stage / RTX checks")
    a, _ = p.parse_known_args(argv)
    return a


# ---------------------------------------------------------------------------------------------------------- helpers
def isaac_version() -> str:
    for fn in (lambda: __import__("isaacsim.core.version", fromlist=["get_version"]).get_version(),
               lambda: __import__("omni.isaac.version", fromlist=["get_version"]).get_version()):
        try:
            v = fn()
            return ".".join(str(x) for x in v[:3]) if isinstance(v, (list, tuple)) else str(v)
        except Exception:  # noqa: BLE001
            continue
    try:
        import omni.kit.app
        return "kit " + omni.kit.app.get_app().get_build_version()
    except Exception:  # noqa: BLE001
        return "unknown"


def footprint_inside(t, bounds, half_w=2.12, half_l=4.7) -> float:
    """Smallest distance (m) from any footprint corner to the world bounds; < 0 = outside."""
    s, c = math.sin(math.radians(t.yaw)), math.cos(math.radians(t.yaw))
    xmin, zmin, xmax, zmax = bounds
    best = math.inf
    for a in (half_l, -half_l):
        for b in (half_w, -half_w):
            x, z = t.x + s * a + c * b, t.z + c * a - s * b
            best = min(best, x - xmin, xmax - x, z - zmin, zmax - z)
    return best


def save_png(path: Path, rgb) -> str:
    import numpy as np
    try:
        from PIL import Image
        Image.fromarray(np.ascontiguousarray(rgb[..., :3]).astype(np.uint8)).save(path)
        return str(path)
    except ImportError:
        np.save(path.with_suffix(".npy"), rgb)
        return str(path.with_suffix(".npy"))


# ---------------------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    a = parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()
    t_start = time.time()

    # python version (isaac/sim/main.py:91 nested quotes in an f-string need 3.12)
    py = sys.version_info
    rep.add("python", "PASS" if py >= (3, 12) else "FAIL", f"{py.major}.{py.minor}.{py.micro}"
            + ("" if py >= (3, 12) else " < 3.12: isaac/sim/main.py will not even parse (Isaac 5.1?). Use the 6.2 container"))

    config = json.loads(Path(a.config).read_text())
    if a.renderer:
        config["renderer"] = a.renderer

    app = None
    if not a.dry_run:
        def start_app():
            from isaacsim import SimulationApp
            return SimulationApp({"headless": True, "renderer": config.get("renderer", "RealTimePathTracing"),
                                  "width": 640, "height": 360})
        app = rep.run("isaac_app", start_app)
        if app is not None:
            rep.add("isaac_app", "PASS", f"headless, renderer {config.get('renderer')}, Isaac {isaac_version()}")
    else:
        rep.add("isaac_app", "SKIP", "--dry-run")

    try:
        return _body(a, rep, app, config, out, t_start)
    finally:
        (out / "report.json").write_text(json.dumps({"ok": not rep.failed(), "seconds": round(time.time() - t_start, 1),
                                                     "world": a.world, "checks": rep.rows}, indent=1, default=str))
        print("\n" + rep.table(), flush=True)
        print(f"[smoke] report: {out / 'report.json'}", flush=True)
        if app is not None:
            try:
                app.close()
            except Exception:  # noqa: BLE001
                pass


def _body(a, rep: Report, app, config: dict, out: Path, t_start: float) -> int:
    import numpy as np

    # warp (same rule as isaac/sim/main.py raycast_enabled("auto"), inlined so a 3.11 python can still report)
    try:
        import warp  # noqa: F401
        raycast = True
        rep.add("warp", "PASS", f"warp {getattr(warp, '__version__', '?')}")
    except ImportError as e:
        raycast = False
        rep.add("warp", "FAIL", f"not importable ({e}): no depth, no lidar, heightmap ground. "
                "Inside Isaac enable omni.warp.core or pip install warp-lang into ./python.sh")

    from isaac.sim.sim import Sim

    import inspect

    kib_supported = "keep_in_bounds" in inspect.signature(Sim.__init__).parameters
    kw = {"keep_in_bounds": True} if a.keep_in_bounds and kib_supported else {}
    sim = rep.run("world", Sim, a.world, raycast=raycast, views=[], **kw)
    if sim is None:
        return 1
    ids = [t.id for t in sim.trucks]
    rep.add("world", "PASS" if len(ids) == 3 else "WARN", f"{sim.world.id}: trucks {ids}, bounds {sim.world.bounds}, "
            f"excavators {len(sim.excavators)}, walkers {len(sim.walkers)}, caster {'warp' if sim.caster else 'none'}")

    # KeepInBounds lives in isaac/sim only once isaac/patches/keep_in_bounds.patch is applied (RL: isaac/rl_env.py wrapper)
    guarded = bool(kw) and getattr(sim.trucks[0], "guard", None) is not None
    if guarded:
        g = sim.trucks[0].guard
        rep.add("guard", "INFO", f"KeepInBounds on: inner box {g.lo.tolist()}..{g.hi.tolist()}")
    elif a.keep_in_bounds and not kib_supported:
        rep.add("guard", "WARN", "--keep-in-bounds given but isaac/sim has no KeepInBounds (apply isaac/patches/keep_in_bounds.patch)")
    else:
        rep.add("guard", "INFO", f"off (isaac/sim {'supports --keep-in-bounds' if kib_supported else 'without the keep_in_bounds patch'})")

    # stage + RTX
    renderer = None
    if app is not None:
        def make_stage():
            from isaac.sim.scene import IsaacRenderer
            t0 = time.time()
            r = IsaacRenderer(app, sim, argparse.Namespace(cache=a.cache, fx="off"), config)
            names = [n for n, _, _ in r.annotators]
            stage = r.stage
            missing = [p for p in ["/World/Scan", "/World/Sky", "/World/Overview",
                                   *(f"/World/Trucks/{i.replace('-', '_')}/front_camera" for i in ids)]
                       if not stage.GetPrimAtPath(p)]
            rep.add("stage", "FAIL" if missing else "PASS",
                    f"built in {time.time() - t0:.1f}s, cameras {names}" + (f", missing {missing}" if missing else ""))
            return r
        renderer = rep.run("stage", make_stage)
    else:
        rep.add("stage", "SKIP", "no SimulationApp")

    excavators = None
    if a.excavators:
        def install_excavators():
            from isaac.excavators import install
            ex = install(sim, renderer, a.radio_url, cache=a.cache)
            if not ex.items:
                rep.add("excavators", "WARN", "world.json has no excavators")
                return ex
            srcs = ", ".join(f"{x.id}: {x.source}" for x in ex.items)
            radio = ex.links[0].url if ex.links else "off"
            rep.add("excavators", "PASS", f"{len(ex.items)} animated ({srcs}); radio {radio}; usd parts {'yes' if ex.ops else 'no'}")
            return ex
        excavators = rep.run("excavators", install_excavators)
        house_seen = {}

    # settle: N physics frames (no commands), render every k frames
    def settle():
        t0 = time.time()
        renders = 0
        for k in range(a.frames):
            sim.step(PHYSICS_DT, sim.time + PHYSICS_DT)
            if excavators is not None:
                for x in excavators.items:
                    house_seen.setdefault(x.id, []).append(float(x.pose[0]))
            if renderer is not None:
                renderer.update(sim, PHYSICS_DT)
                if a.render_every and k % a.render_every == 0:
                    renderer.render()
                    renders += 1
        sim.sync_obstacles()
        el = time.time() - t0
        rep.add("settle", "PASS", f"{a.frames} frames in {el:.1f}s ({a.frames / max(el, 1e-6):.0f} fps, {renders} RTX renders)")
    rep.run("settle", settle)

    if excavators is not None and excavators.items:
        def check_excavator(x):
            swing = max(house_seen.get(x.id, [0])) - min(house_seen.get(x.id, [0]))
            moved = bool(np.abs(x.pose).max() > 1.0)
            detail = f"pose (house, boom, stick, bucket) {np.round(x.pose, 1).tolist()}, house swing {swing:.0f} deg over settle, " \
                     f"digging {x.digging}"
            if sim.caster is not None:
                detail += f", caster boxes {sim.caster.nbox} (trucks {len(sim.trucks)} + {len(excavators.items)} x 5 parts)"
            if excavators.ops:
                from pxr import UsdGeom  # noqa: F401
                base = f"/World/Equipment/{x.id.replace('-', '_')}"
                model = renderer.stage.GetPrimAtPath(base + "/model")
                boom = renderer.stage.GetPrimAtPath(base + "/rig/boom")
                detail += f", rig {'ok' if boom else 'MISSING'}, static model {'inactive' if model and not model.IsActive() else 'active?'}"
            ok = moved or not x.digging
            rep.add(f"exc:{x.id}", "PASS" if ok else "FAIL", detail + ("" if ok else " (digging but did not move)"))
        for x in excavators.items:
            rep.run(f"exc:{x.id}", check_excavator, x)
        for link in excavators.links:
            rep.add("radio", "INFO", f"GET {link.url}: polls {link.polls}, errors {link.errors}, states {link.states}")

    # ground under each spawn (Warp ray down = what isaac/sim's GroundFollow uses; PhysX has no colliders here)
    for t in sim.trucks:
        def ground(t=t):
            if sim.caster is None:
                rep.add(f"ground:{t.id}", "SKIP", "no Warp caster (heightmap ground)")
                return
            g = sim.caster.height(t.x, t.z)
            if not math.isfinite(g):
                rep.add(f"ground:{t.id}", "FAIL", f"ray down at ({t.x:.1f}, {t.z:.1f}) missed collider + surround")
                return
            err = t.y - g
            rep.add(f"ground:{t.id}", "PASS" if abs(err) <= GROUND_TOL else "FAIL",
                    f"spawn ({t.x:.1f}, {t.z:.1f}) yaw {t.yaw:.0f}: ground {g:.2f}, truck y {t.y:.2f} (err {err:+.2f} m)")
        rep.run(f"ground:{t.id}", ground)

    # USD prim pose == sim pose (axis swap x, z, y)
    if renderer is not None:
        for i, t in enumerate(sim.trucks):
            def prim(i=i, t=t):
                from pxr import UsdGeom
                p = renderer.stage.GetPrimAtPath(f"/World/Trucks/{t.id.replace('-', '_')}")
                m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
                tr = np.array(m.ExtractTranslation())
                want = np.array([t.x, t.z, t.y])
                d = float(np.linalg.norm(tr - want))
                rep.add(f"prim:{t.id}", "PASS" if d < 0.05 else "FAIL", f"USD {np.round(tr, 2).tolist()} vs sim(x,z,y) "
                        f"{np.round(want, 2).tolist()} (|d| {d:.3f} m)")
            rep.run(f"prim:{t.id}", prim)

    # sensors
    def grab_images(suffix: str = "") -> dict:
        if renderer is None:
            return {}
        imgs = {}
        for _ in range(4):          # RTX annotators lag a frame or two; take the last
            imgs = renderer.render()
        for name, rgb in imgs.items():
            if suffix and name not in ids:
                continue
            save_png(out / f"{name}{suffix}.png", rgb)
        return imgs

    images = rep.run("rgb", grab_images) or {}
    depths = rep.run("depth", sim.depths) or [None] * len(sim.trucks)
    lidars = rep.run("lidar", sim.lidars) or [np.zeros((0, 3))] * len(sim.trucks)
    for i, t in enumerate(sim.trucks):
        if renderer is None:
            rep.add(f"rgb:{t.id}", "SKIP", "no RTX renderer")
        else:
            img = images.get(t.id)
            if img is None or not getattr(img, "size", 0):
                rep.add(f"rgb:{t.id}", "FAIL", "no frame from the rgb annotator")
            else:
                mean, std = float(img[..., :3].mean()), float(img[..., :3].std())
                ok = mean > 3 and std > 2
                rep.add(f"rgb:{t.id}", "PASS" if ok else "FAIL", f"{img.shape[1]}x{img.shape[0]} mean {mean:.0f} std {std:.0f}"
                        + ("" if ok else " (black/flat: renderer warm-up, lights or textures)") + f" -> {out / (t.id + '.png')}")
        d = depths[i] if i < len(depths) else None
        if d is None:
            rep.add(f"depth:{t.id}", "FAIL" if sim.caster is None else "SKIP", "no depth (Warp raycasts off)")
        else:
            miss = float(np.mean(d >= 99.999)) * 100
            rep.add(f"depth:{t.id}", "PASS" if miss < 99 else "FAIL", f"{d.shape[1]}x{d.shape[0]} min {d.min():.1f} "
                    f"median {np.median(d):.1f} max {d.max():.1f} m, miss {miss:.0f}%")
        pts = lidars[i] if i < len(lidars) else np.zeros((0, 3))
        np.save(out / f"{t.id}_lidar.npy", np.asarray(pts, np.float32))
        n = len(pts)
        rep.add(f"lidar:{t.id}", "PASS" if n > 0 else "FAIL", f"{n} points (max 7200)"
                + (f", range {np.linalg.norm(pts, axis=1).min():.1f}..{np.linalg.norm(pts, axis=1).max():.1f} m" if n else
                   " (no hits: Warp off, or sensor inside/under terrain)"))

    # drive test: truck-0, throttle 0.5 / steer 0.2 for drive_seconds
    def drive():
        t = sim.trucks[0]
        start = (t.x, t.z, t.yaw)
        bounds = sim.world.bounds
        max_err, min_margin, statuses = 0.0, math.inf, {}
        steps = int(round(a.drive_seconds / PHYSICS_DT))
        for k in range(steps):
            now = sim.time + PHYSICS_DT
            t.command(DRIVE_THROTTLE, DRIVE_STEER, now)
            sim.step(PHYSICS_DT, now)
            if renderer is not None:
                renderer.update(sim, PHYSICS_DT)
            if sim.caster is not None and k % 5 == 0:
                g = sim.caster.height(t.x, t.z)
                max_err = max(max_err, abs(t.y - g) if math.isfinite(g) else math.inf)
            min_margin = min(min_margin, footprint_inside(t, bounds))
            st = getattr(t, "guard_status", "n/a")
            statuses[st] = statuses.get(st, 0) + 1
        dist = math.hypot(t.x - start[0], t.z - start[1])
        on_ground = max_err <= GROUND_TOL and math.isfinite(t.y)
        inside = min_margin >= 0
        moved = dist > 1.0 or statuses.get("blocked", 0) > 0
        ok = on_ground and inside and moved
        rep.add("drive", "PASS" if ok else "FAIL",
                f"{t.id} {a.drive_seconds:.0f}s thr {DRIVE_THROTTLE} steer {DRIVE_STEER}: moved {dist:.1f} m, "
                f"yaw {start[2]:.0f}->{t.yaw:.0f}, speed {t.speed:.1f} m/s, max ground err {max_err:.2f} m, "
                f"min bounds margin {min_margin:.1f} m, guard {statuses}"
                + ("" if guarded else " (no KeepInBounds in isaac/sim)"))
        sim.sync_obstacles()
        if renderer is not None:
            grab_images("_after_drive")
    rep.run("drive", drive)

    # INFO: RTX lidar extension (not used by isaac/sim; Warp raycasts reproduce Unity's pattern exactly)
    if app is not None:
        try:
            import isaacsim.sensors.rtx  # noqa: F401
            rep.add("rtx_lidar", "INFO", "isaacsim.sensors.rtx importable (unused; lidar = Warp raycasts)")
        except Exception as e:  # noqa: BLE001
            rep.add("rtx_lidar", "INFO", f"isaacsim.sensors.rtx not importable ({e}); fine, isaac/sim uses Warp")

    if a.brain:
        host, port = a.brain.rsplit(":", 1)
        try:
            socket.create_connection((host, int(port)), timeout=2).close()
            rep.add("brain", "INFO", f"TCP {a.brain} open (brain sim socket up)")
        except OSError as e:
            rep.add("brain", "INFO", f"TCP {a.brain} not reachable ({e}); start the brain before run_watson.sh up")

    rep.add("total", "INFO", f"{time.time() - t_start:.0f}s wall")
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)   # SimulationApp.close() can hang on some Kit builds; the report is already written
