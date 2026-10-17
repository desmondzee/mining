"""Offline checks for the regex fallback parser and road routing.

Run: uv run --with fastapi --with uvicorn --with httpx --with python-multipart radio/test_parse.py

The end-to-end excavator test runs against a mock radio on RADIO_TEST_URL (default http://127.0.0.1:8091):
  PORT=8091 RADIO_MOCK=1 bash radio/run.sh     # skipped (not failed) if nothing is listening there
"""

import math
import os
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

fails = 0
for text, want in CASES:
    got = p.parse_regex(text)
    simple = [(c["name"], c["args"].get("trucks"), {k: v for k, v in c["args"].items() if k != "trucks" and v is not None}) for c in got]
    ok = simple == [(n, t, a) for n, t, a in want]
    fails += not ok
    print(("PASS " if ok else "FAIL ") + f"{text!r}\n      -> {simple}" + ("" if ok else f"\n  want {want}"))

for text, want in EXC_CASES:
    got = p.parse_regex(text)
    simple = []
    for c in got:
        if "excavators" in c["args"]:
            simple.append((c["name"], c["args"]["excavators"]))
        else:
            simple.append((c["name"], c["args"].get("trucks"), {k: v for k, v in c["args"].items() if k != "trucks" and v is not None}))
    ok = simple == want
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

# ---- end-to-end against a mock radio instance (PORT=8091 RADIO_MOCK=1 bash radio/run.sh)
e2e = 0
URL = os.environ.get("RADIO_TEST_URL", "http://127.0.0.1:8091")
try:
    import httpx
    c = httpx.Client(base_url=URL, timeout=20.0)
    h = c.get("/api/radio/health").json()
except Exception as ex:  # noqa: BLE001
    print(f"SKIP e2e: no radio at {URL} ({type(ex).__name__})")
    h = None
if h is not None:
    if not h.get("mock"):
        print(f"SKIP e2e: {URL} is not in mock mode (won't drive live trucks)")
    else:
        def exc_states():
            return {e["id"]: e["state"] for e in c.get("/api/radio/state").json()["excavators"]}

        def cmd(text):
            return c.post("/api/radio/command", json={"text": text}).json()

        def check(name, cond, info=""):
            global fails, e2e
            e2e += 1
            fails += not cond
            print(f"{'PASS' if cond else 'FAIL'} e2e {name} {info}")

        c.post("/api/radio/release")
        st = c.get("/api/radio/state").json()
        check("state has excavators", len(st.get("excavators", [])) == 3 and
              all({"id", "callsign", "face", "face_name", "state", "x", "z", "heading_deg"} <= set(e) for e in st["excavators"]))
        check("initial all holding", set(exc_states().values()) == {"holding"}, exc_states())
        r = cmd("excavator one start digging")
        check("dig -> digging", exc_states()["exc-0"] == "digging", [x["text"] for x in r["replies"]])
        check("dig reply", any(x["text"] == "Copy, Excavator One digging at Lower Face." for x in r["replies"]))
        r = cmd("excavator one hold")
        check("hold -> holding", exc_states()["exc-0"] == "holding", [x["text"] for x in r["replies"]])
        cmd("all excavators start digging")
        check("all dig", set(exc_states().values()) == {"digging"}, exc_states())
        r = cmd("all stop")
        check("all stop -> all holding", set(exc_states().values()) == {"holding"}, [x["text"] for x in r["replies"]])
        cmd("excavator at the middle face start digging")
        check("by face", exc_states() == {"exc-0": "holding", "exc-1": "digging", "exc-2": "holding"}, exc_states())
        r = cmd("truck one go to excavator two")
        check("truck -> excavator", any(x["text"] == "Copy, Truck One heading to Excavator Two at Middle Face." for x in r["replies"]),
              [x["text"] for x in r["replies"]])
        r = cmd("truck two go to the excavator")  # nearest digging = Excavator Two
        check("truck -> nearest digging excavator", any("Excavator Two" in x["text"] for x in r["replies"]), [x["text"] for x in r["replies"]])
        ex = c.get("/api/radio/excavators").json()
        check("/api/radio/excavators", len(ex) == 3 and set(ex[0]) == {"id", "face", "state", "heading_deg", "pos3"}, ex[1])
        c.post("/api/radio/release")
        check("release -> all holding", set(exc_states().values()) == {"holding"})

total = len(CASES) + len(EXC_CASES) + 6 + e2e
print(f"\n{total - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
