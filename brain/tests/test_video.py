import asyncio
import zlib

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from brain.app import create_app
from brain.fakesim import FakeQuarry
from brain.protocol import compress_points, decompress_points, pack_points
from brain.video import GOP_FRAMES, VIEWER_QUEUE, AccessUnit, VideoHub, pack_au, unpack_au


def nal_types(au: bytes) -> set[int]:
    return {au[i + 3] & 0x1F for i in range(len(au) - 3) if au[i : i + 3] == b"\0\0\1"}


def frames(n: int) -> list[np.ndarray]:
    quarry = FakeQuarry(1)
    out = []
    for _ in range(n):
        quarry.step(1 / 15)
        out.append(quarry.render_truck(quarry.trucks[0])[0])
    return out


async def collect(hub: VideoHub, viewer, count: int) -> list[bytes]:
    got = []
    while len(got) < count:
        got += await asyncio.wait_for(viewer.next_batch(), 5)
    return got


def test_encoder_output_decodes_with_keyframe_on_join():
    async def go():
        hub = VideoHub()
        early = hub.subscribe()
        imgs = frames(GOP_FRAMES + 5)
        for img in imgs:
            hub.submit("truck-0", img)
            await asyncio.sleep(0.02)
        first = await collect(hub, early, GOP_FRAMES + 5)
        late = hub.subscribe()
        joined = await collect(hub, late, 1)
        hub.close()
        return imgs, first, joined

    imgs, first, joined = asyncio.run(go())
    camera, key, _, au = unpack_au(first[0])
    assert camera == "truck-0" and key and {7, 8, 5} <= nal_types(au)
    assert unpack_au(first[GOP_FRAMES])[1]
    assert not any(unpack_au(m)[1] for m in first[1:GOP_FRAMES])

    dec = av.CodecContext.create("h264", "r")
    decoded = [f for m in first for f in dec.decode(av.Packet(unpack_au(m)[3]))]
    assert len(decoded) == len(first) and decoded[0].width == 640 and decoded[0].height == 360
    err = np.abs(decoded[0].to_ndarray(format="rgb24").astype(int) - imgs[0]).mean()
    assert err < 8

    _, key, _, au = unpack_au(joined[0])
    assert key and 7 in nal_types(au)


def test_fanout_shares_one_encoded_message():
    hub = VideoHub()
    viewers = [hub.subscribe() for _ in range(5)]
    au = AccessUnit(True, 0, pack_au("cam", True, 0, b"\0\0\1\x65"))
    hub.publish("cam", au)
    assert all(v.queues["cam"][0].message is au.message for v in viewers)


def test_slow_viewer_drops_until_keyframe():
    hub = VideoHub()
    v = hub.subscribe()
    msg = lambda key: AccessUnit(key, 0, pack_au("cam", key, 0, b"x"))
    hub.publish("cam", msg(True))
    for _ in range(VIEWER_QUEUE + 5):
        hub.publish("cam", msg(False))
    assert "cam" in v.need_key and len(v.queues["cam"]) == 0
    hub.publish("cam", msg(True))
    hub.publish("cam", msg(False))
    assert [a.key for a in v.queues["cam"]] == [True, False]


def test_ws_video_h264_and_jpeg_fallback():
    app = create_app(sim_port=None, web_dir=None)
    hub = app.state.h264
    with TestClient(app) as client:
        hub.publish("cam", AccessUnit(True, 7, pack_au("cam", True, 7, b"\0\0\1\x67")))
        with client.websocket_connect("/ws/video") as ws:
            assert unpack_au(ws.receive_bytes()) == ("cam", True, 7, b"\0\0\1\x67")
        with client.websocket_connect("/ws/video?codec=jpeg") as ws:
            client.portal.call(app.state.video.publish, "cam", b"\xff\xd8jpeg")
            msg = ws.receive_bytes()
            assert msg == b"\x03cam\xff\xd8jpeg"


@pytest.mark.parametrize("seed", [0, 1])
def test_lidar_compression_roundtrip(seed):
    quarry = FakeQuarry(1, seed=seed)
    pts = quarry.scan_lidar(quarry.trucks[0])
    pts[0] = [300, -300, 0.01]
    blob = compress_points(pts)
    assert len(blob) < len(pack_points(pts)) / 2
    np.testing.assert_allclose(decompress_points(blob), pts, atol=0.006)
    assert len(zlib.decompress(blob)) == len(pts) * 6
