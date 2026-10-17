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
        origin, rot = sim.lidar_pose(0)
        near = lidars[0][np.hypot(lidars[0][:, 0], lidars[0][:, 2]) < 20] @ rot.T + origin
        assert np.median(np.abs(near[:, 1] - [sim.height(x, z) for x, _, z in near])) < 0.3


def test_excavator_state(sim):
    for rig in sim.rigs.values():
        rig.digging = True
    for k in range(30):
        sim.step(1 / 30, k / 30)
    state = sim.excavator_state()
    assert len(state) == 3 and all(s["digging"] and s["bucket"] for s in state)
    b = sim.excavators[0]
    assert np.hypot(state[0]["bucket"]["position"][0] - b.x, state[0]["bucket"]["position"][2] - b.z) < 12


def test_reset_puts_trucks_at_spawn_and_keeps_world_running(sim):
    spawn = [(t.spawn[0], t.spawn[2], t.spawn[3]) for t in sim.trucks]
    sim.reset_trucks()
    for k in range(60):
        sim.command("truck-0", 1.0, 0.3, k / 30)
        sim.step(1 / 30, k / 30)
    moved = sim.trucks[0]
    assert np.hypot(moved.x - spawn[0][0], moved.z - spawn[0][1]) > 1 and moved.speed > 0
    walkers = [(w.x, w.z) for w in sim.walkers]
    sim.request_reset()
    assert sim.reset_requested
    sim.step(1 / 30, 3.0)
    assert not sim.reset_requested
    for t, (x, z, yaw) in zip(sim.trucks, spawn):
        assert t.speed == 0.0 and t.throttle == 0.0 and t.steer == 0.0
        assert np.hypot(t.x - x, t.z - z) < 0.05 and abs(t.yaw - yaw) < 0.5
        assert abs(t.y - sim.height(t.x, t.z)) < 1.5
    assert [(w.x, w.z) for w in sim.walkers] != walkers
