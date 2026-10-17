import io
import json
import socket
import struct

import numpy as np
from PIL import Image

PREFIX = struct.Struct("<II")
MAX_HEADER = 64 * 1024
MAX_PAYLOAD = 64 * 1024 * 1024
JPEG_QUALITY = 80


def encode(header: dict, payload: bytes = b"") -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return PREFIX.pack(len(h), len(payload)) + h + payload


def encode_jpeg(rgb: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def encode_frame(camera: str, jpeg: bytes, w: int, h: int, depth: np.ndarray | None = None, **fields) -> bytes:
    header = {
        "type": "frame",
        "camera": camera,
        "truck": fields.pop("truck", ""),
        "rgb": {"w": w, "h": h, "format": "jpeg", "bytes": len(jpeg), "flip": False},
        "depth": {"w": 0, "h": 0},
        **fields,
    }
    payload = jpeg
    if depth is not None:
        dh, dw = depth.shape
        header["depth"] = {"w": dw, "h": dh}
        payload += depth.astype("<f4", copy=False).tobytes()
    return encode(header, payload)


def pack_points(points: np.ndarray) -> bytes:
    return np.clip(np.round(points * 100), -32768, 32767).astype("<i2").tobytes()


def encode_lidar(truck: str, points: np.ndarray, pose: dict, sensor: dict | None = None) -> bytes:
    header = {"type": "lidar", "truck": truck, "points": len(points), "format": "int16cm", "pose": pose}
    if sensor is not None:
        header["sensor"] = sensor
    return encode(header, pack_points(points))


def encode_actors(pedestrians: list[dict]) -> bytes:
    return encode({"type": "actors", "pedestrians": pedestrians})


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def read_message(sock: socket.socket) -> tuple[dict, bytes]:
    hlen, plen = PREFIX.unpack(recv_exactly(sock, PREFIX.size))
    if hlen > MAX_HEADER or plen > MAX_PAYLOAD:
        raise ConnectionError(f"message too large: header={hlen} payload={plen}")
    header = json.loads(recv_exactly(sock, hlen))
    payload = recv_exactly(sock, plen) if plen else b""
    return header, payload
