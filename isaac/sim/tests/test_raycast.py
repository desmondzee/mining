import math

import numpy as np
import pytest

wp = pytest.importorskip("warp")

from isaac.sim import world as W  # noqa: E402
from isaac.sim.glb import read_glb  # noqa: E402
from isaac.sim.kinematics import yaw_matrix  # noqa: E402
from isaac.sim.raycast import Caster  # noqa: E402

PLANE = (np.array([[-100, 0, -100], [100, 0, -100], [100, 0, 100], [-100, 0, 100]], float), np.array([[0, 2, 1], [0, 3, 2]]))


@pytest.fixture(scope="module")
def caster():
    return Caster(PLANE, device="cpu")


def test_ground_plane(caster):
    d = caster.cast(np.array([[0, 2, 0], [0, 2, 0]]), np.array([[0, -1, 0], [0, 1, 0]]), 100)
    assert d[0] == pytest.approx(2.0) and math.isinf(d[1])
    assert caster.height(5, 5) == pytest.approx(0.0)
    assert math.isnan(caster.probe(np.array([500.0]), np.array([0.0]), np.array([10.0]), np.array([20.0]))[0])


def test_box_and_ignore(caster):
    caster.set_boxes(np.array([[0, 1, 10]]), yaw_matrix(45)[None], np.array([[1, 1, 1]]))
    o, d = np.array([[0, 1, 0]]), np.array([[0, 0, 1.0]])
    assert caster.cast(o, d, 100)[0] == pytest.approx(10 - math.sqrt(2), abs=1e-4)
    assert math.isinf(caster.cast(o, d, 100, ignore=0)[0])
    caster.set_boxes(np.zeros((0, 3)), np.zeros((0, 3, 3)), np.zeros((0, 3)))


def test_cylinder(caster):
    caster.set_cylinders(np.array([[0, 0, 20]]), 0.3, 1.8)
    d = caster.cast(np.array([[0, 1, 0], [0, 2.5, 0]]), np.array([[0, 0, 1.0], [0, 0, 1.0]]), 100)
    assert d[0] == pytest.approx(19.7, abs=1e-4) and math.isinf(d[1])
    top = caster.cast(np.array([[0, 5, 20]]), np.array([[0, -1.0, 0]]), 100)
    assert top[0] == pytest.approx(5 - 1.8, abs=1e-4)
    caster.set_cylinders(np.zeros((0, 3)), 0.3, 1.8)


def test_roads_lie_on_collider():
    world = W.load_world(W.REPO / "data/worlds/quarry")
    c = Caster(read_glb(world.asset("collider")).triangles(), device="cpu")
    pts = np.array([p for r in world.roads for p in r.points])
    h = c.probe(pts[:, 0], pts[:, 2], np.full(len(pts), 10000.0), np.full(len(pts), 20000.0))
    err = np.abs(h - pts[:, 1])
    assert np.isfinite(h).all()
    assert np.percentile(err, 95) < 1.0
