import asyncio
import json
import math
import time

import numpy as np
import pytest

from brain.app import create_app
from brain.detector import (
    Detector,
    DetectorWorker,
    Letterbox,
    decode,
    from_env,
    ground_distance,
    letterbox,
    nms,
    providers_for,
    to_meta,
    unletterbox,
)
from brain.fakesim import FakeQuarry
from brain.hud import frame_meta, unpack_meta
from brain.protocol import encode, encode_frame


def test_letterbox_pads_without_resizing_640x360():
    rgb = np.full((360, 640, 3), 200, np.uint8)
    blob, lb = letterbox(rgb, (384, 640))
    assert blob.shape == (1, 3, 384, 640) and blob.dtype == np.float32
    assert (lb.scale, lb.pad_x, lb.pad_y) == (1.0, 0, 12)
    assert blob[0, 0, 0, 0] == pytest.approx(114 / 255) and blob[0, 0, 12, 0] == pytest.approx(200 / 255)
    assert blob[0, 0, 371, 0] == pytest.approx(200 / 255) and blob[0, 0, 372, 0] == pytest.approx(114 / 255)


def test_letterbox_scales_and_unletterbox_inverts():
    rgb = np.zeros((720, 1280, 3), np.uint8)
    blob, lb = letterbox(rgb, (640, 640))
    assert blob.shape == (1, 3, 640, 640) and lb.scale == 0.5 and lb.pad_y == 140
    frame_box = np.array([[100.0, 200.0, 300.0, 500.0]])
    lb_box = frame_box * lb.scale + [lb.pad_x, lb.pad_y, lb.pad_x, lb.pad_y]
    assert unletterbox(lb_box, lb, 1280, 720) == pytest.approx(frame_box)


def test_unletterbox_clips_to_frame():
    out = unletterbox(np.array([[-10.0, 0.0, 700.0, 400.0]]), Letterbox(1.0, 0, 12), 640, 360)
    assert out.tolist() == [[0.0, 0.0, 640.0, 360.0]]


def test_nms_suppresses_overlaps_and_keeps_order():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [20, 20, 30, 30], [0, 0, 10, 9]], np.float32)
    scores = np.array([0.6, 0.9, 0.5, 0.3], np.float32)
    assert nms(boxes, scores, 0.5).tolist() == [1, 2]
    assert nms(boxes, scores, 0.99).tolist() == [1, 0, 2, 3]
    assert nms(boxes, scores, 0.5, limit=1).tolist() == [1]


def yolo_output(dets, nc, n=20):
    out = np.zeros((1, 4 + nc, n), np.float32)
    for i, (cx, cy, w, h, cls, score) in enumerate(dets):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + cls, i] = score
    return out


def test_decode_yolov8_layout_is_class_aware():
    out = yolo_output([(50, 50, 20, 40, 0, 0.9), (51, 50, 20, 40, 0, 0.8), (51, 50, 20, 40, 1, 0.7), (200, 100, 10, 10, 1, 0.2)], nc=2)
    det = decode(out, 2, 0.35, 0.5)
    assert det.shape == (2, 6)
    assert det[0].tolist() == pytest.approx([40, 30, 60, 70, 0.9, 0])
    assert det[1, 5] == 1 and det[1, 4] == pytest.approx(0.7)
    assert decode(out.transpose(0, 2, 1), 2, 0.35, 0.5).shape == (2, 6)


def test_decode_end2end_and_empty():
    e2e = np.array([[[1, 2, 3, 4, 0.9, 5], [0, 0, 1, 1, 0.1, 2]]], np.float32)
    assert decode(e2e, 80, 0.35, 0.5)[0].tolist() == pytest.approx([1, 2, 3, 4, 0.9, 5]) and len(decode(e2e, 80, 0.35, 0.5)) == 1
    assert decode(np.zeros((1, 84, 100), np.float32), 80, 0.35, 0.5).shape == (0, 6)


def test_ground_distance_bottom_centre():
    depth = np.full((36, 64), 80.0, np.float32)
    depth[20:30, 30:34] = 12.0
    box = [300.0, 150.0, 340.0, 300.0]
    assert ground_distance(box, depth, 640, 360, 90) == pytest.approx(12.0, rel=0.01)
    off = [600.0, 150.0, 640.0, 300.0]
    depth[:, 60:] = 10.0
    f = 320 / math.tan(math.radians(45))
    assert ground_distance(off, depth, 640, 360, 90) == pytest.approx(10 * math.hypot(1, 300 / f), rel=0.01)
    assert ground_distance(box, None, 640, 360, 90) is None
    assert ground_distance(box, np.full((36, 64), np.inf, np.float32), 640, 360, 90) is None


def test_to_meta_normalises_and_adds_distance():
    depth = np.full((36, 64), 25.0, np.float32)
    det = to_meta([{"box": [320.0, 90.0, 480.0, 270.0], "cls": "person", "conf": 0.876}], depth, 640, 360, 90, 5.0)
    assert det == {"t": 5.0, "items": [{"box": [0.5, 0.25, 0.75, 0.75], "cls": "person", "conf": 0.88, "dist": pytest.approx(25 * math.hypot(1, 80 / 320), abs=0.05)}]}
    assert "dist" not in to_meta([{"box": [0, 0, 10, 10], "cls": "x", "conf": 0.5}], None, 640, 360, 90, 1.0)["items"][0]


