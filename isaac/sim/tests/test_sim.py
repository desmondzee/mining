import math

import numpy as np
import pytest

from isaac.sim import excavators as E
from isaac.sim import world as W
from isaac.sim.sim import Sim

EXTRA = [{"id": "exc-x", "pos3": [0.0, 0.0, 0.0], "heading_deg": 90.0}]
VIEWS = [{"name": "drone", "mode": "chase", "truck": "truck-0", "width": 64, "height": 36}]


@pytest.fixture(scope="module", params=[False, True], ids=["heightmap", "warp"])
def sim(request):
    if request.param:
        pytest.importorskip("warp")
    return Sim(W.REPO / "data/worlds/quarry", raycast=request.param, views=VIEWS, extra_excavators=EXTRA)


def test_hello(sim):
    h = sim.hello()
    assert h["cameras"] == ["overview", "truck-0", "truck-1", "truck-2", "drone"]
    assert len(h["obstacles"]) == 4 and len(h["paths"]) == 4


def test_drive_and_sensors(sim):
    t = sim.trucks[0]
    x0, z0 = t.x, t.z
    for k in range(60):
        sim.command("truck-0", 1.0, 0.0, k / 30)
        sim.step(1 / 30, k / 30)
    assert np.hypot(t.x - x0, t.z - z0) > 5
    assert abs(t.y - sim.height(t.x, t.z)) < 1.5
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
    assert len(state) == 4 and all(s["digging"] and s["bucket"] for s in state)
    b = sim.excavators[0]
    assert np.hypot(state[0]["bucket"]["position"][0] - b.x, state[0]["bucket"]["position"][2] - b.z) < 12


def test_extra_excavator_digs_and_loading_aim(sim):
    assert Sim(W.REPO / "data/worlds/quarry", raycast=False, extra_excavators=EXTRA).rigs["exc-x"].digging
    b = sim.excavators[0]
    t = sim.trucks[1]
    t.x, t.z, t.speed = b.x + 1.0, b.z + 1.0, 0.0
    assert sim.loading()["truck-1"] == b.id
    yaw = math.radians(sim.excavator_yaw(b) - 90)
    sim.aim({b.id: (b.x + 10 * math.sin(yaw), b.z + 10 * math.cos(yaw))})
    assert sim.rigs[b.id].dump_yaw == pytest.approx(-90.0)
    sim.aim({b.id: (b.x - 10 * math.sin(yaw), b.z - 10 * math.cos(yaw))})
    assert sim.rigs[b.id].dump_yaw == pytest.approx(90.0)
    sim.aim({})
    assert sim.rigs[b.id].dump_yaw == E.TRUCK_SIDE_YAW
