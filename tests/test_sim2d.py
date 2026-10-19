import math

import numpy as np
import pytest

from sim2d.env import CAND_DIM, N_CAND, OBS_DIM, ROUTE_STEP, TRUCK_HALF_W, TRUCK_R, DriveEnv, Mover
from sim2d.world import load, resample


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


def _ramp_with_obstacle(world, start, goal, road_id, ahead=40.0, offset=0.0):
    """Truck at 8 m/s mid-ramp, a stopped vehicle on the route `ahead` m further, `offset` m to the right."""
    env = DriveEnv(world, n_movers=(0, 0), n_peds=(0, 0), seed=0)
    env.reset(start, goal)
    ramp = resample(world.roads[world.road_ids.index(road_id)], 1.0)
    i = int(np.argmin(np.linalg.norm(env.route - ramp[len(ramp) // 2], axis=1)))
    head = env.route[i + 3] - env.route[i]
    tr = env.truck
    tr.x, tr.z = env.route[i]
    tr.yaw, tr.v = math.atan2(head[0], head[1]), 8.0
    env.route_idx = i
    j = i + int(ahead / ROUTE_STEP)
    d = env.route[j + 1] - env.route[j]
    d /= np.linalg.norm(d)
    ox, oz = env.route[j] + np.array([d[1], -d[0]]) * offset
    env.movers = [Mover(pts=ramp, s=0.0, direction=1, speed=0.0, static=True, x=float(ox), z=float(oz))]
    env._plan()
    return env


def _body_over_edge(env):
    tr = env.truck
    rx, rz = math.cos(tr.yaw) * TRUCK_HALF_W, -math.sin(tr.yaw) * TRUCK_HALF_W
    return bool(env.world.blocked_at(np.array([tr.x, tr.x + rx, tr.x - rx]), np.array([tr.z, tr.z + rz, tr.z - rz])).any())


RAMP_CASES = [(s, g, r) for s, g in [("crusher", "pit_floor"), ("pit_floor", "crusher")] for r in ("ramp_main_1", "ramp_main_2", "ramp_main_3")]


@pytest.mark.parametrize("start,goal,road_id", RAMP_CASES)
def test_expert_stops_behind_obstacle_on_narrow_ramp(world, start, goal, road_id):
    # The truck must stop before the vehicle. After REPLAN_AFTER it may go round, but only with
    # its whole body on the road.
    env = _ramp_with_obstacle(world, start, goal, road_id)
    m, tr = env.movers[0], env.truck
    stopped = False
    for _ in range(300):
        _, _, done, info = env.step(env.expert_action())
        assert not info["collided"] and not _body_over_edge(env)
        stopped |= env.replans == 0 and tr.v < 0.1 and math.hypot(m.x - tr.x, m.z - tr.z) > TRUCK_R + m.radius
        if done:
            break
    assert stopped


@pytest.mark.parametrize("offset", [-5.0, 5.0])
@pytest.mark.parametrize("start,goal,road_id", RAMP_CASES)
def test_expert_keeps_body_on_ramp_next_to_obstacle(world, start, goal, road_id, offset):
    # The obstacle leaves a gap on one side. The truck may pass only if its whole body fits.
    env = _ramp_with_obstacle(world, start, goal, road_id, offset=offset)
    for _ in range(300):
        _, _, done, info = env.step(env.expert_action())
        assert not info["collided"] and not _body_over_edge(env)
        if done:
            break


def test_training_seeds_must_not_overlap_test_seeds():
    from sim2d.env import check_train_seeds

    check_train_seeds(range(10000, 12000))
    for seeds in (range(0, 2000), range(1000, 1030), [950000]):
        with pytest.raises(ValueError):
            check_train_seeds(seeds)


def test_tracked_mode_estimates_cluster_velocity():
    from sim2d.env import DriveEnv
    from sim2d.world import load

    env = DriveEnv(load("default"), n_movers=(0, 0), n_peds=(0, 0), seed=0, movers="tracked")
    env.reset()
    blob = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    still = np.array([[40.0, 40.0]])
    for k in range(30):
        v = env._track(np.vstack([blob + [0.3 * k, 0.0], still]))
    np.testing.assert_allclose(v[:3], [[3.0, 0.0]] * 3, atol=0.1)
    np.testing.assert_allclose(v[3], [0.0, 0.0])


def test_stack_index_stays_inside_each_trip():
    from sim2d.model import STACK_GAP, stack_index

    trips = np.array([0] * 5 + [1] * 8)
    idx = stack_index(trips, 3)
    assert idx[0].tolist() == [0, 0, 0]
    assert idx[4].tolist() == [4, 4 - STACK_GAP, 0]
    assert idx[5].tolist() == [5, 5, 5]
    assert idx[12].tolist() == [12, 12 - STACK_GAP, 12 - 2 * STACK_GAP]
