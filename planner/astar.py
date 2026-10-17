"""8-connected grid A* over a cost grid."""

from __future__ import annotations

import heapq
import math
from typing import List, Optional, Tuple

import numpy as np

Cell = Tuple[int, int]

_MOVES = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
_SQRT2 = math.sqrt(2.0)


def astar(cost: np.ndarray, start: Cell, goal: Cell) -> Optional[List[Cell]]:
    """Return the cheapest cell path from start to goal, or None.

    Step cost = distance * mean cost of the two cells. The octile heuristic
    times the minimum cell cost stays admissible, so the path is optimal.
    """
    h_scale = float(cost[np.isfinite(cost)].min())
    rows, cols = cost.shape
    gy, gx = goal

    def h(r: int, c: int) -> float:
        dy, dx = abs(r - gy), abs(c - gx)
        return h_scale * (max(dy, dx) + (_SQRT2 - 1.0) * min(dy, dx))

    g = {start: 0.0}
    parent = {start: start}
    open_heap = [(h(*start), 0.0, start)]
    closed = set()
    while open_heap:
        _, g_cur, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == goal:
            path = [cur]
            while cur != start:
                cur = parent[cur]
                path.append(cur)
            return path[::-1]
        closed.add(cur)
        r, c = cur
        for dr, dc in _MOVES:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            cell_cost = cost[nr, nc]
            if not np.isfinite(cell_cost):
                continue
            step = (_SQRT2 if dr and dc else 1.0) * 0.5 * (cost[r, c] + cell_cost)
            ng = g_cur + step
            nxt = (nr, nc)
            if ng < g.get(nxt, math.inf):
                g[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(open_heap, (ng + h(nr, nc), ng, nxt))
    return None
