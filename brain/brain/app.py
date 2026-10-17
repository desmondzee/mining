import asyncio
import time
import json
import logging
import math
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import detector, hud, planning, radio_proxy
from .compact import pack_state
from .bridge import SimBridge
from .hub import LatestHub
from .video import VideoHub
from .world import World

log = logging.getLogger(__name__)

STATE_HZ = 10
LIDAR_VIEWER_HZ = 10
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MIN_BOX = 0.02
MAX_BOX_ASPECT = 8.0
ZOOM_CAMERAS = ("overview",)


class Goal(BaseModel):
    x: float | None = None
    z: float | None = None
    site: str | None = None


class View(BaseModel):
    box: list[float]


def check_box(box: list[float]) -> list[float]:
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise HTTPException(422, "box must be [x0, y0, x1, y1]")
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if not (0 <= x0 and 0 <= y0 and x1 <= 1 and y1 <= 1):
        raise HTTPException(422, "box must lie within 0..1")
    if w < MIN_BOX or h < MIN_BOX:
        raise HTTPException(422, f"box must be at least {MIN_BOX} in each dimension")
    if max(w / h, h / w) > MAX_BOX_ASPECT:
        raise HTTPException(422, f"box aspect must be within 1:{MAX_BOX_ASPECT:g}")
    return [round(v, 4) for v in box]


def load_world_meta(world_dir: Path) -> dict:
    try:
        return json.loads((world_dir / "world.json").read_text())
    except (OSError, ValueError):
        return {}


def create_app(sim_port: int | None = 9000, web_dir: Path | None = DEFAULT_WEB_DIR) -> FastAPI:
    world_dir = Path(os.environ.get("WORLD_DIR", planning.DEFAULT_WORLD_DIR))
    meta = load_world_meta(world_dir)
    preview = world_dir / meta["assets"]["preview"] if meta.get("assets", {}).get("preview") else None
    preview = preview if preview and preview.is_file() else None
    world = World()
    nav = planning.nav_map()
    if nav is not None:
        world.bounds = nav.bounds
    video = LatestHub()
    lidar = LatestHub()
    h264 = VideoHub()
    det = detector.from_env()
    bridge = SimBridge(world, video, lidar, h264, det)
    lidar_period = 1 / float(os.environ.get("LIDAR_VIEWER_HZ", LIDAR_VIEWER_HZ))
    heightmap = hud.load_heightmap(os.environ.get("WORLD_DIR"))

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
        if det:
            det.close()

    app = FastAPI(lifespan=lifespan)
    app.state.world = world
    app.state.video = video
    app.state.h264 = h264
    app.state.lidar = lidar
    app.state.bridge = bridge

    @app.get("/api/state")
    def get_state():
        return hud.augment(world.snapshot(), heightmap)

    @app.get("/api/detector")
    def get_detector():
        if det is None:
            return {"enabled": False}
        model = det.detect
        return {"enabled": True, "model": os.path.basename(model.path), "input": list(model.size),
                "classes": len(model.names), **det.stats()}

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

    def nodes() -> dict[str, tuple[float, float]]:
        return world.nodes() or (dict(nav.sites) if nav is not None else {})

    def set_goal(truck_id: str, goal: tuple[float, float] | None, site: str | None = None) -> dict:
        truck = find_truck(truck_id)
        truck.set_goal(goal, site)
        if goal and nav is not None:
            route = planning.plan_route(nav, (truck.x, truck.z), goal)
            if route is None:
                log.warning("no route for %s to %s; driving straight", truck_id, goal)
            else:
                truck.path = route.round(2).tolist()
        return truck.to_dict()

    def set_site(truck_id: str, site: str) -> dict:
        known = nodes()
        if site not in known:
            raise HTTPException(404, f"unknown site {site}")
        return set_goal(truck_id, known[site], site)

    @app.post("/api/trucks/{truck_id}/goal")
    def post_goal(truck_id: str, goal: Goal):
        if goal.site is not None:
            return set_site(truck_id, goal.site)
        if goal.x is None or goal.z is None:
            raise HTTPException(422, "goal needs site or x and z")
        if world.roads:
            known = world.nodes()
            return set_site(truck_id, min(known, key=lambda n: math.dist(known[n], (goal.x, goal.z))))
        return set_goal(truck_id, (goal.x, goal.z))

    @app.get("/api/sites")
    def get_sites():
        if nav is None:
            return []
        return [{"id": k, "type": nav.site_types[k], "pos": list(p)} for k, p in nav.sites.items()]

    @app.post("/api/trucks/{truck_id}/site/{site_id}")
    def post_site(truck_id: str, site_id: str):
        return set_site(truck_id, site_id)

    @app.delete("/api/trucks/{truck_id}/goal")
    def delete_goal(truck_id: str):
        return set_goal(truck_id, None)

    @app.get("/api/trucks/{truck_id}/lidar")
    def get_lidar(truck_id: str):
        scan = world.lidar.get(truck_id)
        if scan is None:
            raise HTTPException(404, f"no lidar scan for {truck_id}")
        meta = {"truck": truck_id, "seq": scan.seq, "points": len(scan.points), "pose": scan.pose, "sensor": scan.sensor}
        return Response(scan.points.tobytes(), media_type="application/octet-stream", headers={"X-Lidar": json.dumps(meta)})

    def find_view(camera: str) -> dict:
        if camera not in ZOOM_CAMERAS:
            raise HTTPException(404, f"camera {camera} has no zoom")
        return world.views.setdefault(camera, {"box": None})

    @app.post("/api/view/{camera}")
    def post_view(camera: str, view: View):
        if find_view(camera)["box"] is not None:
            raise HTTPException(409, f"{camera} is already zoomed")
        bridge.send_view(camera, check_box(view.box))
        return world.views[camera]

    @app.delete("/api/view/{camera}")
    def delete_view(camera: str):
        find_view(camera)
        bridge.send_view(camera, None)
        return world.views[camera]

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

    state_cache = {"t": 0.0, "text": ""}

    def packed_state() -> str:
        now = time.monotonic()
        if now - state_cache["t"] >= 1 / STATE_HZ / 2:
            state_cache["text"] = pack_state(hud.augment(world.snapshot(network=False), heightmap))
            state_cache["t"] = now
        return state_cache["text"]

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_text(packed_state())
                await asyncio.sleep(1 / STATE_HZ)
        except (WebSocketDisconnect, RuntimeError):
            pass

    radio_proxy.mount(app)
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
