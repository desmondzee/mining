import asyncio
import json

import numpy as np
from fastapi.testclient import TestClient

from brain.app import create_app
from brain.fakesim import FakeQuarry
from brain.protocol import encode, encode_frame, encode_lidar, read_message


def test_frame_produces_action_and_jpeg():
    async def go():
        app = create_app(sim_port=None, web_dir=None)
        bridge, world, video = app.state.bridge, app.state.world, app.state.video
        server = await asyncio.start_server(bridge.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        quarry = FakeQuarry(1)
        t = quarry.trucks[0]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode({"type": "hello", "trucks": [t.id], "cameras": [t.id], "bounds": quarry.bounds}))
        await writer.drain()
        await asyncio.sleep(0.05)
        world.trucks[t.id].goal = (t.x + 40, t.z)

        rgb, depth = quarry.render_truck(t)
        writer.write(encode_frame(t.id, rgb, depth, truck=t.id, pose={"x": t.x, "z": t.z, "yaw": 0}))
        await writer.drain()
        header, _ = await asyncio.wait_for(read_message(reader), 5)
        frames = await asyncio.wait_for(video.wait_newer({}), 5)
        connected = world.sim_connected

        writer.close()
        server.close()
        return header, frames, connected

    header, frames, connected = asyncio.run(go())
    assert header["type"] == "action" and header["truck"] == "truck-0"
    assert header["steer"] > 0 and header["throttle"] > 0
    (camera, jpeg), = frames
    assert camera == "truck-0" and jpeg[:2] == b"\xff\xd8"
    assert connected


def test_goal_api():
    app = create_app(sim_port=None, web_dir=None)
    app.state.world.truck("truck-0")
    client = TestClient(app)

    assert client.post("/api/trucks/nope/goal", json={"x": 1, "z": 2}).status_code == 404
    r = client.post("/api/trucks/truck-0/goal", json={"x": 1, "z": 2})
    assert r.status_code == 200 and r.json()["goal"] == [1, 2]
    assert client.get("/api/state").json()["trucks"][0]["goal"] == [1, 2]
    assert client.delete("/api/trucks/truck-0/goal").json()["goal"] is None


def test_lidar_api_and_policy_input():
    async def go():
        app = create_app(sim_port=None, web_dir=None)
        server = await asyncio.start_server(app.state.bridge.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        quarry = FakeQuarry(1)
        t = quarry.trucks[0]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        pts = quarry.scan_lidar(t)
        writer.write(encode_lidar(t.id, pts, {"x": t.x, "z": t.z, "yaw": t.yaw}))
        await writer.drain()
        (truck_id, msg), = await asyncio.wait_for(app.state.lidar.wait_newer({}), 5)
        writer.close()
        server.close()
        return app, pts, truck_id, msg

    app, pts, truck_id, msg = asyncio.run(go())
    from brain.protocol import pack_points

    assert truck_id == "truck-0" and msg.endswith(pack_points(pts))
    r = TestClient(app).get("/api/trucks/truck-0/lidar")
    assert r.status_code == 200 and json.loads(r.headers["x-lidar"])["points"] == len(pts)
    np.testing.assert_allclose(np.frombuffer(r.content, "<f4").reshape(-1, 3), pts, atol=0.006)
    assert TestClient(app).get("/api/trucks/nope/lidar").status_code == 404
