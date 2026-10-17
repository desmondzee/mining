"""Radio-driven excavator animation on top of isaac/sim (add-on, no changes to isaac/sim needed).

isaac/sim/scene.py places each excavator as /World/Equipment/<id>/model, a reference to the NON-rigged
assets/models/excavator/excavator.glb flattened into meshes, and isaac/sim/sim.py gives the Warp caster one static box
per machine. `install()` adds, at runtime:

* a rig under /World/Equipment/<id>/rig/{tracks,house,boom,stick,bucket}: one USD per node of
  assets/models/excavator/excavator_rigged.glb (same bbox as excavator.glb, so Des's model_box matrix applies), each
  with one transform op written per frame. The static `model` prim is deactivated (not deleted).
* the scripted dig cycle per excavator: dig at the face, swing, dump over the truck bed at the preprogrammed spot,
  swing back. Keyframes come from world.json equipment[] (envgen/exc_aim.py):
      exc_keys_deg (+ exc_period_s)                    -> played as is
      dig_target + dump_target (no keys)               -> 2-link IK (envgen.exc_aim.Arm) to the targets
      dig_yaw_deg + dump_yaw_deg only                  -> rig.json arm keys, house swung between the two yaws
      nothing                                          -> assets/models/excavator/rig.json dig_cycle
  House angles are relative to heading_deg in the world-yaw convention (0 = +z, positive toward +x), like the browser
  preview and exc_aim; boom/stick/bucket signs are rig.json's (glTF frame).
* a read-only RadioLink: GET {RADIO_URL}/api/radio/excavators every 0.5 s ([{id, face, state}], state digging|holding;
  `{"items": [...]}` / `{"excavators": [...]}` also accepted). Never sends anything. No radio -> `default_state`.
* moving obstacles: the caster's set_boxes is wrapped so each animated excavator's single static box is replaced by
  five oriented boxes (tracks, house, boom, stick, bucket) that follow the rig, so depth/lidar see the swinging arm.
  The hello obstacle circles (brain planner) stay Des's static ones.

    from isaac.excavators import install
    excavators = install(sim, renderer, os.environ.get("RADIO_URL"), cache=args.cache)   # renderer=None: headless

Pose maths is numpy only (tests run without Isaac); USD is touched only when a scene is given (pxr imported lazily).
No truck bed tipping here (isaac/sim's truck is one flattened mesh) and no dirt/dust (owned by Des).
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from isaac.sim import world as W  # noqa: E402
from isaac.sim.kinematics import yaw_matrix  # noqa: E402

RIGGED_GLB = "assets/models/excavator/excavator_rigged.glb"
RIG_JSON = "assets/models/excavator/rig.json"
JOINTS = ("house", "boom", "stick", "bucket")
PARTS = ("tracks", *JOINTS)
DEFAULT_PERIOD_S = 8.0
TRACK_RATE, REST_RATE = 8.0, 2.5     # 1/s, as the browser preview / Unity MineExcavatorRig
POLL_S = 0.5
RIG_DUMP_HOUSE = 97.4                # rig.json dig_cycle house column value that means "over the truck"
MAX_DT = 0.1


def smootherstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def cycle_pose(keys: list[list[float]], u: float) -> np.ndarray:
    """keys = [[phase, house, boom, stick, bucket], ...] (phase 0..1, deg) -> pose at phase u (smootherstep)."""
    i = 0
    while i < len(keys) - 2 and u > keys[i + 1][0]:
        i += 1
    a, b = keys[i], keys[i + 1]
    s = smootherstep((u - a[0]) / max(1e-9, b[0] - a[0]))
    return np.array([a[k] + (b[k] - a[k]) * s for k in range(1, 5)], float)


def wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def axis_rotation(axis, deg: float) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) * c + s * k + (1 - c) * np.outer(a, a)


def about(pivot: np.ndarray, r: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = pivot - r @ pivot
    return m


def obb(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """PCA oriented box -> (centre, proper rotation with axes as columns, half extents)."""
    mean = points.mean(axis=0)
    _, vecs = np.linalg.eigh(np.cov((points - mean).T))
    if np.linalg.det(vecs) < 0:
        vecs[:, 0] = -vecs[:, 0]
    local = (points - mean) @ vecs
    lo, hi = local.min(axis=0), local.max(axis=0)
    return mean + vecs @ ((lo + hi) / 2), vecs, (hi - lo) / 2


# ------------------------------------------------------------------------------------------------------------ rig
class Rig:
    """Excavator joints in isaac/sim's model frame: W.model_box(excavator_rigged.glb, horizontal=10 m), i.e. Unity-like
    (+Y up, +Z forward, x mirrored from glTF), origin at the bbox bottom centre, the frame scene.py places with
    isaac_matrix(yaw_matrix(heading), (x, ground - sink, z))."""

    def __init__(self, repo: Path = W.REPO) -> None:
        self.glb_path = repo / RIGGED_GLB
        self.matrix, self.size = W.model_box(self.glb_path, horizontal=W.EXCAVATOR_LENGTH)
        rig = json.loads((repo / RIG_JSON).read_text())
        self.mirror = np.diag(np.sign(np.diag(self.matrix[:3, :3])))
        self.pivot = {j: (self.matrix @ [*rig["joints"][j]["pivot_m"], 1.0])[:3] for j in JOINTS}
        self.axis = {j: np.asarray(rig["joints"][j]["axis"], float) for j in JOINTS}
        cycle = rig.get("dig_cycle", {})
        self.keys = [list(map(float, k)) for k in cycle.get("keys_deg", [])] or [[0.0, 0, 0, 0, 0], [1.0, 0, 0, 0, 0]]
        self.period = float(cycle.get("period_s", DEFAULT_PERIOD_S))
        self._boxes: dict[str, tuple] | None = None

    def rotation(self, joint: str, deg: float) -> np.ndarray:
        if joint == "house":   # world-yaw convention (exc_aim / preview): +deg turns the arm from +z toward +x
            return yaw_matrix(deg)
        return self.mirror @ axis_rotation(self.axis[joint], deg) @ self.mirror

    def transforms(self, pose) -> dict[str, np.ndarray]:
        """pose = (house, boom, stick, bucket) deg -> {part: 4x4 model-frame transform of its rest-pose mesh}."""
        out, m = {"tracks": np.eye(4)}, np.eye(4)
        for j, deg in zip(JOINTS, pose):
            m = m @ about(self.pivot[j], self.rotation(j, float(deg)))
            out[j] = m
        return out

    def bucket_pin(self, pose) -> np.ndarray:
        """Model-frame position of the stick/bucket pin (what exc_aim aims at dig_target / dump_target)."""
        t = self.transforms(pose)["stick"]
        return t[:3, :3] @ self.pivot["bucket"] + t[:3, 3]

    def part_glbs(self):
        """{part: Glb} of the rigged model's node meshes in glTF rest frame (materials/images trimmed per part)."""
        from isaac.sim.glb import Glb, read_glb

        glb = read_glb(self.glb_path)
        out = {}
        for part in PARTS:
            prims = [p for p in glb.primitives if p.name.rsplit("_", 1)[0] == part]
            mats = sorted({p.material for p in prims if p.material is not None})
            imgs = sorted({glb.materials[m].image for m in mats if glb.materials[m].image is not None})
            mat_map, img_map = {m: i for i, m in enumerate(mats)}, {k: i for i, k in enumerate(imgs)}
            materials = []
            for m in mats:
                src = glb.materials[m]
                materials.append(type(src)(src.name, src.color, img_map.get(src.image), src.unlit, src.double_sided))
            prims = [type(p)(p.name, p.points, p.triangles, p.normals, p.uvs, p.colors, mat_map.get(p.material)) for p in prims]
            out[part] = Glb(prims, materials, [glb.images[k] for k in imgs])
        return out

    def boxes(self) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """{part: (centre, rotation, half)} rest-pose oriented boxes in the model frame (for the Warp caster)."""
        if self._boxes is None:
            parts = self.part_glbs()
            self._boxes = {}
            for part, g in parts.items():
                if g.primitives:
                    pts = np.concatenate([p.points for p in g.primitives]) @ self.matrix[:3, :3].T + self.matrix[:3, 3]
                    self._boxes[part] = obb(pts)
        return self._boxes


