#!/usr/bin/env python3
"""Validate project data against schema/*.schema.json (JSON Schema draft 2020-12).

    envgen/.venv.nosync/bin/python schema/validate.py            # worlds + live services if up
    envgen/.venv.nosync/bin/python schema/validate.py --offline  # files only
    envgen/.venv.nosync/bin/python schema/validate.py --world quarry

Checks
  1. data/worlds/*/world.json, navgrid.json, heightmap_meta.json against their schemas, plus cross-file
     invariants JSON Schema cannot express (ids resolve, bounds = origin + size, array lengths, raw file size,
     stop poses on passable cells, trucks on their parking slots, asset files exist).
  2. Live radio (RADIO_URL, default http://localhost:8090): /api/radio/state, /world, /excavators, /health,
     cross-checked against the world.json it loaded. Skipped if not reachable.
  3. Live brain (BRAIN_URL, default http://127.0.0.1:8080): /api/state, /api/world. Skipped if not reachable.
  4. Brain contract offline: brain/brain/world.py from `git show origin/main:` -> World.snapshot() and
     Truck.to_dict() validated (skipped if git / origin/main unavailable).
  5. Radio parser offline: imports radio/server.py (needs fastapi/httpx; skipped otherwise), runs the regex
     parser on sample phrases and validates the tool calls. Nothing is POSTed to the radio.

Exit status 1 if any schema validation fails or an invariant marked ERROR fails. WARN lines are reported
inconsistencies that do not fail the run. Read-only: never writes data.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover
    sys.exit("pip install jsonschema  (into envgen/.venv.nosync)")

SCHEMA_DIR = Path(__file__).resolve().parent
REPO = SCHEMA_DIR.parent
WORLDS = Path(os.environ.get("DATA_DIR") or (REPO / "data")) / "worlds"
BASE = "https://physical-mining.local/schema/"


class Report:
    def __init__(self) -> None:
        self.fail = 0
        self.warn = 0
        self.ok = 0

    def passed(self, what: str) -> None:
        self.ok += 1
        print(f"  PASS  {what}")

    def error(self, what: str, msg: str) -> None:
        self.fail += 1
        print(f"  FAIL  {what}: {msg}")

    def warning(self, what: str, msg: str) -> None:
        self.warn += 1
        print(f"  WARN  {what}: {msg}")

    def skip(self, what: str, msg: str) -> None:
        print(f"  SKIP  {what}: {msg}")


R = Report()


# ------------------------------------------------------------------ schema loading
def load_registry() -> Registry:
    resources = []
    for p in sorted(SCHEMA_DIR.glob("*.schema.json")):
        doc = json.loads(p.read_text())
        Draft202012Validator.check_schema(doc)
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


REGISTRY = load_registry()


def validator(name: str, fragment: str = "") -> Draft202012Validator:
    schema = {"$ref": BASE + name + (("#" + fragment) if fragment else "")}
    return Draft202012Validator(schema, registry=REGISTRY)


def check(what: str, inst, name: str, fragment: str = "") -> bool:
    errs = sorted(validator(name, fragment).iter_errors(inst), key=lambda e: list(e.absolute_path))
    if not errs:
        R.passed(f"{what}  [{name}{'#' + fragment if fragment else ''}]")
        return True
    for e in errs[:8]:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        R.error(what, f"{path}: {e.message[:300]}")
    if len(errs) > 8:
        R.error(what, f"... {len(errs) - 8} more schema errors")
    return False


# ------------------------------------------------------------------ world package
def close(a, b, tol=0.05) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


def cell_cost(nav: dict, x: float, z: float) -> float | None:
    c = int((x - nav["origin"][0]) // nav["cell_m"])
    r = int((z - nav["origin"][1]) // nav["cell_m"])
    if not (0 <= c < nav["width"] and 0 <= r < nav["height"]):
        return None
    return nav["cost"][r * nav["width"] + c]


def world_invariants(wid: str, w: dict, nav: dict | None, meta: dict | None, wdir: Path) -> None:
    tag = f"{wid}/world.json"
    errs, warns = [], []
    sites = {s["id"]: s for s in w.get("sites", [])}
    junctions = {j["id"]: j for j in w.get("junctions", [])}
    roads = w.get("roads", [])

    if w.get("id") != wid:
        errs.append(f"id {w.get('id')!r} != folder name {wid!r}")
    for kind, items in (("site", w.get("sites", [])), ("junction", w.get("junctions", [])), ("road", roads),
                        ("truck", w.get("trucks", [])), ("equipment", w.get("equipment", []))):
        ids = [i["id"] for i in items]
        if len(ids) != len(set(ids)):
            errs.append(f"duplicate {kind} ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    if set(sites) & set(junctions):
        errs.append(f"site/junction id clash: {sorted(set(sites) & set(junctions))}")

    if "bounds" in w:
        o, s = w["origin"], w["size_m"]
        if not close(w["bounds"], [o[0], o[1], o[0] + s[0], o[1] + s[1]]):
            errs.append(f"bounds {w['bounds']} != origin + size_m")
    else:
        warns.append("no 'bounds' (legacy world; contract says bounds = origin .. origin + size_m)")

    types_ = [s["type"] for s in sites.values()]
    for t in ("crusher", "parking"):
        if types_.count(t) != 1:
            errs.append(f"expected exactly one {t} site, found {types_.count(t)}")
    if "ore" not in types_:
        errs.append("no ore site")

    nodes = set(sites) | set(junctions)
    endpoints = set()
    for r in roads:
        for end in ("from", "to"):
            if r[end] not in nodes:
                errs.append(f"road {r['id']}.{end} = {r[end]!r} is not a site or junction")
            endpoints.add(r[end])
    if roads:
        lonely = sorted(set(sites) - endpoints)
        if lonely:
            errs.append(f"sites not on any road: {lonely}")

    for e in w.get("equipment", []):
        f = sites.get(e.get("face"))
        if not f or f["type"] != "ore":
            errs.append(f"equipment {e['id']}.face = {e.get('face')!r} is not an ore site")
    if w.get("equipment"):
        ore = sorted(s for s, v in sites.items() if v["type"] == "ore")
        faces = sorted(e["face"] for e in w["equipment"] if e.get("type") == "excavator")
        if faces != ore:
            warns.append(f"excavator faces {faces} != ore sites {ore} (contract: one per ore site)")

    park = next((s for s in sites.values() if s["type"] == "parking"), None)
    slots = (park or {}).get("slots") or []
    trucks = w.get("trucks", [])
    if slots and len(slots) != len(trucks):
        errs.append(f"{len(slots)} parking slots for {len(trucks)} trucks")
    for t in trucks:
        if "slot" in t:
            if t["slot"] >= len(slots):
                errs.append(f"truck {t['id']}.slot {t['slot']} out of range ({len(slots)} slots)")
                continue
            sl = slots[t["slot"]]
            if not close(t["spawn"], [sl["pos3"][0], sl["pos3"][2]], 0.06) or abs(t["heading_deg"] - sl["heading_deg"]) > 0.06:
                errs.append(f"truck {t['id']} spawn/heading != slots[{t['slot']}]")
    legacy = [t["id"] for t in trucks if not t["id"].startswith("truck-")]
    if legacy:
        warns.append(f"legacy truck ids {legacy} (brain / radio expect truck-N)")

    # asset files exist
    for k, v in (w.get("assets") or {}).items():
        if k.endswith("_source") or v is None:
            continue
        if not (wdir / v).is_file():
            errs.append(f"assets.{k} = {v!r} missing on disk")
    if not (wdir / w.get("navgrid", "navgrid.json")).is_file():
        errs.append("navgrid file missing")

    if nav:
        if not close(nav["origin"], w["origin"], 0.05):
            warns.append(f"navgrid origin {nav['origin']} != world origin {w['origin']}")
        ext = [nav["width"] * nav["cell_m"], nav["height"] * nav["cell_m"]]
        if not close(ext, w["size_m"], nav["cell_m"]):
            warns.append(f"navgrid extent {ext} != size_m {w['size_m']}")
        poses = []
        for s in sites.values():
            for k in ("load_point", "dump_point"):
                if k in s:
                    poses.append((f"{s['id']}.{k}", s[k]["pos3"]))
            for i, sl in enumerate(s.get("slots", [])):
                poses.append((f"{s['id']}.slots[{i}]", sl["pos3"]))
        for name, p in poses:
            c = cell_cost(nav, p[0], p[2])
            if c is None or c <= 0:
                errs.append(f"{name} {p} is on an impassable / off-grid navgrid cell (cost {c})")
        for s in sites.values():
            c = cell_cost(nav, s["pos"][0], s["pos"][1])
            if c is None or c <= 0:
                warns.append(f"site {s['id']} pos {s['pos']} on impassable / off-grid cell (cost {c})")

    if meta:
        if not close(meta["origin"], w["origin"], 0.05) or not close(meta["size_m"], w["size_m"], 0.05):
            errs.append("heightmap_meta origin/size_m != world.json origin/size_m")
        if abs(meta["max_height_m"] - meta["min_height_m"] - meta["height_range_m"]) > 0.01:
            errs.append("heightmap_meta height_range_m != max - min")
        raw = wdir / (w.get("assets", {}).get("heightmap_raw") or "heightmap.raw")
        if raw.is_file() and raw.stat().st_size != meta["resolution"] ** 2 * 2:
            errs.append(f"heightmap.raw is {raw.stat().st_size} B, expected {meta['resolution'] ** 2 * 2}")

    for m in errs:
        R.error(f"{tag} invariant", m)
    for m in warns:
        R.warning(f"{tag}", m)
    if not errs:
        R.passed(f"{tag} cross-file invariants ({len(warns)} warnings)")


def validate_worlds(only: str | None) -> dict[str, dict]:
    print(f"\n== World packages ({WORLDS.relative_to(REPO) if WORLDS.is_relative_to(REPO) else WORLDS})")
    loaded = {}
    for wdir in sorted(p for p in WORLDS.iterdir() if p.is_dir()):
        wid = wdir.name
        if only and wid != only:
            continue
        wj = wdir / "world.json"
        if not wj.is_file():
            R.warning(f"{wid}/", "folder has no world.json (incomplete / empty world dir)")
            continue
        w = json.loads(wj.read_text())
        loaded[wid] = w
        check(f"{wid}/world.json", w, "world.schema.json")
        nav = meta = None
        nj = wdir / "navgrid.json"
        if nj.is_file():
            nav = json.loads(nj.read_text())
            ok = check(f"{wid}/navgrid.json", nav, "navgrid.schema.json")
            n = nav.get("width", 0) * nav.get("height", 0)
            if ok and (len(nav["heights"]) != n or len(nav["cost"]) != n):
                R.error(f"{wid}/navgrid.json", f"len(heights)={len(nav['heights'])}, len(cost)={len(nav['cost'])}, width*height={n}")
                nav = None
        else:
            R.error(f"{wid}/navgrid.json", "missing (required)")
        mj = wdir / "heightmap_meta.json"
        if mj.is_file():
            meta = json.loads(mj.read_text())
            check(f"{wid}/heightmap_meta.json", meta, "heightmap_meta.schema.json")
        world_invariants(wid, w, nav, meta, wdir)
    if "default" not in loaded and not only:
        R.error("data/worlds/default", "contract requires a 'default' world")
    return loaded


# ------------------------------------------------------------------ live services
def get_json(url: str, timeout=3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - local dev URLs
        return json.loads(r.read())


def validate_radio(worlds: dict[str, dict]) -> None:
    base = os.environ.get("RADIO_URL", "http://localhost:8090").rstrip("/")
    print(f"\n== Live radio ({base})")
    try:
        health = get_json(base + "/api/radio/health")
    except (urllib.error.URLError, OSError, ValueError) as e:
        R.skip("radio", f"not reachable ({e})")
        return
    check("GET /api/radio/health", health, "radio_state.schema.json", "/$defs/Health")
    state = get_json(base + "/api/radio/state")
    check("GET /api/radio/state", state, "radio_state.schema.json")
    rworld = get_json(base + "/api/radio/world")
    check("GET /api/radio/world", rworld, "radio_world.schema.json")
    try:
        excs = get_json(base + "/api/radio/excavators")
        check("GET /api/radio/excavators", excs, "radio_excavators.schema.json")
    except urllib.error.HTTPError as e:
        R.skip("GET /api/radio/excavators", f"HTTP {e.code} (route only exists on this branch's radio)")
        excs = None

    w = worlds.get(str(rworld.get("id")))
    if not w:
        R.warning("radio", f"radio world {rworld.get('id')!r} not among data/worlds")
        return
    errs = []
    if [s["id"] for s in rworld["sites"]] != [s["id"] for s in w["sites"]]:
        errs.append("site ids differ from world.json")
    if [r["id"] for r in rworld["roads"]] != [r["id"] for r in w["roads"]]:
        errs.append("road ids differ from world.json")
    if rworld.get("bounds") != w.get("bounds"):
        errs.append("bounds differ from world.json")
    wtrucks = sorted(t["id"] for t in w["trucks"])
    if sorted(t["id"] for t in state["trucks"]) != wtrucks:
        errs.append(f"state trucks {[t['id'] for t in state['trucks']]} != world.json trucks {wtrucks}")
    if excs is not None:
        weq = {e["id"]: e for e in w.get("equipment", [])}
        for e in excs:
            src = weq.get(e["id"])
            if not src or src["face"] != e["face"] or not close(src["pos3"], e["pos3"], 0.01):
                errs.append(f"excavator {e['id']} differs from world.json equipment")
    for m in errs:
        R.error("radio vs world.json", m)
    if not errs:
        R.passed(f"radio state/world/excavators consistent with data/worlds/{rworld['id']}/world.json")
    for t in state["trucks"]:
        wt = next((x for x in w["trucks"] if x["id"] == t["id"]), None)
        if wt and wt["callsign"] != t["callsign"]:
            R.warning("radio callsign", f"{t['id']}: radio says {t['callsign']!r}, world.json says {wt['callsign']!r}")


def validate_brain_live() -> None:
    base = os.environ.get("BRAIN_URL", "http://127.0.0.1:8080").rstrip("/")
    print(f"\n== Live brain ({base})")
    try:
        st = get_json(base + "/api/state")
    except (urllib.error.URLError, OSError, ValueError) as e:
        R.skip("brain", f"not reachable ({e})")
        return
    check("GET /api/state", st, "brain_state.schema.json")
    try:
        check("GET /api/world", get_json(base + "/api/world"), "brain_state.schema.json", "/$defs/BrainWorld")
    except (urllib.error.URLError, OSError, ValueError) as e:
        R.skip("GET /api/world", str(e))


# ------------------------------------------------------------------ offline contract checks
def validate_brain_offline() -> None:
    print("\n== Brain contract (origin/main brain/brain/world.py, offline)")
    try:
        src = subprocess.run(["git", "-C", str(REPO), "show", "origin/main:brain/brain/world.py"],
                             capture_output=True, text=True, check=True, timeout=20).stdout
    except (subprocess.SubprocessError, OSError) as e:
        R.skip("brain world.py", f"git show failed ({e})")
        return
    mod = types.ModuleType("brain_world")
    sys.modules["brain_world"] = mod  # dataclasses resolve annotations through sys.modules
    try:
        exec(compile(src, "origin/main:brain/brain/world.py", "exec"), mod.__dict__)  # noqa: S102 - repo code
    except Exception as e:  # noqa: BLE001
        R.skip("brain world.py", f"could not load ({type(e).__name__}: {e})")
        return
    w = mod.World()
    w.set_network([{"id": "r1", "from": "a", "to": "b", "width_m": 15.0, "one_way": False,
                    "points": [{"x": 0, "z": 0}, {"x": 5, "z": 0}]}],
                  [{"id": "crusher", "type": "crusher", "name": "Primary Crusher", "x": 5, "z": 0}],
                  [{"id": "j_1", "x": 0, "z": 0}])
    for i in range(3):
        w.truck(f"truck-{i}")
    t = w.truck("truck-0")
    t.set_goal((5.0, 0.0), "crusher")
    t.path = [[0.0, 0.0], [5.0, 0.0]]
    check("World.snapshot() (with network)", json.loads(json.dumps(w.snapshot())), "brain_state.schema.json")
    check("World.snapshot(network=False) (/ws/state)", json.loads(json.dumps(w.snapshot(network=False))), "brain_state.schema.json")
    check("Truck.to_dict() (goal response)", json.loads(json.dumps(t.to_dict())), "brain_goal.schema.json", "/$defs/GoalResponse")
    for body in ({"x": 12.5, "z": -3.0}, {"site": "crusher"}):
        check(f"goal body {json.dumps(body)}", body, "brain_goal.schema.json")
    bad = {"z": 1.0}
    if validator("brain_goal.schema.json").is_valid(bad):
        R.error("brain_goal.schema.json", f"{bad} should be rejected")
    else:
        R.passed("brain_goal.schema.json rejects {'z': 1.0}")


SAMPLE_COMMANDS = [
    "truck one go to the lower face",
    "all trucks return to the ready line",
    "truck two haul three loads from the middle face to the crusher",
    "everyone stop",
    "status report",
    "excavator one start digging",
    "all excavators hold",
    "truck one go to excavator two",
    "truck three haul from excavator three to the waste dump",
    "excavator two status",
]


def validate_parser() -> None:
    print("\n== Radio parser tool calls (radio/server.py, offline regex parser)")
    sys.path.insert(0, str(REPO / "radio"))
    try:
        import server as radio_server  # type: ignore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        R.skip("radio parser", f"cannot import radio/server.py ({type(e).__name__}: {e}); "
                               "run with: uv run --with jsonschema --with fastapi --with httpx --with python-multipart schema/validate.py")
        return
    world = radio_server.World(radio_server.WORLD_PATH)
    ids = sorted(world.trucks) or ["truck-0", "truck-1", "truck-2"]
    p = radio_server.Parser(world, lambda: ids)
    p.exc_state_fn = lambda: {}
    names = set()
    for text in SAMPLE_COMMANDS:
        calls = p.parse_regex(text)
        if not calls:
            R.warning("parser", f"no tool call for {text!r}")
        for c in calls:
            names.add(c["name"])
            check(f"parse {text!r} -> {c['name']}", c, "radio_command.schema.json", "/$defs/ToolCall")
    # LLM tool definitions: names + argument keys must match the ToolCall schema
    tc = json.loads((SCHEMA_DIR / "radio_command.schema.json").read_text())["$defs"]
    argdefs = {"go_to": "GoToArgs", "haul": "HaulArgs", "return_to_parking": "TrucksArgs", "stop": "TrucksArgs",
               "status": "TrucksArgs", "dig": "ExcavatorsArgs", "hold": "ExcavatorsArgs"}
    errs = []
    for t in p.tools_schema():
        fn = t["function"]
        if fn["name"] not in argdefs:
            errs.append(f"LLM tool {fn['name']!r} not in schema")
            continue
        keys = set(fn["parameters"]["properties"])
        want = set(tc[argdefs[fn["name"]]]["properties"])
        if keys != want:
            errs.append(f"{fn['name']} args {sorted(keys)} != schema {sorted(want)}")
    for m in errs:
        R.error("Parser.tools_schema()", m)
    if not errs:
        R.passed(f"Parser.tools_schema() matches radio_command.schema.json ({len(p.tools_schema())} tools)")

    # Radio.execute against the in-process MockBrain (no network, no TTS, loop not started)
    import asyncio  # noqa: PLC0415

    async def run_execute():
        rd = radio_server.radio  # module-level Radio(); start() is never called, so no brain polling
        rd.brain = radio_server.MockBrain(rd.world)
        rd.state, rd.brain_ok = await rd.brain.state(), True
        out = []
        for text in SAMPLE_COMMANDS:
            calls = rd.parser.parse_regex(text)
            actions, replies = await rd.execute(calls)
            out.append((text, {"heard": text, "parser": "regex", "actions": actions,
                               "replies": [{"id": "0" * 12, "truck_id": tid, "callsign": frm, "text": txt, "audio_url": None}
                                           for frm, tid, txt in replies] or
                                          [{"id": "0" * 12, "truck_id": None, "callsign": "Control",
                                            "text": "Control, say again, over.", "audio_url": None}],
                               "ms": 0}))
        rd.state = await rd.brain.state()
        return out, rd.state_msg()

    try:
        results, smsg = asyncio.run(run_execute())
    except Exception as e:  # noqa: BLE001
        R.skip("Radio.execute", f"{type(e).__name__}: {e}")
        return
    for text, resp in results:
        check(f"execute {text!r} -> CommandResponse", json.loads(json.dumps(resp)), "radio_command.schema.json", "/$defs/CommandResponse")
    check("Radio.state_msg() after commands (mock)", json.loads(json.dumps(smsg)), "radio_state.schema.json", "/$defs/StateMessage")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip live radio / brain")
    ap.add_argument("--world", help="validate only this world id")
    a = ap.parse_args()
    print(f"schemas: {', '.join(p.name for p in sorted(SCHEMA_DIR.glob('*.schema.json')))} (all valid draft 2020-12)")
    worlds = validate_worlds(a.world)
    if not a.offline:
        validate_radio(worlds)
        validate_brain_live()
    validate_brain_offline()
    validate_parser()
    print(f"\n{R.ok} passed, {R.fail} failed, {R.warn} warnings")
    return 1 if R.fail else 0


if __name__ == "__main__":
    sys.exit(main())
