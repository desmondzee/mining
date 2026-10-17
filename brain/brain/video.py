import asyncio
import logging
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from fractions import Fraction

import av
import numpy as np

log = logging.getLogger(__name__)

FPS = 15
GOP_FRAMES = 15
TRUCK_KBPS = 600
OVERVIEW_KBPS = 400
VIEWER_QUEUE = 30
PRESET = "veryfast"
PROFILE = "baseline"
KEYFRAME = 1
AU_HEADER = struct.Struct("<BI")


@dataclass(frozen=True)
class AccessUnit:
    key: bool
    timestamp_ms: int
    message: bytes


def pack_au(camera: str, key: bool, timestamp_ms: int, data: bytes) -> bytes:
    name = camera.encode()
    return struct.pack("B", len(name)) + name + AU_HEADER.pack(KEYFRAME if key else 0, timestamp_ms & 0xFFFFFFFF) + data


def unpack_au(msg: bytes) -> tuple[str, bool, int, bytes]:
    n = msg[0]
    flags, ts = AU_HEADER.unpack_from(msg, 1 + n)
    return msg[1 : 1 + n].decode(), bool(flags & KEYFRAME), ts, msg[1 + n + AU_HEADER.size :]


def bitrate_for(camera: str) -> int:
    return (OVERVIEW_KBPS if camera == "overview" else TRUCK_KBPS) * 1000


def open_encoder(width: int, height: int, bitrate: int) -> av.CodecContext:
    ctx = av.CodecContext.create("libx264", "w")
    ctx.width, ctx.height = width, height
    ctx.pix_fmt = "yuv420p"
    ctx.time_base = Fraction(1, 1000)
    ctx.framerate = Fraction(FPS, 1)
    ctx.bit_rate = bitrate
    ctx.gop_size = GOP_FRAMES
    ctx.max_b_frames = 0
    kbps = bitrate // 1000
    ctx.options = {
        "preset": PRESET,
        "tune": "zerolatency",
        "profile": PROFILE,
        "x264-params": f"keyint={GOP_FRAMES}:min-keyint={GOP_FRAMES}:scenecut=0:vbv-maxrate={kbps}:vbv-bufsize={kbps}:threads=1",
    }
    ctx.open()
    return ctx


class CameraEncoder:
    def __init__(self, camera: str, on_au, bitrate: int | None = None) -> None:
        self.camera = camera
        self.bitrate = bitrate or bitrate_for(camera)
        self._on_au = on_au
        self._pending: np.ndarray | None = None
        self._cond = threading.Condition()
        self._closed = False
        self.encoded = 0
        self.dropped = 0
        self.encode_s = 0.0
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=f"h264-{camera}", daemon=True)
        self._thread.start()

    def submit(self, rgb: np.ndarray) -> None:
        with self._cond:
            if self._pending is not None:
                self.dropped += 1
            self._pending = rgb
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        ctx = None
        last_pts = -1
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                rgb, self._pending = self._pending, None
            h, w = rgb.shape[:2]
            w, h = w & ~1, h & ~1
            try:
                start = time.perf_counter()
                if ctx is None or (ctx.width, ctx.height) != (w, h):
                    ctx = open_encoder(w, h, self.bitrate)
                frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb[:h, :w]), format="rgb24").reformat(format="yuv420p")
                pts = max(int((time.monotonic() - self._t0) * 1000), last_pts + 1)
                frame.pts = last_pts = pts
                packets = ctx.encode(frame)
                self.encode_s += time.perf_counter() - start
                self.encoded += 1
            except Exception:
                log.exception("h264 encode failed for %s", self.camera)
                ctx = None
                continue
            for p in packets:
                self._on_au(self.camera, bool(p.is_keyframe), int(p.pts), bytes(p))


class Viewer:
    def __init__(self) -> None:
        self.queues: dict[str, deque[AccessUnit]] = {}
        self.need_key: set[str] = set()
        self.event = asyncio.Event()
        self.skipped = 0

    def offer(self, camera: str, au: AccessUnit) -> None:
        q = self.queues.setdefault(camera, deque())
        if au.key:
            self.need_key.discard(camera)
            if len(q) >= VIEWER_QUEUE:
                q.clear()
        elif camera in self.need_key:
            self.skipped += 1
            return
        elif len(q) >= VIEWER_QUEUE:
            self.skipped += len(q) + 1
            q.clear()
            self.need_key.add(camera)
            return
        q.append(au)
        self.event.set()

    async def next_batch(self) -> list[bytes]:
        await self.event.wait()
        self.event.clear()
        out = []
        for q in self.queues.values():
            out.extend(au.message for au in q)
            q.clear()
        return out


class VideoHub:
    def __init__(self) -> None:
        self.encoders: dict[str, CameraEncoder] = {}
        self.gops: dict[str, list[AccessUnit]] = {}
        self.viewers: set[Viewer] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, camera: str, rgb: np.ndarray) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        enc = self.encoders.get(camera)
        if enc is None:
            enc = self.encoders[camera] = CameraEncoder(camera, self._from_encoder)
        enc.submit(rgb)

    def _from_encoder(self, camera: str, key: bool, timestamp_ms: int, data: bytes) -> None:
        au = AccessUnit(key, timestamp_ms, pack_au(camera, key, timestamp_ms, data))
        try:
            self._loop.call_soon_threadsafe(self.publish, camera, au)
        except RuntimeError:
            pass

    def publish(self, camera: str, au: AccessUnit) -> None:
        if au.key:
            self.gops[camera] = [au]
        elif camera in self.gops:
            self.gops[camera].append(au)
        else:
            return
        for v in self.viewers:
            v.offer(camera, au)

    def subscribe(self) -> Viewer:
        v = Viewer()
        for camera, gop in self.gops.items():
            for au in gop:
                v.offer(camera, au)
        self.viewers.add(v)
        return v

    def unsubscribe(self, v: Viewer) -> None:
        self.viewers.discard(v)

    def stats(self) -> dict:
        return {
            c: {"encoded": e.encoded, "dropped": e.dropped, "encode_ms": 1000 * e.encode_s / max(e.encoded, 1)}
            for c, e in self.encoders.items()
        }

    def close(self) -> None:
        for e in self.encoders.values():
            e.close()
        self.encoders.clear()
