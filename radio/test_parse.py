"""Offline checks for the regex fallback parser and road routing.

Run: uv run --with fastapi --with uvicorn --with httpx --with python-multipart radio/test_parse.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import WORLD_PATH, Parser, World, path_len, simplify  # noqa: E402

world = World(WORLD_PATH)
ids = ["truck-0", "truck-1", "truck-2"]
p = Parser(world, lambda: ids)
ore = {s.name.split()[0].lower(): s.id for s in world.sites.values() if s.type == "ore"}
crusher = world.by_type("crusher").id
dump = world.by_type("dump").id
fuel = world.by_type("fuel").id

CASES = [
    ("truck one go to the lower face", [("go_to", ["truck-0"], {"site": ore["lower"]})]),
    ("Truck One, go to the Lower Face.", [("go_to", ["truck-0"], {"site": ore["lower"]})]),
    ("all trucks return to the ready line", [("return_to_parking", ids, {})]),
    ("All trucks, return to the red line.", [("return_to_parking", ids, {})]),
    ("truck two haul from the middle face to the crusher", [("haul", ["truck-1"], {"from_site": ore["middle"], "to_site": crusher})]),
    ("Truck to, haul from the middle phase to the crusher.", [("haul", ["truck-1"], {"from_site": ore["middle"], "to_site": crusher})]),
    ("truck three haul three loads from the upper face to the crusher", [("haul", ["truck-2"], {"from_site": ore["upper"], "to_site": crusher, "loads": 3})]),
    ("everyone stop", [("stop", ids, {})]),
    ("All stop! All stop!", [("stop", ids, {})]),
    ("status report", [("status", ids, {})]),
    ("truck two status", [("status", ["truck-1"], {})]),
    ("truck zero go to the crusher", [("go_to", ["truck-0"], {"site": crusher})]),
    ("truck 3 go to the fuel bay", [("go_to", ["truck-2"], {"site": fuel})]),
    ("trucks one and two go to the waste dump", [("go_to", ["truck-0", "truck-1"], {"site": dump})]),
    ("truck one go to the upper face and truck two return to the ready line",
     [("go_to", ["truck-0"], {"site": ore["upper"]}), ("return_to_parking", ["truck-1"], {})]),
    ("truck one stop", [("stop", ["truck-0"], {})]),
    ("good morning", []),
]

fails = 0
for text, want in CASES:
    got = p.parse_regex(text)
    simple = [(c["name"], c["args"].get("trucks"), {k: v for k, v in c["args"].items() if k != "trucks" and v is not None}) for c in got]
    ok = simple == [(n, t, a) for n, t, a in want]
    fails += not ok
    print(("PASS " if ok else "FAIL ") + f"{text!r}\n      -> {simple}" + ("" if ok else f"\n  want {want}"))

# routing sanity: parking -> each site follows roads and ends at the stop point
park = world.trucks["truck-0"]["spawn"]
for sid in [ore["lower"], ore["middle"], ore["upper"], crusher, dump, fuel]:
    s = world.sites[sid]
    raw = world.road_path(tuple(park), s.point)
    wps = simplify(tuple(park), raw)
    gaps = max(math.dist((a.x, a.z), (b.x, b.z)) for a, b in zip(wps, wps[1:])) if len(wps) > 1 else 0
    end_ok = math.dist((wps[-1].x, wps[-1].z), s.point) < 1.0 and wps[-1].final
    bends = sum(w.bend for w in wps)
    def seg_d(q, a, b):
        vx, vz = b[0] - a[0], b[1] - a[1]
        L2 = vx * vx + vz * vz or 1e-9
        t = max(0.0, min(1.0, ((q[0] - a[0]) * vx + (q[1] - a[1]) * vz) / L2))
        return math.dist(q, (a[0] + t * vx, a[1] + t * vz))
    dev = max(min(seg_d((w.x, w.z), a, b) for a, b in zip(raw, raw[1:])) for w in wps) if len(raw) > 1 else 0
    ok = end_ok and gaps <= 25.0 and dev <= 2.0
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} route parking -> {s.name:16s} {path_len(tuple(park), wps):6.0f} m, "
          f"{len(raw):3d} road pts -> {len(wps):3d} waypoints ({bends} bend), max gap {gaps:.1f} m, max off-road {dev:.1f} m")

print(f"\n{len(CASES) + 6 - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
