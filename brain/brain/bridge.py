import asyncio
import io
import logging
import struct
import time

import numpy as np
from PIL import Image

from . import policy
from .detector import DetectorWorker
from .hub import LatestHub
from .hud import frame_meta, pack_meta
from .protocol import ProtocolError, compress_points, decode_frame, decode_lidar, encode, read_message
from .video import VideoHub
from .world import Scan, World

log = logging.getLogger(__name__)

JPEG_QUALITY = 75
POSE = struct.Struct("<3f")


def encode_jpeg(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb)).save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def lidar_message(truck_id: str, scan: Scan) -> bytes:
    name = truck_id.encode()
    pose = POSE.pack(scan.pose["x"], scan.pose["z"], scan.pose["yaw"])
    return struct.pack("B", len(name)) + name + pose + compress_points(scan.points)


class SimBridge:
    def __init__(self, world: World, video: LatestHub, lidar: LatestHub, h264: VideoHub | None = None,
                 detector: DetectorWorker | None = None) -> None:
        self.world = world
        self.video = video
        self.h264 = h264
        self.lidar = lidar
        self.detector = detector
        self._writer: asyncio.StreamWriter | None = None
        self._scan_seq = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if self._writer is not None:
            log.warning("new sim connection from %s replaces the existing one", peer)
            self._writer.close()
        self._writer = writer
        self.world.sim_connected = True
        log.info("sim connected from %s", peer)
        try:
            while True:
                header, payload = await read_message(reader)
                kind = header.get("type")
                if kind == "hello":
                    self._on_hello(header)
                elif kind == "frame":
                    await self._on_frame(header, payload, writer)
                elif kind == "lidar":
                    await self._on_lidar(header, payload)
                elif kind == "actors":
                    self.world.pedestrians = list(header.get("pedestrians", []))
                else:
                    log.warning("ignoring unknown message type %r", kind)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except ProtocolError:
            log.exception("protocol error from %s", peer)
        finally:
            if self._writer is writer:
                self._writer = None
                self.world.sim_connected = False
                self.world.pedestrians = []
            writer.close()
            log.info("sim disconnected (%s)", peer)

    def _on_hello(self, header: dict) -> None:
        if "bounds" in header:
            self.world.bounds = [float(v) for v in header["bounds"]]
        self.world.cameras = list(header.get("cameras", []))
        self.world.obstacles = list(header.get("obstacles", []))
        self.world.paths = [list(p.get("points", [])) for p in header.get("paths", [])]
        self.world.truck_model = str(header.get("truck_model", ""))
        self.world.set_network(
            list(header.get("roads", [])), list(header.get("sites", [])), list(header.get("junctions", []))
        )
        for truck_id in header.get("trucks", []):
            self.world.truck(truck_id)
        log.info("sim hello: trucks=%s cameras=%s", header.get("trucks"), self.world.cameras)

    async def _on_lidar(self, header: dict, payload: bytes) -> None:
        truck_id = header["truck"]
        self._scan_seq += 1
        scan = Scan(self._scan_seq, header["pose"], decode_lidar(header, payload))
        self.world.lidar[truck_id] = scan
        await self.lidar.publish(truck_id, lidar_message(truck_id, scan))

    async def _on_frame(self, header: dict, payload: bytes, writer: asyncio.StreamWriter) -> None:
        rgb, depth, jpeg = decode_frame(header, payload)
        t = time.time()
        det = None
        if self.detector and header.get("truck"):
            self.detector.submit(header["camera"], rgb, depth, t, (header.get("cam") or {}).get("hfov"))
            det = self.detector.latest(header["camera"], t)
        meta = frame_meta(header, t, det)
        if self.h264:
            self.h264.submit(header["camera"], rgb, meta)
        if jpeg is None and self.video.subscribers:
            jpeg = await asyncio.to_thread(encode_jpeg, rgb)
        if jpeg is not None:
            await self.video.publish(header["camera"], pack_meta(meta) + jpeg)

        truck_id = header.get("truck")
        if not truck_id:
            return
        truck = self.world.truck(truck_id)
        pose = header.get("pose", {})
        truck.x = float(pose.get("x", truck.x))
        truck.z = float(pose.get("z", truck.z))
        truck.yaw = float(pose.get("yaw", truck.yaw))
        truck.speed = float(header.get("speed", truck.speed))

        scan = self.world.lidar.get(truck_id)
        action = policy.act(truck, rgb, depth, scan.points if scan else None)
        truck.throttle, truck.steer, truck.status = action.throttle, action.steer, action.status
        if action.status == "arrived":
            truck.set_goal(None)
            truck.status = "arrived"
        writer.write(
            encode({"type": "action", "truck": truck_id, "throttle": action.throttle, "steer": action.steer})
        )
        await writer.drain()
