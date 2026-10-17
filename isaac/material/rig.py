import json
import math
import struct
import zlib
from pathlib import Path

import numpy as np

from isaac.sim import world as W
from isaac.sim.kinematics import yaw_matrix

RIGGED_GLB = "assets/models/excavator/excavator_rigged.glb"
CYCLE_S = 8.0
DUMP_YAW = 1.7
TRACK_RATE = 8.0
REST_RATE = 2.5
PARTS = ("tracks", "house", "boom", "stick", "bucket")
PARENT = {"tracks": None, "house": None, "boom": "house", "stick": "boom", "bucket": "stick"}
OFFSET = {
    "tracks": (0.0, 0.0, 0.0),
    "house": (0.0, 1.6123379468917853, -0.1230015754699707),
    "boom": (0.0, 0.6341491365416669, 0.07620181521291683),
    "stick": (0.0, 2.766117151997867, 5.229409373674814),
    "bucket": (0.0, -2.8967954598621404, 0.13093993268610848),
}
KEYS = np.array(
    [
        [0.00, 0.0, 12.0, -42.0, -60.0],
        [0.12, 0.0, 20.0, -50.0, -85.0],
        [0.30, 0.0, 14.0, -12.0, -10.0],
        [0.42, 0.0, -14.0, -5.0, 0.0],
        [0.60, 1.0, -14.0, -15.0, 0.0],
        [0.70, 1.0, -12.0, -35.0, -110.0],
        [0.78, 1.0, -12.0, -35.0, -100.0],
        [1.00, 0.0, 12.0, -42.0, -60.0],
    ]
)
PHASES = (("reach", 0.0), ("dig", 0.12), ("lift", 0.30), ("swing", 0.42), ("dump", 0.60), ("return", 0.78))
TO_ISAAC = np.array([[1.0, 0, 0, 0], [0, 0, 1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])


def smoother(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * t * (t * (t * 6 - 15) + 10)


def pose(u: float, dump_yaw: float = DUMP_YAW, lift_deg: float = 0.0) -> np.ndarray:
    u = u % 1.0
    i = int(np.searchsorted(KEYS[:-1, 0], u, side="left")) - 1
    i = min(max(i, 0), len(KEYS) - 2)
    a, b = KEYS[i], KEYS[i + 1]
    s = smoother((u - a[0]) / (b[0] - a[0]))
    out = a[1:] + (b[1:] - a[1:]) * s
    out[1] -= lift_deg * out[0]
    out[3] += lift_deg * out[0]
    out[0] *= dump_yaw
    return out


def phase_name(u: float) -> tuple[str, float]:
    u = u % 1.0
    for k in range(len(PHASES) - 1, -1, -1):
        name, start = PHASES[k]
        if u >= start:
            end = PHASES[k + 1][1] if k + 1 < len(PHASES) else 1.0
            return name, (u - start) / (end - start)
    return PHASES[0][0], 0.0


def rot_x(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[1:3, 1:3] = [[c, -s], [s, c]]
    return m


def rot_y(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def translate(p) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = p
    return m


def chain(joints: np.ndarray) -> dict[str, np.ndarray]:
    yaw, boom, stick, bucket = joints
    local = {
        "tracks": translate(OFFSET["tracks"]),
        "house": translate(OFFSET["house"]) @ rot_y(yaw),
        "boom": translate(OFFSET["boom"]) @ rot_x(boom),
        "stick": translate(OFFSET["stick"]) @ rot_x(stick),
        "bucket": translate(OFFSET["bucket"]) @ rot_x(bucket),
    }
    out = {}
    for p in PARTS:
        out[p] = local[p] if PARENT[p] is None else out[PARENT[p]] @ local[p]
    return out


def base_matrix(x: float, y: float, z: float, yaw_deg: float, model: np.ndarray | None = None) -> np.ndarray:
    if model is None:
        model, _ = W.model_box(W.REPO / W.EXCAVATOR_GLB, horizontal=W.EXCAVATOR_LENGTH)
    m = np.eye(4)
    m[:3, :3] = yaw_matrix(yaw_deg)
    m[:3, 3] = [x, y, z]
    return TO_ISAAC @ m @ model


def rest_offsets() -> dict[str, tuple[float, float, float]]:
    data = (W.REPO / RIGGED_GLB).read_bytes()
    length, _ = struct.unpack_from("<II", data, 12)
    nodes = json.loads(data[20 : 20 + length])["nodes"]
    return {n["name"]: tuple(n.get("translation", (0.0, 0.0, 0.0))) for n in nodes if n.get("name") in PARTS}


class ExcavatorRig:
    def __init__(self, ident: str, base: np.ndarray, period: float = CYCLE_S, dump_yaw: float = DUMP_YAW, phase: float | None = None,
                 lift_deg: float = 0.0) -> None:
        self.id = ident
        self.base = np.asarray(base, float)
        self.period = period
        self.dump_yaw = dump_yaw
        self.lift_deg = lift_deg
        self.phase = (zlib.crc32(ident.encode()) % 1000) / 1000 if phase is None else phase
        self.joints = np.zeros(4)
        self.digging = False

    def step(self, dt: float, digging: bool | None = None) -> None:
        if digging is not None:
            self.digging = digging
        goal = np.zeros(4)
        if self.digging:
            self.phase = (self.phase + dt / max(1.0, self.period)) % 1.0
            goal = pose(self.phase, self.dump_yaw, self.lift_deg)
        k = 1 - math.exp(-dt * (TRACK_RATE if self.digging else REST_RATE))
        self.joints += (goal - self.joints) * k

    def state(self) -> tuple[str, float]:
        return phase_name(self.phase) if self.digging else ("hold", 0.0)

    def matrices(self) -> dict[str, np.ndarray]:
        return {p: self.base @ m for p, m in chain(self.joints).items()}

    def bucket(self) -> np.ndarray:
        return self.base @ chain(self.joints)["bucket"]

    def aim(self, target_xy) -> None:
        local = np.linalg.solve(self.base, np.array([target_xy[0], target_xy[1], self.base[2, 3], 1.0]))
        self.dump_yaw = math.atan2(local[0], local[2] - OFFSET["house"][2])


def part_glbs(path: Path | None = None):
    from isaac.sim.glb import Glb, read_glb

    glb = read_glb(path or W.REPO / RIGGED_GLB)
    rest = chain(np.zeros(4))
    out = {}
    for part in PARTS:
        prims = []
        inv = np.linalg.inv(rest[part])
        for p in glb.primitives:
            if p.name.rsplit("_", 1)[0] != part:
                continue
            q = type(p)(p.name, p.points @ inv[:3, :3].T + inv[:3, 3], p.triangles, p.normals, p.uvs, p.colors, p.material)
            prims.append(q)
        out[part] = Glb(prims, glb.materials, glb.images)
    return out
