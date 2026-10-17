import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

from brain.planning import driver, navmap  # noqa: E402
from brain.planning.scorer import CheckpointError, NumpyScorer, load_scorer  # noqa: E402
from sim2d import env as sim  # noqa: E402
from sim2d.world import WORLDS_DIR, load  # noqa: E402

CONSTANTS = [
    "MAX_V", "MAX_STEER", "WHEELBASE", "ACCEL", "DT", "HORIZON", "RDT", "N_ROLL", "N_CAND", "HOLD_ACTION",
    "N_RAYS", "LIDAR_FOV", "LIDAR_RANGE", "ROUTE_POINTS", "ROUTE_SPACING", "ROUTE_STEP", "OBS_DIM", "CAND_DIM",
    "TRUCK_HALF_W", "EDGE_MARGIN", "HIT_MARGIN", "ARRIVE_RADIUS",
]


@pytest.mark.parametrize("name", CONSTANTS)
def test_brain_constants_match_sim2d(name):
    assert getattr(driver, name) == getattr(sim, name)


def test_candidate_grid_matches_sim2d():
    assert np.array_equal(driver.CAND_STEER, sim.CAND_STEER)
    assert np.array_equal(driver.CAND_SPEED, sim.CAND_SPEED)


@pytest.fixture(scope="module")
def quarry():
    return load("quarry"), navmap.load(Path(WORLDS_DIR) / "quarry")


def test_grids_match(quarry):
    world, nav = quarry
    assert (world.ox, world.oz, world.cell) == (nav.ox, nav.oz, nav.cell)
    assert np.array_equal(world.blocked, nav.blocked)


@pytest.mark.parametrize("start,goal,ticks", [("parking", "ore_1", 0), ("parking", "ore_1", 60), ("crusher", "dump", 120)])
def test_brain_features_equal_sim2d_observe(quarry, start, goal, ticks):
    world, nav = quarry
    env = sim.DriveEnv(world, n_movers=(0, 0), n_peds=(0, 0), seed=0)
    env.reset(start, goal, load=0.4)
    for _ in range(ticks):
        env.step(env.expert_action())
    ob = env.observe()
    tr = env.truck
    seen = {}

    def capture(obs, cands, mask):
        seen.update(obs=obs, cands=cands, mask=mask)
        return np.zeros(len(cands))

    driver.drive(
        nav, env.route, env.route_idx, tr.x, tr.z, math.degrees(tr.yaw), tr.v, tr.steer, None, capture, load=tr.load
    )
    np.testing.assert_allclose(seen["obs"], ob["obs"], atol=1e-5)
    np.testing.assert_allclose(seen["cands"], ob["cands"], atol=1e-5)
    assert np.array_equal(seen["mask"], ob["mask"])


def test_numpy_scorer_equals_torch(tmp_path):
    torch = pytest.importorskip("torch")
    from brain.planning.export_scorer import export
    from sim2d.model import PathScorer, save

    torch.manual_seed(0)
    model = PathScorer(width=64).eval()
    save(model, tmp_path / "m.pt")
    export(tmp_path / "m.pt", tmp_path / "m.npz")
    scorer = load_scorer(tmp_path / "m.npz")
    assert scorer.config["width"] == 64
    rng = np.random.default_rng(0)
    obs = rng.normal(size=(16, sim.OBS_DIM)).astype(np.float32)
    cands = rng.normal(size=(16, sim.N_CAND, sim.CAND_DIM)).astype(np.float32)
    mask = np.ones((16, sim.N_CAND), bool)
    with torch.no_grad():
        ref = model(torch.from_numpy(obs), torch.from_numpy(cands), torch.from_numpy(mask)).numpy()
    np.testing.assert_allclose(scorer.logits(obs, cands), ref, rtol=0, atol=1e-5)
    np.testing.assert_allclose(scorer(obs[0], cands[0], mask[0]), ref[0], rtol=0, atol=1e-5)


def test_export_rejects_wrong_feature_size(tmp_path):
    torch = pytest.importorskip("torch")
    from brain.planning.export_scorer import export

    w = {k: v.numpy() for k, v in __import__("sim2d.model").model.PathScorer(width=32).state_dict().items()}
    w["scene.0.weight"] = np.zeros((32, sim.OBS_DIM + 1), np.float32)
    with pytest.raises(CheckpointError, match="OBS_DIM"):
        NumpyScorer(w)
    torch.save({"config": {"width": 32}, "state": {k: torch.from_numpy(v) for k, v in w.items()}}, tmp_path / "bad.pt")
    with pytest.raises(CheckpointError):
        export(tmp_path / "bad.pt", tmp_path / "bad.npz")
