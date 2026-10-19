"""Top-down image around the truck, as a camera student would get it from a perfect detector.

Channels (uint8, 0 or 255):
  0 drivable ground (not blocked)
  1 movers inside the lidar field of view and range, drawn as discs (no occlusion)
  2 the route ahead
Rows run from 48 m ahead (top) to 16 m behind (bottom); columns from 32 m left to 32 m right.
"""

from __future__ import annotations

import math

import numpy as np

from .env import LIDAR_FOV, LIDAR_RANGE, DriveEnv

SIZE = 64
RES = 1.0  # m per pixel
AHEAD = 48.0
ROUTE_AHEAD = 80  # route samples drawn

_r, _c = np.mgrid[0:SIZE, 0:SIZE]
FWD = AHEAD - (_r + 0.5) * RES  # (SIZE, SIZE) metres forward of the truck
RIGHT = -SIZE * RES / 2 + (_c + 0.5) * RES


def render(env: DriveEnv) -> np.ndarray:
    tr = env.truck
    s, c = math.sin(tr.yaw), math.cos(tr.yaw)
    x = tr.x + s * FWD + c * RIGHT
    z = tr.z + c * FWD - s * RIGHT
    img = np.zeros((3, SIZE, SIZE), dtype=np.uint8)
    img[0] = np.where(env.world.blocked_at(x, z), 0, 255)
    for m in env.movers:
        dx, dz = m.x - tr.x, m.z - tr.z
        rel = (math.atan2(dx, dz) - tr.yaw + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) < LIDAR_FOV / 2 and math.hypot(dx, dz) < LIDAR_RANGE:
            r = max(m.radius, RES)
            img[1][(x - m.x) ** 2 + (z - m.z) ** 2 < r * r] = 255
    ni = env._nearest_idx()
    route = env.route[ni : ni + ROUTE_AHEAD]
    f = (route[:, 0] - tr.x) * s + (route[:, 1] - tr.z) * c
    rt = (route[:, 0] - tr.x) * c - (route[:, 1] - tr.z) * s
    row = np.floor((AHEAD - f) / RES).astype(int)
    col = np.floor((rt + SIZE * RES / 2) / RES).astype(int)
    ok = (row >= 0) & (row < SIZE) & (col >= 0) & (col < SIZE)
    img[2][row[ok], col[ok]] = 255
    return img