# ------------------------------------------------------------------------------------------------------- keyframes
def ik_keys(entry: dict) -> list[list[float]]:
    """Dig-cycle keys that put the bucket pin on entry's dig_target / dump_target (envgen.exc_aim 2-link IK)."""
    from envgen.exc_aim import LIMITS, PHASES, PITCH, Arm

    arm = Arm()
    hd = float(entry.get("heading_deg", 0.0))
    ex, ey, ez = (float(v) for v in entry["pos3"])
    ax, az = ex + math.sin(math.radians(hd)) * arm.slew_z, ez + math.cos(math.radians(hd)) * arm.slew_z

    def polar(t):
        return math.degrees(math.atan2(t[0] - ax, t[2] - az)), math.hypot(t[0] - ax, t[2] - az), float(t[1]) - ey

    dig_yaw, dig_r, dig_h = polar(entry["dig_target"])
    dump_yaw, dump_r, dump_h = polar(entry["dump_target"])
    hp = wrap(float(entry.get("dump_yaw_deg", dump_yaw)) - hd)
    hg = hp + wrap(float(entry.get("dig_yaw_deg", dig_yaw)) - float(entry.get("dump_yaw_deg", dump_yaw)))
    poses = [(hg, dig_r, dig_h + 1.6), (hg, dig_r, dig_h), (hg, max(dig_r - 2.0, 4.8), dig_h + 0.2),
             (hg, max(dig_r - 1.0, 5.5), dig_h + 3.5), (hp, dump_r, dump_h + 0.8), (hp, dump_r, dump_h),
             (hp, dump_r, dump_h + 0.3), (hg, dig_r, dig_h + 1.6)]
    keys = []
    for ph, pitch, (house, r, h) in zip(PHASES, PITCH, poses):
        b, s, _, _ = arm.ik(r, h)
        keys.append([ph, house, b, s, float(np.clip(pitch - b - s, *LIMITS["bucket"]))])
    return keys


