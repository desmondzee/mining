"""Excavator dig cycle + truck bed tipping on the stage built by build_stage.py. Pure pxr: works inside Isaac Sim
(call Animator.update(dt) every frame from the main thread) and locally with usd-core for testing.

    anim = Animator(stage)                     # discovers /World/Equipment/* rigs and /World/Trucks/*/BedHinge
    anim.set_excavator_state("exc-0", "digging")    # or "holding" (eases back to rest)
    anim.tip_bed("truck-0", 48)                # hold the bed at 48 deg (0 lowers it); clamped to 0..mine:bedMaxTipDeg
    anim.dump_cycle("truck-0", 6.0)            # raise, hold, lower within ~6 s
    anim.update(dt)

    link = RadioLink(anim, "http://127.0.0.1:8090")   # optional, read-only poller of GET /api/radio/state
    link.start(); ...; link.stop()

Module-level helpers set_excavator_state(id, state) / tip_bed(truck_id, deg) use the Animator made by init(stage).

Joints (USD xformOps written by build_stage.py, Isaac model frame +X fwd, +Y left/cab side, +Z up):
    house   xformOp:rotateZ:joint   + = swing toward the cab (+Y) side = browser rotation.y (+1.7 rad = truck side)
    boom    xformOp:rotateY:joint   + = boom down          (= browser/Unity rotation.x about glTF +X)
    stick   xformOp:rotateY:joint   + = stick in
    bucket  xformOp:rotateY:joint   + = bucket curl
    truck   BedHinge xformOp:rotateY:tip = -tip_deg (rig.json: +angle about glTF -X raises the front)
No dirt/dust here (owned by another teammate).

[exc-aim] Per-excavator scripted cycle: world.json equipment[].exc_keys_deg / exc_period_s (written by
envgen/exc_aim.py: dig at the toe of the face, dump with the bucket pin 1.5 m over the truck bed at the face's
load_point, 2-link IK on the rig.json pins). Same keys as the browser preview, so Isaac matches it. Falls back to
rig.json dig_cycle / DEFAULT_KEYS when a machine has none. world.json is found via Animator(world_json=...),
$WORLD_JSON, or next to / one level above the stage's root layer (data/worlds/<id>/isaac/world.usda).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
import zlib

from pxr import Usd, UsdGeom

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXC_RIG = os.path.join(REPO, "assets/models/excavator/rig.json")
TRUCK_RIG = os.path.join(REPO, "assets/models/komatsu_hd465/rig.json")

# envgen/preview/index.html EXC_KEYS, house column in degrees (1.7 rad = 97.4 deg). rig.json dig_cycle overrides.
DEFAULT_KEYS = [
    [0.00, 0.0, 12, -42, -60],    # at the face, reaching out
    [0.12, 0.0, 20, -50, -85],    # boom down, stick out, bucket open
    [0.30, 0.0, 14, -12, -10],    # dig: stick in + bucket curl
    [0.42, 0.0, -14, -5, 0],      # boom up with a full bucket
    [0.60, 97.4, -14, -15, 0],    # swing to the truck side
    [0.70, 97.4, -12, -35, -110],  # dump
    [0.78, 97.4, -12, -35, -100],
    [1.00, 0.0, 12, -42, -60],    # swing back to the face
]
DEFAULT_PERIOD_S = 8.0
TRACK_RATE, REST_RATE = 8.0, 2.5   # 1/s (browser / MineExcavatorRig)
JOINTS = ("house", "boom", "stick", "bucket")


def _load_cycle():
    try:
        with open(EXC_RIG) as f:
            dc = json.load(f).get("dig_cycle", {})
        keys = dc.get("keys_deg")
        if keys and all(len(k) == 5 for k in keys):
            return [list(map(float, k)) for k in keys], float(dc.get("period_s", DEFAULT_PERIOD_S))
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_KEYS, DEFAULT_PERIOD_S


def smootherstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def cycle_pose(keys, u: float) -> list[float]:
    i = 0
    while i < len(keys) - 2 and u > keys[i + 1][0]:
        i += 1
    a, b = keys[i], keys[i + 1]
    s = smootherstep((u - a[0]) / max(1e-9, b[0] - a[0]))
    return [a[k] + (b[k] - a[k]) * s for k in range(1, 5)]


def _norm_id(s: str) -> str:
    return str(s).replace("-", "_")


def _load_equipment(stage: Usd.Stage, world_json: str | None) -> dict:
    """[exc-aim] {normalised exc id: equipment dict} from world.json (empty if not found)."""
    cands = [world_json, os.environ.get("WORLD_JSON")]
    try:
        d = os.path.dirname(stage.GetRootLayer().realPath or "")
        if d:
            cands += [os.path.join(d, "world.json"), os.path.join(os.path.dirname(d), "world.json")]
    except Exception:
        pass
    for c in cands:
        if c and os.path.exists(c):
            try:
                with open(c) as f:
                    return {_norm_id(e["id"]): e for e in json.load(f).get("equipment", []) if "id" in e}
            except (OSError, ValueError, KeyError):
                continue
    return {}


class ExcavatorAnim:
    def __init__(self, prim: Usd.Prim, keys, period: float, equipment: dict | None = None):
        self.prim = prim
        self.id = (prim.GetAttribute("mine:id").Get() if prim.HasAttribute("mine:id") else None) or prim.GetName()
        self.face = prim.GetAttribute("mine:face").Get() if prim.HasAttribute("mine:face") else None
        self.keys, self.period = keys, period
        e = (equipment or {}).get(_norm_id(self.id))          # [exc-aim] scripted dig-at-face / dump-over-bed keys
        if e and isinstance(e.get("exc_keys_deg"), list) and len(e["exc_keys_deg"]) > 1:
            self.keys = [list(map(float, k)) for k in e["exc_keys_deg"]]
            self.period = float(e.get("exc_period_s", period))
            if e.get("exc_aim", {}).get("warnings"):
                print(f"[exc-aim] {self.id}: " + "; ".join(e["exc_aim"]["warnings"]))
        self.attrs = {}
        path = prim.GetPath()
        for j in JOINTS:
            path = path.AppendChild(j)
            jp = prim.GetStage().GetPrimAtPath(path)
            op = "xformOp:rotateZ:joint" if j == "house" else "xformOp:rotateY:joint"
            if not jp or not jp.HasAttribute(op):
                raise ValueError(f"{prim.GetPath()} has no rig joint {path}")
            self.attrs[j] = jp.GetAttribute(op)
        self.digging = False
        self.phase = (zlib.crc32(str(self.id).encode()) % 1000) / 1000.0   # de-sync several excavators
        self.pose = [0.0, 0.0, 0.0, 0.0]

    def set_state(self, state: str) -> None:
        self.digging = state == "digging"
        if self.prim.HasAttribute("mine:state"):
            self.prim.GetAttribute("mine:state").Set("digging" if self.digging else "holding")

    def update(self, dt: float) -> None:
        goal = [0.0, 0.0, 0.0, 0.0]
        if self.digging:
            self.phase = (self.phase + dt / max(1.0, self.period)) % 1.0
            goal = cycle_pose(self.keys, self.phase)
        k = 1.0 - math.exp(-dt * (TRACK_RATE if self.digging else REST_RATE))
        for i in range(4):
            self.pose[i] += (goal[i] - self.pose[i]) * k
        for i, j in enumerate(JOINTS):
            self.attrs[j].Set(float(self.pose[i]))


class BedAnim:
    def __init__(self, truck_prim: Usd.Prim):
        self.prim = truck_prim
        self.id = (truck_prim.GetAttribute("mine:id").Get() if truck_prim.HasAttribute("mine:id") else None) \
            or truck_prim.GetName()
        st = truck_prim.GetStage()
        hinge = None
        for rel in ("BedHinge", "Model/BedHinge"):    # asset referenced on the truck itself, or under Model
            hinge = st.GetPrimAtPath(truck_prim.GetPath().AppendPath(rel))
            if hinge and hinge.HasAttribute("xformOp:rotateY:tip"):
                break
        else:
            raise ValueError(f"{truck_prim.GetPath()} has no BedHinge")
        self.attr = hinge.GetAttribute("xformOp:rotateY:tip")
        asset_root = hinge.GetParent()                 # carries mine:bedMaxTipDeg etc.

        def get(name, default):
            a = asset_root.GetAttribute(name)
            return float(a.Get()) if a and a.Get() is not None else default
        self.max_deg = get("mine:bedMaxTipDeg", 48.0)
        self.raise_rate = self.max_deg / max(0.1, get("mine:bedRaiseS", 3.0))   # deg/s
        self.lower_rate = self.max_deg / max(0.1, get("mine:bedLowerS", 2.5))
        self.angle = 0.0
        self.target = 0.0
        self.cycle_t: float | None = None
        self.cycle_total = 6.0

    def tip(self, deg: float) -> None:
        self.cycle_t = None
        self.target = min(self.max_deg, max(0.0, float(deg)))

    def dump_cycle(self, total_s: float = 6.0) -> None:
        self.cycle_t, self.cycle_total = 0.0, max(total_s, self.max_deg / self.raise_rate + self.max_deg / self.lower_rate)

    def update(self, dt: float) -> None:
        if self.cycle_t is not None:
            self.cycle_t += dt
            lower_at = self.cycle_total - self.max_deg / self.lower_rate
            self.target = self.max_deg if self.cycle_t < lower_at else 0.0
            if self.cycle_t >= self.cycle_total and self.angle <= 1e-3:
                self.cycle_t = None
        d = self.target - self.angle
        step = (self.raise_rate if d > 0 else self.lower_rate) * dt
        self.angle += max(-step, min(step, d))
        self.attr.Set(-float(self.angle))


class Animator:
    def __init__(self, stage: Usd.Stage, root: str = "/World", world_json: str | None = None):
        self.stage = stage
        self.keys, self.period = _load_cycle()
        self.equipment = _load_equipment(stage, world_json)   # [exc-aim]
        self.excavators: dict[str, ExcavatorAnim] = {}
        self.beds: dict[str, BedAnim] = {}
        self.links: list["RadioLink"] = []
        self.rescan(root)

    def rescan(self, root: str = "/World") -> None:
        eq = self.stage.GetPrimAtPath(f"{root}/Equipment")
        for p in (eq.GetChildren() if eq else []):
            try:
                a = ExcavatorAnim(p, self.keys, self.period, self.equipment)
            except ValueError:
                continue
            self.excavators.setdefault(_norm_id(a.id), a)
        tr = self.stage.GetPrimAtPath(f"{root}/Trucks")
        for p in (tr.GetChildren() if tr else []):
            try:
                b = BedAnim(p)
            except ValueError:
                continue
            self.beds.setdefault(_norm_id(b.id), b)

    def excavator(self, exc_id: str) -> ExcavatorAnim | None:
        e = self.excavators.get(_norm_id(exc_id))
        if e is None:  # allow lookup by face id
            e = next((x for x in self.excavators.values() if x.face == exc_id), None)
        return e

    def set_excavator_state(self, exc_id: str, state: str) -> bool:
        e = self.excavator(exc_id)
        if e is None:
            return False
        e.set_state(state)
        return True

    def tip_bed(self, truck_id: str, deg: float) -> bool:
        b = self.beds.get(_norm_id(truck_id))
        if b is None:
            return False
        b.tip(deg)
        return True

    def dump_cycle(self, truck_id: str, total_s: float = 6.0) -> bool:
        b = self.beds.get(_norm_id(truck_id))
        if b is None:
            return False
        b.dump_cycle(total_s)
        return True

    def update(self, dt: float) -> None:
        dt = min(max(dt, 0.0), 0.1)
        for link in self.links:
            link.apply()
        for e in self.excavators.values():
            e.update(dt)
        for b in self.beds.values():
            b.update(dt)


class RadioLink:
    """Read-only poller of the radio's GET {url}/api/radio/state (schema/radio_state.schema.json). Never sends goals
    or actions. The HTTP thread only records the latest state; USD is touched in Animator.update (main thread).

    excavators[].state ('digging'|'holding') -> set_excavator_state (matched by id, else by face).
    trucks[].phase: 'dumping' (or any phase containing 'dump'/'tip' except travel legs 'to_*') -> one ~6 s
    dump_cycle on the rising edge."""

    def __init__(self, animator: Animator, url: str | None = None, period_s: float = 0.5, dump_s: float = 6.0):
        self.anim = animator
        self.url = (url or os.environ.get("RADIO_URL", "http://127.0.0.1:8090")).rstrip("/") + "/api/radio/state"
        self.period_s, self.dump_s = period_s, dump_s
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._exc_state: dict[str, str] = {}
        self._tipping: dict[str, bool] = {}
        self.errors = 0
        self.polls = 0
        animator.links.append(self)

    def start(self) -> "RadioLink":
        self._thread = threading.Thread(target=self._run, name="RadioLink", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self in self.anim.links:
            self.anim.links.remove(self)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=2.0) as r:
                    data = json.loads(r.read())
                with self._lock:
                    self._latest = data
                self.polls += 1
            except Exception:  # radio down: keep the last state, retry
                self.errors += 1
            self._stop.wait(self.period_s)

    @staticmethod
    def is_tip_phase(phase: str | None) -> bool:
        p = (phase or "").lower()
        return ("dump" in p or "tip" in p) and not p.startswith("to_")

    def apply(self) -> None:
        with self._lock:
            data, self._latest = self._latest, None
        if not data:
            return
        for e in data.get("excavators", []) or []:
            st = e.get("state")
            key = e.get("id") or e.get("face")
            if st and key and self._exc_state.get(key) != st:
                if self.anim.set_excavator_state(e.get("id") or "", st) or \
                        (e.get("face") and self.anim.set_excavator_state(e["face"], st)):
                    self._exc_state[key] = st
        for t in data.get("trucks", []) or []:
            tid = t.get("id")
            if not tid:
                continue
            tipping = self.is_tip_phase(t.get("phase"))
            if tipping and not self._tipping.get(tid):
                self.anim.dump_cycle(tid, self.dump_s)
            self._tipping[tid] = tipping


# ------------------------------------------------------------------ module-level convenience API
_default: Animator | None = None


def init(stage: Usd.Stage, root: str = "/World", world_json: str | None = None) -> Animator:
    global _default
    _default = Animator(stage, root, world_json)
    return _default


def set_excavator_state(exc_id: str, state: str) -> bool:
    assert _default, "call animate.init(stage) first"
    return _default.set_excavator_state(exc_id, state)


def tip_bed(truck_id: str, deg: float) -> bool:
    assert _default, "call animate.init(stage) first"
    return _default.tip_bed(truck_id, deg)


def update(dt: float) -> None:
    if _default:
        _default.update(dt)


def _selftest(world_usda: str) -> None:
    """Local test: open a built stage, add one truck reference in memory, run the cycle and a fake radio."""
    import http.server

    stage = Usd.Stage.Open(world_usda)
    t = UsdGeom.Xform.Define(stage, "/World/Trucks/truck_0").GetPrim()      # same layout as load_world.spawn_trucks
    UsdGeom.Xform.Define(stage, "/World/Trucks/truck_0/Model").GetPrim().GetReferences().AddReference(
        "models/komatsu_hd465.usdc")
    t.CreateAttribute("mine:id", __import__("pxr").Sdf.ValueTypeNames.String).Set("truck-0")
    anim = init(stage)
    print(f"  animator: {len(anim.excavators)} excavators {sorted(anim.excavators)}, beds {sorted(anim.beds)}, "
          f"cycle keys={len(anim.keys)} period={anim.period}s")
    assert set_excavator_state("exc-0", "digging") and tip_bed("truck-0", 30)
    samples = []
    for i in range(int(8.0 / (1 / 60))):
        update(1 / 60)
        if i % 60 == 0:
            e = anim.excavators["exc_0"]
            samples.append([round(a, 1) for a in e.pose])
    print("  exc-0 pose (house, boom, stick, bucket) each 1 s:", samples)
    print("  truck-0 bed rotateY:tip after 8 s:", round(anim.beds["truck_0"].attr.Get(), 2))

    state = {"excavators": [{"id": "exc-1", "face": "x", "state": "digging"}, {"id": "exc-0", "state": "holding"}],
             "trucks": [{"id": "truck-0", "phase": "to_dump"}]}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            b = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tip_bed("truck-0", 0)
    link = RadioLink(anim, f"http://127.0.0.1:{srv.server_port}", period_s=0.1).start()
    peak = 0.0
    for i in range(600):          # 10 s at 60 Hz, real-time sleeps so the poller runs
        if i == 60:
            state["trucks"][0]["phase"] = "dumping"
        update(1 / 60)
        peak = max(peak, -anim.beds["truck_0"].attr.Get())
        time.sleep(1 / 600)
    link.stop()
    srv.shutdown()
    print(f"  radio: polls={link.polls} errors={link.errors} exc-1 digging={anim.excavators['exc_1'].digging} "
          f"exc-0 digging={anim.excavators['exc_0'].digging} bed peak={peak:.1f} deg "
          f"bed now={-anim.beds['truck_0'].attr.Get():.1f} deg")
    assert anim.excavators["exc_1"].digging and not anim.excavators["exc_0"].digging and peak > 40


if __name__ == "__main__":
    import sys
    _selftest(sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "data/worlds/default/isaac/world.usda"))
    print("animate self-test ok")
