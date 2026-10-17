import asyncio
import json
import logging
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bridge import SimBridge
from .hub import LatestHub
from .world import World

log = logging.getLogger(__name__)

STATE_HZ = 10
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class Goal(BaseModel):
    x: float | None = None
    z: float | None = None
    site: str | None = None


def load_world_meta(world_dir: Path | None) -> dict:
    try:
        return json.loads((world_dir / "world.json").read_text())
    except (OSError, TypeError, ValueError):
        return {}


def create_app(
    sim_port: int | None = 9000, web_dir: Path | None = DEFAULT_WEB_DIR, world_dir: Path | None = None
) -> FastAPI:
    meta = load_world_meta(world_dir)
    preview = world_dir / meta["assets"]["preview"] if meta.get("assets", {}).get("preview") else None
    preview = preview if preview and preview.is_file() else None
    world = World()
    video = LatestHub()
    lidar = LatestHub()
    bridge = SimBridge(world, video, lidar)

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
    app.state.video = video
    app.state.lidar = lidar
    app.state.bridge = bridge

    @app.get("/api/state")
    def get_state():
        return world.snapshot()

    @app.get("/api/world")
    def get_world():
        return {**world.network(), "preview": preview is not None, "preview_bounds": meta.get("bounds")}

    @app.get("/api/world/preview")
    def get_preview():
        if preview is None:
            raise HTTPException(404, "no world preview")
        return FileResponse(preview)

    def find_truck(truck_id: str):
        if truck_id not in world.trucks:
            raise HTTPException(404, f"unknown truck {truck_id}")
        return world.trucks[truck_id]

    @app.post("/api/trucks/{truck_id}/goal")
    def post_goal(truck_id: str, goal: Goal):
        truck = find_truck(truck_id)
        graph = world.graph
        site = goal.site
        if site is None:
            if goal.x is None or goal.z is None:
                raise HTTPException(422, "goal needs site or x and z")
            if graph is None:
                truck.set_goal((goal.x, goal.z))
                return truck.to_dict()
            site = graph.nearest_node(goal.x, goal.z)
        if graph is None or site not in graph.nodes:
            raise HTTPException(404, f"unknown site {site}")
        route = graph.route(truck.x, truck.z, site, truck.yaw)
        if route is None:
            raise HTTPException(409, f"no route to {site}")
        truck.set_goal(graph.nodes[site], site, route)
        return truck.to_dict()

    @app.delete("/api/trucks/{truck_id}/goal")
    def delete_goal(truck_id: str):
        truck = find_truck(truck_id)
        truck.set_goal(None)
        return truck.to_dict()

    @app.get("/api/trucks/{truck_id}/lidar")
    def get_lidar(truck_id: str):
        scan = world.lidar.get(truck_id)
        if scan is None:
            raise HTTPException(404, f"no lidar scan for {truck_id}")
        meta = {"truck": truck_id, "seq": scan.seq, "points": len(scan.points), "pose": scan.pose}
        return Response(scan.points.tobytes(), media_type="application/octet-stream", headers={"X-Lidar": json.dumps(meta)})

    @app.websocket("/ws/video")
    async def ws_video(ws: WebSocket):
        await ws.accept()
        seen: dict[str, int] = {}
        try:
            while True:
                for camera, jpeg in await video.wait_newer(seen):
                    name = camera.encode()
                    await ws.send_bytes(struct.pack("B", len(name)) + name + jpeg)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/lidar")
    async def ws_lidar(ws: WebSocket, truck: str):
        await ws.accept()
        seen: dict[str, int] = {}
        try:
            while True:
                for truck_id, msg in await lidar.wait_newer(seen):
                    if truck_id == truck:
                        await ws.send_bytes(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(world.snapshot(network=False))
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
    world_dir = Path(os.environ["WORLD_DIR"]) if os.environ.get("WORLD_DIR") else None
    app = create_app(sim_port=int(os.environ.get("SIM_PORT", 9000)), web_dir=web_dir, world_dir=world_dir)
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
