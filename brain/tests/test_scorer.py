import json
import math
from pathlib import Path

import numpy as np
import pytest

from brain import planning
from brain.planning.driver import CAND_DIM, N_CAND, OBS_DIM, drive
from brain.planning.navmap import load
from brain.planning.learned import LAYERS, CheckpointError, NumpyScorer, OnnxScorer, load_scorer
from brain.policy import act
from brain.world import Truck

WORLDS = Path(__file__).resolve().parents[2] / "data" / "worlds"


def random_weights(width: int = 16, obs_dim: int = OBS_DIM, seed: int = 0, std: float | None = 0.3) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    shapes = {
        "scene.0": (width, obs_dim),
        "scene.2": (width, width),
        "path.0": (width // 2, CAND_DIM),
        "path.2": (width // 2, width // 2),
        "head.0": (width, width + width // 2),
        "head.2": (1, width),
    }
    w = {}
    for k in LAYERS:
        w[f"{k}.weight"] = rng.normal(0, std or shapes[k][1] ** -0.5, shapes[k]).astype(np.float32)
        w[f"{k}.bias"] = rng.normal(0, 0.1, shapes[k][0]).astype(np.float32)
    return w


def reference(w, obs, cands):
    relu = lambda a: np.maximum(a, 0)  # noqa: E731
    lin = lambda k, x: x.astype(np.float64) @ w[f"{k}.weight"].T.astype(np.float64) + w[f"{k}.bias"]  # noqa: E731
    s = relu(lin("scene.2", relu(lin("scene.0", obs))))
    p = relu(lin("path.2", relu(lin("path.0", cands))))
    h = relu(lin("head.0", np.concatenate([np.repeat(s[None], len(cands), 0), p], axis=1)))
    return lin("head.2", h)[:, 0]


@pytest.fixture
def npz(tmp_path):
    w = random_weights()
    path = tmp_path / "s.npz"
    np.savez(path, config=np.array(json.dumps({"width": 16})), **w)
    return path, w


def test_npz_round_trip_matches_reference(npz):
    path, w = npz
    scorer = load_scorer(path)
    assert scorer.config == {"width": 16}
    rng = np.random.default_rng(1)
    obs, cands = rng.normal(size=OBS_DIM), rng.normal(size=(N_CAND, CAND_DIM))
    np.testing.assert_allclose(scorer(obs, cands, np.ones(N_CAND, bool)), reference(w, obs, cands), atol=1e-4)


def test_rejects_wrong_feature_size():
    with pytest.raises(CheckpointError, match="OBS_DIM=80"):
        NumpyScorer(random_weights(obs_dim=OBS_DIM + 1))
    w = random_weights()
    del w["head.2.bias"]
    with pytest.raises(CheckpointError, match="head.2.bias"):
        NumpyScorer(w)


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_onnx_scorer_matches_numpy(device):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    base = NumpyScorer(random_weights(width=512, std=None))
    onnx_scorer = OnnxScorer(base, device)
    if device == "cuda" and onnx_scorer.device != "cuda":
        pytest.skip("CUDA unavailable")
    rng = np.random.default_rng(2)
    obs, cands = rng.normal(size=(3, OBS_DIM)), rng.normal(size=(3, N_CAND, CAND_DIM))
    np.testing.assert_allclose(onnx_scorer.logits(obs, cands), base.logits(obs, cands), atol=1e-4, rtol=1e-5)
    np.testing.assert_allclose(onnx_scorer(obs[0], cands[0], None), base(obs[0], cands[0], None), atol=1e-4, rtol=1e-5)


def test_cpu_device_keeps_numpy(npz):
    assert isinstance(load_scorer(npz[0], device="cpu"), NumpyScorer)


def test_env_selects_scorer_and_falls_back(npz, monkeypatch, tmp_path):
    planning.scorer.cache_clear()
    monkeypatch.setenv("PLANNER_DEVICE", "cpu")
    monkeypatch.delenv("PLANNER_CHECKPOINT", raising=False)
    monkeypatch.setenv("PLANNER_CKPT", str(tmp_path / "missing.npz"))
    assert planning.scorer() is None
    planning.scorer.cache_clear()
    monkeypatch.setenv("PLANNER_CKPT", str(npz[0].with_suffix(".pt")))
    assert isinstance(planning.scorer(), NumpyScorer)
    planning.scorer.cache_clear()


@pytest.fixture
def quarry():
    if not (WORLDS / "quarry" / "navgrid.json").is_file():
        pytest.skip("no quarry world")
    return load(WORLDS / "quarry")


def test_learned_choice_is_best_safe_candidate(quarry):
    route = planning.plan_route(quarry, quarry.sites["parking"], quarry.sites["ore_1"])
    head = route[4] - route[0]
    yaw = math.degrees(math.atan2(head[0], head[1]))
    prefer = np.arange(N_CAND, dtype=float)
    d, _ = drive(quarry, route, 0, *route[0], yaw, 0.0, 0.0, None, lambda o, c, m: prefer)
    assert d.planner == "learned" and d.safe.any()
    assert d.choice == np.flatnonzero(d.safe).max() and d.scores.shape == (N_CAND,)


def test_state_exposes_planner_and_candidates(quarry, npz, monkeypatch):
    monkeypatch.setattr(planning, "nav_map", lambda: quarry)
    monkeypatch.setattr(planning, "scorer", lambda: load_scorer(npz[0]))
    planning.reset()
    goal = quarry.sites["ore_1"]
    route = planning.plan_route(quarry, quarry.sites["parking"], goal)
    head = route[4] - route[0]
    truck = Truck("t", x=route[0][0], z=route[0][1], yaw=math.degrees(math.atan2(head[0], head[1])), goal=goal)
    truck.path = route.tolist()
    a = act(truck, None, None, None, now=0.0)
    d = truck.to_dict()
    assert d["planner"] == "learned" and len(d["candidates"]) == N_CAND == len(d["candidate_scores"])
    assert 0 <= d["choice"] < N_CAND and (a.status == "holding" or d["candidate_scores"][d["choice"]] is not None)
    assert d["candidate_scores"][d["choice"]] == max(s for s in d["candidate_scores"] if s is not None)
    truck.set_goal(None)
    assert truck.to_dict()["candidates"] == [] and truck.choice == -1


def test_idle_truck_reports_rules_without_checkpoint(monkeypatch):
    monkeypatch.setattr(planning, "scorer", lambda: None)
    truck = Truck("t")
    act(truck, None, None)
    assert truck.planner == "rules"
