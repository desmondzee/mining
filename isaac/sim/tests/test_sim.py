import numpy as np
import pytest

from isaac.sim import world as W
from isaac.sim.sim import Sim

VIEWS = [{"name": "drone", "mode": "chase", "truck": "truck-0", "width": 64, "height": 36}]


@pytest.fixture(scope="module", params=[False, True], ids=["heightmap", "warp"])
def sim(request):
    if request.param:
        pytest.importorskip("warp")
    return Sim(W.REPO / "data/worlds/quarry", raycast=request.param, views=VIEWS)


def test_hello(sim):
    h = sim.hello()
    assert h["cameras"] == ["overview", "truck-0", "truck-1", "truck-2", "drone"]
    assert len(h["obstacles"]) == 3 and len(h["paths"]) == len(sim.areas) == 6
    assert all(len(p["points"]) >= 4 for p in h["paths"])


def test_drive_and_sensors(sim):
    t = sim.trucks[0]
    x0, z0 = t.x, t.z
    for k in range(60):
        sim.command("truck-0", 1.0, 0.0, k / 30)
        sim.step(1 / 30, k / 30)
    assert np.hypot(t.x - x0, t.z - z0) > 5
    assert abs(t.y - sim.height(t.x, t.z)) < 1.5
    assert len(sim.actors()) == 12
    assert all(abs(w.y - sim.height(w.x, w.z)) < 1.0 for w in sim.walkers)
    sim.sync_obstacles()
    depths, lidars = sim.depths(), sim.lidars()
    assert len(depths) == len(lidars) == 3
    if sim.caster is not None:
        assert depths[0].shape == (36, 64) and np.isfinite(depths[0]).all() and depths[0].min() > 0
        assert depths[0][-1].mean() < depths[0][0].mean()
        assert len(lidars[0]) > 1000 and lidars[0][:, 1].min() < -2


def test_excavator_state(sim):
    for rig in sim.rigs.values():
        rig.digging = True
    for k in range(30):
        sim.step(1 / 30, k / 30)
    state = sim.excavator_state()
    assert len(state) == 3 and all(s["digging"] and s["bucket"] for s in state)
    b = sim.excavators[0]
    assert np.hypot(state[0]["bucket"]["position"][0] - b.x, state[0]["bucket"]["position"][2] - b.z) < 12
