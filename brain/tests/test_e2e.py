"""End-to-end: fakesim -> bridge -> policy -> actions back, frames out to the hub."""

import asyncio

from fastapi.testclient import TestClient

from brain.app import create_app
from brain.fakesim import FakeQuarry
from brain.protocol import encode, encode_frame, read_message


def test_frame_produces_action_and_jpeg():
    async def go():
        app = create_app(sim_port=None, web_dir=None)
        bridge, world, hub = app.state.bridge, app.state.world, app.state.hub
        server = await asyncio.start_server(bridge.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        quarry = FakeQuarry(1)
        t = quarry.trucks[0]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode({"type": "hello", "trucks": [t.id], "cameras": [t.id], "bounds": quarry.bounds}))
        await writer.drain()
        await asyncio.sleep(0.05)
        world.trucks[t.id].goal = (t.x + 40, t.z)  # due east -> steer right

        rgb, depth = quarry.render_truck(t)
        writer.write(encode_frame(t.id, rgb, depth, truck=t.id, pose={"x": t.x, "z": t.z, "yaw": 0}))
        await writer.drain()
        header, _ = await asyncio.wait_for(read_message(reader), 5)
        frames = await asyncio.wait_for(hub.wait_newer({}), 5)
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
