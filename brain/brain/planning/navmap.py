import heapq
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class NavMap:
    width: int
    height: int
    cell: float
    ox: float
    oz: float
    cost: np.ndarray
    ground: np.ndarray
    blocked: np.ndarray
    inflated: np.ndarray
    sites: dict[str, tuple[float, float]]
    site_types: dict[str, str]

    @property
    def bounds(self) -> list[float]:
        return [self.ox, self.oz, self.ox + self.width * self.cell, self.oz + self.height * self.cell]

    def cell_of(self, x, z) -> tuple[np.ndarray, np.ndarray]:
        c = np.floor((np.asarray(x) - self.ox) / self.cell).astype(int)
        r = np.floor((np.asarray(z) - self.oz) / self.cell).astype(int)
        return r, c

    def _lookup(self, grid: np.ndarray, x, z, outside):
        r, c = np.broadcast_arrays(*self.cell_of(x, z))
        shape = r.shape
        r, c = r.reshape(-1), c.reshape(-1)
        inside = (r >= 0) & (c >= 0) & (r < self.height) & (c < self.width)
        out = np.full(r.shape, outside, dtype=grid.dtype)
        out[inside] = grid[r[inside], c[inside]]
        return out.reshape(shape)

    def blocked_at(self, x, z) -> np.ndarray:
        return self._lookup(self.blocked, x, z, True)

    def inflated_at(self, x, z) -> np.ndarray:
        return self._lookup(self.inflated, x, z, True)

    def ground_at(self, x, z) -> np.ndarray:
        return self._lookup(self.ground, x, z, np.nan)

    def cell_centre(self, r: int, c: int) -> tuple[float, float]:
        return self.ox + (c + 0.5) * self.cell, self.oz + (r + 0.5) * self.cell

    def astar(self, start: tuple[float, float], goal: tuple[float, float]) -> np.ndarray | None:
        sr, sc = (int(v) for v in self.cell_of(*start))
        gr, gc = (int(v) for v in self.cell_of(*goal))
        h, w = self.height, self.width
        if not (0 <= sr < h and 0 <= sc < w and 0 <= gr < h and 0 <= gc < w):
            return None
        passable = ~self.inflated
        passable[sr, sc] = passable[gr, gc] = True
        g = {(sr, sc): 0.0}
        parent: dict[tuple[int, int], tuple[int, int] | None] = {(sr, sc): None}
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
                    if not (0 <= rr < h and 0 <= cc < w) or not passable[rr, cc] or (rr, cc) in done:
                        continue
                    ng = gcur + (1.4142 if dr and dc else 1.0) * 0.5 * (self.cost[r, c] + self.cost[rr, cc])
                    if ng < g.get((rr, cc), math.inf):
                        g[(rr, cc)] = ng
                        parent[(rr, cc)] = cur
                        hh = max(abs(rr - gr), abs(cc - gc)) + 0.4142 * min(abs(rr - gr), abs(cc - gc))
                        heapq.heappush(heap, (ng + hh, ng, (rr, cc)))
        if (gr, gc) not in parent:
            return None
        cells = []
        node = (gr, gc)
        while node is not None:
            cells.append(self.cell_centre(*node))
            node = parent[node]
        return np.array(cells[::-1])


def load(world_dir: Path) -> NavMap:
    nav = json.loads((world_dir / "navgrid.json").read_text())
    meta = json.loads((world_dir / "world.json").read_text())
    w, h = nav["width"], nav["height"]
    cost = np.array(nav["cost"], dtype=float).reshape(h, w)
    blocked = cost < 0
    inflated = blocked.copy()
    padded = np.pad(blocked, 1, constant_values=True)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            inflated |= padded[1 + dr : 1 + dr + h, 1 + dc : 1 + dc + w]
    return NavMap(
        width=w,
        height=h,
        cell=float(nav["cell_m"]),
        ox=float(nav["origin"][0]),
        oz=float(nav["origin"][1]),
        cost=np.where(blocked, np.inf, cost),
        ground=np.array(nav["heights"], dtype=float).reshape(h, w),
        blocked=blocked,
        inflated=inflated,
        sites={s["id"]: (float(s["pos"][0]), float(s["pos"][1])) for s in meta["sites"]},
        site_types={s["id"]: s["type"] for s in meta["sites"]},
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
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    q = np.arange(0.0, s[-1] + 1e-9, step)
    return np.stack([np.interp(q, s, pts[:, 0]), np.interp(q, s, pts[:, 1])], axis=1)
