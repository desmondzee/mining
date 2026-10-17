import asyncio
import logging
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bridge import SimBridge
from .hub import FrameHub
from .world import World

log = logging.getLogger(__name__)

STATE_HZ = 10
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class Goal(BaseModel):
    x: float
    z: float


def create_app(sim_port: int | None = 9000, web_dir: Path | None = DEFAULT_WEB_DIR) -> FastAPI:
    world = World()
    hub = FrameHub()
    bridge = SimBridge(world, hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        server = None
        if sim_port is not None:
            server = await asyncio.start_server(bridge.handle, "0.0.0.0", sim_port)
            log.info("waiting for sim on tcp port %d", sim_port)
        yield
        if server:
            server.close()

    app = FastAPI(lifespan=lifespan)
    app.state.world = world
    app.state.hub = hub
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

    @app.websocket("/ws/video")
    async def ws_video(ws: WebSocket):
        await ws.accept()
        seen: dict[str, int] = {}
        try:
            while True:
                for camera, jpeg in await hub.wait_newer(seen):
                    name = camera.encode()
                    await ws.send_bytes(struct.pack("B", len(name)) + name + jpeg)
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
