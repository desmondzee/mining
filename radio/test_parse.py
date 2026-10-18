import math
import os
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
    # excavators as truck destinations
    ("truck one go to excavator two", [("go_to", ["truck-0"], {"site": ore["middle"], "excavator": "exc-1"})]),
    ("truck one load at excavator one", [("go_to", ["truck-0"], {"site": ore["lower"], "excavator": "exc-0"})]),
    ("truck one to the excavator", [("go_to", ["truck-0"], {"excavator": "nearest"})]),
    ("truck one unload excavator", [("go_to", ["truck-0"], {"excavator": "nearest"})]),
    ("send truck two to digger three", [("go_to", ["truck-1"], {"site": ore["upper"], "excavator": "exc-2"})]),
    ("truck one haul from excavator two", [("haul", ["truck-0"], {"from_site": ore["middle"], "excavator": "exc-1"})]),
    ("truck one go to the excavator at the upper face", [("go_to", ["truck-0"], {"site": ore["upper"], "excavator": "exc-2"})]),
    # fuzzy near-misses (no LLM)
    ("truk one go to the lowr face", [("go_to", ["truck-0"], {"site": ore["lower"]})]),
    ("truck two go to the crasher", [("go_to", ["truck-1"], {"site": crusher})]),
    ("stauts report", [("status", ids, {})]),
]

ALL_EXC = ["exc-0", "exc-1", "exc-2"]
EXC_CASES = [
    ("excavator one start digging", [("dig", ["exc-0"])]),
    ("Excavator One, start digging.", [("dig", ["exc-0"])]),
    ("excavator two hold", [("hold", ["exc-1"])]),
    ("excavator two hold digging", [("hold", ["exc-1"])]),
    ("excavator two stop digging", [("hold", ["exc-1"])]),
    ("excavator two pause", [("hold", ["exc-1"])]),
    ("all excavators start digging", [("dig", ALL_EXC)]),
    ("all excavators hold", [("hold", ALL_EXC)]),
    ("every digger hold", [("hold", ALL_EXC)]),
    ("excavator at the middle face start digging", [("dig", ["exc-1"])]),
    ("the lower face excavator start digging", [("dig", ["exc-0"])]),
    ("excavator at the lower face hold", [("hold", ["exc-0"])]),
    ("digger one start digging", [("dig", ["exc-0"])]),
    ("shovel one start digging", [("dig", ["exc-0"])]),
    ("excavator 1 start digging", [("dig", ["exc-0"])]),
    ("excavator three status", [("exc_status", ["exc-2"])]),
    ("excavator one and excavator two start digging", [("dig", ["exc-0", "exc-1"])]),
    # mishearings
    ("excavate her one start digging", [("dig", ["exc-0"])]),
    ("digger won start digging", [("dig", ["exc-0"])]),
    ("x cavator two hold", [("hold", ["exc-1"])]),
    ("excavaytor one start diging", [("dig", ["exc-0"])]),
    # truck + excavator in one breath
    ("truck one go to the lower face and excavator one start digging",
     [("go_to", ["truck-0"], {"site": ore["lower"]}), ("dig", ["exc-0"])]),
    ("excavator one start digging and truck two haul from the middle face to the crusher",
     [("dig", ["exc-0"]), ("haul", ["truck-1"], {"from_site": ore["middle"], "to_site": crusher})]),
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


@pytest.mark.parametrize("text,want", EXC_CASES)
def test_regex_parser_excavators(text, want):
    simple = []
    for c in p.parse_regex(text):
        if "excavators" in c["args"]:
            simple.append((c["name"], c["args"]["excavators"]))
        else:
            simple.append((c["name"], c["args"].get("trucks"), {k: v for k, v in c["args"].items() if k != "trucks" and v is not None}))
    assert simple == want


@pytest.fixture(scope="module")
def mock_radio():
    httpx = pytest.importorskip("httpx")
    url = os.environ.get("RADIO_TEST_URL", "http://127.0.0.1:8091")
    try:
        c = httpx.Client(base_url=url, timeout=20.0)
        health = c.get("/api/radio/health").json()
    except Exception:
        pytest.skip(f"no mock radio at {url}")
    if not health.get("mock"):
        pytest.skip(f"{url} is not in mock mode")
    c.post("/api/radio/release")
    yield c
    c.post("/api/radio/release")


def test_excavator_commands_end_to_end(mock_radio):
    c = mock_radio

    def states():
        return {e["id"]: e["state"] for e in c.get("/api/radio/state").json()["excavators"]}

    def cmd(text):
        return [r["text"] for r in c.post("/api/radio/command", json={"text": text}).json()["replies"]]

    ex = c.get("/api/radio/state").json()["excavators"]
    assert len(ex) == 3 and all({"id", "callsign", "face", "face_name", "state", "x", "z", "heading_deg"} <= set(e) for e in ex)
    assert set(states().values()) == {"holding"}
    assert "Copy, Excavator One digging at Lower Face." in cmd("excavator one start digging")
    assert states()["exc-0"] == "digging"
    cmd("excavator one hold")
    assert states()["exc-0"] == "holding"
    cmd("all excavators start digging")
    assert set(states().values()) == {"digging"}
    cmd("all stop")
    assert set(states().values()) == {"holding"}
    cmd("excavator at the middle face start digging")
    assert states() == {"exc-0": "holding", "exc-1": "digging", "exc-2": "holding"}
    assert "Copy, Truck One heading to Excavator Two at Middle Face." in cmd("truck one go to excavator two")
    assert any("Excavator Two" in t for t in cmd("truck two go to the excavator"))
    light = c.get("/api/radio/excavators").json()
    assert len(light) == 3 and set(light[0]) == {"id", "face", "state", "heading_deg", "pos3"}


CLARIFY_CASES = [
    ("truck one start digging", "truck-0", "Truck One here, I haul, I don't dig. Want me at an excavator?"),
    ("truck two dig", "truck-1", "Truck Two here, I haul, I don't dig. Want me at an excavator?"),
    ("excavator two drive to the crusher", "exc-1", "Excavator Two here, I stay at Middle Face. Want me to start digging?"),
    ("excavator one go to the waste dump", "exc-0", "Excavator One here, I stay at Lower Face. Want me to start digging?"),
    ("truck five go to the dump", "dispatch", "Control, we only have trucks one to three."),
    ("truck four", "dispatch", "Control, we only have trucks one to three."),
    ("truck three do a backflip", "truck-2",
     "Truck Three here, say again? I can go to a face, an excavator, the crusher, the dump, fuel or the ready line."),
    ("excavator three sing a song", "exc-2", "Excavator Three here, I can start digging or hold. Say again?"),
]


@pytest.mark.parametrize("text,unit,msg", CLARIFY_CASES)
def test_regex_clarify(text, unit, msg):
    assert p.parse_regex(text) == [{"name": "clarify", "args": {"unit": unit, "message": msg}}]


def test_no_unit_no_clarify():
    assert p.parse_regex("good morning") == []
    assert p.parse_regex("the weather is nice") == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
