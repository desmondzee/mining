"""Load a world from navgrid.json + world.json and plan routes on it.

Coordinates follow CONTRACT.md: metres, ground plane x (east) / z (north).
Nav grid row = z index, column = x index, cost -1 = impassable.
"""

from __future__ import annotations

import heapq
import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

WORLDS_DIR = os.path.join(os.path.dirname(__file__), "worlds")


@dataclass
class World:
    name: str
    n: int
    cell: float
    ox: float
    oz: float
    cost: np.ndarray  # (n, n) float, row = z
    blocked: np.ndarray  # (n, n) bool
    inflated: np.ndarray  # (n, n) bool, blocked grown by one cell
    sites: Dict[str, Tuple[float, float]]
    site_types: Dict[str, str]
    roads: List[np.ndarray]  # each (k, 2) x/z centreline
    road_ids: List[str]
    spawns: List[Tuple[float, float]]

    def cell_of(self, x, z):
        c = np.floor((np.asarray(x) - self.ox) / self.cell).astype(int)
        r = np.floor((np.asarray(z) - self.oz) / self.cell).astype(int)
        return r, c

    def blocked_at(self, x, z) -> np.ndarray:
        """True where (x, z) is impassable or off the map. Works on arrays."""
        r, c = self.cell_of(x, z)
        inside = (r >= 0) & (c >= 0) & (r < self.n) & (c < self.n)
        out = np.ones(np.shape(r), dtype=bool)
        out[inside] = self.blocked[r[inside], c[inside]]
        return out

    def cell_centre(self, r: int, c: int) -> Tuple[float, float]:
        return self.ox + (c + 0.5) * self.cell, self.oz + (r + 0.5) * self.cell

    def astar(self, start: Tuple[float, float], goal: Tuple[float, float], extra_blocked: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
        """Cheapest 8-connected path on the inflated grid, as (k, 2) x/z points.

        extra_blocked: optional (n, n) bool mask, for example cells around a parked vehicle.
        """
        sr, sc = (int(v) for v in self.cell_of(*start))
        gr, gc = (int(v) for v in self.cell_of(*goal))
        n = self.n
        # Allow start and goal cells inside the inflated margin (sites sit next to walls).
        passable = ~self.inflated if extra_blocked is None else ~(self.inflated | extra_blocked)
        passable[sr, sc] = passable[gr, gc] = True
        g = {(sr, sc): 0.0}
        parent = {(sr, sc): None}
        heap = [(0.0, 0.0, (sr, sc))]
        done = set()
        while heap:
            _, gcur, cur = heapq.heappop(heap)
            if cur in done:
                continue
            if cur == (gr, gc):
                break
            done.add(cur)
            r, c = cur
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if not dr and not dc:
                        continue
                    rr, cc = r + dr, c + dc
                    if not (0 <= rr < n and 0 <= cc < n) or not passable[rr, cc] or (rr, cc) in done:
                        continue
                    step = (1.4142 if dr and dc else 1.0) * 0.5 * (self.cost[r, c] + self.cost[rr, cc])
                    ng = gcur + step
                    if ng < g.get((rr, cc), math.inf):
                        g[(rr, cc)] = ng
                        parent[(rr, cc)] = cur
                        h = max(abs(rr - gr), abs(cc - gc)) + 0.4142 * min(abs(rr - gr), abs(cc - gc))
                        heapq.heappush(heap, (ng + h, ng, (rr, cc)))
        if (gr, gc) not in parent:
            return None
        cells = []
        node = (gr, gc)
        while node is not None:
            cells.append(self.cell_centre(*node))
            node = parent[node]
        return np.array(cells[::-1])


def load(world_id: str = "default", worlds_dir: str = WORLDS_DIR) -> World:
    folder = os.path.join(worlds_dir, world_id)
    with open(os.path.join(folder, "navgrid.json")) as f:
        nav = json.load(f)
    with open(os.path.join(folder, "world.json")) as f:
        meta = json.load(f)
    n = nav["width"]
    cost = np.array(nav["cost"], dtype=float).reshape(nav["height"], n)
    blocked = cost < 0
    cost = np.where(blocked, np.inf, cost)
    inflated = blocked.copy()
    padded = np.pad(blocked, 1, constant_values=True)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            inflated |= padded[1 + dr : 1 + dr + n, 1 + dc : 1 + dc + n]
    roads = [np.array([[p[0], p[2]] for p in r["points"]]) for r in meta.get("roads", [])]
    return World(
        name=meta.get("name", world_id),
        n=n,
        cell=float(nav["cell_m"]),
        ox=float(nav["origin"][0]),
        oz=float(nav["origin"][1]),
        cost=cost,
        blocked=blocked,
        inflated=inflated,
        sites={s["id"]: tuple(s["pos"]) for s in meta["sites"]},
        site_types={s["id"]: s["type"] for s in meta["sites"]},
        roads=roads,
        road_ids=[r["id"] for r in meta.get("roads", [])],
        spawns=[tuple(t["spawn"]) for t in meta.get("trucks", [])],
    )


def chaikin(pts: np.ndarray, iters: int = 3) -> np.ndarray:
    p = pts
    for _ in range(iters):
        q = np.empty((2 * (len(p) - 1) + 2, 2))
        q[0], q[-1] = p[0], p[-1]
        q[1:-1:2] = 0.75 * p[:-1] + 0.25 * p[1:]
        q[2:-1:2] = 0.25 * p[:-1] + 0.75 * p[1:]
        p = q
    return p


def resample(pts: np.ndarray, step: float) -> np.ndarray:
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    q = np.arange(0.0, s[-1] + 1e-9, step)
    return np.stack([np.interp(q, s, pts[:, 0]), np.interp(q, s, pts[:, 1])], axis=1)
