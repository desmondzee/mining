import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FPS = 15
SUBSTEP = 1 / 60
MAX_DT = 0.2
STATS_EVERY = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--brain", default="127.0.0.1:9000")
    p.add_argument("--world", default=str(ROOT / "data/worlds/quarry"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--raycast", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--lidar", choices=["raycast", "rtx"], default="raycast")
    p.add_argument("--cache", default=str(ROOT / "downloads/isaac/cache"))
    p.add_argument("--renderer", default="RealTimePathTracing")
    p.add_argument("--fps", type=float, default=FPS)
    p.add_argument("--people", choices=["capsule", "isaac"], default="capsule")
    p.add_argument("--dome-rotation", type=float, nargs=3, default=[0.0, 0.0, 0.0])
    p.add_argument("--save-dir", default="")
    p.add_argument("--save-every", type=int, default=0)
    p.add_argument("--duration", type=float, default=0.0)
    args, _ = p.parse_known_args(argv)
    return args


def raycast_enabled(mode: str) -> bool:
    if mode != "auto":
        return mode == "on"
    try:
        import warp  # noqa: F401
    except ImportError:
        return False
    return True


class DryRenderer:
    def __init__(self, sim) -> None:
        import numpy as np

        from isaac.sim.protocol import encode_jpeg
        from isaac.sim.sensors import CAM_H, CAM_W

        black = encode_jpeg(np.zeros((CAM_H, CAM_W, 3), np.uint8))
        self.frames = {name: (black, CAM_W, CAM_H) for name in ["overview", *(t.id for t in sim.trucks)]}

    def update(self, sim) -> None:
        pass

    def render(self) -> dict:
        return self.frames


def run(args: argparse.Namespace) -> None:
    app = None
    if not args.dry_run:
        from isaacsim import SimulationApp

        app = SimulationApp({"headless": True, "renderer": args.renderer, "width": 640, "height": 360, "anti_aliasing": 0})

    from isaac.sim.bridge import Bridge
    from isaac.sim.protocol import encode_actors, encode_frame, encode_jpeg, encode_lidar
    from isaac.sim.sensors import ACTORS_HZ, LIDAR_HZ, cam_field
    from isaac.sim.sim import Sim

    sim = Sim(args.world, raycast=raycast_enabled(args.raycast))
    print(f"world {sim.world.id}: trucks {[t.id for t in sim.trucks]}, walkers {len(sim.walkers)}, raycast {sim.caster is not None}", flush=True)
    if args.dry_run:
        renderer = DryRenderer(sim)
    else:
        from isaac.sim.scene import IsaacRenderer

        renderer = IsaacRenderer(app, sim, args)

    host, port = args.brain.rsplit(":", 1)
    bridge = Bridge(host, int(port), sim.hello, sim.command).start()
    pool = ThreadPoolExecutor(4)
    save = Path(args.save_dir) if args.save_dir else None
    if save:
        save.mkdir(parents=True, exist_ok=True)

    def jpeg(item):
        rgb = item
        if isinstance(rgb, tuple):
            return rgb
        return encode_jpeg(rgb), rgb.shape[1], rgb.shape[0]

    period = 1 / args.fps
    start = last = next_tick = time.monotonic()
    next_lidar = next_actors = 0.0
    frames = 0
    timing = {"render": 0.0, "jpeg": 0.0, "rays": 0.0}
    stats_at = start + STATS_EVERY
    while app is None or app.is_running():
        now = time.monotonic()
        dt = min(now - last, MAX_DT)
        last = now
        steps = max(1, round(dt / SUBSTEP))
        for _ in range(steps):
            sim.step(dt / steps, now)
        sim.sync_obstacles()
        renderer.update(sim)

        t0 = time.monotonic()
        images = renderer.render()
        t1 = time.monotonic()
        encoded = dict(zip(images, pool.map(jpeg, images.values())))
        t2 = time.monotonic()
        live = bridge.connected.is_set()
        if live:
            if sim.time >= next_actors:
                next_actors = sim.time + 1 / ACTORS_HZ
                bridge.send(encode_actors(sim.actors()))
            if sim.time >= next_lidar:
                next_lidar = sim.time + 1 / LIDAR_HZ
                for i, t in enumerate(sim.trucks):
                    bridge.send(encode_lidar(t.id, sim.lidar(i), t.pose()))
            if "overview" in encoded:
                data, w, h = encoded["overview"]
                bridge.send(encode_frame("overview", data, w, h))
            for i, t in enumerate(sim.trucks):
                if t.id in encoded:
                    data, w, h = encoded[t.id]
                    bridge.send(encode_frame(t.id, data, w, h, sim.depth(i), truck=t.id, pose=t.pose(), speed=t.speed, cam=cam_field(*sim.camera_pose(i))))
        t3 = time.monotonic()
        timing["render"] += t1 - t0
        timing["jpeg"] += t2 - t1
        timing["rays"] += t3 - t2
        frames += 1
        if save and args.save_every and frames % args.save_every == 0:
            for name, (data, _, _) in encoded.items():
                (save / f"{name}_{frames:05d}.jpg").write_bytes(data)
        if now >= stats_at:
            n = max(frames, 1)
            span = now - start
            print(
                f"t={span:.0f}s fps={frames / span:.1f} render={timing['render'] / n * 1000:.1f}ms jpeg={timing['jpeg'] / n * 1000:.1f}ms "
                f"send+rays={timing['rays'] / n * 1000:.1f}ms connected={live} sent={bridge.sent} dropped={bridge.dropped} "
                + " ".join(f"{t.id}=({t.x:.1f},{t.y:.1f},{t.z:.1f},{t.yaw:.0f}deg,{t.speed:.1f}m/s)" for t in sim.trucks),
                flush=True,
            )
            stats_at = now + STATS_EVERY
        if args.duration and now - start >= args.duration:
            break
        next_tick += period
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.monotonic()
    bridge.stop()
    if app is not None:
        app.close()


if __name__ == "__main__":
    run(parse_args())
