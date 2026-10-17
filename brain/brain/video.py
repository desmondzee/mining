import asyncio
import functools
import logging
import os
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
BASE_PIXELS = 640 * 360
MAX_KBPS = 4000
VIEWER_QUEUE = 30
PRESET = "veryfast"
NVENC_PRESET = "p4"
PROFILE = "baseline"
ENCODER = "auto"
X264 = "libx264"
NVENC = "h264_nvenc"
PROBE_W = 320
PROBE_H = 240
KEYFRAME = 1
AU_HEADER = struct.Struct("<BIH")


@dataclass(frozen=True)
class AccessUnit:
    key: bool
    timestamp_ms: int
    message: bytes


def pack_au(camera: str, key: bool, timestamp_ms: int, data: bytes, meta: bytes = b"") -> bytes:
    name = camera.encode()
    header = AU_HEADER.pack(KEYFRAME if key else 0, timestamp_ms & 0xFFFFFFFF, len(meta))
    return struct.pack("B", len(name)) + name + header + meta + data


def unpack_au(msg: bytes) -> tuple[str, bool, int, bytes, bytes]:
    n = msg[0]
    flags, ts, meta_len = AU_HEADER.unpack_from(msg, 1 + n)
    start = 1 + n + AU_HEADER.size
    return msg[1 : 1 + n].decode(), bool(flags & KEYFRAME), ts, msg[start + meta_len :], msg[start : start + meta_len]


def bitrate_for(camera: str) -> int:
    return (OVERVIEW_KBPS if camera == "overview" else TRUCK_KBPS) * 1000


def scaled_bitrate(bitrate: int, width: int, height: int) -> int:
    return int(min(bitrate * max(1.0, width * height / BASE_PIXELS), MAX_KBPS * 1000))


def open_encoder(width: int, height: int, bitrate: int, codec: str = X264) -> av.CodecContext:
    ctx = av.CodecContext.create(codec, "w")
    ctx.width, ctx.height = width, height
    ctx.pix_fmt = "yuv420p"
    ctx.time_base = Fraction(1, 1000)
    ctx.framerate = Fraction(FPS, 1)
    ctx.bit_rate = bitrate
    ctx.gop_size = GOP_FRAMES
    ctx.max_b_frames = 0
    kbps = bitrate // 1000
    if codec == NVENC:
        ctx.options = {
            "preset": NVENC_PRESET,
            "tune": "ull",
            "profile": PROFILE,
            "rc": "cbr",
            "zerolatency": "1",
            "delay": "0",
            "bf": "0",
            "forced-idr": "1",
            "maxrate": str(bitrate),
            "bufsize": str(bitrate),
        }
    else:
        ctx.options = {
            "preset": PRESET,
            "tune": "zerolatency",
            "profile": PROFILE,
            "x264-params": f"keyint={GOP_FRAMES}:min-keyint={GOP_FRAMES}:scenecut=0:vbv-maxrate={kbps}:vbv-bufsize={kbps}:threads=1",
        }
    ctx.open()
    return ctx


@functools.cache
def nvenc_available() -> bool:
    try:
        ctx = open_encoder(PROBE_W, PROBE_H, bitrate_for(""), NVENC)
        frame = av.VideoFrame(PROBE_W, PROBE_H, "yuv420p")
        frame.pts = 0
        ctx.encode(frame)
        return True
    except Exception as e:
        log.info("h264_nvenc unavailable: %s", e)
        return False


def choose_encoder(choice: str | None = None) -> str:
    choice = (choice or os.environ.get("VIDEO_ENCODER", ENCODER)).strip().lower()
    if choice == "x264":
        return X264
    if nvenc_available():
        return NVENC
    if choice == "nvenc":
        log.warning("VIDEO_ENCODER=nvenc but h264_nvenc unavailable; using libx264")
    return X264


class CameraEncoder:
    def __init__(self, camera: str, on_au, bitrate: int | None = None, codec: str = X264) -> None:
        self.camera = camera
        self.bitrate = bitrate or bitrate_for(camera)
        self.codec = codec
        self._on_au = on_au
        self._pending: tuple[np.ndarray, bytes] | None = None
        self._cond = threading.Condition()
        self._closed = False
        self.encoded = 0
        self.dropped = 0
        self.encode_s = 0.0
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=f"h264-{camera}", daemon=True)
        self._thread.start()

    def submit(self, rgb: np.ndarray, meta: bytes = b"") -> None:
        with self._cond:
            if self._pending is not None:
                self.dropped += 1
            self._pending = (rgb, meta)
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
                (rgb, meta), self._pending = self._pending, None
            h, w = rgb.shape[:2]
            w, h = w & ~1, h & ~1
            try:
                start = time.perf_counter()
                if ctx is None or (ctx.width, ctx.height) != (w, h):
                    ctx = self._open(w, h)
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
                self._on_au(self.camera, bool(p.is_keyframe), int(p.pts), bytes(p), meta)

    def _open(self, w: int, h: int) -> av.CodecContext:
        if self.codec != X264:
            try:
                return open_encoder(w, h, scaled_bitrate(self.bitrate, w, h), self.codec)
            except Exception as e:
                log.warning("%s failed for %s (%s); using libx264", self.codec, self.camera, e)
                self.codec = X264
        return open_encoder(w, h, scaled_bitrate(self.bitrate, w, h))


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
    def __init__(self, codec: str | None = None) -> None:
        self.encoders: dict[str, CameraEncoder] = {}
        self.gops: dict[str, list[AccessUnit]] = {}
        self.viewers: set[Viewer] = set()
        self.codec = codec or choose_encoder()
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, camera: str, rgb: np.ndarray, meta: bytes = b"") -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        enc = self.encoders.get(camera)
        if enc is None:
            enc = self.encoders[camera] = CameraEncoder(camera, self._from_encoder, codec=self.codec)
        enc.submit(rgb, meta)

    def _from_encoder(self, camera: str, key: bool, timestamp_ms: int, data: bytes, meta: bytes = b"") -> None:
        au = AccessUnit(key, timestamp_ms, pack_au(camera, key, timestamp_ms, data, meta))
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
            c: {"codec": e.codec, "encoded": e.encoded, "dropped": e.dropped, "encode_ms": round(1000 * e.encode_s / max(e.encoded, 1), 2)}
            for c, e in self.encoders.items()
        }

    def close(self) -> None:
        for e in self.encoders.values():
            e.close()
        self.encoders.clear()
