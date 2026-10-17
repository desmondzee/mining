import asyncio
import json
import struct

import numpy as np

PREFIX = struct.Struct("<II")
MAX_HEADER = 64 * 1024
MAX_PAYLOAD = 64 * 1024 * 1024


class ProtocolError(Exception):
    pass


def encode(header: dict, payload: bytes = b"") -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return PREFIX.pack(len(h), len(payload)) + h + payload


async def read_message(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    hlen, plen = PREFIX.unpack(await reader.readexactly(PREFIX.size))
    if hlen > MAX_HEADER or plen > MAX_PAYLOAD:
        raise ProtocolError(f"message too large: header={hlen} payload={plen}")
    header = json.loads(await reader.readexactly(hlen))
    payload = await reader.readexactly(plen) if plen else b""
    return header, payload


def encode_frame(
    camera: str,
    rgb: np.ndarray,
    depth: np.ndarray | None = None,
    **fields,
) -> bytes:
    h, w, _ = rgb.shape
    header = {"type": "frame", "camera": camera, "rgb": {"w": w, "h": h, "flip": False}, **fields}
    payload = rgb.astype(np.uint8, copy=False).tobytes()
    if depth is not None:
        dh, dw = depth.shape
        header["depth"] = {"w": dw, "h": dh}
        payload += depth.astype("<f4", copy=False).tobytes()
    return encode(header, payload)


def decode_frame(header: dict, payload: bytes) -> tuple[np.ndarray, np.ndarray | None]:
    rgb_meta = header["rgb"]
    w, h = rgb_meta["w"], rgb_meta["h"]
    channels = rgb_meta.get("channels", 3)
    if channels not in (3, 4):
        raise ProtocolError(f"unsupported channel count {channels}")
    rgb_len = w * h * channels
    depth_meta = header.get("depth")
    if depth_meta and not (depth_meta.get("w") and depth_meta.get("h")):
        depth_meta = None
    depth_len = depth_meta["w"] * depth_meta["h"] * 4 if depth_meta else 0
    if len(payload) != rgb_len + depth_len:
        raise ProtocolError(f"frame payload is {len(payload)} bytes, expected {rgb_len + depth_len}")

    rgb = np.frombuffer(payload, np.uint8, rgb_len).reshape(h, w, channels)[..., :3]
    if rgb_meta.get("flip"):
        rgb = rgb[::-1]

    depth = None
    if depth_meta:
        depth = np.frombuffer(payload, "<f4", offset=rgb_len).reshape(depth_meta["h"], depth_meta["w"])
    return rgb, depth
