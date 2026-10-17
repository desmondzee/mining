import asyncio
import io

import numpy as np
from brain import protocol as brain
from PIL import Image

from isaac.sim import protocol as ours

POSE = {"x": 1.5, "z": -2.25, "yaw": 359.5}


def roundtrip(data: bytes):
    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return await brain.read_message(reader)

    return asyncio.run(go())


def test_constants_match():
    assert ours.PREFIX.format == brain.PREFIX.format
    assert ours.MAX_HEADER == brain.MAX_HEADER
    assert ours.MAX_PAYLOAD == brain.MAX_PAYLOAD


def test_encode_bytes_identical():
    header = {"type": "hello", "trucks": ["truck-0"], "bounds": [-362.5, -362.5, 362.5, 362.5], "paths": [{"points": [{"x": 1.0, "z": 2.0}]}]}
    assert ours.encode(header) == brain.encode(header)
    assert ours.encode({"type": "x"}, b"abc") == brain.encode({"type": "x"}, b"abc")


def test_lidar_bytes_identical():
    pts = np.random.default_rng(0).uniform(-400, 400, (500, 3)).astype(np.float32)
    assert ours.encode_lidar("truck-1", pts, POSE) == brain.encode_lidar("truck-1", pts, POSE)
    assert ours.pack_points(pts) == brain.pack_points(pts)
    assert ours.encode_lidar("truck-1", pts[:0], POSE) == brain.encode_lidar("truck-1", pts[:0], POSE)


def test_actors_bytes_identical():
    peds = [{"id": "Pedestrian_00", "x": 1.0, "z": 2.0, "yaw": 90.0}]
    assert ours.encode_actors(peds) == brain.encode({"type": "actors", "pedestrians": peds})


def test_jpeg_frame_decodes_in_brain():
    rgb = np.zeros((36, 64, 3), np.uint8)
    rgb[:18] = (200, 50, 20)
    jpeg = ours.encode_jpeg(rgb)
    depth = np.arange(64 * 36, dtype=np.float32).reshape(36, 64)
    header, payload = roundtrip(ours.encode_frame("truck-0", jpeg, 64, 36, depth, truck="truck-0", pose=POSE, speed=3.0))
    assert header["rgb"] == {"w": 64, "h": 36, "format": "jpeg", "bytes": len(jpeg), "flip": False}
    assert header["depth"] == {"w": 64, "h": 36} and header["truck"] == "truck-0" and header["pose"] == POSE
    out, out_depth, encoded = brain.decode_frame(header, payload)
    assert encoded == jpeg and out.shape == (36, 64, 3)
    assert abs(int(out[0, 0, 0]) - 200) < 10 and out[-1, 0].max() < 10
    np.testing.assert_array_equal(out_depth, depth)


def test_overview_frame_has_empty_depth():
    jpeg = ours.encode_jpeg(np.zeros((360, 640, 3), np.uint8))
    header, payload = roundtrip(ours.encode_frame("overview", jpeg, 640, 360))
    assert header["truck"] == "" and header["depth"] == {"w": 0, "h": 0}
    rgb, depth, _ = brain.decode_frame(header, payload)
    assert depth is None and rgb.shape == (360, 640, 3)
    assert Image.open(io.BytesIO(payload)).size == (640, 360)


def test_depth_bytes_match_brain_layout():
    depth = np.linspace(0, 100, 64 * 36, dtype=np.float32).reshape(36, 64)
    b = brain.encode_frame("truck-0", np.zeros((2, 2, 3), np.uint8), depth)
    o = ours.encode_frame("truck-0", b"JPEG", 2, 2, depth)
    assert b[-depth.nbytes :] == o[-depth.nbytes :]


class FakeSock:
    def __init__(self, data: bytes):
        self.data = data

    def recv(self, n: int) -> bytes:
        chunk, self.data = self.data[: min(n, 3)], self.data[min(n, 3) :]
        return chunk


def test_read_message_reads_brain_action():
    msg = brain.encode({"type": "action", "truck": "truck-0", "throttle": 0.5, "steer": -0.25})
    sock = FakeSock(msg + msg)
    for _ in range(2):
        header, payload = ours.read_message(sock)
        assert header == {"type": "action", "truck": "truck-0", "throttle": 0.5, "steer": -0.25} and payload == b""


def test_bridge_dispatches_reset():
    from isaac.sim.bridge import Bridge

    calls = []
    bridge = Bridge("h", 1, dict, lambda *a: None, None, lambda: calls.append(1))
    bridge.receive(FakeSock(brain.encode({"type": "reset"})))
    assert calls == [1]
