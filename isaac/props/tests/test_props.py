import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate as Gn  # noqa: E402
import geometry as G  # noqa: E402

WORLD = json.loads((Gn.WORLD_DIR / "world.json").read_text())


@pytest.fixture(scope="session")
def data():
    return Gn.generate(write_usd=False, check=True)


def footprint(e):
    return 0.55 * max(e["scale"][0], e["scale"][2]) if e["kind"] in ("boulder", "debris") else (e["obstacle"] or {}).get("r", 0.25)


def xz(data):
    return np.array([[e["pos"][0], e["pos"][2]] for e in data["props"]])


def test_counts(data):
    s = data["stats"]
    assert s["debris_rocks"] == Gn.DEBRIS_ROCKS
    assert s["toe_boulders"] + s["berm_boulders"] + s["spoil_boulders"] + s["isolated_boulders"] == (
        Gn.TOE_BOULDERS + Gn.BERM_BOULDERS + Gn.SPOIL_BOULDERS + Gn.ISOLATED_BOULDERS)
    assert s["rock_piles"] == Gn.ROCK_PILES
    assert s["berms"] > 0


def test_outside_road_corridors(data):
    roads = G.Roads(WORLD)
    r = np.array([footprint(e) for e in data["props"]])
    assert (roads.clearance(xz(data)) - r >= Gn.CORRIDOR_MARGIN - 0.3).all()


def test_off_pads_and_crossings(data):
    pads = np.array(list(G.nav_points(WORLD).values()))
    p, r = xz(data), np.array([footprint(e) for e in data["props"]])
    assert (np.hypot(*(p[:, None] - pads[None]).transpose(2, 0, 1)).min(axis=1) - r >= Gn.PAD_RADIUS - 1e-6).all()
    for seg in G.crossing_segments(WORLD):
        assert (G.segment_distance(p, seg) - r >= 2.0).all()


def test_crossings_are_walker_areas():
    sys.path.insert(0, str(Gn.REPO / "brain"))
    from brain.pedestrians import make_walkers
    roads = [{"id": r["id"], "from": r["from"], "to": r["to"], "width_m": r["width_m"], "one_way": r.get("one_way", False),
              "points": [(q[0], q[2]) for q in r["points"]]} for r in WORLD["roads"]]
    areas, _ = make_walkers(roads, {s["id"]: s["type"] for s in WORLD["sites"]})
    segs = G.crossing_segments(WORLD)
    assert len(segs) == sum(len(a.polygon()) for a in areas) and len(areas) == 6
    assert np.allclose(segs[0][0], np.array(areas[0].polygon()[0]), atol=1e-6)


def test_obstacles_do_not_overlap(data):
    big = [e for e in data["props"] if e["obstacle"] and e["kind"] == "boulder"]
    p = np.array([[e["obstacle"]["x"], e["obstacle"]["z"]] for e in big])
    r = np.array([e["obstacle"]["r"] for e in big])
    piles = [e for e in data["props"] if e["kind"] == "rock_pile"]
    in_pile = np.zeros(len(big), bool)
    for m in piles:
        in_pile |= np.hypot(p[:, 0] - m["pos"][0], p[:, 1] - m["pos"][2]) < m["obstacle"]["r"]
    d = np.hypot(*(p[:, None] - p[None]).transpose(2, 0, 1))
    lim = (r[:, None] + r[None]) * 0.9 - 0.02
    np.fill_diagonal(d, np.inf)
    both = in_pile[:, None] & in_pile[None]
    assert not ((d < lim) & ~both).any()


def test_berms_clear_of_corridors_and_crossings(data):
    roads = G.Roads(WORLD)
    for b in data["berms"]:
        line = np.array(b["line"])
        assert (roads.clearance(line) >= Gn.BERM_TOE_GAP + Gn.BERM_BASE / 2 - 0.35).all(), b["id"]
        for seg in G.crossing_segments(WORLD):
            assert (G.segment_distance(line, seg) > Gn.BERM_CROSSING_GAP - 1.0).all()


def test_connectivity_preserved(data):
    nav = data["navgrid_check"]
    assert nav["with_props"]["pairs_connected"] == nav["baseline"]["pairs_connected"] == "21/21"
    assert nav["with_props"]["nav_point_pairs_connected"] == nav["baseline"]["nav_point_pairs_connected"]
    assert nav["with_props"]["problems"] == []


def test_deterministic(data):
    again = Gn.generate(write_usd=False, check=False)
    assert again["props"] == data["props"]
    assert again["berms"] == data["berms"]


def test_committed_props_json_current(data):
    committed = json.loads((Gn.WORLD_DIR / "props.json").read_text())
    assert committed["props"] == data["props"]
    assert committed["berms"] == data["berms"]


def test_euler_roundtrip():
    rng = np.random.default_rng(0)
    for _ in range(200):
        R = G.align_up(np.array([rng.normal(0, 0.4), 1.0, rng.normal(0, 0.4)])) @ G.rot_y(rng.uniform(0, 360))
        assert np.allclose(G.euler_to_matrix(G.matrix_to_euler(R)), R, atol=1e-9)


def test_loader_transform_matches_triangles(data):
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    import load
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    sample = {"props": [e for e in data["props"] if e["kind"] in ("boulder", "cone", "rock_pile")][:20], "berms": []}
    info = load.add_props(stage, sample, Gn.ASSETS_DIR, instanceable=False)
    assert info["props"] == len(sample["props"])
    V, F = load.prop_triangles(sample, Gn.ASSETS_DIR, berms=False)
    cache = UsdGeom.XformCache()
    pts = []
    for e in sample["props"]:
        prim = stage.GetPrimAtPath(f"/World/Props/{e['kind']}/{e['id']}/collision")
        m = np.asarray(cache.GetLocalToWorldTransform(prim), float).T
        v = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), float)
        pts.append(v @ m[:3, :3].T + m[:3, 3])
    iso = np.concatenate(pts)
    assert np.allclose(iso[:, [0, 2, 1]], V, atol=1e-3)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    assert len(F) and np.isfinite(a).all() and np.isfinite(np.cross(b - a, c - a)).all()
