import math
from pathlib import Path

import pytest

from brain.fakesim import load_world
from brain.routing import RoadGraph, cumulative, project

QUARRY = Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry"


def line(*pts):
    out = []
    for (ax, az), (bx, bz) in zip(pts, pts[1:]):
        n = max(1, int(math.dist((ax, az), (bx, bz)) // 5))
        out += [{"x": ax + (bx - ax) * k / n, "z": az + (bz - az) * k / n} for k in range(n)]
    return out + [{"x": pts[-1][0], "z": pts[-1][1]}]


def road(rid, a, b, pts, one_way=False):
    return {"id": rid, "from": a, "to": b, "width_m": 10, "one_way": one_way, "points": line(*pts)}


SITES = [{"id": "A", "type": "parking", "name": "A", "x": 0, "z": 0}, {"id": "C", "type": "dump", "name": "C", "x": 100, "z": 100}]
JUNCTIONS = [{"id": "B", "x": 100, "z": 0}, {"id": "D", "x": 0, "z": 100}]


def square(one_way_db=False):
    roads = [
        road("ab", "A", "B", [(0, 0), (100, 0)]),
        road("bc", "B", "C", [(100, 0), (100, 100)]),
        road("dc", "D", "C", [(0, 100), (100, 100)], one_way=one_way_db),
        road("ad", "A", "D", [(0, 0), (0, 100)]),
    ]
    return RoadGraph(roads, SITES, JUNCTIONS)


def length(route):
    return cumulative([tuple(p) for p in route])[-1]


def test_dijkstra_shortest_and_one_way():
    g = RoadGraph(
        [road("ab", "A", "B", [(0, 0), (100, 0)]), road("bc", "B", "C", [(100, 0), (100, 100)]),
         road("ac", "A", "C", [(0, 0), (0, 300), (100, 100)], one_way=True)],
        SITES, JUNCTIONS,
    )
    assert g.node_path("A", "C") == ["A", "B", "C"]
    assert g.node_path("C", "A") == ["C", "B", "A"]
    g2 = RoadGraph([road("ca", "C", "A", [(100, 100), (0, 0)], one_way=True)], SITES, JUNCTIONS)
    assert g2.node_path("C", "A") == ["C", "A"] and g2.node_path("A", "C") is None
    assert g2.route(0, 0, "C") is None


def test_one_way_forces_detour():
    assert square().node_path("C", "D") == ["C", "D"]
    assert square(one_way_db=True).node_path("C", "D") == ["C", "B", "A", "D"]


def test_entry_projects_onto_nearest_road_and_follows_it():
    route = square().route(30, 4, "C")
    assert route[0] == pytest.approx([30, 0])
    assert route[-1] == [100, 100]
    assert [100, 0] in route
    assert length(route) == pytest.approx(170)


def test_entry_direction_uses_shorter_way_and_heading():
    g = square()
    assert g.route(10, -3, "D")[1][0] < 10
    assert g.route(10, -3, "D", yaw=270)[1][0] < 10
    assert g.route(90, -3, "D", yaw=90)[1][0] > 90


def test_route_is_continuous_and_ends_at_goal():
    w = load_world(QUARRY)
    g = RoadGraph(w["roads"], w["sites"], w["junctions"])
    for _, x, z, yaw in w["trucks"]:
        for goal in g.nodes:
            route = g.route(x, z, goal, yaw)
            assert route[-1] == pytest.approx(list(g.nodes[goal]), abs=0.01)
            gaps = [math.dist(a, b) for a, b in zip(route, route[1:])]
            assert max(gaps, default=0) < 8 and min(gaps, default=1) >= 0.5


def test_snaps_to_nearest_node():
    g = square()
    assert g.nearest_node(95, 8) == "B"
    assert g.nearest_node(-20, 120) == "D"


def test_project_returns_arc_length():
    pts = [(0, 0), (10, 0), (10, 10)]
    d, s, p, i = project(pts, cumulative(pts), 12, 5)
    assert (d, s, p, i) == (pytest.approx(2), pytest.approx(15), (10, 5), 1)
