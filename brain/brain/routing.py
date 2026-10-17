import heapq
import math
from dataclasses import dataclass

ENTRY_MARGIN = 10.0
UTURN_PENALTY = 30.0
MIN_SPACING = 0.5

Point = tuple[float, float]


def cumulative(points: list[Point]) -> list[float]:
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    return cum


def project(points: list[Point], cum: list[float], x: float, z: float, lo: float = -math.inf, hi: float = math.inf):
    best = (math.inf, 0.0, points[0], 0)
    for i, ((ax, az), (bx, bz)) in enumerate(zip(points, points[1:])):
        if cum[i + 1] < lo or cum[i] > hi:
            continue
        dx, dz = bx - ax, bz - az
        seg2 = dx * dx + dz * dz
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / seg2)) if seg2 else 0.0
        px, pz = ax + t * dx, az + t * dz
        d = math.hypot(x - px, z - pz)
        if d < best[0]:
            best = (d, cum[i] + t * (cum[i + 1] - cum[i]), (px, pz), i)
    return best


def point_at(points: list[Point], cum: list[float], s: float) -> Point:
    if s >= cum[-1]:
        return points[-1]
    for i in range(len(points) - 1):
        if cum[i + 1] >= s:
            seg = cum[i + 1] - cum[i]
            t = (s - cum[i]) / seg if seg else 0.0
            (ax, az), (bx, bz) = points[i], points[i + 1]
            return ax + t * (bx - ax), az + t * (bz - az)
    return points[-1]


@dataclass
class Road:
    id: str
    a: str
    b: str
    points: list[Point]
    cum: list[float]
    one_way: bool

    @property
    def length(self) -> float:
        return self.cum[-1]


class RoadGraph:
    def __init__(self, roads: list[dict], sites: list[dict], junctions: list[dict]) -> None:
        self.nodes: dict[str, Point] = {n["id"]: (float(n["x"]), float(n["z"])) for n in [*sites, *junctions]}
        self.roads: list[Road] = []
        self.incoming: dict[str, list[tuple[str, Road, bool]]] = {n: [] for n in self.nodes}
        for r in roads:
            pts = [(float(p["x"]), float(p["z"])) for p in r.get("points", [])]
            if r["from"] not in self.nodes or r["to"] not in self.nodes or len(pts) < 2:
                continue
            road = Road(r["id"], r["from"], r["to"], pts, cumulative(pts), bool(r.get("one_way", False)))
            self.roads.append(road)
            self.incoming[road.b].append((road.a, road, False))
            if not road.one_way:
                self.incoming[road.a].append((road.b, road, True))

    def nearest_node(self, x: float, z: float) -> str:
        return min(self.nodes, key=lambda n: math.dist(self.nodes[n], (x, z)))

    def costs_to(self, goal: str) -> tuple[dict[str, float], dict[str, tuple[str, Road, bool]]]:
        dist = {goal: 0.0}
        nxt: dict[str, tuple[str, Road, bool]] = {}
        heap = [(0.0, goal)]
        while heap:
            d, node = heapq.heappop(heap)
            if d > dist[node]:
                continue
            for prev, road, reverse in self.incoming[node]:
                nd = d + road.length
                if nd < dist.get(prev, math.inf):
                    dist[prev] = nd
                    nxt[prev] = (node, road, reverse)
                    heapq.heappush(heap, (nd, prev))
        return dist, nxt

    def node_path(self, start: str, goal: str) -> list[str] | None:
        dist, nxt = self.costs_to(goal)
        if start not in dist:
            return None
        path = [start]
        while path[-1] != goal:
            path.append(nxt[path[-1]][0])
        return path

    def route(self, x: float, z: float, goal: str, yaw: float | None = None) -> list[list[float]] | None:
        if not self.roads or goal not in self.nodes:
            return None
        dist, nxt = self.costs_to(goal)
        hits = [(project(r.points, r.cum, x, z), r) for r in self.roads]
        nearest = min(h[0][0] for h in hits)
        best = None
        for (d, s, p, i), road in hits:
            if d > nearest + ENTRY_MARGIN:
                continue
            for forward in (True, False):
                if not forward and road.one_way:
                    continue
                exit_node = road.b if forward else road.a
                if exit_node not in dist:
                    continue
                cost = d + (road.length - s if forward else s) + dist[exit_node]
                if yaw is not None:
                    (ax, az), (bx, bz) = road.points[i], road.points[i + 1]
                    tangent = math.degrees(math.atan2(bx - ax, bz - az)) + (0.0 if forward else 180.0)
                    if abs((tangent - yaw + 180.0) % 360.0 - 180.0) > 90.0:
                        cost += UTURN_PENALTY
                if best is None or cost < best[0]:
                    best = (cost, road, forward, s, p, exit_node)
        if best is None:
            return None
        _, road, forward, s, p, node = best
        out = [p]

        def extend(points):
            for q in points:
                if math.dist(out[-1], q) >= MIN_SPACING:
                    out.append(q)

        if forward:
            extend(q for q, c in zip(road.points, road.cum) if c > s)
        else:
            extend(q for q, c in reversed(list(zip(road.points, road.cum))) if c < s)
        while node != goal:
            node, road, reverse = nxt[node]
            extend(reversed(road.points) if reverse else road.points)
        extend([self.nodes[goal]])
        if math.dist(out[-1], self.nodes[goal]) > 0:
            out[-1] = self.nodes[goal]
        return [[round(px, 2), round(pz, 2)] for px, pz in out]
