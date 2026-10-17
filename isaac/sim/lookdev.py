import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from isaac.sim.main import load_config, parse_args  # noqa: E402

FRAMES = 40


def main() -> None:
    args = parse_args()
    variants = json.loads(Path(sys.argv[sys.argv.index("--variants") + 1]).read_text())
    config = load_config(args)
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": config.get("renderer", "RealTimePathTracing"), "width": 640, "height": 360})
    import carb
    from PIL import Image
    from pxr import Gf

    from isaac.sim.scene import IsaacRenderer
    from isaac.sim.sim import Sim
    from isaac.sim.usdbuild import sun_matrix

    sim = Sim(args.world, views=config.get("views", []))
    r = IsaacRenderer(app, sim, args, config)
    out = Path(args.save_dir)
    out.mkdir(parents=True, exist_ok=True)
    settings = carb.settings.get_settings()
    sun = r.stage.GetPrimAtPath("/World/Sun")
    dome = r.stage.GetPrimAtPath("/World/Sky")
    for name, v in variants.items():
        for key, value in v.get("rtx", {}).items():
            settings.set(key, value)
        if "sun" in v:
            s = v["sun"]
            sun.GetAttribute("inputs:intensity").Set(float(s.get("intensity", 3000)))
            if "color" in s:
                sun.GetAttribute("inputs:color").Set(Gf.Vec3f(*s["color"]))
            sun.GetAttribute("xformOp:transform").Set(sun_matrix(float(s.get("elevation", 40)), float(s.get("azimuth", 220))))
        if "dome" in v:
            dome.GetAttribute("inputs:intensity").Set(float(v["dome"].get("intensity", 700)))
            if "rotation" in v["dome"]:
                dome.GetAttribute("xformOp:rotateXYZ").Set(Gf.Vec3f(*v["dome"]["rotation"]))
        if "scan" in v:
            r.tune_scan(v["scan"])
        for _ in range(FRAMES):
            sim.step(1 / 30, 0.0)
            r.update(sim, 1 / 30)
            images = r.render()
        for cam, img in images.items():
            Image.fromarray(img).save(out / f"{name}_{cam}.jpg", quality=90)
        print(f"LOOKDEV {name} saved", flush=True)
    app.close()


if __name__ == "__main__":
    main()
