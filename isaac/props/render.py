import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--world", default="/mining/data/worlds/quarry")
ap.add_argument("--props", default=None)
ap.add_argument("--assets", default="/mining/isaac/assets/props")
ap.add_argument("--scan", default="/out/usd")
ap.add_argument("--out", default="/mining/downloads/props/renders")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
ap.add_argument("--frames", type=int, default=60)
ap.add_argument("--no-props", action="store_true")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "renderer": "RealTimePathTracing", "width": args.width, "height": args.height})

import carb  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image  # noqa: E402
from pxr import Gf, UsdGeom, UsdLux  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load  # noqa: E402

S = load.S
settings = carb.settings.get_settings()
for k, v in {"/rtx/post/motionblur/enabled": False, "/rtx/post/dof/enabled": False, "/rtx/post/lensFlares/enabled": False,
             "/rtx/post/tonemap/op": 6, "/rtx/post/histogram/enabled": False, "/rtx/post/tonemap/filmIso": 100.0,
             "/rtx/post/tonemap/cameraShutter": 50.0, "/rtx/post/tonemap/fNumber": 5.0}.items():
    settings.set(k, v)

world_dir = Path(args.world)
world = json.loads((world_dir / "world.json").read_text())
props_path = Path(args.props) if args.props else world_dir / "props.json"
props = json.loads(props_path.read_text())

stage = omni.usd.get_context().get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
UsdGeom.Xform.Define(stage, "/World")


def ref(path: str, usd: str, m: np.ndarray) -> None:
    xf = UsdGeom.Xform.Define(stage, path)
    xf.AddTransformOp().Set(Gf.Matrix4d(m.T.tolist()))
    xf.GetPrim().GetReferences().AddReference(usd)


swap = np.eye(4)
swap[:3, :3] = S
st = world["scan_transform"]
ref("/World/Scan", f"{args.scan}/visual.usd", swap @ np.array(st["matrix"], float))
ref("/World/Surround", f"{args.scan}/surround.usd", swap)
dome = UsdLux.DomeLight.Define(stage, "/World/Sky")
dome.CreateTextureFileAttr(str(world_dir / world["assets"]["pano"]))
dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
dome.CreateIntensityAttr(700.0)
sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
sun.CreateIntensityAttr(3000.0)
sun.CreateAngleAttr(0.53)
sun.CreateColorAttr(Gf.Vec3f(1.0, 0.95, 0.88))
e, a = math.radians(40.0), math.radians(220.0)
z = np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])
x = np.cross([0.0, 0.0, 1.0], z)
x /= np.linalg.norm(x)
m = np.eye(4)
m[:3, :3] = np.column_stack([x, np.cross(z, x), z])
UsdGeom.Xformable(sun).AddTransformOp().Set(Gf.Matrix4d(m.T.tolist()))

if not args.no_props:
    print("props", load.add_props(stage, props, args.assets), flush=True)

roads = {r["id"]: np.array(r["points"], float) for r in world["roads"]}


def truck(name: str, pos: np.ndarray, heading: float) -> None:
    xf = UsdGeom.Xform.Define(stage, f"/World/Trucks/{name}")
    p = S @ pos
    xf.AddTranslateOp().Set(Gf.Vec3d(*p.tolist()))
    xf.AddRotateZOp().Set(90.0 - heading)
    body = np.zeros((4, 4))
    body[0, 1] = body[1, 2] = body[2, 0] = body[3, 3] = 1.0
    ref(f"/World/Trucks/{name}/body", f"{args.scan}/komatsu_hd465.usd", body.T)


def camera(name: str, eye_w, target_w, hfov: float = 70.0) -> str:
    eye, target = S @ np.asarray(eye_w, float), S @ np.asarray(target_w, float)
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, [0.0, 0.0, 1.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    m = np.eye(4)
    m[:3, :3] = np.column_stack([r, u, -f])
    m[:3, 3] = eye
    cam = UsdGeom.Camera.Define(stage, f"/World/Cameras/{name}")
    ap_h = 20.955
    cam.CreateHorizontalApertureAttr(ap_h)
    cam.CreateVerticalApertureAttr(ap_h * args.height / args.width)
    cam.CreateFocalLengthAttr(ap_h / 2 / math.tan(math.radians(hfov / 2)))
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 5000.0))
    cam.AddTransformOp().Set(Gf.Matrix4d(m.T.tolist()))
    return cam.GetPath().pathString