def keys_for(entry: dict, rig: Rig) -> tuple[list[list[float]], float, str]:
    """-> (keys, period_s, source) for one world.json equipment[] entry (see module docstring for the order)."""
    keys = entry.get("exc_keys_deg")
    if isinstance(keys, list) and len(keys) > 1 and all(len(k) == 5 for k in keys):
        return [list(map(float, k)) for k in keys], float(entry.get("exc_period_s", rig.period)), "world.json exc_keys_deg"
    if entry.get("dig_target") and entry.get("dump_target") and entry.get("pos3"):
        try:
            return ik_keys(entry), float(entry.get("exc_period_s", 10.0)), "world.json dig/dump targets (IK)"
        except ImportError:
            pass
    if "dig_yaw_deg" in entry and "dump_yaw_deg" in entry:
        hd = float(entry.get("heading_deg", 0.0))
        hp = wrap(float(entry["dump_yaw_deg"]) - hd)
        hg = hp + wrap(float(entry["dig_yaw_deg"]) - float(entry["dump_yaw_deg"]))
        out = [[k[0], hg + (hp - hg) * k[1] / RIG_DUMP_HOUSE, *k[2:]] for k in rig.keys]
        return out, rig.period, "world.json dig/dump yaw + rig.json arm keys"
    return [list(k) for k in rig.keys], rig.period, "rig.json dig_cycle"


