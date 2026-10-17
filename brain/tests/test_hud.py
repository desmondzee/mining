import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from brain.app import create_app
from brain.fakesim import CAM_H, CAM_HEIGHT, CAM_W, FOV_DEG, FakeQuarry, camera_pose
from brain.hud import Heightmap, augment, frame_meta, load_heightmap, pack_meta, unpack_meta


def write_world(tmp_path, res=5, size=40.0, origin=-20.0, lo=-10.0, rng=40.0):
    z, x = np.mgrid[0:res, 0:res]
    heights = x * 1.0 + z * 2.0
    raw = np.round((heights - lo) / rng * 65535).astype("<u2")
    raw.tofile(tmp_path / "heightmap.raw")
    meta = {"file": "heightmap.raw", "resolution": res, "size_m": [size, size], "origin": [origin, origin],
            "min_height_m": lo, "height_range_m": rng}
    (tmp_path / "heightmap_meta.json").write_text(json.dumps(meta))
    return tmp_path


def test_heightmap_row0_is_south_col0_is_west_and_bilinear(tmp_path):
    hm = load_heightmap(write_world(tmp_path))
    assert hm.sample(-20, -20) == pytest.approx(0, abs=1e-3)
    assert hm.sample(20, -20) == pytest.approx(4, abs=1e-3)
    assert hm.sample(-20, 20) == pytest.approx(8, abs=1e-3)
    assert hm.sample(-15, -15) == pytest.approx(1.5, abs=1e-3)
    assert hm.sample(999, 999) == pytest.approx(12, abs=1e-3)


def test_missing_heightmap_is_none(tmp_path):
    assert load_heightmap(tmp_path) is None
    assert load_heightmap(None) is None


def test_real_world_heightmap_loads():
    hm = load_heightmap(__import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry")
    if hm is None:
        pytest.skip("quarry world not present")
    y = hm.sample([-258.83], [47.91])
    assert -141 < float(y[0]) < 23


def test_augment_adds_ground_heights_and_route_y():
    hm = Heightmap(np.array([[0.0, 10.0], [0.0, 10.0]], np.float32), (0.0, 0.0), (10.0, 10.0))
    snap = {"trucks": [{"id": "t", "x": 5.0, "z": 5.0, "route": [[0, 0], [10, 0], [5, 5]]}],
            "pedestrians": [{"id": "p", "x": 10.0, "z": 0.0}], "obstacles": [{"x": 0.0, "z": 0.0, "r": 2}]}
    out = augment(snap, hm, t=12.5)
    assert out["t"] == 12.5
    assert out["trucks"][0]["y"] == 5.0 and out["trucks"][0]["route_y"] == [0.0, 10.0, 5.0]
    assert out["pedestrians"][0]["y"] == 10.0 and out["obstacles"][0]["y"] == 0.0
    assert augment({"trucks": []}, None, t=1)["t"] == 1


def test_frame_meta_roundtrip():
    header = {"camera": "truck-0", "truck": "truck-0", "pose": {"x": 1, "z": 2, "yaw": 3}, "speed": 4.5,
              "cam": {"x": 1, "y": 3.6, "z": 2, "yaw": 3, "pitch": 0, "roll": 0, "hfov": 90}, "rgb": {}}
    meta, end = unpack_meta(pack_meta(frame_meta(header, t=7.0)) + b"rest")
    assert meta == {"t": 7.0, **{k: header[k] for k in ("cam", "pose", "speed", "truck")}}
    assert end == len(pack_meta(frame_meta(header, t=7.0)))
    overview, _ = unpack_meta(pack_meta(frame_meta({"camera": "overview", "truck": ""}, t=1.0)))
    assert overview == {"t": 1.0}


def test_fakesim_camera_pose_matches_renderer():
    quarry = FakeQuarry(1)
    quarry.rocks, quarry.pedestrians = quarry.rocks[:0], []
    t = quarry.trucks[0]
    cam = camera_pose(t)
    assert cam == {"x": t.x, "y": CAM_HEIGHT, "z": t.z, "yaw": t.yaw, "pitch": 0.0, "roll": 0.0, "hfov": FOV_DEG}
    _, depth = quarry.render_truck(t)
    focal = (CAM_W / 2) / np.tan(np.radians(FOV_DEG / 2))
    for d in (10.0, 25.0, 60.0):
        row = int(CAM_H / 2 + focal * CAM_HEIGHT / d)
        assert depth[row, CAM_W // 2] == pytest.approx(d, rel=0.08)


def test_fakesim_columns_are_pinhole():
    quarry = FakeQuarry(1)
    quarry.pedestrians = []
    t = quarry.trucks[0]
    quarry.rocks = np.array([[t.x + 10.0, t.z + 30.0, 0.5, 2.0, 0]])
    _, depth = quarry.render_truck(t)
    cols = np.flatnonzero(depth[int(CAM_H / 2) + 25] < 40)
    focal = (CAM_W / 2) / np.tan(np.radians(FOV_DEG / 2))
    assert cols.mean() + 0.5 == pytest.approx(CAM_W / 2 + focal * 10 / 30, abs=1.0)


def test_state_has_time_and_heights(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLD_DIR", str(write_world(tmp_path)))
    app = create_app(sim_port=None, web_dir=None)
    truck = app.state.world.truck("truck-0")
    truck.x, truck.z = -20.0, 20.0
    state = TestClient(app).get("/api/state").json()
    assert state["t"] > 0 and state["trucks"][0]["y"] == pytest.approx(8, abs=0.01)
