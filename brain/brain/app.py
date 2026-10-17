import asyncio
import json
import logging
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bridge import SimBridge
from .hub import LatestHub
from .video import VideoHub
from .world import World

log = logging.getLogger(__name__)

STATE_HZ = 10
LIDAR_VIEWER_HZ = 10
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class Goal(BaseModel):
    x: float
    z: float


def create_app(sim_port: int | None = 9000, web_dir: Path | None = DEFAULT_WEB_DIR) -> FastAPI:
    world = World()
    video = LatestHub()
    lidar = LatestHub()
    h264 = VideoHub()
    bridge = SimBridge(world, video, lidar, h264)
    lidar_period = 1 / float(os.environ.get("LIDAR_VIEWER_HZ", LIDAR_VIEWER_HZ))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        server = None
        if sim_port is not None:
            server = await asyncio.start_server(bridge.handle, "0.0.0.0", sim_port)
            log.info("waiting for sim on tcp port %d", sim_port)
        yield
        if server:
            server.close()
        h264.close()

    app = FastAPI(lifespan=lifespan)
    app.state.world = world
    app.state.video = video
    app.state.h264 = h264
    app.state.lidar = lidar
    app.state.bridge = bridge

    @app.get("/api/state")
    def get_state():
        return world.snapshot()

    def set_goal(truck_id: str, goal: tuple[float, float] | None) -> dict:
        if truck_id not in world.trucks:
            raise HTTPException(404, f"unknown truck {truck_id}")
        truck = world.trucks[truck_id]
        truck.goal = goal
        truck.status = "driving" if goal else "idle"
        return truck.to_dict()

    @app.post("/api/trucks/{truck_id}/goal")
    def post_goal(truck_id: str, goal: Goal):
        return set_goal(truck_id, (goal.x, goal.z))

    @app.delete("/api/trucks/{truck_id}/goal")
    def delete_goal(truck_id: str):
        return set_goal(truck_id, None)

    @app.get("/api/trucks/{truck_id}/lidar")
    def get_lidar(truck_id: str):
        scan = world.lidar.get(truck_id)
        if scan is None:
            raise HTTPException(404, f"no lidar scan for {truck_id}")
        meta = {"truck": truck_id, "seq": scan.seq, "points": len(scan.points), "pose": scan.pose}
        return Response(scan.points.tobytes(), media_type="application/octet-stream", headers={"X-Lidar": json.dumps(meta)})

    @app.websocket("/ws/video")
    async def ws_video(ws: WebSocket, codec: str = "h264"):
        await ws.accept()
        if codec == "h264":
            viewer = h264.subscribe()
            try:
                while True:
                    for msg in await viewer.next_batch():
                        await ws.send_bytes(msg)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                h264.unsubscribe(viewer)
            return
        seen: dict[str, int] = {}
        video.subscribers += 1
        try:
            while True:
                for camera, jpeg in await video.wait_newer(seen):
                    name = camera.encode()
                    await ws.send_bytes(struct.pack("B", len(name)) + name + jpeg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            video.subscribers -= 1

    @app.websocket("/ws/lidar")
    async def ws_lidar(ws: WebSocket, truck: str):
        await ws.accept()
        seen: dict[str, int] = {}
        loop = asyncio.get_running_loop()
        next_send = 0.0
        try:
            while True:
                await asyncio.sleep(max(0.0, next_send - loop.time()))
                for truck_id, msg in await lidar.wait_newer(seen):
                    if truck_id == truck:
                        await ws.send_bytes(msg)
                        next_send = loop.time() + lidar_period
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(world.snapshot())
                await asyncio.sleep(1 / STATE_HZ)
        except (WebSocketDisconnect, RuntimeError):
            pass

    if web_dir and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    web_dir = Path(os.environ.get("WEB_DIR", DEFAULT_WEB_DIR))
    app = create_app(sim_port=int(os.environ.get("SIM_PORT", 9000)), web_dir=web_dir)
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