def test_worker_latest_frame_wins_and_expires():
    calls = []

    def detect(rgb):
        calls.append(int(rgb[0, 0, 0]))
        time.sleep(0.05)
        return [{"box": [0.0, 0.0, 64.0, 36.0], "cls": "truck", "conf": 0.9}]

    w = DetectorWorker(detect, workers=1, max_age=10)
    try:
        now = time.time()
        for i in range(5):
            w.submit("cam", np.full((36, 64, 3), i, np.uint8), None, now + i * 0.001)
        deadline = time.time() + 2
        while w.latest("cam", now) is None or w.latest("cam", now)["t"] < round(now + 0.004, 3):
            assert time.time() < deadline
            time.sleep(0.01)
        assert calls[-1] == 4 and len(calls) < 5 and w.dropped >= 1
        assert w.latest("cam", now)["items"][0]["box"] == [0.0, 0.0, 1.0, 1.0]
        assert w.latest("cam", now + 100) is None and w.latest("other") is None
    finally:
        w.close()


def test_frame_meta_carries_det():
    det = {"t": 1.5, "items": [{"box": [0.1, 0.2, 0.3, 0.4], "cls": "person", "conf": 0.9}]}
    meta, _ = unpack_meta(len(frame_meta({"truck": "t"}, 2.0, det)).to_bytes(2, "little") + frame_meta({"truck": "t"}, 2.0, det))
    assert meta == {"t": 2.0, "truck": "t", "det": det}
    assert "det" not in json.loads(frame_meta({"truck": "t"}, 2.0))


def constant_model(path, out: np.ndarray, names: dict, h=384, w=640):
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    nodes = [
        helper.make_node("ReduceSum", ["images"], ["s"], keepdims=0),
        helper.make_node("Mul", ["s", "zero"], ["z"]),
        helper.make_node("Add", ["const", "z"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes, "g",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, h, w])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(out.shape))],
        [numpy_helper.from_array(out, "const"), numpy_helper.from_array(np.zeros((), np.float32), "zero")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    helper.set_model_props(model, {"names": repr(names)})
    onnx.save(model, path)
    return str(path)


def test_detector_maps_model_boxes_to_frame_pixels(tmp_path):
    pytest.importorskip("onnxruntime")
    out = yolo_output([(320, 112, 40, 120, 0, 0.92), (100, 200, 60, 40, 7, 0.6), (500, 300, 10, 10, 2, 0.1)], nc=80)
    names = {i: f"c{i}" for i in range(80)} | {0: "person", 7: "truck"}
    det = Detector(constant_model(tmp_path / "m.onnx", out, names))
    assert det.size == (384, 640) and det.names[0] == "person" and len(det.names) == 80
    assert det.device in ("cpu", "cuda")
    items = det(np.zeros((360, 640, 3), np.uint8))
    assert [d["cls"] for d in items] == ["person", "truck"]
    assert items[0]["box"] == [300.0, 40.0, 340.0, 160.0] and items[0]["conf"] == pytest.approx(0.92, abs=1e-3)
    assert items[1]["box"] == [70.0, 168.0, 130.0, 208.0]
    det.classes = {"truck"}
    assert [d["cls"] for d in det(np.zeros((360, 640, 3), np.uint8))] == ["truck"]


def test_providers_for_device():
    gpu = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert providers_for("auto", gpu) == gpu and providers_for("cuda", gpu) == gpu
    assert providers_for("cpu", gpu) == ["CPUExecutionProvider"]
    assert providers_for("auto", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert providers_for("cuda", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_from_env_is_noop_without_model(monkeypatch, tmp_path):
    monkeypatch.delenv("DETECTOR_MODEL", raising=False)
    assert from_env() is None
    monkeypatch.setenv("DETECTOR_MODEL", str(tmp_path / "missing.onnx"))
    assert from_env() is None


def test_bridge_attaches_latest_detections_to_meta():
    class Fake:
        def __init__(self):
            self.jobs = []

        def submit(self, camera, rgb, depth, t, hfov=None):
            self.jobs.append((camera, rgb.shape, depth.shape, t, hfov))

        def latest(self, camera, now=None):
            return {"t": 1.0, "items": [{"box": [0.4, 0.3, 0.6, 0.9], "cls": "person", "conf": 0.8, "dist": 14.2}]}

    async def go():
        app = create_app(sim_port=None, web_dir=None)
        bridge, video = app.state.bridge, app.state.video
        bridge.detector = fake = Fake()
        video.subscribers = 1
        server = await asyncio.start_server(bridge.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        quarry = FakeQuarry(1)
        t = quarry.trucks[0]
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode({"type": "hello", "trucks": [t.id], "cameras": [t.id], "bounds": quarry.bounds}))
        rgb, depth = quarry.render_truck(t)
        cam = {"x": t.x, "y": 3.6, "z": t.z, "yaw": 0, "pitch": 0, "roll": 0, "hfov": 90}
        writer.write(encode_frame(t.id, rgb, depth, truck=t.id, pose={"x": t.x, "z": t.z, "yaw": 0}, cam=cam))
        await writer.drain()
        (camera, msg), = await asyncio.wait_for(video.wait_newer({}), 5)
        writer.close()
        server.close()
        return fake.jobs, msg

    jobs, msg = asyncio.run(go())
    meta, _ = unpack_meta(msg)
    assert meta["det"]["items"][0]["cls"] == "person" and meta["det"]["t"] == 1.0
    (camera, shape, dshape, t, hfov), = jobs
    assert camera == "truck-0" and shape[2] == 3 and len(dshape) == 2
    assert hfov == 90 and abs(t - meta["t"]) < 1e-3