# ------------------------------------------------------------------------------------------------------- animation
@dataclass
class Excavator:
    id: str
    face: str | None
    box: W.Box
    keys: list[list[float]]
    period: float
    source: str
    digging: bool = True
    phase: float = 0.0
    pose: np.ndarray = field(default_factory=lambda: np.zeros(4))

    def __post_init__(self) -> None:
        self.phase = (zlib.crc32(self.id.encode()) % 1000) / 1000.0   # de-sync machines

    def set_state(self, state: str) -> None:
        self.digging = state == "digging"

    def step(self, dt: float) -> None:
        goal = np.zeros(4)
        if self.digging:
            self.phase = (self.phase + dt / max(1.0, self.period)) % 1.0
            goal = cycle_pose(self.keys, self.phase)
        k = 1.0 - math.exp(-dt * (TRACK_RATE if self.digging else REST_RATE))
        self.pose += (goal - self.pose) * k

    def base(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array([self.box.x, self.box.y, self.box.z]), yaw_matrix(self.box.yaw)


class Excavators:
    """All animated excavators of a Sim. step(dt) advances the cycle (numpy only); write_usd() moves the rig prims."""

    def __init__(self, sim, default_state: str = "digging", rig: Rig | None = None) -> None:
        self.sim = sim
        self.rig = rig or Rig()
        entries = {e.get("id"): e for e in sim.world.equipment}
        self.items: list[Excavator] = []
        for b in sim.excavators:
            e = entries.get(b.id, {})
            keys, period, source = keys_for(e, self.rig)
            self.items.append(Excavator(b.id, e.get("face"), b, keys, period, source, default_state == "digging"))
        self.links: list[RadioLink] = []
        self.ops: dict[str, dict[str, object]] = {}

    def find(self, key: str | None) -> Excavator | None:
        if not key:
            return None
        norm = str(key).replace("_", "-")
        return next((x for x in self.items if x.id == norm or x.id == key), None) or next((x for x in self.items if x.face == key), None)

    def set_state(self, key: str, state: str) -> bool:
        x = self.find(key)
        if x is not None:
            x.set_state(state)
        return x is not None

    def step(self, dt: float) -> None:
        dt = min(max(float(dt), 0.0), MAX_DT)
        for link in self.links:
            link.apply(self)
        for x in self.items:
            x.step(dt)

    def boxes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """World oriented boxes (centres, rotations, half extents) of every part of every machine, current pose."""
        rest = self.rig.boxes()
        cs, rs, hs = [], [], []
        for x in self.items:
            p, ry = x.base()
            for part, t in self.rig.transforms(x.pose).items():
                if part not in rest:
                    continue
                c, r, h = rest[part]
                cs.append(p + ry @ (t[:3, :3] @ c + t[:3, 3]))
                rs.append(ry @ t[:3, :3] @ r)
                hs.append(h)
        return np.array(cs).reshape(-1, 3), np.array(rs).reshape(-1, 3, 3), np.array(hs).reshape(-1, 3)

    # -- USD (Isaac or usd-core)
    def build_usd(self, stage, cache: str | Path) -> None:
        from pxr import UsdGeom

        cache = Path(cache)
        usds = convert_parts(self.rig, cache)
        for x in self.items:
            base = f"/World/Equipment/{x.id.replace('-', '_')}"
            if not stage.GetPrimAtPath(base):
                UsdGeom.Xform.Define(stage, base)
            model = stage.GetPrimAtPath(base + "/model")
            if model:
                model.SetActive(False)
            self.ops[x.id] = {}
            for part in PARTS:
                if part not in usds:
                    continue
                xf = UsdGeom.Xform.Define(stage, f"{base}/rig/{part}")
                xf.GetPrim().GetReferences().AddReference(str(usds[part]))
                self.ops[x.id][part] = xf.AddTransformOp()
        self.write_usd()

    def write_usd(self) -> None:
        if not self.ops:
            return
        from isaac.sim.usdbuild import isaac_matrix

        for x in self.items:
            ops = self.ops.get(x.id, {})
            for part, t in self.rig.transforms(x.pose).items():
                if part in ops:
                    ops[part].Set(isaac_matrix(t[:3, :3], t[:3, 3]))


def convert_parts(rig: Rig, cache: Path) -> dict[str, Path]:
    """One cached Z-up USD per rigged node, through isaac/sim/usdbuild (same axis swap / cache key as convert())."""
    from isaac.sim.glb import transform
    from isaac.sim.usdbuild import TO_ISAAC, cache_key, write_mesh_usd

    full = TO_ISAAC @ rig.matrix
    key = cache_key(rig.glb_path, full)
    out, todo = {}, {}
    for part in PARTS:
        out[part] = cache / f"excavator_{part}_{key}.usdc"
        if not out[part].exists():
            todo[part] = out[part]
    if todo:
        glbs = rig.part_glbs()
        for part, path in todo.items():
            if not glbs[part].primitives:
                out.pop(part)
                continue
            tmp = path.with_suffix(".tmp.usdc")
            write_mesh_usd(transform(glbs[part], full), tmp, f"excavator_{part}")
            tmp.replace(path)
    return out


# ------------------------------------------------------------------------------------------------------- radio link
class RadioLink:
    """Read-only poller of GET {url}/api/radio/excavators. The HTTP thread only stores the latest body; states are
    applied on the sim thread in Excavators.step()."""

    def __init__(self, url: str, period_s: float = POLL_S, timeout_s: float = 2.0) -> None:
        self.url = url.rstrip("/") + "/api/radio/excavators"
        self.period_s, self.timeout_s = period_s, timeout_s
        self.polls = self.errors = 0
        self.states: dict[str, str] = {}
        self._latest = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "RadioLink":
        self._thread = threading.Thread(target=self._run, name="RadioLink", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout_s) as r:
                data = json.loads(r.read())
            with self._lock:
                self._latest = data
            self.polls += 1
        except Exception:  # noqa: BLE001  radio down or bad body: keep the last state, retry
            self.errors += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.period_s)

    @staticmethod
    def items(data) -> list[dict]:
        if isinstance(data, dict):
            data = data.get("items", data.get("excavators", []))
        return [d for d in data or [] if isinstance(d, dict)]

    def apply(self, excavators: Excavators) -> None:
        with self._lock:
            data, self._latest = self._latest, None
        if data is None:
            return
        for e in self.items(data):
            state = e.get("state")
            if state not in ("digging", "holding"):
                continue
            if excavators.set_state(e.get("id"), state) or excavators.set_state(e.get("face"), state):
                self.states[e.get("id") or e.get("face")] = state


