"""Road builder regression tests (plain asserts).

    envgen/.venv.nosync/bin/python -m envgen.tests.test_roads     # or: python envgen/tests/test_roads.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from envgen import common as C  # noqa: E402


def blocked_samples(roads, cost, origin, cell, step=0.1):
    """Count samples (every `step` m along each output segment) that fall in impassable cells."""
    h, w = cost.shape
    bad = []
    for r in roads:
        P = np.array([[q[0], q[2]] for q in r["points"]])
        for a, b in zip(P[:-1], P[1:]):
            L = float(np.hypot(*(b - a)))
            for t in np.linspace(0, 1, max(2, int(np.ceil(L / step)) + 1)):
                x, z = a + t * (b - a)
                row, col = int(np.floor((z - origin[1]) / cell)), int(np.floor((x - origin[0]) / cell))
                if not (0 <= row < h and 0 <= col < w) or cost[row, col] <= 0:
                    bad.append((col, row))
    return bad


def test_r5_l_corridor():
    """REVIEW.md R5 repro: 30x30 flat grid @ 1 m, only an L corridor (row 2 cols 2..24, col 24 rows 2..24)."""
    n, cell, origin = 30, 1.0, [0.0, 0.0]
    H = np.zeros((n, n))
    cost = -np.ones((n, n))
    cost[2, 2:25] = 1.0
    cost[2:25, 24] = 1.0
    sites = [{"id": "crusher", "type": "crusher", "name": "Crusher", "pos": [2.5, 2.5]},
             {"id": "ore_1", "type": "ore", "name": "Ore", "pos": [24.5, 24.5]}]
    junctions, roads, mask = C.build_road_network(H, cost, origin, cell, sites, snap_m=0)
    assert roads, "no road built"
    bad = blocked_samples(roads, cost, origin, cell)
    assert not bad, f"{len(bad)} samples in blocked cells, e.g. {sorted(set(bad))[:5]}"
    pts = np.array([[q[0], q[2]] for r in roads for q in r["points"]])
    in_corner = (np.floor(pts[:, 0]) == 24) & (np.floor(pts[:, 1]) == 2)
    assert in_corner.any(), "bend lost: no vertex in the corner cell (col 24, row 2)"
    world = {"sites": sites, "junctions": junctions, "roads": roads}
    assert C.check_road_graph(world) == [], C.check_road_graph(world)
    seg = [np.hypot(*(np.subtract(b, a)[[0, 2]])) for r in roads for a, b in zip(r["points"][:-1], r["points"][1:])]
    assert max(seg) <= 5.0 + 1e-6, max(seg)


def test_diagonal_corner_rule():
    """A diagonal step between two cells whose orthogonal neighbours are both blocked is not allowed."""
    cost = -np.ones((3, 3))
    cost[0, 0] = cost[1, 1] = 1.0
    assert C.least_cost_path(cost, (0, 0), (1, 1)) is None
    G = C.grade_graph(np.zeros((3, 3)), cost, 1.0)
    assert G.nnz == 0


if __name__ == "__main__":
    test_r5_l_corridor()
    test_diagonal_corner_rule()
    print("test_roads: all passed")
