import asyncio

import numpy as np
import pytest

from brain.protocol import ProtocolError, decode_frame, encode, encode_frame, read_message


def roundtrip(data: bytes):
    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return await read_message(reader)

    return asyncio.run(go())


def test_frame_roundtrip_with_depth():
    rgb = np.random.default_rng(0).integers(0, 255, (4, 6, 3), dtype=np.uint8)
    depth = np.arange(12, dtype=np.float32).reshape(3, 4)
    header, payload = roundtrip(encode_frame("truck-0", rgb, depth, truck="truck-0", pose={"x": 1}))

    assert header["camera"] == "truck-0" and header["pose"] == {"x": 1}
    out_rgb, out_depth, _ = decode_frame(header, payload)
    np.testing.assert_array_equal(out_rgb, rgb)
    np.testing.assert_array_equal(out_depth, depth)


def test_flip_flag_reverses_rows():
    rgb = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3)
    header, payload = roundtrip(encode_frame("overview", rgb))
    header["rgb"]["flip"] = True
    out, depth, _ = decode_frame(header, payload)
    np.testing.assert_array_equal(out, rgb[::-1])
    assert depth is None


def test_payload_size_mismatch_is_rejected():
    header, payload = roundtrip(encode_frame("x", np.zeros((2, 2, 3), np.uint8)))
    with pytest.raises(ProtocolError):
        decode_frame(header, payload[:-1])


def test_plain_message():
    header, payload = roundtrip(encode({"type": "hello", "trucks": ["a"]}))
    assert header == {"type": "hello", "trucks": ["a"]} and payload == b""


def test_rgba_and_empty_depth_as_sent_by_unity():
    rgba = np.random.default_rng(1).integers(0, 255, (2, 3, 4), dtype=np.uint8)
    header = {"type": "frame", "camera": "overview", "truck": "",
              "rgb": {"w": 3, "h": 2, "flip": False, "channels": 4}, "depth": {"w": 0, "h": 0}}
    rgb, depth, _ = decode_frame(header, rgba.tobytes())
    np.testing.assert_array_equal(rgb, rgba[..., :3])
    assert depth is None


def test_lidar_roundtrip():
    from brain.protocol import decode_lidar, encode_lidar

    pts = np.array([[0.0, -3.0, 12.345], [-1.234, 0.5, 99.99]], np.float32)
    header, payload = roundtrip(encode_lidar("truck-0", pts, {"x": 1, "z": 2, "yaw": 3}))
    assert header["type"] == "lidar" and header["points"] == 2 and header["format"] == "int16cm"
    assert len(payload) == 12
    np.testing.assert_allclose(decode_lidar(header, payload), pts, atol=0.006)
    with pytest.raises(ProtocolError):
        decode_lidar(header, payload[:-2])


def test_lidar_f32_still_accepted():
    from brain.protocol import decode_lidar

    pts = np.arange(6, dtype=np.float32).reshape(2, 3)
    np.testing.assert_array_equal(decode_lidar({"points": 2}, pts.tobytes()), pts)


def test_jpeg_frame_passes_through_with_depth():
    import io

    from PIL import Image

    rgb = np.zeros((36, 64, 3), np.uint8)
    rgb[:, :32] = 200
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=95)
    jpeg = buf.getvalue()
    depth = np.full((9, 16), 5.0, np.float32)
    header = {"type": "frame", "camera": "truck-0", "truck": "truck-0",
              "rgb": {"w": 64, "h": 36, "format": "jpeg", "bytes": len(jpeg)}, "depth": {"w": 16, "h": 9}}
    out, out_depth, encoded = decode_frame(header, jpeg + depth.tobytes())
    assert encoded == jpeg and out.shape == (36, 64, 3) and abs(int(out[0, 0, 0]) - 200) < 10
    np.testing.assert_array_equal(out_depth, depth)
