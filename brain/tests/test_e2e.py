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


def quarry_app():
    from pathlib import Path

    from brain.fakesim import load_world

    world_dir = Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry"
    app = create_app(sim_port=None, web_dir=None, world_dir=world_dir)
    world = load_world(world_dir)
    q = FakeQuarry(3, world=world)
    app.state.bridge._on_hello(q.hello())
    app.state.sites = {s["id"]: [s["x"], s["z"]] for s in world["sites"]}
    for t in q.trucks:
        truck = app.state.world.truck(t.id)
        truck.x, truck.z, truck.yaw = t.x, t.z, t.yaw
    return app, TestClient(app)


def test_site_goal_routes_over_roads():
    app, client = quarry_app()
    r = client.post("/api/trucks/truck-0/goal", json={"site": "crusher"})
    body = r.json()
    assert r.status_code == 200 and body["goal_site"] == "crusher"
    crusher = app.state.sites["crusher"]
    assert body["goal"] == crusher and body["route"][-1] == crusher
    assert len(body["route"]) > 20
    assert client.post("/api/trucks/truck-0/goal", json={"site": "nope"}).status_code == 404
    assert client.post("/api/trucks/truck-0/goal", json={}).status_code == 422
    state = client.get("/api/state").json()
    assert {"roads", "sites", "junctions", "world_seq"} <= set(state)
    assert state["trucks"][0]["route"] == body["route"]
    cleared = client.delete("/api/trucks/truck-0/goal").json()
    assert cleared["goal"] is None and cleared["goal_site"] is None and cleared["route"] == []


def test_xy_goal_snaps_to_nearest_node():
    app, client = quarry_app()
    ox, oz = app.state.sites["ore_2"]
    body = client.post("/api/trucks/truck-1/goal", json={"x": ox + 3, "z": oz - 4}).json()
    assert body["goal_site"] == "ore_2" and body["goal"] == [ox, oz]


def test_xy_goal_without_roads_is_straight_line():
    app = create_app(sim_port=None, web_dir=None)
    app.state.world.truck("truck-0")
    body = TestClient(app).post("/api/trucks/truck-0/goal", json={"x": 5, "z": 6}).json()
    assert body["goal"] == [5, 6] and body["goal_site"] is None and body["route"] == []


def test_world_endpoint_and_preview():
    _, client = quarry_app()
    w = client.get("/api/world").json()
    assert len(w["roads"]) == 8 and len(w["sites"]) == 7 and len(w["junctions"]) == 2
    assert w["preview"] and w["preview_bounds"] == w["bounds"]
    r = client.get("/api/world/preview")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert TestClient(create_app(sim_port=None, web_dir=None)).get("/api/world/preview").status_code == 404
