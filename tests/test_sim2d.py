import numpy as np
import pytest

from sim2d.env import CAND_DIM, N_CAND, OBS_DIM, DriveEnv
from sim2d.world import load


@pytest.fixture(scope="module")
def world():
    return load("default")


def test_world_loads(world):
    assert world.n == 130
    assert "pit_floor" in world.sites
    assert world.blocked.any()


def test_observation_shapes(world):
    env = DriveEnv(world, seed=0)
    ob = env.reset("parking", "pit_floor")
    assert ob["obs"].shape == (OBS_DIM,)
    assert ob["cands"].shape == (N_CAND, CAND_DIM)
    assert ob["mask"].shape == (N_CAND,) and ob["mask"].any()
    assert np.isfinite(ob["obs"]).all()


def test_quarry_has_pedestrians_that_do_not_yield():
    env = DriveEnv(load("quarry"), n_movers=(0, 0), n_peds=(4, 4), seed=3)
    env.reset("parking", "ore_1")
    peds = [m for m in env.movers if m.pedestrian]
    assert peds and all(m.radius == 0.3 for m in peds)
    before = [(m.x, m.z) for m in peds]
    for _ in range(10):
        env.step(env.expert_action())
    assert any((m.x, m.z) != b for m, b in zip(peds, before))


@pytest.mark.parametrize("start,goal", [("parking", "pit_floor"), ("crusher", "pit_south")])
def test_expert_reaches_goal_without_collision(world, start, goal):
    env = DriveEnv(world, n_movers=(3, 3), seed=1)
    env.reset(start, goal)
    done, info = False, {}
    while not done:
        _, _, done, info = env.step(env.expert_action())
    assert info["arrived"] and not info["collided"]
