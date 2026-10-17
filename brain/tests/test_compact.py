import json

from brain.compact import StateDelta, pack_state


def test_pack_state_rounds_and_thins():
    path = [[i * 0.123456, i * 0.987654] for i in range(16)]
    state = {"t": 1.23456789, "trucks": [{"id": "truck-0", "x": 1.23456, "candidates": [path], "candidate_scores": [0.123456]}]}
    out = json.loads(pack_state(state))
    t = out["trucks"][0]
    assert t["x"] == 1.23 and out["t"] == 1.23
    assert len(t["candidates"][0]) == 5 and t["candidates"][0][-1] == [1.9, 14.8]
    assert t["candidate_scores"] == [0.12]


def test_top_candidates_keep_scored_and_chosen():
    cands = [[[float(i), 0.0], [float(i), 1.0]] for i in range(20)]
    scores = [None if i % 5 == 0 else i / 20 for i in range(20)]
    state = {"trucks": [{"id": "a", "candidates": cands, "candidate_scores": scores, "choice": 1, "ghost_paths": [[[0, 0]]]}]}
    t = json.loads(pack_state(state))["trucks"][0]
    assert len(t["candidates"]) == 8 and None not in t["candidate_scores"]
    assert t["candidates"][t["choice"]][0][0] == 1.0 and t["ghost_paths"] == []


def test_delta_sends_static_and_route_once():
    delta = StateDelta()
    state = {"obstacles": [{"x": 1.0}], "trucks": [{"id": "a", "goal": [1, 2], "route": [[0, 0], [1, 1]], "route_y": [0, 0]}]}
    first = json.loads(delta.pack(state))
    second = json.loads(delta.pack(state))
    assert "obstacles" in first and "route" in first["trucks"][0]
    assert "obstacles" not in second and "route" not in second["trucks"][0] and "route_y" not in second["trucks"][0]
    state["obstacles"] = []
    assert json.loads(delta.pack(state))["obstacles"] == []


def test_pack_state_drops_non_finite():
    assert json.loads(pack_state({"trucks": [], "v": float("nan")}))["v"] is None
