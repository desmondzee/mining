import json
import logging
import struct
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

META_LEN = struct.Struct("<H")
META_FIELDS = ("cam", "pose", "speed", "truck")


def frame_meta(header: dict, t: float | None = None) -> bytes:
    meta = {"t": round(time.time() if t is None else t, 3)}
    meta.update({k: header[k] for k in META_FIELDS if header.get(k) not in (None, "")})
    data = json.dumps(meta, separators=(",", ":")).encode()
    return data if len(data) <= 0xFFFF else b""


def pack_meta(meta: bytes) -> bytes:
    return META_LEN.pack(len(meta)) + meta


def unpack_meta(msg: bytes, offset: int = 0) -> tuple[dict, int]:
    (n,) = META_LEN.unpack_from(msg, offset)
    start = offset + META_LEN.size
    return (json.loads(msg[start : start + n]) if n else {}), start + n


class Heightmap:
    def __init__(self, heights: np.ndarray, origin: tuple[float, float], size: tuple[float, float]) -> None:
        self.heights = heights
        self.origin = origin
        self.size = size

    def sample(self, x, z) -> np.ndarray:
        rows, cols = self.heights.shape
        u = np.clip((np.asarray(x, float) - self.origin[0]) / self.size[0] * (cols - 1), 0, cols - 1)
        v = np.clip((np.asarray(z, float) - self.origin[1]) / self.size[1] * (rows - 1), 0, rows - 1)
        c0, r0 = np.minimum(u.astype(int), cols - 2), np.minimum(v.astype(int), rows - 2)
        fu, fv = u - c0, v - r0
        h = self.heights
        top = h[r0, c0] * (1 - fu) + h[r0, c0 + 1] * fu
        bottom = h[r0 + 1, c0] * (1 - fu) + h[r0 + 1, c0 + 1] * fu
        return top * (1 - fv) + bottom * fv


def load_heightmap(world_dir: str | Path | None) -> Heightmap | None:
    if not world_dir:
        return None
    world_dir = Path(world_dir)
    try:
        meta = json.loads((world_dir / "heightmap_meta.json").read_text())
        res = int(meta["resolution"])
        raw = np.fromfile(world_dir / meta.get("file", "heightmap.raw"), "<u2")
        if raw.size != res * res:
            raise ValueError(f"heightmap has {raw.size} samples, expected {res * res}")
        heights = meta["min_height_m"] + raw.reshape(res, res).astype(np.float32) / 65535 * meta["height_range_m"]
        hm = Heightmap(heights, tuple(meta["origin"]), tuple(meta["size_m"]))
    except (OSError, ValueError, KeyError) as e:
        log.warning("no heightmap in %s (%s); HUD uses the local ground plane", world_dir, e)
        return None
    log.info("HUD heightmap %dx%d from %s", res, res, world_dir)
    return hm


def with_ground(items: list[dict], heightmap: Heightmap) -> list[dict]:
    if not items:
        return items
    ys = heightmap.sample([i["x"] for i in items], [i["z"] for i in items])
    return [{**i, "y": round(float(y), 2)} for i, y in zip(items, ys)]


def augment(snapshot: dict, heightmap: Heightmap | None, t: float | None = None) -> dict:
    snapshot["t"] = round(time.time() if t is None else t, 3)
    if heightmap is None:
        return snapshot
    snapshot["trucks"] = with_ground(snapshot["trucks"], heightmap)
    snapshot["pedestrians"] = with_ground(snapshot["pedestrians"], heightmap)
    snapshot["obstacles"] = with_ground(snapshot["obstacles"], heightmap)
    for truck in snapshot["trucks"]:
        route = truck.get("route")
        if route:
            pts = np.asarray(route, float)
            truck["route_y"] = np.round(heightmap.sample(pts[:, 0], pts[:, 1]), 2).tolist()
    return snapshot
