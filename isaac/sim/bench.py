import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from isaac.sim.main import load_config, parse_args  # noqa: E402

SECONDS = 15.0


def main() -> None:
    args = parse_args()
    config = load_config(args)
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": config.get("renderer", "RealTimePathTracing"), "width": 640, "height": 360})
    import numpy as np
    import omni.replicator.core as rep

    from isaac.sim.scene import IsaacRenderer
    from isaac.sim.sim import Sim

    sim = Sim(args.world, views=config.get("views", []))
    r = IsaacRenderer(app, sim, args, config)
    modes = {
        "step": lambda: rep.orchestrator.step(rt_subframes=1, pause_timeline=False),
        "step_dt0": lambda: rep.orchestrator.step(rt_subframes=1, pause_timeline=False, delta_time=0.0),
        "step_nowait": lambda: rep.orchestrator.step(rt_subframes=1, pause_timeline=False, wait_for_render=False),
        "update": app.update,
        "update2": lambda: (app.update(), app.update()),
    }
    for name, fn in modes.items():
        r.render = lambda fn=fn: (fn(), r.collect())[1]
        last, fresh, frames = {}, {}, 0
        t0 = time.time()
        while time.time() - t0 < SECONDS:
            sim.command("truck-0", 1.0, 0.3, time.monotonic())
            sim.step(1 / 15, time.monotonic())
            r.update(sim, 1 / 15)
            images = r.render()
            frames += 1
            for cam, img in images.items():
                sig = img[::17, ::23].tobytes()
                if last.get(cam) != sig:
                    last[cam] = sig
                    fresh[cam] = fresh.get(cam, 0) + 1
        span = time.time() - t0
        print(f"BENCH {name} loop_fps={frames / span:.1f} fresh_fps=" + ",".join(f"{k}:{v / span:.1f}" for k, v in fresh.items()), flush=True)
    app.close()


if __name__ == "__main__":
    main()
