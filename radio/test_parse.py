import math
import sys
from pathlib import Path

import pytest

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
    ("Truck one, go to the lower face", [("go_to", ["truck-0"], {"site": ore["lower"]})]),
    ("all trucks return to the ready line", [("return_to_parking", ids, {})]),
    ("All trucks, return to the ready line.", [("return_to_parking", ids, {})]),
    ("All trucks, return to the red line.", [("return_to_parking", ids, {})]),
    ("truck two haul from the middle face to the crusher", [("haul", ["truck-1"], {"from_site": ore["middle"], "to_site": crusher})]),
    ("Truck to, haul from the middle phase to the crusher.", [("haul", ["truck-1"], {"from_site": ore["middle"], "to_site": crusher})]),
    ("truck three haul three loads from the upper face to the crusher", [("haul", ["truck-2"], {"from_site": ore["upper"], "to_site": crusher, "loads": 3})]),
    ("everyone stop", [("stop", ids, {})]),
    ("Everyone stop.", [("stop", ids, {})]),
    ("All stop! All stop!", [("stop", ids, {})]),
    ("status report", [("status", ids, {})]),
    ("Status report.", [("status", ids, {})]),
    ("truck two status", [("status", ["truck-1"], {})]),
    ("truck zero go to the crusher", [("go_to", ["truck-0"], {"site": crusher})]),
    ("truck 3 go to the fuel bay", [("go_to", ["truck-2"], {"site": fuel})]),
    ("trucks one and two go to the waste dump", [("go_to", ["truck-0", "truck-1"], {"site": dump})]),
    ("truck one go to the upper face and truck two return to the ready line",
     [("go_to", ["truck-0"], {"site": ore["upper"]}), ("return_to_parking", ["truck-1"], {})]),
    ("truck one stop", [("stop", ["truck-0"], {})]),
    ("truck two go to the unloading point", [("go_to", ["truck-1"], {"site": crusher})]),
    ("good morning", []),
]


@pytest.mark.parametrize("text,want", CASES)
def test_regex_parser(text, want):
    got = p.parse_regex(text)
    simple = [(c["name"], c["args"].get("trucks"), {k: v for k, v in c["args"].items() if k != "trucks" and v is not None}) for c in got]
    assert simple == [(n, t, a) for n, t, a in want]


def seg_d(q, a, b):
    vx, vz = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vz * vz or 1e-9
    t = max(0.0, min(1.0, ((q[0] - a[0]) * vx + (q[1] - a[1]) * vz) / L2))
    return math.dist(q, (a[0] + t * vx, a[1] + t * vz))


@pytest.mark.parametrize("sid", sorted(s.id for s in world.sites.values() if s.type != "parking"))
def test_route_from_parking_follows_roads(sid):
    park = tuple(world.trucks["truck-0"]["spawn"])
    s = world.sites[sid]
    raw = world.road_path(park, s.point)
    wps = simplify(park, raw)
    gaps = max(math.dist((a.x, a.z), (b.x, b.z)) for a, b in zip(wps, wps[1:])) if len(wps) > 1 else 0
    dev = max(min(seg_d((w.x, w.z), a, b) for a, b in zip(raw, raw[1:])) for w in wps) if len(raw) > 1 else 0
    assert math.dist((wps[-1].x, wps[-1].z), s.point) < 1.0 and wps[-1].final
    assert gaps <= 25.0
    assert dev <= 2.0
    assert path_len(park, wps) > 0


def test_world_has_no_stale_road_ids():
    assert {r["id"] for r in world.road_lines} == {r["id"] for r in world.raw["roads"]}
    b = world.bounds
    for s in world.sites.values():
        assert b[0] <= s.point[0] <= b[2] and b[1] <= s.point[1] <= b[3]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