def station(road: str, s: float) -> tuple[np.ndarray, np.ndarray]:
    P = roads[road]
    d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, [0, 2]], axis=0), axis=1))])
    s = s if s >= 0 else d[-1] + s
    p = np.array([np.interp(s, d, P[:, k]) for k in range(3)])
    q = np.array([np.interp(s + 2, d, P[:, k]) for k in range(3)])
    t = (q - p)[[0, 2]]
    return p, t / np.linalg.norm(t)


def berm_view() -> tuple:
    b = max(props["berms"], key=lambda b: b["length_m"])
    line = np.array(b["line"])
    P = roads[b["road"]]
    d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, [0, 2]], axis=0), axis=1))])
    idx = int(np.argmin(np.linalg.norm(P[:, [0, 2]] - line[min(3, len(line) - 1)], axis=1)))
    p, t = station(b["road"], float(d[idx]))
    ahead_s = float(d[idx]) + 45.0
    q, tq = station(b["road"], ahead_s)
    if np.linalg.norm(q[[0, 2]] - line[-1]) > np.linalg.norm(p[[0, 2]] - line[-1]):
        p, t = station(b["road"], float(d[idx]) + 0.0)
        q, tq = station(b["road"], float(d[idx]) - 45.0)
        t = -t
    side = 1 if b["side"] == "right" else -1
    n = np.array([t[1], -t[0]]) * side
    eye = p + np.array([n[0] * -3.0, 3.6, n[1] * -3.0])
    tgt = q + np.array([n[0] * 2.0, 0.5, n[1] * 2.0])
    mid, mt = station(b["road"], float(d[idx]) + (25.0 if np.dot(tq, t) > 0 else -25.0))
    return eye, tgt, mid, mt


views = {}
eye, tgt, mid, mt = berm_view()
truck("truck_0", mid + np.array([0, 0.0, 0]), math.degrees(math.atan2(-mt[0], -mt[1])) % 360)
views["haul_road_berm"] = camera("haul_road_berm", eye, tgt, 75.0)

iso = max((e for e in props["props"] if e["kind"] == "boulder" and e["obstacle"]), key=lambda e: e["scale"][0])
k = min(roads, key=lambda r: np.min(np.linalg.norm(roads[r][:, [0, 2]] - [iso["pos"][0], iso["pos"][2]], axis=1)))
P = roads[k]
i = int(np.argmin(np.linalg.norm(P[:, [0, 2]] - [iso["pos"][0], iso["pos"][2]], axis=1)))
j = max(0, i - 7) if i > 7 else min(len(P) - 1, i + 7)
views["road_boulder"] = camera("road_boulder", P[j] + [0, 3.6, 0], np.array(iso["pos"]) + [0, 0.8, 0], 70.0)

tank = next((e for e in props["props"] if e["kind"] == "fuel_tank"), None)
if tank:
    tp = np.array(tank["pos"])
    fuel = next(s for s in world["sites"] if s["id"] == "fuel")
    fp = np.array(fuel["pos3"])
    v = tp - fp
    v[1] = 0
    v /= np.linalg.norm(v)
    views["fuel_bay"] = camera("fuel_bay", fp - v * 6 + [0, 4.0, 0], tp + [0, 1.0, 0], 70.0)

pile = max((e for e in props["props"] if e["kind"] == "rock_pile"), key=lambda e: e["scale"][0])
pp = np.array(pile["pos"])
views["overview"] = camera("overview", pp + [-70.0, 60.0, -70.0], pp + [10.0, 0.0, 10.0], 60.0)
cr = sorted(world["roads"], key=lambda r: r["id"])[0]["id"]
cp, ct = station(cr, float(np.sum(np.linalg.norm(np.diff(roads[cr][:, [0, 2]], axis=0), axis=1))) / 2 - 30.0)
cq, _ = station(cr, float(np.sum(np.linalg.norm(np.diff(roads[cr][:, [0, 2]], axis=0), axis=1))) / 2 + 5.0)
views["crossing"] = camera("crossing", cp + [0, 3.6, 0], cq + [0, 0.5, 0], 70.0)

out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
for _ in range(20):
    app.update()
for name, path in views.items():
    rp = rep.create.render_product(path, (args.width, args.height))
    ann = rep.AnnotatorRegistry.get_annotator("rgb")
    ann.attach(rp)
    for _ in range(args.frames):
        app.update()
    data = np.asarray(ann.get_data())
    if data.size:
        Image.fromarray(data[..., :3]).save(out / f"{name}{'_noprops' if args.no_props else ''}.png")
        print("saved", name, flush=True)
    ann.detach()
    rp.destroy()
app.close()
