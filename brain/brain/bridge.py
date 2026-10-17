"""TCP server the simulator connects to: frames in, truck actions out."""

import asyncio
import io
import logging

import numpy as np
from PIL import Image

from . import policy
from .hub import FrameHub
from .protocol import ProtocolError, decode_frame, encode, read_message
from .world import World

log = logging.getLogger(__name__)

JPEG_QUALITY = 75


def encode_jpeg(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb)).save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


class SimBridge:
    def __init__(self, world: World, hub: FrameHub) -> None:
        self.world = world
        self.hub = hub
        self._writer: asyncio.StreamWriter | None = None

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
            writer.close()
            log.info("sim disconnected (%s)", peer)

    def _on_hello(self, header: dict) -> None:
        if "bounds" in header:
            self.world.bounds = [float(v) for v in header["bounds"]]
        self.world.cameras = list(header.get("cameras", []))
        for truck_id in header.get("trucks", []):
            self.world.truck(truck_id)
        log.info("sim hello: trucks=%s cameras=%s", header.get("trucks"), self.world.cameras)

    async def _on_frame(self, header: dict, payload: bytes, writer: asyncio.StreamWriter) -> None:
        rgb, depth = decode_frame(header, payload)
        jpeg = await asyncio.to_thread(encode_jpeg, rgb)
        await self.hub.publish(header["camera"], jpeg)

        truck_id = header.get("truck")
        if not truck_id:
            return
        truck = self.world.truck(truck_id)
        pose = header.get("pose", {})
        truck.x = float(pose.get("x", truck.x))
        truck.z = float(pose.get("z", truck.z))
        truck.yaw = float(pose.get("yaw", truck.yaw))
        truck.speed = float(header.get("speed", truck.speed))

        action = policy.act(truck, rgb, depth)
        truck.throttle, truck.steer, truck.status = action.throttle, action.steer, action.status
        if action.status == "arrived":
            truck.goal = None
        writer.write(
            encode({"type": "action", "truck": truck_id, "throttle": action.throttle, "steer": action.steer})
        )
        await writer.drain()
