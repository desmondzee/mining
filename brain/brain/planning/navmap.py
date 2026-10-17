import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NODE_SNAP_M = 15.0


@dataclass
class Road:
    start: str
    end: str
    points: np.ndarray
    one_way: bool

    @property
    def length(self) -> float:
        return float(np.hypot(*np.diff(self.points, axis=0).T).sum())


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
    nodes: dict[str, tuple[float, float]] = field(default_factory=dict)
    roads: list[Road] = field(default_factory=list)

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

    def nearest_node(self, x: float, z: float) -> str | None:
        best = min(self.nodes.items(), key=lambda kv: math.hypot(kv[1][0] - x, kv[1][1] - z), default=None)
        if best is None or math.hypot(best[1][0] - x, best[1][1] - z) > NODE_SNAP_M:
            return None
        return best[0]

    def road_route(self, start: tuple[float, float], goal: tuple[float, float]) -> np.ndarray | None:
        a, b = self.nearest_node(*start), self.nearest_node(*goal)
        if a is None or b is None:
            return None
        if a == b:
            return np.array([start, goal])
        edges: dict[str, list[tuple[str, np.ndarray, float]]] = {}
        for road in self.roads:
            edges.setdefault(road.start, []).append((road.end, road.points, road.length))
            if not road.one_way:
                edges.setdefault(road.end, []).append((road.start, road.points[::-1], road.length))
        dist = {a: 0.0}
        parent: dict[str, tuple[str, np.ndarray]] = {}
        heap = [(0.0, a)]
        while heap:
            d, node = heapq.heappop(heap)
            if node == b:
                break
            if d > dist[node]:
                continue
            for nxt, pts, length in edges.get(node, []):
                if d + length < dist.get(nxt, math.inf):
                    dist[nxt] = d + length
                    parent[nxt] = (node, pts)
                    heapq.heappush(heap, (d + length, nxt))
        if b not in parent:
            return None
        legs = []
        node = b
        while node != a:
            node, pts = parent[node]
            legs.append(pts)
        return np.vstack([[start], *legs[::-1], [goal]])

    def cells_near(self, points: np.ndarray, radius: float) -> np.ndarray:
        mask = np.zeros((self.height, self.width), bool)
        k = int(math.ceil(radius / self.cell))
        for dr in range(-k, k + 1):
            for dc in range(-k, k + 1):
                if math.hypot(dr, dc) * self.cell > radius + self.cell:
                    continue
                r, c = self.cell_of(points[:, 0], points[:, 1])
                r, c = r + dr, c + dc
                ok = (r >= 0) & (c >= 0) & (r < self.height) & (c < self.width)
                mask[r[ok], c[ok]] = True
        return mask

    def astar(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        inflate: bool = True,
        extra_blocked: np.ndarray | None = None,
    ) -> np.ndarray | None:
        sr, sc = (int(v) for v in self.cell_of(*start))
        gr, gc = (int(v) for v in self.cell_of(*goal))
        h, w = self.height, self.width
        if not (0 <= sr < h and 0 <= sc < w and 0 <= gr < h and 0 <= gc < w):
            return None
        passable = ~(self.inflated if inflate else self.blocked)
        if extra_blocked is not None:
            passable &= ~extra_blocked
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
    sites = {s["id"]: (float(s["pos"][0]), float(s["pos"][1])) for s in meta["sites"]}
    nodes = dict(sites)
    nodes.update({j["id"]: (float(j["pos"][0]), float(j["pos"][2])) for j in meta.get("junctions", [])})
    roads = [
        Road(r["from"], r["to"], np.array([[p[0], p[2]] for p in r["points"]], dtype=float), bool(r.get("one_way")))
        for r in meta.get("roads", [])
        if len(r.get("points", [])) > 1
    ]
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
        sites=sites,
        site_types={s["id"]: s["type"] for s in meta["sites"]},
        nodes=nodes,
        roads=roads,
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
