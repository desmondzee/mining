import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG = Path(__file__).with_name("config.json")
SUBSTEP = 1 / 30
MAX_DT = 0.2
STATS_EVERY = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--brain", default="127.0.0.1:9000")
    p.add_argument("--world", default=str(ROOT / "data/worlds/quarry"))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--raycast", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--cache", default=str(ROOT / "downloads/isaac/cache"))
    p.add_argument("--renderer", default="")
    p.add_argument("--fps", type=float, default=0.0)
    p.add_argument("--fx", choices=["off", "low", "high"], default="low")
    p.add_argument("--no-views", action="store_true")
    p.add_argument("--save-dir", default="")
    p.add_argument("--save-every", type=int, default=0)
    p.add_argument("--duration", type=float, default=0.0)
    args, _ = p.parse_known_args(argv)
    return args


def load_config(args: argparse.Namespace) -> dict:
    config = json.loads(Path(args.config).read_text())
    if args.fps:
        config["fps"] = args.fps
    if args.renderer:
        config["renderer"] = args.renderer
    if args.no_views:
        config["views"] = []
    return config


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

        from isaac.sim.sensors import CAM_H, CAM_W

        self.frames = {name: np.zeros((CAM_H, CAM_W, 3), np.uint8) for name in ["overview", *(t.id for t in sim.trucks)]}
        self.frames.update({v.name: np.zeros((v.height, v.width, 3), np.uint8) for v in sim.views})
        self.view_poses = {}

    def update(self, sim, dt: float) -> None:
        self.view_poses = sim.view_poses(dt)

    def render(self) -> dict:
        return self.frames


def run(args: argparse.Namespace) -> None:
    config = load_config(args)
    app = None
    if not args.dry_run:
        from isaacsim import SimulationApp

        app = SimulationApp({"headless": True, "renderer": config.get("renderer", "RealTimePathTracing"), "width": 640, "height": 360})

    import numpy as np

    from isaac.sim.bridge import Bridge
    from isaac.sim.protocol import encode_actors, encode_frame, encode_jpeg, encode_lidar
    from isaac.sim.sensors import ACTORS_HZ, FPS, LIDAR_HZ, cam_field
    from isaac.sim.sim import Sim

    sim = Sim(args.world, raycast=raycast_enabled(args.raycast), views=config.get("views", []), lidar_level=config.get("lidar_frame", "sensor") == "level")
    print(f"world {sim.world.id}: trucks {[t.id for t in sim.trucks]}, walkers {len(sim.walkers)}, views {[v.name for v in sim.views]}, raycast {sim.caster is not None}, fps {config.get("fps", FPS)}", flush=True)
    if args.dry_run:
        renderer = DryRenderer(sim)
    else:
        from isaac.sim.scene import IsaacRenderer

        renderer = IsaacRenderer(app, sim, args, config)

    host, port = args.brain.rsplit(":", 1)
    bridge = Bridge(host, int(port), sim.hello, sim.command).start()
    jpeg_pool = ThreadPoolExecutor(6)
    publisher = ThreadPoolExecutor(1)
    noise = float(config.get("noise_sigma", 0.0))
    rng = np.random.default_rng()
    save = Path(args.save_dir) if args.save_dir else None
    if save:
        save.mkdir(parents=True, exist_ok=True)
    hfov = {v.name: v.hfov for v in sim.views}

    def jpeg(rgb):
        if noise > 0:
            rgb = np.clip(rgb + rng.normal(0, noise, rgb.shape), 0, 255).astype(np.uint8)
        return encode_jpeg(rgb), rgb.shape[1], rgb.shape[0]

    dedupe = not args.dry_run
    last_sig: dict[str, bytes] = {}
    fresh: dict[str, int] = {}

    def publish(images, depths, poses, cams, views, frame):
        new = {}
        for name, img in images.items():
            sig = img[::17, ::23].tobytes()
            if not dedupe or last_sig.get(name) != sig:
                last_sig[name] = sig
                fresh[name] = fresh.get(name, 0) + 1
                new[name] = img
        encoded = dict(zip(new, jpeg_pool.map(jpeg, new.values())))
        if bridge.connected.is_set():
            for name, (data, w, h) in encoded.items():
                if name in poses:
                    i, pose, speed = poses[name]
                    bridge.send(encode_frame(name, data, w, h, depths[i], truck=name, pose=pose, speed=speed, cam=cams[name]))
                elif name in views:
                    bridge.send(encode_frame(name, data, w, h, cam=views[name]))
                else:
                    bridge.send(encode_frame(name, data, w, h))
        if save and args.save_every and frame % args.save_every == 0:
            for name, (data, _, _) in encoded.items():
                (save / f"{name}_{frame:05d}.jpg").write_bytes(data)

    period = 1 / config.setdefault("fps", FPS)
    start = last = next_tick = time.monotonic()
    next_lidar = next_actors = 0.0
    frames = 0
    pending = previous = None
    lag = getattr(renderer, "lag", 0)
    timing = dict.fromkeys(["step", "render", "rays", "publish"], 0.0)
    stats_at = start + STATS_EVERY
    while app is None or app.is_running():
        now = time.monotonic()
        dt = min(now - last, MAX_DT)
        last = now
        t0 = time.monotonic()
        steps = max(1, round(dt / SUBSTEP))
        for _ in range(steps):
            sim.step(dt / steps, now)
        sim.sync_obstacles()
        renderer.update(sim, dt)
        t1 = time.monotonic()
        images = renderer.render()
        t2 = time.monotonic()
        live = bridge.connected.is_set()
        depths = sim.depths() if live else [None] * len(sim.trucks)
        poses = {t.id: (i, t.pose(), t.speed) for i, t in enumerate(sim.trucks)}
        cams = {t.id: cam_field(*sim.camera_pose(i)) for i, t in enumerate(sim.trucks)}
        views = {name: cam_field(p, r, hfov[name]) for name, (p, r) in renderer.view_poses.items()}
        if live:
            if sim.time >= next_actors:
                next_actors = max(next_actors + 1 / ACTORS_HZ, sim.time - 1 / ACTORS_HZ)
                bridge.send(encode_actors(sim.actors()))
            if sim.time >= next_lidar:
                next_lidar = max(next_lidar + 1 / LIDAR_HZ, sim.time - 1 / LIDAR_HZ)
                for t, points in zip(sim.trucks, sim.lidars()):
                    bridge.send(encode_lidar(t.id, points, t.pose()))
        t3 = time.monotonic()
        if pending is not None:
            pending.result()
        t4 = time.monotonic()
        frames += 1
        meta = (depths, poses, cams, views)
        if lag:
            meta, previous = (previous or meta), meta
        pending = publisher.submit(publish, images, *meta, frames)
        timing["step"] += t1 - t0
        timing["render"] += t2 - t1
        timing["rays"] += t3 - t2
        timing["publish"] += t4 - t3
        if now >= stats_at:
            n = max(frames, 1)
            span = now - start
            print(
                f"t={span:.0f}s fps={frames / span:.1f} " + " ".join(f"{k}={v / n * 1000:.1f}ms" for k, v in timing.items())
                + " fresh_fps=" + ",".join(f"{k}:{v / span:.1f}" for k, v in fresh.items())
                + f" connected={live} sent={bridge.sent} dropped={bridge.dropped} "
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
    if pending is not None:
        pending.result()
    bridge.stop()
    if app is not None:
        app.close()


if __name__ == "__main__":
    run(parse_args())
