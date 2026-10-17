import math

import numpy as np
from scipy.spatial import cKDTree

CORRIDOR_MIN_WIDTH = 10.0
CROSSING_ROADS = 4
CROSSING_EXTRA = 12.0
S = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])


def resample(P: np.ndarray, step: float) -> np.ndarray:
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    t = np.linspace(0, s[-1], max(1, int(round(s[-1] / step))) + 1)
    return np.stack([np.interp(t, s, P[:, k]) for k in range(P.shape[1])], 1)


def corridor_half(road: dict) -> float:
    return max(float(road.get("width_m") or 0.0), CORRIDOR_MIN_WIDTH) / 2


def crossing_segments(world: dict) -> list[np.ndarray]:
    out = []
    for road in sorted(world["roads"], key=lambda r: r["id"])[:CROSSING_ROADS]:
        pts = [(p[0], p[2]) for p in road["points"]]
        lengths = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        remaining = sum(lengths) / 2
        for i, length in enumerate(lengths):
            if remaining <= length or i == len(lengths) - 1:
                f = remaining / length if length else 0.0
                (ax, az), (bx, bz) = pts[i], pts[i + 1]
                mx, mz = ax + (bx - ax) * f, az + (bz - az) * f
                break
            remaining -= length
        seg = math.hypot(bx - ax, bz - az)
        rx, rz = (bz - az) / seg, -(bx - ax) / seg
        half = (road["width_m"] + CROSSING_EXTRA) / 2
        out.append(np.array([[mx - rx * half, mz - rz * half], [mx + rx * half, mz + rz * half]]))
    return out


def segment_distance(p: np.ndarray, seg: np.ndarray) -> np.ndarray:
    p = np.atleast_2d(p)
    a, b = seg
    ab = b - a
    t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def nav_points(world: dict) -> dict[str, list[float]]:
    pts = {}
    for s in world["sites"]:
        pts[s["id"]] = list(s["pos"])
        for k in ("load_point", "dump_point"):
            if s.get(k):
                pts[f"{s['id']}.{k}"] = [s[k]["pos3"][0], s[k]["pos3"][2]]
        for i, q in enumerate(s.get("slots", [])):
            pts[f"{s['id']}.slots[{i}]"] = [q["pos3"][0], q["pos3"][2]]
    return pts


class Roads:
    def __init__(self, world: dict, step: float = 0.5) -> None:
        self.roads = world["roads"]
        self.lines, self.trees, self.halves, self.y = [], [], [], []
        for r in self.roads:
            P = resample(np.array(r["points"], float), step)
            self.lines.append(P[:, [0, 2]])
            self.y.append(P[:, 1])
            self.trees.append(cKDTree(P[:, [0, 2]]))
            self.halves.append(corridor_half(r))

    def distances(self, p: np.ndarray) -> np.ndarray:
        p = np.atleast_2d(p)
        return np.stack([t.query(p)[0] for t in self.trees], 1)

    def clearance(self, p: np.ndarray) -> np.ndarray:
        return (self.distances(p) - np.array(self.halves)[None, :]).min(axis=1)

    def nearest(self, p: np.ndarray) -> tuple[int, int, float]:
        best = (0, 0, np.inf)
        for k, t in enumerate(self.trees):
            d, i = t.query(p)
            if d < best[2]:
                best = (k, int(i), float(d))
        return best

    def tangent(self, k: int, i: int) -> np.ndarray:
        L = self.lines[k]
        a, b = L[max(i - 2, 0)], L[min(i + 2, len(L) - 1)]
        t = b - a
        return t / max(np.linalg.norm(t), 1e-9)


def rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_x(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def euler_to_matrix(rot_deg) -> np.ndarray:
    rx, ry, rz = rot_deg
    return rot_y(ry) @ rot_x(rx) @ rot_z(rz)


def matrix_to_euler(R: np.ndarray) -> list[float]:
    a = math.asin(max(-1.0, min(1.0, -R[1, 2])))
    b = math.atan2(R[0, 2], R[2, 2])
    c = math.atan2(R[1, 0], R[1, 1])
    return [math.degrees(a), math.degrees(b), math.degrees(c)]


def align_up(n: np.ndarray) -> np.ndarray:
    up = np.array([0.0, 1.0, 0.0])
    n = n / np.linalg.norm(n)
    v = np.cross(up, n)
    s, c = np.linalg.norm(v), float(up @ n)
    if s < 1e-9:
        return np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k * ((1 - c) / s ** 2)


def heading_deg(t: np.ndarray) -> float:
    return math.degrees(math.atan2(t[0], t[1])) % 360
