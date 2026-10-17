import heapq
import math
from dataclasses import dataclass, replace

import numpy as np

from .navmap import NavMap, resample

SPACING_M = 2.0
LINK_M = 4.0
ATTACH_M = 6.0
ATTACH_K = 6
LEG_STRAIGHT_M = 3.0
SITE_RELAX_M = 20.0


@dataclass
class RoadGraph:
    pts: np.ndarray
    adj: list[list[tuple[int, float]]]


_graphs: dict[int, RoadGraph] = {}
_corridors: dict[int, NavMap] = {}


def build(nav: NavMap) -> RoadGraph:
    pts: list[np.ndarray] = []
    owner: list[int] = []
    ends: dict[str, int] = {}
    edges: list[tuple[int, int]] = []
    for k, road in enumerate(nav.roads):
        line = resample(road.points, SPACING_M)
        if np.hypot(*(line[-1] - road.points[-1])) > 0.05:
            line = np.vstack([line, road.points[-1]])
        ids = list(range(len(pts), len(pts) + len(line)))
        pts.extend(line)
        owner.extend([k] * len(line))
        for key, i in ((road.start, ids[0]), (road.end, ids[-1])):
            if key in ends:
                edges.append((ends[key], i))
            else:
                ends[key] = i
        edges.extend(zip(ids[:-1], ids[1:], strict=True))
    p, own = np.array(pts), np.array(owner)
    d = np.hypot(p[:, None, 0] - p[None, :, 0], p[:, None, 1] - p[None, :, 1])
    upper = np.arange(len(p))[:, None] < np.arange(len(p))[None, :]
    ii, jj = np.nonzero((d < LINK_M) & (own[:, None] != own[None, :]) & upper)
    edges.extend(zip(ii.tolist(), jj.tolist(), strict=True))
    adj: list[list[tuple[int, float]]] = [[] for _ in range(len(p))]
    for a, b in edges:
        w = float(d[a, b])
        adj[a].append((b, w))
        adj[b].append((a, w))
    return RoadGraph(p, adj)


def graph(nav: NavMap) -> RoadGraph | None:
    if not nav.roads:
        return None
    key = id(nav.roads)
    if key not in _graphs:
        _graphs[key] = build(nav)
    return _graphs[key]


def attach(g: RoadGraph, p, banned: np.ndarray) -> list[tuple[int, float]]:
    d = np.where(banned, np.inf, np.hypot(g.pts[:, 0] - p[0], g.pts[:, 1] - p[1]))
    order = np.argsort(d)[:ATTACH_K]
    return [(int(i), float(d[i])) for i in order if d[i] <= d[order[0]] + ATTACH_M]


def leg(nav: NavMap, a, b) -> np.ndarray | None:
    if math.dist(a, b) <= LEG_STRAIGHT_M:
        return np.array([a, b])
    return nav.astar(tuple(a), tuple(b))


def route(nav: NavMap, start, goal, avoid: np.ndarray | None = None, avoid_radius: float = 0.0) -> np.ndarray | None:
    g = graph(nav)
    if g is None:
        return None
    banned = np.zeros(len(g.pts), bool)
    if avoid is not None and len(avoid):
        gap = np.hypot(g.pts[:, None, 0] - avoid[None, :, 0], g.pts[:, None, 1] - avoid[None, :, 1])
        banned = (gap < avoid_radius).any(axis=1)
    if banned.all():
        return None
    src, dst = attach(g, start, banned), dict(attach(g, goal, banned))
    dist = dict(src)
    parent: dict[int, int | None] = {i: None for i, _ in src}
    heap = [(d, i) for i, d in src]
    heapq.heapify(heap)
    best, end = math.inf, None
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist.get(i, math.inf):
            continue
        if d >= best:
            break
        if i in dst and d + dst[i] < best:
            best, end = d + dst[i], i
        for j, w in g.adj[i]:
            if not banned[j] and d + w < dist.get(j, math.inf):
                dist[j], parent[j] = d + w, i
                heapq.heappush(heap, (d + w, j))
    if end is None:
        return None
    chain: list[int] = []
    k: int | None = end
    while k is not None:
        chain.append(k)
        k = parent[k]
    road = g.pts[chain[::-1]]
    head, tail = leg(nav, start, road[0]), leg(nav, road[-1], goal)
    if head is None or tail is None:
        return None
    return np.vstack([head[:-1], road, tail[1:]])


def corridor_nav(nav: NavMap) -> NavMap:
    key = id(nav)
    if key not in _corridors:
        h, w = nav.blocked.shape
        zs, xs = np.mgrid[0:h, 0:w]
        centres = np.stack([nav.ox + (xs.ravel() + 0.5) * nav.cell, nav.oz + (zs.ravel() + 0.5) * nav.cell], axis=1)
        allowed = np.zeros(h * w, bool)
        for road in nav.roads:
            line = resample(road.points, nav.cell / 2)
            lo, hi = line.min(axis=0) - road.width, line.max(axis=0) + road.width
            near = np.flatnonzero(((centres >= lo) & (centres <= hi)).all(axis=1))
            dd = np.min(np.linalg.norm(centres[near, None, :] - line[None], axis=-1), axis=1)
            allowed[near[dd <= road.width / 2 + nav.cell / 2]] = True
        for x, z in nav.sites.values():
            allowed |= np.hypot(centres[:, 0] - x, centres[:, 1] - z) <= SITE_RELAX_M
        _corridors[key] = replace(nav, blocked=nav.blocked | ~allowed.reshape(h, w))
    return _corridors[key]


def for_truck(nav: NavMap, x: float, z: float) -> NavMap:
    if not nav.roads:
        return nav
    c = corridor_nav(nav)
    return nav if c.blocked_at(x, z) else c
