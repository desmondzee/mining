import json

from brain.compact import pack_state


def test_pack_state_rounds_and_thins():
    path = [[i * 0.123456, i * 0.987654] for i in range(16)]
    state = {"t": 1.23456789, "trucks": [{"id": "truck-0", "x": 1.23456, "candidates": [path], "candidate_scores": [0.123456]}]}
    out = json.loads(pack_state(state))
    t = out["trucks"][0]
    assert t["x"] == 1.23 and out["t"] == 1.23
    assert len(t["candidates"][0]) == 6 and t["candidates"][0][-1] == [1.9, 14.8]
    assert t["candidate_scores"] == [0.12]


def test_pack_state_drops_non_finite():
    assert json.loads(pack_state({"trucks": [], "v": float("nan")}))["v"] is None
