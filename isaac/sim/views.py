import math
from dataclasses import dataclass, field

import numpy as np

from .kinematics import look_rotation, yaw_matrix

CHASE_SMOOTH = 0.5
UP = np.array([0.0, 1.0, 0.0])


@dataclass
class View:
    name: str
    mode: str = "chase"
    width: int = 1280
    height: int = 720
    hfov: float = 70.0
    truck: str = ""
    excavator: str = ""
    azimuth: float = 0.0
    distance: float = 30.0
    elevation: float = 18.0
    target_height: float = 2.0
    center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    radius: float = 300.0
    period: float = 120.0
    position: list[float] = field(default_factory=lambda: [0.0, 100.0, 0.0])
    look_at: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    smooth: float = CHASE_SMOOTH
    pos: np.ndarray | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "View":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "pos"}
        return cls(**known)


def look(position: np.ndarray, target: np.ndarray) -> np.ndarray:
    f = np.asarray(target, float) - position
    if np.linalg.norm(f[[0, 2]]) < 1e-6:
        f = f + np.array([0.0, 0.0, 1e-3])
    return look_rotation(f, UP)


def view_pose(view: View, sim, dt: float) -> tuple[np.ndarray, np.ndarray]:
    if view.mode == "fixed":
        p = np.array(view.position, float)
        return p, look(p, view.look_at)
    if view.mode == "orbit":
        a = 2 * math.pi * sim.time / max(view.period, 1e-3)
        cx, cz = view.center
        ground = sim.height(cx, cz)
        p = np.array([cx + view.radius * math.sin(a), ground + view.position[1], cz + view.radius * math.cos(a)])
        return p, look(p, [cx, ground, cz])
    if view.mode == "excavator":
        box = next((b for b in sim.excavators if b.id == view.excavator), sim.excavators[0])
        base = np.array([box.x, box.y, box.z])
        e = math.radians(view.elevation)
        offset = yaw_matrix(sim.excavator_yaw(box) + view.azimuth) @ np.array([0.0, math.sin(e), math.cos(e)]) * view.distance
        p = base + offset
        return p, look(p, base + [0.0, view.target_height, 0.0])
    truck = next((t for t in sim.trucks if t.id == view.truck), sim.trucks[0])
    base = np.array([truck.x, truck.y, truck.z])
    e = math.radians(view.elevation)
    behind = yaw_matrix(truck.yaw) @ np.array([0.0, math.sin(e), -math.cos(e)]) * view.distance
    want = base + behind
    if view.pos is None or view.smooth <= 0:
        view.pos = want
    else:
        view.pos = view.pos + (want - view.pos) * (1 - math.exp(-dt / view.smooth))
    return view.pos.copy(), look(view.pos, base + [0.0, view.target_height, 0.0])