# ------------------------------------------------------------------------------------------------------- install
def hook_caster(sim, excavators: Excavators) -> None:
    """Wrap sim.caster.set_boxes: Sim.sync_obstacles passes [trucks..., excavators...]; the static excavator boxes
    are swapped for the animated part boxes. No-op without a caster (raycast off)."""
    caster = getattr(sim, "caster", None)
    if caster is None or getattr(caster, "_excavators_hooked", False):
        return
    original = caster.set_boxes

    def set_boxes(centres, rotations, half):
        n, k = len(sim.trucks), len(sim.excavators)
        centres, rotations, half = np.asarray(centres, float), np.asarray(rotations, float), np.asarray(half, float)
        if k and len(centres) >= n + k:
            keep = np.r_[0:n, n + k : len(centres)]
            ec, er, eh = excavators.boxes()
            centres = np.concatenate([centres[keep].reshape(-1, 3), ec])
            rotations = np.concatenate([rotations[keep].reshape(-1, 3, 3), er])
            half = np.concatenate([half[keep].reshape(-1, 3), eh])
        original(centres, rotations, half)

    caster.set_boxes = set_boxes
    caster._excavators_hooked = True


def install(sim, scene=None, radio_url: str | None = None, cache: str | Path | None = None,
            default_state: str = "digging", poll_s: float = POLL_S) -> Excavators:
    """Animate sim's excavators. sim = isaac.sim.sim.Sim; scene = isaac.sim.scene.IsaacRenderer (or anything with
    .stage and .update(sim, dt)) or None for headless/RL; radio_url None -> $RADIO_URL, "" -> no radio.
    Wraps sim.step (advance the cycle), sim.caster.set_boxes (moving arm boxes) and scene.update (write the rig)."""
    excavators = Excavators(sim, default_state)
    if not excavators.items:
        return excavators
    url = os.environ.get("RADIO_URL", "") if radio_url is None else radio_url
    if url:
        excavators.links.append(RadioLink(url, poll_s).start())

    step = sim.step

    def sim_step(dt, now):
        step(dt, now)
        excavators.step(dt)

    sim.step = sim_step
    hook_caster(sim, excavators)
    if scene is not None and getattr(scene, "stage", None) is not None:
        excavators.build_usd(scene.stage, cache or (W.REPO / "downloads/isaac/cache"))
        update = scene.update

        def scene_update(s, dt):
            update(s, dt)
            excavators.write_usd()

        scene.update = scene_update
    for x in excavators.items:
        warn = next((e.get("exc_aim", {}).get("warnings") for e in sim.world.equipment if e.get("id") == x.id), None)
        print(f"excavators: {x.id} face {x.face} keys from {x.source}, period {x.period:.0f}s"
              + (f" (exc_aim: {'; '.join(warn)})" if warn else ""), flush=True)
    print(f"excavators: radio {'GET ' + excavators.links[0].url if excavators.links else 'off'} "
          f"(default {default_state}), caster boxes {'animated' if getattr(sim, 'caster', None) is not None else 'off'}, "
          f"usd {'rigged' if excavators.ops else 'none'}", flush=True)
    return excavators
