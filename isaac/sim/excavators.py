import json
import math
import threading
import time
import urllib.request
import zlib

import numpy as np

CYCLE_PERIOD = 8.0
TRUCK_SIDE_YAW = -97.0
TRACK_RATE = 8.0
REST_RATE = 2.5
POLL_PERIOD = 1.0
JOINTS = ("house", "boom", "stick", "bucket")
KEYS = [
    (0.00, 0.0, 12.0, -42.0, -60.0),
    (0.12, 0.0, 20.0, -50.0, -85.0),
    (0.30, 0.0, 14.0, -12.0, -10.0),
    (0.42, 0.0, -14.0, -5.0, 0.0),
    (0.60, 1.0, -14.0, -15.0, 0.0),
    (0.70, 1.0, -12.0, -35.0, -110.0),
    (0.78, 1.0, -12.0, -35.0, -100.0),
    (1.00, 0.0, 12.0, -42.0, -60.0),
]


def smoother(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def cycle_pose(phase: float) -> list[float]:
    i = 0
    while i < len(KEYS) - 2 and phase > KEYS[i + 1][0]:
        i += 1
    a, b = KEYS[i], KEYS[i + 1]
    u = smoother((phase - a[0]) / (b[0] - a[0]))
    pose = [a[j] + (b[j] - a[j]) * u for j in range(1, 5)]
    pose[0] *= TRUCK_SIDE_YAW
    return pose


class DigRig:
    def __init__(self, excavator_id: str, digging: bool = False) -> None:
        self.id = excavator_id
        self.digging = digging
        self.initial_phase = (zlib.crc32(excavator_id.encode()) % 1000) / 1000.0
        self.phase = self.initial_phase
        self.pose = [0.0, 0.0, 0.0, 0.0]

    def reset(self) -> None:
        self.digging = False
        self.phase = self.initial_phase
        self.pose = [0.0, 0.0, 0.0, 0.0]
        if getattr(self, "material", None) is not None:
            self.material = 0.0

    def step(self, dt: float) -> None:
        goal = [0.0, 0.0, 0.0, 0.0]
        if self.digging:
            self.phase = (self.phase + dt / max(1.0, CYCLE_PERIOD)) % 1.0
            goal = cycle_pose(self.phase)
        k = 1 - math.exp(-dt * (TRACK_RATE if self.digging else REST_RATE))
        self.pose = [p + (g - p) * k for p, g in zip(self.pose, goal)]

    def joint_rotations(self) -> dict[str, np.ndarray]:
        yaw, boom, stick, bucket = (math.radians(v) for v in self.pose)
        return {"house": rot_y(yaw), "boom": rot_x(boom), "stick": rot_x(stick), "bucket": rot_x(bucket)}


def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1.0, 0], [-s, 0, c]])


class RadioPoller:
    def __init__(self, url: str, rigs: dict[str, DigRig]) -> None:
        self.url = url.rstrip("/") + "/api/radio/excavators"
        self.rigs = rigs
        self.headings: dict[str, float] = {}
        self.thread = threading.Thread(target=self.run, name="radio-excavators", daemon=True)

    def start(self) -> "RadioPoller":
        self.thread.start()
        return self

    def reset(self) -> None:
        self.headings.clear()
        for rig in self.rigs.values():
            rig.reset()

    def apply(self, entries: list[dict]) -> None:
        for e in entries:
            rig = self.rigs.get(e.get("id"))
            if rig is None:
                continue
            rig.digging = e.get("state") == "digging"
            if "heading_deg" in e:
                self.headings[rig.id] = float(e["heading_deg"])

    def run(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=2) as r:
                    data = json.loads(r.read())
                self.apply(data if isinstance(data, list) else data.get("excavators", []))
            except (OSError, ValueError):
                pass
            time.sleep(POLL_PERIOD)
